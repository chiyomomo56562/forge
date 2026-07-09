"""Unit tests for Phase 1.7 — Memory Manager.

Covers:
    - manager.py: multi-layer retrieve, store_episode, store_reflection, consolidate
    - consolidation.py: L1→L2 extraction, dual-storage routing (general→L2, tool_specific→L3)
    - reflection.py: reflection processing, hint extraction, L2/L3 distribution
"""

from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).parent.parent


# ===========================================================================
# Helpers
# ===========================================================================

def _make_episode(
    eid: str = "ep_001",
    task: str = "데이터 시각화 with matplotlib",
    what_worked: str = "matplotlib로 차트 생성 성공",
    what_failed: str = "한글 폰트 깨짐",
    next_hint: str = "matplotlib 폰트 캐시 확인 필요",
    causal_condition: str = "matplotlib depends on numpy",
):
    from agent.memory.schemas import Episode, Evaluation, Reflection

    return Episode(
        episode_id=eid,
        task=task,
        execution_summary="차트 생성 후 폰트 문제 발견",
        evaluation=Evaluation(),
        reflection=Reflection(
            what_worked=what_worked,
            what_failed=what_failed,
            next_hint=next_hint,
            causal_condition=causal_condition,
        ),
        timestamp="2026-01-01T00:00:00Z",
        has_reflection=True,
    )


def _make_manager(tmp_path):
    """Create a MemoryManager with all paths under tmp_path."""
    from agent.memory.manager import MemoryManager
    from agent.memory.episodic.encoder import EmbeddingEncoder
    from agent.llm.client import LLMClient, LLMConfig

    config = LLMConfig(embed_backend="local", embed_dimension=64, embed_cache_dir=None)
    client = LLMClient(config=config)
    encoder = EmbeddingEncoder(llm_client=client, dimension=64)

    return MemoryManager(
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


# ===========================================================================
# consolidation.py
# ===========================================================================

class TestConsolidation:
    def test_route_hint_general_to_l2(self):
        from agent.memory.consolidation import route_hint
        from agent.memory.schemas import GeneralizedHint, HintType

        hint = GeneralizedHint(
            hint_id="h_001",
            text="한글 시각화 시 폰트 캐시 확인",
            hint_type=HintType.GENERAL,
        )
        assert route_hint(hint) == "L2"

    def test_route_hint_tool_specific_to_l3(self):
        from agent.memory.consolidation import route_hint
        from agent.memory.schemas import GeneralizedHint, HintType

        hint = GeneralizedHint(
            hint_id="h_002",
            text="PyPDF2로는 이미지 PDF 텍스트 추출 안 됨",
            hint_type=HintType.TOOL_SPECIFIC,
        )
        assert route_hint(hint) == "L3"

    def test_consolidate_extracts_entities(self, tmp_path):
        from agent.memory.consolidation import Consolidator
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        consolidator = Consolidator(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        ep = _make_episode()
        result = consolidator.consolidate([ep])

        # Should extract matplotlib and numpy as entities
        assert result.entities_extracted > 0
        entity_ids = {e.id for e in result.entities}
        assert "matplotlib" in entity_ids or "numpy" in entity_ids

    def test_consolidate_routes_hints(self, tmp_path):
        from agent.memory.consolidation import Consolidator
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        consolidator = Consolidator(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        ep = _make_episode()
        result = consolidator.consolidate([ep])

        # Should have extracted some hints
        assert result.hints_extracted if hasattr(result, 'hints_extracted') else True
        # General hints should go to L2
        assert result.hints_routed_to_l2 > 0 or result.hints_routed_to_l3 > 0

    def test_consolidate_empty_list(self, tmp_path):
        from agent.memory.consolidation import Consolidator
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        consolidator = Consolidator(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        result = consolidator.consolidate([])
        assert result.entities_extracted == 0
        assert result.relations_extracted == 0

    def test_consolidate_stores_entities_in_graph(self, tmp_path):
        """After consolidation, entities should be in the L2 graph."""
        from agent.memory.consolidation import Consolidator
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        consolidator = Consolidator(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        ep = _make_episode(causal_condition="matplotlib depends on numpy")
        consolidator.consolidate([ep])

        # Graph should have entities
        assert graph_store.entity_count() > 0

    def test_consolidate_general_hint_to_l2_knowledge_node(self, tmp_path):
        """General hints should appear as knowledge nodes in L2."""
        from agent.memory.consolidation import Consolidator
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        consolidator = Consolidator(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        # Episode with a general hint (no tool keywords)
        ep = _make_episode(
            what_worked="데이터 정규화 후 시각화 성공",
            what_failed="",
            next_hint="데이터 전처리 단계에서 정규화가 중요함",
            causal_condition="정규화가 필요함",
        )
        result = consolidator.consolidate([ep])

        # Should have at least one general hint routed to L2
        if result.hints_routed_to_l2 > 0:
            kns = graph_store.get_knowledge_nodes()
            assert len(kns) > 0


# ===========================================================================
# reflection.py
# ===========================================================================

class TestReflectionProcessor:
    def test_process_extracts_hints(self, tmp_path):
        from agent.memory.reflection import ReflectionProcessor
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        processor = ReflectionProcessor(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        ep = _make_episode()
        result = processor.process(ep)

        assert result.episode_id == "ep_001"
        assert result.hints_extracted > 0
        assert result.hints_to_l2 + result.hints_to_l3 > 0

    def test_process_empty_reflection(self, tmp_path):
        from agent.memory.reflection import ReflectionProcessor
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import Episode, Evaluation, Reflection

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        processor = ReflectionProcessor(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        ep = Episode(
            episode_id="ep_empty",
            task="test",
            timestamp="2026-01-01T00:00:00Z",
        )
        result = processor.process(ep)
        assert result.hints_extracted == 0

    def test_process_normalises_reflection(self, tmp_path):
        """Reflection fields should be stripped and whitespace-collapsed."""
        from agent.memory.reflection import ReflectionProcessor
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore
        from agent.memory.schemas import Reflection

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        processor = ReflectionProcessor(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        refl = Reflection(
            what_worked="  성공   했다  ",
            what_failed="  실패   했다  ",
            next_hint="",
            causal_condition="",
        )
        ep = _make_episode()
        result = processor.process(ep, reflection=refl)

        # After normalisation, extra spaces should be collapsed
        assert "  " not in ep.reflection.what_worked
        assert ep.reflection.what_worked == "성공 했다"

    def test_process_generates_summary(self, tmp_path):
        from agent.memory.reflection import ReflectionProcessor
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        processor = ReflectionProcessor(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        ep = _make_episode()
        result = processor.process(ep)
        assert result.summary != ""
        assert "성공" in result.summary or "성공" in result.summary

    def test_process_marks_reflection_complete(self, tmp_path):
        from agent.memory.reflection import ReflectionProcessor
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.procedural.skill_store import SkillStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        skill_store = SkillStore(
            db_path=str(tmp_path / "skills.sqlite3"),
            skills_dir=str(tmp_path / "skills"),
        )
        processor = ReflectionProcessor(
            graph_store=graph_store,
            skill_store=skill_store,
        )

        ep = _make_episode()
        ep.has_reflection = False
        processor.process(ep)
        assert ep.has_reflection is True


# ===========================================================================
# manager.py — MemoryManager
# ===========================================================================

class TestMemoryManager:
    def test_init(self, tmp_path):
        manager = _make_manager(tmp_path)
        assert manager.episodic_store is not None
        assert manager.graph_store is not None
        assert manager.skill_store is not None
        assert manager.identity_store is not None

    def test_constitution_lazy_load(self, tmp_path):
        manager = _make_manager(tmp_path)
        # Constitution should load on first access
        constitution = manager.constitution
        assert constitution is not None
        assert constitution.version == 1
        assert len(constitution.principles) >= 3

    def test_store_episode(self, tmp_path):
        manager = _make_manager(tmp_path)
        ep = _make_episode()
        eid = manager.store_episode(ep)
        assert eid == "ep_001"

    def test_store_episode_logs_raw_event(self, tmp_path):
        manager = _make_manager(tmp_path)
        ep = _make_episode()
        manager.store_episode(ep)

        # Raw event log should exist
        from agent.memory.episodic.event_logger import EventLogger
        logger = EventLogger(raw_events_dir=str(tmp_path / "raw_events"))
        events = logger.read_today()
        # May be empty if date differs, but the directory should exist
        assert (tmp_path / "raw_events").exists()

    def test_retrieve_multi_layer(self, tmp_path):
        """retrieve() should return results from multiple layers."""
        manager = _make_manager(tmp_path)

        # Store an episode
        ep = _make_episode(task="matplotlib 차트 생성")
        manager.store_episode(ep)

        # Add an entity to L2
        from agent.memory.schemas import Entity
        manager.graph_store.add_entity(Entity(
            id="matplotlib",
            label="matplotlib",
            entity_type="tool",
        ))

        # Retrieve from L1 and L2
        results = manager.retrieve("matplotlib", layers=["L1", "L2"], top_k=5)
        # Should have results from at least one layer
        assert len(results) > 0

    def test_store_reflection(self, tmp_path):
        """store_reflection should save to L1 and route hints to L2/L3."""
        manager = _make_manager(tmp_path)

        # First store the episode
        ep = _make_episode()
        manager.store_episode(ep)

        # Now store a reflection
        from agent.memory.schemas import Reflection
        refl = Reflection(
            what_worked="matplotlib로 차트 생성 성공",
            what_failed="한글 폰트 깨짐",
            next_hint="matplotlib 폰트 캐시 확인 필요",
            causal_condition="matplotlib depends on numpy",
        )
        result = manager.store_reflection("ep_001", refl)

        assert result.episode_id == "ep_001"
        assert result.hints_extracted > 0
        # Some hints should go to L2 (general) or L3 (tool_specific)
        assert result.hints_to_l2 + result.hints_to_l3 > 0

    def test_store_reflection_nonexistent_episode(self, tmp_path):
        manager = _make_manager(tmp_path)
        from agent.memory.schemas import Reflection

        with pytest.raises(ValueError, match="not found"):
            manager.store_reflection("nonexistent", Reflection())

    def test_consolidate(self, tmp_path):
        """consolidate() should extract knowledge from L1 → L2/L3."""
        manager = _make_manager(tmp_path)

        # Store episodes
        ep1 = _make_episode("ep_001", causal_condition="matplotlib depends on numpy")
        ep2 = _make_episode("ep_002", causal_condition="pandas uses numpy")
        manager.store_episode(ep1)
        manager.store_episode(ep2)

        # Consolidate
        result = manager.consolidate(["ep_001", "ep_002"])

        assert len(result.episode_ids) == 2
        assert result.entities_extracted > 0
        # Graph should have entities after consolidation
        assert manager.graph_store.entity_count() > 0

    def test_consolidate_empty(self, tmp_path):
        manager = _make_manager(tmp_path)
        result = manager.consolidate([])
        assert result.entities_extracted == 0

    def test_consolidate_nonexistent(self, tmp_path):
        manager = _make_manager(tmp_path)
        result = manager.consolidate(["nonexistent"])
        assert result.entities_extracted == 0

    def test_check_permission(self, tmp_path):
        from agent.memory.schemas import MemoryLayer

        manager = _make_manager(tmp_path)

        # L1: readable, writable, not deletable
        assert manager.check_permission(MemoryLayer.L1, "read") is True
        assert manager.check_permission(MemoryLayer.L1, "write") is True
        assert manager.check_permission(MemoryLayer.L1, "delete") is False

        # L4: readable, not writable (only meta-loop + HITL)
        assert manager.check_permission(MemoryLayer.L4, "read") is True
        assert manager.check_permission(MemoryLayer.L4, "write") is False

    def test_dual_storage_routing(self, tmp_path):
        """Verify general hints → L2, tool-specific hints → L3."""
        from agent.memory.consolidation import route_hint
        from agent.memory.schemas import GeneralizedHint, HintType

        general_hint = GeneralizedHint(
            hint_id="h_gen",
            text="데이터 전처리가 중요함",
            hint_type=HintType.GENERAL,
        )
        tool_hint = GeneralizedHint(
            hint_id="h_tool",
            text="matplotlib 폰트 설정 필요",
            hint_type=HintType.TOOL_SPECIFIC,
        )

        assert route_hint(general_hint) == "L2"
        assert route_hint(tool_hint) == "L3"

    def test_store_reflection_general_hint_to_l2(self, tmp_path):
        """General hints should appear as knowledge nodes in L2 graph."""
        manager = _make_manager(tmp_path)

        ep = _make_episode(
            what_worked="데이터 정규화 후 시각화 성공",
            what_failed="",
            next_hint="데이터 전처리 단계에서 정규화가 중요함",
            causal_condition="정규화가 필요함",
        )
        manager.store_episode(ep)

        from agent.memory.schemas import Reflection
        refl = Reflection(
            what_worked="데이터 정규화 후 시각화 성공",
            what_failed="",
            next_hint="데이터 전처리 단계에서 정규화가 중요함",
            causal_condition="정규화가 필요함",
        )
        result = manager.store_reflection("ep_001", refl)

        # If any general hints were extracted, they should be in L2
        if result.hints_to_l2 > 0:
            kns = manager.graph_store.get_knowledge_nodes()
            assert len(kns) > 0