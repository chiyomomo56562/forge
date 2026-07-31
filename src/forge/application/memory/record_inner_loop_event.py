from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from forge.domain.memory import L0Event, L0EventType
from forge.ports.outbound import L0EventStore


class RecordInnerLoopEventService:
    def __init__(self, store: L0EventStore) -> None:
        self._store = store

    def handle(
        self,
        *,
        session_id: str,
        event_type: L0EventType,
        payload: Mapping[str, Any],
        causation_id: str | None = None,
        event_id: str | None = None,
    ) -> L0Event:
        manifest = self._store.get_manifest(session_id)
        event = L0Event(
            event_id=event_id or f"evt_{uuid4().hex}",
            session_id=session_id,
            episode_id=manifest.episode_id,
            sequence=manifest.next_sequence,
            occurred_at=datetime.now(UTC),
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
        )
        return self._store.append(event)
