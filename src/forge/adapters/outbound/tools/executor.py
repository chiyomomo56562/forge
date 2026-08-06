"""Adapt the shared LangChain tool collection to Inner Loop attempts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, cast

from langchain_core.tools import BaseTool

from forge.domain.inner_loop import PlanStep, ToolExecution
from forge.domain.memory import ExecutionOutcome
from forge.ports.outbound import ToolAuthorizationPolicy


class RegistryPlanStepExecutor:
    """Execute Inner Loop steps through the same ``BaseTool`` instances as ToolNode."""

    def __init__(
        self,
        tools: Sequence[BaseTool] | object,
        authorization: ToolAuthorizationPolicy | None = None,
    ) -> None:
        # Compatibility for non-production callers while all composition paths
        # pass the shared BaseTool collection directly.
        if authorization is not None:
            from .langchain_tools import build_langchain_tools

            tools = build_langchain_tools(tools, authorization)  # type: ignore[arg-type]
        self._tools = {tool.name: tool for tool in tools}

    def execute(self, step: PlanStep, *, session_id: str = "", attempt: int = 0) -> ToolExecution:
        del session_id, attempt
        if step.tool_name is None:
            return ToolExecution(step.step_id, step.summary, ExecutionOutcome.COMPLETED)
        tool = self._tools.get(step.tool_name)
        if tool is None:
            return self._halted(step, "tool.unknown")
        try:
            raw_result = tool.invoke(dict(step.tool_arguments))
            payload = _tool_payload(raw_result)
        except Exception:
            return self._halted(step, "tool.execution_failed", step.tool_arguments)

        status = payload.get("status")
        output = payload.get("output", {})
        if not isinstance(output, Mapping):
            output = {}
        error = payload.get("safe_error_code")
        safe_error_code = error if isinstance(error, str) else None
        if status == "success":
            outcome = ExecutionOutcome.COMPLETED
        elif status == "failed":
            outcome = ExecutionOutcome.FAILED
        else:
            outcome = ExecutionOutcome.HALTED
        summary = payload.get("summary")
        truncated = payload.get("truncated") is True
        return ToolExecution(
            step.step_id,
            summary if isinstance(summary, str) else "Tool invocation completed.",
            outcome,
            (step.tool_name,),
            retryable=outcome is ExecutionOutcome.FAILED,
            safe_error_code=safe_error_code,
            audit_details={
                "tool_status": status,
                "tool_arguments": _audit_arguments(step.tool_arguments),
            },
            output=dict(output),
            truncated=truncated,
        )

    @staticmethod
    def _halted(
        step: PlanStep, code: str, arguments: Mapping[str, object] | None = None
    ) -> ToolExecution:
        details: dict[str, Any] = {"tool_status": "denied"}
        if arguments is not None:
            details["tool_arguments"] = _audit_arguments(arguments)
        return ToolExecution(
            step.step_id,
            "Tool invocation was denied.",
            ExecutionOutcome.HALTED,
            (step.tool_name,) if step.tool_name else (),
            safe_error_code=code,
            audit_details=details,
        )


def _tool_payload(result: object) -> Mapping[str, object]:
    if not isinstance(result, str):
        raise ValueError("Tool returned a non-text result")
    payload = json.loads(result)
    if not isinstance(payload, dict):
        raise ValueError("Tool returned an invalid payload")
    return cast(Mapping[str, object], payload)


def _audit_arguments(value: Mapping[str, object]) -> dict[str, object]:
    canonical = json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return cast(dict[str, object], _redact_strings(canonical))


def _redact_strings(value: object) -> object:
    if isinstance(value, str):
        return {"sha256": sha256(value.encode("utf-8")).hexdigest(), "length": len(value)}
    if isinstance(value, list):
        return [_redact_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_strings(item) for key, item in value.items()}
    return value
