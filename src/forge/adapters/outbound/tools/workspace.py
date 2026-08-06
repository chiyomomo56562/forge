"""Workspace-scoped LangChain tools."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from forge.ports.outbound import ToolAuthorizationPolicy

from ._langchain import invoke_registered_tool
from .builtin import BuiltinToolRegistry


def workspace_tools(
    registry: BuiltinToolRegistry, authorization: ToolAuthorizationPolicy
) -> tuple[BaseTool, ...]:
    @tool(
        "workspace.list_files",
        description="List files under a workspace path matching a glob pattern.",
    )
    def list_files(
        path: Annotated[
            str | None, Field(description="Relative directory path from workspace root.")
        ] = None,
        glob: Annotated[str | None, Field(description="Glob pattern to match.")] = None,
    ) -> str:
        return invoke_registered_tool(
            registry,
            authorization,
            "workspace.list_files",
            {k: v for k, v in {"path": path, "glob": glob}.items() if v is not None},
        )

    @tool(
        "workspace.read_file",
        description="Read a UTF-8 text file within the workspace up to a byte limit.",
    )
    def read_file(
        path: Annotated[str, Field(description="Relative file path from workspace root.")],
        offset: Annotated[
            int | None, Field(description="Byte offset to start reading from.")
        ] = None,
        max_bytes: Annotated[int | None, Field(description="Maximum bytes to read.")] = None,
    ) -> str:
        return invoke_registered_tool(
            registry,
            authorization,
            "workspace.read_file",
            {
                k: v
                for k, v in {"path": path, "offset": offset, "max_bytes": max_bytes}.items()
                if v is not None
            },
        )

    @tool(
        "workspace.search_text", description="Search for a literal text string in workspace files."
    )
    def search_text(
        query: Annotated[str, Field(description="Literal text to search for.")],
        path: Annotated[str | None, Field(description="Relative directory to search in.")] = None,
    ) -> str:
        return invoke_registered_tool(
            registry,
            authorization,
            "workspace.search_text",
            {k: v for k, v in {"query": query, "path": path}.items() if v is not None},
        )

    @tool("workspace.apply_patch", description="Apply a unified diff patch to workspace files.")
    def apply_patch(
        patch: Annotated[str, Field(description="Unified diff patch text to apply.")],
    ) -> str:
        return invoke_registered_tool(
            registry, authorization, "workspace.apply_patch", {"patch": patch}
        )

    return list_files, read_file, search_text, apply_patch
