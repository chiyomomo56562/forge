"""Architecture Self-Modification — 아키텍처 자가 수정.

Proposes changes to the system's internal architecture:
    - Workflow redesign (inner loop parameter adjustments)
    - Skill category addition/removal (L3 procedural memory)
    - Tool policy updates (L4 tool_policy.yml)

All changes require HITL approval via the :class:`ProposalQueue`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory.procedural.skill_store import SkillStore
from ..memory.schemas import Skill, SkillMetadata, SkillStatus
from ..utils.logging import get_logger
from ..utils.time import iso_now

from .change_proposal import ChangeProposal, ProposalQueue, ProposalType

logger = get_logger("agent.meta_loop.architecture_modifier")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ArchitectureChangeResult:
    """Result of an architecture modification execution.

    Attributes:
        success: Whether the modification was applied.
        change_type: Type of change ('add_skill_category' | 'remove_skill_category' | 'workflow_update').
        details: Dict with change-specific details.
        error: Error message if failed.
    """
    success: bool = False
    change_type: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


# ---------------------------------------------------------------------------
# Architecture Modifier
# ---------------------------------------------------------------------------

class ArchitectureModifier:
    """Propose and execute architecture self-modifications.

    Args:
        skill_store: A :class:`SkillStore` for L3 skill operations.
        proposal_queue: A :class:`ProposalQueue` for change management.
    """

    def __init__(
        self,
        skill_store: SkillStore | None = None,
        proposal_queue: ProposalQueue | None = None,
    ):
        self.skill_store = skill_store
        self.proposal_queue = proposal_queue or ProposalQueue()

    # ------------------------------------------------------------------
    # Propose changes
    # ------------------------------------------------------------------

    def propose_skill_category_add(
        self,
        category_id: str,
        description: str,
        initial_skill_code: str = "",
        reason: str = "",
    ) -> ChangeProposal:
        """Propose adding a new skill category.

        Args:
            category_id: ID for the new skill category.
            description: What this skill category does.
            initial_skill_code: Optional seed code for the first skill.
            reason: Why this category is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "add_skill_category",
            "category_id": category_id,
            "description": description,
            "initial_skill_code": initial_skill_code,
        }

        return self.proposal_queue.create(
            type=ProposalType.ARCHITECTURE_MODIFICATION,
            title=f"Add skill category '{category_id}'",
            description=reason or f"Add new skill category: {description}",
            changes=changes,
            executor=self._execute_skill_category_add,
        )

    def propose_skill_category_remove(
        self,
        category_id: str,
        reason: str = "",
    ) -> ChangeProposal:
        """Propose removing a skill category.

        Args:
            category_id: ID of the skill category to remove.
            reason: Why this category should be removed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "remove_skill_category",
            "category_id": category_id,
        }

        return self.proposal_queue.create(
            type=ProposalType.ARCHITECTURE_MODIFICATION,
            title=f"Remove skill category '{category_id}'",
            description=reason or f"Remove skill category '{category_id}'",
            changes=changes,
            executor=self._execute_skill_category_remove,
        )

    def propose_workflow_update(
        self,
        parameter_name: str,
        new_value: Any,
        current_value: Any = None,
        reason: str = "",
    ) -> ChangeProposal:
        """Propose a workflow parameter update.

        Args:
            parameter_name: Name of the parameter to update.
            new_value: New value.
            current_value: Current value (for audit).
            reason: Why this change is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "workflow_update",
            "parameter": parameter_name,
            "new_value": new_value,
            "current_value": current_value,
        }

        return self.proposal_queue.create(
            type=ProposalType.ARCHITECTURE_MODIFICATION,
            title=f"Update workflow parameter '{parameter_name}'",
            description=reason or f"Update {parameter_name}: {current_value} → {new_value}",
            changes=changes,
            executor=self._execute_workflow_update,
        )

    # ------------------------------------------------------------------
    # Execute (called by ProposalQueue after HITL approval)
    # ------------------------------------------------------------------

    def _execute_skill_category_add(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute adding a new skill category (seed skill)."""
        if self.skill_store is None:
            raise RuntimeError("No skill store available")

        changes = proposal.changes
        category_id = changes["category_id"]
        description = changes["description"]
        initial_code = changes.get("initial_skill_code", "")

        # Create a seed skill for the new category
        seed_skill = Skill(
            skill_id=f"seed_{category_id}",
            name=category_id,
            code=initial_code or f"# Seed skill for category: {category_id}\ndef run():\n    pass\n",
            description=description,
            metadata=SkillMetadata(
                status=SkillStatus.SEED,
                success_rate=0.0,
                total_executions=0,
            ),
            created_at=iso_now(),
            updated_at=iso_now(),
        )
        self.skill_store.upsert(seed_skill)

        logger.info(f"Skill category '{category_id}' added (seed skill created)")
        return {
            "category_id": category_id,
            "seed_skill_id": seed_skill.skill_id,
        }

    def _execute_skill_category_remove(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute removing a skill category (archive all skills)."""
        if self.skill_store is None:
            raise RuntimeError("No skill store available")

        changes = proposal.changes
        category_id = changes["category_id"]

        # Find all skills with this category prefix and archive them
        all_skills = self.skill_store.list_all()
        archived_count = 0
        for skill in all_skills:
            if category_id in skill.skill_id or category_id in skill.name:
                self.skill_store.update_status(skill.skill_id, SkillStatus.ARCHIVED)
                archived_count += 1

        logger.info(f"Skill category '{category_id}' removed ({archived_count} skills archived)")
        return {
            "category_id": category_id,
            "archived_count": archived_count,
        }

    def _execute_workflow_update(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute a workflow parameter update.

        This is a record-only change — actual parameter updates would be
        applied to the relevant config files by the caller.
        """
        changes = proposal.changes
        logger.info(
            f"Workflow parameter '{changes['parameter']}' updated: "
            f"{changes.get('current_value')} → {changes['new_value']}"
        )
        return {
            "parameter": changes["parameter"],
            "old_value": changes.get("current_value"),
            "new_value": changes["new_value"],
        }