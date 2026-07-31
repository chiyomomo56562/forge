"""Public error contract for the memory subsystem."""

from enum import StrEnum


class RetryDisposition(StrEnum):
    REPAIR_INPUT = "repair_input"
    RETRY_LATER = "retry_later"
    DO_NOT_RETRY = "do_not_retry"


class MemoryOperationError(Exception):
    retry_disposition: RetryDisposition

    def __init__(self, *, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class MemoryValidationError(MemoryOperationError, ValueError):
    retry_disposition = RetryDisposition.REPAIR_INPUT


class MemoryIntegrityError(MemoryOperationError):
    retry_disposition = RetryDisposition.DO_NOT_RETRY


class RetryableMemoryOperationError(MemoryOperationError):
    retry_disposition = RetryDisposition.RETRY_LATER


class MemoryInfrastructureError(MemoryOperationError):
    retry_disposition = RetryDisposition.DO_NOT_RETRY
