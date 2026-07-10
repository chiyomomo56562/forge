"""Unit tests for Phase 2.3 — Tools system.

Covers:
    - base.py: Tool abstract class, ToolResult, ToolContext, ToolParameter
    - registry.py: registration, lookup, policy enforcement, skill adapter
    - builtin/file_io.py: FileReadTool, FileWriteTool, FileDeleteTool
    - builtin/search.py: WebSearchTool (mock backend)
    - builtin/code_exec.py: CodeExecTool (inline sandbox)
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# base.py
# ===========================================================================

class TestToolBase:
    def test_tool_result_str(self):
        from agent.tools.base import ToolResult

        ok = ToolResult(success=True, output="hello")
        err = ToolResult(success=False, error="bad")
        assert "[OK]" in str(ok)
        assert "[ERROR]" in str(err)

    def test_tool_parameter_defaults(self):
        from agent.tools.base import ToolParameter

        p = ToolParameter(name="x")
        assert p.type == "string"
        assert p.required is True
        assert p.default is None

    def test_tool_context_defaults(self):
        from agent.tools.base import ToolContext

        ctx = ToolContext()
        assert ctx.session_id == ""
        assert ctx.sandbox is True
        assert ctx.user_confirm is None

    def test_tool_to_dict(self):
        from agent.tools.base import Tool, ToolClass, ToolParameter

        class DummyTool(Tool):
            name = "dummy"
            description = "A dummy tool."
            parameters = [ToolParameter(name="x", type="int")]

            def execute(self, args, context=None):
                from agent.tools.base import ToolResult
                return ToolResult(success=True, output="ok")

        d = DummyTool().to_dict()
        assert d["name"] == "dummy"
        assert d["description"] == "A dummy tool."
        assert d["tool_class"] == ToolClass.AUTONOMOUS
        assert len(d["parameters"]) == 1
        assert d["parameters"][0]["name"] == "x"

    def test_safe_execute_catches_exception(self):
        from agent.tools.base import Tool, ToolResult

        class CrashTool(Tool):
            name = "crash"

            def execute(self, args, context=None):
                raise RuntimeError("boom")

        result = CrashTool().safe_execute({})
        assert result.success is False
        assert "RuntimeError" in result.error
        assert "duration_seconds" in result.metadata


# ===========================================================================
# registry.py
# ===========================================================================

class TestToolRegistry:
    def test_register_and_get(self):
        from agent.tools.base import Tool, ToolResult
        from agent.tools.registry import ToolRegistry

        class FooTool(Tool):
            name = "foo"
            description = "Foo tool"

            def execute(self, args, context=None):
                return ToolResult(success=True, output="foo")

        registry = ToolRegistry(policy_path="nonexistent.yml")
        registry.register(FooTool())
        assert registry.get("foo") is not None
        assert "foo" in registry.list_names()
        assert len(registry.list_tools()) == 1

    def test_register_duplicate_raises(self):
        from agent.tools.base import Tool, ToolResult
        from agent.tools.registry import ToolRegistry

        class BarTool(Tool):
            name = "bar"

            def execute(self, args, context=None):
                return ToolResult(success=True)

        registry = ToolRegistry(policy_path="nonexistent.yml")
        registry.register(BarTool())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(BarTool())

    def test_unregister(self):
        from agent.tools.base import Tool, ToolResult
        from agent.tools.registry import ToolRegistry

        class BazTool(Tool):
            name = "baz"

            def execute(self, args, context=None):
                return ToolResult(success=True)

        registry = ToolRegistry(policy_path="nonexistent.yml")
        registry.register(BazTool())
        assert registry.unregister("baz") is True
        assert registry.get("baz") is None
        assert registry.unregister("baz") is False

    def test_execute_unknown_tool(self):
        from agent.tools.registry import ToolRegistry

        registry = ToolRegistry(policy_path="nonexistent.yml")
        result = registry.execute("nonexistent", {})
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_list_for_planner(self):
        from agent.tools.base import Tool, ToolResult
        from agent.tools.registry import ToolRegistry

        class PlanTool(Tool):
            name = "plan_tool"
            description = "Planning tool"

            def execute(self, args, context=None):
                return ToolResult(success=True)

        registry = ToolRegistry(policy_path="nonexistent.yml")
        registry.register(PlanTool())
        tools = registry.list_for_planner()
        assert len(tools) == 1
        assert tools[0]["name"] == "plan_tool"

    def test_policy_loads_confirmation_and_forbidden(self):
        from agent.tools.registry import ToolRegistry

        registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        assert "deleting_files" in registry._confirmation_keys
        assert "exec_unsandboxed" in registry._forbidden_ids

    def test_forbidden_tool_not_executed(self):
        from agent.tools.base import Tool, ToolResult
        from agent.tools.registry import ToolRegistry

        class ForbiddenTool(Tool):
            name = "exec_unsandboxed"
            description = "Should be forbidden"
            tool_class = "forbidden"

            def execute(self, args, context=None):
                return ToolResult(success=True, output="should not see this")

        registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        # register should skip forbidden tools
        registry.register(ForbiddenTool())
        assert registry.get("exec_unsandboxed") is None

    def test_confirmation_required_blocks_without_callback(self):
        from agent.tools.base import Tool, ToolResult
        from agent.tools.registry import ToolRegistry

        class WriteTool(Tool):
            name = "file_write"
            description = "Write a file"
            tool_class = "confirmation_required"

            def execute(self, args, context=None):
                return ToolResult(success=True, output="written")

        registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        registry.register(WriteTool())

        # No context → blocked
        result = registry.execute("file_write", {"path": "test.txt", "content": "x"})
        assert result.success is False
        assert "confirmation" in result.error.lower()

    def test_confirmation_required_with_user_approve(self):
        from agent.tools.base import Tool, ToolResult, ToolContext
        from agent.tools.registry import ToolRegistry

        class WriteTool(Tool):
            name = "file_write"
            description = "Write a file"
            tool_class = "confirmation_required"

            def execute(self, args, context=None):
                return ToolResult(success=True, output="written")

        registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        registry.register(WriteTool())

        ctx = ToolContext(user_confirm=lambda tid, desc: True)
        result = registry.execute("file_write", {"path": "test.txt", "content": "x"}, ctx)
        assert result.success is True

    def test_confirmation_required_with_user_deny(self):
        from agent.tools.base import Tool, ToolResult, ToolContext
        from agent.tools.registry import ToolRegistry

        class WriteTool(Tool):
            name = "file_write"
            description = "Write a file"
            tool_class = "confirmation_required"

            def execute(self, args, context=None):
                return ToolResult(success=True, output="written")

        registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        registry.register(WriteTool())

        ctx = ToolContext(user_confirm=lambda tid, desc: False)
        result = registry.execute("file_write", {"path": "test.txt", "content": "x"}, ctx)
        assert result.success is False
        assert "denied" in result.error.lower()


# ===========================================================================
# builtin/file_io.py
# ===========================================================================

class TestFileReadTool:
    def test_read_existing_file(self, tmp_path):
        from agent.tools.builtin.file_io import FileReadTool
        from agent.tools.base import ToolContext

        f = tmp_path / "test.txt"
        f.write_text("hello world")

        tool = FileReadTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute({"path": str(f)}, ctx)
        assert result.success is True
        assert result.output == "hello world"

    def test_read_nonexistent_file(self, tmp_path):
        from agent.tools.builtin.file_io import FileReadTool
        from agent.tools.base import ToolContext

        tool = FileReadTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute({"path": str(tmp_path / "nope.txt")}, ctx)
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_read_missing_path_param(self):
        from agent.tools.builtin.file_io import FileReadTool

        tool = FileReadTool()
        result = tool.safe_execute({})
        assert result.success is False

    def test_read_with_max_bytes(self, tmp_path):
        from agent.tools.builtin.file_io import FileReadTool
        from agent.tools.base import ToolContext

        f = tmp_path / "test.txt"
        f.write_text("abcdefghij")

        tool = FileReadTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute({"path": str(f), "max_bytes": 5}, ctx)
        assert result.success is True
        assert result.output == "abcde"


class TestFileWriteTool:
    def test_write_new_file(self, tmp_path):
        from agent.tools.builtin.file_io import FileWriteTool
        from agent.tools.base import ToolContext

        tool = FileWriteTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute(
            {"path": str(tmp_path / "out.txt"), "content": "test content"}, ctx
        )
        assert result.success is True
        assert (tmp_path / "out.txt").read_text() == "test content"

    def test_write_append(self, tmp_path):
        from agent.tools.builtin.file_io import FileWriteTool
        from agent.tools.base import ToolContext

        f = tmp_path / "out.txt"
        f.write_text("line1\n")

        tool = FileWriteTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute(
            {"path": str(f), "content": "line2\n", "append": True}, ctx
        )
        assert result.success is True
        assert f.read_text() == "line1\nline2\n"

    def test_write_creates_parent_dirs(self, tmp_path):
        from agent.tools.builtin.file_io import FileWriteTool
        from agent.tools.base import ToolContext

        tool = FileWriteTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute(
            {"path": str(tmp_path / "sub" / "dir" / "out.txt"), "content": "x"}, ctx
        )
        assert result.success is True
        assert (tmp_path / "sub" / "dir" / "out.txt").exists()


class TestFileDeleteTool:
    def test_delete_file(self, tmp_path):
        from agent.tools.builtin.file_io import FileDeleteTool
        from agent.tools.base import ToolContext

        f = tmp_path / "to_delete.txt"
        f.write_text("bye")

        tool = FileDeleteTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute({"path": str(f)}, ctx)
        assert result.success is True
        assert not f.exists()

    def test_delete_nonexistent(self, tmp_path):
        from agent.tools.builtin.file_io import FileDeleteTool
        from agent.tools.base import ToolContext

        tool = FileDeleteTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute({"path": str(tmp_path / "nope.txt")}, ctx)
        assert result.success is False

    def test_delete_directory_rejected(self, tmp_path):
        from agent.tools.builtin.file_io import FileDeleteTool
        from agent.tools.base import ToolContext

        d = tmp_path / "subdir"
        d.mkdir()

        tool = FileDeleteTool()
        ctx = ToolContext(working_dir=str(tmp_path), sandbox=False)
        result = tool.safe_execute({"path": str(d)}, ctx)
        assert result.success is False
        assert "directory" in result.error.lower()


# ===========================================================================
# builtin/search.py
# ===========================================================================

class TestWebSearchTool:
    def test_mock_search(self):
        from agent.tools.builtin.search import WebSearchTool

        tool = WebSearchTool(backend="mock")
        result = tool.safe_execute({"query": "python tutorial", "max_results": 3})
        assert result.success is True
        assert isinstance(result.output, list)
        assert len(result.output) == 3
        assert "title" in result.output[0]
        assert "url" in result.output[0]
        assert "snippet" in result.output[0]

    def test_missing_query(self):
        from agent.tools.builtin.search import WebSearchTool

        tool = WebSearchTool(backend="mock")
        result = tool.safe_execute({})
        assert result.success is False
        assert "query" in result.error.lower()

    def test_rate_limit(self):
        from agent.tools.builtin.search import WebSearchTool

        tool = WebSearchTool(backend="mock", rate_limit_per_min=2)
        r1 = tool.safe_execute({"query": "a"})
        r2 = tool.safe_execute({"query": "b"})
        r3 = tool.safe_execute({"query": "c"})
        assert r1.success is True
        assert r2.success is True
        assert r3.success is False
        assert "rate limit" in r3.error.lower()


# ===========================================================================
# builtin/code_exec.py
# ===========================================================================

class TestCodeExecTool:
    def test_simple_print(self):
        from agent.tools.builtin.code_exec import CodeExecTool

        tool = CodeExecTool()
        result = tool.safe_execute({"code": "print('hello sandbox')"})
        assert result.success is True
        assert "hello sandbox" in result.output

    def test_math_computation(self):
        from agent.tools.builtin.code_exec import CodeExecTool

        tool = CodeExecTool()
        result = tool.safe_execute({"code": "import math\nresult = math.sqrt(16)\nprint(result)"})
        assert result.success is True
        assert "4" in result.output

    def test_missing_code_param(self):
        from agent.tools.builtin.code_exec import CodeExecTool

        tool = CodeExecTool()
        result = tool.safe_execute({})
        assert result.success is False
        assert "code" in result.error.lower()

    def test_syntax_error_captured(self):
        from agent.tools.builtin.code_exec import CodeExecTool

        tool = CodeExecTool()
        result = tool.safe_execute({"code": "this is not valid python"})
        assert result.success is False

    def test_rate_limit(self):
        from agent.tools.builtin.code_exec import CodeExecTool

        tool = CodeExecTool(rate_limit_per_min=2)
        r1 = tool.safe_execute({"code": "print(1)"})
        r2 = tool.safe_execute({"code": "print(2)"})
        r3 = tool.safe_execute({"code": "print(3)"})
        assert r1.success is True
        assert r2.success is True
        assert r3.success is False
        assert "rate limit" in r3.error.lower()


# ===========================================================================
# Integration: register_builtin
# ===========================================================================

class TestRegisterBuiltin:
    def test_register_builtin_tools(self):
        from agent.tools.registry import ToolRegistry

        registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        registry.register_builtin()

        names = registry.list_names()
        assert "file_read" in names
        assert "web_search" in names
        assert "code_exec_sandboxed" in names

        # file_write should be confirmation_required
        write_tool = registry.get("file_write")
        assert write_tool is not None
        assert write_tool.requires_confirmation() is True

        # file_read should be autonomous
        read_tool = registry.get("file_read")
        assert read_tool is not None
        assert read_tool.requires_confirmation() is False

    def test_full_flow_read_write(self, tmp_path):
        from agent.tools.registry import ToolRegistry
        from agent.tools.base import ToolContext

        registry = ToolRegistry(
            policy_path=str(PROJECT_ROOT / "constitution" / "tool_policy.yml")
        )
        registry.register_builtin()

        ctx = ToolContext(
            working_dir=str(tmp_path),
            sandbox=False,
            user_confirm=lambda tid, desc: True,
        )

        # Write
        write_result = registry.execute(
            "file_write",
            {"path": str(tmp_path / "flow.txt"), "content": "integration test"},
            ctx,
        )
        assert write_result.success is True

        # Read
        read_result = registry.execute(
            "file_read",
            {"path": str(tmp_path / "flow.txt")},
            ctx,
        )
        assert read_result.success is True
        assert read_result.output == "integration test"