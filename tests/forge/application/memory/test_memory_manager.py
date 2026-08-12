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
