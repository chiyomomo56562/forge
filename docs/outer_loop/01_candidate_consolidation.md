# 1. Pattern Candidate 증거 통합

## 책임

새 L1 Episode를 기존 Pattern Candidate에 연결해, 배치가 바뀌어도 지지·반박
증거가 누적되게 한다. 이 단계는 일반 지식을 확정하지 않는다.

## 의사 코드

```text
function consolidate_candidates(eligible_episodes):
    changed_candidates = []

    for episode in eligible_episodes:
        signature = normalize_pattern(
            condition = episode.reflection.causal_condition,
            action = episode.reflection.what_worked,
            outcome = episode.evaluation.status
        )
        candidate = find_best_candidate(signature)
        if candidate is null:
            candidate = create_candidate(signature)

        evidence_kind = classify_evidence(episode)
        // support | counterexample | inconclusive
        append_evidence(candidate, episode.episode_id, evidence_kind)
        update_candidate_aggregates(candidate, episode.evaluation)
        update_conditions_and_exceptions(candidate, episode.reflection)
        changed_candidates.append(candidate)

    return unique(changed_candidates)
```

## 규칙

- 성공만 누적하지 않는다. CIB를 통과한 실패는 반례 또는 약한 증거다.
- 후보는 L1 ID를 보존한다. 집계 수치만 남기면 L2 지식의 근거를 감사할 수 없다.
- 너무 다른 조건의 사례는 같은 후보에 합치지 않고 더 좁은 후보로 분리한다.
