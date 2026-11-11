"""Executor errors."""

from typing import Any

from cotaforge.ir.errors import PlanError

AMBIGUOUS_REFERENCE = "ambiguous_reference"
"""``PlanError.rule`` of a reference that matched more than one candidate."""

UNRESOLVED_REFERENCE = "resolution"
"""``PlanError.rule`` of a reference that matched nothing (or names an op with no geometry)."""


class ResolutionError(PlanError):
    """A symbolic reference resolved to zero or several candidates: the plan is wrong, not the CAD.

    ``candidate_details`` (one line per candidate, empty when nothing matched) lists the attributes that
    tell the candidates apart — centre, normal, area, surface, radius, role and the op that created the
    face — so the author can narrow the selector in one turn; ``message`` / ``str()`` carry the same list.
    ``rule`` is ``ambiguous_reference`` when ``candidates > 1`` and ``resolution`` otherwise.
    """

    def __init__(
        self,
        op_id: str,
        path: str,
        ref: Any,
        candidates: int,
        message: str,
        candidate_details: tuple[str, ...] = (),
    ) -> None:
        self.path = path
        self.ref = ref
        self.candidates = candidates
        self.candidate_details = tuple(candidate_details)
        rule = AMBIGUOUS_REFERENCE if candidates > 1 else UNRESOLVED_REFERENCE
        super().__init__(plan_id=None, op_id=op_id, rule=rule, message=f"{path}: {message}")
        if self.candidate_details:
            listing = "\n".join(f"  - {line}" for line in self.candidate_details)
            self.message = f"{self.message}\n{listing}"
            self.args = (f"{self.args[0]}\n{listing}",)


class ExecutorStateError(RuntimeError):
    """The executor was driven in an illegal state (e.g. next_operation before start)."""


class UndoFloorError(ExecutorStateError):
    """``Executor.undo_to`` refused BEFORE touching the CAD: it would revert steps that ran before the session was
    resumed (``below``); ``floor_op`` is the newest of them. The reopened document has no undo journal for them
    (a reopened document has no history): an in-place edit still changes them, a rebuild replays them."""

    def __init__(self, message: str, *, floor_op: str, below: tuple[str, ...]) -> None:
        super().__init__(message)
        self.floor_op = floor_op
        self.below = below


class UndoInterruptedError(ExecutorStateError):
    """``Executor.undo_to`` stopped and the CAD can no longer be trusted: the executor is ``degraded``. Either the
    adapter could not revert ``failed_op`` and did not say "rolled back" (``stage == "revert"``; ``info`` is its
    ``ErrorInfo``, ``None`` for a history mismatch), or every revert worked and the part could not be measured
    afterwards (``stage == "measure"``). ``reverted`` are the ops that were reverted before it stopped."""

    def __init__(
        self, message: str, *, failed_op: str, info: Any, reverted: tuple[str, ...], stage: str = "revert"
    ) -> None:
        super().__init__(message)
        self.failed_op = failed_op
        self.info = info
        self.reverted = reverted
        self.stage = stage
