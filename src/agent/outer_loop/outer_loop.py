"""Outer Loop Orchestrator — 7단계 프로세스 순차 실행.

Coordinates the 7-step outer loop process:

    1. Data Aggregation       — aggregator.py
    2. Metrics Recording       — metrics.py (CIB + M17 + BC)
    3. Cache Refresh           — cache_refresher.py
    4. Self-Model Recalculation — self_model_recalculator.py (M14)
    5. Independent Audit       — auditor.py (M15 deviation)
    6. Growth Rate Regulation  — growth_regulator.py (M16)
    7. Meta Loop Trigger       — meta_trigger.py

The outer loop runs every *N* episodes (adaptive N).  After each run, it
optionally recomputes the adaptive N value for the next cycle.

Usage::

    from agent.outer_loop import OuterLoop

    outer = OuterLoop(memory_manager=mm, ...)
    result = outer.run()
    if result.meta_trigger.triggered:
        # Hand off to meta loop
        ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..memory.manager import MemoryManager
from ..memory.constitution.guard import CIBGuard
from ..memory.identity.self_model import SelfModel
from ..memory.identity.updater import IdentityUpdater
from ..utils.logging import get_logger
from ..utils.serialization import write_jsonl
from ..utils.time import iso_now

from .aggregator import DataAggregator, AggregationResult
from .metrics import MetricsRecorder, MetricsResult
from .cache_refresher import CacheRefresher, CacheRefreshResult
from .self_model_recalculator import SelfModelRecalculator, RecalculationResult
from .auditor import IndependentAuditor, AuditResult
from .growth_regulator import GrowthRateRegulator, GrowthRegulationResult, GrowthSignal
from .meta_trigger import MetaTrigger, TriggerResult, TriggerType

logger = get_logger("agent.outer_loop")


# ---------------------------------------------------------------------------
# Outer Loop State — persisted between runs
# ---------------------------------------------------------------------------

@dataclass
class OuterLoopState:
    """Persistent state of the outer loop across runs.

    Attributes:
        outer_loop_count: Total number of outer loop cycles executed.
        total_episodes_seen: Total episodes at last outer loop run.
        current_N: Current adaptive N value.
        base_N: Base N value (from risk level).
        risk_level: Current risk level ('low' | 'medium' | 'high' | 'critical').
        last_coherence_index: Coherence index from the last run.
        last_success_rate: Success rate from the last run.
    """
    outer_loop_count: int = 0
    total_episodes_seen: int = 0
    current_N: int = 50
    base_N: int = 50
    risk_level: str = "medium"
    last_coherence_index: float | None = None
    last_success_rate: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outer_loop_count": self.outer_loop_count,
            "total_episodes_seen": self.total_episodes_seen,
            "current_N": self.current_N,
            "base_N": self.base_N,
            "risk_level": self.risk_level,
            "last_coherence_index": self.last_coherence_index,
            "last_success_rate": self.last_success_rate,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OuterLoopState:
        return cls(
            outer_loop_count=d.get("outer_loop_count", 0),
            total_episodes_seen=d.get("total_episodes_seen", 0),
            current_N=d.get("current_N", 50),
            base_N=d.get("base_N", 50),
            risk_level=d.get("risk_level", "medium"),
            last_coherence_index=d.get("last_coherence_index"),
            last_success_rate=d.get("last_success_rate"),
        )


# ---------------------------------------------------------------------------
# Outer Loop Result — aggregate result of one full 7-step run
# ---------------------------------------------------------------------------

@dataclass
class OuterLoopResult:
    """Aggregate result of one outer loop cycle.

    Attributes:
        aggregation: Step 1 result.
        metrics: Step 2 result.
        cache_refresh: Step 3 result.
        recalculation: Step 4 result.
        audit: Step 5 result.
        growth_regulation: Step 6 result.
        meta_trigger: Step 7 result.
        state: Updated outer loop state after this run.
        adaptive_N: Computed adaptive N for the next cycle.
        timestamp: ISO 8601 timestamp of this run.
    """
    aggregation: AggregationResult = field(default_factory=AggregationResult)
    metrics: MetricsResult = field(default_factory=MetricsResult)
    cache_refresh: CacheRefreshResult = field(default_factory=CacheRefreshResult)
    recalculation: RecalculationResult = field(default_factory=RecalculationResult)
    audit: AuditResult = field(default_factory=AuditResult)
    growth_regulation: GrowthRegulationResult = field(default_factory=GrowthRegulationResult)
    meta_trigger: TriggerResult = field(default_factory=TriggerResult)
    state: OuterLoopState = field(default_factory=OuterLoopState)
    adaptive_N: int = 50
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Outer Loop Orchestrator
# ---------------------------------------------------------------------------

# Risk level → base N mapping
_RISK_N_MAP = {
    "low": 100,
    "medium": 50,
    "high": 20,
    "critical": 10,
}


class OuterLoop:
    """Outer Loop orchestrator — runs the 7-step health check process.

    Args:
        memory_manager: A :class:`MemoryManager` instance.
        state_path: Path to persist outer loop state (JSON).
        audit_log_path: Path for audit log (JSONL).
        window_size: Self-model window size (default 50).
        risk_level: Initial risk level (default 'medium').
        adaptive_N_config: Configuration for adaptive N computation.
        growth_regulator_config: Configuration for M16 thresholds.
        meta_trigger_config: Configuration for meta loop thresholds.
    """

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        state_path: str = "data/memory/audit/outer_loop_state.json",
        audit_log_path: str = "data/memory/audit/outer_loop_log.jsonl",
        window_size: int = 50,
        risk_level: str = "medium",
        adaptive_N_config: dict[str, Any] | None = None,
        growth_regulator_config: dict[str, Any] | None = None,
        meta_trigger_config: dict[str, Any] | None = None,
    ):
        self.memory_manager = memory_manager
        self.state_path = Path(state_path)
        self.audit_log_path = Path(audit_log_path)
        self.window_size = window_size

        # Load or initialize state
        self.state = self._load_state(risk_level)

        # Initialize step modules
        episodic_store = (
            memory_manager.episodic_store if memory_manager else None
        )
        self.aggregator = DataAggregator(
            episodic_store=episodic_store,
            window_size=window_size,
        )

        # Self-model from memory manager
        self_model = None
        identity_updater = None
        if memory_manager is not None:
            self_model = SelfModel(
                store=memory_manager.identity_store,
                window_size=window_size,
            )
            identity_updater = IdentityUpdater(
                store=memory_manager.identity_store,
                self_model=self_model,
            )

        self.metrics_recorder = MetricsRecorder(
            cib_guard=CIBGuard(),
            self_model=self_model,
        )
        self.cache_refresher = CacheRefresher(memory_manager=memory_manager)
        self.self_model_recalculator = SelfModelRecalculator(
            self_model=self_model,
            identity_updater=identity_updater,
            window_size=window_size,
        )
        self.auditor = IndependentAuditor(
            self_model=self_model,
            identity_updater=identity_updater,
        )

        gr_config = growth_regulator_config or {}
        self.growth_regulator = GrowthRateRegulator(**gr_config)

        mt_config = meta_trigger_config or {}
        self.meta_trigger = MetaTrigger(**mt_config)

        self.adaptive_N_config = adaptive_N_config or {
            "enabled": True,
            "min_multiplier": 0.5,
            "max_multiplier": 2.0,
            "high_volatility_threshold": 0.15,
            "low_volatility_threshold": 0.03,
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> OuterLoopResult:
        """Run the full 7-step outer loop process.

        Returns:
            :class:`OuterLoopResult` with all step results.
        """
        timestamp = iso_now()
        logger.info(
            f"=== Outer Loop cycle {self.state.outer_loop_count + 1} "
            f"(N={self.state.current_N}) ==="
        )

        result = OuterLoopResult(timestamp=timestamp)

        # Step 1: Data Aggregation
        logger.info("Step 1: Data Aggregation")
        result.aggregation = self.aggregator.aggregate(
            window_size=self.state.current_N,
        )

        # Step 2: Metrics Recording (CIB + M17 + BC)
        logger.info("Step 2: Metrics Recording")
        constitution = None
        if self.memory_manager is not None:
            try:
                constitution = self.memory_manager.constitution
            except Exception as e:
                logger.warning(f"Failed to load constitution: {e}")

        result.metrics = self.metrics_recorder.record(
            aggregation_result=result.aggregation,
            constitution=constitution,
        )

        # Step 3: Cache Refresh
        logger.info("Step 3: Cache Refresh")
        result.cache_refresh = self.cache_refresher.refresh()

        # Step 4: Self-Model Recalculation (M14)
        logger.info("Step 4: Self-Model Recalculation")
        result.recalculation = self.self_model_recalculator.recalculate(
            aggregation_result=result.aggregation,
            avg_cib_score=result.metrics.avg_cib_score,
        )

        # Step 5: Independent Audit (M15 deviation)
        logger.info("Step 5: Independent Audit")
        result.audit = self.auditor.audit(
            aggregation_result=result.aggregation,
        )

        # Step 6: Growth Rate Regulation (M16)
        logger.info("Step 6: Growth Rate Regulation")
        coherence = result.recalculation.coherence_index or result.metrics.coherence_index
        result.growth_regulation = self.growth_regulator.regulate(
            aggregation_result=result.aggregation,
            coherence_index=coherence,
            timestamp=timestamp,
        )

        # Step 7: Meta Loop Trigger
        logger.info("Step 7: Meta Loop Trigger")
        total_episodes = self._get_total_episode_count()
        result.meta_trigger = self.meta_trigger.evaluate(
            episode_count=total_episodes,
            outer_loop_count=self.state.outer_loop_count + 1,
            stagnation_detected=(
                result.growth_regulation.signal == GrowthSignal.STAGNATION
            ),
        )

        # Update state
        self.state.outer_loop_count += 1
        self.state.total_episodes_seen = total_episodes
        self.state.last_coherence_index = coherence
        self.state.last_success_rate = result.aggregation.success_rate

        # Compute adaptive N for next cycle
        result.adaptive_N = self._compute_adaptive_n(result)
        self.state.current_N = result.adaptive_N

        result.state = self.state

        # Persist state and log
        self._save_state()
        self._log_run(result)

        logger.info(
            f"=== Outer Loop complete: signal={result.growth_regulation.signal.value}, "
            f"trigger={result.meta_trigger.trigger_type.value}, "
            f"next_N={result.adaptive_N} ==="
        )

        return result

    # ------------------------------------------------------------------
    # Adaptive N computation
    # ------------------------------------------------------------------

    def _compute_adaptive_n(self, result: OuterLoopResult) -> int:
        """Compute adaptive N for the next outer loop cycle.

        Based on CIB and Phoenix score volatility:
            - High volatility (> 0.15): N = max(base_N // 2, 10)
            - Low volatility (< 0.03):  N = min(base_N * 2, 200)
            - Normal: N = base_N
        """
        config = self.adaptive_N_config
        if not config.get("enabled", True):
            return self.state.base_N

        cib_scores = result.aggregation.cib_scores
        phoenix_scores = result.aggregation.phoenix_scores

        cib_volatility = _std_dev(cib_scores[-20:]) if cib_scores else 0.0
        phoenix_volatility = _std_dev(phoenix_scores[-20:]) if phoenix_scores else 0.0

        combined = 0.6 * cib_volatility + 0.4 * phoenix_volatility

        high_threshold = config.get("high_volatility_threshold", 0.15)
        low_threshold = config.get("low_volatility_threshold", 0.03)
        min_mult = config.get("min_multiplier", 0.5)
        max_mult = config.get("max_multiplier", 2.0)

        base_N = self.state.base_N

        if combined > high_threshold:
            new_N = max(int(base_N * min_mult), 10)
        elif combined < low_threshold:
            new_N = min(int(base_N * max_mult), 200)
        else:
            new_N = base_N

        if new_N != self.state.current_N:
            logger.info(
                f"Adaptive N updated: {self.state.current_N} → {new_N} "
                f"(volatility={combined:.4f})"
            )

        return new_N

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self, risk_level: str) -> OuterLoopState:
        """Load state from disk or initialize new state."""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                state = OuterLoopState.from_dict(data)
                logger.info(f"Loaded outer loop state: count={state.outer_loop_count}")
                return state
            except Exception as e:
                logger.warning(f"Failed to load state, initializing fresh: {e}")

        base_N = _RISK_N_MAP.get(risk_level, 50)
        return OuterLoopState(
            current_N=base_N,
            base_N=base_N,
            risk_level=risk_level,
        )

    def _save_state(self) -> None:
        """Persist state to disk."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.state.to_dict(), indent=2)
            )
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    def _log_run(self, result: OuterLoopResult) -> None:
        """Append a summary of this run to the audit log."""
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": result.timestamp,
                "outer_loop_count": self.state.outer_loop_count,
                "episode_count": result.aggregation.episode_count,
                "success_rate": result.aggregation.success_rate,
                "avg_phoenix": result.aggregation.avg_phoenix_score,
                "avg_cib": result.metrics.avg_cib_score,
                "coherence_index": result.recalculation.coherence_index,
                "calibration_error": result.recalculation.calibration_error,
                "audit_deviation": result.audit.deviation,
                "audit_flagged": result.audit.flagged,
                "growth_signal": result.growth_regulation.signal.value,
                "meta_trigger": result.meta_trigger.trigger_type.value,
                "adaptive_N": result.adaptive_N,
            }
            write_jsonl(self.audit_log_path, entry)
        except Exception as e:
            logger.warning(f"Failed to log run: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_total_episode_count(self) -> int:
        """Get the total number of episodes in L1."""
        if self.memory_manager is None:
            return self.state.total_episodes_seen
        try:
            return self.memory_manager.episodic_store.count()
        except Exception as e:
            logger.warning(f"Failed to get episode count: {e}")
            return self.state.total_episodes_seen


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _std_dev(values: list[float]) -> float:
    """Compute standard deviation of a list of values."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return round(variance ** 0.5, 4)