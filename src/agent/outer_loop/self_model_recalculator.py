"""Step 4: Self-Model Recalculation — 최근 50개 에피소드 윈도우 기준 M14 재계산.

Recalculates the agent's self-model (M14) using the most recent 50 episodes:
    - Recompute window statistics (avg calibration error, success rate,
      confidence margin, overconfident ratio)
    - Recompute coherence index (M17) with fresh CIB data
    - Update capability records from recent episode outcomes

This step ensures the agent's "self-resume" stays current, enabling accurate
self-predictions for future inner loop cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..memory.identity.self_model import SelfModel, WindowStats
from ..memory.identity.updater import IdentityUpdater, StatisticsUpdateResult
from ..utils.logging import get_logger

logger = get_logger("agent.outer_loop.self_model_recalculator")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class RecalculationResult:
    """Result of self-model recalculation.

    Attributes:
        window_stats: Computed window statistics.
        coherence_index: Updated coherence index (M17).
        calibration_error: Average calibration error.
        calibration_direction: Dominant calibration direction.
        records_updated: Number of self-model records updated.
    """
    window_stats: WindowStats | None = None
    coherence_index: float | None = None
    calibration_error: float | None = None
    calibration_direction: str | None = None
    records_updated: int = 0


# ---------------------------------------------------------------------------
# Self-Model Recalculator
# ---------------------------------------------------------------------------

class SelfModelRecalculator:
    """Recalculate M14 self-model from recent episode data.

    Args:
        self_model: A :class:`SelfModel` instance.
        identity_updater: An :class:`IdentityUpdater` instance (optional).
        window_size: Number of recent episodes for window stats (default 50).
    """

    def __init__(
        self,
        self_model: SelfModel | None = None,
        identity_updater: IdentityUpdater | None = None,
        window_size: int = 50,
    ):
        self.self_model = self_model
        self.identity_updater = identity_updater
        self.window_size = window_size

    def recalculate(
        self,
        aggregation_result: Any | None = None,
        avg_cib_score: float | None = None,
    ) -> RecalculationResult:
        """Recalculate self-model window statistics and coherence index.

        Args:
            aggregation_result: :class:`AggregationResult` from Step 1.
            avg_cib_score: Average CIB score for coherence index computation.

        Returns:
            :class:`RecalculationResult` with updated metrics.
        """
        if self.self_model is None:
            logger.warning("No self-model available, recalculation skipped")
            return RecalculationResult()

        # Compute window statistics from self-model records
        window_stats = self.self_model.compute_window_stats()

        # Compute coherence index with fresh CIB data
        coherence = self.self_model._compute_coherence(
            window_stats,
            avg_cib_score=avg_cib_score,
        )

        # Determine dominant calibration direction
        calibration_direction = self._dominant_direction()

        result = RecalculationResult(
            window_stats=window_stats,
            coherence_index=coherence,
            calibration_error=window_stats.avg_calibration_error,
            calibration_direction=calibration_direction,
            records_updated=window_stats.success_rate is not None and 1 or 0,
        )

        logger.info(
            f"Self-model recalculated: cal_error={result.calibration_error}, "
            f"coherence={coherence}, direction={calibration_direction}"
        )
        return result

    def _dominant_direction(self) -> str | None:
        """Determine the dominant calibration direction from recent records."""
        if self.self_model is None:
            return None

        summary = self.self_model.get_calibration_summary()
        direction_counts = summary.get("direction_counts", {})
        if not direction_counts:
            return None

        # Return the direction with the highest count
        return max(direction_counts, key=direction_counts.get)