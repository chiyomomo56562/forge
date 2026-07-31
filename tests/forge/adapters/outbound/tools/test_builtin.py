"""내장 도구 registry의 workspace·권한 경계 테스트.

최종 수정일: 2026-07-31
"""

from pathlib import Path

import pytest

from forge.adapters.outbound.tools import (
    BuiltinToolRegistry,
    RegistryPlanStepExecutor,
    StaticToolAuthorizationPolicy,
)
from forge.adapters.outbound.tools.builtin import ToolInvocationError
from forge.domain.inner_loop import PlanStep
from forge.domain.memory import ExecutionOutcome


def _registry(root: Path) -> BuiltinToolRegistry:
    """작은 output limit을 적용한 테스트용 registry를 만든다.

    최종 수정일: 2026-07-31
    """
    return BuiltinToolRegistry(root, max_read_bytes=8, max_search_results=2, max_output_bytes=16)


def test_read_file_returns_text_but_audit_payload_does_not_need_content(tmp_path: Path) -> None:
    """UTF-8 파일 읽기가 byte 상한과 truncation을 보존하는지 검증한다.

    최종 수정일: 2026-07-31
    """
    (tmp_path / "note.txt").write_text("abcdefghijk", encoding="utf-8")

    registry = _registry(tmp_path)
    execution = RegistryPlanStepExecutor(registry, StaticToolAuthorizationPolicy()).execute(
        PlanStep("read", "Read note", "workspace.read_file", {"path": "note.txt"}),
        session_id="ses_test",
    )

    assert execution.outcome is ExecutionOutcome.COMPLETED
    assert execution.output["content"] == "abcdefgh"
    assert execution.truncated is True
    assert execution.audit_details["tool_arguments"]["path"] != "note.txt"


def test_workspace_escape_is_rejected_before_handler_execution(tmp_path: Path) -> None:
    """상대 경로 탈출이 schema 검증에서 거부되는지 검증한다.

    최종 수정일: 2026-07-31
    """
    registry = _registry(tmp_path)

    with pytest.raises(ToolInvocationError, match="tool.path_outside_workspace"):
        registry.validate_arguments("workspace.read_file", {"path": "../outside.txt"})


def test_mutation_without_grant_maps_denial_to_halted(tmp_path: Path) -> None:
    """승인 없는 mutation이 filesystem 변경 없이 halted가 되는지 검증한다.

    최종 수정일: 2026-07-31
    """
    target = tmp_path / "target.txt"
    target.write_text("unchanged", encoding="utf-8")
    executor = RegistryPlanStepExecutor(_registry(tmp_path), StaticToolAuthorizationPolicy())

    execution = executor.execute(
        PlanStep("patch", "Patch target", "workspace.apply_patch", {"patch": "ignored"}),
        session_id="ses_test",
    )

    assert execution.outcome is ExecutionOutcome.HALTED
    assert execution.safe_error_code == "tool.approval_required"
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_unknown_tool_is_defensive_halted_result(tmp_path: Path) -> None:
    """손상된 plan이 executor에 도달해도 실행 없이 halted가 되는지 검증한다.

    최종 수정일: 2026-07-31
    """
    executor = RegistryPlanStepExecutor(_registry(tmp_path), StaticToolAuthorizationPolicy())
    execution = executor.execute(
        PlanStep("unknown", "Unknown", "not.registered", {}), session_id="ses_test"
    )

    assert execution.outcome is ExecutionOutcome.HALTED
    assert execution.safe_error_code == "tool.unknown"


def test_list_files_truncates_at_result_limit(tmp_path: Path) -> None:
    """파일 탐색 결과가 configured item 상한을 넘지 않는지 검증한다.

    최종 수정일: 2026-07-31
    """
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    executor = RegistryPlanStepExecutor(_registry(tmp_path), StaticToolAuthorizationPolicy())
    execution = executor.execute(
        PlanStep("list", "List", "workspace.list_files", {"path": "."}), session_id="ses_test"
    )

    assert len(execution.output["files"]) == 2
    assert execution.truncated is True
