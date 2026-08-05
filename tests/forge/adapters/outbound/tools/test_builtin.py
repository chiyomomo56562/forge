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
from forge.domain.inner_loop import PlanStep, ToolInvocation, ToolStatus
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


def test_tool_schemas_returns_all_registered_tools(tmp_path: Path) -> None:
    """tool_schemas가 등록된 전체 도구의 JSON Schema를 반환하는지 검증한다.

    최종 수정일: 2026-08-04
    """
    schemas = _registry(tmp_path).tool_schemas()

    names = [s.name for s in schemas]
    assert names == [
        "workspace.list_files",
        "workspace.read_file",
        "workspace.search_text",
        "git.status",
        "git.diff",
        "workspace.apply_patch",
        "project.verify",
    ]


def test_tool_schemas_read_file_has_required_path(tmp_path: Path) -> None:
    """read_file schema가 path를 required로 선언하는지 검증한다.

    최종 수정일: 2026-08-04
    """
    schemas = {s.name: s for s in _registry(tmp_path).tool_schemas()}

    read_file = schemas["workspace.read_file"]
    assert "path" in read_file.parameters["required"]
    assert read_file.parameters["properties"]["path"]["type"] == "string"


def test_tool_schemas_verify_has_enum_template(tmp_path: Path) -> None:
    """project.verify schema가 template enum을 포함하는지 검증한다.

    최종 수정일: 2026-08-04
    """
    schemas = {s.name: s for s in _registry(tmp_path).tool_schemas()}

    verify = schemas["project.verify"]
    assert verify.parameters["properties"]["template"]["enum"] == ["pytest", "ruff", "mypy"]
    assert "template" in verify.parameters["required"]


def test_tool_schemas_git_status_has_no_required(tmp_path: Path) -> None:
    """인자 없는 git.status schema에 required가 없는지 검증한다.

    최종 수정일: 2026-08-04
    """
    schemas = {s.name: s for s in _registry(tmp_path).tool_schemas()}

    git_status = schemas["git.status"]
    assert "required" not in git_status.parameters


# --- apply_patch handler 테스트 ---


class _AllowMutationPolicy:
    """테스트용으로 WORKSPACE_MUTATION을 허용하는 권한 정책.

    최종 수정일: 2026-08-05
    """

    def authorize(self, invocation: object, definition: object) -> bool:
        return True


def _make_invocation(patch_text: str) -> ToolInvocation:
    """apply_patch 테스트용 ToolInvocation을 만든다.

    최종 수정일: 2026-08-05
    """
    return ToolInvocation(
        tool_name="workspace.apply_patch",
        arguments={"patch": patch_text},
        session_id="ses_test",
        step_id="step_patch",
        attempt=0,
    )


def test_apply_patch_modifies_existing_file(tmp_path: Path) -> None:
    """정상 unified diff가 기존 파일 내용을 수정하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    (tmp_path / "hello.txt").write_text("hello world\n", encoding="utf-8")
    registry = _registry(tmp_path)

    patch = """\
--- a/hello.txt
+++ b/hello.txt
@@ -1 +1 @@
-hello world
+hello forge
"""
    result = registry.execute(_make_invocation(patch))

    assert result.status is ToolStatus.COMPLETED
    assert result.output["patched_files"] == ["hello.txt"]
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello forge\n"


def test_apply_patch_creates_new_file(tmp_path: Path) -> None:
    """--- /dev/null 헤더로 새 파일을 생성하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    registry = _registry(tmp_path)

    patch = """\
--- /dev/null
+++ b/new_file.txt
@@ -0,0 +1,2 @@
+line one
+line two
"""
    result = registry.execute(_make_invocation(patch))

    assert result.status is ToolStatus.COMPLETED
    assert "new_file.txt" in result.output["patched_files"]
    assert (tmp_path / "new_file.txt").read_text(encoding="utf-8") == "line one\nline two\n"


def test_apply_patch_rejects_path_outside_workspace(tmp_path: Path) -> None:
    """workspace 외부 경로를 대상으로 하는 patch가 거부되는지 검증한다.

    최종 수정일: 2026-08-05
    """
    registry = _registry(tmp_path)

    patch = """\
--- a/../outside.txt
+++ b/../outside.txt
@@ -1 +1 @@
-old
+new
"""
    result = registry.execute(_make_invocation(patch))

    assert result.status is ToolStatus.FAILED
    assert result.safe_error_code == "tool.path_outside_workspace"


def test_apply_patch_rejects_invalid_format(tmp_path: Path) -> None:
    """unified diff 형식이 아닌 patch가 거부되는지 검증한다.

    최종 수정일: 2026-08-05
    """
    registry = _registry(tmp_path)

    result = registry.execute(_make_invocation("this is not a patch"))

    assert result.status is ToolStatus.FAILED
    assert result.safe_error_code == "tool.invalid_patch_format"


def test_apply_patch_rejects_context_mismatch(tmp_path: Path) -> None:
    """원본과 context 라인이 다르면 patch가 실패하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    (tmp_path / "mismatch.txt").write_text("actual content\n", encoding="utf-8")
    registry = _registry(tmp_path)

    patch = """\
--- a/mismatch.txt
+++ b/mismatch.txt
@@ -1 +1 @@
-expected content
+new content
"""
    result = registry.execute(_make_invocation(patch))

    assert result.status is ToolStatus.FAILED
    assert result.safe_error_code == "tool.patch_context_mismatch"
    # 원본이 변경되지 않았는지 확인
    assert (tmp_path / "mismatch.txt").read_text(encoding="utf-8") == "actual content\n"


def test_apply_patch_multiple_files(tmp_path: Path) -> None:
    """하나의 patch로 여러 파일을 동시에 수정하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    (tmp_path / "a.txt").write_text("aaa\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("bbb\n", encoding="utf-8")
    registry = _registry(tmp_path)

    patch = """\
--- a/a.txt
+++ b/a.txt
@@ -1 +1 @@
-aaa
+AAA
--- a/b.txt
+++ b/b.txt
@@ -1 +1 @@
-bbb
+BBB
"""
    result = registry.execute(_make_invocation(patch))

    assert result.status is ToolStatus.COMPLETED
    assert set(result.output["patched_files"]) == {"a.txt", "b.txt"}
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "AAA\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "BBB\n"


def test_apply_patch_through_executor_with_mutation_grant(tmp_path: Path) -> None:
    """mutation 허용 정책에서 executor가 patch를 정상 실행하는지 검증한다.

    최종 수정일: 2026-08-05
    """
    (tmp_path / "target.txt").write_text("old line\n", encoding="utf-8")
    registry = _registry(tmp_path)
    executor = RegistryPlanStepExecutor(registry, _AllowMutationPolicy())

    patch = """\
--- a/target.txt
+++ b/target.txt
@@ -1 +1 @@
-old line
+new line
"""
    execution = executor.execute(
        PlanStep("patch", "Patch target", "workspace.apply_patch", {"patch": patch}),
        session_id="ses_test",
    )

    assert execution.outcome is ExecutionOutcome.COMPLETED
    assert (tmp_path / "target.txt").read_text(encoding="utf-8") == "new line\n"
