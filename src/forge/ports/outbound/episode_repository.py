from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from forge.domain.memory import (
    Episode,
    EpisodeSearchFilters,
    EpisodeSearchHit,
    PersistEpisodeResult,
    ReindexResult,
)


class EpisodeRepository(Protocol):
    def save(self, episode: Episode) -> PersistEpisodeResult: ...

    def get(self, episode_id: str) -> Episode | None: ...

    def resolve_latest_episode(self, episode_id: str) -> Episode | None: ...

    def search(
        self, query: str, *, top_k: int, filters: EpisodeSearchFilters
    ) -> list[EpisodeSearchHit]: ...

    def list_after(
        self, created_after: datetime, *, filters: EpisodeSearchFilters
    ) -> list[Episode]: ...

    def reindex(self, *, episode_ids: Sequence[str] | None = None) -> ReindexResult: ...
