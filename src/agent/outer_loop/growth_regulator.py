"""Step 6: Growth Rate Regulator (M16) — 추락/정체/과속 감지.

Monitors the system's growth trajectory and detects three abnormal signals:

    | Signal | Condition | Action |
    |--------|-----------|--------|
    | **Crash (추락)** | Recent 20 episodes' avg success rate drops by >= 0.15
      vs the previous 20 | Force CIB gate → suspend learning → analyze |
    | **Stagnation (정체)** | Coherence index change < 0.01 for 50+ episodes |
      Trigger meta-loop stagnation response |
    | **Overgrowth (과속)** | Coherence index rises >= 0.2 within 7 days |
      Force CIB gate → suspect overfitting → enhance generalization check |

When a signal is detected, the regulator returns the appropriate action
for the outer loop to execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..utils.logging import get_logger

logger = get_logger("agent.outer_loop.growth_regulator")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GrowthSignal(str, Enum):
    """M16 growth rate signal types."""
    NORMAL = "normal"
    CRASH = "crash"           # 추락
    STAGNATION = "stagnation"  # 정체
    OVERGROWTH = "overgrowth"  # 과속


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class GrowthRegulationResult:
    """Result of growth rate regulation.

    Attributes:
        signal: The detected :class:`GrowthSignal`.
        action: Recommended action string.
        details: Signal-specific details (e.g., success rate drop, coherence delta).
        cib_force_required: Whether CIB gate should be force-invoked.
        learning_suspended: Whether learning should be suspended.
        meta_trigger_required: Whether a meta-loop trigger should be fired.
    """
    signal: GrowthSignal = GrowthSignal.NORMAL
    action: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    cib_force_required: bool = False
    learning_suspended: bool = False
    meta_trigger_required: bool = False


# ---------------------------------------------------------------------------
# Growth Rate Regulator (M16)
# ---------------------------------------------------------------------------

class GrowthRateRegulator:
    """M16 Growth Rate Regulator — detect crash, stagnation, and overgrowth.

    Args:
        crash_window: Number of episodes for crash comparison window (default 20).
        crash_delta_threshold: Success rate drop threshold for crash (default 0.15).
        stagnation_window: Minimum episodes for stagnation detection (default 50).
        stagnation_coherence_delta: Coherence change threshold (default 0.01).
        overgrowth_days: Days window for overgrowth detection (default 7).
        overgrowth_coherence_rise: Coherence rise threshold (default 0.2).
        coherence_tracker: Optional :class:`CoherenceIndexTracker` for
            persistent, time-windowed coherence analysis. If provided,
            replaces the ad-hoc in-memory history.
    """

    def __init__(
        self,
        crash_window: int = 20,
        crash_delta_threshold: float = 0.15,
        stagnation_window: int = 50,
        stagnation_coherence_delta: float = 0.01,
        overgrowth_days: int = 7,
        overgrowth_coherence_rise: float = 0.2,
        coherence_tracker: Any | None = None,
    ):
        self.crash_window = crash_window
        self.crash_delta_threshold = crash_delta_threshold
        self.stagnation_window = stagnation_window
        self.stagnation_coherence_delta = stagnation_coherence_delta
        self.overgrowth_days = overgrowth_days
        self.overgrowth_coherence_rise = overgrowth_coherence_rise
        self.coherence_tracker = coherence_tracker

        # Fallback in-memory history (used when no tracker is provided)
        self._coherence_history: list[tuple[str, float]] = []  # (timestamp, coherence)
        self._success_rate_history: list[float] = []

    def regulate(
        self,
        aggregation_result: Any | None = None,
        coherence_index: float | None = None,
        timestamp: str = "",
    ) -> GrowthRegulationResult:
        """Run growth rate regulation checks.

        Args:
            aggregation_result: :class:`AggregationResult` from Step 1.
            coherence_index: Current coherence index (M17).
            timestamp: ISO 8601 timestamp for history tracking.

        Returns:
            :class:`GrowthRegulationResult` with detected signal and action.
        """
        # Track coherence history (use tracker if available, else fallback)
        if self.coherence_tracker is not None and coherence_index is not None:
            # Tracker handles its own persistence and history
            pass  # Recording is done by the outer loop via tracker.record()
        elif coherence_index is not None and timestamp:
            self._coherence_history.append((timestamp, coherence_index))
            self._coherence_history = self._coherence_history[-200:]

        # Track success rate history
        if aggregation_result is not None:
            self._success_rate_history.append(aggregation_result.success_rate)
            self._success_rate_history = self._success_rate_history[-100:]

        # Check signals in priority order: crash > overgrowth > stagnation
        crash_result = self._check_crash(aggregation_result)
        if crash_result.signal != GrowthSignal.NORMAL:
            return crash_result

        overgrowth_result = self._check_overgrowth(coherence_index, timestamp)
        if overgrowth_result.signal != GrowthSignal.NORMAL:
            return overgrowth_result

        stagnation_result = self._check_stagnation(coherence_index)
        if stagnation_result.signal != GrowthSignal.NORMAL:
            return stagnation_result

        return GrowthRegulationResult(
            signal=GrowthSignal.NORMAL,
            action="No abnormal growth signals detected.",
            details={
                "coherence_index": coherence_index,
                "success_rate": aggregation_result.success_rate if aggregation_result else None,
            },
        )

    # ------------------------------------------------------------------
    # Signal checks
    # ------------------------------------------------------------------

    def _check_crash(self, aggregation_result: Any | None) -> GrowthRegulationResult:
        """Check for crash signal (success rate drop >= 0.15).

        Compares the most recent ``crash_window`` episodes' success rate
        against the previous ``crash_window`` episodes.
        """
        if aggregation_result is None or aggregation_result.episode_count < self.crash_window * 2:
            return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

        # Use success rate history if available
        history = self._success_rate_history
        if len(history) >= 2:
            recent_rate = history[-1]
            previous_rate = history[-2] if len(history) >= 2 else None

            if previous_rate is not None:
                drop = previous_rate - recent_rate
                if drop >= self.crash_delta_threshold:
                    logger.warning(
                        f"CRASH detected: success rate dropped by {drop:.4f} "
                        f"({previous_rate:.4f} → {recent_rate:.4f})"
                    )
                    return GrowthRegulationResult(
                        signal=GrowthSignal.CRASH,
                        action="Force CIB gate → suspend learning → analyze root cause",
                        details={
                            "previous_rate": round(previous_rate, 4),
                            "recent_rate": round(recent_rate, 4),
                            "drop": round(drop, 4),
                            "threshold": self.crash_delta_threshold,
                        },
                        cib_force_required=True,
                        learning_suspended=True,
                    )

        return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

    def _check_overgrowth(
        self,
        coherence_index: float | None,
        timestamp: str,
    ) -> GrowthRegulationResult:
        """Check for overgrowth signal (coherence rises >= 0.2 in 7 days).

        Uses the :class:`CoherenceIndexTracker` for proper time-windowed
        comparison if available; falls back to in-memory history otherwise.
        """
        # Use coherence tracker if available (proper 7-day window)
        if self.coherence_tracker is not None:
            result = self.coherence_tracker.detect_overgrowth(
                current_coherence=coherence_index,
            )
            if result["detected"]:
                details = result["details"]
                logger.warning(
                    f"OVERGROWTH detected: coherence rose by {result['rise']:.4f} "
                    f"({details.get('earliest_coherence', '?')} → "
                    f"{details.get('current_coherence', '?')}) within {self.overgrowth_days} days"
                )
                return GrowthRegulationResult(
                    signal=GrowthSignal.OVERGROWTH,
                    action="Force CIB gate → suspect overfitting → enhance generalization check",
                    details=details,
                    cib_force_required=True,
                )
            return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

        # Fallback: in-memory history
        if coherence_index is None or not self._coherence_history:
            return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

        if len(self._coherence_history) < 2:
            return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

        earliest_coherence = self._coherence_history[0][1]
        rise = coherence_index - earliest_coherence

        if rise >= self.overgrowth_coherence_rise:
            logger.warning(
                f"OVERGROWTH detected: coherence rose by {rise:.4f} "
                f"({earliest_coherence:.4f} → {coherence_index:.4f})"
            )
            return GrowthRegulationResult(
                signal=GrowthSignal.OVERGROWTH,
                action="Force CIB gate → suspect overfitting → enhance generalization check",
                details={
                    "earliest_coherence": round(earliest_coherence, 4),
                    "current_coherence": round(coherence_index, 4),
                    "rise": round(rise, 4),
                    "threshold": self.overgrowth_coherence_rise,
                },
                cib_force_required=True,
            )

        return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

    def _check_stagnation(
        self,
        coherence_index: float | None,
    ) -> GrowthRegulationResult:
        """Check for stagnation signal (coherence change < 0.01 for 50+ episodes).

        Uses the :class:`CoherenceIndexTracker` for proper windowed analysis
        if available; falls back to in-memory history otherwise.
        """
        # Use coherence tracker if available
        if self.coherence_tracker is not None:
            if self.coherence_tracker.detect_stagnation():
                stats = self.coherence_tracker.compute_stats(
                    self.coherence_tracker.get_recent(self.stagnation_window)
                )
                logger.warning(
                    f"STAGNATION detected: coherence range {stats.get('range', 0):.4f} "
                    f"< {self.stagnation_coherence_delta} over {self.stagnation_window} entries"
                )
                return GrowthRegulationResult(
                    signal=GrowthSignal.STAGNATION,
                    action="Trigger meta-loop stagnation response",
                    details={
                        "coherence_range": stats.get("range", 0.0),
                        "threshold": self.stagnation_coherence_delta,
                        "window_size": self.stagnation_window,
                        "mean": stats.get("mean"),
                        "std": stats.get("std"),
                    },
                    meta_trigger_required=True,
                )
            return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

        # Fallback: in-memory history
        if coherence_index is None or len(self._coherence_history) < self.stagnation_window:
            return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

        recent = self._coherence_history[-self.stagnation_window:]
        coherences = [c for _, c in recent]
        if not coherences:
            return GrowthRegulationResult(signal=GrowthSignal.NORMAL)

        coherence_range = max(coherences) - min(coherences)
        if coherence_range < self.stagnation_coherence_delta:
            logger.warning(
                f"STAGNATION detected: coherence range {coherence_range:.4f} "
                f"< {self.stagnation_coherence_delta} over {len(coherences)} episodes"
            )
            return GrowthRegulationResult(
                signal=GrowthSignal.STAGNATION,
                action="Trigger meta-loop stagnation response",
                details={
                    "coherence_range": round(coherence_range, 4),
                    "threshold": self.stagnation_coherence_delta,
                    "window_size": len(coherences),
                },
                meta_trigger_required=True,
            )

        return GrowthRegulationResult(signal=GrowthSignal.NORMAL)