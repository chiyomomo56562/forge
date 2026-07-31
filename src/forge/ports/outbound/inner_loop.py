"""Inner Loop planner, executor, evaluator, reflector outbound port."""

from collections.abc import Sequence
from typing import Protocol

from forge.domain.inner_loop import InnerLoopPlan, PlanStep, ToolExecution
from forge.domain.memory import Evaluation, Reflection


class InnerLoopPlanner(Protocol):
    def create_plan(
        self, *, task_request: str, context_episode_ids: Sequence[str]
    ) -> InnerLoopPlan: ...


class PlanStepExecutor(Protocol):
    def execute(self, step: PlanStep) -> ToolExecution: ...


class InnerLoopEvaluator(Protocol):
    def evaluate(
        self, *, task_request: str, execution: ToolExecution, retry_count: int
    ) -> Evaluation: ...


class InnerLoopReflector(Protocol):
    def reflect(
        self, *, task_request: str, execution: ToolExecution, evaluation: Evaluation
    ) -> Reflection: ...
