"""Constitution tool policy adapter for the shared tool invocation boundary."""

from forge.domain.constitution import ConstitutionPolicy, ToolPolicyAction, ToolPolicyDecision
from forge.domain.inner_loop import ToolDefinition, ToolInvocation
from forge.ports.outbound import ToolAuthorizationPolicy


class ConstitutionToolAuthorizationPolicy:
    """Apply stable constitution policy IDs before local runtime permissions."""

    def __init__(
        self, policy: ConstitutionPolicy, runtime_policy: ToolAuthorizationPolicy
    ) -> None:
        self._policy = policy
        self._runtime_policy = runtime_policy

    def evaluate(
        self, invocation: ToolInvocation, definition: ToolDefinition
    ) -> ToolPolicyDecision:
        if definition.policy_id is None:
            return ToolPolicyDecision(
                ToolPolicyAction.DENIED,
                "tool.policy_unmapped",
                definition.name,
            )
        policy_id = definition.policy_id
        decision = self._policy.evaluate_tool(policy_id)
        if decision.action is not ToolPolicyAction.ALLOWED:
            return decision
        if self._runtime_policy.authorize(invocation, definition):
            return decision
        return ToolPolicyDecision(
            ToolPolicyAction.DENIED,
            "tool.runtime_disallowed",
            policy_id,
        )

    def authorize(self, invocation: ToolInvocation, definition: ToolDefinition) -> bool:
        return self.evaluate(invocation, definition).allowed
