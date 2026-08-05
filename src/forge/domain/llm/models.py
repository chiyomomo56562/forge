"""provider와 무관한 LLM 모델 응답 및 메시지 값.

최종 수정일: 2026-08-04
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ChatMessage:
    """LLM 호출에 사용하는 단일 메시지.

    LangChain 메시지 타입에 의존하지 않고, adapter가 provider 형식으로 변환한다.

    Args:
        role: ``"system"`` | ``"user"`` | ``"assistant"``.
        content: 메시지 텍스트.

    최종 수정일: 2026-08-04
    """

    role: str
    content: str


@dataclass(frozen=True)
class ToolCallData:
    """모델 응답에 포함된 단일 도구 호출.

    Args:
        name: 모델이 선택한 도구 이름.
        arguments: 도구에 전달할 JSON 인자.
        id: 제공자가 부여한 호출 식별자.

    최종 수정일: 2026-08-04
    """

    name: str
    arguments: Mapping[str, Any]
    id: str = ""


@dataclass(frozen=True)
class ModelResponse:
    """``ChatModel.invoke`` 가 반환하는 provider 무관 응답.

    Args:
        content: 모델이 생성한 텍스트.
        tool_calls: 모델이 요청한 도구 호출 목록. 도구 미사용 시 빈 튜플.
        model_name: 응답 생성에 사용된 모델 식별자.

    최종 수정일: 2026-08-04
    """

    content: str
    tool_calls: tuple[ToolCallData, ...] = ()
    model_name: str = ""
    raw: Any = None
