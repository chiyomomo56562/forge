from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ForgeBaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
        str_strip_whitespace=True,
    )


class FileRole(str, Enum):
    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    DOC = "doc"
    POLICY = "policy"
    MEMORY = "memory"
    OTHER = "other"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_REVIEW = "needs_review"


class ReviewType(str, Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"


class RetryAction(str, Enum):
    TARGETED_RETRY = "targeted_retry"
    REPLAN = "replan"
    HUMAN_ESCALATION = "human_escalation"
    COMPLETE = "complete"


class ChangeType(str, Enum):
    ADD = "add"
    MODIFY = "modify"
    DELETE = "delete"
    RENAME = "rename"


class EpisodeOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"


class MemoryLayer(str, Enum):
    L0 = "l0"
    L1 = "l1"
    L3 = "l3"
    L4 = "l4"


class UserRequest(ForgeBaseModel):
    request_id: str = Field(description="Unique identifier for the user request.")
    run_id: str | None = Field(default=None, description="Execution run identifier.")
    user_text: str = Field(description="Original user request text.")
    normalized_goal: str = Field(description="Planner-friendly normalized goal.")
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    requested_at: datetime = Field(default_factory=datetime.utcnow)


class FileCandidate(ForgeBaseModel):
    path: str = Field(description="Repository-relative file path.")
    role: FileRole = Field(description="Why this file matters for the task.")
    reason: str = Field(description="Why the localizer selected this file.")
    score: float = Field(ge=0.0, le=1.0, description="Relevance score.")
    symbols: list[str] = Field(default_factory=list, description="Matched symbols or keywords.")


class PlanStep(ForgeBaseModel):
    step_id: str = Field(description="Stable step identifier.")
    title: str = Field(description="Short step title.")
    goal: str = Field(description="What this step is trying to accomplish.")
    target_files: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list, description="Other step IDs.")
    risks: list[str] = Field(default_factory=list)
    priority: Priority = Field(default=Priority.MEDIUM)
    notes: list[str] = Field(default_factory=list)


class StructuredPlan(ForgeBaseModel):
    plan_id: str = Field(description="Stable plan identifier.")
    request_id: str = Field(description="Source user request identifier.")
    summary: str = Field(description="High-level execution strategy.")
    assumptions: list[str] = Field(default_factory=list)
    file_candidates: list[FileCandidate] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list)
    global_risks: list[str] = Field(default_factory=list)
    success_checks: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_files(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for step in self.steps:
            for path in step.target_files:
                if path not in seen:
                    seen.add(path)
                    ordered.append(path)
        return ordered


class CodeSnippet(ForgeBaseModel):
    path: str = Field(description="Repository-relative file path.")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    reason: str = Field(description="Why this snippet was loaded.")
    content: str = Field(description="Actual code or text snippet.")


class TaskContextRef(ForgeBaseModel):
    kind: str = Field(description="Context type, e.g. diff, error_log, review_feedback.")
    ref_id: str = Field(description="Stable identifier in task memory.")
    summary: str = Field(description="Short human-readable summary.")


class PolicyDocument(ForgeBaseModel):
    layer: MemoryLayer
    name: str = Field(description="Logical document name.")
    path: str = Field(description="Repository-relative file path.")
    content: dict[str, Any] = Field(default_factory=dict)


class TaskEvent(ForgeBaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    event_type: str = Field(description="e.g. planner_output, review_feedback, retry_decision.")
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class TaskContext(ForgeBaseModel):
    run_id: str
    events: list[TaskEvent] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def event_count(self) -> int:
        return len(self.events)


class CodeContext(ForgeBaseModel):
    request_id: str
    plan_id: str
    snippets: list[CodeSnippet] = Field(default_factory=list)
    related_context: list[TaskContextRef] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class PatchChange(ForgeBaseModel):
    path: str = Field(description="Repository-relative file path.")
    change_type: ChangeType
    summary: str = Field(description="Short explanation of the change.")
    diff: str = Field(description="Unified diff or structured diff fragment.")


class PatchResult(ForgeBaseModel):
    patch_id: str = Field(description="Stable patch identifier.")
    request_id: str
    plan_id: str
    rationale: str = Field(description="Why this patch implements the plan.")
    changes: list[PatchChange] = Field(default_factory=list)
    test_changes: list[PatchChange] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewIssue(ForgeBaseModel):
    issue_id: str = Field(description="Stable issue identifier.")
    severity: Priority
    rule_id: str | None = Field(default=None, description="Policy or rule identifier.")
    message: str = Field(description="What failed or needs attention.")
    file_path: str | None = Field(default=None)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    suggestion: str | None = Field(default=None, description="Suggested remediation.")


class ReviewFeedback(ForgeBaseModel):
    review_id: str = Field(description="Stable review identifier.")
    patch_id: str
    review_type: ReviewType
    status: ReviewStatus
    summary: str
    issues: list[ReviewIssue] = Field(default_factory=list)
    executed_checks: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list, description="Logs, reports, or test outputs.")
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)


class RetryDecision(ForgeBaseModel):
    decision_id: str = Field(description="Stable retry decision identifier.")
    request_id: str
    patch_id: str | None = Field(default=None)
    action: RetryAction
    reason: str = Field(description="Why this next action was chosen.")
    feedback_refs: list[str] = Field(default_factory=list)
    next_step_hint: str | None = Field(default=None, description="Actionable guidance for the next agent.")
    retry_count: int = Field(default=0, ge=0)
    decided_at: datetime = Field(default_factory=datetime.utcnow)


class EpisodeCandidate(ForgeBaseModel):
    episode_id: str = Field(description="Stable episodic memory identifier.")
    request_id: str
    run_id: str | None = Field(default=None)
    outcome: EpisodeOutcome
    summary: str = Field(description="What happened in this run.")
    success_patterns: list[str] = Field(default_factory=list)
    failure_patterns: list[str] = Field(default_factory=list)
    user_preferences: list[str] = Field(default_factory=list)
    violated_rule_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
