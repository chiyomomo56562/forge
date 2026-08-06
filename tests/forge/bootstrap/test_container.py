"""Bootstrap composition tests for conversation tools and Inner Loop planners."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from langchain_core.messages import AIMessage

from forge.bootstrap import container
from forge.domain.conversation import SendMessageCommand
from forge.domain.inner_loop import ToolSchema
from forge.domain.llm import ChatMessage, ModelResponse, ToolCallData


class FakeRegistry:
    def tool_schemas(self) -> Sequence[ToolSchema]:
        return _TOOL_SCHEMAS


class FakeStructuredModel:
    def invoke(self, _messages: Sequence[ChatMessage]) -> Mapping[str, Any]:
        return {
            "steps": [
                {
                    "summary": "List files",
                    "tool_name": "workspace.list_files",
                    "tool_arguments": {"path": "."},
                }
            ]
        }


class ScriptedDomainModel:
    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        self.calls.append(list(messages))
        return self._responses.pop(0)


class RecordingFactory:
    def __init__(self, tool_model: ScriptedDomainModel | None = None) -> None:
        self.tool_model = tool_model or ScriptedDomainModel((ModelResponse(content="done"),))
        self.tool_schemas: Sequence[ToolSchema] | None = None
        self.structured_schema: Mapping[str, Any] | None = None

    def create_tool_model(self, tools: Sequence[ToolSchema]) -> ScriptedDomainModel:
        self.tool_schemas = tuple(tools)
        return self.tool_model

    def create_structured_model(self, response_schema: Mapping[str, Any]) -> FakeStructuredModel:
        self.structured_schema = response_schema
        return FakeStructuredModel()


class LangChainToolCallModel:
    def invoke(self, _messages: Sequence[object]) -> AIMessage:
        return AIMessage(
            content="tool call only",
            tool_calls=[
                {"name": "workspace.read_file", "args": {"path": "note.txt"}, "id": "call_1"}
            ],
        )


_TOOL_SCHEMAS = (
    ToolSchema("workspace.list_files", "List files.", {"type": "object"}),
    ToolSchema("workspace.read_file", "Read file.", {"type": "object"}),
)


def _write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    return path


def _agent_config(workspace: Path, *, conversation_tools: bool = True) -> dict[str, Any]:
    return {
        "llm": {"backend": "ollama", "ollama": {}},
        "model": {
            "tool_calling_strategy": "prompt",
            "structured_output_strategy": "prompt",
        },
        "inner_loop": {"planner": {"type": "deterministic"}},
        "conversation": {
            "tools": {
                "enabled": conversation_tools,
                "max_tool_feedback_bytes": 4096,
                "max_protocol_failures": 2,
                "allow_verification": False,
            }
        },
        "tools": {"workspace_root": str(workspace)},
    }


@pytest.mark.parametrize("planner_type", ["deterministic", "llm", "native_tool"])
def test_inner_loop_planner_type_matrix_is_independent_of_conversation_tools(
    monkeypatch: pytest.MonkeyPatch, planner_type: str
) -> None:
    factory = RecordingFactory()
    monkeypatch.setattr(
        container, "_build_chat_model_factory", lambda _config, **_kwargs: factory
    )
    config = _agent_config(Path.cwd(), conversation_tools=True)
    config["inner_loop"]["planner"]["type"] = planner_type

    planner = container._build_planner(config, FakeRegistry())

    assert planner is not None
    if planner_type == "llm":
        assert factory.structured_schema is not None
        assert factory.tool_schemas is None
    elif planner_type == "native_tool":
        assert factory.tool_schemas == _TOOL_SCHEMAS
        assert factory.structured_schema is None


def test_default_conversation_tools_execute_read_only_tool_e2e(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "note.txt").write_text("hello from workspace", encoding="utf-8")
    model = ScriptedDomainModel(
        (
            ModelResponse(
                content="",
                tool_calls=(
                    ToolCallData(
                        id="call_read",
                        name="workspace.read_file",
                        arguments={"path": "note.txt"},
                    ),
                ),
            ),
            ModelResponse(content="file says hello", model_name="fake-domain"),
        )
    )
    factory = RecordingFactory(model)
    monkeypatch.setattr(
        container, "_build_chat_model_factory", lambda _config, **_kwargs: factory
    )
    config_path = _write_config(tmp_path, _agent_config(tmp_path))

    service = container.build_receive_message_service(config_path=str(config_path))
    reply = service.handle(SendMessageCommand("conv-read", "read note"))

    assert reply.text == "file says hello"
    assert {schema.name for schema in factory.tool_schemas or ()} >= {
        "workspace.read_file",
        "workspace.apply_patch",
    }
    assert len(model.calls) == 2
    feedback = model.calls[1][-1]
    assert feedback.role == "user"
    assert "<tool_result_json>" in feedback.content
    assert "hello from workspace" in feedback.content


def test_default_conversation_tools_reject_apply_patch_without_mutation_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("unchanged\n", encoding="utf-8")
    patch = """\
--- a/note.txt
+++ b/note.txt
@@ -1 +1 @@
-unchanged
+changed
"""
    model = ScriptedDomainModel(
        (
            ModelResponse(
                content="",
                tool_calls=(
                    ToolCallData(
                        id="call_patch",
                        name="workspace.apply_patch",
                        arguments={"patch": patch},
                    ),
                ),
            ),
            ModelResponse(content="cannot patch", model_name="fake-domain"),
        )
    )
    factory = RecordingFactory(model)
    monkeypatch.setattr(
        container, "_build_chat_model_factory", lambda _config, **_kwargs: factory
    )
    config_path = _write_config(tmp_path, _agent_config(tmp_path))

    service = container.build_receive_message_service(config_path=str(config_path))
    reply = service.handle(SendMessageCommand("conv-patch", "patch note"))

    assert reply.text == "cannot patch"
    assert target.read_text(encoding="utf-8") == "unchanged\n"
    feedback = model.calls[1][-1].content
    assert "tool.approval_required" in feedback
    assert '"status": "denied"' in feedback


def test_conversation_tools_disabled_preserves_pure_chat_without_executor(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, _agent_config(tmp_path, conversation_tools=False))
    service = container.build_receive_message_service(
        LangChainToolCallModel(), config_path=str(config_path)
    )

    reply = service.handle(SendMessageCommand("conv-chat", "read note"))

    assert reply.text == "tool call only"
    assert reply.tool_calls[0].name == "workspace.read_file"


def test_unsupported_conversation_tool_strategy_fails_clearly(tmp_path: Path) -> None:
    config = _agent_config(tmp_path)
    config["model"]["tool_calling_strategy"] = "native"
    config_path = _write_config(tmp_path, config)

    with pytest.raises(ValueError, match="Unsupported conversation tools tool_calling_strategy"):
        container.build_receive_message_service(config_path=str(config_path))


def test_unsupported_llm_planner_structured_strategy_fails_clearly() -> None:
    config = _agent_config(Path.cwd(), conversation_tools=False)
    config["inner_loop"]["planner"]["type"] = "llm"
    config["model"]["structured_output_strategy"] = "native"

    with pytest.raises(
        ValueError, match="Unsupported inner_loop planner structured_output_strategy"
    ):
        container._build_planner(config, FakeRegistry())
