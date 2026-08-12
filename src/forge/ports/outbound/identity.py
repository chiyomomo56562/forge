"""L5 read-only identity lookup boundary."""

from typing import Protocol

from forge.domain.identity import AgentIdentity, Capability


class IdentityRepository(Protocol):
    def load_identity(self) -> AgentIdentity: ...
    def capability_for(self, task_category: str) -> Capability: ...
