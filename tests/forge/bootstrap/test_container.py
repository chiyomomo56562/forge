"""Composition tests for the shared LangChain tool collection."""

from pathlib import Path

from forge.adapters.outbound.tools import (
    BuiltinToolRegistry,
    StaticToolAuthorizationPolicy,
    build_langchain_tools,
)


def test_shared_collection_contains_the_seven_decorated_tools(tmp_path: Path) -> None:
    tools = build_langchain_tools(BuiltinToolRegistry(tmp_path), StaticToolAuthorizationPolicy())

    assert [tool.name for tool in tools] == [
        "workspace.list_files",
        "workspace.read_file",
        "workspace.search_text",
        "git.status",
        "git.diff",
        "workspace.apply_patch",
        "project.verify",
    ]
