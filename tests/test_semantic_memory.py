"""Unit tests for Phase 1.4 — L2 Semantic Memory.

Covers:
    - graph_store.py: node/edge CRUD, save/load (.graphml), snapshots
    - json_store.py: entities/relations/concepts read/write
    - extractor.py: entity/relation extraction from episodes, hint classification
    - resolver.py: duplicate entity merge, synonym grouping
    - reasoner.py: path finding, neighborhood, dependency analysis, hint search
"""

from __future__ import annotations

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _make_entity(
    eid: str = "ent_001",
    label: str = "matplotlib",
    entity_type: str = "tool",
    confidence: float = 0.8,
    source_episodes: list[str] | None = None,
):
    from agent.memory.schemas import Entity

    return Entity(
        id=eid,
        label=label,
        entity_type=entity_type,
        confidence=confidence,
        source_episodes=source_episodes or ["ep_001"],
        created_at="2026-01-01T00:00:00Z",
    )


def _make_relation(
    source: str = "ent_001",
    target: str = "ent_002",
    relation: str = "depends_on",
    weight: float = 1.0,
):
    from agent.memory.schemas import Relation

    return Relation(
        source=source,
        target=target,
        relation=relation,
        weight=weight,
        source_episodes=["ep_001"],
        created_at="2026-01-01T00:00:00Z",
    )


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


# ===========================================================================
# graph_store.py — Node/Edge CRUD
# ===========================================================================

class TestGraphStoreCRUD:
    def test_add_and_get_entity(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        entity = _make_entity("ent_001", "matplotlib")
        store.add_entity(entity)

        retrieved = store.get_entity("ent_001")
        assert retrieved is not None
        assert retrieved.id == "ent_001"
        assert retrieved.label == "matplotlib"
        assert retrieved.entity_type == "tool"

    def test_get_entity_nonexistent(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        assert store.get_entity("nonexistent") is None

    def test_remove_entity(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("ent_001"))
        assert store.entity_count() == 1

        assert store.remove_entity("ent_001") is True
        assert store.entity_count() == 0
        assert store.get_entity("ent_001") is None

    def test_remove_entity_nonexistent(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        assert store.remove_entity("nonexistent") is False

    def test_list_entities(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("e1", "matplotlib"))
        store.add_entity(_make_entity("e2", "pandas"))

        entities = store.list_entities()
        assert len(entities) == 2

    def test_entity_count(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        assert store.entity_count() == 0
        store.add_entity(_make_entity("e1"))
        assert store.entity_count() == 1

    def test_add_and_get_relation(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("e1", "matplotlib"))
        store.add_entity(_make_entity("e2", "numpy"))
        store.add_relation(_make_relation("e1", "e2", "depends_on"))

        rel = store.get_relation("e1", "e2")
        assert rel is not None
        assert rel.relation == "depends_on"
        assert rel.source == "e1"
        assert rel.target == "e2"

    def test_get_relation_nonexistent(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        assert store.get_relation("a", "b") is None

    def test_remove_relation(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("e1"))
        store.add_entity(_make_entity("e2"))
        store.add_relation(_make_relation("e1", "e2"))

        assert store.remove_relation("e1", "e2") is True
        assert store.get_relation("e1", "e2") is None

    def test_remove_relation_nonexistent(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        assert store.remove_relation("a", "b") is False

    def test_list_relations(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("e1"))
        store.add_entity(_make_entity("e2"))
        store.add_entity(_make_entity("e3"))
        store.add_relation(_make_relation("e1", "e2", "depends_on"))
        store.add_relation(_make_relation("e2", "e3", "uses"))

        rels = store.list_relations()
        assert len(rels) == 2

    def test_relation_count(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        assert store.relation_count() == 0
        store.add_entity(_make_entity("e1"))
        store.add_entity(_make_entity("e2"))
        store.add_relation(_make_relation("e1", "e2"))
        assert store.relation_count() == 1

    def test_get_relations_of(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("e1"))
        store.add_entity(_make_entity("e2"))
        store.add_entity(_make_entity("e3"))
        store.add_relation(_make_relation("e1", "e2", "depends_on"))
        store.add_relation(_make_relation("e3", "e1", "uses"))

        outgoing = store.get_relations_of("e1", direction="out")
        assert len(outgoing) == 1
        assert outgoing[0].target == "e2"

        incoming = store.get_relations_of("e1", direction="in")
        assert len(incoming) == 1
        assert incoming[0].source == "e3"

        both = store.get_relations_of("e1", direction="both")
        assert len(both) == 2


# ===========================================================================
# graph_store.py — Save/Load
# ===========================================================================

class TestGraphStorePersistence:
    def test_save_and_load_graphml(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        graphml = str(tmp_path / "graph.graphml")
        gpickle = str(tmp_path / "graph.gpickle")

        store = GraphStore(graphml_path=graphml, gpickle_path=gpickle)
        store.add_entity(_make_entity("e1", "matplotlib"))
        store.add_entity(_make_entity("e2", "numpy"))
        store.add_relation(_make_relation("e1", "e2", "depends_on"))
        store.save(use_pickle=False)

        # Load into a new store
        store2 = GraphStore(graphml_path=graphml, gpickle_path=gpickle)
        assert store2.entity_count() == 2
        assert store2.relation_count() == 1

        entity = store2.get_entity("e1")
        assert entity is not None
        assert entity.label == "matplotlib"

    def test_save_and_load_pickle(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        graphml = str(tmp_path / "graph.graphml")
        gpickle = str(tmp_path / "graph.gpickle")

        store = GraphStore(graphml_path=graphml, gpickle_path=gpickle)
        store.add_entity(_make_entity("e1", "pandas"))
        store.save(use_pickle=True)

        assert store.graphml_path.exists()
        assert store.gpickle_path.exists()

        store2 = GraphStore(graphml_path=graphml, gpickle_path=gpickle)
        entity = store2.get_entity("e1")
        assert entity is not None
        assert entity.label == "pandas"

    def test_save_snapshot(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("e1", "matplotlib"))
        snap_path = store.save_snapshot(snapshots_dir=str(tmp_path / "snapshots"))
        assert snap_path != ""
        # Snapshot file should exist
        from pathlib import Path
        assert Path(snap_path).exists()


# ===========================================================================
# graph_store.py — Knowledge nodes (generalized hints)
# ===========================================================================

class TestGraphStoreKnowledgeNodes:
    def test_add_and_get_knowledge_nodes(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore
        from agent.memory.schemas import KnowledgeNode

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("e1", "matplotlib"))

        kn = KnowledgeNode(
            node_id="kn_001",
            hint_text="한글 포함 시각화 시 폰트 캐시 확인",
            source_episodes=["ep_001"],
            connected_entities=["e1"],
        )
        store.add_knowledge_node(kn)

        nodes = store.get_knowledge_nodes()
        assert len(nodes) == 1
        assert nodes[0].hint_text == "한글 포함 시각화 시 폰트 캐시 확인"
        assert "e1" in nodes[0].connected_entities


# ===========================================================================
# json_store.py
# ===========================================================================

class TestJsonStore:
    def test_save_and_load_entities(self, tmp_path):
        from agent.memory.semantic.json_store import JsonStore

        store = JsonStore(
            entities_path=str(tmp_path / "entities.json"),
            relations_path=str(tmp_path / "relations.json"),
            concepts_path=str(tmp_path / "concepts.json"),
        )
        entities = [_make_entity("e1", "matplotlib"), _make_entity("e2", "pandas")]
        store.save_entities(entities)

        loaded = store.load_entities()
        assert len(loaded) == 2
        assert loaded[0].label in ("matplotlib", "pandas")

    def test_add_entity_upsert(self, tmp_path):
        from agent.memory.semantic.json_store import JsonStore

        store = JsonStore(
            entities_path=str(tmp_path / "entities.json"),
            relations_path=str(tmp_path / "relations.json"),
            concepts_path=str(tmp_path / "concepts.json"),
        )
        store.add_entity(_make_entity("e1", "matplotlib"))
        store.add_entity(_make_entity("e1", "matplotlib_updated"))

        loaded = store.load_entities()
        assert len(loaded) == 1
        assert loaded[0].label == "matplotlib_updated"

    def test_save_and_load_relations(self, tmp_path):
        from agent.memory.semantic.json_store import JsonStore

        store = JsonStore(
            entities_path=str(tmp_path / "entities.json"),
            relations_path=str(tmp_path / "relations.json"),
            concepts_path=str(tmp_path / "concepts.json"),
        )
        relations = [_make_relation("e1", "e2", "depends_on")]
        store.save_relations(relations)

        loaded = store.load_relations()
        assert len(loaded) == 1
        assert loaded[0].relation == "depends_on"

    def test_add_relation_upsert(self, tmp_path):
        from agent.memory.semantic.json_store import JsonStore

        store = JsonStore(
            entities_path=str(tmp_path / "entities.json"),
            relations_path=str(tmp_path / "relations.json"),
            concepts_path=str(tmp_path / "concepts.json"),
        )
        store.add_relation(_make_relation("e1", "e2", "depends_on"))
        store.add_relation(_make_relation("e1", "e2", "uses"))

        loaded = store.load_relations()
        assert len(loaded) == 1
        assert loaded[0].relation == "uses"

    def test_save_and_load_concepts(self, tmp_path):
        from agent.memory.semantic.json_store import JsonStore

        store = JsonStore(
            entities_path=str(tmp_path / "entities.json"),
            relations_path=str(tmp_path / "relations.json"),
            concepts_path=str(tmp_path / "concepts.json"),
        )
        concepts = [{"id": "c1", "name": "data_viz", "description": "Data visualization"}]
        store.save_concepts(concepts)

        loaded = store.load_concepts()
        assert len(loaded) == 1
        assert loaded[0]["name"] == "data_viz"

    def test_load_empty(self, tmp_path):
        from agent.memory.semantic.json_store import JsonStore

        store = JsonStore(
            entities_path=str(tmp_path / "entities.json"),
            relations_path=str(tmp_path / "relations.json"),
            concepts_path=str(tmp_path / "concepts.json"),
        )
        assert store.load_entities() == []
        assert store.load_relations() == []
        assert store.load_concepts() == []

    def test_sync_from_graph(self, tmp_path):
        from agent.memory.semantic.json_store import JsonStore
        from agent.memory.semantic.graph_store import GraphStore

        graph_store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        graph_store.add_entity(_make_entity("e1", "matplotlib"))
        graph_store.add_entity(_make_entity("e2", "numpy"))
        graph_store.add_relation(_make_relation("e1", "e2", "depends_on"))

        json_store = JsonStore(
            entities_path=str(tmp_path / "entities.json"),
            relations_path=str(tmp_path / "relations.json"),
            concepts_path=str(tmp_path / "concepts.json"),
        )
        json_store.sync_from_graph(graph_store)

        assert len(json_store.load_entities()) == 2
        assert len(json_store.load_relations()) == 1


# ===========================================================================
# extractor.py
# ===========================================================================

class TestEntityExtractor:
    def test_extract_with_rules_finds_tools(self):
        from agent.memory.semantic.extractor import EntityExtractor

        extractor = EntityExtractor(llm_client=None)
        episode = _make_episode(
            task="데이터 시각화 with matplotlib",
            causal_condition="matplotlib depends on numpy",
        )
        entities, relations = extractor.extract(episode)

        entity_ids = {e.id for e in entities}
        assert "matplotlib" in entity_ids
        assert "numpy" in entity_ids

    def test_extract_finds_relations(self):
        from agent.memory.semantic.extractor import EntityExtractor

        extractor = EntityExtractor(llm_client=None)
        episode = _make_episode(
            causal_condition="matplotlib depends on numpy",
        )
        entities, relations = extractor.extract(episode)

        # Should find depends_on relation between matplotlib and numpy
        dep_rels = [r for r in relations if r.relation == "depends_on"]
        assert len(dep_rels) >= 1
        assert dep_rels[0].source == "matplotlib"
        assert dep_rels[0].target == "numpy"

    def test_extract_batch_deduplicates(self):
        from agent.memory.semantic.extractor import EntityExtractor

        extractor = EntityExtractor(llm_client=None)
        ep1 = _make_episode(eid="ep_001", causal_condition="matplotlib depends on numpy")
        ep2 = _make_episode(eid="ep_002", causal_condition="matplotlib uses pandas")

        entities, relations = extractor.extract_batch([ep1, ep2])

        # matplotlib should appear only once
        matplotlib_ents = [e for e in entities if e.id == "matplotlib"]
        assert len(matplotlib_ents) == 1
        # Should have source_episodes from both episodes
        assert "ep_001" in matplotlib_ents[0].source_episodes
        assert "ep_002" in matplotlib_ents[0].source_episodes

    def test_extract_generalized_hints(self):
        from agent.memory.semantic.extractor import EntityExtractor
        from agent.memory.schemas import HintType

        extractor = EntityExtractor(llm_client=None)
        episode = _make_episode(
            what_worked="matplotlib로 차트 생성 성공",
            what_failed="한글 폰트 깨짐",
            next_hint="matplotlib 폰트 캐시 확인 필요",
            causal_condition="폰트 설정이 필요함",
        )
        hints = extractor.extract_generalized_hints(episode)

        assert len(hints) == 4  # all 4 reflection fields populated

        # Hints mentioning matplotlib should be tool_specific
        tool_hints = [h for h in hints if h.hint_type == HintType.TOOL_SPECIFIC]
        assert len(tool_hints) >= 1

        # "폰트 설정이 필요함" should be general
        general_hints = [h for h in hints if h.hint_type == HintType.GENERAL]
        assert len(general_hints) >= 1

    def test_extract_hints_empty_reflection(self):
        from agent.memory.semantic.extractor import EntityExtractor

        extractor = EntityExtractor(llm_client=None)
        from agent.memory.schemas import Episode, Evaluation, Reflection

        episode = Episode(
            episode_id="ep_empty",
            task="test task",
            timestamp="2026-01-01T00:00:00Z",
        )
        hints = extractor.extract_generalized_hints(episode)
        assert len(hints) == 0


# ===========================================================================
# resolver.py
# ===========================================================================

class TestEntityResolver:
    def test_resolve_exact_duplicates(self):
        from agent.memory.semantic.resolver import EntityResolver

        resolver = EntityResolver()
        entities = [
            _make_entity("e1", "matplotlib", source_episodes=["ep_001"]),
            _make_entity("e1", "matplotlib", source_episodes=["ep_002"]),
        ]
        resolved = resolver.resolve(entities)
        assert len(resolved) == 1
        assert set(resolved[0].source_episodes) == {"ep_001", "ep_002"}

    def test_resolve_synonyms(self):
        from agent.memory.semantic.resolver import EntityResolver

        resolver = EntityResolver()
        entities = [
            _make_entity("matplotlib", "matplotlib"),
            _make_entity("plt", "plt"),
        ]
        resolved = resolver.resolve(entities)
        # matplotlib and plt are synonyms → merged
        assert len(resolved) == 1

    def test_resolve_label_similarity(self):
        from agent.memory.semantic.resolver import EntityResolver

        resolver = EntityResolver(similarity_threshold=0.8)
        entities = [
            _make_entity("ent_a", "data_visualization", entity_type="concept"),
            _make_entity("ent_b", "data_visualization", entity_type="concept"),
        ]
        resolved = resolver.resolve(entities)
        assert len(resolved) == 1

    def test_resolve_no_merge_different_types(self):
        from agent.memory.semantic.resolver import EntityResolver

        resolver = EntityResolver(similarity_threshold=0.5)
        entities = [
            _make_entity("ent_a", "matplotlib", entity_type="tool"),
            _make_entity("ent_b", "matplotlib", entity_type="person"),
        ]
        resolved = resolver.resolve(entities)
        assert len(resolved) == 2

    def test_resolve_with_relations_remaps(self):
        from agent.memory.semantic.resolver import EntityResolver

        resolver = EntityResolver()
        entities = [
            _make_entity("matplotlib", "matplotlib"),
            _make_entity("plt", "plt"),
            _make_entity("numpy", "numpy"),
        ]
        relations = [
            _make_relation("matplotlib", "numpy", "depends_on"),
            _make_relation("plt", "numpy", "uses"),
        ]
        resolved_entities, resolved_relations = resolver.resolve_with_relations(entities, relations)

        # matplotlib and plt merged → 2 entities
        assert len(resolved_entities) == 2

        # Relations should be remapped (plt → matplotlib)
        sources = {r.source for r in resolved_relations}
        assert "plt" not in sources
        assert "matplotlib" in sources

    def test_resolve_with_relations_no_self_loops(self):
        from agent.memory.semantic.resolver import EntityResolver

        resolver = EntityResolver()
        entities = [
            _make_entity("matplotlib", "matplotlib"),
            _make_entity("plt", "plt"),
        ]
        relations = [
            _make_relation("matplotlib", "plt", "uses"),
        ]
        resolved_entities, resolved_relations = resolver.resolve_with_relations(entities, relations)

        # After merging matplotlib and plt, the self-loop should be removed
        assert len(resolved_relations) == 0

    def test_add_synonym_group(self):
        from agent.memory.semantic.resolver import EntityResolver

        resolver = EntityResolver()
        resolver.add_synonym_group({"custom_tool", "ct"})

        entities = [
            _make_entity("custom_tool", "Custom Tool"),
            _make_entity("ct", "CT"),
        ]
        resolved = resolver.resolve(entities)
        assert len(resolved) == 1


# ===========================================================================
# reasoner.py
# ===========================================================================

class TestGraphReasoner:
    def _setup_graph(self, tmp_path):
        from agent.memory.semantic.graph_store import GraphStore

        store = GraphStore(
            graphml_path=str(tmp_path / "graph.graphml"),
            gpickle_path=str(tmp_path / "graph.gpickle"),
        )
        store.add_entity(_make_entity("matplotlib", "matplotlib"))
        store.add_entity(_make_entity("numpy", "numpy"))
        store.add_entity(_make_entity("pandas", "pandas"))
        store.add_relation(_make_relation("matplotlib", "numpy", "depends_on"))
        store.add_relation(_make_relation("pandas", "numpy", "depends_on"))
        return store

    def test_find_paths(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        reasoner = GraphReasoner(graph_store=store)

        paths = reasoner.find_paths("matplotlib", "numpy")
        assert len(paths) >= 1
        assert paths[0] == ["matplotlib", "numpy"]

    def test_find_paths_no_connection(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        store.add_entity(_make_entity("isolated", "isolated"))
        reasoner = GraphReasoner(graph_store=store)

        paths = reasoner.find_paths("matplotlib", "isolated")
        assert paths == []

    def test_shortest_path(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        reasoner = GraphReasoner(graph_store=store)

        path = reasoner.shortest_path("matplotlib", "numpy")
        assert path is not None
        assert path[0] == "matplotlib"
        assert path[-1] == "numpy"

    def test_shortest_path_no_path(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        store.add_entity(_make_entity("isolated", "isolated"))
        reasoner = GraphReasoner(graph_store=store)

        assert reasoner.shortest_path("matplotlib", "isolated") is None

    def test_get_neighborhood(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        reasoner = GraphReasoner(graph_store=store)

        neighbors = reasoner.get_neighborhood("numpy", max_depth=1)
        neighbor_ids = {e.id for e in neighbors}
        assert "matplotlib" in neighbor_ids
        assert "pandas" in neighbor_ids

    def test_get_related_by_type(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        reasoner = GraphReasoner(graph_store=store)

        deps = reasoner.get_related("matplotlib", relation_type="depends_on")
        assert len(deps) == 1
        assert deps[0].id == "numpy"

    def test_get_dependencies(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        reasoner = GraphReasoner(graph_store=store)

        deps = reasoner.get_dependencies("matplotlib")
        assert len(deps) == 1
        assert deps[0].id == "numpy"

    def test_get_dependents(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        reasoner = GraphReasoner(graph_store=store)

        dependents = reasoner.get_dependents("numpy")
        dependent_ids = {e.id for e in dependents}
        assert "matplotlib" in dependent_ids
        assert "pandas" in dependent_ids

    def test_search_hints(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner
        from agent.memory.schemas import KnowledgeNode

        store = self._setup_graph(tmp_path)
        store.add_knowledge_node(KnowledgeNode(
            node_id="kn_001",
            hint_text="한글 폰트 설정 필요",
            connected_entities=["matplotlib"],
        ))
        store.add_knowledge_node(KnowledgeNode(
            node_id="kn_002",
            hint_text="데이터 정규화 권장",
            connected_entities=["pandas"],
        ))
        reasoner = GraphReasoner(graph_store=store)

        results = reasoner.search_hints("폰트")
        assert len(results) == 1
        assert results[0].node_id == "kn_001"

    def test_get_hints_for_entity(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner
        from agent.memory.schemas import KnowledgeNode

        store = self._setup_graph(tmp_path)
        store.add_knowledge_node(KnowledgeNode(
            node_id="kn_001",
            hint_text="한글 폰트 설정 필요",
            connected_entities=["matplotlib"],
        ))
        reasoner = GraphReasoner(graph_store=store)

        hints = reasoner.get_hints_for_entity("matplotlib")
        assert len(hints) == 1
        assert hints[0].hint_text == "한글 폰트 설정 필요"

    def test_graph_stats(self, tmp_path):
        from agent.memory.semantic.reasoner import GraphReasoner

        store = self._setup_graph(tmp_path)
        reasoner = GraphReasoner(graph_store=store)

        stats = reasoner.graph_stats()
        assert stats["nodes"] == 3
        assert stats["edges"] == 2
        assert stats["density"] >= 0.0
        assert stats["avg_degree"] > 0.0