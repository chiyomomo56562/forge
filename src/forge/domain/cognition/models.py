"""Pure values used to separate cognition from Inner Loop orchestration."""

from dataclasses import dataclass, field
from enum import StrEnum

from forge.domain.inner_loop import InnerLoopPlan, ToolExecution
from forge.domain.memory import ExecutionOutcome


class CognitionDecision(StrEnum):
    RETRY = "retry"
    REPLAN = "replan"
    SUMMARIZE = "summarize"


@dataclass(frozen=True)
class RetrievedMemoryContext:
    """Vetted, size-bounded memory made available to a planner."""

    episode_ids: tuple[str, ...] = ()
    l2_knowledge: tuple[str, ...] = ()
    l3_skills: tuple[str, ...] = ()
    capability_summary: str | None = None

    def as_payload(self) -> dict[str, object]:
        return {
            "episode_ids": list(self.episode_ids),
            "l2_knowledge": list(self.l2_knowledge),
            "l3_skills": list(self.l3_skills),
            "capability": self.capability_summary,
        }


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
    memory_context: RetrievedMemoryContext = field(default_factory=RetrievedMemoryContext)


@dataclass(frozen=True)
class ReasonedExecution:
    """Execution facts normalized for a transition decision."""

    execution: ToolExecution

    @property
    def completed(self) -> bool:
        return self.execution.outcome is ExecutionOutcome.COMPLETED

    @property
    def protocol_failure(self) -> bool:
        return bool(self.execution.safe_error_code == "tool.protocol_failure")
