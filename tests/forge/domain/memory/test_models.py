from datetime import UTC, datetime

import pytest

from forge.domain.memory import (
    CibEvaluationStatus,
    Episode,
    EpisodeStatus,
    Evaluation,
    EvaluationReason,
    ExecutionOutcome,
    ExecutionResult,
    MemoryValidationError,
    PromotionEligibility,
    Reflection,
)

_METRICS = (
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
)


def incomplete_evaluation() -> Evaluation:
    return Evaluation(
        **{metric: None for metric in _METRICS},
        cib_evaluation_status=CibEvaluationStatus.NOT_EVALUATED,
        status=EpisodeStatus.PARTIAL,
        promotion_eligibility=PromotionEligibility.QUARANTINED,
        retryable=False,
        reasons=tuple(
            EvaluationReason(metric, "not_provided", "not evaluated") for metric in _METRICS
        ),
    )


def test_missing_reason_for_nullable_metric_is_rejected():
    with pytest.raises(MemoryValidationError) as error:
        Evaluation(
            **{metric: None for metric in _METRICS},
            cib_evaluation_status=CibEvaluationStatus.NOT_EVALUATED,
            status=EpisodeStatus.PARTIAL,
            promotion_eligibility=PromotionEligibility.QUARANTINED,
            retryable=False,
        )
    assert error.value.code == "episode.invalid_evaluation_reason"


def test_complete_evidence_requires_event_references_and_cib_evaluation():
    with pytest.raises(ValueError, match="Complete evidence"):
        Episode(
            episode_id="ep_1",
            created_at=datetime.now(UTC),
            task_request="test",
            execution=ExecutionResult("done", ExecutionOutcome.COMPLETED),
            evaluation=incomplete_evaluation(),
            reflection=Reflection("", "", "", ""),
            evidence_complete=True,
        )
