"""A ``hole`` on an oblique planar face against the real FreeCAD worker.

The rod is extruded from a plane tilted 30 degrees about X, so its far cap has the normal ``(0, -sin30, cos30)``: not
a principal direction. The geometry was always right (the hole removes exactly ``pi r^2 d``); what used to fail was the
verification of a through-all hole, which compared a bounding-box extent that is only exact along a world axis.
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

ROD_VOLUME = math.pi * 20.0**2 * 100.0
CAP_NORMAL = [0.0, -0.5, math.sqrt(3.0) / 2.0]
HOLE_RADIUS = 3.0

TILT = {
    "id": "tilt",
    "op": "ref_plane",
    "intent": "XY rotated 30 degrees about X",
    "define": {
        "how": "angle",
        "base": {"kind": "plane", "name": "XY"},
        "about": {"origin": [0, 0, 0], "direction": [1, 0, 0]},
        "angle_deg": 30,
    },
}
ROD_SKETCH = {
    "id": "rod_sketch",
    "op": "sketch",
    "intent": "outer circle",
    "on": {"kind": "plane", "from_op": "tilt"},
    "entities": [{"kind": "circle", "name": "outer", "center": [0, 0], "diameter": 40}],
}
ROD = {"id": "rod", "op": "extrude", "intent": "rod", "sketch": "rod_sketch", "depth_mm": 100}


def _hole(end_condition: str, depth: float | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "drill",
        "op": "hole",
        "intent": "drill into the tilted far cap",
        "face": {"from_op": "rod", "select": {"surface": "plane", "normal": CAP_NORMAL}},
        "position": [0, 0],
        "standard": "simple",
        "diameter_mm": 2 * HOLE_RADIUS,
        "end_condition": end_condition,
        "drill_point": "flat",  # the tests ask for a flat bottom; FreeCAD's default is angled
    }
    if depth is not None:
        body["depth_mm"] = depth
    return body


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> ExecutionTrace:
    plan = parse_plan({"id": "oblique", "kind": "part", "operations": list(operations)})
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


def _check(trace: ExecutionTrace, name: str) -> Any:
    step = trace.step("drill")
    assert step is not None
    return next(c for c in step.last_level2() if c.name == name)


def test_blind_hole_on_a_tilted_cap_removes_exactly_pi_r2_d(adapter: FreeCADAdapter) -> None:
    trace = _run(adapter, TILT, ROD_SKETCH, ROD, _hole("blind", 10.0))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(ROD_VOLUME - math.pi * HOLE_RADIUS**2 * 10.0, abs=1e-3)
    volume = _check(trace, "volume_delta")
    assert volume.kind is CheckKind.asserted and volume.ok  # the real boolean, not a box: exact even when oblique
    assert _check(trace, "cut_volume_bound").kind is CheckKind.asserted
    assert _check(trace, "cut_volume_bound").ok


def test_through_all_hole_on_a_tilted_cap_is_not_falsely_rejected(adapter: FreeCADAdapter) -> None:
    trace = _run(adapter, TILT, ROD_SKETCH, ROD, _hole("through_all"))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(ROD_VOLUME - math.pi * HOLE_RADIUS**2 * 100.0, abs=1e-3)
    extent = _check(trace, "cut_extent_along_n")
    assert extent.kind is CheckKind.reported
    bound = _check(trace, "cut_volume_bound")
    assert bound.kind is CheckKind.asserted and bound.ok
    for name in ("hole_axis_direction", "hole_axis_position", "hole_radius"):
        assert _check(trace, name).kind is CheckKind.asserted and _check(trace, name).ok, name
