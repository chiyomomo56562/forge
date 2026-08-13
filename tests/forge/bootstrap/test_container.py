"""Composition tests for the shared LangChain tool collection."""

from pathlib import Path

import yaml
from langchain_core.messages import AIMessage

from forge.adapters.outbound.tools import (
    BuiltinToolRegistry,
    StaticToolAuthorizationPolicy,
    build_langchain_tools,
)
from forge.bootstrap.container import _build_conversation_runtime, build_mcp_server_configs


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


class _ToolCapableModel:
    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return AIMessage(content="done")


def test_conversation_runtime_uses_configured_tool_round_budget(tmp_path: Path) -> None:
    config = {
        "conversation": {
            "tools": {
                "enabled": True,
                "max_tool_rounds": 47,
            }
        },
        "tools": {"workspace_root": str(tmp_path)},
    }

    runtime = _build_conversation_runtime(
        config, config_path="unused.yml", chat_model=_ToolCapableModel()
    )

    assert runtime._max_tool_rounds == 47


def test_agent_config_declares_generous_tool_round_budget() -> None:
    config_path = Path(__file__).parents[3] / "config" / "agent.yml"

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["conversation"]["tools"]["max_tool_rounds"] == 30
    assert config["conversation"]["tools"]["allow_workspace_mutation"] is True
    assert config["conversation"]["tools"]["allow_verification"] is True


def test_mcp_is_disabled_by_default(tmp_path: Path) -> None:
    config = tmp_path / "agent.yml"
    config.write_text("mcp:\n  enabled: false\n  servers: []\n", encoding="utf-8")

    assert build_mcp_server_configs(str(config)) == ()


def test_enabled_mcp_requires_an_explicit_allowlist_and_valid_transport(tmp_path: Path) -> None:
    config = tmp_path / "agent.yml"
    config.write_text(
        """mcp:
  enabled: true
  servers:
    - id: filesystem
      transport: stdio
      command: uvx
      arguments: [example-mcp]
      allowed_tools: [read_file]
      timeout_seconds: 12
      max_output_bytes: 1000
""",
        encoding="utf-8",
    )

    assert build_mcp_server_configs(str(config))[0].allowed_tools == ("read_file",)
