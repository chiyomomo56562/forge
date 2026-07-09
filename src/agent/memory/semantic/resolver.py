"""Entity resolver for L2 Semantic Memory.

Merges similar/duplicate entities and groups synonyms to maintain a
clean knowledge graph.  Uses string similarity (normalized Levenshtein
distance) and label-based matching.

The resolver operates on a list of :class:`Entity` objects and returns
a deduplicated list with merged metadata.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from ..schemas import Entity, Relation
from ...utils.logging import get_logger

logger = get_logger("agent.memory.semantic.resolver")

# ---------------------------------------------------------------------------
# Default synonym groups (can be extended via config)
# ---------------------------------------------------------------------------

_DEFAULT_SYNONYMS: list[set[str]] = [
    {"matplotlib", "plt", "pyplot"},
    {"pandas", "pd"},
    {"numpy", "np"},
    {"opencv", "cv2", "pil", "pillow"},
    {"beautifulsoup", "bs4"},
    {"scikit-learn", "sklearn"},
    {"postgresql", "postgres", "psql"},
    {"requests", "http", "urllib"},
    {"openai", "gpt", "chatgpt"},
    {"anthropic", "claude"},
    {"ollama", "llama"},
]


class EntityResolver:
    """Resolve and merge duplicate/similar entities.

    Args:
        similarity_threshold: Minimum normalized similarity (0–1) for
            two entity labels to be considered the same.
        synonym_groups: List of synonym sets. Entities whose IDs appear
            in the same set are merged. Defaults to :data:`_DEFAULT_SYNONYMS`.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.85,
        synonym_groups: list[set[str]] | None = None,
    ):
        self.similarity_threshold = similarity_threshold
        self.synonym_groups = synonym_groups or [set(s) for s in _DEFAULT_SYNONYMS]
        # Build reverse lookup: term → group index
        self._synonym_lookup: dict[str, int] = {}
        for i, group in enumerate(self.synonym_groups):
            for term in group:
                self._synonym_lookup[term.lower()] = i

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, entities: list[Entity]) -> list[Entity]:
        """Merge duplicate/similar entities into a single list.

        Args:
            entities: List of entities to deduplicate.

        Returns:
            Deduplicated list of :class:`Entity` with merged metadata.
        """
        if not entities:
            return []

        merged: dict[str, Entity] = {}
        id_map: dict[str, str] = {}  # original_id → canonical_id

        for entity in entities:
            canonical_id = self._find_canonical(entity, merged)

            if canonical_id is None:
                # New unique entity
                merged[entity.id] = entity
                id_map[entity.id] = entity.id
            else:
                # Merge into existing
                existing = merged[canonical_id]
                merged[canonical_id] = self._merge(existing, entity)
                id_map[entity.id] = canonical_id

        # Store id_map for relation remapping
        self._last_id_map = id_map

        return list(merged.values())

    def resolve_with_relations(
        self,
        entities: list[Entity],
        relations: list[Relation],
    ) -> tuple[list[Entity], list[Relation]]:
        """Resolve entities and remap relations to canonical IDs.

        Args:
            entities: List of entities to deduplicate.
            relations: List of relations to remap.

        Returns:
            Tuple of ``(resolved_entities, remapped_relations)``.
        """
        resolved = self.resolve(entities)
        id_map = getattr(self, "_last_id_map", {})

        remapped: list[Relation] = []
        seen_edges: set[tuple[str, str, str]] = set()

        for rel in relations:
            new_source = id_map.get(rel.source, rel.source)
            new_target = id_map.get(rel.target, rel.target)

            # Skip self-loops created by merging
            if new_source == new_target:
                continue

            key = (new_source, new_target, rel.relation)
            if key in seen_edges:
                # Merge source_episodes into existing relation
                for existing in remapped:
                    if (existing.source, existing.target, existing.relation) == key:
                        existing.source_episodes = list(
                            set(existing.source_episodes + rel.source_episodes)
                        )
                        break
                continue

            seen_edges.add(key)
            remapped.append(Relation(
                source=new_source,
                target=new_target,
                relation=rel.relation,
                weight=rel.weight,
                source_episodes=list(rel.source_episodes),
                created_at=rel.created_at,
                properties=dict(rel.properties),
            ))

        return resolved, remapped

    # ------------------------------------------------------------------
    # Internal matching
    # ------------------------------------------------------------------

    def _find_canonical(self, entity: Entity, existing: dict[str, Entity]) -> str | None:
        """Find the canonical entity ID that *entity* should merge into.

        Returns:
            Canonical ID string, or ``None`` if no match (new entity).
        """
        # 1. Exact ID match
        if entity.id in existing:
            return entity.id

        # 2. Synonym group match
        for existing_id, existing_entity in existing.items():
            if self._are_synonyms(entity.id, existing_entity.id):
                return existing_id

        # 3. Label similarity match
        for existing_id, existing_entity in existing.items():
            if self._are_similar(entity, existing_entity):
                return existing_id

        return None

    def _are_synonyms(self, id_a: str, id_b: str) -> bool:
        """Check if two entity IDs belong to the same synonym group."""
        idx_a = self._synonym_lookup.get(id_a.lower())
        idx_b = self._synonym_lookup.get(id_b.lower())
        if idx_a is not None and idx_b is not None:
            return idx_a == idx_b
        return False

    def _are_similar(self, a: Entity, b: Entity) -> bool:
        """Check if two entities are similar enough to merge.

        Uses label string similarity and entity type matching.
        """
        # Must be same entity type (don't merge a tool with a person)
        if a.entity_type != b.entity_type:
            return False

        # Label similarity
        similarity = SequenceMatcher(None, a.label.lower(), b.label.lower()).ratio()
        return similarity >= self.similarity_threshold

    @staticmethod
    def _merge(primary: Entity, secondary: Entity) -> Entity:
        """Merge *secondary* into *primary*, keeping primary's ID.

        Merges source_episodes, takes the higher confidence, and
        combines properties.
        """
        merged_episodes = list(set(primary.source_episodes + secondary.source_episodes))
        merged_properties = {**secondary.properties, **primary.properties}

        return Entity(
            id=primary.id,
            entity_type=primary.entity_type,
            label=primary.label,
            source_episodes=merged_episodes,
            confidence=max(primary.confidence, secondary.confidence),
            created_at=primary.created_at or secondary.created_at,
            properties=merged_properties,
        )

    # ------------------------------------------------------------------
    # Synonym group management
    # ------------------------------------------------------------------

    def add_synonym_group(self, synonyms: set[str]) -> None:
        """Add a new synonym group.

        Args:
            synonyms: Set of synonymous terms.
        """
        idx = len(self.synonym_groups)
        self.synonym_groups.append(synonyms)
        for term in synonyms:
            self._synonym_lookup[term.lower()] = idx
        logger.debug(f"Added synonym group: {synonyms}")