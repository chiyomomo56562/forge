"""Tools package — base abstractions, registry, and builtin tools.

Public API::

    from agent.tools import (
        Tool, ToolResult, ToolContext, ToolParameter, ToolClass,
        ToolRegistry, SkillToolAdapter,
        FileReadTool, FileWriteTool, FileDeleteTool,
        WebSearchTool, CodeExecTool,
    )
"""

from .base import Tool, ToolClass, ToolParameter, ToolResult, ToolContext
from .registry import ToolRegistry, SkillToolAdapter
from .builtin import (
    FileReadTool,
    FileWriteTool,
    FileDeleteTool,
    WebSearchTool,
    CodeExecTool,
)

__all__ = [
    "Tool",
    "ToolClass",
    "ToolParameter",
    "ToolResult",
    "ToolContext",
    "ToolRegistry",
    "SkillToolAdapter",
    "FileReadTool",
    "FileWriteTool",
    "FileDeleteTool",
    "WebSearchTool",
    "CodeExecTool",
]