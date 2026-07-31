# Inner Loop runtime 통합 계획

## 목표

현재 구현된 L0 JSONL 이벤트와 L1 Episode 저장소를 실제 실행 흐름으로 연결한다.
각 Inner Loop는 하나의 `session_id`/`episode_id`를 사용하며, 안전한 계획·실행·평가·반성
결과를 L0에 남긴 뒤 `FinalizeEpisodeService`로 정확히 하나의 L1 Episode를 확정한다.

기존 conversation CLI의 단순 대화 경로는 유지한다. Inner Loop는 별도 inbound use case로
도입해, 명시적으로 호출한 작업만 도구 실행과 메모리 축적을 수행한다.

## 확인된 기준과 정합성 보정

- Inner Loop 문서는 세션 시작, 컨텍스트/계획, 실행, 평가, 반성, L1 저장 순서를 정의한다.
  [00_session_setup.md](../../docs/inner_loop/00_session_setup.md),
  [01_context_planning.md](../../docs/inner_loop/01_context_planning.md),
  [02_execution.md](../../docs/inner_loop/02_execution.md),
  [03_evaluation.md](../../docs/inner_loop/03_evaluation.md),
  [04_reflection.md](../../docs/inner_loop/04_reflection.md),
  [05_episode_persistence.md](../../docs/inner_loop/05_episode_persistence.md).
- 현재 L0 enum은 `session.started`, `plan.completed`, `execution.*`,
  `evaluation.completed`, `reflection.completed`를 제공한다.
  [l0_events.py](../../src/forge/domain/memory/l0_events.py).
- Finalize는 정확히 하나의 terminal `execution.completed`와 L1의 전체 평가 metric,
  metric별 값 또는 reason을 요구한다.
  [finalize_episode.py](../../src/forge/application/memory/finalize_episode.py),
  [models.py](../../src/forge/domain/memory/models.py).
- 문서의 오래된 snake_case 이벤트명(`session_started`, `plan_created`,
  `evaluation_completed`)은 실제 enum의 dot notation과 맞지 않는다. 구현 단계에서 문서를
  `session.started`, `plan.completed`, `evaluation.completed`로 통일한다.

## 범위와 비범위

포함:

- L1 검색 결과를 선택적으로 계획 context에 넣는 read-only memory use case
- 명시적인 plan·tool execution·evaluation·reflection port와 기본 구현
- 재시도, 중단, L0 event producer, L1 Finalize 연결
- CLI 또는 Python facade에서 Inner Loop를 한 번 실행하는 경로

제외:

- L2/L3/L4/L5의 실제 저장소 및 헌법 판정 엔진
- 장기 background worker, 분산 queue, 병렬 tool scheduler
- 자동 Pattern Candidate 생성과 Outer/Meta Loop 실행
- 기존 일반 대화의 자동 도구 실행 전환

## 설계 결정

### 실행 경계

`InnerLoopService`는 application layer orchestration만 담당한다. 모델, 도구, L1 검색,
평가, 반성은 모두 port로 주입한다. 따라서 service는 LangChain/Chroma/CLI 세부사항을 모르며
deterministic fake port로 통합 테스트할 수 있다. `PlanStepExecutor`는 **한 번의 attempt만**
실행하고 retryable 결과를 반환한다. attempt 번호, retry budget/backoff 판단, L0 이벤트 기록은
오직 `InnerLoopService`가 소유한다.

```text
RunInnerLoopCommand
  → StartInnerLoopSessionService
  → SearchEpisodesService (best-effort context)
  → Planner port
  → PlanStepExecutor port (single attempt)
  → Evaluator port
  → Reflector port
  → FinalizeEpisodeService → PersistEpisodeService/Repository
  → InnerLoopResult
```

### 최소 domain/application 계약

새 domain 값은 provider payload가 아니라 아래의 작은 실행 계약으로 둔다.

- `InnerLoopPlan`: 안전한 summary, step tuple, 선택적 `pattern_candidate_id`
- `PlanStep`: stable step ID, action summary, 선택적 tool name, retry 허용 여부
- `ToolExecution`: step ID, summary, outcome(`completed`/`failed`/`halted`), tool names,
  safe error class, retryable 여부. terminal 여부는 step attempt 결과가 아니라 loop aggregate에만 둔다.
- `EvaluationInput`/`ReflectionInput`: evaluator·reflector가 L1의 전체 필드를 생성하는 데
  필요한 실행 집계와 근거 event ID
- `InnerLoopResult`: session/episode ID, terminal outcome, L1 persist result, event IDs

ports는 `MemoryContextProvider`, `InnerLoopPlanner`, `PlanStepExecutor`,
`InnerLoopEvaluator`, `InnerLoopReflector`로 나눈다. 첫 구현에서는 L1 search만
`MemoryContextProvider`의 실제 adapter로 연결하고, 계획/평가/반성은 deterministic
규칙 기반 adapter와 테스트 fake를 제공한다. LLM 기반 producer는 그 다음 단계에서 별도
adapter로 대체한다.

### execution terminal 계약

step attempt 이벤트와 L1에 매핑되는 loop 최종 실행을 분리한다.

- `execution.started`: 한 step attempt 직전 기록한다.
- `execution.completed`: 해당 attempt가 성공했음을 기록하며 `is_terminal`을 갖지 않는다.
- `execution.failed`: 해당 attempt가 실패했음을 기록하며 `is_terminal`을 갖지 않는다.
- `execution.summary.completed`: 모든 step·retry를 집계한 loop 최종 실행 이벤트다. **정확히 한 건**을
  기록하며 `summary`, `outcome`, `tool_names`, `attempt`, `is_terminal=true`를 가진다. outcome은
  `completed`·`failed`·`halted` 모두 가능하다.

따라서 retry budget 소진·안전 중단·planner failure를 포함한 모든 Finalize 가능 session은
`execution.summary.completed`를 한 번 기록한다. Finalize reducer는 기존
`execution.completed(is_terminal=true)` 대신 이 이벤트를 유일한 terminal source로 사용한다.
이 변경을 위해 `L0EventType`과 `FinalizeEpisodeService`를 함께 변경한다.

### Finalize 가능 조건

Finalize는 다음 네 근거가 모두 있을 때만 실행한다.

1. `session.started` 한 건
2. `execution.summary.completed` 한 건
3. `evaluation.completed` 한 건
4. `reflection.completed` 한 건

planner가 계획을 만들기 전에 실패해도 Inner Loop는 synthetic loop aggregate
(`outcome=failed`, 빈 tool tuple, safe failure summary)를 생성하고 deterministic evaluator와
reflector를 실행할 수 있다. 이 경우에도 위 네 조건을 만족하면 실패 Episode를 저장한다.
반대로 evaluator/reflector 자체가 생성되지 못했거나 aggregate 생성 전 process가 중단되면,
`session.failed`만 기록하고 L1 Episode는 만들지 않는다.

`plan.completed`는 Finalize의 필수 근거가 아니다. planner failure의 정상 복구 경로는 다음과
같으며, 계획 이벤트가 없다는 사실 자체가 안전한 실패 결과를 나타낸다.

```text
session.started
→ execution.summary.completed(outcome=failed)
→ evaluation.completed
→ reflection.completed
→ episode.persisted | episode.index_deferred
→ session.completed
```

planner failure 때 memory context 상태를 별도 이벤트로 추가하지 않는다. retryable context
fallback 여부와 safe error code는 synthetic aggregate summary에 넣고, orchestration 자체가
복구 불가하게 끝난 경우에는 `session.failed` payload에 넣는다.

`session.completed`는 orchestration·기록·Finalize가 정상 종료됐음을 뜻하며, Episode execution
outcome이 `completed`, `failed`, `halted` 중 무엇인지는 구분하지 않는다. `session.failed`는
aggregate/evaluation/reflection/Finalize의 필수 조건을 충족하지 못해 orchestration 자체가
완료되지 못했을 때만 기록한다.

### memory context 실패 정책

L1 context search는 오류 타입별로만 best-effort를 적용한다.

- `RetryableMemoryOperationError`: empty context로 계속하며 `plan.completed`에
  `memory_context_status="unavailable"`과 safe error code를 기록한다.
- `MemoryInfrastructureError`, `MemoryValidationError`: session을 중단하고 safe
  `session.failed`를 남긴다.
- 예상하지 못한 예외: 숨기지 않고 전파한다.

검색 성공 시 `plan.completed`에는 `memory_context_status="available"`,
`memory_context_error_code=null`, `retrieved_episode_ids`를 구조화 payload로 기록한다.

### L0 producer 계약

| 단계 | 실제 이벤트 | 핵심 payload |
| --- | --- | --- |
| 시작 | `session.started` | `task_request`, `task_category`, `settings_ref` |
| 계획 | `plan.completed` | summary, step/tool 목록, retrieved episode IDs, `pattern_candidate_id` |
| 각 step 시작 | `execution.started` | step ID, action summary, attempt, tool name |
| 각 step 결과 | `execution.completed` / `execution.failed` | step ID, summary, `tool_names`, attempt, retryable, safe error class |
| 실행 집계 | `execution.summary.completed` | summary, outcome, ordered-unique `tool_names`, total attempt, `is_terminal=true` |
| 평가 | `evaluation.completed` | 11 metrics, CIB status, status, retryable, eligibility, reasons, evidence completeness |
| 반성 | `reflection.completed` | 네 reflection field |
| Finalize 결과 | 기존 Finalize가 기록 | `episode.persisted` 또는 `episode.index_deferred`, 뒤이어 `session.completed` |

실패가 복구 불가하거나 retry budget을 소진했을 때도 `execution.summary.completed`, evaluation,
reflection을 반드시 만든다. 이는 실패 경험을 L1에 보존하되 CIB/근거 정책에 따라
`quarantined`로 분리하기 위함이다.

## 구현 단계

1. **문서·설정 계약 정리**
   - `docs/inner_loop/00`~`05`의 이벤트명을 실제 dot notation으로 통일하고, 단계별 payload
     표를 위 계약으로 보강한다.
   - `config/agent.yml`의 `inner_loop`에 step timeout, retry backoff, memory context top-k,
     LLM producer 사용 여부를 명시한다. 기본값은 deterministic mode로 둔다.

2. **Inner Loop domain 및 port 추가**
   - `src/forge/domain/inner_loop/`에 command/result/plan/step/execution 값을 추가한다.
   - `src/forge/ports/outbound/`에 planner, executor, evaluator, reflector, context provider
     protocol을 추가하고 public export를 갱신한다.
   - Evaluator payload factory는 모든 nullable L1 metric이 값 또는
     `EvaluationReason(metric, reason_code, detail)` 중 정확히 하나를 갖게 보장한다.

3. **Application orchestrator와 L0 event producer 구현**
   - `src/forge/application/inner_loop/run_inner_loop.py`에서 Start → context/plan → single-attempt
     execution 반복/retry → execution aggregate → evaluation → reflection → Finalize 순서를 구현한다.
   - `RecordInnerLoopEventService`만으로 node 이벤트를 추가하며 `causation_id`는 직전
     관련 event ID로 연결한다.
   - planner/실행 오류는 synthetic aggregate·평가·반성을 만들 수 있을 때만 Finalize한다.
     그 외에는 `session.failed`만 기록한다. L1 저장 오류는 기존
     `FinalizeEpisodeService`의 `episode.failed` 정책을 그대로 사용한다.

4. **기본 adapter와 L1 context 연결**
   - 규칙 기반 planner/executor/evaluator/reflector를 `adapters/outbound/inner_loop/`에 둔다.
     executor는 allowlisted no-op/echo 검증 도구만 제공하고 임의 shell 실행은 하지 않는다.
   - `SearchEpisodesService`를 context provider로 감싸며, index의 **retryable** 일시 장애만
     context 없이 계속 진행한다. infrastructure/validation 오류는 session을 중단한다.
   - container에 `build_inner_loop_service()`를 추가하고 L0 store, L1 services, default
     adapters를 조립한다.

5. **명시적 inbound 경로 추가**
   - 기존 chat CLI와 분리된 `forge inner-loop run` Click command 또는 inbound facade를
     추가한다. 입력은 request, task category, deterministic/LLM producer mode로 제한한다.
   - 결과는 사용자용 summary와 session/episode ID만 출력한다. L0 JSONL 원문과 예외 cause는
     콘솔에 노출하지 않는다.

6. **관측성·회복·문서 완료**
   - 구조화 로그에 session ID, episode ID, terminal outcome, retry count, index state만
     기록한다.
   - session 재실행은 새 session ID를 만들고, 같은 event ID append 재시도만 idempotent로
     처리한다. 중단된 기존 session resume은 별도 후속 기능으로 남긴다.
   - README에 실행 예시, 데이터 경로, 실패/재시도 의미를 추가한다.

## 수용 기준

1. 하나의 성공 실행은 순서대로 `session.started` → `plan.completed` → execution attempt events →
   `execution.summary.completed` →
   `evaluation.completed` → `reflection.completed` → `episode.persisted` →
   `session.completed`를 남긴다.
2. L1 `raw_event_refs`는 Finalize 이전의 근거 event ID만 sequence 순으로 포함하며,
   persistence/session 종료 이벤트는 포함하지 않는다.
3. 여러 step과 실패 후 재시도에서 유일한 execution aggregate의 summary/outcome과 ordered-unique tool
   names가 L1에 정확히 반영된다.
4. evaluator가 만든 모든 11 metrics는 value/reason 상호배타 규칙과 CIB/status/eligibility
   규칙을 만족한다.
5. recoverable tool 실패는 설정된 budget 안에서 retry하고, budget 소진 또는 안전 중단은
   terminal `failed`/`halted` Episode와 safe event payload를 남긴다.
6. L1 검색 불가 시에도 계획은 empty context로 계속되며, L1 저장 불가 시 기존 L0는
   보존되고 `episode.failed`가 남는다.
7. 기존 `forge` 대화 CLI 회귀가 없다.

## 테스트 계획

- **Domain unit**: plan/step ID, 허용 tool, evaluation metric/reason factory, retry decision,
  status/CIB mapping.
- **Application unit**: port fake로 정상, retry-success, nonretryable failure, safe halt,
  planner failure의 synthetic aggregate, retryable search fallback, infrastructure search stop,
  Finalize failure를 각각 검증. planner failure는 `plan.completed` 없이 aggregate·평가·반성·
  실패 Episode·`session.completed`를 남기는지, evaluator/reflector 실패는 L1 저장 없이
  `session.failed`만 남기는지를 별도 test로 고정한다.
- **Integration**: temp JSONL + SQLite/Chroma fake로 event 순서, causation chain,
  raw refs, index deferred, session failure를 검증.
- **CLI smoke**: deterministic `inner-loop run`이 session/episode ID와 요약을 출력하고
  기존 conversation command가 그대로 동작하는지 검증.
- **회귀**: `.venv/bin/ruff check src/forge tests/forge` 및
  `.venv/bin/pytest tests/forge -q`.

## 위험과 완화

| 위험 | 완화 |
| --- | --- |
| 문서와 enum/payload 계약 불일치 | 구현 전 문서 표를 single source로 정리하고 contract test 추가 |
| LLM 기반 평가의 비결정성 | deterministic producer부터 도입하고 LLM adapter는 schema validation 뒤에만 사용 |
| tool 실행이 권한 범위를 넓힘 | 첫 adapter는 allowlist/no-op으로 제한, 임의 shell/네트워크 실행은 별도 승인 기능으로 분리 |
| 평가 payload 누락으로 Finalize 실패 | evaluator factory가 모든 nullable metric의 값/근거를 생성하고 unit test로 고정 |
| L1 검색 장애가 작업 자체를 막음 | read context는 best-effort, save/finalize만 typed failure 정책을 적용 |

## ADR

**결정:** 기존 conversation runtime과 분리된, port 기반의 deterministic Inner Loop를 먼저
도입한다.

**Drivers:** L0/L1 계약을 실제 실행으로 검증해야 하고, 도구 실행·평가·반성은 현재
conversation 기능에 없는 새로운 권한/안전 경계이기 때문이다.

**대안:** (1) 기존 LangGraph conversation graph에 노드를 바로 추가한다 — 일반 대화가
실행·저장 부작용을 갖게 되어 회귀 및 권한 위험이 크다. (2) 처음부터 LLM planner/evaluator를
연결한다 — payload와 평가가 비결정적이라 L0/L1 contract 문제를 분리하기 어렵다.

**결과:** 첫 사용성은 제한적이지만, deterministic end-to-end 근거를 만든 뒤 LLM과 실제
tool adapter를 안전하게 교체할 수 있다.

**후속:** 위 수용 기준이 통과한 뒤에만 LLM planner/reflector와 resume 기능을 별도 계획으로
추가한다.
