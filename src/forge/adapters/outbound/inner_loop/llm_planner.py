"""LangChain-native tool-call planner for the Inner Loop."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from forge.domain.inner_loop import InnerLoopPlan, PlanStep, ToolExecution

__all__ = ["NativeToolCallPlanner", "PlanGenerationError"]


class PlanGenerationError(RuntimeError):
    """The model did not return a valid native tool call plan."""


class NativeToolCallPlanner:
    """Plan with a model bound to the shared LangChain ``BaseTool`` collection."""

    def __init__(
        self, model: Any, *, tools: Sequence[BaseTool], system_prompt: str | None = None
    ) -> None:
        if system_prompt is not None and not system_prompt.strip():
            raise ValueError("system_prompt must not be empty when provided")
        self._model = model.bind_tools(tools)
        self._tools = frozenset(tool.name for tool in tools)
        self._system_prompt = system_prompt or (
            "You are a task planner. Use tool calls for each independent step "
            "needed to complete the user's request."
        )

    def create_plan(
        self, *, task_request: str, context_episode_ids: Sequence[str]
    ) -> InnerLoopPlan:
        del context_episode_ids
        return self._invoke(
            [SystemMessage(content=self._system_prompt), HumanMessage(content=task_request)]
        )

    def create_plan_after_feedback(
        self,
        *,
        task_request: str,
        context_episode_ids: Sequence[str],
        last_execution: ToolExecution,
        feedback: Mapping[str, object],
        feedback_count: int,
    ) -> InnerLoopPlan:
        del context_episode_ids, last_execution
        return self._invoke(
            [
                SystemMessage(content=self._system_prompt),
                HumanMessage(content=task_request),
                HumanMessage(content=_feedback_prompt(feedback, feedback_count)),
            ]
        )

    def _invoke(self, messages: Sequence[object]) -> InnerLoopPlan:
        response = self._model.invoke(messages)
        if not isinstance(response, AIMessage):
            raise PlanGenerationError("Planner model did not return an AI message")
        calls = response.tool_calls
        if not calls:
            raise PlanGenerationError("Planner returned no tool calls")
        steps: list[PlanStep] = []
        seen: set[str] = set()
        for index, call in enumerate(calls, 1):
            name, args, call_id = call.get("name"), call.get("args"), call.get("id")
            if not isinstance(name, str) or name not in self._tools or not isinstance(args, dict):
                raise PlanGenerationError(f"Invalid tool call at step {index}")
            step_id = (
                call_id if isinstance(call_id, str) and call_id.strip() else f"tool_call_{index}"
            )
            if step_id in seen:
                raise PlanGenerationError(f"Duplicate tool call id: {step_id}")
            seen.add(step_id)
            steps.append(
                PlanStep(
                    step_id=step_id,
                    summary=f"Call {name}",
                    tool_name=name,
                    tool_arguments=dict(args),
                    retry_allowed=True,
                )
            )
        return InnerLoopPlan(
            summary=f"LLM-generated tool-call plan with {len(steps)} step(s).", steps=tuple(steps)
        )


def _feedback_prompt(feedback: Mapping[str, object], feedback_count: int) -> str:
    return (
        "Previous plan execution did not complete the task. Preserve completed steps "
        "from the supplied plan and return tool calls only for remaining work. "
        "This is feedback cycle "
        + str(feedback_count)
        + ".\n"
        + json.dumps(dict(feedback), ensure_ascii=False, sort_keys=True)
    )
