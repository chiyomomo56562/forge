"""Planner-only LangChain tool used to select a reviewed L3 skill."""

from typing import Annotated

from langchain_core.tools import BaseTool, tool
from pydantic import Field


def build_procedural_selection_tool() -> BaseTool:
    """Expose explicit L3 selection to the native planner.

    The callable is never executed directly: ``RunInnerLoopService`` intercepts
    ``l3.execute`` and delegates it to ``SkillExecutor`` after checking that the
    selected ID was supplied by the vetted memory context.
    """

    @tool(  # type: ignore[untyped-decorator]
        "l3.execute",
        description=(
            "Execute one reviewed procedural skill. Use only a skill_id listed in "
            "the vetted l3_skill_ids memory context."
        ),
    )
    def execute_procedural_skill(
        skill_id: Annotated[str, Field(description="The selected active L3 skill ID.")],
    ) -> str:
        return f"L3 skill {skill_id} is selected for Inner Loop execution."

    return execute_procedural_skill
