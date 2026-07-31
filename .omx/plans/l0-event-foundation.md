# L0 실행 이벤트 기반과 L1 Episode 연결 계획

## 목표

Inner Loop의 의미 있는 각 노드 전이에서 append-only L0 이벤트를 기록하고, 한 번의
Inner Loop가 끝날 때 그 이벤트 ID들을 근거로 불변 L1 `Episode`를 하나 만든다. L0는
감사·재생성·디버깅을 위한 원본 실행 기록이고, L1은 검색·반성·후속 학습에 쓰는 평가된
요약이다. L0 여러 개와 L1 하나는 동일한 `session_id`와 사전 할당한 `episode_id`로
연결한다.

이 계획은 새 `src/forge` 경계에만 적용한다. 구형 `src/agent`의 `EventLogger`와
orchestrator는 참조 자료일 뿐, 두 저장 경로를 병렬로 확장하지 않는다.

## 확인된 기반과 제약

- L1 `Episode`는 이미 `session_id`, `episode_id`, `raw_event_refs`를 가지며 참조 ID의
  중복을 금지한다. [models.py](src/forge/domain/memory/models.py)의 `Episode`를 L0→L1
  연결의 정본 계약으로 사용한다.
- `SqliteChromaEpisodeRepository.save()`는 SQLite 정본을 먼저 저장하고 Chroma는 검색용
  projection으로 관리한다. 따라서 L0는 Chroma에 저장하지 않고 독립 append-only 저장소를
  사용한다. [sqlite_chroma_episode_repository.py](src/forge/adapters/outbound/memory/sqlite_chroma_episode_repository.py)
- 기존 설계 문서는 session 시작과 L1 영속화가 L0 이벤트를 남겨야 함을 규정한다.
  [00_session_setup.md](docs/inner_loop/00_session_setup.md),
  [05_episode_persistence.md](docs/inner_loop/05_episode_persistence.md),
  [06_l1_storage_schema.md](docs/inner_loop/06_l1_storage_schema.md)
- 현재 `src/forge`에는 Inner Loop 노드와 L0 producer가 없다. 따라서 L0만 먼저
  도입하더라도 기존 conversation CLI 동작은 바꾸지 않는다.

## 설계 결정

### 1. 이벤트 단위와 수명

L0는 **모든 함수 호출**이 아니라 다음의 의미 있는 노드/외부 경계마다 기록한다.

| 시점 | event type | 필수 내용 |
| --- | --- | --- |
| 세션 시작 | `session.started` | 요청의 안전한 요약, 고정된 설정/예산 식별자 |
| 계획 완료 | `plan.completed` | 계획 요약, 선택한 도구/단계, model·prompt 참조 |
| 도구/모델 호출 | `execution.started`, `execution.completed`, `execution.failed` | 호출 종류, 입력·출력 참조/요약, 지연·비용·오류 분류 |
| 검증·평가 | `evaluation.completed` | 점수, 근거, CIB 결과 |
| 반성 | `reflection.completed` | 네 반성 필드 |
| L1 결과 | `episode.persisted`, `episode.index_deferred`, `episode.failed` | episode ID, index state, 실패의 safe code |
| 세션 종료 | `session.completed`, `session.failed` | 최종 상태, 종료 원인 |

토큰 스트림, 대형 도구 출력, 비밀값은 이벤트 본문에 그대로 넣지 않는다. 정규화·마스킹된
요약과 content hash, 선택적 외부 artifact 참조만 저장한다. Event type별 payload schema
검증으로 이 규칙을 강제한다.

### 실행 이벤트를 단일 L1 `ExecutionResult`로 집계하는 규칙

하나의 Inner Loop에는 계획 단계·도구 호출·재시도로 여러 `execution.completed`와
`execution.failed` 이벤트가 생길 수 있다. `execution.completed` payload는 `summary`,
`outcome`, `tool_names`, `attempt`, `is_terminal`을 가진다. Finalize는 다음 reducer를
사용한다.

1. `is_terminal=true`인 `execution.completed` 이벤트가 정확히 하나여야 한다. 이는
   Inner Loop가 최종 실행 결론을 확정할 때 producer가 기록한다. 없거나 둘 이상이면
   `MemoryValidationError(code="l0.invalid_terminal_execution")`로 Finalize를 중단한다.
2. `Episode.execution.summary`와 `Episode.execution.outcome`은 terminal 이벤트에서만
   가져온다. 이전 실패·부분 결과의 요약으로 덮어쓰지 않는다.
3. `Episode.execution.tool_names`는 모든 `execution.completed`·`execution.failed`
   이벤트의 `tool_names`를 sequence 순서로 합친 ordered-unique tuple이다. 실패한
   시도에서 사용한 도구도 감사 가능한 L1 요약에 보존된다.
4. 이전 `execution.failed` 뒤 재시도가 terminal success로 끝나면 L1 outcome은 success다.
   실패 이벤트 자체와 retry 원인은 L0에 보존돼 L1 평가·반성의 근거가 된다.
5. terminal 이벤트가 `FAILED` 또는 `HALTED`여도 L1 생성은 가능하다. Finalize는 outcome을
   성공으로 바꾸지 않고 evaluation·reflection producer의 결과를 그대로 조합한다.

### 2. 식별자·순서·인과성

새 domain value `L0Event`는 최소 필드 `event_id`, `session_id`, `episode_id`,
`sequence`, `occurred_at`, `event_type`, `payload`, `causation_id`, `schema_version`을
가진다.

- `event_id`는 `evt_` 접두사의 UUID/ULID, `session_id`는 `ses_`, `episode_id`는
  `ep_`로 생성한다.
- session 안에서 `sequence`는 0부터 단조 증가하고, timestamp는 UTC여야 한다.
- `causation_id`는 이벤트를 발생시킨 직전 L0 이벤트 또는 요청 이벤트를 가리킨다.
- L1의 `raw_event_refs`는 **Episode 생성 시점까지** 발생한 근거 이벤트의 unique event
  ID를 sequence 오름차순으로 담는다. 포함 범위는 `session.started`, `plan.completed`,
  `execution.*`, `evaluation.completed`, `reflection.completed`다. L1 persistence 결과와
  session 종료 이벤트(`episode.persisted`, `episode.index_deferred`, `episode.failed`,
  `session.completed`, `session.failed`)는 포함하지 않는다.
- 따라서 `episode.persisted`가 자신을 만든 Episode의 근거가 되는 순환 참조는 만들지
  않는다. L1 생성 후에도 L0를 수정하거나 삭제하지 않는다.

### 3. 저장소와 내구성

새 `L0EventStore` port와 JSONL adapter를 만든다. 기본 경로는
`data/memory/working/sessions/<session_id>/events.jsonl`로 해 session 단위 재생성과
L1 참조 검증이 전체 날짜 파일 스캔 없이 가능하게 한다.

- append는 한 줄의 완전한 JSON을 기록하고 flush/fsync한 뒤 성공으로 반환한다.
- 임시 파일과 rename을 이용해 session manifest를 갱신한다. manifest에는 next sequence,
  생성 시각, schema version, 완료 상태를 둔다.
- JSONL의 마지막 불완전 행은 recovery에서 무시·기록하되, 중간 행 손상이나 sequence
  gap은 `MemoryInfrastructureError`로 중단한다.
- `episode.persisted` 이벤트는 L1 SQLite save 결과를 받은 뒤에만 append한다. 반대로
  L1 생성 전 발생한 이벤트는 L1 save 실패 뒤에도 감사 기록으로 남는다.

### 4. L0에서 L1을 만드는 application 흐름

새 `FinalizeEpisodeService`가 session manifest와 L0 이벤트를 읽는다. L1에 필요한
`ExecutionResult`, `Evaluation`, `Reflection`은 해당 완료 이벤트 payload에서 복원하고,
`raw_event_refs`에는 Finalize 이전의 근거 event ID만 sequence 순으로 넣는다.

| L0 payload 필드 | L1 대상 | 제공 규칙 |
| --- | --- | --- |
| `session.started.task_request` | `Episode.task_request` | 필수 |
| `session.started.task_category` | `Episode.task_category` | 필수; producer가 분류 결과를 제공 |
| `execution.completed.summary` | `Episode.execution.summary` | 필수; `is_terminal=true`인 정확히 한 이벤트에서만 사용 |
| `execution.completed.outcome` | `Episode.execution.outcome` | 필수; terminal 이벤트에서만 사용 |
| 모든 `execution.completed`·`execution.failed`의 `tool_names` | `Episode.execution.tool_names` | sequence 순 ordered-unique 합집합; 사용 도구가 없으면 빈 tuple |
| `evaluation.completed.<metric>` | `Episode.evaluation`의 nullable metric | 각 metric은 값 또는 대응 `reasons` 중 정확히 하나를 제공 |
| `evaluation.completed.reasons` | `Episode.evaluation.reasons` | 필수; 값이 없는 metric의 reason을 포함 |
| `evaluation.completed.cib_evaluation_status`, `cib_score` | `Episode.evaluation.cib_*` | 필수 |
| `evaluation.completed.status`, `retryable`, `promotion_eligibility` | 대응 `Episode.evaluation` 필드 | 필수 |
| `reflection.completed.what_worked`, `what_failed`, `next_hint`, `causal_condition` | `Episode.reflection` | 필수; 내용이 없으면 빈 문자열 |
| `evaluation.completed.evidence_complete` | `Episode.evidence_complete` | 필수; Finalize가 추론해 덮어쓰지 않음 |
| `plan.completed.pattern_candidate_id` | `Episode.pattern_candidate_id` | 선택; 없으면 `None` |
| Finalize context | `Episode.created_at`, `session_id`, `episode_id`, `schema_version` | session 시작 시 확정된 값 |

Finalize는 이 표의 필수 producer 필드가 없을 때 임의 기본값을 만들지 않고
`MemoryValidationError`로 실패시킨다. 선택 필드만 명시적으로 `None` 또는 빈 collection의
기본값을 사용한다.

```text
start session -> append session.started
for each inner-loop node:
  append node started/completed/failed event
append evaluation.completed and reflection.completed
build Episode(raw_event_refs=pre-finalize evidence event IDs)
PersistEpisodeService.handle(episode)
append episode.persisted | episode.index_deferred | episode.failed
append session.completed | session.failed
```

L1 저장 실패는 이미 생성된 L0를 되돌리지 않는다. `episode.failed`에는 typed exception의
`code`와 `safe_message`만 기록하며 cause나 환경 정보는 기록하지 않는다. L1이
`FAILED`/`STALE` index state로 성공한 경우는 `episode.index_deferred`를 기록하고,
SQLite 저장 완료 Episode로 취급한다.

### 5. 오류와 재시도 계약

- 잘못된 event envelope/payload/sequence는 `MemoryValidationError`로 노출한다.
- append·manifest·읽기 손상·I/O 실패는 `MemoryInfrastructureError`로 변환한다.
- 잠금/일시 I/O만 `RetryableMemoryOperationError(code="l0.temporarily_unavailable")`로
  분류한다. append 재시도는 같은 `event_id`에 대해 idempotent해야 한다.
- Finalize는 L1의 typed error를 재분류하지 않는다. `REPAIR_INPUT`은 세션 실패 이벤트를
  남기고 producer에 돌려주며, `RETRY_LATER`는 같은 episode ID로 재시도할 수 있다.

## 구현 단계

1. `src/forge/domain/memory/l0_events.py`와 `errors.py`에 `L0Event`, event type enum,
   session manifest value, UTC/ID/sequence/payload validation을 추가하고 domain exports를
   갱신한다.
2. `src/forge/ports/outbound/l0_event_store.py`에 append, list by session, get by ID,
   finalize manifest 계약을 정의한다. `src/forge/adapters/outbound/memory/jsonl_l0_event_store.py`
   에 atomic JSONL/manifest 구현과 typed I/O 변환을 추가한다.
3. `src/forge/application/memory/`에 `StartInnerLoopSessionService`,
   `RecordInnerLoopEventService`, `FinalizeEpisodeService`를 추가한다. Finalize는 L0 event
   schemas를 L1 domain values로 변환하고 `PersistEpisodeService`와 연결한다.
4. `src/forge/bootstrap/container.py`에 L0 store와 services 조립을 추가하되, 현재
   conversation CLI의 기본 경로에는 자동 실행을 연결하지 않는다. 후속 Inner Loop가
   명시적으로 session 서비스를 호출하도록 port를 노출한다.
5. `config/memory.yml`에 L0 root path, retention, 최대 inline payload bytes, redaction policy
   version을 추가한다. `config/agent.yml`의 working/session 경로와 한 가지 정본 경로로
   통합한다.
6. `docs/inner_loop/00_session_setup.md`, `05_episode_persistence.md`,
   `06_l1_storage_schema.md`를 실제 event schema, sequence, error/recovery, L0→L1
   finalization 흐름으로 동기화한다.

## 테스트와 수용 기준

1. Domain tests는 ID 접두사, UTC, unique/monotonic sequence, allowed event type, payload
   schema, 금지된 민감 필드가 `MemoryValidationError`와 안정 code를 내는지 검증한다.
2. JSONL adapter integration tests는 append 순서, restart recovery, partial-last-line 회복,
   duplicate `event_id` idempotency, corrupted middle line, I/O/lock 오류의 typed mapping을
   검증한다.
3. Finalize integration tests는 하나의 session에서 노드별 L0 이벤트 여러 개가 생기고,
   최종 L1의 `raw_event_refs`가 정확히 같은 sequence 순서를 보존하되 L1 persistence와
   session 종료 이벤트는 제외하는지 검증한다.
4. Finalize mapping tests는 표의 모든 필수 payload 필드가 대응 L1 필드로 정확히 복원되고,
   nullable metric의 값/reason 상호배타 규칙과 누락 필수 필드의 `MemoryValidationError`를
   검증한다.
5. Execution reducer tests는 다수의 완료·실패 이벤트에서 terminal summary/outcome과
   ordered-unique tool names가 정확히 집계되는지, terminal 이벤트가 없거나 둘 이상이면
   `l0.invalid_terminal_execution`으로 실패하는지 검증한다.
6. L1 SQLite 저장 실패에도 이전 L0 이벤트와 `episode.failed`가 남는지, Chroma 실패로
   `IndexState.FAILED`/`STALE`가 반환되면 L0에 `episode.index_deferred`가 남는지 검증한다.
7. 회귀 검증은 다음을 실행한다.

```bash
.venv/bin/ruff check src/forge tests/forge
.venv/bin/pytest tests/forge -q
```

## 위험과 완화

| 위험 | 완화 |
| --- | --- |
| 원본 로그에 secret/PII 유입 | typed payload allowlist, redactor, hash/artifact reference, 테스트 fixture 검사 |
| L0 append 실패가 작업을 멈춤 | 명시적 typed retry; 성공으로 위장하거나 L1만 저장하지 않음 |
| 재시도 중 이벤트 중복 | caller-supplied stable `event_id`, append idempotency, manifest sequence 검증 |
| L0와 L1의 불일치 | L1은 raw event ID 존재·순서 검증 뒤에만 생성; L1 결과 이벤트는 저장 결과 뒤에 기록 |
| 대형 로그로 인한 비용 | inline payload 상한, external artifact reference, retention/compaction 정책 |

## ADR

**결정:** 노드별 L0 이벤트와 Inner Loop당 하나의 L1 Episode를 채택한다.

**Drivers:** 실행 재현성, 평가·반성 재생성 가능성, 실패/재시도 감사, L1 검색 밀도.

**대안:** (1) 루프당 L0 하나만 저장 — 원인 분해와 재개가 어렵다. (2) L1만 저장 —
원본 근거와 재평가 가능성이 사라진다.

**결과:** 저장량과 redaction 책임은 늘지만, L1은 작고 안정적인 학습 단위로 유지된다.

**후속:** 이 계획 승인 후 `$ultragoal`을 기본 실행 관리로 사용한다. 병렬 구현이 필요하면
domain/port, JSONL adapter, application/finalize, tests·docs를 `$team`의 독립 lanes로 나눈다.
