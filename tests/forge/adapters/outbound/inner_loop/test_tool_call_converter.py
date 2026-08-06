"""ToolCallData에서 InnerLoopPlan으로 변환하는 adapter 테스트."""

from collections.abc import Mapping

import pytest

from forge.adapters.outbound.inner_loop.tool_call_converter import (
    ToolCallConversionError,
    convert_tool_calls_to_plan,
)
from forge.domain.llm import ToolCallData


def test_converter_preserves_order_ids_names_and_arguments():
    calls = (
        ToolCallData(
            id="call_read",
            name="workspace.read_file",
            arguments={"path": "README.md"},
        ),
        ToolCallData(
            id="call_unknown",
            name="unregistered.future_tool",
            arguments={"value": 1},
        ),
    )

    plan = convert_tool_calls_to_plan(calls)

    assert [step.step_id for step in plan.steps] == ["call_read", "call_unknown"]
    assert [step.tool_name for step in plan.steps] == [
        "workspace.read_file",
        "unregistered.future_tool",
    ]
    assert [step.tool_arguments for step in plan.steps] == [
        {"path": "README.md"},
        {"value": 1},
    ]


def test_converter_generates_stable_ids_for_absent_call_ids():
    calls = (
        ToolCallData(name="workspace.list_files", arguments={"path": "."}),
        ToolCallData(name="workspace.read_file", arguments={"path": "pyproject.toml"}),
    )

    first = convert_tool_calls_to_plan(calls)
    second = convert_tool_calls_to_plan(calls)

    assert [step.step_id for step in first.steps] == ["tool_call_1", "tool_call_2"]
    assert [step.step_id for step in second.steps] == ["tool_call_1", "tool_call_2"]


def test_converter_rejects_duplicate_ids():
    calls = (
        ToolCallData(id="call_1", name="workspace.list_files", arguments={}),
        ToolCallData(id="call_1", name="workspace.read_file", arguments={"path": "README.md"}),
    )

    with pytest.raises(ToolCallConversionError, match="Duplicate tool call id: call_1"):
        convert_tool_calls_to_plan(calls)


def test_converter_rejects_duplicate_generated_and_model_ids():
    calls = (
        ToolCallData(name="workspace.list_files", arguments={}),
        ToolCallData(id="tool_call_1", name="workspace.read_file", arguments={}),
    )

    with pytest.raises(ToolCallConversionError, match="Duplicate tool call id: tool_call_1"):
        convert_tool_calls_to_plan(calls)


def test_converter_rejects_empty_name():
    calls = (ToolCallData(name=" ", arguments={}),)

    with pytest.raises(ToolCallConversionError, match="missing or invalid name"):
        convert_tool_calls_to_plan(calls)


def test_converter_rejects_non_mapping_arguments():
    calls = (ToolCallData(name="workspace.list_files", arguments=[]),)  # type: ignore[arg-type]

    with pytest.raises(ToolCallConversionError, match="arguments must be a mapping"):
        convert_tool_calls_to_plan(calls)


def test_converter_accepts_mapping_arguments_subclass():
    class CustomMapping(Mapping[str, object]):
        def __init__(self) -> None:
            self._items = {"path": "."}

        def __getitem__(self, key: str) -> object:
            return self._items[key]

        def __iter__(self):
            return iter(self._items)

        def __len__(self) -> int:
            return len(self._items)

    plan = convert_tool_calls_to_plan((
        ToolCallData(name="workspace.list_files", arguments=CustomMapping()),
    ))

    assert plan.steps[0].tool_arguments == {"path": "."}
