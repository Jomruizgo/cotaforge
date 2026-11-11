"""A through-all cut or hole in a thin section of a taller part, against the real FreeCAD worker.

A 100 x 100 x 10 plate carries a 30 x 30 x 40 boss on its top (the part is 50 mm tall); the tools go through the plate
outside the boss, so they cross 10 mm of material. A C-section (two 10 mm arms 30 mm apart, 50 mm across) is
crossed by a cut and a hole that go through both arms. The verifier must accept what the CAD really does and judge the
material under the tool, not the height of the part.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

PLATE_VOLUME = 100.0 * 100.0 * 10.0
BOSS_VOLUME = 30.0 * 30.0 * 40.0
SLOT = 10.0 * 10.0
RADIUS = 4.0
DISC = math.pi * RADIUS**2

PLATE_WITH_BOSS: list[dict[str, Any]] = [
    {
        "id": "plate_sketch",
        "op": "sketch",
        "intent": "plate outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 100, "height": 100}],
    },
    {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 10},
    {
        "id": "boss_sketch",
        "op": "sketch",
        "intent": "boss outline on the plate top",
        "on": {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}},
        "entities": [{"kind": "rect", "name": "boss", "center": [0, 0], "width": 30, "height": 30}],
    },
    {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sketch", "depth_mm": 40},
]


def _line(name: str, start: list[float], end: list[float]) -> dict[str, Any]:
    return {"kind": "line", "name": name, "start": start, "end": end}


# plan view of a C opening towards +x: web x 0..10, arms y 0..10 and y 40..50 up to x = 60; 40 mm tall (z 0..40)
C_POINTS = [[0, 0], [60, 0], [60, 10], [10, 10], [10, 40], [60, 40], [60, 50], [0, 50]]
C_SECTION: list[dict[str, Any]] = [
    {
        "id": "c_sketch",
        "op": "sketch",
        "intent": "C-section outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [_line(f"l{i}", C_POINTS[i], C_POINTS[(i + 1) % len(C_POINTS)]) for i in range(len(C_POINTS))],
    },
    {"id": "c", "op": "extrude", "intent": "C-section", "sketch": "c_sketch", "depth_mm": 40},
]
C_VOLUME = (60 * 10 * 2 + 10 * 30) * 40.0


def _slot_sketch(on: dict[str, Any], center: list[float]) -> dict[str, Any]:
    entity = {"kind": "rect", "name": "slot", "center": center, "width": 10, "height": 10}
    return {"id": "slot_sketch", "op": "sketch", "intent": "slot", "on": on, "entities": [entity]}


def _cut(direction: str) -> dict[str, Any]:
    return {
        "id": "drill",
        "op": "cut",
        "intent": "slot through",
        "sketch": "slot_sketch",
        "end_condition": "through_all",
        "direction": direction,
    }


def _hole(face: dict[str, Any], position: list[float]) -> dict[str, Any]:
    return {
        "id": "drill",
        "op": "hole",
        "intent": "bore through",
        "face": face,
        "position": position,
        "standard": "simple",
        "diameter_mm": 2 * RADIUS,
        "end_condition": "through_all",
        "drill_point": "flat",
    }


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> ExecutionTrace:
    plan = parse_plan({"id": "thin_section", "kind": "part", "operations": list(operations)})
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
            [(c.name, c.expected, c.observed, c.message) for c in s.last_level2() if c.ok is False]
            if s.attempts
            else None,
        )
        for s in trace.steps
    ]
    assert trace.final_state == ExecutorState.completed, detail


def _check(trace: ExecutionTrace, name: str) -> Any:
    step = trace.step("drill")
    assert step is not None
    return next(c for c in step.last_level2() if c.name == name)


def _extent_is_the_material(trace: ExecutionTrace, thickness: float) -> None:
    extent = _check(trace, "cut_extent_along_n")
    assert extent.kind is CheckKind.asserted and extent.ok, (extent.expected, extent.observed, extent.message)
    assert extent.observed == pytest.approx(thickness, abs=1e-3)  # what FreeCAD really cut
    assert extent.expected == pytest.approx(thickness, abs=1e-3)  # a solid block: the material is the expectation


def test_a_cut_up_through_the_plate_outside_the_boss(adapter: FreeCADAdapter) -> None:
    sketch = _slot_sketch({"kind": "plane", "name": "XY"}, [35, 0])
    trace = _run(adapter, *PLATE_WITH_BOSS, sketch, _cut("positive"))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME - SLOT * 10.0, abs=1e-3)
    assert _check(trace, "volume_delta").kind is CheckKind.asserted
    _extent_is_the_material(trace, 10.0)


def test_a_cut_down_through_the_plate_outside_the_boss(adapter: FreeCADAdapter) -> None:
    top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1], "center": [0, 0, 10]}}
    trace = _run(adapter, *PLATE_WITH_BOSS, _slot_sketch(top, [35, 0]), _cut("negative"))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME - SLOT * 10.0, abs=1e-3)
    _extent_is_the_material(trace, 10.0)


def test_a_hole_up_through_the_plate_from_its_underside(adapter: FreeCADAdapter) -> None:
    bottom = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, -1]}}
    trace = _run(adapter, *PLATE_WITH_BOSS, _hole(bottom, [35, 0]))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME - DISC * 10.0, abs=1e-3)
    _extent_is_the_material(trace, 10.0)
    for name in ("hole_axis_direction", "hole_axis_position", "hole_radius", "new_faces_count"):
        assert _check(trace, name).kind is CheckKind.asserted, name  # a hole in solid material: its faces are promised


def test_a_hole_down_through_the_plate_outside_the_boss(adapter: FreeCADAdapter) -> None:
    top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1], "center": [0, 0, 10]}}
    trace = _run(adapter, *PLATE_WITH_BOSS, _hole(top, [35, 0]))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME + BOSS_VOLUME - DISC * 10.0, abs=1e-3)
    _extent_is_the_material(trace, 10.0)


def test_a_cut_across_both_arms_of_a_c_section(adapter: FreeCADAdapter) -> None:
    """The cut goes through two plates 10 mm thick with a 30 mm gap: 20 mm of material, walls that run 50 mm."""
    sketch = _slot_sketch({"kind": "plane", "name": "XZ"}, [40, -20])  # XZ: u = x, v = -z, normal +y
    trace = _run(adapter, *C_SECTION, sketch, _cut("positive"))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(C_VOLUME - SLOT * 20.0, abs=1e-3)
    assert _check(trace, "volume_delta").kind is CheckKind.asserted
    extent = _check(trace, "cut_extent_along_n")
    assert extent.kind is CheckKind.asserted and extent.ok, (extent.expected, extent.observed, extent.message)
    assert extent.observed == pytest.approx(50.0, abs=1e-3)


def test_a_hole_across_both_arms_of_a_c_section(adapter: FreeCADAdapter) -> None:
    side = {"kind": "face", "from_op": "c", "select": {"normal": [0, -1, 0], "center": [30, 0, 20]}}
    trace = _run(adapter, *C_SECTION, _hole(side, [10, 0]))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(C_VOLUME - DISC * 20.0, abs=1e-3)
    assert _check(trace, "volume_delta").kind is CheckKind.asserted
    extent = _check(trace, "cut_extent_along_n")
    assert extent.kind is CheckKind.asserted and extent.ok, (extent.expected, extent.observed, extent.message)
    assert _check(trace, "new_faces_count").kind is CheckKind.reported  # two walls, one per arm
