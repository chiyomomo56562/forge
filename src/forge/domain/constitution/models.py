"""Read-only constitutional decisions used by runtime loops."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class CibDecision:
    allowed: bool
    reason_code: str


class ToolPolicyAction(StrEnum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"


@dataclass(frozen=True)
class ToolPolicyDecision:
    action: ToolPolicyAction
    reason_code: str
    policy_id: str

    @property
    def allowed(self) -> bool:
        return self.action is ToolPolicyAction.ALLOWED


@dataclass(frozen=True)
class ConstitutionPolicy:
    version: int
    cib_threshold: float
    sensitive_patterns: tuple[str, ...]
    autonomous_tool_ids: tuple[str, ...] = ()
    confirmation_required_tool_ids: tuple[str, ...] = ()
    forbidden_tool_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.version <= 0 or not 0.0 <= self.cib_threshold <= 1.0:
            raise ValueError("Constitution policy is invalid.")
        classes = (
            *self.autonomous_tool_ids,
            *self.confirmation_required_tool_ids,
            *self.forbidden_tool_ids,
        )
        if len(set(classes)) != len(classes) or any(not item.strip() for item in classes):
            raise ValueError("Constitution tool policy IDs must be unique and non-empty.")

    def evaluate_tool(self, policy_id: str) -> ToolPolicyDecision:
        if policy_id in self.forbidden_tool_ids:
            return ToolPolicyDecision(ToolPolicyAction.DENIED, "tool.policy_forbidden", policy_id)
        if policy_id in self.confirmation_required_tool_ids:
            return ToolPolicyDecision(
                ToolPolicyAction.APPROVAL_REQUIRED,
                "tool.approval_required",
                policy_id,
            )
        if policy_id in self.autonomous_tool_ids:
            return ToolPolicyDecision(ToolPolicyAction.ALLOWED, "tool.policy_allowed", policy_id)
        return ToolPolicyDecision(ToolPolicyAction.DENIED, "tool.policy_unmapped", policy_id)
