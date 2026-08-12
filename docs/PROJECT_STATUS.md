# Forge 프로젝트 진행 상황 요약

> 작성일: 2026-08-05
> 기준: `src/forge`, `tests/forge`, `.omx/plans`, `config/`, `pyproject.toml`

---

## 1. 프로젝트 개요

**Forge**는 자기 진화형 AI 에이전트 프레임워크(Gnosis)로, 5계층 메모리 아키텍처
(L1–L5), Inner/Outer/Meta Loop, 헌법 기반 안전 시스템(CIB)을 설계 기반으로 한다.
현재 헥사고날 아키텍처(hexagonal architecture)로 구현되어 있으며, LangChain/LangGraph를
핵심 실행 엔진으로 채택했다.

### 아키텍처 경계

```text
CLI (--conversation-id, --system, --query, --inner-loop)
  -> application service (facade)
  -> forge.runtime (LangGraph: MessagesState + InMemorySaver)
  -> LangChain BaseChatModel
  -> provider adapter (LiteLLM / Codex SDK / Ollama)
```

- **domain**: provider 무관 불변 값 객체 (dataclass, StrEnum)
- **ports**: Protocol 기반 outbound/inbound 경계
- **application**: facade service + Inner Loop orchestration
- **adapters**: CLI, LLM provider, memory store, tool registry
- **bootstrap**: composition root (의존성 주입)
- **runtime**: LangGraph 대화 그래프 조립

---

## 2. Phase별 구현 상태

### Phase 0: 프로젝트 인프라 — ✅ 완료

| 항목 | 상태 | 비고 |
------|------|------|
| 디렉터리 구조 | ✅ | `config/`, `constitution/`, `identity/`, `src/forge/`, `tests/forge/`, `scripts/` |
| `pyproject.toml` | ✅ | langchain, langgraph, langchain-litellm, litellm, chromadb, openai-codex, ollama 등 |
| 환경 변수 (`.env`) | ✅ | `.env.example` 존재 |
| `config/agent.yml` | ✅ | LLM backend(ollama/openai), Inner Loop, conversation tools, model strategy 설정 |
| `config/memory.yml` | ✅ | L1 SQLite/Chroma 경로, L0 JSONL 경로 |
| 헌법 YAML | ✅ | `constitution/base.yml`, `safety.yml`, `interaction_policy.yml`, `tool_policy.yml` |
| L5 YAML | ✅ | `identity/identity.yml`, `self_model.yml`, `capabilities.yml` |
| `identity.sqlite3` | ✅ | self_model 테이블 포함 |

### Phase 1: 메모리 계층 — 부분 완료

#### 1.1 스키마 & 코어 — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `domain/memory/models.py` | `Episode`, `Evaluation`, `Reflection`, `ExecutionResult`, `EpisodeSearchFilters` 등 불변 dataclass + 검증 로직 | ✅ |
| `domain/memory/l0_events.py` | `L0Event`, `L0SessionManifest`, `L0EventType` enum, payload 민감정보 필터링 | ✅ |
| `domain/memory/errors.py` | `MemoryValidationError` typed exception | ✅ |
| `domain/inner_loop/models.py` | `ToolDefinition`, `ToolInvocation`, `ToolResult`, `PlanStep`, `InnerLoopPlan`, `ToolExecution` | ✅ |
| `domain/llm/models.py` | `ChatMessage`, `ModelResponse`, `ToolCallData` | ✅ |
| `domain/conversation/models.py` | `SendMessageCommand`, `AssistantReply`, `ToolCall` | ✅ |

#### 1.2 L1 일화 기억 (Episodic) — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `adapters/outbound/memory/sqlite_episode_store.py` | SQLite 정본 CRUD | ✅ |
| `adapters/outbound/memory/chroma_episode_index.py` | Chroma 벡터 인덱스 (embedding, 검색) | ✅ |
| `adapters/outbound/memory/sqlite_chroma_episode_repository.py` | SQLite + Chroma 통합 repository, `PersistEpisodeResult`, `ReindexResult` | ✅ |
| `adapters/outbound/memory/settings.py` | `MemorySettings` (경로, collection, projection version) | ✅ |
| `adapters/outbound/memory/errors.py` | `EpisodeIntegrityError`, `RetryableMemoryOperationError`, `MemoryInfrastructureError`, `EpisodeIndexUnavailableError` | ✅ |
| `application/memory/persist_episode.py` | `PersistEpisodeService` | ✅ |
| `application/memory/search_episodes.py` | `SearchEpisodesService` | ✅ |
| `application/memory/reindex_episodes.py` | `ReindexEpisodesService` | ✅ |
| `application/memory/finalize_episode.py` | `FinalizeEpisodeService` (L0 → L1 확정) | ✅ |
| `ports/outbound/episode_repository.py` | `EpisodeRepository` Protocol | ✅ |
| 테스트 | `test_sqlite_chroma_episode_repository.py`, `test_models.py`, `test_finalize_episode.py`, `test_services.py` | ✅ |

> **미구현**: L1 → L2 자동 추출(consolidation), 밀도 우선 검색(reflection 우선), 선택적 주입 파이프라인

#### 1.3 L0 원본 이벤트 — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `adapters/outbound/memory/jsonl_l0_event_store.py` | append-only JSONL 이벤트 저장소, session manifest 관리 | ✅ |
| `application/memory/start_inner_loop_session.py` | `StartInnerLoopSessionService` | ✅ |
| `application/memory/record_inner_loop_event.py` | `RecordInnerLoopEventService` | ✅ |
| `ports/outbound/l0_event_store.py` | `L0EventStore` Protocol | ✅ |
| 테스트 | `test_jsonl_l0_event_store.py` | ✅ |

#### 1.4 L2 시맨틱 기억 — ❌ 미구현

README 설계에는 NetworkX 그래프, JSON 스토어, 엔티티 추출, 중복 병합, 추론이 포함되나,
현재 `src/forge`에 L2 관련 구현이 없다.

#### 1.5 L4 헌법 — ⚠️ YAML만 존재

`constitution/*.yml` 파일은 존재하나, `src/forge`에 헌법 loader, validator, CIB guard
구현이 없다. `Evaluation` 도메인 모델에 `cib_score`, `cib_evaluation_status` 필드는
정의되어 있으나, 실제 CIB 검증 로직(방향성 함수 C, K-Scenario 대입)은 구현되지 않았다.

#### 1.6 L5 정체성 — ⚠️ YAML + SQLite만 존재

`identity/*.yml`과 `identity.sqlite3`은 존재하나, `src/forge`에 self_model CRUD,
칼리브레이션 에러 계산, 윈도우 통계, updater 구현이 없다.

#### 1.7 메모리 매니저 — ❌ 미구현

`MemoryManager` 통합 라우터, L1→L2 consolidation, 이중 저장 전략 라우팅은
구현되지 않았다.

### Phase 2: 이너 루프 (Inner Loop) — ✅ 기본 구조 완료

#### 2.1 LLM 클라이언트 / Provider — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `adapters/outbound/llm/litellm_codex_gateway.py` | `ChatModelFactory`, `ChatModelSettings`, `CodexProvider` (LiteLLM custom provider), `ChatLiteLLM` 생성 | ✅ |
| `adapters/outbound/llm/providers/__init__.py` | `ProviderAdapter` Protocol, `LiteLLMChatModel` (LangChain → 내부 `ModelResponse` 변환) | ✅ |
| `adapters/outbound/llm/providers/ollama.py` | `OllamaProvider`, `OllamaSettings` | ✅ |
| `adapters/outbound/llm/providers/codex.py` | `CodexLLMProvider`, `CodexProvider` (adapter), `CodexSettings` | ✅ |
| `adapters/outbound/llm/conversation_bridge.py` | `DomainChatModelBridge` (domain `ChatModel` → LangChain `AIMessage` 변환) | ✅ |
| `adapters/outbound/llm/strategies/prompt_structured.py` | `PromptStructuredOutputStrategy` (JSON Schema 주입 + 파싱) | ✅ |
| `adapters/outbound/llm/strategies/__init__.py` | `ToolCallingError` | ✅ |
| `ports/outbound/model_gateway.py` | `ChatModel`, `StructuredChatModel`, `ConversationRuntime` Protocol | ✅ |
| 테스트 | `test_litellm_codex_gateway.py`, `test_conversation_bridge.py`, `test_prompt_structured.py` | ✅ |

**지원 백엔드**:
- `ollama`: `ChatLiteLLM(model="ollama_chat/...")` via Ollama API
- `openai`: `ChatLiteLLM(model="forge_codex/forge")` via Codex SDK custom provider

#### 2.2 인지 모듈 (Cognition) — ❌ 미구현

README 설계의 `context_builder`, `planner`, `reasoner`, `decision`,
`reflection_loop` 중 `src/forge`에 별도 인지 모듈이 없다. Inner Loop의
plan/evaluate/reflect 노드가 이 역할을 부분적으로 대체한다.

#### 2.3 도구 시스템 (Tools) — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `adapters/outbound/tools/builtin.py` | `BuiltinToolRegistry` (7개 도구: list_files, read_file, search_text, git.status, git.diff, apply_patch, project.verify), `StaticToolAuthorizationPolicy`, 경로 검증, 출력 제한 | ✅ |
| `adapters/outbound/tools/_langchain.py` | `invoke_registered_tool` (검증→권한→실행→JSON payload) | ✅ |
| `adapters/outbound/tools/workspace.py` | LangChain `@tool` 데코레이터: list_files, read_file, search_text, apply_patch | ✅ |
| `adapters/outbound/tools/git.py` | LangChain `@tool`: git.status, git.diff | ✅ |
| `adapters/outbound/tools/project.py` | LangChain `@tool`: project.verify (pytest/ruff/mypy) | ✅ |
| `adapters/outbound/tools/langchain_tools.py` | `build_langchain_tools` (7개 도구를 안정적 순서로 조립) | ✅ |
| `adapters/outbound/tools/executor.py` | `RegistryPlanStepExecutor` (Inner Loop step → LangChain tool 실행, audit redaction) | ✅ |
| `ports/outbound/inner_loop.py` | `ToolRegistry`, `ToolAuthorizationPolicy` Protocol | ✅ |
| 테스트 | `test_builtin.py` (apply_patch 포함 12개 테스트) | ✅ |

**도구 권한 계층**:
- `READ_ONLY`: 항상 허용 (list_files, read_file, search_text, git.status, git.diff)
- `WORKSPACE_MUTATION`: 설정 시 허용 (apply_patch)
- `VERIFICATION`: 설정 시 허용 (project.verify)

#### 2.4 이너 루프 파이프라인 — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `application/inner_loop/run_inner_loop.py` | `RunInnerLoopService` (LangGraph StateGraph: start → plan → execute_attempt → summarize → evaluate → reflect → finalize), retry conditional edge, feedback-aware replan, step dependency 검증 | ✅ |
| `adapters/outbound/inner_loop/deterministic.py` | `DeterministicPlanner`, `DeterministicExecutor`, `DeterministicEvaluator`, `DeterministicReflector` | ✅ |
| `adapters/outbound/inner_loop/llm_planner.py` | `NativeToolCallPlanner` (LangChain model.bind_tools → tool call plan 생성), `PlanGenerationError` | ✅ |
| `application/conversation/tool_feedback.py` | `serialize_tool_execution`, `protocol_failure_feedback` (안전한 모델 피드백 payload, 민감정보/경로/traceback redaction) | ✅ |
| `application/conversation/receive_initial_input.py` | `ReceiveMessageService` (facade: 입력 검증 → runtime.invoke) | ✅ |
| 테스트 | `test_run_inner_loop.py`, `test_hexagonal_initial_input.py`, `test_tool_feedback.py` | ✅ |

**Inner Loop 그래프 구조**:
```text
START → start_session → plan → execute_attempt
  ↕ (retry / replan conditional edges)
  → summarize_execution → evaluate → reflect → finalize → END
```

**Planner 종류**:
- `deterministic`: 고정 단일 step (테스트/기본)
- `native_tool`: LangChain model.bind_tools → tool call plan

### Phase 2.5: 대화 Runtime (LangChain/LangGraph 전환) — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `runtime/conversation.py` | `LangGraphConversationRuntime` (MessagesState + InMemorySaver, 1노드 model + ToolNode, tool round limit, protocol failure handling, system instruction per-invocation) | ✅ |
| `runtime/context.py` | `ConversationContext` (system_instruction, conversation_id — checkpoint에 저장되지 않는 호출 전용 값) | ✅ |
| `runtime/__init__.py` | `LangGraphConversationRuntime` export | ✅ |
| 테스트 | `test_conversation_runtime.py` | ✅ |

**대화 Runtime 기능**:
- `conversation_id` → LangGraph `thread_id` (프로세스 수명 대화 문맥)
- `system_instruction` → invocation context (checkpoint에 미저장)
- tool calling: `bind_tools` → `ToolNode` 실행 → model 재호출 루프
- `max_tool_rounds` (기본 30), `max_protocol_failures` (기본 2)
- protocol failure 시 안전한 피드백 메시지 생성

### Phase 2.6: Bootstrap & CLI — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `bootstrap/container.py` | `build_receive_message_service`, `build_inner_loop_service`, `build_memory_services`, `build_l0_event_store`, `_build_conversation_runtime` (tools enabled 시 bind_tools), `_build_planner` (deterministic / native_tool), `_build_tool_registry` | ✅ |
| `adapters/inbound/cli.py` | `run_message` (단일 대화), `run_inner_loop` (Inner Loop), REPL 모드, `--conversation-id`, `--system`, `--query`, `--inner-loop`, `--config` | ✅ |
| 테스트 | `test_container.py`, `test_cli.py` | ✅ |

### Phase 3: 아우터 루프 (Outer Loop) — ❌ 미구현

README 설계의 7단계 프로세스, M16 그로스 레이트 레귤레이터, M17 코히어런스 인덱스,
어댑티브 N은 구현되지 않았다.

### Phase 4: 메타 루프 (Meta Loop) — ❌ 미구현

헌법 개정, 아키텍처 자가 수정, HITL 게이트, 수학적 가정 위반 탐지는 구현되지 않았다.

### Phase 5: 한계 보완 및 고도화 — ❌ 미구현

헌법 시나리오 자동 생성, 어댑티브 N 정제, 수학적 가정 완화는 구현되지 않았다.

---

## 3. 테스트 현황

| 테스트 파일 | 대상 | 상태 |
-------------|------|------|
| `tests/forge/bootstrap/test_container.py` | composition root, tool round budget, agent config | ✅ |
| `tests/forge/runtime/test_conversation_runtime.py` | 대화 runtime, thread 격리, system instruction | ✅ |
| `tests/forge/adapters/inbound/test_cli.py` | CLI 단일 호출, REPL, inner-loop | ✅ |
| `tests/forge/adapters/outbound/tools/test_builtin.py` | 도구 registry, apply_patch, 권한 정책 | ✅ |
| `tests/forge/adapters/outbound/llm/test_litellm_codex_gateway.py` | LLM factory, provider 등록 | ✅ |
| `tests/forge/adapters/outbound/llm/test_conversation_bridge.py` | domain → LangChain 메시지 변환 | ✅ |
| `tests/forge/adapters/outbound/llm/strategies/test_prompt_structured.py` | JSON Schema 주입 + 파싱 | ✅ |
| `tests/forge/adapters/outbound/memory/test_sqlite_chroma_episode_repository.py` | L1 repository CRUD + 검색 | ✅ |
| `tests/forge/adapters/outbound/memory/test_jsonl_l0_event_store.py` | L0 JSONL 이벤트 저장 | ✅ |
| `tests/forge/application/conversation/test_tool_feedback.py` | 안전 피드백 payload, redaction | ✅ |
| `tests/forge/application/conversation/test_hexagonal_initial_input.py` | facade hexagonal 경계 | ✅ |
| `tests/forge/application/inner_loop/test_run_inner_loop.py` | Inner Loop 그래프, retry, replan | ✅ |
| `tests/forge/application/memory/test_services.py` | L1 persist/search/reindex service | ✅ |
| `tests/forge/application/memory/test_finalize_episode.py` | L0 → L1 finalize | ✅ |
| `tests/forge/domain/memory/test_models.py` | Episode/Evaluation/Reflection 검증 | ✅ |

> **주의**: `pytest`, `ruff`, `mypy` 검증 템플릿이 현재 `tool.approval_required` 상태이므로
> 실제 통과 여부는 미검증.

---

## 4. 현재 미커밋 변경 사항 (Working Tree)

| 파일 | 변경 내용 |
------|----------|
| `config/agent.yml` | `conversation.tools` 블록 추가 (enabled: true, max_tool_rounds: 30, allow_workspace_mutation: true) |
| `src/forge/adapters/outbound/tools/builtin.py` | `workspace.apply_patch` 도구 추가 (unified diff 파싱 및 적용) |
| `src/forge/bootstrap/container.py` | `_build_conversation_runtime`에 tool binding 지원 추가 |
| `src/forge/runtime/conversation.py` | tool calling 루프 추가 (ToolNode, routing, protocol failure, round limit) |
| `tests/forge/adapters/outbound/tools/test_builtin.py` | apply_patch 테스트 7개 추가 |
| `tests/forge/bootstrap/test_container.py` | tool round budget 및 agent config assertion 테스트 추가 |

---

## 5. 알려진 문제

### 5.1 `git.diff` 도구 버그

`builtin.py`의 `_git_diff` 메서드가 `--no-submodule` 플래그를 `git diff`에 전달한다.
이 플래그는 존재하지 않는 git 옵션이며, `git diff` 실행 시 `error: invalid option:
--no-submodule` 오류를 발생시킨다.

**수정 필요**: `"--no-submodule"` 인자를 command vector에서 제거.

### 5.2 검증 템플릿 접근 제한

`project.verify` (pytest/ruff/mypy) 템플릿이 `tool.approval_required` 상태로,
현재 자동화 검증을 실행할 수 없다.

---

## 6. 구현 계획 문서 (.omx/plans)

| 계획 문서 | 상태 | 요약 |
-----------|------|------|
| `llm-router-codex-terra.md` | ✅ 완료 | Codex SDK + LiteLLM 라우팅, GPT-5.6 Terra |
| `hexagonal-l1-memory-implementation.md` | ✅ 완료 | L1 SQLite + Chroma 헥사고날 구현 |
| `l1-memory-error-taxonomy-and-retry-policy.md` | ✅ 완료 | L1 오류 분류 및 재시도 계약 |
| `inner-loop-tool-registry-and-execution.md` | ✅ 완료 | Inner Loop 도구 registry 및 실행 |
| `inner-loop-l0-event-foundation.md` | ✅ 완료 | L0 이벤트 + L1 Episode 수직 슬라이스 |
| `inner-loop-runtime-integration.md` | ✅ 완료 | Inner Loop runtime 통합 |
| `forge-langchain-conversation-tools-mcp.md` | ⚠️ 진행 중 | LangChain 대화 전환 + tool calling (MCP는 미구현) |
| `l0-event-foundation.md` | — | 바이너리 파일 (읽기 불가) |

---

## 7. 종합 진행도

| Phase | 진행도 | 상태 |
-------|--------|------|
| Phase 0: 인프라 | 100% | ✅ 완료 |
| Phase 1: 메모리 계층 | ~40% | L1/L0 완료, L2/L4/L5/Manager 미구현 |
| Phase 2: 이너 루프 | ~85% | LLM/Tools/Inner Loop/대화 Runtime/CLI 완료, 인지 모듈 미구현 |
| Phase 3: 아우터 루프 | 0% | ❌ 미구현 |
| Phase 4: 메타 루프 | 0% | ❌ 미구현 |
| Phase 5: 한계 보완 | 0% | ❌ 미구현 |

### 다음 우선순위 (제안)

1. **`git.diff` 버그 수정** — `--no-submodule` 플래그 제거
2. **미커밋 변경 사항 검증** — pytest/ruff/mypy 실행 후 커밋
3. **L4 헌법 구현** — CIB guard, K-Scenario 검증, 방향성 함수 C
4. **L5 정체성 구현** — self_model CRUD, 칼리브레이션 에러, 윈도우 통계
5. **L2 시맨틱 기억 구현** — NetworkX 그래프, 엔티티 추출, 중복 병합
6. **메모리 매니저 구현** — L1~L5 통합 라우팅, 이중 저장 전략
7. **인지 모듈 구현** — context_builder, 선택적 주입, 밀도 우선 검색
8. **Phase 3: 아우터 루프** — 7단계 프로세스, M16/M17
