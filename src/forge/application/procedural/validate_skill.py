"""Explicit validation lane for reviewed L3 procedures."""

from __future__ import annotations

from dataclasses import dataclass

from forge.application.procedural.execute_skill import SkillExecutor, SkillRunResult
from forge.domain.memory import Evaluation
from forge.ports.outbound import InnerLoopEvaluator


@dataclass(frozen=True)
class SkillValidationResult:
    run: SkillRunResult
    evaluation: Evaluation


class SkillValidationService:
    """Runs reviewed steps only for validating skills, then records an evaluator result."""

    def __init__(self, executor: SkillExecutor, evaluator: InnerLoopEvaluator) -> None:
        self._executor = executor
        self._evaluator = evaluator

    def validate(
        self, skill_id: str, *, episode_id: str, session_id: str = ""
    ) -> SkillValidationResult:
        run = self._executor.execute_validation(
            skill_id, episode_id=episode_id, session_id=session_id
        )
        final = run.executions[-1]
        evaluation = self._evaluator.evaluate(
            task_request=f"Validate reviewed L3 skill {skill_id}",
            execution=final,
            retry_count=0,
        )
        self._executor.record_evaluation(skill_id, episode_id=episode_id, evaluation=evaluation)
        return SkillValidationResult(run, evaluation)
