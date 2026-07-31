"""Append-only L0 이벤트의 도메인 계약.

최종 수정일: 2026-07-31
"""

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
    EXECUTION_SUMMARY_COMPLETED = "execution.summary.completed"
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
    """payload가 JSON 안전 값이며 민감 field를 포함하지 않는지 재귀 검사한다.

    Args:
        value: 이벤트 payload 전체 또는 그 하위 값.

    Raises:
        MemoryValidationError: JSON으로 표현할 수 없거나 금지된 민감 key가 있을 때.

    최종 수정일: 2026-07-31
    """
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
    """세션의 다음 sequence와 완료 상태를 보존하는 저장소 값.

    최종 수정일: 2026-07-31
    """

    session_id: str
    episode_id: str
    created_at: datetime
    next_sequence: int = 0
    completed: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        """세션/에피소드 ID 접두사와 다음 sequence의 유효성을 검증한다.

        Args:
            없음. dataclass field를 직접 검증한다.

        최종 수정일: 2026-07-31
        """
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
    """Inner Loop의 의미 있는 노드 전이를 나타내는 불변 원본 이벤트.

    최종 수정일: 2026-07-31
    """

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
        """이벤트 envelope, UTC 시각, 인과 ID, payload 안전성을 검증한다.

        Args:
            없음. dataclass field를 직접 검증한다.

        최종 수정일: 2026-07-31
        """
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
