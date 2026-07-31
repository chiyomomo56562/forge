# 헥사고날 아키텍처 기반 L1 메모리 구현 계획

## 목표와 범위

`forge`에 L1 Episodic Memory를 추가한다. 완성된 `Episode`를 저장하고, 자연어 질의와
구조화 필터로 검색하며, SQLite 정본과 Chroma 검색 인덱스의 불일치를 복구할 수 있어야
한다.

이번 계획은 **메모리 저장소 자체**를 구현하는 계획이다. Inner Loop, L0 이벤트 writer,
자동 평가·반성 producer, L2/L3 승격, CLI 관리 명령은 구현하지 않는다. 이들은 나중에
inbound adapter 또는 application use case로 `EpisodeRepository`를 호출한다.

설계 기준은 현재 Forge의 경계와 같다.

- application은 `Protocol` 포트에만 의존한다.
  - `src/forge/application/conversation/receive_initial_input.py:1-3`
  - `src/forge/ports/outbound/model_gateway.py:1-5`
- 구체 클래스의 조립은 bootstrap만 담당한다.
  - `src/forge/bootstrap/container.py:1-20`
- inbound CLI는 application service만 호출하고 runtime/저장소를 직접 알지 않는다.
  - `src/forge/adapters/inbound/cli.py:10-11, 97-120`

메모리 데이터·물리 스키마의 정본은
[`docs/memory/implementation-design.md`](../../docs/memory/implementation-design.md)다.
이 계획은 그 스키마를 어떤 헥사고날 경계로 구현할지를 결정한다.

## 경계와 의존성

```text
inbound adapter (현재 없음; 이후 CLI/API/Inner Loop)
  -> application.memory
       PersistEpisodeService
       SearchEpisodesService
       ReindexEpisodesService
  -> outbound ports
       EpisodeRepository
       (Clock은 outbound adapter 내부 의존성)
  -> adapters.outbound.memory
       SqliteChromaEpisodeRepository
         -> SQLite episode store (정본)
         -> Chroma episode index (파생 인덱스)
  -> bootstrap.container (실제 경로·Chroma client 조립)
```

### 책임 분리

| 계층 | 책임 | 금지 사항 |
| --- | --- | --- |
| `domain.memory` | immutable Episode 값, 평가 불변조건, supersession 규칙의 순수 검증 | `sqlite3`, `chromadb`, 파일 경로, 환경변수 import |
| `application.memory` | 명령 검증, port 호출, use case 반환값 구성 | SQL, Chroma metadata, collection 이름, retry 구현 |
| `ports.outbound` | application이 요구하는 저장·검색 계약 | 어댑터 구현체 import |
| `adapters.outbound.memory` | SQLite DDL/transaction, Chroma projection, 상태 전이, 재색인 | inbound/CLI import |
| `bootstrap` | config를 읽어 concrete adapter와 service를 조립 | domain 규칙 재구현 |

`SqliteChromaEpisodeRepository`는 **하나의 outbound adapter**다. SQLite와 Chroma를
둘로 나눈 port로 application에 노출하지 않는다. 정본 commit → projection → index state
전이는 infrastructure 복구 정책이며 application의 업무 규칙이 아니기 때문이다.

## 포트와 도메인 계약

### Domain 모델

**추가 파일**

- `src/forge/domain/memory/models.py`
- `src/forge/domain/memory/validation.py`
- `src/forge/domain/memory/__init__.py`

**값 객체**

- `Episode`
- `ExecutionResult`
- `Evaluation`
- `Reflection`
- `EpisodeSearchFilters`
- `EpisodeSearchHit`
- `IndexState` (`pending`, `indexed`, `stale`, `failed`)
- `CibEvaluationStatus` (`passed`, `failed`, `not_evaluated`)
- `PersistEpisodeResult` (`episode_id`, `index_state`)

Domain validation이 강제할 규칙:

1. score와 ratio는 `None` 또는 0.0~1.0이다.
2. `execution_outcome`은 `completed|failed|halted`, 평가 `status`는
   `Success|Partial|Failure`다. `failed + Success` 조합은 불가하다.
3. CIB가 `not_evaluated`이면 score는 `None`, promotion은 `quarantined`다.
4. nullable 평가 metric은 값이 없을 때 정확히 하나의 reason을 갖고, 값이 있으면
   reason이 없다.
5. `evidence_complete=True`이면 적어도 한 event ref, non-null success score,
   평가된 CIB가 있어야 한다.
6. `supersedes_episode_id`는 자기 자신을 가리킬 수 없다. 기존 체인 순환과 분기는
   repository 조회가 필요한 규칙이므로 adapter transaction에서 추가로 검증한다.

### Outbound port

**추가 파일**

- `src/forge/ports/outbound/episode_repository.py`
- 갱신: `src/forge/ports/outbound/__init__.py`

```python
class EpisodeRepository(Protocol):
    def save(self, episode: Episode) -> PersistEpisodeResult: ...
    def get(self, episode_id: str) -> Episode | None: ...
    def resolve_latest_episode(self, episode_id: str) -> Episode | None: ...
    def search(
        self, query: str, *, top_k: int, filters: EpisodeSearchFilters
    ) -> list[EpisodeSearchHit]: ...
    def list_after(self, created_after: datetime, *, filters: EpisodeSearchFilters) -> list[Episode]: ...
    def reindex(self, *, episode_ids: Sequence[str] | None = None) -> ReindexResult: ...
```

`EpisodeRepository`는 저장 성공 후 Chroma가 실패한 경우에도 `save()` 실패로 만들지
않는다. SQLite 정본이 commit된 것은 성공이며, `PersistEpisodeResult.index_state`가
`failed` 또는 `stale`로 즉시 관찰 가능해야 한다. SQLite 정본 저장 자체가 실패하면
예외를 발생시킨다.

### Application use cases

**추가 파일**

- `src/forge/application/memory/persist_episode.py`
- `src/forge/application/memory/search_episodes.py`
- `src/forge/application/memory/reindex_episodes.py`
- `src/forge/application/memory/__init__.py`

Use case는 각각 command DTO를 받고 port를 호출한다. 테스트에서 fake repository로
검증할 수 있어야 하며, Chroma/SQLite를 생성하지 않는다. M1에는 inbound port 또는 CLI
command를 만들지 않는다. 현재는 bootstrap으로 service를 만들고 adapter integration
test에서 직접 호출한다.

## Outbound adapter 설계

### 파일 배치

```text
src/forge/adapters/outbound/memory/
  __init__.py
  sqlite_chroma_episode_repository.py  # EpisodeRepository 구현, transaction orchestration
  sqlite_episode_store.py              # DDL, SQL row mapping, SQLite only
  chroma_episode_index.py              # document/metadata projection, Chroma only
  settings.py                          # immutable MemorySettings
```

`sqlite_episode_store.py`는 Chroma를 import하지 않고, `chroma_episode_index.py`는
SQLite connection을 받거나 SQL을 실행하지 않는다. 두 구현의 순서와 오류 분류는
`sqlite_chroma_episode_repository.py`만 소유한다.

### 저장 알고리즘

1. application이 검증된 `Episode`를 adapter에 전달한다.
2. adapter는 SQLite transaction에서 `supersedes` 대상 존재·단일 successor·순환을
   검증한다.
3. `episodes` 행을 입력 `Episode`의 `evidence_complete` 값 그대로,
   `index_state='pending'`으로 insert하고 event refs/evaluation reasons를 insert한다.
4. 전체 cross-table 불변조건을 defensive validation으로 검사한다. 불일치하면
   transaction을 rollback하고, 일치하면 commit한다. adapter는 domain 값인
   `evidence_complete`를 계산하거나 변경하지 않는다. Episode 내용은 이후 수정하지
   않는다.
5. Chroma projection upsert를 시도한다.
6. 성공 시 SQLite의 운영 필드만 `indexed`, `indexed_at=UTC now`로 update한다.
7. 새 Episode의 최초 projection 실패는 `failed`로 update한다. `stale`은 Chroma record가
   존재하지만 현재 `projection_version` 또는 `embedding_model_id`와 맞지 않는 경우다.
   그러한 Episode의 재색인 실패는 `stale`로 유지한다. `reindex()`가 동기적으로 복구한다.

이 알고리즘은 outbox, background worker, CQRS를 도입하지 않는다.

### 검색 알고리즘

1. `task_category`, `status`, `promotion_eligibility`, `evidence_complete`처럼 Chroma
   flat metadata로 표현되는 scalar filter는 Chroma `where` 조건으로 먼저 적용한다.
2. `candidate_k = max(top_k * 5, 20)`으로 Chroma 후보 `(episode_id, similarity)`를
   받는다.
3. 후보마다 SQLite에서 `resolve_latest_episode(id)`를 호출한다. 보정본은 원본
   similarity를 승계한다.
4. 같은 최종 Episode로 해석된 후보는 최고 similarity 하나만 남긴다.
5. SQLite 정본을 hydrate하고 supersession, `index_state`, 날짜 범위, 정본 무결성 등
   Chroma가 판정할 수 없는 filter를 적용한다.
6. similarity 내림차순, 동점이면 `created_at` 내림차순으로 정렬해 top-k를 반환한다.

M1은 후보가 부족할 때 추가 Chroma page를 조회하지 않는다. 따라서 API는 요청한
`top_k`보다 적은 결과를 반환할 수 있음을 명시한다.

`resolve_latest_episode()`는 successor를 따라가되 방문 ID 집합으로 순환을 검출한다.
direct successor가 둘 이상이면 데이터 오류로 실패한다. SQLite schema의
`UNIQUE(supersedes_episode_id)`는 새 분기를 막고, repository 검증은 기존 손상 데이터를
방어한다.

## 실제 저장 스키마와 migration

SQLite DDL은 `docs/memory/implementation-design.md`의 아래 테이블을 그대로 구현한다.

- `episodes`
- `episode_event_refs`
- `episode_evaluation_reasons`

**추가 파일**

- `src/forge/adapters/outbound/memory/sqlite_episode_store.py`
- `src/forge/adapters/outbound/memory/migrations/001_create_l1.sql`
- `tests/forge/adapters/outbound/memory/test_sqlite_episode_store.py`

Migration runner는 adapter 내부에 둔다. `schema_migrations(version INTEGER PRIMARY KEY,
applied_at TEXT NOT NULL)`을 만들고, transaction에서 미적용 version만 적용한다.
따라서 bootstrap이 repository를 여러 번 조립해도 migration은 idempotent하다.
application/domain은 migration 파일이나 테이블 이름을 알지 않는다.

Chroma collection은 `MemorySettings`에서 전달받는 path, collection name, embedding
함수, `projection_version`, `embedding_model_id`로 만들며, document와 flat metadata만
보관한다. Chroma metadata의 `cib_score=-1.0` sentinel은 반드시
`cib_evaluation_status`와 함께 쓴다. `NULL`의 의미는 SQLite에만 있다. 색인 상태는
저장된 projection/model version과 현재 `MemorySettings`를 비교해 `indexed` 또는
`stale`로 판정한다.

## Bootstrap과 설정

**갱신 파일**

- `src/forge/bootstrap/container.py`
- `src/forge/bootstrap/__init__.py`
- `config/memory.yml`

`build_episode_repository()`와 `build_memory_services()`를 제공한다. `config/memory.yml`
의 `episodic.chroma_path`, `collection_name`에 더해 SQLite 정본 경로
`episodic.sqlite_path: data/memory/episodic/episodes.sqlite3`를 명시한다.

`Clock`은 `SqliteChromaEpisodeRepository` 생성 시 주입해 `indexed_at`을 결정론적으로
테스트하게 한다. application/domain port로 노출하지 않는다.

`build_receive_message_service()`는 M1에서 메모리를 주입받지 않는다. 대화 runtime에
메모리 adapter를 직접 주입하면 현재 대화 기능과 메모리 producer/검색 정책을 섞게 되므로,
이 연결은 다음 계획에서 별도 use case로 추가한다.

## 테스트 계획

### Domain unit tests

`tests/forge/domain/memory/test_models.py`

- score/ratio bounds, CIB와 promotion 관계
- execution outcome과 evaluation status 관계
- nullable metric/reason 관계
- evidence-complete 조건

### Application unit tests

`tests/forge/application/memory/`

- fake `EpisodeRepository`로 persist/search/reindex delegation
- application 계층에 `sqlite3`, `chromadb`, adapter import가 없음을 import 경계 test로 확인

### Adapter integration tests

`tests/forge/adapters/outbound/memory/`

- SQLite save/get과 immutable row 보존
- supersedes 순환·분기 거부, `resolve_latest_episode()` 체인 해석
- UTC timestamp와 SQLite row mapping
- 최초 Chroma 실패 `pending → failed`
- projection/model version 불일치 `indexed → stale`
- stale Episode 재색인 성공 `stale → indexed`, 재색인 실패 시 stale 유지
- reindex 성공 `failed|stale → indexed`
- Chroma 후보에서 원본만 반환돼도 최신 보정본이 원본 score로 반환됨
- oversampling 이후 filter가 후보를 제외해도 가능한 top-k만 반환됨
- SQLite 정본으로 Chroma collection 재구축

실제 Chroma server/네트워크는 요구하지 않는다. in-memory 또는 fake Chroma index로
상태 전이와 projection을 검증하고, 별도 선택 integration test에서 PersistentClient를
임시 경로로 사용한다.

## 수용 기준

- domain/application/ports에는 `sqlite3`, `chromadb`, Chroma collection/path import가 없다.
- application은 하나의 `EpisodeRepository` port만 의존한다.
- SQLite가 L1 정본이고 Chroma는 완전히 재생성 가능하다.
- 보정 체인은 순환·분기할 수 없고, 검색은 최종 보정본을 반환한다.
- 색인 상태와 실패 복구 규칙이 테스트로 고정된다.
- search는 oversampling, score 승계, deduplication, filter, deterministic tie-break를
  수행한다.
- M1은 현재 conversation runtime 또는 CLI의 기존 동작을 변경하지 않는다.

## 구현 후 다음 단계

M1이 통과하면 별도 계획에서 `SearchEpisodesService`를 현재 메시지 처리 흐름의
**계획 전 읽기 전용 context injection**에 연결한다. Episode의 자동 생성·평가·반성·L0
기록은 그 다음 Inner Loop 계획에서 추가한다.
