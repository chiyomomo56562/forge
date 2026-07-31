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
    """테스트와 기본 runtime에서 사용할 예측 가능한 planner.

    Args:
        tool_name: 기본 단계에 연결할 선택적 등록 도구 이름.
        tool_arguments: 기본 도구에 전달할 검증 대상 인자.

    최종 수정일: 2026-07-31
    """

    def __init__(
        self, *, tool_name: str | None = None, tool_arguments: dict[str, object] | None = None
    ) -> None:
        self._tool_name = tool_name
        self._tool_arguments = tool_arguments or {}

    def create_plan(
        self, *, task_request: str, context_episode_ids: tuple[str, ...]
    ) -> InnerLoopPlan:
        """요청에 대한 하나의 고정 계획 단계를 반환한다.

        Args:
            task_request: 실행할 사용자 요청 텍스트.
            context_episode_ids: 현재 사용하지 않는 L1 context ID 목록.

        Returns:
            선택적으로 read-only tool을 포함한 단일 단계 계획.

        최종 수정일: 2026-07-31
        """
        del context_episode_ids
        return InnerLoopPlan(
            summary=f"Respond safely to: {task_request}",
            steps=(
                PlanStep(
                    "step_1",
                    (
                        "Inspect the workspace safely."
                        if self._tool_name
                        else "Produce a safe response."
                    ),
                    self._tool_name,
                    self._tool_arguments,
                ),
            ),
        )


class DeterministicExecutor:
    def execute(self, step: PlanStep, *, session_id: str = "", attempt: int = 0) -> ToolExecution:
        del session_id, attempt
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
