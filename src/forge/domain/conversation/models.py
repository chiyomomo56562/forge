"""Application-facing values for a conversation invocation."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SendMessageCommand:
    """application 경계에서 받는 사용자 메시지 요청이다.

    LangChain의 HumanMessage와 다르다. 호출 메타데이터를 담으며 text만
    LangGraph checkpoint의 메시지 상태로 변환된다.

    Args:
        conversation_id: 이어갈 대화를 식별하는 ID.
        text: 사용자가 입력한 텍스트.
        system_instruction: 이번 모델 호출에만 적용할 시스템 지시문.
    """

    conversation_id: str
    text: str
    system_instruction: str = ""


@dataclass(frozen=True)
class AssistantReply:
    """application facade가 호출자에게 돌려주는 assistant 답변이다.

    Args:
        text: 모델이 생성한 텍스트 답변.
        model: 답변 생성에 사용된 모델 이름.
    """
    text: str
    model: str
