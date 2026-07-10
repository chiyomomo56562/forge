"""Base tool abstractions for the Forge agent framework.

Defines the core interfaces that every tool must implement:

    - :class:`Tool` — abstract base class (name, description, execute)
    - :class:`ToolResult` — standardised execution result
    - :class:`ToolContext` — runtime context passed to tools (session, working dir, etc.)
    - :class:`ToolParameter` — parameter schema for tool self-description

All builtin tools and L3 skill-backed tools conform to the :class:`Tool`
interface so the registry and orchestrator can treat them uniformly.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field  # field used by ToolResult/ToolContext
from typing import Any, Callable

from ..utils.logging import get_logger

logger = get_logger("agent.tools.base")


# ===========================================================================
# Tool classification (mirrors constitution/tool_policy.yml)
# ===========================================================================

class ToolClass:
    """Tool classification categories from tool_policy.yml.

    - ``autonomous`` — no user confirmation needed
    - ``confirmation_required`` — user must approve before execution
    - ``forbidden`` — never allowed
    """

    AUTONOMOUS = "autonomous"
    CONFIRMATION_REQUIRED = "confirmation_required"
    FORBIDDEN = "forbidden"


# ===========================================================================
# Parameter schema
# ===========================================================================

@dataclass
class ToolParameter:
    """A single parameter for a tool's self-describing schema.

    Used by the planner to understand what arguments a tool accepts.
    """
    name: str
    type: str = "string"  # string | int | float | bool | list | dict
    description: str = ""
    required: bool = True
    default: Any = None
    choices: list[Any] | None = None


# ===========================================================================
# Execution result
# ===========================================================================

@dataclass
class ToolResult:
    """Standardised result returned by every tool execution.

    Attributes:
        success: Whether the execution succeeded.
        output: Primary output (string or structured data).
        error: Error message if ``success`` is ``False``.
        metadata: Additional metadata (duration, tool-specific info).
    """
    success: bool
    output: Any = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.success:
            return f"[OK] {self.output}"
        return f"[ERROR] {self.error}"


# ===========================================================================
# Execution context
# ===========================================================================

@dataclass
class ToolContext:
    """Runtime context passed to tools during execution.

    Provides access to session-scoped resources without requiring tools
    to import global state.

    Attributes:
        session_id: Current inner-loop session ID.
        working_dir: Working directory for file operations.
        sandbox: Whether to enforce sandbox restrictions.
        user_confirm: Optional callback for user confirmation (HITL).
            Called as ``user_confirm(tool_id, description) -> bool``.
        extra: Arbitrary tool-specific context.
    """
    session_id: str = ""
    working_dir: str = "."
    sandbox: bool = True
    user_confirm: Callable[[str, str], bool] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ===========================================================================
# Abstract tool base
# ===========================================================================

class Tool(ABC):
    """Abstract base class for all tools.

    Subclasses must implement :meth:`execute`.  They should also set the
    class attributes ``name`` and ``description`` (or override
    :meth:`get_name` / :meth:`get_description`).

    Class attributes:
        name: Unique tool identifier (e.g. ``"file_read"``).
        description: Human-readable description of what the tool does.
        tool_class: Classification from :class:`ToolClass`.
        parameters: List of :class:`ToolParameter` for self-description.
    """

    name: str = ""
    description: str = ""
    tool_class: str = ToolClass.AUTONOMOUS
    parameters: list[ToolParameter] = []  # subclasses set as plain list

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @abstractmethod
    def execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        """Execute the tool.

        Args:
            args: Tool arguments keyed by parameter name.
            context: Runtime context (session, working dir, etc.).

        Returns:
            :class:`ToolResult` with success/failure and output.
        """

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def get_name(self) -> str:
        """Return the tool's unique name."""
        return self.name or self.__class__.__name__

    def get_description(self) -> str:
        """Return the tool's description."""
        return self.description

    def get_parameters(self) -> list[ToolParameter]:
        """Return the tool's parameter schema."""
        return list(self.parameters)

    def requires_confirmation(self) -> bool:
        """Whether this tool requires user confirmation before execution."""
        return self.tool_class == ToolClass.CONFIRMATION_REQUIRED

    def is_forbidden(self) -> bool:
        """Whether this tool is forbidden."""
        return self.tool_class == ToolClass.FORBIDDEN

    def to_dict(self) -> dict[str, Any]:
        """Serialise the tool metadata for planner / LLM consumption."""
        return {
            "name": self.get_name(),
            "description": self.get_description(),
            "tool_class": self.tool_class,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default,
                    "choices": p.choices,
                }
                for p in self.get_parameters()
            ],
        }

    # ------------------------------------------------------------------
    # Safe execution wrapper
    # ------------------------------------------------------------------

    def safe_execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        """Execute with automatic timing and error capture.

        Wraps :meth:`execute` so callers don't need try/except boilerplate.
        Logs the call and records duration in ``metadata``.
        """
        start = time.time()
        tool_name = self.get_name()
        logger.info(f"Executing tool: {tool_name} args={list(args.keys())}")

        try:
            result = self.execute(args, context)
        except Exception as e:
            duration = time.time() - start
            logger.error(f"Tool {tool_name} raised exception: {e}", exc_info=True)
            return ToolResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                metadata={"tool": tool_name, "duration_seconds": round(duration, 4)},
            )

        duration = time.time() - start
        if result.metadata is None:
            result.metadata = {}
        result.metadata.setdefault("tool", tool_name)
        result.metadata.setdefault("duration_seconds", round(duration, 4))

        logger.info(
            f"Tool {tool_name} finished: success={result.success} "
            f"duration={result.metadata['duration_seconds']}s"
        )
        return result