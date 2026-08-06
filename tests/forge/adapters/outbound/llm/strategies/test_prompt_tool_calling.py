"""Prompt tool-calling 전략의 도구 설명 주입과 tool-call 파싱 테스트.

최종 수정일: 2026-08-05
"""

from collections.abc import Sequence

import pytest

from forge.adapters.outbound.llm.strategies.prompt_tool_calling import (
    PromptToolCallingStrategy,
    ToolCallingError,
)
from forge.domain.inner_loop import ToolSchema
from forge.domain.llm import ChatMessage, ModelResponse


class FakeChatModel:
    """고정 응답을 반환하는 fake ChatModel.

    최종 수정일: 2026-08-05
    """

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[list[ChatMessage]] = []

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        self.calls.append(list(messages))
        return ModelResponse(content=self._content, model_name="fake")


class FakeChatModelWithRaw(FakeChatModel):
    """raw 필드를 포함하는 fake ChatModel.

    최종 수정일: 2026-08-05
    """

    def __init__(self, content: str, *, raw: object) -> None:
        super().__init__(content)
        self._raw = raw

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        self.calls.append(list(messages))
        return ModelResponse(content=self._content, model_name="fake", raw=self._raw)


_TOOLS = (
    ToolSchema(
        "workspace.list_files",
        "List files under a workspace path.",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    ),
    ToolSchema(
        "workspace.read_file",
        "Read a file within the workspace.",
        {"type": "object", "properties": {"path": {"type": "string"}}},
    ),
)


def test_tool_instruction_injected_into_system_prompt():
    """도구 설명이 system prompt에 추가되는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel("No tools needed.")
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    tool_model.invoke([ChatMessage("user", "list files")])

    messages = model.calls[0]
    assert messages[0].role == "system"
    assert "workspace.list_files" in messages[0].content
    assert "workspace.read_file" in messages[0].content


def test_tool_instruction_appended_to_existing_system_prompt():
    """기존 system message가 있으면 도구 설명이 추가로 붙는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel("No tools needed.")
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    tool_model.invoke([
        ChatMessage("system", "You are a helpful assistant."),
        ChatMessage("user", "list files"),
    ])

    messages = model.calls[0]
    assert messages[0].role == "system"
    assert "You are a helpful assistant." in messages[0].content
    assert "workspace.list_files" in messages[0].content


def test_no_tools_skips_instruction_injection():
    """도구가 0개면 지시문을 주입하지 않고 base model을 직접 호출하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel("Hello!")
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, ())

    response = tool_model.invoke([ChatMessage("user", "hi")])

    assert response.content == "Hello!"
    # base model이 직접 호출되므로 지시문이 주입되지 않음
    messages = model.calls[0]
    assert all("tool_calls" not in m.content for m in messages)


def test_parse_envelope_single_tool_call():
    """envelope 형식에서 단일 tool-call을 파싱하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": ['
        '{"id": "call_1", "name": "workspace.list_files", '
        '"arguments": {"path": "."}}]}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "list files")])

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "workspace.list_files"
    assert response.tool_calls[0].arguments == {"path": "."}
    assert response.tool_calls[0].id == "call_1"
    # content는 tool-call JSON이 제거되어 빈 문자열
    assert response.content == ""


def test_parse_envelope_multiple_tool_calls():
    """envelope 형식에서 여러 tool-call을 파싱하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": ['
        '{"id": "call_1", "name": "workspace.list_files", "arguments": {"path": "src"}},'
        '{"id": "call_2", "name": "workspace.read_file", "arguments": {"path": "README.md"}}'
        ']}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "explore")])

    assert len(response.tool_calls) == 2
    assert response.tool_calls[0].name == "workspace.list_files"
    assert response.tool_calls[0].id == "call_1"
    assert response.tool_calls[1].name == "workspace.read_file"
    assert response.tool_calls[1].id == "call_2"


def test_missing_id_generates_uuid():
    """id가 없으면 자동으로 생성되는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": [{"name": "workspace.list_files", "arguments": {}}]}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "list")])

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id.startswith("call_")
    assert len(response.tool_calls[0].id) > len("call_")


def test_plain_text_response_no_tool_calls():
    """일반 텍스트 응답은 tool_calls가 빈 튜플인지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel("I can help with that directly without tools.")
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "hello")])

    assert response.tool_calls == ()
    assert response.content == "I can help with that directly without tools."


def test_json_array_in_text_not_parsed_as_tool_call():
    """텍스트 중간의 JSON 배열이 tool-call로 오인되지 않는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        'Here is an example JSON array:\n[{"name": "test", "value": 1}]\nThat was just an example.'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "show example")])

    assert response.tool_calls == ()


def test_code_fence_json_not_parsed_as_tool_call():
    """코드 펜스 내부의 JSON이 tool-call로 오인되지 않는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        "Here is the format:\n```json\n"
        '[{"name": "workspace.list_files", "arguments": {}}]'
        "\n```\nUse this."
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "show format")])

    assert response.tool_calls == ()


def test_envelope_with_unknown_tool_raises():
    """envelope 내 unknown 도구 이름이 ToolCallingError를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": [{"id": "call_1", "name": "unknown.tool", "arguments": {}}]}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="unknown tool"):
        tool_model.invoke([ChatMessage("user", "call unknown")])


def test_envelope_with_invalid_json_raises():
    """envelope 형식이나 잘못된 JSON이 ToolCallingError를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel('{"tool_calls": [invalid json]}')
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="not valid JSON"):
        tool_model.invoke([ChatMessage("user", "bad json")])


def test_json_without_tool_calls_key_treated_as_plain_text():
    """"tool_calls" 키가 없는 JSON 응답은 일반 텍스트로 처리되는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel('{"result": "no tools here"}')
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "json without tool_calls")])

    assert response.tool_calls == ()
    assert response.content == '{"result": "no tools here"}'


def test_envelope_empty_array_raises():
    """tool_calls 배열이 비어있으면 ToolCallingError를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel('{"tool_calls": []}')
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="empty"):
        tool_model.invoke([ChatMessage("user", "empty calls")])


def test_preserves_model_name_and_raw_response():
    """tool-call 파싱 시 model_name과 raw가 보존되는지 검증한다.

    최종 수정일: 2026-08-05
    """
    raw_obj = {"some": "metadata"}
    model = FakeChatModelWithRaw(
        '{"tool_calls": [{"id": "call_1", "name": "workspace.list_files", "arguments": {}}]}',
        raw=raw_obj,
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "list")])

    assert response.model_name == "fake"
    assert response.raw is raw_obj


def test_json_with_tool_calls_in_value_not_envelope():
    """값 안에 "tool_calls" 단어가 포함된 JSON은 envelope으로 오인되지 않는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel('{"answer": "tool_calls 필드는 도구 호출 목록입니다."}')
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    response = tool_model.invoke([ChatMessage("user", "explain tool_calls")])

    assert response.tool_calls == ()
    assert "tool_calls" in response.content


def test_envelope_with_extra_keys_raises():
    """envelope에 tool_calls 외 추가 키가 있으면 ToolCallingError를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": ['
        '{"id": "call_1", "name": "workspace.list_files", "arguments": {}}], '
        '"extra": 1}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="only the 'tool_calls' key"):
        tool_model.invoke([ChatMessage("user", "extra key")])


def test_envelope_missing_arguments_raises():
    """arguments 키가 없는 tool-call이 ToolCallingError를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": [{"id": "call_1", "name": "workspace.list_files"}]}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="missing 'arguments'"):
        tool_model.invoke([ChatMessage("user", "no arguments")])


def test_envelope_non_object_arguments_raises():
    """arguments가 객체가 아닌 경우 ToolCallingError를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": [{"id": "call_1", "name": "workspace.list_files", "arguments": []}]}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="non-object arguments"):
        tool_model.invoke([ChatMessage("user", "array arguments")])


def test_envelope_null_arguments_raises():
    """arguments가 null인 경우 non-object arguments 에러를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": [{"id": "call_1", "name": "workspace.list_files", "arguments": null}]}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="non-object arguments"):
        tool_model.invoke([ChatMessage("user", "null arguments")])


def test_envelope_duplicate_id_raises():
    """중복된 id가 있으면 ToolCallingError를 발생시키는지 검증한다.

    최종 수정일: 2026-08-05
    """
    model = FakeChatModel(
        '{"tool_calls": ['
        '{"id": "call_1", "name": "workspace.list_files", "arguments": {}},'
        '{"id": "call_1", "name": "workspace.read_file", "arguments": {}}'
        ']}'
    )
    strategy = PromptToolCallingStrategy()
    tool_model = strategy.apply(model, _TOOLS)

    with pytest.raises(ToolCallingError, match="Duplicate tool call id"):
        tool_model.invoke([ChatMessage("user", "duplicate id")])
