"""Unit tests for Phase 2.2 — Cognition modules.

Covers:
    - context_builder.py: selective injection, density-first, token budget
    - planner.py: plan generation (heuristic + LLM fallback)
    - reasoner.py: plan validation, alternative generation
    - decision.py: plan selection, step ordering
    - reflection_loop.py: 4-field reflection extraction
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _make_manager(tmp_path):
    from agent.memory.manager import MemoryManager
    from agent.memory.episodic.encoder import EmbeddingEncoder
    from agent.llm.client import LLMClient, LLMConfig

    config = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
    client = LLMClient(config=config)
    encoder = EmbeddingEncoder(llm_client=client, dimension=64)

    return MemoryManager(
        chroma_path=str(tmp_path / "chroma"),
        graphml_path=str(tmp_path / "graph.graphml"),
        gpickle_path=str(tmp_path / "graph.gpickle"),
        sqlite_path=str(tmp_path / "skills.sqlite3"),
        skills_dir=str(tmp_path / "skills"),
        constitution_dir=str(PROJECT_ROOT / "constitution"),
        identity_db_path=str(tmp_path / "identity.sqlite3"),
        raw_events_dir=str(tmp_path / "raw_events"),
        encoder=encoder,
    )


# ===========================================================================
# context_builder.py
# ===========================================================================

class TestContextBuilder:
    def test_build_empty_without_manager(self):
        from agent.cognition.context_builder import ContextBuilder, BuiltContext

        builder = ContextBuilder(memory_manager=None)
        context = builder.build("test query")
        assert isinstance(context, BuiltContext)
        assert len(context.blocks) == 0

    def test_build_with_manager(self, tmp_path):
        """Context should include blocks from memory layers."""
        from agent.cognition.context_builder import ContextBuilder
        from agent.memory.schemas import Episode, Evaluation, Reflection

        mgr = _make_manager(tmp_path)

        # Store an episode with reflection
        ep = Episode(
            episode_id="ep_001",
            task="matplotlib 차트 생성",
            execution_summary="차트 생성 성공",
            evaluation=Evaluation(),
            reflection=Reflection(
                what_worked="matplotlib로 차트 생성",
                what_failed="한글 폰트 깨짐",
                next_hint="폰트 캐시 확인",
                causal_condition="폰트 설정 필요",
            ),
            timestamp="2026-01-01T00:00:00Z",
            has_reflection=True,
        )
        mgr.store_episode(ep)

        builder = ContextBuilder(memory_manager=mgr, top_k=5)
        context = builder.build("matplotlib 차트")

        # Should have at least one block
        assert len(context.blocks) > 0
        assert context.total_tokens > 0

    def test_density_first_prioritizes_reflection(self, tmp_path):
        """Records with reflection should come before those without."""
        from agent.cognition.context_builder import ContextBuilder
        from agent.memory.schemas import Episode, Evaluation, Reflection

        mgr = _make_manager(tmp_path)

        # Episode WITH reflection
        ep1 = Episode(
            episode_id="ep_with_refl",
            task="matplotlib 차트",
            execution_summary="성공",
            evaluation=Evaluation(),
            reflection=Reflection(what_worked="성공"),
            timestamp="2026-01-01T00:00:00Z",
            has_reflection=True,
        )
        mgr.store_episode(ep1)

        builder = ContextBuilder(memory_manager=mgr, top_k=5)
        context = builder.build("matplotlib")

        # First block should be from L1 with reflection data
        if context.blocks:
            assert context.blocks[0].layer == "L1"

    def test_token_budget_truncation(self, tmp_path):
        """Context should be truncated when token budget is exceeded."""
        from agent.cognition.context_builder import ContextBuilder
        from agent.memory.schemas import Episode, Evaluation, Reflection

        mgr = _make_manager(tmp_path)

        # Store multiple episodes
        for i in range(5):
            ep = Episode(
                episode_id=f"ep_{i:03d}",
                task=f"task {i} " * 100,  # Long task to exceed budget
                execution_summary="summary " * 50,
                evaluation=Evaluation(),
                reflection=Reflection(what_worked="worked " * 50),
                timestamp=f"2026-01-0{i+1}T00:00:00Z",
                has_reflection=True,
            )
            mgr.store_episode(ep)

        builder = ContextBuilder(memory_manager=mgr, top_k=10, token_budget=100)
        context = builder.build("task")

        # Should be truncated
        assert context.truncated is True

    def test_to_prompt_string(self):
        from agent.cognition.context_builder import BuiltContext, ContextBlock

        ctx = BuiltContext(blocks=[
            ContextBlock(layer="L1", source="episodic", content="Task: test"),
            ContextBlock(layer="L2", source="semantic", content="Entity: matplotlib"),
        ])
        prompt = ctx.to_prompt_string()
        assert "[L1]" in prompt
        assert "[L2]" in prompt
        assert "Task: test" in prompt
        assert "Entity: matplotlib" in prompt

    def test_to_prompt_string_empty(self):
        from agent.cognition.context_builder import BuiltContext

        ctx = BuiltContext()
        prompt = ctx.to_prompt_string()
        assert "no relevant" in prompt.lower()


# ===========================================================================
# planner.py
# ===========================================================================

class TestPlanner:
    def test_plan_heuristic(self):
        """Heuristic plan should have 3 steps."""
        from agent.cognition.planner import Planner, Plan

        planner = Planner(llm_client=None)
        plan = planner.plan("Create a data visualization")

        assert isinstance(plan, Plan)
        assert plan.step_count == 3
        assert plan.task_category == "general"
        assert 0.0 <= plan.estimated_success <= 1.0

    def test_plan_steps_have_descriptions(self):
        from agent.cognition.planner import Planner

        planner = Planner(llm_client=None)
        plan = planner.plan("Analyze data")

        for step in plan.steps:
            assert step.description != ""

    def test_plan_with_context(self):
        """Plan should be generated even with context."""
        from agent.cognition.planner import Planner

        planner = Planner(llm_client=None)
        plan = planner.plan("test task", context="Some memory context")

        assert plan.step_count > 0

    def test_plan_with_llm_fallback(self):
        """When LLM fails, should fall back to heuristic."""
        from agent.cognition.planner import Planner
        from agent.llm.client import LLMClient, LLMConfig

        cfg = LLMConfig(
            backend="ollama",
            ollama_base_url="http://nonexistent:99999",
            ollama_max_retries=1,
        )
        client = LLMClient(config=cfg)
        planner = Planner(llm_client=client)
        plan = planner.plan("test task")

        # Should fall back to heuristic
        assert plan.step_count == 3
        assert "heuristic" in plan.raw_response.lower()


# ===========================================================================
# reasoner.py
# ===========================================================================

class TestReasoner:
    def test_validate_feasible_plan(self):
        """A simple plan should be feasible."""
        from agent.cognition.reasoner import Reasoner
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[
            PlanStep(description="Step 1"),
            PlanStep(description="Step 2"),
        ], estimated_success=0.8)

        reasoner = Reasoner(llm_client=None)
        result = reasoner.validate(plan)

        assert result.feasible is True
        assert result.confidence > 0.0

    def test_validate_empty_plan(self):
        """Empty plan should not be feasible."""
        from agent.cognition.reasoner import Reasoner
        from agent.cognition.planner import Plan

        plan = Plan(steps=[], estimated_success=0.5)
        reasoner = Reasoner(llm_client=None)
        result = reasoner.validate(plan)

        assert result.feasible is False
        assert "no steps" in result.risks[0].lower()

    def test_validate_low_success_rate(self):
        """Plan with very low estimated success should have risks."""
        from agent.cognition.reasoner import Reasoner
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[
            PlanStep(description="Step 1"),
        ], estimated_success=0.1)

        reasoner = Reasoner(llm_client=None)
        result = reasoner.validate(plan)

        assert len(result.risks) > 0
        assert any("low" in r.lower() for r in result.risks)

    def test_validate_unmet_prerequisites(self):
        """Steps with unmet prerequisites should be flagged."""
        from agent.cognition.reasoner import Reasoner
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[
            PlanStep(description="Step A", prerequisites=["nonexistent"]),
        ])

        reasoner = Reasoner(llm_client=None)
        result = reasoner.validate(plan)

        assert len(result.missing_prerequisites) > 0

    def test_generate_alternatives_heuristic(self):
        """Heuristic should generate a reversed alternative."""
        from agent.cognition.reasoner import Reasoner
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[
            PlanStep(description="Step A"),
            PlanStep(description="Step B"),
            PlanStep(description="Step C"),
        ])

        reasoner = Reasoner(llm_client=None)
        alternatives = reasoner.generate_alternatives(plan)

        assert len(alternatives) == 1
        assert alternatives[0].steps[0].description == "Step C"

    def test_generate_alternatives_single_step(self):
        """Single-step plan should have no heuristic alternatives."""
        from agent.cognition.reasoner import Reasoner
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[PlanStep(description="Only step")])
        reasoner = Reasoner(llm_client=None)
        alternatives = reasoner.generate_alternatives(plan)
        assert len(alternatives) == 0


# ===========================================================================
# decision.py
# ===========================================================================

class TestDecisionMaker:
    def test_select_feasible_primary(self):
        """Feasible primary plan should be selected."""
        from agent.cognition.decision import DecisionMaker
        from agent.cognition.planner import Plan, PlanStep
        from agent.cognition.reasoner import ValidationResult

        plan = Plan(steps=[PlanStep(description="Step 1")], estimated_success=0.8)
        validation = ValidationResult(feasible=True, confidence=0.7)

        dm = DecisionMaker()
        result = dm.decide(plan, validation)

        assert result.selected_plan is not None
        assert result.selected_plan.steps[0].description == "Step 1"
        assert "Primary plan is feasible" in result.selection_reason

    def test_select_alternative_when_primary_has_issues(self):
        """When primary has issues and a better alternative exists, select it."""
        from agent.cognition.decision import DecisionMaker
        from agent.cognition.planner import Plan, PlanStep
        from agent.cognition.reasoner import ValidationResult

        primary = Plan(steps=[PlanStep(description="Primary")], estimated_success=0.2)
        alt = Plan(steps=[PlanStep(description="Alternative")], estimated_success=0.9)
        validation = ValidationResult(feasible=False, risks=["Low success rate"], confidence=0.2)

        dm = DecisionMaker()
        result = dm.decide(primary, validation, alternatives=[alt])

        assert result.selected_plan is not None
        assert result.selected_plan.steps[0].description == "Alternative"

    def test_select_primary_when_no_better_alternative(self):
        """When no better alternative, select primary despite risks."""
        from agent.cognition.decision import DecisionMaker
        from agent.cognition.planner import Plan, PlanStep
        from agent.cognition.reasoner import ValidationResult

        primary = Plan(steps=[PlanStep(description="Primary")], estimated_success=0.8)
        alt = Plan(steps=[PlanStep(description="Alt")], estimated_success=0.3)
        validation = ValidationResult(feasible=False, risks=["Some risk"], confidence=0.4)

        dm = DecisionMaker()
        result = dm.decide(primary, validation, alternatives=[alt])

        assert result.selected_plan is not None
        assert result.selected_plan.steps[0].description == "Primary"

    def test_step_ordering_by_prerequisites(self):
        """Steps should be ordered so prerequisites come first."""
        from agent.cognition.decision import DecisionMaker
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[
            PlanStep(description="Step B", prerequisites=["step a"]),
            PlanStep(description="Step A"),
        ])

        dm = DecisionMaker()
        ordered = dm._order_steps(plan)

        # Step A (no prereqs) should come before Step B (prereq: step a)
        assert ordered.steps[0].description == "Step A"
        assert ordered.steps[1].description == "Step B"

    def test_step_ordering_preserves_independent_order(self):
        """Independent steps should preserve their original order."""
        from agent.cognition.decision import DecisionMaker
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[
            PlanStep(description="Step A"),
            PlanStep(description="Step B"),
            PlanStep(description="Step C"),
        ])

        dm = DecisionMaker()
        ordered = dm._order_steps(plan)

        assert [s.description for s in ordered.steps] == ["Step A", "Step B", "Step C"]


# ===========================================================================
# reflection_loop.py
# ===========================================================================

class TestReflectionLoop:
    def test_reflect_success(self):
        """Reflection on a successful task should extract what_worked."""
        from agent.cognition.reflection_loop import ReflectionLoop
        from agent.memory.schemas import Evaluation, EpisodeStatus

        loop = ReflectionLoop(llm_client=None)
        eval_result = Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.9)

        result = loop.reflect(
            task="Create chart",
            execution_summary="Chart created successfully",
            evaluation=eval_result,
        )

        assert result.reflection.what_worked != ""
        assert "successfully" in result.reflection.what_worked.lower() or "success" in result.reflection.what_worked.lower()
        assert result.source == "heuristic"

    def test_reflect_failure(self):
        """Reflection on a failed task should extract what_failed."""
        from agent.cognition.reflection_loop import ReflectionLoop
        from agent.memory.schemas import Evaluation, EpisodeStatus

        loop = ReflectionLoop(llm_client=None)
        eval_result = Evaluation(status=EpisodeStatus.FAILURE, success_score=0.1)

        result = loop.reflect(
            task="Create chart",
            execution_summary="Chart creation failed due to font error",
            evaluation=eval_result,
        )

        assert result.reflection.what_failed != ""
        assert result.reflection.next_hint != ""

    def test_reflect_partial(self):
        """Reflection on a partial task should have both worked and failed."""
        from agent.cognition.reflection_loop import ReflectionLoop
        from agent.memory.schemas import Evaluation, EpisodeStatus

        loop = ReflectionLoop(llm_client=None)
        eval_result = Evaluation(status=EpisodeStatus.PARTIAL, success_score=0.5)

        result = loop.reflect(
            task="Data analysis",
            execution_summary="Analysis partially completed",
            evaluation=eval_result,
        )

        assert result.reflection.what_worked != ""
        assert result.reflection.what_failed != ""

    def test_reflect_high_pain_index(self):
        """High pain index should add failure insight."""
        from agent.cognition.reflection_loop import ReflectionLoop
        from agent.memory.schemas import Evaluation, EpisodeStatus

        loop = ReflectionLoop(llm_client=None)
        eval_result = Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.3, pain_index=0.7)

        result = loop.reflect(
            task="test",
            execution_summary="Completed with issues",
            evaluation=eval_result,
            pain_index=0.7,
        )

        assert result.reflection.what_failed != "" or "pain" in result.reflection.next_hint.lower()

    def test_reflect_no_evaluation(self):
        """Reflection should work even without evaluation."""
        from agent.cognition.reflection_loop import ReflectionLoop

        loop = ReflectionLoop(llm_client=None)
        result = loop.reflect(
            task="test task",
            execution_summary="Task executed",
        )

        assert result.reflection.what_worked != ""
        assert result.source == "heuristic"

    def test_reflect_from_episode(self):
        """reflect_from_episode should extract from an Episode object."""
        from agent.cognition.reflection_loop import ReflectionLoop
        from agent.memory.schemas import Episode, Evaluation, EpisodeStatus

        loop = ReflectionLoop(llm_client=None)
        ep = Episode(
            episode_id="ep_001",
            task="Test task",
            execution_summary="Executed successfully",
            evaluation=Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.9),
            timestamp="2026-01-01T00:00:00Z",
        )

        result = loop.reflect_from_episode(ep)
        assert result.reflection.what_worked != ""

    def test_reflect_summary(self):
        """Reflection result should include a summary."""
        from agent.cognition.reflection_loop import ReflectionLoop
        from agent.memory.schemas import Evaluation, EpisodeStatus

        loop = ReflectionLoop(llm_client=None)
        result = loop.reflect(
            task="test",
            execution_summary="Done",
            evaluation=Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.9),
        )

        assert result.summary != ""

    def test_reflect_with_llm_fallback(self):
        """When LLM fails, should fall back to heuristic."""
        from agent.cognition.reflection_loop import ReflectionLoop
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.schemas import Evaluation, EpisodeStatus

        cfg = LLMConfig(
            backend="ollama",
            ollama_base_url="http://nonexistent:99999",
            ollama_max_retries=1,
        )
        client = LLMClient(config=cfg)
        loop = ReflectionLoop(llm_client=client)

        result = loop.reflect(
            task="test",
            execution_summary="Done",
            evaluation=Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.9),
        )

        # Should fall back to heuristic
        assert result.source == "heuristic"
        assert result.reflection.what_worked != ""