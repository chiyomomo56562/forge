from datetime import UTC, datetime

from forge.adapters.outbound.procedural import SqliteProceduralRepository
from forge.application.procedural import ProceduralMemoryService, SkillLifecyclePolicy
from forge.domain.outer_loop import L2Knowledge, L2KnowledgeStatus
from forge.domain.procedural import SkillExecution, SkillStatus


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
