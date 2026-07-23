# 6. 적용 후 검증·감사·롤백

## 책임

적용 결과가 제안의 성공 기준을 충족하는지 검증하고, 실제 영향과 감사 이력을
저장한다. 검증 실패 또는 예상 밖의 안전 영향이 있으면 롤백한다.

## 의사 코드

```text
function validate_and_audit(proposal, execution_result):
    validation = run_validation_plan(proposal.validation_plan)
    cib = evaluate_post_change_cib(proposal)
    scope = verify_actual_changes_within_approval(proposal)

    passed = validation.passed and cib.min_score >= 0.95 and scope.valid
    audit_record = {
        proposal_id: proposal.id,
        executed_at: now(),
        validation: validation,
        cib: cib,
        scope: scope,
        passed: passed
    }
    persist_meta_audit(audit_record)

    if not passed:
        rollback(proposal, audit_record)
        mark_validation_failed(proposal.id, audit_record)
    else:
        refresh_affected_caches(proposal)
        mark_validation_passed(proposal.id, audit_record)

    return audit_record
```

## 완료 조건

- 실제 변경 범위가 승인 범위와 일치한다.
- 제안의 검증 계획과 CIB/K-Scenario 검사가 통과했다.
- 변경 전후 버전, 승인 정보, 적용 결과, 검증 결과가 감사 로그에 남아 있다.
- 실패했다면 정본·캐시·권한 상태가 롤백됐고 실패 이유가 보존돼 있다.
