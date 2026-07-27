"""The only place that knows concrete application and adapter classes."""

from forge.adapters.outbound.llm import ChatClient, LiteLLMCodexGateway
from forge.application.conversation import ReceiveInitialInputService
from forge.ports.outbound import ModelGateway


def build_initial_input_service(
    model_client: ChatClient | None = None,
    *,
    model_gateway: ModelGateway | None = None,
    config_path: str = "config/agent.yml",
) -> ReceiveInitialInputService:
    if model_gateway is not None:
        gateway = model_gateway
    elif model_client is not None:
        gateway = LiteLLMCodexGateway(model_client)
    else:
        gateway = LiteLLMCodexGateway.from_config(config_path)
    return ReceiveInitialInputService(model_gateway=gateway)
