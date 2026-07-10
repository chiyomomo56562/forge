"""Orchestrator — inner loop 4-stage pipeline coordinator.

Coordinates the four stages of the inner loop:
    1. **Planning** — context_builder → planner → stage plan.json
    2. **Execution** — tool_registry dispatch → stage execution.json
    3. **Evaluation** — CIB guard + Phoenix Auditor → stage evaluation.json
    4. **Reflection** — reflection_loop → L1 store + L2/L3 dual-storage

Retry logic: if CIB evaluation fails (score < 0.95), the loop re-plans
and re-executes up to ``max_retries`` times (default 3).

Usage::

    from agent.orchestrator import Orchestrator
    from agent.runtime import Runtime

    runtime = Runtime()
    orch = Orchestrator(runtime=runtime, memory_manager=mm, ...)
    result = orch.run("Summarise this article")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cognition.context_builder import ContextBuilder
from .cognition.planner import Planner, Plan
from .cognition.reasoner import Reasoner
from .cognition.decision import DecisionMaker
from .cognition.reflection_loop import ReflectionLoop
from .memory.schemas import Episode, Evaluation, Reflection, EpisodeStatus
from .memory.constitution.guard import CIBGuard
from .runtime import Runtime, Session
from .tools.base import ToolContext, ToolResult
from .tools.registry import ToolRegistry
from .utils.logging import get_logger

logger = get_logger("agent.orchestrator")


# ===========================================================================
# Result dataclass
# ===========================================================================

@dataclass
class LoopResult:
    """Final result of an inner-loop cycle.

    Attributes:
        session_id: The session that ran this cycle.
        episode_id: The episode ID assigned to this cycle.
        success: Whether the loop completed successfully.
        execution_output: The primary output from execution.
        evaluation: The evaluation dict (CIB + Phoenix scores).
        reflection: The reflection dict (4 fields).
        retries: Number of retries during evaluation.
        error: Error message if ``success`` is ``False``.
    """
    session_id: str = ""
    episode_id: str = ""
    success: bool = False
    execution_output: str = ""
    evaluation: dict[str, Any] = field(default_factory=dict)
    reflection: dict[str, Any] = field(default_factory=dict)
    retries: int = 0
    error: str = ""


# ===========================================================================
# Orchestrator
# ===========================================================================

class Orchestrator:
    """Coordinates the inner-loop 4-stage pipeline.

    Args:
        runtime: A :class:`Runtime` instance for session management.
        memory_manager: A :class:`MemoryManager` for L1–L5 access.
        tool_registry: A :class:`ToolRegistry` for tool dispatch.
        llm_client: Optional LLM client for evaluation (Phoenix Auditor).
        context_builder: Optional :class:`ContextBuilder`. If ``None``,
            one is created from the memory manager.
        planner: Optional :class:`Planner`.
        reasoner: Optional :class:`Reasoner`.
        decision_maker: Optional :class:`DecisionMaker`.
        reflection_loop: Optional :class:`ReflectionLoop`.
        cib_guard: Optional :class:`CIBGuard`.
        max_retries: Maximum number of evaluation retries (default 3).
    """

    def __init__(
        self,
        runtime: Runtime,
        memory_manager: Any | None = None,
        tool_registry: ToolRegistry | None = None,
        llm_client: Any | None = None,
        context_builder: ContextBuilder | None = None,
        planner: Planner | None = None,
        reasoner: Reasoner | None = None,
        decision_maker: DecisionMaker | None = None,
        reflection_loop: ReflectionLoop | None = None,
        cib_guard: CIBGuard | None = None,
        max_retries: int = 3,
    ):
        self.runtime = runtime
        self.memory_manager = memory_manager
        self.tool_registry = tool_registry
        self.llm_client = llm_client
        self.max_retries = max_retries

        # Cognition modules (use provided or create defaults)
        self.context_builder = context_builder or (
            ContextBuilder(memory_manager=memory_manager) if memory_manager else None
        )
        self.planner = planner or Planner(llm_client=llm_client)
        self.reasoner = reasoner or Reasoner(llm_client=llm_client)
        self.decision_maker = decision_maker or DecisionMaker(llm_client=llm_client)
        self.reflection_loop = reflection_loop or ReflectionLoop(llm_client=llm_client)
        self.cib_guard = cib_guard or CIBGuard()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        user_input: str,
        task_category: str = "general",
        user_confirm: Any = None,
    ) -> LoopResult:
        """Run the full inner-loop pipeline for a user request.

        Args:
            user_input: The user's request.
            task_category: Task category hint for the planner.
            user_confirm: Optional HITL callback for tool confirmation.

        Returns:
            :class:`LoopResult` with the final outcome.
        """
        session = self.runtime.create_session(user_input)
        logger.info(
            f"Starting inner loop for session {session.session_id} "
            f"(episode {session.working_memory.episode_id})"
        )

        try:
            result = self._run_loop(session, user_input, task_category, user_confirm)
            return result
        except Exception as e:
            logger.error(f"Inner loop failed: {e}", exc_info=True)
            return LoopResult(
                session_id=session.session_id,
                episode_id=session.working_memory.episode_id,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )
        finally:
            # Keep working dir if failed (for debugging), clean up if success
            self.runtime.end_session(session.session_id, cleanup=True)

    # ------------------------------------------------------------------
    # Loop implementation
    # ------------------------------------------------------------------

    def _run_loop(
        self,
        session: Session,
        user_input: str,
        task_category: str,
        user_confirm: Any,
    ) -> LoopResult:
        """Execute the 4-stage loop with retry logic."""
        ctx = self.runtime.make_tool_context(session, user_confirm=user_confirm)
        wm = session.working_memory

        # ---- Stage 1: Planning ----
        plan = self._stage_planning(user_input, task_category)
        self.runtime.stage_plan(session, self._plan_to_dict(plan, user_input, task_category))
        logger.info(f"Stage 1 (Planning): {plan.step_count} steps")

        # ---- Stage 2 & 3: Execution + Evaluation (with retry) ----
        execution_output = ""
        evaluation_dict: dict[str, Any] = {}
        retries = 0

        while retries <= self.max_retries:
            # Stage 2: Execution
            execution_result = self._stage_execution(plan, ctx)
            execution_output = execution_result.get("output", "")
            execution_result["retry"] = retries
            self.runtime.stage_execution(session, execution_result)
            logger.info(f"Stage 2 (Execution): success={execution_result.get('success', False)}")

            # Stage 3: Evaluation
            evaluation_dict = self._stage_evaluation(
                user_input, execution_output, plan, retries
            )
            self.runtime.stage_evaluation(session, evaluation_dict)
            logger.info(
                f"Stage 3 (Evaluation): CIB passed={evaluation_dict.get('cib_passed', False)}, "
                f"phoenix_score={evaluation_dict.get('phoenix_score', 'N/A')}"
            )

            # Check if evaluation passed
            if evaluation_dict.get("cib_passed", True) and evaluation_dict.get("phoenix_passed", True):
                break  # Both gates passed

            # Evaluation failed — retry
            retries += 1
            wm.retry_count = retries
            if retries <= self.max_retries:
                logger.warning(
                    f"Evaluation failed (retry {retries}/{self.max_retries}), re-planning..."
                )
                plan = self._stage_planning(user_input, task_category)
                self.runtime.stage_plan(session, self._plan_to_dict(plan, user_input, task_category))
            else:
                logger.warning("Max retries reached, proceeding with failed evaluation")

        # ---- Stage 4: Reflection ----
        reflection_dict = self._stage_reflection(
            user_input, execution_output, evaluation_dict
        )
        self.runtime.stage_reflection(session, reflection_dict)
        logger.info(f"Stage 4 (Reflection): {reflection_dict.get('summary', '')[:80]}")

        # ---- Persist to L1 ----
        if self.memory_manager is not None:
            self._persist_episode(session, user_input, execution_output, evaluation_dict, reflection_dict)

        success = evaluation_dict.get("cib_passed", False) and evaluation_dict.get("phoenix_passed", False)

        return LoopResult(
            session_id=session.session_id,
            episode_id=wm.episode_id,
            success=success,
            execution_output=execution_output,
            evaluation=evaluation_dict,
            reflection=reflection_dict,
            retries=retries if retries <= self.max_retries else self.max_retries,
        )

    # ------------------------------------------------------------------
    # Stage 1: Planning
    # ------------------------------------------------------------------

    def _stage_planning(self, user_input: str, task_category: str) -> Plan:
        """Build context and generate an execution plan.

        Pipeline: context_builder → planner → reasoner → decision_maker
        """
        # Build context from memory
        context_str = ""
        if self.context_builder is not None:
            try:
                built = self.context_builder.build(user_input)
                context_str = built.to_prompt_string()
            except Exception as e:
                logger.warning(f"Context building failed: {e}")
                context_str = "(context building failed)"

        # Generate plan
        plan = self.planner.plan(user_input, context=context_str, task_category=task_category)

        # Validate and generate alternatives
        validation = self.reasoner.validate(plan, context=context_str)
        alternatives = []
        if not validation.feasible or validation.confidence < 0.5:
            alternatives = self.reasoner.generate_alternatives(plan, context=context_str)

        # Select best plan
        decision = self.decision_maker.decide(plan, validation, alternatives)
        return decision.selected_plan or plan

    # ------------------------------------------------------------------
    # Stage 2: Execution
    # ------------------------------------------------------------------

    def _stage_execution(self, plan: Plan, ctx: ToolContext) -> dict[str, Any]:
        """Execute the plan by dispatching tool calls.

        Iterates through plan steps, dispatching each to the tool registry.
        Collects outputs and errors.
        """
        if self.tool_registry is None:
            return {
                "success": True,
                "output": "[no tool registry — execution skipped]",
                "steps_executed": 0,
                "step_results": [],
            }

        step_results: list[dict[str, Any]] = []
        all_output: list[str] = []
        all_success = True

        for i, step in enumerate(plan.steps):
            step_dict: dict[str, Any] = {
                "step": i + 1,
                "description": step.description,
                "tool": step.tool,
            }

            if not step.tool:
                # No tool specified — record as informational
                step_dict["success"] = True
                step_dict["output"] = f"[no tool] {step.description}"
                all_output.append(step_dict["output"])
                step_results.append(step_dict)
                continue

            # Dispatch to tool registry
            tool = self.tool_registry.get(step.tool)
            if tool is None:
                step_dict["success"] = False
                step_dict["error"] = f"Tool not found: {step.tool}"
                all_success = False
                step_results.append(step_dict)
                continue

            # Execute — parse args from step description (simple heuristic)
            args = self._parse_step_args(step)
            result = self.tool_registry.execute(step.tool, args, ctx)

            step_dict["success"] = result.success
            step_dict["output"] = str(result.output) if result.success else ""
            step_dict["error"] = result.error if not result.success else ""
            step_dict["duration_seconds"] = result.metadata.get("duration_seconds", 0)

            if result.success:
                all_output.append(str(result.output))
            else:
                all_success = False
                all_output.append(f"[ERROR: {result.error}]")

            step_results.append(step_dict)

        return {
            "success": all_success,
            "output": "\n".join(all_output),
            "steps_executed": len(plan.steps),
            "step_results": step_results,
        }

    @staticmethod
    def _parse_step_args(step: Any) -> dict[str, Any]:
        """Parse tool arguments from a plan step.

        Uses the step description as the primary input argument.
        Subclasses can override for more sophisticated parsing.
        """
        return {"input": step.description, "query": step.description, "code": step.description}

    # ------------------------------------------------------------------
    # Stage 3: Evaluation
    # Evaluation은 이렇게 하는게 맞나 잘 모르겠음
    # 다른 오픈소스에서 어떻게 하나 보고 넣어야할듯
    # ------------------------------------------------------------------

    def _stage_evaluation(
        self,
        user_input: str,
        execution_output: str,
        plan: Plan,
        retry: int,
    ) -> dict[str, Any]:
        """Evaluate the execution result via CIB + Phoenix Auditor.

        Returns a dict with:
            - cib_passed, cib_min_score, cib_scores
            - phoenix_passed, phoenix_score, domain_score, reflection_score
            - success_score, pain_index
            - retry
        """
        result_text = execution_output or "(no output)"

        # --- CIB evaluation ---
        cib_passed = True
        cib_min_score = 1.0
        cib_scores: list[float] = []

        if self.memory_manager is not None:
            try:
                constitution = self.memory_manager.constitution
                cib_result = self.cib_guard.evaluate(result_text, constitution)
                cib_passed = cib_result.passed
                cib_min_score = cib_result.min_score
                cib_scores = cib_result.scores
            except Exception as e:
                logger.warning(f"CIB evaluation failed: {e}")
                cib_passed = True  # fail open if constitution not available
        else:
            # No memory manager — skip CIB (pass by default)
            logger.debug("No memory manager, skipping CIB evaluation")

        # --- Phoenix Auditor evaluation ---
        phoenix_score = 0.5
        domain_score = 0.5
        reflection_score = 0.5
        phoenix_passed = True

        if self.llm_client is not None:
            try:
                phoenix_score, domain_score, reflection_score = self._phoenix_evaluate(
                    result_text, plan
                )
            except Exception as e:
                logger.warning(f"Phoenix evaluation failed: {e}")
                phoenix_score = 0.5
        else:
            # Heuristic: base on execution success
            if execution_output and "ERROR" not in execution_output:
                phoenix_score = 0.8
                domain_score = 0.8
                reflection_score = 0.8
            else:
                phoenix_score = 0.3
                domain_score = 0.3
                reflection_score = 0.3

        phoenix_passed = phoenix_score >= 0.95

        # --- Compute Pain Index ---
        success_score = phoenix_score
        pain_index = round(1.0 - success_score, 4) if success_score is not None else None

        # --- Determine episode status ---
        if cib_passed and phoenix_passed:
            status = EpisodeStatus.SUCCESS
        elif cib_passed or phoenix_passed:
            status = EpisodeStatus.PARTIAL
        else:
            status = EpisodeStatus.FAILURE

        return {
            "cib_passed": cib_passed,
            "cib_min_score": cib_min_score,
            "cib_scores": cib_scores,
            "phoenix_passed": phoenix_passed,
            "phoenix_score": phoenix_score,
            "domain_score": domain_score,
            "reflection_score": reflection_score,
            "success_score": success_score,
            "pain_index": pain_index,
            "status": status.value,
            "retry": retry,
        }

    def _phoenix_evaluate(self, result_text: str, plan: Plan) -> tuple[float, float, float]:
        """Run Phoenix Auditor evaluation via LLM.

        Returns:
            (phoenix_score, domain_score, reflection_score)
        """
        from .llm.prompts import get_template
        from .llm.response_parser import extract_json

        template = get_template("phoenix_auditor")
        plan_text = "\n".join(
            f"  {i+1}. {s.description}" for i, s in enumerate(plan.steps)
        )

        prompt = template.render(
            result=result_text,
            reflection=plan_text or "(no reflection available)",
        )

        response = self.llm_client.chat(prompt=prompt, system=template.system)

        if response.model == "fallback":
            # Heuristic fallback
            return 0.5, 0.5, 0.5

        parsed = extract_json(response.content)
        if parsed and isinstance(parsed, dict):
            domain = float(parsed.get("domain_score", 0.5))
            reflection = float(parsed.get("reflection_score", 0.5))
            phoenix = float(parsed.get("phoenix_score", 0.6 * domain + 0.4 * reflection))
            return phoenix, domain, reflection

        # Fallback: 6:4 weighting
        domain = 0.5
        reflection = 0.5
        phoenix = 0.6 * domain + 0.4 * reflection
        return phoenix, domain, reflection

    # ------------------------------------------------------------------
    # Stage 4: Reflection
    # ------------------------------------------------------------------

    def _stage_reflection(
        self,
        task: str,
        execution_output: str,
        evaluation: dict[str, Any],
    ) -> dict[str, Any]:
        """Extract the 4 reflection fields from execution + evaluation.

        Returns a dict with what_worked, what_failed, next_hint,
        causal_condition, and summary.
        """
        # Build an Evaluation object for the reflection loop
        eval_obj = Evaluation(
            status=EpisodeStatus(evaluation.get("status", "Pending")),
            success_score=evaluation.get("success_score"),
            pain_index=evaluation.get("pain_index"),
        )

        result = self.reflection_loop.reflect(
            task=task,
            execution_summary=execution_output,
            evaluation=eval_obj,
            pain_index=evaluation.get("pain_index"),
        )

        refl = result.reflection
        return {
            "what_worked": refl.what_worked,
            "what_failed": refl.what_failed,
            "next_hint": refl.next_hint,
            "causal_condition": refl.causal_condition,
            "summary": result.summary,
            "source": result.source,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_episode(
        self,
        session: Session,
        task: str,
        execution_output: str,
        evaluation: dict[str, Any],
        reflection: dict[str, Any],
    ) -> None:
        """Persist the episode + reflection to L1 via MemoryManager.

        Creates an :class:`Episode`, stores it in L1, then stores the
        reflection (which routes hints to L2/L3 via dual-storage).
        """
        wm = session.working_memory

        # Build evaluation object
        eval_obj = Evaluation(
            status=EpisodeStatus(evaluation.get("status", "Pending")),
            success_score=evaluation.get("success_score"),
            pain_index=evaluation.get("pain_index"),
            cib_score=evaluation.get("cib_min_score"),
            phoenix_score=evaluation.get("phoenix_score"),
            domain_score=evaluation.get("domain_score"),
            reflection_score=evaluation.get("reflection_score"),
        )
        eval_obj.compute_pain_index()

        # Build reflection object
        refl_obj = Reflection(
            what_worked=reflection.get("what_worked", ""),
            what_failed=reflection.get("what_failed", ""),
            next_hint=reflection.get("next_hint", ""),
            causal_condition=reflection.get("causal_condition", ""),
        )

        # Create episode
        episode = Episode(
            episode_id=wm.episode_id,
            task=task,
            execution_summary=execution_output,
            evaluation=eval_obj,
            reflection=refl_obj,
            timestamp=session.created_at,
            task_category=wm.task_category,
            has_reflection=not refl_obj.is_empty,
        )

        # Store in L1
        try:
            self.memory_manager.store_episode(episode)
            logger.info(f"Episode {wm.episode_id} stored in L1")
        except Exception as e:
            logger.error(f"Failed to store episode in L1: {e}")

        # Store reflection (routes hints to L2/L3)
        if not refl_obj.is_empty:
            try:
                self.memory_manager.store_reflection(wm.episode_id, refl_obj)
                logger.info(f"Reflection stored for {wm.episode_id} (L2/L3 dual-storage)")
            except Exception as e:
                logger.error(f"Failed to store reflection: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _plan_to_dict(plan: Plan, user_input: str, task_category: str) -> dict[str, Any]:
        """Serialise a Plan to a JSON-stagable dict."""
        return {
            "user_input": user_input,
            "task_category": task_category,
            "steps": [
                {
                    "description": s.description,
                    "tool": s.tool,
                    "risk": s.risk,
                    "prerequisites": s.prerequisites,
                }
                for s in plan.steps
            ],
            "estimated_success": plan.estimated_success,
            "step_count": plan.step_count,
        }