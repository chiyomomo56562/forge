"""Fail-closed outbound contract for configured MCP servers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol


class McpClientError(RuntimeError):
    """A safe, stable MCP boundary failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class McpServerConfig:
    """One explicitly enabled and policy-scoped MCP server connection."""

    server_id: str
    transport: Literal["stdio", "streamable_http"]
    allowed_tools: tuple[str, ...]
    command: str | None = None
    arguments: tuple[str, ...] = ()
    url: str | None = None
    timeout_seconds: int = 30
    max_output_bytes: int = 32_768

    def __post_init__(self) -> None:
        if not self.server_id.strip() or not self.allowed_tools:
            raise ValueError("MCP server ID and allowlisted tools are required")
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or any(
            not name.strip() for name in self.allowed_tools
        ):
            raise ValueError("MCP allowlisted tool names must be unique and non-empty")
        if self.timeout_seconds <= 0 or self.max_output_bytes <= 0:
            raise ValueError("MCP limits must be positive")
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP servers require a command")
        if self.transport == "streamable_http" and not self.url:
            raise ValueError("streamable HTTP MCP servers require a URL")


@dataclass(frozen=True)
class McpToolDescriptor:
    server_id: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class McpToolCallResult:
    content: Mapping[str, Any]
    is_error: bool = False
    truncated: bool = False


class McpClient(Protocol):
    async def list_tools(self, server_id: str) -> Sequence[McpToolDescriptor]: ...
    async def call_tool(
        self, server_id: str, tool_name: str, arguments: Mapping[str, Any]
    ) -> McpToolCallResult: ...
    async def close(self) -> None: ...
