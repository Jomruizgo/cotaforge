"""Tests for cotaforge.verification.level1."""

from cotaforge.contract import ErrorCode, ErrorInfo, FeatureHandle, OperationResult
from cotaforge.verification.level1 import Level1Check, failure_reason
from cotaforge.verification.results import CheckKind, CheckResult


def _result(feature: bool = True, rebuild_ok: bool = True) -> OperationResult:
    return OperationResult(
        operation_id="op", feature=FeatureHandle(id="feature:1") if feature else None, rebuild_ok=rebuild_ok
    )


def test_ok_when_feature_exists_and_rebuild_ok() -> None:
    check = Level1Check().check(_result(), None)
    assert check.ok is True
    assert check.name == "level1"
    assert check.kind == CheckKind.asserted


def test_fails_on_error() -> None:
    error = ErrorInfo(code=ErrorCode.geometry_failed, message="boom", recoverable=True)
    check = Level1Check().check(None, error)
    assert check.ok is False
    assert "geometry_failed" in check.message
    assert "boom" in check.message


def test_fails_when_no_feature_or_rebuild_failed() -> None:
    assert Level1Check().check(_result(feature=False), None).ok is False
    assert Level1Check().check(_result(rebuild_ok=False), None).ok is False
    assert Level1Check().check(None, None).ok is False


def test_query_ops_do_not_require_a_feature() -> None:
    # sketch / check_interference return no feature: the executor asks for requires_feature=False
    assert Level1Check().check(_result(feature=False), None, requires_feature=False).ok is True
    assert Level1Check().check(_result(feature=False, rebuild_ok=False), None, requires_feature=False).ok is False


def test_failure_reason_returns_the_four_words_in_their_four_cases() -> None:
    error = ErrorInfo(code=ErrorCode.geometry_failed, message="boom", recoverable=True)
    assert failure_reason(_result(), None) == "ok"
    assert failure_reason(None, error) == "adapter_error"
    assert failure_reason(None, None) == "adapter_error"
    assert failure_reason(_result(feature=False), None) == "no_feature"
    assert failure_reason(_result(rebuild_ok=False), None) == "rebuild_failed"


def test_failure_reason_respects_requires_feature() -> None:
    assert failure_reason(_result(feature=False), None, requires_feature=False) == "ok"


def test_check_still_returns_the_exact_check_result_of_today_in_all_four_cases() -> None:
    """Regression: ``Level1Check.check`` now delegates its verdict to ``failure_reason``, but the
    ``CheckResult`` it returns (shape and message) must not change."""
    error = ErrorInfo(code=ErrorCode.geometry_failed, message="boom", recoverable=True)

    ok = Level1Check().check(_result(), None)
    assert ok == CheckResult(name="level1", kind=CheckKind.asserted, ok=True, expected=True, observed=True)

    on_error = Level1Check().check(None, error)
    assert on_error == CheckResult(
        name="level1",
        kind=CheckKind.asserted,
        ok=False,
        expected=True,
        observed=False,
        message="geometry_failed: boom",
    )

    no_result = Level1Check().check(None, None)
    assert no_result == CheckResult(
        name="level1", kind=CheckKind.asserted, ok=False, expected=True, observed=False, message="no result"
    )

    no_feature = Level1Check().check(_result(feature=False), None)
    assert no_feature == CheckResult(
        name="level1", kind=CheckKind.asserted, ok=False, expected=True, observed=False, message="no feature created"
    )

    rebuild_failed = Level1Check().check(_result(rebuild_ok=False), None)
    assert rebuild_failed == CheckResult(
        name="level1", kind=CheckKind.asserted, ok=False, expected=True, observed=False, message="rebuild failed"
    )
