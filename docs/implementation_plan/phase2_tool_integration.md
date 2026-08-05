# Phase 2 Tool Integration 구현 계획

> 본 문서는 Implementation Process.md의 Phase 2 (이너 루프) 중 미구현 항목(#8~#12)에 대한
> 실행 순서와 단계별 작업을 정의한다.
> 기본 방침: **독립 항목 우선 → 기반 전략 → 변환 → 피드백 → 통합**

---

## 0. 현재 구현 상태 요약

### 완료된 항목 (#1~#7)

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| 1 | 도구 도메인 모델 | ✅ | `domain/inner_loop/models.py`: ToolRiskTier, ToolStatus, ToolSchema, ToolDefinition, ToolInvocation, ToolResult |
| 2 | ToolRegistry / AuthorizationPolicy 포트 | ✅ | `ports/outbound/inner_loop.py` |
| 3 | BuiltinToolRegistry | ✅ (6/7) | 7개 등록, 6개 구현, `workspace.apply_patch` 스텁 |
| 4 | RegistryPlanStepExecutor | ✅ | `adapters/outbound/tools/executor.py` |
| 5 | Inner Loop 오케스트레이션 (LangGraph) | ✅ | `application/inner_loop/run_inner_loop.py` |
| 6 | LLM 게이트웨이 (LiteLLM/Codex) | ✅ | `litellm_codex_gateway.py`, `providers/ollama.py`, `providers/codex.py` |
| 7 | LLM → 도구 선택 (LLM-driven planner) | ✅ | `inner_loop/llm_planner.py`: structured output 방식 |

### 미구현 항목 (#8~#12)

| # | 항목 | 상태 | 핵심 파일 |
|---|------|------|-----------|
| 8 | LLM에 도구 바인딩 (bind_tools) | ⚠️ 부분 | `strategies/ports.py`, `bootstrap/container.py:243` |
| 9 | tool_call 파싱 → PlanStep 변환 | ❌ 미구현 | `runtime/conversation.py:87`, `domain/llm/models.py:29` |
| 10 | 도구 결과 → LLM feed-back 루프 | ❌ 미구현 | `application/inner_loop/run_inner_loop.py`, `runtime/conversation.py` |
| 11 | 대화 경로에 툴 통합 | ❌ 미구현 | `bootstrap/container.py:52`, `runtime/conversation.py` |
| 12 | workspace.apply_patch handler | ❌ 스텁 | `adapters/outbound/tools/builtin.py:329` |

---

## 1. 의존성 그래프

```
    #12 (apply_patch handler)          #8 (PromptToolCallingStrategy)
          |                                     |
          |                                     v
          |                              #9 (tool_call → PlanStep)
          |                                     |
          |                                     v
          +----> #11 (대화 툴 통합) <---- #10 (feed-back 루프)
```

**핵심 의존관계:**
- #12는 독립적 — 다른 항목에 의존하지 않음
- #8은 #9, #10, #11의 기반 — 도구 호출 응답을 받아야 후속 작업 가능
- #9는 #8에 의존 — ModelResponse.tool_calls가 채워져야 변환 가능
- #10은 #8, #9에 의존 — 도구 결과를 LLM에 재전달하려면 tool_call 파싱 필요
- #11은 #8, #9, #10에 의존 — 대화 그래프에 도구 실행 사이클을 통합

---

## 2. 구현 순서

### Step 1: #12 workspace.apply_patch handler

**우선순위:** 최우선 (독립 항목, 가장 낮은 위험)
**선행 조건:** 없음
**목표:** 스텁 handler를 실제 구현으로 교체한다.

| 작업 | 파일 | 상세 |
|------|------|------|
| patch 적용 handler | `src/forge/adapters/outbound/tools/builtin.py` | `_apply_patch` 메서드 구현. unified diff를 파싱하여 workspace 내 파일에 적용 |
| 경로 검증 | `src/forge/adapters/outbound/tools/builtin.py` | patch 대상 파일이 workspace root 내에 있는지 검증 (path traversal 방지) |
| handler 등록 | `src/forge/adapters/outbound/tools/builtin.py:329` | `_RegisteredTool(..., handler=None)` → `handler=self._apply_patch` |
| 권한 정책 | `src/forge/adapters/outbound/tools/builtin.py` | `WORKSPACE_MUTATION` 위험도이므로 `StaticToolAuthorizationPolicy`에서 기본 거부. `allow_verification`과 별도로 `allow_mutation` 설정 추가 검토 |

**구현 상세:**

```python
def _apply_patch(self, invocation: ToolInvocation) -> ToolResult:
    """unified diff patch를 workspace 파일에 적용한다.

    Args:
        invocation: tool_name="workspace.apply_patch", arguments={"patch": "..."}.

    Returns:
        적용된 파일 목록과 상태를 포함한 ToolResult.
    """
    patch_text = invocation.arguments["patch"]
    # 1. unified diff 파싱
    # 2. 각 hunk의 대상 파일 경로가 workspace 내인지 검증
    # 3. 파일 읽기 → patch 적용 → 파일 쓰기
    # 4. 실패 시 ToolResult(status=FAILED, ...)
    # 5. 성공 시 ToolResult(status=COMPLETED, summary="Patched N file(s)")
```

**테스트:** `tests/forge/adapters/outbound/tools/test_builtin.py`
- [ ] 정상 unified diff 적용 후 파일 내용 확인
- [ ] workspace 외부 경로 접근 시 거부
- [ ] 잘못된 patch 형식 시 FAILED 반환
- [ ] 존재하지 않는 파일 생성 (new file)
- [ ] 여러 파일 동시 patch

---

### Step 2: #8 PromptToolCallingStrategy 구현

**우선순위:** 2순위 (#9, #10, #11의 기반)
**선행 조건:** 없음 (#12와 병렬 가능)
**목표:** `_PlaceholderToolCallingStrategy`를 실제 구현으로 교체한다.

| 작업 | 파일 | 상세 |
|------|------|------|
| 전략 구현 | `src/forge/adapters/outbound/llm/strategies/prompt_tool_calling.py` (신규) | `PromptToolCallingStrategy` 클래스. prompt 주입으로 도구 schema를 전달하고, 응답에서 tool_call을 파싱 |
| 전략 등록 | `src/forge/adapters/outbound/llm/strategies/__init__.py` | `PromptToolCallingStrategy` export 추가 |
| container 교체 | `src/forge/bootstrap/container.py:238` | `_PlaceholderToolCallingStrategy()` → `PromptToolCallingStrategy()` |
| config 검증 | `src/forge/bootstrap/container.py:225` | `tool_calling_strategy: prompt` 허용 (현재는 에러 발생) |

**구현 상세:**

```python
class PromptToolCallingStrategy:
    """prompt 주입과 응답 파싱으로 tool-calling을 구현하는 전략.

    native API 대신 prompt에 도구 schema를 설명하고,
    모델 응답에서 JSON tool_call을 파싱한다.
    """

    def apply(self, base_model: ChatModel, tools: Sequence[ToolSchema]) -> ChatModel:
        """base model을 tool-calling 가능한 model wrapper로 변환한다.

        Args:
            base_model: 순수 채팅 모델.
            tools: 도구 schema 목록.

        Returns:
            invoke 시 prompt에 도구 설명을 주입하고 응답에서
            tool_calls를 추출하는 ChatModel 래퍼.
        """
        return _PromptToolModel(base_model, tools)
```

**핵심 설계:**
- `_PromptToolModel`은 `ChatModel.invoke()` 구현 시:
  1. system prompt에 도구 schema 설명을 주입
  2. 모델 응답 텍스트에서 JSON tool_call 블록 파싱
  3. `ModelResponse(content=..., tool_calls=(ToolCallData(...), ...))` 반환
- `PromptStructuredOutputStrategy`와 동일한 패턴 (prompt 주입 + JSON 파싱)

**테스트:** `tests/forge/adapters/outbound/llm/strategies/test_prompt_tool_calling.py`
- [ ] 도구 schema가 system prompt에 포함되는지 확인
- [ ] 응답에서 tool_call 파싱 정상
- [ ] tool_call 없는 응답 시 빈 튜플 반환
- [ ] 잘못된 JSON 형식 시 빈 튜플 또는 에러

---

### Step 3: #9 tool_call → PlanStep 변환

**우선순위:** 3순위
**선행 조건:** Step 2 (#8) 완료
**목표:** `ModelResponse.tool_calls` (ToolCallData)를 `PlanStep`으로 변환하는 경로를 구현한다.

| 작업 | 파일 | 상세 |
|------|------|------|
| 변환 함수 | `src/forge/adapters/outbound/inner_loop/tool_call_converter.py` (신규) | `ToolCallData` → `PlanStep` 변환. 도구 이름 검증, arguments 매핑 |
| 대화 경로 연결 | `src/forge/runtime/conversation.py` | `_extract_tool_calls` 결과를 PlanStep으로 변환하는 로직 추가 (또는 별도 adapter) |
| Inner Loop 연결 | `src/forge/adapters/outbound/inner_loop/llm_planner.py` | native tool_call 경로 추가 (현재는 structured output만 사용) |

**구현 상세:**

```python
def tool_calls_to_plan(
    tool_calls: Sequence[ToolCallData],
    allowed_tool_names: frozenset[str],
) -> InnerLoopPlan:
    """ModelResponse.tool_calls를 InnerLoopPlan으로 변환한다.

    Args:
        tool_calls: 모델 응답에서 추출한 도구 호출 목록.
        allowed_tool_names: registry에 등록된 도구 이름 집합.

    Returns:
        각 tool_call이 PlanStep이 된 실행 계획.

    Raises:
        PlanGenerationError: 알 수 없는 도구 이름이 포함된 경우.
    """
    steps = tuple(
        PlanStep(
            step_id=f"step_{i}",
            summary=f"Call {tc.name}",
            tool_name=tc.name,
            tool_arguments=dict(tc.arguments),
            retry_allowed=True,
        )
        for i, tc in enumerate(tool_calls, start=1)
        if tc.name in allowed_tool_names
    )
    if not steps:
        raise PlanGenerationError("No valid tool calls in model response")
    return InnerLoopPlan(
        summary=f"Native tool-call plan with {len(steps)} step(s).",
        steps=steps,
    )
```

**핵심 설계:**
- 기존 `LLMPlanner`는 structured output(JSON plan) 방식만 지원
- native tool_call 경로는 `UnifiedChatModelFactory.create_tool_model()`로 생성된 모델 사용
- 두 경로(deterministic / structured / native)는 config의 `inner_loop.planner.type`로 선택
- `conversation.py`의 `_extract_tool_calls`는 `ToolCall`을 반환하므로, `ToolCall` → `PlanStep` 변환도 필요

**테스트:** `tests/forge/adapters/outbound/inner_loop/test_tool_call_converter.py`
- [ ] ToolCallData → PlanStep 변환 정상
- [ ] 알 수 없는 도구 이름 시 PlanGenerationError
- [ ] 빈 tool_calls 시 PlanGenerationError
- [ ] arguments 매핑 정상

---

### Step 4: #10 도구 결과 → LLM feed-back 루프

**우선순위:** 4순위
**선행 조건:** Step 2 (#8), Step 3 (#9) 완료
**목표:** 도구 실행 결과를 LLM에 재전달하여 다음 행동을 결정하는 피드백 사이클을 구현한다.

| 작업 | 파일 | 상세 |
|------|------|------|
| Inner Loop 피드백 | `src/forge/application/inner_loop/run_inner_loop.py` | `execute_attempt` 노드 후 결과를 LLM에 재전달하는 conditional edge 추가 |
| 대화 피드백 | `src/forge/runtime/conversation.py` | `call_model` → `execute_tools` → `call_model` 사이클 추가 |
| 상태 확장 | `src/forge/application/inner_loop/run_inner_loop.py` | `InnerLoopState`에 `tool_results` 필드 추가 |

**구현 상세 — 대화 런타임:**

```python
# runtime/conversation.py — 그래프 구조 변경
builder = StateGraph(ConversationState, context_schema=ConversationContext)
builder.add_node("call_model", self._call_model)
builder.add_node("execute_tools", self._execute_tools)
builder.add_edge(START, "call_model")
builder.add_conditional_edges(
    "call_model",
    self._should_execute_tools,
    {"execute_tools": "execute_tools", END: END},
)
builder.add_edge("execute_tools", "call_model")
```

```python
# _should_execute_tools: AIMessage.tool_calls가 있으면 execute_tools로
# _execute_tools: tool_calls를 PlanStep으로 변환 → executor.execute() →
#                 ToolMessage를 messages에 추가 → call_model로 재진입
```

**구현 상세 — Inner Loop:**

```python
# run_inner_loop.py — 피드백 edge 추가
# 현재: plan → execute_attempt → (retry | summarize) → evaluate → reflect
# 변경: plan → execute_attempt → (retry | re_plan | summarize) → ...
#        re_plan: 도구 결과를 LLM에 전달하여 계획 수정

builder.add_conditional_edges(
    "execute_attempt",
    self._next_after_attempt,
    {
        "attempt": "execute_attempt",
        "re_plan": "plan",          # 신규: 결과 기반 재계획
        "summarize": "summarize_execution",
    },
)
```

**핵심 설계:**
- 대화 런타임: LangGraph의 `ToolNode` 패턴을 참고하되, `RegistryPlanStepExecutor`를 사용
- Inner Loop: 도구 실패 시 LLM에게 결과를 전달하고 재계획을 요청
- 피드백 최대 횟수 제한 (`max_retries`와 별도 또는 공용)

**테스트:**
- [ ] 도구 호출 후 결과가 LLM에 재전달되는지 확인
- [ ] 도구 실패 시 재계획 트리거
- [ ] 최대 피드백 횟수 초과 시 종료
- [ ] 도구 호출 없는 응답 시 바로 종료

---

### Step 5: #11 대화 경로에 툴 통합

**우선순위:** 5순위 (최종 통합)
**선행 조건:** Step 2 (#8), Step 3 (#9), Step 4 (#10) 완료
**목표:** 대화 런타임에 도구 registry/executor를 연결하고 bootstrap에서 조립한다.

| 작업 | 파일 | 상세 |
|------|------|------|
| 런타임 확장 | `src/forge/runtime/conversation.py` | `LangGraphConversationRuntime`에 `ToolRegistry`, `PlanStepExecutor` 주입 |
| bootstrap 조립 | `src/forge/bootstrap/container.py:52` | `build_receive_message_service`에 tool registry, executor, authorization policy 주입 |
| 상태 확장 | `src/forge/runtime/conversation.py` | `ConversationState`에 `tool_results` 필드 추가 (또는 MessagesState 활용) |
| config 연동 | `src/forge/bootstrap/container.py` | `config/agent.yml`의 `tools` 섹션에서 registry 설정 읽기 |

**구현 상세 — bootstrap:**

```python
def build_receive_message_service(
    chat_model: Any | None = None,
    *,
    config_path: str = "config/agent.yml",
) -> ReceiveMessageService:
    """대화 facade, LangGraph runtime, 도구 registry를 조립한다."""
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # 모델 생성 (tool-calling 가능)
    factory = _build_chat_model_factory(config)
    tool_schemas = registry.tool_schemas()
    model = factory.create_tool_model(tool_schemas)

    # 도구 registry + executor
    tool_config = config.get("tools", {})
    registry = BuiltinToolRegistry(
        tool_config.get("workspace_root", "."),
        max_read_bytes=tool_config.get("max_read_bytes", 32_768),
        ...
    )
    executor = RegistryPlanStepExecutor(
        registry,
        StaticToolAuthorizationPolicy(
            allow_verification=bool(tool_config.get("allow_verification", True))
        ),
    )

    return ReceiveMessageService(
        LangGraphConversationRuntime(model, executor=executor)
    )
```

**구현 상세 — 런타임:**

```python
class LangGraphConversationRuntime:
    def __init__(
        self,
        model: Any,
        *,
        executor: PlanStepExecutor | None = None,
        checkpointer: InMemorySaver | None = None,
    ) -> None:
        self._model = model
        self._executor = executor
        self._checkpointer = checkpointer or InMemorySaver()

        builder = StateGraph(ConversationState, context_schema=ConversationContext)
        builder.add_node("call_model", self._call_model)
        if executor is not None:
            builder.add_node("execute_tools", self._execute_tools)
            builder.add_edge(START, "call_model")
            builder.add_conditional_edges(
                "call_model",
                self._should_execute_tools,
                {"execute_tools": "execute_tools", END: END},
            )
            builder.add_edge("execute_tools", "call_model")
        else:
            builder.add_edge(START, "call_model")
            builder.add_edge("call_model", END)

        self._graph = builder.compile(checkpointer=self._checkpointer)
```

**핵심 설계:**
- `executor=None`이면 기존 동작 유지 (도구 없는 순수 채팅) — 하위 호환
- `executor` 주입 시 tool_call → execute → feed-back 사이클 동작
- `ReceiveMessageService` 인터페이스는 변경 없음 (외부 API 호환)

**테스트:** `tests/forge/runtime/test_conversation.py`
- [ ] executor 주입 시 도구 호출 → 실행 → 재호출 사이클 동작
- [ ] executor 미주입 시 기존 순수 채팅 동작 유지
- [ ] bootstrap에서 도구 설정 누락 시 graceful fallback
- [ ] 전체 E2E: 사용자 입력 → 도구 호출 → 결과 피드백 → 최종 답변

---

## 3. 구현 순서 요약

```
Step 1: #12 apply_patch handler        ← 독립, 최우선
    ↓ (병렬 가능)
Step 2: #8 PromptToolCallingStrategy   ← 기반 전략
    ↓
Step 3: #9 tool_call → PlanStep 변환   ← 변환 계층
    ↓
Step 4: #10 feed-back 루프             ← 피드백 사이클
    ↓
Step 5: #11 대화 툴 통합               ← 최종 통합
```

| Step | 항목 | 예상 난이도 | 선행 조건 | 병렬 가능 |
|------|------|------------|-----------|-----------|
| 1 | #12 apply_patch | 낮음 | 없음 | Step 2와 병렬 |
| 2 | #8 ToolCallingStrategy | 중간 | 없음 | Step 1과 병렬 |
| 3 | #9 tool_call 변환 | 낮음 | Step 2 | - |
| 4 | #10 feed-back 루프 | 높음 | Step 2, 3 | - |
| 5 | #11 대화 툴 통합 | 높음 | Step 2, 3, 4 | - |

---

## 4. 마일스톤

### Step 1~2 완료 후
- [ ] BuiltinToolRegistry 7/7 도구 구현 (apply_patch 포함)
- [ ] PromptToolCallingStrategy로 `UnifiedChatModelFactory.create_tool_model()` 사용 가능
- [ ] `_PlaceholderToolCallingStrategy` 제거

### Step 3~4 완료 후
- [ ] native tool_call 경로로 Inner Loop 계획 생성 가능
- [ ] 도구 실행 결과를 LLM에 재전달하는 피드백 사이클 동작
- [ ] structured output과 native tool_call 두 경로 모두 지원

### Step 5 완료 후 (Phase 2 Tool Integration 마일스톤)
- [ ] 대화 런타임에 도구 registry/executor 통합
- [ ] 사용자 입력 → 도구 호출 → 결과 피드백 → 최종 답변 E2E
- [ ] bootstrap에서 도구 설정 기반 자동 조립
- [ ] executor 미주입 시 기존 순수 채팅 호환
- [ ] 모든 tool integration 테스트 통과