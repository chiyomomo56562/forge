from datetime import UTC, datetime

import pytest

from forge.adapters.outbound.memory import JsonlL0EventStore
from forge.domain.memory import L0Event, L0EventType, L0SessionManifest, MemoryInfrastructureError


def _manifest() -> L0SessionManifest:
    return L0SessionManifest("ses_a", "ep_a", datetime.now(UTC))


def _event(sequence: int = 0) -> L0Event:
    return L0Event(
        f"evt_{sequence}",
        "ses_a",
        "ep_a",
        sequence,
        datetime.now(UTC),
        L0EventType.SESSION_STARTED,
        {"task_request": "x", "task_category": "test"},
    )


def test_append_is_durable_and_idempotent(tmp_path) -> None:
    store = JsonlL0EventStore(tmp_path)
    store.create_session(_manifest())
    event = _event()
    assert store.append(event) == event
    assert JsonlL0EventStore(tmp_path).append(event) == event
    assert JsonlL0EventStore(tmp_path).list_events("ses_a") == (event,)
    assert store.get_manifest("ses_a").next_sequence == 1


def test_partial_final_line_is_ignored_but_middle_corruption_fails(tmp_path) -> None:
    store = JsonlL0EventStore(tmp_path)
    store.create_session(_manifest())
    store.append(_event())
    path = tmp_path / "ses_a" / "events.jsonl"
    path.write_text(path.read_text() + '{"event_id":', encoding="utf-8")
    assert len(store.list_events("ses_a")) == 1
    path.write_text("not-json\n{}\n", encoding="utf-8")
    with pytest.raises(MemoryInfrastructureError, match="Event log"):
        store.list_events("ses_a")
