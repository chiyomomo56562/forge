"""Command-line adapter for Forge's LangGraph conversation runtime."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from uuid import uuid4

from forge.bootstrap import build_receive_message_service
from forge.domain.conversation import SendMessageCommand


def run_message(
    query: str,
    *,
    conversation_id: str | None = None,
    system_instruction: str = "",
    config_path: str = "config/agent.yml",
) -> str:
    """단일 CLI 메시지를 실행하고 assistant 텍스트만 반환한다.

    Args:
        query: 사용자가 보낸 텍스트.
        conversation_id: 이어갈 대화 ID. 없으면 새 UUID를 만든다.
        system_instruction: 이번 모델 호출에만 적용할 지시문.
        config_path: LLM 설정 YAML 경로.
    """
    service = build_receive_message_service(config_path=config_path)
    return service.handle(
        SendMessageCommand(
            conversation_id=conversation_id or str(uuid4()),
            text=query,
            system_instruction=system_instruction,
        )
    ).text


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 인자를 해석해 단일 호출 또는 REPL을 실행한다.

    Args:
        argv: 테스트용 인자 목록. 없으면 실제 명령행 인자를 사용한다.
    """
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge conversation CLI (in-memory conversation state).",
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default=None,
        help="Run a single message and exit. If omitted, starts a conversation REPL.",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config/agent.yml",
        help="Path to the agent config YAML (default: config/agent.yml).",
    )
    parser.add_argument(
        "--conversation-id",
        type=str,
        default=None,
        help="Conversation ID (in-memory for this process only).",
    )
    parser.add_argument(
        "--system",
        type=str,
        default="",
        help="Instruction applied only to the current model invocation.",
    )
    args = parser.parse_args(argv)

    if args.query is not None:
        try:
            print(
                run_message(
                    args.query,
                    conversation_id=args.conversation_id,
                    system_instruction=args.system,
                    config_path=args.config,
                )
            )
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    conversation_id = args.conversation_id or str(uuid4())
    print("Forge Conversation")
    print(f"Conversation ID: {conversation_id}")
    print("State is retained only while this process is running.")
    print("Type 'exit' or 'quit' to leave.\n")
    service = build_receive_message_service(config_path=args.config)
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
            print(
                service.handle(
                    SendMessageCommand(
                        conversation_id=conversation_id,
                        text=user_input,
                        system_instruction=args.system,
                    )
                ).text
            )
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
    return 0


def cli() -> None:
    """패키지 console script가 호출하는 CLI 진입점이다."""
    raise SystemExit(main())


if __name__ == "__main__":
    cli()
