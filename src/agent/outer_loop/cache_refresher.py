"""Step 3: Cache Refresh — 메모리 캐시 최신화.

Refreshes in-memory caches after the outer loop has aggregated new data.
This ensures that subsequent inner loop cycles use the most up-to-date
memory state.

Operations:
    - Reload L2 semantic graph from disk (if modified during consolidation)
    - Clear L1 episodic retriever cache (force re-embedding on next query)
    - Reload L3 skill loader cache (pick up any new/updated skills)
    - Refresh L4 constitution (reload YAML if changed)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..utils.logging import get_logger

logger = get_logger("agent.outer_loop.cache_refresher")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CacheRefreshResult:
    """Result of cache refresh operations.

    Attributes:
        l1_refreshed: Whether L1 (episodic) cache was refreshed.
        l2_refreshed: Whether L2 (semantic) graph was reloaded.
        l3_refreshed: Whether L3 (procedural) skill cache was refreshed.
        l4_refreshed: Whether L4 (constitution) was reloaded.
        errors: List of error messages (empty if all succeeded).
    """
    l1_refreshed: bool = False
    l2_refreshed: bool = False
    l3_refreshed: bool = False
    l4_refreshed: bool = False
    errors: list[str] = None  # type: ignore

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# ---------------------------------------------------------------------------
# Cache Refresher
# ---------------------------------------------------------------------------

class CacheRefresher:
    """Refresh in-memory caches across all memory layers.

    Args:
        memory_manager: A :class:`MemoryManager` instance (optional).
    """

    def __init__(self, memory_manager: Any | None = None):
        self.memory_manager = memory_manager

    def refresh(self) -> CacheRefreshResult:
        """Refresh all caches.

        Returns:
            :class:`CacheRefreshResult` with per-layer status.
        """
        result = CacheRefreshResult()

        if self.memory_manager is None:
            logger.info("No memory manager, cache refresh skipped")
            return result

        # L1 — Clear episodic retriever cache
        try:
            retriever = getattr(self.memory_manager, "episodic_retriever", None)
            if retriever is not None and hasattr(retriever, "clear_cache"):
                retriever.clear_cache()
            result.l1_refreshed = True
            logger.debug("L1 cache refreshed")
        except Exception as e:
            result.errors.append(f"L1: {e}")
            logger.warning(f"L1 cache refresh failed: {e}")

        # L2 — Reload semantic graph from disk
        try:
            graph_store = getattr(self.memory_manager, "graph_store", None)
            if graph_store is not None and hasattr(graph_store, "load"):
                graph_store.load()
            result.l2_refreshed = True
            logger.debug("L2 graph reloaded")
        except Exception as e:
            result.errors.append(f"L2: {e}")
            logger.warning(f"L2 graph reload failed: {e}")

        # L3 — Refresh skill loader cache
        try:
            skill_loader = getattr(self.memory_manager, "skill_loader", None)
            if skill_loader is not None and hasattr(skill_loader, "refresh"):
                skill_loader.refresh()
            result.l3_refreshed = True
            logger.debug("L3 skill cache refreshed")
        except Exception as e:
            result.errors.append(f"L3: {e}")
            logger.warning(f"L3 skill cache refresh failed: {e}")

        # L4 — Reload constitution (reset lazy cache)
        try:
            self.memory_manager._constitution = None  # force lazy reload
            result.l4_refreshed = True
            logger.debug("L4 constitution cache cleared for reload")
        except Exception as e:
            result.errors.append(f"L4: {e}")
            logger.warning(f"L4 constitution reload failed: {e}")

        logger.info(
            f"Cache refresh: L1={result.l1_refreshed}, L2={result.l2_refreshed}, "
            f"L3={result.l3_refreshed}, L4={result.l4_refreshed}, "
            f"errors={len(result.errors)}"
        )
        return result