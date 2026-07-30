# Forge LangChain 기반 대화 전환 계획

## 목적과 이번 범위

Forge의 단발성 `InitialInput -> ModelGateway -> ModelReply` 경로를 LangChain과
LangGraph를 핵심 실행 엔진으로 쓰는 `forge.runtime`으로 전환한다. 이로써
`conversation_id`별 다회 대화 문맥은 runtime의 thread-level checkpoint가 관리하며,
Forge가 자체 메시지 저장소나 `ConversationRepository`를 구현하지 않는다.

이번 구현은 **도구 호출, 스킬 실행, MCP 서버 연결을 구현하지 않는다.** 미래에 이들을
도입할 수 있도록 LangChain/LangGraph 메시지 및 모델 경계를 선택하는 기반 작업이다.

## 근거와 결정

- 현재 Forge gateway는 system/user 배열을 만든 뒤 Codex SDK에 `"role: content"`
  평문으로 전달한다. 이는 LangChain 메시지/모델 인터페이스로 교체할 대상이다.
  - `src/forge/adapters/outbound/llm/litellm_codex_gateway.py:44-46`
  - `src/forge/adapters/outbound/llm/litellm_codex_gateway.py:181-218`
- Forge 대화 도메인과 포트는 현재 단발성 계약이다.
  - `src/forge/domain/conversation/models.py`
  - `src/forge/ports/outbound/model_gateway.py`
- LangGraph `InMemorySaver`와 `thread_id`는 프로세스 수명의 다회 대화 상태를
  관리하며, 이후 DB-backed checkpointer로 바꿀 수 있다. 장기 기억/벡터 검색은
  이 범위에 포함하지 않는다. [LangGraph memory guide](https://docs.langchain.com/oss/python/langgraph/add-memory)

### 아키텍처 경계

```text
CLI (--conversation-id, --system)
  -> application `ReceiveMessageService` facade
  -> Forge runtime (compiled LangGraph: MessagesState + InMemorySaver)
  -> LangChain BaseChatModel
  -> provider adapter (LiteLLM / Codex SDK)
```

1. `forge.runtime`이 LangGraph graph 조립, 메시지 추가, 대화별 상태 분리,
   checkpoint 수명과 모델 node 실행을 소유한다.
2. `forge.application`의 `ReceiveMessageService`는 inbound command를 검증하고
   runtime invocation을 호출하는 얇은 facade로 유지한다. CLI 등 inbound adapter는
   runtime을 직접 import하지 않는다.
3. LangChain은 runtime 내부의 표준 메시지·모델 실행 계약이며, 단순 adapter나
   infrastructure로 취급하지 않는다.
4. 현재 단계에는 `bind_tools`, `ToolNode`, `create_agent`, MCP client,
   `ToolExecutionGateway`, `ConversationRepository`를 만들지 않는다.
5. `agent` 패키지의 tools, skills, cognition, memory, orchestrator는 수정하지 않는다.
6. tool calling을 도입할 때에는 새 계획으로 정책/HITL/audit을 설계한다. LangChain의
   도구 인터페이스가 Forge의 헌법 정책을 자동으로 대체한다고 가정하지 않는다.
7. `system_instruction`은 각 invocation의 runtime context다. model node는 해당
   호출 직전에만 `SystemMessage`를 앞에 붙이며, node 반환값 및 checkpoint의
   `MessagesState`에는 system message를 넣지 않는다.
8. application command `SendMessageCommand`는 user의 요청 경계 값이고,
   LangChain `HumanMessage`는 graph state에 저장되는 모델 문맥 메시지다. facade가
   command의 `text`를 `HumanMessage`로 변환하는 runtime 호출로 연결한다.

## 의존성

### 추가

- `langchain`
- `langchain-core`
- `langgraph`
- `langchain-litellm` (LiteLLM Router를 계속 사용할 경우)

### 이번에 추가하지 않음

- `langchain-mcp-adapters`
- provider별 tool integration 패키지
- LangGraph DB checkpointer 패키지

정확한 호환 버전은 설치 시 upstream metadata를 확인해 `pyproject.toml`에 기록한다.
현재 `litellm==1.93.0`, `openai-codex==0.144.4`와의 조합은 작은 adapter 테스트로
고정한다.

## 구현 단계

### 1. LangChain/LangGraph 의존성과 메시지 모델 적합성 확인

**파일**

- `pyproject.toml`
- 새 `tests/forge/adapters/outbound/llm/test_langchain_compatibility.py`

**작업**

1. 최소 의존성을 추가하고 lockfile이 있다면 갱신한다.
2. LangChain `SystemMessage`, `HumanMessage`, `AIMessage`가 Forge가 지원할
   system/user/assistant 대화에 필요한 content·model metadata를 보존하는지 fake
   model로 테스트한다.
3. `langchain-litellm` + 현재 LiteLLM custom provider registration이 import와
   초기화 단계에서 충돌하지 않는지 검증한다.

**완료 기준**

- 의존성 설치 후 `pytest`에서 메시지 생성과 fake model invocation이 통과한다.
- 네트워크·실제 Codex 로그인 없이 테스트가 수행된다.

### 2. LangChain chat-model 기반 provider 경계 전환

**파일**

- 분할/추가: `src/forge/adapters/outbound/llm/langchain_model_factory.py`
- 분할/추가: `src/forge/adapters/outbound/llm/codex_chat_model.py` (필요한 경우)
- 축소 또는 제거: `src/forge/adapters/outbound/llm/litellm_codex_gateway.py`
- 갱신: `src/forge/adapters/outbound/llm/__init__.py`
- 갱신: `tests/forge/adapters/outbound/llm/test_litellm_codex_gateway.py`

**작업**

1. runtime이 사용할 `BaseChatModel`을 구성하는 provider factory를 만든다.
2. 기존 Codex managed-login/custom provider가 계속 필요한지 확인하고, 필요하면
   LangChain adapter 뒤에만 유지한다. `ChatOpenAI`가 이를 자동 대체한다고 가정하지
   않는다.
3. `config/agent.yml`의 backend, model, temperature, max_tokens를 실제 model
   선택과 invocation 옵션에 반영한다. 현재 gateway의 `TERRA_MODEL` 고정 선택을
   제거한다.
4. 이번 단계의 응답은 assistant text 하나만 허용한다. tool call 관련 필드는
   처리하거나 저장하지 않는다.

**완료 기준**

- user/assistant 메시지 순서와 모델 옵션이 fake model 테스트에서 보존된다.
- Codex와 Ollama 구성 경로가 선택 규칙에 따라 초기화된다.
- Forge domain/application/ports에 LangChain, LiteLLM, Codex import가 없다.

### 3. LangGraph 기반 Forge runtime 구성

**파일**

- 추가: `src/forge/runtime/conversation.py`
- 추가: `src/forge/runtime/context.py`
- 교체: `src/forge/application/conversation/receive_initial_input.py` →
  `receive_message.py` (`ReceiveMessageService` facade)
- 교체: `src/forge/ports/inbound/receive_initial_input.py` →
  `receive_message.py` (`ReceiveMessage` port)
- 갱신: `src/forge/application/conversation/__init__.py`,
  `src/forge/ports/inbound/__init__.py`
- 추가/교체: `tests/forge/runtime/test_conversation_runtime.py`

**작업**

1. `forge.runtime.conversation`에 `MessagesState`를 쓰는 1노드 graph를 작성한다.
   node는 checkpoint state의 user/assistant 메시지를 LangChain chat model에 전달하고
   AI 응답만 state에 추가한다.
2. graph를 `InMemorySaver`로 compile하고, `conversation_id`를 LangGraph
   `configurable.thread_id`로 전달한다.
3. `system_instruction`은 `context_schema`/runtime context의 호출별 값으로 전달한다.
   model node는 `[SystemMessage(system_instruction), *state["messages"]]`를 모델에
   전달하되, 반환할 state update에는 AI 응답만 넣는다.
4. `SendMessageCommand`는 `conversation_id`, `text`, 선택 `system_instruction`을
   가진 application command다. `ReceiveMessageService`가 이를 검증하고 runtime에
   전달하며, runtime은 `text`만 새 `HumanMessage`로 graph state에 추가한다. 별도
   Forge `ChatMessage`, `ChatRequest`, `ChatResponse`, repository 포트는 만들지 않는다.
5. graph의 마지막 `AIMessage` content를 facade의 Forge 응답으로 변환한다.

**완료 기준**

- 동일 `thread_id`의 두 호출에서 두 번째 모델 입력이 첫 user/assistant turn을 포함한다.
- 서로 다른 `thread_id`의 이력은 분리된다.
- `InMemorySaver` 외의 직접 dict/list 저장소나 repository 구현이 없다.
- 첫 호출의 `system_instruction`은 checkpoint state에 없고, 두 번째 호출에 다른
  instruction을 전달하면 두 번째 모델 호출에만 그 값이 적용된다.
- 프로세스를 재시작하면 이력이 사라지는 현재 한계가 CLI help와 테스트에 명시된다.

### 4. Bootstrap과 CLI를 graph 수명에 연결

**파일**

- 갱신: `src/forge/bootstrap/container.py`
- 갱신: `src/forge/adapters/inbound/cli.py`
- 갱신: `tests/forge/adapters/inbound/test_cli.py`
- 갱신: `tests/forge/application/conversation/test_hexagonal_initial_input.py`

**작업**

1. composition root가 REPL 시작 시 `forge.runtime`과 `InMemorySaver`를 한 번 만들고 같은
   인스턴스를 모든 입력에 재사용하게 한다.
2. `--conversation-id`를 지원한다. REPL에서는 미지정 시 UUID를 생성·표시하고,
   `--query`는 지정 ID 또는 새 ID로 한 번 graph를 호출한다.
3. `--system`을 지원하고, assistant 최종 text만 stdout에 쓴다.
4. 기존 `InitialInput`, `ModelReply`, `ReceiveInitialInputService`,
   `complete_initial_input` 및 대응 CLI helper를 제거한다.

**완료 기준**

- REPL의 두 입력이 같은 thread state를 사용한다.
- query mode, 빈 입력, 모델 오류, `--conversation-id` 동작이 테스트된다.
- `rg` 결과에 legacy Forge 대화 계약 참조가 남지 않는다.

### 5. 문서화와 미래 확장 경계 기록

**파일**

- 갱신: `README.md`
- 새 또는 갱신: `docs/architecture/langchain-chat.md`
- 갱신: `library_integration_review.md` (현재 결정과의 차이 기록)

**작업**

1. short-term conversation state는 LangGraph checkpointer이고, 장기 기억과
   Forge의 L1-L5 memory system이 아니라는 점을 문서화한다.
2. tool/skill/MCP는 아직 미구현이며 다음 설계에서 policy, HITL, audit을 먼저
   결정해야 한다고 명시한다.
3. DB checkpointer, message trimming/summarization, streaming, LangGraph tool
   loop는 후속 선택지로만 기록한다.

**완료 기준**

- 사용자가 대화 상태의 수명, `conversation_id` 사용법, 미지원 기능을 알 수 있다.
- 현재 구현 범위와 향후 tool/MCP 계획이 혼동되지 않는다.

## 수용 기준

- `forge.runtime`의 LangGraph가 세션 메시지 상태와 checkpoint를 관리하며 Forge가 직접 저장소를 구현하지 않는다.
- 동일 `conversation_id`는 프로세스 수명 동안 문맥을 유지하고, 다른 ID는 격리된다.
- LangChain model node가 system instruction을 invocation context에서만 적용하고,
  checkpoint에는 user/assistant 메시지만 저장한다.
- 설정의 backend/model/temperature/max_tokens가 실제 호출에 반영된다.
- `src/forge` 도메인·포트·애플리케이션 계층에 LangChain/LiteLLM/Codex/CLI import가 없다.
- tool calling, L3 skill execution, MCP client/adapter, 정책 실행 게이트는 이번 diff에 없다.
- 기존 단발성 Forge 계약 참조가 제거된다.

## 검증

1. `pytest tests/forge -q` — graph state, 모델 adapter, CLI 계약.
2. `ruff check src/forge tests/forge`.
3. `mypy src/forge`.
4. `pytest -q` — 전체 회귀.
5. fake LangChain model로 다회 대화, thread 격리, system instruction의 비저장성,
   provider 오류를
   네트워크 없이 검증한다.
6. 선택 smoke test로 Codex 및 Ollama 설정을 각각 한 번 호출한다. 자격 증명이나
   로컬 서버가 없으면 필수 검증에서 제외하고 사유를 기록한다.

## 위험과 완화

| 위험 | 완화 |
| --- | --- |
| LangChain만으로 상태가 자동 관리된다고 오해 | `MessagesState + InMemorySaver + thread_id`를 명시적으로 graph에 연결한다. |
| Codex custom provider가 LangChain LiteLLM 통합과 맞지 않음 | fake compatibility test를 먼저 만들고, 필요하면 custom adapter를 유지한다. |
| system prompt가 매 호출마다 checkpoint에 누적 | invocation context로만 전달하고 checkpoint snapshot에 system message가 없는지 테스트로 고정한다. |
| in-memory state가 영속 memory로 오해됨 | CLI help와 문서에 프로세스 종료 시 소멸을 명시한다. |
| 미래 tool/MCP 요구가 이번 범위를 부풀림 | `bind_tools`/MCP adapter/policy gateway는 후속 설계로 분리한다. |

## 완료 조건

Forge CLI가 LangGraph의 in-memory checkpoint를 사용해 `conversation_id`별 다회
대화를 제공하고, 위 자동화 검증이 통과하면 완료다. tool calling과 MCP는 구현하지
않는다.
