"""A hole placed by a world point (``at``) instead of ``position`` ((u, v) from the face centre), on the fake."""

from typing import Any

import pytest

from cotaforge.adapters.fake.adapter import FakeAdapter
from cotaforge.contract import ConnectRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.ir.errors import PlanError
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

# 100 x 60 x 8 plate, corner at the origin: the top face is centred at (50, 30, 8).
PLATE = [
    {
        "id": "s1",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
    },
    {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s1", "depth_mm": 8},
]


def _hole(**where: Any) -> dict[str, Any]:
    return {
        "id": "h1",
        "op": "hole",
        "intent": "hole",
        "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
        **where,
    }


def _run(hole: dict[str, Any]) -> ExecutionTrace:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    executor = Executor(
        parse_plan({"id": "p", "kind": "part", "operations": [*PLATE, hole]}), adapter, Verifier(), ExecutorConfig()
    )
    executor.start()
    return executor.run_to_end()


def test_a_world_point_drills_where_the_equivalent_face_position_does() -> None:
    by_position = _run(_hole(position=[-30, -20]))
    by_point = _run(_hole(at=[20, 10, 8]))
    assert by_point.final_state == ExecutorState.completed
    assert by_point.final_mass is not None and by_position.final_mass is not None
    assert by_point.final_mass.volume_mm3 == pytest.approx(by_position.final_mass.volume_mm3)
    assert by_point.final_mass.centroid == pytest.approx(by_position.final_mass.centroid)
    step = by_point.step("h1")
    assert step is not None
    assert {c.name for c in step.last_level2() if c.kind == CheckKind.asserted and c.ok} >= {"hole_axis_position"}


def test_the_point_is_projected_onto_the_face() -> None:
    far_above = _run(_hole(at=[20, 10, 80]))
    assert far_above.final_state == ExecutorState.completed
    assert far_above.final_mass == _run(_hole(at=[20, 10, 8])).final_mass


def test_a_point_off_the_face_says_by_how_much() -> None:
    trace = _run(_hole(at=[120, 10, 8]))
    step = trace.step("h1")
    assert step is not None and step.status is not StepStatus.success
    assert "20.000 mm outside" in (step.plan_error or "")


def test_position_and_at_are_exclusive() -> None:
    for where in ({"position": [0, 0], "at": [20, 10, 8]}, {}):
        with pytest.raises(PlanError, match="exactly one of position"):
            parse_plan({"id": "p", "kind": "part", "operations": [*PLATE, _hole(**where)]})
