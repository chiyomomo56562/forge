from forge.application.conversation import ReceiveMessageService
from forge.domain.conversation import AssistantReply, SendMessageCommand


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def invoke(
        self,
        *,
        conversation_id: str,
        text: str,
        system_instruction: str = "",
    ) -> AssistantReply:
        self.calls.append((conversation_id, text, system_instruction))
        return AssistantReply(text="reply", model="fake-model")


def test_application_facade_validates_and_delegates_to_runtime():
    runtime = FakeRuntime()
    service = ReceiveMessageService(runtime)

    reply = service.handle(SendMessageCommand("conversation-1", "hello", "be concise"))

    assert reply == AssistantReply(text="reply", model="fake-model")
    assert runtime.calls == [("conversation-1", "hello", "be concise")]


def test_application_facade_rejects_empty_values():
    service = ReceiveMessageService(FakeRuntime())

    for command, message in (
        (SendMessageCommand("", "hello"), "Conversation ID"),
        (SendMessageCommand("conversation-1", "  "), "Message text"),
    ):
        try:
            service.handle(command)
        except ValueError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("Expected validation failure")
