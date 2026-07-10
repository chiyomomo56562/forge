"""Tool registry — central catalogue for all agent tools.

Manages tool registration, lookup, and execution dispatch.  Integrates
with two external systems:

    1. **L3 Procedural Memory** (:class:`SkillLoader`) — registered skills
       are exposed as tools via :class:`SkillToolAdapter`.
    2. **L4 Constitution** (``tool_policy.yml``) — tool classification
       (autonomous / confirmation_required / forbidden) is loaded from
       the policy file and enforced at execution time.

Usage::

    from agent.tools import ToolRegistry

    registry = ToolRegistry()
    registry.register_builtin()          # register file_io, search, code_exec
    registry.register_skills(loader)     # register L3 skills as tools

    tool = registry.get("file_read")
    result = tool.safe_execute({"path": "README.md"}, ctx)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..utils.logging import get_logger
from .base import Tool, ToolClass, ToolContext, ToolResult, ToolParameter

logger = get_logger("agent.tools.registry")


# ===========================================================================
# Skill → Tool adapter
# ===========================================================================

class SkillToolAdapter(Tool):
    """Wraps an L3 Skill as a :class:`Tool` so the registry can dispatch it.

    The adapter delegates execution to the :class:`SkillExecutor` from
    L3 procedural memory.  The skill's ``reflection_hints`` and
    ``causal_conditions`` are exposed as part of the tool description so
    the planner has context.
    """

    def __init__(self, skill: Any, executor: Any | None = None):
        """Create an adapter for a skill.

        Args:
            skill: A :class:`Skill` object (from L3 procedural memory).
            executor: A :class:`SkillExecutor` instance. If ``None``,
                a new one is created.
        """
        self._skill = skill
        self._executor = executor

        # Import here to avoid circular dependency at module load time
        if self._executor is None:
            from ..memory.procedural.skill_executor import SkillExecutor
            self._executor = SkillExecutor()

        # Build description from skill metadata
        hints_text = ""
        if skill.reflection_hints:
            hints_text = "\n\nHints:\n" + "\n".join(f"- {h}" for h in skill.reflection_hints)

        self.name = skill.skill_id
        self.description = skill.description or skill.name or skill.skill_id
        self.description += hints_text
        self.tool_class = ToolClass.AUTONOMOUS
        self.parameters = [
            ToolParameter(
                name="input",
                type="string",
                description="Input argument for the skill's execute function.",
                required=False,
                default="",
            ),
        ]

    def execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        """Execute the wrapped skill via :class:`SkillExecutor`."""
        input_value = args.get("input", "")
        result = self._executor.execute(self._skill, input_value=input_value)

        if result.success:
            return ToolResult(
                success=True,
                output=result.output,
                metadata={
                    "skill_id": self._skill.skill_id,
                    "result_value": result.result_value,
                    "timed_out": result.timed_out,
                },
            )
        return ToolResult(
            success=False,
            error=result.error or result.traceback_str or "Skill execution failed",
            metadata={
                "skill_id": self._skill.skill_id,
                "timed_out": result.timed_out,
            },
        )


# ===========================================================================
# Tool registry
# ===========================================================================

class ToolRegistry:
    """Central registry for all tools (builtin + L3 skill-backed).

    Args:
        policy_path: Path to ``tool_policy.yml``. If the file exists,
            tool classifications are loaded from it.
    """

    def __init__(self, policy_path: str = "constitution/tool_policy.yml"):
        self._tools: dict[str, Tool] = {}
        self._policy: dict[str, Any] = {}
        self._confirmation_keys: set[str] = set()
        self._forbidden_ids: set[str] = set()
        self._policy_path = policy_path

        self._load_policy()

    # ------------------------------------------------------------------
    # Policy loading
    # ------------------------------------------------------------------

    def _load_policy(self) -> None:
        """Load tool_policy.yml and build lookup sets."""
        path = Path(self._policy_path)
        if not path.exists():
            logger.warning(f"Tool policy not found at {path}, using defaults")
            return

        with path.open("r", encoding="utf-8") as f:
            self._policy = yaml.safe_load(f) or {}

        # Confirmation-required keys
        self._confirmation_keys = set(
            self._policy.get("require_confirmation_for", [])
        )

        # Forbidden tool IDs
        forbidden = self._policy.get("tool_classes", {}).get("forbidden", [])
        self._forbidden_ids = {item["id"] for item in forbidden if "id" in item}

        logger.info(
            f"Loaded tool policy: {len(self._confirmation_keys)} confirmation keys, "
            f"{len(self._forbidden_ids)} forbidden tools"
        )

    def _resolve_tool_class(self, tool_id: str) -> str:
        """Determine the tool class from policy for a given tool ID.

        Checks the policy's tool_classes section to find the classification
        (autonomous, confirmation_required, forbidden).
        """
        if tool_id in self._forbidden_ids:
            return ToolClass.FORBIDDEN

        tool_classes = self._policy.get("tool_classes", {})

        # Check confirmation_required
        for item in tool_classes.get("confirmation_required", []):
            if item.get("id") == tool_id:
                return ToolClass.CONFIRMATION_REQUIRED

        # Check autonomous
        for item in tool_classes.get("autonomous", []):
            if item.get("id") == tool_id:
                return ToolClass.AUTONOMOUS

        # Default: autonomous
        return ToolClass.AUTONOMOUS

    def _get_confirmation_key(self, tool_id: str) -> str | None:
        """Get the confirmation_key for a confirmation_required tool.

        Returns ``None`` if the tool is not in the confirmation_required list.
        """
        tool_classes = self._policy.get("tool_classes", {})
        for item in tool_classes.get("confirmation_required", []):
            if item.get("id") == tool_id:
                return item.get("confirmation_key")
        return None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Register a tool instance.

        Args:
            tool: A :class:`Tool` instance.

        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        name = tool.get_name()
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")

        # Apply policy classification if available
        policy_class = self._resolve_tool_class(name)
        if policy_class == ToolClass.FORBIDDEN:
            logger.warning(f"Tool {name} is forbidden by policy, skipping registration")
            return
        tool.tool_class = policy_class

        self._tools[name] = tool
        logger.info(f"Registered tool: {name} (class={tool.tool_class})")

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry.

        Returns:
            ``True`` if the tool was found and removed.
        """
        if name in self._tools:
            del self._tools[name]
            logger.info(f"Unregistered tool: {name}")
            return True
        return False

    def register_builtin(self) -> None:
        """Register all builtin tools (file_io, search, code_exec)."""
        from .builtin.file_io import FileReadTool, FileWriteTool, FileDeleteTool
        from .builtin.search import WebSearchTool
        from .builtin.code_exec import CodeExecTool

        builtins = [
            FileReadTool(),
            FileWriteTool(),
            FileDeleteTool(),
            WebSearchTool(),
            CodeExecTool(),
        ]

        for tool in builtins:
            try:
                self.register(tool)
            except ValueError as e:
                logger.warning(f"Failed to register builtin {tool.get_name()}: {e}")

    def register_skills(self, skill_loader: Any) -> None:
        """Register all active L3 skills as tools.

        Args:
            skill_loader: A :class:`SkillLoader` instance.  Only skills
                with status ``Active`` (or ``Seed``) are registered.
        """
        try:
            skills = skill_loader.store.list_active()
        except Exception as e:
            logger.warning(f"Failed to list active skills: {e}")
            return

        for skill in skills:
            adapter = SkillToolAdapter(skill)
            try:
                self.register(adapter)
            except ValueError as e:
                logger.warning(f"Failed to register skill {skill.skill_id}: {e}")

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, name: str) -> Tool | None:
        """Look up a tool by name.

        Returns:
            The :class:`Tool` instance, or ``None`` if not found.
        """
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def list_for_planner(self) -> list[dict[str, Any]]:
        """Return tool metadata in a format the planner can consume.

        Returns:
            List of tool dicts (name, description, tool_class, parameters).
        """
        return [tool.to_dict() for tool in self._tools.values()]

    # ------------------------------------------------------------------
    # Execution dispatch
    # ------------------------------------------------------------------

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        context: ToolContext | None = None,
    ) -> ToolResult:
        """Execute a tool by name with policy enforcement.

        Enforces:
            1. Forbidden tools are never executed.
            2. Confirmation-required tools prompt the user (via
               ``context.user_confirm``) before execution.
            3. Unknown tools return an error result.

        Args:
            name: Tool name.
            args: Tool arguments.
            context: Runtime context.

        Returns:
            :class:`ToolResult`.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Tool not found: {name}",
                metadata={"tool": name},
            )

        # Forbidden check
        if tool.is_forbidden():
            logger.error(f"Attempted to execute forbidden tool: {name}")
            return ToolResult(
                success=False,
                error=f"Tool '{name}' is forbidden by policy",
                metadata={"tool": name},
            )

        # Confirmation check
        if tool.requires_confirmation():
            if context and context.user_confirm:
                confirm_key = self._get_confirmation_key(name)
                description = tool.get_description()
                prompt_desc = f"[{confirm_key}] {description}" if confirm_key else description
                approved = context.user_confirm(name, prompt_desc)
                if not approved:
                    logger.info(f"User denied execution of tool: {name}")
                    return ToolResult(
                        success=False,
                        error=f"User denied execution of tool '{name}'",
                        metadata={"tool": name, "denied": True},
                    )
            else:
                logger.warning(
                    f"Tool '{name}' requires confirmation but no user_confirm "
                    f"callback provided — blocking execution"
                )
                return ToolResult(
                    success=False,
                    error=f"Tool '{name}' requires user confirmation but no callback available",
                    metadata={"tool": name, "no_callback": True},
                )

        return tool.safe_execute(args, context)