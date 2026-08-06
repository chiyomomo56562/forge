"""Invocation-only context for the conversation runtime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationContext:
    """checkpoint 메시지에 저장하지 않는 호출 전용 값을 담는다.

    Args:
        system_instruction: 이번 모델 호출에만 적용할 시스템 지시문.
        conversation_id: 도구 실행 session_id로 사용할 현재 대화 식별자.
    """

    system_instruction: str = ""
    conversation_id: str = ""
