"""Values for cross-layer memory reads and reflection routing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from forge.domain.identity import AgentIdentity, Capability
from forge.domain.memory.models import EpisodeSearchHit
from forge.domain.outer_loop import L2Knowledge


class ReflectionRoute(StrEnum):
    L2_GENERALIZATION = "l2_generalization"
    L3_PROCEDURE_PENDING = "l3_procedure_pending"
    L1_ONLY = "l1_only"


@dataclass(frozen=True)
class MemoryReadResult:
    l1_hits: tuple[EpisodeSearchHit, ...]
    l2_knowledge: tuple[L2Knowledge, ...]
    identity: AgentIdentity
    capability: Capability


@dataclass(frozen=True)
class ReflectionRoutingDecision:
    route: ReflectionRoute
    reason: str
