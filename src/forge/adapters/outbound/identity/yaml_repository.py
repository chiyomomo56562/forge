"""Read-only YAML adapter for L5 identity and capabilities."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from forge.domain.identity import AgentIdentity, Capability


class YamlIdentityRepository:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def load_identity(self) -> AgentIdentity:
        identity = self._load("identity.yml")
        return AgentIdentity(
            name=str(identity["name"]),
            identity_type=str(identity["type"]),
            autonomy_level=str(identity["autonomy_level"]["current"]),
            version=int(identity["version"]),
        )

    def capability_for(self, task_category: str) -> Capability:
        capabilities = self._load("capabilities.yml")
        category = next(
            (item for item in capabilities["categories"] if item["id"] == task_category), None
        )
        if category is None:
            defaults = capabilities["unknown_category"]
            category = {
                "id": task_category,
                "label": defaults["label"],
                "success_rate": defaults["default_success_rate"],
                "confidence": defaults["default_confidence"],
                "effort_estimate": defaults["default_effort"],
                "total_attempts": 0,
            }
        return Capability(
            category=str(category["id"]),
            label=str(category["label"]),
            success_rate=float(category["success_rate"]),
            confidence=float(category["confidence"]),
            effort_estimate=float(category["effort_estimate"]),
            total_attempts=int(category["total_attempts"]),
        )

    def _load(self, name: str) -> dict[str, Any]:
        with (self._root / name).open(encoding="utf-8") as source:
            return yaml.safe_load(source) or {}
