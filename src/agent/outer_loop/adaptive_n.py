"""Adaptive N (어댑티브 N) — L2 한계 보완: CIB/피닉스 변동성 기반 N 동적 조정.

The outer loop runs every *N* episodes.  A fixed N is suboptimal because
the ideal check frequency depends on environmental volatility.  This module
dynamically adjusts N based on recent CIB and Phoenix score volatility:

    - **High volatility (> 0.15):** N shrinks to ``max(base_N // 2, 10)``
      → more frequent checks (early intervention when risk is detected)
    - **Low volatility (< 0.03):** N expands to ``min(base_N * 2, 200)``
      → resource savings during stable periods
    - **Normal:** N stays at ``base_N``

Safety constraints:
    - N is always within ``[base_N // 2, base_N * 2]``
    - N never goes below 10 or above 200
    - All changes are logged to ``data/memory/audit/adaptive_N_log.jsonl``

Risk level → base N mapping:
    - low:      100
    - medium:   50
    - high:     20
    - critical: 10
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger
from ..utils.serialization import write_jsonl
from ..utils.time import iso_now

logger = get_logger("agent.outer_loop.adaptive_n")


# ---------------------------------------------------------------------------
# Risk level → base N mapping
# ---------------------------------------------------------------------------

RISK_N_MAP: dict[str, int] = {
    "low": 100,
    "medium": 50,
    "high": 20,
    "critical": 10,
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AdaptiveNResult:
    """Result of an adaptive N computation.

    Attributes:
        new_N: The computed N value for the next cycle.
        old_N: The previous N value.
        base_N: The base N value (from risk level).
        cib_volatility: Standard deviation of recent CIB scores.
        phoenix_volatility: Standard deviation of recent Phoenix scores.
        combined_volatility: Weighted combination (0.6 * CIB + 0.4 * Phoenix).
        direction: 'expanded' | 'shrunk' | 'unchanged'.
        reason: Human-readable reason for the change.
    """
    new_N: int = 50
    old_N: int = 50
    base_N: int = 50
    cib_volatility: float = 0.0
    phoenix_volatility: float = 0.0
    combined_volatility: float = 0.0
    direction: str = "unchanged"
    reason: str = ""


# ---------------------------------------------------------------------------
# Adaptive N Calculator
# ---------------------------------------------------------------------------

class AdaptiveNCalculator:
    """Compute adaptive N based on CIB/Phoenix score volatility.

    Args:
        log_path: Path for the adaptive N change log (JSONL).
        high_volatility_threshold: Combined volatility above this → shrink N.
        low_volatility_threshold: Combined volatility below this → expand N.
        cib_weight: Weight for CIB volatility (default 0.6).
        phoenix_weight: Weight for Phoenix volatility (default 0.4).
        min_multiplier: Minimum N as fraction of base_N (default 0.5).
        max_multiplier: Maximum N as fraction of base_N (default 2.0).
        absolute_min: Absolute minimum N value (default 10).
        absolute_max: Absolute maximum N value (default 200).
        volatility_window: Number of recent scores for volatility computation (default 20).
        enabled: Whether adaptive N is enabled (default True).
    """

    def __init__(
        self,
        log_path: str = "data/memory/audit/adaptive_N_log.jsonl",
        high_volatility_threshold: float = 0.15,
        low_volatility_threshold: float = 0.03,
        cib_weight: float = 0.6,
        phoenix_weight: float = 0.4,
        min_multiplier: float = 0.5,
        max_multiplier: float = 2.0,
        absolute_min: int = 10,
        absolute_max: int = 200,
        volatility_window: int = 20,
        enabled: bool = True,
    ):
        self.log_path = Path(log_path)
        self.high_volatility_threshold = high_volatility_threshold
        self.low_volatility_threshold = low_volatility_threshold
        self.cib_weight = cib_weight
        self.phoenix_weight = phoenix_weight
        self.min_multiplier = min_multiplier
        self.max_multiplier = max_multiplier
        self.absolute_min = absolute_min
        self.absolute_max = absolute_max
        self.volatility_window = volatility_window
        self.enabled = enabled

        # History of N values for analysis
        self._n_history: list[dict[str, Any]] = []
        self._load_history()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(
        self,
        base_N: int,
        current_N: int,
        cib_scores: list[float] | None = None,
        phoenix_scores: list[float] | None = None,
        risk_level: str = "medium",
        outer_loop_count: int = 0,
        timestamp: str | None = None,
    ) -> AdaptiveNResult:
        """Compute the adaptive N for the next outer loop cycle.

        Args:
            base_N: Base N value (from risk level).
            current_N: Current N value.
            cib_scores: Recent CIB scores (most recent first or any order).
            phoenix_scores: Recent Phoenix scores.
            risk_level: Current risk level ('low' | 'medium' | 'high' | 'critical').
            outer_loop_count: Current outer loop cycle number.
            timestamp: ISO 8601 timestamp. If None, uses current time.

        Returns:
            :class:`AdaptiveNResult` with the computed N and volatility details.
        """
        ts = timestamp or iso_now()

        if not self.enabled:
            return AdaptiveNResult(
                new_N=base_N,
                old_N=current_N,
                base_N=base_N,
                reason="Adaptive N disabled, using base_N.",
            )

        # Compute volatility
        cib_vol = _std_dev(cib_scores[-self.volatility_window:]) if cib_scores else 0.0
        phoenix_vol = _std_dev(phoenix_scores[-self.volatility_window:]) if phoenix_scores else 0.0
        combined = self.cib_weight * cib_vol + self.phoenix_weight * phoenix_vol

        # Determine new N
        if combined > self.high_volatility_threshold:
            new_N = max(int(base_N * self.min_multiplier), self.absolute_min)
            direction = "shrunk"
            reason = (
                f"High volatility ({combined:.4f} > {self.high_volatility_threshold}) "
                f"→ N shrunk to {new_N} for more frequent checks"
            )
        elif combined < self.low_volatility_threshold:
            new_N = min(int(base_N * self.max_multiplier), self.absolute_max)
            direction = "expanded"
            reason = (
                f"Low volatility ({combined:.4f} < {self.low_volatility_threshold}) "
                f"→ N expanded to {new_N} for resource savings"
            )
        else:
            new_N = base_N
            direction = "unchanged"
            reason = (
                f"Normal volatility ({combined:.4f}) "
                f"→ N stays at base_N={base_N}"
            )

        result = AdaptiveNResult(
            new_N=new_N,
            old_N=current_N,
            base_N=base_N,
            cib_volatility=round(cib_vol, 4),
            phoenix_volatility=round(phoenix_vol, 4),
            combined_volatility=round(combined, 4),
            direction=direction,
            reason=reason,
        )

        # Log the change
        self._log_change(result, risk_level, outer_loop_count, ts)

        if new_N != current_N:
            logger.info(
                f"Adaptive N: {current_N} → {new_N} "
                f"(volatility={combined:.4f}, direction={direction})"
            )

        return result

    @staticmethod
    def get_base_n(risk_level: str) -> int:
        """Get the base N value for a given risk level.

        Args:
            risk_level: One of 'low', 'medium', 'high', 'critical'.

        Returns:
            Base N value (100, 50, 20, or 10). Defaults to 50 for unknown.
        """
        return RISK_N_MAP.get(risk_level, 50)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return the N value change history."""
        return list(self._n_history)

    def _load_history(self) -> None:
        """Load N change history from disk."""
        if not self.log_path.exists():
            return
        try:
            from ..utils.serialization import read_jsonl_all
            self._n_history = read_jsonl_all(self.log_path)
            logger.info(f"Loaded {len(self._n_history)} adaptive N records from {self.log_path}")
        except Exception as e:
            logger.warning(f"Failed to load adaptive N history: {e}")
            self._n_history = []

    def _log_change(
        self,
        result: AdaptiveNResult,
        risk_level: str,
        outer_loop_count: int,
        timestamp: str,
    ) -> None:
        """Log an N value change to the JSONL audit log.

        Records every computation (not just changes) for full auditability.
        """
        entry = {
            "timestamp": timestamp,
            "outer_loop_count": outer_loop_count,
            "old_N": result.old_N,
            "new_N": result.new_N,
            "base_N": result.base_N,
            "risk_level": risk_level,
            "direction": result.direction,
            "cib_volatility": result.cib_volatility,
            "phoenix_volatility": result.phoenix_volatility,
            "combined_volatility": result.combined_volatility,
            "reason": result.reason,
        }

        self._n_history.append(entry)
        # Keep only recent 500 entries in memory
        self._n_history = self._n_history[-500:]

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(self.log_path, entry)
        except Exception as e:
            logger.warning(f"Failed to log adaptive N change: {e}")

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def get_n_value_history(self, n: int = 20) -> list[int]:
        """Return the most recent *n* N values.

        Args:
            n: Number of recent values to return.

        Returns:
            List of N values (oldest first).
        """
        recent = self._n_history[-n:]
        return [e.get("new_N", 50) for e in recent]

    def get_volatility_trend(self, n: int = 10) -> list[float]:
        """Return the most recent *n* combined volatility values.

        Args:
            n: Number of recent values to return.

        Returns:
            List of combined volatility values (oldest first).
        """
        recent = self._n_history[-n:]
        return [e.get("combined_volatility", 0.0) for e in recent]


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