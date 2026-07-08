# Implementation Process

> 본 문서는 README.md의 설계 명세를 기반으로, 실제 구현 순서와 단계별 작업을 정의한다.
> 기본 방침: **Memory + Inner Loop 1차 구현, Outer/Meta Loop 2차 구현.**

---

## 0. 구현 우선순위 원칙

```
Phase 0: 프로젝트 인프라 (Project Setup)
    ↓
Phase 1: 메모리 계층 (L1 → L3 → L2 → L4 → L5 → Manager)
    ↓
Phase 2: 이너 루프 (LLM → Cognition → Tools → Loop Pipeline)
    ↓
Phase 3: 아우터 루프 (차순위)
    ↓
Phase 4: 메타 루프 (차순위)
    ↓
Phase 5: 한계 보완 및 고도화 (Section 7 + M14-M17 정제)
```

### 의존성 그래프

```
                    Phase 0 (Infra)
                         |
              +----------+----------+
              |                     |
         Phase 1.1               Phase 1.2
        (Schemas/Core)         (L1 Episodic)
              |                     |
         Phase 1.3               Phase 1.3
        (L3 Procedural)        (L3 Procedural)
              |                     |
              +--------+------------+
                       |
                  Phase 1.4 (L2 Semantic)
                       |
                  Phase 1.5 (L4 Constitution)
                       |
                  Phase 1.6 (L5 Identity)
                       |
                  Phase 1.7 (Memory Manager)
                       |
              +--------+------------+
              |                     |
         Phase 2.1               Phase 2.2
        (LLM Client)           (Cognition)
              |                     |
         Phase 2.3               Phase 2.3
        (Tools)                 (Tools)
              |                     |
              +--------+------------+
                       |
                  Phase 2.4 (Inner Loop Pipeline)
                       |
                  Phase 3 (Outer Loop) — 차순위
                       |
                  Phase 4 (Meta Loop) — 차순위
                       |
                  Phase 5 (한계 보완 / 고도화)
```

---

## Phase 0: 프로젝트 인프라 (Project Setup)

**목표:** 모든 구현의 기반이 되는 디렉터리 구조, 설정 파일, 유틸리티를 구축한다.

### 0.1 디렉터리 구조 생성

README.md의 파일 구조 트리를 그대로 생성한다.

```
forge/
├── config/          (agent.yml, memory.yml, database.yml, logging.yml)
├── constitution/    (base.yml, safety.yml, interaction_policy.yml, tool_policy.yml)
├── identity/        (identity.yml, self_model.yml, capabilities.yml)
├── data/memory/     (episodic/, semantic/, procedural/, working/, audit/)
├── data/logs/
├── data/cache/      (embeddings/, tool_results/)
├── src/agent/       (memory/, cognition/, tools/, llm/, utils/)
├── scripts/
├── tests/
├── notebooks/
```

### 0.2 프로젝트 설정

| 작업 | 파일 | 설명 |
|------|------|------|
| 의존성 정의 | `pyproject.toml` | chromadb, networkx, pyyaml, sqlite3(표준), pydantic, openai/anthropic SDK 등 |
| 환경 변수 | `.env` | LLM API 키, DB 경로, 임베딩 모델명 등 |
| Git 제외 | `.gitignore` | data/, .env, *.sqlite3, __pycache__/, working/sessions/*/ |
| 에이전트 설정 | `config/agent.yml` | 모델명, temperature, max_tokens, 기본 N값 |
| 메모리 설정 | `config/memory.yml` | 각 계층별 저장소 경로, 검색 top_k, 캐시 정책 |
| DB 설정 | `config/database.yml` | Chroma 경로, SQLite 경로, 그래프 파일 경로 |
| 로깅 설정 | `config/logging.yml` | 로그 레벨, 파일 경로, 포맷 |

### 0.3 유틸리티 구현

| 작업 | 파일 | 설명 |
|------|------|------|
| ID 생성 | `src/agent/utils/ids.py` | `generate_episode_id()`, `generate_skill_id()`, UUID 기반 |
| 시간 유틸 | `src/agent/utils/time.py` | ISO 8601 타임스탬프, 날짜 파싱, 윈도우 계산 |
| 직렬화 | `src/agent/utils/serialization.py` | JSONL 읽기/쓰기, YAML 로드/덤프, 피클 처리 |
| 로깅 | `src/agent/utils/logging.py` | config/logging.yml 기반 로거 초기화, 에러 로그 분리 |

### 0.4 헌법 YAML 초안 작성

| 작업 | 파일 | 설명 |
|------|------|------|
| 기본 원칙 | `constitution/base.yml` | version, principles (honesty, user_control, memory_minimization) |
| 안전 원칙 | `constitution/safety.yml` | CIB 임계값(0.95), 금지 행위 정의 |
| 상호작용 정책 | `constitution/interaction_policy.yml` | 사용자 승인 필수 행위 목록 |
| 도구 정책 | `constitution/tool_policy.yml` | require_confirmation_for 목록 |

### 0.5 L5 YAML 초안 작성

| 작업 | 파일 | 설명 |
|------|------|------|
| 정체성 정의 | `identity/identity.yml` | 에이전트 이름, 역할, 기본 정체성 서술 |
| 셀프 모델 | `identity/self_model.yml` | 초기 능력치, 예측 편향 기본값 |
| 역량 정의 | `identity/capabilities.yml` | 작업 카테고리별 초기 역량 수치 |

### 마일스톤

- [ ] 디렉터리 구조 생성 완료
- [ ] `pyproject.toml` 의존성 설치 가능 (`pip install -e .`)
- [ ] 유틸리티 단위 테스트 통과
- [ ] 헌법 YAML 로드 가능 (파싱 에러 없음)

---

## Phase 1: 메모리 계층 구현 (Memory Layers)

**목표:** L1~L5 기억 계층과 이를 통합하는 Memory Manager를 구현한다.
**원칙:** 하위 계층부터 순차 구현. 각 계층은 독립적으로 테스트 가능해야 한다.

### 1.1 스키마 & 코어 (Schemas & Core)

**선행 조건:** Phase 0 완료

| 작업 | 파일 | 상세 |
|------|------|------|
| 데이터 모델 정의 | `src/agent/memory/schemas.py` | `MemoryRecord`, `Entity`, `Relation`, `Episode`, `Skill`, `Reflection` pydantic 모델 |
| 메모리 정책 | `src/agent/memory/policies.py` | 읽기/쓰기/삭제 정책, 접근 권한, 보존 기간 |
| 랭킹 | `src/agent/memory/ranking.py` | 중요도·최신성·관련도 가중 합산 점수 계산 |

**핵심 설계:**
- `MemoryRecord`는 모든 계층의 기본 단위. `layer: Literal["L1","L2","L3","L4","L5"]` 필드 포함.
- `Episode` 모델은 README의 L1 예시 구조를 그대로 반영: `episode_id`, `task`, `execution_summary`, `evaluation(status, pain_index)`, `reflection(what_worked, what_failed, next_hint, causal_condition)`, `timestamp`
- `Skill` 모델은 L3 예시 반영: `skill_id`, `code`, `metadata(status, success_rate, version)`, `reflection`, `protected`

**테스트:** `tests/test_memory_manager.py` (스키마 검증 부분)

---

### 1.2 L1 일화 기억 (Episodic Memory)

**선행 조건:** Phase 1.1 완료

| 작업 | 파일 | 상세 |
|------|------|------|
| 임베딩 생성 | `src/agent/memory/episodic/encoder.py` | 텍스트 → 벡터 변환. config의 임베딩 모델 사용. 캐시 지원(`data/cache/embeddings/`) |
| Chroma 래퍼 | `src/agent/memory/episodic/store.py` | 컬렉션 생성, upsert, 삭제. 경로: `data/memory/episodic/chroma/` |
| 의미론 검색 | `src/agent/memory/episodic/retriever.py` | 유사도 검색, top_k 반환, 메타데이터 필터링. **밀도 우선 검색**: reflection 데이터 우선 |
| 이벤트 로깅 | `src/agent/memory/episodic/event_logger.py` | 원본 이벤트 JSONL 기록. 경로: `data/memory/episodic/raw_events/YYYY-MM-DD.jsonl` |

**구현 순서:**
1. `encoder.py` — 임베딩 함수 구현 + 캐시
2. `store.py` — Chroma 컬렉션 CRUD
3. `event_logger.py` — JSONL 원본 로그
4. `retriever.py` — 검색 + 밀도 우선 로직

**핵심 설계:**
- 반성(Reflection) 데이터는 `reflection` 필드가 채워진 Episode로 저장. retriever는 `has_reflection=True` 메타데이터를 우선 필터링.
- 선택적 주입(Selective Injection): retriever가 좁은 검색부터 시작하여 점진적 확장 API 제공 (`retrieve(query, top_k, expand=False)`)

**테스트:** `tests/test_episodic_memory.py`
- [ ] Episode 저장 후 동일 ID로 조회 가능
- [ ] 의미론적 유사도 검색 정상 작동
- [ ] reflection 우선 검색 확인
- [ ] JSONL 원본 로그 파일 생성 확인

---

### 1.3 L3 절차적 기억 (Procedural Memory)

**선행 조건:** Phase 1.1 완료 (Phase 1.2와 병렬 가능)

| 작업 | 파일 | 상세 |
|------|------|------|
| SQLite 래퍼 | `src/agent/memory/procedural/skill_store.py` | 스킬 CRUD. 경로: `data/memory/procedural/skills.sqlite3` |
| 스킬 로더 | `src/agent/memory/procedural/skill_loader.py` | DB에서 스킬 코드 로드, 메모리 캐싱 |
| 스킬 실행 | `src/agent/memory/procedural/skill_executor.py` | `exec()` 기반 코드 실행, 샌드박스, 타임아웃 |
| 스킬 평가 | `src/agent/memory/procedural/skill_evaluator.py` | 실행 후 success_rate 갱신, 상태 전이 판정 |

**스킬 생명 주기 상태 머신 (Skill Lifecycle):**

```
Seed ──(success_rate > 0.9)──→ Active
Active ──(success_rate < 0.5)──→ Degrading
Degrading ──(success_rate < 0.2 or 30일 미사용)──→ Archived
Archived ──(메타 루프 복원)──→ Seed (재검증 필요)
```

| 전이 | 조건 | 액션 |
|------|------|------|
| Seed → Active | success_rate > 0.9 (최근 5회) | status 갱신, 활성 스킬 목록에 추가 |
| Active → Degrading | success_rate < 0.5 (최근 10회) | status 갱신, 경고 로그 |
| Degrading → Archived | success_rate < 0.2 또는 30일 미사용 | status 갱신, 활성 목록에서 제거 |
| Active → Degrading (복구) | Degrading 상태에서 success_rate > 0.7 (최근 5회) | Active 복귀 |

**이중 저장 전략 — L3 담당:**
- `reflection_hints` 필드: 도구 종속적 절차 힌트 저장 (예: "PyPDF2로는 이미지 PDF 텍스트 추출 안 됨, OCR 스킬 연동 필요")
- inner loop 반성 단계에서 추출된 도구 종속적 힌트 → `skill_store.update_reflection_hints(skill_id, hints)` 호출

**마이그레이션:**
- `data/memory/procedural/migrations/` — SQLite 스키마 마이그레이션 스크립트
- `data/memory/procedural/skill_registry.json` — 스킬 메타데이터 레지스트리

**테스트:** `tests/test_procedural_memory.py`
- [ ] 스킬 저장/조회/삭제 CRUD
- [ ] 상태 머신 전이 (Seed → Active → Degrading → Archived)
- [ ] success_rate 갱신 로직
- [ ] reflection_hints 필드 업데이트
- [ ] 코드 실행 (샌드박스, 타임아웃)

---

### 1.4 L2 시맨틱 기억 (Semantic Memory)

**선행 조건:** Phase 1.2 (L1), Phase 1.3 (L3) 완료 — L1에서 추출하여 L2에 저장하므로

| 작업 | 파일 | 상세 |
|------|------|------|
| NetworkX 래퍼 | `src/agent/memory/semantic/graph_store.py` | 그래프 로드/저장(.graphml, .gpickle), 노드/엣지 CRUD. 경로: `data/memory/semantic/graph/` |
| JSON 스토어 | `src/agent/memory/semantic/json_store.py` | concepts.json, entities.json, relations.json 읽기/쓰기 |
| 엔티티/관계 추출 | `src/agent/memory/semantic/extractor.py` | L1 에피소드에서 entity/relation 추출 (LLM 활용) |
| 중복 엔티티 병합 | `src/agent/memory/semantic/resolver.py` | 유사 엔티티 머지, 동의어 그룹핑 |
| 추론 | `src/agent/memory/semantic/reasoner.py` | 그래프 기반 간단 추론, 경로 탐색 |

**이중 저장 전략 — L2 담당:**
- 범용적 지식 힌트 (예: "한글 포함 시각화 시 폰트 캐시 확인") → 지식 그래프의 노드/엣지로 저장
- `extractor.py`가 L1 반성에서 범용적 힌트를 식별하여 그래프에 추가
- `consolidation.py` (Phase 1.7)가 L1 → L2 추출 파이프라인 담당

**스냅샷:**
- `data/memory/semantic/snapshots/YYYY-MM-DD/` — 주기적 그래프 스냅샷

**테스트:** `tests/test_semantic_memory.py`
- [ ] 그래프 노드/엣지 추가/삭제
- [ ] L1 에피소드에서 엔티티 추출
- [ ] 중복 엔티티 병합
- [ ] 그래프 저장/로드 (.graphml)
- [ ] 범용 힌트 → 그래프 노드 저장 확인

---

### 1.5 L4 헌법 (Constitution)

**선행 조건:** Phase 0.4 (헌법 YAML 초안) 완료

| 작업 | 파일 | 상세 |
|------|------|------|
| YAML 로더 | `src/agent/memory/constitution/loader.py` | constitution/*.yml 로드, 병합, 버전 관리 |
| 검증기 | `src/agent/memory/constitution/validator.py` | 헌법 원칙 검증, K-Scenario 대입, 방향성 함수 C 산출 (0~1) |
| 가드 | `src/agent/memory/constitution/guard.py` | CIB 게이트: 점수 < 0.95 시 학습/행동 차단 (Block). 하드 게이트 강제 |

**CIB 게이트 로직:**

```python
class CIBGuard:
    THRESHOLD = 0.95

    def evaluate(self, plan_or_result, constitution) -> CIBResult:
        scores = []
        for scenario in constitution.k_scenarios:
            score = self.validator.validate_direction(plan_or_result, scenario)
            scores.append(score)
        min_score = min(scores)
        passed = min_score >= self.THRESHOLD
        return CIBResult(
            scores=scores,
            min_score=min_score,
            passed=passed,
            blocked=not passed
        )
```

**HITL 게이트 (모든 계층 공통):**
- 절대층, 원칙층, 전략층 모두 메타 루프를 통한 수정 시 **인간 승인 필수**
- `guard.py`에 `require_hitl_approval(layer)` 함수 구현 — 메타 루프 단계에서 호출

**테스트:** `tests/test_constitution.py`
- [ ] YAML 로드 및 파싱
- [ ] K-Scenario 검증 (통과/실패 케이스)
- [ ] CIB 점수 0.95 미만 시 차단 확인
- [ ] 모든 계층 HITL 승인 요구 확인

---

### 1.6 L5 정체성 (Identity)

**선행 조건:** Phase 1.1 (스키마), Phase 0.5 (L5 YAML) 완료

| 작업 | 파일 | 상세 |
|------|------|------|
| SQLite 스토어 | `src/agent/memory/identity/identity_store.py` | `identity.sqlite3` 관리, self_model 테이블 DDL 실행 |
| 셀프 모델 | `src/agent/memory/identity/self_model.py` | M14 구현: 예측/실제/칼리브레이션 에러 기록, 윈도우 통계 계산 |
| 역량 모델 | `src/agent/memory/identity/capability_model.py` | 작업 카테고리별 역량 수치 관리 |
| 업데이터 | `src/agent/memory/identity/updater.py` | 아우터 루프용(통계 갱신) / 메타 루프용(근본 재설계) 분리 |

**M14 셀프 모델 테이블 (README Section 8 반영):**

```sql
-- identity_store.py에서 초기화 시 실행
CREATE TABLE IF NOT EXISTS self_model (
    record_id       TEXT PRIMARY KEY,
    episode_id      TEXT NOT NULL,
    task_category   TEXT NOT NULL,
    predicted_success   REAL NOT NULL,
    predicted_effort    REAL,
    actual_success      REAL NOT NULL,
    actual_effort       REAL,
    calibration_error   REAL NOT NULL,
    calibration_direction TEXT NOT NULL,  -- 'overconfident' | 'underconfident' | 'calibrated'
    window_avg_calibration  REAL,
    window_success_rate      REAL,
    window_confidence_margin REAL,
    coherence_index    REAL,
    timestamp         TEXT NOT NULL,
    updated_by        TEXT NOT NULL  -- 'outer_loop' | 'meta_loop'
);
```

**L5 역할 분리 (README 설계 원칙 반영):**
- `updater.py`에 두 가지 모드:
  - `update_statistics(episode_data)` — 아우터 루프용: 윈도우 통계, success_rate, calibration_error 갱신
  - `redesign_identity(new_config)` — 메타 루프용: 정체성 근본 재설계 (HITL 승인 필요)

**테스트:** `tests/test_identity.py`
- [ ] self_model 테이블 생성
- [ ] 예측/실제 결과 저장 후 calibration_error 자동 계산
- [ ] calibration_direction 분류 (overconfident/underconfident/calibrated)
- [ ] 윈도우 통계 (최근 50개) 갱신
- [ ] 통계 갱신 vs 근본 재설계 모드 분리

---

### 1.7 메모리 매니저 (Memory Manager)

**선행 조건:** Phase 1.2 ~ 1.6 모두 완료

| 작업 | 파일 | 상세 |
|------|------|------|
| 전체 라우터 | `src/agent/memory/manager.py` | L1~L5 통합 라우팅, 계층 간 데이터 흐름 관리 |
| L1→L2 추출 | `src/agent/memory/consolidation.py` | 에피소드에서 지식 추출, 이중 저장 전략 라우팅 (범용→L2, 도구종속→L3) |
| 반성 처리 | `src/agent/memory/reflection.py` | 반성 데이터 정리, 요약, 일반화 힌트 추출 및 L2/L3 분산 저장 |

**MemoryManager 인터페이스:**

```python
class MemoryManager:
    def __init__(self, config: MemoryConfig):
        self.episodic = EpisodicStore(config.chroma_path)
        self.semantic = SemanticGraphStore(config.graph_path)
        self.procedural = SkillStore(config.sqlite_path)
        self.constitution = ConstitutionLoader(config.constitution_path)
        self.identity = IdentityStore(config.identity_path)

    def retrieve(self, query: str, layers: list[str], top_k: int) -> list[MemoryRecord]:
        """선택적 주입: 지정된 계층에서 의미론적으로 관련된 기억만 검색"""

    def store_episode(self, episode: Episode) -> str:
        """L1에 에피소드 저장 + 원본 로그 기록"""

    def store_reflection(self, episode_id: str, reflection: Reflection) -> None:
        """L1에 반성 저장 + 일반화 힌트를 L2/L3에 분산 저장 (이중 저장 전략)"""

    def consolidate(self, episode_ids: list[str]) -> None:
        """L1 에피소드들에서 L2 지식 추출 + L3 힌트 업데이트"""
```

**이중 저장 전략 라우팅 (consolidation.py 핵심):**

```python
def route_hint(hint: str, hint_type: str) -> str:
    """
    반성에서 추출된 일반화 힌트의 저장 계층을 결정.
    hint_type: 'general' (범용 지식) | 'tool_specific' (도구 종속적 절차)
    """
    if hint_type == 'general':
        return 'L2'  # 시맨틱 그래프에 노드/엣지로 저장
    elif hint_type == 'tool_specific':
        return 'L3'  # procedural skill_store의 reflection_hints 필드에 저장
```

**테스트:** `tests/test_memory_manager.py`
- [ ] 다중 계층 동시 검색
- [ ] 에피소드 저장 후 L1 조회
- [ ] 반성 저장 시 L1 + L2/L3 분산 저장 확인
- [ ] consolidation: L1 에피소드 → L2 지식 추출
- [ ] 이중 저장 라우팅 (범용→L2, 도구종속→L3)

### Phase 1 마일스톤

- [ ] L1~L5 모든 계층 독립적 CRUD 작동
- [ ] MemoryManager를 통한 통합 검색/저장 작동
- [ ] 이중 저장 전략 (L2/L3 분산) 검증 완료
- [ ] CIB 게이트 (0.95 임계값) 작동 확인
- [ ] M14 셀프 모델 (칼리브레이션 에러) 기록 확인
- [ ] 모든 메모리 테스트 통과

---

## Phase 2: 이너 루프 구현 (Inner Loop)

**목표:** 계획 → 실행 → 평가 → 반성의 4단계 이너 루프를 구현한다.
**선행 조건:** Phase 1 (메모리 계층) 완료

### 2.1 LLM 클라이언트

**선행 조건:** Phase 0 완료 (Phase 1과 병렬 가능)

| 작업 | 파일 | 상세 |
|------|------|------|
| LLM 클라이언트 | `src/agent/llm/client.py` | OpenAI/Anthropic API 래퍼, 재시도 로직, 타임아웃 |
| 프롬프트 템플릿 | `src/agent/llm/prompts.py` | 계획/실행/평가/반성용 프롬프트 템플릿, 선택적 주입용 컨텍스트 빌더 |
| 응답 파서 | `src/agent/llm/response_parser.py` | LLM 응답 → 구조화된 객체 (plan, reflection 등) |

**핵심 설계:**
- `client.py`는 동일 인터페이스로 여러 LLM 백엔드 지원 (OpenAI, Anthropic, 로컬)
- `prompts.py`에 수행자용 프롬프트와 **피닉스 어디터용 평가 프롬프트** 분리 정의 (M15 구조적 분리)

---

### 2.2 인지 모듈 (Cognition)

**선행 조건:** Phase 1.7 (MemoryManager), Phase 2.1 (LLM) 완료

| 작업 | 파일 | 상세 |
|------|------|------|
| 컨텍스트 빌더 | `src/agent/cognition/context_builder.py` | **선택적 주입**: MemoryManager에서 관련 기억만 추출하여 LLM 컨텍스트 구성. 밀도 우선 (reflection 우선) |
| 계획기 | `src/agent/cognition/planner.py` | 입력 + 주입된 기억 → 실행 계획 수립. L1/L2/L3에서 N개 검색 |
| 추론기 | `src/agent/cognition/reasoner.py` | 계획의 타당성 검증, 대안 생성 |
| 의사결정 | `src/agent/cognition/decision.py` | 최종 실행 계획 확정, 우선순위 정렬 |
| 반성 루프 | `src/agent/cognition/reflection_loop.py` | 실행 결과 → 4대 반성 필드 추출 (what_worked, what_failed, next_hint, causal_condition) |

**선택적 주입 파이프라인 (context_builder.py):**

```
1. 사용자 입력 수신
2. 좁은 검색: L1 reflection 데이터 top_k=3 (밀도 우선)
3. 연관 확장: 검색된 reflection에 연관된 L1 에피소드 + L2 지식 + L3 스킬 힌트
4. 토큰 예산 내에서 컨텍스트 조합
5. LLM 프롬프트에 주입
```

---

### 2.3 도구 시스템 (Tools)

**선행 조건:** Phase 0 완료 (Phase 2.1과 병렬 가능)

| 작업 | 파일 | 상세 |
|------|------|------|
| 베이스 | `src/agent/tools/base.py` | Tool 추상 클래스, 인터페이스 정의 (name, description, execute) |
| 레지스트리 | `src/agent/tools/registry.py` | 도구 등록/조회, L3 스킬과 연동 |
| 검색 | `src/agent/tools/builtin/search.py` | 웹 검색 도구 |
| 파일 I/O | `src/agent/tools/builtin/file_io.py` | 파일 읽기/쓰기 도구 |
| 코드 실행 | `src/agent/tools/builtin/code_exec.py` | 샌드박스 코드 실행 도구 |

**핵심 설계:**
- `registry.py`는 L3 `skill_loader.py`와 연동 — 등록된 스킬을 도구로 노출
- 헌법 `tool_policy.yml`의 `require_confirmation_for` 항목과 연동 — 승인 필요 도구 자동 식별

---

### 2.4 이너 루프 파이프라인

**선행 조건:** Phase 2.1, 2.2, 2.3 모두 완료

| 작업 | 파일 | 상세 |
|------|------|------|
| 오케스트레이터 | `src/agent/orchestrator.py` | 이너 루프 4단계 조율: 계획→실행→평가→반성 |
| 런타임 | `src/agent/runtime.py` | 세션 관리, 작업 메모리(working memory), 루프 실행 컨텍스트 |
| 진입점 | `src/agent/main.py` | CLI/API 진입점, 사용자 입력 → 런타임 시작 |

**이너 루프 4단계 구현 명세:**

#### 단계 1: 계획 수립 (Planning)

```
입력 → context_builder (선택적 주입) → planner (계획 수립) → working/sessions/{sid}/plan.json 저장
```

- MemoryManager.retrieve()로 L1/L2/L3에서 관련 기억 N개 검색
- 밀도 우선: reflection 데이터 우선, 필요시 원본 에피소드 확장
- 계획 결과를 `data/memory/working/sessions/{session_id}/plan.json`에 스테이징

#### 단계 2: 실행 (Execution)

```
계획 → tool_registry 호출 → 결과 생성 → working/sessions/{sid}/ 준비
```

- 실행 중 저장: 성공/실패 상태, Pain Index (실행 단계에서는 성공 점수 미정이므로 빈 값)
- 인과 조건 + 힌트를 데이터 필드에 포함
- 재시도 및 즉각적 오류 수정은 이너 루프 내에서 처리

#### 단계 3: 평가 (Evaluation)

```
결과 → CIBGuard.evaluate() + Phoenix Auditor 채점 → working/sessions/{sid}/evaluation.json 저장
```

- **CIB 검증:** 헌법 K-Scenarios에 결과 대입, 방향성 함수 C 산출, 0.95 이상 시 통과
- **Phoenix Auditor (M15):** 수행자와 구조적 분리된 평가
  - 별도 프롬프트/세션으로 도메인 점수(60%) + 성찰 점수(40%) = Phoenix_Score 산출
  - CIB 점수와 Phoenix_Score **둘 다 0.95 이상**이어야 학습 허용
- 평가 결과를 `data/memory/working/sessions/{session_id}/evaluation.json`에 스테이징
- **평가 단계 재시도:** CIB 미달 시 계획 수정 후 재실행 (최대 3회)

#### 단계 4: 반성 (Reflection)

```
실행+평가 결과 → reflection_loop (4대 필드 추출) → L1 저장 + L2/L3 분산 저장
```

- 4대 반성 필드 추출: `what_worked`, `what_failed`, `next_hint`, `causal_condition`
- 1:1 관계: 1 에피소드 → 1 평가 → 1 반성
- 반성 결과 → L1 저장 (MemoryManager.store_reflection)
- 일반화 힌트 → 이중 저장 전략:
  - 범용 지식 → L2 그래프 (consolidation.py)
  - 도구 종속적 절차 → L3 reflection_hints (skill_store.py)
- Pain Index 확정: `pain_index = 1 - success_score` (평가 단계에서 성공 점수 확정 후)
- 반성 결과를 `data/memory/working/sessions/{session_id}/reflection.json`에 스테이징 → L1 확정 저장 후 세션 정리

**Pain Index 계산:**

```python
# 실행 단계: 성공 점수 미정
pain_index = None  # 빈 값

# 평가 단계 완료 후:
success_score = phoenix_score  # 또는 CIB 점수
pain_index = 1 - success_score
# pain_index가 높을수록 반성 강도 증가
```

**테스트:** `tests/` 전체
- [ ] 계획 수립 → working/sessions/{sid}/plan.json 스테이징
- [ ] 실행 → 도구 호출 결과 저장
- [ ] CIB 평가 → 0.95 미달 시 차단
- [ ] Phoenix Auditor → 6:4 채점 정상
- [ ] 반성 → 4대 필드 추출 + L1 저장
- [ ] 이중 저장 → L2/L3 분산 저장 확인
- [ ] Pain Index 계산 (실행 시 None, 평가 후 확정)
- [ ] 전체 루프 E2E 테스트 (입력 → 반성 저장까지)

### Phase 2 마일스톤

- [ ] 이너 루프 4단계 E2E 작동 (입력 → 반성 저장)
- [ ] 선택적 주입 (밀도 우선, 점진적 확장) 작동
- [ ] CIB 게이트 + Phoenix Auditor 이중 평가 작동
- [ ] working/sessions/에 계획/평가/반성 결과 스테이징 → L1 확정 저장
- [ ] 이중 저장 전략 (L2/L3) 반성 힌트 분산 저장
- [ ] Pain Index 계산 (실행 시 빈값 → 평가 후 확정)
- [ ] 모든 이너 루프 테스트 통과

---

## Phase 3: 아우터 루프 (Outer Loop) — 차순위

**목표:** N개 에피소드 누적 시 시스템 건전성 점검 루프 구현
**선행 조건:** Phase 2 (이너 루프) 완료

### 3.1 아우터 루프 7단계 프로세스

| 단계 | 구현 내용 | 관련 메커니즘 |
|------|----------|-------------|
| 1. 데이터 집계 | 최근 N개 에피소드 성공률, 피닉스 점수 평균 산출 | - |
| 2. 지표 기록 | CIB 전체 검증 + 코히어런스 인덱스(M17) + 행동 일관성(BC) 기록 | M17 |
| 3. 캐시 갱신 | 메모리 캐시 최신화 | - |
| 4. 자기 모델 재계산 | 최근 50개 에피소드 윈도우 기준 셀프 모델(M14) 재계산 | M14 |
| 5. 독립 감사 | 수행자 자기평가 vs 피닉스 점수 편차 확인 (≥0.2 시 M14 반영) | M15 |
| 6. 성장 속도 조절 | 그로스 레이트 레귤레이터(M16): 추락/정체/과속 감지 | M16 |
| 7. 메타 루프 트리거 | 정기 진화(1,000 에피소드) OR 긴급 점검(루프 100회) | Trigger Separation |

### 3.2 M16 그로스 레이트 레귤레이터 구현

| 신호 | 조건 | 조치 |
|------|------|------|
| 추락 | 최근 20 에피소드 평균 성공률이 이전 20 대비 0.15+ 하락 | CIB 게이트 강제 호출 → 학습 일시 중지 |
| 정체 | 코히어런스 인덱스 변화량이 50 에피소드 이상 0.01 미만 | 메타 루프 정체 대응 트리거 |
| 과속 | 7일 내 코히어런스 인덱스 0.2점+ 상승 | CIB 게이트 강제 호출 → 과적합 검증 |

### 3.3 M17 코히어런스 인덱스 구현

```
C = 0.5 × avg(CIB_scores) + 0.5 × (1 - avg(calibration_error))
```

- 최근 50 에피소드 윈도우 기준
- C 지속 하락 시 메타 루프 정체 대응 트리거 발동

### 3.4 어댑티브 N (Adaptive N) — L2 한계 보완

- 매 아우터 루프 종료 후 다음 N값 재계산
- CIB/피닉스 점수 변동성에 따라 `[base_N//2, base_N*2]` 범위에서 동적 조정
- 변경 이력: `data/memory/audit/adaptive_N_log.jsonl`

### Phase 3 마일스톤

- [ ] 7단계 프로세스 순차 실행
- [ ] M14 윈도우 통계 갱신 (아우터 루프)
- [ ] M16 알람 3종 (추락/정체/과속) 작동
- [ ] M17 코히어런스 인덱스 산출
- [ ] 어댑티브 N 동적 조정 작동
- [ ] 메타 루프 트리거 (정기 진화 / 긴급 점검) 발동

---

## Phase 4: 메타 루프 (Meta Loop) — 차순위

**목표:** 시스템 헌법 및 구조의 자가 재설계 루프 구현
**선행 조건:** Phase 3 (아우터 루프) 완료

### 4.1 메타 루프 주요 작업

| 작업 | 상세 | HITL |
|------|------|------|
| 헌법(L4) 개정 | 절대층/원칙층/전략층 업데이트, CIB 임계값 재조정 | **필수** |
| 아키텍처 자가 수정 | 워크플로우 재설계, 스킬 카테고리 추가/제거 | **필수** |
| 조직 재편 | 팀 구조 최적화, 연합 학습 토폴로지 변경 | **필수** |
| L5 정체성 재설계 | 셀프 모델 근본적 재평가 (updater.redesign_identity) | **필수** |

### 4.2 HITL 게이트 구현

- 모든 메타 루프 변경 사항은 **인간 승인 없이 실행 불가**
- 변경 제안 → 대기 → 인간 승인/거부 → 승인 시 적용
- `guard.py`의 `require_hitl_approval(layer)` 연동

### 4.3 수학적 가정 위반 탐지 — L3 한계 보완

- Phoenix Auditor(M15) 역할 확장: 손실 함수 비볼록 전환 탐지
- 탐지 지표: 성공률 분포 바이모달, CIB 분산 임계 초과
- 조치: CIB 임계값 자동 상향 (0.95 → 0.97), 메타 루프 긴급 점검 요청

### Phase 4 마일스톤

- [ ] 헌법 개정 파이프라인 (HITL 승인 포함)
- [ ] L5 정체성 근본 재설계 (통계 갱신과 분리)
- [ ] 수학적 가정 위반 탐지 + CIB 자동 상향
- [ ] 모든 변경 사항 HITL 게이트 통과 확인

---

## Phase 5: 한계 보완 및 고도화

**목표:** Section 7의 3가지 한계 보완 파이프라인을 각 단계에 통합
**선행 조건:** 해당 Phase 완료 후

### 5.1 헌법 시나리오 자동 초안 생성 (L1 한계 보완)

**통합 시점:** Phase 1.5 (L4 Constitution) 완료 후

```
[헌법 원칙 YAML] → LLM 프롬프트 → 테스트 시나리오 초안 → 인간 검토(HITL) → 확정된 K-Scenario
```

- `constitution/scenarios/drafts/` — LLM 생성 초안
- `constitution/scenarios/approved/` — 인간 승인 완료
- 승인된 시나리오만 CIB 검증에 사용

### 5.2 어댑티브 N (L2 한계 보완)

**통합 시점:** Phase 3 (Outer Loop) 완료 후
- Phase 3.4에 이미 포함됨

### 5.3 수학적 가정 완화 (L3 한계 보완)

**통합 시점:** Phase 4 (Meta Loop) 완료 후
- Phase 4.3에 이미 포함됨

---

## 구현 순서 요약 (Implementation Order Summary)

### 1차 구현 (Priority 1)

```
Phase 0: 프로젝트 인프라
  ├── 0.1 디렉터리 구조
  ├── 0.2 설정 파일
  ├── 0.3 유틸리티 (ids, time, serialization, logging)
  ├── 0.4 헌법 YAML 초안
  └── 0.5 L5 YAML 초안

Phase 1: 메모리 계층
  ├── 1.1 스키마 & 코어 (schemas, policies, ranking)
  ├── 1.2 L1 일화 기억 (encoder, store, retriever, event_logger)
  ├── 1.3 L3 절차적 기억 (skill_store, loader, executor, evaluator) ← 1.2와 병렬 가능
  ├── 1.4 L2 시맨틱 기억 (graph_store, json_store, extractor, resolver, reasoner)
  ├── 1.5 L4 헌법 (loader, validator, guard)
  ├── 1.6 L5 정체성 (identity_store, self_model, capability_model, updater)
  └── 1.7 메모리 매니저 (manager, consolidation, reflection)

Phase 2: 이너 루프
  ├── 2.1 LLM 클라이언트 (client, prompts, response_parser) ← Phase 1과 병렬 가능
  ├── 2.2 인지 모듈 (context_builder, planner, reasoner, decision, reflection_loop)
  ├── 2.3 도구 시스템 (base, registry, builtin/*) ← 2.1과 병렬 가능
  └── 2.4 이너 루프 파이프라인 (orchestrator, runtime, main)
```

### 2차 구현 (Priority 2)

```
Phase 3: 아우터 루프
  ├── 3.1 7단계 프로세스
  ├── 3.2 M16 그로스 레이트 레귤레이터
  ├── 3.3 M17 코히어런스 인덱스
  └── 3.4 어댑티브 N

Phase 4: 메타 루프
  ├── 4.1 헌법/아키텍처/조직 개정
  ├── 4.2 HITL 게이트
  └── 4.3 수학적 가정 위반 탐지

Phase 5: 한계 보완 정제
  ├── 5.1 헌법 시나리오 자동 생성
  ├── 5.2 어댑티브 N (Phase 3에 통합)
  └── 5.3 수학적 가정 완화 (Phase 4에 통합)
```

---

## 병렬 구현 가능 지점

| 병렬 A | 병렬 B | 조건 |
|--------|--------|------|
| Phase 1.2 (L1 Episodic) | Phase 1.3 (L3 Procedural) | Phase 1.1 완료 후 |
| Phase 1 (Memory) | Phase 2.1 (LLM Client) | Phase 0 완료 후 |
| Phase 2.1 (LLM) | Phase 2.3 (Tools) | Phase 0 완료 후 |

---

## 테스트 전략

### 단위 테스트 (각 Phase별)

| 테스트 파일 | 대상 Phase |
|-------------|-----------|
| `tests/test_episodic_memory.py` | Phase 1.2 |
| `tests/test_semantic_memory.py` | Phase 1.4 |
| `tests/test_procedural_memory.py` | Phase 1.3 |
| `tests/test_constitution.py` | Phase 1.5 |
| `tests/test_identity.py` | Phase 1.6 |
| `tests/test_memory_manager.py` | Phase 1.7 |

### 통합 테스트

| 테스트 | 대상 |
|--------|------|
| 이너 루프 E2E | Phase 2.4 (입력 → 계획 → 실행 → 평가 → 반성 → 저장) |
| 이중 저장 검증 | Phase 1.7 + 2.4 (반성 힌트 L2/L3 분산) |
| CIB 게이트 검증 | Phase 1.5 + 2.4 (0.95 미달 차단) |
| Phoenix Auditor 검증 | Phase 2.4 (6:4 채점, 수행자 분리) |

### 스크립트

| 스크립트 | 용도 | 시점 |
|----------|------|------|
| `scripts/init_memory.py` | 모든 메모리 저장소 초기화 | Phase 1 완료 후 |
| `scripts/migrate_skills.py` | 스킬 DB 마이그레이션 | Phase 1.3 완료 후 |
| `scripts/rebuild_semantic_graph.py` | L2 그래프 재구축 | Phase 1.4 완료 후 |
| `scripts/consolidate_episodes.py` | L1 → L2 수동 추출 | Phase 1.7 완료 후 |
| `scripts/inspect_memory.py` | 메모리 상태 검사 도구 | Phase 1 완료 후 |

---

## 위험 및 주의사항

1. **Chroma DB 버전 호환성:** Chroma 버전에 따라 API가 변경될 수 있음. `pyproject.toml`에 버전 핀 권장.
2. **임베딩 모델 의존성:** encoder.py는 임베딩 모델 교체가 용이하도록 추상화. 로컬 모델(Sentence-Transformers)과 API 모델(OpenAI) 모두 지원 권장.
3. **exec() 보안:** skill_executor.py의 코드 실행은 샌드박스 필수. 타임아웃, 리소스 제한, 허용 모듈 화이트리스트 적용.
4. **LLM 비용:** 선택적 주입(Selective Injection)은 토큰 절감이 핵심 목적. 컨텍스트 빌더에서 토큰 예산 하드 리밋 설정 필수.
5. **SQLite 동시성:** 아우터 루프와 이너 루프가 동시에 SQLite에 접근할 수 있음. WAL 모드 권장.
6. **헌법 YAML 검증:** 런타임에 헌법 YAML이 손상되면 시스템 전체가 차단될 수 있음. 로드 시 스키마 검증 필수.