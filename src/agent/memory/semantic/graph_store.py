"""NetworkX graph store for L2 Semantic Memory.

Provides CRUD operations for entities (nodes) and relations (edges) in a
directed knowledge graph, with persistence to GraphML and pickle formats.

Paths (from ``config/memory.yml``)::

    data/memory/semantic/graph/knowledge_graph.graphml
    data/memory/semantic/graph/knowledge_graph.gpickle
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import networkx as nx

from ..schemas import Entity, Relation, KnowledgeNode, HintType
from ...utils.logging import get_logger
from ...utils.serialization import write_pickle
from ...utils.time import iso_now

logger = get_logger("agent.memory.semantic.graph_store")


class GraphStore:
    """NetworkX-backed knowledge graph for L2 semantic memory.

    Args:
        graphml_path: Path for GraphML persistence.
        gpickle_path: Path for pickle persistence.
    """

    def __init__(
        self,
        graphml_path: str = "data/memory/semantic/graph/knowledge_graph.graphml",
        gpickle_path: str = "data/memory/semantic/graph/knowledge_graph.gpickle",
    ):
        self.graphml_path = Path(graphml_path)
        self.gpickle_path = Path(gpickle_path)
        self.graph: nx.DiGraph = nx.DiGraph()
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load the graph from disk (pickle first, then GraphML)."""
        if self.gpickle_path.exists():
            import pickle
            with self.gpickle_path.open("rb") as f:
                self.graph = pickle.load(f)
            logger.info(f"Loaded graph from {self.gpickle_path} ({self.graph.number_of_nodes()} nodes)")
        elif self.graphml_path.exists():
            self.graph = nx.read_graphml(self.graphml_path)
            # Deserialize JSON strings back to list/dict (from GraphML sanitization)
            self._deserialize_graphml_attrs()
            logger.info(f"Loaded graph from {self.graphml_path} ({self.graph.number_of_nodes()} nodes)")
        else:
            logger.info("No existing graph found, starting fresh")

    @staticmethod
    def _sanitize_for_graphml(graph: nx.DiGraph) -> nx.DiGraph:
        """Return a copy of *graph* with list/dict attrs converted to JSON strings.

        GraphML only supports scalar attribute values (str, int, float, bool).
        This method serialises list/dict values to JSON strings so the graph
        can be written to GraphML format.
        """
        import copy
        g = copy.deepcopy(graph)
        for node_id, data in g.nodes(data=True):
            for key, val in data.items():
                if isinstance(val, (list, dict)):
                    data[key] = json.dumps(val, ensure_ascii=False)
        for _, _, data in g.edges(data=True):
            for key, val in data.items():
                if isinstance(val, (list, dict)):
                    data[key] = json.dumps(val, ensure_ascii=False)
        return g

    def _deserialize_graphml_attrs(self) -> None:
        """Convert JSON-string attributes back to list/dict after GraphML load.

        Heuristic: try to JSON-parse any string value that starts with ``[``
        or ``{``.  This reverses the :meth:`_sanitize_for_graphml` conversion.
        """
        for node_id, data in self.graph.nodes(data=True):
            for key, val in data.items():
                if isinstance(val, str) and val and val[0] in "[{":
                    try:
                        data[key] = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        pass
        for _, _, data in self.graph.edges(data=True):
            for key, val in data.items():
                if isinstance(val, str) and val and val[0] in "[{":
                    try:
                        data[key] = json.loads(val)
                    except (json.JSONDecodeError, ValueError):
                        pass

    def save(self, *, use_pickle: bool = True) -> None:
        """Save the graph to disk.

        Args:
            use_pickle: If ``True``, saves as pickle (faster, preserves types).
                Also always saves GraphML for human readability.
        """
        self.graphml_path.parent.mkdir(parents=True, exist_ok=True)

        # Always save GraphML for human inspection
        # GraphML doesn't support list/dict attributes — convert to JSON strings
        graphml_graph = self._sanitize_for_graphml(self.graph)
        nx.write_graphml(graphml_graph, self.graphml_path)
        logger.debug(f"Saved GraphML to {self.graphml_path}")

        if use_pickle:
            write_pickle(self.gpickle_path, self.graph)
            logger.debug(f"Saved pickle to {self.gpickle_path}")

    def save_snapshot(self, snapshots_dir: str = "data/memory/semantic/snapshots") -> str:
        """Save a dated snapshot of the current graph.

        Args:
            snapshots_dir: Base directory for snapshots.

        Returns:
            Path to the saved snapshot file.
        """
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        snap_dir = Path(snapshots_dir) / date_str
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_path = snap_dir / f"graph_{iso_now().replace(':', '-')}.graphml"
        graphml_graph = self._sanitize_for_graphml(self.graph)
        nx.write_graphml(graphml_graph, snap_path)
        logger.info(f"Saved snapshot to {snap_path}")
        return str(snap_path)

    # ------------------------------------------------------------------
    # Entity (node) CRUD
    # ------------------------------------------------------------------

    def add_entity(self, entity: Entity) -> None:
        """Add or update an entity as a graph node.

        Args:
            entity: The :class:`Entity` to add.
        """
        attrs = entity.model_dump()
        # Use 'id' as the node identifier
        self.graph.add_node(entity.id, **attrs)
        logger.debug(f"Added entity '{entity.id}' ({entity.entity_type})")

    def get_entity(self, entity_id: str) -> Entity | None:
        """Retrieve an entity by ID.

        Returns:
            :class:`Entity` or ``None`` if not found.
        """
        if entity_id not in self.graph:
            return None
        data = dict(self.graph.nodes[entity_id])
        return self._node_to_entity(entity_id, data)

    def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity and all its edges.

        Returns:
            ``True`` if the entity existed and was removed.
        """
        if entity_id not in self.graph:
            return False
        self.graph.remove_node(entity_id)
        logger.debug(f"Removed entity '{entity_id}'")
        return True

    def list_entities(self) -> list[Entity]:
        """Return all entities in the graph."""
        return [
            self._node_to_entity(node_id, dict(data))
            for node_id, data in self.graph.nodes(data=True)
        ]

    def entity_count(self) -> int:
        """Return the number of entities (nodes)."""
        return self.graph.number_of_nodes()

    # ------------------------------------------------------------------
    # Relation (edge) CRUD
    # ------------------------------------------------------------------

    def add_relation(self, relation: Relation) -> None:
        """Add or update a relation as a graph edge.

        Ensures both source and target nodes exist (creates placeholder
        nodes if they don't).

        Args:
            relation: The :class:`Relation` to add.
        """
        # Ensure nodes exist
        for node_id in (relation.source, relation.target):
            if node_id not in self.graph:
                self.graph.add_node(node_id, id=node_id, label=node_id, entity_type="unknown")

        attrs = relation.model_dump()
        # Use (source, target) as the edge key; store relation type in attrs
        self.graph.add_edge(relation.source, relation.target, **attrs)
        logger.debug(f"Added relation '{relation.source}' --{relation.relation}--> '{relation.target}'")

    def get_relation(self, source: str, target: str) -> Relation | None:
        """Retrieve a relation (edge) by source and target.

        Returns:
            :class:`Relation` or ``None`` if the edge does not exist.
        """
        if not self.graph.has_edge(source, target):
            return None
        data = dict(self.graph.edges[source, target])
        return self._edge_to_relation(data)

    def remove_relation(self, source: str, target: str) -> bool:
        """Remove a relation (edge).

        Returns:
            ``True`` if the edge existed and was removed.
        """
        if not self.graph.has_edge(source, target):
            return False
        self.graph.remove_edge(source, target)
        logger.debug(f"Removed relation '{source}' → '{target}'")
        return True

    def list_relations(self) -> list[Relation]:
        """Return all relations (edges) in the graph."""
        return [
            self._edge_to_relation(dict(data))
            for _, _, data in self.graph.edges(data=True)
        ]

    def relation_count(self) -> int:
        """Return the number of relations (edges)."""
        return self.graph.number_of_edges()

    def get_relations_of(self, entity_id: str, direction: str = "both") -> list[Relation]:
        """Return relations involving a specific entity.

        Args:
            entity_id: The entity to look up.
            direction: ``"out"`` for outgoing, ``"in"`` for incoming,
                ``"both"`` for all.

        Returns:
            List of :class:`Relation` objects.
        """
        results: list[Relation] = []
        if direction in ("out", "both"):
            for _, target, data in self.graph.out_edges(entity_id, data=True):
                results.append(self._edge_to_relation(dict(data)))
        if direction in ("in", "both"):
            for source, _, data in self.graph.in_edges(entity_id, data=True):
                results.append(self._edge_to_relation(dict(data)))
        return results

    # ------------------------------------------------------------------
    # Knowledge node (generalized hints)
    # ------------------------------------------------------------------

    def add_knowledge_node(self, node: KnowledgeNode) -> None:
        """Add a knowledge node (generalized hint) to the graph.

        The knowledge node is stored as a special node type with
        ``entity_type="knowledge"``. Connected entities are linked via
        ``"related_to"`` edges.

        Args:
            node: The :class:`KnowledgeNode` to add.
        """
        attrs = node.model_dump()
        attrs["entity_type"] = "knowledge"
        self.graph.add_node(node.node_id, **attrs)

        # Link to connected entities
        for entity_id in node.connected_entities:
            if entity_id not in self.graph:
                self.graph.add_node(entity_id, id=entity_id, label=entity_id, entity_type="unknown")
            self.graph.add_edge(
                node.node_id, entity_id,
                source=node.node_id, target=entity_id,
                relation="related_to", weight=1.0,
                source_episodes=[], created_at=iso_now(),
                properties={},
            )
        logger.debug(f"Added knowledge node '{node.node_id}'")

    def get_knowledge_nodes(self) -> list[KnowledgeNode]:
        """Return all knowledge nodes (generalized hints) in the graph."""
        results: list[KnowledgeNode] = []
        for node_id, data in self.graph.nodes(data=True):
            if data.get("entity_type") == "knowledge":
                results.append(self._node_to_knowledge_node(node_id, dict(data)))
        return results

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def find_paths(
        self,
        source: str,
        target: str,
        max_depth: int = 3,
    ) -> list[list[str]]:
        """Find all simple paths from source to target up to *max_depth*.

        Args:
            source: Source entity ID.
            target: Target entity ID.
            max_depth: Maximum path length (number of edges).

        Returns:
            List of paths, where each path is a list of entity IDs.
        """
        if source not in self.graph or target not in self.graph:
            return []
        try:
            return list(nx.all_simple_paths(self.graph, source, target, cutoff=max_depth))
        except nx.NetworkXError:
            return []

    def neighbors(self, entity_id: str, max_depth: int = 1) -> list[str]:
        """Return all entity IDs within *max_depth* hops of the given entity.

        Args:
            entity_id: Starting entity.
            max_depth: Maximum hop distance.

        Returns:
            List of entity IDs (excluding the starting entity).
        """
        if entity_id not in self.graph:
            return []
        result: set[str] = set()
        frontier = {entity_id}
        for _ in range(max_depth):
            next_frontier: set[str] = set()
            for node in frontier:
                for neighbor in self.graph.successors(node):
                    if neighbor != entity_id and neighbor not in result:
                        next_frontier.add(neighbor)
                for neighbor in self.graph.predecessors(node):
                    if neighbor != entity_id and neighbor not in result:
                        next_frontier.add(neighbor)
            result |= next_frontier
            frontier = next_frontier
            if not frontier:
                break
        return list(result)

    # ------------------------------------------------------------------
    # Internal converters
    # ------------------------------------------------------------------

    @staticmethod
    def _node_to_entity(node_id: str, data: dict[str, Any]) -> Entity:
        """Convert a graph node's attribute dict to an :class:`Entity`."""
        return Entity(
            id=data.get("id", node_id),
            entity_type=data.get("entity_type", "concept"),
            label=data.get("label", node_id),
            source_episodes=data.get("source_episodes", []),
            confidence=data.get("confidence", 0.5),
            created_at=data.get("created_at", ""),
            properties=data.get("properties", {}),
        )

    @staticmethod
    def _edge_to_relation(data: dict[str, Any]) -> Relation:
        """Convert an edge attribute dict to a :class:`Relation`."""
        return Relation(
            source=data.get("source", ""),
            target=data.get("target", ""),
            relation=data.get("relation", "related_to"),
            weight=data.get("weight", 1.0),
            source_episodes=data.get("source_episodes", []),
            created_at=data.get("created_at", ""),
            properties=data.get("properties", {}),
        )

    @staticmethod
    def _node_to_knowledge_node(node_id: str, data: dict[str, Any]) -> KnowledgeNode:
        """Convert a graph node to a :class:`KnowledgeNode`."""
        return KnowledgeNode(
            node_id=data.get("node_id", node_id),
            hint_text=data.get("hint_text", ""),
            hint_type=HintType(data.get("hint_type", "general")),
            source_episodes=data.get("source_episodes", []),
            confidence=data.get("confidence", 0.5),
            created_at=data.get("created_at", ""),
            connected_entities=data.get("connected_entities", []),
        )