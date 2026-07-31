"""L0 근거로부터 복원하는 불변 L1 episodic-memory 값.

최종 수정일: 2026-07-31
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from forge.domain.memory.errors import MemoryValidationError


class ExecutionOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    HALTED = "halted"


class EpisodeStatus(StrEnum):
    SUCCESS = "Success"
    PARTIAL = "Partial"
    FAILURE = "Failure"


class PromotionEligibility(StrEnum):
    ELIGIBLE = "eligible"
    QUARANTINED = "quarantined"


class CibEvaluationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


class IndexState(StrEnum):
    PENDING = "pending"
    INDEXED = "indexed"
    STALE = "stale"
    FAILED = "failed"


_NULLABLE_METRICS = frozenset(
    {
        "result_quality",
        "requirement_coverage",
        "verification_confidence",
        "success_score",
        "pain_index",
        "retry_ratio",
        "tool_error_ratio",
        "budget_overrun_ratio",
        "rework_ratio",
        "human_intervention_ratio",
        "cib_score",
    }
)


def _require_utc(value: datetime, field_name: str) -> None:
    """datetime 값이 UTC aware인지 검증한다.

    Args:
        value: 검사할 시각.
        field_name: 오류 코드에 쓸 field 이름.

    최종 수정일: 2026-07-31
    """
    if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
        raise MemoryValidationError(
            code=f"episode.invalid_{field_name}", safe_message=f"{field_name} must be UTC."
        )


def _validate_score(value: float | None, field_name: str) -> None:
    """선택 평가 점수가 0~1 범위인지 검증한다.

    Args:
        value: 검사할 점수 또는 평가 미완료를 의미하는 ``None``.
        field_name: 오류 추적을 위한 metric 이름.

    최종 수정일: 2026-07-31
    """
    if value is not None and not 0.0 <= value <= 1.0:
        raise MemoryValidationError(
            code="episode.invalid_score", safe_message="Evaluation score must be between 0 and 1."
        )


@dataclass(frozen=True)
class EvaluationReason:
    metric: str
    reason_code: str
    detail: str

    def __post_init__(self) -> None:
        """값이 없는 metric의 근거를 검증한다.

        Args:
            없음. dataclass field를 직접 검증한다.

        최종 수정일: 2026-07-31
        """
        if self.metric not in _NULLABLE_METRICS:
            raise MemoryValidationError(
                code="episode.invalid_evaluation_metric",
                safe_message="Evaluation metric is invalid.",
            )
        if not self.reason_code.strip() or not self.detail.strip():
            raise MemoryValidationError(
                code="episode.invalid_evaluation_reason",
                safe_message="Evaluation reason is invalid.",
            )


@dataclass(frozen=True)
class ExecutionResult:
    summary: str
    outcome: ExecutionOutcome
    tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """실행 요약이 비어 있지 않은지 검증한다.

        Args:
            없음. dataclass field를 직접 검증한다.

        최종 수정일: 2026-07-31
        """
        if not self.summary.strip():
            raise MemoryValidationError(
                code="episode.invalid_execution", safe_message="Execution summary is required."
            )


@dataclass(frozen=True)
class Evaluation:
    result_quality: float | None
    requirement_coverage: float | None
    verification_confidence: float | None
    success_score: float | None
    pain_index: float | None
    retry_ratio: float | None
    tool_error_ratio: float | None
    budget_overrun_ratio: float | None
    rework_ratio: float | None
    human_intervention_ratio: float | None
    cib_score: float | None
    cib_evaluation_status: CibEvaluationStatus
    status: EpisodeStatus
    promotion_eligibility: PromotionEligibility
    retryable: bool
    reasons: tuple[EvaluationReason, ...] = ()

    def __post_init__(self) -> None:
        """점수, CIB 상태, nullable metric의 값/근거 상호배타성을 검증한다.

        Args:
            없음. dataclass field를 직접 검증한다.

        최종 수정일: 2026-07-31
        """
        for metric in _NULLABLE_METRICS:
            _validate_score(getattr(self, metric), metric)
        if self.cib_evaluation_status is CibEvaluationStatus.NOT_EVALUATED:
            valid_cib = (
                self.cib_score is None
                and self.promotion_eligibility is PromotionEligibility.QUARANTINED
            )
        elif self.cib_evaluation_status is CibEvaluationStatus.PASSED:
            valid_cib = self.cib_score is not None and self.cib_score >= 0.95
        else:
            valid_cib = self.cib_score is not None and self.cib_score < 0.95
        if not valid_cib:
            raise MemoryValidationError(
                code="episode.invalid_cib", safe_message="CIB evaluation is inconsistent."
            )
        reason_metrics = [reason.metric for reason in self.reasons]
        if len(reason_metrics) != len(set(reason_metrics)):
            raise MemoryValidationError(
                code="episode.invalid_evaluation_reason",
                safe_message="Evaluation reasons are invalid.",
            )
        for metric in _NULLABLE_METRICS:
            if (getattr(self, metric) is not None) == (metric in reason_metrics):
                raise MemoryValidationError(
                    code="episode.invalid_evaluation_reason",
                    safe_message="Evaluation reasons are invalid.",
                )


@dataclass(frozen=True)
class Reflection:
    what_worked: str
    what_failed: str
    next_hint: str
    causal_condition: str

    @property
    def has_content(self) -> bool:
        """네 reflection field 중 하나라도 비어 있지 않은지 반환한다.

        Returns:
            의미 있는 reflection이 있으면 ``True``.

        최종 수정일: 2026-07-31
        """
        return any(
            value.strip()
            for value in (self.what_worked, self.what_failed, self.next_hint, self.causal_condition)
        )


@dataclass(frozen=True)
class Episode:
    episode_id: str
    created_at: datetime
    task_request: str
    execution: ExecutionResult
    evaluation: Evaluation
    reflection: Reflection
    evidence_complete: bool
    task_category: str = "general"
    session_id: str | None = None
    request_id: str | None = None
    raw_event_refs: tuple[str, ...] = ()
    pattern_candidate_id: str | None = None
    supersedes_episode_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        """L1 식별자, UTC 시각, 근거 참조 및 outcome/evaluation 정합성을 검증한다.

        Args:
            없음. dataclass field를 직접 검증한다.

        최종 수정일: 2026-07-31
        """
        if not self.episode_id.startswith("ep_"):
            raise MemoryValidationError(
                code="episode.invalid_id", safe_message="Episode ID is invalid."
            )
        if not self.task_request.strip() or not self.task_category.strip():
            raise MemoryValidationError(
                code="episode.invalid_input", safe_message="Episode input is invalid."
            )
        _require_utc(self.created_at, "created_at")
        if self.supersedes_episode_id == self.episode_id:
            raise MemoryValidationError(
                code="episode.invalid_supersession", safe_message="Episode supersession is invalid."
            )
        if len(self.raw_event_refs) != len(set(self.raw_event_refs)):
            raise MemoryValidationError(
                code="episode.invalid_event_refs",
                safe_message="Episode event references are invalid.",
            )
        if self.evidence_complete and (
            not self.raw_event_refs
            or self.evaluation.success_score is None
            or self.evaluation.cib_evaluation_status is CibEvaluationStatus.NOT_EVALUATED
        ):
            raise MemoryValidationError(
                code="episode.invalid_evidence", safe_message="Complete evidence is invalid."
            )
        if (
            self.execution.outcome is ExecutionOutcome.FAILED
            and self.evaluation.status is EpisodeStatus.SUCCESS
        ):
            raise MemoryValidationError(
                code="episode.invalid_evaluation", safe_message="Episode evaluation is invalid."
            )


@dataclass(frozen=True)
class EpisodeSearchFilters:
    task_category: str | None = None
    status: EpisodeStatus | None = None
    promotion_eligibility: PromotionEligibility | None = None
    evidence_complete: bool = True
    created_after: datetime | None = None


@dataclass(frozen=True)
class EpisodeSearchHit:
    episode: Episode
    similarity: float


@dataclass(frozen=True)
class PersistEpisodeResult:
    episode_id: str
    index_state: IndexState


@dataclass(frozen=True)
class ReindexResult:
    indexed_episode_ids: tuple[str, ...]
    failed_episode_ids: tuple[str, ...]
