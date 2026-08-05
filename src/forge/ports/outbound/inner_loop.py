"""Inner Loop planner, executor, evaluator, reflector outbound port."""

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from forge.domain.inner_loop import (
    InnerLoopPlan,
    PlanStep,
    ToolDefinition,
    ToolExecution,
    ToolInvocation,
    ToolResult,
    ToolSchema,
)
from forge.domain.memory import Evaluation, Reflection


class InnerLoopPlanner(Protocol):
    def create_plan(
        self, *, task_request: str, context_episode_ids: Sequence[str]
    ) -> InnerLoopPlan: ...


@runtime_checkable
class FeedbackAwareInnerLoopPlanner(Protocol):
    def create_plan_after_feedback(
        self,
        *,
        task_request: str,
        context_episode_ids: Sequence[str],
        last_execution: ToolExecution,
        feedback: Mapping[str, object],
        feedback_count: int,
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

    def tool_schemas(self) -> Sequence[ToolSchema]:
        """LLM tool-calling에 전달할 등록 도구 전체의 JSON Schema 목록."""
        ...


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
