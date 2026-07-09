"""Identity updater for L5.

Two update modes (per README design principles):

    1. **update_statistics()** — Outer loop: incremental update of
       self-model calibration and capability records from episode data.
       Called after each episode evaluation.

    2. **redesign_identity()** — Meta loop: fundamental redesign of
       identity configuration (autonomy level, identity core, self-model
       bias).  Requires HITL approval before execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..schemas import UpdaterSource
from .identity_store import IdentityStore
from .self_model import SelfModel
from .capability_model import CapabilityModel
from ...utils.logging import get_logger
from ...utils.time import iso_now

logger = get_logger("agent.memory.identity.updater")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class StatisticsUpdateResult:
    """Result of an outer-loop statistics update."""
    self_model_record_id: str | None = None
    capability_updated: bool = False
    calibration_error: float | None = None
    calibration_direction: str | None = None


@dataclass
class RedesignResult:
    """Result of a meta-loop identity redesign."""
    approved: bool = False
    changes: dict[str, Any] = None  # type: ignore
    reason: str = ""


# ---------------------------------------------------------------------------
# Updater
# ---------------------------------------------------------------------------

class IdentityUpdater:
    """Update L5 identity data in two modes.

    Args:
        store: An :class:`IdentityStore` instance.
        self_model: A :class:`SelfModel` instance.
        capability_model: A :class:`CapabilityModel` instance.
        identity_yaml_path: Path to ``identity/identity.yml``.
    """

    def __init__(
        self,
        store: IdentityStore | None = None,
        self_model: SelfModel | None = None,
        capability_model: CapabilityModel | None = None,
        identity_yaml_path: str = "identity/identity.yml",
    ):
        self.store = store or IdentityStore()
        self.self_model = self_model or SelfModel(store=self.store)
        self.capability_model = capability_model or CapabilityModel(store=self.store)
        self.identity_yaml_path = Path(identity_yaml_path)

    # ------------------------------------------------------------------
    # Mode 1: Outer loop — statistics update
    # ------------------------------------------------------------------

    def update_statistics(
        self,
        episode_id: str,
        task_category: str,
        predicted_success: float,
        actual_success: float,
        predicted_effort: float | None = None,
        actual_effort: float | None = None,
    ) -> StatisticsUpdateResult:
        """Update self-model and capabilities from an episode result.

        Called by the outer loop after each episode evaluation.

        Args:
            episode_id: The episode ID.
            task_category: Task category (e.g. 'coding').
            predicted_success: Predicted success score (0–1).
            actual_success: Actual success score (0–1).
            predicted_effort: Predicted effort (optional).
            actual_effort: Actual effort (optional).

        Returns:
            :class:`StatisticsUpdateResult` with update details.
        """
        # 1. Record self-model calibration
        sm_record = self.self_model.record(
            episode_id=episode_id,
            task_category=task_category,
            predicted_success=predicted_success,
            actual_success=actual_success,
            predicted_effort=predicted_effort,
            actual_effort=actual_effort,
            updated_by=UpdaterSource.OUTER_LOOP,
        )

        # 2. Update capability record
        self.capability_model.update_from_episode(
            category_id=task_category,
            actual_success=actual_success,
            actual_effort=actual_effort,
        )

        logger.info(
            f"Statistics updated: episode={episode_id}, category={task_category}, "
            f"cal_error={sm_record.calibration_error:.4f}, "
            f"direction={sm_record.calibration_direction.value}"
        )

        return StatisticsUpdateResult(
            self_model_record_id=sm_record.record_id,
            capability_updated=True,
            calibration_error=sm_record.calibration_error,
            calibration_direction=sm_record.calibration_direction.value,
        )

    # ------------------------------------------------------------------
    # Mode 2: Meta loop — fundamental redesign
    # ------------------------------------------------------------------

    def redesign_identity(
        self,
        new_config: dict[str, Any],
        hitl_approved: bool = False,
    ) -> RedesignResult:
        """Fundamentally redesign the agent's identity configuration.

        Called by the meta loop.  Requires HITL approval.

        Args:
            new_config: New identity configuration dict. May include:
                - ``autonomy_level``: New autonomy level (L0–L5)
                - ``identity_core``: New values, boundaries, self_description
                - ``initial_bias``: New prediction bias settings
                - ``calibration``: New calibration thresholds
            hitl_approved: Whether human approval has been granted.

        Returns:
            :class:`RedesignResult` with approval status and changes.
        """
        if not hitl_approved:
            result = RedesignResult(
                approved=False,
                changes={},
                reason="HITL BLOCKED: Identity redesign requires human approval. No approval granted.",
            )
            logger.warning(result.reason)
            return result

        changes: dict[str, Any] = {}

        # Load current identity YAML
        current = self._load_identity_yaml()

        # Apply changes
        if "autonomy_level" in new_config:
            old_level = current.get("autonomy_level", {}).get("current", "unknown")
            new_level = new_config["autonomy_level"].get("current", old_level)
            changes["autonomy_level"] = {"old": old_level, "new": new_level}
            current["autonomy_level"] = new_config["autonomy_level"]

        if "identity_core" in new_config:
            changes["identity_core"] = "updated"
            current["identity_core"] = new_config["identity_core"]

        if "initial_bias" in new_config:
            changes["initial_bias"] = "updated"
            current.setdefault("initial_bias", {})
            current["initial_bias"].update(new_config["initial_bias"])

        if "calibration" in new_config:
            changes["calibration"] = "updated"
            current.setdefault("calibration", {})
            current["calibration"].update(new_config["calibration"])

        # Update version history
        history = current.get("version_history", [])
        version = len(history) + 1
        history.append({
            "version": version,
            "date": iso_now()[:10],
            "change": f"Meta-loop redesign: {', '.join(changes.keys())}",
            "approved_by": "human",
        })
        current["version_history"] = history
        current["version"] = version

        # Save updated YAML
        self._save_identity_yaml(current)

        result = RedesignResult(
            approved=True,
            changes=changes,
            reason=f"Identity redesigned (v{version}): {list(changes.keys())}",
        )
        logger.info(result.reason)
        return result

    # ------------------------------------------------------------------
    # YAML I/O
    # ------------------------------------------------------------------

    def _load_identity_yaml(self) -> dict[str, Any]:
        """Load the current identity YAML."""
        if not self.identity_yaml_path.exists():
            return {}
        with self.identity_yaml_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _save_identity_yaml(self, data: dict[str, Any]) -> None:
        """Save the identity YAML."""
        self.identity_yaml_path.parent.mkdir(parents=True, exist_ok=True)
        with self.identity_yaml_path.open("w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        logger.info(f"Saved identity YAML to {self.identity_yaml_path}")

    # ------------------------------------------------------------------
    # Convenience: get current identity state
    # ------------------------------------------------------------------

    def get_identity_state(self) -> dict[str, Any]:
        """Return the current identity state summary.

        Combines YAML config, self-model stats, and capabilities.
        """
        yaml_data = self._load_identity_yaml()
        cal_summary = self.self_model.get_calibration_summary()
        capabilities = self.capability_model.list_all()

        return {
            "name": yaml_data.get("name", ""),
            "autonomy_level": yaml_data.get("autonomy_level", {}),
            "identity_core": yaml_data.get("identity_core", {}),
            "self_model": cal_summary,
            "capabilities": [c.model_dump() for c in capabilities],
            "version": yaml_data.get("version", 1),
        }