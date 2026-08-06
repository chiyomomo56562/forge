"""The single LangChain tool collection used by every execution path."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.tools import BaseTool

from forge.ports.outbound import ToolAuthorizationPolicy

from .builtin import BuiltinToolRegistry
from .git import git_tools
from .project import project_tools
from .workspace import workspace_tools


def build_langchain_tools(
    registry: BuiltinToolRegistry, authorization: ToolAuthorizationPolicy
) -> Sequence[BaseTool]:
    """Build the seven decorated tools in their stable registration order."""
    workspace = workspace_tools(registry, authorization)
    return (
        *workspace[:3],
        *git_tools(registry, authorization),
        workspace[3],
        *project_tools(registry, authorization),
    )
