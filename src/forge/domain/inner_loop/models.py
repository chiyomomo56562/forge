"""provider와 무관한 Inner Loop 실행 및 도구 계약 값.

최종 수정일: 2026-07-31
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from forge.domain.memory import ExecutionOutcome


class ToolRiskTier(StrEnum):
    """도구 호출에 적용할 기본 권한 수준.

    최종 수정일: 2026-07-31
    """

    READ_ONLY = "read_only"
    WORKSPACE_MUTATION = "workspace_mutation"
    VERIFICATION = "verification"


class ToolStatus(StrEnum):
    """단일 도구 호출의 결과 상태.

    `DENIED`는 도구 호출이 정책상 실행되지 않았다는 뜻이며, Inner Loop의
    terminal `halted` outcome과는 별도 개념이다.

    최종 수정일: 2026-07-31
    """

    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


class PlanStepStatus(StrEnum):
    """작업 그래프 안에서 개별 계획 단계의 실행 상태."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ToolDefinition:
    """레지스트리에 노출되는 도구의 안정적인 정책 메타데이터.

    구체적인 입력 validator와 JSON Schema 투영은 adapter registry가 소유한다.

    최종 수정일: 2026-07-31
    """

    name: str
    version: str
    risk_tier: ToolRiskTier
    timeout_seconds: int
    max_output_bytes: int


@dataclass(frozen=True)
class ToolInvocation:
    """검증된 계획 단계를 한 번 실행하기 위한 호출 값.

    최종 수정일: 2026-07-31
    """

    tool_name: str
    arguments: Mapping[str, Any]
    session_id: str
    step_id: str
    attempt: int


@dataclass(frozen=True)
class ToolResult:
    """도구 handler가 executor에 반환하는 단일 attempt 결과.

    `output`은 다음 planner/응답 계층이 사용할 수 있는 도구 결과이며, L0에는
    `audit_details`와 artifact ID만 기록한다.

    최종 수정일: 2026-07-31
    """

    status: ToolStatus
    summary: str
    output: Mapping[str, Any] = field(default_factory=dict)
    audit_details: Mapping[str, Any] = field(default_factory=dict)
    output_artifact_refs: tuple[str, ...] = ()
    retryable: bool = False
    safe_error_code: str | None = None
    duration_ms: int = 0
    truncated: bool = False


@dataclass(frozen=True)
class PlanStep:
    """한 번 이상 attempt할 수 있는 계획 단계."""

    step_id: str
    summary: str
    tool_name: str | None = None
    tool_arguments: Mapping[str, Any] = field(default_factory=dict)
    retry_allowed: bool = True
    depends_on: tuple[str, ...] = ()
    max_attempts: int | None = None


@dataclass(frozen=True)
class InnerLoopPlan:
    """planner가 생성한 실행 가능한 단계 목록."""

    summary: str
    steps: tuple[PlanStep, ...]
    pattern_candidate_id: str | None = None


@dataclass(frozen=True)
class ToolExecution:
    """PlanStepExecutor가 수행한 단일 attempt의 안전한 결과."""

    step_id: str
    summary: str
    outcome: ExecutionOutcome
    tool_names: tuple[str, ...] = ()
    retryable: bool = False
    safe_error_code: str | None = None
    audit_details: Mapping[str, Any] = field(default_factory=dict)
    output: Mapping[str, Any] = field(default_factory=dict)
    output_artifact_refs: tuple[str, ...] = ()
    truncated: bool = False
