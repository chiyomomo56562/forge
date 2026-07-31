# Inner Loop 평가된 L1 Episode 수직 슬라이스 계획

## 목적과 범위

현재 Forge는 `ReceiveMessageService` → `LangGraphConversationRuntime` → chat model
경로로 텍스트 답변과 프로세스 내 대화 문맥을 제공한다. 다음 단계에서는 한 메시지
처리를 **평가된 L1 Episode**로 완결한다. L0 append-only 이벤트는 이 Episode의
원본 증거이며, 독립적인 선행 기능이나 완료 지점이 아니다.

이번 작업의 완료 지점은 “한 사용자 메시지 처리”가 고정된 제약 스냅샷,
`session_id`/`episode_id`/`request_id`, 세션 내 순번을 가진 L0 증거, 결정론적
Evaluation과 Reflection, 이를 참조하는 immutable L1 Episode를 **같은 처리 흐름에서**
남기는 것이다. L0만 기록하고 끝내는 중간 마일스톤은 두지 않는다.
ChromaDB 저장·검색 주입, 도구 호출, LLM 기반 계획·반성·CIB 판정, DB checkpoint는
포함하지 않는다.

## 근거와 결정

- 현재 대화 runtime은 `InMemorySaver`만 사용하며 장기 기억·도구·DB checkpoint를
  명시적으로 범위 밖으로 둔다.
  - `docs/architecture/langchain-chat.md:6-15`
  - `src/forge/runtime/conversation.py:32-62`
- Inner Loop의 첫 산출물은 동일한 `session_id`와 `episode_id`를 공유하는 Session,
  Constraints, `session_started` L0 이벤트다.
  - `docs/inner_loop/00_session_setup.md:5-13, 18-41`
- L1은 원본 로그를 복제하지 않고 시간순 `raw_event_refs`와 완료된 Evaluation,
  Reflection으로 L0을 참조해야 한다. 따라서 L0은 모델 호출 사실뿐 아니라 성공/실패,
  pain index의 구성 근거, 평가 결과, 반성 결과를 남겨야 한다.
  - `docs/inner_loop/05_episode_persistence.md:5-7, 28-30`
  - `docs/inner_loop/06_l1_storage_schema.md:9-15, 20-33`
- 레거시 `agent` 패키지는 새 `forge` 런타임의 의존성이 아니다. 특히 레거시는
  `pain_index = 1 - success_score`를 전제하지만, 현재 설계는 두 값을 독립 지표로
  정의한다. 모델·저장소 코드를 그대로 이식하지 않는다.
  - `src/agent/memory/schemas.py:87-118`
  - `docs/inner_loop/03_evaluation.md:12-21`

## 아키텍처 경계

```text
CLI / future inbound adapter
  -> ReceiveMessageService
  -> ReceiveMessageUseCase (session orchestration)
     -> SessionFactory (L4/L5 제약을 한 번만 snapshot)
     -> EventLog port -> JSONL file adapter
     -> ConversationRuntime -> LangGraphConversationRuntime -> chat model
     -> AttemptEvaluator -> ReflectionFactory -> EpisodeRepository
  -> AssistantReply
```

1. `domain`은 Session, immutable Constraints, L0Event와 이벤트 종류를 소유한다.
2. `application`은 세션 시작, 모델 호출 전후 기록, 오류 기록의 순서를 소유한다.
3. `ports`는 이벤트·Episode 영속화와 제약 공급자 인터페이스를 정의한다.
4. `adapters/outbound`가 JSONL 파일 쓰기와 초기에는 정적/설정 기반 제약 공급을
   구현한다.
5. `runtime`은 오직 대화 상태와 모델 호출을 계속 소유한다. JSONL, 평가, Episode,
   헌법 파일을 직접 알지 않는다.

## L1 우선 계약과 L0 증거 계약

구현 순서는 L0 파일 writer가 아니라 L1의 최소 계약에서 시작한다. 먼저 다음 질문에
코드로 답한다: “어떤 경우가 성공/부분 성공/실패인가?”, “pain은 무엇으로 계산되는가?”,
“CIB를 아직 평가할 수 없으면 Episode는 어떤 승격 상태인가?” 그 답을 `Evaluation`과
`Episode` 값 객체 및 테스트로 고정한 뒤, 해당 판단의 입력·출력을 L0 이벤트로 남긴다.

### L0 이벤트 계약

모든 이벤트에는 아래 공통 필드를 둔다.

```text
schema_version: 1
event_id: evt_<UUID 또는 ULID>
event_type: string
occurred_at: UTC ISO-8601
session_id: ses_<UUID 또는 ULID>
episode_id: ep_<UUID 또는 ULID>
request_id: req_<UUID 또는 ULID>
sequence: positive integer (session-scoped, strictly increasing)
payload: JSON object
```

이번 단계에서 발생시키는 이벤트와 최소 payload는 다음과 같다.

| 순서 | 이벤트 | 최소 payload |
| --- | --- | --- |
| 1 | `session_started` | request, risk_level, authority_summary, budget_summary |
| 2 | `model_call_started` | conversation_id, input_char_count, system_instruction_present |
| 3 | `model_call_completed` | model, output_char_count, latency_ms |
| 실패 시 | `model_call_failed` | error_type, safe_error_summary, latency_ms |
| 4 | `evaluation_completed` | result_quality, requirement_coverage, verification_confidence, success_score, pain_index, pain_components, cib_score (or null), cib_evaluation_status, status, promotion_eligibility, retryable |
| 5 | `reflection_completed` | what_worked, what_failed, next_hint, causal_condition |
| 6 | `episode_persisted` | episode_id, promotion_eligibility |

원문 사용자 입력이나 모델 답변은 `session_started` 이외의 이벤트에 중복 저장하지
않는다. 오류 이벤트에는 API 키, provider 응답 원문, 시스템 프롬프트를 기록하지
않는다. `request_id`는 `handle()` 호출마다 새로 만들고, 그 호출이 만든 모든 이벤트가
공유한다. `sequence`는 Session마다 1에서 시작해 append 순서대로 1씩 증가한다. 이벤트
파일은 수정·삭제 없이 append만 허용한다.

`evaluation_completed`는 L0의 일부다. 성공/실패 상태를 단순 모델 호출 성공과
혼동하지 않도록, 모델이 답변을 반환했더라도 요구사항 충족·검증 근거가 부족하면
`Partial` 또는 `Failure`가 될 수 있다. `pain_index`는 `success_score`의 역수가
아니며, retry/tool error/budget overrun/rework/human intervention의 각 0~1 구성값과
계산 결과를 함께 payload에 기록한다. CIB의 실제 L4 구현 전에는 CIB 값에 임의의
통과 점수를 넣지 않고 `cib_score=null`, `cib_evaluation_status=not_evaluated`로
명시해 L1을 승격 불가(`quarantined`)로 기록한다.

## 구현 단계

### 1. Evaluation·Reflection·L1 Episode 계약과 판정 규칙을 먼저 고정

**파일**

- 추가: `src/forge/domain/inner_loop/models.py`
- 추가: `src/forge/domain/inner_loop/__init__.py`
- 추가: `tests/forge/domain/inner_loop/test_models.py`

**작업**

1. `ExecutionResult`, `Evaluation`, `Reflection`, `Episode`를 immutable dataclass
   또는 동등한 immutable 값 객체로 정의한다. `Episode`의 필수 입력이 모두 없으면
   저장할 수 없게 한다.
2. Evaluation은 `result_quality`, `requirement_coverage`,
   `verification_confidence`, `success_score`, `pain_index`, nullable `cib_score`,
   `cib_evaluation_status`, status, promotion eligibility, retryability와 각각의
   계산 근거를 가진다.
3. 성공/부분 성공/실패, pain component 계산, CIB 미평가 시 quarantine의 순수
   판정 함수를 작성하고 경계값 테스트를 먼저 만든다.
4. Reflection은 결과·평가·관찰된 조건을 입력으로 하며, LLM 호출 없이도 최소한
   사실 기반의 네 필드를 만들 수 있게 한다.
5. 그 다음 `Session`, `SessionConstraints`, `L0Event`와 식별자를 정의한다. Session
   생성 시 `ses_`, `ep_`, request마다 `req_` ID를 정하고, 이벤트에는 `evt_` ID와
   session-scoped sequence를 강제한다.

**완료 기준**

- Evaluation의 상태와 score/pain/CIB 근거가 경계값 테스트로 고정되고, 불완전한
  Episode는 생성할 수 없다.
- Session과 그 이벤트들은 같은 session/episode ID를 가진다. 한 요청의 이벤트는
  같은 request ID를 가지며 Session 내 sequence는 중복·역전되지 않는다.
- 빈/유효하지 않은 ID, timezone 없는 시각, JSON 불가능 payload가 거부된다.
- success와 pain을 별개 값으로 저장하며, CIB 미평가는
  `cib_evaluation_status=not_evaluated`와 `quarantined`로만 표현할 수 있고 임의의
  안전 점수로 바꿀 수 없다.

### 2. L0·L1 저장 포트를 구현하되, 평가된 Episode만 저장하도록 제한

**파일**

- 추가: `src/forge/ports/outbound/event_log.py`
- 추가: `src/forge/ports/outbound/episode_repository.py`
- 추가: `src/forge/ports/outbound/constraints_provider.py`
- 갱신: `src/forge/ports/outbound/__init__.py`
- 추가: `src/forge/adapters/outbound/events/jsonl_event_log.py`
- 추가: `src/forge/adapters/outbound/episodes/jsonl_episode_repository.py`
- 추가: `src/forge/adapters/outbound/policy/config_constraints_provider.py`
- 추가: `tests/forge/adapters/outbound/events/test_jsonl_event_log.py`
- 추가: `tests/forge/adapters/outbound/policy/test_config_constraints_provider.py`

**작업**

1. `EventLog.append(event)`, `EpisodeRepository.save(episode)`,
   `ConstraintsProvider.snapshot(request)` 포트를 정의한다.
2. JSONL 어댑터는 이벤트의 UTC `occurred_at`에서 날짜를 계산해
   `data/memory/episodic/raw_events/YYYY-MM-DD.jsonl`에 한 이벤트를 한 줄의 JSON으로
   append한다. 로컬 timezone이나 파일 쓰기 시각을 파일명에 사용하지 않으며, 부모
   디렉터리는 없을 때 생성한다.
3. append 도중 실패하면 부분 JSON 라인을 남기지 않도록 단일 write 경로를 사용하고,
   호출자에게 실패를 전달한다.
4. 첫 단계의 ConstraintsProvider는 config 기본값에서 risk level, authority summary,
   budget summary를 만든다. L4/L5 실구현을 추측해 만들지 않는다.
5. EpisodeRepository는 L1 정본을 JSON 파일로 저장한다. ChromaDB는 다음 단계로
   미루되, `episode_id`를 이후 Chroma ID로 그대로 사용할 수 있게 한다.

**완료 기준**

- 연속 append의 파일 행은 유효한 JSON이고 호출 순서를 보존한다.
- 날짜/경로는 테스트의 임시 디렉터리로 주입할 수 있으며, UTC 자정 경계 전후의
  timestamp도 각각 올바른 UTC 날짜 파일에 기록된다.
- 제약 snapshot은 한 처리 중 재조회되지 않는다.
- 저장된 Episode는 L0 event ID, Evaluation, Reflection을 모두 가지며 덮어쓰지
  않는다.

### 3. 한 요청을 L0 증거에서 평가된 L1 Episode까지 완결하도록 service 통합

**파일**

- 추가 또는 교체: `src/forge/application/conversation/receive_message.py`
- 추가: `src/forge/application/inner_loop/evaluation.py`
- 추가: `src/forge/application/inner_loop/reflection.py`
- 갱신: `src/forge/application/conversation/__init__.py`
- 갱신: `src/forge/bootstrap/container.py`
- 갱신: `tests/forge/application/conversation/test_receive_message.py`
- 갱신 또는 교체: `tests/forge/application/conversation/test_hexagonal_initial_input.py`

**작업**

1. 현재 `ReceiveMessageService.handle()`의 입력 검증을 보존하면서, 한 요청마다
   Session과 Constraints snapshot을 먼저 만든다.
2. runtime 호출 전 `session_started`, `model_call_started`를 append한다.
3. 성공 시 `model_call_completed`를 append하고 기존 `AssistantReply`를 그대로
   반환할 준비를 한다. 호출 소요시간과 출력 길이를 ExecutionResult에 기록한다.
4. runtime이 예외를 내면 `model_call_failed`를 한 번 append한 뒤 원래 예외를
   다시 올린다. 응답을 성공처럼 바꾸거나 예외를 삼키지 않는다.
5. EventLog append 자체가 실패하면 runtime 호출 전이면 호출하지 않고 실패시킨다.
   모델 호출 후 완료 이벤트 기록이 실패한 경우에는 오류를 노출하고 로그 누락을
   숨기지 않는다.
6. 성공/실패 ExecutionResult를 받은 뒤 deterministic evaluator가 Evaluation을
   만들고 `evaluation_completed`를 append한다. 첫 단계의 기준은 명시적으로
   관찰 가능한 값만 사용한다: 모델 호출 오류 여부, 재시도 횟수, budget 사용량,
   caller가 제공한 verification 결과. 제공되지 않은 품질·검증 값은 추측하지 않고
   해당 평가 component를 `null`로, 평가 근거 상태를 `not_evaluated`로 남긴다.
7. Reflection은 모델에게 새 호출을 하지 않는 템플릿 기반 값으로 만든다. 예:
   호출 성공이면 what_worked에 “모델 응답 수신”, 검증 미실시는 next_hint에
   “요구사항 검증 필요”를 기록한다. 이는 일반 지식이 아니라 관찰 조건을 보존한다.
8. Evaluation과 Reflection이 모두 있으면 Episode를 만들고 저장한 뒤
   `episode_persisted`를 append한다. 어느 단계가 실패해도 이미 append된 L0 이벤트는
   제거하지 않는다.

**완료 기준**

- 성공 호출의 이벤트 순서는 정확히 started → model_started → model_completed →
  evaluation_completed → reflection_completed → episode_persisted다.
- 모델 실패의 이벤트 순서는 started → model_started → model_failed →
  evaluation_completed → reflection_completed → episode_persisted다.
- 모델이 받는 메시지와 `AssistantReply` 계약은 기존 runtime 테스트와 동일하다.
- 모델 호출 성공, task success, safety/CIB 통과가 서로 다른 상태로 테스트된다.

### 4. 조립·CLI 관측성·문서 경계 갱신

**파일**

- 갱신: `src/forge/bootstrap/container.py`
- 갱신: `src/forge/adapters/inbound/cli.py`
- 갱신: `docs/architecture/langchain-chat.md`
- 추가: `docs/inner_loop/implementation-l0.md`
- 갱신: `tests/forge/bootstrap/test_container.py` (필요 시 추가)
- 갱신: `tests/forge/adapters/inbound/test_cli.py`

**작업**

1. composition root에서 기본 JSONL EventLog와 기본 ConstraintsProvider를 한 번
   조립하고, 테스트에서는 모두 대체 가능하게 유지한다.
2. CLI 성공 출력 형식은 기존 assistant text만 유지한다. 로그 경로나 session ID를
   기본 stdout에 추가하지 않는다.
3. 문서에 L0의 저장 위치, 이벤트 수명, 현재 제약 snapshot의 한계, L1/검색/도구가
   아직 미구현임을 기록한다. 이 단계에서 L1 정본 JSON은 생성하지만 ChromaDB 검색은
   아직 미구현임을 구분해 쓴다.
4. `--query`는 새 프로세스에서 service를 다시 조립하므로 대화 checkpoint를
   영속화하지 않는다는 기존 설명을 유지한다.

**완료 기준**

- CLI fake model smoke test에서 text 출력과 이벤트 기록이 함께 검증된다.
- 문서가 L0 audit log와 LangGraph 단기 대화 state를 명확히 구분한다.

## 수용 기준

- 각 `ReceiveMessageService.handle()` 호출은 하나의 immutable session/episode
  쌍, 고유 request ID, ExecutionResult·Evaluation·Reflection을 가진 L1 Episode,
  여섯 개의 성공 이벤트 또는 여섯 개의 실패 이벤트를 남긴다.
- 모든 이벤트는 JSONL의 한 줄이며 schema version, event ID, UTC 시간, session ID,
  episode ID, request ID, session-scoped sequence, type, payload를 가진다.
- 이벤트 순서는 `sequence`로 Session 내에서 재구성 가능하며, 단일 요청 이벤트는
  같은 request ID를 공유한다. 과거 이벤트는 수정되지 않는다.
- JSONL 파일명 날짜는 항상 이벤트 `occurred_at`의 UTC 날짜와 일치한다.
- `evaluation_completed`에는 success/failure status, success score, pain index와
  그 구성 근거, CIB 평가 상태가 기록된다. 모델 응답 수신은 task success나 CIB 통과를
  의미하지 않는다.
- 모델 실패와 로그 실패는 서로 구별 가능하고, 비밀값·시스템 프롬프트·provider
  원문은 실패 이벤트에 기록되지 않는다.
- 대화 thread 격리와 invocation-only system instruction 동작은 회귀하지 않는다.
- L1 ChromaDB 검색, L2/L3 승격, 도구, MCP, streaming, DB checkpoint는 이번 diff에 없다.

## 검증 계획

1. `pytest tests/forge/domain/inner_loop -q` — 값 객체, evaluation 상태 전이와
   직렬화 제약.
2. `pytest tests/forge/adapters/outbound/events tests/forge/adapters/outbound/policy -q`
   — JSONL append, UTC 날짜 파티션, 임시 경로, snapshot 계약.
3. `pytest tests/forge/application/conversation tests/forge/runtime -q` — 이벤트
   순서, 오류 경로, pain 구성값, Episode 저장, 기존 대화 상태 회귀.
4. `pytest tests/forge/adapters/inbound -q` — CLI 출력 및 error path.
5. `ruff check src/forge tests/forge && mypy src/forge`.
6. `pytest -q` — 레거시 테스트를 포함한 전체 회귀. 실패가 기존 레거시 독립성 문제면
   새 변경과 분리해 보고한다.
7. 파일 기반 smoke test로 JSONL을 파싱해 한 성공 요청의 여섯 이벤트와 공통
   `session_id`/`episode_id`/`request_id`, 증가하는 sequence를 확인한다. UTC 자정
   경계 timestamp의 파일명도 확인한다.

## 위험과 완화

| 위험 | 완화 |
| --- | --- |
| L0가 장기 기억 또는 대화 checkpoint로 오해됨 | architecture 문서와 CLI help에 상태 수명을 분리해 명시한다. |
| 로그가 모델 응답·시스템 프롬프트를 중복해 민감 정보와 저장량을 키움 | event payload allowlist와 실패-event redaction 테스트를 둔다. |
| 로그 기록 실패를 숨겨 감사 근거가 끊김 | EventLog 오류를 명시적으로 호출자에게 전달하고 테스트한다. |
| 로컬 시간대와 UTC 파일 파티션이 달라 감사 조회가 어긋남 | 파일 경로를 write 시각이 아닌 `occurred_at`의 UTC 날짜에서만 계산하고 자정 경계 테스트를 둔다. |
| 미래 L4/L5 구조를 성급히 구현 | 이번 ConstraintsProvider는 인터페이스와 최소 config snapshot만 제공한다. |
| 모델 응답을 작업 성공으로 오인 | Evaluation에서 model-call outcome과 task status를 분리하고, 검증 근거가 없으면 해당 component를 `null`/`not_evaluated`, task status를 `Partial`로 기록한다. |
| CIB 미구현인데 통과 점수를 기록 | `not_evaluated`를 명시하고 promotion eligibility를 `quarantined`로 고정한다. |
| 레거시 agent 모델을 무비판적으로 재사용 | 새 문서의 independent pain/CIB 계약으로 새 domain 모델을 작성한다. |

## 중단 조건과 후속 작업

이 계획은 L0만의 구현이 아니라, 결정론적 Evaluation/Reflection과 immutable L1
Episode JSON 정본이 실제 메시지 처리 경로에서 함께 검증되면 끝난다. 다음 계획에서 ChromaDB 저장·검색을
연결하고, 그 뒤 L4 CIB 구현이 준비되면 `not_evaluated` 상태를 실제 안전 평가로
대체한다. DB-backed conversation checkpoint와 도구 실행은 이 후속 범위와 독립적으로
결정한다.
