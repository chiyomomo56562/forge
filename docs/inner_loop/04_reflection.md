# 4. 반성

## 책임

한 작업 결과를 조건·행동·결과 중심으로 압축해, 나중에 검색하고 패턴 증거로
사용할 수 있는 L1 반성을 만든다. 반성은 일반 지식을 확정하지 않는다.

## 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| 사용자 요청, Execution Result, Evaluation, L0 이벤트 | Reflection, `reflection_completed` L0 이벤트 |

## 필수 필드

| 필드 | 의미 |
| --- | --- |
| `what_worked` | 성공에 기여한 행동 또는 선택 |
| `what_failed` | 실패·오류·불필요한 비용의 원인 |
| `next_hint` | 다음 시도에서 유지하거나 피할 구체적 힌트 |
| `causal_condition` | 결과가 발생한 환경·입력·권한·도구 상태 |

## 의사 코드

```text
function create_reflection(session, execution, evaluation):
    evidence = select_relevant_l0_events(session.events, execution)

    reflection = Reflection(
        what_worked = describe_effective_actions(
            execution, evaluation, evidence
        ),
        what_failed = describe_failures_and_costs(
            execution, evaluation, evidence
        ),
        next_hint = create_next_attempt_hint(
            execution, evaluation, evidence
        ),
        causal_condition = describe_conditions(
            session.user_request, execution, evidence
        )
    )

    reflection = validate_reflection(
        reflection,
        rules = ["specific", "evidence-grounded", "not-universal"]
    )

    append_l0(session, "reflection_completed", {reflection: reflection})
    return reflection
```

## 품질 규칙

- “항상”, “모든 경우” 같은 일반화 표현을 피한다.
- 관찰된 조건 없이 행동만 기록하지 않는다.
- 실패가 없더라도 실제로 확인하지 못한 한계를 `what_failed` 또는 `next_hint`에
  남긴다.
- CIB 실패라면 위반 가능 행동과 차단 이유를 명확히 기록한다.
