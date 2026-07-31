from forge.domain.memory import EpisodeSearchFilters, EpisodeSearchHit
from forge.ports.outbound import EpisodeRepository


class SearchEpisodesService:
    def __init__(self, repository: EpisodeRepository) -> None:
        self._repository = repository

    def handle(
        self, query: str, *, top_k: int, filters: EpisodeSearchFilters
    ) -> list[EpisodeSearchHit]:
        if not query.strip():
            raise ValueError("Memory search query must not be empty")
        return self._repository.search(query, top_k=top_k, filters=filters)
