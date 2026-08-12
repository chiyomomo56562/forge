"""Read-only L5 identity values."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentIdentity:
    name: str
    identity_type: str
    autonomy_level: str
    version: int

    def __post_init__(self) -> None:
        if not all((self.name.strip(), self.identity_type.strip(), self.autonomy_level.strip())):
            raise ValueError("Identity fields are required.")


@dataclass(frozen=True)
class Capability:
    category: str
    label: str
    success_rate: float
    confidence: float
    effort_estimate: float
    total_attempts: int

    def __post_init__(self) -> None:
        if not self.category.strip() or not self.label.strip() or self.total_attempts < 0:
            raise ValueError("Capability fields are invalid.")
        if not all(
            0.0 <= value <= 1.0
            for value in (self.success_rate, self.confidence, self.effort_estimate)
        ):
            raise ValueError("Capability scores must be between 0 and 1.")
