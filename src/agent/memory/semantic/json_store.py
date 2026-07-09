"""JSON store for L2 Semantic Memory.

Provides read/write access to three JSON files that mirror the graph
structure for human inspection and lightweight access:

    - ``entities.json``  — list of :class:`Entity` dicts
    - ``relations.json`` — list of :class:`Relation` dicts
    - ``concepts.json``  — list of concept definitions (free-form)

Paths (from ``config/memory.yml``)::

    data/memory/semantic/json/concepts.json
    data/memory/semantic/json/entities.json
    data/memory/semantic/json/relations.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schemas import Entity, Relation
from ...utils.logging import get_logger
from ...utils.serialization import read_json, write_json

logger = get_logger("agent.memory.semantic.json_store")


class JsonStore:
    """JSON-backed store for L2 semantic memory data.

    Args:
        concepts_path: Path to concepts.json.
        entities_path: Path to entities.json.
        relations_path: Path to relations.json.
    """

    def __init__(
        self,
        concepts_path: str = "data/memory/semantic/json/concepts.json",
        entities_path: str = "data/memory/semantic/json/entities.json",
        relations_path: str = "data/memory/semantic/json/relations.json",
    ):
        self.concepts_path = Path(concepts_path)
        self.entities_path = Path(entities_path)
        self.relations_path = Path(relations_path)

    # ------------------------------------------------------------------
    # Entities
    # ------------------------------------------------------------------

    def load_entities(self) -> list[Entity]:
        """Load all entities from ``entities.json``."""
        if not self.entities_path.exists():
            return []
        data = read_json(self.entities_path)
        return [Entity(**item) for item in data]

    def save_entities(self, entities: list[Entity]) -> None:
        """Save all entities to ``entities.json``."""
        data = [e.model_dump() for e in entities]
        write_json(self.entities_path, data)
        logger.debug(f"Saved {len(entities)} entities to {self.entities_path}")

    def add_entity(self, entity: Entity) -> None:
        """Add a single entity (loads, appends, saves)."""
        entities = self.load_entities()
        # Remove existing with same ID (upsert)
        entities = [e for e in entities if e.id != entity.id]
        entities.append(entity)
        self.save_entities(entities)

    # ------------------------------------------------------------------
    # Relations
    # ------------------------------------------------------------------

    def load_relations(self) -> list[Relation]:
        """Load all relations from ``relations.json``."""
        if not self.relations_path.exists():
            return []
        data = read_json(self.relations_path)
        return [Relation(**item) for item in data]

    def save_relations(self, relations: list[Relation]) -> None:
        """Save all relations to ``relations.json``."""
        data = [r.model_dump() for r in relations]
        write_json(self.relations_path, data)
        logger.debug(f"Saved {len(relations)} relations to {self.relations_path}")

    def add_relation(self, relation: Relation) -> None:
        """Add a single relation (loads, appends, saves)."""
        relations = self.load_relations()
        # Remove existing with same source+target (upsert)
        relations = [
            r for r in relations
            if not (r.source == relation.source and r.target == relation.target)
        ]
        relations.append(relation)
        self.save_relations(relations)

    # ------------------------------------------------------------------
    # Concepts
    # ------------------------------------------------------------------

    def load_concepts(self) -> list[dict[str, Any]]:
        """Load all concepts from ``concepts.json``."""
        if not self.concepts_path.exists():
            return []
        return read_json(self.concepts_path)

    def save_concepts(self, concepts: list[dict[str, Any]]) -> None:
        """Save all concepts to ``concepts.json``."""
        write_json(self.concepts_path, concepts)
        logger.debug(f"Saved {len(concepts)} concepts to {self.concepts_path}")

    def add_concept(self, concept: dict[str, Any]) -> None:
        """Add a single concept (loads, appends, saves)."""
        concepts = self.load_concepts()
        # Upsert by 'id' if present
        cid = concept.get("id")
        if cid:
            concepts = [c for c in concepts if c.get("id") != cid]
        concepts.append(concept)
        self.save_concepts(concepts)

    # ------------------------------------------------------------------
    # Bulk sync with GraphStore
    # ------------------------------------------------------------------

    def sync_from_graph(self, graph_store: Any) -> None:
        """Sync entities and relations from a :class:`GraphStore`.

        Args:
            graph_store: A :class:`GraphStore` instance to read from.
        """
        entities = graph_store.list_entities()
        relations = graph_store.list_relations()
        self.save_entities(entities)
        self.save_relations(relations)
        logger.info(
            f"Synced {len(entities)} entities and {len(relations)} relations "
            f"from graph to JSON"
        )