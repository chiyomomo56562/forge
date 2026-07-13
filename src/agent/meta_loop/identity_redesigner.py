"""L5 Identity Redesign — 정체성 근본 재설계.

Proposes fundamental redesign of the agent's L5 identity:
    - Autonomy level adjustment (L0–L5)
    - Identity core (values, boundaries, self-description)
    - Initial bias (prediction bias settings)
    - Calibration thresholds

Delegates execution to :class:`IdentityUpdater.redesign_identity()` which
already has HITL enforcement built in.

All changes require HITL approval via the :class:`ProposalQueue`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory.identity.updater import IdentityUpdater, RedesignResult
from ..utils.logging import get_logger

from .change_proposal import ChangeProposal, ProposalQueue, ProposalType

logger = get_logger("agent.meta_loop.identity_redesigner")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class IdentityRedesignResult:
    """Result of an identity redesign execution.

    Attributes:
        success: Whether the redesign was applied.
        approved: Whether HITL approval was granted.
        changes: Dict of applied changes.
        version: New identity version.
        error: Error message if failed.
    """
    success: bool = False
    approved: bool = False
    changes: dict[str, Any] = field(default_factory=dict)
    version: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Identity Redesigner
# ---------------------------------------------------------------------------

class IdentityRedesigner:
    """Propose and execute L5 identity redesigns.

    Args:
        identity_updater: An :class:`IdentityUpdater` instance.
        proposal_queue: A :class:`ProposalQueue` for change management.
    """

    def __init__(
        self,
        identity_updater: IdentityUpdater | None = None,
        proposal_queue: ProposalQueue | None = None,
    ):
        self.identity_updater = identity_updater
        self.proposal_queue = proposal_queue or ProposalQueue()

    # ------------------------------------------------------------------
    # Propose changes
    # ------------------------------------------------------------------

    def propose_autonomy_level_change(
        self,
        new_level: str,
        current_level: str = "unknown",
        reason: str = "",
    ) -> ChangeProposal:
        """Propose changing the agent's autonomy level.

        Args:
            new_level: New autonomy level (L0–L5).
            current_level: Current autonomy level.
            reason: Why this change is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "redesign_identity",
            "autonomy_level": {"current": new_level},
            "current_level": current_level,
        }

        return self.proposal_queue.create(
            type=ProposalType.IDENTITY_REDESIGN,
            title=f"Change autonomy level: {current_level} → {new_level}",
            description=reason or f"Adjust agent autonomy from {current_level} to {new_level}",
            changes=changes,
            executor=self._execute_redesign,
        )

    def propose_identity_core_update(
        self,
        new_identity_core: dict[str, Any],
        reason: str = "",
    ) -> ChangeProposal:
        """Propose updating the agent's identity core.

        Args:
            new_identity_core: New identity core dict (values, boundaries,
                self_description).
            reason: Why this change is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "redesign_identity",
            "identity_core": new_identity_core,
        }

        return self.proposal_queue.create(
            type=ProposalType.IDENTITY_REDESIGN,
            title="Update identity core",
            description=reason or "Fundamental identity core update",
            changes=changes,
            executor=self._execute_redesign,
        )

    def propose_bias_adjustment(
        self,
        new_bias: dict[str, Any],
        reason: str = "",
    ) -> ChangeProposal:
        """Propose adjusting the agent's prediction bias.

        Args:
            new_bias: New bias settings dict.
            reason: Why this adjustment is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "redesign_identity",
            "initial_bias": new_bias,
        }

        return self.proposal_queue.create(
            type=ProposalType.IDENTITY_REDESIGN,
            title="Adjust prediction bias",
            description=reason or "Adjust agent's initial prediction bias settings",
            changes=changes,
            executor=self._execute_redesign,
        )

    def propose_calibration_update(
        self,
        new_calibration: dict[str, Any],
        reason: str = "",
    ) -> ChangeProposal:
        """Propose updating calibration thresholds.

        Args:
            new_calibration: New calibration settings dict.
            reason: Why this update is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "redesign_identity",
            "calibration": new_calibration,
        }

        return self.proposal_queue.create(
            type=ProposalType.IDENTITY_REDESIGN,
            title="Update calibration thresholds",
            description=reason or "Adjust self-model calibration thresholds",
            changes=changes,
            executor=self._execute_redesign,
        )

    def propose_full_redesign(
        self,
        new_config: dict[str, Any],
        reason: str = "",
    ) -> ChangeProposal:
        """Propose a full identity redesign.

        Args:
            new_config: Complete new identity configuration. May include
                autonomy_level, identity_core, initial_bias, calibration.
            reason: Why a full redesign is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "redesign_identity",
            **new_config,
        }

        return self.proposal_queue.create(
            type=ProposalType.IDENTITY_REDESIGN,
            title="Full identity redesign",
            description=reason or "Fundamental identity redesign (all aspects)",
            changes=changes,
            executor=self._execute_redesign,
        )

    # ------------------------------------------------------------------
    # Execute (called by ProposalQueue after HITL approval)
    # ------------------------------------------------------------------

    def _execute_redesign(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute identity redesign via IdentityUpdater.

        The IdentityUpdater already has HITL enforcement built in.
        Since the proposal was approved through the ProposalQueue (which
        is the HITL gate), we pass hitl_approved=True.
        """
        if self.identity_updater is None:
            raise RuntimeError("No identity updater available")

        # Build the new_config from proposal changes (excluding 'action' key)
        new_config = {
            k: v for k, v in proposal.changes.items()
            if k != "action"
        }

        result = self.identity_updater.redesign_identity(
            new_config=new_config,
            hitl_approved=True,  # Already approved via ProposalQueue
        )

        if not result.approved:
            raise PermissionError(f"Identity redesign blocked: {result.reason}")

        logger.info(f"Identity redesign applied: {result.reason}")
        return {
            "approved": True,
            "changes": result.changes,
            "reason": result.reason,
        }