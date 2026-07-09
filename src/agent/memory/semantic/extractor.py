"""Entity/relation extractor for L2 Semantic Memory.

Extracts entities and relations from L1 episodic memory episodes using
LLM-based analysis.  Also identifies generalized (general) hints from
reflection data for the dual-storage strategy.

The extractor uses the unified :class:`LLMClient` for LLM calls, with
a rule-based fallback when no LLM is available.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import Entity, Relation, Episode, GeneralizedHint, HintType
from ...utils.logging import get_logger
from ...utils.time import iso_now

logger = get_logger("agent.memory.semantic.extractor")

# ---------------------------------------------------------------------------
# Rule-based fallback patterns
# ---------------------------------------------------------------------------

# Common tool / technology names that appear in task descriptions
_TOOL_PATTERN = re.compile(
    r"\b("
    r"PyPDF2|pdfplumber|matplotlib|pandas|numpy|seaborn|plotly|"
    r"requests|beautifulsoup|selenium|scrapy|openpyxl|xlsxwriter|"
    r"PIL|Pillow|opencv|cv2|sklearn|scikit-learn|tensorflow|"
    r"pytorch|transformers|openai|anthropic|ollama|chromadb|"
    r"networkx|sqlite|postgresql|redis|docker|kubernetes"
    r")\b",
    re.IGNORECASE,
)

# Relation patterns in reflection text
_RELATION_PATTERNS = [
    (re.compile(r"(\w+)\s+(?:depends on|requires|needs)\s+(\w+)", re.IGNORECASE), "depends_on"),
    (re.compile(r"(\w+)\s+is\s+(?:a|an)\s+(\w+)", re.IGNORECASE), "is_a"),
    (re.compile(r"(\w+)\s+causes?\s+(\w+)", re.IGNORECASE), "causes"),
    (re.compile(r"(\w+)\s+(?:uses?|uses)\s+(\w+)", re.IGNORECASE), "uses"),
    (re.compile(r"(\w+)\s+(?:integrates?|integrates? with)\s+(\w+)", re.IGNORECASE), "integrates_with"),
]

# Hint classification keywords
_TOOL_SPECIFIC_KEYWORDS = [
    "pypdf", "pdfplumber", "matplotlib", "pandas", "numpy", "seaborn",
    "plotly", "requests", "beautifulsoup", "selenium", "openpyxl",
    "pillow", "opencv", "sklearn", "tensorflow", "pytorch", "font",
    "ocr", "tesseract", "docker", "kubernetes", "sqlite", "postgres",
    "chromadb", "networkx", "ollama", "openai", "anthropic",
]


class EntityExtractor:
    """Extract entities and relations from L1 episodes.

    Args:
        llm_client: Optional :class:`LLMClient` for LLM-based extraction.
            If ``None``, uses rule-based fallback.
    """

    def __init__(self, llm_client: Any | None = None):
        self._llm_client = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, episode: Episode) -> tuple[list[Entity], list[Relation]]:
        """Extract entities and relations from a single episode.

        Args:
            episode: The :class:`Episode` to analyze.

        Returns:
            Tuple of ``(entities, relations)``.
        """
        if self._llm_client is not None:
            try:
                return self._extract_with_llm(episode)
            except Exception as e:
                logger.warning(f"LLM extraction failed, falling back to rules: {e}")

        return self._extract_with_rules(episode)

    def extract_batch(
        self,
        episodes: list[Episode],
    ) -> tuple[list[Entity], list[Relation]]:
        """Extract from multiple episodes, deduplicating by entity ID.

        Args:
            episodes: List of episodes to process.

        Returns:
            Tuple of ``(entities, relations)`` with duplicates merged.
        """
        all_entities: dict[str, Entity] = {}
        all_relations: list[Relation] = []

        for ep in episodes:
            entities, relations = self.extract(ep)
            for e in entities:
                if e.id in all_entities:
                    # Merge source episodes
                    existing = all_entities[e.id]
                    existing.source_episodes = list(
                        set(existing.source_episodes + e.source_episodes)
                    )
                else:
                    all_entities[e.id] = e
            all_relations.extend(relations)

        return list(all_entities.values()), all_relations

    def extract_generalized_hints(self, episode: Episode) -> list[GeneralizedHint]:
        """Extract generalized hints from an episode's reflection.

        Classifies each hint as ``general`` (→ L2) or ``tool_specific``
        (→ L3) based on keyword matching.

        Args:
            episode: The episode with reflection data.

        Returns:
            List of :class:`GeneralizedHint` objects.
        """
        hints: list[GeneralizedHint] = []
        reflection = episode.reflection

        if reflection.is_empty:
            return hints

        # Combine reflection fields into candidate hints
        candidates = [
            reflection.what_worked,
            reflection.what_failed,
            reflection.next_hint,
            reflection.causal_condition,
        ]

        for i, text in enumerate(candidates):
            if not text.strip():
                continue

            hint_type = self._classify_hint(text)
            hint = GeneralizedHint(
                hint_id=f"{episode.episode_id}_hint_{i}",
                text=text.strip(),
                hint_type=hint_type,
                source_episodes=[episode.episode_id],
                confidence=0.6,
                created_at=iso_now(),
            )
            hints.append(hint)

        return hints

    # ------------------------------------------------------------------
    # LLM-based extraction
    # ------------------------------------------------------------------

    def _extract_with_llm(self, episode: Episode) -> tuple[list[Entity], list[Relation]]:
        """Use LLM to extract entities and relations from an episode."""
        prompt = self._build_prompt(episode)
        response = self._llm_client.chat(
            prompt=prompt,
            system="You are an entity-relation extraction system. Respond in JSON only.",
        )

        try:
            data = json.loads(response.content)
        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON, falling back to rules")
            return self._extract_with_rules(episode)

        entities: list[Entity] = []
        for item in data.get("entities", []):
            entities.append(Entity(
                id=item.get("id", ""),
                entity_type=item.get("type", "concept"),
                label=item.get("label", item.get("id", "")),
                source_episodes=[episode.episode_id],
                confidence=item.get("confidence", 0.7),
                created_at=iso_now(),
            ))

        relations: list[Relation] = []
        for item in data.get("relations", []):
            relations.append(Relation(
                source=item.get("source", ""),
                target=item.get("target", ""),
                relation=item.get("type", "related_to"),
                weight=item.get("weight", 1.0),
                source_episodes=[episode.episode_id],
                created_at=iso_now(),
            ))

        return entities, relations

    @staticmethod
    def _build_prompt(episode: Episode) -> str:
        """Build the LLM extraction prompt for an episode."""
        return (
            f"Extract entities and relations from the following episode.\n\n"
            f"Task: {episode.task}\n"
            f"Execution Summary: {episode.execution_summary}\n"
            f"Reflection - What worked: {episode.reflection.what_worked}\n"
            f"Reflection - What failed: {episode.reflection.what_failed}\n"
            f"Reflection - Next hint: {episode.reflection.next_hint}\n"
            f"Reflection - Causal condition: {episode.reflection.causal_condition}\n\n"
            f"Respond as JSON: {{\"entities\": [{{\"id\": \"...\", \"type\": \"...\", \"label\": \"...\"}}], "
            f"\"relations\": [{{\"source\": \"...\", \"target\": \"...\", \"type\": \"...\"}}]}}"
        )

    # ------------------------------------------------------------------
    # Rule-based fallback extraction
    # ------------------------------------------------------------------

    def _extract_with_rules(self, episode: Episode) -> tuple[list[Entity], list[Relation]]:
        """Rule-based entity/relation extraction (no LLM required)."""
        text = " ".join([
            episode.task,
            episode.execution_summary,
            episode.reflection.what_worked,
            episode.reflection.what_failed,
            episode.reflection.next_hint,
            episode.reflection.causal_condition,
        ])

        # Extract tool/technology entities
        entities: dict[str, Entity] = {}
        for match in _TOOL_PATTERN.finditer(text):
            label = match.group(1)
            eid = label.lower().replace(" ", "_")
            if eid not in entities:
                entities[eid] = Entity(
                    id=eid,
                    entity_type="tool",
                    label=label,
                    source_episodes=[episode.episode_id],
                    confidence=0.7,
                    created_at=iso_now(),
                )

        # Extract relations from reflection text
        relations: list[Relation] = []
        reflection_text = " ".join([
            episode.reflection.what_worked,
            episode.reflection.what_failed,
            episode.reflection.next_hint,
            episode.reflection.causal_condition,
        ])
        for pattern, rel_type in _RELATION_PATTERNS:
            for match in pattern.finditer(reflection_text):
                source = match.group(1).lower()
                target = match.group(2).lower()
                # Only create relations between known entities
                if source in entities and target in entities:
                    relations.append(Relation(
                        source=source,
                        target=target,
                        relation=rel_type,
                        source_episodes=[episode.episode_id],
                        created_at=iso_now(),
                    ))

        return list(entities.values()), relations

    # ------------------------------------------------------------------
    # Hint classification
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_hint(text: str) -> HintType:
        """Classify a hint as general or tool-specific.

        Args:
            text: The hint text.

        Returns:
            :class:`HintType.GENERAL` or :class:`HintType.TOOL_SPECIFIC`.
        """
        text_lower = text.lower()
        for keyword in _TOOL_SPECIFIC_KEYWORDS:
            if keyword in text_lower:
                return HintType.TOOL_SPECIFIC
        return HintType.GENERAL