# Inner Loop 오케스트레이션

## 목적과 경계

Inner Loop는 하나의 사용자 요청을 안전하게 실행하고, 그 결과를 다음 작업에
참조할 수 있는 **L1 Episode 증거**로 남기는 빠른 실행 경로다.

Inner Loop가 하는 일은 다음으로 한정한다.

- 관련 기억을 선택적으로 읽고 계획을 만든다.
- 실행 과정의 원본을 L0에 기록한다.
- 결과와 안전성을 평가한다.
- 작업을 반성해 L1 Episode로 저장한다.
- L1 증거를 Pattern Candidate에 연결한다.

Inner Loop는 L1을 L2/L3으로 승격하지 않고, L4 헌법이나 L5 권한을 변경하지
않는다. 승격은 Outer Loop, 구조·정책 변경은 Meta Loop의 책임이다.

## 전체 흐름

```text
사용자 요청
  ↓
세션 생성 + L0 시작 이벤트 기록
  ↓
1. 컨텍스트 구성과 계획
  ↓
2. 실행과 L0 이벤트 기록
  ↓
3. 평가와 안전 게이트
  ├─ 통과 → 4. 반성
  └─ 재시도 가능 → 계획으로 돌아감
  └─ 재시도 불가 → 4. 반성 (실패/격리 결과 포함)
  ↓
4. 반성
  ↓
5. L1 Episode 저장 + Pattern Candidate 증거 연결
  ↓
응답 반환
```

## 공통 데이터

Inner Loop는 실행 내내 같은 `session_id`와 `episode_id`를 사용한다. 모든 L0
이벤트와 최종 L1 Episode는 이 ID를 공유해야 감사와 재구성이 가능하다.

```text
Session
  session_id
  episode_id
  user_request
  retry_count

L0 Event
  event_id, session_id, episode_id, sequence, timestamp, type, payload

L1 Episode
  episode_id, task, raw_event_refs, outcome, evaluation, reflection,
  promotion_eligibility
```

## 0. 세션 시작과 초기 기록

### 해야 할 일

1. 고유한 `session_id`, `episode_id`를 만든다.
2. 사용자 요청과 실행 환경을 L0에 `session_started` 이벤트로 기록한다.
3. L4 헌법과 L5 권한 범위를 읽어 이 세션의 변경 불가능한 제약으로 고정한다.
4. 도구 예산, 재시도 한도, 위험 수준을 세션 상태에 기록한다.

### 산출물

- 활성 세션
- 첫 L0 이벤트
- 실행 제약 집합

## 1. 컨텍스트 구성과 계획

### 해야 할 일

1. 요청과 의미적으로 관련된 L1 Episode를 검색한다. 반성 정보가 있는 Episode를
   우선한다.
2. 관련 L2 조건부 지식과 L3 검증된 스킬을 추가로 검색한다.
3. L4 헌법의 금지 사항·승인 요구를, L5의 현재 권한과 능력 한계를 계획의
   제약으로 적용한다.
4. 토큰·시간 예산 내에서 필요한 기억만 선택 주입한다.
5. 실행 단계, 사용할 도구, 예상 부작용, 중단 조건을 포함한 계획을 생성한다.
6. 계획 자체를 L0에 `plan_created` 이벤트로 기록한다.
7. 계획이 L4 제약을 만족하지 않으면 실행하지 않고, 안전한 대안 또는 거부
   응답을 계획으로 만든다.

### 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| 사용자 요청, L1/L2/L3 검색 결과, L4/L5 제약 | 실행 계획, 주입 컨텍스트, L0 계획 이벤트 |

## 2. 실행과 L0 이벤트 기록

### 해야 할 일

1. 계획의 각 단계를 순서대로 실행한다.
2. 매 도구 호출 전에는 도구 이름, 인자 요약, 권한 결정, 예상 위험을 L0에
   기록한다.
3. 호출 결과, 오류, 외부 부작용, 중간 산출물, 사용자 승인 여부를 L0에
   기록한다.
4. 실행 중 새 위험 또는 L4 위반 가능성이 발견되면 즉시 해당 행동을 중단하고
   평가 단계로 넘긴다.
5. 실행 결과를 사용자에게 반환할 수 있는 형태로 정리한다.

### 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| 실행 계획, 도구, 세션 제약 | 실행 결과, 순서화된 L0 이벤트 |

## 3. 평가와 안전 게이트

### 해야 할 일

1. 작업이 사용자의 성공 기준을 만족했는지 평가한다.
2. 이번 시도의 `success_score`, 성공/실패 상태, `Pain Index`를 기록한다.
3. 결과와 실제 실행 행동을 L4의 CIB/K-Scenario에 평가한다.
4. CIB, 품질, 재시도 가능성을 바탕으로 다음 중 하나를 결정한다.

| 결정 | 다음 행동 |
| --- | --- |
| 통과 | 반성 단계로 진행 |
| 수정 가능 실패 | 실패 원인을 L0에 기록하고 제한된 횟수 안에서 재계획·재실행 |
| 안전 실패 | 위험 행동을 차단하고 격리 상태로 반성·저장 |
| 최종 실패 | 실패 결과를 반성·저장하고 사용자에게 제한 사항을 설명 |

### 중요한 원칙

- CIB 실패는 결과를 유효한 학습·승격 증거로 만들지 못하게 한다.
- CIB 실패 Episode 자체는 삭제하지 않는다. 감사와 재발 방지를 위해 L1에
  `quarantined` 상태로 남긴다.
- 이 단계의 성공/실패는 **이번 시도**의 결과이지, L3 스킬 전체 성공률이 아니다.

### 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| 실행 결과, L0 이벤트, 성공 기준, L4 CIB | Evaluation, 재시도/중단 결정, L0 평가 이벤트 |

## 4. 반성

### 해야 할 일

실행 결과와 평가를 바탕으로 다음 네 필드를 만든다.

| 필드 | 질문 |
| --- | --- |
| `what_worked` | 무엇이 성공에 기여했는가? |
| `what_failed` | 무엇이 실패했거나 비용을 만들었는가? |
| `next_hint` | 다음 시도에서 무엇을 유지·피해야 하는가? |
| `causal_condition` | 어떤 조건에서 이 결과가 나왔는가? |

반성은 한 번의 사례를 압축하는 작업이다. “항상”, “모든 경우”처럼 일반 지식을
확정하는 표현을 피하고, 관측된 조건과 결과를 분명히 남긴다.

### 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| 사용자 요청, 실행 요약, Evaluation, L0 이벤트 | Reflection, L0 반성 이벤트 |

## 5. L1 Episode 저장과 후보 증거 연결

### 해야 할 일

1. L0 참조, 실행 요약, Evaluation, Reflection을 묶어 불변 L1 Episode를 만든다.
2. ChromaDB에는 검색용 문서와 필터 가능한 메타데이터를 `episode_id`로 저장한다.
3. CIB 실패 또는 미완료 Episode에는 `promotion_eligibility=quarantined`를 둔다.
4. CIB를 통과한 Episode는 조건·행동·결과를 기준으로 Pattern Candidate를
   생성하거나 기존 후보에 증거로 연결한다.
5. 이 단계에서는 후보의 누적 수치를 갱신할 수 있지만 L2/L3 승격 결정을 하지
   않는다.
6. L0에 `episode_persisted` 이벤트를 기록하고 세션을 종료한다.

### L1의 ChromaDB 저장 형태

```text
id: episode_id

document:
  작업, 결과, what_worked, what_failed, next_hint, causal_condition을
  검색 가능한 자연어 텍스트로 조합한 값

metadata:
  created_at, task_category, status, success_score, pain_index, cib_score,
  promotion_eligibility, pattern_candidate_id
```

L0 원본 전체나 중첩된 복잡한 평가 객체는 ChromaDB 메타데이터에 넣지 않는다.
L0는 append-only 로그에 두고, ChromaDB는 L1의 의미 검색과 필터링에 사용한다.

## 의사 코드

```text
function run_inner_loop(user_request):
    session = create_session(user_request)
    constraints = load_constraints(L4, L5)
    append_l0(session, "session_started", {
        request: user_request,
        constraints: constraints.summary
    })

    retry_count = 0
    final_execution = null
    final_evaluation = null

    while retry_count <= MAX_RETRIES:
        context = selectively_retrieve(
            query=user_request,
            layers=[L1, L2, L3],
            constraints=constraints,
            prefer_l1_reflections=true,
            token_budget=SESSION_TOKEN_BUDGET
        )

        plan = make_plan(user_request, context, constraints)
        append_l0(session, "plan_created", {plan: plan})

        if not preflight_cib_passes(plan, constraints.L4):
            final_execution = blocked_execution("계획이 헌법 제약을 위반함")
            final_evaluation = evaluate_blocked_plan(plan, constraints.L4)
            break

        execution = execute_plan_with_l0_logging(plan, session, constraints)
        evaluation = evaluate(
            request=user_request,
            execution=execution,
            l0_events=session.events,
            constitution=constraints.L4
        )
        append_l0(session, "evaluation_completed", {evaluation: evaluation})

        final_execution = execution
        final_evaluation = evaluation

        if evaluation.cib_passed and evaluation.quality_passed:
            break

        if evaluation.retryable and retry_count < MAX_RETRIES:
            retry_count += 1
            append_l0(session, "retry_requested", {
                retry_count: retry_count,
                reason: evaluation.failure_reason
            })
            continue

        break

    reflection = reflect(
        task=user_request,
        execution=final_execution,
        evaluation=final_evaluation
    )
    append_l0(session, "reflection_completed", {reflection: reflection})

    eligibility = (
        "eligible"
        if final_evaluation.cib_passed
        else "quarantined"
    )

    episode = create_l1_episode(
        episode_id=session.episode_id,
        task=user_request,
        raw_event_refs=session.event_ids,
        execution=final_execution,
        evaluation=final_evaluation,
        reflection=reflection,
        promotion_eligibility=eligibility
    )
    store_l1_in_chroma(episode)

    if eligibility == "eligible":
        candidate = find_or_create_pattern_candidate(
            condition=reflection.causal_condition,
            action=reflection.what_worked,
            outcome=episode.outcome
        )
        link_episode_evidence(candidate, episode)

    append_l0(session, "episode_persisted", {
        episode_id: episode.id,
        promotion_eligibility: eligibility
    })
    end_session(session)

    return make_response(final_execution, final_evaluation)
```

## 완료 조건

Inner Loop 한 회차는 다음 조건을 만족하면 완료다.

- 사용자에게 실행 결과 또는 안전한 실패 설명을 반환했다.
- 계획·실행·평가·반성의 핵심 이벤트가 L0에 남아 있다.
- Evaluation과 Reflection을 가진 L1 Episode가 저장됐다.
- Episode가 승격 가능 또는 격리 상태로 명확히 분류됐다.
- 승격 가능 Episode는 Pattern Candidate에 연결됐거나, 연결할 수 없었던 이유가
  L0에 기록됐다.
