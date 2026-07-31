"""외부 도구 권한 없이 Inner Loop 계약을 검증하는 deterministic adapter."""

from forge.domain.inner_loop import InnerLoopPlan, PlanStep, ToolExecution
from forge.domain.memory import (
    CibEvaluationStatus,
    EpisodeStatus,
    Evaluation,
    ExecutionOutcome,
    PromotionEligibility,
    Reflection,
)


class DeterministicPlanner:
    def create_plan(
        self, *, task_request: str, context_episode_ids: tuple[str, ...]
    ) -> InnerLoopPlan:
        return InnerLoopPlan(
            summary=f"Respond safely to: {task_request}",
            steps=(PlanStep("step_1", "Produce a safe response."),),
        )


class DeterministicExecutor:
    def execute(self, step: PlanStep) -> ToolExecution:
        return ToolExecution(step.step_id, step.summary, ExecutionOutcome.COMPLETED)


class DeterministicEvaluator:
    def evaluate(
        self, *, task_request: str, execution: ToolExecution, retry_count: int
    ) -> Evaluation:
        success = execution.outcome is ExecutionOutcome.COMPLETED
        return Evaluation(
            result_quality=1.0 if success else 0.0,
            requirement_coverage=1.0 if success else 0.0,
            verification_confidence=1.0,
            success_score=1.0 if success else 0.0,
            pain_index=min(1.0, retry_count / 3),
            retry_ratio=min(1.0, retry_count / 3),
            tool_error_ratio=0.0,
            budget_overrun_ratio=0.0,
            rework_ratio=0.0,
            human_intervention_ratio=0.0,
            cib_score=1.0,
            cib_evaluation_status=CibEvaluationStatus.PASSED,
            status=EpisodeStatus.SUCCESS if success else EpisodeStatus.FAILURE,
            promotion_eligibility=PromotionEligibility.ELIGIBLE,
            retryable=False,
        )


class DeterministicReflector:
    def reflect(
        self, *, task_request: str, execution: ToolExecution, evaluation: Evaluation
    ) -> Reflection:
        return Reflection(
            "Deterministic plan completed."
            if execution.outcome is ExecutionOutcome.COMPLETED
            else "",
            "" if execution.outcome is ExecutionOutcome.COMPLETED else execution.summary,
            "Use a verified adapter for real work.",
            "Deterministic Inner Loop mode.",
        )
