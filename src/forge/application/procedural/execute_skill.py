"""Safe execution path for reviewed L3 procedural skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from forge.application.procedural.manage_skills import ProceduralMemoryService
from forge.domain.inner_loop import PlanStep, ToolExecution
from forge.domain.memory import Evaluation, ExecutionOutcome
from forge.domain.procedural import SkillExecution, SkillStatus
from forge.ports.outbound.inner_loop import PlanStepExecutor
from forge.ports.outbound.procedural_repository import ProceduralRepository


class SkillExecutionError(ValueError):
    """Raised before any tool call when an L3 skill is not executable."""


@dataclass(frozen=True)
class SkillRunResult:
    skill_id: str
    executions: tuple[ToolExecution, ...]


class SkillExecutor:
    """Run only explicitly bound L3 tool steps through the standard executor."""

    def __init__(
        self,
        repository: ProceduralRepository,
        executor: PlanStepExecutor,
        lifecycle: ProceduralMemoryService,
        *,
        max_steps_per_run: int = 8,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._lifecycle = lifecycle
        if max_steps_per_run <= 0:
            raise ValueError("L3 execution step limit must be positive")
        self._max_steps_per_run = max_steps_per_run

    def execute_active(
        self,
        skill_id: str,
        *,
        episode_id: str,
        cib_score: float | None = None,
        session_id: str = "",
    ) -> SkillRunResult:
        """Run an Active skill as part of normal Inner Loop work."""
        return self._execute(
            skill_id,
            required_status=SkillStatus.ACTIVE,
            episode_id=episode_id,
            cib_score=cib_score,
            session_id=session_id,
        )

    def execute_validation(
        self, skill_id: str, *, episode_id: str, session_id: str = ""
    ) -> SkillRunResult:
        """Run a Validating skill only in the explicit promotion lane."""
        return self._execute(
            skill_id,
            required_status=SkillStatus.VALIDATING,
            episode_id=episode_id,
            session_id=session_id,
        )

    def _execute(
        self,
        skill_id: str,
        *,
        required_status: SkillStatus,
        episode_id: str,
        cib_score: float | None = None,
        session_id: str = "",
    ) -> SkillRunResult:
        skill = self._repository.get(skill_id)
        if skill is None:
            raise SkillExecutionError("Unknown skill")
        if skill.status is not required_status:
            raise SkillExecutionError(
                f"Skill must be {required_status.value} to execute in this lane"
            )
        if not skill.executable_steps:
            raise SkillExecutionError("Skill has no reviewed executable steps")
        if len(skill.executable_steps) > self._max_steps_per_run:
            raise SkillExecutionError("Skill exceeds the L3 execution step limit")

        executions: list[ToolExecution] = []
        for index, step in enumerate(skill.executable_steps):
            execution = self._executor.execute(
                PlanStep(
                    step_id=step.step_id,
                    summary=f"L3 skill {skill.skill_id}: {step.step_id}",
                    tool_name=step.tool_name,
                    tool_arguments=step.tool_arguments,
                    retry_allowed=False,
                    depends_on=(skill.executable_steps[index - 1].step_id,)
                    if index
                    else (),
                ),
                session_id=session_id,
            )
            executions.append(execution)
            if execution.outcome is not ExecutionOutcome.COMPLETED:
                break

        succeeded = len(executions) == len(skill.executable_steps) and all(
            item.outcome is ExecutionOutcome.COMPLETED for item in executions
        )
        if cib_score is not None:
            self._record(skill.skill_id, episode_id, succeeded, cib_score)
        return SkillRunResult(skill.skill_id, tuple(executions))

    def record_evaluation(
        self, skill_id: str, *, episode_id: str, evaluation: Evaluation
    ) -> None:
        """Update L3 lifecycle only after the Inner Loop has evaluated its outcome."""
        success = evaluation.success_score is not None and evaluation.success_score >= 0.5
        self._record(
            skill_id,
            episode_id,
            success,
            evaluation.cib_score or 0.0,
            pain_index=evaluation.pain_index,
            tool_error_ratio=evaluation.tool_error_ratio,
        )

    def _record(
        self,
        skill_id: str,
        episode_id: str,
        succeeded: bool,
        cib_score: float,
        *,
        pain_index: float | None = None,
        tool_error_ratio: float | None = None,
    ) -> None:
        self._lifecycle.record_execution(
            SkillExecution(
                skill_id=skill_id,
                episode_id=episode_id,
                success_score=1.0 if succeeded else 0.0,
                cib_score=cib_score,
                executed_at=datetime.now(UTC),
                pain_index=pain_index,
                tool_error_ratio=tool_error_ratio,
            )
        )
