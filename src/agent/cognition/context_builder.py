"""Context builder for selective injection (선택적 주입).

Builds the LLM context by retrieving only the most relevant memories
from the MemoryManager, following the density-first principle:

    1. Narrow search: L1 reflection data top_k=3 (density-first)
    2. Associative expansion: related L1 episodes + L2 knowledge + L3 skill hints
    3. Token budget: trim context to fit within the token budget
    4. Format as a structured context string for LLM prompt injection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..memory.schemas import MemoryRecord, MemoryLayer
from ..memory.manager import MemoryManager
from ..utils.logging import get_logger

logger = get_logger("agent.cognition.context_builder")


@dataclass
class ContextBlock:
    """A single block of injected context."""
    layer: str
    source: str  # e.g. "episodic", "semantic", "procedural"
    content: str
    token_estimate: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BuiltContext:
    """Result of building context for LLM injection."""
    blocks: list[ContextBlock] = field(default_factory=list)
    total_tokens: int = 0
    truncated: bool = False

    def to_prompt_string(self) -> str:
        """Format the context as a string for LLM prompt injection."""
        if not self.blocks:
            return "(no relevant memories found)"

        parts: list[str] = []
        for block in self.blocks:
            parts.append(f"### [{block.layer}] {block.source}\n{block.content}")
        return "\n\n".join(parts)


class ContextBuilder:
    """Build LLM context via selective injection from memory layers.

    Args:
        memory_manager: A :class:`MemoryManager` instance.
        top_k: Initial narrow search count (density-first).
        expand_k: Additional results for associative expansion.
        token_budget: Maximum token budget for the context.
    """

    def __init__(
        self,
        memory_manager: MemoryManager | None = None,
        top_k: int = 3,
        expand_k: int = 10,
        token_budget: int = 6000,
    ):
        self.memory_manager = memory_manager
        self.top_k = top_k
        self.expand_k = expand_k
        self.token_budget = token_budget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, query: str, memory_manager: MemoryManager | None = None) -> BuiltContext:
        """Build the injection context for a user query.

        Pipeline:
            1. Narrow search: L1 reflection data (density-first)
            2. Associative expansion: L2 knowledge + L3 skill hints
            3. Token budget trimming
            4. Format as structured context

        Args:
            query: The user's input query.
            memory_manager: Override the stored MemoryManager.

        Returns:
            :class:`BuiltContext` with context blocks.
        """
        mgr = memory_manager or self.memory_manager
        if mgr is None:
            logger.warning("No MemoryManager available, returning empty context")
            return BuiltContext()

        context = BuiltContext()
        used_tokens = 0

        # Step 1: Narrow search — L1 reflection data (density-first)
        l1_records = self._search_l1(mgr, query, self.top_k)
        for record in l1_records:
            block = self._record_to_block(record)
            if used_tokens + block.token_estimate > self.token_budget:
                context.truncated = True
                break
            context.blocks.append(block)
            used_tokens += block.token_estimate

        # Step 2: Associative expansion — L2 knowledge
        l2_records = self._search_l2(mgr, query, self.top_k)
        for record in l2_records:
            block = self._record_to_block(record)
            if used_tokens + block.token_estimate > self.token_budget:
                context.truncated = True
                break
            context.blocks.append(block)
            used_tokens += block.token_estimate

        # Step 3: Associative expansion — L3 skill hints
        l3_records = self._search_l3(mgr, query, self.top_k)
        for record in l3_records:
            block = self._record_to_block(record)
            if used_tokens + block.token_estimate > self.token_budget:
                context.truncated = True
                break
            context.blocks.append(block)
            used_tokens += block.token_estimate

        context.total_tokens = used_tokens
        logger.info(
            f"Built context: {len(context.blocks)} blocks, "
            f"~{used_tokens} tokens"
            f"{' (truncated)' if context.truncated else ''}"
        )
        return context

    # ------------------------------------------------------------------
    # Layer-specific search
    # ------------------------------------------------------------------

    def _search_l1(self, mgr: MemoryManager, query: str, top_k: int) -> list[MemoryRecord]:
        """Search L1 episodic memory (density-first: reflection data first)."""
        try:
            records = mgr.retrieve(query, layers=["L1"], top_k=top_k)
            # Density-first: prioritize records with reflection data
            with_reflection = [r for r in records if r.data.get("has_reflection", False)]
            without_reflection = [r for r in records if not r.data.get("has_reflection", False)]
            return with_reflection + without_reflection
        except Exception as e:
            logger.warning(f"L1 search failed: {e}")
            return []

    def _search_l2(self, mgr: MemoryManager, query: str, top_k: int) -> list[MemoryRecord]:
        """Search L2 semantic memory (knowledge nodes + entities)."""
        try:
            return mgr.retrieve(query, layers=["L2"], top_k=top_k)
        except Exception as e:
            logger.warning(f"L2 search failed: {e}")
            return []

    def _search_l3(self, mgr: MemoryManager, query: str, top_k: int) -> list[MemoryRecord]:
        """Search L3 procedural memory (skills)."""
        try:
            return mgr.retrieve(query, layers=["L3"], top_k=top_k)
        except Exception as e:
            logger.warning(f"L3 search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Record → ContextBlock conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _record_to_block(record: MemoryRecord) -> ContextBlock:
        """Convert a MemoryRecord to a ContextBlock for injection."""
        layer = record.layer.value
        data = record.data

        if layer == "L1":
            content = ContextBuilder._format_l1(data)
            source = "episodic"
        elif layer == "L2":
            content = ContextBuilder._format_l2(data)
            source = "semantic"
        elif layer == "L3":
            content = ContextBuilder._format_l3(data)
            source = "procedural"
        else:
            content = str(data)
            source = "unknown"

        # Rough token estimate: ~4 chars per token
        token_estimate = max(1, len(content) // 4)

        return ContextBlock(
            layer=layer,
            source=source,
            content=content,
            token_estimate=token_estimate,
            metadata={"record_id": record.record_id},
        )

    @staticmethod
    def _format_l1(data: dict[str, Any]) -> str:
        """Format an L1 episodic record for injection."""
        parts: list[str] = []
        task = data.get("task", "")
        if task:
            parts.append(f"Task: {task}")

        if data.get("has_reflection"):
            refl_what_worked = data.get("what_worked", "")
            refl_what_failed = data.get("what_failed", "")
            refl_next_hint = data.get("next_hint", "")
            refl_causal = data.get("causal_condition", "")

            if refl_what_worked:
                parts.append(f"What worked: {refl_what_worked}")
            if refl_what_failed:
                parts.append(f"What failed: {refl_what_failed}")
            if refl_next_hint:
                parts.append(f"Next hint: {refl_next_hint}")
            if refl_causal:
                parts.append(f"Causal condition: {refl_causal}")

        return " | ".join(parts) if parts else str(data)

    @staticmethod
    def _format_l2(data: dict[str, Any]) -> str:
        """Format an L2 semantic record for injection."""
        # Knowledge node
        if "hint_text" in data:
            return f"Knowledge hint: {data['hint_text']}"

        # Entity
        label = data.get("label", "")
        entity_type = data.get("entity_type", "")
        if label:
            return f"Entity: {label} (type: {entity_type})"

        return str(data)

    @staticmethod
    def _format_l3(data: dict[str, Any]) -> str:
        """Format an L3 procedural record for injection."""
        name = data.get("name", "")
        description = data.get("description", "")
        hints = data.get("reflection_hints", [])

        parts: list[str] = []
        if name:
            parts.append(f"Skill: {name}")
        if description:
            parts.append(f"Description: {description}")
        if hints:
            parts.append(f"Hints: {'; '.join(hints[:3])}")

        return " | ".join(parts) if parts else str(data)