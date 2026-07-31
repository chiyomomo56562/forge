from forge.adapters.outbound.inner_loop import (
    DeterministicEvaluator,
    DeterministicExecutor,
    DeterministicPlanner,
    DeterministicReflector,
)
from forge.adapters.outbound.memory import JsonlL0EventStore
from forge.application.inner_loop import RunInnerLoopService
from forge.application.memory import (
    FinalizeEpisodeService,
    RecordInnerLoopEventService,
    StartInnerLoopSessionService,
)
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
