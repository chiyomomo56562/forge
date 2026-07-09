"""Embedding encoder for L1 Episodic Memory.

Thin wrapper around :class:`agent.llm.LLMClient` that provides embedding
functionality.  All LLM/embedding calls are routed through the unified
LLM client — this module does NOT import LLM SDKs directly.

Includes a file-based cache to avoid re-embedding identical text.
"""

from __future__ import annotations

from typing import Any

from ...llm.client import LLMClient, LLMConfig
from ...utils.logging import get_logger

logger = get_logger("agent.memory.episodic.encoder")


class EmbeddingEncoder:
    """Encode text into dense vectors via the unified LLMClient.

    This is a thin wrapper around :class:`agent.llm.LLMClient` that provides
    the same ``encode()`` / ``encode_batch()`` interface expected by the
    episodic store and retriever.

    Args:
        llm_client: A pre-configured LLMClient instance. If None, a new
            client is created from the default config path.
        config_path: Path to agent.yml (used when llm_client is None).
        dimension: Override embedding dimension (auto-detected from config
            if not provided).
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        config_path: str = "config/agent.yml",
        dimension: int | None = None,
    ):
        if llm_client is not None:
            self._client = llm_client
        else:
            self._client = LLMClient.from_config(config_path)

        # Determine dimension
        if dimension is not None:
            self.dimension = dimension
        elif self._client.config.embed_backend == "ollama":
            self.dimension = self._client.config.embed_ollama_dimension
        else:
            self.dimension = self._client.config.embed_dimension

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode(self, text: str) -> list[float]:
        """Encode a single text into a vector.

        Delegates to LLMClient.embed() which handles:
            - Backend selection (local | ollama | openai)
            - File-based caching
            - Hash fallback when no SDK is installed
        """
        if not text or not text.strip():
            return [0.0] * self.dimension
        return self._client.embed(text)

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode multiple texts (batch)."""
        return [self.encode(t) for t in texts]

    # ------------------------------------------------------------------
    # Hash-based fallback (exposed for testing)
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_embedding(text: str, dimension: int = 384) -> list[float]:
        """Generate a deterministic pseudo-embedding from text hash.

        This is a fallback when no embedding library is available.
        NOT suitable for production semantic search — only for testing.
        """
        return LLMClient._hash_embedding(text, dimension)