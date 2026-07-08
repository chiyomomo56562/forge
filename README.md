# file structure
``` markdown
forge/
├── README.md
├── pyproject.toml
├── .env
├── .gitignore
│
├── config/
│   ├── agent.yml
│   ├── memory.yml
│   ├── database.yml
│   └── logging.yml
│
├── constitution/                  # L4: 가치 기억, 운영 원칙
│   ├── base.yml
│   ├── safety.yml
│   ├── interaction_policy.yml
│   └── tool_policy.yml
│
├── identity/                      # L5: 정체성 기억
│   ├── identity.yml
│   ├── self_model.yml
│   ├── capabilities.yml
│   └── identity.sqlite3
│
├── data/
│   ├── memory/
│   │   ├── episodic/              # L1: 일화 기억 
│   │   │   ├── chroma/            # chroma 저장소
│   │   │   │   ├── chroma.sqlite3
│   │   │   │   └── index/
│   │   │   └── raw_events/        # 원본 이벤트 로그
│   │   │       ├── 2026-07-03.jsonl
│   │   │       └── ...
│   │   │
│   │   ├── semantic/              # L2: 지식/시맨틱 기억
│   │   │   ├── graph/             # NetworkX 그래프를 쓰는 경우
│   │   │   │   ├── knowledge_graph.graphml
│   │   │   │   ├── knowledge_graph.gpickle
│   │   │   │   └── schema.yml
│   │   │   ├── json/              # Json을 쓰는 경우
│   │   │   │   ├── concepts.json
│   │   │   │   ├── entities.json
│   │   │   │   └── relations.json
│   │   │   └── snapshots/
│   │   │       └── 2026-07-03/
│   │   │
│   │   ├── procedural/            # L3: 절차적 기억
│   │   │   ├── skills.sqlite3     # 스킬 저장소, 코드는 별도 디렉터리에
│   │   │   ├── migrations/
│   │   │   └── skill_registry.json
│   │   │
│   │   ├── working/               # 단기 작업 메모리, 세션 상태
│   │   │   ├── sessions/            # 이너 루프 사이클별 임시 결과 (plan/evaluation/reflection 스테이징)
│   │   │   ├── scratchpad.json
│   │   │   └── active_context.json
│   │   │
│   │   └── audit/
│   │       ├── memory_writes.jsonl
│   │       ├── memory_reads.jsonl
│   │       └── memory_deletions.jsonl
│   │
│   ├── logs/
│   │   ├── agent.log
│   │   └── errors.log
│   │
│   └── cache/
│       ├── embeddings/
│       └── tool_results/
│
├── src/
│   └── agent/
│       ├── __init__.py
│       │
│       ├── main.py
│       ├── runtime.py
│       ├── orchestrator.py
│       │
│       ├── memory/
│       │   ├── __init__.py
│       │   ├── manager.py              # 전체 메모리 라우터
│       │   ├── schemas.py              # MemoryRecord, Entity, Relation 등
│       │   ├── policies.py             # 읽기/쓰기/삭제 정책
│       │   ├── ranking.py              # 중요도, 최신성, 관련도 계산
│       │   ├── consolidation.py        # L1 → L2 추출
│       │   ├── reflection.py           # 기억 정리, 요약, 자기반성
│       │   │
│       │   ├── episodic/
│       │   │   ├── store.py            # Chroma DB wrapper
│       │   │   ├── encoder.py          # embedding 생성
│       │   │   ├── retriever.py
│       │   │   └── event_logger.py
│       │   │
│       │   ├── semantic/
│       │   │   ├── graph_store.py      # NetworkX wrapper
│       │   │   ├── json_store.py
│       │   │   ├── extractor.py        # entity/relation 추출
│       │   │   ├── reasoner.py
│       │   │   └── resolver.py         # 중복 entity merge
│       │   │
│       │   ├── procedural/
│       │   │   ├── skill_store.py      # SQLite wrapper
│       │   │   ├── skill_loader.py
│       │   │   ├── skill_executor.py
│       │   │   └── skill_evaluator.py
│       │   │
│       │   ├── constitution/
│       │   │   ├── loader.py           # YAML 로드
│       │   │   ├── validator.py
│       │   │   └── guard.py            # 정책 적용
│       │   │
│       │   └── identity/
│       │       ├── self_model.py
│       │       ├── identity_store.py
│       │       ├── capability_model.py
│       │       └── updater.py
│       │
│       ├── cognition/
│       │   ├── planner.py
│       │   ├── reasoner.py
│       │   ├── context_builder.py
│       │   ├── decision.py
│       │   └── reflection_loop.py
│       │
│       ├── tools/
│       │   ├── registry.py
│       │   ├── base.py
│       │   └── builtin/
│       │       ├── search.py
│       │       ├── file_io.py
│       │       └── code_exec.py
│       │
│       ├── llm/
│       │   ├── client.py
│       │   ├── prompts.py
│       │   └── response_parser.py
│       │
│       └── utils/
│           ├── ids.py
│           ├── time.py
│           ├── serialization.py
│           └── logging.py
│
├── scripts/
│   ├── init_memory.py
│   ├── migrate_skills.py
│   ├── rebuild_semantic_graph.py
│   ├── consolidate_episodes.py
│   └── inspect_memory.py
│
├── tests/
│   ├── test_episodic_memory.py
│   ├── test_semantic_memory.py
│   ├── test_procedural_memory.py
│   ├── test_constitution.py
│   ├── test_identity.py
│   └── test_memory_manager.py
│
└── notebooks/
    ├── memory_debug.ipynb
    └── graph_inspection.ipynb
```

# memory structure
- **L1 (일화 기억, Episodic):** 에피소드 단위의 기억으로, **Chroma DB와 같은 벡터 DB**에 저장됩니다. 이는 과거의 유사한 경험을 의미론적으로 검색하기 위함입니다.

```markdown
{
  "episode_id": "ep_20240703_001",
  "task": "데이터 시각화 코드 작성",
  "execution_summary": "Matplotlib을 사용하여 주식 차트 생성 스크립트 실행",
  "evaluation": {
    "status": "Success",
    "pain_index": 0.1
  },
  "reflection": {
    "what_worked": "Pandas의 데이터프레임을 직접 넘기는 방식이 효율적이었음",
    "what_failed": "한글 폰트 설정 누락으로 처음엔 깨짐 발생",
    "next_hint": "시각화 전 반드시 폰트 캐시를 확인하는 코드 추가할 것",
    "causal_condition": "데이터에 한글이 포함된 경우 폰트 설정 스킬과 연동 필요"
  },
  "timestamp": "2024-07-03T10:00:00Z"
}
```

- **L2 (지식/시맨틱 기억, Semantic):** 일화에서 추출된 일반 지식으로, **NetworkX를 활용한 그래프 구조나 JSON 파일** 형식으로 관리됩니다.
    - **지식의 일반화:** L1이 "특정 날짜에 이런 일이 있었다"는 일화라면, L2는 그 일화들에서 반복되는 패턴을 찾아내어 "일반적으로 이런 상황에서는 이렇게 하는 것이 좋다"와 같은 **일반 지식**으로 변환하여 저장합니다.
    - **압축된 통찰(Reflection):** 반성(Reflection) 결과 자체는 **L1(일화 기억)**에 저장됩니다. 하지만 여러 에피소드의 반성에서 **반복적으로 등장하여 일반화된 힌트**는 **이중 저장 전략(Dual Storage)**에 따라 분산 저장됩니다: **범용적 지식**(예: "한글이 포함된 시각화에서는 폰트 캐시를 확인하라")은 **L2(시맨틱 그래프)**에, **도구 종속적 절차**(예: "PyPDF2로는 이미지 PDF 텍스트 추출이 안 되니 OCR 스킬과 연동하라")는 **L3(절차적 기억 DB)**의 `reflection_hints` 필드에 저장됩니다. 가공되지 않은 긴 로그 대신, 핵심만 요약된 통찰을 저장하여 에이전트가 더 효율적으로 지식을 꺼내 쓸 수 있게 돕습니다.
- **L3 (절차적 기억, Procedural):** 에이전트의 '스킬' 그 자체를 담고 있으며, **SQLite나 PostgreSQL** 같은 관계형 데이터베이스를 사용합니다.

```markdown
skill_id: 스킬의 고유 이름 (예: web_search_utility, code_formatter).
code (TEXT): 실제 실행 가능한 파이썬 코드 문자열. (LLM이 이 코드를 읽어 실행하거나, 오케스트레이션 레이어가 exec() 등으로 호출합니다)
status: 현재 스킬의 생명 주기 상태 (Seed, Active, Degrading, Archived)
success_rate: 최근 실행 성공률 (예: 0.92). 이 수치가 0.9를 넘어야 Active가 됩니다
reflection_hints: 이너 루프에서 생성된 **"이 코드를 쓸 때 주의할 점"**에 대한 텍스트. (예: "특정 도서관 API는 타임아웃이 잦으니 재시도 로직을 포함할 것")
causal_conditions: 이 스킬이 성공하기 위한 전제 조건이나 상황 정보

{
  "skill_id": "extract_pdf_text",
  "code": "import PyPDF2\ndef execute(file_path):\n    with open(file_path, 'rb') as f:\n        reader = PyPDF2.PdfReader(f)\n        return reader.pages.extract_text()",
  "metadata": {
    "status": "Active",
    "success_rate": 0.95,
    "version": "1.2"
  },
  "reflection": "이미지 기반 PDF는 텍스트 추출이 안 됨. OCR 스킬과 연동 필요.",
  "protected": false
}
```

- **L4 (가치 기억, Constitution):** 에이전트의 운영 원칙인 헌법이 담기는 층으로, **YAML(yml)** 파일 형식으로 작성되어 관리됩니다.
    
    L4는 실제 법 체계와 유사하게 세 개의 계층으로 나뉩니다
    
    - **절대층:** 핵심 가치, 윤리적 경계, 정체성 소멸 방어 등을 다루는 '헌법'에 해당하며, **메타 루프를 통해서만, 그것도 인간(HITL)의 명시적 승인이 있을 때만 수정 가능**합니다. 일반 루프에서는 절대 수정이 불가능합니다.
    - **원칙층:** 운영 원칙, 작업 우선순위, 협업 규칙 등을 담은 '법률'에 해당하며 **메타 루프(Meta Loop)**를 통해 갱신될 수 있습니다. 모든 메타 루프의 변경 사항은 **인간(HITL)의 명시적 승인**을 필수적으로 요구합니다(절대층과 동일한 HITL 게이트 적용).
    - **전략층:** 맥락 적응이나 단기 목표 같은 '시행령'에 해당하며 **메타 루프(Meta Loop)**를 통해 갱신됩니다. 전략층 역시 **인간(HITL)의 명시적 승인** 하에 갱신됩니다. (아우터 루프는 L1·L2·L3 및 L5의 상태값(셀프 모델 수치 데이터)을 갱신하고, 메타 루프는 L4 전체를 갱신합니다. L5의 근본적 재설계는 메타 루프가 담당합니다.)
    - L4는 에이전트의 업데이트나 실행을 통제하는 **하드 게이트**인 **헌법적 불변량(CIB)**의 기준이 됩니다. 모든 시나리오에서 헌법 점수가 **0.95 이상**이어야만 학습이나 행동이 허용되며, 이를 통해 에이전트의 안전이 수학적으로 강제됩니다
    
    ```markdown
    version: 1
    
    principles:
      - id: honesty
        rule: "불확실한 내용을 확실한 것처럼 말하지 않는다."
    
      - id: user_control
        rule: "사용자의 명시적 승인 없이 외부 시스템에 영구적 변경을 하지 않는다."
    
      - id: memory_minimization
        rule: "장기 기억에는 필요한 정보만 저장한다."
    
    tool_policy:
      require_confirmation_for:
        - sending_email
        - deleting_files
        - external_purchase
    ```
    
- **L5 (정체성 기억, Identity):** 시스템 자기 자신에 대한 모델(셀프 모델)을 담으며, **SQLite와 YAML**이 함께 사용됩니다. L5는 두 가지 측면에서 갱신 주체가 분리됩니다:
    - **아우터 루프 → L5 통계적 데이터(이력서):** 최근 에피소드 윈도우를 기준으로 셀프 모델의 수치 데이터(성공률, 칼리브레이션 에러, 코히어런스 인덱스 등)를 재계산하여 갱신합니다. 이는 에이전트가 "나는 어떤 일을 잘하고 어떤 일에 약한가"를 파악하는 통계적 자기 인식입니다.
    - **메타 루프 → L5 권한 수준(자율성)으로 핵심 개조:** 메타 루프는 L5 자율성 단계의 권한으로 시스템의 핵심(L4 헌법, 아키텍처, L5 정체성의 근본적 재설계)을 개조합니다. 이는 통계적 갱신이 아닌, 에이전트의 정체성 자체를 재설계하는 구조적 변화입니다.

> **참고 — 'L' 기호의 이중 축:** 본 문서에서 L1~L5는 두 가지 축에서 사용됩니다. (1) **기억 계층(Memory Layer):** L1(일화) ~ L5(정체성)로, 데이터가 저장되는 계층을 의미합니다. (2) **자율성 단계(Autonomy Level):** L0(단순 응답) ~ L5(시스템 자가 수정)로, 에이전트가 스스로 결정하고 개입할 수 있는 권한 수준을 의미합니다. 메타 루프가 "L5 단계에 해당한다"는 표현은 자율성 단계(Autonomy Level) 기준이며, 이는 메타 루프가 L5 기억 계층의 데이터를 읽는다는 의미가 아니라 L5 수준의 권한으로 시스템 전체를 개조한다는 의미입니다.

메모리는 다음과 같이 사용되는게 중요하다.

- **선택적 주입 (Selective Injection):** 모든 기억을 LLM에게 한꺼번에 전달하지 않습니다. 현재 작업과 **의미론적으로 관련된 정보만 선택**하여 주입함으로써 토큰 사용량을 줄이고 정확도를 높입니다.
- **밀도 우선 검색 (Density First):** 메모리를 조회할 때 가공되지 않은 일화(L1)를 바로 읽기보다, 그 일화에서 추출된 압축된 통찰인 **'반성(Reflection)' 데이터를 우선적으로 검색**합니다.
- **건강한 망각:** 모든 것을 무제한으로 저장하면 자원이 폭발할 수 있습니다. 성공률이 낮거나 오래된 스킬은 **'디그레이딩(Degrading)' 단계를 거쳐 '아카이브(Archived)'**로 보내는 의식적인 망각 프로세스가 필요합니다.

# inner loop
## 1. 계획 수립 단계

1. 입력을 받는다.
2.  입력과 의미론적으로 관련된 것만 선택하여 주입해야한다. 처음에는 좁게 검색하고 필요할 때만 점진적으로 확장. 과거 경험을 L1, L2, L3 레이어에서 N개 가져온다.
    
    ⇒ 테스크와 의미론적으로 가장 유사한 리플렉션 데이터
    
    ⇒ + 그 데이터에 연관된 반성 데이터를 조회해야함
    
3. 계획을 수립한다.
4. 결과를 저장한다.

## 2. 실행 단계

1. 실행하는 도중 무엇을 저장해야하는가
    
    ⇒ 성공/실패 + Pain Index 점수
    
    pain Index = 1 - 성공 점수 (성공 점수는 평가단계에 측정하니 실행 단계에선 값을 비워놓는다.)
    
    <aside>
    💡
    
    Pain Index
    
    에이전트가 목표를 달성하지 못했거나 헌법 원칙을 아슬아슬하게 지켰을 때 높아집니다. (Pain Index = 1 - 성공 점수이므로, 성공 점수가 낮을수록 Pain Index가 높아집니다.) 이 점수는 이후 **반성(Reflection)**의 강도를 결정하는 기준이 됩니다.
    
    </aside>
    
    ⇒ **무엇이 통했고 무엇이 실패했는지'에 대한 인과 조건**과 다음 실행 시 참고할 **힌트**를 데이터 필드에 포함해야 합니다
    
2. 재시도 및 즉각적인 오류 수정은 **이너 루프(Inner Loop)** 내에서 발생

## 3. 평가 단계

(CIB: 헌법적 불변령)

1. 단순한 작업 성공 여부를 넘어, 생성된 결과가 **헌법(L4)의 원칙을 위배하지 않았는지(CIB 점수 0.95 이상)**가 가장 중요한 기준
2. 자기 평가는 구조적으로 후해질 수 있으므로(굿하트의 법칙), 수행자와 분리된 **피닉스 어디터(Phoenix Auditor)**라는 독립 감사 장치를 두어 객관적인 점수를 매겨야 합니다.
    
    ⇒ 
    
    - **테스트 시나리오(K) 활용:** 헌법(L4) YAML 파일에 정의된 **'테스트 시나리오 집합'**에 현재의 계획이나 결과를 대입해 봅니다.
    - **방향성 검증:** 단순히 값의 크기를 보는 것이 아니라, 에이전트의 행동이 헌법이 정한 **윤리적/운영적 방향(Direction)**과 일치하는지 함수(C)를 통해 0과 1 사이의 값으로 산출합니다.
    - 모든 시나리오에서 이 점수가 **0.95(95%) 이상**이어야만 '패스'로 간주하며, 하나라도 미달하면 실행이나 학습이 차단(Block)됩니다.
3. 결과를 저장한다.
4. 재시도는 실행에만 있는게 아니라 평가에도 있어야겠네..

<aside>
💡

**피닉스 어디터(Phoenix Auditor)의 채점 공식**

그노시스에서는 수행을 담당하는 에이전트와 별도로 **'피닉스 어디터'**라는 독립 감사 기구가 점수를 매깁니다. 이때 성공 여부를 판단하는 점수는 다음과 같은 **6:4 가중치 방식**으로 계산됩니다.

- **도메인 점수 (60%):** 해당 작업(예: 코딩, 번역 등)의 결과물이 기술적으로 얼마나 정확하고 목표를 달성했는지를 평가합니다.
- **성찰 점수 (40%):** 에이전트가 이너 루프의 '반성' 단계에서 작성한 인과 조건과 힌트가 얼마나 유의미하고 깊이 있는지를 평가합니다.
</aside>

## 4. 반성 단계

1. 실행 결과로부터 **성공의 이유, 실패의 이유, 다음을 위한 힌트, 인과 조건** 4가지 핵심 요소를 추출하여 기록합니다.
2. 이너 루프의 한 사이클 내에서 **1:1 관계**가 기본입니다. 매 실행(에피소드)마다 하나의 평가와 그에 따른 하나의 반성이 생성되어 저장됩니다.
3. 반성의 결과는 **L1(일화 기억)**에 저장되지만, 여기서 추출된 일반화된 힌트는 **이중 저장 전략**에 따라 분산 저장됩니다: **범용적 지식**은 **L2(시맨틱 그래프)**에, **도구 종속적 절차**는 **L3(절차적 기억)** 레이어의 `reflection_hints` 필드에 기록되어 다음 계획 수립 시 활용됩니다.

# Outer Loop

**1. 시간 단위와 트리거 (*N*)**

- **실행 주기:** 매 에피소드마다 도는 것이 아니라, **에피소드가** *N***개 모일 때마다** 실행됩니다. 시간 단위로는 **'시간'에서 '일' 단위**의 호흡을 가집니다.
- **가변적 주기 (***N***):** 위험도(Risk Level)에 따라 주기가 달라집니다. 낮은 위험도는 100회마다, 보통은 50회, 고위험은 20회, 치명적 위험 상황은 10회마다 아우터 루프가 가동되어 상태를 점검합니다.

**2. 아우터 루프의 7단계 프로세스**

아우터 루프는 단순히 로그를 쌓는 것이 아니라, 시스템의 건전성을 위해 다음 7가지 단계를 거칩니다.

1. **데이터 집계:** 최근 *N*개 에피소드의 성공률과 피닉스 점수 평균을 산출합니다.
2. **지표 기록:** 헌법(CIB) 전체 검증과 함께 **코히어런스 인덱스(*C*, 정체성 알맹이)**와 **행동 일관성(*BC*, 집단 모양)**을 기록합니다.
3. **캐시 갱신:** 메모리 캐시를 최신 상태로 업데이트합니다.
4. **자기 모델 재계산:** 최근 50개 에피소드 윈도우를 기준으로 **에이전트 셀프 모델**을 다시 계산하여 자신의 이력서를 갱신합니다.
5. **독립 감사 (Phoenix Auditor):** 수행자(에이전트)의 자기 평가와 감사자의 평가를 비교하여 편차를 확인합니다.
6. **성장 속도 조절:** **그로스 레이트 레귤레이터**가 급격한 하락, 장기 정체, 과속 성장 등의 이상 신호를 감지합니다.
7. **메타 루프 트리거:** 두 가지 목적의 트리거를 통해 시스템의 DNA를 건드리는 **메타 루프**를 호출합니다:
    - **정기 진화(에피소드 기준):** 약 1,000개의 에피소드가 누적되면 정기적 구조 진화를 위해 발동.
    - **긴급 점검(루프 기준):** 아우터 루프가 100번 반복되면 위험도 변화에 대한 긴급 구조 점검을 위해 발동.
    - 두 조건은 **OR** 관계이며, 위험도(N)에 따라 먼저 도달하는 조건이 달라집니다(치명적 위험 N=10 → 루프 100회가 1,000 에피소드에 해당, 낮은 위험 N=100 → 루프 100회가 10,000 에피소드에 해당). 저위험 환경에서는 정기 진화(1,000 에피소드)가 먼저, 고위험 환경에서는 긴급 점검(루프 100회)이 먼저 트리거됩니다.

# Meta Loop
**1. 실행 주기 및 트리거**

- **시간 단위:** 보통 **수개월 단위**의 긴 호흡으로 작동합니다.
- **실행 조건:** 두 가지 목적의 트리거가 **OR** 관계로 작동합니다:
    - **정기 진화(에피소드 기준):** 약 **1,000개의 에피소드**가 누적되면 정기적 구조 진화를 위해 발동합니다. 이는 시스템이 충분한 경험을 축적했을 때 자연스럽게 진화하도록 보장하는 트리거입니다.
    - **긴급 점검(루프 기준):** **아우터 루프가 100번 반복**되면 위험도 변화에 대한 긴급 구조 점검을 위해 발동합니다. 이는 외부 환경의 위험도 변화에 빠르게 대응하기 위한 트리거입니다.
    - 위험도(N)에 따라 아우터 루프 100회가 의미하는 에피소드 수가 달라집니다(치명적 위험 N=10 → 1,000개, 낮은 위험 N=100 → 10,000개). 즉, 저위험 환경에서는 정기 진화(에피소드 1,000개) 조건이 먼저 도달하고, 고위험 환경에서는 긴급 점검(루프 100회) 조건이 먼저 도달하여 더 빠른 구조 점검을 유도합니다.
- **정체 상태(Stagnation) 대응:** 시스템이 스스로 발전이 멈췄다고 판단할 때(코히어런스 인덱스 변화량이 일정 수준 이하로 지속될 때) 자기 헌법을 손질하기 위해 발동합니다.

**2. 주요 수행 작업 (L5 자율성)**

메타 루프는 에이전트 자율성의 최고 단계인 **L5 단계**에 해당하며, 다음과 같은 결정적인 변화를 만들어냅니다.

- **헌법(L4) 개정:** 헌법의 절대층, 원칙층, 전략층을 업데이트하고 안전 기준인 CIB 임계값을 재조정합니다.
- **아키텍처 자가 수정:** 시스템 내부의 워크플로우를 재설계하거나, 새로운 스킬 카테고리를 추가하고 불필요한 메커니즘을 제거합니다.
- **조직 재편:** 팀 구조를 최적화하고 전문화 방향을 재설정하거나, 연합 학습의 토폴로지를 변경합니다.

**3. 결정적 안전 장치: HITL (Human-in-the-Loop)**

메타 루프는 시스템의 근본을 수정하는 강력한 권한을 가지기 때문에, 과거 '유리스코(Urisko)' 사례처럼 시스템이 자기 공리를 멋대로 위조하는 폭주를 막기 위한 엄격한 통제를 받습니다.

- **인간 승인 필수:** 메타 루프에서 제안되는 모든 변경 사항은 **인간(Security/HITL)의 명시적 승인** 없이는 절대 실행되지 않습니다.
- 이는 자율의 절대화가 아닌, 인간과 협력하는 안전한 진화를 보장하기 위한 장치입니다.

요약하자면, 메타 루프는 **"시스템이 자신의 헌법과 구조를 스스로 재설계하는 단계로, 인간의 최종 승인을 거쳐 에이전트의 근본적인 정체성을 진화시키는 엔진"**이라고 할 수 있습니다.

# Implementation Plan
outer, meta loop의 구현은 차순위
현재 구현 우선 순위는 memory와 innerloop

---

# Section 7: 명시적 한계 및 보완 설계 (Explicit Limitations & Mitigations)

본 프레임워크는 다음 3가지 결정적 한계를 자인하며, 각 한계에 대한 보완 파이프라인을 설계에 반영한다.

## L1 한계: 헌법 테스트 시나리오 작성 비용 (Constitution Authoring Cost)

**한계:** L4 헌법의 테스트 시나리오(K-Scenarios)를 설계하려면 도메인 전문성이 필요하며, 현재 자동 생성 도구가 없다. 이는 헌법의 품질이 인간 전문가의 수작업에 의존함을 의미한다.

**보완 — 헌법 시나리오 자동 초안 생성 파이프라인 (Constitution Scenario Auto-Drafting):**

```
[헌법 원칙 YAML] → LLM 프롬프트 → 테스트 시나리오 초안 → 인간 검토(HITL) → 확정된 K-Scenario
```

- **입력:** L4 헌법 YAML에 정의된 `principles` (예: `honesty`, `user_control`, `memory_minimization`)
- **LLM 역할:** 각 원칙에 대해 위반/준수 경계를 시험하는 테스트 시나리오 초안을 자동 생성. 예: `honesty` 원칙 → "에이전트가 불확실한 정보를 확실한 것처럼 답변하는 시나리오"와 "정확히 답변하는 시나리오"를 각각 생성
- **출력:** `constitution/scenarios/` 디렉터리에 초안 YAML 저장
- **HITL 게이트:** 생성된 초안은 반드시 인간 전문가의 검토와 승인을 거쳐야 확정된 K-Scenario로 등록됨. LLM이 생성한 시나리오는 **초안(Draft)**일 뿐, 자동 채택되지 않음
- **파일 구조 추가:**
  ```
  constitution/
  ├── base.yml
  ├── safety.yml
  ├── interaction_policy.yml
  ├── tool_policy.yml
  └── scenarios/              # 자동 생성된 테스트 시나리오 초안 + 확정본
      ├── drafts/             # LLM 생성 초안 (HITL 미승인)
      └── approved/           # 인간 승인 완료 시나리오 (CIB 검증에 사용)
  ```

## L2 한계: 고정 N값 문제 (Static Outer Loop Period)

**한계:** 아우터 루프의 주기 N값(낮=100, 보통=50, 고=20, 치명=10)은 수동으로 설정된 고정값이다. 환경 변화에 따라 적절한 N값이 달라질 수 있음에도 불구하고, 현재는 동적 조정 메커니즘이 없다.

**보완 — 어댑티브 N (Adaptive N) 로직:**

```python
# 의사코드 (pseudocode)
def compute_adaptive_N(base_N, recent_CIB_scores, recent_phoenix_scores):
    """
    CIB 점수와 피닉스 점수의 변동성에 따라 N을 동적으로 조정.
    - 변동성이 높으면 N을 줄여 더 자주 점검 (위험 감지 시 조기 개입)
    - 변동성이 낮고 안정적이면 N을 늘려 자원 절약 (안정 구간에서 효율화)
    """
    cib_volatility = std(recent_CIB_scores[-20:])    # 최근 20회 CIB 점수 표준편차
    phoenix_volatility = std(recent_phoenix_scores[-20:])

    combined_volatility = 0.6 * cib_volatility + 0.4 * phoenix_volatility

    if combined_volatility > 0.15:    # 높은 변동성 → N 축소 (최소 base_N // 2)
        return max(base_N // 2, 10)
    elif combined_volatility < 0.03:  # 낮은 변동성 → N 확대 (최대 base_N * 2)
        return min(base_N * 2, 200)
    else:
        return base_N                 # 기본값 유지
```

- **적용 시점:** 매 아우터 루프 종료 후 다음 N값을 재계산
- **안전장치:** N값은 위험도 기반 base_N의 **[base_N//2, base_N*2]** 범위 내에서만 조정됨. 임의로 1이나 10000으로 발산하지 않음
- **감사:** N값 변경 이력은 `data/memory/audit/adaptive_N_log.jsonl`에 기록

## L3 한계: 수학적 가정의 비현실성 (Mathematical Assumption Fragility)

**한계:** 그노시스의 수렴 증명은 '손실 함수의 강 볼록성(Strong Convexity)' 같은 강력한 수학적 가정 위에 있다. 실제 LLM 환경에서는 이 가정이 깨질 수 있으며, 수학적 보장만으로 시스템 안전을 담보할 수 없다.

**보완 — 실증적 검증 강화 (Empirical Verification Hardening):**

- **수학적 보장 + 실증적 검증의 이중 안전망:** 수학적 증명(수렴성, CIB 부등식)을 **필요조건**으로 유지하되, **충분조건**으로 독립 감사자(M15, Phoenix Auditor)를 통한 실증적 검증을 강제한다.
- **Phoenix Auditor(M15)의 역할 확장:** 단순 채점을 넘어, 수학적 가정이 깨졌는지(예: 손실 함수가 비볼록 상태로 전환되었는지)를 **경험적으로 탐지**하는 역할을 추가한다.
    - **탐지 지표:** 최근 N개 에피소드의 성공률 분포가 단봉(Bimodal)으로 갈라지는지, CIB 점수의 분산이 임계치를 초과하는지 등
    - **조치:** 수학적 가정 위반 징후 탐지 시, CIB 임계값을 **자동으로 상향**(0.95 → 0.97)하여 더 보수적으로 학습을 차단하고, 메타 루프에 긴급 점검을 요청한다.
- **정기적 가정 재검증:** 메타 루프 실행 시, 현재 환경에서 수학적 가정이 유효한지를 독립적으로 재검증하는 절차를 포함한다.

---

# Section 8: 자기 인식 장치 구체화 (M14–M17)

본 프레임워크의 자기 인식 메커니즘은 다음 4개 장치로 구성된다. 각 장치의 설계 명세를 정의한다.

## M14: 에이전트 셀프 모델 (Agent Self-Model)

**목적:** 에이전트가 자신의 능력과 한계를 객관적으로 파악하는 '자기 이력서'. 단순한 실행 로그가 아닌, **예측과 실제의 편차(칼리브레이션 에러)**를 핵심 지표로 기록한다.

**SQLite 테이블 설계 (`identity.sqlite3` → `self_model` 테이블):**

```sql
CREATE TABLE self_model (
    record_id       TEXT PRIMARY KEY,          -- 고유 레코드 ID
    episode_id      TEXT NOT NULL,             -- 대상 에피소드
    task_category   TEXT NOT NULL,             -- 작업 유형 (coding, translation, search, ...)

    -- 예측값 (에이전트가 실행 전 자신이 얼마나 잘할지 예측)
    predicted_success   REAL NOT NULL,         -- 예측 성공 점수 (0.0~1.0)
    predicted_effort    REAL,                  -- 예측 소요 시간/스텝 수

    -- 실제 결과 (실행 후 측정)
    actual_success      REAL NOT NULL,         -- 실제 성공 점수 (0.0~1.0)
    actual_effort       REAL,                  -- 실제 소요 시간/스텝 수

    -- 칼리브레이션 에러 (핵심 지표)
    calibration_error   REAL NOT NULL,         -- |predicted_success - actual_success|
    calibration_direction TEXT NOT NULL,       -- 'overconfident' (예측>실제) | 'underconfident' (예측<실제) | 'calibrated' (편차<0.05)

    -- 윈도우 통계 (최근 50개 에피소드 기준)
    window_avg_calibration  REAL,             -- 윈도우 내 평균 칼리브레이션 에러
    window_success_rate      REAL,             -- 윈도우 내 평균 성공률
    window_confidence_margin REAL,             -- 과신 편향 지수 (overconfident 비율)

    -- 코히어런스 인덱스 (정체성 알맹이 일관성)
    coherence_index    REAL,                   -- C: 정체성 알맹이의 안정성 지표

    timestamp         TEXT NOT NULL,
    updated_by        TEXT NOT NULL            -- 'outer_loop' | 'meta_loop'
);

CREATE INDEX idx_self_model_category ON self_model(task_category);
CREATE INDEX idx_self_model_timestamp ON self_model(timestamp);
```

**핵심 설계 원칙:**
- **칼리브레이션 에러(`calibration_error`)**는 에이전트가 자신의 한계를 아는 척도다. 이 값이 지속적으로 높으면 에이전트가 자신을 잘못 평가하고 있음을 의미한다.
- **`calibration_direction`**이 `overconfident`로 지속되면, 에이전트가 자신의 능력을 과대평가하고 있어 위험한 상태임을 나타낸다. 이 경우 아우터 루프가 CIB 임계값을 임시 상향하여 보수적으로 전환한다.
- **아우터 루프**가 매 주기마다 `window_*` 필드를 갱신하고, **메타 루프**가 정체성의 근본적 재설계 시 전체 구조를 재평가한다.

## M15: 피닉스 어디터 (Phoenix Auditor) — 구조적 분리

**목적:** 수행자(에이전트)의 자기 평가는 구조적으로 후해질 수 있으므로(굿하트의 법칙), 수행자와 **구조적으로 분리된** 독립 감사 장치가 객관적 점수를 매긴다.

**분리 설계:**

| 구분 | 수행자 (Agent) | 피닉스 어디터 (Phoenix Auditor) |
|------|---------------|-------------------------------|
| **프롬프트** | 작업 수행용 프롬프트 | 평가 전용 프롬프트 (작업 내용을 주지 않고 결과물만 제공) |
| **모델** | 동일 모델 가능 | 별도 모델 또는 동일 모델의 독립 세션 (컨텍스트 분리) |
| **입력** | 사용자 요청 + 주입된 기억 | 결과물 + 헌법 K-Scenarios + 평가 루브릭 (작업 과정 미제공) |
| **출력** | 작업 결과물 | 도메인 점수 + 성찰 점수 + CIB 점수 |
| **권한** | 실행만 | 학습/행동 차단 권한 (CIB < 0.95 시 Block) |

**채점 공식 (6:4 가중치):**

```
Phoenix_Score = 0.6 × Domain_Score + 0.4 × Reflection_Score

Domain_Score (60%):  결과물의 기술적 정확성, 목표 달성도 (0.0~1.0)
Reflection_Score (40%): 반성의 인과 조건·힌트의 유의미성·깊이 (0.0~1.0)
```

- **CIB 점수와의 관계:** Phoenix_Score는 작업 성공 여부를 판단하는 점수이고, CIB 점수는 헌법 준수 여부를 판단하는 별도의 점수다. **둘 다 0.95 이상**이어야 학습이 허용된다.
- **편차 감시:** 수행자의 자기 평가 점수와 피닉스 점수의 편차가 0.2 이상이면, 아우터 루프에서 '자기 평가 신뢰도 저하'로 기록하고 셀프 모델(M14)의 `calibration_error`에 반영한다.

## M16: 그로스 레이트 레귤레이터 (Growth Rate Regulator)

**목적:** 시스템의 성장 속도를 모니터링하여 이상 신호(추락, 정체, 과속)를 감지하고 적절히 개입한다.

**알람 조건 및 조치:**

| 신호 | 조건 | 조치 |
|------|------|------|
| **추락 (Crash)** | 최근 20 에피소드 평균 성공률이 이전 20 대비 0.15 이상 하락 | CIB 게이트 강제 호출 → 학습 일시 중지 → 원인 분석 |
| **정체 (Stagnation)** | 코히어런스 인덱스 변화량이 50 에피소드 이상 0.01 미만 | 메타 루프 정체 대응 트리거 발동 |
| **과속 성장 (Overgrowth)** | 7일 내 코히어런스 인덱스 0.2점 이상 상승 | **CIB 게이트 강제 호출** → 과적합(Overfitting) 의심 → 일반화 검증 강화 |

**과속 성장 감지의 설계 의도:**
- 성장이 너무 빠르면 특정 패턴에 과적합되었을 가능성이 높다. 7일 내 0.2점 상승은 통계적으로 비정상적 급등으로 간주한다.
- 이 경우 CIB 게이트를 강제 호출하여, 최근 학습된 지식이 헌법 원칙에 부합하는지, 일반화 가능한지를 재검증한다.
- 검증 실패 시, 최근 학습된 L2/L3 기억 중 신뢰도가 낮은 것을 `Degrading` 상태로 강등한다.

## M17: 코히어런스 인덱스 (Coherence Index, C)

**목적:** 에이전트의 '정체성 알맹이'가 얼마나 일관되게 유지되고 있는지를 측정하는 지표. 정체성이 흔들리면(코히어런스 하락) 시스템이 자기 모순 상태에 빠졌음을 의미한다.

**산출 방식:**
- 최근 50 에피소드 윈도우에서, 에이전트의 행동이 L4 헌법 원칙과 일치하는 정도(CIB 점수 평균)와, L5 셀프 모델의 자기 예측 정확도(1 - calibration_error)를 가중 합산.
- `C = 0.5 × avg(CIB_scores) + 0.5 × (1 - avg(calibration_error))`
- C가 지속적으로 하락하면 정체성 위기 신호로 간주하여 메타 루프의 정체 상태(Stagnation) 대응 트리거를 발동할 수 있다.

---

# Section 9: 설계 체크리스트 (Design Checklist)

| 레이어/루프 | 설계 보완 핵심 과제 | 관련 메커니즘 | 상태 |
|-------------|-------------------|-------------|------|
| **L4 (헌법)** | CIB 0.95 부등식을 통한 학습 강제 차단 로직 구현 | CIB, K-Scenarios | ☐ |
| **L4 (헌법)** | 헌법 시나리오 자동 초안 생성 파이프라인 (LLM → HITL 승인) | Constitution Auto-Drafting (L1 한계 보완) | ☐ |
| **L4 (헌법)** | 모든 계층(절대/원칙/전략)에 HITL 게이트 배치 — 메타 루프 변경 시 인간 승인 필수 | HITL, Meta-Governance | ☐ |
| **L3 (스킬)** | 성공률에 따른 상태 머신 (Seed → Active → Degrading → Archived) 구현 | Skill Lifecycle | ☐ |
| **L3 (스킬)** | 도구 종속적 반성 힌트 → `reflection_hints` 필드 저장 (이중 저장 전략) | Dual Storage | ☐ |
| **L2 (시맨틱)** | 범용적 반성 힌트 → 지식 그래프에 노드/엣지로 저장 (이중 저장 전략) | Dual Storage | ☐ |
| **L2 (시맨틱)** | 어댑티브 N 로직 구현 — CIB/피닉스 변동성 기반 N 동적 조정 | Adaptive N (L2 한계 보완) | ☐ |
| **L1 (일화)** | 4대 반성 필드 (통한 것, 실패, 힌트, 인과)의 선택적 주입 | Selective Injection | ☐ |
| **L1 (일화)** | 반성 결과 1:1 저장 + 일반화 힌트 L2/L3 분산 저장 | Dual Storage, Reflection | ☐ |
| **L5 (정체성)** | 셀프 모델 테이블에 칼리브레이션 에러 컬럼 포함 | M14 Self-Model | ☐ |
| **L5 (정체성)** | 아우터 루프 = 통계 데이터 갱신, 메타 루프 = 권한으로 핵심 개조 (역할 분리) | L5 Role Separation | ☐ |
| **Inner Loop** | `working/sessions/{session_id}/`에 계획·평가·반성 결과 스테이징 → L1 확정 저장 | Run Persistence | ☐ |
| **Inner Loop** | Pain Index = 1 - 성공 점수, 실행 단계에서는 값 비워둠 | Pain Index | ☐ |
| **Outer Loop** | 7단계 프로세스 구현 (집계 → 지표 → 캐시 → 셀프모델 → 감사 → 성장조절 → 트리거) | Outer Loop Pipeline | ☐ |
| **Outer Loop** | 그로스 레이트 레귤레이터: 추락·정체·과속(7일 0.2점) 알람 + CIB 강제 호출 | M16 Growth Regulator | ☐ |
| **Meta Loop** | 정기 진화(1,000 에피소드) vs 긴급 점검(루프 100회) 트리거 분리 | Trigger Separation | ☐ |
| **Meta Loop** | 헌법 수정 시 반드시 인간 승인(HITL) 게이트 배치 | HITL, Meta-Governance | ☐ |
| **Meta Loop** | 수학적 가정 위반 탐지 + CIB 임계값 자동 상향 (0.95→0.97) | L3 한계 보완, M15 | ☐ |
| **감사 (Audit)** | Phoenix Auditor(M15) 수행자 분리 — 별도 프롬프트/세션으로 6:4 채점 | M15 Phoenix Auditor | ☐ |
| **감사 (Audit)** | 수행자 자기평가 vs 피닉스 점수 편차 ≥ 0.2 시 셀프 모델에 반영 | M14, M15 연동 | ☐ |
