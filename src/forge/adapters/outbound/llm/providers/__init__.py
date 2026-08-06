"""LLM provider adapter — 원시 모델 생성과 응답 정규화만 담당한다.

provider adapter는 ``ChatModel`` 계약을 구현하는 base model을 생성한다.
tool calling이나 structured output 전략은 상위 factory가 주입한다.

최종 수정일: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from forge.domain.llm import ChatMessage, ModelResponse, ToolCallData
from forge.ports.outbound.model_gateway import ChatModel

__all__ = ["ProviderAdapter", "LiteLLMChatModel"]


class ProviderAdapter(Protocol):
    """base ``ChatModel`` 을 생성하는 provider 경계.

    provider 종류(Ollama, Codex 등)에 따라 구현체가 달라지지만,
    반환 타입은 항상 동일한 ``ChatModel`` 이다.
    """

    def create_base(self) -> ChatModel:
        """순수 채팅 모델을 생성한다."""
        ...


_ROLE_TO_LANGCHAIN: dict[str, type[SystemMessage | HumanMessage | AIMessage]] = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


def _to_langchain_messages(messages: Sequence[ChatMessage]) -> list[Any]:
    """내부 ``ChatMessage`` 를 LangChain 메시지로 변환한다.

    최종 수정일: 2026-07-31
    """
    result: list[Any] = []
    for message in messages:
        cls = _ROLE_TO_LANGCHAIN.get(message.role, HumanMessage)
        result.append(cls(content=message.content))
    return result


def _to_model_response(response: Any) -> ModelResponse:
    """LangChain ``AIMessage`` 를 내부 ``ModelResponse`` 로 정규화한다.

    최종 수정일: 2026-07-31
    """
    content = response.content
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
        content = "".join(text_parts)
    elif not isinstance(content, str):
        content = str(content)

    raw_calls = getattr(response, "tool_calls", None) or []
    tool_calls = tuple(
        ToolCallData(
            name=call.get("name", ""),
            arguments=dict(call.get("args") or call.get("arguments") or {}),
            id=call.get("id", ""),
        )
        for call in raw_calls
        if call.get("name")
    )

    model_name = str(response.response_metadata.get("model_name", "")) if hasattr(
        response, "response_metadata"
    ) else ""

    return ModelResponse(
        content=content,
        tool_calls=tool_calls,
        model_name=model_name,
        raw=response,
    )


class LiteLLMChatModel:
    """LangChain ``BaseChatModel`` 을 내부 ``ChatModel`` 계약으로 감싸는 adapter.

    Args:
        model: LangChain 호환 채팅 모델 인스턴스.

    최종 수정일: 2026-07-31
    """

    def __init__(self, model: BaseChatModel) -> None:
        self._model = model

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        """내부 메시지를 LangChain 메시지로 변환해 모델을 호출한다.

        Args:
            messages: role과 content를 가진 대화 메시지 목록.

        Returns:
            content·tool_calls·model_name을 포함한 provider 무관 응답.
        """
        langchain_messages = _to_langchain_messages(messages)
        response = self._model.invoke(langchain_messages)
        return _to_model_response(response)
