"""구체 adapter와 application service를 조립하는 composition root."""

from pathlib import Path
from typing import Any

import yaml
from chromadb import PersistentClient

from forge.adapters.outbound.llm import ChatModelFactory
from forge.adapters.outbound.memory import (
    ChromaEpisodeIndex,
    JsonlL0EventStore,
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
    chat_model: Any | None = None, *, config_path: str = "config/agent.yml"
) -> ReceiveMessageService:
    """대화 facade와 LangGraph runtime을 조립한다.

    Args:
        chat_model: 테스트/별도 구성에서 주입할 선택적 모델.
        config_path: 모델 미주입 시 읽을 LLM 설정 경로.

    Returns:
        준비된 대화 입력 facade.

    최종 수정일: 2026-07-31
    """
    model = chat_model or ChatModelFactory.from_config(config_path).create()
    return ReceiveMessageService(LangGraphConversationRuntime(model))


def build_l0_event_store(root_path: str = "data/memory/working/sessions") -> JsonlL0EventStore:
    """대화 CLI와 분리된 L0 JSONL 저장소를 조립한다.

    Args:
        root_path: session별 JSONL 및 manifest를 저장할 기준 경로.

    Returns:
        Inner Loop 서비스에 주입할 L0 저장소.

    최종 수정일: 2026-07-31
    """
    return JsonlL0EventStore(root_path)


def build_memory_services(
    config_path: str = "config/memory.yml",
) -> tuple[PersistEpisodeService, SearchEpisodesService, ReindexEpisodesService]:
    """SQLite 정본과 Chroma projection을 사용하는 L1 service를 조립한다.

    Args:
        config_path: L1 SQLite/Chroma 경로를 담은 YAML 설정 경로.

    Returns:
        persist, search, reindex application service tuple.

    최종 수정일: 2026-07-31
    """
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
