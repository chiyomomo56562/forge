from datetime import UTC, datetime

import pytest

from forge.adapters.outbound.procedural import SqliteProceduralRepository
from forge.application.procedural import ProceduralMemoryService, SkillLifecyclePolicy
from forge.domain.outer_loop import L2Knowledge, L2KnowledgeStatus
from forge.domain.procedural import SkillExecution, SkillStatus, SkillStep


def test_active_l2_creates_seed_and_successful_cib_safe_samples_activate_it(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    service = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    skill = service.seed_from_l2(
        L2Knowledge(
            "l2_test",
            "pc_test",
            "inspect then verify",
            "repository",
            1.0,
            L2KnowledgeStatus.ACTIVE,
            ("ep_1",),
            (),
            datetime.now(UTC),
        )
    )
    assert skill is not None and skill.status is SkillStatus.SEED
    for number in range(3):
        skill = service.record_execution(
            SkillExecution(skill.skill_id, f"ep_{number}", 1.0, 1.0, datetime.now(UTC))
        )
    assert skill.status is SkillStatus.ACTIVE
    assert repository.list_active()[0].procedure == ("inspect then verify",)


def test_repeated_low_performance_archives_a_skill(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    service = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    skill = service.seed_from_l2(
        L2Knowledge(
            "l2_low",
            "pc_low",
            "inspect then verify",
            "repository",
            1.0,
            L2KnowledgeStatus.ACTIVE,
            ("ep_1",),
            (),
            datetime.now(UTC),
        )
    )
    assert skill is not None
    for number in range(4):
        skill = service.record_execution(
            SkillExecution(skill.skill_id, f"ep_low_{number}", 0.0, 1.0, datetime.now(UTC))
        )
    assert skill.status is SkillStatus.ARCHIVED


def test_tool_reflection_is_promoted_to_a_non_executable_step_draft(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    service = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    repository.store_pending_hint("ep_1", "use focused search", ("workspace.search_text",))

    skill = service.seed_from_l2(
        L2Knowledge(
            "l2_draft",
            "pc_draft",
            "inspect the repository",
            "repository",
            1.0,
            L2KnowledgeStatus.ACTIVE,
            ("ep_1",),
            (),
            datetime.now(UTC),
        )
    )

    assert skill is not None
    assert skill.step_drafts[0].tool_name == "workspace.search_text"
    assert skill.step_drafts[0].hint == "use focused search"
    with pytest.raises(ValueError, match="not present in a reviewed draft"):
        service.bind_executable_steps(
            skill.skill_id, (SkillStep("read", "workspace.read_file", {"path": "README.md"}),)
        )
    approved = service.bind_executable_steps(
        skill.skill_id,
        (SkillStep("search", "workspace.search_text", {"query": "TODO"}),),
    )
    assert approved.executable_steps[0].tool_name == "workspace.search_text"
