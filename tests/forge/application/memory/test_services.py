from collections.abc import Sequence
from datetime import datetime

from forge.application.memory import (
    PersistEpisodeService,
    ReindexEpisodesService,
    SearchEpisodesService,
)
from forge.domain.memory import EpisodeSearchFilters, PersistEpisodeResult, ReindexResult


class FakeRepository:
    def __init__(self) -> None:
        self.saved = None
        self.searches = []
        self.reindex_ids = None

    def save(self, episode):
        self.saved = episode
        return PersistEpisodeResult("ep_1", "indexed")

    def search(self, query: str, *, top_k: int, filters: EpisodeSearchFilters):
        self.searches.append((query, top_k, filters))
        return []

    def reindex(self, *, episode_ids: Sequence[str] | None = None):
        self.reindex_ids = episode_ids
        return ReindexResult((), ())

    def get(self, _episode_id: str):
        return None

    def resolve_latest_episode(self, _episode_id: str):
        return None

    def list_after(self, _created_after: datetime, *, filters: EpisodeSearchFilters):
        return []


def test_services_delegate_only_to_repository_port():
    repository = FakeRepository()

    assert PersistEpisodeService(repository).handle("episode").episode_id == "ep_1"
    assert (
        SearchEpisodesService(repository).handle("query", top_k=1, filters=EpisodeSearchFilters())
        == []
    )
    assert ReindexEpisodesService(repository).handle(episode_ids=["ep_1"]).indexed_episode_ids == ()
    assert repository.saved == "episode"
    assert repository.searches[0][0] == "query"
    assert repository.reindex_ids == ["ep_1"]
