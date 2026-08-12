"""Selective, policy-filtered L1/L2 context construction for cognition."""

from __future__ import annotations

from forge.domain.cognition import RetrievedMemoryContext
from forge.domain.memory import EpisodeSearchFilters, PromotionEligibility
from forge.domain.outer_loop import L2KnowledgeStatus
from forge.ports.outbound import (
    ConstitutionRepository,
    EpisodeRepository,
    IdentityRepository,
    OuterLoopStore,
)


class MemoryContextBuilder:
    def __init__(
        self,
        episodes: EpisodeRepository,
        l2_store: OuterLoopStore,
        constitution: ConstitutionRepository,
        identity: IdentityRepository,
        *,
        top_k: int = 3,
    ) -> None:
        self._episodes = episodes
        self._l2_store = l2_store
        self._constitution = constitution
        self._identity = identity
        self._top_k = top_k

    def build(self, *, task_request: str, task_category: str) -> RetrievedMemoryContext:
        hits = self._episodes.search(
            task_request,
            top_k=self._top_k,
            filters=EpisodeSearchFilters(promotion_eligibility=PromotionEligibility.ELIGIBLE),
        )
        episode_ids = tuple(
            hit.episode.episode_id
            for hit in hits
            if self._constitution.evaluate_l2_evidence(hit.episode).allowed
        )
        tokens = set(task_request.casefold().split())
        knowledge = []
        for item in self._l2_store.load_knowledge():
            if item.status is not L2KnowledgeStatus.ACTIVE:
                continue
            text = f"{item.condition}: {item.statement}"
            if tokens and not tokens.intersection(text.casefold().split()):
                continue
            if self._constitution.evaluate_memory_text(text).allowed:
                knowledge.append(text)
            if len(knowledge) == self._top_k:
                break
        capability = self._identity.capability_for(task_category)
        return RetrievedMemoryContext(
            episode_ids=episode_ids,
            l2_knowledge=tuple(knowledge),
            capability_summary=(
                f"{capability.category}: confidence={capability.confidence:.2f}, "
                f"success_rate={capability.success_rate:.2f}"
            ),
        )
