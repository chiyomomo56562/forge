# 5. 승인 범위 내 통제된 적용

## 책임

승인된 제안만 실행하고, 승인된 변경 집합 밖의 파일·권한·외부 시스템에는 영향을
주지 않게 한다. 적용 전 상태를 보존해 실패 시 즉시 되돌릴 수 있어야 한다.

## 의사 코드

```text
function execute_in_approved_scope(proposal):
    assert proposal.status == "approved"
    assert approval_record_exists(proposal.id)

    snapshot = create_pre_change_snapshot(proposal.affected_resources)
    lock_affected_resources(proposal.affected_resources)

    try:
        result = execute_registered_proposal_handler(
            proposal.type,
            proposal.changes
        )
        if not result.success:
            raise ExecutionFailed(result.error)

        mark_proposal_executed(proposal.id, result.applied_changes)
        return result
    except error:
        restore_snapshot(snapshot)
        mark_proposal_failed(proposal.id, error)
        return failed_result(error)
    finally:
        unlock_affected_resources()
```

## 적용 유형

- `constitution_revision`: 승인된 YAML 변경과 헌법 버전·승인 이력 갱신
- `identity_redesign`: 승인된 L5 선언 데이터와 정체성 버전 이력 갱신
- `architecture_modification`: 승인된 코드·구성 변경을 제한된 작업 공간에 적용
- `organizational_restructuring`: 승인된 역할·도구·워크플로 정의만 변경
