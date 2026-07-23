# 6. 캐시 갱신과 체크포인트

## 책임

L2/L3/L5 파생 상태가 확정된 뒤 검색 캐시를 갱신하고, 실패 시 재개할 수 있도록
Outer Loop 상태와 워터마크를 원자적으로 저장한다.

## 의사 코드

```text
function refresh_and_checkpoint(run_input, results, state):
    refresh_l2_graph_cache()
    refresh_l3_skill_cache()
    refresh_l4_read_cache_if_version_changed()
    refresh_l5_self_model_cache()

    checkpoint = {
        outer_loop_count: state.outer_loop_count + 1,
        consolidation_watermark: max_id(run_input.all_new_episodes),
        last_metrics: results.metrics,
        last_audit: results.audit,
        learning_suspended: results.growth.learning_suspended,
        updated_at: now()
    }

    atomically_write_outer_loop_state(checkpoint)
    append_outer_loop_audit_log(checkpoint, results.summary)
    return checkpoint
```

## 규칙

- 캐시는 정본이 아니다. 캐시 갱신 실패는 정본 상태를 되돌리지 않는다.
- 워터마크는 모든 통합 결과와 감사 로그가 저장된 후에만 전진한다.
- 체크포인트 전 실패 시 같은 Episode를 다시 처리할 수 있어야 하므로,
  Candidate 증거 연결은 idempotent해야 한다.
