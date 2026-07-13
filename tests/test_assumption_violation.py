"""Unit tests for Phase 4.3 — Mathematical Assumption Violation Detection.

Covers:
    - assumption_violation.py: Bimodal detection, CIB variance, recommended actions
    - outer_loop.py: Integration with outer loop (violation detection in Step 7)
    - meta_loop.py: Emergency inspection with assumption re-verification
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# AssumptionViolationDetector — Bimodal detection
# ===========================================================================

class TestBimodalDetection:
    def test_no_violation_unimodal(self, tmp_path):
        """Unimodal distribution should not trigger bimodal detection."""
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(min_samples=10)
        # Unimodal: all scores around 0.8
        scores = [0.78, 0.79, 0.80, 0.81, 0.82, 0.80, 0.79, 0.81, 0.78, 0.82,
                  0.80, 0.79, 0.81, 0.80, 0.78, 0.82, 0.79, 0.81, 0.80, 0.79]
        result = detector.detect(success_scores=scores, cib_scores=None)

        assert result.bimodal_detected is False
        assert result.detected is False

    def test_bimodal_detected(self, tmp_path):
        """Bimodal distribution should trigger detection."""
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector, ViolationType

        detector = AssumptionViolationDetector(
            bimodal_gap_threshold=0.3,
            bimodal_min_cluster_size=5,
            min_samples=10,
        )
        # Bimodal: two clusters around 0.3 and 0.8
        scores = [0.3, 0.32, 0.28, 0.31, 0.29, 0.30, 0.33, 0.27, 0.30, 0.31,
                  0.80, 0.82, 0.78, 0.81, 0.79, 0.80, 0.83, 0.77, 0.80, 0.81]
        result = detector.detect(success_scores=scores, cib_scores=None)

        assert result.bimodal_detected is True
        assert result.detected is True
        assert result.violation_type == ViolationType.BIMODAL_SUCCESS
        assert len(result.success_rate_clusters) == 2
        assert result.success_rate_clusters[0] < 0.5
        assert result.success_rate_clusters[1] > 0.5

    def test_insufficient_samples(self, tmp_path):
        """Too few samples should not trigger detection."""
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(min_samples=20)
        scores = [0.3, 0.8, 0.3, 0.8]  # Only 4 samples
        result = detector.detect(success_scores=scores, cib_scores=None)

        assert result.bimodal_detected is False
        assert result.detected is False

    def test_small_gap_not_bimodal(self, tmp_path):
        """Small gap between clusters should not trigger bimodal."""
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(
            bimodal_gap_threshold=0.3,
            min_samples=10,
        )
        # Clusters close together (gap < 0.3)
        scores = [0.65, 0.66, 0.64, 0.67, 0.63, 0.66, 0.65, 0.64, 0.66, 0.65,
                  0.72, 0.73, 0.71, 0.74, 0.70, 0.73, 0.72, 0.71, 0.73, 0.72]
        result = detector.detect(success_scores=scores, cib_scores=None)

        assert result.bimodal_detected is False


# ===========================================================================
# AssumptionViolationDetector — CIB variance
# ===========================================================================

class TestCIBVariance:
    def test_no_violation_stable_cib(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(
            cib_variance_threshold=0.02,
            min_samples=10,
        )
        # Stable CIB scores
        cib_scores = [0.97, 0.971, 0.969, 0.97, 0.972, 0.968, 0.97, 0.971, 0.969, 0.97,
                     0.971, 0.969, 0.97, 0.972, 0.968, 0.97, 0.971, 0.969, 0.97, 0.971]
        result = detector.detect(success_scores=None, cib_scores=cib_scores)

        assert result.cib_variance_exceeded is False
        assert result.detected is False

    def test_cib_variance_exceeded(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector, ViolationType

        detector = AssumptionViolationDetector(
            cib_variance_threshold=0.001,
            min_samples=10,
        )
        # Highly variable CIB scores
        cib_scores = [0.5, 0.9, 0.4, 0.95, 0.3, 0.85, 0.45, 0.92, 0.35, 0.88,
                      0.42, 0.97, 0.38, 0.9, 0.5, 0.93, 0.33, 0.87, 0.48, 0.95]
        result = detector.detect(success_scores=None, cib_scores=cib_scores)

        assert result.cib_variance_exceeded is True
        assert result.detected is True
        assert result.violation_type == ViolationType.CIB_VARIANCE_EXCEEDED
        assert result.cib_variance > 0.001


# ===========================================================================
# AssumptionViolationDetector — Combined violations
# ===========================================================================

class TestCombinedViolations:
    def test_combined_violation(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector, ViolationType

        detector = AssumptionViolationDetector(
            bimodal_gap_threshold=0.3,
            cib_variance_threshold=0.001,
            min_samples=10,
        )
        # Both bimodal success and high CIB variance
        success_scores = [0.3, 0.32, 0.28, 0.31, 0.29, 0.30, 0.33, 0.27, 0.30, 0.31,
                          0.80, 0.82, 0.78, 0.81, 0.79, 0.80, 0.83, 0.77, 0.80, 0.81]
        cib_scores = [0.5, 0.9, 0.4, 0.95, 0.3, 0.85, 0.45, 0.92, 0.35, 0.88,
                      0.42, 0.97, 0.38, 0.9, 0.5, 0.93, 0.33, 0.87, 0.48, 0.95]
        result = detector.detect(success_scores=success_scores, cib_scores=cib_scores)

        assert result.detected is True
        assert result.violation_type == ViolationType.COMBINED
        assert result.bimodal_detected is True
        assert result.cib_variance_exceeded is True

    def test_no_violation(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector, ViolationType

        detector = AssumptionViolationDetector(min_samples=10)
        # Stable scores
        success_scores = [0.80, 0.81, 0.79, 0.80, 0.82, 0.78, 0.80, 0.81, 0.79, 0.80,
                           0.81, 0.79, 0.80, 0.82, 0.78, 0.80, 0.81, 0.79, 0.80, 0.81]
        cib_scores = [0.97, 0.971, 0.969, 0.97, 0.972, 0.968, 0.97, 0.971, 0.969, 0.97,
                      0.971, 0.969, 0.97, 0.972, 0.968, 0.97, 0.971, 0.969, 0.97, 0.971]
        result = detector.detect(success_scores=success_scores, cib_scores=cib_scores)

        assert result.detected is False
        assert result.violation_type == ViolationType.NONE


# ===========================================================================
# AssumptionViolationDetector — Recommended actions
# ===========================================================================

class TestRecommendedActions:
    def test_actions_for_bimodal(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(
            bimodal_gap_threshold=0.3,
            min_samples=10,
        )
        scores = [0.3, 0.32, 0.28, 0.31, 0.29, 0.30, 0.33, 0.27, 0.30, 0.31,
                  0.80, 0.82, 0.78, 0.81, 0.79, 0.80, 0.83, 0.77, 0.80, 0.81]
        result = detector.detect(success_scores=scores, cib_scores=None)
        actions = detector.get_recommended_actions(result)

        assert len(actions) >= 2  # elevate CIB + emergency meta loop
        assert any(a["action"] == "elevate_cib_threshold" for a in actions)
        assert any(a["action"] == "request_emergency_meta_loop" for a in actions)
        assert any(a["action"] == "flag_non_convex_loss" for a in actions)

    def test_actions_for_cib_variance(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(
            cib_variance_threshold=0.001,
            min_samples=10,
        )
        cib_scores = [0.5, 0.9, 0.4, 0.95, 0.3, 0.85, 0.45, 0.92, 0.35, 0.88,
                      0.42, 0.97, 0.38, 0.9, 0.5, 0.93, 0.33, 0.87, 0.48, 0.95]
        result = detector.detect(success_scores=None, cib_scores=cib_scores)
        actions = detector.get_recommended_actions(result)

        assert any(a["action"] == "elevate_cib_threshold" for a in actions)
        assert any(a["action"] == "flag_cib_instability" for a in actions)

    def test_no_actions_when_no_violation(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(min_samples=10)
        result = detector.detect(success_scores=None, cib_scores=None)
        actions = detector.get_recommended_actions(result)

        assert len(actions) == 0

    def test_recommended_cib_threshold(self, tmp_path):
        from agent.meta_loop.assumption_violation import AssumptionViolationDetector

        detector = AssumptionViolationDetector(
            normal_cib_threshold=0.95,
            emergency_cib_threshold=0.97,
            bimodal_gap_threshold=0.3,
            min_samples=10,
        )
        scores = [0.3, 0.32, 0.28, 0.31, 0.29, 0.30, 0.33, 0.27, 0.30, 0.31,
                  0.80, 0.82, 0.78, 0.81, 0.79, 0.80, 0.83, 0.77, 0.80, 0.81]
        result = detector.detect(success_scores=scores, cib_scores=None)

        assert result.recommended_cib_threshold == 0.97
        assert result.emergency_meta_loop_required is True


# ===========================================================================
# Outer Loop integration
# ===========================================================================

class TestOuterLoopViolationIntegration:
    def test_violation_result_in_result(self, tmp_path):
        """Verify that violation_result is included in OuterLoopResult."""
        from agent.outer_loop import OuterLoop

        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        result = outer.run()

        assert hasattr(result, "violation_result")
        assert result.violation_result is not None

    def test_audit_log_includes_violation(self, tmp_path):
        """Verify that audit log includes assumption_violation field."""
        from agent.outer_loop import OuterLoop

        audit_path = tmp_path / "audit.jsonl"
        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(audit_path),
        )
        outer.run()

        entry = json.loads(audit_path.read_text().strip().split("\n")[0])
        assert "assumption_violation" in entry
        assert entry["assumption_violation"] == "none"  # No data → no violation


# ===========================================================================
# Meta Loop integration
# ===========================================================================

class TestMetaLoopViolationIntegration:
    def test_emergency_inspection_includes_assumption_scenario(self, tmp_path):
        """Verify that emergency inspection proposes assumption re-verification."""
        from agent.meta_loop import MetaLoop

        meta = MetaLoop(
            memory_manager=None,
            state_path=str(tmp_path / "meta_state.json"),
            log_path=str(tmp_path / "meta_log.jsonl"),
            proposal_log_path=str(tmp_path / "proposals.jsonl"),
            hitl_audit_path=str(tmp_path / "hitl_audit.jsonl"),
        )
        result = meta.run(trigger_type="emergency_inspection")

        # Should include a K-Scenario for assumption violation
        scenario_proposals = [
            p for p in result.proposals_created
            if "assumption" in p.title.lower() or "assumption" in p.description.lower()
        ]
        assert len(scenario_proposals) >= 1

    def test_violation_triggers_stagnation_flag(self, tmp_path):
        """Verify that assumption violation triggers meta loop via stagnation flag."""
        from agent.outer_loop.meta_trigger import MetaTrigger, TriggerType

        # When violation is detected, stagnation_detected=True
        trigger = MetaTrigger(episode_threshold=1000, outer_loop_threshold=100)
        result = trigger.evaluate(
            episode_count=50,
            outer_loop_count=5,
            stagnation_detected=True,  # Violation sets this flag
        )

        assert result.triggered is True
        assert result.trigger_type == TriggerType.STAGNATION_RESPONSE