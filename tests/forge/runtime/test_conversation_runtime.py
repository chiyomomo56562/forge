import json

from langchain_core.messages import AIMessage

from forge.domain.inner_loop import PlanStep, ToolExecution
from forge.domain.memory import ExecutionOutcome
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


class ToolLoopModel:
    def __init__(self, first: AIMessage, final: AIMessage | None = None) -> None:
        self._responses = [first, final or AIMessage(content="final")]
        self.calls = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return self._responses.pop(0)


class RecordingExecutor:
    def __init__(self, outcomes: dict[str, ExecutionOutcome] | None = None) -> None:
        self.calls: list[tuple[PlanStep, str]] = []
        self._outcomes = outcomes or {}

    def execute(self, step: PlanStep, *, session_id: str = "", attempt: int = 0):
        self.calls.append((step, session_id))
        outcome = self._outcomes.get(step.step_id, ExecutionOutcome.COMPLETED)
        code = None
        if outcome is ExecutionOutcome.HALTED:
            code = "tool.approval_required"
        elif outcome is ExecutionOutcome.FAILED:
            code = "tool.command_failed"
        return ToolExecution(
            step.step_id,
            f"summary {step.step_id}",
            outcome,
            (step.tool_name or "unknown",),
            safe_error_code=code,
            output={
                "value": step.tool_arguments.get("path", step.step_id),
                "audit_details": "must stay ordinary output only",
                "secret": "sk-" + ("a" * 20),
            },
        )


def _tool_ai(*calls):
    return AIMessage(content="", tool_calls=list(calls), response_metadata={"model_name": "fake"})


class MalformedProtocolModel:
    def __init__(self, failures_before_final: int) -> None:
        self.calls = []
        self._failures_before_final = failures_before_final

    def invoke(self, messages):
        from forge.adapters.outbound.llm.strategies import ToolCallingError

        self.calls.append(list(messages))
        if len(self.calls) <= self._failures_before_final:
            raise ToolCallingError("not valid JSON")
        return AIMessage(content="recovered")


def test_runtime_retries_malformed_protocol_with_synthetic_tool_message():
    model = MalformedProtocolModel(failures_before_final=1)
    runtime = LangGraphConversationRuntime(
        model,
        max_protocol_failures=2,
    )

    reply = runtime.invoke(conversation_id="conv-protocol", text="use tool")

    assert reply.text == "recovered"
    assert len(model.calls) == 2
    synthetic = model.calls[1][-1]
    assert synthetic.type == "tool"
    assert synthetic.tool_call_id == "tool_protocol"
    payload = json.loads(synthetic.content)
    assert payload["status"] == "failed"
    assert payload["name"] == "tool_protocol"


def test_runtime_exhausts_malformed_protocol_with_safe_final_response():
    model = MalformedProtocolModel(failures_before_final=10)
    runtime = LangGraphConversationRuntime(
        model,
        max_protocol_failures=1,
    )

    reply = runtime.invoke(conversation_id="conv-protocol-limit", text="use tool")

    assert reply.text == (
        "I could not safely process the malformed tool-call request. Please try again."
    )
    assert len(model.calls) == 2


class RepeatingToolModel:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return _tool_ai(
            {
                "name": "workspace.read_file",
                "args": {"path": f"note-{len(self.calls)}.txt"},
                "id": f"call_{len(self.calls)}",
            }
        )


def test_runtime_does_not_execute_tool_calls_without_executor():
    model = FakeToolCallChatModel()
    runtime = LangGraphConversationRuntime(model)

    reply = runtime.invoke(conversation_id="thread-no-executor", text="list files")

    assert reply.tool_calls[0].id == "call_1"
    state = runtime._graph.get_state(
        {"configurable": {"thread_id": "thread-no-executor"}}
    ).values
    assert [(message.type, message.content) for message in state["messages"]] == [
        ("human", "list files"),
        ("ai", "I will list files for you."),
    ]
