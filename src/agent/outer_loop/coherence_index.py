"""M17 Coherence Index — 전용 트래커, 트렌드 분석, 지속 하락 감지.

The coherence index (M17) measures how consistently the agent's identity
aligns with its constitution and self-model:

    C = 0.5 × avg(CIB_scores) + 0.5 × (1 - avg(calibration_error))

This module provides a dedicated tracker that:

    1. **Persists** coherence index history to disk (JSONL) — survives restarts
    2. **Computes trend** — slope, direction (rising/falling/stable)
    3. **Detects sustained decline** — C 지속 하락 (consecutive decreases)
    4. **Time-windowed analysis** — proper 7-day window for overgrowth detection
    5. **Statistics** — mean, std, min, max, range over any window

The tracker replaces the ad-hoc in-memory history in ``growth_regulator.py``
with a robust, persistent, and analytically richer implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..utils.logging import get_logger
from ..utils.serialization import write_jsonl
from ..utils.time import iso_now, parse_iso

logger = get_logger("agent.outer_loop.coherence_index")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CoherenceTrend(str, Enum):
    """Coherence index trend direction."""
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CoherenceRecord:
    """A single coherence index measurement.

    Attributes:
        timestamp: ISO 8601 timestamp of the measurement.
        coherence_index: The coherence index value (0–1).
        avg_cib_score: Average CIB score used in computation.
        calibration_error: Average calibration error used in computation.
        outer_loop_count: Outer loop cycle number when this was measured.
    """
    timestamp: str
    coherence_index: float
    avg_cib_score: float | None = None
    calibration_error: float | None = None
    outer_loop_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "coherence_index": self.coherence_index,
            "avg_cib_score": self.avg_cib_score,
            "calibration_error": self.calibration_error,
            "outer_loop_count": self.outer_loop_count,
        }


@dataclass
class TrendAnalysis:
    """Result of coherence index trend analysis.

    Attributes:
        trend: The detected :class:`CoherenceTrend`.
        slope: Rate of change per entry (positive = rising, negative = falling).
        delta: Total change over the analysis window (last - first).
        window_size: Number of entries used for analysis.
        sustained_decline_count: Consecutive entries where C decreased.
        sustained_decline: Whether a sustained decline is detected.
    """
    trend: CoherenceTrend = CoherenceTrend.UNKNOWN
    slope: float = 0.0
    delta: float = 0.0
    window_size: int = 0
    sustained_decline_count: int = 0
    sustained_decline: bool = False


# ---------------------------------------------------------------------------
# Coherence Index Tracker (M17)
# ---------------------------------------------------------------------------

class CoherenceIndexTracker:
    """Track and analyse the M17 coherence index over time.

    Args:
        history_path: Path to the JSONL file for persistence.
        max_history: Maximum number of records to keep in memory/disk.
        stagnation_window: Minimum entries for stagnation detection (default 50).
        stagnation_delta: Coherence change threshold for stagnation (default 0.01).
        overgrowth_days: Days window for overgrowth detection (default 7).
        overgrowth_coherence_rise: Coherence rise threshold for overgrowth (default 0.2).
        sustained_decline_threshold: Consecutive falling entries for sustained decline (default 5).
        slope_threshold: Minimum |slope| to classify as rising/falling (default 0.001).
    """

    def __init__(
        self,
        history_path: str = "data/memory/audit/coherence_history.jsonl",
        max_history: int = 500,
        stagnation_window: int = 50,
        stagnation_delta: float = 0.01,
        overgrowth_days: int = 7,
        overgrowth_coherence_rise: float = 0.2,
        sustained_decline_threshold: int = 5,
        slope_threshold: float = 0.001,
    ):
        self.history_path = Path(history_path)
        self.max_history = max_history
        self.stagnation_window = stagnation_window
        self.stagnation_delta = stagnation_delta
        self.overgrowth_days = overgrowth_days
        self.overgrowth_coherence_rise = overgrowth_coherence_rise
        self.sustained_decline_threshold = sustained_decline_threshold
        self.slope_threshold = slope_threshold

        self._history: list[CoherenceRecord] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load coherence history from disk."""
        if not self.history_path.exists():
            return
        try:
            from ..utils.serialization import read_jsonl_all
            records = read_jsonl_all(self.history_path)
            self._history = [
                CoherenceRecord(
                    timestamp=r.get("timestamp", ""),
                    coherence_index=float(r.get("coherence_index", 0.0)),
                    avg_cib_score=r.get("avg_cib_score"),
                    calibration_error=r.get("calibration_error"),
                    outer_loop_count=r.get("outer_loop_count", 0),
                )
                for r in records
            ]
            logger.info(f"Loaded {len(self._history)} coherence records from {self.history_path}")
        except Exception as e:
            logger.warning(f"Failed to load coherence history: {e}")
            self._history = []

    def _save(self) -> None:
        """Save coherence history to disk."""
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            # Write all records (overwrite, not append, to enforce max_history)
            import json
            lines = [
                json.dumps(r.to_dict(), ensure_ascii=False)
                for r in self._history[-self.max_history:]
            ]
            self.history_path.write_text("\n".join(lines) + ("\n" if lines else ""))
        except Exception as e:
            logger.warning(f"Failed to save coherence history: {e}")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        coherence_index: float,
        avg_cib_score: float | None = None,
        calibration_error: float | None = None,
        outer_loop_count: int = 0,
        timestamp: str | None = None,
    ) -> CoherenceRecord:
        """Record a new coherence index measurement.

        Args:
            coherence_index: The coherence index value (0–1).
            avg_cib_score: Average CIB score used in computation.
            calibration_error: Average calibration error used in computation.
            outer_loop_count: Outer loop cycle number.
            timestamp: ISO 8601 timestamp. If None, uses current time.

        Returns:
            The created :class:`CoherenceRecord`.
        """
        record = CoherenceRecord(
            timestamp=timestamp or iso_now(),
            coherence_index=round(coherence_index, 4),
            avg_cib_score=round(avg_cib_score, 4) if avg_cib_score is not None else None,
            calibration_error=round(calibration_error, 4) if calibration_error is not None else None,
            outer_loop_count=outer_loop_count,
        )
        self._history.append(record)

        # Trim to max_history
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]

        self._save()
        logger.info(
            f"Recorded coherence index: C={coherence_index:.4f} "
            f"(loop #{outer_loop_count}, total records={len(self._history)})"
        )
        return record

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    @property
    def history(self) -> list[CoherenceRecord]:
        """Return the full coherence history (oldest first)."""
        return list(self._history)

    def latest(self) -> CoherenceRecord | None:
        """Return the most recent coherence record, or None if empty."""
        return self._history[-1] if self._history else None

    def get_recent(self, n: int = 50) -> list[CoherenceRecord]:
        """Return the most recent *n* records (oldest first within the window)."""
        return self._history[-n:] if self._history else []

    def get_within_days(self, days: int) -> list[CoherenceRecord]:
        """Return records from within the last *days* days.

        Args:
            days: Number of days to look back.

        Returns:
            List of :class:`CoherenceRecord` within the time window (oldest first).
        """
        if not self._history:
            return []

        from datetime import datetime, timedelta, timezone

        try:
            latest_ts = parse_iso(self._history[-1].timestamp)
            cutoff = latest_ts - timedelta(days=days)
        except Exception:
            # Fallback: just return recent records
            return self._history[-100:]

        result: list[CoherenceRecord] = []
        for record in self._history:
            try:
                ts = parse_iso(record.timestamp)
                if ts >= cutoff:
                    result.append(record)
            except Exception:
                continue

        return result

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def compute_stats(self, records: list[CoherenceRecord] | None = None) -> dict[str, float | None]:
        """Compute statistics over a set of records.

        Args:
            records: Records to analyse. If None, uses all history.

        Returns:
            Dict with mean, std, min, max, range.
        """
        if records is None:
            records = self._history

        if not records:
            return {"mean": None, "std": None, "min": None, "max": None, "range": None}

        values = [r.coherence_index for r in records]
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        std = variance ** 0.5

        return {
            "mean": round(mean, 4),
            "std": round(std, 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "range": round(max(values) - min(values), 4),
        }

    # ------------------------------------------------------------------
    # Trend analysis
    # ------------------------------------------------------------------

    def analyse_trend(self, window: int | None = None) -> TrendAnalysis:
        """Analyse the trend of the coherence index over a recent window.

        Args:
            window: Number of recent records to analyse. If None, uses
                ``stagnation_window``.

        Returns:
            :class:`TrendAnalysis` with trend direction, slope, and
            sustained decline detection.
        """
        n = window or self.stagnation_window
        records = self.get_recent(n)

        if len(records) < 2:
            return TrendAnalysis(window_size=len(records))

        values = [r.coherence_index for r in records]
        n_vals = len(values)

        # Compute slope (simple linear: (last - first) / (n - 1))
        delta = values[-1] - values[0]
        slope = delta / (n_vals - 1) if n_vals > 1 else 0.0

        # Determine trend
        if abs(slope) < self.slope_threshold:
            trend = CoherenceTrend.STABLE
        elif slope > 0:
            trend = CoherenceTrend.RISING
        else:
            trend = CoherenceTrend.FALLING

        # Detect sustained decline (consecutive decreases)
        sustained_count = 0
        max_sustained = 0
        for i in range(1, n_vals):
            if values[i] < values[i - 1]:
                sustained_count += 1
                max_sustained = max(max_sustained, sustained_count)
            else:
                sustained_count = 0

        sustained_decline = max_sustained >= self.sustained_decline_threshold

        return TrendAnalysis(
            trend=trend,
            slope=round(slope, 6),
            delta=round(delta, 4),
            window_size=n_vals,
            sustained_decline_count=max_sustained,
            sustained_decline=sustained_decline,
        )

    # ------------------------------------------------------------------
    # Signal detection (for M16 growth regulator)
    # ------------------------------------------------------------------

    def detect_stagnation(self) -> bool:
        """Detect stagnation: coherence range < threshold over a long window.

        Returns:
            True if the coherence index has barely changed (range <
            ``stagnation_delta``) over at least ``stagnation_window`` entries.
        """
        records = self.get_recent(self.stagnation_window)
        if len(records) < self.stagnation_window:
            return False

        stats = self.compute_stats(records)
        coherence_range = stats.get("range", 0.0)
        if coherence_range is None:
            return False

        is_stagnant = coherence_range < self.stagnation_delta
        if is_stagnant:
            logger.warning(
                f"Stagnation detected: coherence range {coherence_range:.4f} "
                f"< {self.stagnation_delta} over {len(records)} entries"
            )
        return is_stagnant

    def detect_overgrowth(self, current_coherence: float | None = None) -> dict[str, Any]:
        """Detect overgrowth: coherence rises >= threshold within days window.

        Uses a proper time-windowed comparison: current coherence vs the
        coherence from ``overgrowth_days`` ago.

        Args:
            current_coherence: Current coherence index. If None, uses the
                latest recorded value.

        Returns:
            Dict with 'detected' (bool), 'rise' (float), 'details' (dict).
        """
        if current_coherence is None:
            latest_record = self.latest()
            if latest_record is None:
                return {"detected": False, "rise": 0.0, "details": {}}
            current_coherence = latest_record.coherence_index

        # Get records from within the overgrowth_days window
        window_records = self.get_within_days(self.overgrowth_days)

        if len(window_records) < 2:
            return {"detected": False, "rise": 0.0, "details": {"window_size": len(window_records)}}

        # Compare current vs the earliest in the time window
        earliest_coherence = window_records[0].coherence_index
        rise = current_coherence - earliest_coherence

        detected = rise >= self.overgrowth_coherence_rise
        if detected:
            logger.warning(
                f"Overgrowth detected: coherence rose by {rise:.4f} "
                f"({earliest_coherence:.4f} → {current_coherence:.4f}) "
                f"within {self.overgrowth_days} days"
            )

        return {
            "detected": detected,
            "rise": round(rise, 4),
            "details": {
                "earliest_coherence": round(earliest_coherence, 4),
                "current_coherence": round(current_coherence, 4),
                "window_days": self.overgrowth_days,
                "window_size": len(window_records),
                "threshold": self.overgrowth_coherence_rise,
            },
        }

    def detect_sustained_decline(self) -> dict[str, Any]:
        """Detect sustained decline: C 지속 하락.

        Checks if the coherence index has been consistently decreasing over
        a number of consecutive entries, indicating a potential identity crisis.

        Returns:
            Dict with 'detected' (bool), 'consecutive_decreases' (int),
            'total_decline' (float), 'details' (dict).
        """
        trend = self.analyse_trend()

        return {
            "detected": trend.sustained_decline,
            "consecutive_decreases": trend.sustained_decline_count,
            "total_decline": trend.delta if trend.delta < 0 else 0.0,
            "details": {
                "trend": trend.trend.value,
                "slope": trend.slope,
                "delta": trend.delta,
                "window_size": trend.window_size,
                "threshold": self.sustained_decline_threshold,
            },
        }

    # ------------------------------------------------------------------
    # Coherence index computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute(
        avg_cib_score: float | None = None,
        calibration_error: float | None = None,
        cib_weight: float = 0.5,
        calibration_weight: float = 0.5,
    ) -> float | None:
        """Compute the coherence index (M17).

        C = w_cib × avg(CIB) + w_cal × (1 - avg(calibration_error))

        Args:
            avg_cib_score: Average CIB score (0–1). If None, defaults to 1.0.
            calibration_error: Average calibration error (0–1). If None,
                defaults to 0.0 (perfect calibration).
            cib_weight: Weight for CIB component (default 0.5).
            calibration_weight: Weight for calibration component (default 0.5).

        Returns:
            Coherence index in [0, 1], or None if both inputs are None.
        """
        if avg_cib_score is None and calibration_error is None:
            return None

        cib_component = avg_cib_score if avg_cib_score is not None else 1.0
        cal_component = (1.0 - calibration_error) if calibration_error is not None else 1.0

        coherence = cib_weight * cib_component + calibration_weight * cal_component
        return round(max(0.0, min(1.0, coherence)), 4)