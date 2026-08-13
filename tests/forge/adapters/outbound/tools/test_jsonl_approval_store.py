from datetime import UTC, datetime, timedelta

from forge.adapters.outbound.tools import JsonlToolApprovalStore
from forge.ports.outbound import ToolApprovalRequest, ToolApprovalStatus


def _request(now: datetime, *, call_id: str = "call-1") -> ToolApprovalRequest:
    return ToolApprovalRequest(
        call_id=call_id,
        session_id="session-1",
        tool_id="workspace.apply_patch",
        policy_id="file_write",
        arguments_sha256="a" * 64,
        arguments_summary={"patch": {"sha256": "a" * 64, "length": 12}},
        requested_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def test_approval_is_bound_to_session_and_arguments_then_consumed_once(tmp_path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    store = JsonlToolApprovalStore(tmp_path / "approvals.jsonl")
    store.request(_request(now))

    approved = store.decide("call-1", approved=True, reviewer="reviewer", now=now)
    assert approved.status is ToolApprovalStatus.APPROVED

    store = JsonlToolApprovalStore(tmp_path / "approvals.jsonl")
    assert store.get("call-1", now=now) == approved

    mismatch = store.consume(
        "call-1", session_id="session-2", arguments_sha256="a" * 64, now=now
    )
    assert mismatch.status is ToolApprovalStatus.APPROVED
    assert mismatch.safe_error_code == "tool.approval_binding_mismatch"

    consumed = store.consume(
        "call-1", session_id="session-1", arguments_sha256="a" * 64, now=now
    )
    assert consumed.status is ToolApprovalStatus.CONSUMED
    assert store.consume(
        "call-1", session_id="session-1", arguments_sha256="a" * 64, now=now
    ).safe_error_code == "tool.approval_not_approved"


def test_pending_approval_expires_without_becoming_executable(tmp_path) -> None:
    now = datetime(2026, 8, 13, tzinfo=UTC)
    store = JsonlToolApprovalStore(tmp_path / "approvals.jsonl")
    store.request(_request(now))

    record = store.get("call-1", now=now + timedelta(minutes=5))

    assert record is not None
    assert record.status is ToolApprovalStatus.EXPIRED
    assert record.safe_error_code == "tool.approval_expired"
    assert store.list_pending(now=now + timedelta(minutes=5)) == ()
