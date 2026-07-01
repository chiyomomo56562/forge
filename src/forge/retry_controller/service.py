from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from forge.contracts import (
    Priority,
    RetryAction,
    RetryControllerInput,
    RetryDecision,
    ReviewIssue,
    ReviewStatus,
)


class RetryControllerService:
    """Simple decision maker for retry_once, replan, or escalate."""

    REPLAN_RULE_IDS = {
        "PLAN-001",
        "PLAN-003",
    }

    ESCALATE_RULE_TOKENS = {
        "SECURITY",
        "L0",
    }

    def __init__(self, project_root: str | Path | None = None, *, max_retry_count: int = 1) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.max_retry_count = max_retry_count

    def decide(self, retry_input: RetryControllerInput) -> RetryDecision:
        feedback = retry_input.feedback

        if feedback.status == ReviewStatus.PASS:
            return RetryDecision(
                decision_id=f"decision-{uuid4()}",
                request_id=retry_input.user_request.request_id,
                patch_id=feedback.patch_id,
                action=RetryAction.COMPLETE,
                reason="Review passed, so no retry or replan is needed.",
                feedback_refs=[feedback.review_id],
                next_step_hint="Proceed to the next stage.",
                retry_count=retry_input.retry_count,
            )

        issues = feedback.issues
        if self._should_escalate(retry_input.retry_count, issues):
            return RetryDecision(
                decision_id=f"decision-{uuid4()}",
                request_id=retry_input.user_request.request_id,
                patch_id=feedback.patch_id,
                action=RetryAction.HUMAN_ESCALATION,
                reason="The review found high-risk issues or the retry budget was exhausted.",
                feedback_refs=[feedback.review_id],
                next_step_hint="Ask for human input or explicit approval before continuing.",
                retry_count=retry_input.retry_count,
            )

        if self._should_replan(issues):
            return RetryDecision(
                decision_id=f"decision-{uuid4()}",
                request_id=retry_input.user_request.request_id,
                patch_id=feedback.patch_id,
                action=RetryAction.REPLAN,
                reason="The issues point to planning or targeting problems rather than a simple patch correction.",
                feedback_refs=[feedback.review_id],
                next_step_hint="Re-run the planner with the review findings folded into the new plan.",
                retry_count=retry_input.retry_count,
            )

        return RetryDecision(
            decision_id=f"decision-{uuid4()}",
            request_id=retry_input.user_request.request_id,
            patch_id=feedback.patch_id,
            action=RetryAction.TARGETED_RETRY,
            reason="The issues look localized enough for one focused coder retry.",
            feedback_refs=[feedback.review_id],
            next_step_hint=self._build_retry_hint(issues),
            retry_count=retry_input.retry_count + 1,
        )

    def _should_escalate(self, retry_count: int, issues: list[ReviewIssue]) -> bool:
        if retry_count >= self.max_retry_count:
            return True
        for issue in issues:
            if issue.severity == Priority.CRITICAL:
                return True
            rule_id = (issue.rule_id or "").upper()
            if any(token in rule_id for token in self.ESCALATE_RULE_TOKENS):
                return True
        return False

    def _should_replan(self, issues: list[ReviewIssue]) -> bool:
        high_count = 0
        for issue in issues:
            if issue.severity in {Priority.HIGH, Priority.CRITICAL}:
                high_count += 1
            if (issue.rule_id or "") in self.REPLAN_RULE_IDS:
                return True
            message = issue.message.lower()
            if "target_files" in message or "scope" in message:
                return True
        return high_count >= 2

    def _build_retry_hint(self, issues: list[ReviewIssue]) -> str:
        actionable = [issue.suggestion for issue in issues if issue.suggestion]
        if actionable:
            return "Fix the reported issues: " + " | ".join(actionable[:3])
        messages = [issue.message for issue in issues]
        if messages:
            return "Address the review findings: " + " | ".join(messages[:3])
        return "Adjust the patch based on the latest review feedback."
