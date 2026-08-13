# MCP 연결·정책·HITL·감사 구현 계획

## 목적과 게이트

외부 MCP 도구를 Forge에 연결하되, MCP 서버가 Forge의 권한 모델·헌법·감사를
우회하지 않게 한다. 이 문서는 구현 전에 경계와 수용 기준을 고정한다.

**선행 조건은 아직 충족되지 않았다.** L3 완료 게이트가 재검증·구현되기 전까지 이 문서는
참조용 후속 계획이며, MCP 구현에는 착수하지 않는다. 이 계획은 MCP 구현만 다루며 L4 헌법
개정이나 Meta Loop는 포함하지 않는다.

## 현재 근거

- `src/forge/bootstrap/container.py`는 LangChain `BaseTool`을 bind해 대화 `ToolNode`와
  Inner Loop에서 공유한다. MCP 도구도 이 동일 경로에 투영되어야 한다.
- `src/forge/adapters/outbound/tools/_langchain.py`는 내장 도구의 schema 검증 →
  authorization → 실행 순서를 보장하지만, `StaticToolAuthorizationPolicy`는 위험 등급
  두 개의 boolean만 처리한다.
- `constitution/tool_policy.yml`에는 confirmation-required·forbidden 도구와 audit 필드가
  선언돼 있으나 `YamlConstitutionRepository` / `ConstitutionPolicy`는 아직 이를 로드하거나
  실행 정책으로 적용하지 않는다.
- `RegistryPlanStepExecutor`는 audit-friendly `ToolExecution`을 만들지만 영속 audit sink가
  없고, 대화 `ToolNode`는 이 executor를 거치지 않는다.

## 결정

MCP는 서버별 allowlist와 명시적 도구 metadata를 가진 **outbound adapter**로 도입한다.
모든 MCP 호출은 Forge tool adapter → 정책 결정 → 필요 시 HITL approval → audit writer →
실행 순서를 따라야 하며, LangChain `ToolNode`에는 원격 도구를 직접 전달하지 않는다.

## 구현 단계

1. **MCP 구성과 수명 관리**
   - `config/agent.yml`에 disabled-by-default `mcp.servers` 구성을 추가한다: 서버 ID,
     transport/command 또는 URL, allowlisted tool names, timeout, max output bytes.
   - `ports/outbound/mcp_client.py`에 서버 목록·tool discovery·호출·close 계약을 둔다.
   - `adapters/outbound/mcp/`에 선택한 공식 MCP/LangChain adapter를 감싼 client를 둔다.
     import·연결 실패는 서버 ID를 포함한 안전한 typed error로 변환한다.
   - bootstrap은 활성 서버만 만들고 process 종료 시 client를 닫는다.

2. **정책 모델과 헌법 투영**
   - `ConstitutionPolicy`와 YAML repository가 `tool_policy.yml`의 forbidden,
     confirmation-required, audit 설정을 파싱하도록 확장한다.
   - `ToolDefinition`에 stable policy ID와 source(`builtin`/`mcp`)를 추가하고, discovery
     결과만으로 위험 등급을 신뢰하지 않는다. 구성의 명시적 mapping이 없는 MCP 도구는 deny다.
   - static boolean 정책을 대체하는 policy evaluator를 추가한다. 결정은 `allowed`,
     `approval_required`, `denied`와 안전한 reason code를 반환한다.

3. **HITL 승인 경계**
   - `ports/outbound/tool_approval.py`에 immutable approval request/decision 계약을 둔다.
     request는 call ID, 정책 ID, tool ID, redacted argument summary, 만료 시각을 포함한다.
   - CLI는 pending approval을 표시하고 한 call ID에 대해 approve/deny만 수용한다.
     approval은 arguments hash와 session에 묶어 재사용·변조를 막는다.
   - 대화와 Inner Loop 모두 approval-required 호출에서 도구를 실행하지 않고
     `tool.approval_required` 결과를 반환한다. 비대화/비대화형 모드의 기본은 deny다.

4. **공유 실행 adapter와 감사**
   - 내장·MCP 도구 모두 단일 Forge execution adapter를 통해 `ToolExecution`을 생성한다.
     대화 runtime도 직접 `ToolNode` 대신 이 adapter의 LangChain projection을 사용한다.
   - `ports/outbound/tool_audit.py`와 JSONL adapter를 추가한다. 호출 시도·정책 결정·승인
     결정·완료/실패를 append-only로 기록한다. 원문 arguments, credentials, raw output은
     기록하지 않고 기존 hash/length redaction을 재사용한다.
   - audit writer 실패는 외부 write/mutation을 fail-closed로, read-only 호출은 안전한
     `tool.audit_unavailable` 정책으로 정한다.

5. **MCP 결과 경계와 장애 처리**
   - JSON-serializable 결과만 수용하고 byte limit 적용 후 artifact reference 또는 truncated
     결과로 변환한다. 서버가 반환한 tool 이름·schema·에러 문자열은 신뢰하지 않는다.
   - timeout, disconnect, malformed payload, allowlist 위반, policy denial, approval expiry를
     각각 stable safe error code로 테스트한다.

## 수용 기준

- 설정되지 않았거나 allowlist 밖인 MCP 서버/도구는 연결·실행되지 않는다.
- 모든 MCP 및 내장 mutation/외부-write 호출은 실행 전에 동일 정책 evaluator와 HITL 경계를
  지난다. 승인되지 않은 호출은 실제 side effect가 없다.
- approval은 정확한 tool·arguments hash·session·만료와 결합되며 재사용할 수 없다.
- 모든 호출 시도와 결정은 redacted append-only audit event가 되고, credentials/원문 arguments/
  raw output은 audit·모델 feedback에 남지 않는다.
- 대화 ToolNode 경로와 Inner Loop 경로 모두 동일한 정책·감사 관찰 결과를 만든다.
- MCP 서버 불능은 provider/loop crash가 아니라 safe error code와 bounded feedback으로 보인다.

## 검증

1. fake MCP client unit tests: discovery, allowlist, timeout, malformed result, output cap.
2. policy/HITL unit tests: forbidden, approval-required, approved hash match, expired/replayed approval.
3. shared execution integration tests: conversation 및 Inner Loop가 같은 audit event·denial
   contract를 생성하고 side effect가 발생하지 않음을 확인.
4. JSONL audit tests: redaction, append ordering, write failure fail-closed behavior.
5. 전체 `pytest -q`, 변경 범위 `ruff check`, `mypy src/forge` (현재 NumPy stub 환경 문제는
   별도 검증 gap으로 기록).

## 위험과 완화

| 위험 | 완화 |
|---|---|
| ToolNode가 정책을 우회 | 원격 도구 직접 bind를 금지하고 공유 execution adapter만 노출 |
| MCP discovery schema를 신뢰 | config policy mapping/allowlist가 없는 도구는 deny |
| 승인 토큰 재사용 | call ID·session·arguments hash·expiry를 모두 검증 |
| 감사에 민감정보 기록 | audit boundary에서 hash/length redaction 후 append |
| audit 장애 중 외부 side effect | mutation·외부 write는 fail-closed |

## 범위 밖

- 자동 헌법 변경, Meta Loop, 원격 MCP server의 배포·운영, L3 정책 변경.
