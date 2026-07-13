"""Change Proposal System — 변경 제안 생성/대기/승인/실행.

The meta loop does not apply changes directly.  Instead, it creates
*proposals* that are queued for human review.  Only after HITL approval
are proposals executed.

Lifecycle::

    Created → Pending → (Approved | Rejected)
                         ↓          ↓
                      Executed   Discarded

This ensures that no fundamental system change is ever applied without
explicit human consent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from ..utils.logging import get_logger
from ..utils.serialization import write_jsonl
from ..utils.time import iso_now
from ..utils.ids import generate_record_id

logger = get_logger("agent.meta_loop.change_proposal")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProposalStatus(str, Enum):
    """Lifecycle states of a change proposal."""
    PENDING = "pending"      # Created, awaiting human review
    APPROVED = "approved"    # Human approved, ready for execution
    REJECTED = "rejected"    # Human rejected
    EXECUTED = "executed"    # Successfully applied
    FAILED = "failed"        # Execution failed


class ProposalType(str, Enum):
    """Types of meta loop changes."""
    CONSTITUTION_REVISION = "constitution_revision"
    ARCHITECTURE_MODIFICATION = "architecture_modification"
    ORGANIZATIONAL_RESTRUCTURING = "organizational_restructuring"
    IDENTITY_REDESIGN = "identity_redesign"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ChangeProposal:
    """A proposed meta loop change awaiting HITL approval.

    Attributes:
        proposal_id: Unique identifier.
        type: The :class:`ProposalType`.
        title: Short human-readable title.
        description: Detailed description of the proposed change.
        changes: Dict of specific changes to apply.
        status: Current :class:`ProposalStatus`.
        created_at: ISO 8601 timestamp.
        reviewed_at: When human reviewed (approved/rejected).
        executed_at: When the change was applied.
        reviewer: Who reviewed (human identifier).
        reason: Reason for approval/rejection.
        execution_result: Result of execution (if executed).
        error: Error message if execution failed.
    """
    proposal_id: str = ""
    type: ProposalType = ProposalType.CONSTITUTION_REVISION
    title: str = ""
    description: str = ""
    changes: dict[str, Any] = field(default_factory=dict)
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: str = ""
    reviewed_at: str = ""
    executed_at: str = ""
    reviewer: str = ""
    reason: str = ""
    execution_result: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "type": self.type.value,
            "title": self.title,
            "description": self.description,
            "changes": self.changes,
            "status": self.status.value,
            "created_at": self.created_at,
            "reviewed_at": self.reviewed_at,
            "executed_at": self.executed_at,
            "reviewer": self.reviewer,
            "reason": self.reason,
            "execution_result": self.execution_result,
            "error": self.error,
        }


@dataclass
class ProposalResult:
    """Result of executing a proposal."""
    proposal_id: str = ""
    success: bool = False
    applied_changes: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ---------------------------------------------------------------------------
# Proposal Queue
# ---------------------------------------------------------------------------

class ProposalQueue:
    """Manage the lifecycle of change proposals.

    Proposals are persisted to a JSONL file for audit trail.

    Args:
        log_path: Path for the proposal log (JSONL).
    """

    def __init__(
        self,
        log_path: str = "data/memory/audit/meta_proposals.jsonl",
    ):
        self.log_path = Path(log_path)
        self._proposals: dict[str, ChangeProposal] = {}
        self._executors: dict[str, Callable] = {}
        self._load()

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        type: ProposalType | str,
        title: str,
        description: str,
        changes: dict[str, Any] | None = None,
        executor: Callable | None = None,
    ) -> ChangeProposal:
        """Create a new change proposal.

        Args:
            type: The :class:`ProposalType` or string.
            title: Short title.
            description: Detailed description.
            changes: Dict of specific changes.
            executor: Callable to execute when approved. If None,
                the proposal is record-only (execution handled externally).

        Returns:
            The created :class:`ChangeProposal` with status PENDING.
        """
        if isinstance(type, str):
            type = ProposalType(type)

        proposal = ChangeProposal(
            proposal_id=generate_record_id("prop"),
            type=type,
            title=title,
            description=description,
            changes=changes or {},
            status=ProposalStatus.PENDING,
            created_at=iso_now(),
        )

        self._proposals[proposal.proposal_id] = proposal
        if executor is not None:
            self._executors[proposal.proposal_id] = executor

        self._log(proposal)
        logger.info(f"Created proposal {proposal.proposal_id}: {title} ({type.value})")
        return proposal

    # ------------------------------------------------------------------
    # Review (HITL)
    # ------------------------------------------------------------------

    def approve(
        self,
        proposal_id: str,
        reviewer: str = "human",
        reason: str = "",
    ) -> ChangeProposal | None:
        """Approve a pending proposal (HITL gate).

        Args:
            proposal_id: The proposal to approve.
            reviewer: Who approved (human identifier).
            reason: Reason for approval.

        Returns:
            The updated :class:`ChangeProposal`, or None if not found.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            logger.warning(f"Proposal {proposal_id} not found")
            return None

        if proposal.status != ProposalStatus.PENDING:
            logger.warning(
                f"Proposal {proposal_id} is {proposal.status.value}, "
                f"cannot approve"
            )
            return proposal

        proposal.status = ProposalStatus.APPROVED
        proposal.reviewed_at = iso_now()
        proposal.reviewer = reviewer
        proposal.reason = reason

        self._log(proposal)
        logger.info(f"Proposal {proposal_id} APPROVED by {reviewer}")
        return proposal

    def reject(
        self,
        proposal_id: str,
        reviewer: str = "human",
        reason: str = "",
    ) -> ChangeProposal | None:
        """Reject a pending proposal (HITL gate).

        Args:
            proposal_id: The proposal to reject.
            reviewer: Who rejected.
            reason: Reason for rejection.

        Returns:
            The updated :class:`ChangeProposal`, or None if not found.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            logger.warning(f"Proposal {proposal_id} not found")
            return None

        if proposal.status != ProposalStatus.PENDING:
            logger.warning(
                f"Proposal {proposal_id} is {proposal.status.value}, "
                f"cannot reject"
            )
            return proposal

        proposal.status = ProposalStatus.REJECTED
        proposal.reviewed_at = iso_now()
        proposal.reviewer = reviewer
        proposal.reason = reason

        self._log(proposal)
        logger.info(f"Proposal {proposal_id} REJECTED by {reviewer}: {reason}")
        return proposal

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(self, proposal_id: str) -> ProposalResult:
        """Execute an approved proposal.

        Args:
            proposal_id: The approved proposal to execute.

        Returns:
            :class:`ProposalResult` with execution outcome.
        """
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            return ProposalResult(
                proposal_id=proposal_id,
                success=False,
                error="Proposal not found",
            )

        if proposal.status != ProposalStatus.APPROVED:
            return ProposalResult(
                proposal_id=proposal_id,
                success=False,
                error=f"Proposal is {proposal.status.value}, not APPROVED",
            )

        executor = self._executors.get(proposal_id)
        if executor is None:
            return ProposalResult(
                proposal_id=proposal_id,
                success=False,
                error="No executor registered for this proposal",
            )

        try:
            result = executor(proposal)
            proposal.status = ProposalStatus.EXECUTED
            proposal.executed_at = iso_now()
            proposal.execution_result = result if isinstance(result, dict) else {}

            self._log(proposal)
            logger.info(f"Proposal {proposal_id} EXECUTED successfully")
            return ProposalResult(
                proposal_id=proposal_id,
                success=True,
                applied_changes=proposal.execution_result,
            )
        except Exception as e:
            proposal.status = ProposalStatus.FAILED
            proposal.executed_at = iso_now()
            proposal.error = str(e)

            self._log(proposal)
            logger.error(f"Proposal {proposal_id} EXECUTION FAILED: {e}")
            return ProposalResult(
                proposal_id=proposal_id,
                success=False,
                error=str(e),
            )

    def execute_approved(self) -> list[ProposalResult]:
        """Execute all approved proposals.

        Returns:
            List of :class:`ProposalResult` for each executed proposal.
        """
        results: list[ProposalResult] = []
        for pid, proposal in list(self._proposals.items()):
            if proposal.status == ProposalStatus.APPROVED:
                results.append(self.execute(pid))
        return results

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, proposal_id: str) -> ChangeProposal | None:
        """Get a proposal by ID."""
        return self._proposals.get(proposal_id)

    def list_pending(self) -> list[ChangeProposal]:
        """Return all pending proposals."""
        return [
            p for p in self._proposals.values()
            if p.status == ProposalStatus.PENDING
        ]

    def list_approved(self) -> list[ChangeProposal]:
        """Return all approved (but not yet executed) proposals."""
        return [
            p for p in self._proposals.values()
            if p.status == ProposalStatus.APPROVED
        ]

    def list_all(self) -> list[ChangeProposal]:
        """Return all proposals."""
        return list(self._proposals.values())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _log(self, proposal: ChangeProposal) -> None:
        """Append a proposal state change to the JSONL log."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(self.log_path, proposal.to_dict())
        except Exception as e:
            logger.warning(f"Failed to log proposal: {e}")

    def _load(self) -> None:
        """Load proposal history from disk (for audit, not for re-execution)."""
        if not self.log_path.exists():
            return
        try:
            from ..utils.serialization import read_jsonl_all
            records = read_jsonl_all(self.log_path)
            # Only load the latest state of each proposal
            for r in records:
                pid = r.get("proposal_id", "")
                if pid:
                    status = r.get("status", "pending")
                    self._proposals[pid] = ChangeProposal(
                        proposal_id=pid,
                        type=ProposalType(r.get("type", "constitution_revision")),
                        title=r.get("title", ""),
                        description=r.get("description", ""),
                        changes=r.get("changes", {}),
                        status=ProposalStatus(status),
                        created_at=r.get("created_at", ""),
                        reviewed_at=r.get("reviewed_at", ""),
                        executed_at=r.get("executed_at", ""),
                        reviewer=r.get("reviewer", ""),
                        reason=r.get("reason", ""),
                        execution_result=r.get("execution_result", {}),
                        error=r.get("error", ""),
                    )
            logger.info(f"Loaded {len(self._proposals)} proposals from {self.log_path}")
        except Exception as e:
            logger.warning(f"Failed to load proposals: {e}")