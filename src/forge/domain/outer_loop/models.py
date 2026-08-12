"""Immutable values used to consolidate eligible L1 episodes into L2 knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class L2KnowledgeStatus(StrEnum):
    ACTIVE = "active"
    WEAKENED = "weakened"
    RETIRED = "retired"


@dataclass(frozen=True)
class PatternCandidate:
    candidate_id: str
    signature: str
    statement: str
    condition: str
    support_episode_ids: tuple[str, ...] = ()
    counterexample_episode_ids: tuple[str, ...] = ()
    knowledge_id: str | None = None

    def __post_init__(self) -> None:
        if not all((self.candidate_id.strip(), self.signature.strip(), self.statement.strip())):
            raise ValueError("Pattern candidate fields are required.")
        if set(self.support_episode_ids) & set(self.counterexample_episode_ids):
            raise ValueError("Pattern evidence cannot be both support and counterexample.")

    @property
    def evidence_count(self) -> int:
        return len(self.support_episode_ids) + len(self.counterexample_episode_ids)

    @property
    def confidence(self) -> float:
        return len(self.support_episode_ids) / self.evidence_count if self.evidence_count else 0.0


@dataclass(frozen=True)
class L2Knowledge:
    knowledge_id: str
    candidate_id: str
    statement: str
    condition: str
    confidence: float
    status: L2KnowledgeStatus
    support_episode_ids: tuple[str, ...]
    counterexample_episode_ids: tuple[str, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        if not all((self.knowledge_id.strip(), self.candidate_id.strip(), self.statement.strip())):
            raise ValueError("L2 knowledge fields are required.")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("L2 confidence must be between 0 and 1.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("L2 updated_at must be timezone-aware.")


@dataclass(frozen=True)
class OuterLoopCheckpoint:
    watermark: datetime | None = None
    processed_episode_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.watermark is not None and (
            self.watermark.tzinfo is None or self.watermark.utcoffset() is None
        ):
            raise ValueError("Outer Loop watermark must be timezone-aware.")


@dataclass(frozen=True)
class OuterLoopResult:
    processed_episode_ids: tuple[str, ...]
    promoted_knowledge_ids: tuple[str, ...]
    updated_knowledge_ids: tuple[str, ...]
    checkpoint: OuterLoopCheckpoint
