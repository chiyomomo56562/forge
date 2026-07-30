from typing import Protocol

from forge.domain.conversation import AssistantReply, SendMessageCommand


class ReceiveMessage(Protocol):
    """외부 adapter가 application facade를 호출할 때 따르는 인터페이스다."""

    def handle(self, command: SendMessageCommand) -> AssistantReply:
        """사용자 메시지 명령을 처리한다.

        Args:
            command: 대화 ID와 사용자 입력을 담은 application 명령 객체.
        """
        ...
