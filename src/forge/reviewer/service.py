from __future__ import annotations

from fnmatch import fnmatch
from json import JSONDecodeError, loads
from pathlib import Path
from uuid import uuid4

from openai_codex import Codex, Sandbox
from pydantic import BaseModel, Field, field_validator

from forge.contracts import (
    CodeContext,
    PatchChange,
    PolicyDocument,
    Priority,
    ReviewFeedback,
    ReviewIssue,
    ReviewStatus,
    ReviewType,
    ReviewerInput,
)


class PolicyRule(BaseModel):
    id: str
    description: str | None = None
    message: str | None = None
    severity: str = "medium"
    action: str = "warn"
    path_matches: str | None = None
    change_type_in: list[str] = Field(default_factory=list)
    require_target_file: bool | None = None
    require_diff: bool | None = None
    require_test_change: bool | None = None
    require_candidate_file: bool | None = None
    max_total_changes: int | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, value: object) -> str:
        return str(value or "medium").lower()

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: object) -> str:
        return str(value or "warn").lower()


class StaticReviewerService:
    """Fast, non-executing review pass over patch structure and plan alignment."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()

    def review(self, reviewer_input: ReviewerInput) -> ReviewFeedback:
        issues: list[ReviewIssue] = []
        executed_checks: list[str] = []

        issues.extend(self._check_patch_has_changes(reviewer_input, executed_checks))
        issues.extend(self._check_target_files_declared(reviewer_input, executed_checks))
        issues.extend(self._check_scope_alignment(reviewer_input, executed_checks))
        issues.extend(self._check_diff_presence(reviewer_input, executed_checks))
        issues.extend(self._check_new_file_policy(reviewer_input, executed_checks))
        issues.extend(self._check_policy_rules(reviewer_input, executed_checks))

        status = ReviewStatus.FAIL if any(issue.severity in {Priority.HIGH, Priority.CRITICAL} for issue in issues) else ReviewStatus.PASS
        if not issues:
            summary = "Static review passed with no issues."
        elif status == ReviewStatus.FAIL:
            summary = f"Static review failed with {len(issues)} issue(s)."
        else:
            summary = f"Static review found {len(issues)} non-blocking issue(s)."

        return ReviewFeedback(
            review_id=f"review-{uuid4()}",
            patch_id=reviewer_input.patch.patch_id,
            review_type=ReviewType.STATIC,
            status=status,
            summary=summary,
            issues=issues,
            executed_checks=executed_checks,
        )

    def _check_patch_has_changes(self, reviewer_input: ReviewerInput, executed_checks: list[str]) -> list[ReviewIssue]:
        executed_checks.append("patch_has_changes")
        patch = reviewer_input.patch
        if patch.changes or patch.test_changes:
            return []
        return [
            ReviewIssue(
                issue_id=f"issue-{uuid4()}",
                severity=Priority.HIGH,
                rule_id="STATIC-001",
                message="Patch contains no code or test changes.",
                suggestion="Return at least one concrete change or explain why no change is needed before review.",
            )
        ]

    def _check_target_files_declared(self, reviewer_input: ReviewerInput, executed_checks: list[str]) -> list[ReviewIssue]:
        executed_checks.append("target_files_declared")
        allowed = set(reviewer_input.plan.target_files)
        issues: list[ReviewIssue] = []

        for change in self._all_changes(reviewer_input):
            if change.path not in allowed:
                issues.append(
                    ReviewIssue(
                        issue_id=f"issue-{uuid4()}",
                        severity=Priority.HIGH,
                        rule_id="PLAN-003",
                        message=f"Changed file '{change.path}' is not listed in plan.target_files.",
                        file_path=change.path,
                        suggestion="Update the plan to include this file or keep the patch inside declared target files.",
                    )
                )
        return issues

    def _check_scope_alignment(self, reviewer_input: ReviewerInput, executed_checks: list[str]) -> list[ReviewIssue]:
        executed_checks.append("scope_alignment")
        goal_tokens = self._tokens(reviewer_input.user_request.normalized_goal)
        issues: list[ReviewIssue] = []

        for change in self._all_changes(reviewer_input):
            path_tokens = self._tokens(change.path)
            summary_tokens = self._tokens(change.summary)
            overlap = goal_tokens.intersection(path_tokens.union(summary_tokens))
            if not overlap:
                issues.append(
                    ReviewIssue(
                        issue_id=f"issue-{uuid4()}",
                        severity=Priority.MEDIUM,
                        rule_id="PLAN-001",
                        message=f"Change for '{change.path}' has weak overlap with the normalized goal.",
                        file_path=change.path,
                        suggestion="Double-check that this edit is necessary for the user request and describe the link more clearly.",
                    )
                )
        return issues

    def _check_diff_presence(self, reviewer_input: ReviewerInput, executed_checks: list[str]) -> list[ReviewIssue]:
        executed_checks.append("diff_presence")
        issues: list[ReviewIssue] = []

        for change in self._all_changes(reviewer_input):
            diff = change.diff.strip()
            if not diff:
                issues.append(
                    ReviewIssue(
                        issue_id=f"issue-{uuid4()}",
                        severity=Priority.HIGH,
                        rule_id="STATIC-002",
                        message=f"Change for '{change.path}' does not include a diff payload.",
                        file_path=change.path,
                        suggestion="Include a unified diff or structured patch text for every change.",
                    )
                )
            elif change.change_type == "modify" and "@@" not in diff and "--- " not in diff:
                issues.append(
                    ReviewIssue(
                        issue_id=f"issue-{uuid4()}",
                        severity=Priority.MEDIUM,
                        rule_id="STATIC-003",
                        message=f"Modify change for '{change.path}' does not look like a unified diff.",
                        file_path=change.path,
                        suggestion="Prefer unified diff fragments so later stages can reason about exact edits.",
                    )
                )
        return issues

    def _check_new_file_policy(self, reviewer_input: ReviewerInput, executed_checks: list[str]) -> list[ReviewIssue]:
        executed_checks.append("new_file_policy")
        issues: list[ReviewIssue] = []

        existing_files = {candidate.path for candidate in reviewer_input.plan.file_candidates}
        for change in self._all_changes(reviewer_input):
            if change.change_type == "add" and change.path not in existing_files:
                issues.append(
                    ReviewIssue(
                        issue_id=f"issue-{uuid4()}",
                        severity=Priority.MEDIUM,
                        rule_id="PLAN-002",
                        message=f"New file '{change.path}' was added outside the planner's candidate set.",
                        file_path=change.path,
                        suggestion="Confirm that a new file is truly needed and capture that reasoning in the plan.",
                    )
                )
        return issues

    def _check_policy_rules(self, reviewer_input: ReviewerInput, executed_checks: list[str]) -> list[ReviewIssue]:
        executed_checks.append("policy_rules")
        issues: list[ReviewIssue] = []
        rules = self._collect_policy_rules(
            reviewer_input.l0_constitution,
            reviewer_input.l1_project_policy,
        )
        if not rules:
            return issues

        all_changes = self._all_changes(reviewer_input)
        candidate_paths = {candidate.path for candidate in reviewer_input.plan.file_candidates}
        target_paths = set(reviewer_input.plan.target_files)
        has_test_change = any(change.path in target_paths and self._looks_like_test_path(change.path) for change in reviewer_input.patch.test_changes)

        for rule in rules:
            if rule.max_total_changes is not None and len(all_changes) > rule.max_total_changes:
                issues.append(
                    self._make_issue(
                        rule=rule,
                        message=rule.message or f"Patch exceeds the allowed change count ({rule.max_total_changes}).",
                    )
                )

            if rule.require_test_change and not has_test_change:
                issues.append(
                    self._make_issue(
                        rule=rule,
                        message=rule.message or "Policy requires at least one test change for this patch.",
                    )
                )

            for change in all_changes:
                if not self._rule_applies_to_change(rule, change):
                    continue
                if rule.require_target_file and change.path not in target_paths:
                    issues.append(
                        self._make_issue(
                            rule=rule,
                            message=rule.message or f"Policy requires '{change.path}' to be listed in target_files.",
                            file_path=change.path,
                        )
                    )
                if rule.require_diff and not change.diff.strip():
                    issues.append(
                        self._make_issue(
                            rule=rule,
                            message=rule.message or f"Policy requires a diff payload for '{change.path}'.",
                            file_path=change.path,
                        )
                    )
                if rule.require_candidate_file and change.path not in candidate_paths:
                    issues.append(
                        self._make_issue(
                            rule=rule,
                            message=rule.message or f"Policy requires '{change.path}' to come from localizer candidates.",
                            file_path=change.path,
                        )
                    )

        return issues

    def _collect_policy_rules(self, l0_document: PolicyDocument, l1_documents: list[PolicyDocument]) -> list[PolicyRule]:
        rules: list[PolicyRule] = []
        for document in [l0_document, *l1_documents]:
            raw_rules = document.content.get("rules", [])
            if not isinstance(raw_rules, list):
                continue
            for raw_rule in raw_rules:
                if not isinstance(raw_rule, dict):
                    continue
                rules.append(PolicyRule.model_validate(raw_rule))
        return rules

    def _rule_applies_to_change(self, rule: PolicyRule, change: PatchChange) -> bool:
        if rule.path_matches and not fnmatch(change.path, rule.path_matches):
            return False
        if rule.change_type_in and change.change_type not in set(rule.change_type_in):
            return False
        return True

    def _make_issue(self, *, rule: PolicyRule, message: str, file_path: str | None = None) -> ReviewIssue:
        severity = self._priority_from_rule(rule)
        return ReviewIssue(
            issue_id=f"issue-{uuid4()}",
            severity=severity,
            rule_id=rule.id,
            message=message,
            file_path=file_path,
            suggestion=rule.description,
        )

    def _all_changes(self, reviewer_input: ReviewerInput) -> list[PatchChange]:
        return [*reviewer_input.patch.changes, *reviewer_input.patch.test_changes]

    def _priority_from_rule(self, rule: PolicyRule) -> Priority:
        mapping = {
            "low": Priority.LOW,
            "medium": Priority.MEDIUM,
            "high": Priority.HIGH,
            "critical": Priority.CRITICAL,
        }
        severity = mapping.get(rule.severity, Priority.MEDIUM)
        if rule.action == "fail" and severity in {Priority.LOW, Priority.MEDIUM}:
            return Priority.HIGH
        return severity

    def _looks_like_test_path(self, path: str) -> bool:
        lower = path.lower()
        return (
            lower.startswith("tests/")
            or "/tests/" in lower
            or lower.endswith("_test.py")
            or lower.endswith(".test.ts")
            or lower.endswith(".spec.ts")
            or lower.endswith(".test.js")
            or lower.endswith(".spec.js")
        )

    def _tokens(self, text: str) -> set[str]:
        cleaned = (
            text.lower()
            .replace("/", " ")
            .replace("\\", " ")
            .replace("_", " ")
            .replace("-", " ")
            .replace(".", " ")
        )
        return {token for token in cleaned.split() if len(token) >= 3}


class CodexReviewerService:
    """Semantic review using Codex SDK over patch, plan, context, and policy documents."""

    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        model: str = "gpt-5.4-mini",
        sandbox: Sandbox = Sandbox.read_only,
    ) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.model = model
        self.sandbox = sandbox

    def review(self, reviewer_input: ReviewerInput) -> ReviewFeedback:
        prompt = self.build_prompt(reviewer_input)

        with Codex() as codex:
            thread = codex.thread_start(
                model=self.model,
                cwd=str(self.project_root),
                sandbox=self.sandbox,
            )
            result = thread.run(prompt)

        if not result.final_response:
            raise ValueError("Codex returned no final response for semantic review.")

        try:
            payload = loads(result.final_response)
        except JSONDecodeError as exc:
            raise ValueError("Codex did not return valid JSON for ReviewFeedback.") from exc

        payload.setdefault("review_id", f"review-{uuid4()}")
        payload.setdefault("patch_id", reviewer_input.patch.patch_id)
        payload.setdefault("review_type", ReviewType.STATIC)
        return ReviewFeedback.model_validate(payload)

    def build_prompt(self, reviewer_input: ReviewerInput) -> str:
        policies = self._render_policies(
            reviewer_input.l0_constitution,
            reviewer_input.l1_project_policy,
        )
        code_context = self._render_code_context(reviewer_input.code_context)
        patch_text = self._render_patch(reviewer_input.patch)
        review_focus = "\n".join(f"- {item}" for item in reviewer_input.plan.review_focus) or "- none"
        success_checks = "\n".join(f"- {item}" for item in reviewer_input.plan.success_checks) or "- none"

        return (
            "You are the semantic review stage in a coding pipeline.\n"
            "Evaluate the patch for correctness, scope control, design fit, policy violations, regression risk, "
            "and missing tests. Review deeply, but return only JSON.\n\n"
            "Output rules:\n"
            "- Return valid JSON matching the ReviewFeedback contract.\n"
            "- review_type must be 'static'.\n"
            "- status must be 'pass' or 'fail'.\n"
            "- Put actionable findings in issues.\n"
            "- Use high or critical severity for blocking issues.\n"
            "- If there are no meaningful issues, return an empty issues list.\n\n"
            f"User request:\n{reviewer_input.user_request.user_text}\n\n"
            f"Normalized goal:\n{reviewer_input.user_request.normalized_goal}\n\n"
            f"Plan summary:\n{reviewer_input.plan.summary}\n\n"
            f"Plan strategy:\n{reviewer_input.plan.strategy}\n\n"
            f"Review focus:\n{review_focus}\n\n"
            f"Success checks:\n{success_checks}\n\n"
            f"Policies:\n{policies}\n\n"
            f"Patch:\n{patch_text}\n\n"
            f"Code context:\n{code_context}\n\n"
            "Return JSON with this shape:\n"
            "{\n"
            '  "review_id": "optional-string",\n'
            f'  "patch_id": "{reviewer_input.patch.patch_id}",\n'
            '  "review_type": "static",\n'
            '  "status": "pass|fail",\n'
            '  "summary": "short review summary",\n'
            '  "issues": [\n'
            "    {\n"
            '      "issue_id": "optional-string",\n'
            '      "severity": "low|medium|high|critical",\n'
            '      "rule_id": "optional-rule-id",\n'
            '      "message": "what is wrong",\n'
            '      "file_path": "optional/path.py",\n'
            '      "line_start": 1,\n'
            '      "line_end": 2,\n'
            '      "suggestion": "how to fix it"\n'
            "    }\n"
            "  ],\n"
            '  "executed_checks": ["semantic_policy_review", "semantic_patch_review"],\n'
            '  "artifacts": []\n'
            "}"
        )

    def _render_policies(self, l0_document: PolicyDocument, l1_documents: list[PolicyDocument]) -> str:
        lines: list[str] = []
        for document in [l0_document, *l1_documents]:
            lines.append(f"[{document.layer}] {document.name} ({document.path})")
            if document.content:
                lines.append(str(document.content))
            else:
                lines.append("{}")
        return "\n".join(lines)

    def _render_patch(self, patch) -> str:
        sections = [f"Rationale: {patch.rationale}"]
        for label, changes in (("changes", patch.changes), ("test_changes", patch.test_changes)):
            sections.append(f"{label}:")
            if not changes:
                sections.append("- none")
                continue
            for change in changes:
                sections.append(
                    f"- path={change.path}, type={change.change_type}, summary={change.summary}\n{change.diff}"
                )
        return "\n".join(sections)

    def _render_code_context(self, code_context: CodeContext | None) -> str:
        if code_context is None:
            return "No code context provided."

        blocks: list[str] = []
        for snippet in code_context.snippets[:8]:
            blocks.append(
                f"FILE: {snippet.path}:{snippet.start_line}-{snippet.end_line}\n"
                f"REASON: {snippet.reason}\n"
                "```python\n"
                f"{snippet.content}\n"
                "```"
            )
        if code_context.related_context:
            blocks.append(
                "Related context:\n"
                + "\n".join(f"- {item.kind} ({item.ref_id}): {item.summary}" for item in code_context.related_context)
            )
        if code_context.open_questions:
            blocks.append("Open questions:\n" + "\n".join(f"- {item}" for item in code_context.open_questions))
        return "\n\n".join(blocks) if blocks else "No snippets provided."
