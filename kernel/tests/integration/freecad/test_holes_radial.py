"""``hole`` across the wall of a tube (position = [axial_mm, angle_deg]) and cosmetic tapped holes, against the real
FreeCAD worker (both supported versions).

The tube is a rod O40 x 100 bored O30 from its far end, either standing on XY
or leaning 30 degrees. The oracle is ``verification.formulas.radial_hole_volume``: an exact integral of what a
round hole removes along a radius of the tube. OCC integrates cylinder-cylinder cuts 5e-4..2.5e-3 low (even a plain
``Part.cut``) and differencing two 55000 mm3 volumes adds ~0.5 mm3 of noise, so the removed volume is compared at
3e-3 relative plus 1 mm3, never at the 1e-4 of plane cuts.
"""

import json
import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    DocumentKind,
    EntityRequest,
    FaceRequest,
    FeatureRequest,
    NewDocumentRequest,
    RunNativeRequest,
    radial_entry,
)
from cotaforge.contract.threads import FINE_THREADS, THREADS, thread_sizes
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.formulas import radial_hole_volume
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

OP_TOLERANCE = 3e-3  # relative: OCC is 5e-4..2.5e-3 low on cylinder-cylinder cuts
OP_NOISE_MM3 = 1.0  # absolute: volume differencing on a 55000 mm3 tube is ~1e-5 relative (0.5 mm3) noise on its own
XY = {"kind": "plane", "name": "XY"}
OUTER_WALL = {"surface": "cylinder", "radius": 20}


def _tube(tilted: bool) -> list[dict[str, Any]]:
    """Rod O40 x 100 bored O30 from its far end: standing on XY, or leaning 30 degrees about X."""
    ops: list[dict[str, Any]] = []
    base: dict[str, Any] = XY
    if tilted:
        ops.append(
            {
                "id": "tilt",
                "op": "ref_plane",
                "intent": "XY rotated 30 degrees about X",
                "define": {
                    "how": "angle",
                    "base": XY,
                    "about": {"origin": [0, 0, 0], "direction": [1, 0, 0]},
                    "angle_deg": 30,
                },
            }
        )
        base = {"kind": "plane", "from_op": "tilt"}
    ops += [
        {
            "id": "rod_sketch",
            "op": "sketch",
            "intent": "outer circle",
            "on": base,
            "entities": [{"kind": "circle", "name": "outer", "center": [0, 0], "diameter": 40}],
        },
        {"id": "rod", "op": "extrude", "intent": "rod", "sketch": "rod_sketch", "depth_mm": 100},
        {
            "id": "tube_axis",
            "op": "ref_axis",
            "intent": "centre line of the rod",
            "define": {"how": "entity", "entity": {"kind": "face", "from_op": "rod", "select": OUTER_WALL}},
        },
        {
            "id": "far_end",
            "op": "ref_plane",
            "intent": "plane at the far end",
            "define": {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "tube_axis"}, "at_mm": 100},
        },
        {
            "id": "bore_sketch",
            "op": "sketch",
            "intent": "inner circle",
            "on": {"kind": "plane", "from_op": "far_end"},
            "entities": [{"kind": "circle", "name": "inner", "center": [0, 0], "diameter": 30}],
        },
        {
            "id": "tube",
            "op": "cut",
            "intent": "bore the rod from the far end",
            "sketch": "bore_sketch",
            "end_condition": "through_all",
            "direction": "negative",
        },
    ]
    return ops


def _cross_hole(
    position: list[float], end_condition: str = "blind", depth: float | None = 8.0, **extra: Any
) -> dict[str, Any]:
    hole: dict[str, Any] = {
        "id": "cross",
        "op": "hole",
        "intent": "cross hole through the tube wall",
        "face": {"kind": "face", "from_op": "rod", "select": OUTER_WALL},
        "position": position,
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": end_condition,
    }
    if depth is not None and end_condition == "blind":
        hole["depth_mm"] = depth
    hole.update(extra)
    return hole


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "radial", "kind": "part", "operations": list(operations)})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end()


def _detail(trace: ExecutionTrace) -> list[Any]:
    return [
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


def _assert_green(trace: ExecutionTrace) -> None:
    detail = _detail(trace)
    assert trace.final_state is ExecutorState.completed, detail
    assert all(s.status is StepStatus.success for s in trace.steps), detail


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None, op_id
    return {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok}


def _check(trace: ExecutionTrace, op_id: str, name: str) -> Any:
    step = trace.step(op_id)
    assert step is not None
    return next(c for c in step.last_level2() if c.name == name)


def _removed(trace: ExecutionTrace, op_id: str = "cross") -> float:
    """Volume the hole removed, measured by the kernel before and after it (the tube alone is itself 1e-5 off its
    analytic volume on a tilted axis, which would swamp a 140 mm3 hole)."""
    step = trace.step(op_id)
    assert step is not None and step.mass_before is not None and step.mass_after is not None
    return step.mass_before.volume_mm3 - step.mass_after.volume_mm3


def _error_message(trace: ExecutionTrace, op_id: str) -> str:
    step = trace.step(op_id)
    assert step is not None, (op_id, _detail(trace))
    if not step.attempts:  # refused while computing the expectations, before the adapter was called
        return str(step.plan_error)
    error = step.attempts[-1].error
    return "" if error is None else error.message


RADIAL_ASSERTED = {
    "hole_axis_direction",
    "hole_axis_position",
    "hole_radius",
    "cut_volume_bound",
}

# (id, depth for a blind hole or None for through-all, oracle depth)
CASES = [("blind8", 8.0, 8.0), ("pocket3", 3.0, 3.0), ("through", None, None)]


@pytest.mark.parametrize("tilted", [False, True], ids=["vertical", "tilted"])
@pytest.mark.parametrize(("label", "depth", "oracle_depth"), CASES, ids=[c[0] for c in CASES])
def test_a_radial_hole_removes_what_the_integral_says(
    adapter: FreeCADAdapter, tilted: bool, label: str, depth: float | None, oracle_depth: float | None
) -> None:
    end = "through_all" if depth is None else "blind"
    _, trace = _run(adapter, *_tube(tilted), _cross_hole([0, 0], end, depth))
    _assert_green(trace)
    expected = radial_hole_volume(20.0, 15.0, 3.0, oracle_depth)
    assert _removed(trace) == pytest.approx(expected, rel=OP_TOLERANCE, abs=OP_NOISE_MM3), (label, tilted)
    assert RADIAL_ASSERTED <= _asserted_ok(trace, "cross")
    assert "bbox_within" in _asserted_ok(trace, "cross")  # angle 0 drills along world X: a principal axis
    if depth is not None:
        assert "hole_entry_face_modified" in _asserted_ok(trace, "cross")
    assert _check(trace, "cross", "volume_delta").kind is CheckKind.reported  # never asserted at 1e-4


@pytest.mark.parametrize("tilted", [False, True], ids=["vertical", "tilted"])
@pytest.mark.parametrize(
    ("position", "label"),
    [([0, 90], "angle90"), ([20, 45], "axial20_angle45"), ([-30, 180], "axial-30_angle180"), ([10, -90], "negative")],
    ids=lambda p: p if isinstance(p, str) else "",
)
def test_the_axial_offset_and_the_angle_place_the_hole(
    adapter: FreeCADAdapter, tilted: bool, position: list[float], label: str
) -> None:
    _, trace = _run(adapter, *_tube(tilted), _cross_hole(position, "blind", 8.0))
    _assert_green(trace)
    assert _removed(trace) == pytest.approx(
        radial_hole_volume(20.0, 15.0, 3.0, 8.0), rel=OP_TOLERANCE, abs=OP_NOISE_MM3
    ), label
    assert RADIAL_ASSERTED <= _asserted_ok(trace, "cross")


def test_a_hole_in_a_rotated_and_translated_tube(adapter: FreeCADAdapter) -> None:
    """The body carries its pose in ``Body.Placement``: the hole sketch and the axis sense must be written in the
    body frame (the mistake would drill into the wrong place, so the asserted axis position fails)."""
    move = [
        {
            "id": "turn",
            "op": "rotate",
            "intent": "lay it down",
            "target": {"kind": "body", "from_op": "rod"},
            "axis": "X",
            "angle_deg": 90,
        },
        {
            "id": "shift",
            "op": "translate",
            "intent": "move it",
            "target": {"kind": "body", "from_op": "rod"},
            "offset": [30, 40, 50],
        },
    ]
    _, trace = _run(adapter, *_tube(False), *move, _cross_hole([10, 30], "through_all"))
    _assert_green(trace)
    assert _removed(trace) == pytest.approx(
        radial_hole_volume(20.0, 15.0, 3.0, None), rel=OP_TOLERANCE, abs=OP_NOISE_MM3
    )
    assert RADIAL_ASSERTED <= _asserted_ok(trace, "cross")


def test_a_hole_on_a_face_picked_by_a_selection_token(adapter: FreeCADAdapter) -> None:
    """``from_selection`` on a cylindrical face: a token built headlessly with ``persistent_reference``, the same
    seam the visible selection bridge uses."""
    executor, trace = _run(adapter, *_tube(True))
    _assert_green(trace)
    feature = executor.feature_for("rod")
    assert feature is not None
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=feature)).faces
    wall = next(f for f in faces if f.radius is not None and abs(f.radius - 20.0) < 1e-6)
    token = adapter.persistent_reference(EntityRequest(operation_id="q", entity=wall.handle)).token
    hole = _cross_hole([0, 0], "blind", 8.0)
    hole["face"] = {"kind": "face", "from_selection": token}
    executor.extend([hole])
    trace = executor.run_to_end()
    _assert_green(trace)
    assert _removed(trace) == pytest.approx(
        radial_hole_volume(20.0, 15.0, 3.0, 8.0), rel=OP_TOLERANCE, abs=OP_NOISE_MM3
    )


def test_a_hole_on_the_bore_is_refused_with_an_actionable_message(adapter: FreeCADAdapter) -> None:
    bore = _cross_hole([0, 0], "blind", 4.0)
    bore["face"] = {"kind": "face", "from_op": "tube", "select": {"surface": "cylinder", "radius": 15}}
    _, trace = _run(adapter, *_tube(False), bore)
    assert trace.final_state is ExecutorState.failed
    message = _error_message(trace, "cross")
    assert "concave" in message and "bore" in message, message


def test_a_position_off_the_face_is_refused_and_names_the_axial_range(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *_tube(False), _cross_hole([80, 0], "blind", 8.0))
    assert trace.final_state is ExecutorState.failed
    message = _error_message(trace, "cross")
    assert "axial end" in message and "+-50" in message, message  # refused by the expectation, before the adapter


# --- tapped holes: the thread is cosmetic, the geometry is the tap-drill cylinder ----------------------------------

PLATE = [
    {
        "id": "s0",
        "op": "sketch",
        "intent": "outline",
        "on": XY,
        "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 100, "height": 100}],
    },
    {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s0", "depth_mm": 30},
]
TAP_DRILLS = {
    size: THREADS[size].tap_drill_mm for size in thread_sizes()
}  # every size of the table, both FreeCAD versions


def _tapped(size: str, **extra: Any) -> dict[str, Any]:
    hole = {
        "id": "tap",
        "op": "hole",
        "intent": "tapped hole",
        "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
        "position": [0, 0],
        "standard": "iso_tapped",
        "size": size,
        "end_condition": "blind",
        "drill_point": "flat",  # the tests ask for a flat bottom; FreeCAD's default is angled
        "depth_mm": 15,
    }
    hole.update(extra)
    return hole


@pytest.mark.parametrize("size", list(TAP_DRILLS))
def test_a_tapped_hole_is_the_tap_drill_cylinder(adapter: FreeCADAdapter, size: str) -> None:
    _, trace = _run(adapter, *PLATE, _tapped(size, thread_depth_mm=10))
    _assert_green(trace)
    drill = TAP_DRILLS[size]
    assert trace.final_mass is not None
    assert 100.0 * 100.0 * 30.0 - trace.final_mass.volume_mm3 == pytest.approx(
        math.pi * (drill / 2) ** 2 * 15.0, abs=1e-3
    )
    asserted = _asserted_ok(trace, "tap")
    assert {"hole_radius", "volume_delta", "hole_axis_direction"} <= asserted
    meta = _check(trace, "tap", "hole_thread_metadata")
    assert meta.kind is CheckKind.reported and meta.expected == float(size[1:])


FINE_SAMPLE = ["M8x1", "M12x1.25", "M20x1.5", "M30x2", "M42x3", "M45x1.5", "M60x4"]


@pytest.mark.parametrize("size", FINE_SAMPLE)
def test_a_fine_tapped_hole_is_the_tap_drill_cylinder(adapter: FreeCADAdapter, size: str) -> None:
    _, trace = _run(adapter, *PLATE, _tapped(size, thread_depth_mm=10))
    _assert_green(trace)
    drill = FINE_THREADS[size].tap_drill_mm
    assert trace.final_mass is not None
    assert 100.0 * 100.0 * 30.0 - trace.final_mass.volume_mm3 == pytest.approx(
        math.pi * (drill / 2) ** 2 * 15.0, abs=1e-3
    )
    meta = _check(trace, "tap", "hole_thread_metadata")
    assert meta.kind is CheckKind.reported and meta.expected == FINE_THREADS[size].major_mm


FREECAD_TABLES = r"""
import json
import FreeCAD as App
import Part
import Sketcher
doc = App.ActiveDocument
body = doc.addObject("PartDesign::Body", "Body")
xy = [o for o in body.Origin.OutList if o.Role == "XY_Plane"][0]
base = body.newObject("Sketcher::SketchObject", "S0")
base.AttachmentSupport = (xy, [""])
base.MapMode = "FlatFace"
base.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 40), False)
base.addConstraint(Sketcher.Constraint("Coincident", 0, 3, -1, 1))
base.addConstraint(Sketcher.Constraint("Diameter", 0, 80.0))
pad = body.newObject("PartDesign::Pad", "P")
pad.Profile = base
pad.Length = 30
doc.recompute()
sk = body.newObject("Sketcher::SketchObject", "S1")
sk.AttachmentSupport = (xy, [""])
sk.MapMode = "FlatFace"
sk.AttachmentOffset = App.Placement(App.Vector(0, 0, 30), App.Rotation())
sk.addGeometry(Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 5), False)
sk.addConstraint(Sketcher.Constraint("Coincident", 0, 3, -1, 1))
sk.addConstraint(Sketcher.Constraint("Diameter", 0, 10.0))
doc.recompute()
hole = body.newObject("PartDesign::Hole", "H")
hole.Profile = sk
hole.Threaded = True
hole.ModelThread = False
found = {}
for kind in ("ISOMetricProfile", "ISOMetricFineProfile"):
    hole.ThreadType = kind
    for entry in hole.getEnumerationsOfProperty("ThreadSize"):
        hole.ThreadSize = entry
        doc.recompute()
        found[kind + ":" + entry] = float(getattr(hole.Diameter, "Value", hole.Diameter))
print("TABLES" + json.dumps(found))
"""


def test_every_size_of_the_table_is_in_freecads_iso_tables_with_the_same_tap_drill(adapter: FreeCADAdapter) -> None:
    """The sizes are transcribed from the ISO 261 series; FreeCAD's own tables, with the drill it computes for each, are
    the check that no size is invented and no drill differs (the tapped hole is refused when they differ)."""
    adapter.new_document(NewDocumentRequest(operation_id="tables_doc", kind=DocumentKind.part, name="tables"))
    result = adapter.run_native(RunNativeRequest(operation_id="tables", code=FREECAD_TABLES, intent="read the tables"))
    assert result.ok, (result.error, result.broken)
    found = json.loads(result.stdout.split("TABLES", 1)[1])

    def numbers(designation: str) -> tuple[float, float | None]:
        major, _, pitch = designation[1:].partition("x")
        return float(major), (float(pitch) if pitch else None)

    def freecad(designation: str, kind: str) -> float:
        major, pitch = numbers(designation)
        for key, drill in found.items():
            entry_kind, entry = key.split(":", 1)
            got_major, got_pitch = numbers(entry)
            if entry_kind == kind and got_major == major and (pitch is None or got_pitch == pitch):
                return drill
        raise AssertionError(f"{designation} is not in FreeCAD's {kind} table")

    for spec in THREADS.values():
        assert freecad(spec.designation, "ISOMetricProfile") == pytest.approx(spec.tap_drill_mm, abs=1e-3), spec
    for spec in FINE_THREADS.values():
        assert freecad(spec.designation, "ISOMetricFineProfile") == pytest.approx(spec.tap_drill_mm, abs=1e-3), spec


def test_a_tapped_through_hole_keeps_the_tap_drill(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, *PLATE, _tapped("M8", end_condition="through_all", depth_mm=None))
    _assert_green(trace)
    assert trace.final_mass is not None
    assert 100.0 * 100.0 * 30.0 - trace.final_mass.volume_mm3 == pytest.approx(math.pi * 3.4**2 * 30.0, abs=1e-3)


def test_a_tapped_radial_hole_in_the_tube(adapter: FreeCADAdapter) -> None:
    hole = _cross_hole([0, 0], "blind", 8.0, standard="iso_tapped", size="M6", thread_depth_mm=6)
    del hole["diameter_mm"]
    _, trace = _run(adapter, *_tube(True), hole)
    _assert_green(trace)
    assert _removed(trace) == pytest.approx(
        radial_hole_volume(20.0, 15.0, 2.5, 8.0), rel=OP_TOLERANCE, abs=OP_NOISE_MM3
    )
    assert "hole_radius" in _asserted_ok(trace, "cross")


@pytest.mark.parametrize("tilted", [False, True], ids=["vertical", "tilted"])
@pytest.mark.parametrize("angle", [0, 45, 90, 180, -90, -135])
def test_the_hole_enters_on_the_requested_side(adapter: FreeCADAdapter, tilted: bool, angle: float) -> None:
    """A tube is symmetric: the removed volume is the same on every side, so only the geometry of the created hole
    shows whether the angle was honoured. The wall of a blind hole 8 mm deep lies between 12 and 20 mm from the tube
    axis along the requested radial, and nowhere near the opposite side."""
    executor, trace = _run(adapter, *_tube(tilted))
    _assert_green(trace)
    feature = executor.feature_for("rod")
    assert feature is not None
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=feature)).faces
    outer = next(f for f in faces if f.radius is not None and abs(f.radius - 20.0) < 1e-6)
    entry = radial_entry(outer, (5.0, float(angle)))
    executor.extend([_cross_hole([5, angle], "blind", 8.0)])
    trace = executor.run_to_end()
    _assert_green(trace)
    step = trace.step("cross")
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    created = [
        adapter.face_info(FaceRequest(operation_id="q", face=handle))
        for handle in step.attempts[-1].result.created_faces
    ]
    wall = next(f for f in created if f.surface.value == "cylinder")
    along = sum((c - a) * o for c, a, o in zip(wall.center, entry.axis_point, entry.outward, strict=True))
    assert 12.0 <= along <= 20.0 + 1e-6, (angle, along)
