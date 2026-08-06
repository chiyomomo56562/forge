"""Shared safe invocation boundary for LangChain tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from forge.domain.inner_loop import ToolInvocation, ToolStatus
from forge.ports.outbound import ToolAuthorizationPolicy

from .builtin import BuiltinToolRegistry, ToolInvocationError


def invoke_registered_tool(
    registry: BuiltinToolRegistry,
    authorization: ToolAuthorizationPolicy,
    name: str,
    arguments: Mapping[str, object],
) -> str:
    """Validate, authorize, and execute one workspace-bound tool call."""
    try:
        canonical = registry.validate_arguments(name, dict(arguments))
        invocation = ToolInvocation(name, canonical, "", "", 0)
        if not authorization.authorize(invocation, registry.definition_for(name)):
            return _payload(name, "denied", "Tool invocation was denied.", "tool.approval_required")
        result = registry.execute(invocation)
    except ToolInvocationError as exc:
        return _payload(name, "failed", "Tool invocation failed.", exc.code)

    status = {
        ToolStatus.COMPLETED: "success",
        ToolStatus.DENIED: "denied",
        ToolStatus.FAILED: "failed",
    }[result.status]
    return _payload(
        name, status, result.summary, result.safe_error_code, result.output, result.truncated
    )


def _payload(
    name: str,
    status: str,
    summary: str,
    safe_error_code: str | None = None,
    output: Mapping[str, Any] | None = None,
    truncated: bool = False,
) -> str:
    value: dict[str, object] = {
        "name": name,
        "status": status,
        "summary": summary,
        "output": dict(output or {}),
        "truncated": truncated,
    }
    if safe_error_code:
        value["safe_error_code"] = safe_error_code
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
