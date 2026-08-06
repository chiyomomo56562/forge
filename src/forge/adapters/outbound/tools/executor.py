"""도구 registry 결과를 기존 Inner Loop 단일 attempt 계약으로 변환한다.

최종 수정일: 2026-07-31
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any, cast

from forge.adapters.outbound.tools.builtin import ToolInvocationError
from forge.domain.inner_loop import PlanStep, ToolExecution, ToolInvocation, ToolStatus
from forge.domain.memory import ExecutionOutcome
from forge.ports.outbound import ToolAuthorizationPolicy, ToolRegistry

_ARTIFACT_ID = re.compile(r"art_[A-Za-z0-9_-]+\Z")


class RegistryPlanStepExecutor:
    """registry·정책·handler를 거쳐 정확히 한 도구 attempt를 수행한다.

    Args:
        registry: 도구 이름, 입력 schema, handler를 소유하는 adapter.
        authorization: 도구 위험도별 실행 허용 여부를 결정하는 policy.

    최종 수정일: 2026-07-31
    """

    def __init__(self, registry: ToolRegistry, authorization: ToolAuthorizationPolicy) -> None:
        self._registry = registry
        self._authorization = authorization

    def execute(self, step: PlanStep, *, session_id: str = "", attempt: int = 0) -> ToolExecution:
        """계획 단계 하나를 검증·인가·실행하고 Inner Loop 결과로 변환한다.

        Args:
            step: 실행할 planner 단계.
            session_id: L0 causation과 도구 호출을 묶는 현재 세션 ID.
            attempt: Inner Loop가 관리하는 현재 attempt 번호.

        Returns:
            재시도 책임을 가진 orchestrator가 바로 소비할 단일 attempt 결과.

        최종 수정일: 2026-07-31
        """
        if step.tool_name is None:
            return ToolExecution(step.step_id, step.summary, ExecutionOutcome.COMPLETED)
        try:
            definition = self._registry.definition_for(step.tool_name)
            arguments = self._registry.validate_arguments(step.tool_name, step.tool_arguments)
        except ToolInvocationError as exc:
            return self._halted(step, exc.code)
        invocation = ToolInvocation(step.tool_name, arguments, session_id, step.step_id, attempt)
        if not self._authorization.authorize(invocation, definition):
            return self._halted(step, "tool.approval_required", arguments)
        try:
            result = self._registry.execute(invocation)
        except ToolInvocationError as exc:
            return self._halted(step, exc.code, arguments)
        if not all(_ARTIFACT_ID.fullmatch(value) for value in result.output_artifact_refs):
            return self._halted(step, "tool.invalid_artifact_ref", arguments)
        outcome = {
            ToolStatus.COMPLETED: ExecutionOutcome.COMPLETED,
            ToolStatus.FAILED: ExecutionOutcome.FAILED,
            ToolStatus.DENIED: ExecutionOutcome.HALTED,
        }[result.status]
        return ToolExecution(
            step.step_id,
            result.summary,
            outcome,
            (step.tool_name,),
            retryable=result.retryable if outcome is ExecutionOutcome.FAILED else False,
            safe_error_code=result.safe_error_code,
            audit_details={
                "tool_status": result.status.value,
                "tool_arguments": _audit_arguments(arguments),
                **result.audit_details,
                "duration_ms": result.duration_ms,
            },
            output=result.output,
            output_artifact_refs=result.output_artifact_refs,
            truncated=result.truncated,
        )

    @staticmethod
    def _halted(
        step: PlanStep, code: str, arguments: Mapping[str, object] | None = None
    ) -> ToolExecution:
        """정책·방어적 검증 거부를 retry 불가 halted 실행으로 만든다.

        최종 수정일: 2026-07-31
        """
        details: dict[str, Any] = {"tool_status": ToolStatus.DENIED.value}
        if arguments is not None:
            details["tool_arguments"] = _audit_arguments(arguments)
        return ToolExecution(
            step.step_id,
            "Tool invocation was denied.",
            ExecutionOutcome.HALTED,
            (step.tool_name,) if step.tool_name else (),
            safe_error_code=code,
            audit_details=details,
        )


def _audit_arguments(value: Mapping[str, object]) -> dict[str, object]:
    """민감할 수 있는 문자열을 hash로 바꿔 안정적인 L0 인자 표현을 만든다.

    최종 수정일: 2026-07-31
    """
    canonical = json.loads(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return cast(dict[str, object], _redact_strings(canonical))


def _redact_strings(value: object) -> object:
    """L0 근거 이벤트에 raw 문자열 인자가 남지 않게 재귀적으로 변환한다.

    최종 수정일: 2026-07-31
    """
    if isinstance(value, str):
        return {"sha256": sha256(value.encode("utf-8")).hexdigest(), "length": len(value)}
    if isinstance(value, list):
        return [_redact_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_strings(item) for key, item in value.items()}
    return value
