"""Prompt structured-output 전략의 JSON 파싱과 schema 검증 테스트.

최종 수정일: 2026-07-31
"""

from collections.abc import Sequence

import pytest

from forge.adapters.outbound.llm.strategies.prompt_structured import (
    PromptStructuredOutputStrategy,
    StructuredOutputError,
)
from forge.domain.llm import ChatMessage, ModelResponse


class FakeChatModel:
    """고정 응답을 반환하는 fake ChatModel."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[list[ChatMessage]] = []

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        self.calls.append(list(messages))
        return ModelResponse(content=self._content, model_name="fake")


_SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
    },
    "required": ["name", "count"],
}


def test_parse_pure_json_response():
    """모델이 순수 JSON을 반환하면 파싱하여 반환한다."""
    model = FakeChatModel('{"name": "test", "count": 3}')
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    result = structured.invoke([ChatMessage("user", "hello")])

    assert result == {"name": "test", "count": 3}


def test_parse_json_in_code_fence():
    """모델이 ```json 블록으로 JSON을 반환하면 추출하여 파싱한다."""
    model = FakeChatModel('Here is the plan:\n```json\n{"name": "test", "count": 1}\n```\nDone.')
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    result = structured.invoke([ChatMessage("user", "hello")])

    assert result == {"name": "test", "count": 1}


def test_parse_json_embedded_in_text():
    """모델이 텍스트 사이에 JSON을 반환하면 중괄호 기준으로 추출한다."""
    model = FakeChatModel('Sure! {"name": "test", "count": 5} There you go.')
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    result = structured.invoke([ChatMessage("user", "hello")])

    assert result == {"name": "test", "count": 5}


def test_invalid_json_raises():
    """유효한 JSON이 없으면 StructuredOutputError를 발생시킨다."""
    model = FakeChatModel("This is not JSON at all.")
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    with pytest.raises(StructuredOutputError, match="Could not parse JSON"):
        structured.invoke([ChatMessage("user", "hello")])


def test_missing_required_field_raises():
    """required 필드가 누락되면 StructuredOutputError를 발생시킨다."""
    model = FakeChatModel('{"name": "test"}')
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    with pytest.raises(StructuredOutputError, match="Required field 'count'"):
        structured.invoke([ChatMessage("user", "hello")])


def test_wrong_type_raises():
    """필드 type이 불일치하면 StructuredOutputError를 발생시킨다."""
    model = FakeChatModel('{"name": "test", "count": "not a number"}')
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    with pytest.raises(StructuredOutputError, match="expected type 'integer'"):
        structured.invoke([ChatMessage("user", "hello")])


def test_schema_instruction_injected_into_system_message():
    """system message에 schema 지시문이 추가되는지 확인한다."""
    model = FakeChatModel('{"name": "test", "count": 1}')
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    structured.invoke([
        ChatMessage("system", "You are a planner."),
        ChatMessage("user", "hello"),
    ])

    messages = model.calls[0]
    assert messages[0].role == "system"
    assert "JSON Schema" in messages[0].content
    assert "You are a planner." in messages[0].content


def test_schema_instruction_inserted_when_no_system_message():
    """system message가 없으면 새 system message를 삽입한다."""
    model = FakeChatModel('{"name": "test", "count": 1}')
    strategy = PromptStructuredOutputStrategy()
    structured = strategy.apply(model, _SIMPLE_SCHEMA)

    structured.invoke([ChatMessage("user", "hello")])

    messages = model.calls[0]
    assert messages[0].role == "system"
    assert "JSON Schema" in messages[0].content
    assert messages[1].role == "user"
