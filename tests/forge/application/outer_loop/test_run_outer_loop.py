from datetime import UTC, datetime, timedelta

from forge.adapters.outbound.outer_loop import JsonOuterLoopStore
from forge.application.outer_loop import OuterLoopPolicy, RunOuterLoopService
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


class DenyConstitution:
    def evaluate_l2_evidence(self, _episode):
        from forge.domain.constitution import CibDecision

        return CibDecision(False, "memory.sensitive_content")


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


def _service(tmp_path, episodes: list[Episode]) -> tuple[RunOuterLoopService, JsonOuterLoopStore]:
    store = JsonOuterLoopStore(tmp_path / "outer-loop.json")
    return (
        RunOuterLoopService(
            FakeEpisodeRepository(episodes),
            store,
            OuterLoopPolicy(batch_size=3, min_episodes_for_generalization=3),
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
