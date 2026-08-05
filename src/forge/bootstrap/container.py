"""구체 adapter와 application service를 조립하는 composition root."""

from pathlib import Path
from typing import Any

import yaml
from chromadb import PersistentClient

from forge.adapters.outbound.inner_loop import (
    PLAN_SCHEMA,
    DeterministicEvaluator,
    DeterministicPlanner,
    DeterministicReflector,
    LLMPlanner,
)
from forge.adapters.outbound.llm import (
    ChatModelFactory,
    CodexProviderAdapter,
    CodexSettings,
    OllamaProvider,
    OllamaSettings,
    PromptStructuredOutputStrategy,
    PromptToolCallingStrategy,
    UnifiedChatModelFactory,
)
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
from forge.ports.outbound import InnerLoopPlanner
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
        _build_planner(agent_config, registry),
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


def _build_provider(config: dict[str, Any]):
    """llm 설정에서 provider adapter를 생성한다.

    provider 이름으로 기능을 분기하지 않고, 설정에 따라 adapter를 선택한다.

    최종 수정일: 2026-08-04
    """
    llm = config.get("llm", {})
    backend = llm.get("backend", "ollama")
    selected = llm.get(backend, {})
    if backend == "ollama":
        return OllamaProvider(
            OllamaSettings(
                model=selected.get("model", "glm-5.2:cloud"),
                base_url=selected.get("base_url", "http://localhost:11434"),
                temperature=selected.get("temperature"),
                max_tokens=selected.get("max_tokens"),
            )
        )
    if backend == "openai":
        return CodexProviderAdapter(
            CodexSettings(
                model=selected.get("model", "gpt-5.6-luna"),
                temperature=selected.get("temperature"),
                max_tokens=selected.get("max_tokens"),
            )
        )
    raise ValueError(f"Unsupported LLM backend: {backend}")


def _build_chat_model_factory(config: dict[str, Any]) -> UnifiedChatModelFactory:
    """provider와 전략을 조합해 ``UnifiedChatModelFactory`` 를 생성한다.

    전략 선택은 config의 ``model.tool_calling_strategy`` 와
    ``model.structured_output_strategy`` 에 따른다.
    초기 기본값은 ``prompt`` 로 모든 모델에 적용 가능하다.

    최종 수정일: 2026-08-04
    """
    model_config = config.get("model", {})
    tool_strategy_name = model_config.get("tool_calling_strategy", "prompt")
    structured_strategy_name = model_config.get("structured_output_strategy", "prompt")

    if tool_strategy_name != "prompt":
        raise ValueError(
            f"Tool calling strategy '{tool_strategy_name}' is not yet supported. "
            "Use 'prompt' for the initial implementation."
        )
    if structured_strategy_name != "prompt":
        raise ValueError(
            f"Structured output strategy '{structured_strategy_name}' is not yet supported. "
            "Use 'prompt' for the initial implementation."
        )

    return UnifiedChatModelFactory(
        provider=_build_provider(config),
        tool_strategy=PromptToolCallingStrategy(),
        structured_strategy=PromptStructuredOutputStrategy(),
    )


def _build_planner(
    agent_config: dict[str, Any], registry: BuiltinToolRegistry
) -> InnerLoopPlanner:
    """config에 따라 deterministic 또는 LLM planner를 생성한다.

    기본값은 ``deterministic`` 이며, ``inner_loop.planner.type: llm`` 인 경우
    ``UnifiedChatModelFactory`` 로 structured model을 만들어 ``LLMPlanner`` 를 조립한다.

    최종 수정일: 2026-08-04
    """
    planner_config = agent_config.get("inner_loop", {}).get("planner", {})
    planner_type = planner_config.get("type", "deterministic")

    if planner_type == "deterministic":
        return DeterministicPlanner(
            tool_name=planner_config.get("tool_name", "workspace.list_files"),
            tool_arguments=planner_config.get("tool_arguments", {"path": "."}),
        )
    if planner_type == "llm":
        factory = _build_chat_model_factory(agent_config)
        model = factory.create_structured_model(PLAN_SCHEMA)
        return LLMPlanner(
            model,
            tool_schemas=registry.tool_schemas(),
            system_prompt=planner_config.get("system_prompt"),
        )
    raise ValueError(
        f"Unsupported planner type: {planner_type}. Use 'deterministic' or 'llm'."
    )
