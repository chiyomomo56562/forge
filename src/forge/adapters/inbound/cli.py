"""Command-line adapter for the independent forge initial-input path."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from forge.bootstrap import build_initial_input_service
from forge.domain.conversation import InitialInput


def run_initial_input(query: str, *, config_path: str = "config/agent.yml") -> str:
    service = build_initial_input_service(config_path=config_path)
    return service.handle(InitialInput(query)).text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge initial-input CLI.",
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default=None,
        help="Run a single initial input and exit. If omitted, starts an initial-input REPL.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config/agent.yml",
        help="Path to the agent config YAML (default: config/agent.yml).",
    )
    args = parser.parse_args(argv)

    if args.query is not None:
        try:
            print(run_initial_input(args.query, config_path=args.config))
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    print("Forge Initial Input")
    print("Type 'exit' or 'quit' to leave.\n")
    service = build_initial_input_service(config_path=args.config)
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
            print(service.handle(InitialInput(user_input)).text)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
    return 0


def cli() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
