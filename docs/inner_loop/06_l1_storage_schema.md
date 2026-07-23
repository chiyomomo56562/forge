# L1 Episode 저장 스키마

## 목적

이 문서는 L1 Episode를 구현하기 전에 고정하는 저장 계약이다. L1은 한 작업의
평가된 경험이며, ChromaDB는 이를 의미적으로 검색하기 위한 주 저장소 겸 벡터
인덱스로 사용한다.

L1에 저장하는 것은 원본 L0 전체가 아니라, L0를 참조하는 **평가·반성 완료된
Episode**다. L0는 별도의 append-only JSONL 로그로 유지한다.

```text
L0 JSONL 원본 이벤트 ── raw_event_refs ──→ L1 Episode
                                              ├─ document: ChromaDB 임베딩 대상
                                              └─ metadata: 필터·감사·추적 필드
```

## 식별자와 불변 조건

| 필드 | 형식 | 규칙 |
| --- | --- | --- |
| `episode_id` | `ep_<ULID 또는 UUID>` | ChromaDB `id`와 반드시 같다. 생성 후 변경하지 않는다. |
| `session_id` | `ses_<ULID 또는 UUID>` | 하나의 실행 세션을 식별한다. |
| `raw_event_refs` | `evt_*` ID 배열 | L0 원본 이벤트를 시간순으로 참조한다. |
| `schema_version` | 정수 | 스키마 변경 시 증가한다. 초기값은 `1`이다. |
| `created_at` | UTC ISO-8601 | Episode 확정 시각이다. |

- L1 Episode는 생성 뒤 내용 수정 대신 `supersedes_episode_id`를 가진 보정
  Episode를 추가한다.
- CIB 실패 Episode도 삭제하지 않는다. `promotion_eligibility=quarantined`로
  저장해 검색·감사는 가능하지만 L2/L3 승격 증거에서는 제외한다.
- ChromaDB metadata에는 문자열·숫자·불리언만 둔다. 배열, 중첩 객체, 큰 원문은
  `document` 또는 L0 참조로 분리한다.

## 논리적 Episode 정본

아래는 애플리케이션이 만드는 논리적 L1 Episode다. 이 객체의 일부는 ChromaDB
metadata에 평탄화하고, 나머지는 검색 문서 또는 L0 참조로 표현한다.

```json
{
  "schema_version": 1,
  "episode_id": "ep_01JABC...",
  "session_id": "ses_01JABC...",
  "created_at": "2026-07-23T10:30:00Z",
  "task": {
    "request": "스캔 PDF에서 텍스트를 추출해줘",
    "category": "pdf_extraction"
  },
  "raw_event_refs": ["evt_01", "evt_02", "evt_03"],
  "execution": {
    "summary": "PDF 유형을 판별한 뒤 OCR로 텍스트를 추출했다.",
    "outcome": "Success",
    "tool_names": ["pdf_inspect", "ocr"]
  },
  "evaluation": {
    "status": "Success",
    "result_quality": 0.95,
    "requirement_coverage": 1.0,
    "verification_confidence": 0.90,
    "success_score": 0.955,
    "pain_index": 0.05,
    "cib_score": 0.98
  },
  "reflection": {
    "what_worked": "이미지 기반 PDF를 판별한 뒤 OCR을 사용했다.",
    "what_failed": "텍스트 추출기는 이미지 레이어가 없어 사용할 수 없었다.",
    "next_hint": "처음에 PDF 유형을 검사한 뒤 도구를 선택한다.",
    "causal_condition": "입력이 스캔된 이미지 기반 PDF였다."
  },
  "promotion": {
    "eligibility": "eligible",
    "pattern_candidate_id": "pc_scanned_pdf_ocr"
  },
  "supersedes_episode_id": null
}
```

## ChromaDB 레코드 계약

ChromaDB에는 다음 세 값만 저장한다.

```python
collection.add(
    ids=[episode.episode_id],
    documents=[make_search_document(episode)],
    metadatas=[make_chroma_metadata(episode)]
)
```

### `document`: 임베딩 대상 텍스트

`document`는 검색 결과가 다음 계획에 바로 도움이 되도록 조건·행동·결과·반성을
한글 자연어로 조합한다. L0 원문이나 도구의 대용량 출력은 넣지 않는다.

```text
[작업]
스캔 PDF에서 텍스트를 추출해줘

[조건]
입력이 스캔된 이미지 기반 PDF였다.

[결과]
성공: PDF 유형을 판별한 뒤 OCR로 텍스트를 추출했다.

[통했던 것]
이미지 기반 PDF를 판별한 뒤 OCR을 사용했다.

[실패·비용]
텍스트 추출기는 이미지 레이어가 없어 사용할 수 없었다.

[다음 힌트]
처음에 PDF 유형을 검사한 뒤 도구를 선택한다.
```

### `metadata`: 필터와 추적용 평탄화 필드

```json
{
  "schema_version": 1,
  "episode_id": "ep_01JABC...",
  "session_id": "ses_01JABC...",
  "created_at": "2026-07-23T10:30:00Z",
  "task_category": "pdf_extraction",
  "outcome": "Success",
  "status": "Success",
  "success_score": 0.955,
  "pain_index": 0.05,
  "cib_score": 0.98,
  "promotion_eligibility": "eligible",
  "pattern_candidate_id": "pc_scanned_pdf_ocr",
  "has_reflection": true,
  "supersedes_episode_id": ""
}
```

| metadata 필드 | 형식 | 용도 |
| --- | --- | --- |
| `schema_version` | int | 읽기 측 마이그레이션 분기 |
| `episode_id`, `session_id` | string | L0·후보·감사 추적 |
| `created_at` | ISO-8601 string | 기간 필터와 워터마크 |
| `task_category` | string | 작업군 필터 |
| `outcome`, `status` | string | 성공·부분 성공·실패 필터 |
| `success_score`, `pain_index`, `cib_score` | float 0~1 | 품질·마찰·안전 기준 필터 |
| `promotion_eligibility` | `eligible` 또는 `quarantined` | Outer Loop 승격 증거 제외 필터 |
| `pattern_candidate_id` | string 또는 빈 문자열 | 누적 가설 추적 |
| `has_reflection` | bool | 반성 밀도 우선 검색 |
| `supersedes_episode_id` | string 또는 빈 문자열 | 보정 Episode 추적 |

## 상태 규칙

| 조건 | `status` | `promotion_eligibility` | Pattern Candidate 연결 |
| --- | --- | --- | --- |
| CIB < 0.95 | `Failure` | `quarantined` | 연결하지 않음 |
| CIB ≥ 0.95, success ≥ 0.85 | `Success` | `eligible` | 지지 또는 반례 증거로 연결 |
| CIB ≥ 0.95, 0.40 ≤ success < 0.85 | `Partial` | `eligible` | 조건부/약한 증거로 연결 |
| CIB ≥ 0.95, success < 0.40 | `Failure` | `eligible` | 반례 또는 실패 증거로 연결 |

성공 Episode만 후보에 연결하면 생존자 편향이 생긴다. CIB를 통과한 실패도
“어떤 조건에서 어떤 행동이 통하지 않았는가”를 보여 주는 반례 증거로 연결한다.

## 저장 의사 코드

```text
function make_search_document(episode):
    return format(
        "[작업] {task}\n" +
        "[조건] {causal_condition}\n" +
        "[결과] {status}: {execution_summary}\n" +
        "[통했던 것] {what_worked}\n" +
        "[실패·비용] {what_failed}\n" +
        "[다음 힌트] {next_hint}",
        episode
    )

function make_chroma_metadata(episode):
    return {
        schema_version: episode.schema_version,
        episode_id: episode.episode_id,
        session_id: episode.session_id,
        created_at: episode.created_at,
        task_category: episode.task.category,
        outcome: episode.execution.outcome,
        status: episode.evaluation.status,
        success_score: episode.evaluation.success_score,
        pain_index: episode.evaluation.pain_index,
        cib_score: episode.evaluation.cib_score,
        promotion_eligibility: episode.promotion.eligibility,
        pattern_candidate_id: episode.promotion.pattern_candidate_id or "",
        has_reflection: true,
        supersedes_episode_id: episode.supersedes_episode_id or ""
    }

function save_l1_episode(episode):
    validate_l1_episode(episode)

    chroma.add(
        ids = [episode.episode_id],
        documents = [make_search_document(episode)],
        metadatas = [make_chroma_metadata(episode)]
    )

    append_l0_by_id(episode.session_id, "l1_persisted", {
        episode_id: episode.episode_id,
        promotion_eligibility: episode.promotion.eligibility
    })
```

## 조회 규칙

1. 일반 계획 단계는 `has_reflection=true`인 L1을 의미 검색한다.
2. 일반 지식 승격을 위한 Outer Loop는
   `promotion_eligibility=eligible`만 증거로 읽는다.
3. 안전 분석·감사는 `quarantined`를 포함해 조회할 수 있다.
4. `supersedes_episode_id`가 있는 보정 Episode가 있으면 최신 보정본을 우선
   읽되, 원본 Episode도 감사용으로 보존한다.

## 구현 전 결정해야 할 항목

- `task_category`의 표준 분류 목록
- 각 작업군의 `result_quality`와 `requirement_coverage` 측정 방법
- Pattern Candidate의 정규화 키와 영속 저장소
- ChromaDB collection 이름, 임베딩 모델, 거리 함수, 보존 기간
- L0 이벤트 파일의 파티션 규칙과 보정 Episode 생성 권한
