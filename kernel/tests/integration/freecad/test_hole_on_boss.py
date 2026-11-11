"""A ``hole`` on the top face of a boss padded on a base plate, against the real FreeCAD worker.

The boss is merged into the plate, so a hole through it crosses boss + plate along the axis: the verifier asserts
pi r^2 x 25 for a through-all hole. The plate's own top face, aimed at a spot the boss covers, is refused up front.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

PLATE_VOLUME = 80.0 * 60.0 * 10.0
BOSS_VOLUME = 30.0 * 30.0 * 15.0
RADIUS = 4.0
DISC = math.pi * RADIUS**2

# plate 80 x 60 x 10 centred on the XY origin; boss 30 x 30 x 15 on its top face (z 10..25), centred at x=0, y=0
PART: list[dict[str, Any]] = [
    {
        "id": "plate_sketch",
        "op": "sketch",
        "intent": "plate outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 80, "height": 60}],
    },
    {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 10},
    {
        "id": "boss_sketch",
        "op": "sketch",
        "intent": "boss outline on the plate top",
        "on": {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}},
        "entities": [{"kind": "rect", "name": "boss", "center": [0, 0], "width": 30, "height": 30}],
    },
    {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sketch", "depth_mm": 15},
]


def _hole(end_condition: str, where: dict[str, Any], depth: float | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "drill",
        "op": "hole",
        "intent": "drill the boss top",
        "face": {"kind": "face", "from_op": "boss", "select": {"normal": [0, 0, 1], "center": [0, 0, 25]}},
        "standard": "simple",
        "diameter_mm": 2 * RADIUS,
        "end_condition": end_condition,
        "drill_point": "flat",  # the tests ask for a flat bottom; FreeCAD's default is angled
        **where,
    }
    if depth is not None:
        body["depth_mm"] = depth
    return body


def _run(adapter: FreeCADAdapter, hole: dict[str, Any]) -> ExecutionTrace:
    plan = parse_plan({"id": "boss_hole", "kind": "part", "operations": [*PART, hole]})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor.run_to_end()


def _assert_green(trace: ExecutionTrace) -> None:
    detail = [
        (
            s.op_id,
            s.status.value,
            s.plan_error,
            s.attempts[-1].error if s.attempts else None,
            [(c.name, c.expected, c.observed) for c in s.last_level2() if c.ok is False] if s.attempts else None,
        )
        for s in trace.steps
        if s.status is not StepStatus.success
    ]
    assert trace.final_state is ExecutorState.completed, detail
    assert all(s.status is StepStatus.success for s in trace.steps), detail


WHERES = [
    pytest.param({"position": [5, 3]}, id="position"),
    pytest.param({"at": [5, 3, 25]}, id="at"),
]


@pytest.mark.parametrize("kind", ["blind", "through_all"])
@pytest.mark.parametrize("where", WHERES)
def test_a_hole_aimed_through_the_face_under_the_boss_is_refused_before_it_cuts(
    adapter: FreeCADAdapter, where: dict[str, Any], kind: str
) -> None:
    """The plate's top face spans the box of the plate, but its centre is covered by the boss: a hole there starts
    inside the part. It used to run and then fail the verification with a bare count mismatch."""
    hole = _hole(kind, where, depth=10.0 if kind == "blind" else None)
    hole["face"] = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    trace = _run(adapter, hole)
    step = trace.step("drill")
    assert step is not None and step.status is not StepStatus.success
    assert "is not on face" in f"{step.plan_error} {step.attempts[-1].error if step.attempts else ''}"
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME, abs=1e-3)


def test_a_hole_on_the_plate_top_beside_the_boss_is_fine(adapter: FreeCADAdapter) -> None:
    hole = _hole("through_all", {"position": [-30, 0]})
    hole["face"] = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    trace = _run(adapter, hole)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME - DISC * 10.0, abs=1e-3)


@pytest.mark.parametrize("where", WHERES)
def test_blind_hole_on_the_boss_top_removes_pi_r2_d(adapter: FreeCADAdapter, where: dict[str, Any]) -> None:
    trace = _run(adapter, _hole("blind", where, depth=10.0))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME - DISC * 10.0, abs=1e-3)
    step = trace.step("drill")
    assert step is not None
    volume = next(c for c in step.last_level2() if c.name == "volume_delta")
    assert volume.kind is CheckKind.asserted and volume.ok


@pytest.mark.parametrize("where", WHERES)
def test_through_all_hole_on_the_boss_top_crosses_boss_and_plate(
    adapter: FreeCADAdapter, where: dict[str, Any]
) -> None:
    trace = _run(adapter, _hole("through_all", where))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME - DISC * 25.0, abs=1e-3)
    step = trace.step("drill")
    assert step is not None
    extent = next(c for c in step.last_level2() if c.name == "cut_extent_along_n")
    assert extent.ok is not False
    for name in ("hole_axis_direction", "hole_axis_position", "hole_radius"):
        assert any(c.name == name and c.kind is CheckKind.asserted and c.ok for c in step.last_level2()), name
