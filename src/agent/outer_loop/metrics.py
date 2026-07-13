"""Step 2: Metrics Recording — CIB 전체 검증 + 코히어런스 인덱스(M17) + 행동 일관성(BC).

Records system health metrics after data aggregation:

    - **CIB validation**: Run all K-Scenarios against recent episode results,
      compute the average CIB score.
    - **Coherence Index (M17)**: ``C = 0.5 × avg(CIB) + 0.5 × (1 - avg(calibration_error))``
      Measures how consistently the agent's identity aligns with its constitution
      and self-model.
    - **Behavioral Consistency (BC)**: Fraction of episodes where the agent's
      action matched its predicted plan (status == Success when predicted high,
      status != Success when predicted low).  A simple proxy: the ratio of
      episodes where ``success_score`` is consistent with the plan's expected
      outcome.

The coherence index is the key signal for M16 (growth regulator) and the
meta loop stagnation trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory.constitution.guard import CIBGuard, CIBResult
from ..memory.identity.self_model import SelfModel, WindowStats
from ..utils.logging import get_logger

logger = get_logger("agent.outer_loop.metrics")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MetricsResult:
    """Recorded system health metrics.

    Attributes:
        avg_cib_score: Average CIB score across recent episodes (0–1).
        cib_passed: Whether all CIB evaluations passed (>= 0.95).
        coherence_index: M17 coherence index (0–1).
        calibration_error: Average calibration error from self-model.
        behavioral_consistency: BC ratio (0–1).
        window_size: Number of episodes used for computation.
    """
    avg_cib_score: float | None = None
    cib_passed: bool = True
    coherence_index: float | None = None
    calibration_error: float | None = None
    behavioral_consistency: float | None = None
    window_size: int = 0
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Metrics Recorder
# ---------------------------------------------------------------------------

class MetricsRecorder:
    """Record CIB, coherence index (M17), and behavioral consistency (BC).

    Args:
        cib_guard: A :class:`CIBGuard` instance for CIB validation.
        self_model: A :class:`SelfModel` instance for calibration data.
        cib_weight: Weight for CIB in coherence index (default 0.5).
        calibration_weight: Weight for calibration in coherence index (default 0.5).
    """

    def __init__(
        self,
        cib_guard: CIBGuard | None = None,
        self_model: SelfModel | None = None,
        cib_weight: float = 0.5,
        calibration_weight: float = 0.5,
    ):
        self.cib_guard = cib_guard or CIBGuard()
        self.self_model = self_model
        self.cib_weight = cib_weight
        self.calibration_weight = calibration_weight

    def record(
        self,
        aggregation_result: Any | None = None,
        constitution: Any | None = None,
        episode_texts: list[str] | None = None,
    ) -> MetricsResult:
        """Record metrics from aggregation and self-model data.

        Args:
            aggregation_result: :class:`AggregationResult` from Step 1.
            constitution: The :class:`Constitution` model for CIB validation.
            episode_texts: List of episode result texts for CIB validation.
                If None, uses aggregation's episode_ids to skip per-episode CIB.

        Returns:
            :class:`MetricsResult` with all recorded metrics.
        """
        # --- CIB validation ---
        avg_cib = None
        cib_passed = True

        if aggregation_result is not None:
            # Use pre-computed CIB scores from aggregation if available
            if aggregation_result.avg_cib_score is not None:
                avg_cib = aggregation_result.avg_cib_score
                cib_scores = aggregation_result.cib_scores
                if cib_scores:
                    cib_passed = all(s >= self.cib_guard.threshold for s in cib_scores)
            elif constitution is not None and episode_texts:
                # Run CIB validation on each episode result
                scores = []
                for text in episode_texts:
                    result = self.cib_guard.evaluate(text, constitution)
                    scores.append(result.min_score)
                avg_cib = _safe_mean(scores)
                cib_passed = all(s >= self.cib_guard.threshold for s in scores)

        # --- Calibration error from self-model ---
        calibration_error = None
        if self.self_model is not None:
            window_stats = self.self_model.compute_window_stats()
            calibration_error = window_stats.avg_calibration_error

        # --- Coherence index (M17) ---
        # C = w_cib × avg(CIB) + w_cal × (1 - avg(calibration_error))
        coherence = self._compute_coherence(avg_cib, calibration_error)

        # --- Behavioral consistency (BC) ---
        bc = self._compute_behavioral_consistency(aggregation_result)

        result = MetricsResult(
            avg_cib_score=avg_cib,
            cib_passed=cib_passed,
            coherence_index=coherence,
            calibration_error=calibration_error,
            behavioral_consistency=bc,
            window_size=aggregation_result.episode_count if aggregation_result else 0,
            details={
                "cib_weight": self.cib_weight,
                "calibration_weight": self.calibration_weight,
            },
        )

        logger.info(
            f"Metrics recorded: avg_cib={avg_cib}, coherence={coherence}, "
            f"cal_error={calibration_error}, BC={bc}"
        )
        return result

    def _compute_coherence(
        self,
        avg_cib: float | None,
        calibration_error: float | None,
    ) -> float | None:
        """Compute the coherence index (M17).

        C = w_cib × avg(CIB) + w_cal × (1 - avg(calibration_error))

        Returns None if either component is missing.
        """
        if avg_cib is None and calibration_error is None:
            return None

        # Use defaults: CIB=1.0 if no data, calibration=0.0 if no data
        cib_component = avg_cib if avg_cib is not None else 1.0
        cal_component = (1.0 - calibration_error) if calibration_error is not None else 1.0

        coherence = (
            self.cib_weight * cib_component
            + self.calibration_weight * cal_component
        )
        return round(max(0.0, min(1.0, coherence)), 4)

    @staticmethod
    def _compute_behavioral_consistency(
        aggregation_result: Any | None,
    ) -> float | None:
        """Compute behavioral consistency (BC).

        BC is the ratio of episodes where the outcome was consistent with
        the expected behavior — i.e., episodes that either succeeded (status
        = Success) or failed cleanly (status = Failure) without ambiguous
        Partial/Pending states.

        A higher BC means the agent's behavior is more predictable and
        consistent.
        """
        if aggregation_result is None or aggregation_result.episode_count == 0:
            return None

        dist = aggregation_result.status_distribution
        consistent = dist.get("Success", 0) + dist.get("Failure", 0)
        total = aggregation_result.episode_count
        return round(consistent / total, 4) if total > 0 else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)