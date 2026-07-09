"""Capability model for L5 Identity.

Manages task-category-level capability records: success rate, confidence,
effort estimate, and total attempts.  Supports EMA-based updates from
the outer loop and YAML-based initialization.

Capabilities are loaded from ``identity/capabilities.yml`` at startup and
updated dynamically as the agent accumulates experience.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..schemas import CapabilityRecord
from .identity_store import IdentityStore
from ...utils.logging import get_logger

logger = get_logger("agent.memory.identity.capability_model")

# Default capability for unknown categories
_DEFAULT_CAPABILITY = CapabilityRecord(
    id="unknown",
    label="알 수 없는 작업",
    success_rate=0.5,
    confidence=0.7,
    effort_estimate=0.5,
    total_attempts=0,
)


class CapabilityModel:
    """Manage task-category capabilities with EMA-based updates.

    Args:
        store: An :class:`IdentityStore` instance.
        yaml_path: Path to ``capabilities.yml`` for initialization.
        smoothing_factor: EMA smoothing factor (α). New value =
            (1-α) × existing + α × window_average.
        min_attempts: Minimum attempts before statistics are updated.
        window_size: Number of recent episodes for window average.
    """

    def __init__(
        self,
        store: IdentityStore | None = None,
        yaml_path: str = "identity/capabilities.yml",
        smoothing_factor: float = 0.3,
        min_attempts: int = 3,
        window_size: int = 50,
    ):
        self.store = store or IdentityStore()
        self.yaml_path = Path(yaml_path)
        self.smoothing_factor = smoothing_factor
        self.min_attempts = min_attempts
        self.window_size = window_size

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize_from_yaml(self) -> int:
        """Load capabilities from YAML and seed the database.

        Only inserts categories that don't already exist in the DB
        (does not overwrite existing records).

        Returns:
            Number of new capabilities inserted.
        """
        if not self.yaml_path.exists():
            logger.warning(f"Capabilities YAML not found: {self.yaml_path}")
            return 0

        with self.yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        categories = data.get("categories", [])
        inserted = 0

        for cat in categories:
            cap_id = cat.get("id", "")
            if not cap_id:
                continue
            # Skip if already exists
            if self.store.get_capability(cap_id) is not None:
                continue

            cap = CapabilityRecord(
                id=cap_id,
                label=cat.get("label", cap_id),
                success_rate=cat.get("success_rate", 0.5),
                confidence=cat.get("confidence", 0.7),
                effort_estimate=cat.get("effort_estimate", 0.5),
                total_attempts=cat.get("total_attempts", 0),
            )
            self.store.upsert_capability(cap)
            inserted += 1

        logger.info(f"Initialized {inserted} capabilities from {self.yaml_path}")
        return inserted

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get(self, category_id: str) -> CapabilityRecord:
        """Get capability for a category.

        If the category doesn't exist, returns a default capability
        and optionally registers it.

        Args:
            category_id: The task category ID.

        Returns:
            :class:`CapabilityRecord`.
        """
        cap = self.store.get_capability(category_id)
        if cap is not None:
            return cap

        # Return default and auto-register
        default = CapabilityRecord(
            id=category_id,
            label=_DEFAULT_CAPABILITY.label,
            success_rate=_DEFAULT_CAPABILITY.success_rate,
            confidence=_DEFAULT_CAPABILITY.confidence,
            effort_estimate=_DEFAULT_CAPABILITY.effort_estimate,
        )
        self.store.upsert_capability(default)
        logger.info(f"Auto-registered new capability: {category_id}")
        return default

    def list_all(self) -> list[CapabilityRecord]:
        """Return all registered capabilities."""
        return self.store.list_capabilities()

    # ------------------------------------------------------------------
    # Updating (outer loop)
    # ------------------------------------------------------------------

    def update_from_episode(
        self,
        category_id: str,
        actual_success: float,
        actual_effort: float | None = None,
    ) -> CapabilityRecord:
        """Update a capability from a single episode result.

        Uses EMA smoothing:
            new_success_rate = (1-α) × existing + α × actual
            new_confidence = (1-α) × existing + α × actual
            new_effort = (1-α) × existing + α × actual_effort

        Args:
            category_id: The task category.
            actual_success: Actual success score (0–1).
            actual_effort: Actual effort (optional).

        Returns:
            Updated :class:`CapabilityRecord`.
        """
        cap = self.get(category_id)
        alpha = self.smoothing_factor

        new_success = (1 - alpha) * cap.success_rate + alpha * actual_success
        new_confidence = (1 - alpha) * cap.confidence + alpha * actual_success
        new_attempts = cap.total_attempts + 1

        new_effort = cap.effort_estimate
        if actual_effort is not None:
            new_effort = (1 - alpha) * cap.effort_estimate + alpha * actual_effort

        updated = CapabilityRecord(
            id=cap.id,
            label=cap.label,
            success_rate=round(new_success, 4),
            confidence=round(new_confidence, 4),
            effort_estimate=round(new_effort, 4),
            total_attempts=new_attempts,
        )
        self.store.upsert_capability(updated)
        logger.debug(
            f"Updated capability {category_id}: "
            f"success={new_success:.4f}, confidence={new_confidence:.4f}, "
            f"attempts={new_attempts}"
        )
        return updated

    def update_batch(
        self,
        updates: list[tuple[str, float, float | None]],
    ) -> list[CapabilityRecord]:
        """Update multiple capabilities from episode results.

        Args:
            updates: List of ``(category_id, actual_success, actual_effort)`` tuples.

        Returns:
            List of updated :class:`CapabilityRecord` objects.
        """
        results: list[CapabilityRecord] = []
        for category_id, actual_success, actual_effort in updates:
            results.append(self.update_from_episode(category_id, actual_success, actual_effort))
        return results

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, category_id: str) -> dict[str, float]:
        """Return predicted success and effort for a category.

        Args:
            category_id: The task category.

        Returns:
            Dict with ``predicted_success``, ``confidence``, ``effort_estimate``.
        """
        cap = self.get(category_id)
        return {
            "predicted_success": cap.success_rate,
            "confidence": cap.confidence,
            "effort_estimate": cap.effort_estimate,
            "total_attempts": cap.total_attempts,
        }