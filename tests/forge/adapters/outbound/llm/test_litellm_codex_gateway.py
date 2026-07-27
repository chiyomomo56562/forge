from forge.adapters.outbound.llm import (
    CODEX_PROVIDER,
    INITIAL_USER_INPUT_ROUTE,
    TERRA_MODEL,
    CodexProvider,
    LiteLLMCodexGateway,
)
from forge.domain.conversation import InitialInput


class FakeRunResult:
    final_response = "codex response"


class FakeThread:
    def __init__(self):
        self.calls = []

    def run(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return FakeRunResult()


class FakeCodex:
    def __init__(self):
        self.thread = FakeThread()
        self.thread_start_calls = []
        self.login_keys = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def login_api_key(self, api_key):
        self.login_keys.append(api_key)

    def thread_start(self, **kwargs):
        self.thread_start_calls.append(kwargs)
        return self.thread


def test_codex_provider_uses_read_only_ephemeral_thread(monkeypatch):
    fake = FakeCodex()

    class Sandbox:
        read_only = "read_only"

    class ApprovalMode:
        deny_all = "deny_all"

    monkeypatch.setattr(
        "openai_codex.Sandbox",
        Sandbox,
    )
    monkeypatch.setattr(
        "openai_codex.ApprovalMode",
        ApprovalMode,
    )
    provider = CodexProvider(codex_factory=lambda: fake)

    response = provider.completion(
        model=TERRA_MODEL,
        messages=[{"role": "user", "content": "hello"}],
        api_base="",
        custom_prompt_dict={},
        model_response=__import__("litellm").types.utils.ModelResponse(),
        print_verbose=lambda *_args, **_kwargs: None,
        encoding=None,
        api_key=None,
        logging_obj=None,
        optional_params={},
    )

    assert response.choices[0].message.content == "codex response"
    assert fake.thread_start_calls == [
        {
            "model": TERRA_MODEL,
            "sandbox": "read_only",
            "approval_mode": "deny_all",
            "ephemeral": True,
        }
    ]
    assert fake.thread.calls == [
        (
            "user: hello",
            {
                "model": TERRA_MODEL,
                "sandbox": "read_only",
                "approval_mode": "deny_all",
            },
        )
    ]


def test_gateway_routes_initial_input_through_codex_provider():
    provider = CodexProvider(codex_factory=FakeCodex)
    gateway = LiteLLMCodexGateway(codex_provider=provider)

    reply = gateway.complete_initial_input(InitialInput("hello"))

    assert reply.text == "codex response"
    assert reply.model == TERRA_MODEL


def test_initial_route_is_codex_custom_provider():
    provider = CodexProvider(codex_factory=FakeCodex)
    gateway = LiteLLMCodexGateway(codex_provider=provider)

    deployment = gateway._router.get_available_deployment(
        model=INITIAL_USER_INPUT_ROUTE,
        messages=[{"role": "user", "content": "hello"}],
    )

    assert deployment["litellm_params"]["custom_llm_provider"] == CODEX_PROVIDER
    assert deployment["litellm_params"]["model"] == TERRA_MODEL
