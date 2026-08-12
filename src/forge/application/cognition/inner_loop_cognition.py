"""Planning, evaluation, reflection, and transition decisions for the Inner Loop."""

from collections.abc import Mapping

from forge.application.conversation.tool_feedback import serialize_tool_execution
from forge.domain.cognition import CognitionDecision, InnerLoopContext, ReasonedExecution
from forge.domain.inner_loop import InnerLoopPlan, PlanStep, PlanStepStatus, ToolExecution
from forge.domain.memory import Evaluation, ExecutionOutcome, Reflection
from forge.ports.outbound import (
    FeedbackAwareInnerLoopPlanner,
    InnerLoopEvaluator,
    InnerLoopPlanner,
    InnerLoopReflector,
    MemoryAwareInnerLoopPlanner,
)


class InnerLoopCognition:
    """Owns Inner Loop cognitive work without recording events or mutating state."""

    def __init__(
        self,
        planner: InnerLoopPlanner,
        evaluator: InnerLoopEvaluator,
        reflector: InnerLoopReflector,
        *,
        max_tool_feedback_bytes: int = 8_192,
    ) -> None:
        self._planner = planner
        self._evaluator = evaluator
        self._reflector = reflector
        self._max_tool_feedback_bytes = max_tool_feedback_bytes

    def create_plan(
        self, context: InnerLoopContext, last_execution: ToolExecution | None = None
    ) -> InnerLoopPlan:
        if last_execution is not None and isinstance(self._planner, MemoryAwareInnerLoopPlanner):
            return self._planner.create_plan_after_feedback_with_memory(
                task_request=context.task_request,
                context_episode_ids=context.memory_context.episode_ids,
                last_execution=last_execution,
                feedback=self.feedback(last_execution, context),
                feedback_count=context.feedback_count,
                memory_context=context.memory_context.as_payload(),
            )
        if last_execution is not None and isinstance(self._planner, FeedbackAwareInnerLoopPlanner):
            return self._planner.create_plan_after_feedback(
                task_request=context.task_request,
                context_episode_ids=context.memory_context.episode_ids,
                last_execution=last_execution,
                feedback=self.feedback(last_execution, context),
                feedback_count=context.feedback_count,
            )
        if isinstance(self._planner, MemoryAwareInnerLoopPlanner):
            return self._planner.create_plan_with_memory(
                task_request=context.task_request,
                context_episode_ids=context.memory_context.episode_ids,
                memory_context=context.memory_context.as_payload(),
            )
        return self._planner.create_plan(
            task_request=context.task_request,
            context_episode_ids=context.memory_context.episode_ids,
        )

    def decide(
        self, context: InnerLoopContext, step: PlanStep, execution: ToolExecution
    ) -> CognitionDecision:
        reasoned = ReasonedExecution(execution)
        max_attempts = step.max_attempts or context.max_retries + 1
        if (
            not reasoned.completed
            and step.retry_allowed
            and execution.retryable
            and context.attempt + 1 < max_attempts
        ):
            return CognitionDecision.RETRY
        if (
            not reasoned.completed
            and execution.outcome is ExecutionOutcome.FAILED
            and not reasoned.protocol_failure
            and isinstance(self._planner, FeedbackAwareInnerLoopPlanner)
            and context.feedback_count < context.max_feedback_cycles
        ):
            return CognitionDecision.REPLAN
        return CognitionDecision.SUMMARIZE

    def evaluate(self, context: InnerLoopContext, execution: ToolExecution) -> Evaluation:
        return self._evaluator.evaluate(
            task_request=context.task_request, execution=execution, retry_count=context.retry_count
        )

    def reflect(
        self, context: InnerLoopContext, execution: ToolExecution, evaluation: Evaluation
    ) -> Reflection:
        return self._reflector.reflect(
            task_request=context.task_request, execution=execution, evaluation=evaluation
        )

    def feedback(self, execution: ToolExecution, context: InnerLoopContext) -> Mapping[str, object]:
        feedback = dict(
            serialize_tool_execution(execution, max_output_bytes=self._max_tool_feedback_bytes)
        )
        if context.plan is not None:
            feedback["plan"] = {
                "summary": context.plan.summary,
                "steps": [
                    {
                        "step_id": step.step_id,
                        "summary": step.summary,
                        "tool_name": step.tool_name,
                        "depends_on": list(step.depends_on),
                        "status": context.step_statuses.get(step.step_id),
                    }
                    for step in context.plan.steps
                ],
            }
        return feedback

    @staticmethod
    def merge_replan(
        previous: InnerLoopPlan, statuses: Mapping[str, str], replacement: InnerLoopPlan
    ) -> tuple[InnerLoopPlan, dict[str, str]]:
        completed = [
            step
            for step in previous.steps
            if statuses.get(step.step_id) == PlanStepStatus.SUCCEEDED.value
        ]
        completed_ids = {step.step_id for step in completed}
        replacement_steps = tuple(
            step for step in replacement.steps if step.step_id not in completed_ids
        )
        return (
            InnerLoopPlan(
                replacement.summary,
                (*completed, *replacement_steps),
                replacement.pattern_candidate_id,
            ),
            {
                **{step.step_id: PlanStepStatus.SUCCEEDED.value for step in completed},
                **{step.step_id: PlanStepStatus.PENDING.value for step in replacement_steps},
            },
        )
