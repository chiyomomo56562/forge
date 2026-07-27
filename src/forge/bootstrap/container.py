"""The only place that knows concrete application and adapter classes."""

from forge.adapters.outbound.llm import ChatClient, LiteLLMCodexGateway
from forge.application.conversation import ReceiveInitialInputService


def build_initial_input_service(model_client: ChatClient) -> ReceiveInitialInputService:
    gateway = LiteLLMCodexGateway(model_client)
    return ReceiveInitialInputService(model_gateway=gateway)
