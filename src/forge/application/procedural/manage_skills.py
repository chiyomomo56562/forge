from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import cast

from forge.domain.outer_loop import L2Knowledge, L2KnowledgeStatus
from forge.domain.procedural import (
    ProceduralSkill,
    SkillExecution,
    SkillStatus,
    SkillStep,
    SkillStepDraft,
)
from forge.ports.outbound.procedural_repository import ProceduralRepository


@dataclass(frozen=True)
class SkillLifecyclePolicy:
    min_samples: int = 3
    min_seed_evidence: int = 1
    min_repeated_tool_sequences: int = 1
    evaluation_window_samples: int = 20
    active_threshold: float = 0.9
    degrading_threshold: float = 0.5
    recovery_threshold: float = 0.7
    pain_threshold: float = 0.5
    tool_error_threshold: float = 0.5

    def __post_init__(self) -> None:
        if (
            self.min_samples <= 0
            or self.min_seed_evidence <= 0
            or self.min_repeated_tool_sequences <= 0
            or self.evaluation_window_samples <= 0
        ):
            raise ValueError("Lifecycle sample limits must be positive")
        if not 0.0 <= self.degrading_threshold <= 1.0:
            raise ValueError("Lifecycle degradation thresholds are invalid")
        if not 0.0 <= self.recovery_threshold <= 1.0:
            raise ValueError("Lifecycle recovery threshold is invalid")
        if not 0.0 <= self.pain_threshold <= 1.0 or not 0.0 <= self.tool_error_threshold <= 1.0:
            raise ValueError("Lifecycle quality thresholds are invalid")


class ProceduralMemoryService:
    def __init__(self, repository: ProceduralRepository, policy: SkillLifecyclePolicy) -> None:
        self._repository, self._policy = repository, policy

    def seed_from_l2(self, knowledge: L2Knowledge) -> ProceduralSkill | None:
        if (
            knowledge.status is not L2KnowledgeStatus.ACTIVE
            or not knowledge.statement.strip()
            or len(set(knowledge.support_episode_ids)) < self._policy.min_seed_evidence
        ):
            return None
        existing = self._repository.get_by_source_l2(knowledge.knowledge_id)
        hints = tuple(
            dict.fromkeys((*knowledge.counterexample_episode_ids, *knowledge.support_episode_ids))
        )
        pending_hint_records = self._repository.pending_hint_records_for(hints)
        sequence = self._repeated_tool_sequence(pending_hint_records)
        if sequence is None:
            return None
        repeated_records = [item for item in pending_hint_records if item[2] == sequence]
        pending_hints = tuple(item[1] for item in repeated_records)
        existing_drafts = existing.step_drafts if existing else ()
        drafts = self._merge_step_drafts(existing_drafts, repeated_records)
        skill = ProceduralSkill(
            skill_id=f"skill_{knowledge.knowledge_id[3:]}",
            source_l2_id=knowledge.knowledge_id,
            procedure=(knowledge.statement, *pending_hints),
            reflection_hints=(knowledge.condition, *pending_hints),
            status=existing.status if existing else SkillStatus.SEED,
            success_rate=existing.success_rate if existing else 0.0,
            total_executions=existing.total_executions if existing else 0,
            updated_at=datetime.now(UTC),
            executable_steps=existing.executable_steps if existing else (),
            step_drafts=drafts,
        )
        self._repository.upsert(skill)
        return skill

    def record_execution(self, execution: SkillExecution) -> ProceduralSkill:
        self._repository.record_execution(execution)
        skill = self._repository.get(execution.skill_id)
        if skill is None:
            raise ValueError("Unknown skill execution")
        return skill

    def refresh(self, skill: ProceduralSkill) -> ProceduralSkill:
        return self._recalculate(skill)

    def refresh_all(self) -> tuple[ProceduralSkill, ...]:
        """Recalculate all lifecycle metrics without deleting or archiving any skill."""
        return tuple(self._recalculate(skill, touch=False) for skill in self._repository.list_all())

    def archive(self, skill_id: str) -> ProceduralSkill:
        """Explicitly retain a skill in the non-executable archive; never deletes it."""
        skill = self._repository.get(skill_id)
        if skill is None:
            raise ValueError("Unknown skill")
        archived = cast(
            ProceduralSkill,
            replace(skill, status=SkillStatus.ARCHIVED, updated_at=datetime.now(UTC)),
        )
        self._repository.upsert(archived)
        return archived

    def begin_validation(self, skill_id: str) -> ProceduralSkill:
        """Move a reviewed skill into the explicit, non-production validation lane."""
        skill = self._repository.get(skill_id)
        if skill is None:
            raise ValueError("Unknown skill")
        if skill.status is SkillStatus.ARCHIVED:
            raise ValueError("Archived skills cannot enter validation")
        if not skill.executable_steps:
            raise ValueError("Validation requires approved executable steps")
        validating = cast(
            ProceduralSkill,
            replace(skill, status=SkillStatus.VALIDATING, updated_at=datetime.now(UTC)),
        )
        self._repository.upsert(validating)
        return validating

    def bind_executable_steps(
        self, skill_id: str, steps: tuple[SkillStep, ...]
    ) -> ProceduralSkill:
        """Persist reviewed tool steps without deriving them from free-form text."""
        skill = self._repository.get(skill_id)
        if skill is None:
            raise ValueError("Unknown skill")
        if any(not step.step_id or not step.tool_name for step in steps):
            raise ValueError("Executable steps require a step ID and tool name")
        draft_tools = {draft.tool_name for draft in skill.step_drafts}
        if not draft_tools:
            raise ValueError("Executable steps require a reviewed step draft")
        if any(step.tool_name not in draft_tools for step in steps):
            raise ValueError("Executable step tool is not present in a reviewed draft")
        updated = cast(
            ProceduralSkill,
            replace(skill, executable_steps=steps, updated_at=datetime.now(UTC)),
        )
        self._repository.upsert(updated)
        return updated

    def approve_step_draft(
        self,
        skill_id: str,
        *,
        draft_id: str,
        step_id: str,
        tool_arguments: dict[str, object],
    ) -> ProceduralSkill:
        """Approve one draft with reviewer-supplied arguments; never infer arguments."""
        skill = self._repository.get(skill_id)
        if skill is None:
            raise ValueError("Unknown skill")
        draft = next((item for item in skill.step_drafts if item.draft_id == draft_id), None)
        if draft is None:
            raise ValueError("Unknown step draft")
        if any(step.step_id == step_id for step in skill.executable_steps):
            raise ValueError("Executable step ID already exists")
        return self.bind_executable_steps(
            skill_id,
            (*skill.executable_steps, SkillStep(step_id, draft.tool_name, tool_arguments)),
        )

    def list_step_drafts(self, skill_id: str) -> tuple[SkillStepDraft, ...]:
        """Return the non-executable drafts that require explicit review."""
        skill = self._repository.get(skill_id)
        if skill is None:
            raise ValueError("Unknown skill")
        return cast(tuple[SkillStepDraft, ...], skill.step_drafts)

    def list_skills(self) -> tuple[ProceduralSkill, ...]:
        """Return all retained L3 skills, including archived records."""
        return tuple(self._repository.list_all())

    def _repeated_tool_sequence(
        self,
        records: list[tuple[str, str, tuple[str, ...]]],
    ) -> tuple[str, ...] | None:
        sequences = Counter(record[2] for record in records if record[2])
        if not sequences:
            return None
        sequence, count = min(
            sequences.items(), key=lambda item: (-item[1], item[0])
        )
        return sequence if count >= self._policy.min_repeated_tool_sequences else None

    @staticmethod
    def _merge_step_drafts(
        existing: tuple[SkillStepDraft, ...],
        records: list[tuple[str, str, tuple[str, ...]]],
    ) -> tuple[SkillStepDraft, ...]:
        drafts = list(existing)
        known = {(draft.source_episode_id, draft.tool_name) for draft in drafts}
        for source_id, hint, tool_names in records:
            for tool_name in tool_names:
                key = (source_id, tool_name)
                if key in known:
                    continue
                drafts.append(
                    SkillStepDraft(
                        draft_id=f"draft_{source_id}_{tool_name.replace('.', '_')}",
                        source_episode_id=source_id,
                        hint=hint,
                        tool_name=tool_name,
                    )
                )
                known.add(key)
        return tuple(drafts)

    def _recalculate(self, skill: ProceduralSkill, *, touch: bool = True) -> ProceduralSkill:
        all_executions = sorted(
            self._repository.executions_for(skill.skill_id), key=lambda item: item.executed_at
        )
        samples = all_executions[-self._policy.evaluation_window_samples :]
        rate = sum(item.success_score for item in samples) / len(samples) if samples else 0.0
        cib_ok = all(item.cib_score >= 0.95 for item in samples)
        pain = [item.pain_index for item in samples if item.pain_index is not None]
        errors = [item.tool_error_ratio for item in samples if item.tool_error_ratio is not None]
        avg_pain = sum(pain) / len(pain) if pain else None
        avg_errors = sum(errors) / len(errors) if errors else 0.0
        status = skill.status
        now = datetime.now(UTC)
        if len(samples) >= self._policy.min_samples and status is not SkillStatus.ARCHIVED:
            if (
                status is SkillStatus.DEGRADING
                and rate >= self._policy.recovery_threshold
                and cib_ok
            ):
                status = SkillStatus.ACTIVE
            elif (
                status is SkillStatus.VALIDATING
                and rate >= self._policy.active_threshold
                and cib_ok
            ):
                status = SkillStatus.ACTIVE
            elif (
                rate < self._policy.degrading_threshold
                or (avg_pain is not None and avg_pain >= self._policy.pain_threshold)
                or avg_errors >= self._policy.tool_error_threshold
            ):
                status = SkillStatus.DEGRADING
            elif status is SkillStatus.SEED:
                status = SkillStatus.DEVELOPING
        updated = replace(
            skill,
            status=status,
            success_rate=rate,
            total_executions=len(all_executions),
            avg_pain_index=avg_pain,
            last_executed_at=max((item.executed_at for item in samples), default=None),
            updated_at=now if touch else skill.updated_at,
        )
        self._repository.upsert(updated)
        return updated
