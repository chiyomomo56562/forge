# L1 메모리 오류 분류와 재시도 계약 구현 계획

## 목표

L1 메모리의 잘못된 입력, 정본 저장 실패, supersession 무결성 실패, Chroma 색인
실패를 모두 `ValueError`로 뭉개지 않는다. 호출자(향후 Inner Loop/AI 포함)가 다음 중
어느 행동을 해야 하는지 **기계적으로** 알 수 있게 한다.

- Episode를 고쳐 다시 요청한다.
- 동일 요청을 잠시 후 다시 시도한다.
- SQLite 저장은 성공했으므로 나중에 재색인한다.
- 재시도하지 말고 운영자/사용자에게 실패를 보고한다.

범위는 L1 domain, application, outbound adapter의 계약과 테스트다. 실제 Inner Loop의
자동 재시도, 사용자용 CLI 메시지, background reindex worker는 이번 범위에 넣지 않는다.

## 현재 문제와 결정

현재 `src/forge/domain/memory/models.py`와
`src/forge/adapters/outbound/memory/sqlite_chroma_episode_repository.py`는 서로 성격이
다른 오류를 모두 `ValueError`로 발생시킨다. 따라서 호출자가 입력 수정과 일시 장애
재시도를 구별할 수 없고, repository는 Chroma의 예상 가능한 실패와 프로그래밍 버그까지
동일하게 다룬다.

다음 두 경로를 명확히 분리한다.

```text
SQLite 정본 commit 성공
  -> Chroma projection 실패
  -> 예외가 아니라 PersistEpisodeResult(index_state=failed|stale)
  -> 호출자는 재색인 작업을 예약/보고할 수 있음

SQLite 정본 commit 실패 또는 입력/무결성 위반
  -> typed exception
  -> retry_disposition으로 repair / retry / stop을 결정
```

즉, `PersistEpisodeResult`는 **저장된 Episode의 색인 상태**를 나타내고, 예외는
**정본 저장 요청 자체가 완료되지 않은 경우**에만 사용한다.

## 오류 모델

### 파일과 의존성

다음 파일을 추가한다.

- `src/forge/domain/memory/errors.py`
- `src/forge/adapters/outbound/memory/errors.py`

`domain.memory.errors`에는 순수한 도메인 계약 위반만 둔다. `sqlite3`, Chroma, OS 예외를
알지 않는다. SQLite/Chroma에서만 알 수 있는 장애는 adapter error로 둔다. 이름이 Python
내장 `MemoryError`와 충돌하지 않도록 최상위 이름은 `MemoryOperationError`로 한다.

```text
MemoryOperationError                         # application boundary가 잡는 공통 기반
├── MemoryValidationError                     # domain/application: Episode·query·filter 불변조건 위반
├── EpisodeIntegrityError                     # adapter: duplicate, missing parent, cycle, branch
├── RetryableMemoryOperationError             # adapter: SQLite busy/locked, Chroma 일시 장애
└── MemoryInfrastructureError                 # adapter: migration, 권한, 예상 밖의 영속화 실패
```

`EpisodeIndexUnavailableError`는 이 public 계층에 속하지 않는 adapter 내부 제어 신호다.
`ChromaEpisodeIndex`만 이를 발생시키며 repository boundary를 절대 넘지 않는다.

```python
class EpisodeIndexUnavailableError(Exception):
    """Internal adapter signal; never escapes repository boundary."""
```

repository는 이 신호를 `save()`/`reindex()`에서는 `FAILED` 또는 `STALE` 결과로,
`search()`에서는 `RetryableMemoryOperationError`로 변환한다.

기존에 계획했던 `RetryablePersistenceError`라는 이름은 검색 장애까지 포괄할 수 없으므로
`RetryableMemoryOperationError`로 바꾼다. SQLite write의 일시 장애와 Chroma
upsert/query의 일시 장애를 같은 재시도 정책으로 노출하되, `code`로 원인을 구분한다.

각 오류는 최소한 아래의 안정적인 필드를 가진다. 호출자는 문자열 메시지를 파싱하지
않는다.

```python
class RetryDisposition(StrEnum):
    REPAIR_INPUT = "repair_input"
    RETRY_LATER = "retry_later"
    DO_NOT_RETRY = "do_not_retry"

class MemoryOperationError(Exception):
    retry_disposition: RetryDisposition

    def __init__(self, *, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code       # 예: "episode.invalid_id", "sqlite.temporarily_unavailable"
        self.safe_message = safe_message

class MemoryValidationError(MemoryOperationError, ValueError):
    retry_disposition = RetryDisposition.REPAIR_INPUT

class EpisodeIntegrityError(MemoryOperationError):
    retry_disposition = RetryDisposition.DO_NOT_RETRY

class RetryableMemoryOperationError(MemoryOperationError):
    retry_disposition = RetryDisposition.RETRY_LATER

class MemoryInfrastructureError(MemoryOperationError):
    retry_disposition = RetryDisposition.DO_NOT_RETRY
```

원인이 되는 외부 예외는 `raise ... from exc`로 보존하되, 그 원문을 `safe_message`에
복사하지 않는다. `Exception.__str__()`은 항상 safe message만 반환한다. DB 경로·환경
정보·provider 내부 메시지가 inbound 응답에 새지 않도록 하고, 내부 원인은 exception
chaining과 운영 로그에서만 확인한다.

### 오류별 호출자 행동

| 오류 | `code` 예 | 재시도 처리 | 정본 commit |
| --- | --- | --- | --- |
| `MemoryValidationError` | `episode.invalid_evidence`, `search.invalid_top_k` | 입력/평가/호출 인자를 고친 뒤 새 요청 | 없음 |
| `EpisodeIntegrityError` | `episode.already_exists`, `episode.id_conflict`, `supersession.cycle` | 재시도 금지; 현재 데이터/요청을 수정 | 없음 |
| `RetryableMemoryOperationError` | `sqlite.temporarily_unavailable`, `chroma.query_unavailable` | 동일 요청을 bounded backoff로 재시도 가능 | SQLite write이면 없음(rollback 보장), search이면 해당 없음 |
| `MemoryInfrastructureError` | `sqlite.migration_failed`, `sqlite.write_failed` | 재시도 금지; 운영 보고 | 성공 결과가 없으면 완료로 가정하지 않음 |
| `PersistEpisodeResult(FAILED)` | `index_state=failed` | 저장 재시도 금지; `reindex()`을 나중에 호출 | 성공 |
| `PersistEpisodeResult(STALE)` | `index_state=stale` | 저장 재시도 금지; 현재 projection 버전으로 `reindex()` | 성공 |

`RetryableMemoryOperationError`의 AI 재시도는 exception 자체가 수행하지 않는다. 후속
orchestrator가 `retry_disposition`을 보고 최대 횟수·backoff·idempotency 정책을 적용한다.
M1 repository는 중복 Episode ID를 성공으로 바꾸지 않는다.
동일한 내용의 Episode를 같은 ID로 재전송했더라도 성공으로 바꾸지 않고
`episode.already_exists`로 실패시킨다. 같은 ID에 다른 내용이 들어온 경우는
`episode.id_conflict`로 구분한다. infrastructure error 뒤 동일 ID를 재시도할 때는
호출자가 먼저 재조회해 저장 완료 여부를 판단한다.

동일성 비교는 SQLite 운영 필드가 아니라 hydrate한 기존 `Episode`와 입력 `Episode`의
immutable 정본 필드에만 적용한다. Episode 도메인 필드, event refs, evaluation reasons는
비교에 포함하고 `index_state`, `indexed_at` 등 projection 운영 필드는 제외한다. DB row의
원시 표현을 직접 비교하지 않는다.

## 구현 순서

### 1. domain validation을 typed error로 교체

대상: `src/forge/domain/memory/models.py`, 새 `errors.py`,
`src/forge/domain/memory/__init__.py`

1. `MemoryValidationError`를 만들고 `code`, `safe_message`,
   `RetryDisposition.REPAIR_INPUT`을 클래스 상수로 고정한다.
2. `_require_utc`, `_validate_score`, `EvaluationReason`, `ExecutionResult`,
   `Evaluation`, `Episode`의 현재 `ValueError`를 각각 의미 있는 code의
   `MemoryValidationError`로 교체한다.
3. 공백만 있는 `query`, `top_k <= 0`, 잘못된 search filter 값은
   `MemoryValidationError`로 분류한다. 공백 query의 안정 code는
   `search.invalid_query`이고 `REPAIR_INPUT`이다.
4. 순수 값 객체 검증은 계속 생성 시점에 수행한다. AI가 재시도할 수 있다는 이유로
   불변조건을 느슨하게 하거나 invalid Episode를 SQLite에 저장하지 않는다.

### 2. repository 무결성 오류와 SQLite 오류를 분리

대상: `src/forge/adapters/outbound/memory/sqlite_chroma_episode_repository.py`,
`sqlite_episode_store.py`, 새 adapter `errors.py`

1. duplicate ID는 기존 row와 입력 Episode가 동일하면 `episode.already_exists`, 내용이
   다르면 `episode.id_conflict`로 구분한다. superseded parent 부재, successor branch,
   cycle, 손상된 successor reference는 각각 `EpisodeIntegrityError`로 바꾼다. 이들은
   `DO_NOT_RETRY`다.
2. SQLite write transaction은 명시적으로 rollback되도록 유지한다. `sqlite3.OperationalError`
   중 일시적인 `busy`/`locked`만
   `RetryableMemoryOperationError(code="sqlite.temporarily_unavailable")`로 변환한다.
3. SQLite `IntegrityError`는 어떤 제약이 깨졌는지 adapter가 식별 가능한 경우
   `EpisodeIntegrityError`로 변환하고, 그렇지 않으면 `MemoryInfrastructureError`로
   감싼다.
4. migration 실패, 권한/디스크 문제, row mapping 실패, 예상하지 못한 SQLite 오류는
   `MemoryInfrastructureError`로 감싼다. `sqlite.write_failed_before_commit`으로 확인할
   수 있는 write 실패만 이 code를 사용한다. 별도 commit-outcome-unknown 예외는 만들지
   않는다. repository가 성공 결과를 반환하지 않으면 호출자는 저장 완료를 가정하지 않는다.
   변환 범위는 `sqlite3.Error`와 `OSError`처럼 외부 경계에서 발생하는 예외로 제한하며,
   `except Exception`으로 repository 자체의 프로그래밍 오류를 감싸지 않는다.
5. 실제 exception message가 환경별로 달라질 수 있으므로 busy/locked 판정은 한 곳의
   작은 helper로 제한하고, 테스트에서 그 helper와 변환 결과를 고정한다.
6. `SqliteEpisodeStore.__init__()`의 directory 생성, connection 생성, PRAGMA 설정,
   `_migrate()`는 repository method 호출 이전의 초기화 경계다. 이 전체 경계를
   `MemoryInfrastructureError(code="sqlite.initialization_failed" 또는
   `"sqlite.migration_failed")`로 변환하고 원인은 `raise ... from exc`로 보존한다.
   bootstrap은 이 typed error를 그대로 전파한다.
7. `state()`, `set_index_state()`, `set_indexed()`처럼 private adapter flow가 존재를
   전제하는 함수는 대상 누락을 raw `KeyError`로 노출하지 않는다. 존재하지 않음을
   `EpisodeIntegrityError(code="episode.not_found")`로 바꾸거나, 호출 전에 존재를
   보장하고 그 불변조건 위반은 `MemoryInfrastructureError`로 감싼다. 구현 시 한 가지
   정책을 선택해 모든 public repository 경로에서 일관되게 적용한다.

### 3. Chroma 실패는 결과 상태로 유지하되, catch 범위를 좁힘

대상: `chroma_episode_index.py`, `sqlite_chroma_episode_repository.py`

1. `ChromaEpisodeIndex`가 client 예외를 adapter 내부의
   `EpisodeIndexUnavailableError`로 정규화한다. 이 타입은 repository 밖으로 전파하지
   않는다. timeout, connection failure, rate
   limit, 일시적 provider unavailable만 unavailable로 분류하고 code는
   `chroma.upsert_unavailable` 또는 `chroma.query_unavailable`로 보존한다. invalid
   argument, schema/dimension mismatch, authentication/authorization, configuration
   error, programming error는 `MemoryInfrastructureError`로 변환한다. 구체적인
   Chroma 예외 클래스 매핑은 adapter 코드와 테스트에만 둔다.
2. `save()`는 새 Episode의 Chroma upsert 실패 시 SQLite `index_state`를 `failed`로
   전환하고 `PersistEpisodeResult(episode_id, FAILED)`를 반환한다.
3. `reindex()`는 **현재 상태가 `indexed`인 record만** projection/model version 불일치를
   검사해 `indexed → stale`로 표시한다. 이미 `failed`인 record는 버전이 달라도
   `failed`로 유지한다. stale Episode의 재색인 실패는 stale을 유지하고, 최초 색인 또는
   failed Episode 재색인의 실패는 failed를 유지한다.
4. Chroma 실패로 인한 색인 상태 update 자체가 SQLite에서 실패하면 결과를 위조하지
   않고 `RetryableMemoryOperationError` 또는 `MemoryInfrastructureError`를 발생시킨다.
5. 코드 버그(`TypeError`, validation bug 등)를 Chroma 장애로 삼켜 `FAILED` 결과로
   바꾸지 않는다. catch 대상은 정규화된 index unavailable 오류 또는 명시적 Chroma
   client 오류뿐이다.
6. `search()`의 `self._index.query()`도 동일한 분류를 사용한다. 일시 query 장애는
   `RetryableMemoryOperationError(code="chroma.query_unavailable")`로 repository 밖에
   전파한다. 검색은 SQLite 정본을
   변경하지 않았으므로 `PersistEpisodeResult` 같은 성공 결과로 바꾸지 않는다. 잘못된
   Chroma 설정/권한/collection 계약은 `MemoryInfrastructureError`로 전파한다.

### 4. application 경계를 얇게 유지

대상: `src/forge/application/memory/*.py`,
`src/forge/ports/outbound/episode_repository.py`

1. `EpisodeRepository.save() -> PersistEpisodeResult` 계약은 유지한다.
2. application service는 typed exception을 문자열로 바꾸거나 재분류하지 않고 그대로
   전파한다. 이는 inbound adapter 또는 미래 AI orchestrator가 `retry_disposition`을
   해석할 수 있게 한다.
3. application이 필요로 하는 상태는 결과 객체의 `index_state`로 충분하다. 이번 단계에
   `retry_hint` 같은 중복 필드를 `PersistEpisodeResult`에 추가하지 않는다.
4. future inbound adapter의 매핑 계약만 문서화한다.
   - `REPAIR_INPUT`: 구조화된 code와 safe message를 producer/AI에 전달
   - `RETRY_LATER`: 일시 실패로 분류해 bounded retry queue에 전달
   - `DO_NOT_RETRY`: 사용자에게 내부 원인 없이 실패를 표시하고 운영 로그에 cause 기록
   - `FAILED`/`STALE` 결과: 저장 성공 응답에 색인 지연을 포함하고 reindex 대상으로 남김

## 테스트 계획

### Domain unit tests

대상: `tests/forge/domain/memory/test_models.py`

- invalid ID, UTC 위반, score 범위, evidence/CIB/reason 불변조건마다
  `MemoryValidationError`와 정확한 `code`, `REPAIR_INPUT`을 검증한다.
- valid Episode 생성은 기존과 동일하게 성공한다.

### Repository integration tests

대상: `tests/forge/adapters/outbound/memory/test_sqlite_chroma_episode_repository.py`

- 동일 내용 duplicate와 ID conflict, missing superseded parent, cycle, branch가
  `EpisodeIntegrityError`와 정확한 code, `DO_NOT_RETRY`를 낸다.
- 공백 query와 `top_k <= 0`이 각각 `MemoryValidationError` 및 안정 code를 낸다.
- SQLite locked/busy를 store seam 또는 sqlite exception stub으로 재현해
  `RetryableMemoryOperationError`와 rollback(episode row 없음)을 검증한다.
- 새 Episode의 Chroma upsert 실패는 예외가 아니라 `PersistEpisodeResult(..., FAILED)`이고
  SQLite Episode는 남는지 검증한다.
- stale Episode reindex 실패는 `STALE`로 유지되는지, failed Episode 재시인 실패는
  `FAILED`로 남는지 검증한다.
- Chroma adapter의 예상 밖 코드 오류는 index failure로 삼켜지지 않고 적절한 typed
  infrastructure error가 되는지 검증한다.
- Chroma query의 timeout/rate-limit은 `RetryableMemoryOperationError`로, 잘못된
  collection/인증/invalid argument는 `MemoryInfrastructureError`로 전파되는지 검증한다.
- store 초기화·migration 실패와 `state()` 대상 누락에서 raw `sqlite3`/`KeyError`가
  public repository 경계 밖으로 새지 않는지 검증한다.

### Application tests

대상: `tests/forge/application/memory/test_services.py`

- fake repository가 던지는 각 `MemoryOperationError`를 application service가 보존해
  전달하는지 검증한다.
- `PersistEpisodeResult(FAILED|STALE)`는 예외로 변환하지 않고 그대로 반환하는지 검증한다.

## 검증 및 완료 기준

1. L1 memory의 public domain/application/repository 경계에서 plain `ValueError`,
   `KeyError`, raw `sqlite3` 또는 Chroma client exception이 직접 발생하지 않는다. 입력
   오류는 `ValueError`를 상속하는 `MemoryValidationError`로만 노출된다.
   표준 라이브러리 validation error를 의도적으로 전파해야 하는 경우만 코드 주석으로
   이유를 남긴다.
2. `save()`의 SQLite commit 성공/실패와 Chroma 상태가 위 표의 계약대로 관찰된다.
3. Domain은 SQLite/Chroma import 없이 유지되고, application은 adapter 구현을 import하지
   않는다.
4. 다음을 실행한다.

```bash
.venv/bin/ruff check src/forge tests/forge
.venv/bin/pytest tests/forge -q
```

`mypy`는 현재 Python 3.11 project 설정과 실행 환경의 NumPy stub(Python 3.12+ 문법)
불일치 때문에 프로젝트 검사 전 실패한다. 이 계획의 구현 완료 조건에는 포함하지 않으며,
별도 환경 정비 작업으로 다룬다.

## 의도적으로 하지 않는 것

- AI가 repository 내부에서 무한 또는 즉시 재시도하는 기능
- exception message 기반의 retry 판단
- Chroma 실패를 SQLite 저장 실패로 되돌리는 분산 transaction
- background job/outbox, 재시도 횟수 저장, dead-letter queue
- 현재 conversation CLI에 메모리 오류 표현을 억지로 추가하는 변경

## 후속 문서 정합화

구현 후에는 producer가 오래된 Chroma-정본 의사 코드를 따르지 않도록 다음 문서를
별도 문서 변경으로 동기화한다. 이는 이번 오류 taxonomy 구현의 source 변경 범위에는
포함하지 않지만, Inner Loop 구현을 시작하기 전 완료해야 한다.

- `docs/inner_loop/06_l1_storage_schema.md`: Chroma를 주 저장소로 설명하는 문장과
  `collection.add()` 직접 저장 의사 코드를 SQLite 정본 →
  `EpisodeRepository.save()` → Chroma projection 순서로 교체한다. `index_state`와
  재색인 계약을 추가한다.
- `docs/inner_loop/05_*`: L1 producer가 storage adapter/Chroma를 직접 호출하지 않고
  application service를 호출하도록 맞춘다.
- `docs/memory/implementation-design.md`: SQLite 정본, `pending/indexed/stale/failed`,
  `indexed → stale`의 한정 조건을 기준 문서로 유지한다.
