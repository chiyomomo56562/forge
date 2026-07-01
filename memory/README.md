``` text
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
```

L0, L1은 yaml형식으로 저장
L2~L4는 sqlite를 사용해서 저장