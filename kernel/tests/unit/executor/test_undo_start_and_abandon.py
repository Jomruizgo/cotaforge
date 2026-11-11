"""``Executor.undo_to(None)`` (undo every step) and ``Executor.abandon_failed`` (leave the failed state)."""

from typing import Any

import pytest

from cotaforge.executor.errors import ExecutorStateError
from cotaforge.executor.trace import ExecutorState, StepStatus
from tests.unit.executor.test_extend_undo import CHAMFER, _executor, _volume


def test_undo_to_none_reverts_every_step_and_restarts_from_an_empty_cursor(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    reverted = ex.undo_to(None)
    assert reverted == ("holes", "hole_1", "base", "base_sketch")
    assert ex.state is ExecutorState.paused
    assert ex.cursor == 0
    assert ex.trace().active_steps() == ()
    assert _volume(ex) == pytest.approx(0.0)
    assert ex.trace().final_mass is not None and ex.trace().final_mass.volume_mm3 == pytest.approx(0.0)


def test_abandon_failed_drops_the_failed_op_so_the_session_can_continue(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    unresolvable = {
        "kind": "edge",
        "of": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 5]}},
        "curve": "line",
    }
    ex.extend([{**CHAMFER, "id": "broken", "edges": [unresolvable]}])
    record = ex.next_operation()
    assert record.status is not StepStatus.success
    assert ex.state is ExecutorState.failed
    with pytest.raises(ExecutorStateError, match="extend"):
        ex.extend([CHAMFER])
    assert ex.abandon_failed() == record
    assert ex.state is ExecutorState.completed
    assert [op.id for op in ex.plan.operations] == ["base_sketch", "base", "hole_1", "holes"]
    ex.extend([CHAMFER])
    assert ex.next_operation().status is StepStatus.success
    # the failed record stays in the trace as evidence, so undo_to lists it as superseded too
    assert ex.undo_to("holes") == ("edge_break", "broken")


def test_abandon_failed_is_refused_unless_failed(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    with pytest.raises(ExecutorStateError, match="abandon_failed"):
        ex.abandon_failed()
