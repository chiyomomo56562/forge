"""Ollama provider adapter — ChatLiteLLM로 Ollama API를 호출하는 base model 생성.

최종 수정일: 2026-07-31
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_litellm import ChatLiteLLM

from forge.adapters.outbound.llm.providers import LiteLLMChatModel
from forge.ports.outbound.model_gateway import ChatModel


@dataclass(frozen=True)
class OllamaSettings:
    """Ollama provider 생성 설정.

    Args:
        model: Ollama 모델명 (예: ``glm-5.2:cloud``).
        base_url: Ollama API 엔드포인트 주소.
        temperature: 응답 다양성 조절 값.
        max_tokens: 생성할 최대 토큰 수.
    """

    model: str
    base_url: str
    temperature: float | None = None
    max_tokens: int | None = None


class OllamaProvider:
    """Ollama API로 base ``ChatModel`` 을 생성하는 provider adapter.

    Args:
        settings: Ollama provider 생성 설정.

    최종 수정일: 2026-07-31
    """

    def __init__(self, settings: OllamaSettings) -> None:
        self._settings = settings

    def create_base(self) -> ChatModel:
        """Ollama ChatLiteLLM을 감싼 ``ChatModel`` 을 반환한다."""
        parameters: dict[str, object] = {}
        if self._settings.temperature is not None:
            parameters["temperature"] = self._settings.temperature
        if self._settings.max_tokens is not None:
            parameters["max_tokens"] = self._settings.max_tokens
        model = ChatLiteLLM(
            model=f"ollama_chat/{self._settings.model}",
            api_base=self._settings.base_url,
            **parameters,
        )
        return LiteLLMChatModel(model)
