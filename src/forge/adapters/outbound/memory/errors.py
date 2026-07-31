"""Adapter-owned L1 memory errors and internal index control signals."""

from forge.domain.memory.errors import (
    MemoryInfrastructureError,
    MemoryIntegrityError,
    RetryableMemoryOperationError,
)

__all__ = [
    "EpisodeIndexUnavailableError",
    "EpisodeIntegrityError",
    "MemoryInfrastructureError",
    "RetryableMemoryOperationError",
]


class EpisodeIntegrityError(MemoryIntegrityError):
    """L1 repository가 정규화해 노출하는 정본 무결성 오류."""


class EpisodeIndexUnavailableError(Exception):
    """Internal adapter signal; never escapes the repository boundary."""

    def __init__(self, *, code: str) -> None:
        super().__init__(code)
        self.code = code
