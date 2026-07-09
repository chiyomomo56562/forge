"""Graph-based reasoner for L2 Semantic Memory.

Performs simple reasoning over the knowledge graph: path finding,
neighborhood expansion, dependency analysis, and hint retrieval.

The reasoner wraps a :class:`GraphStore` and provides higher-level
query methods that combine graph traversal with semantic analysis.
"""

from __future__ import annotations

from typing import Any

from ..schemas import Entity, Relation, KnowledgeNode
from .graph_store import GraphStore
from ...utils.logging import get_logger

logger = get_logger("agent.memory.semantic.reasoner")


class GraphReasoner:
    """Reason over the L2 knowledge graph.

    Args:
        graph_store: A :class:`GraphStore` instance to query.
        max_depth: Default maximum traversal depth.
    """

    def __init__(
        self,
        graph_store: GraphStore | None = None,
        max_depth: int = 3,
    ):
        self.graph_store = graph_store or GraphStore()
        self.max_depth = max_depth

    # ------------------------------------------------------------------
    # Path-based reasoning
    # ------------------------------------------------------------------

    def find_paths(
        self,
        source: str,
        target: str,
        max_depth: int | None = None,
    ) -> list[list[str]]:
        """Find all paths from *source* to *target*.

        Args:
            source: Source entity ID.
            target: Target entity ID.
            max_depth: Maximum path length. Defaults to ``self.max_depth``.

        Returns:
            List of paths (each a list of entity IDs).
        """
        depth = max_depth if max_depth is not None else self.max_depth
        return self.graph_store.find_paths(source, target, max_depth=depth)

    def shortest_path(self, source: str, target: str) -> list[str] | None:
        """Find the shortest path between two entities.

        Args:
            source: Source entity ID.
            target: Target entity ID.

        Returns:
            List of entity IDs forming the shortest path, or ``None``
            if no path exists.
        """
        import networkx as nx
        try:
            return nx.shortest_path(self.graph_store.graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    # ------------------------------------------------------------------
    # Neighborhood reasoning
    # ------------------------------------------------------------------

    def get_neighborhood(
        self,
        entity_id: str,
        max_depth: int | None = None,
    ) -> list[Entity]:
        """Return all entities within *max_depth* hops of the given entity.

        Args:
            entity_id: Center entity.
            max_depth: Maximum hop distance.

        Returns:
            List of :class:`Entity` objects (excluding the center).
        """
        depth = max_depth if max_depth is not None else self.max_depth
        neighbor_ids = self.graph_store.neighbors(entity_id, max_depth=depth)
        entities: list[Entity] = []
        for nid in neighbor_ids:
            ent = self.graph_store.get_entity(nid)
            if ent is not None:
                entities.append(ent)
        return entities

    def get_related(
        self,
        entity_id: str,
        relation_type: str | None = None,
        direction: str = "both",
    ) -> list[Entity]:
        """Return entities directly related to the given entity.

        Args:
            entity_id: The entity to query.
            relation_type: Filter by relation type (e.g. ``"depends_on"``).
                If ``None``, returns all related entities.
            direction: ``"out"``, ``"in"``, or ``"both"``.

        Returns:
            List of related :class:`Entity` objects.
        """
        relations = self.graph_store.get_relations_of(entity_id, direction=direction)

        related_ids: set[str] = set()
        for rel in relations:
            if relation_type is not None and rel.relation != relation_type:
                continue
            if rel.source == entity_id:
                related_ids.add(rel.target)
            else:
                related_ids.add(rel.source)

        entities: list[Entity] = []
        for rid in related_ids:
            ent = self.graph_store.get_entity(rid)
            if ent is not None:
                entities.append(ent)
        return entities

    # ------------------------------------------------------------------
    # Dependency analysis
    # ------------------------------------------------------------------

    def get_dependencies(self, entity_id: str) -> list[Entity]:
        """Return all entities that *entity_id* depends on.

        Follows ``depends_on`` and ``requires`` edges outward.

        Args:
            entity_id: The entity to analyze.

        Returns:
            List of dependency :class:`Entity` objects.
        """
        result: list[Entity] = []
        seen: set[str] = set()

        def _traverse(eid: str, depth: int) -> None:
            if depth >= self.max_depth or eid in seen:
                return
            seen.add(eid)
            for rel in self.graph_store.get_relations_of(eid, direction="out"):
                if rel.relation in ("depends_on", "requires", "needs"):
                    if rel.target not in seen:
                        ent = self.graph_store.get_entity(rel.target)
                        if ent is not None:
                            result.append(ent)
                        _traverse(rel.target, depth + 1)

        _traverse(entity_id, 0)
        return result

    def get_dependents(self, entity_id: str) -> list[Entity]:
        """Return all entities that depend on *entity_id*.

        Args:
            entity_id: The entity to analyze.

        Returns:
            List of dependent :class:`Entity` objects.
        """
        result: list[Entity] = []
        for rel in self.graph_store.get_relations_of(entity_id, direction="in"):
            if rel.relation in ("depends_on", "requires", "needs"):
                ent = self.graph_store.get_entity(rel.source)
                if ent is not None:
                    result.append(ent)
        return result

    # ------------------------------------------------------------------
    # Hint retrieval
    # ------------------------------------------------------------------

    def get_hints_for_entity(self, entity_id: str) -> list[KnowledgeNode]:
        """Return all knowledge nodes (generalized hints) connected to an entity.

        Args:
            entity_id: The entity to find hints for.

        Returns:
            List of :class:`KnowledgeNode` objects.
        """
        all_hints = self.graph_store.get_knowledge_nodes()
        return [
            h for h in all_hints
            if entity_id in h.connected_entities
        ]

    def search_hints(self, keyword: str) -> list[KnowledgeNode]:
        """Search knowledge nodes by keyword in hint text.

        Args:
            keyword: Case-insensitive search term.

        Returns:
            List of matching :class:`KnowledgeNode` objects.
        """
        keyword_lower = keyword.lower()
        all_hints = self.graph_store.get_knowledge_nodes()
        return [
            h for h in all_hints
            if keyword_lower in h.hint_text.lower()
        ]

    # ------------------------------------------------------------------
    # Graph statistics
    # ------------------------------------------------------------------

    def graph_stats(self) -> dict[str, Any]:
        """Return basic statistics about the knowledge graph.

        Returns:
            Dict with ``nodes``, ``edges``, ``density``, ``avg_degree``.
        """
        import networkx as nx
        g = self.graph_store.graph
        n = g.number_of_nodes()
        e = g.number_of_edges()
        density = nx.density(g) if n > 1 else 0.0
        avg_degree = (2 * e / n) if n > 0 else 0.0
        return {
            "nodes": n,
            "edges": e,
            "density": round(density, 4),
            "avg_degree": round(avg_degree, 2),
        }