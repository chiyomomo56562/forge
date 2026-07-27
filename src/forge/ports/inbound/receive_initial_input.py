from typing import Protocol

from forge.domain.conversation import InitialInput, ModelReply


class ReceiveInitialInput(Protocol):
    def handle(self, command: InitialInput) -> ModelReply: ...
