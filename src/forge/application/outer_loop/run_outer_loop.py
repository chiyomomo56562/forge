"""Minimal, checkpointed L1-to-L2 consolidation service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256

from forge.application.procedural import ProceduralMemoryService
from forge.domain.constitution import CibDecision
from forge.domain.identity import Capability
from forge.domain.memory import Episode, EpisodeSearchFilters, EpisodeStatus, PromotionEligibility
from forge.domain.outer_loop import (
    L2Knowledge,
    L2KnowledgeStatus,
    OuterLoopCheckpoint,
    OuterLoopResult,
    PatternCandidate,
)
from forge.ports.outbound import (
    ConstitutionRepository,
    EpisodeRepository,
    IdentityRepository,
    OuterLoopStore,
)


@dataclass(frozen=True)
class OuterLoopPolicy:
    batch_size: int = 20
    min_episodes_for_generalization: int = 3
    promotion_confidence: float = 0.8
    retire_confidence: float = 0.4

    def __post_init__(self) -> None:
        if self.batch_size <= 0 or self.min_episodes_for_generalization <= 0:
            raise ValueError("Outer Loop limits must be positive.")
        if not 0.0 <= self.retire_confidence < self.promotion_confidence <= 1.0:
            raise ValueError("Outer Loop confidence thresholds are invalid.")


@dataclass(frozen=True)
class L3GrowthPolicy:
    """Bound the direction and rate of new L3 learning, never L1/L2 retention."""

    max_new_seeds_per_run: int = 2
    min_capability_confidence: float = 0.7
    min_capability_success_rate: float = 0.7

    def __post_init__(self) -> None:
        if self.max_new_seeds_per_run <= 0:
            raise ValueError("L3 growth seed budget must be positive")
        if not all(
            0.0 <= value <= 1.0
            for value in (self.min_capability_confidence, self.min_capability_success_rate)
        ):
            raise ValueError("L3 growth capability thresholds are invalid")


class RunOuterLoopService:
    """Consolidates complete L1 episodes and atomically advances its checkpoint."""

    def __init__(
        self,
        repository: EpisodeRepository,
        store: OuterLoopStore,
        policy: OuterLoopPolicy,
        constitution: ConstitutionRepository | None = None,
        procedural: ProceduralMemoryService | None = None,
        identity: IdentityRepository | None = None,
        l3_growth: L3GrowthPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._policy = policy
        self._constitution = constitution
        self._procedural = procedural
        self._identity = identity
        self._l3_growth = l3_growth or L3GrowthPolicy()

    def handle(self, *, force: bool = False) -> OuterLoopResult:
        if self._procedural is not None:
            self._procedural.refresh_all()
        checkpoint = self._store.load_checkpoint()
        candidates = {item.candidate_id: item for item in self._store.load_candidates()}
        knowledge = {item.knowledge_id: item for item in self._store.load_knowledge()}
        after = checkpoint.watermark or datetime.min.replace(tzinfo=UTC)
        episodes = self._repository.list_after(after, filters=EpisodeSearchFilters())
        unseen = [
            item for item in episodes if item.episode_id not in checkpoint.processed_episode_ids
        ]
        if not force and len(unseen) < self._policy.batch_size:
            return OuterLoopResult((), (), (), checkpoint)

        batch = unseen[: self._policy.batch_size]
        for episode in batch:
            if self._is_eligible(episode) and self._is_constitutionally_allowed(episode):
                candidate = self._merge_candidate(
                    candidates.get(self._candidate_id(episode)), episode
                )
                candidates[candidate.candidate_id] = candidate

        promoted: list[str] = []
        updated: list[str] = []
        deferred_l3: list[str] = []
        new_seed_count = 0
        for candidate_id, candidate in list(candidates.items()):
            revised, changed, is_promotion = self._evaluate_candidate(candidate, knowledge)
            candidates[candidate_id] = revised
            if changed is not None:
                knowledge[changed.knowledge_id] = changed
                if self._procedural is not None:
                    existing = self._procedural.get_by_source_l2(changed.knowledge_id)
                    allowed = existing is not None or self._allows_l3_growth(
                        changed, new_seed_count
                    )
                    if allowed:
                        seeded = self._procedural.seed_from_l2(changed)
                        if seeded is not None and existing is None:
                            new_seed_count += 1
                    else:
                        deferred_l3.append(changed.knowledge_id)
                (promoted if is_promotion else updated).append(changed.knowledge_id)

        processed = (*checkpoint.processed_episode_ids, *(item.episode_id for item in batch))
        new_checkpoint = OuterLoopCheckpoint(
            watermark=max((item.created_at for item in batch), default=checkpoint.watermark),
            processed_episode_ids=processed[-10_000:],
        )
        self._store.save(
            candidates=list(candidates.values()),
            knowledge=list(knowledge.values()),
            checkpoint=new_checkpoint,
        )
        return OuterLoopResult(
            tuple(item.episode_id for item in batch),
            tuple(promoted),
            tuple(updated),
            new_checkpoint,
            tuple(deferred_l3),
        )

    @staticmethod
    def _is_eligible(episode: Episode) -> bool:
        return (
            episode.evidence_complete
            and episode.evaluation.promotion_eligibility is PromotionEligibility.ELIGIBLE
            and episode.reflection.has_content
        )

    def _is_constitutionally_allowed(self, episode: Episode) -> bool:
        return (
            self._constitution is None or self._constitution.evaluate_l2_evidence(episode).allowed
        )

    @staticmethod
    def _candidate_id(episode: Episode) -> str:
        material = (
            "\n".join(
                (
                    episode.task_category,
                    episode.reflection.causal_condition,
                    episode.reflection.what_worked,
                )
            )
            .strip()
            .casefold()
        )
        return f"pc_{sha256(material.encode()).hexdigest()[:16]}"

    def _merge_candidate(
        self, current: PatternCandidate | None, episode: Episode
    ) -> PatternCandidate:
        candidate_id = self._candidate_id(episode)
        if current is None:
            current = PatternCandidate(
                candidate_id=candidate_id,
                signature=candidate_id[3:],
                statement=(
                    episode.reflection.what_worked.strip() or episode.reflection.next_hint.strip()
                ),
                condition=episode.reflection.causal_condition.strip(),
                task_category=episode.task_category,
            )
        support = current.support_episode_ids
        counterexamples = current.counterexample_episode_ids
        if episode.evaluation.status is EpisodeStatus.SUCCESS:
            support = (
                (*support, episode.episode_id) if episode.episode_id not in support else support
            )
        else:
            counterexamples = (
                (*counterexamples, episode.episode_id)
                if episode.episode_id not in counterexamples
                else counterexamples
            )
        return replace(
            current,
            support_episode_ids=support,
            counterexample_episode_ids=counterexamples,
            task_category=episode.task_category,
        )

    def _evaluate_candidate(
        self, candidate: PatternCandidate, knowledge: dict[str, L2Knowledge]
    ) -> tuple[PatternCandidate, L2Knowledge | None, bool]:
        if candidate.evidence_count < self._policy.min_episodes_for_generalization:
            return candidate, None, False
        now = datetime.now(UTC)
        if candidate.knowledge_id is None:
            if candidate.confidence < self._policy.promotion_confidence:
                return candidate, None, False
            knowledge_id = f"l2_{candidate.candidate_id[3:]}"
            item = L2Knowledge(
                knowledge_id,
                candidate.candidate_id,
                candidate.statement,
                candidate.condition,
                candidate.confidence,
                L2KnowledgeStatus.ACTIVE,
                candidate.support_episode_ids,
                candidate.counterexample_episode_ids,
                now,
                candidate.task_category,
            )
            return replace(candidate, knowledge_id=knowledge_id), item, True

        previous = knowledge[candidate.knowledge_id]
        status = (
            L2KnowledgeStatus.RETIRED
            if candidate.confidence < self._policy.retire_confidence
            else L2KnowledgeStatus.WEAKENED
            if candidate.confidence < self._policy.promotion_confidence
            else L2KnowledgeStatus.ACTIVE
        )
        revised = replace(
            previous,
            confidence=candidate.confidence,
            status=status,
            support_episode_ids=candidate.support_episode_ids,
            counterexample_episode_ids=candidate.counterexample_episode_ids,
            updated_at=now,
            task_category=candidate.task_category,
        )
        return candidate, revised if revised != previous else None, False

    def _allows_l3_growth(self, knowledge: L2Knowledge, new_seed_count: int) -> bool:
        if new_seed_count >= self._l3_growth.max_new_seeds_per_run:
            return False
        if self._constitution is not None:
            decision: CibDecision = self._constitution.evaluate_memory_text(
                f"{knowledge.condition}\n{knowledge.statement}"
            )
            if not decision.allowed:
                return False
        if self._identity is not None:
            capability: Capability = self._identity.capability_for(knowledge.task_category)
            if (
                capability.confidence < self._l3_growth.min_capability_confidence
                or capability.success_rate < self._l3_growth.min_capability_success_rate
            ):
                return False
        return True
