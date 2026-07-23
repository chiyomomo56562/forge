# 2. 변경 제안 작성

## 책임

진단 권고를 사람이 검토하고 되돌릴 수 있는 `ChangeProposal`로 변환한다. 제안은
“무엇을 바꿀지”뿐 아니라 “왜, 어디까지, 어떻게 검증하고 되돌릴지”를 포함한다.

## 의사 코드

```text
function draft_change_proposal(recommendation, evidence):
    proposal = ChangeProposal(
        proposal_id = generate_id("proposal"),
        type = classify_proposal_type(recommendation),
        title = make_short_title(recommendation),
        description = explain_problem_and_expected_benefit(recommendation),
        changes = make_exact_change_set(recommendation),
        status = "pending",
        evidence_refs = references(evidence),
        impact_summary = analyze_impact(recommendation),
        validation_plan = define_validation(recommendation),
        rollback_plan = define_rollback(recommendation),
        required_approval_scope = determine_hitl_scope(recommendation)
    )

    validate_proposal_completeness(proposal)
    persist_proposal(proposal)
    return proposal
```

## 최소 필수 필드

- 변경 유형과 정확한 대상 파일·레코드·파라미터
- 근거 Episode, Candidate, L2/L3 지식 또는 지표 ID
- 예상 이익과 부작용, 영향받는 권한·정책·외부 시스템
- 검증 계획과 명시적인 롤백 방법
- 요청할 HITL 승인 범위와 심각도
