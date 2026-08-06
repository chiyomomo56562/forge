"""LangGraph로 Inner Loop의 L0 producer와 L1 Finalize를 orchestration한다.

최종 수정일: 2026-07-31
"""

from dataclasses import asdict, dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from forge.application.conversation.tool_feedback import serialize_tool_execution
from forge.application.memory import (
    FinalizeEpisodeService,
    RecordInnerLoopEventService,
    StartInnerLoopSessionService,
)
from forge.domain.inner_loop import InnerLoopPlan, PlanStepStatus, ToolExecution
from forge.domain.memory import Evaluation, ExecutionOutcome, L0EventType
from forge.ports.outbound import (
    FeedbackAwareInnerLoopPlanner,
    InnerLoopEvaluator,
    InnerLoopPlanner,
    InnerLoopReflector,
    PlanStepExecutor,
)


class InnerLoopState(TypedDict, total=False):
    """LangGraph 노드 사이에서만 전달되는 Inner Loop 실행 상태."""

    task_request: str
    task_category: str
    session_id: str
    episode_id: str
    plan: InnerLoopPlan
    step_index: int
    step_statuses: dict[str, str]
    attempt: int
    retry_count: int
    feedback_count: int
    tool_names: tuple[str, ...]
    last_execution: ToolExecution
    needs_replan: bool
    execution: ToolExecution
    evaluation: Evaluation


@dataclass(frozen=True)
class InnerLoopResult:
    session_id: str
    episode_id: str
    outcome: ExecutionOutcome


class RunInnerLoopService:
    """StateGraph의 노드·edge로 한 번의 Inner Loop 상태 전이를 수행한다."""

    def __init__(
        self,
        starter: StartInnerLoopSessionService,
        recorder: RecordInnerLoopEventService,
        finalizer: FinalizeEpisodeService,
        planner: InnerLoopPlanner,
        executor: PlanStepExecutor,
        evaluator: InnerLoopEvaluator,
        reflector: InnerLoopReflector,
        *,
        max_retries: int = 3,
        max_feedback_cycles: int = 0,
        max_tool_feedback_bytes: int = 8_192,
    ) -> None:
        self._starter = starter
        self._recorder = recorder
        self._finalizer = finalizer
        self._planner = planner
        self._executor = executor
        self._evaluator = evaluator
        self._reflector = reflector
        self._max_retries = max_retries
        self._max_feedback_cycles = max_feedback_cycles
        self._max_tool_feedback_bytes = max_tool_feedback_bytes
        self._graph = self._build_graph()

    def handle(self, *, task_request: str, task_category: str = "general") -> InnerLoopResult:
        """LangGraph를 실행해 요청 하나를 L1 Episode까지 확정한다.

        Args:
            task_request: 계획·실행·평가할 사용자 작업 요청.
            task_category: L1 Episode에 보존할 작업 분류.

        Returns:
            저장된 session/episode ID와 terminal execution outcome.

        최종 수정일: 2026-07-31
        """
        state = self._graph.invoke({"task_request": task_request, "task_category": task_category})
        return InnerLoopResult(state["session_id"], state["episode_id"], state["execution"].outcome)

    def _build_graph(self) -> Any:
        """명시적 retry conditional edge를 가진 Inner Loop StateGraph를 조립한다."""
        builder = StateGraph(InnerLoopState)
        builder.add_node("start_session", self._start_session)
        builder.add_node("plan", self._plan)
        builder.add_node("execute_attempt", self._execute_attempt)
        builder.add_node("summarize_execution", self._summarize_execution)
        builder.add_node("evaluate", self._evaluate)
        builder.add_node("reflect", self._reflect)
        builder.add_node("finalize", self._finalize)
        builder.add_edge(START, "start_session")
        builder.add_edge("start_session", "plan")
        builder.add_edge("plan", "execute_attempt")
        builder.add_conditional_edges(
            "execute_attempt",
            self._next_after_attempt,
            {
                "attempt": "execute_attempt",
                "re_plan": "plan",
                "summarize": "summarize_execution",
            },
        )
        builder.add_edge("summarize_execution", "evaluate")
        builder.add_edge("evaluate", "reflect")
        builder.add_edge("reflect", "finalize")
        builder.add_edge("finalize", END)
        return builder.compile()

    def _start_session(self, state: InnerLoopState) -> InnerLoopState:
        session = self._starter.handle(
            task_request=state["task_request"], task_category=state["task_category"]
        )
        return {
            "session_id": session.session_id,
            "episode_id": session.episode_id,
            "retry_count": 0,
            "feedback_count": 0,
            "needs_replan": False,
        }

    def _plan(self, state: InnerLoopState) -> InnerLoopState:
        preserved_statuses: dict[str, str] | None = None
        try:
            if state.get("needs_replan") and isinstance(
                self._planner, FeedbackAwareInnerLoopPlanner
            ):
                feedback_count = state["feedback_count"]
                last_execution = state["last_execution"]
                plan = self._planner.create_plan_after_feedback(
                    task_request=state["task_request"],
                    context_episode_ids=(),
                    last_execution=last_execution,
                    feedback=self._planner_feedback(last_execution, state),
                    feedback_count=feedback_count,
                )
                plan, preserved_statuses = _merge_replan(
                    state["plan"], state.get("step_statuses", {}), plan
                )
            else:
                plan = self._planner.create_plan(
                    task_request=state["task_request"], context_episode_ids=()
                )
        except Exception:
            return {
                "plan": InnerLoopPlan("Planning failed before execution.", ()),
                "step_index": 0,
                "needs_replan": False,
            }
        if not _plan_is_valid(plan):
            plan = InnerLoopPlan("Planning failed: invalid step dependencies.", ())
        self._recorder.handle(
            session_id=state["session_id"],
            event_type=L0EventType.PLAN_COMPLETED,
            payload={
                "summary": plan.summary,
                "step_ids": [step.step_id for step in plan.steps],
                "tool_names": [step.tool_name for step in plan.steps if step.tool_name],
                "memory_context_status": "available",
                "memory_context_error_code": None,
                "retrieved_episode_ids": [],
                "pattern_candidate_id": plan.pattern_candidate_id,
                "dependencies": {step.step_id: list(step.depends_on) for step in plan.steps},
            },
        )
        return {
            "plan": plan,
            "step_index": 0,
            "step_statuses": preserved_statuses
            or {step.step_id: PlanStepStatus.PENDING.value for step in plan.steps},
            "attempt": 0,
            "tool_names": (),
            "needs_replan": False,
        }

    def _execute_attempt(self, state: InnerLoopState) -> InnerLoopState:
        plan = state["plan"]
        if not plan.steps:
            return {
                "execution": ToolExecution("planner", plan.summary, ExecutionOutcome.FAILED),
                "needs_replan": False,
            }
        ready_index = _next_ready_step_index(plan, state.get("step_statuses", {}))
        if ready_index is None:
            return {
                "execution": ToolExecution(
                    "planner", "No executable plan steps remain.", ExecutionOutcome.FAILED
                ),
                "needs_replan": False,
            }
        step = plan.steps[ready_index]
        attempt = state.get("attempt", 0)
        statuses = dict(state.get("step_statuses", {}))
        statuses[step.step_id] = PlanStepStatus.RUNNING.value
        self._recorder.handle(
            session_id=state["session_id"],
            event_type=L0EventType.EXECUTION_STARTED,
            payload={
                "step_id": step.step_id,
                "summary": step.summary,
                "attempt": attempt,
                "tool_name": step.tool_name,
                "depends_on": list(step.depends_on),
            },
        )
        result = self._executor.execute(step, session_id=state["session_id"], attempt=attempt)
        self._recorder.handle(
            session_id=state["session_id"],
            event_type=(
                L0EventType.EXECUTION_COMPLETED
                if result.outcome is ExecutionOutcome.COMPLETED
                else L0EventType.EXECUTION_FAILED
            ),
            payload={
                "step_id": step.step_id,
                "summary": result.summary,
                "tool_names": list(result.tool_names),
                "attempt": attempt,
                "retryable": result.retryable,
                "safe_error_code": result.safe_error_code,
                "audit_details": dict(result.audit_details),
                "output_artifact_refs": list(result.output_artifact_refs),
                "truncated": result.truncated,
            },
        )
        tools = tuple(dict.fromkeys((*state.get("tool_names", ()), *result.tool_names)))
        if result.outcome is not ExecutionOutcome.COMPLETED:
            max_attempts = step.max_attempts or self._max_retries + 1
            if step.retry_allowed and result.retryable and attempt + 1 < max_attempts:
                return {
                    "attempt": attempt + 1,
                    "retry_count": state["retry_count"] + 1,
                    "tool_names": tools,
                    "needs_replan": False,
                    "step_statuses": statuses,
                }
            statuses[step.step_id] = PlanStepStatus.FAILED.value
            _mark_blocked_steps(plan, statuses)
            execution = _execution_with_tools(result, tools)
            if self._should_replan(execution, state):
                return {
                    "last_execution": execution,
                    "feedback_count": state["feedback_count"] + 1,
                    "needs_replan": True,
                    "tool_names": tools,
                    "step_statuses": statuses,
                }
            return {
                "execution": execution,
                "needs_replan": False,
                "step_statuses": statuses,
            }
        statuses[step.step_id] = PlanStepStatus.SUCCEEDED.value
        if _next_ready_step_index(plan, statuses) is not None:
            return {
                "attempt": 0,
                "tool_names": tools,
                "needs_replan": False,
                "step_statuses": statuses,
            }
        return {
            "execution": _execution_with_tools(result, tools),
            "needs_replan": False,
            "step_statuses": statuses,
        }

    @staticmethod
    def _next_after_attempt(state: InnerLoopState) -> str:
        if "execution" in state:
            return "summarize"
        if state.get("needs_replan"):
            return "re_plan"
        return "attempt"

    def _should_replan(self, execution: ToolExecution, state: InnerLoopState) -> bool:
        return (
            isinstance(self._planner, FeedbackAwareInnerLoopPlanner)
            and execution.outcome is ExecutionOutcome.FAILED
            and execution.safe_error_code != "tool.protocol_failure"
            and state["feedback_count"] < self._max_feedback_cycles
        )

    def _planner_feedback(
        self, execution: ToolExecution, state: InnerLoopState
    ) -> dict[str, object]:
        feedback = dict(
            serialize_tool_execution(
                execution,
                max_output_bytes=self._max_tool_feedback_bytes,
            )
        )
        feedback["plan"] = {
            "summary": state["plan"].summary,
            "steps": [
                {
                    "step_id": step.step_id,
                    "summary": step.summary,
                    "tool_name": step.tool_name,
                    "depends_on": list(step.depends_on),
                    "status": state.get("step_statuses", {}).get(step.step_id),
                }
                for step in state["plan"].steps
            ],
        }
        return feedback

    def _summarize_execution(self, state: InnerLoopState) -> InnerLoopState:
        execution = state["execution"]
        self._recorder.handle(
            session_id=state["session_id"],
            event_type=L0EventType.EXECUTION_SUMMARY_COMPLETED,
            payload={
                "summary": execution.summary,
                "outcome": execution.outcome.value,
                "tool_names": list(execution.tool_names),
                "attempt": state["retry_count"] + 1,
                "is_terminal": True,
            },
        )
        return {}

    def _evaluate(self, state: InnerLoopState) -> InnerLoopState:
        evaluation = self._evaluator.evaluate(
            task_request=state["task_request"],
            execution=state["execution"],
            retry_count=state["retry_count"],
        )
        self._recorder.handle(
            session_id=state["session_id"],
            event_type=L0EventType.EVALUATION_COMPLETED,
            payload=_evaluation_payload(evaluation, evidence_complete=True),
        )
        return {"evaluation": evaluation}

    def _reflect(self, state: InnerLoopState) -> InnerLoopState:
        reflection = self._reflector.reflect(
            task_request=state["task_request"],
            execution=state["execution"],
            evaluation=state["evaluation"],
        )
        self._recorder.handle(
            session_id=state["session_id"],
            event_type=L0EventType.REFLECTION_COMPLETED,
            payload=asdict(reflection),
        )
        return {}

    def _finalize(self, state: InnerLoopState) -> InnerLoopState:
        self._finalizer.handle(state["session_id"])
        return {}


def _evaluation_payload(evaluation: Evaluation, *, evidence_complete: bool) -> dict[str, object]:
    """L1 Evaluation을 Finalize가 요구하는 JSON-safe L0 payload로 변환한다."""
    payload = asdict(evaluation)
    payload["cib_evaluation_status"] = evaluation.cib_evaluation_status.value
    payload["status"] = evaluation.status.value
    payload["promotion_eligibility"] = evaluation.promotion_eligibility.value
    payload["reasons"] = [asdict(reason) for reason in evaluation.reasons]
    payload["evidence_complete"] = evidence_complete
    return payload


def _plan_is_valid(plan: InnerLoopPlan) -> bool:
    """Reject duplicate, unknown, and cyclic graph dependencies before execution."""
    step_ids = {step.step_id for step in plan.steps}
    if len(step_ids) != len(plan.steps):
        return False
    if any(
        not step.step_id or any(dep not in step_ids for dep in step.depends_on)
        for step in plan.steps
    ):
        return False
    dependencies = {step.step_id: set(step.depends_on) for step in plan.steps}
    resolved: set[str] = set()
    while dependencies:
        ready = {step_id for step_id, deps in dependencies.items() if deps <= resolved}
        if not ready:
            return False
        resolved.update(ready)
        for step_id in ready:
            del dependencies[step_id]
    return True


def _merge_replan(
    previous: InnerLoopPlan, statuses: dict[str, str], replacement: InnerLoopPlan
) -> tuple[InnerLoopPlan, dict[str, str]]:
    """Keep successful work immutable while replacing only unfinished graph work."""
    completed = [
        step
        for step in previous.steps
        if statuses.get(step.step_id) == PlanStepStatus.SUCCEEDED.value
    ]
    completed_ids = {step.step_id for step in completed}
    replacement_steps = tuple(
        step for step in replacement.steps if step.step_id not in completed_ids
    )
    merged = InnerLoopPlan(
        replacement.summary,
        (*completed, *replacement_steps),
        replacement.pattern_candidate_id,
    )
    merged_statuses = {step.step_id: PlanStepStatus.SUCCEEDED.value for step in completed}
    merged_statuses.update(
        {step.step_id: PlanStepStatus.PENDING.value for step in replacement_steps}
    )
    return merged, merged_statuses


def _next_ready_step_index(plan: InnerLoopPlan, statuses: dict[str, str]) -> int | None:
    """Return the first pending step whose dependencies have all succeeded."""
    for index, step in enumerate(plan.steps):
        if statuses.get(step.step_id) != PlanStepStatus.PENDING.value:
            continue
        if all(statuses.get(dep) == PlanStepStatus.SUCCEEDED.value for dep in step.depends_on):
            return index
    return None


def _mark_blocked_steps(plan: InnerLoopPlan, statuses: dict[str, str]) -> None:
    """Propagate failed or blocked dependencies to their pending descendants."""
    changed = True
    while changed:
        changed = False
        for step in plan.steps:
            if statuses.get(step.step_id) != PlanStepStatus.PENDING.value:
                continue
            if any(
                statuses.get(dep) in {PlanStepStatus.FAILED.value, PlanStepStatus.BLOCKED.value}
                for dep in step.depends_on
            ):
                statuses[step.step_id] = PlanStepStatus.BLOCKED.value
                changed = True


def _execution_with_tools(execution: ToolExecution, tools: tuple[str, ...]) -> ToolExecution:
    """Preserve execution detail while carrying accumulated tool names forward."""
    return ToolExecution(
        execution.step_id,
        execution.summary,
        execution.outcome,
        tools,
        retryable=execution.retryable,
        safe_error_code=execution.safe_error_code,
        audit_details=execution.audit_details,
        output=execution.output,
        output_artifact_refs=execution.output_artifact_refs,
        truncated=execution.truncated,
    )
