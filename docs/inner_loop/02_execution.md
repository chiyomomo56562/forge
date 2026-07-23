# 2. 실행과 L0 이벤트 기록

## 책임

계획을 수행하고, 재구성 가능한 순서의 원본 이벤트를 L0에 남긴다. 실행 중
위험이 감지되면 더 진행하지 않고 평가 단계로 제어를 넘긴다.

## 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| Plan, Session, Constraints, Tool Registry | Execution Result, 순서화된 L0 이벤트 |

## 의사 코드

```text
function execute_plan_with_logging(session, plan, constraints, tools):
    results = []

    for step in plan.steps:
        authorization = authorize(step, constraints)
        append_l0(session, "tool_call_requested", {
            step: step.summary,
            tool: step.tool,
            authorization: authorization
        })

        if not authorization.allowed:
            results.append(blocked_result(step, authorization.reason))
            break

        if detect_new_safety_risk(step, results, constraints):
            append_l0(session, "execution_halted", {
                step: step.summary,
                reason: "new safety risk"
            })
            results.append(halted_result(step))
            break

        result = tools.execute(step)
        append_l0(session, "tool_call_completed", {
            tool: step.tool,
            success: result.success,
            output_reference: store_large_output_if_needed(result.output),
            error: result.error
        })
        results.append(result)

        if result.is_fatal_error:
            break

    return summarize_execution(results)
```

## 기록 원칙

- 큰 원본 출력은 L0 이벤트에 직접 중복하지 않고 저장 위치를 참조한다.
- 도구 호출 전후 이벤트를 모두 남겨 실패·중단 원인을 재구성할 수 있게 한다.
- L0은 append-only다. 잘못된 이벤트도 삭제하지 않고 정정 이벤트를 추가한다.
