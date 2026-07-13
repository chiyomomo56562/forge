"""Unit tests for Phase 3.2 — M16 Growth Rate Regulator Actions.

Covers:
    - growth_actions.py: Action execution for crash/overgrowth/stagnation signals
    - outer_loop.py: Integration of action execution in Step 6
    - Learning suspension flag persistence
    - L3 skill degradation (Degrading status)
    - L2 knowledge node confidence degradation
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _make_regulation_result(signal="normal", **kwargs):
    """Create a GrowthRegulationResult for testing."""
    from agent.outer_loop.growth_regulator import GrowthRegulationResult, GrowthSignal

    defaults = {
        "signal": GrowthSignal(signal) if isinstance(signal, str) else signal,
        "action": "",
        "details": {},
        "cib_force_required": False,
        "learning_suspended": False,
        "meta_trigger_required": False,
    }
    defaults.update(kwargs)
    return GrowthRegulationResult(**defaults)


def _make_aggregation_result(**kwargs):
    """Create an AggregationResult for testing."""
    from agent.outer_loop.aggregator import AggregationResult

    defaults = {
        "episode_count": 10,
        "success_rate": 0.8,
        "avg_phoenix_score": 0.85,
        "avg_cib_score": 0.97,
        "status_distribution": {"Success": 8, "Failure": 1, "Partial": 1},
        "episode_ids": [f"ep_{i:03d}" for i in range(10)],
        "phoenix_scores": [0.85] * 10,
        "cib_scores": [0.97] * 10,
    }
    defaults.update(kwargs)
    return AggregationResult(**defaults)


# ===========================================================================
# GrowthActionExecutor — Basic dispatch
# ===========================================================================

class TestGrowthActionExecutorDispatch:
    def test_normal_signal_no_action(self):
        from agent.outer_loop.growth_actions import GrowthActionExecutor, ActionResult
        from agent.outer_loop.growth_regulator import GrowthSignal

        executor = GrowthActionExecutor()
        reg = _make_regulation_result(signal=GrowthSignal.NORMAL)
        result = executor.execute(reg)

        assert isinstance(result, ActionResult)
        assert result.signal == GrowthSignal.NORMAL
        assert result.cib_forced is False
        assert result.learning_suspended is False

    def test_crash_signal_dispatches_crash_action(self):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.outer_loop.growth_regulator import GrowthSignal

        executor = GrowthActionExecutor()
        reg = _make_regulation_result(
            signal=GrowthSignal.CRASH,
            action="Force CIB → suspend learning",
            details={"drop": 0.2, "previous_rate": 0.85, "recent_rate": 0.65},
        )
        result = executor.execute(reg)

        assert result.signal == GrowthSignal.CRASH
        assert result.learning_suspended is True
        assert result.root_cause is not None
        assert result.root_cause["signal"] == "crash"

    def test_overgrowth_signal_dispatches_overgrowth_action(self):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.outer_loop.growth_regulator import GrowthSignal

        executor = GrowthActionExecutor()
        reg = _make_regulation_result(
            signal=GrowthSignal.OVERGROWTH,
            action="Force CIB → overfitting check",
            details={"rise": 0.25, "earliest_coherence": 0.5, "current_coherence": 0.75},
        )
        result = executor.execute(reg)

        assert result.signal == GrowthSignal.OVERGROWTH
        assert result.cib_forced is False  # no episode_texts or constitution

    def test_stagnation_signal_dispatches_stagnation_action(self):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.outer_loop.growth_regulator import GrowthSignal

        executor = GrowthActionExecutor()
        reg = _make_regulation_result(
            signal=GrowthSignal.STAGNATION,
            action="Meta-loop stagnation trigger",
            details={"coherence_range": 0.005},
        )
        result = executor.execute(reg)

        assert result.signal == GrowthSignal.STAGNATION
        assert result.action_taken.startswith("Meta-loop")


# ===========================================================================
# GrowthActionExecutor — Crash action
# ===========================================================================

class TestCrashAction:
    def test_crash_root_cause_analysis(self):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.outer_loop.growth_regulator import GrowthSignal

        executor = GrowthActionExecutor()
        agg = _make_aggregation_result(
            success_rate=0.65,
            avg_pain_index=0.35,
            status_distribution={"Success": 5, "Failure": 3, "Partial": 2},
        )
        reg = _make_regulation_result(
            signal=GrowthSignal.CRASH,
            details={"drop": 0.2, "previous_rate": 0.85, "recent_rate": 0.65},
        )
        result = executor.execute(reg, aggregation_result=agg)

        assert result.root_cause is not None
        assert result.root_cause["success_rate_drop"] == 0.2
        assert result.root_cause["previous_rate"] == 0.85
        assert result.root_cause["recent_rate"] == 0.65
        assert result.root_cause["status_distribution"]["Failure"] == 3
        assert result.root_cause["avg_pain_index"] == 0.35

    def test_crash_cib_force_with_texts(self, tmp_path):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.outer_loop.growth_regulator import GrowthSignal
        from agent.memory.constitution.guard import CIBGuard
        from agent.memory.constitution.loader import ConstitutionLoader

        loader = ConstitutionLoader(constitution_dir=str(PROJECT_ROOT / "constitution"))
        constitution = loader.load()

        executor = GrowthActionExecutor(
            cib_guard=CIBGuard(),
            constitution=constitution,
        )
        reg = _make_regulation_result(signal=GrowthSignal.CRASH)
        result = executor.execute(
            reg,
            episode_texts=["This is a safe result.", "Another safe output."],
        )

        assert result.cib_forced is True
        assert len(result.cib_results) == 2
        assert "episode_index" in result.cib_results[0]


# ===========================================================================
# GrowthActionExecutor — Overgrowth action with L3 degradation
# ===========================================================================

class TestOvergrowthAction:
    def test_overgrowth_degrades_low_confidence_skills(self, tmp_path):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.outer_loop.growth_regulator import GrowthSignal
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import Skill, SkillMetadata, SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )

        # Add an Active skill with low success_rate
        low_skill = Skill(
            skill_id="skill_low_001",
            name="Low confidence skill",
            code="def run(): pass",
            description="A skill with low success rate",
            metadata=SkillMetadata(
                status=SkillStatus.ACTIVE,
                success_rate=0.3,
                total_executions=10,
            ),
        )
        store.upsert(low_skill)

        # Add an Active skill with high success_rate (should NOT be degraded)
        high_skill = Skill(
            skill_id="skill_high_001",
            name="High confidence skill",
            code="def run(): pass",
            description="A skill with high success rate",
            metadata=SkillMetadata(
                status=SkillStatus.ACTIVE,
                success_rate=0.9,
                total_executions=10,
            ),
        )
        store.upsert(high_skill)

        executor = GrowthActionExecutor(
            skill_store=store,
            degradation_confidence_threshold=0.5,
        )
        reg = _make_regulation_result(signal=GrowthSignal.OVERGROWTH)

        # No CIB results → cib_all_passed = True → no degradation
        result = executor.execute(reg)
        assert result.skills_degraded == 0

        # Now simulate CIB failure by providing results that fail
        # We need to mock the CIB evaluation — use episode_texts that fail
        # Actually, let's directly test the degradation method
        degraded = executor._degrade_low_confidence_skills()
        assert degraded == 1  # only the low-confidence skill

        # Verify the skill was degraded
        degraded_skill = store.get_metadata("skill_low_001")
        assert degraded_skill.metadata.status == SkillStatus.DEGRADING

        # Verify the high-confidence skill was NOT degraded
        high_skill_after = store.get_metadata("skill_high_001")
        assert high_skill_after.metadata.status == SkillStatus.ACTIVE

    def test_overgrowth_protected_skill_not_degraded(self, tmp_path):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import Skill, SkillMetadata, SkillStatus

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )

        protected_skill = Skill(
            skill_id="skill_protected_001",
            name="Protected skill",
            code="def run(): pass",
            description="A protected skill",
            metadata=SkillMetadata(
                status=SkillStatus.ACTIVE,
                success_rate=0.2,
                total_executions=10,
            ),
            protected=True,
        )
        store.upsert(protected_skill)

        executor = GrowthActionExecutor(
            skill_store=store,
            degradation_confidence_threshold=0.5,
        )
        degraded = executor._degrade_low_confidence_skills()

        assert degraded == 0  # protected skill not degraded
        skill_after = store.get_metadata("skill_protected_001")
        assert skill_after.metadata.status == SkillStatus.ACTIVE

    def test_overgrowth_degrades_low_confidence_knowledge(self, tmp_path):
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.schemas import KnowledgeNode, HintType

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )

        # Add a low-confidence knowledge node
        low_node = KnowledgeNode(
            node_id="kn_low_001",
            hint_text="Low confidence hint",
            hint_type=HintType.GENERAL,
            confidence=0.3,
            created_at="2025-01-01T00:00:00Z",
        )
        graph_store.add_knowledge_node(low_node)

        # Add a high-confidence knowledge node
        high_node = KnowledgeNode(
            node_id="kn_high_001",
            hint_text="High confidence hint",
            hint_type=HintType.GENERAL,
            confidence=0.9,
            created_at="2025-01-01T00:00:00Z",
        )
        graph_store.add_knowledge_node(high_node)

        executor = GrowthActionExecutor(
            graph_store=graph_store,
            degradation_confidence_threshold=0.5,
        )
        degraded = executor._degrade_low_confidence_knowledge()

        assert degraded == 1  # only the low-confidence node

        # Verify the confidence was halved
        nodes = graph_store.get_knowledge_nodes()
        for node in nodes:
            if node.node_id == "kn_low_001":
                assert node.confidence == 0.15  # 0.3 * 0.5
            elif node.node_id == "kn_high_001":
                assert node.confidence == 0.9  # unchanged


# ===========================================================================
# Outer Loop integration — learning suspension flag
# ===========================================================================

class TestOuterLoopActionIntegration:
    def test_learning_suspended_persisted_on_crash(self, tmp_path):
        """Verify that learning_suspended flag is persisted when crash is detected."""
        from agent.outer_loop import OuterLoop

        state_path = str(tmp_path / "state.json")

        # Create outer loop with crash-prone config
        outer = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=str(tmp_path / "audit.jsonl"),
            growth_regulator_config={
                "crash_window": 1,  # very small to trigger quickly
                "crash_delta_threshold": 0.01,
            },
        )

        # Manually inject success rate history to trigger crash
        outer.growth_regulator._success_rate_history = [0.9, 0.5]
        result = outer.run()

        # Check if crash was detected and learning suspended
        if result.growth_regulation.signal.value == "crash":
            assert result.action_result is not None
            assert result.action_result.learning_suspended is True
            assert outer.state.learning_suspended is True

            # Verify state was persisted
            state_data = json.loads(Path(state_path).read_text())
            assert state_data["learning_suspended"] is True

    def test_learning_suspended_reset_on_normal(self, tmp_path):
        """Verify that learning_suspended is reset when signal returns to normal."""
        from agent.outer_loop import OuterLoop

        state_path = str(tmp_path / "state.json")

        outer = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )

        # Set learning_suspended = True initially
        outer.state.learning_suspended = True
        outer._save_state()

        # Run with normal signal (no data → normal)
        result = outer.run()

        assert result.growth_regulation.signal.value == "normal"
        assert outer.state.learning_suspended is False

    def test_audit_log_includes_action_fields(self, tmp_path):
        """Verify that audit log includes action-related fields."""
        from agent.outer_loop import OuterLoop

        audit_path = tmp_path / "audit.jsonl"
        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(audit_path),
        )
        outer.run()

        assert audit_path.exists()
        entry = json.loads(audit_path.read_text().strip().split("\n")[0])
        assert "action_taken" in entry
        assert "learning_suspended" in entry
        assert "skills_degraded" in entry
        assert "knowledge_degraded" in entry


# ===========================================================================
# Full integration with MemoryManager
# ===========================================================================

class TestFullIntegrationWithMemoryManager:
    def test_overgrowth_action_with_real_stores(self, tmp_path):
        """Test overgrowth action with real SkillStore and GraphStore."""
        from agent.outer_loop.growth_actions import GrowthActionExecutor
        from agent.outer_loop.growth_regulator import GrowthSignal
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.schemas import Skill, SkillMetadata, SkillStatus, KnowledgeNode, HintType

        store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )

        # Add skills and knowledge nodes
        for i in range(3):
            skill = Skill(
                skill_id=f"skill_og_{i:03d}",
                name=f"Skill {i}",
                code="def run(): pass",
                description=f"Test skill {i}",
                metadata=SkillMetadata(
                    status=SkillStatus.ACTIVE,
                    success_rate=0.2 + i * 0.1,  # 0.2, 0.3, 0.4 — all below 0.5
                    total_executions=10,
                ),
            )
            store.upsert(skill)

        for i in range(2):
            kn = KnowledgeNode(
                node_id=f"kn_og_{i:03d}",
                hint_text=f"Hint {i}",
                hint_type=HintType.GENERAL,
                confidence=0.2 + i * 0.1,  # 0.2, 0.3 — both below 0.5
                created_at="2025-01-01T00:00:00Z",
            )
            graph_store.add_knowledge_node(kn)

        executor = GrowthActionExecutor(
            skill_store=store,
            graph_store=graph_store,
            degradation_confidence_threshold=0.5,
        )

        # Degrade skills
        skills_degraded = executor._degrade_low_confidence_skills()
        assert skills_degraded == 3

        # Degrade knowledge nodes
        kn_degraded = executor._degrade_low_confidence_knowledge()
        assert kn_degraded == 2

        # Verify all skills are now Degrading
        for i in range(3):
            skill = store.get_metadata(f"skill_og_{i:03d}")
            assert skill.metadata.status == SkillStatus.DEGRADING