"""LangChain chat-model construction over LiteLLM and the Codex SDK."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# LiteLLM otherwise fetches a mutable remote cost map at import time. Routing
# must remain offline until the selected provider is deliberately invoked.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_litellm import ChatLiteLLM
from litellm.llms.custom_llm import CustomLLM, CustomLLMError
from litellm.types.utils import ModelResponse

CHAT_ROUTE = "forge"
TERRA_MODEL = "gpt-5.6-terra"
CODEX_PROVIDER = "forge_codex"
_CODEX_SDK_AUTH = "codex-sdk-managed"


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """LangChain/LiteLLM 메시지를 Codex SDK용 단일 프롬프트로 바꾼다.

    Args:
        messages: role과 content를 가진 대화 메시지 목록.
    """
    return "\n\n".join(f"{message['role']}: {message['content']}" for message in messages)


class CodexProvider(CustomLLM):  # type: ignore[misc]
    """LiteLLM 요청을 Codex SDK 호출로 변환하는 custom provider다.

    Args:
        codex_factory: Codex SDK 클라이언트를 만드는 함수. 테스트에서 대체할 수 있다.
        target_model: LiteLLM 내부 route 대신 Codex SDK에 전달할 실제 모델 이름.
    """

    def __init__(
        self,
        codex_factory: Callable[[], Any] | None = None,
        *,
        target_model: str | None = None,
    ) -> None:
        super().__init__()
        self._codex_factory = codex_factory or self._default_codex_factory
        self._target_model = target_model

    @staticmethod
    def _default_codex_factory() -> Any:
        """기본 Codex SDK 클라이언트를 생성한다."""
        from openai_codex import Codex

        return Codex()

    def completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        api_base: str,
        custom_prompt_dict: dict[str, Any],
        model_response: ModelResponse,
        print_verbose: Callable[..., Any],
        encoding: Any,
        api_key: str | None,
        logging_obj: Any,
        optional_params: dict[str, Any],
        **_: Any,
    ) -> ModelResponse:
        """LiteLLM의 동기 채팅 요청을 Codex SDK 요청으로 실행한다.

        Args:
            model: LiteLLM이 전달한 내부 모델 route.
            messages: role과 content를 가진 LiteLLM 메시지 목록.
            api_base: LiteLLM 호환용 API 주소 값이며 Codex SDK에서는 사용하지 않는다.
            custom_prompt_dict: LiteLLM 프롬프트 설정이며 Codex SDK에서는 사용하지 않는다.
            model_response: 응답 텍스트를 채워 반환할 LiteLLM 응답 객체.
            print_verbose: LiteLLM의 상세 로그 함수.
            encoding: LiteLLM 토큰 인코딩 정보.
            api_key: 명시적으로 제공된 Codex API 키.
            logging_obj: LiteLLM 요청 로그 객체.
            optional_params: LiteLLM의 추가 모델 호출 옵션.
        """
        from openai_codex import ApprovalMode, Sandbox

        target_model = self._target_model or model
        try:
            with self._codex_factory() as codex:
                if api_key and api_key != _CODEX_SDK_AUTH:
                    codex.login_api_key(api_key)
                thread = codex.thread_start(
                    model=target_model,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                    ephemeral=True,
                )
                result = thread.run(
                    _messages_to_prompt(messages),
                    model=target_model,
                    sandbox=Sandbox.read_only,
                    approval_mode=ApprovalMode.deny_all,
                )
        except Exception as exc:
            raise CustomLLMError(status_code=502, message=f"Codex request failed: {exc}") from exc

        if not result.final_response:
            raise CustomLLMError(status_code=502, message="Codex returned no final response")

        model_response.model = target_model
        model_response.choices[0].message.content = result.final_response
        return model_response

    async def acompletion(self, *args: Any, **kwargs: Any) -> ModelResponse:
        """LiteLLM의 비동기 요청도 같은 동기 Codex SDK 호출로 처리한다.

        Args:
            *args: LiteLLM이 전달하는 위치 인자.
            **kwargs: LiteLLM이 전달하는 이름 있는 인자.
        """
        return self.completion(*args, **kwargs)


@dataclass(frozen=True)
class ChatModelSettings:
    """설정 파일에서 읽은 채팅 모델 생성 값을 보관한다.

    Args:
        backend: 사용할 제공자 이름(openai 또는 ollama).
        model: 제공자에 전달할 실제 모델 이름.
        temperature: 응답 다양성을 조절하는 선택 값.
        max_tokens: 생성할 최대 토큰 수.
        base_url: 제공자 API의 선택 엔드포인트 주소.
        api_key: 제공자 인증에 사용할 선택 API 키.
    """

    backend: str
    model: str
    temperature: float | None
    max_tokens: int | None
    base_url: str | None = None
    api_key: str | None = None


class ChatModelFactory:
    """Forge 대화 runtime이 사용할 LangChain 채팅 모델을 만든다.

    Args:
        settings: 제공자와 모델 관련 설정 값.
        codex_provider: 기본 provider 대신 주입할 Codex provider. 테스트에 사용한다.
    """

    def __init__(
        self,
        settings: ChatModelSettings,
        *,
        codex_provider: CodexProvider | None = None,
    ) -> None:
        """테스트용 provider 주입을 포함한 모델 factory를 초기화한다.

        Args:
            settings: 제공자와 모델 관련 설정 값.
            codex_provider: 기본 provider 대신 사용할 선택 Codex provider.
        """
        self._settings = settings
        self._codex_provider = codex_provider

    @classmethod
    def from_config(cls, config_path: str = "config/agent.yml") -> ChatModelFactory:
        """YAML 설정 파일을 읽어 모델 factory를 만든다.

        Args:
            config_path: llm.backend와 제공자 설정을 읽을 YAML 파일 경로.
        """
        config = _load_yaml(config_path)
        llm = config.get("llm", {})
        backend = llm.get("backend", "ollama")
        selected = llm.get(backend, {})
        return cls(
            ChatModelSettings(
                backend=backend,
                model=selected.get(
                    "model", TERRA_MODEL if backend == "openai" else "glm-5.2:cloud"
                ),
                temperature=selected.get("temperature"),
                max_tokens=selected.get("max_tokens"),
                base_url=selected.get("base_url"),
                api_key=os.environ.get("OPENAI_API_KEY"),
            )
        )

    def create(self) -> BaseChatModel:
        """설정에 맞는 LangChain ChatLiteLLM 인스턴스를 생성한다."""
        parameters: dict[str, Any] = {
            "temperature": self._settings.temperature,
            "max_tokens": self._settings.max_tokens,
        }
        if self._settings.backend == "openai":
            self._register_codex_provider(
                self._codex_provider or CodexProvider(target_model=self._settings.model)
            )
            return ChatLiteLLM(
                model=f"{CODEX_PROVIDER}/{CHAT_ROUTE}",
                api_key=self._settings.api_key or _CODEX_SDK_AUTH,
                **parameters,
            )
        if self._settings.backend == "ollama":
            if not self._settings.base_url:
                raise ValueError("Ollama backend requires llm.ollama.base_url")
            return ChatLiteLLM(
                model=f"ollama_chat/{self._settings.model}",
                api_base=self._settings.base_url,
                **parameters,
            )
        raise ValueError(f"Unsupported Forge LLM backend: {self._settings.backend}")

    def create_with_tools(
        self, tool_schemas: Sequence[dict[str, Any]]
    ) -> BaseChatModel:
        """도구 호출이 가능한 LangChain 채팅 모델을 생성한다.

        Ollama backend는 ``bind_tools``로 JSON Schema를 전달한다.
        Codex(openai) backend는 prompt 기반 flattening으로 구조적 tool_calls
        반환을 지원하지 않으므로 ``NotImplementedError``를 발생시킨다.

        Args:
            tool_schemas: OpenAI function-calling 형식의 도구 정의 목록.
                각 항목은 ``type``, ``function`` 키를 가진 dict이다.

        Raises:
            NotImplementedError: Codex backend에서 tool calling을 요청한 경우.
            ValueError: Ollama backend에 base_url이 없는 경우.

        최종 수정일: 2026-07-31
        """
        model = self.create()
        if self._settings.backend == "openai":
            raise NotImplementedError(
                "Tool calling is not supported for the Codex (openai) backend. "
                "Use the Ollama backend for tool-calling capabilities."
            )
        return model.bind_tools(tool_schemas)

    @staticmethod
    def _register_codex_provider(provider: CodexProvider) -> None:
        """LiteLLM 전역 provider 목록에 Forge Codex provider를 등록한다.

        Args:
            provider: Codex SDK로 요청을 전달할 provider 구현체.
        """
        litellm.custom_provider_map[:] = [
            item for item in litellm.custom_provider_map if item["provider"] != CODEX_PROVIDER
        ]
        litellm.custom_provider_map.append({"provider": CODEX_PROVIDER, "custom_handler": provider})
        from litellm.utils import custom_llm_setup

        custom_llm_setup()


def _load_yaml(config_path: str) -> dict[str, Any]:
    """YAML 설정 파일을 안전하게 읽어 사전으로 반환한다.

    Args:
        config_path: 읽을 YAML 파일 경로. 파일이 없거나 사전 형식이 아니면 빈 사전을 반환한다.
    """
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file) or {}
    return loaded if isinstance(loaded, dict) else {}
