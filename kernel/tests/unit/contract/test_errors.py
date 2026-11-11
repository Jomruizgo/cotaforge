"""Tests for cotaforge.contract.errors: ErrorInfo, AdapterError, retry policy."""

import pytest

from cotaforge.contract.enums import ErrorCode
from cotaforge.contract.errors import AdapterError, ErrorInfo, default_recoverable

_RECOVERABLE = {
    ErrorCode.timeout,
    ErrorCode.geometry_failed,
    ErrorCode.rebuild_failed,
    ErrorCode.mate_failed,
    ErrorCode.internal,
}

_NON_RECOVERABLE = {
    ErrorCode.not_supported,
    ErrorCode.invalid_request,
    ErrorCode.invalid_reference,
    ErrorCode.session_lost,
    ErrorCode.no_active_document,
    ErrorCode.degraded_session,
}


@pytest.mark.parametrize("code", sorted(_RECOVERABLE, key=lambda c: c.value))
def test_default_recoverable_true_for_recoverable_codes(code: ErrorCode) -> None:
    assert default_recoverable(code) is True


@pytest.mark.parametrize("code", sorted(_NON_RECOVERABLE, key=lambda c: c.value))
def test_default_recoverable_false_for_non_recoverable_codes(code: ErrorCode) -> None:
    assert default_recoverable(code) is False


def test_default_recoverable_covers_every_error_code() -> None:
    assert _RECOVERABLE | _NON_RECOVERABLE == set(ErrorCode)


def test_adapter_error_carries_info() -> None:
    info = ErrorInfo(code=ErrorCode.geometry_failed, message="boom", recoverable=True)
    error = AdapterError(info)
    assert error.info is info
    assert str(error) == "boom"


def test_error_info_defaults() -> None:
    info = ErrorInfo(code=ErrorCode.internal, message="x", recoverable=False)
    assert info.operation_id is None
    assert info.details == {}
