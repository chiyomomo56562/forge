"""Tests for Phase 0.4/0.5 — Constitution & Identity YAML loading."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Constitution YAML
# ---------------------------------------------------------------------------

class TestConstitutionBase:
    def test_loads_without_error(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        assert data is not None

    def test_version(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        assert data["version"] == 1

    def test_three_layers_defined(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        layers = data["layers"]
        assert "absolute" in layers
        assert "principle" in layers
        assert "strategy" in layers
        for layer_name, layer in layers.items():
            assert "description" in layer
            assert "update_rule" in layer
            assert "HITL" in layer["update_rule"], f"Layer {layer_name} must require HITL"

    def test_principles_defined(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        principles = data["principles"]
        assert len(principles) >= 3
        ids = {p["id"] for p in principles}
        assert "honesty" in ids
        assert "user_control" in ids
        assert "memory_minimization" in ids

    def test_each_principle_has_layer(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        for p in data["principles"]:
            assert p["layer"] in ("absolute", "principle", "strategy"), \
                f"Principle {p['id']} has invalid layer: {p['layer']}"
            assert "weight" in p
            assert 0.0 <= p["weight"] <= 1.0

    def test_k_scenarios_defined(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        scenarios = data["k_scenarios"]
        assert len(scenarios) >= 5, "Need at least 5 K-Scenarios"

    def test_each_scenario_has_required_fields(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        for s in data["k_scenarios"]:
            assert "id" in s, f"Scenario missing id"
            assert "principle" in s, f"Scenario {s.get('id')} missing principle"
            assert "description" in s, f"Scenario {s.get('id')} missing description"
            assert "expected_behavior" in s, f"Scenario {s.get('id')} missing expected_behavior"
            assert "direction_function" in s, f"Scenario {s.get('id')} missing direction_function"

    def test_scenarios_cover_all_principles(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "base.yml").read_text())
        principle_ids = {p["id"] for p in data["principles"]}
        scenario_principles = {s["principle"] for s in data["k_scenarios"]}
        uncovered = principle_ids - scenario_principles
        assert not uncovered, f"Principles without K-Scenarios: {uncovered}"


class TestConstitutionSafety:
    def test_loads_without_error(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "safety.yml").read_text())
        assert data is not None

    def test_cib_threshold(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "safety.yml").read_text())
        cib = data["cib"]
        assert cib["threshold"] == 0.95
        assert cib["emergency_threshold"] == 0.97
        assert cib["block_on_fail"] is True

    def test_prohibited_actions_defined(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "safety.yml").read_text())
        actions = data["prohibited_actions"]
        assert len(actions) >= 3
        ids = {a["id"] for a in actions}
        assert "no_self_modification_without_hitl" in ids
        assert "no_sensitive_data_storage" in ids
        assert "no_unauthorized_external_action" in ids

    def test_sensitive_patterns_for_filtering(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "safety.yml").read_text())
        patterns = data["safety_boundaries"]["memory"]["sensitive_patterns"]
        assert len(patterns) >= 3
        # Should include API key patterns
        assert any("sk-" in p for p in patterns)


class TestConstitutionInteraction:
    def test_loads_without_error(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "interaction_policy.yml").read_text())
        assert data is not None

    def test_confirmation_actions(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "interaction_policy.yml").read_text())
        actions = data["require_confirmation_for"]
        ids = {a["id"] for a in actions}
        assert "sending_email" in ids
        assert "deleting_files" in ids
        assert "external_purchase" in ids

    def test_delegation_classes(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "interaction_policy.yml").read_text())
        delegation = data["delegation"]
        assert "autonomous_actions" in delegation
        assert "proposed_actions" in delegation
        assert "forbidden_actions" in delegation


class TestConstitutionToolPolicy:
    def test_loads_without_error(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "tool_policy.yml").read_text())
        assert data is not None

    def test_require_confirmation_matches_interaction(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "tool_policy.yml").read_text())
        assert "sending_email" in data["require_confirmation_for"]
        assert "deleting_files" in data["require_confirmation_for"]

    def test_tool_classes_defined(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "tool_policy.yml").read_text())
        classes = data["tool_classes"]
        assert "autonomous" in classes
        assert "confirmation_required" in classes
        assert "forbidden" in classes

    def test_forbidden_tools(self):
        data = yaml.safe_load((PROJECT_ROOT / "constitution" / "tool_policy.yml").read_text())
        forbidden = data["tool_classes"]["forbidden"]
        ids = {t["id"] for t in forbidden}
        assert "exec_unsandboxed" in ids
        assert "store_credentials" in ids


# ---------------------------------------------------------------------------
# Identity YAML
# ---------------------------------------------------------------------------

class TestIdentity:
    def test_identity_loads(self):
        data = yaml.safe_load((PROJECT_ROOT / "identity" / "identity.yml").read_text())
        assert data["name"] == "Gnosis"
        assert "identity_core" in data
        assert "values" in data["identity_core"]
        assert "boundaries" in data["identity_core"]

    def test_autonomy_level(self):
        data = yaml.safe_load((PROJECT_ROOT / "identity" / "identity.yml").read_text())
        auto = data["autonomy_level"]
        assert "current" in auto
        assert "target" in auto
        assert "upgrade_rule" in auto
        assert "HITL" in auto["upgrade_rule"]

    def test_self_model_loads(self):
        data = yaml.safe_load((PROJECT_ROOT / "identity" / "self_model.yml").read_text())
        assert "initial_bias" in data
        assert data["initial_bias"]["direction"] in ("overconfident", "underconfident", "calibrated")
        assert "calibration" in data
        assert data["calibration"]["threshold"] == 0.05

    def test_self_model_coherence(self):
        data = yaml.safe_load((PROJECT_ROOT / "identity" / "self_model.yml").read_text())
        coh = data["coherence_index"]
        assert coh["cib_weight"] == 0.5
        assert coh["calibration_weight"] == 0.5

    def test_capabilities_loads(self):
        data = yaml.safe_load((PROJECT_ROOT / "identity" / "capabilities.yml").read_text())
        cats = data["categories"]
        assert len(cats) >= 5
        ids = {c["id"] for c in cats}
        assert "coding" in ids
        assert "general" in ids

    def test_capabilities_initial_values(self):
        data = yaml.safe_load((PROJECT_ROOT / "identity" / "capabilities.yml").read_text())
        for cat in data["categories"]:
            assert 0.0 <= cat["success_rate"] <= 1.0
            assert 0.0 <= cat["confidence"] <= 1.0
            assert cat["total_attempts"] == 0, f"{cat['id']} should start with 0 attempts"

    def test_capabilities_update_policy(self):
        data = yaml.safe_load((PROJECT_ROOT / "identity" / "capabilities.yml").read_text())
        policy = data["update_policy"]
        assert policy["window_size"] == 50
        assert policy["min_attempts_for_update"] >= 1


# ---------------------------------------------------------------------------
# Cross-file consistency
# ---------------------------------------------------------------------------

class TestCrossFileConsistency:
    def test_tool_policy_confirmation_matches_interaction(self):
        tool = yaml.safe_load((PROJECT_ROOT / "constitution" / "tool_policy.yml").read_text())
        interaction = yaml.safe_load((PROJECT_ROOT / "constitution" / "interaction_policy.yml").read_text())
        tool_confirm = set(tool["require_confirmation_for"])
        interaction_confirm = {a["id"] for a in interaction["require_confirmation_for"]}
        assert tool_confirm == interaction_confirm, \
            f"Mismatch: tool_only={tool_confirm - interaction_confirm}, interaction_only={interaction_confirm - tool_confirm}"

    def test_safety_cib_matches_agent_config(self):
        safety = yaml.safe_load((PROJECT_ROOT / "constitution" / "safety.yml").read_text())
        agent = yaml.safe_load((PROJECT_ROOT / "config" / "agent.yml").read_text())
        assert safety["cib"]["threshold"] == agent["cib"]["threshold"]
        assert safety["cib"]["emergency_threshold"] == agent["cib"]["emergency_threshold"]

    def test_self_model_calibration_matches_memory_config(self):
        self_model = yaml.safe_load((PROJECT_ROOT / "identity" / "self_model.yml").read_text())
        memory = yaml.safe_load((PROJECT_ROOT / "config" / "memory.yml").read_text())
        assert self_model["calibration"]["threshold"] == memory["identity"]["self_model"]["calibration_threshold"]
        assert self_model["calibration"]["window_size"] == memory["identity"]["self_model"]["window_size"]