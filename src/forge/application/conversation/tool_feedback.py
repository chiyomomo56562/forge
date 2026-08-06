"""Safe model feedback payloads for conversation tool execution."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal, TypedDict

from forge.domain.inner_loop import ToolExecution
from forge.domain.memory import ExecutionOutcome

ToolFeedbackStatus = Literal["success", "denied", "failed"]


class ToolFeedbackPayload(TypedDict):
    tool_call_id: str
    name: str
    status: ToolFeedbackStatus
    summary: str
    output: str
    truncated: bool


_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):.*", re.DOTALL)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"']+")
_POSIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![:/\w$])/[^\s\"',;}\\\]]+")
_SENSITIVE_KEY_PATTERN = (
    r"api[_-]?key|authorization|password|secret|token|access[_-]?token|refresh[_-]?token"
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    rf"(?i)\b({_SENSITIVE_KEY_PATTERN}|bearer)"
    r"(\s*[=:]\s*)"
    r"([^\s,;\"'}]+|\"[^\"\\]*(?:\\.[^\"\\]*)*\"|'[^'\\]*(?:\\.[^'\\]*)*')"
)
_JSON_SENSITIVE_FIELD_RE = re.compile(
    rf'(?i)("(?:{_SENSITIVE_KEY_PATTERN})"\s*:\s*)"[^"]*"'
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")


def serialize_tool_execution(
    execution: ToolExecution,
    *,
    max_output_bytes: int,
    workspace_root: Path | str | None = None,
) -> ToolFeedbackPayload:
    """Serialize a ToolExecution into the exact safe model feedback contract."""
    _require_positive_limit(max_output_bytes)
    safe_output, feedback_truncated = _limited_output(
        _feedback_output(execution),
        max_output_bytes=max_output_bytes,
        workspace_root=workspace_root,
    )
    return {
        "tool_call_id": _sanitize_text(execution.step_id, workspace_root=workspace_root),
        "name": _sanitize_text(_tool_name(execution), workspace_root=workspace_root),
        "status": _status_for(execution.outcome),
        "summary": _sanitize_text(_summary_for(execution), workspace_root=workspace_root),
        "output": safe_output,
        "truncated": execution.truncated or feedback_truncated,
    }


def protocol_failure_feedback(
    *,
    tool_call_id: str | None = None,
    safe_error_code: str = "tool.protocol_failure",
    summary: str = "Tool-call protocol failure.",
    max_output_bytes: int,
    workspace_root: Path | str | None = None,
) -> ToolFeedbackPayload:
    """Build synthetic feedback for malformed tool-call protocol failures."""
    _require_positive_limit(max_output_bytes)
    execution = ToolExecution(
        tool_call_id or "tool_protocol",
        summary,
        ExecutionOutcome.FAILED,
        ("tool_protocol",),
        safe_error_code=safe_error_code,
    )
    return serialize_tool_execution(
        execution, max_output_bytes=max_output_bytes, workspace_root=workspace_root
    )


def _status_for(outcome: ExecutionOutcome) -> ToolFeedbackStatus:
    if outcome is ExecutionOutcome.COMPLETED:
        return "success"
    if outcome is ExecutionOutcome.HALTED:
        return "denied"
    return "failed"


def _summary_for(execution: ToolExecution) -> str:
    if execution.safe_error_code:
        return f"{execution.summary} Error code: {execution.safe_error_code}"
    return execution.summary


def _tool_name(execution: ToolExecution) -> str:
    if execution.tool_names:
        return execution.tool_names[0]
    return "unknown"


def _feedback_output(execution: ToolExecution) -> dict[str, object]:
    output = dict(execution.output)
    if execution.safe_error_code:
        output["safe_error_code"] = execution.safe_error_code
    return output


def _limited_output(
    value: dict[str, object], *, max_output_bytes: int, workspace_root: Path | str | None
) -> tuple[str, bool]:
    text = json.dumps(
        value,
        default=_non_serializable_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    safe_text = _sanitize_text(text, workspace_root=workspace_root)
    return _truncate_utf8(safe_text, max_output_bytes)


def _non_serializable_value(value: object) -> str:
    """비JSON tool output은 내용 대신 타입명으로 안전하게 표현한다."""
    return f"[non-serializable {type(value).__name__}]"


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def _sanitize_text(value: object, *, workspace_root: Path | str | None) -> str:
    text = _TRACEBACK_RE.sub("[traceback redacted]", str(value))
    root = _workspace_root(workspace_root)
    if root:
        text = text.replace(root, "$WORKSPACE")
    text = text.replace(os.getcwd(), "$WORKSPACE")
    text = _WINDOWS_ABSOLUTE_PATH_RE.sub("[path redacted]", text)
    text = _POSIX_ABSOLUTE_PATH_RE.sub("[path redacted]", text)
    text = _JSON_SENSITIVE_FIELD_RE.sub(r'\1"[redacted]"', text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _SENSITIVE_ASSIGNMENT_RE.sub(r"\1\2[redacted]", text)
    return _OPENAI_KEY_RE.sub("[secret redacted]", text)


def _workspace_root(workspace_root: Path | str | None) -> str:
    if workspace_root is None:
        return ""
    return str(Path(workspace_root).resolve())


def _require_positive_limit(max_output_bytes: int) -> None:
    if max_output_bytes <= 0:
        raise ValueError("max_output_bytes must be positive.")


__all__ = [
    "ToolFeedbackPayload",
    "ToolFeedbackStatus",
    "protocol_failure_feedback",
    "serialize_tool_execution",
]
