from pathlib import Path

import pytest

from forge.application.conversation.tool_feedback import (
    protocol_failure_feedback,
    serialize_tool_execution,
)
from forge.domain.inner_loop import ToolExecution
from forge.domain.memory import ExecutionOutcome


def test_success_feedback_uses_step_id_as_tool_call_id_and_exact_payload_shape():
    execution = ToolExecution(
        "call_1",
        "Read file.",
        ExecutionOutcome.COMPLETED,
        ("workspace.read_file",),
        output={"path": "note.txt", "content": "hello"},
    )

    payload = serialize_tool_execution(execution, max_output_bytes=200)

    assert payload == {
        "tool_call_id": "call_1",
        "name": "workspace.read_file",
        "status": "success",
        "summary": "Read file.",
        "output": '{"content":"hello","path":"note.txt"}',
        "truncated": False,
    }
    assert set(payload) == {"tool_call_id", "name", "status", "summary", "output", "truncated"}


def test_denied_feedback_maps_halted_outcome_and_uses_safe_error_code_only():
    execution = ToolExecution(
        "call_denied",
        "Tool invocation was denied.",
        ExecutionOutcome.HALTED,
        ("workspace.apply_patch",),
        safe_error_code="tool.approval_required",
        audit_details={"tool_arguments": {"patch": "raw patch must not leak"}},
    )

    payload = serialize_tool_execution(execution, max_output_bytes=200)

    assert payload["status"] == "denied"
    assert payload["summary"] == "Tool invocation was denied. Error code: tool.approval_required"
    assert payload["output"] == '{"safe_error_code":"tool.approval_required"}'
    assert "raw patch must not leak" not in str(payload)
    assert "audit_details" not in str(payload)


def test_failed_feedback_maps_failed_outcome_and_keeps_limited_safe_output():
    execution = ToolExecution(
        "call_failed",
        "Command failed.",
        ExecutionOutcome.FAILED,
        ("workspace.verify",),
        safe_error_code="tool.command_failed",
        output={"exit_code": 1, "output": "failed assertion"},
    )

    payload = serialize_tool_execution(execution, max_output_bytes=200)

    assert payload["status"] == "failed"
    assert payload["summary"] == "Command failed. Error code: tool.command_failed"
    assert payload["output"] == (
        '{"exit_code":1,"output":"failed assertion","safe_error_code":"tool.command_failed"}'
    )


def test_output_limit_truncates_on_utf8_boundary_and_tracks_source_truncation():
    execution = ToolExecution(
        "call_multibyte",
        "Read content.",
        ExecutionOutcome.COMPLETED,
        ("workspace.read_file",),
        output={"content": "한글한글"},
        truncated=True,
    )

    payload = serialize_tool_execution(execution, max_output_bytes=17)

    assert payload["output"].encode("utf-8") == b'{"content":"\xed\x95\x9c'
    assert payload["output"] == '{"content":"한'
    assert payload["truncated"] is True


def test_sanitizes_workspace_paths_tracebacks_and_common_secrets(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    absolute_file = workspace / "secrets.py"
    secret = "sk-" + ("a" * 20)
    execution = ToolExecution(
        "call_secret",
        (
            f"Traceback (most recent call last):\n"
            f'  File "{absolute_file}", line 1\n'
            f"RuntimeError: token={secret}"
        ),
        ExecutionOutcome.FAILED,
        ("workspace.verify",),
        output={
            "path": str(absolute_file),
            "api_key": secret,
            "log": "Authorization: Bearer abc.def.ghi password=hunter2 at C:\\Users\\me\\x",
        },
    )

    payload = serialize_tool_execution(
        execution, max_output_bytes=500, workspace_root=workspace
    )
    rendered = str(payload)

    assert str(workspace) not in rendered
    assert secret not in rendered
    assert "hunter2" not in rendered
    assert "abc.def.ghi" not in rendered
    assert "C:\\Users\\me\\x" not in rendered
    assert "File" not in payload["summary"]
    assert "[traceback redacted]" in payload["summary"]
    assert "$WORKSPACE/secrets.py" in payload["output"]
    assert '"api_key":"[redacted]"' in payload["output"]
    assert "Authorization: [redacted]" in payload["output"]
    assert "password=[redacted]" in payload["output"]
    assert "[path redacted]" in payload["output"]


def test_sanitizes_arbitrary_posix_absolute_paths_but_preserves_workspace_context(
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    workspace_file = workspace / "allowed.txt"
    execution = ToolExecution(
        "call_paths",
        f"Read {workspace_file} and /etc/private.txt.",
        ExecutionOutcome.COMPLETED,
        ("workspace.read_file",),
        output={"workspace_path": str(workspace_file), "outside_path": "/var/log/private.log"},
    )

    payload = serialize_tool_execution(
        execution, max_output_bytes=500, workspace_root=workspace
    )

    assert str(workspace) not in str(payload)
    assert "/etc/private.txt" not in payload["summary"]
    assert "/var/log/private.log" not in payload["output"]
    assert "$WORKSPACE/allowed.txt" in payload["summary"]
    assert "$WORKSPACE/allowed.txt" in payload["output"]
    assert "[path redacted]" in payload["summary"]
    assert "[path redacted]" in payload["output"]


def test_sanitizes_access_and_refresh_token_json_fields():
    execution = ToolExecution(
        "call_tokens",
        "Fetched token payload.",
        ExecutionOutcome.COMPLETED,
        ("workspace.read_file",),
        output={
            "access_token": "access-secret-value",
            "refresh_token": "refresh-secret-value",
            "nested": {"access-token": "hyphen-secret-value"},
        },
    )

    payload = serialize_tool_execution(execution, max_output_bytes=500)

    assert "access-secret-value" not in payload["output"]
    assert "refresh-secret-value" not in payload["output"]
    assert "hyphen-secret-value" not in payload["output"]
    assert '"access_token":"[redacted]"' in payload["output"]
    assert '"refresh_token":"[redacted]"' in payload["output"]
    assert '"access-token":"[redacted]"' in payload["output"]


def test_non_json_output_value_is_replaced_without_leaking_its_representation():
    class SecretObject:
        def __repr__(self) -> str:
            return "SecretObject(password=hunter2)"

    execution = ToolExecution(
        "call_non_json",
        "Read output.",
        ExecutionOutcome.COMPLETED,
        ("workspace.read_file",),
        output={"value": SecretObject()},
    )

    payload = serialize_tool_execution(execution, max_output_bytes=500)

    assert payload["output"] == '{"value":"[non-serializable SecretObject]"}'
    assert "hunter2" not in payload["output"]


def test_protocol_failure_feedback_uses_same_contract_with_synthetic_tool_name():
    payload = protocol_failure_feedback(
        tool_call_id="call_bad_json",
        safe_error_code="tool.invalid_json",
        summary="Malformed tool-call envelope.",
        max_output_bytes=200,
    )

    assert payload == {
        "tool_call_id": "call_bad_json",
        "name": "tool_protocol",
        "status": "failed",
        "summary": "Malformed tool-call envelope. Error code: tool.invalid_json",
        "output": '{"safe_error_code":"tool.invalid_json"}',
        "truncated": False,
    }


def test_requires_explicit_positive_output_byte_limit():
    execution = ToolExecution("call_1", "Done.", ExecutionOutcome.COMPLETED)

    with pytest.raises(ValueError, match="max_output_bytes must be positive"):
        serialize_tool_execution(execution, max_output_bytes=0)
