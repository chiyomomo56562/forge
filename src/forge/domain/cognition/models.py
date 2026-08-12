"""Pure values used to separate cognition from Inner Loop orchestration."""

from dataclasses import dataclass
from enum import StrEnum

from forge.domain.inner_loop import InnerLoopPlan, ToolExecution
from forge.domain.memory import ExecutionOutcome


class CognitionDecision(StrEnum):
    RETRY = "retry"
    REPLAN = "replan"
    SUMMARIZE = "summarize"


@dataclass(frozen=True)
class InnerLoopContext:
    """The current execution facts available to cognition; no memory retrieval."""

    task_request: str
    plan: InnerLoopPlan | None
    step_statuses: dict[str, str]
    attempt: int
    retry_count: int
    feedback_count: int
    max_retries: int
    max_feedback_cycles: int


@dataclass(frozen=True)
class ReasonedExecution:
    """Execution facts normalized for a transition decision."""

    execution: ToolExecution

    @property
    def completed(self) -> bool:
        return self.execution.outcome is ExecutionOutcome.COMPLETED

    @property
    def protocol_failure(self) -> bool:
        return self.execution.safe_error_code == "tool.protocol_failure"
