"""Semantic retriever for L1 Episodic Memory.

Implements:
    - **Density-first search**: reflection-populated episodes are prioritised.
    - **Selective injection**: narrow search with optional progressive expansion.
    - **Metadata filtering**: by task category, status, has_reflection, etc.
"""

from __future__ import annotations

from typing import Any

from ..schemas import Episode
from ...utils.logging import get_logger
from .store import EpisodicStore

logger = get_logger("agent.memory.episodic.retriever")


class EpisodicRetriever:
    """Retrieve episodes from L1 episodic memory with density-first ordering.

    Args:
        store: The EpisodicStore instance.
        encoder: The embedding encoder instance.
        default_top_k: Default number of results.
        density_first: If True, reflection-populated episodes are returned first.
        expand_step: Additional results to fetch when expanding.
    """

    def __init__(
        self,
        store: EpisodicStore,
        encoder: Any = None,
        default_top_k: int = 5,
        density_first: bool = True,
        expand_step: int = 5,
    ):
        self.store = store
        self._encoder = encoder
        self.default_top_k = default_top_k
        self.density_first = density_first
        self.expand_step = expand_step

    # ------------------------------------------------------------------
    # Core retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        expand: bool = False,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve episodes matching a query.

        Args:
            query: Natural language query text.
            top_k: Number of results. Defaults to ``self.default_top_k``.
            expand: If True, fetch ``top_k + expand_step`` results and
                    re-rank with density-first ordering, returning ``top_k``.
            where: Additional Chroma metadata filter.

        Returns:
            List of result dicts with ``id``, ``score``, ``document``, ``metadata``.
        """
        if top_k is None:
            top_k = self.default_top_k

        # Compute query embedding
        if self._encoder is None:
            raise ValueError("No encoder provided to retriever")
        query_embedding = self._encoder.encode(query)

        # Determine fetch size
        fetch_k = top_k + self.expand_step if expand else top_k

        # Query store
        results = self.store.query(
            query_embedding=query_embedding,
            top_k=fetch_k,
            where=where,
        )

        # Apply density-first ordering
        if self.density_first and len(results) > 1:
            results = self._density_first_sort(results)

        # Trim to top_k
        return results[:top_k]

    def retrieve_with_reflection(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve only episodes that have reflection data (density-first).

        This is the primary retrieval method for the inner loop planning stage:
        "테스크와 의미론적으로 가장 유사한 리플렉션 데이터"
        """
        if top_k is None:
            top_k = self.default_top_k

        return self.retrieve(
            query=query,
            top_k=top_k,
            where={"has_reflection": True},
        )

    def retrieve_expand(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve with progressive expansion.

        First fetches reflection-only episodes (narrow search).
        If insufficient, expands to all episodes and re-ranks.
        """
        if top_k is None:
            top_k = self.default_top_k

        # Step 1: Narrow search (reflection only)
        results = self.retrieve_with_reflection(query, top_k=top_k)

        # Step 2: If insufficient, expand
        if len(results) < top_k:
            all_results = self.retrieve(
                query=query,
                top_k=top_k + self.expand_step,
                expand=True,
            )
            # Merge, deduplicate by id
            seen = {r["id"] for r in results}
            for r in all_results:
                if r["id"] not in seen:
                    results.append(r)
                    seen.add(r["id"])
                    if len(results) >= top_k:
                        break

        return results[:top_k]

    # ------------------------------------------------------------------
    # Density-first ordering
    # ------------------------------------------------------------------

    @staticmethod
    def _density_first_sort(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort results: reflection episodes first (by score), then non-reflection (by score).

        Within each group, higher similarity score comes first.
        """
        with_reflection = [r for r in results if r.get("metadata", {}).get("has_reflection", False)]
        without_reflection = [r for r in results if not r.get("metadata", {}).get("has_reflection", False)]

        with_reflection.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        without_reflection.sort(key=lambda r: r.get("score", 0.0), reverse=True)

        return with_reflection + without_reflection