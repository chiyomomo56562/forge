from forge.application.memory import MemoryManager
from forge.domain.memory import Reflection, ReflectionRoute


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
