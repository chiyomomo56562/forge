"""Memory Manager — L1~L5 통합 라우팅.

Integrates all five memory layers into a single interface:
    - L1 Episodic: :class:`EpisodicStore` (Chroma)
    - L2 Semantic: :class:`GraphStore` (NetworkX)
    - L3 Procedural: :class:`SkillStore` (SQLite)
    - L4 Constitution: :class:`ConstitutionLoader` (YAML)
    - L5 Identity: :class:`IdentityStore` (SQLite)

Provides:
    - ``retrieve()`` — Selective injection: search specified layers
    - ``store_episode()`` — Save to L1 + raw event log
    - ``store_reflection()`` — Save reflection to L1 + route hints to L2/L3
    - ``consolidate()`` — Extract knowledge from L1 → L2/L3
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .schemas import (
    Episode,
    Reflection,
    MemoryRecord,
    MemoryLayer,
    Entity,
    Relation,
    Skill,
)
from .policies import DEFAULT_POLICIES, LayerPolicy
from .ranking import rank_records
from .episodic.store import EpisodicStore
from .episodic.encoder import EmbeddingEncoder
from .episodic.event_logger import EventLogger
from .episodic.retriever import EpisodicRetriever
from .semantic.graph_store import GraphStore
from .semantic.reasoner import GraphReasoner
from .procedural.skill_store import SkillStore
from .procedural.skill_loader import SkillLoader
from .constitution.loader import ConstitutionLoader
from .identity.identity_store import IdentityStore
from .consolidation import Consolidator, ConsolidationResult
from .reflection import ReflectionProcessor, ReflectionResult
from ..utils.logging import get_logger

logger = get_logger("agent.memory.manager")


class MemoryManager:
    """Unified memory manager for L1–L5.

    Args:
        chroma_path: Path for L1 Chroma DB.
        graphml_path: Path for L2 GraphML file.
        gpickle_path: Path for L2 pickle file.
        sqlite_path: Path for L3 SQLite DB.
        skills_dir: Directory for L3 skill code files.
        constitution_dir: Path to L4 constitution YAML directory.
        identity_db_path: Path for L5 SQLite DB.
        encoder: Optional :class:`EmbeddingEncoder` for L1.
        llm_client: Optional LLM client for extraction/consolidation.
    """

    def __init__(
        self,
        chroma_path: str = "data/memory/episodic/chroma",
        graphml_path: str = "data/memory/semantic/graph/knowledge_graph.graphml",
        gpickle_path: str = "data/memory/semantic/graph/knowledge_graph.gpickle",
        sqlite_path: str = "data/memory/procedural/skills.sqlite3",
        skills_dir: str = "scripts/skills",
        constitution_dir: str = "constitution",
        identity_db_path: str = "identity/identity.sqlite3",
        raw_events_dir: str = "data/memory/episodic/raw_events",
        encoder: EmbeddingEncoder | None = None,
        llm_client: Any | None = None,
    ):
        # L1 — Episodic
        self.encoder = encoder
        self.episodic_store = EpisodicStore(
            chroma_path=chroma_path,
            encoder=encoder,
        )
        self.event_logger = EventLogger(raw_events_dir=raw_events_dir)
        self.episodic_retriever = EpisodicRetriever(
            store=self.episodic_store,
            encoder=encoder,
        )

        # L2 — Semantic
        self.graph_store = GraphStore(
            graphml_path=graphml_path,
            gpickle_path=gpickle_path,
        )
        self.reasoner = GraphReasoner(graph_store=self.graph_store)

        # L3 — Procedural
        self.skill_store = SkillStore(
            db_path=sqlite_path,
            skills_dir=skills_dir,
        )
        self.skill_loader = SkillLoader(store=self.skill_store)

        # L4 — Constitution
        self.constitution_loader = ConstitutionLoader(
            constitution_dir=constitution_dir,
        )
        self._constitution = None

        # L5 — Identity
        self.identity_store = IdentityStore(db_path=identity_db_path)

        # Consolidation & reflection
        self.consolidator = Consolidator(
            graph_store=self.graph_store,
            skill_store=self.skill_store,
            llm_client=llm_client,
        )
        self.reflection_processor = ReflectionProcessor(
            graph_store=self.graph_store,
            skill_store=self.skill_store,
            llm_client=llm_client,
        )

        # Policies
        self.policies = dict(DEFAULT_POLICIES)

        logger.info("MemoryManager initialized (L1–L5)")

    # ------------------------------------------------------------------
    # L4 Constitution (lazy load)
    # ------------------------------------------------------------------

    @property
    def constitution(self):
        """Lazily loaded constitution model."""
        if self._constitution is None:
            self._constitution = self.constitution_loader.load()
        return self._constitution

    # ------------------------------------------------------------------
    # Retrieve — Selective Injection
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        layers: list[str] | None = None,
        top_k: int = 5,
    ) -> list[MemoryRecord]:
        """Retrieve memories from specified layers (Selective Injection).

        Args:
            query: Search query text.
            layers: List of layer names to search (e.g. ``["L1", "L2"]``).
                If ``None``, searches all layers.
            top_k: Maximum results per layer.

        Returns:
            List of :class:`MemoryRecord` ranked by combined score.
        """
        if layers is None:
            layers = ["L1", "L2", "L3"]

        records: list[MemoryRecord] = []

        if "L1" in layers:
            records.extend(self._retrieve_l1(query, top_k))

        if "L2" in layers:
            records.extend(self._retrieve_l2(query, top_k))

        if "L3" in layers:
            records.extend(self._retrieve_l3(query, top_k))

        # Rank and sort
        ranked = rank_records(records, query_similarity=0.5)
        return ranked[:top_k * len(layers)]

    def _retrieve_l1(self, query: str, top_k: int) -> list[MemoryRecord]:
        """Retrieve from L1 episodic memory."""
        records: list[MemoryRecord] = []
        try:
            results = self.episodic_retriever.retrieve(query, top_k=top_k)
            for item in results:
                ep_data = item.get("metadata", item)
                records.append(MemoryRecord(
                    record_id=ep_data.get("episode_id", ""),
                    layer=MemoryLayer.L1,
                    data=ep_data,
                    created_at=ep_data.get("timestamp", ""),
                    tags=["episodic"],
                ))
        except Exception as e:
            logger.warning(f"L1 retrieval failed: {e}")
        return records

    def _retrieve_l2(self, query: str, top_k: int) -> list[MemoryRecord]:
        """Retrieve from L2 semantic memory (keyword-based)."""
        records: list[MemoryRecord] = []
        try:
            # Search knowledge nodes by keyword
            hints = self.reasoner.search_hints(query)
            for hint in hints[:top_k]:
                records.append(MemoryRecord(
                    record_id=hint.node_id,
                    layer=MemoryLayer.L2,
                    data=hint.model_dump(),
                    created_at=hint.created_at,
                    tags=["semantic", "knowledge_node"],
                ))

            # Also search entities by label
            for entity in self.graph_store.list_entities():
                if query.lower() in entity.label.lower():
                    records.append(MemoryRecord(
                        record_id=entity.id,
                        layer=MemoryLayer.L2,
                        data=entity.model_dump(),
                        created_at=entity.created_at,
                        tags=["semantic", "entity"],
                    ))
                    if len(records) >= top_k:
                        break
        except Exception as e:
            logger.warning(f"L2 retrieval failed: {e}")
        return records[:top_k]

    def _retrieve_l3(self, query: str, top_k: int) -> list[MemoryRecord]:
        """Retrieve from L3 procedural memory (keyword-based)."""
        records: list[MemoryRecord] = []
        try:
            for skill in self.skill_store.list_all():
                if query.lower() in skill.name.lower() or query.lower() in skill.description.lower():
                    records.append(MemoryRecord(
                        record_id=skill.skill_id,
                        layer=MemoryLayer.L3,
                        data=skill.model_dump(),
                        created_at=skill.created_at,
                        tags=["procedural", "skill"],
                    ))
                    if len(records) >= top_k:
                        break
        except Exception as e:
            logger.warning(f"L3 retrieval failed: {e}")
        return records[:top_k]

    # ------------------------------------------------------------------
    # Store Episode
    # ------------------------------------------------------------------

    def store_episode(self, episode: Episode) -> str:
        """Store an episode in L1 + log raw event.

        Args:
            episode: The :class:`Episode` to store.

        Returns:
            The episode ID.
        """
        # Check write policy
        policy = self.policies.get(MemoryLayer.L1)
        if policy and not policy.writable:
            raise PermissionError("L1 is not writable per current policy")

        # Store in L1 (Chroma)
        self.episodic_store.upsert(episode)

        # Log raw event
        self.event_logger.log_episode(episode.model_dump())

        logger.info(f"Stored episode {episode.episode_id} in L1 + raw event log")
        return episode.episode_id

    # ------------------------------------------------------------------
    # Store Reflection
    # ------------------------------------------------------------------

    def store_reflection(
        self,
        episode_id: str,
        reflection: Reflection,
    ) -> ReflectionResult:
        """Store reflection in L1 + route hints to L2/L3 (dual-storage).

        Args:
            episode_id: The episode to attach the reflection to.
            reflection: The :class:`Reflection` data.

        Returns:
            :class:`ReflectionResult` with hint routing statistics.
        """
        # Retrieve the episode from L1
        ep_data = self.episodic_store.get(episode_id)
        if ep_data is None:
            raise ValueError(f"Episode {episode_id} not found in L1")

        # Reconstruct Episode from stored data
        episode = self._reconstruct_episode(episode_id, ep_data, reflection)

        # Process reflection (extracts hints, routes to L2/L3)
        result = self.reflection_processor.process(episode, reflection)

        # Update episode in L1 with reflection
        episode.mark_reflection_complete()
        self.episodic_store.upsert(episode)

        logger.info(
            f"Stored reflection for {episode_id}: "
            f"{result.hints_to_l2} hints → L2, {result.hints_to_l3} hints → L3"
        )
        return result

    # ------------------------------------------------------------------
    # Consolidate
    # ------------------------------------------------------------------

    def consolidate(self, episode_ids: list[str]) -> ConsolidationResult:
        """Consolidate L1 episodes into L2/L3 knowledge.

        Extracts entities, relations, and generalized hints from the
        specified episodes and routes them to the appropriate layers.

        Args:
            episode_ids: List of episode IDs to consolidate.

        Returns:
            :class:`ConsolidationResult` with extraction statistics.
        """
        episodes: list[Episode] = []
        for eid in episode_ids:
            ep_data = self.episodic_store.get(eid)
            if ep_data is None:
                logger.warning(f"Episode {eid} not found, skipping")
                continue
            episode = self._reconstruct_episode(eid, ep_data)
            episodes.append(episode)

        if not episodes:
            logger.warning("No episodes found to consolidate")
            return ConsolidationResult()

        result = self.consolidator.consolidate(episodes)

        # Save L2 graph after consolidation
        self.graph_store.save()

        logger.info(f"Consolidated {len(episodes)} episodes: {result}")
        return result

    # ------------------------------------------------------------------
    # Policy enforcement
    # ------------------------------------------------------------------

    def check_permission(self, layer: MemoryLayer, action: str) -> bool:
        """Check if an action is permitted on a layer.

        Args:
            layer: The memory layer.
            action: ``"read"``, ``"write"``, or ``"delete"``.

        Returns:
            ``True`` if the action is permitted.
        """
        policy = self.policies.get(layer)
        if policy is None:
            return True  # No policy = allowed

        if action == "read":
            return policy.readable
        elif action == "write":
            return policy.writable
        elif action == "delete":
            return policy.deletable
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _reconstruct_episode(
        episode_id: str,
        data: dict[str, Any],
        reflection: Reflection | None = None,
    ) -> Episode:
        """Reconstruct an :class:`Episode` from stored data.

        Args:
            episode_id: The episode ID.
            data: Stored episode data (from Chroma or dict).
            reflection: Optional override reflection.

        Returns:
            Reconstructed :class:`Episode`.
        """
        from .schemas import Evaluation

        # Handle both Chroma metadata format and direct dict
        if "task" in data:
            task = data["task"]
        else:
            task = data.get("document", "")

        refl = reflection
        if refl is None:
            from .schemas import Reflection as R
            refl = R(
                what_worked=data.get("what_worked", ""),
                what_failed=data.get("what_failed", ""),
                next_hint=data.get("next_hint", ""),
                causal_condition=data.get("causal_condition", ""),
            )

        return Episode(
            episode_id=episode_id,
            task=task,
            execution_summary=data.get("execution_summary", ""),
            evaluation=Evaluation(),
            reflection=refl,
            timestamp=data.get("timestamp", ""),
            task_category=data.get("task_category", "general"),
            has_reflection=data.get("has_reflection", False),
        )