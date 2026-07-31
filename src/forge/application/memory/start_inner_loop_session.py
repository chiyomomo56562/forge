from datetime import UTC, datetime
from uuid import uuid4

from forge.domain.memory import L0Event, L0EventType, L0SessionManifest
from forge.ports.outbound import L0EventStore


class StartInnerLoopSessionService:
    def __init__(self, store: L0EventStore) -> None:
        self._store = store

    def handle(
        self, *, task_request: str, task_category: str, settings_ref: str | None = None
    ) -> L0SessionManifest:
        now = datetime.now(UTC)
        manifest = L0SessionManifest(
            session_id=f"ses_{uuid4().hex}", episode_id=f"ep_{uuid4().hex}", created_at=now
        )
        self._store.create_session(manifest)
        payload = {"task_request": task_request, "task_category": task_category}
        if settings_ref is not None:
            payload["settings_ref"] = settings_ref
        self._store.append(
            L0Event(
                event_id=f"evt_{uuid4().hex}",
                session_id=manifest.session_id,
                episode_id=manifest.episode_id,
                sequence=0,
                occurred_at=now,
                event_type=L0EventType.SESSION_STARTED,
                payload=payload,
            )
        )
        return self._store.get_manifest(manifest.session_id)
