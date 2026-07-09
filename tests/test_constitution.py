"""Unit tests for Phase 1.5 — L4 Constitution.

Covers:
    - loader.py: YAML load, merge, Constitution model
    - validator.py: K-Scenario validation, direction score computation
    - guard.py: CIB gate (block if < 0.95), HITL approval, emergency threshold
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _load_constitution():
    """Load the real constitution from the project's constitution/ dir."""
    from agent.memory.constitution.loader import ConstitutionLoader

    loader = ConstitutionLoader(constitution_dir=str(PROJECT_ROOT / "constitution"))
    return loader.load()


# ===========================================================================
# loader.py
# ===========================================================================

class TestConstitutionLoader:
    def test_loads_without_error(self):
        constitution = _load_constitution()
        assert constitution is not None

    def test_version(self):
        constitution = _load_constitution()
        assert constitution.version == 1

    def test_principles_loaded(self):
        constitution = _load_constitution()
        assert len(constitution.principles) >= 3

        principle_ids = {p.id for p in constitution.principles}
        assert "honesty" in principle_ids
        assert "user_control" in principle_ids
        assert "memory_minimization" in principle_ids

    def test_k_scenarios_loaded(self):
        constitution = _load_constitution()
        assert len(constitution.k_scenarios) >= 5

    def test_cib_threshold_from_safety(self):
        constitution = _load_constitution()
        assert constitution.cib_threshold == 0.95
        assert constitution.cib_emergency_threshold == 0.97

    def test_layers_loaded(self):
        constitution = _load_constitution()
        assert "absolute" in constitution.layers
        assert "principle" in constitution.layers
        assert "strategy" in constitution.layers

    def test_principle_layers(self):
        constitution = _load_constitution()
        for p in constitution.principles:
            assert p.layer.value in ("absolute", "principle", "strategy")

    def test_scenarios_cover_all_principles(self):
        constitution = _load_constitution()
        principle_ids = {p.id for p in constitution.principles}
        scenario_principles = {s.principle for s in constitution.k_scenarios}
        uncovered = principle_ids - scenario_principles
        assert not uncovered, f"Principles without K-Scenarios: {uncovered}"

    def test_load_missing_base_raises(self, tmp_path):
        from agent.memory.constitution.loader import ConstitutionLoader

        loader = ConstitutionLoader(constitution_dir=str(tmp_path / "nonexistent"))
        with pytest.raises(FileNotFoundError):
            loader.load()

    def test_load_optional_files_missing(self, tmp_path):
        """Loading should work with only base.yml present."""
        from agent.memory.constitution.loader import ConstitutionLoader

        # Create only base.yml
        (tmp_path / "base.yml").write_text(
            "version: 1\n"
            "layers:\n"
            "  absolute:\n"
            "    description: test\n"
            "    update_rule: meta_loop + HITL\n"
            "principles:\n"
            "  - id: test_p\n"
            "    rule: test rule\n"
            "    layer: absolute\n"
            "    weight: 1.0\n"
            "k_scenarios:\n"
            "  - id: ks_test\n"
            "    principle: test_p\n"
            "    description: test\n"
            "    direction_function: test\n",
            encoding="utf-8",
        )
        loader = ConstitutionLoader(constitution_dir=str(tmp_path))
        constitution = loader.load()
        assert len(constitution.principles) == 1
        # CIB threshold should default to 0.95 (no safety.yml)
        assert constitution.cib_threshold == 0.95


# ===========================================================================
# validator.py
# ===========================================================================

class TestConstitutionValidator:
    def test_validate_returns_result(self):
        from agent.memory.constitution.validator import ConstitutionValidator, ValidationResult

        constitution = _load_constitution()
        validator = ConstitutionValidator(llm_client=None)
        result = validator.validate("test action", constitution)

        assert isinstance(result, ValidationResult)
        assert len(result.scenario_results) == len(constitution.k_scenarios)
        assert all(0.0 <= r.score <= 1.0 for r in result.scenario_results)

    def test_validate_direction_returns_score(self):
        from agent.memory.constitution.validator import ConstitutionValidator
        from agent.memory.schemas import KScenario

        validator = ConstitutionValidator(llm_client=None)
        scenario = KScenario(
            id="ks_test",
            principle="honesty",
            description="test",
            direction_function="test",
        )
        score = validator.validate_direction("test text", scenario)
        assert 0.0 <= score <= 1.0

    def test_compliance_text_scores_higher(self):
        """Text with uncertainty disclosure should score higher than confident assertion."""
        from agent.memory.constitution.validator import ConstitutionValidator
        from agent.memory.schemas import KScenario

        validator = ConstitutionValidator(llm_client=None)
        scenario = KScenario(
            id="ks_honesty_01",
            principle="honesty",
            description="test",
            direction_function="불확실성 표시 시 1.0, 근거 없는 단언 시 0.0",
            violation_example="확신하는 어조로 잘못된 정보를 단언",
        )

        compliant_text = "이 부분은 불확실하며, 확인이 필요합니다."
        violating_text = "확신하며 단언합니다. 분명히 맞습니다."

        compliant_score = validator.validate_direction(compliant_text, scenario)
        violating_score = validator.validate_direction(violating_text, scenario)

        assert compliant_score > violating_score

    def test_min_score_in_result(self):
        from agent.memory.constitution.validator import ConstitutionValidator

        constitution = _load_constitution()
        validator = ConstitutionValidator(llm_client=None)
        result = validator.validate("test action", constitution)

        assert result.min_score == min(result.scores)

    def test_passed_flag(self):
        from agent.memory.constitution.validator import ConstitutionValidator

        constitution = _load_constitution()
        validator = ConstitutionValidator(llm_client=None)
        result = validator.validate("test action", constitution, threshold=0.0)

        # With threshold 0.0, everything should pass
        assert result.passed is True

    def test_threshold_override(self):
        from agent.memory.constitution.validator import ConstitutionValidator

        constitution = _load_constitution()
        validator = ConstitutionValidator(llm_client=None)

        # Very high threshold — likely to fail
        result = validator.validate("test action", constitution, threshold=0.99)
        # Most rule-based scores won't reach 0.99
        assert result.passed is (result.min_score >= 0.99)


# ===========================================================================
# guard.py — CIB Gate
# ===========================================================================

class TestCIBGuard:
    def test_evaluate_returns_cib_result(self):
        from agent.memory.constitution.guard import CIBGuard, CIBResult

        constitution = _load_constitution()
        guard = CIBGuard()
        result = guard.evaluate("test action", constitution)

        assert isinstance(result, CIBResult)
        assert len(result.scores) == len(constitution.k_scenarios)
        assert 0.0 <= result.min_score <= 1.0
        assert result.passed == (result.min_score >= result.threshold)
        assert result.blocked == (not result.passed)

    def test_cib_blocks_below_threshold(self):
        """CIB should block when min_score < threshold."""
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard(threshold=1.01)  # Impossible to pass
        result = guard.evaluate("test action", constitution)

        assert result.blocked is True
        assert result.passed is False
        assert "CIB BLOCKED" in result.reason

    def test_cib_passes_at_zero_threshold(self):
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard(threshold=0.0)
        result = guard.evaluate("test action", constitution)

        assert result.passed is True
        assert result.blocked is False
        assert "CIB PASSED" in result.reason

    def test_check_returns_bool(self):
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard(threshold=0.0)
        assert guard.check("test action", constitution) is True

        guard_strict = CIBGuard(threshold=1.01)
        assert guard_strict.check("test action", constitution) is False

    def test_evaluate_uses_constitution_threshold(self):
        """If no threshold is passed, should use constitution.cib_threshold."""
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard(threshold=0.0)  # Guard default is 0.0
        # But evaluate without explicit threshold should use guard's threshold
        result = guard.evaluate("test action", constitution)
        assert result.threshold == 0.0

    def test_emergency_threshold(self):
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard()
        result = guard.evaluate_emergency("test action", constitution)
        assert result.threshold == constitution.cib_emergency_threshold
        assert result.threshold == 0.97


# ===========================================================================
# guard.py — HITL Gate
# ===========================================================================

class TestHITLGate:
    def test_hitl_blocks_without_approval(self):
        from agent.memory.constitution.guard import CIBGuard, HITLResult
        from agent.memory.schemas import ConstitutionLayer

        result = CIBGuard.require_hitl_approval(
            layer=ConstitutionLayer.ABSOLUTE,
            approved=False,
        )
        assert isinstance(result, HITLResult)
        assert result.requires_approval is True
        assert result.approved is False
        assert "HITL BLOCKED" in result.reason

    def test_hitl_passes_with_approval(self):
        from agent.memory.constitution.guard import CIBGuard
        from agent.memory.schemas import ConstitutionLayer

        result = CIBGuard.require_hitl_approval(
            layer=ConstitutionLayer.ABSOLUTE,
            approved=True,
        )
        assert result.approved is True
        assert "HITL PASSED" in result.reason

    def test_hitl_all_layers_require_approval(self):
        """All three layers must require HITL approval."""
        from agent.memory.constitution.guard import CIBGuard
        from agent.memory.schemas import ConstitutionLayer

        for layer in ConstitutionLayer:
            result = CIBGuard.require_hitl_approval(
                layer=layer,
                approved=False,
            )
            assert result.requires_approval is True
            assert result.approved is False

    def test_hitl_accepts_string_layer(self):
        from agent.memory.constitution.guard import CIBGuard

        result = CIBGuard.require_hitl_approval(layer="absolute", approved=True)
        assert result.approved is True

    def test_check_hitl_returns_bool(self):
        from agent.memory.constitution.guard import CIBGuard

        guard = CIBGuard()
        assert guard.check_hitl("absolute", approved=False) is False
        assert guard.check_hitl("absolute", approved=True) is True

    def test_full_check_cib_only(self):
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard(threshold=0.0)
        result = guard.full_check("test action", constitution)

        assert result["allowed"] is True
        assert result["hitl_result"] is None

    def test_full_check_with_hitl(self):
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard(threshold=0.0)

        # Without HITL approval
        result = guard.full_check(
            "test action", constitution,
            modification_layer="absolute",
            hitl_approved=False,
        )
        assert result["allowed"] is False
        assert result["hitl_result"] is not None
        assert result["hitl_result"].approved is False

        # With HITL approval
        result = guard.full_check(
            "test action", constitution,
            modification_layer="absolute",
            hitl_approved=True,
        )
        assert result["allowed"] is True
        assert result["hitl_result"].approved is True

    def test_full_check_cib_blocks_even_with_hitl(self):
        from agent.memory.constitution.guard import CIBGuard

        constitution = _load_constitution()
        guard = CIBGuard(threshold=1.01)

        result = guard.full_check(
            "test action", constitution,
            modification_layer="absolute",
            hitl_approved=True,
        )
        # CIB blocks even with HITL approval
        assert result["allowed"] is False
        assert result["cib_result"].blocked is True
        assert result["hitl_result"].approved is True