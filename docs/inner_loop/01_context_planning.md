# 1. 컨텍스트 구성과 계획

## 책임

현재 요청에 관련된 기억만 선택적으로 주입하고, L4/L5 제약 안에서 실행 가능한
계획을 만든다. 검색 결과는 계획의 근거이지, 무조건 따라야 하는 명령이 아니다.

## 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| 사용자 요청, L1/L2/L3 검색 결과, Session Constraints | Injected Context, Plan, `plan_created` L0 이벤트 |

## 의사 코드

```text
function build_context_and_plan(session, constraints):
    l1 = search_l1(
        query = session.user_request,
        prefer_reflection = true,
        top_k = 3
    )
    l2 = search_l2(query = session.user_request, top_k = 3)
    l3 = search_l3(query = session.user_request, top_k = 3)

    context = trim_to_token_budget(
        rank_by_relevance_and_density(l1, l2, l3),
        constraints.budgets.context_tokens
    )

    plan = create_plan(
        request = session.user_request,
        memory_context = context,
        prohibited_actions = constraints.constitution.prohibitions,
        approval_requirements = constraints.constitution.approval_rules,
        authority_boundary = constraints.authority
    )

    preflight = check_plan_against_cib(plan, constraints.constitution)
    if not preflight.passed:
        plan = create_safe_alternative_or_refusal(
            request = session.user_request,
            failed_checks = preflight.failed_scenarios
        )

    append_l0(session, "plan_created", {
        plan: plan.summary,
        retrieved_episode_ids: ids(l1),
        retrieved_knowledge_ids: ids(l2),
        retrieved_skill_ids: ids(l3),
        preflight_cib: preflight
    })

    return context, plan
```

## 완료 조건

- 선택 주입된 기억의 출처 ID가 L0에 남아 있다.
- 계획 또는 안전한 거부 계획이 존재한다.
- 계획이 L4/L5 제약과 도구 권한 범위를 벗어나지 않는다.
