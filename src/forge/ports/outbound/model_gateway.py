from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from forge.domain.conversation import AssistantReply
from forge.domain.inner_loop import ToolSchema
from forge.domain.llm import ChatMessage, ModelResponse


class ChatModel(Protocol):
    """provider 무관 채팅 모델 계약.

    ``invoke`` 는 내부 ``ChatMessage`` 를 받아 ``ModelResponse`` 를 반환한다.
    adapter가 LangChain/LiteLLM 타입으로 변환한다.
    """

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        """메시지 목록을 모델에 전달하고 응답을 반환한다.

        Args:
            messages: role과 content를 가진 대화 메시지 목록.

        Returns:
            content·tool_calls·model_name을 포함한 provider 무관 응답.
        """
        ...


class StructuredChatModel(Protocol):
    """구조화된 출력을 반환하는 모델 계약.

    ``invoke`` 는 내부 ``ChatMessage`` 를 받아 JSON 호환 dict를 반환한다.
    JSON 파싱과 schema 검증은 전략 adapter가 담당한다.
    """

    def invoke(self, messages: Sequence[ChatMessage]) -> Mapping[str, Any]:
        """메시지 목록을 모델에 전달하고 구조화된 데이터를 반환한다.

        Args:
            messages: role과 content를 가진 대화 메시지 목록.

        Returns:
            ``response_schema`` 에 부합하는 JSON 호환 dict.
        """
        ...


class ChatModelFactory(Protocol):
    """provider와 무관하게 세 가지 모델 생성 기능을 보장하는 경계.

    모든 provider가 세 기능을 동일하게 제공한다.
    전략 선택(native/prompt)은 composition root가 담당하고,
    adapter는 선택된 전략을 구현한다.
    """

    def create_chat(self) -> ChatModel:
        """순수 채팅 모델을 생성한다."""
        ...

    def create_tool_model(
        self,
        tools: Sequence[ToolSchema],
    ) -> ChatModel:
        """도구 호출이 가능한 모델을 생성한다.

        Args:
            tools: 모델에 전달할 도구 schema 목록.
        """
        ...

    def create_structured_model(
        self,
        response_schema: Mapping[str, Any],
    ) -> StructuredChatModel:
        """구조화된 출력을 반환하는 모델을 생성한다.

        Args:
            response_schema: 모델 응답이 따라야 할 JSON Schema.
        """
        ...


class ConversationRuntime(Protocol):
    """application facade가 의존하는 대화 실행 경계다."""

    def invoke(
        self,
        *,
        conversation_id: str,
        text: str,
        system_instruction: str = "",
    ) -> AssistantReply:
        """메시지 한 건을 현재 대화에 반영하고 assistant 답변을 만든다.

        Args:
            conversation_id: 상태를 분리할 대화 ID.
            text: 이번 사용자 입력 텍스트.
            system_instruction: 이번 모델 호출에만 적용할 지시문.
        """
        ...
