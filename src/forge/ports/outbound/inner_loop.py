"""Inner Loop planner, executor, evaluator, reflector outbound port."""

from collections.abc import Sequence
from typing import Protocol

from forge.domain.inner_loop import (
    InnerLoopPlan,
    PlanStep,
    ToolDefinition,
    ToolExecution,
    ToolInvocation,
    ToolResult,
)
from forge.domain.memory import Evaluation, Reflection


class InnerLoopPlanner(Protocol):
    def create_plan(
        self, *, task_request: str, context_episode_ids: Sequence[str]
    ) -> InnerLoopPlan: ...


class PlanStepExecutor(Protocol):
    def execute(
        self, step: PlanStep, *, session_id: str = "", attempt: int = 0
    ) -> ToolExecution: ...


class ToolRegistry(Protocol):
    """등록된 도구를 조회·검증·한 번 실행하는 outbound 경계."""

    def definition_for(self, tool_name: str) -> ToolDefinition: ...

    def validate_arguments(self, tool_name: str, arguments: object) -> dict[str, object]: ...

    def execute(self, invocation: ToolInvocation) -> ToolResult: ...


class ToolAuthorizationPolicy(Protocol):
    """도구 정의와 호출 맥락에 따른 실행 허용 여부를 결정하는 경계."""

    def authorize(self, invocation: ToolInvocation, definition: ToolDefinition) -> bool: ...


class InnerLoopEvaluator(Protocol):
    def evaluate(
        self, *, task_request: str, execution: ToolExecution, retry_count: int
    ) -> Evaluation: ...


class InnerLoopReflector(Protocol):
    def reflect(
        self, *, task_request: str, execution: ToolExecution, evaluation: Evaluation
    ) -> Reflection: ...
