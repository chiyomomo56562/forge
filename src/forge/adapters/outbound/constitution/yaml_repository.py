"""Read-only YAML adapter for the L4 constitution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from forge.domain.constitution import CibDecision, ConstitutionPolicy, ToolPolicyDecision
from forge.domain.memory import CibEvaluationStatus, Episode


class YamlConstitutionRepository:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def load_policy(self) -> ConstitutionPolicy:
        safety = self._load("safety.yml")
        cib = safety.get("cib", {})
        memory = safety.get("safety_boundaries", {}).get("memory", {})
        tool_policy = self._load("tool_policy.yml")
        classes = tool_policy.get("tool_classes", {})

        def tool_ids(class_name: str) -> tuple[str, ...]:
            entries = classes.get(class_name, [])
            return tuple(
                str(item["id"]) for item in entries if isinstance(item, dict) and "id" in item
            )

        return ConstitutionPolicy(
            version=int(safety["version"]),
            cib_threshold=float(cib["threshold"]),
            sensitive_patterns=tuple(str(item) for item in memory.get("sensitive_patterns", ())),
            autonomous_tool_ids=tool_ids("autonomous"),
            confirmation_required_tool_ids=tool_ids("confirmation_required"),
            forbidden_tool_ids=tool_ids("forbidden"),
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
        decision = self.evaluate_memory_text(content)
        return decision if not decision.allowed else CibDecision(True, "cib.passed")

    def evaluate_memory_text(self, content: str) -> CibDecision:
        if any(re.search(pattern, content) for pattern in self.load_policy().sensitive_patterns):
            return CibDecision(False, "memory.sensitive_content")
        return CibDecision(True, "memory.safe")

    def evaluate_tool(self, policy_id: str) -> ToolPolicyDecision:
        return self.load_policy().evaluate_tool(policy_id)

    def _load(self, name: str) -> dict[str, Any]:
        with (self._root / name).open(encoding="utf-8") as source:
            return yaml.safe_load(source) or {}
