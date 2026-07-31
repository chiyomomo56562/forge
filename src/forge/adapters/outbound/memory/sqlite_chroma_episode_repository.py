"""Composite L1 repository: SQLite canonical data with Chroma search projection."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from chromadb.errors import ChromaError

from forge.adapters.outbound.memory.chroma_episode_index import EpisodeIndex
from forge.adapters.outbound.memory.errors import (
    EpisodeIndexUnavailableError,
    EpisodeIntegrityError,
    MemoryInfrastructureError,
    RetryableMemoryOperationError,
)
from forge.adapters.outbound.memory.settings import MemorySettings
from forge.adapters.outbound.memory.sqlite_episode_store import SqliteEpisodeStore
from forge.domain.memory import (
    Episode,
    EpisodeSearchFilters,
    EpisodeSearchHit,
    IndexState,
    MemoryValidationError,
    PersistEpisodeResult,
    ReindexResult,
)


class SqliteChromaEpisodeRepository:
    def __init__(
        self,
        store: SqliteEpisodeStore,
        index: EpisodeIndex,
        settings: MemorySettings,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._index = index
        self._settings = settings
        self._clock = clock

    def save(self, episode: Episode) -> PersistEpisodeResult:
        existing = self._store.get(episode.episode_id)
        if existing is not None:
            code = (
                "episode.already_exists"
                if self._same_canonical_episode(existing, episode)
                else "episode.id_conflict"
            )
            raise EpisodeIntegrityError(code=code, safe_message="Episode ID already exists.")
        self._validate_supersession(episode)
        self._store.insert(episode)
        try:
            self._index.upsert(episode)
        except EpisodeIndexUnavailableError:
            self._store.set_index_state(episode.episode_id, IndexState.FAILED)
            return PersistEpisodeResult(episode.episode_id, IndexState.FAILED)
        except ChromaError as exc:
            raise MemoryInfrastructureError(
                code="chroma.configuration_failed", safe_message="Episode indexing failed."
            ) from exc
        self._store.set_indexed(
            episode.episode_id,
            self._settings.projection_version,
            self._settings.embedding_model_id,
            self._clock(),
        )
        return PersistEpisodeResult(episode.episode_id, IndexState.INDEXED)

    def get(self, episode_id: str) -> Episode | None:
        return self._store.get(episode_id)

    def resolve_latest_episode(self, episode_id: str) -> Episode | None:
        current = self.get(episode_id)
        if current is None:
            return None
        visited: set[str] = set()
        while True:
            if current.episode_id in visited:
                raise EpisodeIntegrityError(
                    code="supersession.cycle", safe_message="Episode supersession is invalid."
                )
            visited.add(current.episode_id)
            successor_id = self._store.successor_id(current.episode_id)
            if successor_id is None:
                return current
            successor = self.get(successor_id)
            if successor is None:
                raise EpisodeIntegrityError(
                    code="supersession.missing_successor",
                    safe_message="Episode supersession is invalid.",
                )
            current = successor

    def search(
        self, query: str, *, top_k: int, filters: EpisodeSearchFilters
    ) -> list[EpisodeSearchHit]:
        if not query.strip():
            raise MemoryValidationError(
                code="search.invalid_query", safe_message="Search query is required."
            )
        if top_k <= 0:
            raise MemoryValidationError(
                code="search.invalid_top_k", safe_message="Search limit is invalid."
            )
        candidate_k = max(top_k * 5, 20)
        try:
            candidates = self._index.query(query, candidate_k=candidate_k, filters=filters)
        except EpisodeIndexUnavailableError as exc:
            raise RetryableMemoryOperationError(
                code=exc.code, safe_message="Episode search is temporarily unavailable."
            ) from exc
        except ChromaError as exc:
            raise MemoryInfrastructureError(
                code="chroma.configuration_failed", safe_message="Episode search failed."
            ) from exc
        resolved: dict[str, float] = {}
        for episode_id, similarity in candidates:
            latest = self.resolve_latest_episode(episode_id)
            if latest is None:
                continue
            resolved[latest.episode_id] = max(
                similarity, resolved.get(latest.episode_id, float("-inf"))
            )
        hits: list[EpisodeSearchHit] = []
        for episode_id, similarity in resolved.items():
            episode = self.get(episode_id)
            if episode is None or not self._store.index_is_current(
                episode_id, self._settings.projection_version, self._settings.embedding_model_id
            ):
                continue
            if self._matches(episode, filters):
                hits.append(EpisodeSearchHit(episode=episode, similarity=similarity))
        return sorted(hits, key=lambda hit: (-hit.similarity, -hit.episode.created_at.timestamp()))[
            :top_k
        ]

    def list_after(
        self, created_after: datetime, *, filters: EpisodeSearchFilters
    ) -> list[Episode]:
        return [
            episode
            for episode in self._store.list_after(created_after)
            if self._matches(episode, filters)
        ]

    def reindex(self, *, episode_ids: Sequence[str] | None = None) -> ReindexResult:
        candidates = self._store.reindex_candidates(
            tuple(episode_ids) if episode_ids is not None else None
        )
        indexed: list[str] = []
        failed: list[str] = []
        for episode in candidates:
            previous = self._store.state(episode.episode_id)
            is_current = self._store.index_is_current(
                episode.episode_id,
                self._settings.projection_version,
                self._settings.embedding_model_id,
            )
            if previous is IndexState.INDEXED and not is_current:
                self._store.set_index_state(episode.episode_id, IndexState.STALE)
                previous = IndexState.STALE
            try:
                self._index.upsert(episode)
            except EpisodeIndexUnavailableError:
                if previous is IndexState.STALE:
                    self._store.set_index_state(episode.episode_id, IndexState.STALE)
                elif previous is IndexState.PENDING:
                    self._store.set_index_state(episode.episode_id, IndexState.FAILED)
                failed.append(episode.episode_id)
            except ChromaError as exc:
                raise MemoryInfrastructureError(
                    code="chroma.configuration_failed", safe_message="Episode indexing failed."
                ) from exc
            else:
                self._store.set_indexed(
                    episode.episode_id,
                    self._settings.projection_version,
                    self._settings.embedding_model_id,
                    self._clock(),
                )
                indexed.append(episode.episode_id)
        return ReindexResult(tuple(indexed), tuple(failed))

    def _validate_supersession(self, episode: Episode) -> None:
        if episode.supersedes_episode_id is None:
            return
        parent = self.get(episode.supersedes_episode_id)
        if parent is None:
            raise EpisodeIntegrityError(
                code="supersession.missing_parent", safe_message="Episode supersession is invalid."
            )
        visited = {episode.episode_id}
        current = parent
        while current.supersedes_episode_id is not None:
            if current.episode_id in visited:
                raise EpisodeIntegrityError(
                    code="supersession.cycle", safe_message="Episode supersession is invalid."
                )
            visited.add(current.episode_id)
            next_episode = self.get(current.supersedes_episode_id)
            if next_episode is None:
                raise EpisodeIntegrityError(
                    code="supersession.missing_parent",
                    safe_message="Episode supersession is invalid.",
                )
            current = next_episode
        if self._store.successor_id(parent.episode_id) is not None:
            raise EpisodeIntegrityError(
                code="supersession.branch", safe_message="Episode supersession is invalid."
            )

    @staticmethod
    def _matches(episode: Episode, filters: EpisodeSearchFilters) -> bool:
        if filters.task_category is not None and episode.task_category != filters.task_category:
            return False
        if filters.status is not None and episode.evaluation.status is not filters.status:
            return False
        if (
            filters.promotion_eligibility is not None
            and episode.evaluation.promotion_eligibility is not filters.promotion_eligibility
        ):
            return False
        if episode.evidence_complete != filters.evidence_complete:
            return False
        return filters.created_after is None or episode.created_at > filters.created_after

    @staticmethod
    def _same_canonical_episode(existing: Episode, incoming: Episode) -> bool:
        """Compare immutable canonical fields, excluding projection-only storage state."""

        def normalize(episode: Episode) -> Episode:
            return replace(
                episode,
                evaluation=replace(
                    episode.evaluation,
                    reasons=tuple(
                        sorted(episode.evaluation.reasons, key=lambda reason: reason.metric)
                    ),
                ),
            )

        return normalize(existing) == normalize(incoming)
