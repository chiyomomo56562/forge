# Loop Engine

User Request
    ↓
Localizer                  ← 관련 파일 후보 탐색 (최소 메타데이터), graphify 사용
    ↓
Planner                    ← file_candidates + L0/L1
    ↓
Structured Plan (JSON)
    ↓
Context Loader             ← structured_plan 기반으로 실제 코드 + 관련 context 로드
    ↓
Coder                      ← code_context + structured_plan
    ↓
Test Writer
    ↓
Static Reviewer            ← 빠른 정책/헌법/코딩규칙 체크
    ↓
    ├── Fail → Retry Controller
    └── Pass → Patcher     ← 실제 코드에 Patch를 적용해야 Dynamic Reviewer 사용 가능
                 ↓
            Dynamic Reviewer
                ├── Fail → Patcher 롤백 → Retry Controller
                └── Pass → Memory Writer
    ↓
Retry Controller
    ├── Targeted Retry (Coder 재시도)
    ├── Replan (Planner 재호출)
    └── Human Escalation
    ↓
Memory Writer              ← episode_candidate 저장 (성공/실패 모두)

구현에는 python을 사용하며, llm이 필요한 부분에는 codex sdk를 사용한다.
langGraph, RAG는 MVP단계에선 사용하지 않음.


## Localizer
1. 사용자의 요청을 해석하고 요청과 관련이 있는 파일을 최소 비용으로 추려내야한다.
2. 탐색에는 graphify(LLM Wiki)를 활용한다.
3. 결과물은 file_candidates를 포함해야하며, 해당 결과를 planer가 사용할 수 있게 넘겨야한다.

## Planner
1. Localizer로부터 받은 file_candidates와 L0(Constitution), L1(Project Policy)를 입력으로 받아 전체적인 해결 전략을 수립한다.
2. 작업을 논리적 steps로 분해하고, 각 step별 목표, target_files, 예상 위험(risks), 우선순위를 정의한다.
3. Structured Plan(JSON) 형식으로 출력한다. 이 계획은 Coder가 Patch를 생성하고 Reviewer가 검증하는 데 핵심 지침이 된다.
4. Retry 또는 Replan 요청이 들어올 경우, 이전 Reviewer feedback과 Task Context를 반영하여 plan을 개선하거나 완전히 재구성한다.
5. 불가능하거나 위험도가 높은 요청에 대해서는 명확히 경고(risks)에 포함시킨다.

## Context Loader
1. Planner가 만든 Structured Plan을 기반으로 실제로 필요한 코드와 관련 context를 토큰 예산 내에서 효율적으로 로드한다.
2. target_files에 지정된 파일들의 내용을 불러오되, 전체 파일을 무조건 다 로드하지 않고 필요한 부분(함수, 클래스, 관련 import 등)만 선택적으로 로드한다.
3. Coder가 Patch를 잘 생성할 수 있도록 충분하면서도 간결한 code_context를 구성한다.
4. 이전 iteration의 diff, 리뷰 코멘트, 에러 로그 등 Task Context(L3)도 함께 포함하여 Coder가 이전 시도를 참고할 수 있게 한다.


## Coder
1. Context Loader가 제공한 code_context와 Planner의 Structured Plan을 바탕으로 정확한 Patch를 생성한다.
2. 변경 범위를 최소화하면서도 요청을 완수할 수 있는 코드를 작성한다.
3. Patch에는 변경 이유와 각 수정 사항에 대한 간단한 설명을 포함한다.
4. L0, L1 규칙을 최대한 준수하려 노력하되, 불가능한 경우 그 이유를 명확히 밝힌다.

## Test Writer
1. Coder가 생성한 Patch, goal과 risks를 받아 해당 변경에 대한 테스트 코드(단위 테스트, 통합 테스트)를 작성하거나 기존 테스트를 업데이트한다.
2. 테스트 커버리지를 높이고, 회귀 테스트를 강화한다.
3. 테스트 코드 Patch를 생성한다.

### Patch
1. Coder가 출력하는 변경 명세서이다.
2. Unified diff 형식 또는 structured 형식으로 작성되어, 어떤 파일의 어떤 줄이 추가/수정/삭제되는지 명확히 표현한다.
3. Reviewer가 변경 내용을 빠르고 정확하게 검토할 수 있도록 한다.
4. 실제 파일 수정은 Patcher가 Patch를 적용하는 방식으로 이루어진다.

## Static Reviewer
1. Patch를 받아 L0(Constitution), L1(Project Policy), 코딩 규칙, 보안 원칙 등을 위반하는지 빠르게 검사한다.
2. 테스트를 실행하지 않고 정적 분석만으로 피드백을 생성한다.
3. 위반 사항이 발견되면 즉시 structured_feedback을 반환하여 Dynamic Reviewer 단계로 넘어가지 않도록 한다.
4. Fail인 경우 명확한 rule id, 이유, 개선 제안을 포함한다.

## Dynamic Reviewer
1. Static Reviewer를 통과한 Patch에 대해 실제 테스트를 실행한다.
2. 단위 테스트, 통합 테스트 결과를 바탕으로 Patch의 동작이 올바른지 검증한다.
3. 테스트 실패 시 실패 원인, 관련 코드 위치, 개선 방향을 structured_feedback으로 제공한다.
4. 전체적으로 기능 요구사항을 만족하는지도 평가한다.

## Retry Controller
1. Reviewer로부터 받은 structured_feedback을 분석하여 다음 행동을 결정한다.
2. Targeted Retry, Replan, Human Escalation 중 적절한 전략을 선택한다.
3. Targeted Retry 시 이전 feedback을 Context에 추가하여 Coder가 개선할 수 있도록 유도한다.
4. Retry 횟수가 기준을 초과하면 Planner에게 Replan을 요청하거나 Human Escalation을 수행한다.

## Memory Writer
1. 태스크가 완료되거나 주요 iteration 종료 시, 이번 경험을 L4(Episodic Memory)에 저장할 후보로 만든다.
2. 성공 사례, 실패 사례, 자주 깨지는 패턴, 사용자 선호 등을 추출하여 기록한다.
3. Structured Plan, Patch, Reviewer feedback 등을 종합하여 학습 가능한 형태로 정리한다.
4. L0, L1 위반 사례는 특별히 강조하여 저장한다.


# Memory
Memory Architecture
├─ L0. Constitution / 불변
│  ├─ 금지 행위
│  ├─ 승인 필요 조건
│  └─ 보안 원칙
│
├─ L1. Project Policy / 장기 (기능별로 구분)
│  ├─ 코딩 규칙
│  ├─ 아키텍처 원칙
│  ├─ 리뷰 기준
│  └─ 테스트 정책
│
├─ L2. Domain Knowledge / 중기
│  ├─ 프로젝트 구조
│  ├─ 주요 모듈
│  ├─ API 규칙
│  └─ DB 스키마
│
├─ L3. Task Context / 단기
│  ├─ 현재 요청
│  ├─ 수정 대상 파일
│  ├─ 현재 diff
│  ├─ 에러 로그
│  └─ 리뷰 코멘트
│
└─ L4. Episodic Memory / 경험
   ├─ 과거 성공 사례
   ├─ 과거 실패 사례
   ├─ 자주 깨지는 패턴
   └─ 사용자 선호

L0, L1은 yaml형식으로 저장
L2~L4는 sqlite를 사용해서 저장