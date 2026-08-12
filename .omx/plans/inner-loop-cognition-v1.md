# Inner Loop Cognition v1 계획

## 요구사항 요약

`src/forge`에 독립된 Cognition 경계를 추가한다. 범위는 **Inner Loop의 인지 책임**으로 한정한다.

- 포함: 실행 문맥 구성, 계획 생성/재계획, 실행 결과 해석, 다음 전이 결정, 반성 생성
- 제외: L1/L2/L3 검색·주입, L4 CIB 평가 엔진, L5 권한 엔진, Outer/Meta Loop
- 기존 LangGraph Inner Loop의 L0 이벤트 순서, 재시도 한도, 계획 의존성 검증, L1 Episode 확정 계약은 유지한다.

## 현재 근거

- `RunInnerLoopService`가 그래프 조립과 plan/evaluate/reflect의 인지 책임을 함께 소유한다. [`src/forge/application/inner_loop/run_inner_loop.py:91`](../../src/forge/application/inner_loop/run_inner_loop.py:91), [`:112`](../../src/forge/application/inner_loop/run_inner_loop.py:112), [`:286`](../../src/forge/application/inner_loop/run_inner_loop.py:286)
- Planner/Evaluator/Reflector port는 있으나, context·reasoning·decision port는 없다. [`src/forge/ports/outbound/inner_loop.py:16`](../../src/forge/ports/outbound/inner_loop.py:16)
- 재시도 및 재계획 판단은 orchestration service 내부에 하드코딩돼 있다. [`src/forge/application/inner_loop/run_inner_loop.py:274`](../../src/forge/application/inner_loop/run_inner_loop.py:274)
- 현재 deterministic adapter가 planner/evaluator/reflector의 기본 구현이다. [`src/forge/adapters/outbound/inner_loop/deterministic.py:13`](../../src/forge/adapters/outbound/inner_loop/deterministic.py:13)

## 목표 아키텍처

```text
RunInnerLoopService (LangGraph + L0/L1 lifecycle)
  -> InnerLoopCognition (application boundary)
       -> ContextBuilder       # 현재 실행 상태만 정규화; 메모리 검색 없음
       -> Planner              # 기존 InnerLoopPlanner / FeedbackAwareInnerLoopPlanner 재사용
       -> Reasoner             # 실행 + 평가를 인지용 사실로 해석
       -> DecisionPolicy       # retry / replan / finalize 중 다음 전이 결정
       -> Reflector            # 기존 InnerLoopReflector 재사용
```

`RunInnerLoopService`는 상태 전이·이벤트 기록·최종 저장만 맡는다. Cognition은 외부 I/O나 L0/L1 쓰기를 하지 않고, 순수 값 객체와 기존 outbound port로 결과를 반환한다.

## 구현 단계

1. **Cognition 도메인 계약 추가**
   - 새 파일: `src/forge/domain/cognition/models.py`, `src/forge/domain/cognition/__init__.py`
   - 새 값: `InnerLoopContext`(요청, 현재 계획, 단계 상태, attempt/retry/feedback 한도), `ReasonedExecution`(실행 outcome·재시도 가능성·안전 오류), `CognitionDecision` enum(`RETRY`, `REPLAN`, `SUMMARIZE`) 및 `CognitionResult`.
   - 불변 조건: 안전 프로토콜 오류(`tool.protocol_failure`)는 재계획 불가, 한도 초과 시 재시도/재계획 불가, 완료 단계는 재계획 결과에서 보존한다.
   - 테스트: 값 검증과 불변 조건을 `tests/forge/domain/cognition/test_models.py`에 추가한다.

2. **Cognition port와 application service 도입**
   - 새 파일: `src/forge/ports/outbound/cognition.py`, `src/forge/application/cognition/inner_loop_cognition.py` 및 각 `__init__.py`.
   - `ContextBuilder`, `ExecutionReasoner`, `DecisionPolicy` protocol을 추가한다. Planner와 reflector는 기존 `InnerLoopPlanner`, `FeedbackAwareInnerLoopPlanner`, `InnerLoopReflector`를 재사용한다.
   - `InnerLoopCognition`은 `build_context`, `create_plan`, `create_replan`, `reason`, `decide`, `reflect`의 좁은 API를 제공한다. I/O와 이벤트 기록은 금지한다.
   - 테스트: fake planner/executor 결과로 context 전달, feedback 보존, 판단 결과를 `tests/forge/application/cognition/test_inner_loop_cognition.py`에서 단위 검증한다.

3. **기본 인지 구현을 deterministic adapter로 제공**
   - 새 파일: `src/forge/adapters/outbound/inner_loop/cognition.py`.
   - `DeterministicContextBuilder`는 요청·현재 상태·설정 한도를 `InnerLoopContext`로 정규화한다.
   - `DeterministicExecutionReasoner`는 `ToolExecution`을 `ReasonedExecution`으로 변환한다. `DeterministicDecisionPolicy`는 현재 `_should_replan` 및 retry 조건과 동등한 결정을 반환한다.
   - 기존 [`deterministic.py`](../../src/forge/adapters/outbound/inner_loop/deterministic.py)는 Planner/Evaluator/Reflector 구현을 유지한다. 동작 중복을 피하고, 조건문은 새 decision policy로 한 번만 옮긴다.
   - 테스트: completed, retryable failure, non-retryable failure, protocol failure, feedback-budget exhaustion을 표 기반 단위 테스트로 고정한다.

4. **`RunInnerLoopService`를 orchestration 전용으로 축소**
   - 수정: `src/forge/application/inner_loop/run_inner_loop.py`.
   - 생성자에 `InnerLoopCognition`을 주입한다. `_plan`, `_execute_attempt`, `_next_after_attempt`, `_evaluate`, `_reflect`는 cognition 결과를 사용하되, 그래프 노드명·L0 event schema·`InnerLoopState` 공개 의미는 유지한다.
   - `_should_replan`과 planner feedback 구성 책임을 cognition으로 이동한다. `_merge_replan`, 의존성 검증, 단계 상태 업데이트는 orchestration에 남긴다.
   - 테스트: 기존 `tests/forge/application/inner_loop/test_run_inner_loop.py`의 이벤트 순서 및 재계획 테스트를 유지하고, decision 결과가 `RETRY`/`REPLAN`/`SUMMARIZE`로 그래프 edge에 정확히 매핑되는 통합 테스트를 추가한다.

5. **Composition root와 설정 경계 연결**
   - 수정: `src/forge/bootstrap/container.py`, `src/forge/adapters/outbound/inner_loop/__init__.py`, `src/forge/ports/outbound/__init__.py`.
   - `build_inner_loop_service()`에서 cognition service와 deterministic context/reasoner/decision adapter를 조립한다. 기존 `inner_loop.max_retries`, `max_feedback_cycles`, `max_tool_feedback_bytes`를 단일 configuration source로 cognition에 전달한다.
   - `config/agent.yml`에는 새 수치 설정을 추가하지 않는다. 의미가 이미 있는 현재 세 한도를 재사용해 중복 설정을 막는다.
   - 테스트: composition test가 기본 cognition 구현을 주입하고 config 한도를 반영하는지 확인한다.

6. **문서 및 회귀 검증 완료**
   - 수정: `docs/PROJECT_STATUS.md`의 2.2를 “Inner Loop Cognition v1 완료; 메모리 기반 context와 L2~L5 인지는 후속”으로 갱신한다.
   - 수정: `docs/inner_loop/README.md`, `docs/inner_loop/01_context_planning.md`에서 이번 범위가 실행 상태 문맥이지 메모리 검색이 아님을 명시한다.
   - 실행: 관련 domain/application/bootstrap tests → 전체 `pytest` → `ruff check src tests` → `mypy src`.

## 수용 기준

1. `src/forge/domain/cognition`, `application/cognition`, `ports/outbound/cognition.py`가 존재하고 각 책임이 독립적으로 테스트된다.
2. `RunInnerLoopService`는 Cognition API를 통해 계획, 재계획, 판단, 반성을 수행하며 L0/L1 lifecycle을 직접 변경하지 않는다.
3. 기존 완료·재시도·재계획·프로토콜 오류 중단 시나리오의 outcome 및 L0 이벤트 순서가 그대로 유지된다.
4. `tool.protocol_failure`는 재계획하지 않으며, retry/feedback 한도 초과 시 terminal 경로로 전이한다.
5. L1 repository/search service 및 L2~L5 코드/설정은 변경하지 않는다.
6. 새 unit/integration tests와 전체 lint/typecheck가 통과한다. 환경 의존 mypy failure가 있으면 project-code 결과와 외부 stubs failure를 분리해 보고한다.

## 위험과 완화

| 위험 | 완화 |
| --- | --- |
| 기존 그래프 동작 변경 | 기존 Inner Loop 재계획 테스트를 보존하고 decision 표 테스트를 먼저 작성한다. |
| Cognition과 orchestration의 책임 중복 | Cognition은 판단 값만 반환하고, 이벤트·저장·상태 mutation은 `RunInnerLoopService`에 남긴다. |
| L1 문맥 주입으로 범위가 확대됨 | `ContextBuilder` 입력을 Inner Loop state로 한정하고 `EpisodeRepository` 의존성을 추가하지 않는다. |
| 안전 예외가 재계획 경로로 새어 나감 | `tool.protocol_failure`를 decision contract의 불변 금지 조건으로 테스트한다. |

## 검증 순서

1. `pytest tests/forge/domain/cognition tests/forge/application/cognition`
2. `pytest tests/forge/application/inner_loop/test_run_inner_loop.py tests/forge/bootstrap/test_container.py`
3. `pytest`
4. `ruff check src tests`
5. `mypy src`

## 종료 조건

Inner Loop 인지 책임이 독립 경계로 분리되고, 메모리 계층을 건드리지 않은 상태에서 기존 실행·재계획·반성·L1 Episode 확정 계약이 테스트로 보장되면 완료다.
