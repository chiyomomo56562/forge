"""Read-only Git LangChain tools."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from forge.ports.outbound import ToolAuthorizationPolicy

from ._langchain import invoke_registered_tool
from .builtin import BuiltinToolRegistry


def git_tools(
    registry: BuiltinToolRegistry, authorization: ToolAuthorizationPolicy
) -> tuple[BaseTool, ...]:
    @tool("git.status", description="Show short Git working-tree status.")
    def status() -> str:
        return invoke_registered_tool(registry, authorization, "git.status", {})

    @tool("git.diff", description="Show Git diff without external diff or pager.")
    def diff() -> str:
        return invoke_registered_tool(registry, authorization, "git.diff", {})

    return status, diff
