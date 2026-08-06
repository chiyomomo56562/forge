"""prompt 주입과 JSON 파싱으로 구조화된 출력을 제공하는 전략.

provider의 native structured-output API에 의존하지 않고, 모든 모델에
동일하게 적용할 수 있는 prompt 기반 전략이다.

최종 수정일: 2026-07-31
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from forge.domain.llm import ChatMessage
from forge.ports.outbound.model_gateway import ChatModel, StructuredChatModel

_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


class StructuredOutputError(RuntimeError):
    """모델 응답에서 유효한 구조화 데이터를 추출하지 못했을 때 발생한다.

    최종 수정일: 2026-07-31
    """


class PromptStructuredOutputStrategy:
    """JSON Schema를 prompt에 주입하고 응답에서 JSON을 파싱하는 전략.

    ``apply`` 는 base model을 감싸는 ``StructuredChatModel`` 을 반환한다.
    호출 시 system message에 schema 지시문을 추가하고, 응답 텍스트에서
    JSON을 추출·검증한다.

    최종 수정일: 2026-07-31
    """

    def apply(
        self,
        base_model: ChatModel,
        response_schema: Mapping[str, Any],
    ) -> StructuredChatModel:
        """base model에 구조화 출력 기능을 부여한 wrapper를 반환한다.

        Args:
            base_model: 순수 채팅 모델.
            response_schema: 모델 응답이 따라야 할 JSON Schema.
        """
        return _PromptStructuredModel(base_model, response_schema)


class _PromptStructuredModel:
    """schema 지시문을 주입하고 응답에서 JSON을 파싱하는 StructuredChatModel 구현체.

    Args:
        base_model: 실제 LLM 호출을 수행하는 순수 채팅 모델.
        response_schema: 모델 응답이 따라야 할 JSON Schema.

    최종 수정일: 2026-07-31
    """

    def __init__(
        self,
        base_model: ChatModel,
        response_schema: Mapping[str, Any],
    ) -> None:
        self._base_model = base_model
        self._response_schema = response_schema

    def invoke(self, messages: Sequence[ChatMessage]) -> Mapping[str, Any]:
        """schema 지시문을 주입해 모델을 호출하고 JSON 응답을 파싱한다.

        Args:
            messages: role과 content를 가진 대화 메시지 목록.

        Returns:
            ``response_schema`` 에 부합하는 JSON 호환 dict.

        Raises:
            StructuredOutputError: JSON 파싱 또는 schema 검증에 실패한 경우.

        최종 수정일: 2026-07-31
        """
        injected = self._inject_schema_instruction(messages)
        response = self._base_model.invoke(injected)
        data = _parse_json(response.content)
        _validate_against_schema(data, self._response_schema)
        return data

    def _inject_schema_instruction(
        self, messages: Sequence[ChatMessage]
    ) -> list[ChatMessage]:
        """첫 system message에 schema 지시문을 추가하거나 새 system message를 앞에 삽입한다.

        최종 수정일: 2026-07-31
        """
        instruction = _build_schema_instruction(self._response_schema)
        result: list[ChatMessage] = []
        injected = False
        for message in messages:
            if message.role == "system" and not injected:
                result.append(
                    ChatMessage(
                        role="system",
                        content=f"{message.content}\n\n{instruction}",
                    )
                )
                injected = True
            else:
                result.append(message)
        if not injected:
            result.insert(0, ChatMessage(role="system", content=instruction))
        return result


def _build_schema_instruction(schema: Mapping[str, Any]) -> str:
    """JSON Schema를 모델에 전달할 지시문 텍스트로 만든다.

    Args:
        schema: 모델 응답이 따라야 할 JSON Schema dict.

    최종 수정일: 2026-07-31
    """
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        "You must respond with a single JSON object that conforms to the "
        "following JSON Schema.\n"
        "Return only the JSON object. Do not include any other text.\n\n"
        f"JSON Schema:\n{schema_text}"
    )


def _parse_json(content: str) -> dict[str, Any]:
    """모델 응답 텍스트에서 JSON 객체를 추출한다.

    Args:
        content: 모델이 반환한 원시 텍스트.

    Returns:
        파싱된 JSON dict.

    Raises:
        StructuredOutputError: 유효한 JSON을 찾지 못한 경우.

    최종 수정일: 2026-07-31
    """
    text = content.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    fence_match = _JSON_FENCE.search(text)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            result = json.loads(text[first_brace : last_brace + 1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    raise StructuredOutputError(
        f"Could not parse JSON from model response. "
        f"Content preview: {text[:200]}"
    )


def _validate_against_schema(
    data: dict[str, Any], schema: Mapping[str, Any]
) -> None:
    """기본 JSON Schema 검증을 수행한다.

    ``required`` 필드 존재 여부와 ``type`` 일치만 확인한다.
    전체 JSON Schema 검증은 후속 최적화에서 추가할 수 있다.

    Args:
        data: 파싱된 JSON dict.
        schema: 검증 기준 JSON Schema.

    Raises:
        StructuredOutputError: required 필드 누락 또는 type 불일치.

    최종 수정일: 2026-07-31
    """
    for field_name in schema.get("required", []):
        if field_name not in data:
            raise StructuredOutputError(
                f"Required field '{field_name}' is missing from model response."
            )

    properties = schema.get("properties", {})
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
        "null": type(None),
    }
    for field_name, field_schema in properties.items():
        expected_type = field_schema.get("type")
        if field_name in data and expected_type:
            python_type = type_map.get(expected_type)
            if python_type and not isinstance(data[field_name], python_type):
                raise StructuredOutputError(
                    f"Field '{field_name}' expected type '{expected_type}' "
                    f"but got '{type(data[field_name]).__name__}'."
                )
