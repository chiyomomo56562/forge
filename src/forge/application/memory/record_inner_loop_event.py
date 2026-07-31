from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from forge.domain.memory import L0Event, L0EventType
from forge.ports.outbound import L0EventStore


class RecordInnerLoopEventService:
    """현재 manifest의 sequence를 사용해 노드 이벤트를 append한다.

    최종 수정일: 2026-07-31
    """

    def __init__(self, store: L0EventStore) -> None:
        """노드 이벤트를 쓸 L0 store를 주입한다.

        Args:
            store: session manifest와 JSONL 이벤트를 관리하는 outbound port.

        최종 수정일: 2026-07-31
        """
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
        """호출자의 노드 결과를 다음 sequence의 L0 이벤트로 기록한다.

        Args:
            session_id: 이벤트를 추가할 Inner Loop 세션 ID.
            event_type: 기록할 의미 있는 노드 전이 유형.
            payload: 비밀값을 제거한 이벤트별 결과/참조 데이터.
            causation_id: 이 이벤트를 유발한 직전 이벤트의 선택적 ID.
            event_id: 재시도 idempotency에 사용할 선택적 안정 ID.

        Returns:
            실제로 저장된 L0 이벤트.

        최종 수정일: 2026-07-31
        """
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


"""Inner Loop 노드 전이를 L0 이벤트로 기록하는 application service.

최종 수정일: 2026-07-31
"""
