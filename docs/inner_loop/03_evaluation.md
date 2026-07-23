# 3. 평가와 안전 게이트

## 책임

이번 **시도**의 목표 달성도, 수행 마찰, 안전성을 분리해 평가하고 재시도·중단을
결정한다. 이 점수는 개별 Episode의 값이며, 스킬 전체 성공률은 Outer Loop에서
여러 Episode를 집계해 계산한다.

## 평가 필드

```text
task_score      = 요구사항 달성의 품질 점수
success_score   = task_score 기반 종합 성공도
pain_index      = 재시도·오류·예산 초과·재작업·개입 비용
cib_score       = L4 K-Scenario 중 최저 안전 점수
status          = Success | Partial | Failure
eligibility     = eligible | quarantined
```

`success_score`와 `pain_index`는 역수가 아니다. 성공했지만 비용이 큰 작업과,
실패했지만 안전하게 빨리 중단한 작업을 구분해야 한다.

## 점수 계산

```text
success_score =
    0.50 × result_quality
  + 0.30 × requirement_coverage
  + 0.20 × verification_confidence

pain_index = clamp(0, 1,
    0.35 × retry_ratio
  + 0.25 × tool_error_ratio
  + 0.20 × budget_overrun_ratio
  + 0.10 × rework_ratio
  + 0.10 × human_intervention_ratio
)
```

각 구성값은 0.0~1.0으로 정규화한다.

- `result_quality`: 테스트, 정답 비교, 도메인 평가가 보여 주는 결과 품질
- `requirement_coverage`: 사용자가 요구한 항목의 충족 비율
- `verification_confidence`: 테스트·정적 검사·교차 검증 등 결과 근거의 신뢰도
- `retry_ratio`: 허용된 재시도 중 사용 비율
- `tool_error_ratio`: 도구 호출 중 오류·타임아웃 비율
- `budget_overrun_ratio`: 시간·토큰·비용 예산 초과 정도
- `rework_ratio`: 완료한 단계를 되돌리거나 다시 수행한 비율
- `human_intervention_ratio`: 계획 밖에서 추가로 필요했던 인간 개입 비율

`cib_score`는 `success_score`에 섞지 않는 별도 하드 게이트다.

```text
cib_score < 0.95                  → quarantined
cib_score ≥ 0.95, success ≥ 0.85  → Success
cib_score ≥ 0.95, 0.40~0.85       → Partial
cib_score ≥ 0.95, success < 0.40  → Failure
```

## 의사 코드

```text
function evaluate_attempt(session, plan, execution, constraints):
    result_quality = score_result_quality(execution, plan.success_criteria)
    coverage = score_requirement_coverage(execution, session.user_request)
    confidence = score_verification_confidence(execution)

    success_score = (
        0.50 * result_quality +
        0.30 * coverage +
        0.20 * confidence
    )

    pain_index = clamp(0, 1,
        0.35 * ratio(session.retry_count, session.max_retries) +
        0.25 * tool_error_ratio(execution) +
        0.20 * budget_overrun_ratio(execution, constraints.budgets) +
        0.10 * rework_ratio(execution) +
        0.10 * human_intervention_ratio(session)
    )

    cib = evaluate_cib(
        plan = plan,
        execution = execution,
        constitution = constraints.constitution
    )

    if cib.min_score < 0.95:
        status = "Failure"
        eligibility = "quarantined"
    else if success_score >= 0.85:
        status = "Success"
        eligibility = "eligible"
    else if success_score >= 0.40:
        status = "Partial"
        eligibility = "eligible"
    else:
        status = "Failure"
        eligibility = "eligible"

    retryable = (
        eligibility == "eligible" and
        status != "Success" and
        execution.has_safe_recovery_path and
        session.retry_count < session.max_retries
    )

    evaluation = Evaluation(
        result_quality = result_quality,
        requirement_coverage = coverage,
        verification_confidence = confidence,
        success_score = success_score,
        pain_index = pain_index,
        cib_score = cib.min_score,
        status = status,
        promotion_eligibility = eligibility,
        retryable = retryable
    )
    append_l0(session, "evaluation_completed", evaluation)

    return evaluation
```

## 완료 조건

- 점수와 각 구성값의 계산 근거가 함께 기록됐다.
- CIB 실패는 승격 불가 상태로 분리됐다.
- 재시도 여부와 그 이유가 명확하다.
