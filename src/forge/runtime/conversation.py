"""LangGraph-backed, thread-scoped conversation execution."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from forge.adapters.outbound.llm.strategies import ToolCallingError
from forge.application.conversation.tool_feedback import (
    ToolFeedbackPayload,
    protocol_failure_feedback,
)
from forge.domain.conversation import AssistantReply, ToolCall

from .context import ConversationContext

DEFAULT_MAX_TOOL_ROUNDS = 3
DEFAULT_MAX_PROTOCOL_FAILURES = 2
DEFAULT_MAX_TOOL_FEEDBACK_BYTES = 4096
_PROTOCOL_TOOL_CALL_ID = "tool_protocol"
_PROTOCOL_FINAL_RESPONSE = (
    "I could not safely process the malformed tool-call request. Please try again."
)
_TOOL_ROUND_LIMIT_RESPONSE = (
    "I stopped because the tool-call round limit was reached. Please try again."
)


class LangGraphConversationRuntime:
    """LangGraph checkpoint로 프로세스 안의 대화 문맥을 유지한다.

    Args:
        model: LangChain 메시지 목록을 받아 AIMessage를 반환하는 채팅 모델.
        checkpointer: 대화 상태를 보관할 저장소. 생략하면 메모리 저장소를 만든다.
    """

    def __init__(
        self,
        model: Any,
        *,
        checkpointer: InMemorySaver | None = None,
        tools: Sequence[BaseTool] = (),
        max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
        max_protocol_failures: int = DEFAULT_MAX_PROTOCOL_FAILURES,
        max_tool_feedback_bytes: int = DEFAULT_MAX_TOOL_FEEDBACK_BYTES,
        workspace_root: Path | str | None = None,
    ) -> None:
        """모델과 checkpoint 저장소로 LangGraph 실행 그래프를 초기화한다.

        Args:
            model: 메시지 목록을 받아 AIMessage를 반환하는 LangChain 채팅 모델.
            checkpointer: thread_id별 메시지 상태를 저장할 선택 저장소.
        """
        self._model = model
        self._checkpointer = checkpointer or InMemorySaver()
        self._tools = tuple(tools)
        self._max_tool_rounds = max_tool_rounds
        self._max_protocol_failures = max_protocol_failures
        self._max_tool_feedback_bytes = max_tool_feedback_bytes
        self._workspace_root = workspace_root
        builder = StateGraph(MessagesState, context_schema=ConversationContext)
        builder.add_node("call_model", self._call_model)
        builder.add_edge(START, "call_model")
        if self._tools:
            builder.add_node("execute_tools", ToolNode(self._tools))
            builder.add_node("tool_round_limit", self._tool_round_limit)
            builder.add_edge("execute_tools", "call_model")
            builder.add_edge("tool_round_limit", END)
        else:
            builder.add_node("tool_round_limit", self._tool_round_limit)
            builder.add_edge("tool_round_limit", END)
        builder.add_conditional_edges(
            "call_model",
            self._route_after_model,
            {
                "execute_tools": "execute_tools" if self._tools else END,
                "call_model": "call_model",
                "tool_round_limit": "tool_round_limit",
                END: END,
            },
        )
        self._graph = builder.compile(checkpointer=self._checkpointer)

    def invoke(
        self,
        *,
        conversation_id: str,
        text: str,
        system_instruction: str = "",
    ) -> AssistantReply:
        """사용자 메시지 한 건을 대화에 추가하고 모델의 답변을 반환한다.

        Args:
            conversation_id: LangGraph thread_id로 사용할 대화 식별자.
            text: 이번 호출에서 사용자가 입력한 텍스트.
            system_instruction: 이번 모델 호출에만 적용할 시스템 지시문.
        """
        state = self._graph.invoke(
            {"messages": [HumanMessage(content=text)]},
            {"configurable": {"thread_id": conversation_id}},
            context=ConversationContext(
                system_instruction=system_instruction,
                conversation_id=conversation_id,
            ),
        )
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            raise RuntimeError("Conversation runtime did not produce an AI message")
        model = str(message.response_metadata.get("model_name", ""))
        tool_calls = _extract_tool_calls(message)
        return AssistantReply(
            text=_extract_text(message.content), model=model, tool_calls=tool_calls
        )

    def _call_model(
        self,
        state: MessagesState,
        runtime: Runtime[ConversationContext],
    ) -> dict[str, list[AIMessage | ToolMessage]]:
        """저장된 문맥에 현재 지시문을 임시로 붙여 모델을 호출한다.

        Args:
            state: checkpoint에서 복원한 user/assistant 메시지 상태.
            runtime: 이번 호출의 system_instruction을 담은 LangGraph 실행 정보.
        """
        messages = list(state["messages"])
        if runtime.context.system_instruction.strip():
            messages.insert(0, SystemMessage(content=runtime.context.system_instruction))
        try:
            response = self._model.invoke(messages)
        except ToolCallingError as exc:
            return {"messages": [self._protocol_failure_message(state, str(exc))]}
        if not isinstance(response, AIMessage):
            raise RuntimeError("Configured chat model did not return an AIMessage")
        return {"messages": [response]}

    def _route_after_model(self, state: MessagesState) -> str:
        latest = state["messages"][-1]
        if isinstance(latest, ToolMessage) and latest.tool_call_id == _PROTOCOL_TOOL_CALL_ID:
            if _current_turn_protocol_failures(state) > self._max_protocol_failures:
                return END
            return "call_model"
        if not isinstance(latest, AIMessage) or not _has_raw_tool_calls(latest):
            return END
        if _current_turn_tool_rounds(state) > self._max_tool_rounds:
            return "tool_round_limit"
        return "execute_tools"

    def _protocol_failure_message(
        self, state: MessagesState, reason: str
    ) -> AIMessage | ToolMessage:
        if _current_turn_protocol_failures(state) >= self._max_protocol_failures:
            return AIMessage(content=_PROTOCOL_FINAL_RESPONSE)
        payload = protocol_failure_feedback(
            tool_call_id=_PROTOCOL_TOOL_CALL_ID,
            safe_error_code="tool.protocol_failure",
            summary=f"Malformed tool-call envelope. {reason}",
            max_output_bytes=self._max_tool_feedback_bytes,
            workspace_root=self._workspace_root,
        )
        return _tool_message(payload)

    def _tool_round_limit(self, _state: MessagesState) -> dict[str, list[AIMessage]]:
        return {"messages": [AIMessage(content=_TOOL_ROUND_LIMIT_RESPONSE)]}


def _extract_tool_calls(message: AIMessage) -> tuple[ToolCall, ...]:
    """LangChain AIMessage에서 도구 호출 목록을 추출한다.

    Args:
        message: 모델이 반환한 AIMessage.

    Returns:
        도구 호출이 없으면 빈 튜플, 있으면 ToolCall 튜플.

    최종 수정일: 2026-08-04
    """
    raw_calls = getattr(message, "tool_calls", None) or []
    calls: list[ToolCall] = []
    for call in raw_calls:
        name = call.get("name", "")
        if not name:
            continue
        arguments = call.get("args") or call.get("arguments") or {}
        call_id = call.get("id", "")
        calls.append(ToolCall(name=name, arguments=dict(arguments), id=call_id))
    return tuple(calls)


def _has_raw_tool_calls(message: AIMessage) -> bool:
    return bool(getattr(message, "tool_calls", None) or [])


def _tool_message(payload: ToolFeedbackPayload) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        tool_call_id=payload["tool_call_id"],
        name=payload["name"],
    )


def _current_turn_messages(state: MessagesState) -> list[Any]:
    messages = list(state["messages"])
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], HumanMessage):
            return messages[index + 1 :]
    return messages


def _current_turn_tool_rounds(state: MessagesState) -> int:
    return sum(
        isinstance(message, AIMessage) and bool(_extract_tool_calls(message))
        for message in _current_turn_messages(state)
    )


def _current_turn_protocol_failures(state: MessagesState) -> int:
    return sum(
        isinstance(message, ToolMessage) and message.tool_call_id == _PROTOCOL_TOOL_CALL_ID
        for message in _current_turn_messages(state)
    )


def _extract_text(content: str | list[str | dict[str, Any]]) -> str:
    """LangChain assistant content에서 사용자에게 보여 줄 텍스트만 추출한다.

    Args:
        content: 제공자가 반환한 문자열 또는 text/tool 등의 content block 목록.

    Raises:
        RuntimeError: 표시 가능한 텍스트 block이 하나도 없을 때 발생한다.
    """
    if isinstance(content, str):
        return content

    text_parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            text_parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            text_parts.append(block["text"])

    text = "".join(text_parts)
    if not text:
        raise RuntimeError("Conversation runtime produced no displayable text")
    return text
