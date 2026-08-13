"""Single-file atomic JSON persistence for the initial Outer Loop slice."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from forge.domain.outer_loop import (
    GrowthObservation,
    L2Knowledge,
    L2KnowledgeStatus,
    OuterLoopCheckpoint,
    PatternCandidate,
)


class JsonOuterLoopStore:
    """Keeps candidates, L2 knowledge, and the watermark in one atomic document."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_candidates(self) -> list[PatternCandidate]:
        return [self._candidate(item) for item in self._load()["candidates"]]

    def load_knowledge(self) -> list[L2Knowledge]:
        return [self._knowledge(item) for item in self._load()["knowledge"]]

    def load_checkpoint(self) -> OuterLoopCheckpoint:
        item = self._load()["checkpoint"]
        return OuterLoopCheckpoint(
            watermark=datetime.fromisoformat(item["watermark"]) if item["watermark"] else None,
            processed_episode_ids=tuple(item["processed_episode_ids"]),
            growth_observations=tuple(
                GrowthObservation(
                    observed_at=datetime.fromisoformat(str(observation["observed_at"])),
                    episode_count=int(observation["episode_count"]),
                    consolidation_coherence=float(observation["consolidation_coherence"]),
                )
                for observation in item.get("growth_observations", [])
            ),
        )

    def save(
        self,
        *,
        candidates: list[PatternCandidate],
        knowledge: list[L2Knowledge],
        checkpoint: OuterLoopCheckpoint,
    ) -> None:
        payload = {
            "version": 1,
            "candidates": [asdict(item) for item in candidates],
            "knowledge": [
                {
                    **asdict(item),
                    "status": item.status.value,
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in knowledge
            ],
            "checkpoint": {
                "watermark": checkpoint.watermark.isoformat() if checkpoint.watermark else None,
                "processed_episode_ids": list(checkpoint.processed_episode_ids),
                "growth_observations": [
                    {
                        "observed_at": item.observed_at.isoformat(),
                        "episode_count": item.episode_count,
                        "consolidation_coherence": item.consolidation_coherence,
                    }
                    for item in checkpoint.growth_observations
                ],
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent, delete=False) as temp:
            json.dump(payload, temp, ensure_ascii=False, sort_keys=True)
            temp.write("\n")
            temp_path = Path(temp.name)
        temp_path.replace(self._path)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {
                "candidates": [],
                "knowledge": [],
                "checkpoint": {"watermark": None, "processed_episode_ids": []},
            }
        with self._path.open(encoding="utf-8") as source:
            return cast(dict[str, Any], json.load(source))

    @staticmethod
    def _candidate(item: dict[str, Any]) -> PatternCandidate:
        return PatternCandidate(
            candidate_id=str(item["candidate_id"]),
            signature=str(item["signature"]),
            statement=str(item["statement"]),
            condition=str(item["condition"]),
            support_episode_ids=tuple(item["support_episode_ids"]),
            counterexample_episode_ids=tuple(item["counterexample_episode_ids"]),
            knowledge_id=item["knowledge_id"],
            task_category=str(item.get("task_category", "general")),
        )

    @staticmethod
    def _knowledge(item: dict[str, Any]) -> L2Knowledge:
        return L2Knowledge(
            knowledge_id=str(item["knowledge_id"]),
            candidate_id=str(item["candidate_id"]),
            statement=str(item["statement"]),
            condition=str(item["condition"]),
            confidence=float(item["confidence"]),
            status=L2KnowledgeStatus(item["status"]),
            support_episode_ids=tuple(item["support_episode_ids"]),
            counterexample_episode_ids=tuple(item["counterexample_episode_ids"]),
            updated_at=datetime.fromisoformat(str(item["updated_at"])),
            task_category=str(item.get("task_category", "general")),
        )
