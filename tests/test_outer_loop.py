"""Unit tests for Phase 3.1 — Outer Loop 7-step process.

Covers:
    - aggregator.py: Data aggregation from recent N episodes
    - metrics.py: CIB validation + coherence index (M17) + behavioral consistency
    - cache_refresher.py: Cache refresh across memory layers
    - self_model_recalculator.py: M14 self-model window stats recalculation
    - auditor.py: Independent audit (performer vs Phoenix deviation)
    - growth_regulator.py: M16 crash/stagnation/overgrowth detection
    - meta_trigger.py: Meta loop trigger (regular evolution / emergency / stagnation)
    - outer_loop.py: Full 7-step orchestration + adaptive N + state persistence

Tests use mock data and in-memory stores (no LLM or Chroma required).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _make_episode_meta(
    episode_id: str = "ep_001",
    status: str = "Success",
    success_score: float = 0.9,
    phoenix_score: float = 0.85,
    cib_score: float = 0.97,
    pain_index: float = 0.1,
    timestamp: str = "2025-01-01T00:00:00Z",
    task_category: str = "general",
    has_reflection: bool = True,
) -> dict:
    """Create episode metadata dict matching EpisodicStore format."""
    return {
        "episode_id": episode_id,
        "task": f"task for {episode_id}",
        "task_category": task_category,
        "has_reflection": has_reflection,
        "timestamp": timestamp,
        "status": status,
        "success_score": success_score,
        "pain_index": pain_index,
        "cib_score": cib_score,
        "phoenix_score": phoenix_score,
        "domain_score": 0.8,
        "reflection_score": 0.9,
    }


def _make_episode_list(n: int, base_timestamp: str = "2025-01-01T00:00:00Z") -> list[dict]:
    """Create a list of n episode dicts for testing."""
    episodes = []
    for i in range(n):
        episodes.append({
            "id": f"ep_{i:03d}",
            "document": f"episode {i}",
            "metadata": _make_episode_meta(
                episode_id=f"ep_{i:03d}",
                timestamp=f"2025-01-{i+1:02d}T00:00:00Z",
                success_score=0.8 + (i % 5) * 0.03,
                phoenix_score=0.85 + (i % 3) * 0.02,
                cib_score=0.96 + (i % 4) * 0.01,
            ),
        })
    return episodes


class MockEpisodicStore:
    """Minimal mock of EpisodicStore for testing."""

    def __init__(self, episodes: list[dict] | None = None):
        self._episodes = episodes or []

    def list_recent(self, n: int = 50) -> list[dict]:
        sorted_eps = sorted(
            self._episodes,
            key=lambda x: x["metadata"].get("timestamp", ""),
            reverse=True,
        )
        return sorted_eps[:n]

    def count(self) -> int:
        return len(self._episodes)


# ===========================================================================
# Step 1: aggregator.py
# ===========================================================================

class TestDataAggregator:
    def test_aggregate_empty_store(self):
        from agent.outer_loop.aggregator import DataAggregator, AggregationResult

        store = MockEpisodicStore(episodes=[])
        agg = DataAggregator(episodic_store=store, window_size=50)
        result = agg.aggregate()

        assert isinstance(result, AggregationResult)
        assert result.episode_count == 0

    def test_aggregate_with_episodes(self):
        from agent.outer_loop.aggregator import DataAggregator

        episodes = _make_episode_list(10)
        store = MockEpisodicStore(episodes=episodes)
        agg = DataAggregator(episodic_store=store, window_size=50)
        result = agg.aggregate()

        assert result.episode_count == 10
        assert 0.0 <= result.success_rate <= 1.0
        assert result.avg_phoenix_score is not None
        assert result.avg_cib_score is not None
        assert len(result.episode_ids) == 10
        assert len(result.phoenix_scores) == 10
        assert len(result.cib_scores) == 10

    def test_aggregate_window_size_limit(self):
        from agent.outer_loop.aggregator import DataAggregator

        episodes = _make_episode_list(30)
        store = MockEpisodicStore(episodes=episodes)
        agg = DataAggregator(episodic_store=store, window_size=10)
        result = agg.aggregate()

        assert result.episode_count == 10

    def test_aggregate_no_store(self):
        from agent.outer_loop.aggregator import DataAggregator

        agg = DataAggregator(episodic_store=None, window_size=50)
        result = agg.aggregate()

        assert result.episode_count == 0

    def test_aggregate_status_distribution(self):
        from agent.outer_loop.aggregator import DataAggregator

        episodes = [
            {"id": "ep_a", "document": "", "metadata": _make_episode_meta(episode_id="ep_a", status="Success")},
            {"id": "ep_b", "document": "", "metadata": _make_episode_meta(episode_id="ep_b", status="Success")},
            {"id": "ep_c", "document": "", "metadata": _make_episode_meta(episode_id="ep_c", status="Failure")},
            {"id": "ep_d", "document": "", "metadata": _make_episode_meta(episode_id="ep_d", status="Partial")},
        ]
        store = MockEpisodicStore(episodes=episodes)
        agg = DataAggregator(episodic_store=store, window_size=50)
        result = agg.aggregate()

        assert result.status_distribution.get("Success") == 2
        assert result.status_distribution.get("Failure") == 1
        assert result.status_distribution.get("Partial") == 1
        assert result.success_rate == 0.5  # 2/4


# ===========================================================================
# Step 2: metrics.py
# ===========================================================================

class TestMetricsRecorder:
    def test_record_with_aggregation(self):
        from agent.outer_loop.aggregator import AggregationResult
        from agent.outer_loop.metrics import MetricsRecorder

        agg = AggregationResult(
            episode_count=10,
            success_rate=0.8,
            avg_phoenix_score=0.85,
            avg_cib_score=0.97,
            cib_scores=[0.97, 0.98, 0.96],
        )
        recorder = MetricsRecorder()
        result = recorder.record(aggregation_result=agg)

        assert result.avg_cib_score == 0.97
        assert result.coherence_index is not None
        assert 0.0 <= result.coherence_index <= 1.0

    def test_coherence_index_formula(self):
        from agent.outer_loop.aggregator import AggregationResult
        from agent.outer_loop.metrics import MetricsRecorder

        # C = 0.5 * avg_cib + 0.5 * (1 - cal_error)
        # With no self-model: cal_error = None → cal_component = 1.0
        # C = 0.5 * 0.96 + 0.5 * 1.0 = 0.98
        agg = AggregationResult(
            episode_count=5,
            avg_cib_score=0.96,
            cib_scores=[0.96],
        )
        recorder = MetricsRecorder(cib_weight=0.5, calibration_weight=0.5)
        result = recorder.record(aggregation_result=agg)

        assert result.coherence_index == 0.98

    def test_behavioral_consistency(self):
        from agent.outer_loop.aggregator import AggregationResult
        from agent.outer_loop.metrics import MetricsRecorder

        agg = AggregationResult(
            episode_count=10,
            success_rate=0.8,
            status_distribution={"Success": 8, "Failure": 1, "Partial": 1},
        )
        recorder = MetricsRecorder()
        result = recorder.record(aggregation_result=agg)

        # BC = (Success + Failure) / total = 9/10
        assert result.behavioral_consistency == 0.9

    def test_record_no_data(self):
        from agent.outer_loop.metrics import MetricsRecorder

        recorder = MetricsRecorder()
        result = recorder.record()

        assert result.avg_cib_score is None
        assert result.coherence_index is None  # no data → no coherence


# ===========================================================================
# Step 3: cache_refresher.py
# ===========================================================================

class TestCacheRefresher:
    def test_refresh_no_memory_manager(self):
        from agent.outer_loop.cache_refresher import CacheRefresher

        refresher = CacheRefresher(memory_manager=None)
        result = refresher.refresh()

        assert result.l1_refreshed is False
        assert result.l2_refreshed is False
        assert len(result.errors) == 0

    def test_refresh_with_memory_manager(self, tmp_path):
        from agent.outer_loop.cache_refresher import CacheRefresher
        from agent.memory.manager import MemoryManager
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.llm.client import LLMClient, LLMConfig

        config = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=config)
        encoder = EmbeddingEncoder(llm_client=client, dimension=64)

        mm = MemoryManager(
            chroma_path=str(tmp_path / "chroma"),
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
            sqlite_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
            constitution_dir=str(PROJECT_ROOT / "constitution"),
            identity_db_path=str(tmp_path / "identity.sqlite3"),
            raw_events_dir=str(tmp_path / "raw_events"),
            encoder=encoder,
        )

        refresher = CacheRefresher(memory_manager=mm)
        result = refresher.refresh()

        assert result.l1_refreshed is True
        assert result.l4_refreshed is True


# ===========================================================================
# Step 4: self_model_recalculator.py
# ===========================================================================

class TestSelfModelRecalculator:
    def test_recalculate_no_self_model(self):
        from agent.outer_loop.self_model_recalculator import SelfModelRecalculator

        recalc = SelfModelRecalculator(self_model=None)
        result = recalc.recalculate()

        assert result.window_stats is None
        assert result.coherence_index is None

    def test_recalculate_with_self_model(self, tmp_path):
        from agent.memory.identity.identity_store import IdentityStore
        from agent.memory.identity.self_model import SelfModel
        from agent.outer_loop.self_model_recalculator import SelfModelRecalculator

        store = IdentityStore(db_path=str(tmp_path / "identity.sqlite3"))
        sm = SelfModel(store=store, window_size=50)

        # Record some calibration data
        sm.record(
            episode_id="ep_001",
            task_category="coding",
            predicted_success=0.8,
            actual_success=0.9,
        )
        sm.record(
            episode_id="ep_002",
            task_category="coding",
            predicted_success=0.7,
            actual_success=0.6,
        )

        recalc = SelfModelRecalculator(self_model=sm, window_size=50)
        result = recalc.recalculate(avg_cib_score=0.97)

        assert result.window_stats is not None
        assert result.calibration_error is not None
        assert result.coherence_index is not None
        assert 0.0 <= result.coherence_index <= 1.0


# ===========================================================================
# Step 5: auditor.py
# ===========================================================================

class TestIndependentAuditor:
    def test_audit_no_data(self):
        from agent.outer_loop.auditor import IndependentAuditor

        auditor = IndependentAuditor()
        result = auditor.audit()

        assert result.episodes_audited == 0
        assert result.deviation is None

    def test_audit_calibrated(self):
        from agent.outer_loop.auditor import IndependentAuditor

        pairs = [
            {"self_eval": 0.8, "phoenix_score": 0.82},
            {"self_eval": 0.7, "phoenix_score": 0.71},
            {"self_eval": 0.9, "phoenix_score": 0.88},
        ]
        auditor = IndependentAuditor(alert_threshold=0.2)
        result = auditor.audit(episode_pairs=pairs)

        assert result.episodes_audited == 3
        assert result.deviation is not None
        assert result.deviation < 0.2
        assert result.flagged is False
        assert result.bias_direction == "calibrated"

    def test_audit_flagged_overconfident(self):
        from agent.outer_loop.auditor import IndependentAuditor

        pairs = [
            {"self_eval": 0.9, "phoenix_score": 0.5},
            {"self_eval": 0.85, "phoenix_score": 0.55},
            {"self_eval": 0.8, "phoenix_score": 0.5},
        ]
        auditor = IndependentAuditor(alert_threshold=0.2)
        result = auditor.audit(episode_pairs=pairs)

        assert result.deviation is not None
        assert result.deviation >= 0.2
        assert result.flagged is True
        assert result.bias_direction == "overconfident"

    def test_audit_flagged_underconfident(self):
        from agent.outer_loop.auditor import IndependentAuditor

        pairs = [
            {"self_eval": 0.4, "phoenix_score": 0.9},
            {"self_eval": 0.45, "phoenix_score": 0.85},
            {"self_eval": 0.5, "phoenix_score": 0.9},
        ]
        auditor = IndependentAuditor(alert_threshold=0.2)
        result = auditor.audit(episode_pairs=pairs)

        assert result.flagged is True
        assert result.bias_direction == "underconfident"

    def test_audit_from_aggregation(self):
        from agent.outer_loop.aggregator import AggregationResult
        from agent.outer_loop.auditor import IndependentAuditor

        agg = AggregationResult(
            episode_count=5,
            avg_success_score=0.5,
            avg_phoenix_score=0.9,
        )
        auditor = IndependentAuditor(alert_threshold=0.2)
        result = auditor.audit(aggregation_result=agg)

        assert result.deviation is not None
        assert result.deviation == 0.4
        assert result.flagged is True


# ===========================================================================
# Step 6: growth_regulator.py
# ===========================================================================

class TestGrowthRateRegulator:
    def test_normal_signal(self):
        from agent.outer_loop.growth_regulator import GrowthRateRegulator, GrowthSignal
        from agent.outer_loop.aggregator import AggregationResult

        agg = AggregationResult(episode_count=50, success_rate=0.8)
        reg = GrowthRateRegulator()
        result = reg.regulate(aggregation_result=agg, coherence_index=0.85)

        assert result.signal == GrowthSignal.NORMAL
        assert result.cib_force_required is False

    def test_crash_signal(self):
        from agent.outer_loop.growth_regulator import GrowthRateRegulator, GrowthSignal
        from agent.outer_loop.aggregator import AggregationResult

        reg = GrowthRateRegulator(crash_window=20, crash_delta_threshold=0.15)

        # First run — establish baseline
        agg1 = AggregationResult(episode_count=40, success_rate=0.85)
        reg.regulate(aggregation_result=agg1, coherence_index=0.8)

        # Second run — success rate drops by 0.2
        agg2 = AggregationResult(episode_count=40, success_rate=0.65)
        result = reg.regulate(aggregation_result=agg2, coherence_index=0.75)

        assert result.signal == GrowthSignal.CRASH
        assert result.cib_force_required is True
        assert result.learning_suspended is True

    def test_overgrowth_signal(self):
        from agent.outer_loop.growth_regulator import GrowthRateRegulator, GrowthSignal

        reg = GrowthRateRegulator(overgrowth_coherence_rise=0.2)

        # Build history with rising coherence
        reg._coherence_history = [
            ("2025-01-01T00:00:00Z", 0.5),
            ("2025-01-02T00:00:00Z", 0.6),
            ("2025-01-03T00:00:00Z", 0.7),
        ]
        result = reg.regulate(coherence_index=0.75, timestamp="2025-01-04T00:00:00Z")

        assert result.signal == GrowthSignal.OVERGROWTH
        assert result.cib_force_required is True

    def test_stagnation_signal(self):
        from agent.outer_loop.growth_regulator import GrowthRateRegulator, GrowthSignal

        reg = GrowthRateRegulator(
            stagnation_window=50,
            stagnation_coherence_delta=0.01,
        )

        # Build history with barely changing coherence
        reg._coherence_history = [
            (f"2025-01-{i:02d}T00:00:00Z", 0.80 + (i % 3) * 0.001)
            for i in range(1, 52)
        ]
        result = reg.regulate(coherence_index=0.801, timestamp="2025-02-21T00:00:00Z")

        assert result.signal == GrowthSignal.STAGNATION
        assert result.meta_trigger_required is True


# ===========================================================================
# Step 7: meta_trigger.py
# ===========================================================================

class TestMetaTrigger:
    def test_no_trigger(self):
        from agent.outer_loop.meta_trigger import MetaTrigger, TriggerType

        trigger = MetaTrigger(episode_threshold=1000, outer_loop_threshold=100)
        result = trigger.evaluate(episode_count=50, outer_loop_count=5)

        assert result.trigger_type == TriggerType.NONE
        assert result.triggered is False

    def test_regular_evolution_trigger(self):
        from agent.outer_loop.meta_trigger import MetaTrigger, TriggerType

        trigger = MetaTrigger(episode_threshold=1000, outer_loop_threshold=100)
        result = trigger.evaluate(episode_count=1000, outer_loop_count=10)

        assert result.trigger_type == TriggerType.REGULAR_EVOLUTION
        assert result.triggered is True

    def test_emergency_inspection_trigger(self):
        from agent.outer_loop.meta_trigger import MetaTrigger, TriggerType

        trigger = MetaTrigger(episode_threshold=1000, outer_loop_threshold=100)
        result = trigger.evaluate(episode_count=500, outer_loop_count=100)

        assert result.trigger_type == TriggerType.EMERGENCY_INSPECTION
        assert result.triggered is True

    def test_stagnation_trigger(self):
        from agent.outer_loop.meta_trigger import MetaTrigger, TriggerType

        trigger = MetaTrigger()
        result = trigger.evaluate(
            episode_count=50,
            outer_loop_count=5,
            stagnation_detected=True,
        )

        assert result.trigger_type == TriggerType.STAGNATION_RESPONSE
        assert result.triggered is True

    def test_stagnation_takes_priority(self):
        from agent.outer_loop.meta_trigger import MetaTrigger, TriggerType

        trigger = MetaTrigger(episode_threshold=100, outer_loop_threshold=10)
        result = trigger.evaluate(
            episode_count=200,
            outer_loop_count=20,
            stagnation_detected=True,
        )

        # Stagnation should take priority over regular evolution
        assert result.trigger_type == TriggerType.STAGNATION_RESPONSE


# ===========================================================================
# Full orchestration: outer_loop.py
# ===========================================================================

class TestOuterLoop:
    def test_run_without_memory_manager(self, tmp_path):
        from agent.outer_loop import OuterLoop, OuterLoopResult

        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
            window_size=50,
        )
        result = outer.run()

        assert isinstance(result, OuterLoopResult)
        assert result.timestamp != ""
        assert result.state.outer_loop_count == 1
        assert result.adaptive_N > 0

    def test_run_with_memory_manager(self, tmp_path):
        from agent.outer_loop import OuterLoop, OuterLoopResult
        from agent.memory.manager import MemoryManager
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.memory.schemas import Episode, Evaluation, EpisodeStatus, Reflection
        from agent.llm.client import LLMClient, LLMConfig

        config = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=config)
        encoder = EmbeddingEncoder(llm_client=client, dimension=64)

        mm = MemoryManager(
            chroma_path=str(tmp_path / "chroma"),
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
            sqlite_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
            constitution_dir=str(PROJECT_ROOT / "constitution"),
            identity_db_path=str(tmp_path / "identity.sqlite3"),
            raw_events_dir=str(tmp_path / "raw_events"),
            encoder=encoder,
        )

        # Store some episodes
        for i in range(5):
            ep = Episode(
                episode_id=f"ep_test_{i:03d}",
                task=f"test task {i}",
                execution_summary=f"result {i}",
                evaluation=Evaluation(
                    status=EpisodeStatus.SUCCESS,
                    success_score=0.9,
                    phoenix_score=0.88,
                    cib_score=0.97,
                ),
                reflection=Reflection(what_worked="ok"),
                timestamp=f"2025-01-{i+1:02d}T00:00:00Z",
                has_reflection=True,
            )
            mm.store_episode(ep)

        outer = OuterLoop(
            memory_manager=mm,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
            window_size=50,
        )
        result = outer.run()

        assert isinstance(result, OuterLoopResult)
        assert result.aggregation.episode_count == 5
        assert result.state.outer_loop_count == 1

    def test_state_persistence(self, tmp_path):
        from agent.outer_loop import OuterLoop

        state_path = str(tmp_path / "state.json")

        outer1 = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        outer1.run()
        outer1.run()

        # Create new instance — should load persisted state
        outer2 = OuterLoop(
            memory_manager=None,
            state_path=state_path,
            audit_log_path=str(tmp_path / "audit.jsonl"),
        )
        assert outer2.state.outer_loop_count == 2

    def test_adaptive_N_computation(self, tmp_path):
        from agent.outer_loop import OuterLoop

        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(tmp_path / "audit.jsonl"),
            adaptive_N_config={
                "enabled": True,
                "min_multiplier": 0.5,
                "max_multiplier": 2.0,
                "high_volatility_threshold": 0.15,
                "low_volatility_threshold": 0.03,
            },
        )

        # With no data, volatility = 0 → low volatility → N = base_N * 2
        result = outer.run()
        assert result.adaptive_N == outer.state.base_N * 2  # low volatility → expand

    def test_audit_log_written(self, tmp_path):
        from agent.outer_loop import OuterLoop

        audit_path = tmp_path / "audit.jsonl"
        outer = OuterLoop(
            memory_manager=None,
            state_path=str(tmp_path / "state.json"),
            audit_log_path=str(audit_path),
        )
        outer.run()

        assert audit_path.exists()
        # Verify it's valid JSONL
        lines = audit_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert "timestamp" in entry
        assert "outer_loop_count" in entry
        assert "growth_signal" in entry


# ===========================================================================
# EpisodicStore.list_recent (modified in Phase 3.1)
# ===========================================================================

class TestEpisodicStoreListRecent:
    def test_list_recent_empty(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore

        store = EpisodicStore(chroma_path=str(tmp_path / "chroma"))
        result = store.list_recent(n=10)
        assert result == []

    def test_list_recent_with_episodes(self, tmp_path):
        from agent.memory.episodic.store import EpisodicStore
        from agent.memory.schemas import Episode, Evaluation, EpisodeStatus
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.llm.client import LLMClient, LLMConfig

        config = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=config)
        encoder = EmbeddingEncoder(llm_client=client, dimension=64)

        store = EpisodicStore(
            chroma_path=str(tmp_path / "chroma"),
            encoder=encoder,
        )

        for i in range(5):
            ep = Episode(
                episode_id=f"ep_lr_{i:03d}",
                task=f"task {i}",
                evaluation=Evaluation(
                    status=EpisodeStatus.SUCCESS,
                    success_score=0.9,
                    phoenix_score=0.85,
                    cib_score=0.97,
                ),
                timestamp=f"2025-01-{i+1:02d}T00:00:00Z",
            )
            store.upsert(ep)

        result = store.list_recent(n=3)
        assert len(result) == 3
        # Should be sorted by timestamp descending (newest first)
        assert result[0]["metadata"]["timestamp"] >= result[1]["metadata"]["timestamp"]

    def test_extended_metadata(self, tmp_path):
        """Verify that extended metadata (cib_score, phoenix_score, etc.) is stored."""
        from agent.memory.episodic.store import EpisodicStore
        from agent.memory.schemas import Episode, Evaluation, EpisodeStatus
        from agent.memory.episodic.encoder import EmbeddingEncoder
        from agent.llm.client import LLMClient, LLMConfig

        config = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
        client = LLMClient(config=config)
        encoder = EmbeddingEncoder(llm_client=client, dimension=64)

        store = EpisodicStore(
            chroma_path=str(tmp_path / "chroma"),
            encoder=encoder,
        )

        ep = Episode(
            episode_id="ep_meta_test",
            task="metadata test",
            evaluation=Evaluation(
                status=EpisodeStatus.SUCCESS,
                success_score=0.9,
                phoenix_score=0.88,
                cib_score=0.97,
                domain_score=0.85,
                reflection_score=0.9,
            ),
            timestamp="2025-01-01T00:00:00Z",
        )
        store.upsert(ep)

        result = store.list_recent(n=1)
        assert len(result) == 1
        meta = result[0]["metadata"]
        assert meta.get("cib_score") == 0.97
        assert meta.get("phoenix_score") == 0.88
        assert meta.get("domain_score") == 0.85
        assert meta.get("reflection_score") == 0.9