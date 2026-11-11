"""Review fixes for the radial hole, against the real FreeCAD worker: a hole whose circle leaves the cylindrical face is
refused (never a silent notch), and the same plan drills the same world side of a tube whatever sense the face reports
for its axis (a tube extruded along +Z, along -Z, swept along -Z or rotated 180 degrees)."""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import FaceRequest
from cotaforge.executor.trace import ExecutionTrace, ExecutorState
from tests.integration.freecad.test_holes_radial import (
    XY,
    _assert_green,
    _error_message,
    _run,
)

pytestmark = pytest.mark.freecad

ROD_WALL = {"surface": "cylinder", "radius": 10}


def _rod(radius: float = 10.0, **extrude: Any) -> list[dict[str, Any]]:
    return [
        {
            "id": "rod_sketch",
            "op": "sketch",
            "intent": "section",
            "on": XY,
            "entities": [{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 2 * radius}],
        },
        {"id": "rod", "op": "extrude", "intent": "rod", "sketch": "rod_sketch", "depth_mm": 100, **extrude},
    ]


def _half_rod() -> list[dict[str, Any]]:
    """A D20 rod cut in half along y = 0: its outer face covers 0..180 degrees only (the y >= 0 half)."""
    return [
        *_rod(),
        {
            "id": "half_sketch",
            "op": "sketch",
            "intent": "the half to remove",
            "on": XY,
            "entities": [{"kind": "rect", "name": "r", "center": [0, -10], "width": 40, "height": 20}],
        },
        {
            "id": "half",
            "op": "cut",
            "intent": "keep y >= 0",
            "sketch": "half_sketch",
            "end_condition": "through_all",
            "direction": "positive",
        },
    ]


def _hole(position: list[float], diameter: float = 3.0, depth: float = 5.0, end: str = "blind") -> dict[str, Any]:
    hole: dict[str, Any] = {
        "id": "cross",
        "op": "hole",
        "intent": "cross hole",
        "face": {"kind": "face", "from_op": "rod", "select": ROD_WALL},
        "position": position,
        "standard": "simple",
        "diameter_mm": diameter,
        "end_condition": end,
    }
    if end == "blind":
        hole["depth_mm"] = depth
    return hole


def _volume(trace: ExecutionTrace) -> float:
    assert trace.final_mass is not None
    return trace.final_mass.volume_mm3


# --- a hole that leaves the face is refused --------------------------------------------------------------------


def test_a_hole_on_the_end_edge_of_a_rod_is_refused_not_cut_as_a_notch(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *_rod(), _hole([50, 0]))
    assert trace.final_state is ExecutorState.failed
    step = trace.step("cross")
    assert step is not None and not step.attempts  # the expectation refuses it before the adapter is called
    assert "axial end" in str(step.plan_error), step.plan_error
    assert abs(_volume(trace) - math.pi * 100.0 * 100.0) < 1e-3  # the rod is untouched


@pytest.mark.parametrize("angle", [0, 5])
def test_a_hole_on_the_straight_edge_of_a_half_cylinder_is_refused(adapter: FreeCADAdapter, angle: float) -> None:
    """Angle 0 is exactly the edge of the 0..180 degree face (half a notch); 5 degrees (0.9 mm of arc) is still
    inside the face for the centre but the D3 circle crosses the edge."""
    _, trace = _run(adapter, *_half_rod(), _hole([0, angle]))
    assert trace.final_state is ExecutorState.failed
    message = _error_message(trace, "cross")
    assert "not fully on face" in message and "angular edge" in message, message


def test_a_hole_whose_centre_is_off_the_face_names_the_angle_not_the_axial_range(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *_half_rod(), _hole([0, 270]))  # the removed side of the half rod
    assert trace.final_state is ExecutorState.failed
    message = _error_message(trace, "cross")
    assert "angle_deg" in message and "+-50" not in message, message


def test_a_hole_whose_centre_is_beyond_the_axial_end_names_the_axial_range(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *_rod(), _hole([80, 0]))
    message = _error_message(trace, "cross")
    assert "axial" in message and "+-50" in message, message


def test_a_hole_that_fits_on_the_face_is_still_drilled(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *_rod(), _hole([47, 0]))  # 1.5 mm clear of the end edge
    _assert_green(trace)
    _, trace = _run(adapter, *_half_rod(), _hole([0, 90]))
    _assert_green(trace)


# --- the same plan drills the same side whatever sense the axis is reported with -----------------------------


def _wall_direction(adapter: FreeCADAdapter, trace: ExecutionTrace) -> tuple[float, float, float]:
    step = trace.step("cross")
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    faces = [adapter.face_info(FaceRequest(operation_id="q", face=h)) for h in step.attempts[-1].result.created_faces]
    wall = next(f for f in faces if f.surface.value == "cylinder")
    return wall.center


BUILDS = {
    "extruded_up": lambda: _rod(),
    "extruded_down": lambda: _rod(direction="negative"),
    "swept_down": lambda: [
        _rod()[0],
        {
            "id": "rod",
            "op": "sweep",
            "intent": "rod swept down",
            "sketch": "rod_sketch",
            "path": {"points": [[0, 0, 0], [0, 0, -100]]},
        },
    ],
    "rotated_180": lambda: [
        *_rod(),
        {
            "id": "flip",
            "op": "rotate",
            "intent": "turn it upside down",
            "target": {"kind": "body", "from_op": "rod"},
            "axis": "X",
            "angle_deg": 180,
        },
    ],
}


@pytest.mark.parametrize("angle", [0, 45, 135, -90])
@pytest.mark.parametrize("build", list(BUILDS))
def test_the_angle_means_the_same_world_side_for_every_build_direction(
    adapter: FreeCADAdapter, build: str, angle: float
) -> None:
    _, trace = _run(adapter, *BUILDS[build](), _hole([0, angle], diameter=3.0, depth=4.0))
    _assert_green(trace)
    cx, cy, _ = _wall_direction(adapter, trace)
    expected = (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
    along = cx * expected[0] + cy * expected[1]
    across = -cx * expected[1] + cy * expected[0]
    assert 6.0 <= along <= 10.0 + 1e-6, (build, angle, (cx, cy))
    assert abs(across) < 0.05, (build, angle, (cx, cy))


@pytest.mark.parametrize("build", ["extruded_up", "extruded_down", "swept_down"])
def test_the_axial_offset_follows_the_same_world_direction_for_every_build_direction(
    adapter: FreeCADAdapter, build: str
) -> None:
    """Axial is measured along the canonical (upward) axis: +20 is 20 mm above the middle of the rod, whatever the
    sense its axis is reported with."""
    _, trace = _run(adapter, *BUILDS[build](), _hole([20, 0], diameter=3.0, depth=4.0))
    _assert_green(trace)
    wall_z = _wall_direction(adapter, trace)[2]
    middle = 50.0 if build == "extruded_up" else -50.0
    assert abs(wall_z - (middle + 20.0)) < 0.05, (build, wall_z)


@pytest.mark.parametrize("axial", [48.5, -48.5, 48.46])
def test_a_hole_tangent_to_the_end_of_the_face_is_refused_with_the_largest_allowed_offset(
    adapter: FreeCADAdapter, axial: float
) -> None:
    """The D3 circle at 48.5 touches the end edge exactly (48.46 is 0.04 mm short of the 0.05 mm clearance): the
    kernel makes a degenerate edge there that later fails in observation, so the position is refused up front."""
    _, trace = _run(adapter, *_rod(), _hole([axial, 0]))
    assert trace.final_state is ExecutorState.failed
    message = _error_message(trace, "cross")
    assert "axial end" in message and "keep |axial_mm| <= 48.45" in message, message
    assert abs(_volume(trace) - math.pi * 100.0 * 100.0) < 1e-3


def test_a_hole_just_inside_the_clearance_is_drilled(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *_rod(), _hole([48.4, 0]))
    _assert_green(trace)
