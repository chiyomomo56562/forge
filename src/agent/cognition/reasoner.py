"""Reasoner — plan validation and alternative generation.

Validates the feasibility of a plan, identifies risks, and generates
alternative approaches when the primary plan has issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..llm.client import LLMClient
from ..llm.response_parser import extract_json
from .planner import Plan, PlanStep
from ..utils.logging import get_logger

logger = get_logger("agent.cognition.reasoner")


@dataclass
class ValidationResult:
    """Result of validating a plan."""
    feasible: bool = True
    risks: list[str] = field(default_factory=list)
    missing_prerequisites: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.5


class Reasoner:
    """Validate plans and generate alternatives.

    Args:
        llm_client: Optional :class:`LLMClient` for LLM-based reasoning.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, plan: Plan, context: str = "") -> ValidationResult:
        """Validate a plan's feasibility and identify risks.

        Args:
            plan: The :class:`Plan` to validate.
            context: Injected memory context (for risk assessment).

        Returns:
            :class:`ValidationResult` with feasibility, risks, and suggestions.
        """
        if self.llm_client is not None:
            try:
                return self._validate_with_llm(plan, context)
            except Exception as e:
                logger.warning(f"LLM validation failed, using heuristic: {e}")

        return self._validate_heuristic(plan)

    def generate_alternatives(self, plan: Plan, context: str = "") -> list[Plan]:
        """Generate alternative plans when the primary plan has issues.

        Args:
            plan: The original plan.
            context: Memory context.

        Returns:
            List of alternative :class:`Plan` objects (may be empty).
        """
        if self.llm_client is None:
            return self._alternatives_heuristic(plan)

        try:
            return self._alternatives_with_llm(plan, context)
        except Exception as e:
            logger.warning(f"LLM alternative generation failed: {e}")
            return self._alternatives_heuristic(plan)

    # ------------------------------------------------------------------
    # LLM-based validation
    # ------------------------------------------------------------------

    def _validate_with_llm(self, plan: Plan, context: str) -> ValidationResult:
        """Use LLM to validate the plan."""
        plan_text = "\n".join(
            f"  {i+1}. {s.description}" for i, s in enumerate(plan.steps)
        )
        prompt = (
            f"Validate the following execution plan.\n\n"
            f"## Plan\n{plan_text}\n\n"
            f"## Context\n{context or '(none)'}\n\n"
            f"## Task\n"
            f"Assess feasibility, identify risks, and suggest improvements.\n"
            f"Respond as JSON:\n"
            f'```json\n{{"feasible": true, "risks": ["..."], '
            f'"suggestions": ["..."], "confidence": 0.0}}\n```'
        )

        response = self.llm_client.chat(prompt=prompt)
        data = extract_json(response.content)

        if data and isinstance(data, dict):
            return ValidationResult(
                feasible=data.get("feasible", True),
                risks=data.get("risks", []),
                missing_prerequisites=data.get("missing_prerequisites", []),
                suggestions=data.get("suggestions", []),
                confidence=float(data.get("confidence", 0.5)),
            )

        return ValidationResult(feasible=True, confidence=0.5)

    # ------------------------------------------------------------------
    # Heuristic validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_heuristic(plan: Plan) -> ValidationResult:
        """Rule-based plan validation."""
        risks: list[str] = []
        suggestions: list[str] = []
        missing_prereqs: list[str] = []

        # Check: empty plan
        if not plan.steps:
            risks.append("Plan has no steps")
            return ValidationResult(
                feasible=False, risks=risks, confidence=0.1,
            )

        # Check: estimated success too low
        if plan.estimated_success < 0.3:
            risks.append(f"Low estimated success rate ({plan.estimated_success:.2f})")
            suggestions.append("Consider breaking down the task into smaller steps")

        # Check: steps with unmet prerequisites
        completed = set()
        for i, step in enumerate(plan.steps):
            for prereq in step.prerequisites:
                if prereq not in completed:
                    missing_prereqs.append(f"Step {i+1}: '{prereq}' not yet completed")
            completed.add(step.description.lower())

        # Check: too many steps
        if len(plan.steps) > 10:
            risks.append("Plan has many steps — consider simplifying")
            suggestions.append("Group related steps to reduce complexity")

        feasible = len(risks) == 0 or all("Low" not in r for r in risks)
        confidence = max(0.1, 1.0 - len(risks) * 0.2)

        return ValidationResult(
            feasible=feasible,
            risks=risks,
            missing_prerequisites=missing_prereqs,
            suggestions=suggestions,
            confidence=round(confidence, 2),
        )

    # ------------------------------------------------------------------
    # Alternative generation
    # ------------------------------------------------------------------

    def _alternatives_with_llm(self, plan: Plan, context: str) -> list[Plan]:
        """Use LLM to generate alternative plans."""
        plan_text = "\n".join(
            f"  {i+1}. {s.description}" for i, s in enumerate(plan.steps)
        )
        prompt = (
            f"Generate an alternative execution plan for the same task.\n\n"
            f"## Original Plan\n{plan_text}\n\n"
            f"## Context\n{context or '(none)'}\n\n"
            f"## Output Format\n"
            f'```json\n{{"steps": [...], "estimated_success": 0.0, "task_category": "..."}}\n```'
        )

        response = self.llm_client.chat(prompt=prompt)
        data = extract_json(response.content)

        if data and isinstance(data, dict):
            steps = [
                PlanStep(
                    description=s if isinstance(s, str) else s.get("description", str(s)),
                )
                for s in data.get("steps", [])
            ]
            return [Plan(
                steps=steps,
                estimated_success=float(data.get("estimated_success", 0.5)),
                task_category=data.get("task_category", plan.task_category),
                raw_response=response.content,
            )]

        return []

    @staticmethod
    def _alternatives_heuristic(plan: Plan) -> list[Plan]:
        """Generate a simple alternative by reordering steps."""
        if len(plan.steps) < 2:
            return []

        # Simple alternative: reverse step order (for independent tasks)
        alt_steps = list(reversed(plan.steps))
        return [Plan(
            steps=alt_steps,
            estimated_success=plan.estimated_success * 0.8,
            task_category=plan.task_category,
            raw_response="[heuristic alternative: reversed order]",
        )]