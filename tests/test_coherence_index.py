"""Unit tests for Phase 3.3 — M17 Coherence Index Tracker.

Covers:
    - coherence_index.py: CoherenceIndexTracker (record, persist, trend, decline)
    - coherence_index.py: Static compute() method
    - growth_regulator.py: Integration with CoherenceIndexTracker
    - outer_loop.py: Coherence recording and trend in audit log
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# CoherenceIndexTracker — Basic operations
# ===========================================================================

class TestCoherenceIndexTrackerBasic:
    def test_empty_tracker(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "coherence.jsonl"),
        )
        assert tracker.latest() is None
        assert len(tracker.history) == 0

    def test_record_and_retrieve(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker, CoherenceRecord

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "coherence.jsonl"),
        )
        record = tracker.record(
            coherence_index=0.85,
            avg_cib_score=0.97,
            calibration_error=0.05,
            outer_loop_count=1,
        )

        assert isinstance(record, CoherenceRecord)
        assert record.coherence_index == 0.85
        assert tracker.latest().coherence_index == 0.85
        assert len(tracker.history) == 1

    def test_persistence(self, tmp_path):
        """Verify that records are saved to disk and loaded on restart."""
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        path = str(tmp_path / "coherence.jsonl")

        # First instance: record some data
        tracker1 = CoherenceIndexTracker(history_path=path)
        tracker1.record(coherence_index=0.8, outer_loop_count=1)
        tracker1.record(coherence_index=0.82, outer_loop_count=2)
        assert len(tracker1.history) == 2

        # Second instance: should load from disk
        tracker2 = CoherenceIndexTracker(history_path=path)
        assert len(tracker2.history) == 2
        assert tracker2.latest().coherence_index == 0.82

    def test_max_history_limit(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "coherence.jsonl"),
            max_history=5,
        )
        for i in range(10):
            tracker.record(coherence_index=0.5 + i * 0.01, outer_loop_count=i)

        assert len(tracker.history) == 5
        # Latest should be the most recent
        assert tracker.latest().coherence_index == 0.59

    def test_get_recent(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "coherence.jsonl"),
        )
        for i in range(20):
            tracker.record(coherence_index=0.5 + i * 0.01, outer_loop_count=i)

        recent = tracker.get_recent(n=5)
        assert len(recent) == 5
        # Oldest first within the window — values 0.65, 0.66, 0.67, 0.68, 0.69
        assert recent[0].coherence_index == 0.65
        assert recent[-1].coherence_index == 0.69


# ===========================================================================
# CoherenceIndexTracker — Statistics
# ===========================================================================

class TestCoherenceIndexTrackerStats:
    def test_compute_stats_empty(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(history_path=str(tmp_path / "c.jsonl"))
        stats = tracker.compute_stats()
        assert stats["mean"] is None

    def test_compute_stats_with_data(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(history_path=str(tmp_path / "c.jsonl"))
        for v in [0.8, 0.82, 0.85, 0.83, 0.81]:
            tracker.record(coherence_index=v, outer_loop_count=0)

        stats = tracker.compute_stats()
        assert stats["mean"] is not None
        assert stats["min"] == 0.8
        assert stats["max"] == 0.85
        assert stats["range"] == 0.05
        assert stats["std"] is not None
        assert stats["std"] > 0


# ===========================================================================
# CoherenceIndexTracker — Trend analysis
# ===========================================================================

class TestCoherenceIndexTrackerTrend:
    def test_trend_rising(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker, CoherenceTrend

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            slope_threshold=0.001,
        )
        for i in range(10):
            tracker.record(coherence_index=0.5 + i * 0.02, outer_loop_count=i)

        trend = tracker.analyse_trend()
        assert trend.trend == CoherenceTrend.RISING
        assert trend.slope > 0
        assert trend.delta > 0

    def test_trend_falling(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker, CoherenceTrend

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            slope_threshold=0.001,
        )
        for i in range(10):
            tracker.record(coherence_index=0.9 - i * 0.02, outer_loop_count=i)

        trend = tracker.analyse_trend()
        assert trend.trend == CoherenceTrend.FALLING
        assert trend.slope < 0
        assert trend.delta < 0

    def test_trend_stable(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker, CoherenceTrend

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            slope_threshold=0.001,
        )
        for i in range(10):
            tracker.record(coherence_index=0.80 + (i % 3) * 0.0001, outer_loop_count=i)

        trend = tracker.analyse_trend()
        assert trend.trend == CoherenceTrend.STABLE

    def test_trend_unknown_insufficient_data(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker, CoherenceTrend

        tracker = CoherenceIndexTracker(history_path=str(tmp_path / "c.jsonl"))
        trend = tracker.analyse_trend()
        assert trend.trend == CoherenceTrend.UNKNOWN

    def test_sustained_decline_detected(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            sustained_decline_threshold=5,
        )
        # 7 consecutive decreases
        for i in range(7):
            tracker.record(coherence_index=0.9 - i * 0.01, outer_loop_count=i)

        trend = tracker.analyse_trend()
        assert trend.sustained_decline is True
        assert trend.sustained_decline_count >= 5

    def test_sustained_decline_not_detected(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            sustained_decline_threshold=5,
        )
        # Alternating values — no sustained decline
        for i in range(10):
            tracker.record(coherence_index=0.8 + (i % 2) * 0.01, outer_loop_count=i)

        trend = tracker.analyse_trend()
        assert trend.sustained_decline is False

    def test_detect_sustained_decline_method(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            sustained_decline_threshold=5,
        )
        for i in range(8):
            tracker.record(coherence_index=0.85 - i * 0.005, outer_loop_count=i)

        result = tracker.detect_sustained_decline()
        assert result["detected"] is True
        assert result["consecutive_decreases"] >= 5


# ===========================================================================
# CoherenceIndexTracker — Signal detection
# ===========================================================================

class TestCoherenceIndexTrackerSignals:
    def test_detect_stagnation_true(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            stagnation_window=10,
            stagnation_delta=0.01,
        )
        # 10 records with barely changing coherence
        for i in range(10):
            tracker.record(coherence_index=0.80 + (i % 3) * 0.001, outer_loop_count=i)

        assert tracker.detect_stagnation() is True

    def test_detect_stagnation_false(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            stagnation_window=10,
            stagnation_delta=0.01,
        )
        # 10 records with significant change
        for i in range(10):
            tracker.record(coherence_index=0.5 + i * 0.02, outer_loop_count=i)

        assert tracker.detect_stagnation() is False

    def test_detect_stagnation_insufficient_data(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            stagnation_window=50,
        )
        for i in range(10):
            tracker.record(coherence_index=0.8, outer_loop_count=i)

        assert tracker.detect_stagnation() is False  # not enough data

    def test_detect_overgrowth_true(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            overgrowth_days=7,
            overgrowth_coherence_rise=0.2,
        )
        # Record with timestamps spanning 7 days, rising by 0.25
        from datetime import datetime, timedelta, timezone
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        tracker.record(
            coherence_index=0.5,
            outer_loop_count=1,
            timestamp=base.isoformat(),
        )
        tracker.record(
            coherence_index=0.75,
            outer_loop_count=2,
            timestamp=(base + timedelta(days=5)).isoformat(),
        )

        result = tracker.detect_overgrowth(current_coherence=0.75)
        assert result["detected"] is True
        assert result["rise"] >= 0.2

    def test_detect_overgrowth_false(self, tmp_path):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            overgrowth_days=7,
            overgrowth_coherence_rise=0.2,
        )
        tracker.record(coherence_index=0.8, outer_loop_count=1)
        tracker.record(coherence_index=0.82, outer_loop_count=2)

        result = tracker.detect_overgrowth(current_coherence=0.82)
        assert result["detected"] is False
        assert result["rise"] < 0.2


# ===========================================================================
# CoherenceIndexTracker — Static compute
# ===========================================================================

class TestCoherenceIndexCompute:
    def test_compute_with_both_components(self):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        # C = 0.5 * 0.96 + 0.5 * (1 - 0.04) = 0.48 + 0.48 = 0.96
        c = CoherenceIndexTracker.compute(
            avg_cib_score=0.96,
            calibration_error=0.04,
        )
        assert c == 0.96

    def test_compute_with_cib_only(self):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        # CIB=0.9, cal=None → cal_component=1.0
        # C = 0.5 * 0.9 + 0.5 * 1.0 = 0.95
        c = CoherenceIndexTracker.compute(avg_cib_score=0.9)
        assert c == 0.95

    def test_compute_with_cal_only(self):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        # CIB=None → cib_component=1.0, cal=0.1
        # C = 0.5 * 1.0 + 0.5 * 0.9 = 0.95
        c = CoherenceIndexTracker.compute(calibration_error=0.1)
        assert c == 0.95

    def test_compute_with_none(self):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        c = CoherenceIndexTracker.compute()
        assert c is None

    def test_compute_custom_weights(self):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        # C = 0.7 * 0.9 + 0.3 * (1 - 0.1) = 0.63 + 0.27 = 0.9
        c = CoherenceIndexTracker.compute(
            avg_cib_score=0.9,
            calibration_error=0.1,
            cib_weight=0.7,
            calibration_weight=0.3,
        )
        assert c == 0.9

    def test_compute_clamped(self):
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        # Extreme values should be clamped to [0, 1]
        c = CoherenceIndexTracker.compute(
            avg_cib_score=1.0,
            calibration_error=0.0,
        )
        assert c == 1.0

        c = CoherenceIndexTracker.compute(
            avg_cib_score=0.0,
            calibration_error=1.0,
        )
        assert c == 0.0


# ===========================================================================
# Growth Regulator integration with CoherenceIndexTracker
# ===========================================================================

class TestGrowthRegulatorWithTracker:
    def test_regulator_uses_tracker_for_stagnation(self, tmp_path):
        from agent.outer_loop.growth_regulator import (
            GrowthRateRegulator, GrowthSignal,
        )
        from agent.outer_loop.coherence_index import CoherenceIndexTracker

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            stagnation_window=10,
            stagnation_delta=0.01,
        )
        # Fill with stagnant data
        for i in range(10):
            tracker.record(coherence_index=0.80 + (i % 3) * 0.001, outer_loop_count=i)

        reg = GrowthRateRegulator(
            stagnation_window=10,
            stagnation_coherence_delta=0.01,
            coherence_tracker=tracker,
        )

        result = reg.regulate(coherence_index=0.801, timestamp="2025-01-11T00:00:00Z")
        assert result.signal == GrowthSignal.STAGNATION

    def test_regulator_uses_tracker_for_overgrowth(self, tmp_path):
        from agent.outer_loop.growth_regulator import (
            GrowthRateRegulator, GrowthSignal,
        )
        from agent.outer_loop.coherence_index import CoherenceIndexTracker
        from datetime import datetime, timedelta, timezone

        tracker = CoherenceIndexTracker(
            history_path=str(tmp_path / "c.jsonl"),
            overgrowth_days=7,
            overgrowth_coherence_rise=0.2,
        )
        base = datetime(2025, 1, 1, tzinfo=timezone.utc)
        tracker.record(coherence_index=0.5, outer_loop_count=1, timestamp=base.isoformat())
        tracker.record(
            coherence_index=0.6,
            outer_loop_count=2,
            timestamp=(base + timedelta(days=3)).isoformat(),
        )

        reg = GrowthRateRegulator(
            overgrowth_days=7,
            overgrowth_coherence_rise=0.2,
            coherence_tracker=tracker,
        )

        result = reg.regulate(coherence_index=0.75, timestamp=(base + timedelta(days=5)).isoformat())
        assert result.signal == GrowthSignal.OVERGROWTH

    def test_regulator_fallback_without_tracker(self, tmp_path):
        """Verify the regulator still works without a tracker (backward compat)."""
        from agent.outer_loop.growth_regulator import (
            GrowthRateRegulator, GrowthSignal,
        )

        reg = GrowthRateRegulator(
            crash_window=1,
            crash_delta_threshold=0.01,
        )
        # Set history to [0.9] — regulate() will append the current success_rate
        # making it [0.9, 0.5] → drop = 0.4 >= 0.01 → crash
        reg._success_rate_history = [0.9]
        result = reg.regulate(
            aggregation_result=type("A", (), {"episode_count": 40, "success_rate": 0.5})(),
            coherence_index=0.8,
            timestamp="2025-01-01T00:00:00Z",
        )
        # Should detect crash (no tracker needed for crash)
        assert result.signal == GrowthSignal.CRASH


# ===========================================================================
# Outer Loop integration with CoherenceIndexTracker
# ===========================================================================

class TestOuterLoopCoherenceIntegration:
    def test_coherence_recorded_in_result(self, tmp_path):
        from agent.outer_loop import OuterLoop

        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        result = outer.run()

        # coherence_record may be None if no data, but the field should exist
        assert hasattr(result, "coherence_record")

    def test_coherence_history_persisted(self, tmp_path):
        """Verify that coherence history is persisted when coherence is recorded."""
        from agent.outer_loop import OuterLoop

        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        # Manually record coherence (simulating data being available)
        outer.coherence_tracker.record(coherence_index=0.85, outer_loop_count=1)
        outer.coherence_tracker.record(coherence_index=0.87, outer_loop_count=2)

        # Check coherence history file exists
        coherence_path = tmp_path / "coherence_history.jsonl"
        assert coherence_path.exists()

    def test_audit_log_includes_trend(self, tmp_path):
        from agent.outer_loop import OuterLoop

        audit_path = tmp_path / "audit.jsonl"
        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(audit_path),
        )
        outer.run()

        entry = json.loads(audit_path.read_text().strip().split("\n")[0])
        assert "coherence_trend" in entry

    def test_coherence_tracker_loaded_on_restart(self, tmp_path):
        from agent.outer_loop import OuterLoop

        state_path = str(tmp_path / "state.json")
        audit_path = str(tmp_path / "audit.jsonl")

        # First instance — manually record coherence
        outer1 = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=audit_path,
        )
        outer1.coherence_tracker.record(coherence_index=0.85, outer_loop_count=1)
        outer1.coherence_tracker.record(coherence_index=0.87, outer_loop_count=2)
        assert len(outer1.coherence_tracker.history) == 2

        # Second instance — should load coherence history from disk
        outer2 = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=audit_path,
        )
        assert len(outer2.coherence_tracker.history) == 2