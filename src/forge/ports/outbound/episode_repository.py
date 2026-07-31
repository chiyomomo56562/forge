from typing import Protocol

from forge.domain.memory import Episode


class EpisodeRepository(Protocol):
    def save(self, episode: Episode) -> Episode: ...
