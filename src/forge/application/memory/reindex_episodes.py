from collections.abc import Sequence

from forge.domain.memory import ReindexResult
from forge.ports.outbound import EpisodeRepository


class ReindexEpisodesService:
    def __init__(self, repository: EpisodeRepository) -> None:
        self._repository = repository

    def handle(self, *, episode_ids: Sequence[str] | None = None) -> ReindexResult:
        return self._repository.reindex(episode_ids=episode_ids)
