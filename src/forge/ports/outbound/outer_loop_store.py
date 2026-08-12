"""Atomic persistence boundary for Outer Loop state."""

from typing import Protocol

from forge.domain.outer_loop import L2Knowledge, OuterLoopCheckpoint, PatternCandidate


class OuterLoopStore(Protocol):
    def load_candidates(self) -> list[PatternCandidate]: ...
    def load_knowledge(self) -> list[L2Knowledge]: ...
    def load_checkpoint(self) -> OuterLoopCheckpoint: ...
    def save(
        self,
        *,
        candidates: list[PatternCandidate],
        knowledge: list[L2Knowledge],
        checkpoint: OuterLoopCheckpoint,
    ) -> None: ...
