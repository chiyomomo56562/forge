"""Immutable approval records for tool calls that require human confirmation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class ToolApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


@dataclass(frozen=True)
class ToolApprovalRequest:
    """A proposed invocation, bound to an exact session and canonical argument digest."""

    call_id: str
    session_id: str
    tool_id: str
    policy_id: str
    arguments_sha256: str
    arguments_summary: Mapping[str, object]
    requested_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not all((self.call_id, self.session_id, self.tool_id, self.policy_id)):
            raise ValueError("Tool approval request IDs are required")
        if len(self.arguments_sha256) != 64:
            raise ValueError("Tool approval argument digest must be SHA-256")
        if self.requested_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Tool approval timestamps must be timezone-aware")
        if self.expires_at <= self.requested_at:
            raise ValueError("Tool approval expiry must be after request time")


@dataclass(frozen=True)
class ToolApprovalRecord:
    request: ToolApprovalRequest
    status: ToolApprovalStatus
    reviewer: str | None = None
    decided_at: datetime | None = None
    consumed_at: datetime | None = None
    safe_error_code: str | None = None

    @property
    def is_approved(self) -> bool:
        return self.status is ToolApprovalStatus.APPROVED


class ToolApprovalStore(Protocol):
    """Durable, append-only approval state for side-effecting tool calls."""

    def request(self, request: ToolApprovalRequest) -> ToolApprovalRecord: ...

    def get(self, call_id: str, *, now: datetime) -> ToolApprovalRecord | None: ...

    def list_pending(self, *, now: datetime) -> Sequence[ToolApprovalRecord]: ...

    def decide(
        self, call_id: str, *, approved: bool, reviewer: str, now: datetime
    ) -> ToolApprovalRecord: ...

    def consume(
        self,
        call_id: str,
        *,
        session_id: str,
        arguments_sha256: str,
        now: datetime,
    ) -> ToolApprovalRecord: ...
