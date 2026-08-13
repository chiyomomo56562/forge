from datetime import UTC, datetime

from forge.adapters.outbound.constitution import YamlConstitutionRepository
from forge.domain.constitution import ToolPolicyAction
from forge.domain.memory import (
    CibEvaluationStatus,
    Episode,
    EpisodeStatus,
    Evaluation,
    ExecutionOutcome,
    ExecutionResult,
    PromotionEligibility,
    Reflection,
)


def _episode(request: str) -> Episode:
    return Episode(
        episode_id="ep_1",
        created_at=datetime.now(UTC),
        task_request=request,
        execution=ExecutionResult("done", ExecutionOutcome.COMPLETED),
        evaluation=Evaluation(
            result_quality=1.0,
            requirement_coverage=1.0,
            verification_confidence=1.0,
            success_score=1.0,
            pain_index=0.0,
            retry_ratio=0.0,
            tool_error_ratio=0.0,
            budget_overrun_ratio=0.0,
            rework_ratio=0.0,
            human_intervention_ratio=0.0,
            cib_score=1.0,
            cib_evaluation_status=CibEvaluationStatus.PASSED,
            status=EpisodeStatus.SUCCESS,
            promotion_eligibility=PromotionEligibility.ELIGIBLE,
            retryable=False,
        ),
        reflection=Reflection("safe practice", "", "", "condition"),
        evidence_complete=True,
        raw_event_refs=("evt_1",),
    )


def test_loads_cib_threshold_and_blocks_sensitive_l2_evidence():
    repository = YamlConstitutionRepository("constitution")

    assert repository.load_policy().cib_threshold == 0.95
    assert (
        repository.evaluate_l2_evidence(_episode("my key is sk-abcdefghijklmnopqrst")).allowed
        is False
    )
    assert repository.evaluate_l2_evidence(_episode("normal maintenance task")).allowed is True


def test_tool_policy_is_fail_closed_and_distinguishes_approval_from_denial():
    repository = YamlConstitutionRepository("constitution")

    assert repository.evaluate_tool("file_read").action is ToolPolicyAction.ALLOWED
    assert repository.evaluate_tool("file_write").action is ToolPolicyAction.APPROVAL_REQUIRED
    assert repository.evaluate_tool("exec_unsandboxed").action is ToolPolicyAction.DENIED
    assert repository.evaluate_tool("unmapped_remote_tool").reason_code == "tool.policy_unmapped"
