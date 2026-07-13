"""Constitution (L4) Revision — 헌법 개정.

Proposes changes to the L4 constitution:
    - Absolute layer (절대층): core values, ethical boundaries
    - Principle layer (원칙층): operational principles
    - Strategy layer (전략층): contextual adaptation strategies
    - CIB threshold adjustment

All changes require HITL approval via the :class:`ProposalQueue`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..memory.constitution.guard import CIBGuard, HITLResult
from ..memory.constitution.loader import ConstitutionLoader
from ..memory.schemas import Constitution, ConstitutionLayer, Principle, KScenario
from ..utils.logging import get_logger
from ..utils.time import iso_now

from .change_proposal import ChangeProposal, ProposalQueue, ProposalType

logger = get_logger("agent.meta_loop.constitution_reviser")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionRevisionResult:
    """Result of a constitution revision execution.

    Attributes:
        success: Whether the revision was applied.
        version: New constitution version.
        changes: Dict of applied changes.
        cib_threshold_updated: Whether CIB threshold was changed.
        error: Error message if failed.
    """
    success: bool = False
    version: int = 0
    changes: dict[str, Any] = field(default_factory=dict)
    cib_threshold_updated: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Constitution Reviser
# ---------------------------------------------------------------------------

class ConstitutionReviser:
    """Propose and execute constitution (L4) revisions.

    Args:
        constitution_dir: Path to the constitution YAML directory.
        cib_guard: A :class:`CIBGuard` instance for HITL checks.
        proposal_queue: A :class:`ProposalQueue` for change management.
    """

    def __init__(
        self,
        constitution_dir: str = "constitution",
        cib_guard: CIBGuard | None = None,
        proposal_queue: ProposalQueue | None = None,
    ):
        self.constitution_dir = Path(constitution_dir)
        self.cib_guard = cib_guard or CIBGuard()
        self.proposal_queue = proposal_queue or ProposalQueue()
        self.loader = ConstitutionLoader(constitution_dir=str(constitution_dir))

    # ------------------------------------------------------------------
    # Propose changes
    # ------------------------------------------------------------------

    def propose_principle_update(
        self,
        principle_id: str,
        new_rule: str,
        new_weight: float | None = None,
        layer: ConstitutionLayer = ConstitutionLayer.PRINCIPLE,
        description: str = "",
    ) -> ChangeProposal:
        """Propose an update to an existing principle.

        Args:
            principle_id: ID of the principle to update.
            new_rule: New rule text.
            new_weight: New weight (optional).
            layer: Constitution layer (for HITL check).
            description: Human-readable description of why.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "update_principle",
            "principle_id": principle_id,
            "new_rule": new_rule,
            "new_weight": new_weight,
            "layer": layer.value,
        }

        return self.proposal_queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title=f"Update principle '{principle_id}'",
            description=description or f"Update principle '{principle_id}' rule to: {new_rule}",
            changes=changes,
            executor=self._execute_principle_update,
        )

    def propose_principle_add(
        self,
        principle_id: str,
        rule: str,
        layer: ConstitutionLayer = ConstitutionLayer.STRATEGY,
        weight: float = 0.7,
        description: str = "",
    ) -> ChangeProposal:
        """Propose adding a new principle.

        Args:
            principle_id: ID for the new principle.
            rule: Rule text.
            layer: Constitution layer.
            weight: Principle weight.
            description: Human-readable description.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "add_principle",
            "principle_id": principle_id,
            "rule": rule,
            "layer": layer.value,
            "weight": weight,
        }

        return self.proposal_queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title=f"Add principle '{principle_id}'",
            description=description or f"Add new {layer.value} principle: {rule}",
            changes=changes,
            executor=self._execute_principle_add,
        )

    def propose_cib_threshold_update(
        self,
        new_threshold: float,
        new_emergency_threshold: float | None = None,
        description: str = "",
    ) -> ChangeProposal:
        """Propose updating the CIB threshold.

        Args:
            new_threshold: New CIB threshold (0–1).
            new_emergency_threshold: New emergency threshold (optional).
            description: Reason for the change.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "update_cib_threshold",
            "new_threshold": new_threshold,
            "new_emergency_threshold": new_emergency_threshold,
        }

        return self.proposal_queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title=f"Update CIB threshold to {new_threshold}",
            description=description or f"Adjust CIB threshold from current to {new_threshold}",
            changes=changes,
            executor=self._execute_cib_threshold_update,
        )

    def propose_k_scenario_add(
        self,
        scenario_id: str,
        principle: str,
        description: str,
        input_text: str = "",
        expected_behavior: str = "",
        violation_example: str = "",
        direction_function: str = "",
        reason: str = "",
    ) -> ChangeProposal:
        """Propose adding a new K-Scenario.

        Args:
            scenario_id: ID for the new scenario.
            principle: Principle this scenario tests.
            description: Scenario description.
            input_text: Test input.
            expected_behavior: Expected behavior.
            violation_example: Example of violation.
            direction_function: How the direction score is computed.
            reason: Why this scenario is needed.

        Returns:
            :class:`ChangeProposal` with status PENDING.
        """
        changes = {
            "action": "add_k_scenario",
            "scenario_id": scenario_id,
            "principle": principle,
            "description": description,
            "input": input_text,
            "expected_behavior": expected_behavior,
            "violation_example": violation_example,
            "direction_function": direction_function,
        }

        return self.proposal_queue.create(
            type=ProposalType.CONSTITUTION_REVISION,
            title=f"Add K-Scenario '{scenario_id}'",
            description=reason or f"Add new test scenario for principle '{principle}'",
            changes=changes,
            executor=self._execute_k_scenario_add,
        )

    # ------------------------------------------------------------------
    # Execute (called by ProposalQueue after HITL approval)
    # ------------------------------------------------------------------

    def _execute_principle_update(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute a principle update (called after HITL approval)."""
        changes = proposal.changes
        principle_id = changes["principle_id"]
        new_rule = changes["new_rule"]
        new_weight = changes.get("new_weight")
        layer = ConstitutionLayer(changes.get("layer", "principle"))

        # HITL check
        hitl = self.cib_guard.require_hitl_approval(layer, approved=True)
        if not hitl.approved:
            raise PermissionError(f"HITL blocked: {hitl.reason}")

        # Load, modify, save base.yml
        base_data = self._load_base_yaml()
        principles = base_data.get("principles", [])

        updated = False
        for p in principles:
            if p.get("id") == principle_id:
                p["rule"] = new_rule
                if new_weight is not None:
                    p["weight"] = new_weight
                updated = True
                break

        if not updated:
            raise ValueError(f"Principle '{principle_id}' not found")

        base_data["version"] = base_data.get("version", 1) + 1
        self._save_base_yaml(base_data)

        logger.info(f"Principle '{principle_id}' updated (v{base_data['version']})")
        return {"principle_id": principle_id, "new_rule": new_rule, "version": base_data["version"]}

    def _execute_principle_add(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute adding a new principle (called after HITL approval)."""
        changes = proposal.changes
        layer = ConstitutionLayer(changes.get("layer", "strategy"))

        hitl = self.cib_guard.require_hitl_approval(layer, approved=True)
        if not hitl.approved:
            raise PermissionError(f"HITL blocked: {hitl.reason}")

        base_data = self._load_base_yaml()
        principles = base_data.get("principles", [])

        new_principle = {
            "id": changes["principle_id"],
            "rule": changes["rule"],
            "layer": changes["layer"],
            "weight": changes.get("weight", 0.7),
        }
        principles.append(new_principle)
        base_data["principles"] = principles
        base_data["version"] = base_data.get("version", 1) + 1
        self._save_base_yaml(base_data)

        logger.info(f"Principle '{changes['principle_id']}' added (v{base_data['version']})")
        return {"principle_id": changes["principle_id"], "version": base_data["version"]}

    def _execute_cib_threshold_update(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute CIB threshold update (called after HITL approval)."""
        changes = proposal.changes
        new_threshold = changes["new_threshold"]
        new_emergency = changes.get("new_emergency_threshold")

        # CIB threshold is in safety.yml — requires absolute layer HITL
        hitl = self.cib_guard.require_hitl_approval(
            ConstitutionLayer.ABSOLUTE, approved=True,
        )
        if not hitl.approved:
            raise PermissionError(f"HITL blocked: {hitl.reason}")

        safety_data = self._load_safety_yaml()
        cib = safety_data.get("cib", {})
        old_threshold = cib.get("threshold", 0.95)
        cib["threshold"] = new_threshold
        if new_emergency is not None:
            cib["emergency_threshold"] = new_emergency
        safety_data["cib"] = cib
        self._save_safety_yaml(safety_data)

        logger.info(f"CIB threshold updated: {old_threshold} → {new_threshold}")
        return {
            "old_threshold": old_threshold,
            "new_threshold": new_threshold,
            "new_emergency_threshold": new_emergency,
        }

    def _execute_k_scenario_add(self, proposal: ChangeProposal) -> dict[str, Any]:
        """Execute adding a new K-Scenario (called after HITL approval)."""
        changes = proposal.changes

        # K-Scenarios are in base.yml — requires principle layer HITL
        hitl = self.cib_guard.require_hitl_approval(
            ConstitutionLayer.PRINCIPLE, approved=True,
        )
        if not hitl.approved:
            raise PermissionError(f"HITL blocked: {hitl.reason}")

        base_data = self._load_base_yaml()
        scenarios = base_data.get("k_scenarios", [])

        new_scenario = {
            "id": changes["scenario_id"],
            "principle": changes["principle"],
            "description": changes["description"],
            "input": changes.get("input", ""),
            "expected_behavior": changes.get("expected_behavior", ""),
            "violation_example": changes.get("violation_example", ""),
            "direction_function": changes.get("direction_function", ""),
        }
        scenarios.append(new_scenario)
        base_data["k_scenarios"] = scenarios
        base_data["version"] = base_data.get("version", 1) + 1
        self._save_base_yaml(base_data)

        logger.info(f"K-Scenario '{changes['scenario_id']}' added (v{base_data['version']})")
        return {"scenario_id": changes["scenario_id"], "version": base_data["version"]}

    # ------------------------------------------------------------------
    # YAML I/O
    # ------------------------------------------------------------------

    def _load_base_yaml(self) -> dict[str, Any]:
        """Load base.yml as a raw dict."""
        path = self.constitution_dir / "base.yml"
        if not path.exists():
            return {"version": 1, "layers": {}, "principles": [], "k_scenarios": []}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _save_base_yaml(self, data: dict[str, Any]) -> None:
        """Save base.yml."""
        path = self.constitution_dir / "base.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def _load_safety_yaml(self) -> dict[str, Any]:
        """Load safety.yml as a raw dict."""
        path = self.constitution_dir / "safety.yml"
        if not path.exists():
            return {"cib": {"threshold": 0.95, "emergency_threshold": 0.97}}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _save_safety_yaml(self, data: dict[str, Any]) -> None:
        """Save safety.yml."""
        path = self.constitution_dir / "safety.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)