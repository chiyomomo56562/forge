"""Convert provider-neutral LLM tool calls into inner-loop plan steps.

최종 수정일: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from forge.domain.inner_loop import InnerLoopPlan, PlanStep
from forge.domain.llm import ToolCallData

__all__ = ["ToolCallConversionError", "convert_tool_calls_to_plan"]


class ToolCallConversionError(ValueError):
    """ToolCallData 구조가 plan 변환에 유효하지 않을 때 발생한다."""


def convert_tool_calls_to_plan(
    tool_calls: Sequence[ToolCallData],
    *,
    summary: str | None = None,
) -> InnerLoopPlan:
    """LLM tool call 목록을 순서를 보존해 ``InnerLoopPlan`` 으로 변환한다.

    이 변환기는 registry나 authorization policy를 모른다. 도구 이름의 존재 여부,
    인자 schema, 실행 권한은 executor 계층이 검증한다.
    """
    if not tool_calls:
        raise ToolCallConversionError("Planner returned no tool calls")

    seen_ids: set[str] = set()
    steps: list[PlanStep] = []
    for index, call in enumerate(tool_calls, start=1):
        name = _validated_name(call.name, index)
        arguments = _validated_arguments(call.arguments, name)
        step_id = _step_id(call.id, index)
        if step_id in seen_ids:
            raise ToolCallConversionError(f"Duplicate tool call id: {step_id}")
        seen_ids.add(step_id)
        steps.append(
            PlanStep(
                step_id=step_id,
                summary=f"Call {name}",
                tool_name=name,
                tool_arguments=dict(arguments),
                retry_allowed=True,
            )
        )

    return InnerLoopPlan(
        summary=summary or f"LLM-generated tool-call plan with {len(steps)} step(s).",
        steps=tuple(steps),
    )


def _validated_name(name: object, index: int) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ToolCallConversionError(f"Tool call {index} missing or invalid name.")
    return name.strip()


def _validated_arguments(arguments: object, name: str) -> Mapping[str, object]:
    if not isinstance(arguments, Mapping):
        raise ToolCallConversionError(f"Tool call '{name}' arguments must be a mapping.")
    return arguments


def _step_id(call_id: object, index: int) -> str:
    if not isinstance(call_id, str):
        raise ToolCallConversionError(f"Tool call {index} id must be a string.")
    if not call_id.strip():
        return f"tool_call_{index}"
    return call_id
