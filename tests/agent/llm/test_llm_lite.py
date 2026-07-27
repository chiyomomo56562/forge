"""Tests for the LiteLLM router and Codex SDK adapter."""

from __future__ import annotations

from types import SimpleNamespace

from agent.llm.lite import CODEX_PROVIDER, INITIAL_USER_INPUT_ROUTE, TERRA_MODEL, CodexProvider, LLMLite


class _FakeThread:
    def __init__(self):
        self.calls = []

    def run(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(final_response="Terra response")


class _FakeCodex:
    def __init__(self):
        self.thread = _FakeThread()
        self.started = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def thread_start(self, **kwargs):
        self.started.append(kwargs)
        return self.thread


def test_codex_provider_uses_terra_and_read_only():
    fake = _FakeCodex()
    provider = CodexProvider(codex_factory=lambda: fake)
    router = LLMLite(codex_provider=provider)

    response = router.chat("hello")

    assert response.content == "Terra response"
    assert response.model == TERRA_MODEL
    assert fake.started[0]["model"] == TERRA_MODEL
    assert fake.thread.calls[0][1]["model"] == TERRA_MODEL


def test_initial_route_is_the_codex_custom_provider():
    router = LLMLite(codex_provider=CodexProvider(codex_factory=_FakeCodex))

    deployment = router._router.model_list[0]

    assert deployment["litellm_params"]["custom_llm_provider"] == CODEX_PROVIDER
    assert deployment["litellm_params"]["model"] == TERRA_MODEL
