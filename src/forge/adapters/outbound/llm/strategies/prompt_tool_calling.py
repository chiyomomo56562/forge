"""prompt 주입과 응답 파싱으로 tool-calling을 제공하는 전략.

provider의 native tool-calling API에 의존하지 않고, 모든 모델에
동일하게 적용할 수 있는 prompt 기반 전략이다.

출력 계약:
    모델이 도구를 호출할 때는 응답 전체가 아래 단일 JSON 객체여야 한다.

    {"tool_calls": [{"id": "call_1", "name": "tool_name", "arguments": {...}}]}

    도구가 필요 없으면 일반 텍스트로 응답한다. 파서는 전체 응답이
    이 envelope 형식일 때만 tool-call로 해석한다.

보안 경계:
    이 전략은 도구 설명을 prompt에 주입하고 응답을 파싱하는 adapter다.
    인자 schema 검증, 실행 권한, 허용 도구 필터링은 하위 executor 계층
    (``RegistryPlanStepExecutor`` + ``StaticToolAuthorizationPolicy``)이
    담당한다. prompt 기반 방식만으로 prompt injection을 보안적으로
    해결할 수는 없으므로 실행 계층의 검증이 필수다.

최종 수정일: 2026-08-05
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence

from forge.domain.inner_loop import ToolSchema
from forge.domain.llm import ChatMessage, ModelResponse, ToolCallData
from forge.ports.outbound.model_gateway import ChatModel

__all__ = ["PromptToolCallingStrategy", "ToolCallingError"]

_ENVELOPE_PREFIX = re.compile(r'^\{\s*"tool_calls"\s*:')


class ToolCallingError(RuntimeError):
    """응답이 tool-call envelope 형식이나 JSON 파싱에 실패했을 때 발생한다.

    "도구 호출 의도가 있었으나 형식이 잘못됨"과 "도구 호출 없음"을
    구분하기 위해 사용한다.

    최종 수정일: 2026-08-05
    """


class PromptToolCallingStrategy:
    """도구 schema를 prompt에 주입하고 응답에서 tool-call을 파싱하는 전략.

    ``apply`` 는 base model을 감싸는 ``ChatModel`` 을 반환한다.
    호출 시 system message에 도구 설명을 추가하고, 응답이 envelope
    형식이면 tool-call을 추출해 ``ModelResponse.tool_calls`` 를 채운다.

    최종 수정일: 2026-08-05
    """

    def apply(
        self,
        base_model: ChatModel,
        tools: Sequence[ToolSchema],
    ) -> ChatModel:
        """base model에 도구 호출 기능을 부여한 wrapper를 반환한다.

        Args:
            base_model: 순수 채팅 모델.
            tools: 모델에 전달할 도구 schema 목록.
        """
        return _PromptToolModel(base_model, tools)


class _PromptToolModel:
    """도구 설명을 주입하고 응답에서 tool-call을 파싱하는 ChatModel 구현체.

    Args:
        base_model: 실제 LLM 호출을 수행하는 순수 채팅 모델.
        tools: 모델에 전달할 도구 schema 목록.

    최종 수정일: 2026-08-05
    """

    def __init__(
        self,
        base_model: ChatModel,
        tools: Sequence[ToolSchema],
    ) -> None:
        self._base_model = base_model
        self._tools = tuple(tools)
        self._tool_names = frozenset(t.name for t in tools)

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        """도구 설명을 주입해 모델을 호출하고 응답에서 tool-call을 파싱한다.

        Args:
            messages: role과 content를 가진 대화 메시지 목록.

        Returns:
            content와 tool_calls를 포함한 provider 무관 응답.
            도구 호출이 없으면 tool_calls는 빈 튜플이다.
            tool-call을 파싱한 경우 content는 빈 문자열이다.

        Raises:
            ToolCallingError: 응답이 envelope 형식이나 JSON 파싱에 실패한 경우.
        """
        if not self._tools:
            return self._base_model.invoke(messages)

        injected = self._inject_tool_instruction(messages)
        response = self._base_model.invoke(injected)
        tool_calls, content = _parse_tool_calls(response.content, self._tool_names)

        if not tool_calls:
            return response

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            model_name=response.model_name,
            raw=response.raw,
        )

    def _inject_tool_instruction(
        self, messages: Sequence[ChatMessage]
    ) -> list[ChatMessage]:
        """첫 system message에 도구 설명을 추가하거나 새 system message를 삽입한다.

        최종 수정일: 2026-08-05
        """
        instruction = _build_tool_instruction(self._tools)
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


def _build_tool_instruction(tools: Sequence[ToolSchema]) -> str:
    """도구 schema 목록을 모델에 전달할 지시문 텍스트로 만든다.

    Args:
        tools: 모델에 전달할 도구 schema 목록.

    최종 수정일: 2026-08-05
    """
    descriptions: list[str] = []
    for tool in tools:
        params = json.dumps(tool.parameters, ensure_ascii=False)
        descriptions.append(f"- {tool.name}: {tool.description}\n  Parameters: {params}")

    tool_list = "\n".join(descriptions)
    return (
        "You have access to the following tools.\n\n"
        f"Available tools:\n{tool_list}\n\n"
        "When you need to use a tool, your ENTIRE response must be a single "
        "JSON object in this exact format (no other text):\n\n"
        '{"tool_calls": [{"id": "call_1", "name": "tool_name", "arguments": {"key": "value"}}]}\n\n'
        "Rules:\n"
        "- The response must be valid JSON and nothing else when calling tools.\n"
        "- Each tool call must have a unique \"id\" (e.g. \"call_1\", \"call_2\").\n"
        '- "name" must be one of the available tools listed above.\n'
        '- "arguments" must match the tool\'s parameter schema.\n'
        "- If no tool is needed, respond normally with text (not JSON).\n"
        "- Do not include JSON examples or code blocks in non-tool responses."
    )


def _parse_tool_calls(
    content: str,
    allowed_names: frozenset[str],
) -> tuple[tuple[ToolCallData, ...], str]:
    """모델 응답에서 tool-call envelope을 파싱한다.

    전체 응답이 ``{"tool_calls": [...]}`` 형식의 JSON 객체일 때만
    tool-call로 해석한다. 일반 텍스트 응답은 tool-call이 없는 것으로
    처리한다.

    Args:
        content: 모델이 반환한 원시 텍스트.
        allowed_names: registry에 등록된 도구 이름 집합.

    Returns:
        (tool_calls, remaining_content) 튜플.
        tool-call이 파싱되면 remaining_content는 빈 문자열이다.
        tool-call이 없으면 tool_calls는 빈 튜플이고 remaining_content는 원본이다.

    Raises:
        ToolCallingError: envelope 형식이 감지되었으나 JSON 파싱에 실패한 경우.

    최종 수정일: 2026-08-05
    """
    text = content.strip()

    if not _looks_like_envelope(text):
        return (), content

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise ToolCallingError(
            "Response appears to be a tool-call envelope but is not valid JSON."
        )

    if not isinstance(parsed, dict):
        raise ToolCallingError("Tool-call envelope must be a JSON object.")

    if set(parsed) != {"tool_calls"}:
        raise ToolCallingError(
            "Tool-call envelope must contain only the 'tool_calls' key."
        )

    raw_calls = parsed["tool_calls"]
    if not isinstance(raw_calls, list):
        raise ToolCallingError(
            f"'tool_calls' must be a JSON array. Got: {type(raw_calls).__name__}"
        )

    result: list[ToolCallData] = []
    seen_ids: set[str] = set()
    for call in raw_calls:
        if not isinstance(call, dict):
            raise ToolCallingError(
                f"Each tool call must be a JSON object. Got: {type(call).__name__}"
            )
        name = call.get("name", "")
        if not isinstance(name, str) or not name:
            raise ToolCallingError("Tool call missing 'name' field.")
        if name not in allowed_names:
            raise ToolCallingError(
                f"Tool call references unknown tool: {name}"
            )
        if "arguments" not in call:
            raise ToolCallingError(f"Tool call '{name}' missing 'arguments'.")
        arguments = call["arguments"]
        if not isinstance(arguments, dict):
            raise ToolCallingError(
                f"Tool call '{name}' has non-object arguments: "
                f"{type(arguments).__name__}"
            )
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id:
            call_id = f"call_{uuid.uuid4().hex[:8]}"
        if call_id in seen_ids:
            raise ToolCallingError(f"Duplicate tool call id: {call_id}")
        seen_ids.add(call_id)
        result.append(ToolCallData(name=name, arguments=dict(arguments), id=call_id))

    if not result:
        raise ToolCallingError("'tool_calls' array is empty.")

    return tuple(result), ""


def _looks_like_envelope(text: str) -> bool:
    """텍스트가 tool-call envelope 형식인지 판별한다.

    ``{"tool_calls":``로 시작하는 JSON 객체 형태만 envelope으로 인정한다.
    값 안의 ``"tool_calls"`` 단어나 일반 JSON 응답은 제외한다.

    최종 수정일: 2026-08-05
    """
    return bool(_ENVELOPE_PREFIX.match(text))
