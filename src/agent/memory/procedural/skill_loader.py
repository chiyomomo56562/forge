"""Skill loader for L3 Procedural Memory.

Loads skill metadata from the SQLite store and skill code from the
referenced file on disk.  Compiled code objects are cached in memory
for fast repeated execution.

The loader acts as an intermediary between :class:`SkillStore` (persistence)
and :class:`SkillExecutor` (execution), providing a clean API for retrieving
executable skills by ID.

Code file layout::

    scripts/skills/<skill_id>.py

The loader reads the file at ``code_path`` (stored in the DB) to obtain
the executable code string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..schemas import Skill, SkillStatus
from .skill_store import SkillStore
from ...utils.logging import get_logger

logger = get_logger("agent.memory.procedural.skill_loader")


class SkillLoader:
    """Load skills from the store with in-memory caching.

    Caches both the :class:`Skill` model (metadata + code) and the compiled
    code object so repeated lookups avoid both DB reads, file reads, and
    recompilation.

    Args:
        store: A :class:`SkillStore` instance. If ``None``, a default
            store is created.
    """

    def __init__(self, store: SkillStore | None = None):
        self.store = store or SkillStore()
        self._cache: dict[str, Skill] = {}
        self._code_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, skill_id: str, use_cache: bool = True) -> Skill | None:
        """Load a skill by ID (metadata + code from file).

        Args:
            skill_id: The skill identifier.
            use_cache: If ``True``, return cached result when available.

        Returns:
            :class:`Skill` with ``code`` populated from the file, or
            ``None`` if not found.
        """
        if use_cache and skill_id in self._cache:
            return self._cache[skill_id]

        skill = self.store.get(skill_id)
        if skill is None:
            logger.warning(f"Skill not found: {skill_id}")
            return None

        self._cache[skill_id] = skill
        logger.debug(
            f"Loaded skill {skill_id} (status={skill.metadata.status}, "
            f"code_path={skill.code_path})"
        )
        return skill

    def load_metadata(self, skill_id: str) -> Skill | None:
        """Load skill metadata only (no file read).

        Faster than :meth:`load` when you only need metadata.

        Returns:
            :class:`Skill` with empty ``code`` field, or ``None``.
        """
        return self.store.get_metadata(skill_id)

    def load_code(self, skill_id: str, use_cache: bool = True) -> Any | None:
        """Load and compile a skill's code from its file.

        Args:
            skill_id: The skill identifier.
            use_cache: If ``True``, return cached compiled code.

        Returns:
            Compiled code object or ``None`` if the skill is not found,
            the code file is missing, or the code fails to compile.
        """
        if use_cache and skill_id in self._code_cache:
            return self._code_cache[skill_id]

        skill = self.load(skill_id, use_cache=use_cache)
        if skill is None:
            return None

        if not skill.code:
            logger.error(f"Skill {skill_id} has no code (file missing or empty: {skill.code_path})")
            return None

        try:
            compiled = compile(skill.code, skill.code_path or f"<skill:{skill_id}>", "exec")
            self._code_cache[skill_id] = compiled
            return compiled
        except SyntaxError as e:
            logger.error(f"Failed to compile skill {skill_id} from {skill.code_path}: {e}")
            return None

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_active(self) -> list[Skill]:
        """Return all skills with ``Active`` status (metadata only)."""
        return self.store.list_by_status(SkillStatus.ACTIVE)

    def list_seeds(self) -> list[Skill]:
        """Return all skills with ``Seed`` status (metadata only)."""
        return self.store.list_by_status(SkillStatus.SEED)

    def list_all(self) -> list[Skill]:
        """Return all skills in the store (metadata only)."""
        return self.store.list_all()

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def invalidate(self, skill_id: str) -> None:
        """Remove a skill from both caches.

        Call this after updating a skill's code file or metadata to ensure
        the next ``load()`` fetches fresh data.
        """
        self._cache.pop(skill_id, None)
        self._code_cache.pop(skill_id, None)
        logger.debug(f"Invalidated cache for skill {skill_id}")

    def clear_cache(self) -> None:
        """Clear all cached skills and compiled code."""
        self._cache.clear()
        self._code_cache.clear()
        logger.debug("Cleared skill loader cache")

    def is_cached(self, skill_id: str) -> bool:
        """Return ``True`` if the skill is currently in the metadata cache."""
        return skill_id in self._cache

    # ------------------------------------------------------------------
    # Code file management
    # ------------------------------------------------------------------

    def reload_code(self, skill_id: str) -> str | None:
        """Force-reload code from the file, bypassing cache.

        Useful after editing a skill's code file on disk.

        Returns:
            The code string, or ``None`` if the skill or file is not found.
        """
        self._code_cache.pop(skill_id, None)

        skill = self.store.get(skill_id)
        if skill is None:
            return None

        self._cache[skill_id] = skill
        return skill.code if skill.code else None