"""L1→L2/L3 consolidation for the Forge agent framework.

Extracts knowledge from L1 episodic memory and routes it to the
appropriate long-term storage layer using the dual-storage strategy:

    - **General hints** (e.g. "한글 시각화 시 폰트 캐시 확인") → L2 semantic graph
    - **Tool-specific hints** (e.g. "PyPDF2로는 이미지 PDF 텍스트 추출 안 됨") → L3 procedural DB

The consolidation pipeline:
    1. Extract entities/relations from episodes (via :class:`EntityExtractor`)
    2. Resolve/merge duplicate entities (via :class:`EntityResolver`)
    3. Extract generalized hints from reflections
    4. Route hints to L2 (general) or L3 (tool_specific)
"""

from __future__ import annotations

from typing import Any

from .schemas import (
    Episode,
    Entity,
    Relation,
    GeneralizedHint,
    HintType,
    KnowledgeNode,
)
from .semantic.extractor import EntityExtractor
from .semantic.resolver import EntityResolver
from .semantic.graph_store import GraphStore
from .procedural.skill_store import SkillStore
from ..utils.logging import get_logger
from ..utils.time import iso_now

logger = get_logger("agent.memory.consolidation")


# ---------------------------------------------------------------------------
# Hint routing
# ---------------------------------------------------------------------------

def route_hint(hint: GeneralizedHint) -> str:
    """Determine which layer a generalized hint should be stored in.

    Dual-storage strategy:
        - ``general`` → L2 (semantic graph as knowledge node)
        - ``tool_specific`` → L3 (procedural DB reflection_hints)

    Args:
        hint: The :class:`GeneralizedHint` to route.

    Returns:
        ``"L2"`` or ``"L3"``.
    """
    if hint.hint_type == HintType.GENERAL:
        return "L2"
    return "L3"


# ---------------------------------------------------------------------------
# Consolidation result
# ---------------------------------------------------------------------------

class ConsolidationResult:
    """Result of consolidating a batch of episodes.

    Tracks what was extracted and where it was stored.
    """

    def __init__(self) -> None:
        self.episode_ids: list[str] = []
        self.entities_extracted: int = 0
        self.relations_extracted: int = 0
        self.hints_routed_to_l2: int = 0
        self.hints_routed_to_l3: int = 0
        self.entities: list[Entity] = []
        self.relations: list[Relation] = []
        self.hints: list[GeneralizedHint] = []

    def __repr__(self) -> str:
        return (
            f"ConsolidationResult("
            f"episodes={len(self.episode_ids)}, "
            f"entities={self.entities_extracted}, "
            f"relations={self.relations_extracted}, "
            f"L2_hints={self.hints_routed_to_l2}, "
            f"L3_hints={self.hints_routed_to_l3})"
        )


# ---------------------------------------------------------------------------
# Consolidator
# ---------------------------------------------------------------------------

class Consolidator:
    """Consolidate L1 episodes into L2 (semantic) and L3 (procedural) memory.

    Args:
        graph_store: L2 :class:`GraphStore` for entities/relations/knowledge nodes.
        skill_store: L3 :class:`SkillStore` for tool-specific hints.
        extractor: :class:`EntityExtractor` for entity/relation extraction.
        resolver: :class:`EntityResolver` for deduplication.
        llm_client: Optional LLM client for the extractor.
    """

    def __init__(
        self,
        graph_store: GraphStore | None = None,
        skill_store: SkillStore | None = None,
        extractor: EntityExtractor | None = None,
        resolver: EntityResolver | None = None,
        llm_client: Any | None = None,
    ):
        self.graph_store = graph_store or GraphStore()
        self.skill_store = skill_store or SkillStore()
        self.extractor = extractor or EntityExtractor(llm_client=llm_client)
        self.resolver = resolver or EntityResolver()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def consolidate(self, episodes: list[Episode]) -> ConsolidationResult:
        """Consolidate a batch of episodes into L2/L3.

        Args:
            episodes: List of :class:`Episode` to consolidate.

        Returns:
            :class:`ConsolidationResult` with extraction statistics.
        """
        result = ConsolidationResult()
        result.episode_ids = [ep.episode_id for ep in episodes]

        if not episodes:
            return result

        # 1. Extract entities and relations
        raw_entities, raw_relations = self.extractor.extract_batch(episodes)
        logger.info(f"Extracted {len(raw_entities)} entities, {len(raw_relations)} relations")

        # 2. Resolve/merge duplicates
        resolved_entities, resolved_relations = self.resolver.resolve_with_relations(
            raw_entities, raw_relations,
        )
        logger.info(f"After resolution: {len(resolved_entities)} entities, {len(resolved_relations)} relations")

        # 3. Store entities and relations in L2 graph
        for entity in resolved_entities:
            self.graph_store.add_entity(entity)
        for relation in resolved_relations:
            self.graph_store.add_relation(relation)

        result.entities = resolved_entities
        result.relations = resolved_relations
        result.entities_extracted = len(resolved_entities)
        result.relations_extracted = len(resolved_relations)

        # 4. Extract and route generalized hints
        all_hints: list[GeneralizedHint] = []
        for ep in episodes:
            hints = self.extractor.extract_generalized_hints(ep)
            all_hints.extend(hints)

        for hint in all_hints:
            target = route_hint(hint)
            if target == "L2":
                self._store_hint_to_l2(hint)
                result.hints_routed_to_l2 += 1
            else:
                self._store_hint_to_l3(hint)
                result.hints_routed_to_l3 += 1

        result.hints = all_hints
        logger.info(
            f"Consolidated {len(episodes)} episodes: "
            f"{result.entities_extracted} entities → L2, "
            f"{result.hints_routed_to_l2} general hints → L2, "
            f"{result.hints_routed_to_l3} tool-specific hints → L3"
        )
        return result

    # ------------------------------------------------------------------
    # Hint storage
    # ------------------------------------------------------------------

    def _store_hint_to_l2(self, hint: GeneralizedHint) -> None:
        """Store a general hint as a knowledge node in the L2 graph."""
        kn = KnowledgeNode(
            node_id=hint.hint_id,
            hint_text=hint.text,
            hint_type=hint.hint_type,
            source_episodes=hint.source_episodes,
            confidence=hint.confidence,
            created_at=hint.created_at or iso_now(),
            connected_entities=[],
        )
        self.graph_store.add_knowledge_node(kn)
        logger.debug(f"Stored general hint to L2: {hint.hint_id}")

    def _store_hint_to_l3(self, hint: GeneralizedHint) -> None:
        """Store a tool-specific hint to L3 procedural memory.

        Attempts to find a matching skill by source episode. If no skill
        is found, the hint is logged but not stored (it will be picked up
        when a skill is created for this domain).
        """
        # Try to find a skill associated with the source episodes
        for ep_id in hint.source_episodes:
            # Search all skills for ones that reference this episode
            skills = self.skill_store.list_all()
            for skill in skills:
                if ep_id in skill.metadata.last_executed_at or "":
                    self.skill_store.update_reflection_hints(
                        skill.skill_id,
                        skill.reflection_hints + [hint.text],
                    )
                    logger.debug(f"Stored tool-specific hint to L3 skill {skill.skill_id}")
                    return

        # No matching skill found — store as a standalone hint log
        logger.debug(
            f"No matching skill for tool-specific hint {hint.hint_id}, "
            f"skipping L3 storage (will be picked up when skill is created)"
        )