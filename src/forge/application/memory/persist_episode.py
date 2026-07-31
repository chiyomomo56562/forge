from forge.domain.memory import Episode, PersistEpisodeResult
from forge.ports.outbound import EpisodeRepository


class PersistEpisodeService:
    def __init__(self, repository: EpisodeRepository) -> None:
        self._repository = repository

    def handle(self, episode: Episode) -> PersistEpisodeResult:
        return self._repository.save(episode)
