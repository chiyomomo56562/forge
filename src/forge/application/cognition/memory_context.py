"""Selective, policy-filtered L1/L2 context construction for cognition."""

from __future__ import annotations

from forge.application.memory import MemoryManager
from forge.domain.cognition import RetrievedMemoryContext


class MemoryContextBuilder:
    def __init__(
        self,
        manager: MemoryManager,
    ) -> None:
        self._manager = manager

    def build(self, *, task_request: str, task_category: str) -> RetrievedMemoryContext:
        return self._manager.build_context(query=task_request, task_category=task_category)
