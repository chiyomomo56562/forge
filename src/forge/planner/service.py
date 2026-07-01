from __future__ import annotations

from json import JSONDecodeError, loads
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import BaseModel, Field, field_validator

from forge.contracts import (
    FileCandidate,
    PlannerInput,
    PlanStep,
    PolicyDocument,
    Priority,
    StructuredPlan,
)


class PlannerRuleConfig(BaseModel):
    id: str
    title: str
    severity: str
    description: str
    action: str


class TargetFileRules(BaseModel):
    max_target_files: int = Field(default=5, ge=1)
    allow_new_files: bool = True
    new_file_requires_reason: bool = True
    prefer_candidate_files: bool = True


class RiskPolicy(BaseModel):
    human_required_when: list[str] = Field(default_factory=list)


class ReplanPolicy(BaseModel):
    replan_when: list[str] = Field(default_factory=list)


class PlannerConfig(BaseModel):
    id: str = "PLANNER"
    version: str = "0.1"
    planning_rules: list[PlannerRuleConfig] = Field(default_factory=list)
    target_file_rules: TargetFileRules = Field(default_factory=TargetFileRules)
    risk_policy: RiskPolicy = Field(default_factory=RiskPolicy)
    replan_policy: ReplanPolicy = Field(default_factory=ReplanPolicy)

    @field_validator("version", mode="before")
    @classmethod
    def coerce_version(cls, value: object) -> str:
        return str(value)


class PlannerService:
    """Heuristic planner that converts localizer output into a structured execution plan."""

    def __init__(self, project_root: str | Path | None = None, config_path: str | Path | None = None) -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.config_path = Path(config_path) if config_path else self.project_root / ".agents" / "planner.yaml"
        self.config = self._load_config()

    def create_plan(
        self,
        planner_input: PlannerInput,
        *,
        codex_fallback: CodexPlannerFallback | None = None,
    ) -> StructuredPlan:
        selected_candidates = planner_input.file_candidates[: self.config.target_file_rules.max_target_files]
        target_files = [candidate.path for candidate in selected_candidates]

        steps = self._build_steps(selected_candidates)
        assumptions = self._build_assumptions(planner_input)
        risks = self._build_risks(planner_input, selected_candidates)
        required_context = self._build_required_context(planner_input)
        review_focus = self._build_review_focus(selected_candidates, risks)
        success_checks = self._build_success_checks(planner_input, selected_candidates)

        plan = StructuredPlan(
            plan_id=f"plan-{uuid4()}",
            request_id=planner_input.user_request.request_id,
            goal=planner_input.user_request.normalized_goal,
            summary=self._build_summary(planner_input, target_files),
            strategy=self._build_strategy(selected_candidates),
            assumptions=assumptions,
            file_candidates=selected_candidates,
            steps=steps,
            global_risks=risks,
            success_checks=success_checks,
            required_context=required_context,
            review_focus=review_focus,
        )

        if codex_fallback is not None and codex_fallback.is_available():
            try:
                return codex_fallback.refine_plan(planner_input, plan)
            except Exception:
                return plan
        return plan

    def _load_config(self) -> PlannerConfig:
        if not self.config_path.exists():
            return PlannerConfig()

        raw_text = self.config_path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return PlannerConfig()

        loaded = yaml.safe_load(raw_text) or {}
        return PlannerConfig.model_validate(loaded)

    def _build_steps(self, candidates: list[FileCandidate]) -> list[PlanStep]:
        target_files = [candidate.path for candidate in candidates]
        source_files = [candidate.path for candidate in candidates if candidate.role in {"source", "config", "memory"}]
        doc_files = [candidate.path for candidate in candidates if candidate.role in {"doc", "policy", "config"}]
        test_files = [candidate.path for candidate in candidates if candidate.role == "test"]

        steps = [
            PlanStep(
                step_id="step-1",
                title="Inspect Targets",
                goal="Read the highest-signal candidate files and confirm where the change should land.",
                target_files=target_files,
                risks=["Wrong target selection can expand scope or cause unnecessary edits."],
                priority=Priority.HIGH,
                notes=["Prefer existing candidate files before introducing new files."],
            ),
            PlanStep(
                step_id="step-2",
                title="Implement Change",
                goal="Apply the minimum code changes needed to satisfy the user request.",
                target_files=source_files or target_files,
                dependencies=["step-1"],
                risks=["Implementation may drift beyond the requested scope."],
                priority=Priority.HIGH,
                notes=["Keep the patch small and align with project policy."],
            ),
            PlanStep(
                step_id="step-3",
                title="Validate Policy",
                goal="Review the proposed changes against constitution and project policy before runtime validation.",
                target_files=doc_files or target_files,
                dependencies=["step-2"],
                risks=["Policy violations can force a retry or replan."],
                priority=Priority.MEDIUM,
                notes=["Check coding, review, and test policy expectations."],
            ),
        ]

        if test_files:
            steps.append(
                PlanStep(
                    step_id="step-4",
                    title="Update Tests",
                    goal="Adjust or add tests that cover the requested behavior and guard against regressions.",
                    target_files=test_files,
                    dependencies=["step-2"],
                    risks=["Missing test coverage can hide regressions."],
                    priority=Priority.MEDIUM,
                    notes=["Prefer targeted tests that exercise the changed behavior."],
                )
            )

        return steps

    def _build_assumptions(self, planner_input: PlannerInput) -> list[str]:
        assumptions = [
            "The current localizer candidates are sufficient to begin planning.",
            "Existing repository conventions should be preserved unless the request explicitly says otherwise.",
        ]
        if not planner_input.l0_constitution.content:
            assumptions.append("L0 constitution is currently empty, so only structural safeguards are available.")
        if not any(policy.content for policy in planner_input.l1_project_policy):
            assumptions.append("L1 project policy files are currently empty, so planner guidance relies on planner rules and candidate context.")
        if planner_input.task_context and planner_input.task_context.events:
            assumptions.append("Existing task events may indicate this is a retry or replan scenario.")
        return assumptions

    def _build_risks(self, planner_input: PlannerInput, candidates: list[FileCandidate]) -> list[str]:
        risks: list[str] = []
        if any(candidate.role == "config" for candidate in candidates):
            risks.append("Configuration or policy file changes can affect behavior across multiple workflow stages.")
        if any(candidate.role == "memory" for candidate in candidates):
            risks.append("Changes touching memory persistence can impact replayability and later retries.")
        if planner_input.task_context and planner_input.task_context.events:
            latest_event_types = [event.event_type for event in planner_input.task_context.events[-3:]]
            risks.append(f"Recent task context exists ({', '.join(latest_event_types)}), so replan decisions should account for previous failures.")
        if not candidates:
            risks.append("File candidates are insufficient; planning may require re-running localization.")
        return risks

    def _build_required_context(self, planner_input: PlannerInput) -> list[str]:
        required = []
        if not planner_input.file_candidates:
            required.append("Additional file candidates are needed before implementation can start safely.")
        if not planner_input.l0_constitution.content:
            required.append("Constitution details are missing; add L0 rules if stronger policy enforcement is needed.")
        if not any(policy.content for policy in planner_input.l1_project_policy):
            required.append("Project policy files are empty; fill them in to enable stronger planner and reviewer checks.")
        return required

    def _build_review_focus(self, candidates: list[FileCandidate], risks: list[str]) -> list[str]:
        review_focus = [
            "Stay within the user's requested scope.",
            "Ensure every edited file is explicitly represented in target_files.",
        ]
        if any(candidate.role == "source" for candidate in candidates):
            review_focus.append("Check that code changes remain minimal and consistent with existing structure.")
        if any(candidate.role == "test" for candidate in candidates):
            review_focus.append("Verify that tests cover the changed behavior rather than unrelated flows.")
        if risks:
            review_focus.append("Double-check the plan against identified risk areas before patching.")
        return review_focus

    def _build_success_checks(self, planner_input: PlannerInput, candidates: list[FileCandidate]) -> list[str]:
        checks = list(planner_input.user_request.acceptance_criteria)
        if not checks:
            checks.append("The change should satisfy the normalized goal without expanding scope.")
        if candidates:
            checks.append("Implementation should be concentrated in the selected candidate files unless a justified new file is needed.")
        checks.append("The final patch should remain reviewable and compatible with planner policy.")
        return checks

    def _build_summary(self, planner_input: PlannerInput, target_files: list[str]) -> str:
        if target_files:
            return (
                f"Focus on the top candidate files first ({', '.join(target_files[:3])}) "
                "and make the smallest set of changes needed to satisfy the request."
            )
        return "Start with a narrower localization pass before making code changes."

    def _build_strategy(self, candidates: list[FileCandidate]) -> str:
        if not candidates:
            return "Re-run localization or request more context before implementation."
        if any(candidate.role == "source" for candidate in candidates):
            return "Prefer editing existing source files, then validate policy and test impact."
        return "Begin from the highest-signal non-source files and confirm whether code changes are actually required."


class CodexPlannerFallback:
    """Optional Codex SDK-based refinement step for structured plans."""

    def __init__(self, *, project_root: str | Path | None = None, model: str = "gpt-5.4") -> None:
        self.project_root = Path(project_root or Path.cwd()).resolve()
        self.model = model

    def is_available(self) -> bool:
        try:
            from openai_codex import Codex  # noqa: F401
        except ImportError:
            return False
        return True

    def refine_plan(self, planner_input: PlannerInput, draft_plan: StructuredPlan) -> StructuredPlan:
        from openai_codex import Codex, Sandbox

        prompt = self._build_prompt(planner_input, draft_plan)
        with Codex() as codex:
            thread = codex.thread_start(
                model=self.model,
                cwd=str(self.project_root),
                sandbox=Sandbox.read_only,
            )
            result = thread.run(prompt)

        if not result.final_response:
            raise ValueError("Codex returned no final response.")

        try:
            payload = loads(result.final_response)
        except JSONDecodeError as exc:
            raise ValueError("Codex did not return valid JSON.") from exc

        merged = draft_plan.model_dump()
        merged.update(payload)
        return StructuredPlan.model_validate(merged)

    def _build_prompt(self, planner_input: PlannerInput, draft_plan: StructuredPlan) -> str:
        candidate_lines = "\n".join(
            f"- {candidate.path} ({candidate.role}, score={candidate.score:.2f}): {candidate.reason}"
            for candidate in planner_input.file_candidates
        )
        policy_names = ", ".join(policy.name for policy in planner_input.l1_project_policy) or "none"
        return (
            "You are a planning agent. Refine the draft plan without generating code.\n"
            "Preserve user scope, keep target_files explicit, and highlight implementation risks.\n\n"
            f"User request:\n{planner_input.user_request.user_text}\n\n"
            f"Candidate files:\n{candidate_lines or '- none'}\n\n"
            f"L0 loaded: {'yes' if planner_input.l0_constitution.content else 'no'}\n"
            f"L1 policy docs: {policy_names}\n\n"
            f"Draft plan JSON:\n{draft_plan.model_dump_json(indent=2)}\n\n"
            "Return JSON containing only StructuredPlan fields that you want to override."
        )
