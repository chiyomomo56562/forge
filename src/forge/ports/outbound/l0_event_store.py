from typing import Protocol

from forge.domain.memory import L0Event, L0SessionManifest


class L0EventStore(Protocol):
    """L0 원본 이벤트와 세션 manifest를 저장하는 outbound 경계.

    최종 수정일: 2026-07-31
    """

    def create_session(self, manifest: L0SessionManifest) -> None:
        """새 세션 manifest를 생성한다.

        Args:
            manifest: 만들 세션의 초기 상태.

        최종 수정일: 2026-07-31
        """
        ...

    def get_manifest(self, session_id: str) -> L0SessionManifest:
        """현재 세션 manifest를 읽는다.

        Args:
            session_id: 조회할 세션 ID.

        Returns:
            최신 sequence와 상태를 가진 manifest.

        최종 수정일: 2026-07-31
        """
        ...

    def append(self, event: L0Event) -> L0Event:
        """L0 이벤트를 idempotent하게 기록한다.

        Args:
            event: sequence와 event ID가 정해진 이벤트.

        Returns:
            저장됐거나 기존에 저장된 이벤트.

        최종 수정일: 2026-07-31
        """
        ...

    def list_events(self, session_id: str) -> tuple[L0Event, ...]:
        """세션의 이벤트를 sequence 순서로 반환한다.

        Args:
            session_id: 조회할 세션 ID.

        Returns:
            세션에 기록된 L0 이벤트 tuple.

        최종 수정일: 2026-07-31
        """
        ...

    def mark_completed(self, session_id: str) -> None:
        """Finalize가 끝난 세션을 완료 상태로 표시한다.

        Args:
            session_id: 완료 처리할 세션 ID.

        최종 수정일: 2026-07-31
        """
        ...
