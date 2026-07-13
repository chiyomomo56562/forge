"""Meta Loop Orchestrator — 4대 작업 통합 + HITL 게이트.

Coordinates the meta loop's four major tasks:

    1. **Constitution (L4) Revision** — constitution_reviser.py
    2. **Architecture Self-Modification** — architecture_modifier.py
    3. **Organizational Restructuring** — (proposed via architecture_modifier)
    4. **L5 Identity Redesign** — identity_redesigner.py

All changes go through the :class:`ProposalQueue` which enforces HITL:
    - Proposals are created (PENDING)
    - Human reviews and approves/rejects
    - Only approved proposals are executed

The meta loop is triggered by the outer loop's :class:`MetaTrigger`:
    - regular_evolution (1,000 episodes)
    - emergency_inspection (100 outer loop cycles)
    - stagnation_response (coherence stagnation)

Usage::

    from agent.meta_loop import MetaLoop

    meta = MetaLoop(memory_manager=mm, ...)
    result = meta.run(trigger_type="regular_evolution")
    # Proposals are now PENDING — human must approve them
    # After approval:
    meta.execute_approved()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..memory.manager import MemoryManager
from ..memory.constitution.guard import CIBGuard
from ..memory.identity.updater import IdentityUpdater
from ..utils.logging import get_logger
from ..utils.serialization import write_jsonl
from ..utils.time import iso_now

from .change_proposal import (
    ChangeProposal,
    ProposalQueue,
    ProposalStatus,
    ProposalType,
    ProposalResult,
)
from .constitution_reviser import ConstitutionReviser
from .architecture_modifier import ArchitectureModifier
from .identity_redesigner import IdentityRedesigner

logger = get_logger("agent.meta_loop")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class MetaLoopState:
    """Persistent state of the meta loop.

    Attributes:
        meta_loop_count: Total meta loop cycles executed.
        last_trigger_type: Type of the last trigger.
        last_run_timestamp: ISO 8601 timestamp of the last run.
        total_proposals_created: Total proposals ever created.
        total_proposals_approved: Total proposals ever approved.
        total_proposals_executed: Total proposals ever executed.
    """
    meta_loop_count: int = 0
    last_trigger_type: str = ""
    last_run_timestamp: str = ""
    total_proposals_created: int = 0
    total_proposals_approved: int = 0
    total_proposals_executed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta_loop_count": self.meta_loop_count,
            "last_trigger_type": self.last_trigger_type,
            "last_run_timestamp": self.last_run_timestamp,
            "total_proposals_created": self.total_proposals_created,
            "total_proposals_approved": self.total_proposals_approved,
            "total_proposals_executed": self.total_proposals_executed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MetaLoopState:
        return cls(
            meta_loop_count=d.get("meta_loop_count", 0),
            last_trigger_type=d.get("last_trigger_type", ""),
            last_run_timestamp=d.get("last_run_timestamp", ""),
            total_proposals_created=d.get("total_proposals_created", 0),
            total_proposals_approved=d.get("total_proposals_approved", 0),
            total_proposals_executed=d.get("total_proposals_executed", 0),
        )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class MetaLoopResult:
    """Aggregate result of one meta loop cycle.

    Attributes:
        trigger_type: What triggered this meta loop run.
        proposals_created: List of proposals created in this run.
        pending_count: Number of proposals awaiting human review.
        state: Updated meta loop state.
        timestamp: ISO 8601 timestamp.
    """
    trigger_type: str = ""
    proposals_created: list[ChangeProposal] = field(default_factory=list)
    pending_count: int = 0
    state: MetaLoopState = field(default_factory=MetaLoopState)
    timestamp: str = ""


# ---------------------------------------------------------------------------
# Meta Loop Orchestrator
# ---------------------------------------------------------------------------

class MetaLoop:
    """Meta loop orchestrator — proposes fundamental system changes.

    Args:
        memory_manager: A :class:`MemoryManager` instance.
        state_path: Path to persist meta loop state (JSON).
        log_path: Path for the meta loop audit log (JSONL).
        proposal_log_path: Path for the proposal audit log (JSONL).
    """

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        state_path: str = "data/memory/audit/meta_loop_state.json",
        log_path: str = "data/memory/audit/meta_loop_log.jsonl",
        proposal_log_path: str = "data/memory/audit/meta_proposals.jsonl",
    ):
        self.memory_manager = memory_manager
        self.state_path = Path(state_path)
        self.log_path = Path(log_path)

        # Load state
        self.state = self._load_state()

        # Initialize proposal queue (shared across all revisers)
        self.proposal_queue = ProposalQueue(log_path=proposal_log_path)

        # Initialize CIB guard
        self.cib_guard = CIBGuard()

        # Initialize constitution directory
        constitution_dir = "constitution"
        if memory_manager is not None:
            constitution_dir = getattr(
                memory_manager.constitution_loader, "constitution_dir", "constitution"
            )

        # Initialize revisers
        self.constitution_reviser = ConstitutionReviser(
            constitution_dir=str(constitution_dir),
            cib_guard=self.cib_guard,
            proposal_queue=self.proposal_queue,
        )

        skill_store = None
        if memory_manager is not None:
            skill_store = getattr(memory_manager, "skill_store", None)

        self.architecture_modifier = ArchitectureModifier(
            skill_store=skill_store,
            proposal_queue=self.proposal_queue,
        )

        identity_updater = None
        if memory_manager is not None:
            identity_updater = IdentityUpdater(
                store=memory_manager.identity_store,
            )

        self.identity_redesigner = IdentityRedesigner(
            identity_updater=identity_updater,
            proposal_queue=self.proposal_queue,
        )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, trigger_type: str = "regular_evolution") -> MetaLoopResult:
        """Run one meta loop cycle.

        Creates proposals for system changes based on the trigger type.
        Proposals are NOT executed immediately — they require HITL approval.

        Args:
            trigger_type: What triggered this meta loop
                ('regular_evolution' | 'emergency_inspection' | 'stagnation_response').

        Returns:
            :class:`MetaLoopResult` with created proposals.
        """
        timestamp = iso_now()
        logger.info(
            f"=== Meta Loop cycle {self.state.meta_loop_count + 1} "
            f"(trigger: {trigger_type}) ==="
        )

        result = MetaLoopResult(
            trigger_type=trigger_type,
            timestamp=timestamp,
        )

        # Generate proposals based on trigger type
        proposals: list[ChangeProposal] = []

        if trigger_type == "regular_evolution":
            proposals.extend(self._propose_regular_evolution())
        elif trigger_type == "emergency_inspection":
            proposals.extend(self._propose_emergency_inspection())
        elif trigger_type == "stagnation_response":
            proposals.extend(self._propose_stagnation_response())
        else:
            logger.warning(f"Unknown trigger type: {trigger_type}")

        result.proposals_created = proposals
        result.pending_count = len(self.proposal_queue.list_pending())

        # Update state
        self.state.meta_loop_count += 1
        self.state.last_trigger_type = trigger_type
        self.state.last_run_timestamp = timestamp
        self.state.total_proposals_created += len(proposals)

        result.state = self.state

        # Persist state and log
        self._save_state()
        self._log_run(result)

        logger.info(
            f"=== Meta Loop complete: {len(proposals)} proposals created, "
            f"{result.pending_count} pending HITL approval ==="
        )

        return result

    # ------------------------------------------------------------------
    # Execute approved proposals (HITL)
    # ------------------------------------------------------------------

    def execute_approved(self) -> list[ProposalResult]:
        """Execute all proposals that have been approved by a human.

        Returns:
            List of :class:`ProposalResult` for each executed proposal.
        """
        results = self.proposal_queue.execute_approved()
        executed_count = sum(1 for r in results if r.success)
        self.state.total_proposals_executed += executed_count
        self.state.total_proposals_approved += len(results)
        self._save_state()

        logger.info(f"Executed {executed_count} approved proposals")
        return results

    def approve_proposal(
        self,
        proposal_id: str,
        reviewer: str = "human",
        reason: str = "",
    ) -> ChangeProposal | None:
        """Approve a pending proposal (HITL gate).

        Args:
            proposal_id: The proposal to approve.
            reviewer: Who approved.
            reason: Reason for approval.

        Returns:
            The updated proposal, or None if not found.
        """
        return self.proposal_queue.approve(proposal_id, reviewer, reason)

    def reject_proposal(
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
            The updated proposal, or None if not found.
        """
        return self.proposal_queue.reject(proposal_id, reviewer, reason)

    # ------------------------------------------------------------------
    # Proposal generation by trigger type
    # ------------------------------------------------------------------

    def _propose_regular_evolution(self) -> list[ChangeProposal]:
        """Generate proposals for regular evolution.

        Regular evolution proposes:
            - Review and potentially add new K-Scenarios
            - Review and adjust CIB threshold if needed
            - Review identity calibration
        """
        proposals: list[ChangeProposal] = []

        # Propose a review of the CIB threshold
        # (This is a placeholder — in practice, the meta loop would analyse
        #  recent performance data to determine if the threshold needs adjustment)
        proposals.append(
            self.constitution_reviser.propose_cib_threshold_update(
                new_threshold=0.95,  # Keep current (review only)
                description="Regular evolution: review CIB threshold (no change proposed)",
            )
        )

        logger.info(f"Regular evolution: {len(proposals)} proposals created")
        return proposals

    def _propose_emergency_inspection(self) -> list[ChangeProposal]:
        """Generate proposals for emergency inspection.

        Emergency inspection proposes:
            - Stricter CIB threshold (0.95 → 0.97)
            - Architecture review
        """
        proposals: list[ChangeProposal] = []

        # Propose raising CIB threshold for safety
        proposals.append(
            self.constitution_reviser.propose_cib_threshold_update(
                new_threshold=0.97,
                new_emergency_threshold=0.99,
                description="Emergency inspection: raise CIB threshold for increased safety",
            )
        )

        logger.info(f"Emergency inspection: {len(proposals)} proposals created")
        return proposals

    def _propose_stagnation_response(self) -> list[ChangeProposal]:
        """Generate proposals for stagnation response.

        Stagnation response proposes:
            - Identity redesign (adjust bias, calibration)
            - Architecture modification (add new skill categories)
        """
        proposals: list[ChangeProposal] = []

        # Propose identity bias adjustment
        proposals.append(
            self.identity_redesigner.propose_bias_adjustment(
                new_bias={"prediction_optimism": 0.1},  # Slightly more optimistic
                reason="Stagnation: adjust prediction bias to encourage exploration",
            )
        )

        # Propose adding a new skill category for exploration
        proposals.append(
            self.architecture_modifier.propose_skill_category_add(
                category_id="exploration",
                description="Skills for exploring new approaches and breaking stagnation",
                reason="Stagnation: add exploration skill category to encourage new patterns",
            )
        )

        logger.info(f"Stagnation response: {len(proposals)} proposals created")
        return proposals

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> MetaLoopState:
        """Load state from disk or initialize."""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                return MetaLoopState.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load meta loop state: {e}")
        return MetaLoopState()

    def _save_state(self) -> None:
        """Persist state to disk."""
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.state.to_dict(), indent=2)
            )
        except Exception as e:
            logger.warning(f"Failed to save meta loop state: {e}")

    def _log_run(self, result: MetaLoopResult) -> None:
        """Append a summary of this run to the audit log."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": result.timestamp,
                "meta_loop_count": self.state.meta_loop_count,
                "trigger_type": result.trigger_type,
                "proposals_created": len(result.proposals_created),
                "pending_count": result.pending_count,
                "proposal_ids": [p.proposal_id for p in result.proposals_created],
            }
            write_jsonl(self.log_path, entry)
        except Exception as e:
            logger.warning(f"Failed to log meta loop run: {e}")