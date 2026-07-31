from dataclasses import replace
from datetime import UTC, datetime

import pytest

from forge.adapters.outbound.memory import (
    EpisodeIndexUnavailableError,
    EpisodeIntegrityError,
    MemorySettings,
    RetryableMemoryOperationError,
    SqliteChromaEpisodeRepository,
    SqliteEpisodeStore,
)
from forge.domain.memory import (
    CibEvaluationStatus,
    Episode,
    EpisodeSearchFilters,
    EpisodeStatus,
    Evaluation,
    EvaluationReason,
    ExecutionOutcome,
    ExecutionResult,
    IndexState,
    MemoryValidationError,
    PromotionEligibility,
    Reflection,
)

_METRICS = (
    "result_quality",
    "requirement_coverage",
    "verification_confidence",
    "success_score",
    "pain_index",
    "retry_ratio",
    "tool_error_ratio",
    "budget_overrun_ratio",
    "rework_ratio",
    "human_intervention_ratio",
    "cib_score",
)


class FakeIndex:
    def __init__(self, *, fails: bool = False, query_fails: bool = False) -> None:
        self.fails = fails
        self.query_fails = query_fails
        self.candidates: list[tuple[str, float]] = []

    def upsert(self, episode: Episode) -> None:
        if self.fails:
            raise EpisodeIndexUnavailableError(code="chroma.upsert_unavailable")

    def query(self, _query: str, *, candidate_k: int, filters: EpisodeSearchFilters):
        assert candidate_k >= 20
        if self.query_fails:
            raise EpisodeIndexUnavailableError(code="chroma.query_unavailable")
        return self.candidates


def make_episode(episode_id: str, *, supersedes: str | None = None) -> Episode:
    reasons = tuple(
        EvaluationReason(metric, "not_provided", "not evaluated") for metric in _METRICS
    )
    return Episode(
        episode_id=episode_id,
        created_at=datetime(2026, 7, 30, tzinfo=UTC),
        task_request="remember this",
        execution=ExecutionResult("model replied", ExecutionOutcome.COMPLETED),
        evaluation=Evaluation(
            **{metric: None for metric in _METRICS},
            cib_evaluation_status=CibEvaluationStatus.NOT_EVALUATED,
            status=EpisodeStatus.PARTIAL,
            promotion_eligibility=PromotionEligibility.QUARANTINED,
            retryable=False,
            reasons=reasons,
        ),
        reflection=Reflection("worked", "not verified", "verify", "test condition"),
        evidence_complete=False,
        supersedes_episode_id=supersedes,
    )


def make_repository(tmp_path, index: FakeIndex, *, projection_version: int = 1):
    settings = MemorySettings(
        sqlite_path=tmp_path / "episodes.sqlite3",
        chroma_path=tmp_path / "chroma",
        projection_version=projection_version,
        embedding_model_id="test-embed",
    )
    return SqliteChromaEpisodeRepository(SqliteEpisodeStore(settings.sqlite_path), index, settings)


def test_save_uses_sqlite_as_canonical_record_when_index_succeeds(tmp_path):
    repository = make_repository(tmp_path, FakeIndex())

    result = repository.save(make_episode("ep_1"))

    assert result.index_state is IndexState.INDEXED
    assert repository.get("ep_1").task_request == "remember this"


def test_failed_initial_index_is_recoverable(tmp_path):
    repository = make_repository(tmp_path, FakeIndex(fails=True))
    assert repository.save(make_episode("ep_1")).index_state is IndexState.FAILED

    recovered = make_repository(tmp_path, FakeIndex())
    result = recovered.reindex()

    assert result.indexed_episode_ids == ("ep_1",)
    assert result.failed_episode_ids == ()


def test_search_resolves_original_candidate_to_latest_correction(tmp_path):
    index = FakeIndex()
    repository = make_repository(tmp_path, index)
    repository.save(make_episode("ep_1"))
    repository.save(make_episode("ep_2", supersedes="ep_1"))
    index.candidates = [("ep_1", 0.8)]

    hits = repository.search(
        "remember", top_k=1, filters=EpisodeSearchFilters(evidence_complete=False)
    )

    assert [(hit.episode.episode_id, hit.similarity) for hit in hits] == [("ep_2", 0.8)]


def test_reindex_marks_version_mismatch_stale_when_projection_fails(tmp_path):
    repository = make_repository(tmp_path, FakeIndex(), projection_version=1)
    repository.save(make_episode("ep_1"))

    changed_version = make_repository(tmp_path, FakeIndex(fails=True), projection_version=2)
    result = changed_version.reindex()

    assert result.failed_episode_ids == ("ep_1",)
    assert changed_version._store.state("ep_1") is IndexState.STALE


def test_duplicate_ids_distinguish_identical_episode_from_conflict(tmp_path):
    repository = make_repository(tmp_path, FakeIndex())
    episode = make_episode("ep_1")
    repository.save(episode)

    with pytest.raises(EpisodeIntegrityError, match="already exists") as identical:
        repository.save(episode)
    assert identical.value.code == "episode.already_exists"

    conflicting = replace(episode, task_request="different")
    with pytest.raises(EpisodeIntegrityError) as conflict:
        repository.save(conflicting)
    assert conflict.value.code == "episode.id_conflict"


def test_search_input_errors_are_typed(tmp_path):
    repository = make_repository(tmp_path, FakeIndex())

    with pytest.raises(MemoryValidationError) as query_error:
        repository.search("  ", top_k=1, filters=EpisodeSearchFilters())
    assert query_error.value.code == "search.invalid_query"

    with pytest.raises(MemoryValidationError) as limit_error:
        repository.search("query", top_k=0, filters=EpisodeSearchFilters())
    assert limit_error.value.code == "search.invalid_top_k"


def test_search_converts_internal_index_unavailability_to_retryable_error(tmp_path):
    repository = make_repository(tmp_path, FakeIndex(query_fails=True))

    with pytest.raises(RetryableMemoryOperationError) as error:
        repository.search("query", top_k=1, filters=EpisodeSearchFilters())

    assert error.value.code == "chroma.query_unavailable"
