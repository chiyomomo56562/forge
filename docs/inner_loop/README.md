# Inner Loop 단계별 의사 코드

이 폴더는 하나의 사용자 요청을 L1 Episode로 만드는 Inner Loop의 단계별
오케스트레이션을 설명한다. 각 파일은 해당 단계의 입력, 책임, 산출물, 의사
코드를 독립적으로 다룬다.

| 순서 | 단계 | 문서 | 책임 |
| --- | --- | --- | --- |
| 0 | 세션 시작 | [00_session_setup.md](00_session_setup.md) | 식별자·제약·L0 시작 이벤트 생성 |
| 1 | 컨텍스트와 계획 | [01_context_planning.md](01_context_planning.md) | 선택적 기억 주입과 안전한 계획 생성 |
| 2 | 실행 | [02_execution.md](02_execution.md) | 도구 실행과 L0 append-only 기록 |
| 3 | 평가 | [03_evaluation.md](03_evaluation.md) | 성공도·Pain Index·CIB 계산 및 재시도 결정 |
| 4 | 반성 | [04_reflection.md](04_reflection.md) | 네 개 반성 필드 생성 |
| 5 | L1 저장 | [05_episode_persistence.md](05_episode_persistence.md) | ChromaDB 저장과 Pattern Candidate 연결 |
| 설계 | L1 저장 스키마 | [06_l1_storage_schema.md](06_l1_storage_schema.md) | Episode 정본, ChromaDB 레코드, 메타데이터 계약 |

## 단계 간 계약

```text
Session Setup
  → Session + Constraints + L0 session_started
Planning
  → Plan + Injected Context + L0 plan_created
Execution
  → Execution Result + ordered L0 events
Evaluation
  → Evaluation + retry / stop decision
Reflection
  → Reflection (4 fields)
Episode Persistence
  → L1 Episode in ChromaDB + candidate evidence link
```

L1→L2/L3 승격은 이 흐름에 포함되지 않는다. Inner Loop는 증거를 기록하고
Pattern Candidate에 연결할 뿐이며, Outer Loop가 누적 증거를 검증해 승격을
결정한다.
