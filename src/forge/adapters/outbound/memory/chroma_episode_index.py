"""Chroma projection for L1 episodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from chromadb.errors import InternalError, QuotaError, RateLimitError

from forge.adapters.outbound.memory.errors import EpisodeIndexUnavailableError
from forge.domain.memory import Episode, EpisodeSearchFilters


class EpisodeIndex(Protocol):
    def upsert(self, episode: Episode) -> None: ...

    def query(
        self, query: str, *, candidate_k: int, filters: EpisodeSearchFilters
    ) -> list[tuple[str, float]]: ...


def make_search_document(episode: Episode) -> str:
    return "\n".join(
        (
            f"[TASK]\n{episode.task_request}",
            f"[CONDITION]\n{episode.reflection.causal_condition}",
            (f"[RESULT]\n{episode.evaluation.status.value}: {episode.execution.summary}"),
            f"[WHAT_WORKED]\n{episode.reflection.what_worked}",
            f"[WHAT_FAILED]\n{episode.reflection.what_failed}",
            f"[NEXT_HINT]\n{episode.reflection.next_hint}",
        )
    )


@dataclass
class ChromaEpisodeIndex:
    collection: object
    projection_version: int
    embedding_model_id: str

    def upsert(self, episode: Episode) -> None:
        metadata = {
            "schema_version": episode.schema_version,
            "episode_id": episode.episode_id,
            "created_at": episode.created_at.isoformat(),
            "task_category": episode.task_category,
            "status": episode.evaluation.status.value,
            "success_score": episode.evaluation.success_score
            if episode.evaluation.success_score is not None
            else -1.0,
            "pain_index": episode.evaluation.pain_index
            if episode.evaluation.pain_index is not None
            else -1.0,
            "cib_score": episode.evaluation.cib_score
            if episode.evaluation.cib_score is not None
            else -1.0,
            "cib_evaluation_status": episode.evaluation.cib_evaluation_status.value,
            "promotion_eligibility": episode.evaluation.promotion_eligibility.value,
            "has_reflection": episode.reflection.has_content,
            "evidence_complete": episode.evidence_complete,
            "projection_version": self.projection_version,
            "embedding_model_id": self.embedding_model_id,
            "supersedes_episode_id": episode.supersedes_episode_id or "",
        }
        try:
            self.collection.upsert(
                ids=[episode.episode_id],
                documents=[make_search_document(episode)],
                metadatas=[metadata],
            )
        except (
            TimeoutError,
            ConnectionError,
            OSError,
            InternalError,
            QuotaError,
            RateLimitError,
        ) as exc:
            raise EpisodeIndexUnavailableError(code="chroma.upsert_unavailable") from exc

    def query(
        self, query: str, *, candidate_k: int, filters: EpisodeSearchFilters
    ) -> list[tuple[str, float]]:
        where: dict[str, object] = {"evidence_complete": filters.evidence_complete}
        if filters.task_category is not None:
            where["task_category"] = filters.task_category
        if filters.status is not None:
            where["status"] = filters.status.value
        if filters.promotion_eligibility is not None:
            where["promotion_eligibility"] = filters.promotion_eligibility.value
        try:
            result = self.collection.query(query_texts=[query], n_results=candidate_k, where=where)
        except (
            TimeoutError,
            ConnectionError,
            OSError,
            InternalError,
            QuotaError,
            RateLimitError,
        ) as exc:
            raise EpisodeIndexUnavailableError(code="chroma.query_unavailable") from exc
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [
            (str(episode_id), 1.0 - float(distance))
            for episode_id, distance in zip(ids, distances, strict=True)
        ]
