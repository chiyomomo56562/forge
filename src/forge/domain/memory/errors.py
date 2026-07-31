"""L0/L1 메모리 하위 시스템의 공개 오류 계약.

최종 수정일: 2026-07-31
"""

from __future__ import annotations

from enum import StrEnum


class RetryDisposition(StrEnum):
    REPAIR_INPUT = "repair_input"
    RETRY_LATER = "retry_later"
    DO_NOT_RETRY = "do_not_retry"


class MemoryOperationError(Exception):
    """안정 오류 code와 안전한 외부 메시지를 제공하는 메모리 오류 기반 타입."""

    retry_disposition: RetryDisposition

    def __init__(self, *, code: str, safe_message: str) -> None:
        """오류의 복구 코드와 민감정보 없는 메시지를 초기화한다.

        Args:
            code: 호출자의 retry/repair 결정을 위한 안정 코드.
            safe_message: 외부에 노출해도 되는 오류 설명.

        최종 수정일: 2026-07-31
        """
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
