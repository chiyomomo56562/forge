"""세션별 L0 이벤트를 내구성 있는 JSONL로 저장한다.

최종 수정일: 2026-07-31
"""

import errno
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from forge.domain.memory import (
    L0Event,
    L0EventType,
    L0SessionManifest,
    MemoryInfrastructureError,
    MemoryValidationError,
    RetryableMemoryOperationError,
)


class JsonlL0EventStore:
    """L0 이벤트·manifest를 세션 디렉터리에 저장하는 outbound adapter.

    최종 수정일: 2026-07-31
    """

    def __init__(self, root_path: str | Path) -> None:
        """저장소의 세션 루트 경로를 초기화한다.

        Args:
            root_path: 각 ``<session_id>/events.jsonl`` 디렉터리를 둘 기준 경로.

        최종 수정일: 2026-07-31
        """
        self._root_path = Path(root_path)

    def create_session(self, manifest: L0SessionManifest) -> None:
        """새 세션 manifest를 원자적으로 만들거나 동일 요청을 idempotent 처리한다.

        Args:
            manifest: 생성할 세션 ID, Episode ID, 초기 sequence를 담은 manifest.

        Raises:
            MemoryValidationError: 같은 세션 ID가 다른 manifest를 이미 가질 때.
            MemoryInfrastructureError: 영구적인 파일 시스템 오류가 발생할 때.
            RetryableMemoryOperationError: 일시적인 파일 시스템 잠금이 발생할 때.

        최종 수정일: 2026-07-31
        """
        directory = self._session_dir(manifest.session_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "manifest.json"
            if path.exists():
                existing = self.get_manifest(manifest.session_id)
                if existing != manifest:
                    raise MemoryValidationError(
                        code="l0.session_conflict",
                        safe_message="Session already exists with different data.",
                    )
                return
            self._write_manifest(manifest)
        except OSError as exc:
            self._raise_io(exc, "Session storage could not be initialized.")

    def get_manifest(self, session_id: str) -> L0SessionManifest:
        """세션의 현재 sequence·완료 상태를 manifest에서 복원한다.

        Args:
            session_id: 읽을 L0 세션 식별자.

        Returns:
            저장된 ``L0SessionManifest``.

        최종 수정일: 2026-07-31
        """
        try:
            payload = self._read_json(self._session_dir(session_id) / "manifest.json")
            return L0SessionManifest(
                session_id=payload["session_id"],
                episode_id=payload["episode_id"],
                created_at=datetime.fromisoformat(payload["created_at"]),
                next_sequence=payload["next_sequence"],
                completed=payload["completed"],
                schema_version=payload["schema_version"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MemoryInfrastructureError(
                code="l0.invalid_manifest", safe_message="Session manifest is corrupted."
            ) from exc
        except OSError as exc:
            self._raise_io(exc, "Session manifest could not be read.")

    def append(self, event: L0Event) -> L0Event:
        """이벤트 한 건을 fsync한 JSONL 행으로 추가하고 다음 sequence를 갱신한다.

        Args:
            event: 호출자가 생성한 안정적 event ID와 sequence를 가진 L0 이벤트.

        Returns:
            새로 저장했거나 동일 event ID로 이미 저장된 이벤트.

        최종 수정일: 2026-07-31
        """
        manifest = self.get_manifest(event.session_id)
        if event.episode_id != manifest.episode_id:
            raise MemoryValidationError(
                code="l0.episode_mismatch", safe_message="Event episode does not match session."
            )
        events = self.list_events(event.session_id)
        for existing in events:
            if existing.event_id == event.event_id:
                if existing == event:
                    return existing
                raise MemoryValidationError(
                    code="l0.event_id_conflict",
                    safe_message="Event ID conflicts with stored event.",
                )
        if manifest.completed:
            raise MemoryValidationError(
                code="l0.session_completed", safe_message="Session is already complete."
            )
        if event.sequence != manifest.next_sequence:
            raise MemoryValidationError(
                code="l0.invalid_sequence", safe_message="Event sequence is invalid."
            )
        path = self._session_dir(event.session_id) / "events.jsonl"
        record = json.dumps(self._event_to_dict(event), separators=(",", ":"), sort_keys=True)
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(record + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._write_manifest(
                L0SessionManifest(
                    session_id=manifest.session_id,
                    episode_id=manifest.episode_id,
                    created_at=manifest.created_at,
                    next_sequence=manifest.next_sequence + 1,
                    completed=False,
                    schema_version=manifest.schema_version,
                )
            )
            return event
        except OSError as exc:
            self._raise_io(exc, "Event storage failed.")

    def list_events(self, session_id: str) -> tuple[L0Event, ...]:
        """세션의 JSONL 이벤트를 순서대로 읽고 손상 여부를 검증한다.

        Args:
            session_id: 조회할 L0 세션 식별자.

        Returns:
            sequence 오름차순의 불변 이벤트 tuple. 마지막 불완전 행은 복구를 위해 제외한다.

        최종 수정일: 2026-07-31
        """
        path = self._session_dir(session_id) / "events.jsonl"
        if not path.exists():
            return ()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            self._raise_io(exc, "Event storage could not be read.")
        events: list[L0Event] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                event = self._event_from_dict(json.loads(line))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if index == len(lines) - 1 and not path.read_bytes().endswith(b"\n"):
                    break
                raise MemoryInfrastructureError(
                    code="l0.corrupt_event_log", safe_message="Event log is corrupted."
                ) from exc
            if event.session_id != session_id or event.sequence != len(events):
                raise MemoryInfrastructureError(
                    code="l0.corrupt_event_log", safe_message="Event log is corrupted."
                )
            events.append(event)
        return tuple(events)

    def mark_completed(self, session_id: str) -> None:
        """L1 Finalize가 끝난 세션을 완료 상태로 전이한다.

        Args:
            session_id: 완료 처리할 L0 세션 식별자.

        최종 수정일: 2026-07-31
        """
        manifest = self.get_manifest(session_id)
        self._write_manifest(
            L0SessionManifest(
                session_id=manifest.session_id,
                episode_id=manifest.episode_id,
                created_at=manifest.created_at,
                next_sequence=manifest.next_sequence,
                completed=True,
                schema_version=manifest.schema_version,
            )
        )

    def _session_dir(self, session_id: str) -> Path:
        """세션 ID에 대응하는 저장 디렉터리를 계산한다.

        Args:
            session_id: 디렉터리 이름으로 사용할 세션 식별자.

        Returns:
            세션 전용 디렉터리 경로.

        최종 수정일: 2026-07-31
        """
        return self._root_path / session_id

    def _write_manifest(self, manifest: L0SessionManifest) -> None:
        """임시 파일과 rename으로 manifest 교체를 원자화한다.

        Args:
            manifest: 디스크에 반영할 최신 세션 상태.

        최종 수정일: 2026-07-31
        """
        path = self._session_dir(manifest.session_id) / "manifest.json"
        temporary = path.with_suffix(".json.tmp")
        payload = {
            "session_id": manifest.session_id,
            "episode_id": manifest.episode_id,
            "created_at": manifest.created_at.isoformat(),
            "next_sequence": manifest.next_sequence,
            "completed": manifest.completed,
            "schema_version": manifest.schema_version,
        }
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            self._raise_io(exc, "Session manifest could not be written.")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        """JSON 파일 하나를 dictionary로 역직렬화한다.

        Args:
            path: 읽을 JSON 파일의 경로.

        Returns:
            JSON object를 변환한 dictionary.

        최종 수정일: 2026-07-31
        """
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _event_to_dict(event: L0Event) -> dict[str, Any]:
        """도메인 이벤트를 JSONL에 쓸 평탄 dictionary로 변환한다.

        Args:
            event: 직렬화할 L0 도메인 이벤트.

        Returns:
            JSON 직렬화 가능한 이벤트 dictionary.

        최종 수정일: 2026-07-31
        """
        return {
            "event_id": event.event_id,
            "session_id": event.session_id,
            "episode_id": event.episode_id,
            "sequence": event.sequence,
            "occurred_at": event.occurred_at.isoformat(),
            "event_type": event.event_type.value,
            "payload": dict(event.payload),
            "causation_id": event.causation_id,
            "schema_version": event.schema_version,
        }

    @staticmethod
    def _event_from_dict(data: dict[str, Any]) -> L0Event:
        """JSONL dictionary를 검증되는 L0 도메인 이벤트로 복원한다.

        Args:
            data: JSONL 한 행에서 역직렬화한 dictionary.

        Returns:
            유효성 검증을 통과한 ``L0Event``.

        최종 수정일: 2026-07-31
        """
        return L0Event(
            event_id=data["event_id"],
            session_id=data["session_id"],
            episode_id=data["episode_id"],
            sequence=data["sequence"],
            occurred_at=datetime.fromisoformat(data["occurred_at"]),
            event_type=L0EventType(data["event_type"]),
            payload=data["payload"],
            causation_id=data.get("causation_id"),
            schema_version=data.get("schema_version", 1),
        )

    @staticmethod
    def _raise_io(exc: OSError, message: str) -> None:
        """파일 시스템 오류를 공개 메모리 오류 계약으로 정규화한다.

        Args:
            exc: 원래 발생한 운영체제 I/O 예외.
            message: 호출자에게 노출 가능한 안전한 오류 메시지.

        최종 수정일: 2026-07-31
        """
        if exc.errno in {errno.EAGAIN, errno.EBUSY, errno.ETXTBSY}:
            raise RetryableMemoryOperationError(
                code="l0.temporarily_unavailable", safe_message=message
            ) from exc
        raise MemoryInfrastructureError(code="l0.storage_failed", safe_message=message) from exc
