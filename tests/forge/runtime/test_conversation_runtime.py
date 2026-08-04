from langchain_core.messages import AIMessage

from forge.runtime import LangGraphConversationRuntime


class FakeChatModel:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(
            content=f"reply-{len(self.calls)}",
            response_metadata={"model_name": "fake"},
        )


class FakeBlockChatModel:
    def invoke(self, _messages):
        return AIMessage(
            content=[{"type": "text", "text": "block reply"}],
            response_metadata={"model_name": "fake"},
        )


def test_runtime_preserves_thread_history_but_not_system_instruction():
    model = FakeChatModel()
    runtime = LangGraphConversationRuntime(model)

    first = runtime.invoke(
        conversation_id="thread-1", text="first", system_instruction="first system"
    )
    second = runtime.invoke(
        conversation_id="thread-1", text="second", system_instruction="second system"
    )

    assert first.text == "reply-1"
    assert second.text == "reply-2"
    assert [(message.type, message.content) for message in model.calls[0]] == [
        ("system", "first system"),
        ("human", "first"),
    ]
    assert [(message.type, message.content) for message in model.calls[1]] == [
        ("system", "second system"),
        ("human", "first"),
        ("ai", "reply-1"),
        ("human", "second"),
    ]
    state = runtime._graph.get_state({"configurable": {"thread_id": "thread-1"}}).values
    assert [(message.type, message.content) for message in state["messages"]] == [
        ("human", "first"),
        ("ai", "reply-1"),
        ("human", "second"),
        ("ai", "reply-2"),
    ]


def test_runtime_isolates_threads():
    model = FakeChatModel()
    runtime = LangGraphConversationRuntime(model)

    runtime.invoke(conversation_id="one", text="from one")
    runtime.invoke(conversation_id="two", text="from two")

    assert [(message.type, message.content) for message in model.calls[1]] == [
        ("human", "from two")
    ]


def test_runtime_extracts_text_from_content_blocks():
    runtime = LangGraphConversationRuntime(FakeBlockChatModel())

    reply = runtime.invoke(conversation_id="thread-1", text="hello")

    assert reply.text == "block reply"


class FakeToolCallChatModel:
    """tool_calls가 포함된 AIMessage를 반환하는 fake 모델."""

    def invoke(self, _messages):
        return AIMessage(
            content="I will list files for you.",
            response_metadata={"model_name": "fake"},
            tool_calls=[
                {
                    "name": "workspace.list_files",
                    "args": {"path": "."},
                    "id": "call_1",
                }
            ],
        )


def test_runtime_extracts_tool_calls_from_ai_message():
    """AIMessage.tool_calls가 AssistantReply.tool_calls로 추출되는지 검증한다.

    최종 수정일: 2026-08-04
    """
    runtime = LangGraphConversationRuntime(FakeToolCallChatModel())

    reply = runtime.invoke(conversation_id="thread-1", text="list files")

    assert reply.text == "I will list files for you."
    assert len(reply.tool_calls) == 1
    assert reply.tool_calls[0].name == "workspace.list_files"
    assert reply.tool_calls[0].arguments == {"path": "."}
    assert reply.tool_calls[0].id == "call_1"


def test_runtime_returns_empty_tool_calls_when_none():
    """tool_calls가 없는 응답은 빈 튜플을 반환하는지 검증한다.

    최종 수정일: 2026-08-04
    """
    runtime = LangGraphConversationRuntime(FakeChatModel())

    reply = runtime.invoke(conversation_id="thread-1", text="hello")

    assert reply.tool_calls == ()
