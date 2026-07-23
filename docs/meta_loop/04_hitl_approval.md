# 4. HITL 승인

## 책임

사람이 제안의 영향, 검증 근거, 롤백 계획을 검토해 명시적으로 승인하거나
반려하도록 한다. 대기 중인 제안은 어떤 변경도 적용하지 않는다.

## 의사 코드

```text
function request_hitl_approval(proposal, preflight):
    request = ApprovalRequest(
        proposal_id = proposal.id,
        constitution_layer = affected_l4_layer(proposal),
        severity = determine_severity(proposal),
        impact_summary = proposal.impact_summary,
        rollback_plan = proposal.rollback_plan,
        expiry_hours = determine_expiry(proposal)
    )
    persist_approval_request(request)
    notify_reviewer(request)
    return request

function decide_hitl(request, decision, reviewer, reason):
    require_nonempty(reason)

    if request.is_expired:
        return mark_expired(request)
    if decision == "approve":
        mark_proposal_approved(request.proposal_id, reviewer, reason)
    else:
        mark_proposal_rejected(request.proposal_id, reviewer, reason)

    audit_decision(request, decision, reviewer, reason)
```

## 규칙

- L4 개정과 L5 정체성·자율성·권한 변경은 명시적 HITL 승인이 필수다.
- 승인 이유·검토자·시각을 변경 제안과 별도 승인 이력 모두에 기록한다.
- 제안이 반려·만료되면 실행 대기열에서 제거하고, 근거와 결정은 보존한다.
