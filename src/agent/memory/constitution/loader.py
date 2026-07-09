"""Constitution YAML loader for L4.

Loads and merges the four constitution YAML files into a single
:class:`Constitution` model:

    - ``base.yml``             — principles, K-Scenarios, 3-layer structure
    - ``safety.yml``            — CIB thresholds, prohibited actions, safety boundaries
    - ``interaction_policy.yml`` — confirmation rules, response rules, delegation
    - ``tool_policy.yml``       — tool classification, audit config

The loader also extracts CIB thresholds from ``safety.yml`` and applies
them to the :class:`Constitution` model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..schemas import (
    Constitution,
    ConstitutionLayer,
    KScenario,
    Principle,
)
from ...utils.logging import get_logger

logger = get_logger("agent.memory.constitution.loader")

# ---------------------------------------------------------------------------
# Default file names
# ---------------------------------------------------------------------------

_DEFAULT_FILES = {
    "base": "base.yml",
    "safety": "safety.yml",
    "interaction_policy": "interaction_policy.yml",
    "tool_policy": "tool_policy.yml",
}


class ConstitutionLoader:
    """Load constitution YAML files and build a :class:`Constitution` model.

    Args:
        constitution_dir: Path to the directory containing the YAML files.
        files: Dict mapping logical names to file names. Defaults to
            :data:`_DEFAULT_FILES`.
    """

    def __init__(
        self,
        constitution_dir: str = "constitution",
        files: dict[str, str] | None = None,
    ):
        self.constitution_dir = Path(constitution_dir)
        self.files = files or dict(_DEFAULT_FILES)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Constitution:
        """Load all YAML files and return a merged :class:`Constitution`.

        Raises:
            FileNotFoundError: If ``base.yml`` is missing.
        """
        base_data = self._load_yaml("base")
        safety_data = self._load_yaml("safety")
        interaction_data = self._load_yaml("interaction_policy")
        tool_data = self._load_yaml("tool_policy")

        # Build Constitution from base.yml
        constitution = Constitution(
            version=base_data.get("version", 1),
            layers=base_data.get("layers", {}),
            principles=self._parse_principles(base_data.get("principles", [])),
            k_scenarios=self._parse_k_scenarios(base_data.get("k_scenarios", [])),
        )

        # Apply CIB thresholds from safety.yml
        cib = safety_data.get("cib", {})
        constitution.cib_threshold = cib.get("threshold", 0.95)
        constitution.cib_emergency_threshold = cib.get("emergency_threshold", 0.97)

        # Store extra data for downstream use
        self.safety_data = safety_data
        self.interaction_data = interaction_data
        self.tool_data = tool_data

        logger.info(
            f"Loaded constitution v{constitution.version}: "
            f"{len(constitution.principles)} principles, "
            f"{len(constitution.k_scenarios)} K-Scenarios, "
            f"CIB threshold={constitution.cib_threshold}"
        )
        return constitution

    def load_safety_config(self) -> dict[str, Any]:
        """Return the raw safety.yml data (prohibited actions, boundaries)."""
        return self._load_yaml("safety")

    def load_interaction_config(self) -> dict[str, Any]:
        """Return the raw interaction_policy.yml data."""
        return self._load_yaml("interaction_policy")

    def load_tool_config(self) -> dict[str, Any]:
        """Return the raw tool_policy.yml data."""
        return self._load_yaml("tool_policy")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_yaml(self, name: str) -> dict[str, Any]:
        """Load a single YAML file by logical name.

        Returns an empty dict if the file doesn't exist (except for
        ``base`` which is required).
        """
        filename = self.files.get(name, f"{name}.yml")
        path = self.constitution_dir / filename
        if not path.exists():
            if name == "base":
                raise FileNotFoundError(f"Required constitution file not found: {path}")
            logger.warning(f"Constitution file not found: {path}")
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _parse_principles(items: list[dict[str, Any]]) -> list[Principle]:
        """Convert raw YAML principle dicts to :class:`Principle` models."""
        principles: list[Principle] = []
        for item in items:
            layer_str = item.get("layer", "principle")
            try:
                layer = ConstitutionLayer(layer_str)
            except ValueError:
                layer = ConstitutionLayer.PRINCIPLE
            principles.append(Principle(
                id=item["id"],
                rule=item.get("rule", ""),
                layer=layer,
                weight=item.get("weight", 1.0),
            ))
        return principles

    @staticmethod
    def _parse_k_scenarios(items: list[dict[str, Any]]) -> list[KScenario]:
        """Convert raw YAML K-Scenario dicts to :class:`KScenario` models."""
        scenarios: list[KScenario] = []
        for item in items:
            scenarios.append(KScenario(
                id=item["id"],
                principle=item.get("principle", ""),
                description=item.get("description", ""),
                input=str(item.get("input", "")),
                expected_behavior=item.get("expected_behavior", ""),
                violation_example=item.get("violation_example", ""),
                direction_function=item.get("direction_function", ""),
            ))
        return scenarios