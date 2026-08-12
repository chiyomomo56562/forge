from dataclasses import dataclass, replace
from datetime import UTC, datetime

from forge.domain.outer_loop import L2Knowledge, L2KnowledgeStatus
from forge.domain.procedural import ProceduralSkill, SkillExecution, SkillStatus
from forge.ports.outbound.procedural_repository import ProceduralRepository


@dataclass(frozen=True)
class SkillLifecyclePolicy:
    min_samples: int = 3
    active_threshold: float = 0.9
    degrading_threshold: float = 0.5
    archive_threshold: float = 0.2
    recovery_threshold: float = 0.7


class ProceduralMemoryService:
    def __init__(self, repository: ProceduralRepository, policy: SkillLifecyclePolicy) -> None:
        self._repository, self._policy = repository, policy

    def seed_from_l2(self, knowledge: L2Knowledge) -> ProceduralSkill | None:
        if knowledge.status is not L2KnowledgeStatus.ACTIVE or not knowledge.statement.strip():
            return None
        existing = self._repository.get_by_source_l2(knowledge.knowledge_id)
        hints = tuple(
            dict.fromkeys((*knowledge.counterexample_episode_ids, *knowledge.support_episode_ids))
        )
        pending_hints = tuple(self._repository.pending_hints_for(hints))
        skill = ProceduralSkill(
            skill_id=f"skill_{knowledge.knowledge_id[3:]}",
            source_l2_id=knowledge.knowledge_id,
            procedure=(knowledge.statement, *pending_hints),
            reflection_hints=(knowledge.condition, *pending_hints),
            status=existing.status if existing else SkillStatus.SEED,
            success_rate=existing.success_rate if existing else 0.0,
            total_executions=existing.total_executions if existing else 0,
            updated_at=datetime.now(UTC),
        )
        self._repository.upsert(skill)
        return skill

    def record_execution(self, execution: SkillExecution) -> ProceduralSkill:
        self._repository.record_execution(execution)
        skill = self._repository.get(execution.skill_id)
        if skill is None:
            raise ValueError("Unknown skill execution")
        return self._recalculate(skill)

    def refresh(self, skill: ProceduralSkill) -> ProceduralSkill:
        return self._recalculate(skill)

    def _recalculate(self, skill: ProceduralSkill) -> ProceduralSkill:
        samples = self._repository.executions_for(skill.skill_id)
        rate = sum(item.success_score for item in samples) / len(samples) if samples else 0.0
        cib_ok = all(item.cib_score >= 0.95 for item in samples)
        status = skill.status
        if len(samples) >= self._policy.min_samples:
            if status is SkillStatus.DEGRADING and rate < self._policy.archive_threshold:
                status = SkillStatus.ARCHIVED
            elif (
                status is SkillStatus.DEGRADING
                and rate >= self._policy.recovery_threshold
                and cib_ok
            ):
                status = SkillStatus.ACTIVE
            elif rate >= self._policy.active_threshold and cib_ok:
                status = SkillStatus.ACTIVE
            elif rate < self._policy.degrading_threshold:
                status = SkillStatus.DEGRADING
            elif status is SkillStatus.SEED:
                status = SkillStatus.DEVELOPING
        updated = replace(
            skill,
            status=status,
            success_rate=rate,
            total_executions=len(samples),
            updated_at=datetime.now(UTC),
        )
        self._repository.upsert(updated)
        return updated
