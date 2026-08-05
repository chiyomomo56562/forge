"""Codex provider adapter — LiteLLM custom provider로 Codex SDK를 호출하는 base model 생성.

최종 수정일: 2026-08-04
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# LiteLLM otherwise fetches a mutable remote cost map at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from langchain_litellm import ChatLiteLLM
from litellm.llms.custom_llm import CustomLLM, CustomLLMError
from litellm.types.utils import ModelResponse as LiteLLMModelResponse

from forge.adapters.outbound.llm.providers import LiteLLMChatModel
from forge.ports.outbound.model_gateway import ChatModel

CHAT_ROUTE = "forge"
CODEX_PROVIDER = "forge_codex"
_CODEX_SDK_AUTH = "codex-sdk-managed"


def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """LangChain/LiteLLM 메시지를 Codex SDK용 단일 프롬프트로 바꾼다.

    Args:
        messages: role과 content를 가진 대화 메시지 목록.
    """
    return "\n\n".join(f"{message['role']}: {message['content']}" for message in messages)


class CodexLLMProvider(CustomLLM):  # type: ignore[misc]
    """LiteLLM 요청을 Codex SDK 호출로 변환하는 custom provider.

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
        model_response: LiteLLMModelResponse,
        print_verbose: Callable[..., Any],
        encoding: Any,
        api_key: str | None,
        logging_obj: Any,
        optional_params: dict[str, Any],
        **_: Any,
    ) -> LiteLLMModelResponse:
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

    async def acompletion(self, *args: Any, **kwargs: Any) -> LiteLLMModelResponse:
        """LiteLLM의 비동기 요청도 같은 동기 Codex SDK 호출로 처리한다.

        Args:
            *args: LiteLLM이 전달하는 위치 인자.
            **kwargs: LiteLLM이 전달하는 이름 있는 인자.
        """
        return self.completion(*args, **kwargs)


@dataclass(frozen=True)
class CodexSettings:
    """Codex provider 생성 설정.

    Args:
        model: Codex SDK에 전달할 실제 모델 이름.
        temperature: 응답 다양성 조절 값.
        max_tokens: 생성할 최대 토큰 수.
        api_key: Codex 인증 API 키.
    """

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    api_key: str | None = None


class CodexProvider:
    """Codex SDK로 base ``ChatModel`` 을 생성하는 provider adapter.

    Args:
        settings: Codex provider 생성 설정.
        codex_provider: LiteLLM custom provider 구현체. 테스트에서 대체할 수 있다.

    최종 수정일: 2026-07-31
    """

    def __init__(
        self,
        settings: CodexSettings,
        *,
        codex_provider: CodexLLMProvider | None = None,
    ) -> None:
        self._settings = settings
        self._codex_provider = codex_provider or CodexLLMProvider(target_model=settings.model)

    def create_base(self) -> ChatModel:
        """Codex ChatLiteLLM을 감싼 ``ChatModel`` 을 반환한다."""
        self._register_custom_provider()
        parameters: dict[str, object] = {}
        if self._settings.temperature is not None:
            parameters["temperature"] = self._settings.temperature
        if self._settings.max_tokens is not None:
            parameters["max_tokens"] = self._settings.max_tokens
        model = ChatLiteLLM(
            model=f"{CODEX_PROVIDER}/{CHAT_ROUTE}",
            api_key=self._settings.api_key or _CODEX_SDK_AUTH,
            **parameters,
        )
        return LiteLLMChatModel(model)

    def _register_custom_provider(self) -> None:
        """LiteLLM 전역 provider 목록에 Forge Codex provider를 등록한다."""
        litellm.custom_provider_map[:] = [
            item for item in litellm.custom_provider_map if item["provider"] != CODEX_PROVIDER
        ]
        litellm.custom_provider_map.append(
            {"provider": CODEX_PROVIDER, "custom_handler": self._codex_provider}
        )
        from litellm.utils import custom_llm_setup

        custom_llm_setup()
