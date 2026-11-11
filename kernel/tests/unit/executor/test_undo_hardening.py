"""``Executor.undo_to`` / the cleanup revert of a failed step when the adapter's revert itself fails.

Rule: ONLY ``rebuild_failed`` (the FreeCAD worker's "rolled back, the document and the registry are as before") means
the CAD is intact and a retry is sound. Any other adapter error from ``undo_last`` -- a timeout (the RPC layer kills the
worker), ``internal`` flagged ``degraded`` -- leaves the CAD in an unknown state
and degrades the session. A revert that fails inside a step (the cleanup of a failed attempt) degrades too: carrying on
would run the op again on top of the failed attempt's feature.
"""

from typing import Any

import pytest

from cotaforge.contract import AdapterError, BoundingBox, Empty, ErrorCode, ErrorInfo, UndoResult
from cotaforge.executor.errors import ExecutorStateError, UndoInterruptedError
from cotaforge.executor.trace import ExecutorState, StepStatus
from tests.unit.executor.test_extend_undo import _executor, _fake


def _failing_at(adapter: Any, op_id: str, info: ErrorInfo) -> None:
    real = adapter.undo_last

    def failing(request: Empty) -> UndoResult:
        if request.operation_id == op_id:
            raise AdapterError(info.model_copy(update={"operation_id": op_id}))
        return real(request)

    adapter.undo_last = failing


@pytest.mark.parametrize(
    "info",
    [
        ErrorInfo(code=ErrorCode.timeout, message="worker killed", recoverable=True),
        ErrorInfo(code=ErrorCode.internal, message="boom", recoverable=True),
        ErrorInfo(code=ErrorCode.session_lost, message="gone", recoverable=False),
        # a worker whose ``internal`` (recoverable) flagged degraded=True: "undo left the tree changed"
        ErrorInfo(
            code=ErrorCode.internal, message="undo left the tree changed", recoverable=True, details={"degraded": True}
        ),
        # even the code that normally means "rolled back" is not trusted once the worker says it is degraded
        ErrorInfo(code=ErrorCode.rebuild_failed, message="boom", recoverable=True, details={"degraded": True}),
    ],
)
def test_only_a_plain_rebuild_failed_from_undo_last_keeps_the_session_alive(
    plate_plan: dict[str, Any], info: ErrorInfo
) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    _failing_at(adapter, "hole_1", info)

    with pytest.raises(UndoInterruptedError) as raised:
        ex.undo_to("base_sketch")

    assert raised.value.failed_op == "hole_1" and raised.value.reverted == ("holes",)
    assert ex.state is ExecutorState.degraded
    assert [r.op_ids for r in ex.trace().reverts] == [("holes",)]  # what did go is still recorded


def test_retrying_undo_to_with_a_different_target_after_a_recoverable_failure(plate_plan: dict[str, Any]) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    info = ErrorInfo(code=ErrorCode.rebuild_failed, message="boom", recoverable=True)
    real = adapter.undo_last
    state = {"failed": False}

    def once(request: Empty) -> UndoResult:
        if request.operation_id == "hole_1" and not state["failed"]:
            state["failed"] = True
            raise AdapterError(info)
        return real(request)

    adapter.undo_last = once
    with pytest.raises(AdapterError):
        ex.undo_to("base_sketch")
    assert ex.state is ExecutorState.paused and ex.cursor == 3

    assert ex.undo_to("base") == ("hole_1",)  # a different, shallower target: only what is left above it

    assert [r.op_ids for r in ex.trace().reverts] == [("holes",), ("hole_1",)]
    assert ex.cursor == 2 and ex.state is ExecutorState.paused


def test_a_history_mismatch_degrades_through_the_same_error_type(plate_plan: dict[str, Any]) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    real = adapter.undo_last

    def lying(request: Empty) -> UndoResult:
        real(request)
        return UndoResult(reverted=None)

    adapter.undo_last = lying
    with pytest.raises(UndoInterruptedError, match="history disagrees") as raised:
        ex.undo_to("base")
    assert raised.value.info is None and raised.value.failed_op == "holes"
    assert ex.state is ExecutorState.degraded


def test_a_failure_measuring_the_part_after_every_revert_is_recorded_and_degrades(plate_plan: dict[str, Any]) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()

    def broken(request: Any) -> Any:
        raise AdapterError(ErrorInfo(code=ErrorCode.timeout, message="no answer", recoverable=True))

    adapter.bounding_box = broken
    with pytest.raises(UndoInterruptedError, match="could not measure") as raised:
        ex.undo_to("base")

    assert raised.value.reverted == ("holes", "hole_1") and ex.partial_revert == ("holes", "hole_1")
    assert [r.op_ids for r in ex.trace().reverts] == [("holes", "hole_1")]  # the reverts DID happen: on record
    assert ex.state is ExecutorState.degraded


def test_the_final_bbox_follows_a_partial_revert(plate_plan: dict[str, Any]) -> None:
    adapter = _fake()
    ex = _executor(plate_plan, adapter)
    ex.run_to_end()
    _failing_at(adapter, "hole_1", ErrorInfo(code=ErrorCode.rebuild_failed, message="boom", recoverable=True))
    after_revert = BoundingBox(min=(1.0, 2.0, 3.0), max=(4.0, 5.0, 6.0))
    adapter.bounding_box = lambda request: after_revert

    with pytest.raises(AdapterError):
        ex.undo_to("base_sketch")

    assert ex.trace().final_bbox == after_revert  # measured again, not the one from before the revert


def test_a_failed_cleanup_revert_inside_a_step_degrades_instead_of_inviting_a_duplicate(
    plate_plan: dict[str, Any],
) -> None:
    # hole_1's first attempt fails level 1 AFTER creating its feature (rebuild_failed fault); the executor then reverts
    # that attempt -- and that revert fails. The failed attempt's hole may still be in the document: no retry.
    adapter = _fake({"faults": [{"operation_id": "hole_1", "attempt": 1, "kind": "rebuild_failed"}]})
    ex = _executor(plate_plan, adapter)
    _failing_at(adapter, "hole_1", ErrorInfo(code=ErrorCode.rebuild_failed, message="boom", recoverable=True))
    ex.next_operation()
    ex.next_operation()

    record = ex.next_operation()

    assert record.status is StepStatus.failed and len(record.attempts) == 1  # no second attempt
    assert ex.state is ExecutorState.degraded
    error = record.attempts[-1].error
    assert error is not None and not error.recoverable
    assert "may remain in the document" in error.message and "boom" in error.message
    with pytest.raises(ExecutorStateError):
        ex.next_operation()
