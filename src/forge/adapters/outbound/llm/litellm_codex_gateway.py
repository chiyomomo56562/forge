from typing import Protocol

from forge.domain.conversation import InitialInput, ModelReply


class _ChatClient(Protocol):
    def chat(self, prompt: str) -> object: ...


class LiteLLMCodexGateway:
    """Translate the model-library response into the outbound domain port."""

    def __init__(self, client: _ChatClient) -> None:
        self._client = client

    def complete_initial_input(self, command: InitialInput) -> ModelReply:
        response = self._client.chat(command.text)
        return ModelReply(text=response.content, model=response.model)
