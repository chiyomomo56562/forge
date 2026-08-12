"""Read-only constitutional decisions used by runtime loops."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CibDecision:
    allowed: bool
    reason_code: str


@dataclass(frozen=True)
class ConstitutionPolicy:
    version: int
    cib_threshold: float
    sensitive_patterns: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version <= 0 or not 0.0 <= self.cib_threshold <= 1.0:
            raise ValueError("Constitution policy is invalid.")
