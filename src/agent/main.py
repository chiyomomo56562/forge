"""CLI entry point for the Forge agent framework.

Provides a command-line interface for running the inner loop:
    - Interactive REPL mode (default)
    - Single-query mode via ``--query``
    - Configuration via ``--config`` flag

Usage::

    # Interactive mode
    python -m agent.main

    # Single query
    python -m agent.main --query "Summarise this article"

    # With custom config
    python -m agent.main --config config/agent.yml --query "Hello"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .utils.logging import get_logger, setup_logging

logger = get_logger("agent.main")


# ===========================================================================
# Agent bootstrap
# ===========================================================================

def create_agent(
    config_path: str = "config/agent.yml",
    working_dir: str = "data/memory/working/sessions",
    enable_tools: bool = True,
) -> tuple[Any, Any]:
    """Bootstrap the agent: create runtime + orchestrator.

    Args:
        config_path: Path to the agent config YAML.
        working_dir: Working directory for sessions.
        enable_tools: If ``True``, register builtin tools.

    Returns:
        (runtime, orchestrator) tuple.
    """
    from .runtime import Runtime
    from .orchestrator import Orchestrator
    from .llm.client import LLMClient, LLMConfig
    from .tools.registry import ToolRegistry

    # LLM client
    llm_client = LLMClient.from_config(config_path)

    # Memory manager (optional — may not be available in all environments)
    memory_manager = None
    try:
        from .memory.manager import MemoryManager
        from .memory.episodic.encoder import EmbeddingEncoder

        llm_config = LLMConfig.from_yaml(config_path)
        encoder = EmbeddingEncoder(
            llm_client=llm_client,
            dimension=llm_config.embed_dimension,
        )
        memory_manager = MemoryManager(
            encoder=encoder,
            llm_client=llm_client,
        )
        logger.info("MemoryManager initialised")
    except Exception as e:
        logger.warning(f"MemoryManager not available: {e}")

    # Tool registry
    tool_registry = None
    if enable_tools:
        tool_registry = ToolRegistry(
            policy_path="constitution/tool_policy.yml"
        )
        tool_registry.register_builtin()
        if memory_manager is not None:
            try:
                tool_registry.register_skills(memory_manager.skill_loader)
            except Exception as e:
                logger.warning(f"Skill registration failed: {e}")
        logger.info(f"Tool registry: {len(tool_registry.list_names())} tools")

    # Runtime
    runtime = Runtime(base_working_dir=working_dir)

    # Orchestrator
    orchestrator = Orchestrator(
        runtime=runtime,
        memory_manager=memory_manager,
        tool_registry=tool_registry,
        llm_client=llm_client,
    )

    return runtime, orchestrator


# ===========================================================================
# CLI
# ===========================================================================

def run_query(orchestrator: Any, query: str, task_category: str = "general") -> str:
    """Run a single query through the orchestrator.

    Args:
        orchestrator: An :class:`Orchestrator` instance.
        query: The user's request.
        task_category: Task category hint.

    Returns:
        The formatted result string.
    """
    result = orchestrator.run(query, task_category=task_category)

    lines = [
        f"Session:  {result.session_id}",
        f"Episode:  {result.episode_id}",
        f"Success:  {result.success}",
        f"Retries:  {result.retries}",
        "",
        "─── Result ───",
        result.execution_output or "(no output)",
        "",
        "─── Evaluation ───",
        f"  CIB passed:     {result.evaluation.get('cib_passed', 'N/A')}",
        f"  Phoenix score:  {result.evaluation.get('phoenix_score', 'N/A')}",
        f"  Pain index:     {result.evaluation.get('pain_index', 'N/A')}",
        f"  Status:         {result.evaluation.get('status', 'N/A')}",
        "",
        "─── Reflection ───",
        f"  What worked:    {result.reflection.get('what_worked', '')}",
        f"  What failed:    {result.reflection.get('what_failed', '')}",
        f"  Next hint:      {result.reflection.get('next_hint', '')}",
        f"  Causal cond:    {result.reflection.get('causal_condition', '')}",
    ]

    if result.error:
        lines.append("")
        lines.append(f"─── Error ───")
        lines.append(f"  {result.error}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments. If ``None``, uses ``sys.argv``.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge — a self-evolving agent framework.",
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        default=None,
        help="Run a single query and exit. If omitted, starts interactive REPL.",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="config/agent.yml",
        help="Path to the agent config YAML (default: config/agent.yml).",
    )
    parser.add_argument(
        "--working-dir",
        type=str,
        default="data/memory/working/sessions",
        help="Working directory for session data.",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="Disable tool registration.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )

    args = parser.parse_args(argv)

    # Setup logging
    setup_logging(force=True)
    if args.verbose:
        import logging
        logging.getLogger("agent").setLevel(logging.DEBUG)

    # Bootstrap
    try:
        runtime, orchestrator = create_agent(
            config_path=args.config,
            working_dir=args.working_dir,
            enable_tools=not args.no_tools,
        )
    except Exception as e:
        print(f"Failed to initialise agent: {e}", file=sys.stderr)
        return 1

    # Single query mode
    if args.query:
        output = run_query(orchestrator, args.query)
        print(output)
        return 0

    # Interactive REPL
    print("Forge Agent — Interactive Mode")
    print("Type 'exit' or 'quit' to leave.\n")

    while True:
        try:
            user_input = input("user> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if user_input.lower() in ("exit", "quit"):
            print("Goodbye.")
            break

        if not user_input:
            continue

        try:
            output = run_query(orchestrator, user_input)
            print(output)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())