"""Executor.extend / undo_to / check_health on the fake adapter."""

from typing import Any

import pytest

from cotaforge.contract import (
    Adapter,
    AdapterError,
    ConnectRequest,
    Empty,
    ErrorCode,
    ErrorInfo,
    FeatureHandle,
    MassRequest,
    UndoResult,
)
from cotaforge.executor.errors import ExecutorStateError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus, active_steps
from cotaforge.ir import PlanError, parse_plan
from cotaforge.verification.verifier import Verifier
from tests.unit.executor.test_with_fake import PLATE_VOLUME

CHAMFER = {
    "id": "edge_break",
    "op": "chamfer",
    "intent": "Break the top outer edges",
    "edges": [
        {
            "kind": "edge",
            "between": [
                {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
                {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}},
            ],
        }
    ],
    "distance1_mm": 1.0,
}


def _fake(faults: dict[str, Any] | None = None) -> Adapter:
    fake_module = pytest.importorskip("cotaforge.adapters.fake.adapter")
    faults_module = pytest.importorskip("cotaforge.adapters.fake.faults")
    adapter = fake_module.FakeAdapter(faults=faults_module.FaultPlan.model_validate(faults or {"faults": []}))
    adapter.connect(ConnectRequest(operation_id="connect"))
    return adapter


def _executor(plan: dict[str, Any], adapter: Adapter | None = None) -> Executor:
    ex = Executor(parse_plan(plan), adapter or _fake(), Verifier())
    ex.start()
    return ex


def _run_and_pause_after(ex: Executor, op_id: str) -> None:
    while ex.state is ExecutorState.running:
        record = ex.next_operation()
        if record.op_id == op_id:
            ex.pause()
            break
    if ex.state is ExecutorState.running:
        ex.next_operation()


def _volume(ex: Executor) -> float:
    return ex.adapter.mass_properties(MassRequest(operation_id="probe")).volume_mm3


# --- extend ----------------------------------------------------------------------------------------


def test_extend_from_completed_runs_the_new_ops(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.completed
    ex.extend([CHAMFER])
    assert ex.state is ExecutorState.running
    assert ex.plan.operations[-1].id == "edge_break"
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.completed
    assert [s.op_id for s in trace.steps] == ["base_sketch", "base", "hole_1", "holes", "edge_break"]
    assert trace.step("edge_break").status is StepStatus.success  # type: ignore[union-attr]
    assert trace.plan_id == "plate"


def test_extend_from_paused_resumes_into_the_new_ops(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.next_operation()
    ex.pause()
    ex.next_operation()
    assert ex.state is ExecutorState.paused
    ex.extend([CHAMFER])
    assert ex.state is ExecutorState.running
    assert ex.cursor == 2
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.completed
    assert len(trace.steps) == 5


def test_extend_refused_while_running_or_idle(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    with pytest.raises(ExecutorStateError, match="extend"):
        ex.extend([CHAMFER])
    idle = Executor(parse_plan(plate_plan), _fake(), Verifier())
    with pytest.raises(ExecutorStateError):
        idle.extend([CHAMFER])


def test_extend_revalidates_duplicate_id_and_forward_ref(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    before = ex.plan
    with pytest.raises(PlanError) as duplicate:
        ex.extend([{**CHAMFER, "id": "base"}])
    assert duplicate.value.rule == "V1"
    forward = {**CHAMFER, "edges": [{"kind": "edge", "of": {"kind": "face", "from_op": "later"}, "curve": "line"}]}
    with pytest.raises(PlanError) as unknown:
        ex.extend([forward])
    assert unknown.value.rule == "V2"
    assert ex.plan is before
    assert ex.state is ExecutorState.completed


def test_extend_manual_feature_alias_collides_with_an_id_in_the_same_batch(plate_plan: dict[str, Any]) -> None:
    """An alias must be unique against the ids appended in THIS SAME ``extend`` call too, not just
    against the plan as it stood before the call — two operations of one batch can share an id even though
    neither one is in the plan yet, and an alias equal to the second one would silently shadow it."""
    ex = _executor(plate_plan)
    ex.run_to_end()
    before = ex.plan
    manual = FeatureHandle(id="feature:manual-1")
    batch = [CHAMFER, {**CHAMFER, "id": "edge_break_2"}]
    with pytest.raises(PlanError) as collision:
        ex.extend(batch, external_features={"edge_break_2": manual})
    assert collision.value.rule == "V1"
    assert "edge_break_2" in str(collision.value)
    assert ex.plan is before  # the batch is rejected as a whole: nothing appended
    assert ex.state is ExecutorState.completed


def test_extend_accepts_operation_models(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    chamfer = parse_plan({**plate_plan, "operations": [*plate_plan["operations"], CHAMFER]}).operations[-1]
    ex.extend([chamfer])
    assert ex.plan.operations[-1] == chamfer


# --- undo_to ---------------------------------------------------------------------------------------


def test_undo_to_reverts_newest_first_and_keeps_the_records(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    assert _volume(ex) == pytest.approx(PLATE_VOLUME, abs=1e-3)
    reverted = ex.undo_to("base", by="engineer", reason="wrong hole size")
    assert reverted == ("holes", "hole_1")
    assert ex.state is ExecutorState.paused
    assert ex.cursor == 2
    assert _volume(ex) == pytest.approx(48000.0, abs=1e-6)  # plate without holes
    trace = ex.trace()
    assert [s.op_id for s in trace.steps] == ["base_sketch", "base", "hole_1", "holes"]
    assert [s.op_id for s in active_steps(trace)] == ["base_sketch", "base"]
    assert len(trace.reverts) == 1
    assert trace.reverts[0].op_ids == ("holes", "hole_1")
    assert trace.reverts[0].by == "engineer"
    assert trace.reverts[0].reason == "wrong hole size"
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(48000.0)
    assert ExecutionTrace.model_validate(trace.model_dump(mode="json")) == trace


def test_undo_to_removes_context_registrations(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    ctx = ex._ctx
    assert ctx is not None
    assert "hole_1" in ctx.feature_by_op and "holes" in ctx.feature_by_op
    ex.undo_to("base_sketch")
    assert set(ctx.feature_by_op) == set()
    assert set(ctx.sketch_by_op) == {"base_sketch"}
    assert ex.cursor == 1


def test_undo_to_then_resume_re_runs_the_ops_and_active_steps_follows(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    ex.undo_to("base")
    ex.resume()
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.completed
    assert [s.op_id for s in trace.steps] == ["base_sketch", "base", "hole_1", "holes", "hole_1", "holes"]
    assert [s.op_id for s in trace.active_steps()] == ["base_sketch", "base", "hole_1", "holes"]
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME, abs=1e-3)


def test_undo_to_from_failed_supersedes_the_failed_record(plate_plan: dict[str, Any]) -> None:
    # Attempts 1-3 fail (max_retries=2 exhausts them); the re-run after undo_to is attempt 4 and succeeds.
    faults = {"faults": [{"operation_id": "hole_1", "attempt": n, "kind": "raise_error"} for n in (1, 2, 3)]}
    ex = _executor(plate_plan, _fake(faults))
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.failed
    reverted = ex.undo_to("base_sketch")
    assert reverted == ("hole_1", "base")
    assert ex.state is ExecutorState.paused
    assert [s.op_id for s in ex.trace().active_steps()] == ["base_sketch"]
    ex.resume()
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.completed
    assert [s.op_id for s in trace.active_steps()] == ["base_sketch", "base", "hole_1", "holes"]


def test_undo_to_refuses_running_unknown_and_reverted_targets(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    with pytest.raises(ExecutorStateError, match="undo_to"):
        ex.undo_to("base")
    ex.run_to_end()
    with pytest.raises(ExecutorStateError, match="no successful"):
        ex.undo_to("nope")
    ex.undo_to("base")
    with pytest.raises(ExecutorStateError, match="no successful"):
        ex.undo_to("holes")


def test_undo_to_mismatch_degrades(plate_plan: dict[str, Any]) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    real_undo = adapter.undo_last

    def lying_undo(request: Empty) -> UndoResult:
        real_undo(request)
        return UndoResult(reverted=FeatureHandle(id="feature:99"))

    adapter.undo_last = lying_undo  # type: ignore[method-assign]
    with pytest.raises(ExecutorStateError, match="history disagrees"):
        ex.undo_to("base")
    assert ex.state is ExecutorState.degraded
    with pytest.raises(ExecutorStateError):
        ex.next_operation()


def test_undo_to_mismatch_mid_loop_keeps_prior_reverts_and_refuses_further_calls(plate_plan: dict[str, Any]) -> None:
    # Steps (newest first) after "base_sketch" are holes, hole_1, base. The adapter agrees on the
    # first revert (holes) but lies on the second (hole_1): the loop must keep "holes" as reverted,
    # degrade on the hole_1 mismatch, and refuse any further undo_to/extend after that.
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    real_undo = adapter.undo_last

    def lying_undo(request: Empty) -> UndoResult:
        result = real_undo(request)
        if request.operation_id == "hole_1":
            return UndoResult(reverted=FeatureHandle(id="feature:99"))
        return result

    adapter.undo_last = lying_undo  # type: ignore[method-assign]
    with pytest.raises(ExecutorStateError, match="history disagrees"):
        ex.undo_to("base_sketch")
    assert ex.state is ExecutorState.degraded
    trace = ex.trace()
    assert trace.reverts[-1].op_ids == ("holes",)  # steps before the mismatch were recorded as reverted
    assert [s.op_id for s in trace.active_steps()] == ["base_sketch", "base", "hole_1"]
    with pytest.raises(ExecutorStateError, match="undo_to"):
        ex.undo_to("base_sketch")
    with pytest.raises(ExecutorStateError, match="extend"):
        ex.extend([CHAMFER])


def test_undo_to_adapter_error_degrades_and_names_the_op_instead_of_leaking_the_raw_error(
    plate_plan: dict[str, Any],
) -> None:
    # The adapter reverts "holes", then cannot revert "hole_1" (a FreeCAD worker whose revert failed and was rolled back
    # raises degraded_session): the executor must stop there, keep the prior revert on record and not carry on.
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    real_undo = adapter.undo_last

    def failing_undo(request: Empty) -> UndoResult:
        if request.operation_id == "hole_1":
            info = ErrorInfo(
                code=ErrorCode.degraded_session,
                message="undo_last could not revert feature feature:2: boom",
                operation_id="hole_1",
                recoverable=False,
            )
            raise AdapterError(info)
        return real_undo(request)

    adapter.undo_last = failing_undo  # type: ignore[method-assign]
    with pytest.raises(ExecutorStateError, match=r"could not revert op 'hole_1'.*degraded_session.*boom"):
        ex.undo_to("base_sketch")
    assert ex.state is ExecutorState.degraded
    trace = ex.trace()
    assert trace.reverts[-1].op_ids == ("holes",)
    assert [s.op_id for s in trace.active_steps()] == ["base_sketch", "base", "hole_1"]
    with pytest.raises(ExecutorStateError, match="undo_to"):
        ex.undo_to("base_sketch")


def _undo_failing_once_at(adapter: Any, op_id: str, info: ErrorInfo) -> None:
    real_undo = adapter.undo_last
    state = {"failed": False}

    def failing_undo(request: Empty) -> UndoResult:
        if request.operation_id == op_id and not state["failed"]:
            state["failed"] = True
            raise AdapterError(info)
        return real_undo(request)

    adapter.undo_last = failing_undo


def test_undo_to_recoverable_adapter_error_propagates_keeps_progress_and_can_be_retried(
    plate_plan: dict[str, Any],
) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    info = ErrorInfo(code=ErrorCode.rebuild_failed, message="boom", operation_id="hole_1", recoverable=True)
    _undo_failing_once_at(adapter, "hole_1", info)

    with pytest.raises(AdapterError):
        ex.undo_to("base_sketch")

    assert ex.state is ExecutorState.paused  # not degraded: the revert was rolled back, nothing is lost
    assert ex.partial_revert == ("holes",)
    trace = ex.trace()
    assert [r.op_ids for r in trace.reverts] == [("holes",)]
    assert [s.op_id for s in trace.active_steps()] == ["base_sketch", "base", "hole_1"]
    assert ex.cursor == 3  # `holes` is the next op to run again
    assert ex.undo_to("base_sketch") == ("hole_1", "base")  # the retry finishes the job
    assert [r.op_ids for r in ex.trace().reverts] == [("holes",), ("hole_1", "base")]
    assert ex.partial_revert == ()


def test_undo_to_recoverable_error_on_the_first_revert_records_nothing_and_keeps_the_state(
    plate_plan: dict[str, Any],
) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    info = ErrorInfo(code=ErrorCode.rebuild_failed, message="boom", operation_id="holes", recoverable=True)
    _undo_failing_once_at(adapter, "holes", info)

    with pytest.raises(AdapterError):
        ex.undo_to("base_sketch")

    assert ex.state is ExecutorState.completed and ex.cursor == 4
    assert ex.trace().reverts == () and ex.partial_revert == ()


# --- check_health ----------------------------------------------------------------------------------


def test_check_health_records_the_trigger(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    result = ex.check_health("on_resume")
    assert result.healthy
    assert [h.trigger for h in ex.health] == ["before_plan", "on_resume"]
    ex.check_health("after_silence")
    assert ex.trace().health[-1].trigger == "after_silence"


# --- undo_to + extend replaces the reverted tail instead of queueing behind it ----------


def test_extend_after_undo_to_discards_the_reverted_tail_instead_of_appending_after_it(
    plate_plan: dict[str, Any],
) -> None:
    """Before the fix, ``extend`` always appended at ``len(operations)``: after ``undo_to("base")``
    (which reverts ``hole_1``/``holes`` but, by design, keeps them queued for a plain redo), the newly
    extended op landed AFTER those stale ops in the queue and the agent had to close/reopen the session
    to get it to run first. ``extend`` must instead replace the stale tail."""
    ex = _executor(plate_plan)
    ex.run_to_end()
    reverted = ex.undo_to("base", by="engineer", reason="wrong hole size")
    assert reverted == ("holes", "hole_1")
    assert ex.cursor == 2
    ex.extend([CHAMFER])
    assert [op.id for op in ex.plan.operations] == ["base_sketch", "base", "edge_break"]
    assert ex.state is ExecutorState.running
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.completed
    # hole_1/holes stay in the trace as historical (reverted) evidence, but never re-run
    assert [s.op_id for s in trace.steps] == ["base_sketch", "base", "hole_1", "holes", "edge_break"]
    assert [s.op_id for s in trace.active_steps()] == ["base_sketch", "base", "edge_break"]
    assert trace.step("edge_break").status is StepStatus.success  # type: ignore[union-attr]


def test_extend_after_a_no_op_undo_to_still_appends_after_the_untouched_tail(plate_plan: dict[str, Any]) -> None:
    """``undo_to`` on the last successful step reverts nothing (there is no stale tail): the ops still
    queued after the cursor are the ORIGINAL plan's own not-yet-run ops, and extend must keep them, not
    discard them."""
    ex = _executor(plate_plan)
    ex.next_operation()  # base_sketch
    ex.pause_now()
    assert ex.undo_to("base_sketch") == ()
    ex.extend([CHAMFER])
    assert [op.id for op in ex.plan.operations] == ["base_sketch", "base", "hole_1", "holes", "edge_break"]


def test_extend_partial_redo_after_undo_to_still_discards_only_the_remaining_stale_tail(
    plate_plan: dict[str, Any],
) -> None:
    """The engineer redoes one reverted op by hand (``next_operation``) before deciding to extend instead
    of redoing the rest: only the still-unredone remainder of the stale tail is discarded."""
    ex = _executor(plate_plan)
    ex.run_to_end()
    ex.undo_to("base")  # reverts holes, hole_1; cursor -> 2
    ex.resume()
    ex.pause()  # takes effect after the next op finishes
    ex.next_operation()  # redoes hole_1 unchanged, then pauses; cursor -> 3
    assert ex.state is ExecutorState.paused
    assert ex.cursor == 3
    ex.extend([CHAMFER])
    assert [op.id for op in ex.plan.operations] == ["base_sketch", "base", "hole_1", "edge_break"]


def test_pause_now_pauses_between_ops(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.next_operation()
    ex.pause_now()
    assert ex.state is ExecutorState.paused
    assert ex.undo_to("base_sketch") == ()
    ex.resume()
    assert ex.state is ExecutorState.running
    ex.run_to_end()
    with pytest.raises(ExecutorStateError, match="pause_now"):
        ex.pause_now()
