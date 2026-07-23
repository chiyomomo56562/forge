# 0. 세션 시작과 제약 고정

## 책임

한 작업 단위의 식별자를 만들고, 실행 중 바뀌어서는 안 되는 L4/L5 제약을
고정한다. 이후 모든 L0 이벤트와 L1 Episode는 같은 `session_id`와
`episode_id`를 사용한다.

## 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| 사용자 요청, 현재 L4 헌법, 현재 L5 권한, 실행 설정 | Session, Constraints, `session_started` L0 이벤트 |

## 의사 코드

```text
function start_session(user_request, runtime_config):
    session = Session(
        session_id = generate_id("session"),
        episode_id = generate_id("episode"),
        user_request = user_request,
        retry_count = 0,
        max_retries = runtime_config.max_retries
    )

    constraints = {
        constitution: load_l4_constitution(),
        authority: load_l5_authority_boundary(),
        risk_level: classify_risk(user_request),
        budgets: resolve_budgets(runtime_config, user_request)
    }

    append_l0(session, "session_started", {
        request: user_request,
        risk_level: constraints.risk_level,
        authority_summary: constraints.authority.summary,
        budget_summary: constraints.budgets
    })

    return session, constraints
```

## 불변 조건

- 세션 도중 L4/L5의 최신값을 다시 읽어 권한을 넓히지 않는다.
- 제약 변경이 필요하면 현재 세션을 안전하게 끝내고 Meta Loop/HITL 경로로
  처리한다.
