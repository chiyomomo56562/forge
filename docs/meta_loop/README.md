# Meta Loop 단계별 의사 코드

Meta Loop는 시스템의 구조적 변경을 **제안하고 통제**하는 느린 경로다. L4 헌법,
L5 정체성·자율성, 아키텍처, 워크플로 파라미터는 Meta Loop를 거치더라도 자동으로
바뀌지 않는다. 모든 변경은 제안·안전 검토·명시적 HITL 승인·통제된 적용·감사라는
수명 주기를 가진다.

| 순서 | 단계 | 문서 | 책임 |
| --- | --- | --- | --- |
| 0 | 트리거와 증거 묶음 | [00_trigger_evidence.md](00_trigger_evidence.md) | Outer Loop 신호와 근거 고정 |
| 1 | 진단과 범위 설정 | [01_diagnosis_scope.md](01_diagnosis_scope.md) | 구조적 원인·대안·변경 유형 판별 |
| 2 | 변경 제안 작성 | [02_proposal_drafting.md](02_proposal_drafting.md) | 정확한 diff·영향·검증·롤백 정의 |
| 3 | 사전 안전 검토 | [03_preflight_review.md](03_preflight_review.md) | CIB·회귀·권한 영향 검사 |
| 4 | HITL 승인 | [04_hitl_approval.md](04_hitl_approval.md) | 인간의 승인·반려·만료 처리 |
| 5 | 통제된 적용 | [05_controlled_execution.md](05_controlled_execution.md) | 승인 범위만 실행하고 버전화 |
| 6 | 검증·감사·롤백 | [06_validation_audit.md](06_validation_audit.md) | 결과 검증, 이력 보존, 실패 복구 |

## 변경 유형

```text
constitution_revision       L4 원칙·K-Scenario·CIB 임계값 변경
identity_redesign           L5 정체성·자율성·권한 경계 변경
architecture_modification   구성요소·저장소·워크플로 구조 변경
organizational_restructuring 역할·도구·작업 분배 구조 변경
```

## 전체 의사 코드

```text
function run_meta_loop(outer_loop_signal):
    evidence = collect_trigger_evidence(outer_loop_signal)
    diagnosis = diagnose_and_scope(evidence)

    for recommendation in diagnosis.recommendations:
        proposal = draft_change_proposal(recommendation, evidence)
        review = run_preflight_review(proposal)

        if not review.passed:
            record_rejected_preflight(proposal, review)
            continue

        queue_pending_proposal(proposal, review)
        request_hitl_approval(proposal, review)

    return PendingProposalSummary()

function execute_approved_meta_changes():
    for proposal in list_approved_proposals():
        result = execute_in_approved_scope(proposal)
        validation = validate_and_audit(proposal, result)

        if not validation.passed:
            rollback(proposal, validation)

    return ExecutionSummary()
```

Meta Loop의 첫 함수는 **제안 생성까지만** 한다. 실제 변경 적용은 독립된
`execute_approved_meta_changes()`에서 HITL 승인 상태를 확인한 뒤에만 수행한다.
