# Inner Loop 실제 도구 레지스트리 및 실행 계획

> 작성일: 2026-07-31  
> 상태: 계획  
> 범위: Inner Loop가 제한된 실제 개발 작업 도구를 안전하게 선택·실행하고, L0/L1 메모리와 연결하는 M2 기반

## 1. 목표와 완료 기준

현재 Inner Loop는 `PlanStepExecutor` 포트와 실행 이벤트 계약은 갖고 있지만,
`DeterministicExecutor`가 실제 외부 작업을 하지 않는 no-op 구현이다. 이 계획의 목표는
LLM이나 planner가 임의의 쉘을 실행하지 못하도록 하면서, 개발 작업에 필요한 최소 도구를
명시적 레지스트리로 제공하는 것이다.

완료 기준은 다음과 같다.

- planner는 레지스트리에 등록된 도구 이름과 해당 입력 스키마만 사용할 수 있다.
- 각 도구는 workspace 경계, 입력 검증, 실행 시간·출력 크기 제한, 정책 판정을 거친다.
- 읽기, 변경, 검증 실행의 권한이 분리된다.
- 도구 호출 결과는 L0 `execution.*` 근거 이벤트로 남고, L1에는 현재 계약대로 집계된 도구 이름과 실행 요약만 남는다.
- 비밀값, 전체 대용량 출력, 경로 탈출 결과가 L0/L1에 기록되지 않는다.
- 거부·timeout·일시 실패가 실행 결과와 retry 정책에 따라 구별된다.

## 2. 초기 도구 집합

첫 구현은 개발 workspace 내부에서 재현 가능하고 권한 모델이 명확한 도구만 포함한다.

| 단계 | 도구 | 목적 | 기본 권한 | 비고 |
| --- | --- | --- | --- | --- |
| M2a | `workspace.list_files` | 허용된 workspace 아래 파일 탐색 | 허용 | glob 결과 수 제한 |
| M2a | `workspace.read_file` | 텍스트 파일 일부 읽기 | 허용 | 해석된 경로·크기·binary 검사 |
| M2a | `workspace.search_text` | workspace 텍스트 검색 | 허용 | 결과 수·각 snippet 크기 제한 |
| M2a | `git.status` | 변경 상태 확인 | 허용 | 읽기 전용 `git status --short` 고정 호출 |
| M2a | `git.diff` | 변경 내용 확인 | 허용 | 읽기 전용 고정 호출, 출력 상한 |
| M2b | `workspace.apply_patch` | unified patch로 파일 변경 | 명시적 승인 필요 | 임의 파일 overwrite나 shell redirection 금지 |
| M2c | `project.verify` | 허용된 검사 명령 실행 | 정책 기반 승인 | `pytest`, `ruff`, typecheck 등 등록된 template만 실행 |

다음은 초기 범위에서 제외한다.

- 임의 shell 문자열 실행, `rm`, `git reset`, `git commit`, `git push`
- 네트워크/HTTP, 패키지 설치, credential 사용, 데이터베이스 관리 명령
- workspace 밖의 읽기·쓰기와 심볼릭 링크를 통한 경계 우회
- MCP 서버를 도구 실행의 첫 구현으로 직접 노출하는 방식

`project.verify`는 편의상 generic shell이 아니다. 도구 입력은 자유 명령 문자열이 아니라
레지스트리에 선언된 검증 template ID와 허용된 좁은 인자만 받는다.

## 3. 설계 결정

### 3.1 타입 있는 내장 레지스트리를 선택한다

초기 구현은 도구별 입력·출력 타입, 위험도, 기본 timeout, 출력 제한을 가진 내장 레지스트리를 사용한다.
임의 shell 또는 MCP passthrough는 정책 표면이 너무 넓어 M2에서 채택하지 않는다.

```text
Tool-aware planner
  -> PlanStep(tool_name, tool_arguments, retry_allowed)
  -> RegistryPlanStepExecutor
  -> ToolRegistry.resolve()
  -> ToolAuthorizationPolicy.authorize()
  -> registered tool handler (exactly one attempt)
  -> ToolResult
  -> InnerLoopService retry / L0 execution.* / summary / L1 finalize
```

재시도 책임은 기존 결정대로 `InnerLoopService`에 남긴다. 새 executor는 한 번의 도구 attempt만
수행하고, 실패의 `retryable` 여부를 결과로 반환한다.

### 3.2 필요한 도메인·포트 계약

새 계약은 `domain/inner_loop` 및 `ports/outbound`에 둔다.

```python
class ToolDefinition:
    name: str
    version: str
    risk_tier: ToolRiskTier  # READ_ONLY | WORKSPACE_MUTATION | VERIFICATION
    timeout_seconds: int
    max_output_bytes: int

class ToolInvocation:
    tool_name: str
    arguments: Mapping[str, JSONValue]
    session_id: str
    step_id: str
    attempt: int

class ToolResult:
    status: ToolStatus  # COMPLETED | FAILED | DENIED
    summary: str
    output_artifact_refs: tuple[str, ...]
    retryable: bool
    safe_error_code: str | None
    duration_ms: int
    truncated: bool = False

class ToolRegistry(Protocol):
    def resolve(self, tool_name: str) -> RegisteredTool: ...

class ToolAuthorizationPolicy(Protocol):
    def authorize(self, invocation: ToolInvocation, definition: ToolDefinition) -> PolicyDecision: ...
```

`ToolDefinition`은 domain에서 이름·버전·위험도·timeout·출력 상한만 소유한다. 구체 입력 모델과
validation 함수는 registry adapter가 소유한다. 따라서 domain이 특정 schema 라이브러리에 결합되지
않으며, registry가 필요할 때 구체 validator에서 LLM function-calling용 JSON Schema manifest를 투영한다.

`PlanStep`에는 `tool_arguments`를 추가한다. planner 출력은 registry manifest를 기반으로 검증하며,
알 수 없는 도구나 스키마 밖 인자는 **계획 검증 오류**로 거부한다. 정상 경로에서
`RegistryPlanStepExecutor`까지 unknown tool이나 schema-invalid invocation이 전달되면 안 된다.
executor의 unknown-tool 처리는 손상된 호출에 대한 방어적 오류로만 남긴다. planner가 아직 deterministic인 동안에는
테스트 fixture 또는 명시적 plan을 통해 이 경계를 먼저 검증하고, 이후 LLM function-calling adapter를
별도 변경으로 연결한다.

### 3.3 권한과 실패 정책

| 상황 | 실행 | 결과 | Inner Loop 처리 |
| --- | --- | --- | --- |
| read-only 도구, 경계·스키마 통과 | 허용 | `COMPLETED` 또는 도구 실패 | 기존 retry 판단 |
| 등록된 mutation 도구, 승인 없음 | 실행하지 않음 | `DENIED`, `tool.approval_required` | `ToolExecution.outcome=halted`, aggregate `halted` |
| executor에 도달한 unknown tool/invalid invocation | 실행하지 않음 | 방어적 `DENIED`, `tool.invalid_invocation` | retry 불가, halted 집계 |
| 실행 직전 path/workspace 검증 실패 | 실행하지 않음 | `DENIED` 또는 안전한 `FAILED` | failure code별 고정 정책 |
| timeout·일시적 OS lock | 실행 종료 | `FAILED`, retryable | Inner Loop retry budget 적용 |
| verify command의 non-zero exit | 명령은 수행 | `FAILED`, `retryable=False` | 코드 변경 뒤 새 plan step에서만 재검증 |

`DENIED`는 **도구 호출 결과**이고 `HALTED`는 **Inner Loop 실행 결과**다. 따라서
`ToolResult`에 `HALTED`를 추가하지 않는다. 승인 거부와 policy denial은 `ToolResult.DENIED`를 반환하고,
executor 변환 계층이 이를 `ToolExecution.outcome=halted`로, 최종 집계가 `halted`로 기록한다.

출력 상한의 의미도 도구별로 고정한다.

| 도구 종류 | 상한 초과 처리 |
| --- | --- |
| `list_files`, `search_text`, `git.diff` | 결과를 제한까지 반환하고 `COMPLETED`, `truncated=True` |
| `read_file` | 요청 range 및 byte limit까지만 읽고 `COMPLETED`, 필요 시 `truncated=True` |
| process stdout/stderr | 프로세스 결과는 유지하고 inline 요약만 절단한다. 보관 가능한 출력은 artifact로 저장하고 `truncated=True` |
| 중간 절단으로 구조화 결과가 유효하지 않은 도구 | `FAILED`와 안전 error code |

승인 UI/외부 authority가 아직 없으므로 M2b의 기본 정책은 mutation을 항상 거부하는 것이다.
실제 승인 공급자 또는 사용자 요청 기반의 일회성 grant를 추가하는 변경에서만
`workspace.apply_patch`를 실행 가능 상태로 전환한다. 이 결정은 "승인 필요"라는 표기를
승인 없이 자동 허용으로 해석하는 일을 막는다.

## 4. 안전 경계와 결과 보관

### 4.1 workspace 접근

- 모든 파일 인자는 configured workspace root에 대해 resolve한 뒤 root 내부인지 검사한다.
- 심볼릭 링크, `..`, 절대 경로, device/binary 파일, 읽기·출력 한도를 검사한다.
- `apply_patch`는 unified patch parser가 계산한 변경 대상 전체를 사전 검사하고, 허용된 root 내부의 일반 파일만 바꾼다.
- process 도구는 shell을 사용하지 않고 argument vector로 실행하며, cwd도 workspace 내부로 제한한다.
- artifact reference는 handler가 임의 문자열로 만들지 않는다. `ArtifactStore.put(...)`이 반환한
  opaque ID(예: `art_<UUID>`)만 `ToolResult.output_artifact_refs`에 넣고, executor가 이 형식을 다시 검증한다.
  절대 경로와 `file://` URI는 L0 reference로 허용하지 않는다.

### 4.2 출력·민감정보

- 텍스트 결과는 도구별 `max_output_bytes`를 넘기면 절단 여부와 artifact reference만 반환한다.
- 큰 stdout/stderr와 read 결과는 session artifact 저장소에 보관할 수 있지만, L0에는 요약·크기·hash·reference만 기록한다.
- L0의 기존 금지 key 정책(`api_key`, `authorization`, `password`, `secret`, token류)을 실행 전 인자와 실행 후 구조화 결과에 모두 적용한다.
- 원본 command line, 환경변수, 비밀 가능성이 있는 출력은 L1에 넣지 않는다.

`git.status`와 `git.diff`는 읽기 전용이지만 Git 설정 영향도 줄인다. 고정 argument vector에
`-c core.pager=cat`, `-c color.ui=false`, `--no-optional-locks`를 포함하고, external diff driver와
submodule recursion을 사용하지 않는다. user/global config 영향은 가능한 한 고정 옵션으로 덮으며,
timeout과 출력 상한을 모든 Git 호출에 적용한다.

### 4.3 L0/L1 이벤트 매핑

도구 이벤트는 기존 L0 이벤트 타입을 유지한다. 별도 `tool_call_*` 이벤트를 만들지 않는다.

| L0 이벤트 | 필수 도구 메타데이터 |
| --- | --- |
| `execution.started` | tool name/version, step ID, attempt, redacted/canonicalized arguments, policy decision |
| `execution.completed` | status, safe summary, duration, output artifact refs, truncated 여부 |
| `execution.failed` | safe error code/message, retryable, duration, artifact refs |
| `execution.summary.completed` | 전체 outcome, ordered-unique `tool_names`, attempts, terminal reason |

`FinalizeEpisodeService`는 현재처럼 여러 execution 이벤트의 도구 이름을 순서 보존 중복 제거로
집계한다. 따라서 L1 `ExecutionResult.tool_names`는 사용한 도구의 감사 가능한 요약이고,
세부 입출력과 정책 판정은 L0 근거에만 남는다.

## 5. 구현 순서

1. **계약과 설정 추가**
   - `ToolDefinition`, `ToolInvocation`, `ToolResult`, 위험도·상태·정책 결정 타입과 registry/policy port를 추가한다.
   - `PlanStep`에 schema-validated `tool_arguments`를 추가하고, 기존 no-op plan/test fixture를 호환되게 갱신한다.
   - `config/agent.yml`에 workspace root, read/search/output/timeout 상한, registry 허용 목록을 추가한다. 기존 `config/memory.yml`의 procedural 설정은 도구 실행 설정으로 재해석하지 않는다.

2. **M2a 읽기 전용 handler 구현**
   - `workspace.list_files`, `workspace.read_file`, `workspace.search_text`, `git.status`, `git.diff`를 adapter로 구현한다.
   - 공통 path guard, output limiter, structured safe error mapper를 먼저 만들고 모든 handler가 재사용한다.
   - git 도구는 고정 argument vector만 사용한다.

3. **registry executor와 LangGraph 연결**
   - `RegistryPlanStepExecutor`가 resolve → validate → authorize → single attempt 실행을 수행하도록 만든다.
   - `RunInnerLoopService`의 `execute_attempt` LangGraph node가 `ToolResult`를 기존 `ToolExecution`과 L0 payload로 변환한다.
   - retry loop, `execution.summary.completed`, evaluator/reflection/finalize 흐름은 변경하지 않는다.

4. **M2b 변경 도구의 계약을 deny-by-default로 등록**
   - `workspace.apply_patch`의 `ToolDefinition`, 입력 schema, policy decision과 denial event를 먼저 등록한다.
   - approval port가 없는 상태에서는 실제 patch handler를 구현·호출하지 않는다. 이 단계의 목적은 planner와
     authorization 경계, `DENIED → halted` 변환, 감사 이벤트를 실제 filesystem 변경 없이 검증하는 것이다.
   - patch parser와 handler는 일회성 approval grant interface 및 사용자 승인 surface가 준비되는 변경에서 함께 구현한다.

5. **M2c 검증 command template 추가**
   - repository에 맞는 `pytest`, `ruff`, typecheck template를 registry에 명시한다.
   - template별 cwd, timeout, optional target argument 형식을 고정한다. install/network/임의 옵션은 추가하지 않는다.

6. **tool-aware planner와 CLI 선택 경로 추가**
   - planner에는 manifest를 제공해 도구 이름·입력 schema 밖 계획을 만들 수 없게 한다.
   - LLM의 native tool/function calling을 붙일 때도 planner output을 이 계약으로 변환한다.
   - 최초 CLI smoke path는 read-only 도구 하나를 실행하는 deterministic plan으로 제공한다. mutation은 grant 없는 한 denied가 기대 결과다.

7. **문서와 운영 계약 정리**
   - `docs/inner_loop/02_execution.md`의 이전 `tool_call_requested/completed` 서술을 실제 `execution.*` 계약으로 바꾼다.
   - 도구 목록, 위험도, 허용/거부 코드, artifact retention과 L0/L1 데이터 경계를 운영 문서에 추가한다.

## 6. 검증 계획

### 단위 테스트

- registry가 unknown tool, 잘못된 schema, 초과 인자를 거부한다.
- 동일 tool arguments가 canonicalization 뒤에도 안정적인 L0 payload를 만든다.
- path guard가 `..`, absolute path, workspace 밖 symlink, binary/과대 파일을 거부한다.
- list/search/read 결과가 결과 수·byte 상한을 지키며 truncated 상태를 보존한다.
- `apply_patch`가 grant 없이는 filesystem을 변경하지 않는다.
- `ToolResult.DENIED`가 `ToolExecution.outcome=halted`와 halted aggregate로 변환된다.
- handler 또는 손상된 adapter가 반환한 절대 경로·`file://` URI·비정상 artifact reference를 executor가 거부한다.
- process runner가 shell parsing을 하지 않고, 고정 template 밖 명령과 network 관련 option을 허용하지 않는다.
- timeout, non-zero exit, 출력 초과가 안전한 `ToolResult`와 retryability로 매핑된다.
- redactor가 인자와 결과에서 금지된 민감 key를 L0 payload로 보내지 않는다.

### 통합 테스트

- LangGraph Inner Loop가 read-only tool을 한 attempt 실행하고 `execution.started` → `execution.completed` → summary → L1 finalize를 만든다.
- retryable 도구 실패는 retry budget 안에서 재시도하고, non-retryable denial은 halted summary가 된다.
- 실패 Episode가 evaluation/reflection/finalize까지 정상 수행되면 `session.completed`가 기록된다.
- evaluator/reflector 실패는 L1을 만들지 않고 `session.failed`를 기록한다.
- L1에는 tool name 요약만 있고 raw output·비밀값·workspace 밖 경로가 없음을 확인한다.

### 수동 smoke

임시 workspace에서 `list_files`, `read_file`, `search_text`, `git.status`를 실행해 L0 JSONL 및 L1 조회 결과를 확인한다.
그 다음 승인 없는 `apply_patch`가 파일 diff를 만들지 않는지 확인한다.

## 7. 변경 예상 파일

| 영역 | 예상 파일 |
| --- | --- |
| 도메인/포트 | `src/forge/domain/inner_loop/models.py`, `src/forge/ports/outbound/inner_loop.py` |
| 도구 adapter | `src/forge/adapters/outbound/tools/` (신규), `src/forge/adapters/outbound/inner_loop/` |
| orchestration | `src/forge/application/inner_loop/run_inner_loop.py` |
| bootstrap/config | `src/forge/bootstrap.py`, `config/agent.yml` |
| 문서 | `docs/inner_loop/02_execution.md`, 도구 운영 문서(신규) |
| tests | `tests/domain/inner_loop/`, `tests/adapters/outbound/tools/`, `tests/application/inner_loop/` |

## 8. 남은 결정과 이후 단계

- mutation approval의 실제 공급자: CLI one-shot grant, UI approval, 혹은 외부 policy service 중 하나를 별도 설계한다.
- artifact의 보존 기간·암호화·삭제 정책은 session store 정책과 함께 정한다.
- 네트워크 도구와 MCP는 별도 capability/egress 정책, host allowlist, credential vault가 준비된 뒤에만 검토한다.
- LLM provider별 native tool calling은 registry contract가 안정된 뒤 adapter 범위로 추가한다. 도구 실행 안전성은 provider가 아니라 executor/policy 계층이 보장한다.
