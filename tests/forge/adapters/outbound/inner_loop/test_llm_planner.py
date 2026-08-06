"""LLMPlanner의 구조화 응답 파싱, 도구 검증, 예외 처리 테스트.

최종 수정일: 2026-07-31
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from forge.adapters.outbound.inner_loop.llm_planner import (
    LLMPlanner,
    NativeToolCallPlanner,
    PlanGenerationError,
)
from forge.bootstrap import container
from forge.domain.inner_loop import InnerLoopPlan, ToolSchema
from forge.domain.llm import ChatMessage, ModelResponse, ToolCallData


class FakeStructuredModel:
    """고정 dict를 반환하는 fake StructuredChatModel."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = data
        self.calls: list[list[ChatMessage]] = []

    def invoke(self, messages: Sequence[ChatMessage]) -> Mapping[str, Any]:
        self.calls.append(list(messages))
        return self._data


class FailingStructuredModel:
    """예외를 발생시키는 fake StructuredChatModel."""

    def invoke(self, messages: Sequence[ChatMessage]) -> Mapping[str, Any]:
        raise RuntimeError("Model server unavailable")


class FakeToolModel:
    """고정 ModelResponse를 반환하는 fake ChatModel."""

    def __init__(self, response: ModelResponse) -> None:
        self._response = response
        self.calls: list[list[ChatMessage]] = []

    def invoke(self, messages: Sequence[ChatMessage]) -> ModelResponse:
        self.calls.append(list(messages))
        return self._response


class FakeToolModelFactory:
    """tool model 생성 호출을 기록하는 fake factory."""

    def __init__(self, model: FakeToolModel) -> None:
        self._model = model
        self.tools: Sequence[ToolSchema] | None = None

    def create_tool_model(self, tools: Sequence[ToolSchema]) -> FakeToolModel:
        self.tools = tools
        return self._model


class FakeRegistry:
    """tool_schemas만 제공하는 fake registry."""

    def tool_schemas(self) -> Sequence[ToolSchema]:
        return _TOOL_SCHEMAS


_TOOL_SCHEMAS = (
    ToolSchema(
        "workspace.list_files",
        "List files under a workspace path.",
        {"type": "object", "properties": {}},
    ),
    ToolSchema(
        "workspace.read_file",
        "Read a UTF-8 text file.",
        {"type": "object", "properties": {}, "required": ["path"]},
    ),
)


def test_multi_step_plan_from_structured_response():
    """2-step 구조화 응답이 2개 PlanStep으로 변환되는지 검증한다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "List workspace files",
                "tool_name": "workspace.list_files",
                "tool_arguments": {"path": "."},
            },
            {
                "summary": "Read the config file",
                "tool_name": "workspace.read_file",
                "tool_arguments": {"path": "config/agent.yml"},
            },
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    plan = planner.create_plan(task_request="Inspect the workspace", context_episode_ids=())

    assert isinstance(plan, InnerLoopPlan)
    assert len(plan.steps) == 2
    assert plan.steps[0].step_id == "step_1"
    assert plan.steps[0].tool_name == "workspace.list_files"
    assert plan.steps[0].tool_arguments == {"path": "."}
    assert plan.steps[1].step_id == "step_2"
    assert plan.steps[1].tool_name == "workspace.read_file"
    assert plan.steps[1].tool_arguments == {"path": "config/agent.yml"}


def test_empty_steps_raises():
    """빈 steps 배열이면 PlanGenerationError를 발생시킨다."""
    model = FakeStructuredModel({"steps": []})
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    with pytest.raises(PlanGenerationError, match="no tool calls"):
        planner.create_plan(task_request="test", context_episode_ids=())


def test_missing_steps_raises():
    """steps 키가 없으면 PlanGenerationError를 발생시킨다."""
    model = FakeStructuredModel({"other": "data"})
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    with pytest.raises(PlanGenerationError, match="missing 'steps'"):
        planner.create_plan(task_request="test", context_episode_ids=())


def test_unknown_tool_name_raises():
    """허용되지 않은 tool name이면 PlanGenerationError를 발생시킨다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "Run something",
                "tool_name": "nonexistent.tool",
                "tool_arguments": {},
            }
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    with pytest.raises(PlanGenerationError, match="unknown tool: nonexistent.tool"):
        planner.create_plan(task_request="test", context_episode_ids=())


def test_missing_tool_name_raises():
    """tool_name이 누락되면 PlanGenerationError를 발생시킨다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "Step without tool",
                "tool_arguments": {},
            }
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    with pytest.raises(PlanGenerationError, match="missing or invalid 'tool_name'"):
        planner.create_plan(task_request="test", context_episode_ids=())


def test_non_dict_arguments_raises():
    """tool_arguments가 dict가 아니면 PlanGenerationError를 발생시킨다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "Bad args",
                "tool_name": "workspace.list_files",
                "tool_arguments": "not a dict",
            }
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    with pytest.raises(PlanGenerationError, match="'tool_arguments' is not an object"):
        planner.create_plan(task_request="test", context_episode_ids=())


def test_model_exception_propagates():
    """모델 호출 예외가 빈 plan이 아닌 예외로 전파되는지 검증한다."""
    planner = LLMPlanner(FailingStructuredModel(), tool_schemas=_TOOL_SCHEMAS)

    with pytest.raises(RuntimeError, match="Model server unavailable"):
        planner.create_plan(task_request="test", context_episode_ids=())


def test_step_order_preserved():
    """다중 step의 순서가 유지되는지 검증한다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "First step",
                "tool_name": "workspace.list_files",
                "tool_arguments": {"path": "src"},
            },
            {
                "summary": "Second step",
                "tool_name": "workspace.read_file",
                "tool_arguments": {"path": "README.md"},
            },
            {
                "summary": "Third step",
                "tool_name": "workspace.list_files",
                "tool_arguments": {"path": "tests"},
            },
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    plan = planner.create_plan(task_request="test", context_episode_ids=())

    summaries = [step.summary for step in plan.steps]
    assert summaries == ["First step", "Second step", "Third step"]


def test_tool_arguments_preserved():
    """LLM args가 PlanStep.tool_arguments로 그대로 전달되는지 검증한다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "Read file",
                "tool_name": "workspace.read_file",
                "tool_arguments": {"path": "config.yml", "offset": 100, "max_bytes": 2048},
            }
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    plan = planner.create_plan(task_request="test", context_episode_ids=())

    assert plan.steps[0].tool_arguments == {"path": "config.yml", "offset": 100, "max_bytes": 2048}


def test_context_episode_ids_accepted_but_not_in_messages():
    """context_episode_ids 인자는 받지만 모델 메시지에 포함되지 않는지 검증한다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "List files",
                "tool_name": "workspace.list_files",
                "tool_arguments": {},
            }
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    planner.create_plan(task_request="test", context_episode_ids=("ep_1", "ep_2"))

    messages = model.calls[0]
    assert len(messages) == 2
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[1].content == "test"
    # episode IDs should not appear in any message
    for msg in messages:
        assert "ep_1" not in msg.content
        assert "ep_2" not in msg.content


def test_empty_system_prompt_rejected():
    """빈 문자열 system_prompt는 ValueError를 발생시킨다."""
    with pytest.raises(ValueError, match="system_prompt must not be empty"):
        LLMPlanner(
            FakeStructuredModel({"steps": []}),
            tool_schemas=_TOOL_SCHEMAS,
            system_prompt="   ",
        )


def test_default_prompt_includes_tool_descriptions():
    """기본 prompt에 도구 이름과 설명이 포함되는지 검증한다."""
    model = FakeStructuredModel({
        "steps": [
            {
                "summary": "List files",
                "tool_name": "workspace.list_files",
                "tool_arguments": {},
            }
        ]
    })
    planner = LLMPlanner(model, tool_schemas=_TOOL_SCHEMAS)

    planner.create_plan(task_request="test", context_episode_ids=())

    system_message = model.calls[0][0].content
    assert "workspace.list_files" in system_message
    assert "List files under a workspace path." in system_message
    assert "workspace.read_file" in system_message


def test_native_tool_call_planner_converts_model_tool_calls():
    model = FakeToolModel(
        ModelResponse(
            content="",
            tool_calls=(
                ToolCallData(
                    id="call_read",
                    name="workspace.read_file",
                    arguments={"path": "README.md"},
                ),
            ),
        )
    )
    planner = NativeToolCallPlanner(model)

    plan = planner.create_plan(task_request="Read README", context_episode_ids=("ep_1",))

    assert plan.steps[0].step_id == "call_read"
    assert plan.steps[0].tool_name == "workspace.read_file"
    assert plan.steps[0].tool_arguments == {"path": "README.md"}
    messages = model.calls[0]
    assert [message.role for message in messages] == ["system", "user"]
    assert messages[1].content == "Read README"
    assert "ep_1" not in messages[0].content


def test_native_tool_call_planner_raises_plan_generation_error_for_no_calls():
    planner = NativeToolCallPlanner(FakeToolModel(ModelResponse(content="No tool needed")))

    with pytest.raises(PlanGenerationError, match="no tool calls"):
        planner.create_plan(task_request="test", context_episode_ids=())


def test_native_tool_call_planner_rejects_empty_system_prompt():
    with pytest.raises(ValueError, match="system_prompt must not be empty"):
        NativeToolCallPlanner(FakeToolModel(ModelResponse(content="")), system_prompt=" ")


def test_build_planner_supports_native_tool_type(monkeypatch):
    model = FakeToolModel(
        ModelResponse(
            content="",
            tool_calls=(
                ToolCallData(
                    id="call_list",
                    name="workspace.list_files",
                    arguments={"path": "."},
                ),
            ),
        )
    )
    factory = FakeToolModelFactory(model)
    monkeypatch.setattr(container, "_build_chat_model_factory", lambda _config: factory)

    planner = container._build_planner(
        {
            "inner_loop": {
                "planner": {
                    "type": "native_tool",
                    "system_prompt": "Plan with tool calls.",
                }
            }
        },
        FakeRegistry(),
    )

    assert isinstance(planner, NativeToolCallPlanner)
    assert factory.tools == _TOOL_SCHEMAS
    plan = planner.create_plan(task_request="List files", context_episode_ids=())
    assert plan.steps[0].step_id == "call_list"
