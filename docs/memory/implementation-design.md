# Forge 메모리 구현 설계

## 결정과 첫 구현 범위

메모리는 Inner Loop와 독립적으로 먼저 구현한다. 첫 구현(M1)은 **L1 Episode의
정본 저장·의미 검색·필터 검색**까지다. Inner Loop는 나중에 완성된 `Episode`를
생산하는 호출자가 될 뿐, L1 저장소의 선행 조건이 아니다.

L0 이벤트는 M1의 전제 조건이 아니다. Episode의 `raw_event_refs`는 빈 배열을
허용한다. 다만 L0가 존재하는 producer는 이벤트 ID를 시간순으로 넣어야 한다.
L0가 없는 Episode는 검색·감사는 가능하지만, L2/L3 승격 증거로 사용하지 않는다.

### 정본과 인덱스

| 계층 | 정본 | 파생/인덱스 | M1 |
| --- | --- | --- | --- |
| L1 Episodic | `data/memory/episodic/episodes.sqlite3` | Chroma `episodes` collection | 구현 |
| L2 Semantic | GraphML + evidence SQLite | gpickle/선택적 vector index | 스키마만 확정 |
| L3 Procedural | SQLite + versioned skill file | registry JSON | 스키마만 확정 |
| L4 Constitution | Git 관리 YAML | 읽기 캐시 | 기존 파일 읽기만 |
| L5 Identity | SQLite + YAML | 없음 | 스키마만 확정 |

기존 문서가 L1 ChromaDB를 정본으로 설명하더라도, 이 설계에서는 **SQLite를 L1
정본, Chroma를 재생성 가능한 검색 인덱스**로 정한다. 이유는 Chroma metadata가
중첩 객체·배열을 보존하지 못하고, Episode의 보정 이력·이벤트 참조·정확한 필터
조회·마이그레이션을 안전하게 관리하기 어렵기 때문이다. Chroma가 손상되거나
임베딩 모델이 바뀌면 SQLite 정본에서 다시 색인한다.

## 공통 규칙

- 모든 시각은 UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SS.sssZ`)이다.
- ID는 외부 입력이 아닌 Forge가 생성하는 접두어 있는 UUID/ULID다.
  `ep_`, `ses_`, `req_`, `evt_`, `pc_`, `kn_`, `skill_`를 사용한다.
- 삭제가 아니라 상태 변경 또는 보정 레코드를 사용한다. L1 Episode는 update하지
  않으며 보정 시 새 Episode가 기존 Episode를 `supersedes_episode_id`로 가리킨다.
- 모든 SQLite 연결은 `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`을
  적용한다.
- 쓰기 순서는 SQLite 정본 commit → Chroma upsert → SQLite 색인 상태 갱신이다.
  Chroma 실패는 정본을 롤백하지 않는다. 최초 색인 실패는 `failed`, version 불일치로
  이미 `stale`인 Episode의 재색인 실패는 `stale`로 남긴다. 재색인 명령이 이를 복구한다.
- `stale`은 immutable Episode 내용 변경이 아니라, 현재 projection version 또는
  embedding model ID가 마지막 성공 색인 값과 달라진 상태다.

## L1 Episodic Memory

### 책임

L1은 한 작업의 **평가된 경험**을 저장하고, 현재 요청에 관련된 과거 경험을
검색한다. 모델의 원문 대화나 L0 전체 로그를 복사하는 저장소가 아니다.

M1에서는 `EpisodeRepository.save()`, `get()`, `search()`, `list_after()`와
`reindex()`만 구현한다. 실제 사용 흐름은 다음과 같다.

```text
Episode producer (현재는 fixture/admin API, 이후 Inner Loop)
  -> validate immutable Episode
  -> SQLite transaction: episode + refs + evaluation reasons
  -> Chroma upsert
  -> SQLite index_state update
  -> search(query, filters) -> Chroma candidates -> supersession resolve -> SQLite hydrate
```

### `episodes` 테이블

```sql
CREATE TABLE episodes (
    episode_id                  TEXT PRIMARY KEY,
    schema_version              INTEGER NOT NULL DEFAULT 1,
    session_id                  TEXT,
    request_id                  TEXT,
    created_at                  TEXT NOT NULL,

    task_request                TEXT NOT NULL,
    task_category               TEXT NOT NULL DEFAULT 'general',

    execution_summary           TEXT NOT NULL,
    execution_outcome           TEXT NOT NULL
        CHECK (execution_outcome IN ('completed', 'failed', 'halted')),
    tool_names_json             TEXT NOT NULL DEFAULT '[]',

    result_quality              REAL,
    requirement_coverage        REAL,
    verification_confidence     REAL,
    success_score               REAL,
    pain_index                  REAL,
    retry_ratio                 REAL,
    tool_error_ratio            REAL,
    budget_overrun_ratio        REAL,
    rework_ratio                REAL,
    human_intervention_ratio    REAL,
    cib_score                   REAL,
    cib_evaluation_status       TEXT NOT NULL
        CHECK (cib_evaluation_status IN ('passed', 'failed', 'not_evaluated')),
    status                      TEXT NOT NULL
        CHECK (status IN ('Success', 'Partial', 'Failure')),
    promotion_eligibility       TEXT NOT NULL
        CHECK (promotion_eligibility IN ('eligible', 'quarantined')),
    retryable                   INTEGER NOT NULL CHECK (retryable IN (0, 1)),

    what_worked                 TEXT NOT NULL,
    what_failed                 TEXT NOT NULL,
    next_hint                   TEXT NOT NULL,
    causal_condition            TEXT NOT NULL,
    has_reflection              INTEGER NOT NULL CHECK (has_reflection IN (0, 1)),

    pattern_candidate_id        TEXT,
    supersedes_episode_id       TEXT UNIQUE REFERENCES episodes(episode_id),
    evidence_complete           INTEGER NOT NULL CHECK (evidence_complete IN (0, 1)),
    index_state                 TEXT NOT NULL DEFAULT 'pending'
        CHECK (index_state IN ('pending', 'indexed', 'stale', 'failed')),
    indexed_projection_version  INTEGER,
    indexed_embedding_model_id  TEXT,
    indexed_at                  TEXT,

    CHECK (result_quality IS NULL OR result_quality BETWEEN 0.0 AND 1.0),
    CHECK (requirement_coverage IS NULL OR requirement_coverage BETWEEN 0.0 AND 1.0),
    CHECK (verification_confidence IS NULL OR verification_confidence BETWEEN 0.0 AND 1.0),
    CHECK (success_score IS NULL OR success_score BETWEEN 0.0 AND 1.0),
    CHECK (pain_index IS NULL OR pain_index BETWEEN 0.0 AND 1.0),
    CHECK (retry_ratio IS NULL OR retry_ratio BETWEEN 0.0 AND 1.0),
    CHECK (tool_error_ratio IS NULL OR tool_error_ratio BETWEEN 0.0 AND 1.0),
    CHECK (budget_overrun_ratio IS NULL OR budget_overrun_ratio BETWEEN 0.0 AND 1.0),
    CHECK (rework_ratio IS NULL OR rework_ratio BETWEEN 0.0 AND 1.0),
    CHECK (human_intervention_ratio IS NULL OR human_intervention_ratio BETWEEN 0.0 AND 1.0),
    CHECK (cib_score IS NULL OR cib_score BETWEEN 0.0 AND 1.0),
    CHECK (NOT (execution_outcome = 'failed' AND status = 'Success')),
    CHECK (
        (cib_evaluation_status = 'not_evaluated' AND cib_score IS NULL AND promotion_eligibility = 'quarantined')
        OR (cib_evaluation_status = 'passed' AND cib_score >= 0.95)
        OR (cib_evaluation_status = 'failed' AND cib_score < 0.95 AND promotion_eligibility = 'quarantined')
    )
);

CREATE INDEX idx_episodes_created_at ON episodes(created_at);
CREATE INDEX idx_episodes_category_created ON episodes(task_category, created_at DESC);
CREATE INDEX idx_episodes_promotion_created ON episodes(promotion_eligibility, created_at);
CREATE INDEX idx_episodes_index_state ON episodes(index_state);
CREATE INDEX idx_episodes_supersedes ON episodes(supersedes_episode_id);
```

`*_ratio` 다섯 컬럼이 pain index의 계산 근거다. `pain_index`만 저장하면 나중에
“왜 비용이 컸는가”를 구분할 수 없다. 평가할 수 없는 항목은 0으로 꾸며내지 않고
`NULL`과 별도 evaluation reason(아래 표)으로 표현한다.

`execution_outcome`과 `status`는 의도적으로 다르다.

| 필드 | 의미 | 예 |
| --- | --- | --- |
| `execution_outcome` | 실행의 기계적 종료 상태 | 모델이 답변을 반환했으면 `completed`, 예외면 `failed`, 정책상 중단이면 `halted` |
| `status` | 요구사항과 검증을 포함한 평가 결과 | 답변을 받았지만 검증 근거가 없으면 `Partial` |

따라서 두 값이 같아야 한다는 불변조건은 두지 않는다. `execution_outcome=failed`이면
`status=Success`만 금지한다. `completed`가 `Success`를 보장하지 않는 것이 핵심이다.

### `episode_event_refs`와 `episode_evaluation_reasons`

```sql
CREATE TABLE episode_event_refs (
    episode_id  TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE RESTRICT,
    ordinal     INTEGER NOT NULL CHECK (ordinal >= 0),
    event_id    TEXT NOT NULL,
    PRIMARY KEY (episode_id, ordinal),
    UNIQUE (episode_id, event_id)
);

CREATE TABLE episode_evaluation_reasons (
    episode_id  TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE RESTRICT,
    metric       TEXT NOT NULL CHECK (metric IN (
        'result_quality', 'requirement_coverage', 'verification_confidence',
        'success_score', 'pain_index', 'retry_ratio', 'tool_error_ratio',
        'budget_overrun_ratio', 'rework_ratio', 'human_intervention_ratio',
        'cib_score'
    )),
    reason_code  TEXT NOT NULL,
    detail       TEXT NOT NULL,
    PRIMARY KEY (episode_id, metric)
);
```

- `raw_event_refs`가 없으면 `episode_event_refs`는 0행이다.
- `episode_evaluation_reasons` 예: `verification_confidence / not_provided /
  "현재 producer가 검증 결과를 제공하지 않음"`.
- 모든 nullable 평가 metric은 다음 관계를 가져야 한다: metric이 `NULL`이면 정확히
  하나의 reason 행이 필요하고, metric에 값이 있으면 reason 행이 없어야 한다.
- `evidence_complete=1`은 최소 1개의 `episode_event_refs`, non-NULL
  `success_score`, `cib_evaluation_status != 'not_evaluated'`를 모두 요구한다.
  M1에서 수동/외부 producer가 넣는 불완전 Episode는 `0`이고 자동 승격에서 제외한다.

이 관계는 한 행의 `CHECK`만으로 강제할 수 없으므로 `EpisodeRepository.save()`가
단일 SQLite transaction에서 검증한다. 저장 절차는 (1) 입력 Episode의
`evidence_complete`를 포함한 값을 그대로 insert, (2) refs/reasons insert, (3) 전체
불변조건 검증, (4) 불일치면 rollback·일치면 commit이다. adapter는
`evidence_complete`를 계산하거나 변경하지 않는다. 이후 Episode 내용과 refs/reasons는
변경하지 않는다. `index_state`, indexed version, `indexed_at`만 운영 상태로 갱신할 수
있다.

### supersedes 체인 계약

`supersedes_episode_id`는 새 Episode가 보정하려는 **직전 Episode**를 가리킨다.
`UNIQUE` 제약으로 한 Episode에는 하나의 직접 보정본만 둘 수 있으므로 체인이
분기하지 않는다.
저장 전 repository는 다음을 검증한다.

1. 대상 Episode가 존재한다.
2. 새 Episode ID는 대상과 같지 않다.
3. 대상에서 `supersedes_episode_id`를 따라가며 새 Episode ID가 다시 나오면 저장을
   거부한다.
4. 방문한 ID를 집합으로 추적해 기존 데이터 자체에 순환이 발견돼도 조회를 무한
   반복하지 않고 integrity error로 실패한다.

예를 들어 `ep_3 → ep_2 → ep_1 → ep_3`은 거부한다. 내부 API는
`resolve_latest_episode(episode_id) -> Episode`를 제공하며, 입력 Episode를
supersede하는 가장 최근 descendant를 반환한다. 둘 이상의 direct descendant가
발견되면 보정 체인이 분기한 데이터 오류로 취급하고 결과를 임의 선택하지 않는다.

### Chroma `episodes` collection projection

Chroma에는 아래 세 종류만 저장한다.

```python
ids = [episode_id]
documents = [
    "[작업] ...\n[조건] ...\n[결과] ...\n[통했던 것] ...\n[실패·비용] ...\n[다음 힌트] ..."
]
metadatas = [{
    "schema_version": 1,
    "episode_id": "ep_...",
    "created_at": "2026-07-30T...Z",
    "task_category": "general",
    "status": "Partial",
    "success_score": 0.60,
    "pain_index": 0.15,
    "cib_score": -1.0,
    "cib_evaluation_status": "not_evaluated",
    "promotion_eligibility": "quarantined",
    "has_reflection": True,
    "evidence_complete": False,
    "projection_version": 1,
    "embedding_model_id": "configured-model-id",
    "supersedes_episode_id": ""
}]
```

Chroma가 `NULL`을 지원하지 않으므로 `cib_score=-1.0`을 sentinel로 쓰고 반드시
`cib_evaluation_status`와 함께 필터한다. SQLite에서만 `NULL`의 원래 의미를 유지한다.

### 색인 상태 전이

`index_state`는 SQLite 정본과 Chroma projection의 일치 상태다.

| 상태 | 의미 | 허용 전이 |
| --- | --- | --- |
| `pending` | SQLite에 저장됐지만 아직 Chroma upsert를 시도하지 않음 | `indexed`, `failed` |
| `indexed` | SQLite 정본·Chroma projection·projection/model version이 일치 | `stale` |
| `stale` | Chroma record는 있으나 현재 projection version 또는 embedding model ID와 불일치 | `indexed`, `stale` |
| `failed` | 최초 projection 생성에 실패해 사용할 Chroma record가 없음 | `indexed`, `failed` |

새 Episode는 transaction에서 항상 `pending`으로 commit한다. upsert 성공 후
`indexed`, `indexed_projection_version`, `indexed_embedding_model_id`, `indexed_at`을
갱신한다. 최초 upsert 실패는 `failed`다. 현재 설정의 projection version 또는 embedding
model ID가 마지막 성공 색인 값과 달라지면 `indexed → stale`로 표시하며, stale
Episode의 재색인 실패는 stale을 유지한다. M1에는 별도 index-job 테이블이나
background worker를 만들지 않는다. 명시적 `reindex()`가
`failed`/`stale`/선택한 `indexed` Episode를 동기적으로 처리한다.

### L1 조회 규칙

1. `task_category`, `status`, `promotion_eligibility`, `evidence_complete`는 Chroma
   scalar metadata `where` filter로 먼저 적용한다.
2. `search(query, top_k, filters)`는 `candidate_k = max(top_k * 5, 20)`으로 Chroma
   후보 `(episode_id, similarity_score)`를 얻는다.
3. 각 후보에 `resolve_latest_episode()`를 적용한다. 보정본은 원본 후보의
   similarity score를 승계한다. 같은 보정본으로 해석된 후보는 최고 score 하나로
   중복 제거한다.
4. SQLite에서 정본을 hydrate한 뒤 supersession, `index_state`, 복잡한 날짜 범위,
   정본 무결성 등 Chroma가 보장하지 않는 filter를 적용한다. 후보가 부족해도 M1에서는
   추가 페이지를 요청하지 않고 가능한 수만 반환한다.
5. 최종 순서는 similarity score 내림차순, 동점이면 Episode `created_at` 내림차순이다.
6. 기본값은 `has_reflection=true`와 `evidence_complete=1`이다. 관리자가 명시하면
   불완전 Episode도 조회할 수 있다.
7. L2/L3 승격 후보 조회는 `promotion_eligibility=eligible AND evidence_complete=1`
   만 허용한다.

## L2 Semantic Memory (M2 이후)

L2는 L1을 복사하지 않고 조건부 일반 지식과 반례 근거를 저장한다. GraphML 속성은
배열을 안정적으로 표현하지 못하므로, GraphML에는 스칼라만, 증거 연결은 SQLite에 둔다.

```sql
-- data/memory/semantic/evidence.sqlite3
CREATE TABLE knowledge_nodes (
    knowledge_id TEXT PRIMARY KEY,
    statement TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL CHECK (status IN ('active', 'weakened', 'retired')),
    source_candidate_id TEXT,
    created_at TEXT NOT NULL,
    last_validated_at TEXT NOT NULL
);

CREATE TABLE knowledge_conditions (
    knowledge_id TEXT NOT NULL REFERENCES knowledge_nodes(knowledge_id),
    ordinal INTEGER NOT NULL,
    condition_text TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, ordinal)
);

CREATE TABLE knowledge_evidence (
    knowledge_id TEXT NOT NULL REFERENCES knowledge_nodes(knowledge_id),
    episode_id TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK (polarity IN ('support', 'counterexample')),
    linked_at TEXT NOT NULL,
    PRIMARY KEY (knowledge_id, episode_id)
);
```

GraphML node에는 `id`, `type`, `statement`, `confidence`, `status`,
`source_candidate_id`, `created_at`, `last_validated_at`만 쓴다. GraphML edge에는
`source`, `target`, `relation`, `weight`, `evidence_count`, `counterexample_count`,
`updated_at`만 쓴다.

## L3 Procedural Memory (M3 이후)

L3는 실행 가능한 절차의 메타데이터와 성과를 저장한다. 절차 본문은
`skills/<skill_id>.yml` 또는 `.py`로 버전 관리하고 SQLite에는 경로만 저장한다.

```sql
-- data/memory/procedural/skills.sqlite3
CREATE TABLE skills (
    skill_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Seed', 'Developing', 'Active', 'Degrading', 'Archived')),
    source_l2_id TEXT NOT NULL,
    procedure_path TEXT NOT NULL UNIQUE,
    success_rate REAL NOT NULL CHECK (success_rate BETWEEN 0 AND 1),
    avg_pain_index REAL CHECK (avg_pain_index BETWEEN 0 AND 1),
    total_executions INTEGER NOT NULL DEFAULT 0,
    last_executed_at TEXT,
    protected INTEGER NOT NULL DEFAULT 0 CHECK (protected IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE skill_executions (
    skill_id TEXT NOT NULL REFERENCES skills(skill_id),
    episode_id TEXT NOT NULL,
    success_score REAL NOT NULL CHECK (success_score BETWEEN 0 AND 1),
    pain_index REAL CHECK (pain_index BETWEEN 0 AND 1),
    cib_score REAL NOT NULL CHECK (cib_score BETWEEN 0 AND 1),
    executed_at TEXT NOT NULL,
    PRIMARY KEY (skill_id, episode_id)
);
```

## L4 Constitution와 L5 Identity

L4는 계속 YAML 정본이다. `constitution/base.yml`, `safety.yml`,
`interaction_policy.yml`, `tool_policy.yml`을 읽기 전용으로 로드하며, Meta Loop와
HITL 전에는 writer를 만들지 않는다.

L5는 기존 `identity/identity.sqlite3`를 다음 계약으로 정리한다.

```sql
CREATE TABLE self_model (
    record_id TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    task_category TEXT NOT NULL,
    predicted_success REAL NOT NULL CHECK (predicted_success BETWEEN 0 AND 1),
    predicted_effort REAL,
    actual_success REAL NOT NULL CHECK (actual_success BETWEEN 0 AND 1),
    actual_effort REAL,
    calibration_error REAL NOT NULL CHECK (calibration_error BETWEEN 0 AND 1),
    calibration_direction TEXT NOT NULL CHECK (calibration_direction IN ('overconfident', 'underconfident', 'calibrated')),
    window_avg_calibration REAL,
    window_success_rate REAL,
    window_confidence_margin REAL,
    coherence_index REAL,
    timestamp TEXT NOT NULL,
    updated_by TEXT NOT NULL CHECK (updated_by IN ('outer_loop', 'meta_loop'))
);

CREATE TABLE capabilities (
    capability_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    success_rate REAL NOT NULL DEFAULT 0.5 CHECK (success_rate BETWEEN 0 AND 1),
    confidence REAL NOT NULL DEFAULT 0.7 CHECK (confidence BETWEEN 0 AND 1),
    effort_estimate REAL NOT NULL DEFAULT 0.5 CHECK (effort_estimate BETWEEN 0 AND 1),
    total_attempts INTEGER NOT NULL DEFAULT 0
);
```

## 구현 순서

1. L1 immutable domain 모델, status/outcome 구분, evaluation-reason 규칙과 SQLite
   migration을 작성한다.
2. SQLite `EpisodeRepository`의 save/get/list-after/filter, supersedes cycle 거부,
   `resolve_latest_episode()` 테스트를 작성한다.
3. `make_search_document()`와 Chroma projection/reindex를 구현한다. 새 Episode의
   최초 색인 실패는 `failed`, projection/model version 불일치는 `stale`인지 검증한다.
4. 검색 API를 구현한다: oversampled Chroma 후보 → supersession resolve/중복 제거 →
   SQLite hydrate/filter → score/created_at 순위 결정.
5. 현재 대화 runtime에는 **읽기 전용 memory injection**만 연결한다. 쓰기는 아직
   producer가 없으므로 admin/fixture API로만 검증한다.
6. Inner Loop가 구현되면 같은 `EpisodeRepository`에 평가 완료 Episode를 저장하고,
   L0 event ref를 추가한다. 저장소 schema는 바꾸지 않는다.
7. 충분한 eligible + evidence-complete L1이 쌓인 뒤 L2, 그 다음 L3를 구현한다.

SQLite migration은 `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)`을
사용한다. 각 migration은 transaction 안에서 미적용 version만 실행하므로, bootstrap이
repository를 여러 번 조립해도 안전하다.

## M1 수용 기준

- Episode는 SQLite에 한 번만 저장되며 기존 행을 덮어쓸 수 없다.
- SQLite 행의 `episode_id`와 Chroma ID가 일치한다.
- Chroma를 비운 뒤 SQLite 정본만으로 전체 인덱스를 재구축할 수 있다.
- 새 Episode의 색인 상태는 `pending → indexed|failed`이고, projection/model version
  불일치는 `indexed → stale`이 된다.
- 보정 체인이 순환하거나 분기하면 저장 또는 조회가 명시적 integrity error로 실패한다.
- 검색은 `candidate_k = max(top_k * 5, 20)`을 사용하고, 원본 후보가 가리키는 최신
  보정본을 원본 score로 순위화한 뒤 중복 제거·필터한다.
- `cib_evaluation_status=not_evaluated` Episode는 `quarantined`이고 승격 후보 조회에
  포함되지 않는다.
- 검색 결과는 task/condition/reflection을 담은 document의 유사도와 SQL 필터를 모두
  만족하며, 최신 보정 Episode를 우선한다.
- L0 참조가 없는 Episode도 저장·검색할 수 있지만 `evidence_complete=0`이라 L2/L3
  승격 후보가 되지 않는다.
- L2/L3/L4/L5 writer, tool execution, LLM 기반 evaluation/reflection은 M1에 없다.

## 미결정 항목

- 임베딩 모델과 Chroma distance metric
- `task_category` 표준 열거값
- 보존 기간과 개인정보/민감정보 redaction 규칙
- M1의 admin/fixture Episode producer API 형태
- L1 document에 원문 사용자 요청을 어느 수준까지 보존할지

이 항목들은 저장 스키마를 임의로 변경하지 않고, 구현 시작 전 별도 결정으로 고정한다.
