# 2. 실행과 L0 이벤트 기록

## 책임

계획의 검증된 도구 단계를 한 attempt씩 수행하고, 재구성 가능한 순서의 원본 이벤트를 L0에 남긴다.
재시도 예산과 다음 단계 결정은 `InnerLoopService`가 소유하며, `PlanStepExecutor`는 한 attempt만 실행한다.

## 입력과 산출물

| 입력 | 산출물 |
| --- | --- |
| `PlanStep`, Session, Tool Registry, Authorization Policy | `ToolExecution`, 순서화된 L0 `execution.*` 이벤트 |

## 실행 경계

```text
validated PlanStep
  -> RegistryPlanStepExecutor (single attempt)
  -> ToolRegistry.resolve + argument validation
  -> ToolAuthorizationPolicy
  -> registered handler
  -> ToolResult
  -> ToolExecution
  -> InnerLoopService retry 또는 execution.summary.completed
```

- unknown tool과 schema-invalid arguments는 planner output validation에서 거부한다.
- executor의 unknown tool 처리는 손상된 호출에 대한 방어적 `DENIED` 처리다.
- `ToolResult.DENIED`는 도구 호출 결과이고, executor는 이를 `ToolExecution.outcome=halted`로 변환한다.
  `HALTED`는 도구 결과 상태가 아니라 Inner Loop aggregate outcome이다.
- 등록된 mutation 도구는 approval grant가 없으면 handler를 호출하지 않고 `tool.approval_required`로 거부한다.

## L0 기록 계약

| 이벤트 | payload 핵심 필드 |
| --- | --- |
| `execution.started` | step ID, summary, attempt, tool name |
| `execution.completed` | summary, tool names, retryable, audit details, opaque artifact IDs, truncated |
| `execution.failed` | safe error code, retryable, audit details, opaque artifact IDs, truncated |
| `execution.summary.completed` | terminal outcome, ordered-unique tool names, attempts, `is_terminal=true` |

도구의 raw output은 execution 이벤트에 넣지 않는다. L0에는 canonicalized/redacted arguments와
안전한 audit metadata만 남긴다. 큰 output을 보관해야 할 경우 artifact store가 만든 `art_<id>` 형태의
opaque ID만 참조할 수 있으며, 절대 경로나 `file://` URI는 허용하지 않는다.

## 초기 등록 도구와 권한

| 도구 | 위험도 | 기본 동작 |
| --- | --- | --- |
| `workspace.list_files`, `workspace.read_file`, `workspace.search_text` | read-only | workspace root 내부에서만 실행 |
| `git.status`, `git.diff` | read-only | pager·색상·external diff를 비활성화한 고정 argument vector |
| `workspace.apply_patch` | workspace mutation | approval surface가 생길 때까지 항상 거부 |
| `project.verify` | verification | `pytest`·`ruff`·`mypy`의 등록된 template만 shell 없이 실행 |

모든 파일 경로는 workspace root 기준으로 resolve하고 `..`, 절대 경로, root 밖 symlink를 거부한다.
process 도구는 shell 문자열을 실행하지 않으며, timeout과 출력 상한을 적용한다.

## 출력 상한

- `list_files`, `search_text`, `git.diff`는 상한까지 `COMPLETED` 결과를 반환하고 `truncated=true`를 남긴다.
- `read_file`은 요청 offset과 byte limit까지 읽고 필요 시 `truncated=true`를 남긴다.
- process stdout/stderr는 inline 결과만 절단한다. 구조화 결과가 유효하지 않게 되는 도구는 안전 code와 함께 `FAILED`를 반환한다.
- verification command의 non-zero exit는 기본적으로 `retryable=false`다. 코드 변경 뒤 검증은 같은 attempt 재시도가 아니라 새 plan step이다.
