"""Read-only YAML adapter for the L4 constitution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from forge.domain.constitution import CibDecision, ConstitutionPolicy
from forge.domain.memory import CibEvaluationStatus, Episode


class YamlConstitutionRepository:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def load_policy(self) -> ConstitutionPolicy:
        safety = self._load("safety.yml")
        cib = safety.get("cib", {})
        memory = safety.get("safety_boundaries", {}).get("memory", {})
        return ConstitutionPolicy(
            version=int(safety["version"]),
            cib_threshold=float(cib["threshold"]),
            sensitive_patterns=tuple(str(item) for item in memory.get("sensitive_patterns", ())),
        )

    def evaluate_l2_evidence(self, episode: Episode) -> CibDecision:
        policy = self.load_policy()
        if episode.evaluation.cib_evaluation_status is not CibEvaluationStatus.PASSED:
            return CibDecision(False, "cib.not_passed")
        if (episode.evaluation.cib_score or 0.0) < policy.cib_threshold:
            return CibDecision(False, "cib.below_threshold")
        content = "\n".join(
            (
                episode.task_request,
                episode.reflection.what_worked,
                episode.reflection.what_failed,
                episode.reflection.next_hint,
                episode.reflection.causal_condition,
            )
        )
        if any(re.search(pattern, content) for pattern in policy.sensitive_patterns):
            return CibDecision(False, "memory.sensitive_content")
        return CibDecision(True, "cib.passed")

    def _load(self, name: str) -> dict[str, Any]:
        with (self._root / name).open(encoding="utf-8") as source:
            return yaml.safe_load(source) or {}
