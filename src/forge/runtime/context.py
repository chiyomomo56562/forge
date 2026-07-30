"""Invocation-only context for the conversation runtime."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ConversationContext:
    """checkpoint 메시지에 저장하지 않는 호출 전용 값을 담는다.

    Args:
        system_instruction: 이번 모델 호출에만 적용할 시스템 지시문.
    """

    system_instruction: str = ""
