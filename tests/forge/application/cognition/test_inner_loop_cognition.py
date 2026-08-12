from forge.application.cognition import InnerLoopCognition
from forge.domain.cognition import CognitionDecision, InnerLoopContext, RetrievedMemoryContext
from forge.domain.inner_loop import InnerLoopPlan, PlanStep, ToolExecution
from forge.domain.memory import ExecutionOutcome


class _Planner:
    def create_plan(self, *, task_request, context_episode_ids):
        del task_request, context_episode_ids
        return InnerLoopPlan("plan", (PlanStep("step", "do work"),))


class _FeedbackPlanner(_Planner):
    def create_plan_after_feedback(
        self, *, task_request, context_episode_ids, last_execution, feedback, feedback_count
    ):
        del task_request, context_episode_ids, last_execution, feedback, feedback_count
        return InnerLoopPlan("replacement", (PlanStep("replacement", "retry safely"),))


class _MemoryPlanner(_Planner):
    def create_plan_with_memory(self, *, task_request, context_episode_ids, memory_context):
        self.received = (task_request, context_episode_ids, memory_context)
        return InnerLoopPlan("memory plan", (PlanStep("step", "do work"),))

    def create_plan_after_feedback_with_memory(self, **_kwargs):
        raise AssertionError("not needed")


class _Evaluator:
    def evaluate(self, **_kwargs):
        raise AssertionError("not needed for decision tests")


class _Reflector:
    def reflect(self, **_kwargs):
        raise AssertionError("not needed for decision tests")


def _context(**overrides):
    values = {
        "task_request": "task",
        "plan": InnerLoopPlan("plan", (PlanStep("step", "do work"),)),
        "step_statuses": {"step": "pending"},
        "attempt": 0,
        "retry_count": 0,
        "feedback_count": 0,
        "max_retries": 3,
        "max_feedback_cycles": 1,
    }
    values.update(overrides)
    return InnerLoopContext(**values)


def test_decision_retries_retryable_step_with_remaining_attempts():
    cognition = InnerLoopCognition(_Planner(), _Evaluator(), _Reflector())

    decision = cognition.decide(
        _context(),
        PlanStep("step", "do work"),
        ToolExecution("step", "failed", ExecutionOutcome.FAILED, retryable=True),
    )

    assert decision is CognitionDecision.RETRY


def test_decision_replans_only_for_feedback_capable_planner():
    cognition = InnerLoopCognition(_FeedbackPlanner(), _Evaluator(), _Reflector())

    decision = cognition.decide(
        _context(),
        PlanStep("step", "do work"),
        ToolExecution("step", "failed", ExecutionOutcome.FAILED),
    )

    assert decision is CognitionDecision.REPLAN


def test_protocol_failure_never_replans():
    cognition = InnerLoopCognition(_FeedbackPlanner(), _Evaluator(), _Reflector())

    decision = cognition.decide(
        _context(),
        PlanStep("step", "do work"),
        ToolExecution(
            "step", "failed", ExecutionOutcome.FAILED, safe_error_code="tool.protocol_failure"
        ),
    )

    assert decision is CognitionDecision.SUMMARIZE


def test_plan_passes_vetted_memory_to_memory_aware_planner():
    planner = _MemoryPlanner()
    context = _context(
        memory_context=RetrievedMemoryContext(("ep_1",), ("condition: practice",), "coding")
    )

    assert (
        InnerLoopCognition(planner, _Evaluator(), _Reflector()).create_plan(context).summary
        == "memory plan"
    )
    assert planner.received[1] == ("ep_1",)
    assert planner.received[2]["l2_knowledge"] == ["condition: practice"]
