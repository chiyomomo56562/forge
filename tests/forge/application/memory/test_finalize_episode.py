import pytest

from forge.adapters.outbound.memory import JsonlL0EventStore
from forge.application.memory import (
    FinalizeEpisodeService,
    RecordInnerLoopEventService,
    StartInnerLoopSessionService,
)
from forge.domain.memory import L0EventType, MemoryValidationError


class Repository:
    def __init__(self) -> None:
        self.saved = None

    def save(self, episode):
        self.saved = episode
        return episode


def _record_ready_session(tmp_path):
    store = JsonlL0EventStore(tmp_path)
    session = StartInnerLoopSessionService(store).handle(
        task_request="repair file", task_category="coding"
    )
    record = RecordInnerLoopEventService(store)
    record.handle(
        session_id=session.session_id,
        event_type=L0EventType.EXECUTION_FAILED,
        payload={"tool_names": ["shell"]},
    )
    record.handle(
        session_id=session.session_id,
        event_type=L0EventType.EXECUTION_COMPLETED,
        payload={
            "summary": "done",
            "outcome": "success",
            "tool_names": ["shell", "editor"],
            "attempt": 2,
            "is_terminal": True,
        },
    )
    record.handle(
        session_id=session.session_id,
        event_type=L0EventType.EVALUATION_COMPLETED,
        payload={
            "success_score": 1.0,
            "cib_score": 0.9,
            "cib_evaluation_status": "evaluated",
            "status": "completed",
            "retryable": False,
            "promotion_eligibility": "eligible",
            "reasons": [],
            "evidence_complete": True,
        },
    )
    record.handle(
        session_id=session.session_id,
        event_type=L0EventType.REFLECTION_COMPLETED,
        payload={"what_worked": "test", "what_failed": "", "next_hint": "", "causal_condition": ""},
    )
    return store, session


def test_finalize_uses_only_pre_finalize_evidence_and_reduces_execution(tmp_path) -> None:
    store, session = _record_ready_session(tmp_path)
    repository = Repository()
    episode = FinalizeEpisodeService(store, repository).handle(session.session_id)
    assert episode.execution.summary == "done"
    assert episode.execution.tool_names == ("shell", "editor")
    event_types = [event.event_type for event in store.list_events(session.session_id)]
    assert event_types[-2:] == [L0EventType.EPISODE_PERSISTED, L0EventType.SESSION_COMPLETED]
    assert all(
        ref
        not in {
            store.list_events(session.session_id)[-1].event_id,
            store.list_events(session.session_id)[-2].event_id,
        }
        for ref in episode.raw_event_refs
    )


def test_finalize_rejects_missing_terminal_execution(tmp_path) -> None:
    store = JsonlL0EventStore(tmp_path)
    session = StartInnerLoopSessionService(store).handle(task_request="x", task_category="test")
    with pytest.raises(MemoryValidationError) as error:
        FinalizeEpisodeService(store, Repository()).handle(session.session_id)
    assert error.value.code == "l0.invalid_terminal_execution"
