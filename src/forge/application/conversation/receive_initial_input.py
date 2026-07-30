from forge.domain.conversation import AssistantReply, SendMessageCommand
from forge.ports.outbound import ConversationRuntime


class ReceiveMessageService:
    """외부 입력을 대화 runtime으로 전달하는 application facade다.

    Args:
        runtime: 실제 대화 상태와 모델 호출을 수행하는 구현체.
    """

    def __init__(self, runtime: ConversationRuntime) -> None:
        """facade가 호출할 대화 runtime을 주입한다.

        Args:
            runtime: 메시지를 저장하고 모델을 실행할 runtime 구현체.
        """
        self._runtime = runtime

    def handle(self, command: SendMessageCommand) -> AssistantReply:
        """입력을 검증한 뒤 runtime에 전달하고 답변을 반환한다.

        Args:
            command: 대화 ID, 사용자 텍스트, 호출별 지시문을 담은 명령 객체.
        """
        if not command.conversation_id.strip():
            raise ValueError("Conversation ID must not be empty")
        if not command.text.strip():
            raise ValueError("Message text must not be empty")
        return self._runtime.invoke(
            conversation_id=command.conversation_id,
            text=command.text,
            system_instruction=command.system_instruction,
        )
