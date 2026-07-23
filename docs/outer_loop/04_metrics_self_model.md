# 4. 최근 건강도와 L5 통계 재계산

## 책임

최근 N개 Episode로 현재 건강도를 계산하고, L5의 통계적 자기 모델만 갱신한다.
권한이나 자율성 단계는 갱신하지 않는다.

## 의사 코드

```text
function recalculate_health_and_self_model(state, config):
    window = query_recent_l1(n = state.current_N, include_quarantined = true)
    metrics = {
        success_rate: mean(window.success_score),
        avg_pain_index: mean(window.pain_index),
        avg_cib_score: mean(window.cib_score),
        status_distribution: count_by(window.status),
        coherence_index: calculate_coherence(window),
        volatility: calculate_metric_volatility(window)
    }

    self_model_update = recalculate_l5_statistics(
        episodes = window,
        metrics = metrics,
        source = "outer_loop"
    )

    record_metrics(metrics)
    return metrics, self_model_update
```

## 규칙

- `success_score`는 품질·요구사항 충족·검증 신뢰도의 종합값이다.
- `pain_index`는 비용·오류·재시도 마찰이며 성공도의 역수가 아니다.
- CIB 실패를 포함한 최근 건강도는 안전 위험을 보여 주지만, 승격 근거에는
  `eligible` Episode만 사용한다.
