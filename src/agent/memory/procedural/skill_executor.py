"""Skill executor for L3 Procedural Memory.

Executes skill code in a restricted sandbox using ``exec()`` with a
configurable timeout.  The sandbox restricts available builtins and
captures stdout/stderr for inspection.

Safety measures:
    - Restricted ``__builtins__`` (no ``open``, ``eval``, ``exec``, ``compile``, ``__import__`` by default)
    - Timeout via :func:`signal.alarm` (POSIX) or thread-based fallback
    - Output capture (stdout + stderr)
    - Exception capture with traceback
"""

from __future__ import annotations

import io
import signal
import sys
import threading
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any

from ..schemas import Skill
from ...utils.logging import get_logger

logger = get_logger("agent.memory.procedural.skill_executor")

# ---------------------------------------------------------------------------
# Safe builtins — a restricted subset for sandboxed execution
# ---------------------------------------------------------------------------

_SAFE_BUILTINS: dict[str, Any] = {
    # Basic types
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "frozenset": frozenset,
    "bytes": bytes,
    "bytearray": bytearray,
    # Basic functions
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "any": any,
    "all": all,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "type": type,
    "print": print,
    "repr": repr,
    "format": format,
    # Constants
    "True": True,
    "False": False,
    "None": None,
    # Exceptions (for try/except in skill code)
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "RuntimeError": RuntimeError,
    "StopIteration": StopIteration,
    "ZeroDivisionError": ZeroDivisionError,
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SkillExecutionResult:
    """Result of executing a skill."""
    success: bool
    output: str = ""
    error: str = ""
    traceback_str: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    result_value: Any = None


# ---------------------------------------------------------------------------
# Timeout handler (POSIX signal-based)
# ---------------------------------------------------------------------------

class _TimeoutError(Exception):
    """Internal timeout exception for signal-based timeout."""


def _timeout_handler(signum: int, frame: Any) -> None:
    raise _TimeoutError("Skill execution timed out")


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class SkillExecutor:
    """Execute skill code in a sandboxed environment with timeout.

    Args:
        default_timeout: Default timeout in seconds.
        allowed_builtins: Dict of allowed builtins. If ``None``, uses
            the default safe subset.
        allow_imports: If ``True``, allows ``__import__`` in the sandbox
            (use with caution).
    """

    def __init__(
        self,
        default_timeout: float = 10.0,
        allowed_builtins: dict[str, Any] | None = None,
        allow_imports: bool = False,
    ):
        self.default_timeout = default_timeout
        self._builtins = dict(allowed_builtins) if allowed_builtins is not None else dict(_SAFE_BUILTINS)
        if allow_imports:
            self._builtins["__import__"] = __import__

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        skill: Skill,
        inputs: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> SkillExecutionResult:
        """Execute a skill's code in a sandbox.

        Args:
            skill: The :class:`Skill` to execute.
            inputs: Input variables to inject into the execution namespace.
            timeout: Timeout in seconds. If ``None``, uses ``default_timeout``.

        Returns:
            :class:`SkillExecutionResult` with output, errors, and timing.
        """
        timeout = timeout if timeout is not None else self.default_timeout
        inputs = inputs or {}

        # Build the sandbox namespace
        sandbox_globals: dict[str, Any] = {
            "__builtins__": self._builtins,
            "__name__": f"skill_{skill.skill_id}",
        }
        sandbox_locals: dict[str, Any] = dict(inputs)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        import time as _time
        start = _time.monotonic()

        try:
            compiled = compile(skill.code, f"<skill:{skill.skill_id}>", "exec")

            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                self._run_with_timeout(compiled, sandbox_globals, sandbox_locals, timeout)

            duration = _time.monotonic() - start
            result_value = sandbox_locals.get("result", None)

            return SkillExecutionResult(
                success=True,
                output=stdout_buf.getvalue(),
                error=stderr_buf.getvalue(),
                duration_seconds=round(duration, 4),
                result_value=result_value,
            )

        except _TimeoutError:
            duration = _time.monotonic() - start
            logger.warning(f"Skill {skill.skill_id} timed out after {timeout}s")
            return SkillExecutionResult(
                success=False,
                output=stdout_buf.getvalue(),
                error=stderr_buf.getvalue(),
                traceback_str="",
                duration_seconds=round(duration, 4),
                timed_out=True,
            )
        except Exception as e:
            duration = _time.monotonic() - start
            tb_str = traceback.format_exc()
            logger.error(f"Skill {skill.skill_id} execution failed: {e}")
            return SkillExecutionResult(
                success=False,
                output=stdout_buf.getvalue(),
                error=stderr_buf.getvalue(),
                traceback_str=tb_str,
                duration_seconds=round(duration, 4),
            )

    def execute_code(
        self,
        code: str,
        skill_id: str = "anonymous",
        inputs: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> SkillExecutionResult:
        """Execute raw code string (convenience wrapper).

        Args:
            code: Python code string to execute.
            skill_id: Identifier for logging.
            inputs: Input variables to inject.
            timeout: Timeout in seconds.

        Returns:
            :class:`SkillExecutionResult`.
        """
        from ..schemas import Skill as _Skill, SkillMetadata as _SM
        skill = _Skill(
            skill_id=skill_id,
            code=code,
            metadata=_SM(),
        )
        return self.execute(skill, inputs=inputs, timeout=timeout)

    # ------------------------------------------------------------------
    # Internal: timeout implementation
    # ------------------------------------------------------------------

    def _run_with_timeout(
        self,
        compiled: Any,
        globals_dict: dict[str, Any],
        locals_dict: dict[str, Any],
        timeout: float,
    ) -> None:
        """Run compiled code with a timeout.

        Uses ``signal.alarm`` on POSIX systems; falls back to a
        thread-based approach on platforms without ``SIGALRM``.
        """
        use_signal = (
            hasattr(signal, "SIGALRM")
            and threading.current_thread() is threading.main_thread()
        )

        if use_signal:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout)
            try:
                exec(compiled, globals_dict, locals_dict)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)
        else:
            # Thread-based timeout fallback
            result_holder: dict[str, Any] = {"error": None}

            def _target() -> None:
                try:
                    exec(compiled, globals_dict, locals_dict)
                except Exception as e:
                    result_holder["error"] = e

            thread = threading.Thread(target=_target, daemon=True)
            thread.start()
            thread.join(timeout=timeout)

            if thread.is_alive():
                raise _TimeoutError("Skill execution timed out")
            if result_holder["error"] is not None:
                raise result_holder["error"]