"""Memory access policies for the Forge agent framework.

Defines read/write/delete permissions, retention periods, and archival rules
for each memory layer (L1–L5).  Enforced by the MemoryManager.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .schemas import MemoryLayer, SkillStatus


# ===========================================================================
# Permission types
# ===========================================================================

Permission = Literal["read", "write", "delete"]


# ===========================================================================
# Per-layer policies
# ===========================================================================

@dataclass
class LayerPolicy:
    """Access and retention policy for a single memory layer."""
    layer: MemoryLayer
    readable: bool = True
    writable: bool = True
    deletable: bool = False          # Default: no deletion (healthy forgetting via archival)
    requires_audit: bool = True      # Log all operations to audit trail
    retention_days: int = 0          # 0 = unlimited
    max_records: int = 0             # 0 = unlimited


# ===========================================================================
# Default policies per layer
# ===========================================================================

DEFAULT_POLICIES: dict[MemoryLayer, LayerPolicy] = {
    MemoryLayer.L1: LayerPolicy(
        layer=MemoryLayer.L1,
        readable=True,
        writable=True,
        deletable=False,            # Episodes are never hard-deleted; they age out
        requires_audit=True,
        retention_days=0,           # Unlimited (Chroma manages its own lifecycle)
        max_records=0,
    ),
    MemoryLayer.L2: LayerPolicy(
        layer=MemoryLayer.L2,
        readable=True,
        writable=True,
        deletable=True,             # Entities/relations can be pruned
        requires_audit=True,
        retention_days=0,
        max_records=0,
    ),
    MemoryLayer.L3: LayerPolicy(
        layer=MemoryLayer.L3,
        readable=True,
        writable=True,
        deletable=False,            # Skills are archived, not deleted
        requires_audit=True,
        retention_days=0,
        max_records=0,
    ),
    MemoryLayer.L4: LayerPolicy(
        layer=MemoryLayer.L4,
        readable=True,
        writable=False,             # Only meta-loop + HITL can modify
        deletable=False,            # Constitution is never deleted
        requires_audit=True,
        retention_days=0,
        max_records=0,
    ),
    MemoryLayer.L5: LayerPolicy(
        layer=MemoryLayer.L5,
        readable=True,
        writable=True,              # Outer loop updates statistics
        deletable=False,
        requires_audit=True,
        retention_days=0,
        max_records=0,
    ),
}


# ===========================================================================
# Skill lifecycle policy (L3)
# ===========================================================================

@dataclass
class SkillLifecyclePolicy:
    """Thresholds for skill state transitions (README: Seed → Active → Degrading → Archived)."""
    active_threshold: float = 0.9       # Seed → Active
    degrading_threshold: float = 0.5    # Active → Degrading
    archive_threshold: float = 0.2      # Degrading → Archived
    archive_idle_days: int = 30         # Auto-archive if unused for N days
    recovery_threshold: float = 0.7     # Degrading → Active (recovery)

    def determine_status(
        self,
        current_status: SkillStatus,
        success_rate: float,
        days_idle: int = 0,
    ) -> SkillStatus:
        """Determine the next skill status based on current state and metrics.

        Args:
            current_status: Current skill status.
            success_rate: Recent success rate (0.0–1.0).
            days_idle: Days since last execution.

        Returns:
            The new skill status (may be same as current).
        """
        if current_status == SkillStatus.SEED:
            if success_rate >= self.active_threshold:
                return SkillStatus.ACTIVE
            return SkillStatus.SEED

        if current_status == SkillStatus.ACTIVE:
            if success_rate < self.degrading_threshold:
                return SkillStatus.DEGRADING
            return SkillStatus.ACTIVE

        if current_status == SkillStatus.DEGRADING:
            if success_rate >= self.recovery_threshold:
                return SkillStatus.ACTIVE
            if success_rate < self.archive_threshold or days_idle >= self.archive_idle_days:
                return SkillStatus.ARCHIVED
            return SkillStatus.DEGRADING

        # Archived — stays archived unless meta-loop restores
        return SkillStatus.ARCHIVED


# ===========================================================================
# Dual-storage routing policy
# ===========================================================================

@dataclass
class DualStoragePolicy:
    """Routing policy for generalized hints (dual-storage strategy).

    - general hints      → L2 (semantic graph)
    - tool_specific hints → L3 (procedural DB reflection_hints)
    """
    general_target: MemoryLayer = MemoryLayer.L2
    tool_specific_target: MemoryLayer = MemoryLayer.L3

    def route(self, hint_type: str) -> MemoryLayer:
        """Return the target layer for a hint type."""
        if hint_type == "general":
            return self.general_target
        if hint_type == "tool_specific":
            return self.tool_specific_target
        raise ValueError(f"Unknown hint type: {hint_type}. Expected 'general' or 'tool_specific'.")


# ===========================================================================
# Sensitive data filtering policy
# ===========================================================================

@dataclass
class SensitiveDataPolicy:
    """Policy for filtering sensitive data before memory storage.

    Patterns from constitution/safety.yml.
    """
    patterns: list[str] = field(default_factory=lambda: [
        r"sk-[a-zA-Z0-9]{20,}",       # OpenAI API key
        r"ghp_[a-zA-Z0-9]{36}",       # GitHub token
        r"AKIA[A-Z0-9]{16}",          # AWS key
    ])

    def check(self, text: str) -> bool:
        """Return True if *text* contains sensitive data (should be blocked)."""
        import re
        for pattern in self.patterns:
            if re.search(pattern, text):
                return True
        return False


# ===========================================================================
# Combined policy facade
# ===========================================================================

class MemoryPolicy:
    """Combined memory policy facade for all layers.

    Usage::

        policy = MemoryPolicy()
        if policy.can_write(MemoryLayer.L1):
            ...
    """

    def __init__(
        self,
        layer_policies: dict[MemoryLayer, LayerPolicy] | None = None,
        skill_lifecycle: SkillLifecyclePolicy | None = None,
        dual_storage: DualStoragePolicy | None = None,
        sensitive_data: SensitiveDataPolicy | None = None,
    ):
        self.layers = layer_policies or dict(DEFAULT_POLICIES)
        self.skill_lifecycle = skill_lifecycle or SkillLifecyclePolicy()
        self.dual_storage = dual_storage or DualStoragePolicy()
        self.sensitive_data = sensitive_data or SensitiveDataPolicy()

    # --- Permission checks ---
    def can_read(self, layer: MemoryLayer) -> bool:
        return self.layers[layer].readable

    def can_write(self, layer: MemoryLayer) -> bool:
        return self.layers[layer].writable

    def can_delete(self, layer: MemoryLayer) -> bool:
        return self.layers[layer].deletable

    def requires_audit(self, layer: MemoryLayer) -> bool:
        return self.layers[layer].requires_audit

    # --- Sensitive data ---
    def is_sensitive(self, text: str) -> bool:
        """Check if text contains sensitive data that should not be stored."""
        return self.sensitive_data.check(text)

    # --- Skill lifecycle ---
    def get_skill_status(
        self,
        current: SkillStatus,
        success_rate: float,
        days_idle: int = 0,
    ) -> SkillStatus:
        return self.skill_lifecycle.determine_status(current, success_rate, days_idle)

    # --- Dual storage routing ---
    def route_hint(self, hint_type: str) -> MemoryLayer:
        return self.dual_storage.route(hint_type)