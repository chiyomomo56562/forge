# Outer Loop 단계별 의사 코드

Outer Loop는 Inner Loop가 저장한 L1 Episode와 Pattern Candidate를 배치로
통합한다. 최근 N개는 **증분 처리 주기와 현재 건강도 창**이며, 과거 증거를
버리는 범위가 아니다. 후보의 지지·반박 증거는 배치 간 누적된다.

| 순서 | 단계 | 문서 | 책임 |
| --- | --- | --- | --- |
| 0 | 트리거·증분 수집 | [00_trigger_collection.md](00_trigger_collection.md) | N 도달 확인, 워터마크 이후 L1 수집 |
| 1 | 후보 통합 | [01_candidate_consolidation.md](01_candidate_consolidation.md) | 새 Episode를 누적 Pattern Candidate에 반영 |
| 2 | L1→L2 결정 | [02_l1_to_l2.md](02_l1_to_l2.md) | 조건부 일반 지식의 승격·수정·약화·폐기 |
| 3 | L2→L3 결정 | [03_l2_to_l3.md](03_l2_to_l3.md) | Seed 생성 및 스킬 생명 주기 관리 |
| 4 | 지표·자기 모델 | [04_metrics_self_model.md](04_metrics_self_model.md) | 최근 건강도와 L5 통계 재계산 |
| 5 | 독립 감사·성장 제어 | [05_audit_growth_control.md](05_audit_growth_control.md) | 캘리브레이션·급격한 변화 감시 |
| 6 | 캐시·체크포인트 | [06_cache_checkpoint.md](06_cache_checkpoint.md) | 파생 조회 뷰 갱신과 원자적 상태 저장 |
| 7 | Meta Loop 트리거 | [07_meta_trigger.md](07_meta_trigger.md) | 구조적 문제의 제안 경로 이관 |

## 전체 흐름

```text
N 도달 또는 감시 신호
  → 워터마크 이후 eligible L1 수집
  → Pattern Candidate 누적
  → L1 → L2 일반화 결정
  → L2 → L3 Seed/생명 주기 결정
  → 최근 N개 건강도·L5 통계 재계산
  → 독립 감사·성장 속도 제어
  → 캐시 갱신·체크포인트
  → 필요 시 Meta Loop에 제안 전달
```

Outer Loop는 L4 헌법을 변경하거나 L5 권한을 확대하지 않는다. 그러한 구조적
변경은 Meta Loop 제안과 HITL 승인을 거친다.

## 전체 의사 코드

```text
function run_outer_loop(state, config):
    input = start_outer_loop(state, config)
    if input is NotDue:
        return input

    candidates = consolidate_candidates(input.eligible_episodes)
    l2_changes = decide_l1_to_l2(candidates, config.promotion_policy)
    l3_changes = manage_procedural_memory(l2_changes, config.skill_policy)

    metrics, self_model = recalculate_health_and_self_model(state, config)
    audit, growth = audit_and_regulate(
        metrics, self_model, state, config.growth_policy
    )

    results = {
        l2_changes, l3_changes, metrics, self_model, audit, growth
    }
    checkpoint = refresh_and_checkpoint(input, results, state)
    meta_proposal = evaluate_meta_trigger(
        checkpoint, metrics, audit, growth, config.meta_policy
    )

    return OuterLoopResult(results, checkpoint, meta_proposal)
```
