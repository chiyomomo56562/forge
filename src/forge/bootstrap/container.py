"""구체 adapter와 application service를 조립하는 composition root."""

from pathlib import Path
from typing import Any

import yaml
from chromadb import PersistentClient

from forge.adapters.outbound.inner_loop import (
    DeterministicEvaluator,
    DeterministicPlanner,
    DeterministicReflector,
)
from forge.adapters.outbound.llm import ChatModelFactory
from forge.adapters.outbound.memory import (
    ChromaEpisodeIndex,
    JsonlL0EventStore,
    MemorySettings,
    SqliteChromaEpisodeRepository,
    SqliteEpisodeStore,
)
from forge.adapters.outbound.tools import (
    BuiltinToolRegistry,
    RegistryPlanStepExecutor,
    StaticToolAuthorizationPolicy,
)
from forge.application.conversation import ReceiveMessageService
from forge.application.inner_loop import RunInnerLoopService
from forge.application.memory import (
    FinalizeEpisodeService,
    PersistEpisodeService,
    RecordInnerLoopEventService,
    ReindexEpisodesService,
    SearchEpisodesService,
    StartInnerLoopSessionService,
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


def build_inner_loop_service(
    config_path: str = "config/memory.yml",
    *,
    agent_config_path: str = "config/agent.yml",
    max_retries: int = 3,
) -> RunInnerLoopService:
    """기본 deterministic producer와 L0/L1 저장소를 연결한 Inner Loop를 조립한다.

    Args:
        config_path: L1 SQLite/Chroma 및 L0 경로를 담은 YAML 설정 파일.
        agent_config_path: workspace 도구의 한도와 권한을 읽을 agent 설정 파일.
        max_retries: step attempt에 허용할 최대 재시도 횟수.

    Returns:
        명시적으로 호출할 수 있는 Inner Loop application service.

    최종 수정일: 2026-07-31
    """
    with open(config_path, encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    with open(agent_config_path, encoding="utf-8") as config_file:
        agent_config = yaml.safe_load(config_file) or {}
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
    store = build_l0_event_store(config["working"]["l0"]["root_path"])
    tool_config = agent_config.get("tools", {})
    registry = BuiltinToolRegistry(
        tool_config.get("workspace_root", "."),
        max_read_bytes=tool_config.get("max_read_bytes", 32_768),
        max_search_results=tool_config.get("max_search_results", 100),
        max_output_bytes=tool_config.get("max_output_bytes", 32_768),
        timeout_seconds=tool_config.get("timeout_seconds", 30),
    )
    return RunInnerLoopService(
        StartInnerLoopSessionService(store),
        RecordInnerLoopEventService(store),
        FinalizeEpisodeService(store, repository),
        DeterministicPlanner(tool_name="workspace.list_files", tool_arguments={"path": "."}),
        RegistryPlanStepExecutor(
            registry,
            StaticToolAuthorizationPolicy(
                allow_verification=bool(tool_config.get("allow_verification", True))
            ),
        ),
        DeterministicEvaluator(),
        DeterministicReflector(),
        max_retries=max_retries,
    )
