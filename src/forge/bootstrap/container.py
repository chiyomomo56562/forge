"""The only place that knows concrete application and adapter classes."""

from typing import Any

from forge.adapters.outbound.llm import ChatModelFactory
from forge.adapters.outbound.memory import JsonlL0EventStore
from forge.application.conversation import ReceiveMessageService
from forge.runtime import LangGraphConversationRuntime


def build_receive_message_service(
    chat_model: Any | None = None,
    *,
    config_path: str = "config/agent.yml",
) -> ReceiveMessageService:
    """프로세스 수명 동안 공유할 대화 facade와 runtime을 조립한다.

    Args:
        chat_model: 테스트 또는 별도 구성에서 주입할 LangChain 채팅 모델.
        config_path: chat_model이 없을 때 읽을 LLM 설정 YAML 경로.
    """
    model = chat_model or ChatModelFactory.from_config(config_path).create()
    return ReceiveMessageService(LangGraphConversationRuntime(model))


def build_l0_event_store(root_path: str = "data/memory/working/sessions") -> JsonlL0EventStore:
    """대화 CLI와 분리된 L0 JSONL 저장소를 조립한다.

    Args:
        root_path: session별 JSONL 및 manifest를 저장할 기준 경로.

    Returns:
        Inner Loop 서비스에 주입할 ``JsonlL0EventStore`` 인스턴스.

    최종 수정일: 2026-07-31
    """
    return JsonlL0EventStore(root_path)
