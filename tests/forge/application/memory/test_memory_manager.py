from datetime import UTC, datetime, timedelta

from forge.application.memory import MemoryManager
from forge.domain.memory import Reflection, ReflectionRoute
from forge.domain.procedural import ProceduralSkill, SkillStatus


class _Unused:
    pass


def _manager() -> MemoryManager:
    return MemoryManager(_Unused(), _Unused(), _Unused(), _Unused())


def test_routes_general_reflection_to_l2_consolidation():
    decision = _manager().route_reflection(Reflection("lesson", "", "", "condition"), tool_names=())

    assert decision.route is ReflectionRoute.L2_GENERALIZATION


def test_marks_tool_specific_reflection_as_l3_pending_until_l3_exists():
    decision = _manager().route_reflection(
        Reflection("lesson", "", "", "condition"), tool_names=("workspace.read_file",)
    )

    assert decision.route is ReflectionRoute.L3_PROCEDURE_PENDING
    assert decision.reason == "reflection.tool_specific"


def test_empty_reflection_remains_l1_only():
    decision = _manager().route_reflection(Reflection("", "", "", ""), tool_names=())

    assert decision.route is ReflectionRoute.L1_ONLY


def test_tool_specific_reflection_is_persisted_when_a_procedural_store_is_available(tmp_path):
    from forge.adapters.outbound.procedural import SqliteProceduralRepository

    repository = SqliteProceduralRepository(tmp_path / "skills.sqlite3")
    manager = MemoryManager(_Unused(), _Unused(), _Unused(), _Unused(), procedural=repository)

    manager.route_reflection(
        Reflection("lesson", "", "use retries", "condition"),
        tool_names=("workspace.read_file",),
        source_id="ep_1",
    )

    with repository._connect() as db:
        assert (
            db.execute("SELECT hint FROM pending_hints WHERE source_id='ep_1'").fetchone()[0]
            == "use retries"
        )


class _Procedural:
    def __init__(self, skills):
        self._skills = skills

    def list_active(self):
        return self._skills


def _skill(skill_id, procedure, *, success_rate=1.0, age=0):
    return ProceduralSkill(
        skill_id,
        f"l2_{skill_id}",
        procedure,
        (),
        SkillStatus.ACTIVE,
        success_rate,
        3,
        datetime.now(UTC) - timedelta(seconds=age),
    )


def test_l3_context_ranks_relevance_then_success_recency_and_id():
    manager = MemoryManager(
        _Unused(),
        _Unused(),
        _Unused(),
        _Unused(),
        top_k=3,
        procedural=_Procedural(
            [
                _skill("skill_z", ("inspect repository status",), success_rate=0.8),
                _skill("skill_b", ("inspect repository",), age=20),
                _skill("skill_a", ("inspect repository",), age=20),
                _skill("skill_other", ("deploy service",)),
            ]
        ),
    )

    skills = manager._relevant_l3("inspect repository status")

    assert [skill.skill_id for skill in skills] == ["skill_z", "skill_a", "skill_b"]


def test_l3_context_enforces_hard_character_budget_without_reordering_candidates():
    short = _skill("skill_short", ("inspect",))
    long = _skill("skill_long", ("inspect " + "x" * 100,))
    manager = MemoryManager(
        _Unused(),
        _Unused(),
        _Unused(),
        _Unused(),
        top_k=3,
        l3_context_max_chars=len(MemoryManager._render_l3_skill(short)),
        procedural=_Procedural([long, short]),
    )

    skills = manager._relevant_l3("inspect")

    assert [skill.skill_id for skill in skills] == ["skill_short"]
