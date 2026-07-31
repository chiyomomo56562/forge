"""provider와 무관한 Inner Loop 실행 값.

최종 수정일: 2026-07-31
"""

from dataclasses import dataclass

from forge.domain.memory import ExecutionOutcome


@dataclass(frozen=True)
class PlanStep:
    """한 번 이상 attempt할 수 있는 계획 단계."""

    step_id: str
    summary: str
    tool_name: str | None = None
    retry_allowed: bool = True


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
