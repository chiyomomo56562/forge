from typing import Protocol

from forge.domain.conversation import InitialInput, ModelReply


class ModelGateway(Protocol):
    def complete_initial_input(self, command: InitialInput) -> ModelReply: ...
