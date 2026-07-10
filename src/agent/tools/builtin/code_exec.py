"""Sandboxed code execution tool.

Executes Python code in a restricted sandbox using the L3
:class:`SkillExecutor` infrastructure.  The sandbox restricts
available builtins, enforces a timeout, and captures stdout/stderr.

Classified as ``autonomous`` in tool_policy.yml with a rate limit
of 30 per minute and a 30-second timeout.
"""

from __future__ import annotations

import time
from typing import Any

from ..base import Tool, ToolClass, ToolContext, ToolParameter, ToolResult
from ...utils.logging import get_logger

logger = get_logger("agent.tools.builtin.code_exec")


# Allowed modules from tool_policy.yml
_ALLOWED_MODULES = frozenset({
    "math", "json", "re", "datetime", "collections", "itertools", "functools",
})


class CodeExecTool(Tool):
    """Execute Python code in a sandbox.

    Uses the L3 :class:`SkillExecutor` for safe execution with:
        - Restricted builtins (no ``open``, ``eval``, ``exec``, ``__import__``)
        - Timeout (default 30s from tool_policy)
        - stdout/stderr capture

    Args:
        timeout: Maximum execution time in seconds.
        rate_limit_per_min: Maximum calls per minute.
    """

    name = "code_exec_sandboxed"
    description = (
        "Execute Python code in a sandbox. "
        f"Allowed modules: {', '.join(sorted(_ALLOWED_MODULES))}. "
        "Returns stdout, result, and any errors."
    )
    tool_class = ToolClass.AUTONOMOUS
    parameters = [
        ToolParameter(
            name="code",
            type="string",
            description="Python code to execute.",
            required=True,
        ),
        ToolParameter(
            name="timeout",
            type="int",
            description="Maximum execution time in seconds.",
            required=False,
            default=30,
        ),
    ]

    def __init__(self, timeout: int = 30, rate_limit_per_min: int = 30):
        self.default_timeout = timeout
        self.rate_limit_per_min = rate_limit_per_min
        self._call_timestamps: list[float] = []

    def _check_rate_limit(self) -> bool:
        """Return ``True`` if the call is within the rate limit."""
        now = time.time()
        self._call_timestamps = [t for t in self._call_timestamps if now - t < 60.0]
        if len(self._call_timestamps) >= self.rate_limit_per_min:
            return False
        self._call_timestamps.append(now)
        return True

    def execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        code = args.get("code", "")
        timeout = args.get("timeout", self.default_timeout)

        if not code:
            return ToolResult(success=False, error="Missing required parameter: code")

        if not self._check_rate_limit():
            return ToolResult(
                success=False,
                error=f"Rate limit exceeded ({self.rate_limit_per_min}/min)",
            )

        # Use L3 SkillExecutor for sandboxed execution
        try:
            from ...memory.procedural.skill_executor import SkillExecutor
            from ...memory.schemas import Skill, SkillMetadata

            # Build a restricted __import__ that only allows whitelisted modules
            def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name in _ALLOWED_MODULES:
                    return __import__(name, globals, locals, fromlist, level)
                raise ImportError(f"Module '{name}' is not allowed. Allowed: {', '.join(sorted(_ALLOWED_MODULES))}")

            # Pre-import allowed modules as builtins
            custom_builtins: dict[str, Any] = {"__import__": _restricted_import}

            executor = SkillExecutor(
                default_timeout=timeout,
                allowed_builtins=None,  # use default safe builtins
            )
            # Inject restricted __import__ into the executor's builtins
            executor._builtins["__import__"] = _restricted_import

            # Wrap the code in a minimal Skill object for execution
            skill = Skill(
                skill_id="code_exec_inline",
                name="inline_code_exec",
                code=code,
                description="Inline code execution",
                metadata=SkillMetadata(),
            )
            result = executor.execute(skill, timeout=timeout)

            if result.success:
                return ToolResult(
                    success=True,
                    output=result.output,
                    metadata={
                        "timed_out": result.timed_out,
                        "duration_seconds": result.duration_seconds,
                        "result_value": result.result_value,
                    },
                )
            return ToolResult(
                success=False,
                error=result.error or result.traceback_str,
                metadata={
                    "timed_out": result.timed_out,
                    "duration_seconds": result.duration_seconds,
                },
            )

        except ImportError:
            logger.warning("SkillExecutor not available, using inline sandbox")
            return self._inline_exec(code, timeout)
        except Exception as e:
            logger.warning(f"SkillExecutor failed ({e}), using inline sandbox")
            return self._inline_exec(code, timeout)

    def _inline_exec(self, code: str, timeout: int) -> ToolResult:
        """Fallback inline sandbox when SkillExecutor is unavailable.

        Provides basic sandboxing with restricted builtins and
        stdout/stderr capture.  Does not support module imports.
        """
        import io
        from contextlib import redirect_stderr, redirect_stdout

        # Restricted builtins (same as SkillExecutor)
        safe_builtins: dict[str, Any] = {
            "int": int, "float": float, "str": str, "bool": bool,
            "list": list, "dict": dict, "tuple": tuple, "set": set,
            "len": len, "range": range, "enumerate": enumerate, "zip": zip,
            "map": map, "filter": filter, "sorted": sorted, "reversed": reversed,
            "min": min, "max": max, "sum": sum, "abs": abs, "round": round,
            "any": any, "all": all, "isinstance": isinstance, "type": type,
            "print": print, "repr": repr, "format": format,
            "True": True, "False": False, "None": None,
            "Exception": Exception, "ValueError": ValueError,
            "TypeError": TypeError, "KeyError": KeyError,
            "IndexError": IndexError, "AttributeError": AttributeError,
            "RuntimeError": RuntimeError, "StopIteration": StopIteration,
            "ZeroDivisionError": ZeroDivisionError,
        }

        # Add allowed modules
        for mod_name in _ALLOWED_MODULES:
            try:
                safe_builtins[mod_name] = __import__(mod_name)
            except ImportError:
                pass

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        result_value = None

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                local_ns: dict[str, Any] = {"__builtins__": safe_builtins}
                exec(compile(code, "<sandbox>", "exec"), local_ns, local_ns)
                result_value = local_ns.get("result", None)
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                metadata={"backend": "inline"},
            )

        output = stdout_buf.getvalue()
        stderr_output = stderr_buf.getvalue()
        if stderr_output:
            output += f"\n[stderr]\n{stderr_output}" if output else stderr_output

        return ToolResult(
            success=True,
            output=output,
            metadata={"backend": "inline", "result_value": result_value},
        )