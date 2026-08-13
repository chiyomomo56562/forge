"""구체 adapter와 application service를 조립하는 composition root."""

from pathlib import Path
from typing import Any

import yaml
from chromadb import PersistentClient

from forge.adapters.outbound.constitution import YamlConstitutionRepository
from forge.adapters.outbound.identity import YamlIdentityRepository
from forge.adapters.outbound.inner_loop import (
    DeterministicEvaluator,
    DeterministicPlanner,
    DeterministicReflector,
    NativeToolCallPlanner,
)
from forge.adapters.outbound.llm import ChatModelFactory
from forge.adapters.outbound.memory import (
    ChromaEpisodeIndex,
    JsonlL0EventStore,
    MemorySettings,
    SqliteChromaEpisodeRepository,
    SqliteEpisodeStore,
)
from forge.adapters.outbound.outer_loop import JsonOuterLoopStore
from forge.adapters.outbound.procedural import (
    SqliteProceduralRepository,
    build_procedural_selection_tool,
)
from forge.adapters.outbound.tools import (
    BuiltinToolRegistry,
    RegistryPlanStepExecutor,
    StaticToolAuthorizationPolicy,
    build_langchain_tools,
)
from forge.application.cognition import MemoryContextBuilder
from forge.application.conversation import ReceiveMessageService
from forge.application.inner_loop import RunInnerLoopService
from forge.application.memory import (
    FinalizeEpisodeService,
    MemoryManager,
    PersistEpisodeService,
    RecordInnerLoopEventService,
    ReindexEpisodesService,
    SearchEpisodesService,
    StartInnerLoopSessionService,
)
from forge.application.outer_loop import OuterLoopPolicy, RunOuterLoopService
from forge.application.procedural import (
    ProceduralMemoryService,
    SkillExecutor,
    SkillLifecyclePolicy,
    SkillValidationService,
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

    최종 수정일: 2026-08-05
    """
    config = _load_yaml_config(config_path)
    runtime = _build_conversation_runtime(config, config_path=config_path, chat_model=chat_model)
    return ReceiveMessageService(runtime)


def build_l0_event_store(root_path: str = "data/memory/working/sessions") -> JsonlL0EventStore:
    """대화 CLI와 분리된 L0 JSONL 저장소를 조립한다.

    Args:
        root_path: session별 JSONL 및 manifest를 저장할 기준 경로.

    Returns:
        Inner Loop 서비스에 주입할 L0 저장소.

    최종 수정일: 2026-07-31
    """
    return JsonlL0EventStore(root_path)


def build_constitution_repository(
    config_path: str = "config/memory.yml",
) -> YamlConstitutionRepository:
    """읽기 전용 L4 헌법 저장소를 조립한다."""
    config = _load_yaml_config(config_path)
    return YamlConstitutionRepository(config["constitution"]["dir"])


def build_identity_repository(config_path: str = "config/memory.yml") -> YamlIdentityRepository:
    """읽기 전용 L5 정체성·역량 저장소를 조립한다."""
    config = _load_yaml_config(config_path)
    return YamlIdentityRepository(config["identity"]["dir"])


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


def build_memory_manager(config_path: str = "config/memory.yml") -> MemoryManager:
    """현재 구현된 L1/L2/L4/L5를 통합 조회하는 MemoryManager를 조립한다."""
    config = _load_yaml_config(config_path)
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
    return _build_memory_manager(config, repository)


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

    최종 수정일: 2026-08-05
    """
    config = _load_yaml_config(config_path)
    agent_config = _load_yaml_config(agent_config_path)
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
    registry = _build_tool_registry(tool_config)
    tools = build_langchain_tools(
        registry,
        StaticToolAuthorizationPolicy(
            allow_verification=bool(tool_config.get("allow_verification", True))
        ),
    )
    memory_manager = _build_memory_manager(config, repository)
    procedural_repository = _build_procedural_repository(config)
    procedural_lifecycle = ProceduralMemoryService(
        procedural_repository,
        _skill_lifecycle_policy(config),
    )
    plan_executor = RegistryPlanStepExecutor(tools)
    return RunInnerLoopService(
        StartInnerLoopSessionService(store),
        RecordInnerLoopEventService(store),
        FinalizeEpisodeService(store, repository),
        _build_planner(
            agent_config,
            (*tools, build_procedural_selection_tool()),
            agent_config_path,
        ),
        plan_executor,
        DeterministicEvaluator(),
        DeterministicReflector(),
        max_retries=max_retries,
        max_feedback_cycles=_non_negative_int(
            agent_config.get("inner_loop", {}).get("max_feedback_cycles", 0),
            setting="inner_loop.max_feedback_cycles",
        ),
        memory_context_builder=MemoryContextBuilder(memory_manager),
        memory_manager=memory_manager,
        skill_executor=SkillExecutor(procedural_repository, plan_executor, procedural_lifecycle),
    )


def build_outer_loop_service(config_path: str = "config/memory.yml") -> RunOuterLoopService:
    """L1 SQLite/Chroma repository와 checkpointed L1→L2 서비스를 조립한다.

    Args:
        config_path: episodic, semantic, consolidation 설정을 담은 YAML 파일.

    Returns:
        명시적으로 실행할 Outer Loop application service.
    """
    config = _load_yaml_config(config_path)
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
    consolidation = config.get("consolidation", {})
    return RunOuterLoopService(
        repository,
        JsonOuterLoopStore(Path(config["semantic"]["outer_loop_state_path"])),
        OuterLoopPolicy(
            batch_size=_positive_int(consolidation.get("batch_size", 20), setting="batch_size"),
            min_episodes_for_generalization=_positive_int(
                consolidation.get("min_episodes_for_generalization", 3),
                setting="min_episodes_for_generalization",
            ),
            promotion_confidence=float(consolidation.get("promotion_confidence", 0.8)),
            retire_confidence=float(consolidation.get("retire_confidence", 0.4)),
        ),
        build_constitution_repository(config_path),
        ProceduralMemoryService(
            _build_procedural_repository(config),
            _skill_lifecycle_policy(config),
        ),
    )


def build_procedural_memory_service(
    config_path: str = "config/memory.yml",
) -> ProceduralMemoryService:
    """Build the L3 review and lifecycle application service."""
    config = _load_yaml_config(config_path)
    return ProceduralMemoryService(
        _build_procedural_repository(config),
        _skill_lifecycle_policy(config),
    )


def build_skill_validation_service(
    config_path: str = "config/memory.yml",
    *,
    agent_config_path: str = "config/agent.yml",
) -> SkillValidationService:
    """Build the explicit L3 validation executor using the normal tool policy boundary."""
    config = _load_yaml_config(config_path)
    agent_config = _load_yaml_config(agent_config_path)
    tool_config = agent_config.get("tools", {})
    registry = _build_tool_registry(tool_config)
    tools = build_langchain_tools(
        registry,
        StaticToolAuthorizationPolicy(
            allow_verification=bool(tool_config.get("allow_verification", True))
        ),
    )
    repository = _build_procedural_repository(config)
    lifecycle = ProceduralMemoryService(repository, _skill_lifecycle_policy(config))
    return SkillValidationService(
        SkillExecutor(repository, RegistryPlanStepExecutor(tools), lifecycle),
        DeterministicEvaluator(),
    )


def _load_yaml_config(config_path: str) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as config_file:
        return yaml.safe_load(config_file) or {}


def _build_memory_manager(
    config: dict[str, Any], repository: SqliteChromaEpisodeRepository
) -> MemoryManager:
    return MemoryManager(
        repository,
        JsonOuterLoopStore(Path(config["semantic"]["outer_loop_state_path"])),
        YamlConstitutionRepository(config["constitution"]["dir"]),
        YamlIdentityRepository(config["identity"]["dir"]),
        top_k=_positive_int(
            config.get("cognition", {}).get("memory_context_top_k", 3),
            setting="cognition.memory_context_top_k",
        ),
        procedural=_build_procedural_repository(config),
    )


def _skill_lifecycle_policy(config: dict[str, Any]) -> SkillLifecyclePolicy:
    lifecycle = config["procedural"]["lifecycle"]
    return SkillLifecyclePolicy(
        active_threshold=float(lifecycle["active_threshold"]),
        degrading_threshold=float(lifecycle["degrading_threshold"]),
        recovery_threshold=float(lifecycle["recovery_threshold"]),
    )


def _build_procedural_repository(config: dict[str, Any]) -> SqliteProceduralRepository:
    procedural = config["procedural"]
    return SqliteProceduralRepository(
        procedural["db_path"],
        skills_dir=procedural.get("skills_dir"),
        registry_path=procedural.get("registry_path"),
    )


def _build_tool_registry(tool_config: dict[str, Any]) -> BuiltinToolRegistry:
    return BuiltinToolRegistry(
        tool_config.get("workspace_root", "."),
        max_read_bytes=tool_config.get("max_read_bytes", 32_768),
        max_search_results=tool_config.get("max_search_results", 100),
        max_output_bytes=tool_config.get("max_output_bytes", 32_768),
        timeout_seconds=tool_config.get("timeout_seconds", 30),
    )


def _build_conversation_runtime(
    config: dict[str, Any], *, config_path: str, chat_model: Any | None = None
) -> LangGraphConversationRuntime:
    conversation_config = config.get("conversation", {})
    tools_config = conversation_config.get("tools", {})
    if not isinstance(tools_config, dict):
        raise ValueError("conversation.tools must be a mapping")
    if not bool(tools_config.get("enabled", False)):
        model = chat_model or ChatModelFactory.from_config(config_path).create()
        return LangGraphConversationRuntime(model)

    registry_config = dict(config.get("tools", {}))
    registry = _build_tool_registry(registry_config)
    authorization = StaticToolAuthorizationPolicy(
        allow_workspace_mutation=bool(tools_config.get("allow_workspace_mutation", False)),
        allow_verification=bool(tools_config.get("allow_verification", False)),
    )
    tools = build_langchain_tools(registry, authorization)
    # Conversation tool calling is LangChain-native: bind the actual BaseTool
    # instances to the provider model and let ToolNode execute them.
    model = (chat_model or ChatModelFactory.from_config(config_path).create()).bind_tools(tools)
    return LangGraphConversationRuntime(
        model,
        tools=tools,
        max_tool_rounds=_positive_int(
            tools_config.get("max_tool_rounds", 30),
            setting="conversation.tools.max_tool_rounds",
        ),
        max_protocol_failures=_non_negative_int(
            tools_config.get("max_protocol_failures", 2),
            setting="conversation.tools.max_protocol_failures",
        ),
        max_tool_feedback_bytes=_positive_int(
            tools_config.get("max_tool_feedback_bytes", 4_096),
            setting="conversation.tools.max_tool_feedback_bytes",
        ),
        workspace_root=registry_config.get("workspace_root", "."),
    )


def _build_planner(
    agent_config: dict[str, Any], tools: tuple[Any, ...] | list[Any], config_path: str
) -> InnerLoopPlanner:
    """config에 따라 deterministic 또는 LLM planner를 생성한다.

    ``native_tool`` planner는 conversation and ToolNode와 동일한 decorated tools를 bind한다.

    최종 수정일: 2026-08-04
    """
    planner_config = agent_config.get("inner_loop", {}).get("planner", {})
    planner_type = planner_config.get("type", "deterministic")

    if planner_type == "deterministic":
        return DeterministicPlanner(
            tool_name=planner_config.get("tool_name", "workspace.list_files"),
            tool_arguments=planner_config.get("tool_arguments", {"path": "."}),
        )
    if planner_type in {"llm", "native_tool"}:
        model = ChatModelFactory.from_config(config_path).create()
        return NativeToolCallPlanner(
            model,
            tools=tools,
            system_prompt=planner_config.get("system_prompt"),
        )
    raise ValueError(
        f"Unsupported planner type: {planner_type}. Use 'deterministic' or 'native_tool'."
    )


def _positive_int(value: object, *, setting: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{setting} must be a positive integer")
    return value


def _non_negative_int(value: object, *, setting: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{setting} must be a non-negative integer")
    return value
