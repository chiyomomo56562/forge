"""The only place that knows concrete application and adapter classes."""

from pathlib import Path
from typing import Any

import yaml
from chromadb import PersistentClient

from forge.adapters.outbound.llm import ChatModelFactory
from forge.adapters.outbound.memory import (
    ChromaEpisodeIndex,
    MemorySettings,
    SqliteChromaEpisodeRepository,
    SqliteEpisodeStore,
)
from forge.application.conversation import ReceiveMessageService
from forge.application.memory import (
    PersistEpisodeService,
    ReindexEpisodesService,
    SearchEpisodesService,
)
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


def build_memory_services(
    config_path: str = "config/memory.yml",
) -> tuple[PersistEpisodeService, SearchEpisodesService, ReindexEpisodesService]:
    """Build L1 memory use cases with their concrete outbound adapter."""
    with open(config_path, encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    episodic = config["episodic"]
    settings = MemorySettings(
        sqlite_path=Path(episodic["sqlite_path"]),
        chroma_path=Path(episodic["chroma_path"]),
        collection_name=episodic["collection_name"],
    )
    collection = PersistentClient(path=str(settings.chroma_path)).get_or_create_collection(
        settings.collection_name
    )
    repository = SqliteChromaEpisodeRepository(
        SqliteEpisodeStore(settings.sqlite_path),
        ChromaEpisodeIndex(collection, settings.projection_version, settings.embedding_model_id),
        settings,
    )
    return (
        PersistEpisodeService(repository),
        SearchEpisodesService(repository),
        ReindexEpisodesService(repository),
    )
