"""메모리 하위 시스템의 공개 오류 계약.

최종 수정일: 2026-07-31
"""

from enum import StrEnum


class RetryDisposition(StrEnum):
    REPAIR_INPUT = "repair_input"
    RETRY_LATER = "retry_later"
    DO_NOT_RETRY = "do_not_retry"


class MemoryOperationError(Exception):
    """모든 공개 메모리 오류가 제공하는 안정 code와 안전 메시지의 기반 타입.

    최종 수정일: 2026-07-31
    """

    retry_disposition: RetryDisposition

    def __init__(self, *, code: str, safe_message: str) -> None:
        """오류 코드와 호출자에게 노출 가능한 메시지를 초기화한다.

        Args:
            code: programmatic retry/복구 판단에 쓰는 안정 오류 코드.
            safe_message: 비밀값·내부 원인을 제외한 외부 노출 메시지.

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
