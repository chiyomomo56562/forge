"""Reflection loop — extract 4 core reflection fields from execution results.

Processes the execution result and evaluation to extract:
    1. what_worked: What worked well?
    2. what_failed: What failed or went wrong?
    3. next_hint: What hint would help next time?
    4. causal_condition: What were the causal conditions for success/failure?

Uses the LLM for natural-language reflection extraction, with a
heuristic fallback when no LLM is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..llm.client import LLMClient
from ..llm.prompts import get_template
from ..llm.response_parser import parse_reflection
from ..memory.schemas import Reflection, Episode, Evaluation
from ..utils.logging import get_logger

logger = get_logger("agent.cognition.reflection_loop")


@dataclass
class ReflectionResult:
    """Result of the reflection loop."""
    reflection: Reflection
    summary: str = ""
    source: str = "heuristic"  # "llm" or "heuristic"


class ReflectionLoop:
    """Extract reflection fields from execution and evaluation results.

    Args:
        llm_client: Optional :class:`LLMClient` for LLM-based reflection.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reflect(
        self,
        task: str,
        execution_summary: str,
        evaluation: Evaluation | None = None,
        pain_index: float | None = None,
    ) -> ReflectionResult:
        """Extract the 4 reflection fields from execution results.

        Args:
            task: The original task description.
            execution_summary: Summary of what was executed.
            evaluation: Evaluation result (optional).
            pain_index: Pain index from evaluation (optional).

        Returns:
            :class:`ReflectionResult` with the extracted :class:`Reflection`.
        """
        if self.llm_client is not None:
            try:
                return self._reflect_with_llm(task, execution_summary, evaluation, pain_index)
            except Exception as e:
                logger.warning(f"LLM reflection failed, using heuristic: {e}")

        return self._reflect_heuristic(task, execution_summary, evaluation, pain_index)

    def reflect_from_episode(self, episode: Episode) -> ReflectionResult:
        """Convenience: reflect from an :class:`Episode` object.

        Args:
            episode: The episode with execution summary and evaluation.

        Returns:
            :class:`ReflectionResult`.
        """
        return self.reflect(
            task=episode.task,
            execution_summary=episode.execution_summary,
            evaluation=episode.evaluation,
            pain_index=episode.evaluation.pain_index,
        )

    # ------------------------------------------------------------------
    # LLM-based reflection
    # ------------------------------------------------------------------

    def _reflect_with_llm(
        self,
        task: str,
        execution_summary: str,
        evaluation: Evaluation | None,
        pain_index: float | None,
    ) -> ReflectionResult:
        """Use LLM to extract reflection fields."""
        template = get_template("reflection")

        eval_str = ""
        if evaluation is not None:
            eval_str = (
                f"status={evaluation.status.value}, "
                f"success_score={evaluation.success_score}, "
                f"pain_index={evaluation.pain_index}"
            )

        prompt = template.render(
            task=task,
            execution_summary=execution_summary,
            evaluation=eval_str or "(no evaluation)",
            pain_index=str(pain_index or "(not computed)"),
        )

        response = self.llm_client.chat(
            prompt=prompt,
            system=template.system,
        )

        # Check if LLM returned a fallback response
        if response.model == "fallback":
            logger.warning("LLM returned fallback, using heuristic reflection")
            return self._reflect_heuristic(task, execution_summary, evaluation, pain_index)

        parsed = parse_reflection(response.content)
        reflection = Reflection(
            what_worked=parsed.get("what_worked", ""),
            what_failed=parsed.get("what_failed", ""),
            next_hint=parsed.get("next_hint", ""),
            causal_condition=parsed.get("causal_condition", ""),
        )

        # If LLM returned empty reflection, fall back to heuristic
        if reflection.is_empty:
            logger.warning("LLM returned empty reflection, using heuristic")
            return self._reflect_heuristic(task, execution_summary, evaluation, pain_index)

        summary = self._summarise(reflection)
        logger.info(f"LLM reflection extracted: {summary[:80]}")
        return ReflectionResult(reflection=reflection, summary=summary, source="llm")

    # ------------------------------------------------------------------
    # Heuristic reflection
    # ------------------------------------------------------------------

    @staticmethod
    def _reflect_heuristic(
        task: str,
        execution_summary: str,
        evaluation: Evaluation | None,
        pain_index: float | None,
    ) -> ReflectionResult:
        """Rule-based reflection extraction without LLM.

        Uses the execution summary and evaluation status to produce
        basic reflection fields.
        """
        what_worked = ""
        what_failed = ""
        next_hint = ""
        causal_condition = ""

        # Determine success/failure from evaluation
        if evaluation is not None:
            if evaluation.status.value == "Success":
                what_worked = f"Task completed successfully: {execution_summary[:200]}"
                if evaluation.success_score and evaluation.success_score > 0.8:
                    causal_condition = "High success score indicates the approach was effective."
                else:
                    causal_condition = "Task succeeded but with moderate success score."
            elif evaluation.status.value == "Failure":
                what_failed = f"Task failed: {execution_summary[:200]}"
                next_hint = "Review the execution summary and try an alternative approach."
                causal_condition = "Failure may indicate insufficient context or wrong approach."
            elif evaluation.status.value == "Partial":
                what_worked = f"Partially completed: {execution_summary[:100]}"
                what_failed = "Task was not fully completed."
                next_hint = "Focus on the incomplete parts and retry with more context."
                causal_condition = "Partial success suggests the approach is viable but needs refinement."
            else:
                what_worked = f"Task executed: {execution_summary[:200]}"
        else:
            what_worked = f"Task executed: {execution_summary[:200]}"

        # Use pain index for additional insight
        if pain_index is not None and pain_index > 0.5:
            if not what_failed:
                what_failed = f"High pain index ({pain_index:.2f}) indicates significant issues."
            next_hint = next_hint or "Address the high pain index causes before retrying."

        reflection = Reflection(
            what_worked=what_worked,
            what_failed=what_failed,
            next_hint=next_hint,
            causal_condition=causal_condition,
        )

        summary = ReflectionLoop._summarise(reflection)
        logger.info(f"Heuristic reflection extracted: {summary[:80]}")
        return ReflectionResult(reflection=reflection, summary=summary, source="heuristic")

    # ------------------------------------------------------------------
    # Summarisation
    # ------------------------------------------------------------------

    @staticmethod
    def _summarise(reflection: Reflection) -> str:
        """Generate a concise summary of the reflection."""
        parts: list[str] = []
        if reflection.what_worked:
            parts.append(f"성공: {reflection.what_worked[:80]}")
        if reflection.what_failed:
            parts.append(f"실패: {reflection.what_failed[:80]}")
        if reflection.next_hint:
            parts.append(f"힌트: {reflection.next_hint[:80]}")
        if reflection.causal_condition:
            parts.append(f"인과: {reflection.causal_condition[:80]}")
        return " | ".join(parts) if parts else "(empty reflection)"