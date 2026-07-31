"""Adapter-owned L1 memory errors and internal index control signals."""

from forge.domain.memory.errors import MemoryOperationError, RetryDisposition


class EpisodeIntegrityError(MemoryOperationError):
    retry_disposition = RetryDisposition.DO_NOT_RETRY


class RetryableMemoryOperationError(MemoryOperationError):
    retry_disposition = RetryDisposition.RETRY_LATER


class MemoryInfrastructureError(MemoryOperationError):
    retry_disposition = RetryDisposition.DO_NOT_RETRY


class EpisodeIndexUnavailableError(Exception):
    """Internal adapter signal; never escapes the repository boundary."""

    def __init__(self, *, code: str) -> None:
        super().__init__(code)
        self.code = code
