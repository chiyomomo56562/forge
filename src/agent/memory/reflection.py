"""Reflection processing for the Forge agent framework.

Processes reflection data from the inner loop's reflection stage:
    1. Normalises and validates reflection fields
    2. Extracts generalized hints (general vs tool_specific)
    3. Routes hints to L2 (semantic graph) or L3 (procedural DB)
    4. Updates the episode's reflection status

This module is called by :class:`MemoryManager.store_reflection()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import (
    Episode,
    Reflection,
    GeneralizedHint,
    HintType,
    KnowledgeNode,
)
from .semantic.extractor import EntityExtractor
from .semantic.graph_store import GraphStore
from .procedural.skill_store import SkillStore
from .consolidation import route_hint
from ..utils.logging import get_logger
from ..utils.time import iso_now

logger = get_logger("agent.memory.reflection")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ReflectionResult:
    """Result of processing a reflection."""
    episode_id: str
    hints_extracted: int = 0
    hints_to_l2: int = 0
    hints_to_l3: int = 0
    hint_ids: list[str] = field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Reflection processor
# ---------------------------------------------------------------------------

class ReflectionProcessor:
    """Process reflection data and route hints to L2/L3.

    Args:
        graph_store: L2 :class:`GraphStore` for general hints.
        skill_store: L3 :class:`SkillStore` for tool-specific hints.
        extractor: :class:`EntityExtractor` for hint extraction.
        llm_client: Optional LLM client for the extractor.
    """

    def __init__(
        self,
        graph_store: GraphStore | None = None,
        skill_store: SkillStore | None = None,
        extractor: EntityExtractor | None = None,
        llm_client: Any | None = None,
    ):
        self.graph_store = graph_store or GraphStore()
        self.skill_store = skill_store or SkillStore()
        self.extractor = extractor or EntityExtractor(llm_client=llm_client)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        episode: Episode,
        reflection: Reflection | None = None,
    ) -> ReflectionResult:
        """Process an episode's reflection and route hints to L2/L3.

        Args:
            episode: The episode to process. Uses ``episode.reflection``
                if ``reflection`` is ``None``.
            reflection: Override reflection data. If provided, updates
                the episode's reflection field.

        Returns:
            :class:`ReflectionResult` with extraction statistics.
        """
        # Use override or episode's reflection
        if reflection is not None:
            episode.reflection = reflection

        refl = episode.reflection
        result = ReflectionResult(episode_id=episode.episode_id)

        if refl.is_empty:
            logger.debug(f"Episode {episode.episode_id} has empty reflection, skipping")
            return result

        # Normalise reflection fields
        refl = self._normalise(refl)
        episode.reflection = refl
        episode.mark_reflection_complete()

        # Generate summary
        result.summary = self._summarise(refl)

        # Extract generalized hints
        hints = self.extractor.extract_generalized_hints(episode)
        result.hints_extracted = len(hints)

        # Route and store hints
        for hint in hints:
            target = route_hint(hint)
            if target == "L2":
                self._store_to_l2(hint)
                result.hints_to_l2 += 1
            else:
                self._store_to_l3(hint, episode)
                result.hints_to_l3 += 1
            result.hint_ids.append(hint.hint_id)

        # Store hints on the episode for reference
        episode.generalized_hints = hints

        logger.info(
            f"Processed reflection for {episode.episode_id}: "
            f"{result.hints_extracted} hints "
            f"(L2={result.hints_to_l2}, L3={result.hints_to_l3})"
        )
        return result

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(reflection: Reflection) -> Reflection:
        """Normalise reflection field values (strip, collapse whitespace)."""
        def _clean(s: str) -> str:
            return " ".join(s.strip().split())

        return Reflection(
            what_worked=_clean(reflection.what_worked),
            what_failed=_clean(reflection.what_failed),
            next_hint=_clean(reflection.next_hint),
            causal_condition=_clean(reflection.causal_condition),
        )

    # ------------------------------------------------------------------
    # Summarisation
    # ------------------------------------------------------------------

    @staticmethod
    def _summarise(reflection: Reflection) -> str:
        """Generate a concise summary of the reflection.

        Combines the four reflection fields into a single summary string.
        """
        parts: list[str] = []
        if reflection.what_worked:
            parts.append(f"성공: {reflection.what_worked}")
        if reflection.what_failed:
            parts.append(f"실패: {reflection.what_failed}")
        if reflection.next_hint:
            parts.append(f"힌트: {reflection.next_hint}")
        if reflection.causal_condition:
            parts.append(f"인과: {reflection.causal_condition}")
        return " | ".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # Hint storage
    # ------------------------------------------------------------------

    def _store_to_l2(self, hint: GeneralizedHint) -> None:
        """Store a general hint as a knowledge node in L2."""
        kn = KnowledgeNode(
            node_id=hint.hint_id,
            hint_text=hint.text,
            hint_type=hint.hint_type,
            source_episodes=hint.source_episodes,
            confidence=hint.confidence,
            created_at=hint.created_at or iso_now(),
        )
        self.graph_store.add_knowledge_node(kn)
        logger.debug(f"Stored general hint to L2: {hint.hint_id}")

    def _store_to_l3(self, hint: GeneralizedHint, episode: Episode) -> None:
        """Store a tool-specific hint to L3.

        Attempts to find a matching skill. If none found, logs and skips.
        """
        # Try to find skills by task category or name match
        skills = self.skill_store.list_all()
        for skill in skills:
            # Simple heuristic: if the hint text mentions the skill name
            if skill.name and skill.name.lower() in hint.text.lower():
                updated_hints = skill.reflection_hints + [hint.text]
                self.skill_store.update_reflection_hints(skill.skill_id, updated_hints)
                logger.debug(f"Stored tool-specific hint to L3 skill {skill.skill_id}")
                return

        logger.debug(
            f"No matching skill for hint {hint.hint_id}, "
            f"skipping L3 storage"
        )