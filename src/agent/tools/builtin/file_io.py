"""File I/O tools — file read, write, and delete operations.

Three tools are provided:
    - :class:`FileReadTool` — read file contents (autonomous)
    - :class:`FileWriteTool` — write/create files (confirmation_required)
    - :class:`FileDeleteTool` — delete files (confirmation_required)

All tools respect the ``working_dir`` from :class:`ToolContext` and
enforce path safety (no traversal outside the working directory).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..base import Tool, ToolClass, ToolContext, ToolParameter, ToolResult
from ...utils.logging import get_logger

logger = get_logger("agent.tools.builtin.file_io")


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _resolve_path(file_path: str, context: ToolContext | None) -> Path:
    """Resolve a path relative to the working directory.

    If ``file_path`` is absolute, it is used as-is.  Otherwise it is
    resolved relative to ``context.working_dir`` (or ``"."``).

    Returns the resolved :class:`Path`.
    """
    p = Path(file_path)
    if p.is_absolute():
        return p
    base = Path(context.working_dir) if context else Path(".")
    return (base / p).resolve()


def _is_safe_path(path: Path, context: ToolContext | None) -> bool:
    """Check that a path does not escape the working directory.

    When ``context.sandbox`` is ``True``, paths must stay within
    ``working_dir``.  Absolute paths outside the working dir are rejected.
    """
    if context is None or not context.sandbox:
        return True

    base = Path(context.working_dir).resolve()
    try:
        path.resolve().relative_to(base)
    except ValueError:
        return False
    return True


# ===========================================================================
# File Read Tool
# ===========================================================================

class FileReadTool(Tool):
    """Read the contents of a file.

    Classified as ``autonomous`` — no user confirmation needed.
    """

    name = "file_read"
    description = "Read the contents of a text file. Returns the file content as a string."
    tool_class = ToolClass.AUTONOMOUS
    parameters = [
        ToolParameter(
            name="path",
            type="string",
            description="Path to the file to read (relative to working directory or absolute).",
            required=True,
        ),
        ToolParameter(
            name="encoding",
            type="string",
            description="File encoding.",
            required=False,
            default="utf-8",
        ),
        ToolParameter(
            name="max_bytes",
            type="int",
            description="Maximum bytes to read. 0 means read the entire file.",
            required=False,
            default=0,
        ),
    ]

    def execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        file_path = args.get("path", "")
        encoding = args.get("encoding", "utf-8")
        max_bytes = args.get("max_bytes", 0)

        if not file_path:
            return ToolResult(success=False, error="Missing required parameter: path")

        path = _resolve_path(file_path, context)

        if not _is_safe_path(path, context):
            return ToolResult(
                success=False,
                error=f"Path '{file_path}' is outside the working directory (sandbox violation)",
            )

        if not path.exists():
            return ToolResult(success=False, error=f"File not found: {path}")

        if not path.is_file():
            return ToolResult(success=False, error=f"Not a file: {path}")

        try:
            if max_bytes > 0:
                content = path.read_bytes()[:max_bytes].decode(encoding, errors="replace")
            else:
                content = path.read_text(encoding=encoding)
            return ToolResult(
                success=True,
                output=content,
                metadata={"path": str(path), "bytes": len(content)},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to read file: {type(e).__name__}: {e}",
            )


# ===========================================================================
# File Write Tool
# ===========================================================================

class FileWriteTool(Tool):
    """Write content to a file (create or overwrite).

    Classified as ``confirmation_required`` — user must approve.
    """

    name = "file_write"
    description = "Write content to a file. Creates the file if it does not exist, overwrites if it does."
    tool_class = ToolClass.CONFIRMATION_REQUIRED
    parameters = [
        ToolParameter(
            name="path",
            type="string",
            description="Path to the file to write (relative to working directory or absolute).",
            required=True,
        ),
        ToolParameter(
            name="content",
            type="string",
            description="The content to write to the file.",
            required=True,
        ),
        ToolParameter(
            name="encoding",
            type="string",
            description="File encoding.",
            required=False,
            default="utf-8",
        ),
        ToolParameter(
            name="append",
            type="bool",
            description="If True, append to the file instead of overwriting.",
            required=False,
            default=False,
        ),
    ]

    def execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        file_path = args.get("path", "")
        content = args.get("content", "")
        encoding = args.get("encoding", "utf-8")
        append = args.get("append", False)

        if not file_path:
            return ToolResult(success=False, error="Missing required parameter: path")

        path = _resolve_path(file_path, context)

        if not _is_safe_path(path, context):
            return ToolResult(
                success=False,
                error=f"Path '{file_path}' is outside the working directory (sandbox violation)",
            )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with path.open(mode, encoding=encoding) as f:
                f.write(content)
            return ToolResult(
                success=True,
                output=f"Written {len(content)} chars to {path}",
                metadata={"path": str(path), "bytes": len(content), "appended": append},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to write file: {type(e).__name__}: {e}",
            )


# ===========================================================================
# File Delete Tool
# ===========================================================================

class FileDeleteTool(Tool):
    """Delete a file.

    Classified as ``confirmation_required`` — user must approve.
    Refuses to delete directories.
    """

    name = "file_delete"
    description = "Delete a file. Refuses to delete directories."
    tool_class = ToolClass.CONFIRMATION_REQUIRED
    parameters = [
        ToolParameter(
            name="path",
            type="string",
            description="Path to the file to delete.",
            required=True,
        ),
    ]

    def execute(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        file_path = args.get("path", "")

        if not file_path:
            return ToolResult(success=False, error="Missing required parameter: path")

        path = _resolve_path(file_path, context)

        if not _is_safe_path(path, context):
            return ToolResult(
                success=False,
                error=f"Path '{file_path}' is outside the working directory (sandbox violation)",
            )

        if not path.exists():
            return ToolResult(success=False, error=f"File not found: {path}")

        if path.is_dir():
            return ToolResult(
                success=False,
                error=f"Cannot delete directory (only files): {path}",
            )

        try:
            path.unlink()
            return ToolResult(
                success=True,
                output=f"Deleted: {path}",
                metadata={"path": str(path)},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to delete file: {type(e).__name__}: {e}",
            )