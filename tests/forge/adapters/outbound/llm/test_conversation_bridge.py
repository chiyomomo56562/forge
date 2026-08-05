"""Conversation bridge tests for domain ChatModel integration."""

from __future__ import annotations

import json
from collections.abc import Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from forge.adapters.outbound.llm.conversation_bridge import DomainChatModelBridge
from forge.domain.llm import ChatMessage, ModelResponse, ToolCallData


class RecordingDomainModel:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.calls: list[list[ChatMessage]] = []

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        self.calls.append(list(messages))
        return self.response


def test_bridge_preserves_system_human_and_ai_roles_and_content():
    model = RecordingDomainModel(ModelResponse(content="done", model_name="fake-domain"))
    bridge = DomainChatModelBridge(model)

    response = bridge.invoke(
        [
            SystemMessage(content="system"),
            HumanMessage(content="hello"),
            AIMessage(content="previous"),
        ]
    )

    assert [(message.role, message.content) for message in model.calls[0]] == [
        ("system", "system"),
        ("user", "hello"),
        ("assistant", "previous"),
    ]
    assert response.content == "done"
    assert response.response_metadata["model_name"] == "fake-domain"


def test_bridge_returns_ai_message_with_domain_tool_calls():
    model = RecordingDomainModel(
        ModelResponse(
            content="",
            model_name="fake-domain",
            tool_calls=(
                ToolCallData(
                    id="call_1",
                    name="workspace.read_file",
                    arguments={"path": "note.txt"},
                ),
            ),
        )
    )

    response = DomainChatModelBridge(model).invoke([HumanMessage(content="read")])

    assert response.tool_calls[0]["name"] == "workspace.read_file"
    assert response.tool_calls[0]["args"] == {"path": "note.txt"}
    assert response.tool_calls[0]["id"] == "call_1"


def test_bridge_exposes_tool_message_as_explicit_feedback_payload():
    payload = {
        "tool_call_id": "call_1",
        "name": "workspace.read_file",
        "status": "success",
        "summary": "Read file.",
        "output": '{"content":"hello"}',
        "truncated": False,
    }
    model = RecordingDomainModel(ModelResponse(content="done"))

    DomainChatModelBridge(model).invoke(
        [ToolMessage(content=json.dumps(payload), tool_call_id="call_1")]
    )

    message = model.calls[0][0]
    assert message.role == "user"
    assert message.content.startswith("<tool_result_json>")
    assert "not a user request" in message.content
    assert json.dumps(payload) in message.content
