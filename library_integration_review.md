# 라이브러리 도입 평가 및 재작성 아키텍처 설계

> LangChain, LangGraph, DeepAgents, Mem0, LiteLLM 도입 검토
> 작성일: 2025-07-03

---

## 1. 평가 배경

현재 Forge(Gnosis) 프로토타입은 모든 핵심 컴포넌트를 직접 구현:
- LLM client (Ollama/OpenAI 백엔드 추상화)
- 5계층 메모리 (L1 Episodic/Chroma, L2 Semantic/NetworkX, L3 Procedural/SQLite, L4 Constitution/YAML, L5 Identity/SQLite)
- 3루프 (Inner/Outer/Meta)
- Constitutional Governance (CIB 가드, tool_policy)
- 도구 시스템 (Tool ABC, ToolRegistry)

**도입动机:** 이미 구현된 것을 다시 구현할 필요 없고, 커스텀 코드의 안정성 우려

---

## 2. 라이브러리별 평가 (교체 관점)

### LiteLLM → `llm/client.py` 교체
- **판단: ✅ 즉시 교체**
- 대체 범위: LLM 백엔드 추상화, retry, embedding dispatch
- 기존 `_chat_ollama`, `_chat_openai`, `_chat_with_retry`, embedding 캐시 → `litellm.completion()` / `litellm.embedding()` 호출
- 약 300줄 → 50줄 수준으로 단순화
- `ChatResponse`/`ChatMessage` 인터페이스 유지하면 상위 코드 변경 불필요
- 위험: 거의 없음

### LangGraph → `orchestrator.py` + `runtime.py` 교체
- **판단: ⚠️ 부분 교체 추천 (Inner/Outer Loop만)**
- Inner Loop: 4단계 (Plan→Execute→Evaluate→Reflect) + retry 분기 → StateGraph + conditional edges
- Outer Loop: 7단계 순차 → StateGraph
- `WorkingMemory` dataclass + JSON staging → LangGraph `State` + 체크포인터 (SqliteSaver)
- 세션 복구 (현재 없음) → LangGraph 내장 체크포인팅
- **Meta Loop는 직접 구현 유지**: `architecture_modifier.py`가 런타임에 그래프 자체를 재구성해야 하므로 LangGraph의 컴파일 타임 고정 그래프 모델과 충돌

### Mem0 → `memory/episodic/` + `memory/semantic/` 교체
- **판단: ⚠️ 저장소 백엔드로만 제한적 교체**
- L1 Episodic: Mem0 벡터 저장 백엔드
- L2 Semantic: Mem0 graph memory (Mem0Graph)
- **유지해야 할 것:**
  - `Episode`/`Evaluation`/`Reflection` Pydantic 스키마 (success_score, pain_index, cib_score 등 도메인 특화 메타데이터)
  - `route_hint()` 듀얼 스토리지 전략 (general→L2, tool_specific→L3)
  - `Consolidator`와 `ReflectionProcessor`의 라우팅 로직
- **트레이드오프:** Mem0 자동 consolidation을 켜면 듀얼 스토리지 라우팅이 무력화됨 → 자동 기능을 끄고 저장소로만 사용 권장
- L3/L4/L5는 Mem0과 무관 → 기존 유지

### LangChain → `cognition/` + `tools/` + `llm/prompts.py` 교체
- **판단: ✅ 재작성 시 도입 추천 (기존 교체는 비추천, 재작성에서는 추천)**
- `PromptTemplate` → `ChatPromptTemplate`
- `response_parser.py` 수동 JSON 파싱 → `PydanticOutputParser`
- `Tool` ABC + `ToolParameter` → `@tool` 데코레이터 + LangChain 스키마
- `ToolRegistry` → LangChain Tool 리스트 + 커스텀 정책 래퍼
- 재작성에서는 LangGraph와 자연 통합되므로 도입 이점 증가

### DeepAgents → 전체 런타임 교체
- **판단: ⚠️ 재검토 필요 (sandbox/permission 기능 때문)**
- 초기 평가: Forge의 3루프 + 5계층 메모리와 충돌 → 배제
- **재검토 이유:** DeepAgents가 sandbox(가상 파일시스템, 격리된 도구 실행)와 permission(도구별 승인/거부) 시스템을 제공한다면, 이는 직접 구현 비용이 크고 보안에 민감한 영역
- **가능한 통합 방식:** DeepAgents를 Inner Loop의 실행 엔진으로 사용
  - DeepAgents: sandbox 도구 실행 + permission 시스템 (실행 계층)
  - Forge CIB 가드: 도구 실행 전 헌법적 검사 (거버넌스 계층)
  - 두 계층은 보완적 (경쟁하지 않음)
- **해결 필요:** DeepAgents의 파일 기반 메모리를 Forge 5계층 메모리로 교체 가능한지, permission 시스템에 외부 정책(CIB)을 플러그인 가능한지 확인 필요

---

## 3. 재작성 아키텍처 설계

### 핵심 설계 원칙
> 라이브러리가 제공하는 것은 라이브러리에 맡기고, Forge만의 독창적 가치는 직접 구현한다.

### Forge의 독창적 가치 (직접 구현 유지)
1. 5계층 메모리 스키마 (Episode, Evaluation, Reflection, Entity, Relation, Skill, Constitution, SelfModel)
2. 3루프 중 Meta Loop (자가수정 아키텍처)
3. Constitutional Governance (CIB 가드, tool_policy)
4. 듀얼 스토리지 전략 (general→L2, tool_specific→L3)
5. Adaptive N, Self-model calibration (M14)

### 라이브러리 역할 분담

```
┌─────────────────────────────────────────────────────────────┐
│                     Meta Loop (직접 구현)                      │
│  Constitution Revision · Architecture Modification · HITL    │
└──────────────────────────┬──────────────────────────────────┘
                           │ 트리거
┌──────────────────────────┴──────────────────────────────────┐
│                     Outer Loop (LangGraph)                    │
│  7-step StateGraph: Aggregate→Metrics→Cache→SelfModel→      │
│  Audit→GrowthRegulate→MetaTrigger                           │
└──────────────────────────┬──────────────────────────────────┘
                           │ N 에피소드마다
┌──────────────────────────┴──────────────────────────────────┐
│              Inner Loop (LangGraph StateGraph)                │
│  Plan → Execute → Evaluate → [retry?] → Reflect → END       │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  LiteLLM     │  │  LangChain   │  │  Mem0 (L1/L2)    │   │
│  │  LLM 호출    │  │  Tools       │  │  에피소드 저장    │   │
│  │  Embedding   │  │  Prompts     │  │  시맨틱 그래프   │   │
│  │  Retry       │  │  Parsers     │  │  자동 통합        │   │
│  └─────────────┘  └──────────────┘  └──────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  DeepAgents (검토 중)                                  │   │
│  │  Sandbox · Permission · 내장 도구 (file, shell, web) │   │
│  │  → Inner Loop 실행 엔진으로 사용 가능                  │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
         직접 구현: L3 Procedural · L4 Constitution · L5 Identity
                   CIB Guard · 듀얼 스토리지 라우팅 · Reflection
```

### 재작성 디렉터리 구조

```
forge-v2/
├── pyproject.toml
├── config/
│   ├── agent.yml              # LiteLLM 모델 설정
│   ├── memory.yml             # Mem0 + L3/L4/L5 경로
│   └── constitution/          # L4 YAML (기존 그대로 유지)
├── identity/                  # L5 (기존 그대로 유지)
├── src/forge/
│   ├── llm/
│   │   └── client.py          # LiteLLM 얇은 래퍼
│   ├── memory/
│   │   ├── schemas.py         # Episode, Evaluation, Reflection (Pydantic, 유지)
│   │   ├── manager.py         # L1~L5 라우터 (유지, 백엔드만 교체)
│   │   ├── consolidation.py   # L1→L2/L3 듀얼 스토리지 (유지)
│   │   ├── reflection.py      # 4필드 반성 처리 (유지)
│   │   ├── episodic/          # L1: Mem0 백엔드
│   │   ├── semantic/          # L2: Mem0 graph 백엔드
│   │   ├── procedural/        # L3: SQLite (기존 유지)
│   │   ├── constitution/      # L4: YAML (기존 유지)
│   │   └── identity/         # L5: SQLite (기존 유지)
│   ├── tools/
│   │   ├── base.py            # LangChain BaseTool 서브클래스
│   │   ├── registry.py        # LangChain ToolRegistry + L3 SkillAdapter
│   │   └── builtin/           # @tool 데코레이터 기반
│   ├── cognition/
│   │   ├── planner.py         # LangGraph 노드: LLM 계획 생성
│   │   ├── executor.py        # LangGraph 노드: 도구 디스패치
│   │   ├── evaluator.py       # LangGraph 노드: CIB + Phoenix 평가
│   │   └── reflector.py       # LangGraph 노드: 반성 추출
│   ├── loops/
│   │   ├── state.py           # InnerLoopState, OuterLoopState (TypedDict)
│   │   ├── inner_loop.py      # Inner Loop StateGraph
│   │   ├── outer_loop.py      # Outer Loop StateGraph
│   │   └── meta_loop.py       # Meta Loop (직접 구현)
│   └── utils/
└── tests/
```

---

## 4. 라이브러리 도입 결정 요약

| 라이브러리 | 도입 | 역할 | 교체되는 기존 코드 |
|-----------|------|------|-------------------|
| **LiteLLM** | ✅ | LLM 호출/임베딩 | `llm/client.py` 전체 |
| **LangGraph** | ✅ | Inner/Outer 루프 | `orchestrator.py` + `runtime.py` |
| **Mem0** | ✅ (저장소만) | L1/L2 벡터 저장/검색 | `episodic/store.py` + `semantic/graph_store.py` |
| **LangChain** | ✅ | 도구/프롬프트/파서 | `tools/` + `llm/prompts.py` + `response_parser.py` |
| **DeepAgents** | ⚠️ 재검토 | sandbox/permission (실행 엔진) | `tools/` 실행 계층 + sandbox 직접 구현 회피 |

### 직접 구현 유지 영역
- 5계층 스키마 (Episode, Evaluation, Reflection, Entity, Relation, Skill, Constitution, SelfModel)
- 듀얼 스토리지 라우팅 (`route_hint()`)
- Constitutional Governance (CIB 가드, tool_policy)
- L3 Procedural (SQLite), L4 Constitution (YAML), L5 Identity (SQLite)
- Meta Loop (자가수정 아키텍처)
- Adaptive N, Self-model calibration

---

## 5. 미해결 과제

### DeepAgents 재검토 필요 사항
- [ ] DeepAgents의 sandbox/permission 기능 정확한 확인
  - 가상 파일시스템, 격리된 도구 실행, 승인 시스템
- [ ] DeepAgents의 메모리 시스템을 Forge 5계층 메모리로 교체 가능한지
- [ ] DeepAgents의 permission 시스템에 외부 정책 (CIB 가드) 플러그인 가능한지
- [ ] DeepAgents의 루프 구조를 Forge 3루프로 확장 가능한지
- [ ] DeepAgents 의존성 트리 (LangGraph, LangChain 버전 호환성)

### 의존성 복잡도
```toml
dependencies = [
    "litellm>=1.50",
    "langgraph>=0.2",
    "langchain-core>=0.3",
    "langchain>=0.3",
    "mem0ai>=0.1",
    "pydantic>=2.5",
    "pyyaml>=6.0",
    # DeepAgents (검토 후 추가)
]
```