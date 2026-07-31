"""L1 Episode 정본 저장소 outbound port."""

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
    """L1 Episode 저장·조회·검색·재색인 구현이 따라야 할 경계."""

    def save(self, episode: Episode) -> PersistEpisodeResult:
        """Episode를 정본에 저장하고 index 결과를 반환한다.

        Args:
            episode: Finalize가 만든 불변 L1 경험.

        Returns:
            정본 저장 후의 projection 상태.

        최종 수정일: 2026-07-31
        """
        ...

    def get(self, episode_id: str) -> Episode | None: ...
    def resolve_latest_episode(self, episode_id: str) -> Episode | None: ...
    def search(
        self, query: str, *, top_k: int, filters: EpisodeSearchFilters
    ) -> list[EpisodeSearchHit]: ...
    def list_after(
        self, created_after: datetime, *, filters: EpisodeSearchFilters
    ) -> list[Episode]: ...
    def reindex(self, *, episode_ids: Sequence[str] | None = None) -> ReindexResult: ...
