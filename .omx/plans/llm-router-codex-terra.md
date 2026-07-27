# LLM 라우팅: Codex SDK + GPT-5.6 Terra 계획

## 요구사항 요약

1. OpenAI 모델 호출은 `openai-codex` Python SDK로 수행한다.
2. 라우팅 경계로 LLMLite를 두며, 현재 Ollama Cloud와 Codex를 지원하고 이후
   공급자 추가가 가능해야 한다.
3. 최초 사용자 입력 경로는 `gpt-5.6-terra`를 사용한다.
4. 이번 단계는 사용자 입력 → 응답만 검증하며 기억, 도구, Inner/Outer/Meta Loop는
   시작하지 않는다.

## 현재 상태와 결정 근거

- 현재 `LLMClient`는 `openai` / `ollama` 문자열 분기로 직접 SDK를 선택한다
  ([src/agent/llm/client.py](../../src/agent/llm/client.py)); 확장 가능한 라우팅
  경계가 없다.
- 일반 OpenAI Python SDK의 `chat.completions.create()`를 호출하고 있으므로 요구사항
  1을 충족하지 않는다.
- `openai-codex==0.144.4`가 가상환경에 설치되어 있으며, `Codex.thread_start()`와
  `Thread.run()`은 모델을 명시할 수 있다.
- GPT-5.6 Terra는 공식 API 모델 ID `gpt-5.6-terra`이며 Chat Completions와 Responses
  API를 지원한다. 초기 입력에는 Codex SDK의 thread/turn 경로로 이 모델을 고정한다.

## 설계 결정

`LLMLite`를 애플리케이션의 유일한 라우팅 인터페이스로 둔다. 각 provider adapter는
실제 호출만 담당한다.

```text
CLI --llm-only
  -> LLMLite.complete(route="initial_user_input", message)
       -> RouteResolver: codex / ollama_cloud / future provider
       -> CodexAdapter: openai_codex.Codex + gpt-5.6-terra
       -> OllamaCloudAdapter: ollama.Client
       -> Provider-neutral LLMResponse
```

`initial_user_input` 라우트는 Codex adapter와 `gpt-5.6-terra`로 불변 고정한다.
Ollama Cloud는 구성된 별도 route에서만 선택할 수 있으며 fallback으로 Terra를
대체하지 않는다. 공급자 오류는 fallback 텍스트가 아니라 구조화된 오류로 반환한다.

## 수용 기준

1. `--llm-only`는 Runtime, MemoryManager, EmbeddingEncoder, ToolRegistry,
   Orchestrator를 생성하지 않는다.
2. `initial_user_input`은 `openai_codex.Codex`로 시작한 thread/turn에
   `model="gpt-5.6-terra"`를 전달한다.
3. Codex 호출은 `Sandbox.read_only` 및 `ApprovalMode.deny_all`로 시작해 파일 쓰기와
   권한 상승을 허용하지 않는다.
4. Ollama Cloud route는 base URL과 모델을 구성으로 받으며, Codex 구현과 독립된
   adapter다.
5. 알 수 없는 route/provider, 인증 실패, 모델 미가용, timeout은 provider·원인·재시도
   가능 여부를 담은 예외/결과로 표면화한다. `[LLM not available]` 성공처럼 보이는
   응답은 제거한다.
6. 실제 네트워크 없이 adapter, router, CLI 격리 조건을 테스트한다. 별도 opt-in
   integration test는 인증된 Codex 환경에서 Terra 응답을 검증한다.

## 구현 단계

1. **의존성·구성 계약 확정**
   - `pyproject.toml`에 `openai-codex`를 런타임 의존성으로 선언한다. 현재 가상환경에만
     설치되어 있어 재현 가능한 설치 계약이 아니다.
   - `config/agent.yml`의 단일 `llm.backend`를 route/provider 구성으로 교체하고,
     `initial_user_input -> codex -> gpt-5.6-terra`를 명시한다.
   - API key 또는 Codex 로그인 방식은 구성 파일에 저장하지 않고 환경/SDK 로그인에만
     둔다.

2. **Provider-neutral LLMLite 계약 도입**
   - `src/agent/llm/`에 request, response, error, provider protocol을 정의한다.
   - `LLMLite.complete(request)`가 route를 해석하고 하나의 adapter만 호출하게 한다.
   - 모델명은 호출부가 아닌 route policy에서 결정한다. 이후 Anthropic·OpenAI API 등은
     adapter 등록만으로 추가할 수 있게 한다.

3. **Codex adapter 구현**
   - `openai_codex.Codex` context manager를 사용하고, 새 사용자 세션에는 새 thread를
     만든다.
   - 최초 route에서 `model="gpt-5.6-terra"`, `Sandbox.read_only`,
     `ApprovalMode.deny_all`을 설정하고 `Thread.run()`의 `final_response`와 usage를
     provider-neutral 응답으로 변환한다.
   - SDK 초기화, 인증, turn 실패, 빈 최종 응답을 구분해 오류로 변환하고 process/thread를
     항상 닫는다.

4. **Ollama Cloud adapter 분리**
   - 기존 `ollama.Client` 로직을 adapter로 이동하고 cloud base URL, 모델, timeout을
     route 구성에서 받는다.
   - 로컬 `localhost`는 기본값에서 제거한다. Ollama는 명시된 cloud route에서만
     접근한다.

5. **CLI를 LLMLite에 연결**
   - `src/agent/main.py`의 `--llm-only` 경로를 LLMLite에 연결한다.
   - 이 경로에서는 embedding cache도 포함해 어떤 memory/tool/loop 초기화도 하지
     않는다.
   - 기존 전체 파이프라인은 이번 작업에서 변경하지 않고, 이후 단계에서 명시적으로
     LLMLite로 이관한다.

6. **관측성·오류 UX 정비**
   - provider, model, route, latency, request ID(제공될 때), 오류 분류를 로그에 남긴다.
   - 키·프롬프트 전문·Codex thread의 민감 데이터를 로그에 남기지 않는다.
   - CLI는 fallback 문구 대신 해결 가능한 오류(인증 없음, Codex runtime 없음, 모델
     권한 없음, network timeout)를 출력한다.

7. **검증**
   - unit: route 정책, Terra 모델 고정, Codex adapter SDK 호출 인자, Ollama adapter,
     오류 변환, client close.
   - CLI: `--llm-only`에서 전체 부트스트랩이 호출되지 않음을 mock으로 검증.
   - integration (opt-in): 인증된 Codex SDK로 짧은 프롬프트를 보내 Terra의 비어 있지
     않은 응답과 provider/model metadata를 검증한다. 기본 CI에서는 실행하지 않는다.
   - regression: `pytest -q`, `ruff check`, typecheck를 수행한다.

## 위험과 완화

| 위험 | 완화 |
| --- | --- |
| LiteLLM이 직접 OpenAI API를 호출해 Codex SDK 요구를 우회 | LLMLite는 route 결정 및 adapter registry만 담당하고, Codex route의 네트워크 호출은 Codex adapter만 수행하도록 테스트한다. |
| Codex SDK가 코딩 에이전트 도구를 제안 | 초기 route에 read-only sandbox와 deny-all approval을 고정하고, 허용된 출력은 최종 텍스트만 사용한다. |
| 인증 또는 모델 권한 부족이 fallback에 숨음 | fallback을 제거하고 인증/모델/전송 오류를 구분해 실패 처리한다. |
| 새 provider 추가가 기존 cognition 계층에 파급 | `LLMResponse`와 provider protocol을 안정적인 경계로 두고, 기존 `LLMClient` 이관은 후속 단계로 분리한다. |

## ADR

**Decision:** 사용자 최초 입력은 LLMLite route가 선택한 Codex adapter를 통해
`gpt-5.6-terra`로 전송한다.

**Drivers:** Codex SDK 사용 강제, 다중 공급자 확장성, 첫 단계의 최소 실행 범위,
명확한 오류 관찰성.

**Alternatives considered:**

- 기존 `LLMClient`에 `if backend == ...`를 추가: provider가 늘수록 조건 분기가
  증가하고 route 정책과 transport가 결합된다.
- LiteLLM이 OpenAI 호출까지 직접 수행: 일반적 라우팅에는 편하지만 Codex SDK를
  통한 GPT 호출 요구를 위반할 수 있다.
- 처음부터 전체 Orchestrator를 이관: 검증 범위가 지나치게 넓어 최초 연결 실패 원인
  분리가 어렵다.

**Why chosen:** router와 transport adapter를 분리하면 Codex SDK의 사용을 검증 가능하게
강제하면서도 Ollama 및 미래 공급자를 같은 계약에 추가할 수 있다.

**Consequences:** Codex SDK 수명주기·로그인 상태를 명시적으로 관리해야 하며, 초기
LLMLite 구현은 일반 호출 라이브러리보다 얇은 내부 경계가 된다.

**Follow-ups:** 최초 Terra 통합 검증 후에만 planner/reasoner 등 기존 전체 파이프라인을
단계적으로 LLMLite로 이관한다.
