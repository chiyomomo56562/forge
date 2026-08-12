"""L4 read-only enforcement boundary."""

from typing import Protocol

from forge.domain.constitution import CibDecision, ConstitutionPolicy
from forge.domain.memory import Episode


class ConstitutionRepository(Protocol):
    def load_policy(self) -> ConstitutionPolicy: ...
    def evaluate_l2_evidence(self, episode: Episode) -> CibDecision: ...
