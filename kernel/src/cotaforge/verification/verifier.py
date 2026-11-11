"""Verifier facade used by the executor."""

from typing import TYPE_CHECKING, Any

from cotaforge.contract import Adapter, ErrorInfo, OperationResult, Request
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.level1 import Level1Check
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot
from cotaforge.verification.results import CheckResult, Expectation
from cotaforge.verification.thresholds import DEFAULT_THRESHOLDS, Thresholds

if TYPE_CHECKING:
    from cotaforge.ir.ops_primitives import OperationBase


class Verifier:
    def __init__(self, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> None:
        self.thresholds = thresholds
        self.expected_computer = ExpectedComputer(thresholds)
        self.level1_check = Level1Check()
        self.level2_check = Level2Check(thresholds)

    def reset(self) -> None:
        """Forget per-plan state (sketch models, concept shapes, inserted parts)."""
        self.expected_computer.reset()

    def snapshot(self) -> dict[str, Any]:
        """The per-plan tracking state as it stands, for ``restore`` (an op that fails or is undone takes back what
        ``expected`` recorded for it)."""
        return self.expected_computer.snapshot()

    def restore(self, state: dict[str, Any]) -> None:
        self.expected_computer.restore(state)

    def forget_geometry(self) -> None:
        """Drop the analytic shapes tracked so far (an in-place edit changed the document under them)."""
        self.expected_computer.forget_geometry()

    def expected(
        self, op: "OperationBase", request: Request, before: StateSnapshot, adapter: Adapter
    ) -> tuple[Expectation, ...]:
        return self.expected_computer.compute(op, request, before, adapter)

    def level1(
        self, result: OperationResult | None, error: ErrorInfo | None, *, requires_feature: bool = True
    ) -> CheckResult:
        return self.level1_check.check(result, error, requires_feature=requires_feature)

    def level2(self, expectations: tuple[Expectation, ...], observation: Observation) -> tuple[CheckResult, ...]:
        return self.level2_check.check(expectations, observation)
