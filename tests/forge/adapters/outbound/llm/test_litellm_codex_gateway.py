from langchain_core.messages import HumanMessage

from forge.adapters.outbound.llm import (
    CHAT_ROUTE,
    CODEX_PROVIDER,
    TERRA_MODEL,
    ChatModelFactory,
    ChatModelSettings,
    CodexProvider,
)


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

    monkeypatch.setattr("openai_codex.Sandbox", Sandbox)
    monkeypatch.setattr("openai_codex.ApprovalMode", ApprovalMode)
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
    assert fake.thread_start_calls[0]["ephemeral"] is True
    assert fake.thread.calls[0][0] == "user: hello"


def test_openai_factory_builds_langchain_router_with_codex_provider():
    factory = ChatModelFactory(
        ChatModelSettings("openai", TERRA_MODEL, 0.2, 128, api_key="key"),
        codex_provider=CodexProvider(codex_factory=FakeCodex),
    )

    model = factory.create()

    assert model.model == f"{CODEX_PROVIDER}/{CHAT_ROUTE}"


def test_langchain_router_invokes_codex_provider(monkeypatch):
    class Sandbox:
        read_only = "read_only"

    class ApprovalMode:
        deny_all = "deny_all"

    monkeypatch.setattr("openai_codex.Sandbox", Sandbox)
    monkeypatch.setattr("openai_codex.ApprovalMode", ApprovalMode)
    fake = FakeCodex()
    factory = ChatModelFactory(
        ChatModelSettings("openai", TERRA_MODEL, 0.2, 128),
        codex_provider=CodexProvider(codex_factory=lambda: fake),
    )

    response = factory.create().invoke([HumanMessage(content="hello")])

    assert response.content == "codex response"


def test_ollama_factory_uses_configured_endpoint():
    factory = ChatModelFactory(ChatModelSettings("ollama", "glm", 0.3, 512, "http://ollama"))

    model = factory.create()

    assert model.model == "ollama_chat/glm"
    assert model.api_base == "http://ollama"
