"""Append-only JSONL persistence for one-time tool approvals."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from forge.ports.outbound import (
    ToolApprovalRecord,
    ToolApprovalRequest,
    ToolApprovalStatus,
)


class JsonlToolApprovalStore:
    """Rebuild approval state from immutable request, decision, and consume events."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def request(self, request: ToolApprovalRequest) -> ToolApprovalRecord:
        existing = self._records().get(request.call_id)
        if existing is not None:
            if existing.request != request:
                raise ValueError("tool.approval_call_id_conflict")
            return existing
        record = ToolApprovalRecord(request, ToolApprovalStatus.PENDING)
        self._append("requested", record)
        return record

    def get(self, call_id: str, *, now: datetime) -> ToolApprovalRecord | None:
        record = self._records().get(call_id)
        return self._effective(record, now) if record else None

    def list_pending(self, *, now: datetime) -> Sequence[ToolApprovalRecord]:
        return tuple(
            record
            for record in (self._effective(item, now) for item in self._records().values())
            if record.status is ToolApprovalStatus.PENDING
        )

    def decide(
        self, call_id: str, *, approved: bool, reviewer: str, now: datetime
    ) -> ToolApprovalRecord:
        if not reviewer.strip():
            raise ValueError("tool.approval_reviewer_required")
        record = self.get(call_id, now=now)
        if record is None:
            raise ValueError("tool.approval_unknown")
        if record.status is not ToolApprovalStatus.PENDING:
            return ToolApprovalRecord(
                record.request,
                record.status,
                record.reviewer,
                record.decided_at,
                record.consumed_at,
                "tool.approval_not_pending",
            )
        decided = ToolApprovalRecord(
            record.request,
            ToolApprovalStatus.APPROVED if approved else ToolApprovalStatus.DENIED,
            reviewer,
            now,
        )
        self._append("decided", decided)
        return decided

    def consume(
        self,
        call_id: str,
        *,
        session_id: str,
        arguments_sha256: str,
        now: datetime,
    ) -> ToolApprovalRecord:
        record = self.get(call_id, now=now)
        if record is None:
            return self._rejected_unknown(call_id)
        if record.status is not ToolApprovalStatus.APPROVED:
            return ToolApprovalRecord(
                record.request,
                record.status,
                record.reviewer,
                record.decided_at,
                record.consumed_at,
                "tool.approval_not_approved",
            )
        if (
            record.request.session_id != session_id
            or record.request.arguments_sha256 != arguments_sha256
        ):
            return ToolApprovalRecord(
                record.request,
                record.status,
                record.reviewer,
                record.decided_at,
                None,
                "tool.approval_binding_mismatch",
            )
        consumed = ToolApprovalRecord(
            record.request,
            ToolApprovalStatus.CONSUMED,
            record.reviewer,
            record.decided_at,
            now,
        )
        self._append("consumed", consumed)
        return consumed

    @staticmethod
    def _effective(record: ToolApprovalRecord, now: datetime) -> ToolApprovalRecord:
        if record.status is ToolApprovalStatus.PENDING and now >= record.request.expires_at:
            return ToolApprovalRecord(
                record.request,
                ToolApprovalStatus.EXPIRED,
                safe_error_code="tool.approval_expired",
            )
        return record

    @staticmethod
    def _rejected_unknown(call_id: str) -> ToolApprovalRecord:
        now = datetime.now(UTC)
        request = ToolApprovalRequest(
            call_id=call_id,
            session_id="unknown",
            tool_id="unknown",
            policy_id="unknown",
            arguments_sha256="0" * 64,
            arguments_summary={},
            requested_at=now,
            expires_at=now + timedelta(days=1),
        )
        return ToolApprovalRecord(
            request,
            ToolApprovalStatus.DENIED,
            safe_error_code="tool.approval_unknown",
        )

    def _records(self) -> dict[str, ToolApprovalRecord]:
        if not self._path.exists():
            return {}
        records: dict[str, ToolApprovalRecord] = {}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            record = self._record_from_payload(payload)
            records[record.request.call_id] = record
        return records

    def _append(self, event: str, record: ToolApprovalRecord) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"event": event, "record": self._record_to_payload(record)}
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _record_to_payload(record: ToolApprovalRecord) -> dict[str, object]:
        request = record.request
        return {
            "call_id": request.call_id,
            "session_id": request.session_id,
            "tool_id": request.tool_id,
            "policy_id": request.policy_id,
            "arguments_sha256": request.arguments_sha256,
            "arguments_summary": dict(request.arguments_summary),
            "requested_at": request.requested_at.isoformat(),
            "expires_at": request.expires_at.isoformat(),
            "status": record.status.value,
            "reviewer": record.reviewer,
            "decided_at": record.decided_at.isoformat() if record.decided_at else None,
            "consumed_at": record.consumed_at.isoformat() if record.consumed_at else None,
            "safe_error_code": record.safe_error_code,
        }

    @staticmethod
    def _record_from_payload(payload: Mapping[str, Any]) -> ToolApprovalRecord:
        value = payload["record"]
        if not isinstance(value, Mapping):
            raise ValueError("tool.approval_corrupt_log")
        request = ToolApprovalRequest(
            call_id=str(value["call_id"]),
            session_id=str(value["session_id"]),
            tool_id=str(value["tool_id"]),
            policy_id=str(value["policy_id"]),
            arguments_sha256=str(value["arguments_sha256"]),
            arguments_summary=dict(value["arguments_summary"]),
            requested_at=datetime.fromisoformat(str(value["requested_at"])),
            expires_at=datetime.fromisoformat(str(value["expires_at"])),
        )
        return ToolApprovalRecord(
            request,
            ToolApprovalStatus(str(value["status"])),
            str(value["reviewer"]) if value.get("reviewer") else None,
            datetime.fromisoformat(str(value["decided_at"])) if value.get("decided_at") else None,
            datetime.fromisoformat(str(value["consumed_at"])) if value.get("consumed_at") else None,
            str(value["safe_error_code"]) if value.get("safe_error_code") else None,
        )
