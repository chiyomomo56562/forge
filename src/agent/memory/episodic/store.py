"""Chroma DB wrapper for L1 Episodic Memory.

Provides CRUD operations for episodes stored in a Chroma vector database.
Each episode is stored with its embedding and metadata for filtering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..schemas import Episode
from ...utils.logging import get_logger

logger = get_logger("agent.memory.episodic.store")


class EpisodicStore:
    """Chroma DB wrapper for episodic memory (L1).

    Args:
        chroma_path: Directory for the Chroma database.
        collection_name: Chroma collection name.
        encoder: Embedding encoder instance.
    """

    def __init__(
        self,
        chroma_path: str = "data/memory/episodic/chroma",
        collection_name: str = "episodes",
        encoder: Any = None,
    ):
        self.chroma_path = Path(chroma_path)
        self.collection_name = collection_name
        self._encoder = encoder
        self._client: Any = None
        self._collection: Any = None

    # ------------------------------------------------------------------
    # Lazy initialization
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Initialize Chroma client and collection (lazy)."""
        if self._collection is not None:
            return

        try:
            import chromadb
            self.chroma_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.chroma_path))
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            logger.info(f"Initialized Chroma collection '{self.collection_name}' at {self.chroma_path}")
        except ImportError:
            logger.warning("chromadb not installed, using in-memory fallback")
            self._collection = _InMemoryCollection()

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    def upsert(self, episode: Episode, embedding: list[float] | None = None) -> None:
        """Insert or update an episode.

        Args:
            episode: The episode to store.
            embedding: Pre-computed embedding. If None, uses encoder to compute.
        """
        self._ensure_initialized()

        if embedding is None:
            if self._encoder is None:
                raise ValueError("No encoder provided and no embedding given")
            text = self._episode_to_text(episode)
            embedding = self._encoder.encode(text)

        metadata = self._episode_to_metadata(episode)
        document = self._episode_to_text(episode)

        self._collection.upsert(
            ids=[episode.episode_id],
            embeddings=[embedding],
            documents=[document],
            metadatas=[metadata],
        )
        logger.debug(f"Upserted episode {episode.episode_id}")

    def get(self, episode_id: str) -> dict[str, Any] | None:
        """Retrieve a single episode by ID.

        Returns:
            Episode data dict or None if not found.
        """
        self._ensure_initialized()
        result = self._collection.get(ids=[episode_id])
        if not result["ids"]:
            return None
        return self._result_to_dict(result, index=0)

    def get_many(self, episode_ids: list[str]) -> list[dict[str, Any]]:
        """Retrieve multiple episodes by ID."""
        self._ensure_initialized()
        if not episode_ids:
            return []
        result = self._collection.get(ids=episode_ids)
        return [
            self._result_to_dict(result, index=i)
            for i in range(len(result["ids"]))
        ]

    def delete(self, episode_id: str) -> None:
        """Delete an episode by ID."""
        self._ensure_initialized()
        self._collection.delete(ids=[episode_id])
        logger.debug(f"Deleted episode {episode_id}")

    def count(self) -> int:
        """Return the number of stored episodes."""
        self._ensure_initialized()
        return self._collection.count()

    def list_recent(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the most recent *n* episodes sorted by timestamp descending.

        Used by the outer loop to aggregate metrics over a recent window.

        Args:
            n: Maximum number of episodes to return.

        Returns:
            List of episode dicts (``id``, ``document``, ``metadata``),
            sorted by timestamp descending (newest first).
        """
        self._ensure_initialized()

        # Chroma's get() without ids returns all items (up to limit)
        try:
            result = self._collection.get(limit=n)
        except TypeError:
            # Fallback for in-memory collection without limit support
            result = self._collection.get(ids=[])

        if not result.get("ids"):
            return []

        items: list[dict[str, Any]] = []
        metadatas = result.get("metadatas", [])
        documents = result.get("documents", [])
        for i, eid in enumerate(result["ids"]):
            items.append({
                "id": eid,
                "document": documents[i] if documents else "",
                "metadata": metadatas[i] if metadatas else {},
            })

        # Sort by timestamp descending (newest first)
        items.sort(
            key=lambda x: x["metadata"].get("timestamp", ""),
            reverse=True,
        )
        return items[:n]

    def query(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query episodes by embedding similarity.

        Args:
            query_embedding: Query vector.
            top_k: Number of results.
            where: Chroma metadata filter.

        Returns:
            List of episode dicts with similarity scores.
        """
        self._ensure_initialized()
        result = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
        )
        if not result["ids"] or not result["ids"][0]:
            return []
        return [
            self._query_result_to_dict(result, 0, i)
            for i in range(len(result["ids"][0]))
        ]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _episode_to_text(episode: Episode) -> str:
        """Convert an episode to a text representation for embedding."""
        parts = [episode.task, episode.execution_summary]
        if not episode.reflection.is_empty:
            parts.extend([
                episode.reflection.what_worked,
                episode.reflection.what_failed,
                episode.reflection.next_hint,
                episode.reflection.causal_condition,
            ])
        return " ".join(p for p in parts if p)

    @staticmethod
    def _episode_to_metadata(episode: Episode) -> dict[str, Any]:
        """Convert an episode to Chroma metadata (flat, JSON-serializable)."""
        meta: dict[str, Any] = {
            "episode_id": episode.episode_id,
            "task": episode.task,
            "task_category": episode.task_category,
            "has_reflection": episode.has_reflection,
            "timestamp": episode.timestamp,
            "status": episode.evaluation.status.value,
        }
        if episode.evaluation.success_score is not None:
            meta["success_score"] = episode.evaluation.success_score
        if episode.evaluation.pain_index is not None:
            meta["pain_index"] = episode.evaluation.pain_index
        if episode.evaluation.cib_score is not None:
            meta["cib_score"] = episode.evaluation.cib_score
        if episode.evaluation.phoenix_score is not None:
            meta["phoenix_score"] = episode.evaluation.phoenix_score
        if episode.evaluation.domain_score is not None:
            meta["domain_score"] = episode.evaluation.domain_score
        if episode.evaluation.reflection_score is not None:
            meta["reflection_score"] = episode.evaluation.reflection_score
        return meta

    @staticmethod
    def _result_to_dict(result: dict, index: int) -> dict[str, Any]:
        """Convert a Chroma get() result to a plain dict."""
        metadatas = result.get("metadatas", [])
        documents = result.get("documents", [])
        return {
            "id": result["ids"][index],
            "document": documents[index] if documents else "",
            "metadata": metadatas[index] if metadatas else {},
        }

    @staticmethod
    def _query_result_to_dict(result: dict, query_idx: int, result_idx: int) -> dict[str, Any]:
        """Convert a Chroma query() result to a plain dict."""
        ids = result["ids"][query_idx]
        distances = result.get("distances", [[]])[query_idx]
        metadatas = result.get("metadatas", [[]])[query_idx]
        documents = result.get("documents", [[]])[query_idx]
        return {
            "id": ids[result_idx],
            "score": 1.0 - distances[result_idx] if distances else 0.0,
            "document": documents[result_idx] if documents else "",
            "metadata": metadatas[result_idx] if metadatas else {},
        }


# ===========================================================================
# In-memory fallback (when chromadb is not installed)
# ===========================================================================

class _InMemoryCollection:
    """Minimal in-memory collection that mimics Chroma's interface.

    Used as a fallback for testing when chromadb is not available.
    Uses cosine similarity for queries.
    """

    def __init__(self):
        self._data: dict[str, dict[str, Any]] = {}

    def upsert(self, ids: list[str], embeddings: list[list[float]],
               documents: list[str] | None = None, metadatas: list[dict] | None = None) -> None:
        for i, eid in enumerate(ids):
            self._data[eid] = {
                "id": eid,
                "embedding": embeddings[i],
                "document": documents[i] if documents else "",
                "metadata": metadatas[i] if metadatas else {},
            }

    def get(self, ids: list[str], limit: int | None = None) -> dict[str, Any]:
        result_ids, result_docs, result_metas = [], [], []
        for eid in ids:
            if eid in self._data:
                result_ids.append(eid)
                result_docs.append(self._data[eid]["document"])
                result_metas.append(self._data[eid]["metadata"])
        # If ids is empty, return all items (up to limit)
        if not ids:
            for eid, item in self._data.items():
                result_ids.append(eid)
                result_docs.append(item["document"])
                result_metas.append(item["metadata"])
        if limit is not None:
            result_ids = result_ids[:limit]
            result_docs = result_docs[:limit]
            result_metas = result_metas[:limit]
        return {"ids": result_ids, "documents": result_docs, "metadatas": result_metas}

    def delete(self, ids: list[str]) -> None:
        for eid in ids:
            self._data.pop(eid, None)

    def count(self) -> int:
        return len(self._data)

    def query(self, query_embeddings: list[list[float]], n_results: int = 5,
              where: dict | None = None) -> dict[str, Any]:
        if not self._data:
            return {"ids": [[]], "distances": [[]], "metadatas": [[]], "documents": [[]]}

        results: list[tuple[str, float, dict, str]] = []
        for eid, item in self._data.items():
            # Apply metadata filter
            if where and not self._match_where(item["metadata"], where):
                continue
            sim = self._cosine_sim(query_embeddings[0], item["embedding"])
            dist = 1.0 - sim
            results.append((eid, dist, item["metadata"], item["document"]))

        results.sort(key=lambda x: x[1])
        results = results[:n_results]

        return {
            "ids": [[r[0] for r in results]],
            "distances": [[r[1] for r in results]],
            "metadatas": [[r[2] for r in results]],
            "documents": [[r[3] for r in results]],
        }

    @staticmethod
    def _match_where(metadata: dict, where: dict) -> bool:
        for k, v in where.items():
            if metadata.get(k) != v:
                return False
        return True

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)