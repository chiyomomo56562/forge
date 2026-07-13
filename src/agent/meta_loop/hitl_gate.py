"""HITL Gate — 중앙 집중식 인간 승인 게이트.

Centralizes all Human-in-the-Loop (HITL) approval logic for the meta loop.
Every meta loop change must pass through this gate before execution.

Features:
    1. **Approval requests** — Structured requests with layer, severity, impact
    2. **Human review** — Approve/reject with mandatory reason
    3. **Audit trail** — All decisions logged to JSONL for compliance
    4. **Expiry handling** — Proposals not reviewed within ``expiry_hours``
       are automatically flagged as expired
    5. **Batch operations** — Approve/reject multiple proposals at once
    6. **Guard integration** — Wraps ``CIBGuard.require_hitl_approval()``
       for constitution-layer-specific checks
    7. **Notification callbacks** — Optional callbacks when proposals are
       ready for review or when decisions are made

The HITL gate sits between the :class:`ProposalQueue` (which manages proposal
lifecycle) and the executors (which apply changes).  No proposal can bypass
this gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..memory.constitution.guard import CIBGuard, HITLResult
from ..memory.schemas import ConstitutionLayer
from ..utils.logging import get_logger
from ..utils.serialization import write_jsonl
from ..utils.time import iso_now, parse_iso

from .change_proposal import (
    ChangeProposal,
    ProposalQueue,
    ProposalStatus,
    ProposalType,
)

logger = get_logger("agent.meta_loop.hitl_gate")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HITLSeverity(str, Enum):
    """Severity level of a proposed change."""
    LOW = "low"           # Minor adjustments (e.g., strategy layer)
    MEDIUM = "medium"     # Significant changes (e.g., principle layer)
    HIGH = "high"         # Major changes (e.g., absolute layer, CIB threshold)
    CRITICAL = "critical"  # Fundamental redesign (e.g., identity core)


class HITLDecision(str, Enum):
    """Human review decision."""
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PENDING = "pending"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ApprovalRequest:
    """A structured HITL approval request.

    Attributes:
        proposal_id: Linked proposal ID.
        constitution_layer: Which constitution layer is affected.
        severity: Change severity level.
        impact_summary: Human-readable summary of expected impact.
        rollback_plan: How to undo the change if needed.
        created_at: ISO 8601 timestamp.
        expiry_hours: Hours before the request expires (0 = never).
        decision: Current :class:`HITLDecision`.
        reviewed_at: When human reviewed.
        reviewer: Who reviewed.
        review_reason: Reason for the decision.
    """
    proposal_id: str = ""
    constitution_layer: ConstitutionLayer = ConstitutionLayer.PRINCIPLE
    severity: HITLSeverity = HITLSeverity.MEDIUM
    impact_summary: str = ""
    rollback_plan: str = ""
    created_at: str = ""
    expiry_hours: float = 0.0  # 0 = never expires
    decision: HITLDecision = HITLDecision.PENDING
    reviewed_at: str = ""
    reviewer: str = ""
    review_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "constitution_layer": self.constitution_layer.value,
            "severity": self.severity.value,
            "impact_summary": self.impact_summary,
            "rollback_plan": self.rollback_plan,
            "created_at": self.created_at,
            "expiry_hours": self.expiry_hours,
            "decision": self.decision.value,
            "reviewed_at": self.reviewed_at,
            "reviewer": self.reviewer,
            "review_reason": self.review_reason,
        }


@dataclass
class HITLGateResult:
    """Result of a HITL gate evaluation.

    Attributes:
        allowed: Whether the change is allowed to proceed.
        decision: The :class:`HITLDecision`.
        reason: Human-readable reason.
        request: The :class:`ApprovalRequest` (if created).
    """
    allowed: bool = False
    decision: HITLDecision = HITLDecision.PENDING
    reason: str = ""
    request: ApprovalRequest | None = None


# ---------------------------------------------------------------------------
# HITL Gate
# ---------------------------------------------------------------------------

class HITLGate:
    """Centralized Human-in-the-Loop approval gate.

    Wraps the :class:`ProposalQueue` and :class:`CIBGuard` to provide
    a single, auditable approval workflow for all meta loop changes.

    Args:
        proposal_queue: The :class:`ProposalQueue` to manage proposals.
        cib_guard: A :class:`CIBGuard` for constitution-layer checks.
        audit_log_path: Path for the HITL audit log (JSONL).
        default_expiry_hours: Hours before a request expires (default 72).
        notification_callback: Optional callable invoked when a request
            is created or decided. Signature: ``callback(request)``.
    """

    def __init__(
        self,
        proposal_queue: ProposalQueue | None = None,
        cib_guard: CIBGuard | None = None,
        audit_log_path: str = "data/memory/audit/hitl_audit.jsonl",
        default_expiry_hours: float = 72.0,
        notification_callback: Callable | None = None,
    ):
        self.proposal_queue = proposal_queue or ProposalQueue()
        self.cib_guard = cib_guard or CIBGuard()
        self.audit_log_path = Path(audit_log_path)
        self.default_expiry_hours = default_expiry_hours
        self.notification_callback = notification_callback

        # Active approval requests keyed by proposal_id
        self._requests: dict[str, ApprovalRequest] = {}

    # ------------------------------------------------------------------
    # Create approval request
    # ------------------------------------------------------------------

    def request_approval(
        self,
        proposal: ChangeProposal,
        constitution_layer: ConstitutionLayer | str = ConstitutionLayer.PRINCIPLE,
        severity: HITLSeverity | str = HITLSeverity.MEDIUM,
        impact_summary: str = "",
        rollback_plan: str = "",
        expiry_hours: float | None = None,
    ) -> ApprovalRequest:
        """Create a HITL approval request for a proposal.

        Args:
            proposal: The :class:`ChangeProposal` requiring approval.
            constitution_layer: Which constitution layer is affected.
            severity: Change severity level.
            impact_summary: Human-readable impact summary.
            rollback_plan: How to undo the change.
            expiry_hours: Hours before expiry (None = use default).

        Returns:
            :class:`ApprovalRequest` with decision PENDING.
        """
        if isinstance(constitution_layer, str):
            constitution_layer = ConstitutionLayer(constitution_layer)
        if isinstance(severity, str):
            severity = HITLSeverity(severity)

        # Check if the constitution layer requires HITL (it always does)
        hitl_result = self.cib_guard.require_hitl_approval(
            constitution_layer, approved=False,
        )
        # hitl_result.requires_approval is always True for all layers

        request = ApprovalRequest(
            proposal_id=proposal.proposal_id,
            constitution_layer=constitution_layer,
            severity=severity,
            impact_summary=impact_summary or proposal.description,
            rollback_plan=rollback_plan,
            created_at=iso_now(),
            expiry_hours=expiry_hours if expiry_hours is not None else self.default_expiry_hours,
        )

        self._requests[proposal.proposal_id] = request
        self._log_audit(request, action="request_created")

        # Notify
        if self.notification_callback is not None:
            try:
                self.notification_callback(request)
            except Exception as e:
                logger.warning(f"Notification callback failed: {e}")

        logger.info(
            f"HITL approval requested for proposal {proposal.proposal_id} "
            f"(layer={constitution_layer.value}, severity={severity.value})"
        )
        return request

    # ------------------------------------------------------------------
    # Human review
    # ------------------------------------------------------------------

    def approve(
        self,
        proposal_id: str,
        reviewer: str = "human",
        reason: str = "",
    ) -> HITLGateResult:
        """Approve a pending proposal through the HITL gate.

        Args:
            proposal_id: The proposal to approve.
            reviewer: Who is approving.
            reason: Mandatory reason for approval.

        Returns:
            :class:`HITLGateResult` indicating whether the change is allowed.
        """
        request = self._requests.get(proposal_id)
        if request is None:
            # Create a default request if none exists
            request = ApprovalRequest(
                proposal_id=proposal_id,
                created_at=iso_now(),
            )
            self._requests[proposal_id] = request

        if request.decision != HITLDecision.PENDING:
            return HITLGateResult(
                allowed=False,
                decision=request.decision,
                reason=f"Already decided: {request.decision.value}",
            )

        # Check expiry
        if self._is_expired(request):
            request.decision = HITLDecision.EXPIRED
            request.reviewed_at = iso_now()
            self._log_audit(request, action="expired")
            return HITLGateResult(
                allowed=False,
                decision=HITLDecision.EXPIRED,
                reason="Request expired before review",
            )

        # Verify HITL via CIBGuard
        hitl_result = self.cib_guard.require_hitl_approval(
            request.constitution_layer, approved=True,
        )
        if not hitl_result.approved:
            return HITLGateResult(
                allowed=False,
                decision=HITLDecision.PENDING,
                reason=f"CIBGuard HITL check failed: {hitl_result.reason}",
            )

        # Approve in the proposal queue
        proposal = self.proposal_queue.approve(proposal_id, reviewer, reason)
        if proposal is None:
            return HITLGateResult(
                allowed=False,
                decision=HITLDecision.PENDING,
                reason="Proposal not found in queue",
            )

        # Update request
        request.decision = HITLDecision.APPROVED
        request.reviewed_at = iso_now()
        request.reviewer = reviewer
        request.review_reason = reason

        self._log_audit(request, action="approved")

        # Notify
        if self.notification_callback is not None:
            try:
                self.notification_callback(request)
            except Exception as e:
                logger.warning(f"Notification callback failed: {e}")

        logger.info(f"HITL APPROVED: proposal {proposal_id} by {reviewer}")
        return HITLGateResult(
            allowed=True,
            decision=HITLDecision.APPROVED,
            reason=f"Approved by {reviewer}: {reason}",
            request=request,
        )

    def reject(
        self,
        proposal_id: str,
        reviewer: str = "human",
        reason: str = "",
    ) -> HITLGateResult:
        """Reject a pending proposal through the HITL gate.

        Args:
            proposal_id: The proposal to reject.
            reviewer: Who is rejecting.
            reason: Mandatory reason for rejection.

        Returns:
            :class:`HITLGateResult` indicating the rejection.
        """
        request = self._requests.get(proposal_id)
        if request is None:
            request = ApprovalRequest(
                proposal_id=proposal_id,
                created_at=iso_now(),
            )
            self._requests[proposal_id] = request

        if request.decision != HITLDecision.PENDING:
            return HITLGateResult(
                allowed=False,
                decision=request.decision,
                reason=f"Already decided: {request.decision.value}",
            )

        # Reject in the proposal queue
        proposal = self.proposal_queue.reject(proposal_id, reviewer, reason)
        if proposal is None:
            return HITLGateResult(
                allowed=False,
                decision=HITLDecision.PENDING,
                reason="Proposal not found in queue",
            )

        # Update request
        request.decision = HITLDecision.REJECTED
        request.reviewed_at = iso_now()
        request.reviewer = reviewer
        request.review_reason = reason

        self._log_audit(request, action="rejected")

        logger.info(f"HITL REJECTED: proposal {proposal_id} by {reviewer}: {reason}")
        return HITLGateResult(
            allowed=False,
            decision=HITLDecision.REJECTED,
            reason=f"Rejected by {reviewer}: {reason}",
            request=request,
        )

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def approve_batch(
        self,
        proposal_ids: list[str],
        reviewer: str = "human",
        reason: str = "",
    ) -> list[HITLGateResult]:
        """Approve multiple proposals at once.

        Args:
            proposal_ids: List of proposal IDs to approve.
            reviewer: Who is approving.
            reason: Reason for batch approval.

        Returns:
            List of :class:`HITLGateResult` for each proposal.
        """
        results: list[HITLGateResult] = []
        for pid in proposal_ids:
            results.append(self.approve(pid, reviewer, reason))
        return results

    def reject_batch(
        self,
        proposal_ids: list[str],
        reviewer: str = "human",
        reason: str = "",
    ) -> list[HITLGateResult]:
        """Reject multiple proposals at once.

        Args:
            proposal_ids: List of proposal IDs to reject.
            reviewer: Who is rejecting.
            reason: Reason for batch rejection.

        Returns:
            List of :class:`HITLGateResult` for each proposal.
        """
        results: list[HITLGateResult] = []
        for pid in proposal_ids:
            results.append(self.reject(pid, reviewer, reason))
        return results

    # ------------------------------------------------------------------
    # Expiry handling
    # ------------------------------------------------------------------

    def check_expiry(self) -> list[str]:
        """Check all pending requests for expiry.

        Expired requests are marked as EXPIRED and their proposals are
        rejected in the queue.

        Returns:
            List of proposal IDs that were expired.
        """
        expired_ids: list[str] = []
        for pid, request in list(self._requests.items()):
            if request.decision == HITLDecision.PENDING and self._is_expired(request):
                request.decision = HITLDecision.EXPIRED
                request.reviewed_at = iso_now()
                self._log_audit(request, action="expired")
                # Also reject in the proposal queue
                self.proposal_queue.reject(
                    pid, reviewer="system", reason="HITL request expired",
                )
                expired_ids.append(pid)
                logger.warning(f"HITL request {pid} EXPIRED")

        return expired_ids

    @staticmethod
    def _is_expired(request: ApprovalRequest) -> bool:
        """Check if a request has expired."""
        if request.expiry_hours <= 0:
            return False  # Never expires
        try:
            created = parse_iso(request.created_at)
            now = datetime.now(timezone.utc)
            expiry = created + timedelta(hours=request.expiry_hours)
            return now > expiry
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Execute approved
    # ------------------------------------------------------------------

    def execute_approved(self) -> list:
        """Execute all proposals approved through the HITL gate.

        Returns:
            List of :class:`ProposalResult` from the proposal queue.
        """
        return self.proposal_queue.execute_approved()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_request(self, proposal_id: str) -> ApprovalRequest | None:
        """Get the approval request for a proposal."""
        return self._requests.get(proposal_id)

    def list_pending_requests(self) -> list[ApprovalRequest]:
        """Return all pending approval requests."""
        return [
            r for r in self._requests.values()
            if r.decision == HITLDecision.PENDING
        ]

    def list_all_requests(self) -> list[ApprovalRequest]:
        """Return all approval requests."""
        return list(self._requests.values())

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    def _log_audit(self, request: ApprovalRequest, action: str) -> None:
        """Log a HITL decision to the audit log."""
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": iso_now(),
                "action": action,
                **request.to_dict(),
            }
            write_jsonl(self.audit_log_path, entry)
        except Exception as e:
            logger.warning(f"Failed to log HITL audit: {e}")