# Forge 프로젝트 진행 상황 요약

> 작성일: 2026-08-13
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

> **미구현**: 밀도 우선 검색(reflection 우선)

#### 1.3 L0 원본 이벤트 — ✅ 완료

| 파일 | 내용 | 상태 |
------|------|------|
| `adapters/outbound/memory/jsonl_l0_event_store.py` | append-only JSONL 이벤트 저장소, session manifest 관리 | ✅ |
| `application/memory/start_inner_loop_session.py` | `StartInnerLoopSessionService` | ✅ |
| `application/memory/record_inner_loop_event.py` | `RecordInnerLoopEventService` | ✅ |
| `ports/outbound/l0_event_store.py` | `L0EventStore` Protocol | ✅ |
| 테스트 | `test_jsonl_l0_event_store.py` | ✅ |

#### 1.4 L2 시맨틱 기억 — ⚠️ 최소 수직 슬라이스 완료

`domain/outer_loop`, `application/outer_loop`, `adapters/outbound/outer_loop`에
Pattern Candidate와 L2 knowledge의 JSON 저장소를 구현했다. Outer Loop는 complete·eligible
L1 Episode를 후보 증거로 누적하고, 최소 증거 수와 confidence 기준을 충족하면 L2로 승격한다.
반례는 active → weakened → retired 상태 전이를 유도하며, 후보·L2·watermark/checkpoint는
단일 JSON 파일에 원자적으로 기록한다.

> **후속 범위**: NetworkX/GraphML projection, L2→L3 Seed 생성의 반복 가능성 판정 고도화

#### 1.5 L3 절차 기억 — ✅ 완료

`SqliteProceduralRepository`가 L3 스킬·실행 이력·도구 종속 pending hint를 SQLite
정본에 저장한다. skill 변경마다 SQLite에서만 생성하는 versioned YAML 검토 projection과
비-Archive 스킬 JSON registry를 갱신하지만, 둘 다 실행·lifecycle·복구 입력으로 읽지 않는다.
active L2 knowledge는 충분한 근거와 반복된 동일 도구 순서를 통과할 때만 source L2 ID를 가진
Seed 스킬로 생성되고, 운영 표본이 최소 3건이며 성공률 0.90 이상과 CIB 0.95 이상을 만족하면
Active로 승격한다.

Inner Loop의 reflection 단계는 도구를 사용한 경우 `episode_id`를 source ID로 하여
`pending_hints`에 자동 기록한다. Outer Loop는 L2 변경 뒤 L3 Seed 생성을 호출한다.
`SkillExecutor`는 Active 스킬에 명시적으로 바인딩·영속화된 구조화 tool step만 기존
`PlanStepExecutor`로 순서대로 실행하며, 첫 실패에서 중단한다. Inner Loop는 planner의
`l3.execute(skill_id)` 선택을 가로채어, 이번 요청의 vetted memory context에 포함된 skill ID만
실행하고 평가 결과를 lifecycle 표본으로 기록한다. 자연어 Seed procedure나 reflection hint는
도구 호출로 자동 변환하지 않는다.

MemoryManager는 Active L3을 query relevance → success rate → recency → skill ID 순으로
결정적으로 고르고, 항목 수와 `cognition.l3_context_max_chars` 하드 문자 예산을 함께 적용한다.
예산을 넘는 procedure는 주입하지 않는다.

L2 근거의 support/counterexample episode에 저장된 tool-specific reflection은 L3 Seed 갱신 시
`SkillStepDraft`로 승격된다. draft에는 관찰된 도구명·source episode·hint만 들어가며 실행할
인자는 포함하지 않는다. `bind_executable_steps()`는 대응되는 draft가 있는 도구만 승인된
실행 step으로 저장할 수 있다.

`forge --list-l3-drafts --l3-skill-id <id>`는 실행 불가능한 draft를 JSON으로 조회하며,
`forge --approve-l3-draft <draft> --l3-skill-id <id> --step-id <step> --tool-arguments '<JSON>'`
는 검토자가 제공한 인자만 사용해 하나의 executable step을 승인한다.

승급은 `Seed → (승인된 step) → Validating → Active`다. `Validating`은 일반 Inner Loop
후보에 포함되지 않으며, `forge --begin-l3-validation <skill>`으로 명시 전이한 뒤
`forge --validate-l3-skill <skill>`의 검증 실행·평가 표본이 lifecycle 기준을 만족할 때만
`Active`가 된다. 따라서 Active 전용 실행기와 표본 축적 사이의 교착은 없다.
실행 API도 `execute_active()`와 `execute_validation()`으로 분리되어, 일반 실행과 승급 검증이
boolean 플래그로 섞이지 않는다.

`refresh_all()`은 전체 스킬의 lifecycle 지표만 재계산한다. L3 신규 Seed는 L4가 L2 절차
방향을 허용하고 L5 카테고리 역량의 confidence·success rate 기준을 통과하며, 실행당 생성
예산이 남아 있을 때만 생성된다. M16은 최근 성공률 급락 시 신규 Seed를 동결하고, checkpoint에
보존한 M17 전역 coherence 관측값으로 정체·급성장을 감지하면 Seed 한도를 1개로 throttle한다.
M17은 최근 L1의 평균 CIB와 L5 capability confidence의 실제 성공률 보정 오차를 설정 가중치로
결합해 계산한다. 최근 L1 평가의 pain index, retry ratio, tool-error ratio, budget-overrun ratio도
운영 부하로 평가한다. 한 항목 초과는 1개로 감속하고 둘 이상 초과는 동결한다. 보류되어도
L1/L2 증거와 L2 knowledge는 보존된다.
`Archived`는 명시적으로
보존 처리한 스킬을 뜻하며, 유휴 시간이나 낮은 성공률로 자동 전이·삭제되지 않는다. Archive된
스킬의 procedure, draft, 실행 이력은 SQLite에 그대로 유지된다. Outer Loop는 새 L1 배치가
부족해 조기 종료하더라도 이 refresh를 먼저 수행한다.

> **후속 범위**: draft 인자 제안 보조와 batch review, L4 K-Scenario와 L5 updater의 쓰기 경로

> **완료 게이트**: SQLite 정본, 반복 가능성, version/YAML 검토 projection, deterministic
> selection·문자/실행 예산, lifecycle recovery, 명시 Archive·registry 제외, L4/L5 방향 및
> M16/M17 Seed 예산을 회귀 테스트로 검증했다. 다음 구현 범위는 MCP·정책/HITL·감사다.

#### 1.6 L4 헌법 — ⚠️ 최소 읽기·승격 guard 완료

`YamlConstitutionRepository`가 `constitution/safety.yml`을 읽어 CIB threshold와
민감정보 패턴을 제공한다. Outer Loop는 L2 증거를 누적하기 전에 CIB 통과·threshold 및
민감정보 저장 금지를 검사한다. 차단된 L1은 L2 증거로 사용하지 않지만 checkpoint는 전진한다.

> **후속 범위**: K-Scenario/방향성 함수 C 평가, 도구별 사용자 승인 정책 연결, Meta Loop +
> HITL 기반 헌법 변경

#### 1.7 L5 정체성 — ⚠️ 최소 읽기 모델 완료

`YamlIdentityRepository`가 `identity.yml`의 현재 autonomy level과
`capabilities.yml`의 작업 카테고리별 역량·미지원 카테고리 기본값을 조회한다. YAML은 계속
읽기 전용이며, self_model CRUD, 칼리브레이션·윈도우 통계 및 Outer Loop updater는 미구현이다.

#### 1.8 Cognition 메모리 문맥 — ⚠️ L1/L2/L3 선택 주입 완료

`MemoryContextBuilder`가 L1 검색 결과와 active L2 knowledge를 관련성·개수 제한으로
선별한다. L4의 CIB/민감정보 검사를 통과한 항목만 주입하며 L5 capability는 planner의
자기 인식 문맥으로 함께 전달한다. Active L3 스킬은 query 관련성 기준으로 절차 요약과
허용 skill ID를 planner 문맥에 주입한다. native tool planner는 `l3.execute`로 한 개의
retrieved skill을 명시적으로 선택할 수 있고, Inner Loop가 그 선택을 검증·실행한다.

#### 1.9 메모리 매니저 — ⚠️ 최소 통합 파사드·라우터 완료

`MemoryManager`가 L1 검색, active L2 knowledge, L4 안전 필터, L5 identity/capability를
한 번의 읽기 요청으로 조합하고 Cognition의 선택 주입 경로가 이를 사용한다. 반성은
범용 지식이면 L2 일반화, 도구 종속이면 `L3_PROCEDURE_PENDING`, 내용이 없으면 L1 전용으로
분류한다. 도구 종속 반성은 L3 SQLite `pending_hints`에 영속화한다.

L1→L2 consolidation은 이미 Outer Loop가 소유하며, MemoryManager는 이를 중복 실행하지 않는다.

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

#### 2.2 인지 모듈 (Cognition) — ⚠️ Inner Loop v1 완료

`domain/cognition`과 `application/cognition`이 Inner Loop의 실행 문맥 구성,
계획/재계획, 실행 결과 해석, retry/replan/summarize 판단, 평가·반성 위임을
분리한다. `RunInnerLoopService`는 LangGraph 전이와 L0/L1 lifecycle을 유지한다.

> **후속 범위**: Active L3 스킬 검색·선택·실행, L4 K-Scenario 기반 계획 preflight,
> L5 권한 판단은 해당 계층 확장 시 Cognition에 연결한다.

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
| `bootstrap/container.py` | 대화/Inner/Outer Loop 및 L4/L5 읽기 저장소 builder, `_build_conversation_runtime` (tools enabled 시 bind_tools), `_build_planner` (deterministic / native_tool), `_build_tool_registry` | ✅ |
| `adapters/inbound/cli.py` | `run_message` (단일 대화), `run_inner_loop` (Inner Loop), REPL 모드, `--conversation-id`, `--system`, `--query`, `--inner-loop`, `--config` | ✅ |
| 테스트 | `test_container.py`, `test_cli.py` | ✅ |

### Phase 3: 아우터 루프 (Outer Loop) — ⚠️ 최소 수직 슬라이스 완료

`RunOuterLoopService`가 다음 범위를 제공한다:

```text
eligible L1 수집 → Pattern Candidate 증거 누적 → L1→L2 결정
→ L2 upsert/refine/weaken/retire → L2→L3 Seed 생성 → watermark/checkpoint
```

상태는 `semantic.outer_loop_state_path`의 단일 JSON 문서로 저장되며, 배치가 완료된 뒤에만
watermark가 전진한다. `build_outer_loop_service()`는 기존 L1 repository와 이 저장소를 조립한다.

> **후속 범위**: 스케줄/이벤트 trigger와 LangGraph orchestration, M17 기반 Meta Loop trigger

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
| `tests/forge/application/cognition/test_inner_loop_cognition.py` | Inner Loop 인지 판단(retry/replan/summarize) | ✅ |
| `tests/forge/application/memory/test_services.py` | L1 persist/search/reindex service | ✅ |
| `tests/forge/application/memory/test_finalize_episode.py` | L0 → L1 finalize | ✅ |
| `tests/forge/domain/memory/test_models.py` | Episode/Evaluation/Reflection 검증 | ✅ |

> **검증**: 2026-08-13 기준 전체 `pytest -q`는 759개 통과했다. 변경 범위 `ruff`도 통과했다.
> `mypy`는 프로젝트 코드 검사 전 가상환경 NumPy 스텁의 Python 버전 충돌로 중단된다.

---

## 4. 현재 미커밋 변경 사항 (Working Tree)

| 파일 | 변경 내용 |
------|----------|
| `config/agent.yml` | 대화 도구 한도(30), workspace mutation 및 verification 권한 활성화 |
| `src/forge/adapters/outbound/tools/builtin.py` | `workspace.apply_patch`와 수정된 `git.diff` 명령, mutation 권한 정책 |
| `src/forge/bootstrap/container.py` | 대화 tool binding 및 권한/한도 설정 전달 |
| `src/forge/runtime/conversation.py` | ToolNode 기반 다중 라운드 대화 실행 |
| `src/forge/domain/cognition/`, `src/forge/application/cognition/` | Inner Loop Cognition v1 경계와 판단 서비스 |
| `src/forge/application/inner_loop/run_inner_loop.py` | Cognition을 사용하고 L0/L1 lifecycle에 집중하도록 분리 |
| `tests/forge/**` | 대화 도구, mutation, git.diff, Cognition 재시도/재계획 회귀 테스트 |

---

## 5. 알려진 문제

현재 알려진 프로젝트 코드 문제는 없다. 단, `mypy`는 NumPy 스텁의 Python 버전 충돌을
먼저 해결해야 전체 타입 검사를 실행할 수 있다.

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
| `forge-langchain-conversation-tools-mcp.md` | ✅ 완료 | LangChain 대화 전환 + 대화 도구 호출. 이 계획의 MCP 범위는 후속 계획으로 분리됨 |
| `l3-procedural-memory-completion.md` | ✅ 완료 | SQLite 정본·YAML 검토 projection, 반복 도구 시퀀스 Seed gate, context/실행 예산, lifecycle 및 M16/M17 gate 검증 완료 |
| `mcp-policy-hitl-audit.md` | 🚧 구현 중 | L3 완료 게이트 통과 후 MCP disabled-by-default 설정 및 fail-closed server/client 계약부터 구현 시작 |
| `inner-loop-cognition-v1.md` | ✅ 완료 | Inner Loop 인지 책임 분리; L1~L5 문맥 주입은 제외 |
| `l0-event-foundation.md` | — | 바이너리 파일 (읽기 불가) |

---

## 7. 종합 진행도

| Phase | 진행도 | 상태 |
-------|--------|------|
| Phase 0: 인프라 | 100% | ✅ 완료 |
| Phase 1: 메모리 계층 | ~80% | L1/L0/L2/L3 안전 실행, L4/L5 읽기 모델, MemoryManager 완료 |
| Phase 2: 이너 루프 | ~90% | LLM/Tools/대화 Runtime/CLI 및 Inner Loop Cognition v1 완료 |
| Phase 3: 아우터 루프 | ~40% | L1→L2, L2→L3 Seed, L4/L5 방향 및 M16 동적 Seed 예산 gate 완료 |
| Phase 4: 메타 루프 | 0% | ❌ 미구현 |
| Phase 5: 한계 보완 | 0% | ❌ 미구현 |

### 다음 우선순위 (제안)

1. **MCP·정책/HITL·감사** — L3 완료 게이트가 검증됐으므로 MCP adapter의 권한 경계, 사용자 승인 시점, 감사 이벤트와 실패 처리를 설계·테스트 명세로 고정
2. **Outer Loop 확장** — 스케줄/이벤트 trigger와 M17 기반 Meta Loop trigger를 확장
3. **L4/L5 확장** — K-Scenario·도구별 승인 정책과 self_model CRUD·캘리브레이션·Outer Loop updater를 확장
