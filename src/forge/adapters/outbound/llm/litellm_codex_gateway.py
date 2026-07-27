"""LiteLLM routing with a Codex-SDK-backed GPT provider for forge.

The router owns provider selection. The ``codex`` provider is deliberately a
LiteLLM custom provider so GPT traffic reaches ``openai_codex`` rather than the
generic OpenAI Python SDK.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from forge.domain.conversation import InitialInput, ModelReply

# LiteLLM otherwise fetches a mutable remote cost map at import time. Routing
# must remain offline until the selected provider is deliberately invoked.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
import yaml
from litellm import Router
from litellm.llms.custom_llm import CustomLLM, CustomLLMError
from litellm.types.utils import ModelResponse

INITIAL_USER_INPUT_ROUTE = "initial_user_input"
TERRA_MODEL = "gpt-5.6-terra"
CODEX_PROVIDER = "forge_codex"
_CODEX_SDK_AUTH = "codex-sdk-managed"


@dataclass(frozen=True)
class ModelCompletion:
    content: str
    model: str
    raw: object | None = None


class ChatClient(Protocol):
    def chat(self, prompt: str, system: str = "") -> ModelCompletion: ...


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """Preserve role boundaries for the text-only first-turn Codex route."""
    return "\n\n".join(f"{message['role']}: {message['content']}" for message in messages)


class CodexProvider(CustomLLM):
    """LiteLLM custom provider that makes the actual call through Codex SDK."""

    def __init__(self, codex_factory: Callable[[], Any] | None = None) -> None:
        super().__init__()
        self._codex_factory = codex_factory or self._default_codex_factory

    @staticmethod
    def _default_codex_factory() -> Any:
        from openai_codex import Codex

        return Codex()

    def completion(
        self,
        model: str,
        messages: list,
        api_base: str,
        custom_prompt_dict: dict,
        model_response: ModelResponse,
        print_verbose: Callable[..., Any],
        encoding: Any,
        api_key: str | None,
        logging_obj: Any,
        optional_params: dict,
        **_: Any,
    ) -> ModelResponse:
        from openai_codex import ApprovalMode, Sandbox

        try:
            with self._codex_factory() as codex:
                if api_key and api_key != _CODEX_SDK_AUTH:
                    codex.login_api_key(api_key)
                thread = codex.thread_start(
                    model=model,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    ephemeral=True,
                )
                result = thread.run(
                    _messages_to_prompt(messages),
                    model=model,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                )
        except Exception as exc:
            raise CustomLLMError(status_code=502, message=f"Codex request failed: {exc}") from exc

        if not result.final_response:
            raise CustomLLMError(status_code=502, message="Codex returned no final response")

        model_response.model = model
        model_response.choices[0].message.content = result.final_response
        return model_response


class LiteLLMCodexGateway:
    """Forge outbound model gateway backed by LiteLLM routing and Codex SDK."""

    def __init__(
        self,
        client: ChatClient | None = None,
        *,
        codex_provider: CodexProvider | None = None,
        codex_model: str = TERRA_MODEL,
        codex_api_key: str | None = None,
        ollama_base_url: str | None = None,
        ollama_model: str | None = None,
    ) -> None:
        self._client = client
        if client is not None:
            return

        provider = codex_provider or CodexProvider()
        self._codex_provider = provider
        self._codex_model = codex_model
        self._register_codex_provider(provider)
        model_list: list[dict[str, Any]] = [
            {
                "model_name": INITIAL_USER_INPUT_ROUTE,
                "litellm_params": {
                    "model": codex_model,
                    "custom_llm_provider": CODEX_PROVIDER,
                    # LiteLLM validates that a custom provider has credentials
                    # before dispatch. The sentinel means authentication is
                    # managed by the Codex runtime's existing login state.
                    "api_key": codex_api_key or _CODEX_SDK_AUTH,
                },
            }
        ]
        if ollama_base_url and ollama_model:
            model_list.append(
                {
                    "model_name": "ollama_cloud",
                    "litellm_params": {
                        "model": f"ollama_chat/{ollama_model}",
                        "api_base": ollama_base_url,
                    },
                }
            )
        self._router = Router(model_list=model_list, num_retries=0, set_verbose=False)

    @staticmethod
    def _register_codex_provider(provider: CodexProvider) -> None:
        litellm.custom_provider_map[:] = [
            item for item in litellm.custom_provider_map if item["provider"] != CODEX_PROVIDER
        ]
        litellm.custom_provider_map.append(
            {"provider": CODEX_PROVIDER, "custom_handler": provider}
        )
        # Router validates providers during construction; LiteLLM only updates
        # its provider registry when this setup hook is called.
        from litellm.utils import custom_llm_setup

        custom_llm_setup()

    @classmethod
    def from_config(cls, config_path: str = "config/agent.yml") -> "LiteLLMCodexGateway":
        config = _load_yaml(config_path)
        llm_config = config.get("llm", {})
        ollama_config = llm_config.get("ollama", {})
        return cls(
            codex_model=TERRA_MODEL,
            codex_api_key=os.environ.get("OPENAI_API_KEY"),
            ollama_base_url=ollama_config.get("base_url"),
            ollama_model=ollama_config.get("model"),
        )

    def complete_initial_input(self, command: InitialInput) -> ModelReply:
        response = self.chat(command.text)
        return ModelReply(text=response.content, model=response.model)

    def chat(self, prompt: str, system: str = "") -> ModelCompletion:
        if self._client is not None:
            response = self._client.chat(prompt)
            return ModelCompletion(
                content=response.content,
                model=response.model,
                raw=getattr(response, "raw", None),
            )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # LiteLLM Router selects the deployment. Dispatching the Codex route
        # ourselves is intentional: calling ``Router.completion`` would make
        # LiteLLM's OpenAI transport own the GPT request, violating the Codex
        # SDK requirement.
        deployment = self._router.get_available_deployment(
            model=INITIAL_USER_INPUT_ROUTE,
            messages=messages,
        )
        params = deployment["litellm_params"]
        response = self._codex_provider.completion(
            model=params["model"],
            messages=messages,
            api_base="",
            custom_prompt_dict={},
            model_response=ModelResponse(),
            print_verbose=lambda *_args, **_kwargs: None,
            encoding=None,
            api_key=params.get("api_key"),
            logging_obj=None,
            optional_params={},
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("LiteLLM returned an empty response")
        return ModelCompletion(content=content, model=self._codex_model, raw=response)


def _load_yaml(config_path: str) -> dict[str, Any]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}
    if not isinstance(loaded, dict):
        return {}
    return loaded
