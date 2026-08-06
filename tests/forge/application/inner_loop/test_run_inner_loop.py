from collections.abc import Mapping, Sequence

from forge.adapters.outbound.inner_loop import (
    DeterministicEvaluator,
    DeterministicExecutor,
    DeterministicPlanner,
    DeterministicReflector,
)
from forge.adapters.outbound.memory import JsonlL0EventStore
from forge.adapters.outbound.tools import (
    BuiltinToolRegistry,
    RegistryPlanStepExecutor,
    StaticToolAuthorizationPolicy,
)
from forge.application.inner_loop import RunInnerLoopService
from forge.application.memory import (
    FinalizeEpisodeService,
    RecordInnerLoopEventService,
    StartInnerLoopSessionService,
)
from forge.domain.inner_loop import InnerLoopPlan, PlanStep, ToolExecution
from forge.domain.memory import ExecutionOutcome, IndexState, L0EventType, PersistEpisodeResult


class Repository:
    def __init__(self) -> None:
        self.episode = None

    def save(self, episode):
        self.episode = episode
        return PersistEpisodeResult(episode.episode_id, IndexState.INDEXED)


def test_deterministic_inner_loop_records_evidence_and_finalizes_episode(tmp_path) -> None:
    store = JsonlL0EventStore(tmp_path)
    repository = Repository()
    service = RunInnerLoopService(
        StartInnerLoopSessionService(store),
        RecordInnerLoopEventService(store),
        FinalizeEpisodeService(store, repository),
        DeterministicPlanner(),
        DeterministicExecutor(),
        DeterministicEvaluator(),
        DeterministicReflector(),
    )

    result = service.handle(task_request="write a safe answer", task_category="test")

    assert repository.episode is not None
    assert repository.episode.episode_id == result.episode_id
    assert repository.episode.execution.outcome.value == "completed"
    events = store.list_events(result.session_id)
    assert [event.event_type for event in events] == [
        L0EventType.SESSION_STARTED,
        L0EventType.PLAN_COMPLETED,
        L0EventType.EXECUTION_STARTED,
        L0EventType.EXECUTION_COMPLETED,
        L0EventType.EXECUTION_SUMMARY_COMPLETED,
        L0EventType.EVALUATION_COMPLETED,
        L0EventType.REFLECTION_COMPLETED,
        L0EventType.EPISODE_PERSISTED,
        L0EventType.SESSION_COMPLETED,
    ]
    terminal_ids = {events[-1].event_id, events[-2].event_id}
    assert terminal_ids.isdisjoint(repository.episode.raw_event_refs)


class MutationPlanner:
    """승인 없는 patch 단계의 L0/L1 흐름을 검증하는 test planner.

    최종 수정일: 2026-07-31
    """

    def create_plan(
        self, *, task_request: str, context_episode_ids: tuple[str, ...]
    ) -> InnerLoopPlan:
        """항상 mutation 도구를 포함한 단일 단계 계획을 만든다.

        최종 수정일: 2026-07-31
        """
        del task_request, context_episode_ids
        return InnerLoopPlan(
            "Try a denied change.",
            (PlanStep("patch", "Attempt patch", "workspace.apply_patch", {"patch": "ignored"}),),
        )


def test_denied_tool_is_recorded_as_halted_episode(tmp_path) -> None:
    """DENIED 도구 결과가 L0 failed와 L1 halted outcome으로 연결되는지 검증한다.

    최종 수정일: 2026-07-31
    """
    store = JsonlL0EventStore(tmp_path / "l0")
    repository = Repository()
    service = RunInnerLoopService(
        StartInnerLoopSessionService(store),
        RecordInnerLoopEventService(store),
        FinalizeEpisodeService(store, repository),
        MutationPlanner(),
        RegistryPlanStepExecutor(BuiltinToolRegistry(tmp_path), StaticToolAuthorizationPolicy()),
        DeterministicEvaluator(),
        DeterministicReflector(),
    )

    result = service.handle(task_request="change", task_category="test")

    assert result.outcome.value == "halted"
    assert repository.episode is not None
    assert repository.episode.execution.outcome.value == "halted"
    events = store.list_events(result.session_id)
    failed = next(event for event in events if event.event_type is L0EventType.EXECUTION_FAILED)
    assert failed.payload["safe_error_code"] == "tool.approval_required"
    assert failed.payload["audit_details"]["tool_status"] == "denied"


class FeedbackPlanner:
    def __init__(
        self,
        initial_plan: InnerLoopPlan,
        feedback_plan: InnerLoopPlan | None = None,
        *,
        fail_on_feedback: bool = False,
    ) -> None:
        self.initial_plan = initial_plan
        self.feedback_plan = feedback_plan or initial_plan
        self.fail_on_feedback = fail_on_feedback
        self.feedback_calls: list[dict[str, object]] = []

    def create_plan(
        self, *, task_request: str, context_episode_ids: Sequence[str]
    ) -> InnerLoopPlan:
        del task_request, context_episode_ids
        return self.initial_plan

    def create_plan_after_feedback(
        self,
        *,
        task_request: str,
        context_episode_ids: Sequence[str],
        last_execution: ToolExecution,
        feedback: Mapping[str, object],
        feedback_count: int,
    ) -> InnerLoopPlan:
        del task_request, context_episode_ids, last_execution
        self.feedback_calls.append({"feedback": dict(feedback), "feedback_count": feedback_count})
        if self.fail_on_feedback:
            raise RuntimeError("planner unavailable")
        return self.feedback_plan


class StepOutcomeExecutor:
    def __init__(self, outcomes: Mapping[str, ToolExecution]) -> None:
        self.outcomes = dict(outcomes)
        self.steps: list[PlanStep] = []

    def execute(self, step: PlanStep, *, session_id: str = "", attempt: int = 0) -> ToolExecution:
        del session_id, attempt
        self.steps.append(step)
        return self.outcomes[step.step_id]


class OriginalPortPlanner:
    def __init__(self, plan: InnerLoopPlan) -> None:
        self.plan = plan
        self.calls = 0

    def create_plan(
        self, *, task_request: str, context_episode_ids: Sequence[str]
    ) -> InnerLoopPlan:
        del task_request, context_episode_ids
        self.calls += 1
        return self.plan


def _service(tmp_path, planner, executor, *, max_feedback_cycles: int = 0) -> RunInnerLoopService:
    store = JsonlL0EventStore(tmp_path / "l0")
    repository = Repository()
    service = RunInnerLoopService(
        StartInnerLoopSessionService(store),
        RecordInnerLoopEventService(store),
        FinalizeEpisodeService(store, repository),
        planner,
        executor,
        DeterministicEvaluator(),
        DeterministicReflector(),
        max_feedback_cycles=max_feedback_cycles,
    )
    service.store = store
    service.repository = repository
    return service


def test_failed_execution_feedback_replans_and_then_completes(tmp_path) -> None:
    initial_plan = InnerLoopPlan(
        "Use the first tool.",
        (PlanStep("read_bad", "Read missing file", "workspace.read_file", {"path": "missing"}),),
    )
    feedback_plan = InnerLoopPlan(
        "Use a safer fallback.",
        (PlanStep("list_ok", "List files instead", "workspace.list_files", {"path": "."}),),
    )
    planner = FeedbackPlanner(initial_plan, feedback_plan)
    executor = StepOutcomeExecutor(
        {
            "read_bad": ToolExecution(
                "read_bad",
                "Read failed.",
                ExecutionOutcome.FAILED,
                ("workspace.read_file",),
                safe_error_code="tool.file_not_found",
                output={"path": "missing"},
            ),
            "list_ok": ToolExecution(
                "list_ok",
                "Listed files.",
                ExecutionOutcome.COMPLETED,
                ("workspace.list_files",),
                output={"files": ["README.md"]},
            ),
        }
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="inspect workspace", task_category="test")

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert [step.step_id for step in executor.steps] == ["read_bad", "list_ok"]
    assert len(planner.feedback_calls) == 1
    feedback_call = planner.feedback_calls[0]
    assert feedback_call["feedback_count"] == 1
    feedback = feedback_call["feedback"]
    assert feedback["tool_call_id"] == "read_bad"
    assert feedback["name"] == "workspace.read_file"
    assert feedback["status"] == "failed"
    assert "tool.file_not_found" in feedback["summary"]
    events = service.store.list_events(result.session_id)
    assert [event.event_type for event in events].count(L0EventType.PLAN_COMPLETED) == 2
    assert [event.event_type for event in events].count(L0EventType.EXECUTION_FAILED) == 1
    assert [event.event_type for event in events].count(L0EventType.EXECUTION_COMPLETED) == 1


def test_completed_plan_does_not_request_feedback_replan(tmp_path) -> None:
    plan = InnerLoopPlan("Already sufficient.", (PlanStep("ok", "Complete", None, {}),))
    planner = FeedbackPlanner(plan)
    executor = StepOutcomeExecutor(
        {"ok": ToolExecution("ok", "Complete", ExecutionOutcome.COMPLETED)}
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="answer", task_category="test")

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert planner.feedback_calls == []
    assert [step.step_id for step in executor.steps] == ["ok"]


def test_denied_execution_safe_stops_without_replanning(tmp_path) -> None:
    plan = InnerLoopPlan(
        "Denied mutation.",
        (PlanStep("patch", "Patch workspace", "workspace.apply_patch", {"patch": "ignored"}),),
    )
    planner = FeedbackPlanner(plan)
    executor = StepOutcomeExecutor(
        {
            "patch": ToolExecution(
                "patch",
                "Tool invocation was denied.",
                ExecutionOutcome.HALTED,
                ("workspace.apply_patch",),
                safe_error_code="tool.approval_required",
            )
        }
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="change file", task_category="test")

    assert result.outcome is ExecutionOutcome.HALTED
    assert planner.feedback_calls == []
    assert [step.step_id for step in executor.steps] == ["patch"]


def test_protocol_failure_safe_stops_without_ordinary_feedback_count(tmp_path) -> None:
    plan = InnerLoopPlan(
        "Malformed tool protocol.",
        (PlanStep("tool_protocol", "Handle malformed tool call", "tool_protocol", {}),),
    )
    planner = FeedbackPlanner(plan)
    executor = StepOutcomeExecutor(
        {
            "tool_protocol": ToolExecution(
                "tool_protocol",
                "Tool-call protocol failure.",
                ExecutionOutcome.FAILED,
                ("tool_protocol",),
                safe_error_code="tool.protocol_failure",
            )
        }
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="use tool", task_category="test")

    assert result.outcome is ExecutionOutcome.FAILED
    assert planner.feedback_calls == []
    events = service.store.list_events(result.session_id)
    assert [event.event_type for event in events].count(L0EventType.PLAN_COMPLETED) == 1


def test_feedback_limit_safe_stops_after_bounded_replan(tmp_path) -> None:
    initial_plan = InnerLoopPlan("First attempt.", (PlanStep("fail_1", "Fail once", None, {}),))
    feedback_plan = InnerLoopPlan("Second attempt.", (PlanStep("fail_2", "Fail twice", None, {}),))
    planner = FeedbackPlanner(initial_plan, feedback_plan)
    executor = StepOutcomeExecutor(
        {
            "fail_1": ToolExecution("fail_1", "First failure.", ExecutionOutcome.FAILED),
            "fail_2": ToolExecution("fail_2", "Second failure.", ExecutionOutcome.FAILED),
        }
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="recover", task_category="test")

    assert result.outcome is ExecutionOutcome.FAILED
    assert len(planner.feedback_calls) == 1
    assert [step.step_id for step in executor.steps] == ["fail_1", "fail_2"]
    events = service.store.list_events(result.session_id)
    assert [event.event_type for event in events].count(L0EventType.PLAN_COMPLETED) == 2
    assert [event.event_type for event in events].count(L0EventType.EXECUTION_FAILED) == 2


def test_original_planner_port_remains_compatible_and_safe_stops(tmp_path) -> None:
    plan = InnerLoopPlan("Original port.", (PlanStep("fail", "Fail", None, {}),))
    planner = OriginalPortPlanner(plan)
    executor = StepOutcomeExecutor(
        {"fail": ToolExecution("fail", "Failure.", ExecutionOutcome.FAILED)}
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="recover", task_category="test")

    assert result.outcome is ExecutionOutcome.FAILED
    assert planner.calls == 1
    assert [step.step_id for step in executor.steps] == ["fail"]


def test_feedback_planner_error_safe_stops_through_planning_failure(tmp_path) -> None:
    initial_plan = InnerLoopPlan("First attempt.", (PlanStep("fail", "Fail", None, {}),))
    planner = FeedbackPlanner(initial_plan, fail_on_feedback=True)
    executor = StepOutcomeExecutor(
        {"fail": ToolExecution("fail", "Failure.", ExecutionOutcome.FAILED)}
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="recover", task_category="test")

    assert result.outcome is ExecutionOutcome.FAILED
    assert len(planner.feedback_calls) == 1
    assert [step.step_id for step in executor.steps] == ["fail"]
    assert service.repository.episode.execution.summary == "Planning failed before execution."


def test_dependency_graph_runs_ready_steps_before_later_dependents(tmp_path) -> None:
    plan = InnerLoopPlan(
        "Use discovered input.",
        (
            PlanStep("read", "Read result", None, {}, depends_on=("list",)),
            PlanStep("list", "List files", None, {}),
        ),
    )
    executor = StepOutcomeExecutor(
        {
            "list": ToolExecution("list", "Listed.", ExecutionOutcome.COMPLETED),
            "read": ToolExecution("read", "Read.", ExecutionOutcome.COMPLETED),
        }
    )
    service = _service(tmp_path, OriginalPortPlanner(plan), executor)

    result = service.handle(task_request="inspect")

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert [step.step_id for step in executor.steps] == ["list", "read"]


def test_invalid_dependency_graph_fails_before_tool_execution(tmp_path) -> None:
    plan = InnerLoopPlan(
        "Invalid cycle.",
        (
            PlanStep("first", "First", None, {}, depends_on=("second",)),
            PlanStep("second", "Second", None, {}, depends_on=("first",)),
        ),
    )
    executor = StepOutcomeExecutor({})
    service = _service(tmp_path, OriginalPortPlanner(plan), executor)

    result = service.handle(task_request="inspect")

    assert result.outcome is ExecutionOutcome.FAILED
    assert executor.steps == []


def test_replan_preserves_completed_steps_and_replaces_only_remaining_work(tmp_path) -> None:
    initial = InnerLoopPlan(
        "Initial graph.",
        (
            PlanStep("prepare", "Prepare", None, {}),
            PlanStep("fail", "Fail", None, {}, depends_on=("prepare",)),
        ),
    )
    replacement = InnerLoopPlan(
        "Recovery graph.",
        (PlanStep("recover", "Recover", None, {}, depends_on=("prepare",)),),
    )
    planner = FeedbackPlanner(initial, replacement)
    executor = StepOutcomeExecutor(
        {
            "prepare": ToolExecution("prepare", "Prepared.", ExecutionOutcome.COMPLETED),
            "fail": ToolExecution("fail", "Failed.", ExecutionOutcome.FAILED),
            "recover": ToolExecution("recover", "Recovered.", ExecutionOutcome.COMPLETED),
        }
    )
    service = _service(tmp_path, planner, executor, max_feedback_cycles=1)

    result = service.handle(task_request="recover")

    assert result.outcome is ExecutionOutcome.COMPLETED
    assert [step.step_id for step in executor.steps] == ["prepare", "fail", "recover"]
    feedback = planner.feedback_calls[0]["feedback"]
    assert feedback["plan"]["steps"][0]["status"] == "succeeded"
