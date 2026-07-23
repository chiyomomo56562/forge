# 3. 사전 안전 검토

## 책임

제안을 실행하기 전에 변경안 자체가 L4 헌법을 위반하지 않는지, 기존 K-Scenario와
회귀 검증을 통과하는지, 승인 범위와 롤백이 충분한지 확인한다.

## 의사 코드

```text
function run_preflight_review(proposal):
    cib = evaluate_cib(
        plan_or_change = proposal.changes,
        constitution = load_current_constitution()
    )
    impact = evaluate_permission_and_dependency_impact(proposal)
    tests = run_proposal_validation_in_sandbox(proposal.validation_plan)
    rollback = verify_rollback_is_executable(proposal.rollback_plan)

    passed = (
        cib.min_score >= 0.95 and
        impact.within_declared_scope and
        tests.passed and
        rollback.available
    )

    return PreflightReview(
        passed = passed,
        cib = cib,
        impact = impact,
        tests = tests,
        rollback = rollback
    )
```

## 규칙

- 사전 검토가 실패한 제안은 승인 요청으로 보내지 않는다.
- L4 변경 제안은 변경 전 헌법으로만 평가하지 않고, 변경 후 K-Scenario가
  약화되지 않는지도 별도로 검토한다.
- 검증 불가능하거나 롤백 불가능한 변경은 범위를 줄이거나 반려한다.
