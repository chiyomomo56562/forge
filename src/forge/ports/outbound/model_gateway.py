from typing import Protocol

from forge.domain.conversation import AssistantReply


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
