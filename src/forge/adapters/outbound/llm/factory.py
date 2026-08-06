"""provider와 전략을 조합해 세 가지 모델을 생성하는 unified factory.

composition root가 provider adapter와 전략을 주입하면,
factory는 ``ChatModelFactory`` port의 세 기능을 제공한다.

최종 수정일: 2026-08-04
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from forge.adapters.outbound.llm.strategies import (
    StructuredOutputStrategy,
    ToolCallingStrategy,
)
from forge.adapters.outbound.llm.providers import ProviderAdapter
from forge.domain.inner_loop import ToolSchema
from forge.ports.outbound.model_gateway import ChatModel, StructuredChatModel

__all__ = ["UnifiedChatModelFactory"]


class UnifiedChatModelFactory:
    """provider adapter와 전략을 조합해 ``ChatModelFactory`` 계약을 구현한다.

    provider가 무엇이든 동일한 세 기능을 보장한다.
    전략 선택은 composition root가 담당한다.

    Args:
        provider: base ``ChatModel`` 을 생성하는 provider adapter.
        tool_strategy: 도구 호출 기능을 부여하는 전략.
        structured_strategy: 구조화 출력 기능을 부여하는 전략.

    최종 수정일: 2026-08-04
    """

    def __init__(
        self,
        provider: ProviderAdapter,
        tool_strategy: ToolCallingStrategy,
        structured_strategy: StructuredOutputStrategy,
    ) -> None:
        self._provider = provider
        self._tool_strategy = tool_strategy
        self._structured_strategy = structured_strategy

    def create_chat(self) -> ChatModel:
        """순수 채팅 모델을 생성한다."""
        return self._provider.create_base()

    def create_tool_model(
        self,
        tools: Sequence[ToolSchema],
    ) -> ChatModel:
        """도구 호출이 가능한 모델을 생성한다.

        Args:
            tools: 모델에 전달할 도구 schema 목록.
        """
        base = self._provider.create_base()
        return self._tool_strategy.apply(base, tools)

    def create_structured_model(
        self,
        response_schema: Mapping[str, Any],
    ) -> StructuredChatModel:
        """구조화된 출력을 반환하는 모델을 생성한다.

        Args:
            response_schema: 모델 응답이 따라야 할 JSON Schema.
        """
        base = self._provider.create_base()
        return self._structured_strategy.apply(base, response_schema)
