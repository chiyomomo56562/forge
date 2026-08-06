"""Project verification LangChain tool."""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.tools import BaseTool, tool
from pydantic import Field

from forge.ports.outbound import ToolAuthorizationPolicy

from ._langchain import invoke_registered_tool
from .builtin import BuiltinToolRegistry


def project_tools(
    registry: BuiltinToolRegistry, authorization: ToolAuthorizationPolicy
) -> tuple[BaseTool, ...]:
    @tool(
        "project.verify", description="Run a fixed verification template (pytest, ruff, or mypy)."
    )
    def verify(
        template: Annotated[
            Literal["pytest", "ruff", "mypy"], Field(description="Verification template to run.")
        ],
    ) -> str:
        return invoke_registered_tool(
            registry, authorization, "project.verify", {"template": template}
        )

    return (verify,)
