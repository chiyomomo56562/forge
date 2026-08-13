from datetime import UTC, datetime, timedelta

from forge.adapters.outbound.outer_loop import JsonOuterLoopStore
from forge.application.outer_loop import (
    GrowthRegulatorPolicy,
    L3GrowthPolicy,
    OuterLoopPolicy,
    RunOuterLoopService,
)
from forge.domain.identity import Capability
from forge.domain.memory import (
    CibEvaluationStatus,
    Episode,
    EpisodeSearchFilters,
    EpisodeStatus,
    Evaluation,
    ExecutionOutcome,
    ExecutionResult,
    PromotionEligibility,
    Reflection,
)
from forge.domain.outer_loop import (
    GrowthObservation,
    L2Knowledge,
    L2KnowledgeStatus,
    OuterLoopCheckpoint,
)


class DenyConstitution:
    def evaluate_l2_evidence(self, _episode):
        from forge.domain.constitution import CibDecision

        return CibDecision(False, "memory.sensitive_content")

    def evaluate_memory_text(self, _content):
        from forge.domain.constitution import CibDecision

        return CibDecision(False, "growth.forbidden_direction")


class AllowConstitution:
    def evaluate_l2_evidence(self, _episode):
        from forge.domain.constitution import CibDecision

        return CibDecision(True, "cib.passed")

    def evaluate_memory_text(self, _content):
        from forge.domain.constitution import CibDecision

        return CibDecision(True, "memory.safe")


class DirectionDenyConstitution(AllowConstitution):
    def evaluate_memory_text(self, _content):
        from forge.domain.constitution import CibDecision

        return CibDecision(False, "growth.forbidden_direction")


class Identity:
    def __init__(self, confidence=1.0, success_rate=1.0):
        self._capability = Capability("general", "General", success_rate, confidence, 0.5, 3)

    def capability_for(self, _category):
        return self._capability


class Procedural:
    def __init__(self):
        self.seeded = []

    def refresh_all(self):
        return ()

    def get_by_source_l2(self, _knowledge_id):
        return None

    def seed_from_l2(self, knowledge):
        self.seeded.append(knowledge.knowledge_id)
        return object()


class FakeEpisodeRepository:
    def __init__(self, episodes: list[Episode]) -> None:
        self.episodes = episodes
        self.filters: list[EpisodeSearchFilters] = []

    def list_after(
        self, created_after: datetime, *, filters: EpisodeSearchFilters
    ) -> list[Episode]:
        self.filters.append(filters)
        return [episode for episode in self.episodes if episode.created_at > created_after]


def _episode(
    number: int,
    *,
    status: EpisodeStatus = EpisodeStatus.SUCCESS,
    eligibility: PromotionEligibility = PromotionEligibility.ELIGIBLE,
) -> Episode:
    success = status is EpisodeStatus.SUCCESS
    return Episode(
        episode_id=f"ep_{number}",
        created_at=datetime(2026, 8, 12, tzinfo=UTC) + timedelta(seconds=number),
        task_request="inspect the repository",
        execution=ExecutionResult("completed" if success else "failed", ExecutionOutcome.COMPLETED),
        evaluation=Evaluation(
            result_quality=1.0,
            requirement_coverage=1.0,
            verification_confidence=1.0,
            success_score=1.0 if success else 0.0,
            pain_index=0.0,
            retry_ratio=0.0,
            tool_error_ratio=0.0,
            budget_overrun_ratio=0.0,
            rework_ratio=0.0,
            human_intervention_ratio=0.0,
            cib_score=1.0,
            cib_evaluation_status=CibEvaluationStatus.PASSED,
            status=status,
            promotion_eligibility=eligibility,
            retryable=False,
        ),
        reflection=Reflection("use a focused search", "", "search first", "repository inspection"),
        evidence_complete=True,
        raw_event_refs=(f"evt_{number}",),
    )


def _service(
    tmp_path,
    episodes: list[Episode],
    *,
    constitution=None,
    identity=None,
    procedural=None,
    l3_growth=None,
) -> tuple[RunOuterLoopService, JsonOuterLoopStore]:
    store = JsonOuterLoopStore(tmp_path / "outer-loop.json")
    return (
        RunOuterLoopService(
            FakeEpisodeRepository(episodes),
            store,
            OuterLoopPolicy(batch_size=3, min_episodes_for_generalization=3),
            constitution,
            procedural,
            identity,
            l3_growth,
        ),
        store,
    )


def test_promotes_eligible_l1_pattern_and_persists_checkpoint(tmp_path):
    service, store = _service(
        tmp_path,
        [
            _episode(1),
            _episode(2),
            _episode(3),
            _episode(4, eligibility=PromotionEligibility.QUARANTINED),
        ],
    )

    result = service.handle()

    assert result.processed_episode_ids == ("ep_1", "ep_2", "ep_3")
    assert len(result.promoted_knowledge_ids) == 1
    knowledge = store.load_knowledge()[0]
    assert knowledge.support_episode_ids == ("ep_1", "ep_2", "ep_3")
    assert knowledge.confidence == 1.0
    assert store.load_checkpoint().watermark == _episode(3).created_at


def test_quarantined_l1_advances_checkpoint_without_becoming_pattern_evidence(tmp_path):
    service, store = _service(tmp_path, [_episode(1, eligibility=PromotionEligibility.QUARANTINED)])

    result = service.handle(force=True)

    assert result.processed_episode_ids == ("ep_1",)
    assert store.load_candidates() == []
    assert store.load_checkpoint().processed_episode_ids == ("ep_1",)


def test_counterexample_weakens_existing_l2_knowledge(tmp_path):
    episodes = [_episode(1), _episode(2), _episode(3)]
    service, store = _service(tmp_path, episodes)
    first = service.handle()

    episodes.append(_episode(4, status=EpisodeStatus.FAILURE))
    second = service.handle(force=True)

    assert first.promoted_knowledge_ids
    assert second.updated_knowledge_ids == first.promoted_knowledge_ids
    assert store.load_knowledge()[0].status.value == "weakened"
    assert store.load_knowledge()[0].confidence == 0.75


def test_constitution_can_block_l1_evidence_from_l2_promotion(tmp_path):
    service, store = _service(tmp_path, [_episode(1), _episode(2), _episode(3)])
    service._constitution = DenyConstitution()

    result = service.handle()

    assert result.promoted_knowledge_ids == ()
    assert store.load_candidates() == []
    assert result.checkpoint.processed_episode_ids == ("ep_1", "ep_2", "ep_3")


def test_l3_growth_direction_requires_l4_and_l5_approval(tmp_path):
    service, _store = _service(tmp_path, [])
    knowledge = L2Knowledge(
        "l2_growth", "pc_growth", "inspect", "repository", 1.0,
        L2KnowledgeStatus.ACTIVE, ("ep_1",), (), datetime.now(UTC), "general",
    )
    service._constitution = DenyConstitution()
    service._identity = Identity()

    assert service._allows_l3_growth(knowledge, 0) is False

    service._constitution = AllowConstitution()
    service._identity = Identity(confidence=0.2)

    assert service._allows_l3_growth(knowledge, 0) is False


def test_l3_growth_budget_limits_new_seeds(tmp_path):
    service, _store = _service(tmp_path, [])
    service._constitution = AllowConstitution()
    service._identity = Identity()
    service._l3_growth = L3GrowthPolicy(max_new_seeds_per_run=1)
    knowledge = L2Knowledge(
        "l2_growth", "pc_growth", "inspect", "repository", 1.0,
        L2KnowledgeStatus.ACTIVE, ("ep_1",), (), datetime.now(UTC), "general",
    )

    assert service._allows_l3_growth(knowledge, 0) is True
    assert service._allows_l3_growth(knowledge, 1) is False


def test_l3_growth_defers_seed_but_preserves_l2_when_l4_denies_direction(tmp_path):
    procedural = Procedural()
    service, store = _service(
        tmp_path,
        [_episode(1), _episode(2), _episode(3)],
        constitution=DirectionDenyConstitution(),
        identity=Identity(),
        procedural=procedural,
    )

    result = service.handle()

    assert result.promoted_knowledge_ids
    assert result.deferred_l3_knowledge_ids == result.promoted_knowledge_ids
    assert procedural.seeded == []
    assert len(store.load_knowledge()) == 1


def test_l3_growth_seeds_only_with_l4_l5_approval_and_available_budget(tmp_path):
    procedural = Procedural()
    service, _store = _service(
        tmp_path,
        [_episode(1), _episode(2), _episode(3)],
        constitution=AllowConstitution(),
        identity=Identity(),
        procedural=procedural,
        l3_growth=L3GrowthPolicy(max_new_seeds_per_run=1),
    )

    result = service.handle()

    assert result.deferred_l3_knowledge_ids == ()
    assert procedural.seeded == list(result.promoted_knowledge_ids)


def test_m16_freezes_new_l3_seeds_after_a_success_rate_crash(tmp_path):
    service, _store = _service(tmp_path, [])
    service._growth_regulator = GrowthRegulatorPolicy(
        crash_window=2,
        crash_delta_threshold=0.5,
    )
    batch = [
        _episode(1),
        _episode(2),
        _episode(3, status=EpisodeStatus.FAILURE),
        _episode(4, status=EpisodeStatus.FAILURE),
    ]

    limit, reasons = service._l3_growth_limit(batch, OuterLoopCheckpoint(), {})

    assert limit == 0
    assert reasons == ("success_rate_crash",)


def test_m16_throttles_stagnating_l3_growth_and_persists_observation(tmp_path):
    service, store = _service(tmp_path, [])
    service._growth_regulator = GrowthRegulatorPolicy(stagnation_window=3)
    knowledge = L2Knowledge(
        "l2_growth", "pc_growth", "inspect", "repository", 0.8,
        L2KnowledgeStatus.ACTIVE, ("ep_1",), (), datetime.now(UTC), "general",
    )
    checkpoint = OuterLoopCheckpoint(
        processed_episode_ids=("ep_1", "ep_2", "ep_3"),
        growth_observations=(GrowthObservation(datetime.now(UTC), 0, 0.8),),
    )

    limit, reasons = service._l3_growth_limit([], checkpoint, {knowledge.knowledge_id: knowledge})
    persisted = OuterLoopCheckpoint(
        growth_observations=service._append_growth_observation(checkpoint, 3, [knowledge])
    )
    store.save(candidates=[], knowledge=[], checkpoint=persisted)

    assert limit == 1
    assert reasons == ("consolidation_stagnation",)
    assert store.load_checkpoint().growth_observations == persisted.growth_observations
