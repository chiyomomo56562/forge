# Phase 2 Tool Integration — 다음 실행 계획

## 목표와 완료 조건

사용자 대화와 Inner Loop가 등록된 읽기 전용 도구를 안전하게 호출하고, 실행 결과를
모델에 전달해 최종 답변 또는 재계획을 만들 수 있게 한다.

완료 조건은 다음과 같다.

- `ModelResponse.tool_calls`와 LangChain `AIMessage.tool_calls`가 구조적으로 유효한
  `PlanStep`으로 변환된다.
- 대화는 `model → tool execution → model` 순환을 수행하며, tool 결과가 다음 모델 호출에 포함된다.
- Inner Loop는 실행 결과를 근거로 제한된 횟수만 재계획할 수 있다.
- bootstrap은 대화 도구 설정과 Inner Loop planner 설정을 독립적으로 조립한다.
- 기본 권한은 읽기 전용 및 선택적 verification만 허용하며, `workspace.apply_patch`는 기본 거부를 유지한다.
- 단위·통합·E2E 테스트, `ruff`, 전체 `tests/forge`가 통과한다.

## 현재 기준선

- `workspace.apply_patch` handler(#12)와 `PromptToolCallingStrategy`(#8)는 구현되어 있다.
- `PromptToolCallingStrategy`는 domain `ChatModel`/`ModelResponse` 경계에서 동작한다.
- `LangGraphConversationRuntime`은 LangChain 메시지와 모델을 직접 사용하며 tool call을
  `AssistantReply`로 노출만 하고 실행하지 않는다.
- 따라서 #9~#11 구현 전에는 새 prompt strategy가 실제 대화 호출 경로에 도달하지 않는다.

## 책임 경계와 계약

### 책임 분리

| 컴포넌트 | 책임 | 명시적으로 맡지 않는 책임 |
|---|---|---|
| converter | 호출 객체의 구조적 유효성, 원래 순서, 고유 ID, `arguments` mapping을 `PlanStep`으로 옮김 | registry 조회, 권한, schema 검증, 실행 |
| planner | 모델 호출, provider 응답을 converter에 전달, 계획 생성 실패를 자신의 오류 계약으로 처리 | 실행 인가와 handler 선택 |
| executor/policy | 등록 여부, argument schema 검증, 인가, 실행, 안전한 `ToolExecution` 생성 | LLM 출력 파싱과 tool-result prompt 형식 |
| tool-result serializer | `ToolExecution`을 제한·sanitize된 모델 피드백으로 직렬화 | 재계획 판단과 권한 정책 |
| conversation runtime | graph 순환, `ToolMessage` state, turn/protocol 한도 | provider별 parsing 규칙과 registry 정책 |

converter는 registry 또는 허용 도구 집합을 입력받지 않는다. 도구 이름이 비어 있지 않고
arguments가 mapping이며 call ID가 한 turn 안에서 고유한지만 검사한다. registry 등록 여부와
schema 적합성은 executor가 실행 직전에 판단한다.

### tool-result 모델 피드백 계약

도구 실행 뒤 모델에 전달하는 정본 payload는 다음 필드를 가진다.

```json
{
  "tool_call_id": "call_1",
  "name": "workspace.read_file",
  "status": "success | denied | failed",
  "summary": "safe summary",
  "output": "safe output",
  "truncated": false
}
```

- `tool_call_id`는 converter가 만든 `PlanStep.step_id`와 동일하다. 모델이 준 ID가 없으면
  converter가 만든 ID를 사용하며, 중복 ID는 실행 전에 거부한다.
- `output`은 별도 `max_tool_feedback_bytes` 설정으로 제한하고, 잘림은 `truncated: true`로 표시한다.
- 모델 피드백에는 내부 traceback, 절대 경로, 비밀값, raw audit details를 넣지 않는다.
  serializer는 허용된 `ToolExecution.summary`, safe error code, 제한된 output만 사용한다.
- 한 turn의 여러 호출은 **선언 순서대로 실행**한다. 한 호출이 denied 또는 failed여도,
  명시적 stop 정책이 추가되기 전까지 나머지 호출은 실행하고 결과를 모두 모델에 전달한다.

### malformed envelope 정책

프로토콜 실패와 업무 결과 부족은 별개로 관리한다.

- parse 전 call ID를 얻을 수 없으면 synthetic `tool_protocol` failure `ToolMessage`를 만든다.
  모델에는 안전한 오류 코드와 JSON envelope 재출력 지시만 보낸다.
- 개별 call ID를 확보한 뒤 구조 검증이 실패하면 해당 ID의 failed `ToolMessage`를 만든다.
- protocol failure는 `max_protocol_failures`로 제한하며 Inner Loop의 `max_feedback_cycles` 또는
  업무 실행 retry에 포함하지 않는다.
- 한도 초과 시 대화는 안전한 최종 응답으로 종료하고, Inner Loop는 planning failure로 요약·평가 경로에
  들어간다. graph 예외로 종료하지 않는다.

## 구현 단계

### 1. 공용 tool-call 변환 계층 완성 (#9)

대상 파일:

- 신규: `src/forge/adapters/outbound/inner_loop/tool_call_converter.py`
- 수정: `src/forge/adapters/outbound/inner_loop/llm_planner.py`
- 수정: `src/forge/adapters/outbound/inner_loop/__init__.py`
- 신규: `tests/forge/adapters/outbound/inner_loop/test_tool_call_converter.py`

작업:

1. `ToolCallData` 시퀀스를 `InnerLoopPlan`으로 변환하는 순수 함수를 만든다.
2. `PlanStep.step_id`를 `ToolCallData.id`에 대응시키고, ID가 없으면 안정적인 새 ID를 만든다.
3. 빈 호출, 빈 name, mapping이 아닌 arguments, turn 내 중복 ID만 구조 오류로 거부한다.
4. 기존 structured-output `LLMPlanner`는 유지하고, tool model을 사용하는 별도 native planner
   또는 명시적 planner mode를 추가한다.

완료 기준:

- 하나·여러 tool call의 순서, ID, arguments가 `PlanStep`에 보존된다.
- 중복 `tool_call_id`는 executor 호출 전에 거부된다.
- registry에 없는 도구는 converter가 아닌 executor 테스트에서 `tool.unknown`으로 검증된다.
- 기존 structured planner 테스트가 그대로 통과한다.

### 2. tool-result 및 protocol-failure 직렬화 계약 구현

대상 파일:

- 신규: `src/forge/application/conversation/tool_feedback.py` 또는 동등한 전용 module
- 수정: `src/forge/domain/inner_loop/models.py` (계약 값이 필요할 때만)
- 신규: `tests/forge/application/conversation/test_tool_feedback.py`

작업:

1. `ToolExecution`에서 위 JSON 계약의 safe feedback을 만드는 serializer를 구현한다.
2. 성공·거부·실패 상태와 `safe_error_code`를 모델용 문자열로 정규화한다.
3. 출력 크기 제한, UTF-8 안전 절단, `truncated` 표기, 민감 값/절대 경로/traceback sanitization을 구현한다.
4. parse 실패를 표현할 synthetic protocol failure를 같은 serializer 계약으로 만든다.
5. 순차 실행과 실패 후 계속 실행 정책을 runtime이 사용할 명시적 helper/API로 고정한다.

완료 기준:

- 정상·denied·failed·protocol failure의 payload shape가 테스트로 고정된다.
- 모델로 전달되는 output이 설정 상한을 넘지 않고 잘림이 명시된다.
- raw audit details와 민감한 오류 문자열이 feedback에 포함되지 않는다.

### 3. 대화 모델 bridge와 tool execution graph 구현 (#11의 기반)

대상 파일:

- 신규: `src/forge/adapters/outbound/llm/conversation_bridge.py`
- 수정: `src/forge/runtime/conversation.py`
- 신규/수정: `tests/forge/adapters/outbound/llm/test_conversation_bridge.py`
- 수정: `tests/forge/runtime/test_conversation_runtime.py`

작업:

1. bridge는 LangChain `SystemMessage`/`HumanMessage`/`AIMessage`를 domain `ChatMessage`로,
   `ModelResponse`를 `AIMessage`로 변환한다.
2. tool 결과는 LangGraph `ToolMessage`로 state에 보관한다. bridge가 provider에 전달할 때에는
   Step 2 serializer의 payload를 tool-result 전용 prompt 형식으로 명시적으로 직렬화한다.
3. `LangGraphConversationRuntime`에 optional `PlanStepExecutor`, converter, feedback serializer를 주입한다.
4. `call_model` 뒤의 tool call 존재 여부에 따라 `execute_tools` 또는 종료로 분기한다.
5. `execute_tools`는 선언 순서대로 호출을 변환·실행하고, 각 결과를 같은 `tool_call_id`의 `ToolMessage`로 추가한다.
6. protocol failure와 turn 한도를 conditional edge로 처리해 graph 예외를 막는다.

완료 기준:

- executor가 있으면 하나 이상의 tool call을 실행하고 마지막 모델 답변을 반환한다.
- execution 결과의 `ToolMessage.tool_call_id`와 `PlanStep.step_id`가 정확히 일치한다.
- failed/denied 호출과 그 뒤 호출의 실행 순서·결과가 모두 테스트된다.
- executor 미주입 시 입력 LangChain 메시지의 role/content, 일반 모델 응답, checkpoint 이력이 보존된다.
- executor 미주입 시 추가 system message·`ToolMessage`·모델 재호출이 없다.

### 4. Inner Loop 결과 기반 재계획 구현 (#10)

대상 파일:

- 수정: `src/forge/application/inner_loop/run_inner_loop.py`
- 수정: `src/forge/adapters/outbound/inner_loop/llm_planner.py` 또는 신규 feedback-aware planner adapter
- 수정: `tests/forge/application/inner_loop/test_run_inner_loop.py`

작업:

1. `InnerLoopState`에 마지막 `ToolExecution`과 feedback 횟수를 추가한다.
2. 실행 결과 부족을 planner에 전달할 feedback 입력 계약을 정의한다.
3. `execute_attempt` 다음 conditional edge에 `re_plan` 분기를 추가한다.
4. 업무 재계획은 `max_feedback_cycles`로 제한한다. protocol failure 카운터와 섞지 않는다.
5. 한도 초과, 정책 거부, planner 오류는 재계획 루프가 아닌 summarize/evaluate 경로로 종료한다.

완료 기준:

- 실행 결과를 반영한 두 번째 계획이 생성되는 테스트가 있다.
- 업무 feedback과 protocol failure가 독립된 한도로 관리된다.
- 기존 deterministic retry 의미는 변경되지 않으며 무한 graph loop가 없다.

### 5. bootstrap 및 설정 조립 (#11 완료)

대상 파일:

- 수정: `src/forge/bootstrap/container.py`
- 수정: `config/agent.yml`
- 수정: `tests/forge/application/conversation/test_hexagonal_initial_input.py`
- 신규/수정: `tests/forge/bootstrap/test_container.py`

작업:

1. `conversation.tools` 설정은 conversation runtime의 tool-capable model/bridge/executor만 조립한다.
2. `inner_loop.planner.type`은 Inner Loop planner 선택만 담당한다.
3. `max_tool_feedback_bytes`, `max_protocol_failures`, `allow_verification` 등 각 경계의 설정을 최소한으로 둔다.
4. 지원 조합을 작은 matrix로 명시하고 bootstrap 테스트에 고정한다.
5. mutation 권한은 설정만 추가하지 않고 별도 승인 설계가 결정될 때까지 기본 거부를 유지한다.

완료 기준:

- 기본 config에서 읽기 전용 conversation tool E2E가 동작한다.
- `workspace.apply_patch` 호출은 기본 config에서 `tool.approval_required`로 끝난다.
- 지원하지 않는 conversation strategy와 planner mode 조합은 bootstrap에서 명확히 실패한다.
- 설정 matrix가 테스트로 고정된다.

### 6. 회귀·E2E 검증과 문서 갱신

대상 파일:

- 수정: `docs/implementation_plan/phase2_tool_integration.md`
- 필요 시: `docs/architecture/langchain-chat.md`

작업:

1. Fake model로 `사용자 입력 → tool call → ToolMessage → 최종 답변` E2E를 작성한다.
2. 일반 텍스트, 다중 호출, 중복 ID, tool failure, authorization denial, protocol failure,
   output truncation, feedback limit을 포함한다.
3. 구현 계획 표의 #8~#12 상태와 테스트 체크박스를 현재 상태로 갱신한다.
4. prompt-tool JSON envelope, tool-result payload, sanitization, 안전 경계를 architecture 문서에 기록한다.

완료 기준:

- `ruff check src tests`와 `pytest -q tests/forge`가 통과한다.
- 새 E2E는 실행 횟수, `tool_call_id` 매핑, 다음 모델 입력의 제한된 tool 결과를 검증한다.
- 문서가 실제 구현 상태와 일치한다.

## 권장 실행 단위

1. Step 1과 Step 2를 각각 별도 커밋으로 완료한다.
2. Step 3은 두 계약이 확정된 뒤 수행한다.
3. Step 4와 Step 5는 Step 3의 실제 tool-result 표현을 기반으로 순차 수행한다.
4. Step 6의 전체 회귀를 통과한 뒤에만 Phase 2 완료로 표시한다.
