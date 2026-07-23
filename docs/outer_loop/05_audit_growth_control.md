# 5. 독립 감사와 성장 속도 제어

## 책임

에이전트의 자기 평가와 독립 평가의 편차를 점검하고, 성능·코히어런스의 급격한
변화가 있을 때 학습을 제한하거나 Meta Loop 검토를 요청한다.

## 의사 코드

```text
function audit_and_regulate(metrics, self_model, state, policy):
    pairs = load_episode_score_pairs(
        fields = ["success_score", "phoenix_score"]
    )
    audit = independent_audit(pairs)
    // 평균 |success_score - phoenix_score| 및 편향 방향

    growth = evaluate_growth_rate(
        previous = state.previous_metrics,
        current = metrics,
        audit = audit,
        thresholds = policy.thresholds
    )

    if growth.is_crash or metrics.avg_cib_score < policy.min_cib:
        suspend_new_learning()
        request_safety_review(growth)
    else if growth.is_unstable:
        slow_or_freeze_promotions()

    record_audit_and_growth(audit, growth)
    return audit, growth
```

## 규칙

- 감사 편차는 자기 모델의 캘리브레이션 통계에는 반영할 수 있다.
- 감사 실패가 L4/L5 변경 권한을 직접 부여하거나 변경하지는 않는다.
- 학습 중단은 새 승격을 막는 안전 조치이며 L1 기록 자체를 멈추지 않는다.
