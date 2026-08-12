"""Command-line adapter for Forge's LangGraph conversation runtime."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any, cast
from uuid import uuid4

from forge.bootstrap import (
    build_inner_loop_service,
    build_procedural_memory_service,
    build_receive_message_service,
)
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
    return cast(
        str,
        service.handle(
            SendMessageCommand(
                conversation_id=conversation_id or str(uuid4()),
                text=query,
                system_instruction=system_instruction,
            )
        ).text,
    )


def run_inner_loop(
    query: str,
    *,
    task_category: str = "general",
    memory_config_path: str = "config/memory.yml",
) -> str:
    """명시적으로 요청한 deterministic Inner Loop 한 회를 실행한다.

    Args:
        query: Inner Loop가 계획·실행·기억할 작업 요청.
        task_category: L1 Episode에 기록할 작업 분류.
        memory_config_path: L0/L1 저장소 설정 파일.

    Returns:
        사용자에게 표시할 session/episode/outcome 요약.

    최종 수정일: 2026-07-31
    """
    result = build_inner_loop_service(memory_config_path).handle(
        task_request=query, task_category=task_category
    )
    return f"session={result.session_id} episode={result.episode_id} outcome={result.outcome.value}"


def list_l3_step_drafts(*, skill_id: str, memory_config_path: str = "config/memory.yml") -> str:
    """Return review-only L3 step drafts as JSON; this operation has no side effects."""
    drafts = build_procedural_memory_service(memory_config_path).list_step_drafts(skill_id)
    return json.dumps(
        [
            {
                "draft_id": draft.draft_id,
                "source_episode_id": draft.source_episode_id,
                "hint": draft.hint,
                "tool_name": draft.tool_name,
            }
            for draft in drafts
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def approve_l3_step_draft(
    *,
    skill_id: str,
    draft_id: str,
    step_id: str,
    tool_arguments_json: str,
    memory_config_path: str = "config/memory.yml",
) -> str:
    """Approve a draft only with reviewer-supplied JSON object arguments."""
    arguments = json.loads(tool_arguments_json)
    if not isinstance(arguments, dict):
        raise ValueError("--tool-arguments must be a JSON object")
    skill = build_procedural_memory_service(memory_config_path).approve_step_draft(
        skill_id,
        draft_id=draft_id,
        step_id=step_id,
        tool_arguments=cast(dict[str, object], cast(dict[str, Any], arguments)),
    )
    return f"approved skill={skill.skill_id} draft={draft_id} step={step_id}"


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
    parser.add_argument("--l3-skill-id", type=str, help="L3 skill ID for draft review operations.")
    parser.add_argument(
        "--list-l3-drafts",
        action="store_true",
        help="List non-executable drafts for --l3-skill-id.",
    )
    parser.add_argument(
        "--approve-l3-draft", type=str, help="Approve this draft ID for --l3-skill-id."
    )
    parser.add_argument("--step-id", type=str, help="Approved executable step ID.")
    parser.add_argument(
        "--tool-arguments", type=str, help="Reviewer-provided JSON object for an approved step."
    )
    parser.add_argument(
        "--inner-loop",
        action="store_true",
        help="Run the deterministic Inner Loop for --query instead of the conversation runtime.",
    )
    parser.add_argument(
        "--task-category", type=str, default="general", help="L1 category used with --inner-loop."
    )
    parser.add_argument(
        "--memory-config",
        type=str,
        default="config/memory.yml",
        help="Memory config for --inner-loop.",
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

    try:
        if args.list_l3_drafts:
            if not args.l3_skill_id:
                raise ValueError("--list-l3-drafts requires --l3-skill-id")
            print(
                list_l3_step_drafts(
                    skill_id=args.l3_skill_id,
                    memory_config_path=args.memory_config,
                )
            )
            return 0
        if args.approve_l3_draft:
            if not args.l3_skill_id or not args.step_id or args.tool_arguments is None:
                raise ValueError(
                    "--approve-l3-draft requires --l3-skill-id, --step-id, and --tool-arguments"
                )
            print(
                approve_l3_step_draft(
                    skill_id=args.l3_skill_id,
                    draft_id=args.approve_l3_draft,
                    step_id=args.step_id,
                    tool_arguments_json=args.tool_arguments,
                    memory_config_path=args.memory_config,
                )
            )
            return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.query is not None:
        try:
            if args.inner_loop:
                print(
                    run_inner_loop(
                        args.query,
                        task_category=args.task_category,
                        memory_config_path=args.memory_config,
                    )
                )
                return 0
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
