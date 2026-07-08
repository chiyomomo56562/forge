"""ID generation utilities for the Forge agent framework.

Provides deterministic and UUID-based ID generators for episodes, skills,
reflections, plans, evaluations, sessions, and generic records.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from functools import lru_cache


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def generate_episode_id() -> str:
    """Generate a unique episode ID.

    Format: ``ep_YYYYMMDD_HHMMSS_<8-char-uuid>``

    Example: ``ep_20260703_143052_a1b2c3d4``
    """
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"ep_{ts}_{short}"


def generate_skill_id(name: str) -> str:
    """Generate a skill ID from a human-readable name.

    Format: ``<slugified-name>_<8-char-uuid>``

    Example: ``web_search_utility_a1b2c3d4``
    """
    slug = name.lower().strip().replace(" ", "_").replace("-", "_").replace(".", "_")
    # Collapse consecutive underscores
    while "__" in slug:
        slug = slug.replace("__", "_")
    # Remove remaining non-alphanumeric characters
    slug = "".join(c for c in slug if c.isalnum() or c == "_")
    short = uuid.uuid4().hex[:8]
    return f"{slug}_{short}"


def generate_reflection_id(episode_id: str) -> str:
    """Generate a reflection ID linked to an episode.

    Format: ``refl_<episode_id_without_prefix>``
    """
    return f"refl_{episode_id}"


def generate_plan_id() -> str:
    """Generate a unique plan ID.

    Format: ``plan_YYYYMMDD_HHMMSS_<8-char-uuid>``
    """
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"plan_{ts}_{short}"


def generate_eval_id() -> str:
    """Generate a unique evaluation ID.

    Format: ``eval_YYYYMMDD_HHMMSS_<8-char-uuid>``
    """
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"eval_{ts}_{short}"


def generate_session_id() -> str:
    """Generate a unique session ID.

    Format: ``sess_YYYYMMDD_HHMMSS_<8-char-uuid>``
    """
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"sess_{ts}_{short}"


def generate_record_id(prefix: str = "rec") -> str:
    """Generate a generic record ID with a custom prefix.

    Format: ``<prefix>_<uuid4-hex>``
    """
    return f"{prefix}_{uuid.uuid4().hex}"


@lru_cache(maxsize=1)
def generate_run_id() -> str:
    """Generate a run ID (cached per process — one run per process invocation).

    Format: ``run_YYYYMMDD_HHMMSS_<8-char-uuid>``
    """
    ts = _utc_now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    return f"run_{ts}_{short}"