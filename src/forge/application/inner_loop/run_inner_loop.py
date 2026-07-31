"""LangGraph로 Inner Loop의 L0 producer와 L1 Finalize를 orchestration한다.

최종 수정일: 2026-07-31
"""

from dataclasses import asdict, dataclass
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from forge.application.memory import (
    FinalizeEpisodeService,
    RecordInnerLoopEventService,
    StartInnerLoopSessionService,
)
from forge.domain.inner_loop import InnerLoopPlan, ToolExecution
from forge.domain.memory import Evaluation, ExecutionOutcome, L0EventType
from forge.ports.outbound import (
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
    attempt: int
    retry_count: int
    tool_names: tuple[str, ...]
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
    ) -> None:
        self._starter = starter
        self._recorder = recorder
        self._finalizer = finalizer
        self._planner = planner
        self._executor = executor
        self._evaluator = evaluator
        self._reflector = reflector
        self._max_retries = max_retries
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

    def _build_graph(self):
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
            {"attempt": "execute_attempt", "summarize": "summarize_execution"},
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
        }

    def _plan(self, state: InnerLoopState) -> InnerLoopState:
        try:
            plan = self._planner.create_plan(
                task_request=state["task_request"], context_episode_ids=()
            )
        except Exception:
            return {"plan": InnerLoopPlan("Planning failed before execution.", ()), "step_index": 0}
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
            },
        )
        return {"plan": plan, "step_index": 0, "attempt": 0, "tool_names": ()}

    def _execute_attempt(self, state: InnerLoopState) -> InnerLoopState:
        plan = state["plan"]
        if not plan.steps:
            return {"execution": ToolExecution("planner", plan.summary, ExecutionOutcome.FAILED)}
        step = plan.steps[state["step_index"]]
        attempt = state.get("attempt", 0)
        self._recorder.handle(
            session_id=state["session_id"],
            event_type=L0EventType.EXECUTION_STARTED,
            payload={
                "step_id": step.step_id,
                "summary": step.summary,
                "attempt": attempt,
                "tool_name": step.tool_name,
            },
        )
        result = self._executor.execute(step)
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
            },
        )
        tools = tuple(dict.fromkeys((*state.get("tool_names", ()), *result.tool_names)))
        if result.outcome is not ExecutionOutcome.COMPLETED:
            if step.retry_allowed and result.retryable and state["retry_count"] < self._max_retries:
                return {
                    "attempt": attempt + 1,
                    "retry_count": state["retry_count"] + 1,
                    "tool_names": tools,
                }
            return {"execution": ToolExecution(step.step_id, result.summary, result.outcome, tools)}
        if state["step_index"] + 1 < len(plan.steps):
            return {"step_index": state["step_index"] + 1, "attempt": 0, "tool_names": tools}
        return {"execution": ToolExecution(step.step_id, result.summary, result.outcome, tools)}

    @staticmethod
    def _next_after_attempt(state: InnerLoopState) -> str:
        return "summarize" if "execution" in state else "attempt"

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
