"""Single application facade for the currently available memory layers."""

from __future__ import annotations

from forge.domain.cognition import RetrievedMemoryContext
from forge.domain.memory import (
    EpisodeSearchFilters,
    MemoryReadResult,
    PromotionEligibility,
    Reflection,
    ReflectionRoute,
    ReflectionRoutingDecision,
)
from forge.domain.outer_loop import L2Knowledge, L2KnowledgeStatus
from forge.ports.outbound import (
    ConstitutionRepository,
    EpisodeRepository,
    IdentityRepository,
    OuterLoopStore,
)


class MemoryManager:
    """Routes safe reads across L1/L2/L4/L5 and classifies reflection destinations.

    L3 has no repository yet. Tool-specific reflections therefore produce an explicit
    pending route instead of being silently persisted somewhere else.
    """

    def __init__(
        self,
        episodes: EpisodeRepository,
        l2_store: OuterLoopStore,
        constitution: ConstitutionRepository,
        identity: IdentityRepository,
        *,
        top_k: int = 3,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        self._episodes = episodes
        self._l2_store = l2_store
        self._constitution = constitution
        self._identity = identity
        self._top_k = top_k

    def read(self, *, query: str, task_category: str) -> MemoryReadResult:
        if not query.strip():
            raise ValueError("Memory query must not be empty")
        l1_hits = tuple(
            hit
            for hit in self._episodes.search(
                query,
                top_k=self._top_k,
                filters=EpisodeSearchFilters(promotion_eligibility=PromotionEligibility.ELIGIBLE),
            )
            if self._constitution.evaluate_l2_evidence(hit.episode).allowed
        )
        l2_knowledge = tuple(self._relevant_l2(query))
        capability = self._identity.capability_for(task_category)
        identity = self._identity.load_identity()
        return MemoryReadResult(
            l1_hits=l1_hits,
            l2_knowledge=l2_knowledge,
            identity=identity,
            capability=capability,
        )

    def build_context(self, *, query: str, task_category: str) -> RetrievedMemoryContext:
        result = self.read(query=query, task_category=task_category)
        return RetrievedMemoryContext(
            episode_ids=tuple(hit.episode.episode_id for hit in result.l1_hits),
            l2_knowledge=tuple(
                f"{item.condition}: {item.statement}" for item in result.l2_knowledge
            ),
            capability_summary=(
                f"{result.capability.category}: confidence={result.capability.confidence:.2f}, "
                f"success_rate={result.capability.success_rate:.2f}"
            ),
        )

    def route_reflection(
        self, reflection: Reflection, *, tool_names: tuple[str, ...]
    ) -> ReflectionRoutingDecision:
        if not reflection.has_content:
            return ReflectionRoutingDecision(ReflectionRoute.L1_ONLY, "reflection.empty")
        if tool_names:
            return ReflectionRoutingDecision(
                ReflectionRoute.L3_PROCEDURE_PENDING, "reflection.tool_specific"
            )
        return ReflectionRoutingDecision(
            ReflectionRoute.L2_GENERALIZATION, "reflection.generalizable"
        )

    def _relevant_l2(self, query: str) -> list[L2Knowledge]:
        query_tokens = set(query.casefold().split())
        selected = []
        for item in self._l2_store.load_knowledge():
            if item.status is not L2KnowledgeStatus.ACTIVE:
                continue
            text = f"{item.condition}: {item.statement}"
            if query_tokens and not query_tokens.intersection(text.casefold().split()):
                continue
            if self._constitution.evaluate_memory_text(text).allowed:
                selected.append(item)
            if len(selected) == self._top_k:
                break
        return selected
