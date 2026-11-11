"""Level 1: did the adapter call produce a healthy feature?."""

from typing import Literal

from cotaforge.contract import ErrorInfo, OperationResult
from cotaforge.verification.results import CheckKind, CheckResult

LEVEL1_NAME = "level1"


def failure_reason(
    result: OperationResult | None, error: ErrorInfo | None, *, requires_feature: bool = True
) -> Literal["ok", "adapter_error", "no_feature", "rebuild_failed"]:
    """The reason for the level 1 verdict, in one word: the single place that decides why level 1 failed
    (the diagnosis reads this instead of re-parsing ``CheckResult.message``).

    ``adapter_error`` covers both an adapter-raised ``error`` and the (normally unreachable) case of no
    ``error`` and no ``result`` at all -- either way, the adapter did not hand back a usable result.
    """
    if error is not None:
        return "adapter_error"
    if result is None:
        return "adapter_error"
    if requires_feature and result.feature is None:
        return "no_feature"
    if not result.rebuild_ok:
        return "rebuild_failed"
    return "ok"


class Level1Check:
    def check(
        self, result: OperationResult | None, error: ErrorInfo | None, *, requires_feature: bool = True
    ) -> CheckResult:
        """ok iff ``error is None and result.feature is not None and result.rebuild_ok``.

        ``requires_feature=False`` relaxes the feature requirement for query-style ops whose
        adapter method returns no feature (``sketch``, ``check_interference``).
        """
        reason = failure_reason(result, error, requires_feature=requires_feature)
        if reason == "ok":
            return CheckResult(name=LEVEL1_NAME, kind=CheckKind.asserted, ok=True, expected=True, observed=True)
        if reason == "adapter_error":
            message = f"{error.code.value}: {error.message}" if error is not None else "no result"
        elif reason == "no_feature":
            message = "no feature created"
        else:
            message = "rebuild failed"
        return CheckResult(
            name=LEVEL1_NAME, kind=CheckKind.asserted, ok=False, expected=True, observed=False, message=message
        )
