"""Minimal, checkpointed L1-to-L2 consolidation service."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256

from forge.application.procedural import ProceduralMemoryService
from forge.domain.constitution import CibDecision
from forge.domain.identity import Capability
from forge.domain.memory import Episode, EpisodeSearchFilters, EpisodeStatus, PromotionEligibility
from forge.domain.outer_loop import (
    GrowthObservation,
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


@dataclass(frozen=True)
class GrowthRegulatorPolicy:
    """M16 safeguards for the rate of *new* L3 procedures only.

    Consolidation coherence is the mean confidence of active L2 knowledge.  It is a
    bounded L3-growth signal, not the future system-wide M17 coherence index.
    """

    crash_window: int = 20
    crash_delta_threshold: float = 0.15
    stagnation_window: int = 50
    stagnation_coherence_delta: float = 0.01
    overgrowth_days: int = 7
    overgrowth_coherence_rise: float = 0.2
    operational_load_window: int = 20
    pain_threshold: float = 0.5
    retry_ratio_threshold: float = 0.4
    tool_error_ratio_threshold: float = 0.3
    budget_overrun_ratio_threshold: float = 0.2

    def __post_init__(self) -> None:
        if min(
            self.crash_window,
            self.stagnation_window,
            self.overgrowth_days,
            self.operational_load_window,
        ) <= 0:
            raise ValueError("M16 windows must be positive")
        if not all(
            0.0 <= value <= 1.0
            for value in (
                self.crash_delta_threshold,
                self.stagnation_coherence_delta,
                self.overgrowth_coherence_rise,
                self.pain_threshold,
                self.retry_ratio_threshold,
                self.tool_error_ratio_threshold,
                self.budget_overrun_ratio_threshold,
            )
        ):
            raise ValueError("M16 thresholds are invalid")


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
        growth_regulator: GrowthRegulatorPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._policy = policy
        self._constitution = constitution
        self._procedural = procedural
        self._identity = identity
        self._l3_growth = l3_growth or L3GrowthPolicy()
        self._growth_regulator = growth_regulator or GrowthRegulatorPolicy()

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
        growth_limit, growth_reasons = self._l3_growth_limit(
            self._growth_evaluation_window(batch), checkpoint, knowledge
        )
        new_seed_count = 0
        for candidate_id, candidate in list(candidates.items()):
            revised, changed, is_promotion = self._evaluate_candidate(candidate, knowledge)
            candidates[candidate_id] = revised
            if changed is not None:
                knowledge[changed.knowledge_id] = changed
                if self._procedural is not None:
                    existing = self._procedural.get_by_source_l2(changed.knowledge_id)
                    allowed = existing is not None or self._allows_l3_growth(
                        changed, new_seed_count, growth_limit
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
            growth_observations=self._append_growth_observation(
                checkpoint, len(processed), knowledge.values()
            ),
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
            growth_limit,
            growth_reasons,
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

    def _allows_l3_growth(
        self, knowledge: L2Knowledge, new_seed_count: int, growth_limit: int | None = None
    ) -> bool:
        limit = self._l3_growth.max_new_seeds_per_run if growth_limit is None else growth_limit
        if new_seed_count >= limit:
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

    def _l3_growth_limit(
        self,
        batch: list[Episode],
        checkpoint: OuterLoopCheckpoint,
        knowledge: dict[str, L2Knowledge],
    ) -> tuple[int, tuple[str, ...]]:
        """Return M16's effective new-seed budget and auditable reasons."""
        limit = self._l3_growth.max_new_seeds_per_run
        reasons: list[str] = []
        if self._has_success_rate_crash(batch):
            return 0, ("success_rate_crash",)

        operational_load = self._operational_load_reasons(batch)
        if len(operational_load) >= 2:
            return 0, tuple(f"operational_load:{item}" for item in operational_load)
        if operational_load:
            limit = min(limit, 1)
            reasons.extend(f"operational_load:{item}" for item in operational_load)

        coherence = self._consolidation_coherence(knowledge.values())
        episode_count = len(checkpoint.processed_episode_ids) + len(batch)
        observations = checkpoint.growth_observations
        if self._is_stagnating(observations, episode_count, coherence):
            limit = min(limit, 1)
            reasons.append("consolidation_stagnation")
        if self._is_overgrowing(observations, coherence):
            limit = min(limit, 1)
            reasons.append("rapid_consolidation_growth")
        return limit, tuple(reasons)

    def _growth_evaluation_window(self, batch: list[Episode]) -> list[Episode]:
        """Load enough L1 history for M16, rather than relying on one small batch."""
        required = max(
            self._growth_regulator.crash_window * 2,
            self._growth_regulator.operational_load_window,
        )
        if len(batch) >= required:
            return batch[-required:]
        history = self._repository.list_after(
            datetime.min.replace(tzinfo=UTC), filters=EpisodeSearchFilters()
        )
        return sorted(history, key=lambda item: item.created_at)[-required:]

    def _has_success_rate_crash(self, batch: list[Episode]) -> bool:
        window = self._growth_regulator.crash_window
        if len(batch) < window * 2:
            return False
        scores = [
            item.evaluation.success_score
            for item in batch
            if item.evaluation.success_score is not None
        ]
        if len(scores) < window * 2:
            return False
        previous = sum(scores[-(window * 2) : -window]) / window
        current = sum(scores[-window:]) / window
        return previous - current >= self._growth_regulator.crash_delta_threshold

    def _operational_load_reasons(self, batch: list[Episode]) -> tuple[str, ...]:
        """Identify recent evaluation burdens that make new procedure growth unsafe."""
        sample = batch[-self._growth_regulator.operational_load_window :]
        thresholds = (
            ("pain_index", self._growth_regulator.pain_threshold),
            ("retry_ratio", self._growth_regulator.retry_ratio_threshold),
            ("tool_error_ratio", self._growth_regulator.tool_error_ratio_threshold),
            (
                "budget_overrun_ratio",
                self._growth_regulator.budget_overrun_ratio_threshold,
            ),
        )
        overloaded: list[str] = []
        for metric, threshold in thresholds:
            values = [getattr(item.evaluation, metric) for item in sample]
            measured = [value for value in values if value is not None]
            if measured and sum(measured) / len(measured) >= threshold:
                overloaded.append(metric)
        return tuple(overloaded)

    def _is_stagnating(
        self, observations: tuple[GrowthObservation, ...], episode_count: int, coherence: float
    ) -> bool:
        target = episode_count - self._growth_regulator.stagnation_window
        baseline = next(
            (item for item in reversed(observations) if item.episode_count <= target), None
        )
        return baseline is not None and (
            coherence - baseline.consolidation_coherence
            <= self._growth_regulator.stagnation_coherence_delta
        )

    def _is_overgrowing(
        self, observations: tuple[GrowthObservation, ...], coherence: float
    ) -> bool:
        cutoff = datetime.now(UTC).timestamp() - self._growth_regulator.overgrowth_days * 86_400
        baseline = next(
            (item for item in observations if item.observed_at.timestamp() >= cutoff), None
        )
        return baseline is not None and (
            coherence - baseline.consolidation_coherence
            >= self._growth_regulator.overgrowth_coherence_rise
        )

    @staticmethod
    def _consolidation_coherence(knowledge: Iterable[L2Knowledge]) -> float:
        active = [item.confidence for item in knowledge if item.status is L2KnowledgeStatus.ACTIVE]
        return sum(active) / len(active) if active else 0.0

    @staticmethod
    def _append_growth_observation(
        checkpoint: OuterLoopCheckpoint,
        episode_count: int,
        knowledge: Iterable[L2Knowledge],
    ) -> tuple[GrowthObservation, ...]:
        observation = GrowthObservation(
            datetime.now(UTC),
            episode_count,
            RunOuterLoopService._consolidation_coherence(knowledge),
        )
        observations = (*checkpoint.growth_observations, observation)
        return observations[-1_000:]
