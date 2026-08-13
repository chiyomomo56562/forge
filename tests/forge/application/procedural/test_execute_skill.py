from datetime import UTC, datetime

import pytest

from forge.adapters.outbound.inner_loop import DeterministicEvaluator
from forge.adapters.outbound.procedural import SqliteProceduralRepository
from forge.application.procedural import (
    ProceduralMemoryService,
    SkillExecutionError,
    SkillExecutor,
    SkillLifecyclePolicy,
    SkillValidationService,
)
from forge.domain.inner_loop import ToolExecution
from forge.domain.memory import ExecutionOutcome
from forge.domain.procedural import ProceduralSkill, SkillStatus, SkillStep, SkillStepDraft


class RecordingExecutor:
    def __init__(self, outcomes: tuple[ExecutionOutcome, ...] = ()) -> None:
        self.steps = []
        self._outcomes = iter(outcomes or (ExecutionOutcome.COMPLETED,))

    def execute(self, step, *, session_id="", attempt=0):
        del session_id, attempt
        self.steps.append(step)
        return ToolExecution(step.step_id, step.summary, next(self._outcomes))


def _active_skill() -> ProceduralSkill:
    return ProceduralSkill(
        "skill_test",
        "l2_test",
        ("inspect",),
        (),
        SkillStatus.ACTIVE,
        1.0,
        3,
        datetime.now(UTC),
        (),
        (
            SkillStepDraft("draft_list", "ep_0", "inspect", "workspace.list_files"),
            SkillStepDraft("draft_status", "ep_0", "inspect", "git.status"),
        ),
    )


def test_executor_runs_reviewed_steps_in_order_and_records_success(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    lifecycle = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    repository.upsert(_active_skill())
    lifecycle.bind_executable_steps(
        "skill_test",
        (
            SkillStep("inspect", "workspace.list_files", {"path": "."}),
            SkillStep("status", "git.status"),
        ),
    )
    executor = RecordingExecutor((ExecutionOutcome.COMPLETED, ExecutionOutcome.COMPLETED))

    result = SkillExecutor(repository, executor, lifecycle).execute_active(
        "skill_test", episode_id="ep_1", cib_score=1.0, session_id="session_1"
    )

    assert [step.tool_name for step in executor.steps] == ["workspace.list_files", "git.status"]
    assert executor.steps[1].depends_on == ("inspect",)
    assert len(result.executions) == 2
    assert repository.executions_for("skill_test")[0].success_score == 1.0


def test_executor_rejects_active_skill_without_reviewed_tool_steps(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    lifecycle = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    repository.upsert(_active_skill())

    with pytest.raises(SkillExecutionError, match="reviewed executable steps"):
        SkillExecutor(repository, RecordingExecutor(), lifecycle).execute_active(
            "skill_test", episode_id="ep_1", cib_score=1.0
        )


def test_executor_blocks_a_skill_that_exceeds_its_step_budget(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    lifecycle = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    repository.upsert(_active_skill())
    lifecycle.bind_executable_steps(
        "skill_test",
        (
            SkillStep("inspect", "workspace.list_files", {"path": "."}),
            SkillStep("status", "git.status"),
        ),
    )
    executor = RecordingExecutor()

    with pytest.raises(SkillExecutionError, match="step limit"):
        SkillExecutor(repository, executor, lifecycle, max_steps_per_run=1).execute_active(
            "skill_test", episode_id="ep_1", cib_score=1.0
        )

    assert executor.steps == []


def test_executor_stops_on_first_failed_step_and_records_failed_execution(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    lifecycle = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    repository.upsert(_active_skill())
    lifecycle.bind_executable_steps(
        "skill_test",
        (
            SkillStep("inspect", "workspace.list_files", {"path": "."}),
            SkillStep("status", "git.status"),
        ),
    )
    executor = RecordingExecutor((ExecutionOutcome.FAILED, ExecutionOutcome.COMPLETED))

    result = SkillExecutor(repository, executor, lifecycle).execute_active(
        "skill_test", episode_id="ep_failure", cib_score=1.0
    )

    assert [step.step_id for step in executor.steps] == ["inspect"]
    assert len(result.executions) == 1
    assert repository.executions_for("skill_test")[0].success_score == 0.0


def test_validation_execution_is_separate_from_normal_active_execution(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    lifecycle = ProceduralMemoryService(repository, SkillLifecyclePolicy(min_samples=1))
    skill = _active_skill()
    repository.upsert(
        ProceduralSkill(
            skill.skill_id,
            skill.source_l2_id,
            skill.procedure,
            skill.reflection_hints,
            SkillStatus.VALIDATING,
            skill.success_rate,
            skill.total_executions,
            skill.updated_at,
            skill.executable_steps,
            skill.step_drafts,
        )
    )
    lifecycle.bind_executable_steps(
        "skill_test", (SkillStep("inspect", "workspace.list_files", {"path": "."}),)
    )
    executor = RecordingExecutor()

    result = SkillValidationService(
        SkillExecutor(repository, executor, lifecycle), DeterministicEvaluator()
    ).validate("skill_test", episode_id="validation_1")

    assert result.evaluation.success_score == 1.0
    assert repository.get("skill_test").status is SkillStatus.VALIDATING
    assert lifecycle.refresh(repository.get("skill_test")).status is SkillStatus.ACTIVE
    assert [step.tool_name for step in executor.steps] == ["workspace.list_files"]
    with pytest.raises(SkillExecutionError, match="validating"):
        SkillExecutor(repository, executor, lifecycle).execute_validation(
            "skill_test", episode_id="validation_2"
        )
