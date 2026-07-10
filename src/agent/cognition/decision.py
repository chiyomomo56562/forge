"""Decision — final plan selection and priority ordering.

Takes the primary plan, validation result, and alternatives, then
selects the best plan and orders steps by priority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..llm.client import LLMClient
from .planner import Plan, PlanStep
from .reasoner import ValidationResult
from ..utils.logging import get_logger

logger = get_logger("agent.cognition.decision")


@dataclass
class DecisionResult:
    """Result of the decision stage."""
    selected_plan: Plan | None = None
    selection_reason: str = ""
    alternatives_considered: int = 0
    confidence: float = 0.5


class DecisionMaker:
    """Select the best plan and finalize the execution strategy.

    Args:
        llm_client: Optional :class:`LLMClient` for LLM-based selection.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decide(
        self,
        primary_plan: Plan,
        validation: ValidationResult,
        alternatives: list[Plan] | None = None,
    ) -> DecisionResult:
        """Select the best plan from primary + alternatives.

        Selection logic:
            1. If primary plan is feasible and confidence >= 0.5 → select primary
            2. If primary has issues and alternatives exist → pick best alternative
            3. If no alternatives → select primary with warnings

        Args:
            primary_plan: The primary plan from the planner.
            validation: Validation result from the reasoner.
            alternatives: Alternative plans from the reasoner.

        Returns:
            :class:`DecisionResult` with the selected plan.
        """
        alternatives = alternatives or []

        # If primary plan is feasible with good confidence, select it
        if validation.feasible and validation.confidence >= 0.5:
            ordered_plan = self._order_steps(primary_plan)
            result = DecisionResult(
                selected_plan=ordered_plan,
                selection_reason="Primary plan is feasible with sufficient confidence",
                alternatives_considered=len(alternatives),
                confidence=validation.confidence,
            )
            logger.info(f"Selected primary plan (confidence={validation.confidence:.2f})")
            return result

        # Primary plan has issues — check alternatives
        if alternatives:
            best_alt = self._select_best_alternative(alternatives)
            if best_alt.estimated_success > primary_plan.estimated_success:
                ordered_plan = self._order_steps(best_alt)
                result = DecisionResult(
                    selected_plan=ordered_plan,
                    selection_reason=(
                        f"Primary plan had issues (risks: {validation.risks}). "
                        f"Selected alternative with higher estimated success."
                    ),
                    alternatives_considered=len(alternatives),
                    confidence=best_alt.estimated_success,
                )
                logger.info("Selected alternative plan")
                return result

        # No better alternative — use primary with warnings
        ordered_plan = self._order_steps(primary_plan)
        warnings = "; ".join(validation.risks) if validation.risks else "none"
        result = DecisionResult(
            selected_plan=ordered_plan,
            selection_reason=(
                f"Primary plan selected despite risks: {warnings}. "
                f"No better alternatives available."
            ),
            alternatives_considered=len(alternatives),
            confidence=validation.confidence,
        )
        logger.warning(f"Selected primary plan with risks: {warnings}")
        return result

    # ------------------------------------------------------------------
    # Step ordering
    # ------------------------------------------------------------------

    @staticmethod
    def _order_steps(plan: Plan) -> Plan:
        """Order plan steps by priority (prerequisites first).

        Steps with no prerequisites come first, then steps whose
        prerequisites are already satisfied.

        Returns:
            New :class:`Plan` with ordered steps.
        """
        if not plan.steps:
            return plan

        ordered: list[PlanStep] = []
        remaining = list(plan.steps)
        completed: set[str] = set()

        while remaining:
            progress = False
            for step in remaining[:]:
                prereqs = [p.lower() for p in step.prerequisites]
                if not prereqs or all(p in completed for p in prereqs):
                    ordered.append(step)
                    completed.add(step.description.lower())
                    remaining.remove(step)
                    progress = True
            if not progress:
                # Circular dependency or unresolvable prereqs — append remaining
                ordered.extend(remaining)
                break

        return Plan(
            steps=ordered,
            estimated_success=plan.estimated_success,
            task_category=plan.task_category,
            raw_response=plan.raw_response,
        )

    # ------------------------------------------------------------------
    # Alternative selection
    # ------------------------------------------------------------------

    @staticmethod
    def _select_best_alternative(alternatives: list[Plan]) -> Plan:
        """Select the alternative with the highest estimated success."""
        return max(alternatives, key=lambda p: p.estimated_success)