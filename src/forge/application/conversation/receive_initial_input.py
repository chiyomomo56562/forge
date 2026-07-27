from forge.domain.conversation import InitialInput, ModelReply
from forge.ports.outbound import ModelGateway


class ReceiveInitialInputService:
    """First vertical slice: accept text and request a model reply."""

    def __init__(self, model_gateway: ModelGateway) -> None:
        self._model_gateway = model_gateway

    def handle(self, command: InitialInput) -> ModelReply:
        if not command.text.strip():
            raise ValueError("Initial input must not be empty")
        return self._model_gateway.complete_initial_input(command)
