from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from forge.domain.conversation import AssistantReply
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
