"""Trace models; all JSON-serialisable via ``model_dump(mode="json")``."""

from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel

from cotaforge.contract import BoundingBox, CanaryResult, EditResult, ErrorInfo, MassProperties, OperationResult
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.diagnosis import Diagnosis, RepairAction
from cotaforge.verification.results import CheckResult, Expectation


class ExecutorState(str, Enum):  # noqa: UP042
    idle = "idle"
    running = "running"
    paused = "paused"
    completed = "completed"
    failed = "failed"
    degraded = "degraded"


class StepStatus(str, Enum):  # noqa: UP042
    success = "success"
    failed = "failed"
    skipped = "skipped"
    plan_error = "plan_error"


class AttemptRecord(BaseModel, frozen=True):
    number: int
    request: dict
    result: OperationResult | None
    error: ErrorInfo | None
    level1: CheckResult
    level2: tuple[CheckResult, ...] = ()
    undone: bool = False
    flipped: bool = False
    duration_ms: float
    diagnosis: Diagnosis | None = None
    """The classification of *why* this attempt failed level 1, when
    ``ExecutorConfig.repair_on_failure`` is on and this attempt's operation is still within its
    ``max_repairs_per_operation`` budget. ``None`` on a successful attempt and whenever repair is
    disabled or the budget is exhausted."""
    repair_applied: RepairAction | None = None
    """The repair actually applied before the next attempt, when ``diagnosis.repairable`` was ``True``
    and ``Executor._apply_repair`` could concretely mutate the request. ``None`` whenever no repair was
    attempted, or a repairable diagnosis could not be mechanically applied (see ``diagnosis.py``)."""


class StepRecord(BaseModel, frozen=True):
    op_id: str
    op: str
    status: StepStatus
    attempts: tuple[AttemptRecord, ...]
    resolved_refs: dict[str, str] = {}
    notes: tuple[str, ...] = ()
    """What the step resolved that the agent should see: the edges of each edge set (count and one line each) and the
    edges a ``skip_failing`` chamfer or fillet left out."""
    expectations: tuple[Expectation, ...] = ()
    plan_error: str | None = None
    mass_before: MassProperties | None
    mass_after: MassProperties | None
    started_at: datetime
    finished_at: datetime
    anchor: int | None = None
    """Set only on a step run OUTSIDE the plan (``Executor.record_external_step``, e.g. a native fragment): how many
    operations of the plan were done when it ran. Such a step has no operation of its own, so the executor places it
    between the plan operations by this number."""

    def last_level2(self) -> tuple[CheckResult, ...]:
        """Level 2 results of the last attempt (what E reads per op)."""
        return self.attempts[-1].level2 if self.attempts else ()


HealthTrigger = Literal["before_plan", "after_failure", "on_resume", "after_silence"]


class HealthEvent(BaseModel, frozen=True):
    when: datetime
    trigger: HealthTrigger
    result: CanaryResult


class RevertRecord(BaseModel, frozen=True):
    """One ``Executor.undo_to`` call.

    ``op_ids`` lists every step record that came after the target step, newest first, as undone:
    successful steps had their feature reverted in the CAD; failed / plan_error records after the
    target are superseded (the executor had already undone their feature) and are listed too so that
    ``active_steps`` can discard them. Reverted ``StepRecord``s stay in ``ExecutionTrace.steps``:
    evidence is never deleted.
    """

    op_ids: tuple[str, ...]
    by: str
    reason: str
    at: datetime
    warnings: tuple[str, ...] = ()
    """What the adapter reported it could not honour of the engineer's hand work while reverting (``UndoResult``)."""


EDIT_VERIFICATION_NOTE = (
    "recomputed in place by the CAD, not re-verified per operation: the checks of the steps below predate the edit; "
    "the adapter measured the result (health, per-body delta, flags) instead"
)


class EditRecord(BaseModel, frozen=True):
    """One ``Executor.edit_in_place`` call: the ops whose values were changed IN PLACE in the CAD
    instead of being rebuilt. Append-only, like ``RevertRecord``; the ``StepRecord``s of the edited ops and of their
    dependents stay frozen as they were, so ``recomputed_ops`` names exactly the steps whose recorded checks no longer
    describe the document (the dependents were recomputed by the CAD, not re-verified per operation).

    ``status``: ``applied`` (the edit stands), ``unchanged`` (the new requests equal the old ones: only the plan
    changed, the CAD was not called) or ``rolled_back`` (the CAD, or the reference check, refused to keep it; the
    document is as before) or ``refused`` (nothing was attempted: returned to the caller, never recorded)."""

    op_ids: tuple[str, ...]
    status: Literal["applied", "unchanged", "rolled_back", "refused"]
    by: str
    reason: str
    at: datetime
    changes: dict[str, tuple[str, ...]] = {}
    """Edited op id -> the top-level fields of the op that changed."""
    old_requests: dict[str, dict[str, Any]] = {}
    """Edited op id -> the adapter request it held before (what the CAD had), for the ops whose request changed."""
    new_requests: dict[str, dict[str, Any]] = {}
    """Edited op id -> the adapter request this edit sent (what the CAD was given)."""
    plan_revision: int | None = None
    """The plan revision this edit created (``applied``/``unchanged`` in a session that numbers its revisions)."""
    result: EditResult | None = None
    recomputed_ops: tuple[str, ...] = ()
    reference_drift: tuple[str, ...] = ()
    followed: tuple[str, ...] = ()
    """``applied`` only: later steps that select a face by an absolute centre and whose face the edit moved; each one
    names the step, the selector and the centre it now has (the plan was updated to it)."""
    failure: str | None = None
    failure_kind: Literal["semantic", "technical"] | None = None
    """Set with ``failure``. ``semantic``: the checks found the part would break with this value (a rebuild would apply
    it WITHOUT those checks). ``technical``: the mechanism, not the value, failed or refused, so a rebuild is a sound
    way to apply the same change."""
    verification: str = EDIT_VERIFICATION_NOTE


class ExecutionTrace(BaseModel, frozen=True):
    plan_id: str
    adapter: str
    config: ExecutorConfig
    steps: tuple[StepRecord, ...]
    health: tuple[HealthEvent, ...]
    final_state: ExecutorState
    final_mass: MassProperties | None
    final_bbox: BoundingBox | None
    reverts: tuple[RevertRecord, ...] = ()
    edits: tuple[EditRecord, ...] = ()
    """In-place edits (additive: a trace written before they existed loads with none)."""

    def step(self, op_id: str) -> StepRecord | None:
        for record in self.steps:
            if record.op_id == op_id:
                return record
        return None

    def active_steps(self) -> tuple[StepRecord, ...]:
        """Steps not undone by a ``RevertRecord`` — the only way consumers should count steps.

        A revert names the record that was current for each op at the time; an op re-executed after
        a revert gets a fresh record appended later. Records are append-only, so for every op the
        first *k* records are the reverted ones, where *k* is how many times the op appears in
        ``reverts`` (every earlier record of a re-run op is necessarily reverted or superseded).

        Not authoritative once ``final_state`` is ``degraded``: a mid-``undo_to`` mismatch between the
        adapter and the executor's history stops the revert loop early (``executor.py::undo_to``), so
        any step not yet reached by that loop is reported "active" here even though the real CAD state
        is no longer known to agree with it. Callers must treat ``final_state=degraded`` as its own
        blocker rather than trusting this method's counts.
        """
        return active_steps_of(self.steps, self.reverts)


def active_steps_of(steps: Sequence[StepRecord], reverts: Sequence[RevertRecord]) -> tuple[StepRecord, ...]:
    """The steps not undone by a revert (see ``ExecutionTrace.active_steps``), from the records alone."""
    remaining: dict[str, int] = {}
    for revert in reverts:
        for op_id in revert.op_ids:
            remaining[op_id] = remaining.get(op_id, 0) + 1
    active: list[StepRecord] = []
    for record in steps:
        if remaining.get(record.op_id, 0) > 0:
            remaining[record.op_id] -= 1
            continue
        active.append(record)
    return tuple(active)


def active_steps(trace: ExecutionTrace) -> tuple[StepRecord, ...]:
    """Function form of ``ExecutionTrace.active_steps``."""
    return trace.active_steps()


class ExecutorStatus(BaseModel, frozen=True):
    state: ExecutorState
    cursor: int
    total: int
    current_op_id: str | None
    last_step: StepRecord | None
