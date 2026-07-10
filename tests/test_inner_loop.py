"""Unit tests for Phase 2.4 — Inner Loop Pipeline.

Covers:
    - runtime.py: session creation, working memory, staging, cleanup
    - orchestrator.py: 4-stage pipeline (plan→execute→evaluate→reflect)
    - main.py: CLI entry point (create_agent, run_query)

Tests use heuristic fallbacks (no LLM required) and mock tool registries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _make_orchestrator(tmp_path, with_tools=True, with_memory=False):
    """Create a minimal orchestrator for testing."""
    from agent.runtime import Runtime
    from agent.orchestrator import Orchestrator
    from agent.cognition.planner import Planner
    from agent.cognition.reasoner import Reasoner
    from agent.cognition.decision import DecisionMaker
    from agent.cognition.reflection_loop import ReflectionLoop

    runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))

    tool_registry = None
    if with_tools:
        from agent.tools.registry import ToolRegistry
        tool_registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        tool_registry.register_builtin()

    memory_manager = None
    if with_memory:
        from agent.memory.manager import MemoryManager
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.llm.client import LLMClient, LLMConfig

        config = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=config)
        encoder = EmbeddingEncoder(llm_client=client, dimension=64)

        memory_manager = MemoryManager(
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

    orch = Orchestrator(
        runtime=runtime,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        llm_client=None,  # use heuristic fallbacks
        max_retries=2,
    )
    return orch, runtime


# ===========================================================================
# runtime.py
# ===========================================================================

class TestRuntime:
    def test_create_session(self, tmp_path):
        from agent.runtime import Runtime, Session, WorkingMemory

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        session = runtime.create_session("test input")

        assert isinstance(session, Session)
        assert session.session_id.startswith("sess_")
        assert session.working_memory.episode_id.startswith("ep_")
        assert session.working_memory.user_input == "test input"
        assert session.working_dir.exists()
        assert session.session_id in runtime.list_active_sessions()

    def test_stage_and_load(self, tmp_path):
        from agent.runtime import Runtime

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        session = runtime.create_session("test")

        data = {"key": "value", "nested": {"a": 1}}
        path = session.stage("plan", data)
        assert path.exists()
        assert path.name == "plan.json"

        loaded = session.load_staged("plan")
        assert loaded == data

    def test_load_staged_nonexistent(self, tmp_path):
        from agent.runtime import Runtime

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        session = runtime.create_session("test")

        assert session.load_staged("nonexistent") is None

    def test_end_session_cleanup(self, tmp_path):
        from agent.runtime import Runtime

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        session = runtime.create_session("test")
        session.stage("plan", {"x": 1})

        runtime.end_session(session.session_id, cleanup=True)
        assert session.session_id not in runtime.list_active_sessions()
        assert not session.working_dir.exists()

    def test_end_session_no_cleanup(self, tmp_path):
        from agent.runtime import Runtime

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        session = runtime.create_session("test")
        session.stage("plan", {"x": 1})

        runtime.end_session(session.session_id, cleanup=False)
        assert session.session_id not in runtime.list_active_sessions()
        assert session.working_dir.exists()

    def test_make_tool_context(self, tmp_path):
        from agent.runtime import Runtime
        from agent.tools.base import ToolContext

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        session = runtime.create_session("test")

        ctx = runtime.make_tool_context(session, sandbox=True)
        assert isinstance(ctx, ToolContext)
        assert ctx.session_id == session.session_id
        assert ctx.sandbox is True

    def test_stage_helpers(self, tmp_path):
        from agent.runtime import Runtime

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        session = runtime.create_session("test")

        runtime.stage_plan(session, {"steps": []})
        runtime.stage_execution(session, {"output": "done"})
        runtime.stage_evaluation(session, {"cib_passed": True})
        runtime.stage_reflection(session, {"what_worked": "all"})

        assert session.working_memory.plan == {"steps": []}
        assert session.working_memory.execution == {"output": "done"}
        assert session.working_memory.evaluation == {"cib_passed": True}
        assert session.working_memory.reflection == {"what_worked": "all"}

        # Files exist
        assert (session.working_dir / "plan.json").exists()
        assert (session.working_dir / "execution.json").exists()
        assert (session.working_dir / "evaluation.json").exists()
        assert (session.working_dir / "reflection.json").exists()

    def test_working_memory_to_dict(self, tmp_path):
        from agent.runtime import WorkingMemory

        wm = WorkingMemory(session_id="s1", episode_id="e1", user_input="hi")
        d = wm.to_dict()
        assert d["session_id"] == "s1"
        assert d["episode_id"] == "e1"
        assert d["user_input"] == "hi"
        assert "plan" in d
        assert "execution" in d


# ===========================================================================
# orchestrator.py
# ===========================================================================

class TestOrchestratorBasic:
    def test_run_without_tools_or_memory(self, tmp_path):
        from agent.runtime import Runtime
        from agent.orchestrator import Orchestrator, LoopResult

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        orch = Orchestrator(
            runtime=runtime,
            memory_manager=None,
            tool_registry=None,
            llm_client=None,
        )

        result = orch.run("hello world")
        assert isinstance(result, LoopResult)
        assert result.session_id.startswith("sess_")
        assert result.episode_id.startswith("ep_")
        # Without tools, execution is skipped — should still complete
        assert result.execution_output != ""

    def test_run_with_tools(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=True)
        result = orch.run("test query")

        assert result.session_id.startswith("sess_")
        assert result.episode_id.startswith("ep_")
        assert isinstance(result.success, bool)
        assert "cib_passed" in result.evaluation
        assert "phoenix_score" in result.evaluation
        assert "what_worked" in result.reflection

    def test_loop_result_fields(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=True)
        result = orch.run("compute 2+2")

        assert hasattr(result, "session_id")
        assert hasattr(result, "episode_id")
        assert hasattr(result, "success")
        assert hasattr(result, "execution_output")
        assert hasattr(result, "evaluation")
        assert hasattr(result, "reflection")
        assert hasattr(result, "retries")
        assert hasattr(result, "error")

    def test_staging_files_created(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=True)
        result = orch.run("test staging")

        # Session is cleaned up after run, but we can check the result
        assert result.session_id  # session was created
        # The working dir is cleaned up, so we just verify the loop ran
        assert result.execution_output != ""


class TestOrchestratorStages:
    def test_planning_stage(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=False)
        session = runtime.create_session("test plan")
        plan = orch._stage_planning("test plan", "general")

        assert plan is not None
        assert plan.step_count > 0
        assert plan.estimated_success >= 0.0

    def test_execution_no_registry(self, tmp_path):
        from agent.runtime import Runtime
        from agent.orchestrator import Orchestrator
        from agent.cognition.planner import Plan, PlanStep

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        orch = Orchestrator(runtime=runtime, tool_registry=None)

        plan = Plan(steps=[PlanStep(description="do something", tool="")])
        ctx = runtime.make_tool_context(runtime.create_session("test"))
        result = orch._stage_execution(plan, ctx)

        assert result["success"] is True
        assert "skipped" in result["output"].lower()

    def test_execution_with_tool(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=True)
        from agent.cognition.planner import Plan, PlanStep

        session = runtime.create_session("test")
        ctx = runtime.make_tool_context(session, sandbox=False)
        plan = Plan(steps=[
            PlanStep(description="print('hello')", tool="code_exec_sandboxed"),
        ])
        result = orch._stage_execution(plan, ctx)

        assert "steps_executed" in result
        assert result["steps_executed"] == 1

    def test_execution_tool_not_found(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=True)
        from agent.cognition.planner import Plan, PlanStep

        session = runtime.create_session("test")
        ctx = runtime.make_tool_context(session, sandbox=False)
        plan = Plan(steps=[
            PlanStep(description="test", tool="nonexistent_tool"),
        ])
        result = orch._stage_execution(plan, ctx)

        assert result["success"] is False
        assert "not found" in result["step_results"][0]["error"].lower()

    def test_evaluation_heuristic_success(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=False)
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[PlanStep(description="test")])
        eval_dict = orch._stage_evaluation("good output", "good output", plan, 0)

        assert "cib_passed" in eval_dict
        assert "phoenix_score" in eval_dict
        assert "pain_index" in eval_dict
        assert "status" in eval_dict

    def test_evaluation_heuristic_failure(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=False)
        from agent.cognition.planner import Plan, PlanStep

        plan = Plan(steps=[PlanStep(description="test")])
        # Pass error text as execution_output so heuristic detects failure
        eval_dict = orch._stage_evaluation("test task", "[ERROR: something failed]", plan, 0)

        # With error output, phoenix score should be lower
        assert eval_dict["phoenix_score"] < 0.5

    def test_reflection_stage(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=False)

        eval_dict = {
            "status": "Success",
            "success_score": 0.9,
            "pain_index": 0.1,
        }
        refl_dict = orch._stage_reflection("test task", "executed successfully", eval_dict)

        assert "what_worked" in refl_dict
        assert "what_failed" in refl_dict
        assert "next_hint" in refl_dict
        assert "causal_condition" in refl_dict
        assert "summary" in refl_dict
        assert "source" in refl_dict

    def test_reflection_failure(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=False)

        eval_dict = {
            "status": "Failure",
            "success_score": 0.1,
            "pain_index": 0.9,
        }
        refl_dict = orch._stage_reflection("test task", "execution failed", eval_dict)

        assert refl_dict["what_failed"] != ""
        assert refl_dict["next_hint"] != ""


class TestOrchestratorWithMemory:
    def test_full_loop_with_memory(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=True, with_memory=True)
        result = orch.run("test with memory")

        assert result.session_id
        assert result.episode_id
        # Episode should be stored in L1
        # (we can't easily verify without the store, but no error means it worked)

    def test_persist_episode(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=False, with_memory=True)
        session = runtime.create_session("persist test")

        eval_dict = {
            "status": "Success",
            "success_score": 0.85,
            "pain_index": 0.15,
            "cib_min_score": 0.96,
            "phoenix_score": 0.85,
            "domain_score": 0.85,
            "reflection_score": 0.85,
        }
        refl_dict = {
            "what_worked": "everything",
            "what_failed": "",
            "next_hint": "try harder",
            "causal_condition": "good plan",
        }

        # Should not raise
        orch._persist_episode(session, "test task", "output", eval_dict, refl_dict)


class TestOrchestratorRetry:
    def test_retry_count_tracked(self, tmp_path):
        orch, runtime = _make_orchestrator(tmp_path, with_tools=False)
        # Without LLM, heuristic phoenix_score=0.8 < 0.95, so evaluation
        # always fails and retries happen up to max_retries.
        result = orch.run("test retry")
        # max_retries=2 in _make_orchestrator, so retries should be 2
        assert result.retries == 2

    def test_max_retries_respected(self, tmp_path):
        from agent.runtime import Runtime
        from agent.orchestrator import Orchestrator

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        orch = Orchestrator(
            runtime=runtime,
            memory_manager=None,
            tool_registry=None,
            llm_client=None,
            max_retries=1,
        )
        result = orch.run("test")
        # Heuristic phoenix always fails (0.8 < 0.95), so retries = max_retries = 1
        assert result.retries == 1


# ===========================================================================
# main.py
# ===========================================================================

class TestMain:
    def test_run_query(self, tmp_path):
        from agent.main import run_query
        from agent.runtime import Runtime
        from agent.orchestrator import Orchestrator

        runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
        orch = Orchestrator(
            runtime=runtime,
            memory_manager=None,
            tool_registry=None,
            llm_client=None,
        )

        output = run_query(orch, "hello")
        assert "Session:" in output
        assert "Episode:" in output
        assert "Success:" in output
        assert "Result" in output

    def test_create_agent_no_memory(self, tmp_path, monkeypatch):
        """Test create_agent when memory manager fails to initialise."""
        from agent.main import create_agent

        # Point config to a nonexistent file to use defaults
        runtime, orchestrator = create_agent(
            config_path="nonexistent.yml",
            working_dir=str(tmp_path / "sessions"),
            enable_tools=False,
        )
        assert runtime is not None
        assert orchestrator is not None

    def test_create_agent_with_tools(self, tmp_path):
        from agent.main import create_agent

        runtime, orchestrator = create_agent(
            config_path="nonexistent.yml",
            working_dir=str(tmp_path / "sessions"),
            enable_tools=True,
        )
        assert orchestrator.tool_registry is not None
        assert "file_read" in orchestrator.tool_registry.list_names()

    def test_cli_single_query(self, tmp_path, monkeypatch):
        """Test CLI with --query flag."""
        from agent.main import main

        # Patch create_agent to avoid heavy MemoryManager init
        from agent.runtime import Runtime
        from agent.orchestrator import Orchestrator

        def mock_create_agent(*args, **kwargs):
            runtime = Runtime(base_working_dir=str(tmp_path / "sessions"))
            orch = Orchestrator(
                runtime=runtime,
                memory_manager=None,
                tool_registry=None,
                llm_client=None,
            )
            return runtime, orch

        monkeypatch.setattr("agent.main.create_agent", mock_create_agent)

        exit_code = main([
            "--query", "test query",
            "--config", "nonexistent.yml",
            "--working-dir", str(tmp_path / "sessions"),
            "--no-tools",
        ])
        assert exit_code == 0