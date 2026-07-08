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
│   │   │   ├── sessions/
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
    - **압축된 통찰(Reflection):** 반성(Reflection) 결과 자체는 **L1(일화 기억)**에 저장됩니다. 하지만 여러 에피소드의 반성에서 **반복적으로 등장하여 일반화된 힌트**는 **L2**의 핵심 데이터가 됩니다. 가공되지 않은 긴 로그 대신, 핵심만 요약된 통찰을 저장하여 에이전트가 더 효율적으로 지식을 꺼내 쓸 수 있게 돕습니다.
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
    - **원칙층:** 운영 원칙, 작업 우선순위, 협업 규칙 등을 담은 '법률'에 해당하며 오직 **메타 루프(Meta Loop)**를 통해서만 갱신될 수 있습니다.
    - **전략층:** 맥락 적응이나 단기 목표 같은 '시행령'에 해당하며 **메타 루프(Meta Loop)**를 통해 갱신됩니다. (아우터 루프는 L1·L2·L3을 갱신하고, 메타 루프는 L4 전체를 갱신합니다.)
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
    
- **L5 (정체성 기억, Identity):** 시스템 자기 자신에 대한 모델(셀프 모델)을 담으며, **SQLite와 YAML 액션**이 함께 사용됩니다.

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
4. 결과를 /runs 폴더 하위에 저장한다.

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
3. 결과를 /runs 폴더 하위에 저장한다.
4. 재시도는 실행에만 있는게 아니라 평가에도 있어야겠네..

<aside>
💡

**피닉스 어디터(Phoenix Auditor)의 채점 공식**

그노시스에서는 수행을 담당하는 에이전트와 별도로 **'피닉스 어디터'**라는 독립 감사 기구가 점수를 매깁니다. 이때 성공 여부를 판단하는 점수는 다음과 같은 **6:4 가중치 방식**으로 계산됩니다.

- **도메인 점수 (60%):** 해당 작업(예: 코딩, 번역 등)의 결과물이 기술적으로 얼마나 정확하고 목표를 달성했는지를 평가합니다.
- **성찰 점수 (40%):** 에이전트가 이너 루프의 '반성' 단계에서 작성한 인과 조건과 힌트가 얼마나 유의미하고 깊이 있는지를 평가합니다.
</aside>

## 4. 반성 단계

1. 실행 결과로부터 **성공/실패의 이유, 다음을 위한 힌트, 인과 조건** 등 4가지 핵심 요소를 추출하여 기록합니다.
2. 이너 루프의 한 사이클 내에서 **1:1 관계**가 기본입니다. 매 실행(에피소드)마다 하나의 평가와 그에 따른 하나의 반성이 생성되어 저장됩니다.
3. 반성의 결과는 **L1(일화 기억)**에 저장되지만, 여기서 추출된 일반화된 힌트는 **L2(시맨틱)**나 실행 가능한 스킬의 보조 정보인 **L3(절차적 기억)** 레이어의 DB에 기록되어 다음 계획 수립 시 활용됩니다.

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
7. **메타 루프 트리거:** 아우터 루프가 100번 돌 때마다 시스템의 DNA를 건드리는 **메타 루프**를 호출합니다.

# Meta Loop
**1. 실행 주기 및 트리거**

- **시간 단위:** 보통 **수개월 단위**의 긴 호흡으로 작동합니다.
- **실행 조건:** 약 1,000개의 에피소드가 쌓였을 때 실행되거나, 아우터 루프가 100번 반복될 때마다 트리거됩니다.
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

# Implimentation Plan
outer, meta loop의 구현은 차순위
현재 구현 우선 순위는 memory와 innerloop
