"""Error model of the worker: ``WorkerError`` carries an ``ErrorInfo``-shaped dict.

``recoverable`` follows the contract's retry policy exactly and is derived from the code, never chosen ad hoc.
"""

from typing import Any

RECOVERABLE: dict[str, bool] = {
    "timeout": True,
    "geometry_failed": True,
    "rebuild_failed": True,
    "mate_failed": True,
    "internal": True,
    "not_supported": False,
    "invalid_request": False,
    "invalid_reference": False,
    "session_lost": False,
    "no_active_document": False,
    "degraded_session": False,
}

DetailValue = str | float | int | bool | None


class WorkerError(Exception):
    """Raised by handlers; ``main`` turns it into a failure response."""

    def __init__(
        self,
        code: str,
        message: str,
        operation_id: str | None = None,
        details: dict[str, DetailValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.operation_id = operation_id
        self.details: dict[str, DetailValue] = dict(details or {})
        self.recoverable = RECOVERABLE[code]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "operation_id": self.operation_id,
            "details": self.details,
            "recoverable": self.recoverable,
        }


def error(code: str, message: str, operation_id: str | None = None, **details: DetailValue) -> WorkerError:
    return WorkerError(code, message, operation_id, details)


def invalid_reference(handle: str, operation_id: str | None = None) -> WorkerError:
    return error("invalid_reference", f"unknown or stale handle {handle}", operation_id, handle=handle)


def invalid_request(message: str, operation_id: str | None = None) -> WorkerError:
    return error("invalid_request", message, operation_id)


def not_supported(message: str, operation_id: str | None = None) -> WorkerError:
    return error("not_supported", message, operation_id)


def geometry_failed(message: str, operation_id: str | None = None, **details: DetailValue) -> WorkerError:
    return error("geometry_failed", message, operation_id, **details)


def unrecoverable(failure: WorkerError) -> WorkerError:
    """Mark a failure that asking again with the same request cannot fix (the kernel is deterministic), so the executor
    reports it instead of retrying it identically."""
    failure.recoverable = False
    return failure


def mate_failed(message: str, operation_id: str | None = None) -> WorkerError:
    return error("mate_failed", message, operation_id)


def no_active_document(operation_id: str | None = None) -> WorkerError:
    return error("no_active_document", "no active document", operation_id)


def operation_id_of(params: dict[str, Any]) -> str | None:
    value = params.get("operation_id")
    return value if isinstance(value, str) else None
