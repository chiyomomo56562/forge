# 5. L1 Episode 저장과 Pattern Candidate 연결

## 책임

L0 원본 참조, 실행 요약, 평가, 반성을 하나의 불변 L1 Episode로 만든다.
ChromaDB에는 검색 가능한 의미 표현을 저장하고, 승격 가능 Episode만 Pattern
Candidate의 증거로 연결한다.

## 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| Session, Execution Result, Evaluation, Reflection, L0 이벤트 | L1 Episode, ChromaDB record, Candidate evidence link |

## ChromaDB 저장 규칙

```text
id = episode_id

document =
  작업 + 결과 + what_worked + what_failed + next_hint + causal_condition

metadata =
  created_at, task_category, status, success_score, pain_index, cib_score,
  promotion_eligibility, pattern_candidate_id
```

`document`는 임베딩과 의미 검색을 위한 자연어 텍스트다. L0 전체 원본이나
중첩된 복잡한 객체는 metadata에 넣지 않는다. 원본은 append-only L0에 두고,
필요하면 `episode_id`로 참조한다.

## 의사 코드

```text
function persist_episode(session, execution, evaluation, reflection):
    episode = Episode(
        episode_id = session.episode_id,
        task = session.user_request,
        raw_event_refs = session.event_ids,
        execution_summary = summarize(execution),
        outcome = evaluation.status,
        evaluation = evaluation,
        reflection = reflection,
        promotion_eligibility = evaluation.promotion_eligibility
    )

    candidate_id = null
    if evaluation.promotion_eligibility == "eligible":
        candidate = find_or_create_pattern_candidate(
            condition = normalize(reflection.causal_condition),
            action = normalize(reflection.what_worked),
            outcome = normalize(episode.outcome)
        )
        candidate_id = candidate.id
        link_evidence(candidate, episode.episode_id, evaluation)

    chroma.add(
        id = episode.episode_id,
        document = make_search_document(episode),
        metadata = {
            created_at: now(),
            task_category: classify_task(episode.task),
            status: evaluation.status,
            success_score: evaluation.success_score,
            pain_index: evaluation.pain_index,
            cib_score: evaluation.cib_score,
            promotion_eligibility: evaluation.promotion_eligibility,
            pattern_candidate_id: candidate_id
        }
    )

    append_l0(session, "episode_persisted", {
        episode_id: episode.episode_id,
        candidate_id: candidate_id,
        promotion_eligibility: evaluation.promotion_eligibility
    })

    return episode
```

## 완료 조건

- 모든 L0 참조와 Evaluation·Reflection을 가진 Episode가 생성됐다.
- ChromaDB의 ID와 `episode_id`가 일치한다.
- CIB 실패 Episode는 저장되지만 Candidate 증거에 연결되지 않는다.
- Candidate 연결 실패 시에도 Episode는 저장하고 실패 이유를 L0에 남긴다.
