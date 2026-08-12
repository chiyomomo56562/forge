"""Safe execution path for reviewed L3 procedural skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from forge.application.procedural.manage_skills import ProceduralMemoryService
from forge.domain.inner_loop import PlanStep, ToolExecution
from forge.domain.memory import ExecutionOutcome
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
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._lifecycle = lifecycle

    def execute(
        self,
        skill_id: str,
        *,
        episode_id: str,
        cib_score: float,
        session_id: str = "",
    ) -> SkillRunResult:
        skill = self._repository.get(skill_id)
        if skill is None:
            raise SkillExecutionError("Unknown skill")
        if skill.status is not SkillStatus.ACTIVE:
            raise SkillExecutionError("Only active skills may execute")
        if not skill.executable_steps:
            raise SkillExecutionError("Skill has no reviewed executable steps")

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
        self._lifecycle.record_execution(
            SkillExecution(
                skill_id=skill.skill_id,
                episode_id=episode_id,
                success_score=1.0 if succeeded else 0.0,
                cib_score=cib_score,
                executed_at=datetime.now(UTC),
            )
        )
        return SkillRunResult(skill.skill_id, tuple(executions))
