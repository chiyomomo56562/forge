from datetime import UTC, datetime

import pytest

from forge.adapters.outbound.procedural import SqliteProceduralRepository
from forge.application.procedural import (
    ProceduralMemoryService,
    SkillExecutionError,
    SkillExecutor,
    SkillLifecyclePolicy,
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

    result = SkillExecutor(repository, executor, lifecycle).execute(
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
        SkillExecutor(repository, RecordingExecutor(), lifecycle).execute(
            "skill_test", episode_id="ep_1", cib_score=1.0
        )
