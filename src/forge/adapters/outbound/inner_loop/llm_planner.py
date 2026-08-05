"""LLM이 도구를 선택해 Inner Loop 실행 계획을 생성하는 planner adapter.

structured output 전략이 적용된 모델을 사용해 JSON plan을 받고,
도구 이름과 arguments를 검증한 뒤 ``InnerLoopPlan`` 으로 변환한다.
JSON 파싱과 schema 검증은 structured output 전략이 담당한다.

최종 수정일: 2026-08-04
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from forge.domain.inner_loop import InnerLoopPlan, PlanStep, ToolExecution, ToolSchema
from forge.domain.llm import ChatMessage
from forge.ports.outbound.model_gateway import ChatModel, StructuredChatModel

from .tool_call_converter import ToolCallConversionError, convert_tool_calls_to_plan

__all__ = [
    "LLMPlanner",
    "NativeToolCallPlanner",
    "PlanGenerationError",
    "PLAN_SCHEMA",
]


PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief description of what this step does.",
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "The tool to call.",
                    },
                    "tool_arguments": {
                        "type": "object",
                        "description": "Arguments to pass to the tool.",
                    },
                },
                "required": ["summary", "tool_name", "tool_arguments"],
            },
        },
    },
    "required": ["steps"],
}


DEFAULT_PLANNER_PROMPT_VERSION = "v1"
DEFAULT_PLANNER_PROMPT = """\
You are a task planner. Create an execution plan using the available tools.

Available tools:
{tool_descriptions}

Rules:
- Only use tools from the available tools list.
- Each step's arguments must be determinable from the task alone.
- Do not create a later step that depends on the result of an earlier step.
- Include all steps needed to complete the task.
- If no tool is needed, still return a plan with at least one step.
"""


class PlanGenerationError(RuntimeError):
    """LLM planner가 유효한 plan을 생성하지 못했을 때 발생한다.

    최종 수정일: 2026-08-04
    """


@dataclass(frozen=True)
class _ParsedStep:
    """plan 파싱 결과의 단일 step."""

    summary: str
    tool_name: str
    tool_arguments: Mapping[str, Any]


class LLMPlanner:
    """structured output 모델로 ``InnerLoopPlan`` 을 생성하는 LLM-driven planner.

    ``StructuredChatModel`` 이 JSON plan을 반환하면, planner는 도구 이름과
    arguments를 검증하고 ``InnerLoopPlan`` 으로 변환한다.
    JSON 파싱과 schema 검증은 전략이 담당하므로 planner는 변환과 정책만 수행한다.

    Args:
        model: ``create_structured_model`` 로 생성된 구조화 출력 모델.
        tool_schemas: planner에 전달할 도구 schema 목록.
        system_prompt: planner system prompt. ``None`` 이면 기본 prompt를 사용.

    최종 수정일: 2026-07-31
    """

    def __init__(
        self,
        model: StructuredChatModel,
        *,
        tool_schemas: Sequence[ToolSchema],
        system_prompt: str | None = None,
    ) -> None:
        if system_prompt is not None and not system_prompt.strip():
            raise ValueError("system_prompt must not be empty when provided")
        self._model = model
        self._tool_schemas = tuple(tool_schemas)
        self._allowed_tool_names = frozenset(s.name for s in tool_schemas)
        self._system_prompt = system_prompt or _build_default_prompt(tool_schemas)

    def create_plan(
        self,
        *,
        task_request: str,
        context_episode_ids: Sequence[str],
    ) -> InnerLoopPlan:
        """task와 도구 설명을 모델에 전달해 ``InnerLoopPlan`` 을 생성한다.

        Args:
            task_request: 실행할 사용자 작업 요청.
            context_episode_ids: L1 context episode ID 목록. 현재 미사용.

        Returns:
            검증된 ``PlanStep`` 목록을 포함한 실행 계획.

        Raises:
            PlanGenerationError: 모델이 유효한 plan을 생성하지 않은 경우.

        최종 수정일: 2026-08-04
        """
        # Episode retrieval is intentionally out of scope for the initial adapter.
        del context_episode_ids

        messages = [
            ChatMessage(role="system", content=self._system_prompt),
            ChatMessage(role="user", content=task_request),
        ]
        return self._plan_from_structured_data(self._model.invoke(messages))

    def create_plan_after_feedback(
        self,
        *,
        task_request: str,
        context_episode_ids: Sequence[str],
        last_execution: ToolExecution,
        feedback: Mapping[str, object],
        feedback_count: int,
    ) -> InnerLoopPlan:
        """이전 실행 결과를 user feedback으로 전달해 수정 계획을 생성한다."""
        del context_episode_ids, last_execution

        messages = [
            ChatMessage(role="system", content=self._system_prompt),
            ChatMessage(role="user", content=task_request),
            ChatMessage(
                role="user",
                content=_feedback_prompt(feedback=feedback, feedback_count=feedback_count),
            ),
        ]
        return self._plan_from_structured_data(self._model.invoke(messages))

    def _plan_from_structured_data(self, data: Mapping[str, Any]) -> InnerLoopPlan:
        steps = self._parse_steps(data)
        if not steps:
            raise PlanGenerationError("Planner returned no tool calls")
        plan_steps = tuple(
            PlanStep(
                step_id=f"step_{index}",
                summary=step.summary,
                tool_name=step.tool_name,
                tool_arguments=step.tool_arguments,
                retry_allowed=True,
            )
            for index, step in enumerate(steps, start=1)
        )
        return InnerLoopPlan(
            summary=f"LLM-generated plan with {len(plan_steps)} step(s).",
            steps=plan_steps,
        )

    def _parse_steps(self, data: Mapping[str, Any]) -> list[_ParsedStep]:
        """구조화 응답에서 step 목록을 추출하고 검증한다.

        Raises:
            PlanGenerationError: step 구조가 유효하지 않은 경우.

        최종 수정일: 2026-08-04
        """
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list):
            raise PlanGenerationError(
                "Planner response missing 'steps' array."
            )

        parsed: list[_ParsedStep] = []
        for index, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                raise PlanGenerationError(
                    f"Step {index + 1} is not an object."
                )
            summary = raw.get("summary")
            tool_name = raw.get("tool_name")
            tool_arguments = raw.get("tool_arguments")

            if not isinstance(summary, str) or not summary.strip():
                raise PlanGenerationError(
                    f"Step {index + 1} missing or invalid 'summary'."
                )
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise PlanGenerationError(
                    f"Step {index + 1} missing or invalid 'tool_name'."
                )
            if tool_name not in self._allowed_tool_names:
                raise PlanGenerationError(
                    f"Step {index + 1} references unknown tool: {tool_name}"
                )
            if not isinstance(tool_arguments, dict):
                raise PlanGenerationError(
                    f"Step {index + 1} 'tool_arguments' is not an object."
                )

            parsed.append(
                _ParsedStep(
                    summary=summary,
                    tool_name=tool_name,
                    tool_arguments=dict(tool_arguments),
                )
            )
        return parsed


class NativeToolCallPlanner:
    """tool-calling 모델 응답을 ``InnerLoopPlan`` 으로 변환하는 planner."""

    def __init__(
        self,
        model: ChatModel,
        *,
        system_prompt: str | None = None,
    ) -> None:
        if system_prompt is not None and not system_prompt.strip():
            raise ValueError("system_prompt must not be empty when provided")
        self._model = model
        self._system_prompt = system_prompt or (
            "You are a task planner. Use tool calls for each independent step needed "
            "to complete the user's request."
        )

    def create_plan(
        self,
        *,
        task_request: str,
        context_episode_ids: Sequence[str],
    ) -> InnerLoopPlan:
        """task를 tool-calling 모델에 전달해 ``InnerLoopPlan`` 을 생성한다."""
        del context_episode_ids

        messages = [
            ChatMessage(role="system", content=self._system_prompt),
            ChatMessage(role="user", content=task_request),
        ]
        response = self._model.invoke(messages)
        try:
            return convert_tool_calls_to_plan(response.tool_calls)
        except ToolCallConversionError as exc:
            raise PlanGenerationError(str(exc)) from exc

    def create_plan_after_feedback(
        self,
        *,
        task_request: str,
        context_episode_ids: Sequence[str],
        last_execution: ToolExecution,
        feedback: Mapping[str, object],
        feedback_count: int,
    ) -> InnerLoopPlan:
        """이전 tool execution feedback을 포함해 tool-calling 계획을 재생성한다."""
        del context_episode_ids, last_execution

        messages = [
            ChatMessage(role="system", content=self._system_prompt),
            ChatMessage(role="user", content=task_request),
            ChatMessage(
                role="user",
                content=_feedback_prompt(feedback=feedback, feedback_count=feedback_count),
            ),
        ]
        response = self._model.invoke(messages)
        try:
            return convert_tool_calls_to_plan(response.tool_calls)
        except ToolCallConversionError as exc:
            raise PlanGenerationError(str(exc)) from exc


def _build_default_prompt(tool_schemas: Sequence[ToolSchema]) -> str:
    """도구 schema 목록으로 default system prompt를 구성한다.

    최종 수정일: 2026-08-04
    """
    descriptions: list[str] = []
    for schema in tool_schemas:
        params = json.dumps(schema.parameters, ensure_ascii=False, indent=2)
        descriptions.append(f"- {schema.name}: {schema.description}\n  Parameters: {params}")
    return DEFAULT_PLANNER_PROMPT.format(
        tool_descriptions="\n".join(descriptions),
    )


def _feedback_prompt(*, feedback: Mapping[str, object], feedback_count: int) -> str:
    """이전 tool execution 결과를 다음 planner 호출에 전달하는 안정적인 prompt."""
    return (
        "Previous plan execution did not complete the task. "
        f"This is feedback cycle {feedback_count}. "
        "Create a revised plan that addresses the safe tool feedback below. "
        "Do not repeat an identical failing step unless the feedback shows it is retryable.\n"
        f"{json.dumps(dict(feedback), ensure_ascii=False, sort_keys=True)}"
    )

