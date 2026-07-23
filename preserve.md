👤 You
만들어진 결과물을 보고 llm client -> 이거 굳이 내가 만들필요 없어보이긴함, 도구 같은 거 하나하나 내가 만드는건 귀찮으니까 다른 오픈소스에서 뜯어오긴 해야할듯. 설치해서 도구만 쓰는건 안되는 거 많을 거 같음

memory -> 5계층 구조인데, 내가 원하는 계층을 지원하는 오픈소스 x. 그런데 내가 직접 다 만들어서 돌리는 건 너무 낭비라. 다른 오픈소스를 불러와서 쓰긴 해야할듯. 그럼 계층별 수정기준이나 이런건 각 오픈소스에서 알아서 할 거 같기는 한대.  승격만 내가 관리하면 되나, 잘모르겠음.

tool -> 다 내가 만드는건 솔직히 낭비, 다른 오픈소스에서 가져오는 게 맞아보임. 설치해서 사용하기 vs 코드 뜯어오기

utils-> 이건 내가 만드는게

inner loop(cognition), outer loop, meta loop->이건 내가 만들어야함 이런 느낌을 받았는데
🤖 Agent
좋은 통찰입니다. 각 컴포넌트별로 "직접 구현 vs 오픈소스 활용" 전략을 정리해 드리겠습니다.

컴포넌트별 도입 전략
1. LLM Client — 오픈소스로 교체 (LiteLLM)
현재 src/agent/llm/client.py는 OpenAI/Ollama API를 직접 래핑한 코드입니다. 이건 LiteLLM으로 완전히 대체하는 것이 맞습니다.
항목
현재 (자체 구현)
LiteLLM 교체 시
지원 프로바이더
OpenAI, Ollama 2개
100+ 프로바이더 (OpenAI, Anthropic, Ollama, Gemini, Azure 등)
재시도/타임아웃
직접 구현
내장 지원
임베딩
직접 래핑
litellm.embedding() 통합
비용 추적
없음
내장 token/cost 추적
코드량
~300줄
래퍼 ~50줄
pip install litellm
교체 방식: 기존 LLMClient 인터페이스를 유지하면서 내부 구현만 LiteLLM 호출로 교체. 기존 코드(prompts.py, response_parser.py)는 그대로 유지.
2. Memory (5계층) — 하이브리드: 백엔드는 오픈소스, 라우팅/승격은 자체
이게 가장 복잡한 결정입니다. 5계층 구조 자체는 이 프레임워크의 고유 설계이므로 오픈소스에 존재하지 않습니다. 하지만 저장/검색 백엔드는 오픈소스로 대체할 수 있습니다.
계층
현재
오픈소스 대체
자체 유지 부분
L1 일화
ChromaDB 직접 래핑
Mem0 또는 Letta
밀도 우선 검색, reflection 우선 필터링
L2 시맨틱
NetworkX 직접 래핑
NetworkX 유지 (이미 오픈소스)
엔티티 추출, 이중 저장 라우팅
L3 절차
SQLite 직접 구현
SQLite 유지 (표준 라이브러리)
스킬 상태 머신, reflection_hints
L4 헌법
YAML 직접 구현
YAML 유지
CIB 게이트, K-Scenario 검증
L5 정체성
SQLite 직접 구현
SQLite 유지
M14 칼리브레이션, 윈도우 통계
핵심 통찰: 사용자 말대로 "계층별 수정 기준은 각 오픈소스에서 알아서 할 것"이 맞습니다. 예를 들어 Mem0을 L1 백엔드로 쓰면, Mem0이 내부적으로 임베딩/검색/중복제거를 처리합니다. 우리가 관리할 것은 승격(consolidation) 라우팅뿐입니다:
Mem0 (L1 백엔드)  ──consolidation.py──→  NetworkX (L2)
         │                                    │
         └──consolidation.py──→  SQLite (L3)
                                   │
                          [자체 구현: 이중 저장 라우팅]
                          general hint → L2 그래프
                          tool_specific hint → L3 reflection_hints
Mem0 vs Letta 비교:
항목
Mem0
Letta (구 MemGPT)
방식
벡터 메모리 + 그래프
에이전트 메모리 블록 (core/archival)
임베딩
자동 처리
자동 처리
검색
의미론적 + 메타데이터
블록 기반 검색
적합성
L1 일화 기억에 적합
L1 + 작업 메모리에 적합
Python SDK
있음
있음
추천: Mem0을 L1 백엔드로 사용. 밀도 우선 검색(reflection 우선)은 Mem0 위에 우리의 필터링 로직을 얹는 방식.
3. Tools — 오픈소스 도구 프레임워크 활용 (Composio 또는 LangChain Tools)
현재 src/agent/tools/builtin/에 file_io, search, code_exec를 직접 구현했습니다. 이건 낭비가 맞습니다.
옵션
장점
단점
Composio
200+ 사전 구축 도구 (GitHub, Slack, Notion, DB 등)
설치형, 무거움
LangChain Tools
가벼움, 커스텀 도구 쉽게 추가
기본 도구만 제공
코드 뜯어오기
완전한 제어권
유지보수 부담
추천: LangChain Tools를 설치해서 사용. 이유:
•  우리의 ToolRegistry 인터페이스를 유지하면서, 내부적으로 LangChain Tool을 래핑
•  헌법 tool_policy.yml의 승인 정책은 자체 유지 (이게 핵심 가치)
•  LangChain Tool은 가벼워서 설치 부담이 적음
pip install langchain-core
교체 방식:
# 기존: 자체 구현 도구
class FileReadTool(Tool):
    def execute(self, args, ctx): ...

# 교체: LangChain Tool 래핑
from langchain_core.tools import Tool as LC_Tool

class FileReadTool(Tool):
    def __init__(self):
        self._lc_tool = LC_Tool(...)  # LangChain 도구 사용
    def execute(self, args, ctx):
        return self._lc_tool.invoke(args)
4. Utils — 자체 구현 유지 (맞습니다)
ID 생성, 시간 유틸, 직렬화, 로깅은 프로젝트 특화 로직이므로 자체 구현이 맞습니다. 코드량도 적고(각 ~100줄) 의존성이 없습니다.
5. Inner/Outer/Meta Loop — 자체 구현 유지 (핵심 차별점)
이게 이 프레임워크의 존재 이유입니다. 오픈소스에 존재하지 않는 고유 설계:
루프
자체 구현 이유
Inner Loop (Cognition)
4단계(계획→실행→평가→반성) + CIB/Phoenix 이중 평가 + Pain Index + 이중 저장
Outer Loop
7단계 건전성 점검 + M16 그로스 레귤레이터 + 어댑티브 N
Meta Loop
헌법 개정 + HITL 게이트 + 수학적 가정 위반 탐지
이 세 루프는 프레임워크의 핵심 지적 자산이므로 자체 구현을 유지해야 합니다.

종합: 교체 vs 유지 매트릭스
┌─────────────────────┬──────────────┬──────────────────────────┐
│ 컴포넌트            │ 전략         │ 교체 방식                │
├─────────────────────┼──────────────┼──────────────────────────┤
│ LLM Client          │ 오픈소스 교체│ LiteLLM (pip install)    │
│ L1 Episodic 백엔드  │ 오픈소스 교체│ Mem0 (pip install)       │
│ L2 Semantic 백엔드  │ 유지        │ NetworkX (이미 오픈소스)  │
│ L3 Procedural       │ 유지         │ SQLite (표준 라이브러리) │
│ L4 Constitution     │ 유지         │ YAML (고유 설계)         │
│ L5 Identity         │ 유지         │ SQLite (고유 설계)       │
│ Memory Manager      │ 유지 (라우팅)│ consolidation, 이중저장  │
│ Tools               │ 오픈소스 교체│ LangChain Tools (pip)    │
│ Tool Registry       │ 유지         │ 헌법 정책 연동 (고유)    │
│ Utils               │ 유지         │ 프로젝트 특화            │
│ Inner Loop          │ 유지         │ 핵심 차별점              │
│ Outer Loop          │ 유지         │ 핵심 차별점              │
│ Meta Loop           │ 유지         │ 핵심 차별점              │
│ CIB Guard           │ 유지         │ 헌법 강제 (고유)         │
│ HITL Gate           │ 유지         │ 안전 장치 (고유)         │
└─────────────────────┴──────────────┴──────────────────────────┘
추천 우선순위
 1.  LiteLLM 교체 (가장 쉽고 효과 큼) — client.py 내부만 교체, 인터페이스 유지
 2.  LangChain Tools 도입 — builtin/ 도구를 LangChain Tool으로 교체, Registry는 유지
 3.  Mem0 도입 (선택적, 복잡도较高) — L1 백엔드만 교체, 검색 필터링 로직은 유지
어떤 것부터 진행할까요?