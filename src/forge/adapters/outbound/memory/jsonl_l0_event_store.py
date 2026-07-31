"""Durable per-session JSONL storage for L0 events."""

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
    def __init__(self, root_path: str | Path) -> None:
        self._root_path = Path(root_path)

    def create_session(self, manifest: L0SessionManifest) -> None:
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
        return self._root_path / session_id

    def _write_manifest(self, manifest: L0SessionManifest) -> None:
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
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _event_to_dict(event: L0Event) -> dict[str, Any]:
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
        if exc.errno in {errno.EAGAIN, errno.EBUSY, errno.ETXTBSY}:
            raise RetryableMemoryOperationError(
                code="l0.temporarily_unavailable", safe_message=message
            ) from exc
        raise MemoryInfrastructureError(code="l0.storage_failed", safe_message=message) from exc
