"""``Executor.record_external_step``: a step the CAD ran outside the plan is a step of the history."""

from typing import Any

import pytest

from cotaforge.contract import BoxRequest, MassRequest
from cotaforge.executor.errors import ExecutorStateError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from tests.unit.executor.test_extend_undo import CHAMFER, _executor, _volume


def external(ex: Executor, op_id: str, size: float = 5.0) -> None:
    """What a server does for a native step: run it on the adapter, measure, record."""
    before = ex.adapter.mass_properties(MassRequest(operation_id="before"))
    made = ex.adapter.box(BoxRequest(operation_id=op_id, size=(size, size, size)))
    after = ex.adapter.mass_properties(MassRequest(operation_id="after"))
    ex.record_external_step(op_id, "native", feature=made.feature, mass_before=before, mass_after=after)


def test_an_external_step_is_in_the_history_and_undone_with_it(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    plate = _volume(ex)
    external(ex, "nat")
    assert _volume(ex) == pytest.approx(plate + 125.0)
    steps = [s.op_id for s in ex.trace().active_steps()]
    assert steps == ["base_sketch", "base", "hole_1", "holes", "nat"]
    assert ex.trace().steps[-1].anchor == 4 and ex.trace().steps[0].anchor is None

    assert ex.undo_to("holes") == ("nat",)
    assert _volume(ex) == pytest.approx(plate)
    assert ex.cursor == 4


def test_plan_steps_and_external_steps_interleave(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    plate = _volume(ex)
    external(ex, "first")
    ex.extend([CHAMFER])
    assert ex.next_operation().status is StepStatus.success
    external(ex, "second", 2.0)
    assert [s.op_id for s in ex.trace().active_steps()][-3:] == ["first", "edge_break", "second"]

    # back to the first native step: the chamfer and the second native step go, the first stays
    assert ex.undo_to("first") == ("second", "edge_break")
    assert ex.cursor == 4 and ex.state is ExecutorState.paused
    assert _volume(ex) == pytest.approx(plate + 125.0)
    # a plan operation added now replaces the stale tail and runs after the native step
    ex.extend([CHAMFER])
    assert ex.next_operation().status is StepStatus.success
    assert ex.undo_to("holes") == ("edge_break", "first")
    assert ex.cursor == 4 and _volume(ex) == pytest.approx(plate)


def test_undo_to_the_start_takes_external_steps_too(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.next_operation()
    ex.next_operation()
    external(ex, "nat")
    ex.pause_now()
    assert ex.undo_to(None) == ("nat", "base", "base_sketch")
    assert ex.cursor == 0 and _volume(ex) == pytest.approx(0.0)


def test_the_anchor_survives_a_round_trip_of_the_trace(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    external(ex, "nat")
    again = ExecutionTrace.model_validate(ex.trace().model_dump(mode="json"))
    assert again.steps[-1].anchor == 4 and again == ex.trace()


def test_an_external_step_needs_a_fresh_id_and_a_quiet_executor(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan)
    ex.run_to_end()
    external(ex, "nat")
    with pytest.raises(ExecutorStateError, match="already a step"):
        external(ex, "nat")
    with pytest.raises(ExecutorStateError, match="already a step"):
        external(ex, "base")
    ex.state = ExecutorState.failed  # a failed plan must be dealt with first (abandon_failed)
    with pytest.raises(ExecutorStateError, match="record_external_step"):
        external(ex, "late")
