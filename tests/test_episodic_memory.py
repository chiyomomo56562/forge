"""Unit tests for Phase 1.2 — L1 Episodic Memory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ===========================================================================
# encoder.py
# ===========================================================================

class TestEmbeddingEncoder:
    def _make_encoder(self, dimension=64, cache_dir=None):
        """Create an encoder with a test LLMClient (hash fallback)."""
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder

        config = LLMConfig(
            embed_backend="local",
            embed_model="test",
            embed_dimension=dimension,
            embed_cache_dir=str(cache_dir) if cache_dir else None,
        )
        client = LLMClient(config=config)
        return EmbeddingEncoder(llm_client=client, dimension=dimension)

    def test_hash_fallback_returns_vector(self):
        from agent.memory.episodic.encoder import EmbeddingEncoder

        vec = EmbeddingEncoder._hash_embedding("hello world", 384)
        assert len(vec) == 384
        norm = sum(v * v for v in vec) ** 0.5
        assert 0.99 <= norm <= 1.01

    def test_hash_fallback_deterministic(self):
        from agent.memory.episodic.encoder import EmbeddingEncoder

        v1 = EmbeddingEncoder._hash_embedding("same text", 128)
        v2 = EmbeddingEncoder._hash_embedding("same text", 128)
        assert v1 == v2

    def test_hash_fallback_different_text(self):
        from agent.memory.episodic.encoder import EmbeddingEncoder

        v1 = EmbeddingEncoder._hash_embedding("text A", 128)
        v2 = EmbeddingEncoder._hash_embedding("text B", 128)
        assert v1 != v2

    def test_encode_empty_text(self):
        enc = self._make_encoder(dimension=64)
        vec = enc.encode("")
        assert vec == [0.0] * 64

    def test_encode_whitespace_text(self):
        enc = self._make_encoder(dimension=64)
        vec = enc.encode("   ")
        assert vec == [0.0] * 64

    def test_encode_uses_fallback_without_sentence_transformers(self):
        enc = self._make_encoder(dimension=128)
        vec = enc.encode("test text")
        assert len(vec) == 128

    def test_encode_batch(self):
        enc = self._make_encoder(dimension=64)
        vecs = enc.encode_batch(["hello", "world"])
        assert len(vecs) == 2
        assert all(len(v) == 64 for v in vecs)

    def test_cache_store_and_retrieve(self, tmp_path):
        enc = self._make_encoder(dimension=64, cache_dir=tmp_path / "cache")
        v1 = enc.encode("cached text")
        v2 = enc.encode("cached text")
        assert v1 == v2

    def test_unknown_backend_raises(self):
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder

        config = LLMConfig(embed_backend="invalid", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=config)
        enc = EmbeddingEncoder(llm_client=client, dimension=64)
        with pytest.raises(ValueError, match="Unknown embedding backend"):
            enc.encode("test")


# ===========================================================================
# store.py
# ===========================================================================

class TestEpisodicStore:
    def _make_episode(self, eid="ep_001", task="데이터 시각화", has_reflection=False):
        from agent.memory.schemas import Episode, EpisodeStatus, Evaluation, Reflection

        ep = Episode(
            episode_id=eid,
            task=task,
            execution_summary="Matplotlib 차트 생성",
            evaluation=Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.9),
            timestamp="2026-07-03T10:00:00Z",
            task_category="visualization",
        )
        if has_reflection:
            ep.reflection = Reflection(
                what_worked="Pandas 사용",
                what_failed="폰트 설정 누락",
                next_hint="폰트 캐시 확인",
                causal_condition="한글 포함 시 폰트 필요",
            )
            ep.has_reflection = True
        return ep

    def test_upsert_and_get(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"), encoder=enc)
        ep = self._make_episode()
        store.upsert(ep)

        result = store.get("ep_001")
        assert result is not None
        assert result["id"] == "ep_001"
        assert result["metadata"]["task"] == "데이터 시각화"

    def test_get_nonexistent(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore

        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"))
        assert store.get("nonexistent") is None

    def test_delete(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"), encoder=enc)
        store.upsert(self._make_episode())
        assert store.count() == 1

        store.delete("ep_001")
        assert store.count() == 0
        assert store.get("ep_001") is None

    def test_count(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"), encoder=enc)
        assert store.count() == 0

        store.upsert(self._make_episode("ep_001"))
        store.upsert(self._make_episode("ep_002", task="번역"))
        assert store.count() == 2

    def test_query_by_similarity(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"), encoder=enc)

        store.upsert(self._make_episode("ep_001", task="데이터 시각화 코드"))
        store.upsert(self._make_episode("ep_002", task="번역 작업"))

        query_vec = enc.encode("데이터 시각화")
        results = store.query(query_embedding=query_vec, top_k=2)
        assert len(results) == 2
        assert results[0]["id"] == "ep_001"

    def test_query_with_metadata_filter(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"), encoder=enc)

        store.upsert(self._make_episode("ep_001", task="시각화", has_reflection=True))
        store.upsert(self._make_episode("ep_002", task="시각화", has_reflection=False))

        query_vec = enc.encode("시각화")
        results = store.query(
            query_embedding=query_vec, top_k=10, where={"has_reflection": True},
        )
        assert len(results) == 1
        assert results[0]["id"] == "ep_001"

    def test_episode_to_text_includes_reflection(self):
        from agent.memory.episodic.store import EpisodicStore
        from agent.memory.schemas import Episode, Reflection

        ep = Episode(
            episode_id="ep_001", task="test task", execution_summary="test summary",
            timestamp="2026-07-03T10:00:00Z",
        )
        text_no_ref = EpisodicStore._episode_to_text(ep)
        assert "test task" in text_no_ref

        ep.reflection = Reflection(what_worked="worked", what_failed="failed")
        text_with_ref = EpisodicStore._episode_to_text(ep)
        assert "worked" in text_with_ref
        assert "failed" in text_with_ref


# ===========================================================================
# event_logger.py
# ===========================================================================

class TestEventLogger:
    def test_log_creates_file(self, tmp_path):
        from agent.memory.episodic.event_logger import EventLogger

        logger = EventLogger(raw_events_dir=str(tmp_path / "events"))
        logger.log({"episode_id": "ep_001", "timestamp": "2026-07-03T10:00:00Z", "task": "test"})

        files = list((tmp_path / "events").glob("*.jsonl"))
        assert len(files) == 1

    def test_log_appends_to_same_day(self, tmp_path):
        from agent.memory.episodic.event_logger import EventLogger

        logger = EventLogger(raw_events_dir=str(tmp_path / "events"))
        logger.log({"episode_id": "ep_001", "timestamp": "2026-07-03T10:00:00Z"})
        logger.log({"episode_id": "ep_002", "timestamp": "2026-07-03T11:00:00Z"})

        files = list((tmp_path / "events").glob("*.jsonl"))
        assert len(files) == 1

        from agent.utils.serialization import read_jsonl_all
        records = read_jsonl_all(files[0])
        assert len(records) == 2
        assert records[0]["episode_id"] == "ep_001"
        assert records[1]["episode_id"] == "ep_002"

    def test_read_day(self, tmp_path):
        from agent.memory.episodic.event_logger import EventLogger

        logger = EventLogger(raw_events_dir=str(tmp_path / "events"))
        logger.log({"episode_id": "ep_001", "timestamp": "2026-07-03T10:00:00Z"})

        from agent.utils.time import today_str
        records = logger.read_day(today_str())
        assert len(records) == 1
        assert records[0]["episode_id"] == "ep_001"

    def test_read_day_nonexistent(self, tmp_path):
        from agent.memory.episodic.event_logger import EventLogger

        logger = EventLogger(raw_events_dir=str(tmp_path / "events"))
        assert logger.read_day("2020-01-01") == []

    def test_list_days(self, tmp_path):
        from agent.memory.episodic.event_logger import EventLogger

        logger = EventLogger(raw_events_dir=str(tmp_path / "events"))
        logger.log({"episode_id": "ep_001", "timestamp": "2026-07-03T10:00:00Z"})

        days = logger.list_days()
        assert len(days) == 1
        from agent.utils.time import today_str
        assert days[0] == today_str()


# ===========================================================================
# retriever.py
# ===========================================================================

class TestEpisodicRetriever:
    def _setup_store(self, tmp_path, encoder):
        from agent.memory.episodic.store import EpisodicStore
        from agent.memory.schemas import Episode, EpisodeStatus, Evaluation, Reflection

        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"), encoder=encoder)

        ep1 = Episode(
            episode_id="ep_001", task="데이터 시각화 코드 작성",
            execution_summary="Matplotlib 차트 생성",
            evaluation=Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.9),
            timestamp="2026-07-03T10:00:00Z", task_category="visualization",
        )
        ep1.reflection = Reflection(
            what_worked="Pandas 사용", what_failed="폰트 설정",
            next_hint="폰트 캐시 확인", causal_condition="한글 시 폰트 필요",
        )
        ep1.has_reflection = True
        store.upsert(ep1)

        ep2 = Episode(
            episode_id="ep_002", task="데이터 시각화 스크립트",
            execution_summary="Seaborn 히트맵 생성",
            evaluation=Evaluation(status=EpisodeStatus.FAILURE, success_score=0.3),
            timestamp="2026-07-02T10:00:00Z", task_category="visualization",
        )
        store.upsert(ep2)

        ep3 = Episode(
            episode_id="ep_003", task="번역 작업",
            execution_summary="영한 번역",
            evaluation=Evaluation(status=EpisodeStatus.SUCCESS, success_score=0.95),
            timestamp="2026-07-01T10:00:00Z", task_category="translation",
        )
        store.upsert(ep3)

        return store

    def test_retrieve_basic(self, tmp_path):
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.memory.episodic.retriever import EpisodicRetriever

        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = self._setup_store(tmp_path, enc)
        retriever = EpisodicRetriever(store=store, encoder=enc, default_top_k=3)

        results = retriever.retrieve("데이터 시각화")
        assert len(results) <= 3
        assert results[0]["id"] in ("ep_001", "ep_002")

    def test_density_first_ordering(self, tmp_path):
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.memory.episodic.retriever import EpisodicRetriever

        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = self._setup_store(tmp_path, enc)
        retriever = EpisodicRetriever(
            store=store, encoder=enc, default_top_k=3, density_first=True,
        )

        results = retriever.retrieve("데이터 시각화")
        ids = [r["id"] for r in results]
        if "ep_001" in ids and "ep_002" in ids:
            assert ids.index("ep_001") < ids.index("ep_002")

    def test_retrieve_with_reflection_only(self, tmp_path):
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.memory.episodic.retriever import EpisodicRetriever

        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = self._setup_store(tmp_path, enc)
        retriever = EpisodicRetriever(store=store, encoder=enc, default_top_k=5)

        results = retriever.retrieve_with_reflection("데이터 시각화")
        assert all(r["metadata"].get("has_reflection", False) for r in results)
        assert len(results) == 1
        assert results[0]["id"] == "ep_001"

    def test_retrieve_expand(self, tmp_path):
        from agent.llm.client import LLMClient, LLMConfig
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.memory.episodic.retriever import EpisodicRetriever

        _cfg = LLMConfig(embed_backend="local", embed_model="test", embed_dimension=64, embed_cache_dir=None)
        enc = EmbeddingEncoder(llm_client=LLMClient(config=_cfg), dimension=64)
        store = self._setup_store(tmp_path, enc)
        retriever = EpisodicRetriever(
            store=store, encoder=enc, default_top_k=3, expand_step=5,
        )

        results = retriever.retrieve_expand("데이터 시각화", top_k=3)
        assert len(results) >= 2

    def test_retrieve_no_encoder_raises(self, tmp_path):
        from agent.memory.episodic.retriever import EpisodicRetriever
        from agent.memory.episodic.store import EpisodicStore

        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"))
        retriever = EpisodicRetriever(store=store, encoder=None)
        with pytest.raises(ValueError, match="No encoder"):
            retriever.retrieve("test")