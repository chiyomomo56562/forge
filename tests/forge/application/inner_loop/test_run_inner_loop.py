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
from forge.domain.inner_loop import InnerLoopPlan, PlanStep
from forge.domain.memory import IndexState, L0EventType, PersistEpisodeResult


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
