"""Unified LLM client for the Forge agent framework.

Provides a single interface for:
    - **Chat completions** (ollama | openai)
    - **Embeddings** (local | ollama | openai)

All other modules (encoder, cognition, etc.) use this client instead of
importing LLM SDKs directly.

Usage::

    from agent.llm import LLMClient

    client = LLMClient.from_config("config/agent.yml")

    # Chat
    response = client.chat("Hello, who are you?")

    # Embedding
    vec = client.embed("text to embed")
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..utils.logging import get_logger

logger = get_logger("agent.llm.client")


# ===========================================================================
# Data classes
# ===========================================================================

@dataclass
class LLMConfig:
    """Configuration for the LLM client."""
    # Chat
    backend: str = "ollama"               # ollama | openai
    # Ollama chat
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "glm-5.2:cloud"
    ollama_temperature: float = 0.3
    ollama_max_tokens: int = 120000
    ollama_timeout: int = 120
    ollama_max_retries: int = 3
    # OpenAI chat
    openai_model: str = "gpt-5.6-luna"
    openai_temperature: float = 0.3
    openai_max_tokens: int = 4096
    openai_timeout: int = 120
    openai_max_retries: int = 3
    openai_api_key: str = ""
    # Embedding
    embed_backend: str = "local"          # local | ollama | openai
    embed_model: str = "all-MiniLM-L6-v2"
    embed_dimension: int = 384
    embed_cache_dir: str = "data/cache/embeddings"
    embed_ollama_base_url: str = "http://localhost:11434"
    embed_ollama_model: str = "nomic-embed-text"
    embed_ollama_dimension: int = 768

    @classmethod
    def from_yaml(cls, path: str = "config/agent.yml") -> LLMConfig:
        """Load config from agent.yml."""
        path = Path(path)
        if not path.exists():
            logger.warning(f"Config file not found: {path}, using defaults")
            return cls()

        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        llm = data.get("llm", {})
        emb = data.get("embedding", {})
        ollama = llm.get("ollama", {})
        openai = llm.get("openai", {})
        ollama_emb = emb.get("ollama", {})

        return cls(
            backend=llm.get("backend", "ollama"),
            ollama_base_url=ollama.get("base_url", "http://localhost:11434"),
            ollama_model=ollama.get("model", "glm-5.2"),
            ollama_temperature=ollama.get("temperature", 0.3),
            ollama_max_tokens=ollama.get("max_tokens", 120000),
            ollama_timeout=ollama.get("timeout_seconds", 120),
            ollama_max_retries=ollama.get("max_retries", 3),
            openai_model=openai.get("model", "gpt-5.6-luna"),
            openai_temperature=openai.get("temperature", 0.3),
            openai_max_tokens=openai.get("max_tokens", 4096),
            openai_timeout=openai.get("timeout_seconds", 120),
            openai_max_retries=openai.get("max_retries", 3),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            embed_backend=emb.get("backend", "local"),
            embed_model=emb.get("model", "all-MiniLM-L6-v2"),
            embed_dimension=emb.get("dimension", 384),
            embed_cache_dir=emb.get("cache_dir", "data/cache/embeddings"),
            embed_ollama_base_url=ollama_emb.get("base_url", "http://localhost:11434"),
            embed_ollama_model=ollama_emb.get("model", "nomic-embed-text"),
            embed_ollama_dimension=ollama_emb.get("dimension", 768),
        )


@dataclass
class ChatMessage:
    """A single chat message."""
    role: str   # "system" | "user" | "assistant"
    content: str


@dataclass
class ChatResponse:
    """Response from a chat completion."""
    content: str
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None


# ===========================================================================
# Unified LLM Client
# ===========================================================================

class LLMClient:
    """Unified LLM client supporting chat and embeddings across backends.

    Args:
        config: LLMConfig instance. If None, loads from default config path.
    """

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig()

        # Lazy-loaded backend instances
        self._ollama_chat: Any = None
        self._openai_chat: Any = None
        self._local_embedder: Any = None
        self._ollama_embedder: Any = None
        self._openai_embedder: Any = None

        # Embedding cache
        self._cache_dir: Path | None = (
            Path(self.config.embed_cache_dir) if self.config.embed_cache_dir else None
        )
        if self._cache_dir:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, path: str = "config/agent.yml") -> LLMClient:
        """Create a client from a YAML config file."""
        return cls(LLMConfig.from_yaml(path))

    # ------------------------------------------------------------------
    # Chat completions
    # ------------------------------------------------------------------

    def chat(
        self,
        prompt: str,
        system: str = "",
        messages: list[ChatMessage] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """Send a chat completion request.

        Args:
            prompt: User message text.
            system: Optional system prompt.
            messages: Full message list (overrides prompt/system if provided).
            temperature: Override config temperature.
            max_tokens: Override config max_tokens.

        Returns:
            ChatResponse with the generated text.
        """
        # Build messages
        if messages is None:
            messages = []
            if system:
                messages.append(ChatMessage(role="system", content=system))
            messages.append(ChatMessage(role="user", content=prompt))

        if self.config.backend == "ollama":
            return self._chat_with_retry(self._chat_ollama, messages, temperature, max_tokens)
        elif self.config.backend == "openai":
            return self._chat_with_retry(self._chat_openai, messages, temperature, max_tokens)
        else:
            raise ValueError(f"Unknown LLM backend: {self.config.backend}")

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    def _chat_with_retry(
        self,
        backend_fn: Any,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> ChatResponse:
        """Wrap a chat backend call with retry and exponential backoff.

        Falls back to ``_chat_fallback`` after all retries are exhausted.

        Args:
            backend_fn: ``_chat_ollama`` or ``_chat_openai``.
            messages: Chat messages.
            temperature: Temperature override.
            max_tokens: Max tokens override.

        Returns:
            :class:`ChatResponse` from the backend or fallback.
        """
        if self.config.backend == "ollama":
            max_retries = self.config.ollama_max_retries
        else:
            max_retries = self.config.openai_max_retries

        base_delay = 1.0  # seconds

        for attempt in range(1, max_retries + 1):
            try:
                return backend_fn(messages, temperature, max_tokens)
            except Exception as e:
                if attempt < max_retries:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"Chat attempt {attempt}/{max_retries} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"Chat failed after {max_retries} attempts: {e}. "
                        f"Using fallback."
                    )

        return self._chat_fallback(messages)

    def _chat_ollama(
        self,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> ChatResponse:
        """Chat via Ollama API."""
        if self._ollama_chat is None:
            try:
                import ollama
                self._ollama_chat = ollama.Client(host=self.config.ollama_base_url)
                logger.info(f"Ollama chat client connected: {self.config.ollama_base_url}")
            except ImportError:
                return self._chat_fallback(messages)
            except Exception as e:
                logger.warning(f"Failed to connect to Ollama: {e}, using fallback")
                self._ollama_chat = None
                return self._chat_fallback(messages)

        msgs = [{"role": m.role, "content": m.content} for m in messages]
        response = self._ollama_chat.chat(
            model=self.config.ollama_model,
            messages=msgs,
            options={
                "temperature": temperature or self.config.ollama_temperature,
                "num_predict": max_tokens or self.config.ollama_max_tokens,
            },
        )
        return ChatResponse(
            content=response["message"]["content"],
            model=self.config.ollama_model,
            raw=response,
        )

    def _chat_openai(
        self,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
    ) -> ChatResponse:
        """Chat via OpenAI API."""
        if self._openai_chat is None:
            try:
                from openai import OpenAI
                self._openai_chat = OpenAI(api_key=self.config.openai_api_key)
                logger.info("OpenAI chat client initialized")
            except ImportError:
                return self._chat_fallback(messages)
            except Exception as e:
                logger.warning(f"Failed to initialize OpenAI client: {e}, using fallback")
                self._openai_chat = None
                return self._chat_fallback(messages)

        msgs = [{"role": m.role, "content": m.content} for m in messages]
        response = self._openai_chat.chat.completions.create(
            model=self.config.openai_model,
            messages=msgs,
            temperature=temperature or self.config.openai_temperature,
            max_tokens=max_tokens or self.config.openai_max_tokens,
        )
        return ChatResponse(
            content=response.choices[0].message.content,
            model=self.config.openai_model,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
            raw=response,
        )

    def _chat_fallback(self, messages: list[ChatMessage]) -> ChatResponse:
        """Fallback when no LLM SDK is installed."""
        logger.warning("No LLM SDK available, returning fallback response")
        user_msg = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return ChatResponse(
            content=f"[LLM not available] Received: {user_msg[:100]}",
            model="fallback",
        )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Embed a text into a vector.

        Uses the configured embedding backend (local | ollama | openai).
        Includes file-based caching.
        """
        if not text or not text.strip():
            dim = self._embed_dim()
            return [0.0] * dim

        # Check cache
        cached = self._cache_get(text)
        if cached is not None:
            return cached

        # Encode
        backend = self.config.embed_backend
        if backend == "local":
            vec = self._embed_local(text)
        elif backend == "ollama":
            vec = self._embed_ollama(text)
        elif backend == "openai":
            vec = self._embed_openai(text)
        else:
            raise ValueError(f"Unknown embedding backend: {backend}")

        self._cache_put(text, vec)
        return vec

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts."""
        return [self.encode(t) for t in texts]

    def _embed_dim(self) -> int:
        """Return the embedding dimension for the current backend."""
        if self.config.embed_backend == "ollama":
            return self.config.embed_ollama_dimension
        return self.config.embed_dimension

    def _embed_local(self, text: str) -> list[float]:
        """Embed using sentence-transformers (local)."""
        if self._local_embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._local_embedder = SentenceTransformer(self.config.embed_model)
                logger.info(f"Local embedding model loaded: {self.config.embed_model}")
            except ImportError:
                logger.warning("sentence-transformers not installed, using hash fallback")
                return self._hash_embedding(text, self._embed_dim())

        embedding = self._local_embedder.encode(text, normalize_embeddings=True)
        return embedding.tolist()

    def _embed_ollama(self, text: str) -> list[float]:
        """Embed using Ollama API."""
        if self._ollama_embedder is None:
            try:
                import ollama
                self._ollama_embedder = ollama.Client(host=self.config.embed_ollama_base_url)
                logger.info(f"Ollama embedder connected: {self.config.embed_ollama_base_url}")
            except ImportError:
                logger.warning("ollama package not installed, using hash fallback")
                return self._hash_embedding(text, self._embed_dim())

        response = self._ollama_embedder.embeddings(
            model=self.config.embed_ollama_model, prompt=text,
        )
        return response["embedding"]

    def _embed_openai(self, text: str) -> list[float]:
        """Embed using OpenAI API."""
        if self._openai_embedder is None:
            try:
                from openai import OpenAI
                self._openai_embedder = OpenAI(api_key=self.config.openai_api_key)
                logger.info("OpenAI embedder initialized")
            except ImportError:
                logger.warning("openai package not installed, using hash fallback")
                return self._hash_embedding(text, self._embed_dim())

        response = self._openai_embedder.embeddings.create(
            model=self.config.embed_model, input=text,
        )
        return response.data[0].embedding

    # ------------------------------------------------------------------
    # Hash-based fallback embedding
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_embedding(text: str, dimension: int) -> list[float]:
        """Deterministic pseudo-embedding from text hash.

        NOT for production semantic search — only for testing without
        embedding libraries installed.
        """
        h = hashlib.sha256(text.encode("utf-8")).digest()
        extended = (h * ((dimension // len(h)) + 1))[:dimension]
        raw = [b / 255.0 for b in extended]
        mean = sum(raw) / len(raw)
        centered = [v - mean for v in raw]
        norm = sum(v * v for v in centered) ** 0.5
        if norm == 0:
            return centered
        return [v / norm for v in centered]

    # ------------------------------------------------------------------
    # Embedding cache
    # ------------------------------------------------------------------

    def _cache_key(self, text: str) -> str:
        key_str = f"{self.config.embed_backend}:{self.config.embed_model}:{text}"
        return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    def _cache_get(self, text: str) -> list[float] | None:
        if self._cache_dir is None:
            return None
        path = self._cache_dir / f"{self._cache_key(text)}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                return None
        return None

    def _cache_put(self, text: str, vector: list[float]) -> None:
        if self._cache_dir is None:
            return
        path = self._cache_dir / f"{self._cache_key(text)}.json"
        try:
            path.write_text(json.dumps(vector))
        except Exception as e:
            logger.warning(f"Failed to cache embedding: {e}")