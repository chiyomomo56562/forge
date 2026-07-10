"""Planner — execution plan generation.

Takes user input + injected memory context and generates a structured
execution plan using the LLM.  Falls back to a simple heuristic plan
when no LLM is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..llm.client import LLMClient, ChatResponse
from ..llm.prompts import get_template
from ..llm.response_parser import parse_plan
from ..utils.logging import get_logger

logger = get_logger("agent.cognition.planner")


@dataclass
class PlanStep:
    """A single step in an execution plan."""
    description: str
    tool: str = ""
    risk: str = ""
    prerequisites: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """An execution plan produced by the planner."""
    steps: list[PlanStep] = field(default_factory=list)
    estimated_success: float = 0.5
    task_category: str = "general"
    raw_response: str = ""

    @property
    def step_count(self) -> int:
        return len(self.steps)


class Planner:
    """Generate execution plans from user input + memory context.

    Args:
        llm_client: An :class:`LLMClient` instance. If ``None``, uses
            heuristic fallback planning.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        user_input: str,
        context: str = "",
        task_category: str = "general",
    ) -> Plan:
        """Generate an execution plan.

        Args:
            user_input: The user's request.
            context: Injected memory context string (from ContextBuilder).
            task_category: Task category hint.

        Returns:
            :class:`Plan` with steps and metadata.
        """
        if self.llm_client is not None:
            try:
                return self._plan_with_llm(user_input, context, task_category)
            except Exception as e:
                logger.warning(f"LLM planning failed, using heuristic: {e}")

        return self._plan_heuristic(user_input, context, task_category)

    # ------------------------------------------------------------------
    # LLM-based planning
    # ------------------------------------------------------------------

    def _plan_with_llm(
        self,
        user_input: str,
        context: str,
        task_category: str,
    ) -> Plan:
        """Generate a plan using the LLM."""
        template = get_template("planning")
        prompt = template.render(
            user_input=user_input,
            context=context or "(no relevant memories)",
        )

        response: ChatResponse = self.llm_client.chat(
            prompt=prompt,
            system=template.system,
        )

        # Check if LLM returned a fallback response
        if response.model == "fallback":
            logger.warning("LLM returned fallback, using heuristic plan")
            return self._plan_heuristic(user_input, context, task_category)

        parsed = parse_plan(response.content)
        steps = [
            PlanStep(
                description=s if isinstance(s, str) else s.get("description", str(s)),
                tool=s.get("tool", "") if isinstance(s, dict) else "",
                risk=s.get("risk", "") if isinstance(s, dict) else "",
            )
            for s in parsed.get("steps", [])
        ]

        # If parsing yielded no steps, fall back to heuristic
        if not steps:
            logger.warning("LLM returned no parseable steps, using heuristic plan")
            return self._plan_heuristic(user_input, context, task_category)

        plan = Plan(
            steps=steps,
            estimated_success=parsed.get("estimated_success", 0.5),
            task_category=parsed.get("task_category", task_category),
            raw_response=response.content,
        )
        logger.info(
            f"LLM plan generated: {plan.step_count} steps, "
            f"estimated_success={plan.estimated_success:.2f}"
        )
        return plan

    # ------------------------------------------------------------------
    # Heuristic fallback planning
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_heuristic(
        user_input: str,
        context: str,
        task_category: str,
    ) -> Plan:
        """Generate a simple heuristic plan without LLM.

        Creates a basic 3-step plan: analyze → execute → verify.
        """
        steps = [
            PlanStep(
                description=f"Analyze the request: {user_input[:200]}",
                tool="",
                risk="low",
            ),
            PlanStep(
                description="Execute the planned action",
                tool="",
                risk="medium",
                prerequisites=["analysis complete"],
            ),
            PlanStep(
                description="Verify the result meets the user's request",
                tool="",
                risk="low",
                prerequisites=["execution complete"],
            ),
        ]

        plan = Plan(
            steps=steps,
            estimated_success=0.5,
            task_category=task_category,
            raw_response="[heuristic fallback plan]",
        )
        logger.info(f"Heuristic plan generated: {plan.step_count} steps")
        return plan