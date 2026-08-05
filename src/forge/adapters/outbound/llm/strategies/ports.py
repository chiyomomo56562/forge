"""LLM tool-calling 및 structured-output 전략 port."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from forge.domain.inner_loop import ToolSchema
from forge.ports.outbound.model_gateway import ChatModel, StructuredChatModel


class ToolCallingStrategy(Protocol):
    """도구 호출을 지원하는 모델로 변환하는 전략 경계.

    native 전략은 provider API를 사용하고, prompt 전략은 prompt 주입과
    응답 파싱으로 동일한 ``ChatModel`` 계약을 구현한다.
    """

    def apply(
        self,
        base_model: ChatModel,
        tools: Sequence[ToolSchema],
    ) -> ChatModel:
        """base model에 도구 호출 기능을 부여한 모델을 반환한다.

        Args:
            base_model: 도구 기능이 없는 순수 채팅 모델.
            tools: 모델에 전달할 도구 schema 목록.
        """
        ...


class StructuredOutputStrategy(Protocol):
    """구조화된 출력을 반환하는 모델로 변환하는 전략 경계.

    native 전략은 provider API를 사용하고, prompt 전략은 prompt 주입과
    JSON 파싱·schema 검증으로 동일한 ``StructuredChatModel`` 계약을 구현한다.
    """

    def apply(
        self,
        base_model: ChatModel,
        response_schema: Mapping[str, Any],
    ) -> StructuredChatModel:
        """base model에 구조화 출력 기능을 부여한 모델을 반환한다.

        Args:
            base_model: 구조화 출력 기능이 없는 순수 채팅 모델.
            response_schema: 모델 응답이 따라야 할 JSON Schema.
        """
        ...
