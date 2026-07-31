"""Stable public error contract for L1 memory operations."""

from __future__ import annotations

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
