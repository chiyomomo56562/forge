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
