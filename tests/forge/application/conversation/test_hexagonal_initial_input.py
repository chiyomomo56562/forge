from types import SimpleNamespace

from forge.adapters.outbound.llm import LiteLLMCodexGateway
from forge.application.conversation import ReceiveInitialInputService
from forge.domain.conversation import InitialInput


def test_initial_input_service_depends_on_model_port():
    gateway = LiteLLMCodexGateway(
        SimpleNamespace(chat=lambda prompt: SimpleNamespace(content="reply", model="gpt-5.6-terra"))
    )

    reply = ReceiveInitialInputService(gateway).handle(InitialInput("hello"))

    assert reply.text == "reply"
    assert reply.model == "gpt-5.6-terra"
