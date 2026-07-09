"""M14 Agent Self-Model for L5 Identity.

Implements the self-model calibration system:
    - Records predicted vs actual success for each episode
    - Computes calibration_error = |predicted - actual|
    - Classifies calibration direction (overconfident / underconfident / calibrated)
    - Maintains rolling window statistics (last 50 episodes)
    - Computes coherence index (M17): C = 0.5 × avg(CIB) + 0.5 × (1 - avg(calibration_error))

The self-model is the core of the agent's self-awareness: it tracks how
well the agent predicts its own performance, enabling calibration over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..schemas import (
    SelfModelRecord,
    CalibrationDirection,
    UpdaterSource,
)
from .identity_store import IdentityStore
from ...utils.ids import generate_record_id
from ...utils.logging import get_logger
from ...utils.time import iso_now

logger = get_logger("agent.memory.identity.self_model")

# ---------------------------------------------------------------------------
# Window statistics dataclass
# ---------------------------------------------------------------------------

@dataclass
class WindowStats:
    """Rolling window statistics for the self-model."""
    avg_calibration_error: float | None = None
    success_rate: float | None = None
    confidence_margin: float | None = None
    overconfident_ratio: float | None = None
    coherence_index: float | None = None


# ---------------------------------------------------------------------------
# Self-Model manager
# ---------------------------------------------------------------------------

class SelfModel:
    """M14 Agent Self-Model manager.

    Records calibration data and computes window statistics.

    Args:
        store: An :class:`IdentityStore` instance.
        window_size: Number of recent episodes for window statistics.
        calibration_threshold: Below this error → 'calibrated'.
        coherence_cib_weight: Weight for CIB score in coherence index.
        coherence_cal_weight: Weight for calibration in coherence index.
    """

    def __init__(
        self,
        store: IdentityStore | None = None,
        window_size: int = 50,
        calibration_threshold: float = 0.05,
        coherence_cib_weight: float = 0.5,
        coherence_cal_weight: float = 0.5,
    ):
        self.store = store or IdentityStore()
        self.window_size = window_size
        self.calibration_threshold = calibration_threshold
        self.coherence_cib_weight = coherence_cib_weight
        self.coherence_cal_weight = coherence_cal_weight

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        episode_id: str,
        task_category: str,
        predicted_success: float,
        actual_success: float,
        predicted_effort: float | None = None,
        actual_effort: float | None = None,
        updated_by: UpdaterSource = UpdaterSource.OUTER_LOOP,
    ) -> SelfModelRecord:
        """Record a prediction vs actual result and compute calibration.

        Args:
            episode_id: The episode this prediction relates to.
            task_category: Task category (e.g. 'coding', 'translation').
            predicted_success: Predicted success score (0–1).
            actual_success: Actual success score (0–1).
            predicted_effort: Predicted effort (optional).
            actual_effort: Actual effort (optional).
            updated_by: Who is recording this (outer_loop / meta_loop).

        Returns:
            The created :class:`SelfModelRecord` with computed calibration
            and window statistics.
        """
        # Compute calibration
        cal_error, cal_direction = SelfModelRecord.compute_calibration(
            predicted_success, actual_success, self.calibration_threshold,
        )

        # Compute window stats
        window_stats = self.compute_window_stats(task_category)

        # Compute coherence index (M17)
        coherence = self._compute_coherence(window_stats)

        record = SelfModelRecord(
            record_id=generate_record_id("sm"),
            episode_id=episode_id,
            task_category=task_category,
            predicted_success=predicted_success,
            predicted_effort=predicted_effort,
            actual_success=actual_success,
            actual_effort=actual_effort,
            calibration_error=cal_error,
            calibration_direction=cal_direction,
            window_avg_calibration=window_stats.avg_calibration_error,
            window_success_rate=window_stats.success_rate,
            window_confidence_margin=window_stats.confidence_margin,
            coherence_index=coherence,
            timestamp=iso_now(),
            updated_by=updated_by,
        )

        self.store.insert_self_model(record)
        logger.info(
            f"Recorded self-model: episode={episode_id}, category={task_category}, "
            f"predicted={predicted_success:.2f}, actual={actual_success:.2f}, "
            f"cal_error={cal_error:.4f}, direction={cal_direction.value}"
        )
        return record

    # ------------------------------------------------------------------
    # Window statistics
    # ------------------------------------------------------------------

    def compute_window_stats(
        self,
        category: str | None = None,
    ) -> WindowStats:
        """Compute rolling window statistics from recent records.

        Args:
            category: Filter by task category. If ``None``, uses all.

        Returns:
            :class:`WindowStats` with aggregated metrics.
        """
        recent = self.store.get_recent_self_model(category=category, n=self.window_size)

        if not recent:
            return WindowStats()

        n = len(recent)
        cal_errors = [r.calibration_error for r in recent]
        actuals = [r.actual_success for r in recent]
        predicteds = [r.predicted_success for r in recent]
        overconfident_count = sum(
            1 for r in recent
            if r.calibration_direction == CalibrationDirection.OVERCONFIDENT
        )

        avg_cal = sum(cal_errors) / n
        success_rate = sum(actuals) / n
        confidence_margin = (sum(predicteds) / n) - (sum(actuals) / n)
        overconfident_ratio = overconfident_count / n

        return WindowStats(
            avg_calibration_error=round(avg_cal, 4),
            success_rate=round(success_rate, 4),
            confidence_margin=round(confidence_margin, 4),
            overconfident_ratio=round(overconfident_ratio, 4),
        )

    # ------------------------------------------------------------------
    # Coherence index (M17)
    # ------------------------------------------------------------------

    def _compute_coherence(
        self,
        window_stats: WindowStats,
        avg_cib_score: float | None = None,
    ) -> float | None:
        """Compute the coherence index (M17).

        C = w_cib × avg(CIB) + w_cal × (1 - avg(calibration_error))

        Args:
            window_stats: Window statistics.
            avg_cib_score: Average CIB score. If ``None``, uses 1.0
                (assumes constitution is satisfied).

        Returns:
            Coherence index in [0, 1], or ``None`` if insufficient data.
        """
        if window_stats.avg_calibration_error is None:
            return None

        cib = avg_cib_score if avg_cib_score is not None else 1.0
        cal_component = 1.0 - window_stats.avg_calibration_error

        coherence = (
            self.coherence_cib_weight * cib
            + self.coherence_cal_weight * cal_component
        )
        return round(max(0.0, min(1.0, coherence)), 4)

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_calibration_summary(
        self,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Return a summary of calibration state.

        Args:
            category: Filter by task category.

        Returns:
            Dict with calibration metrics and direction counts.
        """
        recent = self.store.get_recent_self_model(category=category, n=self.window_size)

        if not recent:
            return {
                "total_records": 0,
                "avg_calibration_error": None,
                "success_rate": None,
                "direction_counts": {},
                "coherence_index": None,
            }

        n = len(recent)
        direction_counts: dict[str, int] = {}
        for r in recent:
            d = r.calibration_direction.value
            direction_counts[d] = direction_counts.get(d, 0) + 1

        window_stats = self.compute_window_stats(category)
        coherence = self._compute_coherence(window_stats)

        return {
            "total_records": n,
            "avg_calibration_error": window_stats.avg_calibration_error,
            "success_rate": window_stats.success_rate,
            "confidence_margin": window_stats.confidence_margin,
            "overconfident_ratio": window_stats.overconfident_ratio,
            "direction_counts": direction_counts,
            "coherence_index": coherence,
        }

    def get_latest(self, category: str | None = None) -> SelfModelRecord | None:
        """Return the most recent self-model record."""
        records = self.store.list_self_model(category=category, limit=1)
        return records[0] if records else None