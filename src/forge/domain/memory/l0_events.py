"""Append-only L0 event contract."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from forge.domain.memory.errors import MemoryValidationError


class L0EventType(StrEnum):
    SESSION_STARTED = "session.started"
    PLAN_COMPLETED = "plan.completed"
    EXECUTION_STARTED = "execution.started"
    EXECUTION_COMPLETED = "execution.completed"
    EXECUTION_FAILED = "execution.failed"
    EVALUATION_COMPLETED = "evaluation.completed"
    REFLECTION_COMPLETED = "reflection.completed"
    EPISODE_PERSISTED = "episode.persisted"
    EPISODE_INDEX_DEFERRED = "episode.index_deferred"
    EPISODE_FAILED = "episode.failed"
    SESSION_COMPLETED = "session.completed"
    SESSION_FAILED = "session.failed"


_SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
}


def _validate_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise MemoryValidationError(
                    code="l0.invalid_payload", safe_message="Event payload is invalid."
                )
            if key.lower() in _SENSITIVE_KEYS:
                raise MemoryValidationError(
                    code="l0.sensitive_payload",
                    safe_message="Event payload contains sensitive data.",
                )
            _validate_payload(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _validate_payload(child)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise MemoryValidationError(
            code="l0.invalid_payload", safe_message="Event payload is invalid."
        )


@dataclass(frozen=True)
class L0SessionManifest:
    session_id: str
    episode_id: str
    created_at: datetime
    next_sequence: int = 0
    completed: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.session_id.startswith("ses_") or not self.episode_id.startswith("ep_"):
            raise MemoryValidationError(
                code="l0.invalid_session_identity", safe_message="Session identity is invalid."
            )
        if self.next_sequence < 0:
            raise MemoryValidationError(
                code="l0.invalid_sequence", safe_message="Event sequence is invalid."
            )


@dataclass(frozen=True)
class L0Event:
    event_id: str
    session_id: str
    episode_id: str
    sequence: int
    occurred_at: datetime
    event_type: L0EventType
    payload: Mapping[str, Any]
    causation_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.event_id.startswith("evt_"):
            raise MemoryValidationError(
                code="l0.invalid_event_id", safe_message="Event ID is invalid."
            )
        if not self.session_id.startswith("ses_") or not self.episode_id.startswith("ep_"):
            raise MemoryValidationError(
                code="l0.invalid_session_identity", safe_message="Session identity is invalid."
            )
        if self.sequence < 0:
            raise MemoryValidationError(
                code="l0.invalid_sequence", safe_message="Event sequence is invalid."
            )
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise MemoryValidationError(
                code="l0.invalid_occurred_at", safe_message="Event timestamp must be UTC-aware."
            )
        if self.causation_id is not None and not self.causation_id.startswith("evt_"):
            raise MemoryValidationError(
                code="l0.invalid_causation_id", safe_message="Event causation ID is invalid."
            )
        _validate_payload(self.payload)
