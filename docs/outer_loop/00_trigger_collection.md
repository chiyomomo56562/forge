# 0. 트리거 확인과 증분 수집

## 책임

위험도별 N, 긴급 신호, 마지막 통합 워터마크를 기준으로 이번 Outer Loop 실행이
필요한지 판단하고, 새로 확정된 L1 Episode만 수집한다.

## 의사 코드

```text
function start_outer_loop(state, config):
    pending_count = count_l1_after(state.consolidation_watermark)
    emergency_signal = read_emergency_signal()

    if pending_count < state.current_N and not emergency_signal:
        return NotDue(reason="N에 도달하지 않음")

    new_episodes = query_l1(
        created_after = state.consolidation_watermark,
        order_by = "created_at ASC"
    )
    eligible = filter(new_episodes, ep =>
        ep.promotion_eligibility == "eligible"
    )
    quarantined = filter(new_episodes, ep =>
        ep.promotion_eligibility == "quarantined"
    )

    return OuterLoopInput(
        all_new_episodes = new_episodes,
        eligible_episodes = eligible,
        quarantined_episodes = quarantined,
        prior_watermark = state.consolidation_watermark
    )
```

## 규칙

- 격리 Episode는 감사·안전 분석에는 사용하지만 L2/L3 승격 증거로 쓰지 않는다.
- 워터마크는 마지막으로 **성공적으로 체크포인트된** Episode까지만 갱신한다.
- 최근 N개 건강도 창과 증분 수집 대상은 다르다. 전자는 상태 평가, 후자는
  후보 갱신에 사용한다.
