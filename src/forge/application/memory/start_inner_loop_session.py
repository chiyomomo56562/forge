from datetime import UTC, datetime
from uuid import uuid4

from forge.domain.memory import L0Event, L0EventType, L0SessionManifest
from forge.ports.outbound import L0EventStore


class StartInnerLoopSessionService:
    """세션/에피소드 ID를 고정하고 첫 ``session.started`` 이벤트를 남긴다.

    최종 수정일: 2026-07-31
    """

    def __init__(self, store: L0EventStore) -> None:
        """세션 상태를 저장할 L0 store를 주입한다.

        Args:
            store: manifest와 이벤트를 영속화할 outbound port 구현.

        최종 수정일: 2026-07-31
        """
        self._store = store

    def handle(
        self, *, task_request: str, task_category: str, settings_ref: str | None = None
    ) -> L0SessionManifest:
        """세션을 만들고 L1 매핑에 필요한 시작 정보를 기록한다.

        Args:
            task_request: 사용자의 안전하게 정규화된 작업 요청.
            task_category: producer가 결정한 작업 분류.
            settings_ref: 고정된 실행 설정/예산의 선택적 참조 ID.

        Returns:
            첫 이벤트까지 기록된 현재 세션 manifest.

        최종 수정일: 2026-07-31
        """
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


"""Inner Loop L0 세션을 시작하는 application service.

최종 수정일: 2026-07-31
"""
