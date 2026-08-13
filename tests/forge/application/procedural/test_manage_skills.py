from datetime import UTC, datetime

import pytest

from forge.adapters.outbound.procedural import SqliteProceduralRepository
from forge.application.procedural import ProceduralMemoryService, SkillLifecyclePolicy
from forge.domain.outer_loop import L2Knowledge, L2KnowledgeStatus
from forge.domain.procedural import SkillExecution, SkillStatus, SkillStep


def test_reviewed_validating_skill_with_safe_samples_promotes_to_active(tmp_path):
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
            ("ep_1",),
            datetime.now(UTC),
        )
    )
    assert skill is not None and skill.status is SkillStatus.SEED
    repository.store_pending_hint("ep_1", "inspect", ("workspace.list_files",))
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
    assert skill is not None
    service.bind_executable_steps(
        skill.skill_id,
        (SkillStep("inspect", "workspace.list_files", {"path": "."}),),
    )
    skill = service.begin_validation(skill.skill_id)
    assert skill.status is SkillStatus.VALIDATING
    for number in range(3):
        skill = service.record_execution(
            SkillExecution(skill.skill_id, f"ep_{number}", 1.0, 1.0, datetime.now(UTC))
        )
    assert skill.status is SkillStatus.ACTIVE
    assert repository.list_active()[0].procedure == ("inspect then verify", "inspect")


def test_repeated_low_performance_marks_a_skill_degrading_without_archiving_it(tmp_path):
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
    assert skill.status is SkillStatus.DEGRADING


def test_seed_requires_repeated_evidence_and_a_reviewable_tool_hint(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    service = ProceduralMemoryService(repository, SkillLifecyclePolicy(min_seed_evidence=3))
    knowledge = L2Knowledge(
        "l2_gate", "pc_gate", "inspect", "repository", 1.0,
        L2KnowledgeStatus.ACTIVE, ("ep_1", "ep_2", "ep_3"), (), datetime.now(UTC),
    )

    assert service.seed_from_l2(knowledge) is None

    repository.store_pending_hint("ep_1", "inspect first", ("workspace.list_files",))

    assert service.seed_from_l2(knowledge) is not None


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


def test_explicit_archive_preserves_skill_and_execution_history(tmp_path):
    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    service = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    skill = service.seed_from_l2(
        L2Knowledge(
            "l2_idle",
            "pc_idle",
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
    stale = SkillExecution(
        skill.skill_id,
        "ep_old",
        1.0,
        1.0,
        datetime.now(UTC),
    )
    repository.record_execution(stale)

    archived = service.archive(skill.skill_id)

    assert archived.status is SkillStatus.ARCHIVED
    assert repository.get(skill.skill_id).status is SkillStatus.ARCHIVED
    assert repository.executions_for(skill.skill_id) == [stale]




def test_skill_mutations_publish_a_versioned_artifact_and_registry(tmp_path):
    skills_dir = tmp_path / "skills"
    registry_path = tmp_path / "skill_registry.json"
    repository = SqliteProceduralRepository(
        tmp_path / "skills.sqlite3", skills_dir=skills_dir, registry_path=registry_path
    )
    service = ProceduralMemoryService(repository, SkillLifecyclePolicy())
    repository.store_pending_hint("ep_1", "inspect", ("workspace.list_files",))

    skill = service.seed_from_l2(
        L2Knowledge(
            "l2_artifact", "pc_artifact", "inspect", "repository", 1.0,
            L2KnowledgeStatus.ACTIVE, ("ep_1",), (), datetime.now(UTC),
        )
    )

    assert skill is not None
    artifact = skills_dir / f"{skill.skill_id}.yml"
    assert artifact.exists()
    assert "version: 1" in artifact.read_text(encoding="utf-8")
    assert skill.skill_id in registry_path.read_text(encoding="utf-8")

    updated = service.approve_step_draft(
        skill.skill_id, draft_id=skill.step_drafts[0].draft_id, step_id="inspect", tool_arguments={}
    )

    assert repository.get(updated.skill_id).version == 2
    assert "version: 2" in artifact.read_text(encoding="utf-8")
