"""L1 episodic-memory values reconstructed from L0 evidence."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from forge.domain.memory.errors import MemoryValidationError


class ExecutionOutcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    HALTED = "halted"


class EvaluationStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"


class CibEvaluationStatus(StrEnum):
    EVALUATED = "evaluated"
    NOT_EVALUATED = "not_evaluated"


class PromotionEligibility(StrEnum):
    ELIGIBLE = "eligible"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class ExecutionResult:
    summary: str
    outcome: ExecutionOutcome
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationReason:
    metric: str
    reason: str


@dataclass(frozen=True)
class Evaluation:
    success_score: float | None
    cib_score: float | None
    cib_evaluation_status: CibEvaluationStatus
    status: EvaluationStatus
    retryable: bool
    promotion_eligibility: PromotionEligibility
    reasons: tuple[EvaluationReason, ...] = ()


@dataclass(frozen=True)
class Reflection:
    what_worked: str
    what_failed: str
    next_hint: str
    causal_condition: str


@dataclass(frozen=True)
class Episode:
    episode_id: str
    session_id: str
    task_request: str
    task_category: str
    execution: ExecutionResult
    evaluation: Evaluation
    reflection: Reflection
    raw_event_refs: tuple[str, ...]
    evidence_complete: bool
    created_at: datetime
    pattern_candidate_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.episode_id.startswith("ep_"):
            raise MemoryValidationError(
                code="episode.invalid_id", safe_message="Episode ID is invalid."
            )
        if not self.session_id.startswith("ses_"):
            raise MemoryValidationError(
                code="episode.invalid_session_id", safe_message="Session ID is invalid."
            )
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise MemoryValidationError(
                code="episode.invalid_created_at",
                safe_message="Episode timestamp must be UTC-aware.",
            )
        if len(self.raw_event_refs) != len(set(self.raw_event_refs)) or not self.raw_event_refs:
            raise MemoryValidationError(
                code="episode.invalid_evidence", safe_message="Episode evidence is invalid."
            )
