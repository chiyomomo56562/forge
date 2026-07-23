# 7. Meta Loop 트리거와 이관

## 책임

누적 Episode 수, Outer Loop 횟수, 정체·위험 신호를 평가해 구조적 검토가 필요한
경우에만 Meta Loop에 **제안**을 전달한다. 이 단계는 구조 변경을 실행하지 않는다.

## 의사 코드

```text
function evaluate_meta_trigger(state, metrics, audit, growth, policy):
    trigger = null

    if growth.stagnation_detected:
        trigger = "stagnation_response"
    else if state.total_episodes_seen >= policy.episode_threshold:
        trigger = "regular_evolution"
    else if state.outer_loop_count >= policy.outer_loop_threshold:
        trigger = "emergency_inspection"
    else if audit.flagged and growth.is_unstable:
        trigger = "calibration_and_growth_review"

    if trigger is null:
        return NoMetaProposal()

    proposal = create_meta_loop_proposal(
        trigger = trigger,
        evidence = {metrics, audit, growth},
        scope = "diagnosis_only",
        rollback_required = true
    )
    enqueue_meta_proposal(proposal)
    return proposal
```

## 규칙

- Meta Loop 트리거는 L4/L5 변경 승인이 아니다.
- 제안에는 근거 Episode/후보/지표와 예상 영향, 검증 방법, 롤백 계획이 포함된다.
- L4 개정, L5 정체성·자율성 변경, 아키텍처 변경은 Meta Loop의 HITL 절차를
  통과한 뒤에만 적용한다.
