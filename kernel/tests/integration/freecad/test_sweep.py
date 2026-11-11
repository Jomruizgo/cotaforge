"""``sweep`` against the real FreeCAD worker (PartDesign::AdditivePipe).

Every test drives an IR plan through ``Executor`` + ``Verifier`` (so the Level 2 checks run on real FreeCAD answers)
and compares volume, bounding box and faces with analytic values derived by hand. The kernel's own bounding box is
loose on curved swept faces (measured: fillet -7.06/107.06 against the true -5/105), which is why bent sweeps assert
``bbox_contains`` (a lower bound) and only report the exact mitre box.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import Empty, FaceRequest, MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.ir.errors import PlanError
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

XY = {"kind": "plane", "name": "XY"}
AREA = 25.0 * math.pi  # circle D10
L_PATH = [[0, 0, 0], [0, 0, 100], [100, 0, 100]]
FILLET_LENGTH = 200.0 - 40.0 + 10.0 * math.pi  # R20 on the 90 degree corner = 191.41592653...
PLATE = 100.0 * 60.0 * 8.0


def _sketch(entity: dict[str, Any], on: dict[str, Any] | None = None, op_id: str = "sec") -> dict[str, Any]:
    return {"id": op_id, "op": "sketch", "intent": "section", "on": on or XY, "entities": [entity]}


CIRCLE = {"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}


def _sweep(path: list | None = None, radius: float | None = None, sketch: str = "sec", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"points": path or L_PATH}
    if radius is not None:
        body["corner_radius_mm"] = radius
    return {"id": "tube", "op": "sweep", "intent": "tube", "sketch": sketch, "path": body, **extra}


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "sweeps", "kind": "part", "operations": list(operations)})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end()


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


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None, op_id
    return {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok}


def _result(trace: ExecutionTrace, op_id: str) -> Any:
    step = trace.step(op_id)
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    return step.attempts[-1].result


def _check(trace: ExecutionTrace, op_id: str, name: str) -> Any:
    step = trace.step(op_id)
    assert step is not None
    return next(c for c in step.last_level2() if c.name == name)


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _plan_error(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> str:
    _, trace = _run(adapter, *operations)
    step = trace.step("tube")
    assert step is not None and step.status is StepStatus.plan_error, step and step.status
    assert not step.attempts  # never reached the adapter
    return str(step.plan_error)


# --- the reference shapes ------------------------------------------------------------------------------------------


def test_straight_tube_is_exact(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _sketch(CIRCLE), _sweep([[0, 0, 0], [0, 0, 100]]))
    _assert_green(trace)
    assert _asserted_ok(trace, "tube") == {"volume_delta", "new_faces_count", "bbox_after"}
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(AREA * 100.0, abs=1e-3)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (-5, -5, 0)) and _close(trace.final_bbox.max, (5, 5, 100))
    assert len(_result(trace, "tube").created_faces) == 3


def test_mitred_l_bend(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _sketch(CIRCLE), _sweep())
    _assert_green(trace)
    assert _asserted_ok(trace, "tube") == {"volume_delta", "new_faces_count", "bbox_contains"}
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(AREA * 200.0, abs=1e-3)
    assert len(_result(trace, "tube").created_faces) == 4
    exact = _check(trace, "tube", "bbox_after")  # reported only: the analytic mitre box
    assert exact.kind is CheckKind.reported
    assert _close(exact.expected.min, (-5, -5, 0)) and _close(exact.expected.max, (100, 5, 105))  # type: ignore[union-attr]


def test_oblique_rectangular_mitre_in_3d(adapter: FreeCADAdapter) -> None:
    axis = {
        "id": "ax",
        "op": "ref_axis",
        "intent": "first leg",
        "define": {"how": "two_points", "points": [[0, 0, 0], [60, 0, 80]]},
    }
    plane = {
        "id": "dp",
        "op": "ref_plane",
        "intent": "section plane",
        "define": {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "ax"}, "at_mm": 0},
    }
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 10, "height": 4}
    path = [[0, 0, 0], [60, 0, 80], [60, 50, 80]]
    _, trace = _run(adapter, axis, plane, _sketch(rect, {"kind": "plane", "from_op": "dp"}), _sweep(path))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(40.0 * 150.0, abs=1e-3)  # A x (100 + 50)
    assert {"volume_delta", "bbox_contains"} <= _asserted_ok(trace, "tube")
    step = trace.step("tube")
    assert step is not None
    assert "new_faces_count" not in {c.name for c in step.last_level2()}  # rect faces merge across bends


def test_filleted_bend_r20(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _sketch(CIRCLE), _sweep(radius=20.0))
    _assert_green(trace)
    assert _asserted_ok(trace, "tube") == {"volume_delta", "new_faces_count", "bbox_contains"}
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(AREA * FILLET_LENGTH, abs=1e-3)
    assert trace.final_mass.volume_mm3 == pytest.approx(15033.772, abs=1e-3)
    assert len(_result(trace, "tube").created_faces) == 5


# --- composition ---


def test_sweep_merged_into_a_pad_adds_area_times_length(adapter: FreeCADAdapter) -> None:
    base = _sketch({"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}, op_id="s0")
    plate = {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s0", "depth_mm": 8}
    on_top = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
    boss = _sketch({"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}, on_top)
    _, trace = _run(adapter, base, plate, boss, _sweep([[50, 30, 8], [50, 30, 48]]))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE + AREA * 40.0, abs=1e-3)
    assert _check(trace, "tube", "volume_delta").observed == pytest.approx(AREA * 40.0, abs=1e-3)
    assert _check(trace, "tube", "new_faces_count").kind is CheckKind.reported  # merged into an existing body


def test_sweep_then_copy_body(adapter: FreeCADAdapter) -> None:
    copy = {
        "id": "twin",
        "op": "copy_body",
        "intent": "twin",
        "source": {"kind": "body", "from_op": "tube"},
        "offset": [0, 100, 0],
    }
    _, trace = _run(adapter, _sketch(CIRCLE), _sweep(), copy)
    _assert_green(trace)
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(2 * AREA * 200.0, abs=1e-2)
    assert {"volume_delta", "copy_centroid", "source_centroid"} <= _asserted_ok(trace, "twin")


def test_undo_to_the_section_sketch_removes_the_sweep(adapter: FreeCADAdapter) -> None:
    executor, trace = _run(adapter, _sketch(CIRCLE), _sweep(radius=20.0))
    _assert_green(trace)
    assert executor.undo_to("sec") == ("tube",)
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(0.0)


# --- plan errors: refused before the adapter, with the fix in the message ---


def test_centroid_off_the_path_is_a_plan_error(adapter: FreeCADAdapter) -> None:
    off = {"kind": "circle", "name": "c", "center": [10, 0], "diameter": 10}
    message = _plan_error(adapter, _sketch(off), _sweep())
    assert "centroid" in message and "path start" in message


def test_profile_not_normal_to_the_first_segment_is_a_plan_error(adapter: FreeCADAdapter) -> None:
    message = _plan_error(adapter, _sketch(CIRCLE), _sweep([[0, 0, 0], [100, 0, 0], [100, 0, 100]]))
    assert "normal to the first path segment" in message


def test_radius_below_the_section_extent_is_a_plan_error(adapter: FreeCADAdapter) -> None:
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 10, "height": 5}  # extent 5 toward the bend
    message = _plan_error(adapter, _sketch(rect), _sweep(radius=4.0))
    assert "too small for the section" in message and "toward the bend centre" in message


def _bar(width: float, height: float) -> dict[str, Any]:
    return {"kind": "rect", "name": "r", "center": [0, 0], "width": width, "height": height}


def test_a_strap_bent_on_its_thin_side_builds_with_a_small_radius(adapter: FreeCADAdapter) -> None:
    # 5 x 40: the 5 mm side (x) lies in the bend plane (up Z, then along X), so the section reaches 2.5 toward the
    # centre; R3 is valid although the half-diagonal is 20.2
    _, trace = _run(adapter, _sketch(_bar(5, 40)), _sweep(radius=3.0))
    _assert_green(trace)
    assert trace.final_mass is not None
    length = 200.0 - 2.0 * 3.0 + 3.0 * math.pi / 2.0
    assert trace.final_mass.volume_mm3 == pytest.approx(200.0 * length, abs=1e-3)


def test_a_strap_bent_on_its_wide_side_needs_a_large_radius(adapter: FreeCADAdapter) -> None:
    # 40 x 5: the section reaches 20 toward the bend centre, so R15 is refused and R21 builds (V = A x L)
    message = _plan_error(adapter, _sketch(_bar(40, 5)), _sweep(radius=15.0))
    assert "corner 1" in message and "reaches 20" in message
    _, trace = _run(adapter, _sketch(_bar(40, 5)), _sweep(radius=21.0))
    _assert_green(trace)
    assert trace.final_mass is not None
    length = 200.0 - 2.0 * 21.0 + 21.0 * math.pi / 2.0
    assert trace.final_mass.volume_mm3 == pytest.approx(200.0 * length, abs=1e-3)
    assert adapter.is_valid(Empty(operation_id="v")).valid is True


def test_a_circle_bends_at_just_over_its_radius(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _sketch(CIRCLE), _sweep(radius=5.2))  # 1.04 x r: inside the 1.02 margin
    _assert_green(trace)
    assert trace.final_mass is not None
    length = 200.0 - 2.0 * 5.2 + 5.2 * math.pi / 2.0
    assert trace.final_mass.volume_mm3 == pytest.approx(AREA * length, abs=1e-2)
    assert adapter.is_valid(Empty(operation_id="v")).valid is True


def test_a_straight_rectangular_bar_has_two_caps_and_four_sides(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _sketch(_bar(10, 4)), _sweep([[0, 0, 0], [0, 0, 100]]))
    _assert_green(trace)
    assert {"volume_delta", "new_faces_count", "bbox_after"} <= _asserted_ok(trace, "tube")
    assert len(_result(trace, "tube").created_faces) == 6


def test_a_tilted_profile_plane_beyond_the_position_tolerance_is_a_plan_error(adapter: FreeCADAdapter) -> None:
    tilt = math.radians(0.45)  # inside the 0.5 deg angle tolerance, but 5 sin(0.45 deg) = 0.039 mm > 0.01 mm
    path = [[0, 0, 0], [100 * math.sin(tilt), 0, 100 * math.cos(tilt)], [100, 0, 100]]
    message = _plan_error(adapter, _sketch(CIRCLE), _sweep(path))
    assert "off the sketch normal" in message and "exactly along the normal" in message


def test_a_sweep_inside_existing_material_says_what_to_change(adapter: FreeCADAdapter) -> None:
    base = _sketch({"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}, op_id="s0")
    plate = {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s0", "depth_mm": 8}
    inside = _sketch({"kind": "circle", "name": "c", "center": [50, 30], "diameter": 10}, op_id="s1")
    _, trace = _run(adapter, base, plate, inside, {**_sweep([[50, 30, 0], [50, 30, 6]], sketch="s1")})
    step = trace.step("tube")
    assert step is not None and step.status is StepStatus.failed
    assert "must not run through existing material" in str(step.attempts[-1].error)


def test_fillet_that_does_not_fit_is_a_plan_error(adapter: FreeCADAdapter) -> None:
    assert "does not fit" in _plan_error(adapter, _sketch(CIRCLE), _sweep(radius=150.0))


def test_self_touching_path_is_a_plan_error(adapter: FreeCADAdapter) -> None:
    loop = [[0, 0, 0], [0, 0, 100], [40, 0, 100], [40, 0, 6], [4, 0, 6]]
    assert "touch itself" in _plan_error(adapter, _sketch(CIRCLE), _sweep(loop))


def test_a_ring_section_is_rejected_when_the_plan_is_parsed() -> None:
    ring = {
        "id": "sec",
        "op": "sketch",
        "intent": "ring",
        "on": XY,
        "entities": [
            {"kind": "circle", "name": "o", "center": [0, 0], "diameter": 10},
            {"kind": "circle", "name": "i", "center": [0, 0], "diameter": 6},
        ],
    }
    with pytest.raises(PlanError, match="exactly ONE closed profile"):
        parse_plan({"id": "p", "kind": "part", "operations": [ring, _sweep()]})


def test_a_swept_face_can_be_queried(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _sketch(CIRCLE), _sweep(radius=20.0))
    _assert_green(trace)
    surfaces = sorted(
        adapter.face_info(FaceRequest(operation_id="q", face=f)).surface.value
        for f in _result(trace, "tube").created_faces
    )
    assert surfaces == ["cylinder", "cylinder", "plane", "plane", "torus"]
