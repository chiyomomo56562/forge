"""LangGraph-backed, thread-scoped conversation execution."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.runtime import Runtime

from forge.domain.conversation import AssistantReply

from .context import ConversationContext


class LangGraphConversationRuntime:
    """LangGraph checkpoint로 프로세스 안의 대화 문맥을 유지한다.

    Args:
        model: LangChain 메시지 목록을 받아 AIMessage를 반환하는 채팅 모델.
        checkpointer: 대화 상태를 보관할 저장소. 생략하면 메모리 저장소를 만든다.
    """

    def __init__(self, model: Any, *, checkpointer: InMemorySaver | None = None) -> None:
        """모델과 checkpoint 저장소로 LangGraph 실행 그래프를 초기화한다.

        Args:
            model: 메시지 목록을 받아 AIMessage를 반환하는 LangChain 채팅 모델.
            checkpointer: thread_id별 메시지 상태를 저장할 선택 저장소.
        """
        self._model = model
        self._checkpointer = checkpointer or InMemorySaver()
        builder = StateGraph(MessagesState, context_schema=ConversationContext)
        builder.add_node("call_model", self._call_model)
        builder.add_edge(START, "call_model")
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
            context=ConversationContext(system_instruction=system_instruction),
        )
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            raise RuntimeError("Conversation runtime did not produce an AI message")
        model = str(message.response_metadata.get("model_name", ""))
        return AssistantReply(text=_extract_text(message.content), model=model)

    def _call_model(
        self,
        state: MessagesState,
        runtime: Runtime[ConversationContext],
    ) -> dict[str, list[AIMessage]]:
        """저장된 문맥에 현재 지시문을 임시로 붙여 모델을 호출한다.

        Args:
            state: checkpoint에서 복원한 user/assistant 메시지 상태.
            runtime: 이번 호출의 system_instruction을 담은 LangGraph 실행 정보.
        """
        messages = list(state["messages"])
        if runtime.context.system_instruction.strip():
            messages.insert(0, SystemMessage(content=runtime.context.system_instruction))
        response = self._model.invoke(messages)
        if not isinstance(response, AIMessage):
            raise RuntimeError("Configured chat model did not return an AIMessage")
        return {"messages": [response]}
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
