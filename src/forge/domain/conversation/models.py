"""Domain values for the first user-input interaction."""

from dataclasses import dataclass


@dataclass(frozen=True)
class InitialInput:
    text: str


@dataclass(frozen=True)
class ModelReply:
    text: str
    model: str
