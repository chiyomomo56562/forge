"""The only place that knows concrete application and adapter classes."""

from forge.adapters.outbound.llm import LiteLLMCodexGateway
from forge.application.conversation import ReceiveInitialInputService


def build_initial_input_service(model_client: object) -> ReceiveInitialInputService:
    return ReceiveInitialInputService(LiteLLMCodexGateway(model_client))
