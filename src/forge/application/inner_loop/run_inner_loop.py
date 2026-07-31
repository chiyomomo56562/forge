"""L0 producer와 L1 Finalize를 연결하는 deterministic Inner Loop orchestrator.

최종 수정일: 2026-07-31
"""

from dataclasses import asdict, dataclass

from forge.application.memory import (
    FinalizeEpisodeService,
    RecordInnerLoopEventService,
    StartInnerLoopSessionService,
)
from forge.domain.inner_loop import InnerLoopPlan, ToolExecution
from forge.domain.memory import (
    Evaluation,
    ExecutionOutcome,
    L0EventType,
    MemoryOperationError,
)
from forge.ports.outbound import (
    InnerLoopEvaluator,
    InnerLoopPlanner,
    InnerLoopReflector,
    PlanStepExecutor,
)


@dataclass(frozen=True)
class InnerLoopResult:
    session_id: str
    episode_id: str
    outcome: ExecutionOutcome


class RunInnerLoopService:
    """세션 시작부터 L1 확정까지 Inner Loop 한 회를 orchestration한다."""

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

    def handle(self, *, task_request: str, task_category: str = "general") -> InnerLoopResult:
        """요청 하나를 계획·실행·평가·반성하고 L1 Episode로 확정한다.

        Args:
            task_request: 실행할 사용자 작업의 안전한 원문/요약.
            task_category: L1 검색과 필터링에 쓸 작업 분류.

        Returns:
            저장 완료된 session/episode ID와 최종 실행 outcome.

        최종 수정일: 2026-07-31
        """
        session = self._starter.handle(task_request=task_request, task_category=task_category)
        try:
            plan = self._create_plan(session.session_id, task_request)
            execution, retry_count = self._execute_plan(session.session_id, plan)
            self._record_summary(session.session_id, execution, retry_count)
            evaluation = self._evaluator.evaluate(
                task_request=task_request, execution=execution, retry_count=retry_count
            )
            self._recorder.handle(
                session_id=session.session_id,
                event_type=L0EventType.EVALUATION_COMPLETED,
                payload=_evaluation_payload(evaluation, evidence_complete=True),
            )
            reflection = self._reflector.reflect(
                task_request=task_request, execution=execution, evaluation=evaluation
            )
            self._recorder.handle(
                session_id=session.session_id,
                event_type=L0EventType.REFLECTION_COMPLETED,
                payload=asdict(reflection),
            )
            self._finalizer.handle(session.session_id)
            return InnerLoopResult(session.session_id, session.episode_id, execution.outcome)
        except Exception as exc:
            if not isinstance(exc, MemoryOperationError):
                raise
            self._recorder.handle(
                session_id=session.session_id,
                event_type=L0EventType.SESSION_FAILED,
                payload={"code": exc.code, "safe_message": exc.safe_message},
            )
            raise

    def _create_plan(self, session_id: str, task_request: str) -> InnerLoopPlan:
        try:
            plan = self._planner.create_plan(task_request=task_request, context_episode_ids=())
        except MemoryOperationError:
            raise
        except Exception:
            return InnerLoopPlan(summary="Planning failed before execution.", steps=())
        self._recorder.handle(
            session_id=session_id,
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
        return plan

    def _execute_plan(self, session_id: str, plan: InnerLoopPlan) -> tuple[ToolExecution, int]:
        if not plan.steps:
            return ToolExecution("planner", plan.summary, ExecutionOutcome.FAILED), 0
        tools: list[str] = []
        last: ToolExecution | None = None
        retries = 0
        for step in plan.steps:
            attempt = 0
            while True:
                self._recorder.handle(
                    session_id=session_id,
                    event_type=L0EventType.EXECUTION_STARTED,
                    payload={
                        "step_id": step.step_id,
                        "summary": step.summary,
                        "attempt": attempt,
                        "tool_name": step.tool_name,
                    },
                )
                result = self._executor.execute(step)
                event_type = (
                    L0EventType.EXECUTION_COMPLETED
                    if result.outcome is ExecutionOutcome.COMPLETED
                    else L0EventType.EXECUTION_FAILED
                )
                self._recorder.handle(
                    session_id=session_id,
                    event_type=event_type,
                    payload={
                        "step_id": step.step_id,
                        "summary": result.summary,
                        "tool_names": list(result.tool_names),
                        "attempt": attempt,
                        "retryable": result.retryable,
                        "safe_error_code": result.safe_error_code,
                    },
                )
                for tool in result.tool_names:
                    if tool not in tools:
                        tools.append(tool)
                last = result
                if result.outcome is ExecutionOutcome.COMPLETED:
                    break
                if not (step.retry_allowed and result.retryable and retries < self._max_retries):
                    return ToolExecution(
                        step.step_id, result.summary, result.outcome, tuple(tools)
                    ), retries
                retries += 1
                attempt += 1
            if last.outcome is not ExecutionOutcome.COMPLETED:
                break
        assert last is not None
        return ToolExecution(last.step_id, last.summary, last.outcome, tuple(tools)), retries

    def _record_summary(self, session_id: str, execution: ToolExecution, retries: int) -> None:
        self._recorder.handle(
            session_id=session_id,
            event_type=L0EventType.EXECUTION_SUMMARY_COMPLETED,
            payload={
                "summary": execution.summary,
                "outcome": execution.outcome.value,
                "tool_names": list(execution.tool_names),
                "attempt": retries + 1,
                "is_terminal": True,
            },
        )


def _evaluation_payload(evaluation: Evaluation, *, evidence_complete: bool) -> dict[str, object]:
    """L1 Evaluation을 Finalize가 요구하는 JSON-safe L0 payload로 변환한다."""
    payload = asdict(evaluation)
    payload["cib_evaluation_status"] = evaluation.cib_evaluation_status.value
    payload["status"] = evaluation.status.value
    payload["promotion_eligibility"] = evaluation.promotion_eligibility.value
    payload["reasons"] = [asdict(reason) for reason in evaluation.reasons]
    payload["evidence_complete"] = evidence_complete
    return payload
