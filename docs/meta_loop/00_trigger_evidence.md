# 0. 트리거와 증거 묶음

## 책임

Outer Loop가 보낸 정기 진화, 긴급 점검, 정체, 캘리브레이션·성장 이상 신호를
받아 Meta Loop 검토의 입력으로 고정한다. 이 단계는 해결책을 가정하지 않는다.

## 의사 코드

```text
function collect_trigger_evidence(signal):
    evidence = {
        trigger_type: signal.trigger_type,
        triggered_at: now(),
        metrics_snapshot: load_latest_outer_metrics(),
        audit_snapshot: load_latest_independent_audit(),
        growth_snapshot: load_latest_growth_result(),
        affected_episode_ids: signal.related_episode_ids,
        affected_candidate_ids: signal.related_candidate_ids,
        affected_skill_ids: signal.related_skill_ids,
        current_versions: read_l4_l5_and_architecture_versions()
    }

    persist_meta_evidence_snapshot(evidence)
    return evidence
```

## 규칙

- 검토 중 지표가 바뀌어도 판단 근거가 흔들리지 않도록 스냅샷을 저장한다.
- 민감 L0 원문은 필요 최소한으로 참조하고, 증거 묶음에는 ID와 요약을 우선한다.
- 트리거는 변경 승인이나 변경 명령이 아니다.
