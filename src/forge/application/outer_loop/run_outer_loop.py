"""Minimal, checkpointed L1-to-L2 consolidation service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256

from forge.application.procedural import ProceduralMemoryService
from forge.domain.memory import Episode, EpisodeSearchFilters, EpisodeStatus, PromotionEligibility
from forge.domain.outer_loop import (
    L2Knowledge,
    L2KnowledgeStatus,
    OuterLoopCheckpoint,
    OuterLoopResult,
    PatternCandidate,
)
from forge.ports.outbound import ConstitutionRepository, EpisodeRepository, OuterLoopStore


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


class RunOuterLoopService:
    """Consolidates complete L1 episodes and atomically advances its checkpoint."""

    def __init__(
        self,
        repository: EpisodeRepository,
        store: OuterLoopStore,
        policy: OuterLoopPolicy,
        constitution: ConstitutionRepository | None = None,
        procedural: ProceduralMemoryService | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._policy = policy
        self._constitution = constitution
        self._procedural = procedural

    def handle(self, *, force: bool = False) -> OuterLoopResult:
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
        for candidate_id, candidate in list(candidates.items()):
            revised, changed, is_promotion = self._evaluate_candidate(candidate, knowledge)
            candidates[candidate_id] = revised
            if changed is not None:
                knowledge[changed.knowledge_id] = changed
                if self._procedural is not None:
                    self._procedural.seed_from_l2(changed)
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
        )
        return candidate, revised if revised != previous else None, False
