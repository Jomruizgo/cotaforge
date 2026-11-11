"""ref_plane / ref_axis against the real FreeCAD worker (datum planes and axes, fixed pose).

Every test drives a full IR plan through ``Executor`` + ``Verifier`` (so the Level-2 datum pose checks run on
real FreeCAD answers) and compares mass, bounding box and face info with analytic values. The datum steps
themselves must be geometry-neutral: same volume before and after.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    CircleEntity,
    DatumAxisRequest,
    DatumPlaneRequest,
    DatumPlaneTarget,
    Empty,
    FeatureRequest,
    MassRequest,
    SketchFrame,
    SketchRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

S30 = math.sin(math.radians(30.0))
C30 = math.cos(math.radians(30.0))
XY = {"kind": "plane", "name": "XY"}
PLATE = 100.0 * 60.0 * 8.0


def _ir(*operations: dict[str, Any]) -> Any:
    return parse_plan({"id": "datums", "kind": "part", "operations": list(operations)})


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    executor = Executor(_ir(*operations), adapter, Verifier(), ExecutorConfig())
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


def _sketch(op_id: str, on: dict[str, Any], *entities: dict[str, Any]) -> dict[str, Any]:
    return {"id": op_id, "op": "sketch", "intent": "i", "on": on, "entities": list(entities)}


def _rect(cx: float, cy: float, w: float, h: float, name: str = "r") -> dict[str, Any]:
    return {"kind": "rect", "name": name, "center": [cx, cy], "width": w, "height": h}


def _circle(cx: float, cy: float, diameter: float, name: str = "c") -> dict[str, Any]:
    return {"kind": "circle", "name": name, "center": [cx, cy], "diameter": diameter}


def _extrude(op_id: str, sketch: str, depth: float, **extra: Any) -> dict[str, Any]:
    return {"id": op_id, "op": "extrude", "intent": "i", "sketch": sketch, "depth_mm": depth, **extra}


def _plate() -> list[dict[str, Any]]:
    return [_sketch("s0", XY, _rect(50, 30, 100, 60)), _extrude("base", "s0", 8)]


def _feature(trace: ExecutionTrace, op_id: str) -> Any:
    step = trace.step(op_id)
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    return step.attempts[-1].result.feature


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _tilt(op_id: str = "tilt", angle: float = 30.0) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "ref_plane",
        "intent": "i",
        "define": {
            "how": "angle",
            "base": XY,
            "about": {"origin": [0, 0, 0], "direction": [1, 0, 0]},
            "angle_deg": angle,
        },
    }


# --- offset ---------------------------------------------------------------------------------------------------


def test_offset_datum_keeps_the_volume_and_a_boss_sketched_on_it_extrudes_from_there(adapter: FreeCADAdapter) -> None:
    """A datum 20 mm above the plate: the boss is a detached body (merge=False) so its faces are all new. A
    datum coplanar with an existing face is a known limitation, see the module notes in the PR."""
    _, trace = _run(
        adapter,
        *_plate(),
        {"id": "dp", "op": "ref_plane", "intent": "i", "define": {"how": "offset", "base": XY, "distance_mm": 20}},
        _sketch("s1", {"kind": "plane", "from_op": "dp"}, _rect(50, 30, 20, 20)),
        _extrude("boss", "s1", 5, merge=False),
    )
    _assert_green(trace)
    dp = trace.step("dp")
    assert dp is not None and dp.mass_after is not None
    assert dp.mass_after.volume_mm3 == pytest.approx(PLATE, abs=1e-6)  # the datum step changed nothing
    assert {"volume_delta", "datum_origin", "datum_u", "datum_normal"} <= _asserted_ok(trace, "dp")
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE + 20 * 20 * 5, abs=1e-3)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (0, 0, 0), 1e-6) and _close(trace.final_bbox.max, (100, 60, 25), 1e-6)
    boss = _feature(trace, "boss")
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=boss)).faces
    cap = next(f for f in faces if f.normal is not None and _close(f.normal, (0, 0, 1)))
    assert cap.center[2] == pytest.approx(25.0, abs=1e-6)
    assert cap.area_mm2 == pytest.approx(400.0, abs=1e-6)


def test_offset_from_a_face_moves_along_the_outward_normal(adapter: FreeCADAdapter) -> None:
    """Phase A assumed a positive offset from a face goes along FaceInfo.normal (outward). Real FreeCAD says so:
    +5 from the top face (z = 8) is z = 13, and a detached slab there has its base at z = 13."""
    top = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
    _, trace = _run(
        adapter,
        *_plate(),
        {"id": "dp", "op": "ref_plane", "intent": "i", "define": {"how": "offset", "base": top, "distance_mm": 5}},
        _sketch("s1", {"kind": "plane", "from_op": "dp"}, _rect(0, 0, 10, 10)),
        _extrude("slab", "s1", 4, merge=False),
    )
    _assert_green(trace)
    slab = _feature(trace, "slab")
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=slab)).faces
    zs = sorted(f.center[2] for f in faces if f.normal is not None and abs(f.normal[2]) > 0.99)
    assert zs == pytest.approx([13.0, 17.0], abs=1e-6)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE + 10 * 10 * 4, abs=1e-3)


# --- angle ----------------------------------------------------------------------------------------------------


def test_angle_datum_rod_has_the_exact_volume_and_axis(adapter: FreeCADAdapter) -> None:
    _, trace = _run(
        adapter,
        _tilt(),
        _sketch("s1", {"kind": "plane", "from_op": "tilt"}, _circle(0, 0, 10)),
        _extrude("rod", "s1", 40),
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(math.pi * 25.0 * 40.0, rel=1e-9)
    rod = _feature(trace, "rod")
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=rod)).faces
    side = next(f for f in faces if f.axis is not None)
    assert side.radius == pytest.approx(5.0, abs=1e-9)
    n = (0.0, -S30, C30)
    assert abs(sum(a * b for a, b in zip(side.axis.direction, n, strict=True))) == pytest.approx(1.0, abs=1e-9)
    # the far cap is 40 mm from the origin along n
    caps = [f for f in faces if f.normal is not None]
    far = max(caps, key=lambda f: f.center[2])
    assert _close(far.center, (0.0, -40 * S30, 40 * C30), 1e-6)
    assert _close(far.normal, n, 1e-9)


# --- three_points / midplane ----------------------------------------------------------------------------------


def test_three_points_datum_matches_the_plane_through_them(adapter: FreeCADAdapter) -> None:
    """Points (0,0,0), (50,0,0), (0,30,30): the plane contains X and rises 45 degrees; normal (0,-1,1)/sqrt2."""
    _, trace = _run(
        adapter,
        {
            "id": "dp",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "three_points", "points": [[0, 0, 0], [50, 0, 0], [0, 30, 30]]},
        },
        _sketch("s1", {"kind": "plane", "from_op": "dp"}, _rect(0, 0, 10, 10)),
        _extrude("slab", "s1", 6),
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(600.0, rel=1e-9)
    slab = _feature(trace, "slab")
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=slab)).faces
    normal = (0.0, -1 / math.sqrt(2), 1 / math.sqrt(2))
    far = next(f for f in faces if f.normal is not None and _close(f.normal, normal, 1e-9))
    assert _close(far.center, tuple(6 * c for c in normal), 1e-6)


def test_midplane_datum_sits_halfway_between_a_face_and_another_datum(adapter: FreeCADAdapter) -> None:
    top = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
    _, trace = _run(
        adapter,
        *_plate(),
        {"id": "high", "op": "ref_plane", "intent": "i", "define": {"how": "offset", "base": XY, "distance_mm": 40}},
        {
            "id": "mid",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "midplane", "a": top, "b": {"kind": "plane", "from_op": "high"}},
        },
        _sketch("s1", {"kind": "plane", "from_op": "mid"}, _rect(0, 0, 10, 10)),
        _extrude("slab", "s1", 2, merge=False),
    )
    _assert_green(trace)
    slab = _feature(trace, "slab")
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=slab)).faces
    zs = sorted(f.center[2] for f in faces if f.normal is not None and abs(f.normal[2]) > 0.99)
    assert zs == pytest.approx([24.0, 26.0], abs=1e-6)  # halfway between the top face (z = 8) and the datum (z = 40)


# --- normal_to + ref_axis(entity) -----------------------------------------------------------------------------


def _inclined_tube() -> list[dict[str, Any]]:
    """A tube 30 degrees off Z: a solid rod O40 on a datum tilted 30 degrees about X, 100 mm long, then bored O30
    from its far end with a sketch on a plane normal to the rod's own axis (ref_axis on its cylinder + ref_plane).
    (A ring sketch is not verifiable: the expected-value model reads two circles as two profiles.)"""
    return [
        _tilt(),
        _sketch("rod_sketch", {"kind": "plane", "from_op": "tilt"}, _circle(0, 0, 40, "outer")),
        _extrude("rod", "rod_sketch", 100),
        {
            "id": "tube_axis",
            "op": "ref_axis",
            "intent": "i",
            "define": {
                "how": "entity",
                "entity": {"kind": "face", "from_op": "rod", "select": {"surface": "cylinder", "radius": 20}},
            },
        },
        {
            "id": "far_end",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "tube_axis"}, "at_mm": 100},
        },
        _sketch("bore_sketch", {"kind": "plane", "from_op": "far_end"}, _circle(0, 0, 30, "inner")),
        {
            "id": "tube",
            "op": "cut",
            "intent": "i",
            "sketch": "bore_sketch",
            "end_condition": "through_all",
            "direction": "negative",
        },
    ]


def test_normal_to_the_tube_axis_gives_a_plane_perpendicular_to_it(adapter: FreeCADAdapter) -> None:
    _, trace = _run(
        adapter,
        *_inclined_tube(),
        {
            "id": "section",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "tube_axis"}, "at_mm": 0},
        },
    )
    _assert_green(trace)
    axis_step = trace.step("tube_axis")
    section_step = trace.step("section")
    assert axis_step is not None and section_step is not None
    axis = axis_step.attempts[-1].request["axis"]
    frame = section_step.attempts[-1].request["frame"]
    n = (0.0, -S30, C30)
    # the axis of an extruded cylinder points along the extrusion direction (n of the tilted datum) ...
    assert _close(axis["direction"], n, 1e-9)
    assert _close(frame["n"], axis["direction"], 1e-9)
    # ... and, re-anchored by ref_axis(entity), starts at the point of the line nearest the world origin: the tube base
    assert _close(axis["origin"], (0.0, 0.0, 0.0), 1e-6)
    assert _close(frame["origin"], (0.0, 0.0, 0.0), 1e-6)
    assert {"datum_axis_position", "datum_axis_direction"} <= _asserted_ok(trace, "tube_axis")
    assert {"datum_origin", "datum_u", "datum_normal"} <= _asserted_ok(trace, "section")


def test_axis_of_a_linear_edge_comes_from_its_endpoints(adapter: FreeCADAdapter) -> None:
    """Phase A assumed EdgeInfo.axis is None for a straight edge and the executor falls back to start -> end.
    Real FreeCAD agrees: the top/+X edge of the plate is the line x = 100, z = 8 along Y."""
    top = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
    side = {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}}
    _, trace = _run(
        adapter,
        *_plate(),
        {
            "id": "edge_axis",
            "op": "ref_axis",
            "intent": "i",
            "define": {"how": "entity", "entity": {"kind": "edge", "between": [top, side], "curve": "line"}},
        },
    )
    _assert_green(trace)
    step = trace.step("edge_axis")
    assert step is not None
    axis = step.attempts[-1].request["axis"]
    assert abs(axis["direction"][1]) == pytest.approx(1.0, abs=1e-9)
    assert (axis["origin"][0], axis["origin"][2]) == pytest.approx((100.0, 8.0), abs=1e-9)
    assert {"datum_axis_position", "datum_axis_direction"} <= _asserted_ok(trace, "edge_axis")


def _cross_hole_volume(outer: float, inner: float, hole_radius: float) -> float:
    """Volume of a tube (radii ``inner`` / ``outer``) removed by a hole of ``hole_radius`` drilled straight
    through both walls across its axis: I(R) = integral of 4 sqrt((R^2 - y^2)(r^2 - y^2)) dy, y = r sin(t)."""

    def steinmetz(radius: float, steps: int = 20000) -> float:
        r = hole_radius
        h = math.pi / steps
        total = 0.0
        for i in range(steps + 1):
            t = -math.pi / 2 + i * h
            value = 4.0 * math.sqrt(radius**2 - (r * math.sin(t)) ** 2) * r * r * math.cos(t) ** 2
            total += value * (1 if i in (0, steps) else 4 if i % 2 else 2)
        return total * h / 3.0

    return steinmetz(outer) - steinmetz(inner)


def test_cross_hole_through_an_inclined_tube_removes_the_analytic_volume(adapter: FreeCADAdapter) -> None:
    """The leaning-tube case: a tube 30 degrees off Z, a hole drilled across it at mid-length. The datum chain is
    ref_axis (the tube's cylinder) -> ref_plane normal_to (the cross-section) -> ref_plane angle 90 about a
    section axis (a plane containing the tube axis, whose normal is the drilling direction)."""
    tube_volume = math.pi * (20.0**2 - 15.0**2) * 100.0
    mid = (0.0, -50 * S30, 50 * C30)
    _, trace = _run(
        adapter,
        *_inclined_tube(),
        {
            "id": "section",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "tube_axis"}, "at_mm": 50},
        },
        {
            "id": "drill_plane",
            "op": "ref_plane",
            "intent": "i",
            "define": {
                "how": "angle",
                "base": {"kind": "plane", "from_op": "section"},
                "about": {"origin": list(mid), "direction": [1, 0, 0]},
                "angle_deg": 90,
            },
        },
        _sketch("hole_sketch", {"kind": "plane", "from_op": "drill_plane"}, _circle(0, 0, 8, "hole")),
        {
            "id": "hole",
            "op": "cut",
            "intent": "i",
            "sketch": "hole_sketch",
            "end_condition": "through_all",
            "both_directions": True,
        },
    )
    _assert_green(trace)
    hole = trace.step("hole")
    assert hole is not None and hole.mass_after is not None
    removed = tube_volume - hole.mass_after.volume_mm3
    # OCC integrates the cylinder-cylinder intersection surface to about 1e-5, hence the relative tolerance
    assert removed == pytest.approx(_cross_hole_volume(20.0, 15.0, 4.0), rel=1e-4)
    section = trace.step("section")
    assert section is not None
    assert _close(section.attempts[-1].request["frame"]["origin"], mid, 1e-6)  # at_mm is measured from the tube base


# --- undo / redo ----------------------------------------------------------------------------------------------


def test_undo_to_before_a_datum_chain_and_redo_with_another_offset(adapter: FreeCADAdapter) -> None:
    operations = [
        *_plate(),
        {"id": "dp", "op": "ref_plane", "intent": "i", "define": {"how": "offset", "base": XY, "distance_mm": 20}},
        _sketch("s1", {"kind": "plane", "from_op": "dp"}, _rect(50, 30, 20, 20)),
        _extrude("boss", "s1", 5, merge=False),
    ]
    executor = Executor(_ir(*operations), adapter, Verifier(), ExecutorConfig())
    executor.start()
    _assert_green(executor.run_to_end())
    assert executor.undo_to("base") == ("boss", "s1", "dp")
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(PLATE, abs=1e-6)
    executor.extend(
        [
            {"id": "dp2", "op": "ref_plane", "intent": "i", "define": {"how": "offset", "base": XY, "distance_mm": 20}},
            _sketch("s2", {"kind": "plane", "from_op": "dp2"}, _rect(50, 30, 10, 10)),
            _extrude("boss2", "s2", 7, merge=False),
        ]
    )
    trace = executor.run_to_end()
    assert trace.final_state is ExecutorState.completed
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE + 10 * 10 * 7, abs=1e-3)
    assert trace.final_bbox is not None and trace.final_bbox.max[2] == pytest.approx(27.0, abs=1e-6)


# --- adapter-level contract -----------------------------------------------------------------------------------


def test_datum_tools_are_advertised_and_datums_are_not_features(adapter: FreeCADAdapter) -> None:
    assert {"datum_plane", "datum_axis"} <= adapter.capabilities().tools
    frame = {"origin": (0.0, 0.0, 12.0), "u": (1.0, 0.0, 0.0), "v": (0.0, 1.0, 0.0), "n": (0.0, 0.0, 1.0)}
    _, trace = _run(adapter, *_plate())
    _assert_green(trace)
    before = adapter.feature_tree(Empty(operation_id="t"))
    plane = adapter.datum_plane(
        DatumPlaneRequest(operation_id="dp", how="offset", frame=SketchFrame.model_validate(frame))
    )
    axis = adapter.datum_axis(
        DatumAxisRequest.model_validate(
            {
                "operation_id": "da",
                "how": "two_points",
                "axis": {"origin": (1.0, 2.0, 3.0), "direction": (0.0, 0.0, 4.0)},
            }
        )
    )
    assert plane.datum.id.startswith("datum_plane:") and axis.datum.id.startswith("datum_axis:")
    assert plane.frame.origin == pytest.approx((0.0, 0.0, 12.0)) and axis.axis.direction == pytest.approx((0, 0, 1))
    assert adapter.feature_tree(Empty(operation_id="t")) == before
    result = adapter.sketch(
        SketchRequest(
            operation_id="s",
            on=DatumPlaneTarget(handle=plane.datum, frame=plane.frame),
            entities=(CircleEntity(name="c", center=(0, 0), diameter=10),),
        )
    )
    assert result.frame == plane.frame  # a sketch on a datum reports the datum's frame exactly


# --- review fixes: a datum touching the body, and oblique through-all cuts on a plain cube ----------------------


def test_merged_extrude_from_a_datum_coplanar_with_a_face_passes_level2(adapter: FreeCADAdapter) -> None:
    """Plate 100x60x8, datum offset 8 from XY (coplanar with the top face), merged boss: FreeCAD merges the near
    cap into the plate (5 new faces, not 6). The face count is only reported for such a datum; volume and bbox
    are still asserted."""
    _, trace = _run(
        adapter,
        *_plate(),
        {"id": "dp", "op": "ref_plane", "intent": "i", "define": {"how": "offset", "base": XY, "distance_mm": 8}},
        _sketch("s1", {"kind": "plane", "from_op": "dp"}, _rect(50, 30, 20, 20)),
        _extrude("boss", "s1", 5),
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE + 20 * 20 * 5, abs=1e-3)
    assert trace.final_bbox is not None and trace.final_bbox.max[2] == pytest.approx(13.0, abs=1e-6)
    boss = trace.step("boss")
    assert boss is not None
    assert {"volume_delta", "bbox_after"} <= _asserted_ok(trace, "boss")
    assert "new_faces_count" not in _asserted_ok(trace, "boss")
    counted = next(c for c in boss.last_level2() if c.name == "new_faces_count")
    assert (counted.expected, counted.observed) == (6, 5)


def _cube_and_cylinder_volume(
    origin: tuple[float, float, float],
    n: tuple[float, float, float],
    u: tuple[float, float, float],
    v: tuple[float, float, float],
    centre_uv: tuple[float, float],
    radius: float,
    size: float,
    *,
    both: bool,
    steps: int = 400,
) -> float:
    """Independent oracle: volume of the cube [0, size]^3 swept by a circle (in the plane through origin spanned
    by u, v) along n (both ways, or only along -n): integral over the disc of the length of the chord that the
    line through each disc point makes inside the cube (slab method), on a polar grid."""

    def chord(point: tuple[float, ...]) -> float:
        lo, hi = -math.inf, math.inf
        for axis in range(3):
            if abs(n[axis]) < 1e-12:
                if not 0.0 <= point[axis] <= size:
                    return 0.0
                continue
            t0, t1 = (0.0 - point[axis]) / n[axis], (size - point[axis]) / n[axis]
            lo, hi = max(lo, min(t0, t1)), min(hi, max(t0, t1))
        if not both:
            hi = min(hi, 0.0)  # only the side towards -n
        return max(0.0, hi - lo)

    total = 0.0
    for i in range(steps):
        r = (i + 0.5) * radius / steps
        for j in range(steps):
            theta = (j + 0.5) * 2 * math.pi / steps
            a, b = centre_uv[0] + r * math.cos(theta), centre_uv[1] + r * math.sin(theta)
            point = tuple(origin[k] + a * u[k] + b * v[k] for k in range(3))
            total += chord(point) * r * (radius / steps) * (2 * math.pi / steps)
    return total


@pytest.mark.parametrize(
    ("cut_options", "both"),
    [({"both_directions": True}, True), ({"direction": "negative"}, False)],
    ids=["both_directions", "direction_negative"],
)
def test_oblique_through_all_cut_on_a_cube_passes_level2_and_removes_the_swept_volume(
    adapter: FreeCADAdapter, cut_options: dict[str, Any], both: bool
) -> None:
    """Cube 60^3 and a datum through (0,0,30), (60,0,30), (0,60,60) (n = (0,-1,2)/sqrt5): a O20 through-all cut.
    The bounding box of the cut cylindrical wall is loose in FreeCAD, so bbox_within is only reported; the
    independent checks are the bounded volume removal and the numerical chord integral of this test."""
    n = (0.0, -1 / math.sqrt(5), 2 / math.sqrt(5))
    u = (1.0, 0.0, 0.0)
    v = (0.0, 2 / math.sqrt(5), 1 / math.sqrt(5))  # v = n x u
    _, trace = _run(
        adapter,
        _sketch("s0", XY, _rect(30, 30, 60, 60)),
        _extrude("cube", "s0", 60),
        {
            "id": "dp",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "three_points", "points": [[0, 0, 30], [60, 0, 30], [0, 60, 60]]},
        },
        _sketch("s1", {"kind": "plane", "from_op": "dp"}, _circle(30, 30, 20)),
        {"id": "hole", "op": "cut", "intent": "i", "sketch": "s1", "end_condition": "through_all", **cut_options},
    )
    _assert_green(trace)
    hole = trace.step("hole")
    assert hole is not None and hole.mass_after is not None
    assert "cut_volume_bound" in _asserted_ok(trace, "hole")
    removed = 60.0**3 - hole.mass_after.volume_mm3
    expected = _cube_and_cylinder_volume((0, 0, 30), n, u, v, (30, 30), 10.0, 60.0, both=both)
    assert removed == pytest.approx(expected, rel=5e-3)
