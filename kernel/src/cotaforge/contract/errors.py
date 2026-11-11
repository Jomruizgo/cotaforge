"""Adapter error model and retry policy (contract/errors.py)."""

from pydantic import BaseModel

from cotaforge.contract.enums import ErrorCode

# Codes that the executor may retry the same request for (recoverable=True).
_RECOVERABLE_CODES = frozenset(
    {
        ErrorCode.timeout,
        ErrorCode.geometry_failed,
        ErrorCode.rebuild_failed,
        ErrorCode.mate_failed,
        ErrorCode.internal,
    }
)

# Codes that must never be retried (recoverable=False).
_NON_RECOVERABLE_CODES = frozenset(
    {
        ErrorCode.not_supported,
        ErrorCode.invalid_request,
        ErrorCode.invalid_reference,
        ErrorCode.session_lost,
        ErrorCode.no_active_document,
        ErrorCode.degraded_session,
    }
)


def default_recoverable(code: ErrorCode) -> bool:
    """Return the default retry policy for an error code."""
    if code in _RECOVERABLE_CODES:
        return True
    if code in _NON_RECOVERABLE_CODES:
        return False
    msg = f"unknown ErrorCode: {code!r}"
    raise ValueError(msg)


class ErrorInfo(BaseModel, frozen=True):
    code: ErrorCode
    message: str
    operation_id: str | None = None
    details: dict[str, str | float | int | bool | None] = {}
    recoverable: bool


class AdapterError(Exception):
    def __init__(self, info: ErrorInfo) -> None:
        super().__init__(info.message)
        self.info = info
