"""Tests for ExpectedComputer: every row of the expectation table on hand-built inputs."""

import math

import pytest

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    ChamferRequest,
    CircleEntity,
    CircularPatternRequest,
    ComponentHandle,
    CutRequest,
    CylinderRequest,
    DocumentHandle,
    DocumentKind,
    EdgeHandle,
    ExtrudeRequest,
    FaceHandle,
    FeatureHandle,
    FilletRequest,
    FixComponentRequest,
    HoleRequest,
    HoleThread,
    InsertComponentRequest,
    InterferenceRequest,
    LinearPatternRequest,
    MassProperties,
    MassRequest,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    MoveBodyRequest,
    NewDocumentRequest,
    PlaneName,
    Profile,
    RectEntity,
    RevolveRequest,
    SketchFrame,
    SketchHandle,
    SketchRequest,
    SurfaceType,
    Transform,
    sketch_profiles,
)
from cotaforge.ir.ops_features import (
    ChamferOperation,
    CheckInterferenceOperation,
    CircularPatternOperation,
    CutOperation,
    ExtrudeOperation,
    FilletOperation,
    FixComponentOperation,
    HoleOperation,
    InsertComponentOperation,
    LinearPatternOperation,
    MateCoincidentOperation,
    MateConcentricOperation,
    MateDistanceOperation,
    RevolveOperation,
    SketchOperation,
)
from cotaforge.ir.ops_primitives import (
    BooleanIntersectOperation,
    BooleanSubtractOperation,
    BooleanUnionOperation,
    BoxOperation,
    CylinderOperation,
    RotateOperation,
    TranslateOperation,
)
from cotaforge.ir.refs import BodyRef, EdgeRef, FaceRef, PlaneRef
from cotaforge.verification.expected import (
    ExpectedComputer,
    ExtrudeCoverageNotSupportedError,
    ExtrudeIntoHostFaceError,
    ExtrudeOverhangsHostFaceError,
    HoleLeavesFaceError,
    _classify_coverage,
    _face_footprint_uv,
)
from cotaforge.verification.formulas import BoxShape, radial_hole_volume
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind, Expectation
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import (
    PLATE_BBOX,
    PLATE_MASS,
    PLATE_RECT,
    computer_with_pad,
    cylindrical_face,
    line_edge,
    mass,
    planar_face,
    plate_faces,
)

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
PLATE = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX)
PLATE_BBOX_TOP = BoundingBox(min=(0.0, 0.0, 8.0), max=(100.0, 60.0, 8.0))
RECT = {"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}


def _by_name(expectations: tuple[Expectation, ...]) -> dict[str, Expectation]:
    return {e.name: e for e in expectations}


def _computer_with_plate_sketch(stub: StubAdapter) -> ExpectedComputer:
    computer = ExpectedComputer(Thresholds())
    sketch = SketchOperation(id="base_sketch", intent="outline", on=PlaneRef(name="XY"), entities=[RECT])
    request = SketchRequest(operation_id="base_sketch", on=PlaneName.XY, entities=sketch.entities)
    computer.compute(sketch, request, EMPTY, stub)
    return computer


# --- sketch / extrude ---------------------------------------------------------------------------


def test_sketch_emits_only_a_reported_zero_volume() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(name="XY"), entities=[RECT])
    out = computer.compute(
        sketch, SketchRequest(operation_id="s", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub
    )
    assert [(e.name, e.kind) for e in out] == [("volume_delta", CheckKind.reported)]


def test_extrude_rect_on_empty_document() -> None:
    stub = StubAdapter()
    computer = _computer_with_plate_sketch(stub)
    op = ExtrudeOperation(id="base", intent="thickness", sketch="base_sketch", depth_mm=8.0)
    request = ExtrudeRequest(operation_id="base", sketch=SketchHandle(id="sk:1"), depth_mm=8.0)
    by = _by_name(computer.compute(op, request, EMPTY, stub))
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(48000.0)
    assert by["bbox_after"].value == PLATE_BBOX  # empty document: no union with the zero box at the origin
    assert by["new_faces_count"].value == 6


def test_extrude_negative_and_both_directions_sweep() -> None:
    stub = StubAdapter()
    computer = _computer_with_plate_sketch(stub)
    op = ExtrudeOperation(id="base", intent="i", sketch="base_sketch", depth_mm=8.0, direction="negative")
    request = ExtrudeRequest(operation_id="base", sketch=SketchHandle(id="sk:1"), depth_mm=8.0, direction="negative")
    by = _by_name(computer.compute(op, request, EMPTY, stub))
    assert by["bbox_after"].value == BoundingBox(min=(0.0, 0.0, -8.0), max=(100.0, 60.0, 0.0))
    op = ExtrudeOperation(id="b", intent="i", sketch="base_sketch", depth_mm=8.0, both_directions=True, depth2_mm=2.0)
    request = ExtrudeRequest(
        operation_id="b", sketch=SketchHandle(id="sk:1"), depth_mm=8.0, both_directions=True, depth2_mm=2.0
    )
    by = _by_name(computer.compute(op, request, EMPTY, stub))
    assert by["volume_delta"].value == pytest.approx(60000.0)
    assert by["bbox_after"].value == BoundingBox(min=(0.0, 0.0, -2.0), max=(100.0, 60.0, 8.0))


def test_extrude_on_existing_body_unions_with_bbox_before() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "circle", "name": "boss", "center": [50, 30], "diameter": 20}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=PlaneRef(name="XY"), entities=[boss])
    computer.compute(
        sketch, SketchRequest(operation_id="boss_sketch", on=PlaneName.XY, entities=sketch.entities), PLATE, stub
    )
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=20.0)
    by = _by_name(
        computer.compute(
            op, ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=20.0), PLATE, stub
        )
    )
    assert by["volume_delta"].value == pytest.approx(math.pi * 100.0 * 20.0)
    assert by["bbox_after"].value == BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 20.0))
    assert by["new_faces_count"].value == 3


def _boss_on_plate(merge: bool = True) -> dict:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "circle", "name": "boss", "center": [50, 30], "diameter": 20}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=PlaneRef(name="XY"), entities=[boss])
    computer.compute(
        sketch, SketchRequest(operation_id="boss_sketch", on=PlaneName.XY, entities=sketch.entities), PLATE, stub
    )
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=20.0, merge=merge)
    request = ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=20.0, merge=merge)
    return _by_name(computer.compute(op, request, PLATE, stub))


def test_a_merged_extrude_on_a_body_says_what_a_volume_shortfall_means() -> None:
    """Area x depth is only added where the profile is clear of the body: when a rib or boss comes up short, the
    failure message must point at the overlap and at the fix, not just print two numbers."""
    note = _boss_on_plate()["volume_delta"].note
    assert "overlap" in note
    assert "flush" in note


def test_an_extrude_that_makes_its_own_body_carries_no_overlap_note() -> None:
    assert _boss_on_plate(merge=False)["volume_delta"].note == ""


def test_sketch_on_a_face_uses_the_face_frame() -> None:
    stub = StubAdapter()
    top = stub.add_face(plate_faces()[0])
    computer = ExpectedComputer(Thresholds())
    pocket = {"kind": "rect", "name": "pocket", "center": [0, 0], "width": 20, "height": 10}
    sketch = SketchOperation(id="ps", intent="i", on=FaceRef(from_op="base"), entities=[pocket])
    computer.compute(sketch, SketchRequest(operation_id="ps", on=top.handle, entities=sketch.entities), PLATE, stub)
    op = CutOperation(id="pocket", intent="i", sketch="ps", depth_mm=3.0)
    by = _by_name(
        computer.compute(op, CutRequest(operation_id="pocket", sketch=SketchHandle(id="s"), depth_mm=3.0), PLATE, stub)
    )
    # face frame: origin at the face centre (50, 30, 8), u = +X, v = +Y, n = +Z; cut goes -n by 3
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-600.0)
    assert by["bbox_within"].value == PLATE_BBOX


def test_extrude_boss_on_existing_face_merges_the_near_cap() -> None:
    """A boss sketched on an existing face and joined (merge=True, the default)
    does not get a new near cap — it merges into the host face — so new_faces_count = 1 + n_side_faces,
    not 2 + n_side_faces. Validated against real FreeCAD."""
    stub = StubAdapter()
    top = stub.add_face(plate_faces()[0])
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "rect", "name": "boss", "center": [0, 0], "width": 20, "height": 20}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), PLATE, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=10.0)
    by = _by_name(
        computer.compute(
            op, ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=10.0), PLATE, stub
        )
    )
    assert by["volume_delta"].value == pytest.approx(4000.0)
    assert by["new_faces_count"].value == 5  # 4 side walls + 1 top cap; the base merges into the host face


def test_extrude_square_boss_on_existing_face_is_expected_like_the_rect_boss() -> None:
    """The verifier reasons on profiles: a ``square`` yields the same expectations as the equal-sided rect
    ; the entity kind only changes how the adapters dimension the sketch."""
    stub = StubAdapter()
    top = stub.add_face(plate_faces()[0])
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "square", "name": "boss", "center": [0, 0], "side": 20}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), PLATE, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=10.0)
    by = _by_name(
        computer.compute(
            op, ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=10.0), PLATE, stub
        )
    )
    assert by["volume_delta"].value == pytest.approx(4000.0)  # 20 * 20 * 10
    assert by["new_faces_count"].value == 5
    assert by["bbox_after"].value.max == pytest.approx((100.0, 60.0, 18.0))


def test_extrude_cylindrical_boss_on_existing_face_merges_the_near_cap() -> None:
    stub = StubAdapter()
    top = stub.add_face(plate_faces()[0])
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 20}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), PLATE, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=10.0)
    by = _by_name(
        computer.compute(
            op, ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=10.0), PLATE, stub
        )
    )
    assert by["volume_delta"].value == pytest.approx(math.pi * 100.0 * 10.0)
    assert by["new_faces_count"].value == 2  # 1 cylindrical side + 1 top cap


def test_extrude_boss_on_existing_face_not_merged_keeps_both_caps() -> None:
    """merge=False: the boss is not joined to the host body, so its base is a genuine new face
    (two coincident but distinct solids) — the free-standing 2 + n_side_faces rule still applies."""
    stub = StubAdapter()
    top = stub.add_face(plate_faces()[0])
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "rect", "name": "boss", "center": [0, 0], "width": 20, "height": 20}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), PLATE, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=10.0, merge=False)
    by = _by_name(
        computer.compute(
            op,
            ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=10.0, merge=False),
            PLATE,
            stub,
        )
    )
    assert by["new_faces_count"].value == 6  # 4 side walls + 2 caps: not merged into the host body


# --- on-face extrude coverage classes (inside / covers / overhangs) -----------------------------------

BLOCK_BBOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(20.0, 20.0, 8.0))
BLOCK = StateSnapshot(mass=mass(3200.0, (10.0, 10.0, 4.0)), bbox=BLOCK_BBOX)
BLOCK_TOP_FACE = planar_face(
    "face:block_top", (0.0, 0.0, 1.0), (10.0, 10.0, 8.0), 400.0, BoundingBox(min=(0.0, 0.0, 8.0), max=(20.0, 20.0, 8.0))
)
BLOCK_TOP_FRAME = SketchFrame(origin=(10.0, 10.0, 8.0), u=(1.0, 0.0, 0.0), v=(0.0, 1.0, 0.0), n=(0.0, 0.0, 1.0))


def _rect_profile(center: tuple[float, float], width: float, height: float) -> Profile:
    return sketch_profiles([RectEntity(name="p", center=center, width=width, height=height)])[0]


def _circle_profile(center: tuple[float, float], diameter: float) -> Profile:
    return sketch_profiles([CircleEntity(name="p", center=center, diameter=diameter)])[0]


def test_classify_coverage_rect_inside_the_host_face() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    profile = _rect_profile((0.0, 0.0), 10.0, 10.0)
    assert _classify_coverage(profile, host_bbox_uv, 1e-2) == "inside"


def test_classify_coverage_rect_exactly_equal_to_the_host_face_covers_it() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    profile = _rect_profile((0.0, 0.0), 20.0, 20.0)
    assert _classify_coverage(profile, host_bbox_uv, 1e-2) == "covers"


def test_classify_coverage_rect_larger_than_the_host_face_covers_it() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    profile = _rect_profile((0.0, 0.0), 30.0, 30.0)
    assert _classify_coverage(profile, host_bbox_uv, 1e-2) == "covers"


def test_classify_coverage_rect_partially_overlapping_the_host_face_overhangs() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    profile = _rect_profile((5.0, 0.0), 20.0, 5.0)  # bigger in u, smaller in v: neither inside nor covers
    assert _classify_coverage(profile, host_bbox_uv, 1e-2) == "overhangs"


def test_classify_coverage_rect_flush_on_one_edge_is_not_an_overhang() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    profile = _rect_profile((0.0, 7.0), 20.0, 6.0)  # u spans the whole face, v 4..10: touches two edges, within
    assert _classify_coverage(profile, host_bbox_uv, 1e-2) == "flush"
    narrow = _rect_profile((0.0, 7.0), 10.0, 6.0)  # touches only the v = 10 edge
    assert _classify_coverage(narrow, host_bbox_uv, 1e-2) == "flush"


def test_classify_coverage_rect_flush_in_a_corner_is_not_an_overhang() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    profile = _rect_profile((5.0, 5.0), 10.0, 10.0)  # u 0..10, v 0..10
    assert _classify_coverage(profile, host_bbox_uv, 1e-2) == "flush"


def test_classify_coverage_rect_past_the_edge_by_more_than_tolerance_still_overhangs() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    profile = _rect_profile((0.0, 7.1), 10.0, 6.0)  # v up to 10.1
    assert _classify_coverage(profile, host_bbox_uv, 1e-2) == "overhangs"


def test_classify_coverage_circle_covers_only_when_the_host_rectangle_is_inscribed() -> None:
    host_bbox_uv = ((-10.0, -10.0), (10.0, 10.0))
    # bounding square of this circle (radius 15) contains the host bbox, but the host's corners
    # (at distance sqrt(200) ~= 14.14 from the centre) are NOT all inside a radius-15 circle... they
    # are (14.14 < 15) -- use a tighter radius that contains the square's extents but not its corners.
    corner_distance = math.hypot(10.0, 10.0)
    not_inscribed = _circle_profile((0.0, 0.0), diameter=2 * (corner_distance - 1.0))
    assert _classify_coverage(not_inscribed, host_bbox_uv, 1e-2) == "overhangs"
    inscribed = _circle_profile((0.0, 0.0), diameter=2 * (corner_distance + 1.0))
    assert _classify_coverage(inscribed, host_bbox_uv, 1e-2) == "covers"


def test_face_footprint_uv_projects_the_host_face_bbox_onto_the_sketch_frame() -> None:
    assert _face_footprint_uv(BLOCK_TOP_FACE, BLOCK_TOP_FRAME) == ((-10.0, -10.0), (10.0, 10.0))


def test_extrude_boss_that_covers_the_host_face_yields_one_new_face_and_modified_sides() -> None:
    """The ``covers`` class (validated on real FreeCAD): the boss's 20x20 footprint exactly equals the 20x20 host face,
    so the host face is consumed rather than shrunk -- 1 new face (the far cap), and the 4 side faces adjacent
    to the host face are *modified*, not duplicated. ``modified_faces_count`` comes from the host face's
    own edge count (``adapter.edges_of``), not the profile's side count."""
    stub = StubAdapter()
    top = stub.add_face(BLOCK_TOP_FACE)
    for i, (start, end) in enumerate(
        [
            ((0.0, 0.0, 8.0), (20.0, 0.0, 8.0)),
            ((20.0, 0.0, 8.0), (20.0, 20.0, 8.0)),
            ((20.0, 20.0, 8.0), (0.0, 20.0, 8.0)),
            ((0.0, 20.0, 8.0), (0.0, 0.0, 8.0)),
        ]
    ):
        stub.add_edge(line_edge(f"edge:block_top_{i}", ("face:block_top", f"face:block_side_{i}"), start, end))
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "rect", "name": "boss", "center": [0, 0], "width": 20, "height": 20}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), BLOCK, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=5.0)
    by = _by_name(
        computer.compute(
            op, ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=5.0), BLOCK, stub
        )
    )
    assert by["volume_delta"].value == pytest.approx(2000.0)
    assert by["new_faces_count"].value == 1
    assert by["modified_faces_count"].value == 4


def test_extrude_boss_that_covers_the_host_face_with_a_circle_profile_raises() -> None:
    """The ``covers`` class is only supported for a rectangular
    profile congruent with (or containing) a rectangular host face -- a circle whose footprint covers the
    host face's corners must raise before the adapter is ever called, so the fake and FreeCAD worker are
    never asked to build it (parity by construction)."""
    stub = StubAdapter()
    top = stub.add_face(BLOCK_TOP_FACE)
    computer = ExpectedComputer(Thresholds())
    corner_distance = math.hypot(10.0, 10.0)
    boss = {"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 2 * (corner_distance + 1.0)}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), BLOCK, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=5.0)
    request = ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=5.0)
    with pytest.raises(ExtrudeCoverageNotSupportedError, match="only a rectangular profile"):
        computer.compute(op, request, BLOCK, stub)


def test_extrude_boss_that_overhangs_the_host_face_raises() -> None:
    """The ``overhangs`` class is out of scope: raising here (before the adapter is ever called --
    ``executor/executor.py::next_operation`` computes expectations first) is what turns it into a
    ``plan_error`` on both the fake and FreeCAD."""
    stub = StubAdapter()
    top = stub.add_face(BLOCK_TOP_FACE)
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "rect", "name": "boss", "center": [5, 0], "width": 20, "height": 5}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), BLOCK, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=5.0)
    request = ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=5.0)
    with pytest.raises(ExtrudeOverhangsHostFaceError, match="overhangs the host face"):
        computer.compute(op, request, BLOCK, stub)


@pytest.mark.parametrize(
    "op_kwargs",
    [
        {"direction": "negative"},
        {"both_directions": True, "depth2_mm": 2.0},
    ],
)
def test_extrude_merge_on_face_into_the_host_body_raises(op_kwargs: dict) -> None:
    """The 'merge into host face' rule only applies to a purely positive, single-direction sweep
    -- extruding into the host body from its own face is not supported."""
    stub = StubAdapter()
    top = stub.add_face(BLOCK_TOP_FACE)
    computer = ExpectedComputer(Thresholds())
    boss = {"kind": "rect", "name": "boss", "center": [0, 0], "width": 10, "height": 10}
    sketch = SketchOperation(id="boss_sketch", intent="i", on=FaceRef(from_op="base"), entities=[boss])
    computer.compute(sketch, SketchRequest(operation_id="bs", on=top.handle, entities=sketch.entities), BLOCK, stub)
    op = ExtrudeOperation(id="boss", intent="i", sketch="boss_sketch", depth_mm=5.0, **op_kwargs)
    request = ExtrudeRequest(operation_id="boss", sketch=SketchHandle(id="s"), depth_mm=5.0, **op_kwargs)
    with pytest.raises(ExtrudeIntoHostFaceError, match="not supported; use a cut"):
        computer.compute(op, request, BLOCK, stub)


# --- cut ----------------------------------------------------------------------------------------------


def test_cut_blind_exiting_the_body_is_reported() -> None:
    stub = StubAdapter()
    computer = _computer_with_plate_sketch(stub)
    op = CutOperation(id="c", intent="i", sketch="base_sketch", depth_mm=20.0)
    by = _by_name(
        computer.compute(op, CutRequest(operation_id="c", sketch=SketchHandle(id="s"), depth_mm=20.0), PLATE, stub)
    )
    assert by["volume_delta"].kind == CheckKind.reported
    assert "exits" in by["volume_delta"].note


def test_cut_through_all_reports_volume_and_asserts_extent() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    slot = {"kind": "rect", "name": "slot", "center": [50, 30], "width": 10, "height": 4}
    sketch = SketchOperation(id="ss", intent="i", on=PlaneRef(name="XY"), entities=[slot])
    computer.compute(sketch, SketchRequest(operation_id="ss", on=PlaneName.XY, entities=sketch.entities), PLATE, stub)
    op = CutOperation(id="c", intent="i", sketch="ss", end_condition="through_all", direction="positive")
    request = CutRequest(
        operation_id="c", sketch=SketchHandle(id="s"), end_condition="through_all", direction="positive"
    )
    by = _by_name(computer.compute(op, request, PLATE, stub))
    assert by["volume_delta"].kind == CheckKind.reported
    assert by["volume_delta"].value == pytest.approx(-40.0 * 8.0)
    assert by["cut_extent_along_n"].kind == CheckKind.asserted
    assert by["cut_extent_along_n"].value == pytest.approx(8.0)
    assert by["cut_direction"].value == pytest.approx((0.0, 0.0, 1.0))


# --- revolve -------------------------------------------------------------------------------------------


def test_revolve_pappus_rectangle_about_u() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    # rectangle 30 (u) x 10 (v) with v from 10 to 20: centroid distance to the u axis = 15
    rect = {"kind": "rect", "name": "r", "center": [15, 15], "width": 30, "height": 10}
    sketch = SketchOperation(id="rs", intent="i", on=PlaneRef(name="XY"), entities=[rect])
    computer.compute(sketch, SketchRequest(operation_id="rs", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub)
    op = RevolveOperation(id="rev", intent="i", sketch="rs", axis="u")
    request = RevolveRequest(
        operation_id="rev", sketch=SketchHandle(id="s"), axis=Axis(origin=(0, 0, 0), direction=(1, 0, 0))
    )
    by = _by_name(computer.compute(op, request, EMPTY, stub))
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(2 * math.pi * 15.0 * 300.0)
    assert by["new_faces_count"].value == 4
    assert by["bbox_after"].value == BoundingBox(min=(0.0, -20.0, -20.0), max=(30.0, 20.0, 20.0))
    partial = RevolveOperation(id="rev", intent="i", sketch="rs", axis="u", angle_deg=90.0)
    by = _by_name(computer.compute(partial, request.model_copy(update={"angle_deg": 90.0}), EMPTY, stub))
    assert by["volume_delta"].value == pytest.approx(2 * math.pi * 15.0 * 300.0 / 4)
    assert by["bbox_after"].kind == CheckKind.reported


def test_revolve_profile_crossing_the_axis_is_reported() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    rect = {"kind": "rect", "name": "r", "center": [15, 0], "width": 30, "height": 10}
    sketch = SketchOperation(id="rs", intent="i", on=PlaneRef(name="XY"), entities=[rect])
    computer.compute(sketch, SketchRequest(operation_id="rs", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub)
    op = RevolveOperation(id="rev", intent="i", sketch="rs", axis="u")
    request = RevolveRequest(
        operation_id="rev", sketch=SketchHandle(id="s"), axis=Axis(origin=(0, 0, 0), direction=(1, 0, 0))
    )
    by = _by_name(computer.compute(op, request, EMPTY, stub))
    assert by["volume_delta"].kind == CheckKind.reported


# --- hole ------------------------------------------------------------------------------------------------


def _hole(end_condition: str, depth: float | None = None) -> tuple[HoleOperation, HoleRequest]:
    op = HoleOperation(
        id="hole_1",
        intent="i",
        face=FaceRef(from_op="base"),
        position=(-40.0, -20.0),
        standard="iso_clearance",
        size="M6",
        diameter_mm=6.6,
        end_condition=end_condition,
        depth_mm=depth,
    )
    request = HoleRequest(
        operation_id="hole_1",
        face=FaceHandle(id="face:top"),
        position=(-40.0, -20.0),
        standard="iso_clearance",
        size="M6",
        diameter_mm=6.6,
        end_condition=end_condition,
        depth_mm=depth,
        drill_point="flat",  # these tests ask for the flat bottom explicitly (FreeCAD's default is angled)
    )
    return op, request


def test_hole_through_all_on_plate_top_face() -> None:
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face)
    op, request = _hole("through_all")
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    # face frame origin (50, 30, 8): position (-40, -20) -> world (10, 10, 8)
    assert by["hole_axis_position"].value == pytest.approx((10.0, 10.0, 8.0))
    assert by["hole_axis_direction"].value == pytest.approx((0.0, 0.0, -1.0))
    assert by["hole_radius"].value == pytest.approx(3.3)
    assert by["cut_extent_along_n"].value == pytest.approx(8.0)
    assert by["cut_extent_along_n"].kind == CheckKind.asserted
    assert by["volume_delta"].kind == CheckKind.reported
    assert by["volume_delta"].value == pytest.approx(-math.pi * 3.3**2 * 8.0)
    assert by["new_faces_count"].value == 1
    assert all(by[n].kind == CheckKind.asserted for n in ("hole_axis_position", "hole_axis_direction", "hole_radius"))
    assert "hole_entry_face_modified" not in by  # only the blind row asserts this
    # the plate's top face IS its bounding rectangle (6000 mm2 = 100 x 60): the footprint check is asserted
    assert by["hole_within_face"].kind == CheckKind.asserted and by["hole_within_face"].value == PLATE_BBOX_TOP


def test_hole_within_face_is_only_reported_when_the_entry_face_is_not_its_bounding_rectangle() -> None:
    """A disc face (area pi r^2 inside a 2r x 2r box) or a face already holed is larger than its outline:
    box-level containment cannot prove the footprint is on the face, so the check is reported."""
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face)
    holed_top = stub.faces["face:top"].model_copy(update={"area_mm2": 6000.0 - math.pi * 3.3**2})
    stub.faces["face:top"] = holed_top
    op, request = _hole("through_all")
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["hole_within_face"].kind == CheckKind.reported and "not a full rectangle" in by["hole_within_face"].note


def test_hole_blind_inside_body_is_asserted_and_exiting_is_reported() -> None:
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face)
    op, request = _hole("blind", 5.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-math.pi * 3.3**2 * 5.0)
    assert by["new_faces_count"].value == 2
    assert "cut_extent_along_n" not in by
    assert by["hole_entry_face_modified"].kind == CheckKind.asserted
    assert by["hole_entry_face_modified"].value is True
    op, request = _hole("blind", 12.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["volume_delta"].kind == CheckKind.reported


def _oblique_hole_stub() -> StubAdapter:
    """A plate whose top face is tilted (normal 0.6, 0, 0.8): the hole's axis is not a principal direction."""
    stub = StubAdapter()
    stub.add_face(
        planar_face(
            "face:top",
            (0.6, 0.0, 0.8),
            (50.0, 30.0, 8.0),
            5000.0,
            BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0)),
        )
    )
    return stub


def test_oblique_through_all_hole_reports_the_bbox_extent_and_asserts_a_volume_bound() -> None:
    """The bounding-box extent of the hole's created faces is exact only along a principal direction: on an
    oblique face it is reported (the geometry was right, only the extent test was wrong) and the independent
    ``cut_volume_bound`` is asserted instead."""
    op, request = _hole("through_all")
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, _oblique_hole_stub()))
    assert by["cut_extent_along_n"].kind == CheckKind.reported
    assert "oblique" in by["cut_extent_along_n"].note
    bound = by["cut_volume_bound"]
    assert bound.kind == CheckKind.asserted
    assert bound.value == pytest.approx(math.pi * 3.3**2 * PLATE_BBOX.extent_along((0.6, 0.0, 0.8)))
    # the box of the whole body after an oblique cut is loose (cylindrical walls off the axes): reported, as for a cut
    assert by["bbox_within"].kind == CheckKind.reported and "off the world axes" in by["bbox_within"].note
    for name in ("hole_axis_direction", "hole_axis_position", "hole_radius"):
        assert by[name].kind == CheckKind.asserted
    assert by["new_faces_count"].kind == CheckKind.asserted


def test_oblique_blind_hole_reports_the_exact_volume_and_asserts_a_bound() -> None:
    """A blind hole on an oblique face: the bounding box cannot prove the cylinder stays inside the body, so the
    exact ``pi r^2 d`` is reported and the bound (never more than ``pi r^2 d``) is asserted."""
    op, request = _hole("blind", 5.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, _oblique_hole_stub()))
    assert by["volume_delta"].kind == CheckKind.reported
    assert by["volume_delta"].value == pytest.approx(-math.pi * 3.3**2 * 5.0)
    assert by["cut_volume_bound"].kind == CheckKind.asserted
    assert by["cut_volume_bound"].value == pytest.approx(math.pi * 3.3**2 * 5.0)
    assert by["hole_entry_face_modified"].kind == CheckKind.asserted
    assert "cut_extent_along_n" not in by


def test_axis_aligned_hole_rows_do_not_change() -> None:
    """A principal normal stays on the exact, asserted path (no oblique bound row)."""
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face)
    for end, depth in (("through_all", None), ("blind", 5.0)):
        op, request = _hole(end, depth)
        by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
        assert "cut_volume_bound" not in by


# --- hole on a cylindrical face and tapped holes -----------------------------------------------------------------

TUBE_BBOX = BoundingBox(min=(-20.0, -20.0, 0.0), max=(20.0, 20.0, 100.0))
TUBE = StateSnapshot(mass=mass(math.pi * 20.0**2 * 100.0, (0.0, 0.0, 50.0)), bbox=TUBE_BBOX)


def _tube_stub() -> StubAdapter:
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:tube",
            Axis(origin=(0.0, 0.0, 50.0), direction=(0.0, 0.0, 1.0)),
            20.0,
            (0.0, 0.0, 50.0),
            TUBE_BBOX,
        )
    )
    return stub


def _radial(end_condition: str, depth: float | None = None, position: tuple[float, float] = (10.0, 0.0)) -> tuple:
    op = HoleOperation(
        id="cross",
        intent="i",
        face=FaceRef(from_op="tube"),
        position=position,
        standard="simple",
        diameter_mm=6.0,
        end_condition=end_condition,
        depth_mm=depth,
    )
    request = HoleRequest(
        operation_id="cross",
        face=FaceHandle(id="face:tube"),
        position=position,
        standard="simple",
        diameter_mm=6.0,
        end_condition=end_condition,
        depth_mm=depth,
        drill_point="flat",  # these tests ask for the flat bottom explicitly (FreeCAD's default is angled)
    )
    return op, request


def test_radial_blind_hole_asserts_its_axis_and_reports_the_curved_wall_volume() -> None:
    op, request = _radial("blind", 8.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub()))
    assert by["hole_axis_direction"].value == pytest.approx((-1.0, 0.0, 0.0))
    assert by["hole_axis_position"].value == pytest.approx((0.0, 0.0, 60.0))  # on the tube axis, 10 mm up
    assert by["hole_radius"].value == pytest.approx(3.0)
    for name in ("hole_axis_direction", "hole_axis_position", "hole_radius", "hole_entry_face_modified", "bbox_within"):
        assert by[name].kind == CheckKind.asserted, name
    assert by["hole_entry_face_modified"].value is True
    volume = by["volume_delta"]
    assert volume.kind == CheckKind.reported  # OCC cylinder-cylinder volumes are ~1e-3 off: never asserted at 1e-4
    assert volume.value == pytest.approx(-radial_hole_volume(20.0, 0.0, 3.0, 8.0))
    bound = by["cut_volume_bound"]
    assert bound.kind == CheckKind.asserted and bound.value == pytest.approx(math.pi * 9.0 * 8.0)
    assert by["new_faces_count"].kind == CheckKind.reported
    assert "hole_within_face" not in by and "cut_extent_along_n" not in by


def test_radial_through_all_hole_crosses_the_whole_body_and_does_not_assert_an_entry_face() -> None:
    op, request = _radial("through_all")
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub()))
    assert "hole_entry_face_modified" not in by
    assert by["cut_volume_bound"].value == pytest.approx(math.pi * 9.0 * 40.0)  # the body is 40 mm across along X
    assert by["volume_delta"].value == pytest.approx(-radial_hole_volume(20.0, 0.0, 3.0, None))
    assert by["hole_axis_position"].kind == CheckKind.asserted


def test_radial_hole_position_follows_the_angle() -> None:
    op, request = _radial("blind", 5.0, position=(0.0, 90.0))
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub()))
    assert by["hole_axis_direction"].value == pytest.approx((0.0, -1.0, 0.0))
    assert by["hole_axis_position"].value == pytest.approx((0.0, 0.0, 50.0))


def test_radial_hole_records_the_edit_region_on_a_tracked_cylinder_without_forgetting_its_shape() -> None:
    stub = _tube_stub()
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        CylinderOperation(id="tube", intent="i", diameter_mm=40.0, height_mm=100.0),
        CylinderRequest(
            operation_id="tube", diameter_mm=40.0, height_mm=100.0, axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1))
        ),
        EMPTY,
        stub,
    )
    op, request = _radial("blind", 8.0)
    computer.compute(op, request, TUBE, stub)
    assert computer._shape_by_body["tube"] is not None
    assert len(computer._edits["tube"]) == 1
    region = computer._edits["tube"][0]
    assert region.min[0] >= 12.0 - 1e-9 and region.max[0] <= 20.0 + 1e-9  # the drilled slab on the +X side
    assert region.min[2] == pytest.approx(57.0) and region.max[2] == pytest.approx(63.0)


def test_a_hole_on_a_face_that_is_neither_planar_nor_cylindrical_is_still_only_reported() -> None:
    stub = _tube_stub()
    stub.faces["face:tube"] = stub.faces["face:tube"].model_copy(update={"surface": SurfaceType.cone, "axis": None})
    op, request = _radial("blind", 8.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, TUBE, stub))
    assert by["volume_delta"].kind == CheckKind.reported and "not planar" in by["volume_delta"].note


def _tapped(depth: float | None, thread_depth: float | None = None) -> tuple:
    op = HoleOperation(
        id="hole_1",
        intent="i",
        face=FaceRef(from_op="base"),
        position=(-40.0, -20.0),
        standard="iso_tapped",
        size="M6",
        end_condition="blind" if depth else "through_all",
        depth_mm=depth,
        thread_depth_mm=thread_depth,
    )
    request = HoleRequest(
        operation_id="hole_1",
        face=FaceHandle(id="face:top"),
        position=(-40.0, -20.0),
        standard="iso_tapped",
        size="M6",
        diameter_mm=5.0,
        end_condition="blind" if depth else "through_all",
        depth_mm=depth,
        drill_point="flat",  # these tests ask for the flat bottom explicitly (FreeCAD's default is angled)
        thread=HoleThread(size="M6", depth_mm=thread_depth),
    )
    return op, request


def test_a_tapped_hole_is_verified_as_its_tap_drill_cylinder_and_the_thread_is_only_reported() -> None:
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face)
    op, request = _tapped(5.0, 4.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["hole_radius"].value == pytest.approx(2.5) and by["hole_radius"].kind == CheckKind.asserted
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-math.pi * 2.5**2 * 5.0)
    meta = by["hole_thread_metadata"]
    assert meta.kind == CheckKind.reported and meta.value == 6.0
    assert "M6" in meta.note and "pitch 1" in meta.note and "thread depth 4" in meta.note
    through = _by_name(ExpectedComputer(Thresholds()).compute(*_tapped(None), PLATE, stub))
    assert through["hole_thread_metadata"].kind == CheckKind.reported


def test_a_plain_hole_has_no_thread_row() -> None:
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face)
    op, request = _hole("through_all")
    assert "hole_thread_metadata" not in _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))


# --- patterns --------------------------------------------------------------------------------------------


def _seed_hole_faces(stub: StubAdapter) -> None:
    stub.add_face(
        cylindrical_face(
            "face:cyl",
            Axis(origin=(10.0, 10.0, 8.0), direction=(0.0, 0.0, -1.0)),
            3.3,
            (10.0, 10.0, 4.0),
            BoundingBox(min=(6.7, 6.7, 0.0), max=(13.3, 13.3, 8.0)),
            created_by="feature:2",
        ),
        feature_id="feature:2",
    )


def test_linear_pattern_of_a_cut_seed() -> None:
    stub = StubAdapter()
    _seed_hole_faces(stub)
    op = LinearPatternOperation(
        id="holes",
        intent="i",
        features=["hole_1"],
        direction="X",
        spacing_mm=80,
        count=2,
        direction2="Y",
        spacing2_mm=40,
        count2=2,
    )
    request = LinearPatternRequest(
        operation_id="holes",
        features=(FeatureHandle(id="feature:2"),),
        direction=(1, 0, 0),
        spacing_mm=80,
        count=2,
        direction2=(0, 1, 0),
        spacing2_mm=40,
        count2=2,
    )
    seed_delta = -math.pi * 3.3**2 * 8.0
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=seed_delta)
    by = _by_name(computer_with_pad([PLATE_RECT]).compute(op, request, before, stub))
    assert by["instance_count"].value == 4
    assert by["created_faces_count"].value == 3
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(3 * seed_delta)
    # instances exiting the plate -> reported
    exiting = request.model_copy(update={"spacing_mm": 95.0})
    by = _by_name(computer_with_pad([PLATE_RECT]).compute(op, exiting, before, stub))
    assert by["volume_delta"].kind == CheckKind.reported
    # unknown seed delta -> reported
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["volume_delta"].kind == CheckKind.reported


def test_linear_pattern_of_overlapping_bosses_is_reported() -> None:
    stub = StubAdapter()
    stub.add_face(
        planar_face(
            "face:b",
            (0.0, 0.0, 1.0),
            (10.0, 10.0, 20.0),
            100.0,
            BoundingBox(min=(5.0, 5.0, 20.0), max=(15.0, 15.0, 20.0)),
            created_by="feature:2",
        ),
        feature_id="feature:2",
    )
    op = LinearPatternOperation(id="p", intent="i", features=["boss"], direction="X", spacing_mm=5, count=3)
    request = LinearPatternRequest(
        operation_id="p", features=(FeatureHandle(id="feature:2"),), direction=(1, 0, 0), spacing_mm=5, count=3
    )
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=1200.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, before, stub))
    assert by["volume_delta"].kind == CheckKind.reported
    assert by["volume_delta"].value == pytest.approx(2400.0)
    apart = request.model_copy(update={"spacing_mm": 10.0})
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, apart, before, stub))
    assert by["volume_delta"].kind == CheckKind.asserted


def test_circular_pattern() -> None:
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:bolt",
            Axis(origin=(40.0, 0.0, 10.0), direction=(0.0, 0.0, -1.0)),
            3.0,
            (40.0, 0.0, 5.0),
            BoundingBox(min=(37.0, -3.0, 0.0), max=(43.0, 3.0, 10.0)),
            created_by="feature:3",
        ),
        feature_id="feature:3",
    )
    disc = StateSnapshot(
        mass=mass(math.pi * 50.0**2 * 10.0),
        bbox=BoundingBox(min=(-50.0, -50.0, 0.0), max=(50.0, 50.0, 10.0)),
        seed_volume_delta=-math.pi * 9.0 * 10.0,
    )
    op = CircularPatternOperation(
        id="bolts", intent="i", features=["bolt"], axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1)), count=6
    )
    request = CircularPatternRequest(
        operation_id="bolts",
        features=(FeatureHandle(id="feature:3"),),
        axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1)),
        count=6,
    )
    by = _by_name(
        computer_with_pad([{"kind": "circle", "name": "outline", "center": [0, 0], "diameter": 100}], 10.0).compute(
            op, request, disc, stub
        )
    )
    assert by["instance_count"].value == 6
    assert by["created_faces_count"].value == 5
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(5 * -math.pi * 9.0 * 10.0)


# --- chamfer / fillet -----------------------------------------------------------------------------------------


def _plate_with_edge(stub: StubAdapter) -> None:
    for face in plate_faces():
        stub.add_face(face)
    # top/xmax edge along Y at x = 100, z = 8, length 60
    stub.add_edge(line_edge("edge:1", ("face:top", "face:xmax"), (100.0, 0.0, 8.0), (100.0, 60.0, 8.0)))


def test_chamfer_equal_distances() -> None:
    stub = StubAdapter()
    _plate_with_edge(stub)
    op = ChamferOperation(
        id="ch",
        intent="i",
        edges=[EdgeRef(between=(FaceRef(from_op="base"), FaceRef(from_op="base")))],
        distance1_mm=2.0,
    )
    request = ChamferRequest(operation_id="ch", edges=(EdgeHandle(id="edge:1"),), distance1_mm=2.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    # 2 * 2 / 2 * 60 = 120 removed
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-120.0)
    assert by["created_faces_count"].value == 1
    assert "centroid_after" not in by


def test_chamfer_unequal_distances_moves_the_centroid() -> None:
    stub = StubAdapter()
    _plate_with_edge(stub)
    op = ChamferOperation(
        id="ch",
        intent="i",
        edges=[EdgeRef(between=(FaceRef(from_op="base"), FaceRef(from_op="base")))],
        distance1_mm=4.0,
        distance2_mm=2.0,
        distance1_on=FaceRef(from_op="base"),
    )
    request = ChamferRequest(
        operation_id="ch",
        edges=(EdgeHandle(id="edge:1"),),
        distance1_mm=4.0,
        distance2_mm=2.0,
        distance1_face=FaceHandle(id="face:top"),
    )
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    # wedge: d1 = 4 on the top face (along -X from the edge), d2 = 2 on the xmax face (along -Z); V = 4*2/2*60 = 240
    # wedge centroid = (100 - 4/3, 30, 8 - 2/3); centroid_after = (48000*c0 - 240*cw) / 47760
    assert by["volume_delta"].value == pytest.approx(-240.0)
    cw = (100.0 - 4.0 / 3.0, 30.0, 8.0 - 2.0 / 3.0)
    expected = tuple((48000.0 * c - 240.0 * w) / 47760.0 for c, w in zip((50.0, 30.0, 4.0), cw, strict=True))
    assert by["centroid_after"].kind == CheckKind.asserted
    assert by["centroid_after"].value == pytest.approx(expected)
    # swapped distance1_on face flips the wedge centroid
    swapped = request.model_copy(update={"distance1_face": FaceHandle(id="face:xmax")})
    by2 = _by_name(ExpectedComputer(Thresholds()).compute(op, swapped, PLATE, stub))
    cw2 = (100.0 - 2.0 / 3.0, 30.0, 8.0 - 4.0 / 3.0)
    expected2 = tuple((48000.0 * c - 240.0 * w) / 47760.0 for c, w in zip((50.0, 30.0, 4.0), cw2, strict=True))
    assert by2["centroid_after"].value == pytest.approx(expected2)


def test_chamfer_on_non_square_edge_is_reported() -> None:
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face)
    stub.add_edge(line_edge("edge:1", ("face:top", "face:xmax"), (100.0, 0.0, 8.0), (100.0, 60.0, 8.0), dihedral=60.0))
    op = ChamferOperation(
        id="ch",
        intent="i",
        edges=[EdgeRef(between=(FaceRef(from_op="base"), FaceRef(from_op="base")))],
        distance1_mm=2.0,
    )
    request = ChamferRequest(operation_id="ch", edges=(EdgeHandle(id="edge:1"),), distance1_mm=2.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["volume_delta"].kind == CheckKind.reported


def test_fillet_convex_and_concave() -> None:
    stub = StubAdapter()
    _plate_with_edge(stub)
    op = FilletOperation(
        id="fi", intent="i", edges=[EdgeRef(between=(FaceRef(from_op="base"), FaceRef(from_op="base")))], radius_mm=2.0
    )
    request = FilletRequest(operation_id="fi", edges=(EdgeHandle(id="edge:1"),), radius_mm=2.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    # (1 - pi/4) * 4 * 60 = 240 - 60 pi
    assert by["volume_delta"].value == pytest.approx(-(240.0 - 60.0 * math.pi))
    assert by["created_faces_count"].value == 1
    assert by["fillet_radius"].value == 2.0
    stub.edges["edge:1"] = stub.edges["edge:1"].model_copy(update={"convex": False})
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["volume_delta"].value == pytest.approx(240.0 - 60.0 * math.pi)


# --- concept ops ----------------------------------------------------------------------------------------------


def _block_and_bore(stub: StubAdapter) -> ExpectedComputer:
    computer = ExpectedComputer(Thresholds())
    box_op = BoxOperation(id="block", intent="i", size=(100, 60, 20))
    box_request = BoxRequest(operation_id="block", size=(100, 60, 20))
    by = _by_name(computer.compute(box_op, box_request, EMPTY, stub))
    assert by["volume_delta"].value == pytest.approx(120000.0)
    assert by["bbox_after"].value == BoundingBox(min=(0, 0, 0), max=(100, 60, 20))
    assert by["new_faces_count"].value == 6
    block = StateSnapshot(mass=mass(120000.0, (50, 30, 10)), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    cyl_op = CylinderOperation(id="bore", intent="i", diameter_mm=30, height_mm=20, origin=(50, 30, 0))
    cyl_request = CylinderRequest(
        operation_id="bore", diameter_mm=30, height_mm=20, axis=Axis(origin=(50, 30, 0), direction=(0, 0, 1))
    )
    by = _by_name(computer.compute(cyl_op, cyl_request, block, stub))
    assert by["volume_delta"].value == pytest.approx(math.pi * 225.0 * 20.0)
    assert by["bbox_after"].value == BoundingBox(min=(0, 0, 0), max=(100, 60, 20))
    assert by["new_faces_count"].value == 3
    assert by["cylinder_radius"].value == 15.0
    assert by["cylinder_axis_direction"].value == pytest.approx((0.0, 0.0, 1.0))
    return computer


def test_box_cylinder_subtract_and_translate() -> None:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(120000.0, (50, 30, 10))
    stub.mass_by_body["body:2"] = mass(math.pi * 225.0 * 20.0)
    stub.bbox_by_body["body:1"] = BoundingBox(min=(0, 0, 0), max=(100, 60, 20))
    stub.bbox_by_body["body:2"] = BoundingBox(min=(35, 15, 0), max=(65, 45, 20))
    computer = _block_and_bore(stub)
    both = StateSnapshot(mass=mass(120000.0 + math.pi * 4500.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    sub_op = BooleanSubtractOperation(
        id="cutout", intent="i", target=BodyRef(from_op="block"), tools=[BodyRef(from_op="bore")]
    )
    sub_request = BooleanRequest(
        operation_id="cutout",
        kind=BooleanKind.subtract,
        target=BodyHandle(id="body:1"),
        tools=(BodyHandle(id="body:2"),),
    )
    by = _by_name(computer.compute(sub_op, sub_request, both, stub))
    # document-level delta: the tool body (already counted in mass_before) disappears and its
    # intersection with the target is removed: -(V_tool + V_overlap) = -2 * 4500 pi for a bore fully inside
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-2 * math.pi * 4500.0)
    assert by["bbox_within"].value == BoundingBox(min=(0, 0, 0), max=(100, 60, 20))
    after = StateSnapshot(mass=mass(120000.0 - math.pi * 4500.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    move_op = TranslateOperation(id="mv", intent="i", target=BodyRef(from_op="cutout"), offset=(10, 0, 5))
    move_request = MoveBodyRequest(
        operation_id="mv", body=BodyHandle(id="body:1"), transform=Transform(origin=(10, 0, 5))
    )
    by = _by_name(computer.compute(move_op, move_request, after, stub))
    assert by["volume_delta"] == Expectation(name="volume_delta", kind=CheckKind.asserted, unit="mm3", value=0.0)
    assert by["bbox_after"].kind == CheckKind.asserted
    assert by["bbox_after"].value == BoundingBox(min=(10, 0, 5), max=(110, 60, 25))
    # a second boolean on the composite body is no longer analytic
    by = _by_name(computer.compute(sub_op, sub_request, after, stub))
    assert by["volume_delta"].kind == CheckKind.reported


def test_boolean_subtract_keep_tools_delta_is_overlap_only() -> None:
    """Fish-mouth coping: with keep_tools, the tool body is NOT removed from the document -- unlike the
    plain subtract case (-(V_tool + V_overlap)), only the overlap disappears (-V_overlap, the same shape
    as union's formula), and the kept tool's own tracked shape is not invalidated."""
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(120000.0, (50, 30, 10))
    stub.mass_by_body["body:2"] = mass(math.pi * 225.0 * 20.0)
    stub.bbox_by_body["body:1"] = BoundingBox(min=(0, 0, 0), max=(100, 60, 20))
    stub.bbox_by_body["body:2"] = BoundingBox(min=(35, 15, 0), max=(65, 45, 20))
    computer = _block_and_bore(stub)
    both = StateSnapshot(mass=mass(120000.0 + math.pi * 4500.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    sub_op = BooleanSubtractOperation(
        id="cutout", intent="i", target=BodyRef(from_op="block"), tools=[BodyRef(from_op="bore")], keep_tools=True
    )
    sub_request = BooleanRequest(
        operation_id="cutout",
        kind=BooleanKind.subtract,
        target=BodyHandle(id="body:1"),
        tools=(BodyHandle(id="body:2"),),
        keep_tools=True,
    )
    by = _by_name(computer.compute(sub_op, sub_request, both, stub))
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-math.pi * 4500.0)
    assert by["bbox_within"].value == BoundingBox(min=(0, 0, 0), max=(100, 60, 20))


def test_boolean_union_of_disjoint_boxes_and_rotate() -> None:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(1000.0)
    stub.mass_by_body["body:2"] = mass(8000.0)
    stub.bbox_by_body["body:1"] = BoundingBox(min=(0, 0, 0), max=(10, 10, 10))
    stub.bbox_by_body["body:2"] = BoundingBox(min=(10, 0, 0), max=(30, 20, 20))
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="a", intent="i", size=(10, 10, 10)),
        BoxRequest(operation_id="a", size=(10, 10, 10)),
        EMPTY,
        stub,
    )
    computer.compute(
        BoxOperation(id="b", intent="i", size=(20, 20, 20), placement={"origin": [10, 0, 0]}),
        BoxRequest(operation_id="b", size=(20, 20, 20), transform=Transform(origin=(10, 0, 0))),
        EMPTY,
        stub,
    )
    doc = StateSnapshot(mass=mass(9000.0), bbox=BoundingBox(min=(0, 0, 0), max=(30, 20, 20)))
    op = BooleanUnionOperation(id="u", intent="i", target=BodyRef(from_op="a"), tools=[BodyRef(from_op="b")])
    request = BooleanRequest(
        operation_id="u", kind=BooleanKind.union, target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:2"),)
    )
    by = _by_name(computer.compute(op, request, doc, stub))
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(0.0)  # both bodies already counted; touching, no overlap
    assert by["bbox_after"].value == BoundingBox(min=(0, 0, 0), max=(30, 20, 20))
    rot = RotateOperation(id="r", intent="i", target=BodyRef(from_op="u"), axis="Z", angle_deg=45.0)
    rot_request = MoveBodyRequest(
        operation_id="r", body=BodyHandle(id="body:1"), transform=Transform.from_axis_angle((0, 0, 1), 45.0)
    )
    stub.bbox_by_body["body:1"] = doc.bbox
    by = _by_name(computer.compute(rot, rot_request, doc, stub))
    assert by["bbox_after"].kind == CheckKind.reported
    assert computer._shape_of("u") is None
    assert isinstance(computer._shape_of("a"), BoxShape) is False


# --- assembly ------------------------------------------------------------------------------------------------------


def test_insert_component_reads_the_part_document_and_restores_the_active_one() -> None:
    stub = StubAdapter()
    asm = stub.new_document(NewDocumentRequest(operation_id="x", kind=DocumentKind.assembly, name="asm"))
    part = DocumentHandle(id="doc:part")
    stub.mass_by_document["doc:part"] = mass(48000.0, (50, 30, 4))
    stub.bbox_by_document["doc:part"] = PLATE_BBOX
    computer = ExpectedComputer(Thresholds())
    op = InsertComponentOperation(id="ins", intent="i", part="plate", placement={"origin": [0, 0, 100]})
    transform = Transform(origin=(0, 0, 100))
    by = _by_name(
        computer.compute(op, InsertComponentRequest(operation_id="ins", part=part, transform=transform), EMPTY, stub)
    )
    assert by["volume_delta"].value == pytest.approx(48000.0)
    assert by["component_bbox"].kind == CheckKind.asserted
    assert by["component_bbox"].value == BoundingBox(min=(0, 0, 100), max=(100, 60, 108))
    assert by["component_transform"].value == transform
    assert stub.active is not None and stub.active.handle == asm.handle
    # 45 degree placement -> component bbox reported
    turned = Transform.from_axis_angle((0, 0, 1), 45.0)
    by = _by_name(
        computer.compute(op, InsertComponentRequest(operation_id="ins", part=part, transform=turned), EMPTY, stub)
    )
    assert by["component_bbox"].kind == CheckKind.reported
    # check_interference sums the inserted part volumes
    chk = CheckInterferenceOperation(id="chk", intent="i")
    by = _by_name(computer.compute(chk, InterferenceRequest(operation_id="chk"), EMPTY, stub))
    assert by["interference_total"].kind == CheckKind.asserted
    assert by["assembly_volume"].value == pytest.approx(48000.0)
    assert by["assembly_volume"].kind == CheckKind.asserted
    relaxed = CheckInterferenceOperation(id="chk", intent="i", expect_zero=False)
    by = _by_name(computer.compute(relaxed, InterferenceRequest(operation_id="chk"), EMPTY, stub))
    assert by["interference_total"].kind == CheckKind.reported


def test_insert_component_restores_active_document_even_if_mass_properties_raises() -> None:
    class _RaisingStub(StubAdapter):
        def mass_properties(self, request: MassRequest) -> MassProperties:  # type: ignore[override]
            raise RuntimeError("boom")

    stub = _RaisingStub()
    asm = stub.new_document(NewDocumentRequest(operation_id="x", kind=DocumentKind.assembly, name="asm"))
    part = DocumentHandle(id="doc:part")
    op = InsertComponentOperation(id="ins", intent="i", part="plate", placement={"origin": [0, 0, 100]})
    transform = Transform(origin=(0, 0, 100))
    computer = ExpectedComputer(Thresholds())
    with pytest.raises(RuntimeError):
        computer.compute(op, InsertComponentRequest(operation_id="ins", part=part, transform=transform), EMPTY, stub)
    assert stub.active is not None and stub.active.handle == asm.handle


def test_fix_component_expects_the_current_transform() -> None:
    stub = StubAdapter()
    t = Transform(origin=(1, 2, 3))
    stub.component_transforms["comp:1"] = t
    op = FixComponentOperation(id="fix", intent="i", component="ins")
    by = _by_name(
        ExpectedComputer(Thresholds()).compute(
            op, FixComponentRequest(operation_id="fix", component=ComponentHandle(id="comp:1")), EMPTY, stub
        )
    )
    assert by["component_transform"].value == t
    assert by["volume_delta"].value == 0.0


def _mate_setup(stub: StubAdapter) -> None:
    # a: bore face of the block, axis Z through (50, 30); b: pin face, axis Z through (200, 0)
    stub.add_face(
        cylindrical_face(
            "face:a", Axis(origin=(50, 30, 0), direction=(0, 0, 1)), 3.25, (50, 30, 10), PLATE_BBOX, component="comp:1"
        )
    )
    stub.add_face(
        cylindrical_face(
            "face:b", Axis(origin=(200, 0, 0), direction=(0, 0, -1)), 3.0, (200, 0, 20), PLATE_BBOX, component="comp:2"
        )
    )
    # planar: a = block top (z = 20, normal +Z), b = pin end (z = 0 of the pin at its place, normal -Z)
    stub.add_face(planar_face("face:pa", (0, 0, 1), (50, 30, 20), 6000.0, PLATE_BBOX, component="comp:1"))
    stub.add_face(planar_face("face:pb", (0, 0, -1), (200, 0, 5), 28.0, PLATE_BBOX, component="comp:2"))


def test_mate_concentric_translates_the_component_bbox_perpendicular_to_the_axis() -> None:
    stub = StubAdapter()
    _mate_setup(stub)
    pin_box = BoundingBox(min=(197, -3, 5), max=(203, 3, 45))
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, component_bbox_before=pin_box)
    op = MateConcentricOperation(id="m1", intent="i", a=FaceRef(from_op="block"), b=FaceRef(from_op="pin"))
    request = MateConcentricRequest(operation_id="m1", a=FaceHandle(id="face:a"), b=FaceHandle(id="face:b"))
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, before, stub))
    assert by["component_bbox"].kind == CheckKind.asserted
    assert by["component_bbox"].value == BoundingBox(min=(47, 27, 5), max=(53, 33, 45))
    # non-parallel axes -> reported
    stub.faces["face:b"] = stub.faces["face:b"].model_copy(
        update={"axis": Axis(origin=(200, 0, 0), direction=(1, 0, 0))}
    )
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, before, stub))
    assert by["component_bbox"].kind == CheckKind.reported
    # unknown component bbox before -> reported
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["component_bbox"].kind == CheckKind.reported


def test_mate_coincident_and_distance_translate_along_normal_of_a() -> None:
    stub = StubAdapter()
    _mate_setup(stub)
    pin_box = BoundingBox(min=(47, 27, 5), max=(53, 33, 45))
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, component_bbox_before=pin_box)
    op = MateCoincidentOperation(id="m2", intent="i", a=FaceRef(from_op="block"), b=FaceRef(from_op="pin"))
    request = MateCoincidentRequest(operation_id="m2", a=FaceHandle(id="face:pa"), b=FaceHandle(id="face:pb"))
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, before, stub))
    # t = ((a.center - b.center) . n_a) n_a = (20 - 5) Z = +15 Z
    assert by["component_bbox"].value == BoundingBox(min=(47, 27, 20), max=(53, 33, 60))
    op_d = MateDistanceOperation(
        id="m3", intent="i", a=FaceRef(from_op="block"), b=FaceRef(from_op="pin"), distance_mm=4.0, side="negative"
    )
    request_d = MateDistanceRequest(
        operation_id="m3", a=FaceHandle(id="face:pa"), b=FaceHandle(id="face:pb"), distance_mm=4.0, side="negative"
    )
    by = _by_name(ExpectedComputer(Thresholds()).compute(op_d, request_d, before, stub))
    # t = (15 - 4) Z
    assert by["component_bbox"].value == BoundingBox(min=(47, 27, 16), max=(53, 33, 56))


def test_unknown_op_yields_no_expectations() -> None:
    class Weird:
        op = "weird"
        id = "w"

    assert (
        ExpectedComputer(Thresholds()).compute(Weird(), InterferenceRequest(operation_id="w"), EMPTY, StubAdapter())
        == ()
    )  # type: ignore[arg-type]


def test_boolean_intersect_delta_is_document_level() -> None:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(1000.0)
    stub.mass_by_body["body:2"] = mass(1000.0)
    stub.bbox_by_body["body:1"] = BoundingBox(min=(0, 0, 0), max=(10, 10, 10))
    stub.bbox_by_body["body:2"] = BoundingBox(min=(5, 0, 0), max=(15, 10, 10))
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="a", intent="i", size=(10, 10, 10)),
        BoxRequest(operation_id="a", size=(10, 10, 10)),
        EMPTY,
        stub,
    )
    computer.compute(
        BoxOperation(id="b", intent="i", size=(10, 10, 10), placement={"origin": [5, 0, 0]}),
        BoxRequest(operation_id="b", size=(10, 10, 10), transform=Transform(origin=(5, 0, 0))),
        EMPTY,
        stub,
    )
    doc = StateSnapshot(mass=mass(2000.0), bbox=BoundingBox(min=(0, 0, 0), max=(15, 10, 10)))
    op = BooleanIntersectOperation(id="x", intent="i", target=BodyRef(from_op="a"), tools=[BodyRef(from_op="b")])
    request = BooleanRequest(
        operation_id="x", kind=BooleanKind.intersect, target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:2"),)
    )
    by = _by_name(computer.compute(op, request, doc, stub))
    # overlap 5 x 10 x 10 = 500; after = 500, before = 2000 -> -1500
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-1500.0)
    assert by["bbox_within"].value == BoundingBox(min=(0, 0, 0), max=(10, 10, 10))
    assert computer._shape_of("x") == BoxShape(min=(5, 0, 0), max=(10, 10, 10))


def test_revolve_segment_on_the_axis_creates_no_face() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    # stepped half-profile touching the axis (v = 0) along one segment: 8 segments, 7 faces
    pts = [(0, 0), (0, 10), (20, 10), (20, 15), (50, 15), (50, 8), (70, 8), (70, 0)]
    lines = [
        {"kind": "line", "name": f"l{i}", "start": list(pts[i]), "end": list(pts[(i + 1) % len(pts)])}
        for i in range(len(pts))
    ]
    sketch = SketchOperation(id="rs", intent="i", on=PlaneRef(name="XY"), entities=lines)
    computer.compute(sketch, SketchRequest(operation_id="rs", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub)
    op = RevolveOperation(id="rev", intent="i", sketch="rs", axis="u")
    request = RevolveRequest(
        operation_id="rev", sketch=SketchHandle(id="s"), axis=Axis(origin=(0, 0, 0), direction=(1, 0, 0))
    )
    by = _by_name(computer.compute(op, request, EMPTY, stub))
    assert by["new_faces_count"].value == 7
    assert by["volume_delta"].kind == CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(math.pi * (10**2 * 20 + 15**2 * 30 + 8**2 * 20))
    assert by["bbox_after"].value == BoundingBox(min=(0, -15, -15), max=(70, 15, 15))


def test_a_radial_hole_off_the_world_axes_only_reports_the_body_box() -> None:
    """Angle 45 on a vertical tube: the hole axis is oblique, so the kernel's loose box of its wall can grow the box of
    the whole body: ``bbox_within`` is reported there and asserted when the axis is a world axis."""
    op, request = _radial("blind", 8.0, position=(10.0, 45.0))
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub()))
    assert by["bbox_within"].kind == CheckKind.reported
    for name in ("hole_axis_direction", "hole_axis_position", "hole_radius", "cut_volume_bound"):
        assert by[name].kind == CheckKind.asserted, name
    along_x = _by_name(ExpectedComputer(Thresholds()).compute(*_radial("blind", 8.0), TUBE, _tube_stub()))
    assert along_x["bbox_within"].kind == CheckKind.asserted


def test_a_radial_hole_that_provably_crosses_the_axial_end_of_its_face_is_a_plan_error() -> None:
    """The tube face spans z in [0, 100] around its centre (a world-axis tube: the box is exact): a D6 hole 49 mm from
    the middle needs 52 mm of face. The worker refuses it too; the expectation raises first so the executor reports a
    plan error before any adapter call."""
    op, request = _radial("blind", 8.0, position=(49.0, 0.0))
    with pytest.raises(HoleLeavesFaceError, match="axial end"):
        ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub())
    op, request = _radial("blind", 8.0, position=(-46.0, 0.0))
    ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub())  # 1 mm of margin: fine


def test_the_axial_check_is_skipped_when_the_box_of_the_face_is_loose() -> None:
    """A tilted tube has a loose face box: nothing is provable, so nothing is refused here (the worker decides)."""
    stub = _tube_stub()
    tilted = stub.faces["face:tube"].model_copy(
        update={"axis": Axis(origin=(0.0, 0.0, 50.0), direction=(0.0, -0.5, 0.8660254037844386))}
    )
    stub.faces["face:tube"] = tilted
    op, request = _radial("blind", 8.0, position=(49.0, 0.0))
    ExpectedComputer(Thresholds()).compute(op, request, TUBE, stub)


def test_the_axial_offset_is_measured_along_the_canonical_axis_in_the_expectation_too() -> None:
    stub = _tube_stub()
    stub.faces["face:tube"] = stub.faces["face:tube"].model_copy(
        update={"axis": Axis(origin=(0.0, 0.0, 50.0), direction=(0.0, 0.0, -1.0))}
    )
    op, request = _radial("blind", 8.0, position=(10.0, 0.0))
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, TUBE, stub))
    assert by["hole_axis_position"].value == pytest.approx((0.0, 0.0, 60.0))


def test_a_radial_hole_tangent_to_the_axial_end_is_refused_with_the_largest_allowed_offset() -> None:
    op, request = _radial("blind", 8.0, position=(47.0, 0.0))  # D6: touches z = 50 exactly
    with pytest.raises(HoleLeavesFaceError, match=r"keep \|axial_mm\| <= 46\.95"):
        ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub())
    op, request = _radial("blind", 8.0, position=(46.9, 0.0))
    ExpectedComputer(Thresholds()).compute(op, request, TUBE, _tube_stub())


# --- extrude from a base plane that reaches the body: coplanar faces merge (refined solid) ------------------------

WALL = {"kind": "rect", "name": "wall", "center": [40, -31], "width": 80, "height": 50}
BASE_PLATE = StateSnapshot(
    mass=PLATE_MASS, bbox=BoundingBox(min=(0.0, 0.0, 0.0), max=(80.0, 60.0, 6.0))
)  # L bracket base: 80 x 60 x 6


def _wall_faces(plane: PlaneName, before: StateSnapshot, *, merge: bool = True) -> Expectation:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    sketch = SketchOperation(id="ws", intent="i", on=PlaneRef(name=plane.value), entities=[WALL])
    computer.compute(sketch, SketchRequest(operation_id="ws", on=plane, entities=sketch.entities), before, stub)
    op = ExtrudeOperation(id="wall", intent="i", sketch="ws", depth_mm=6.0, merge=merge)
    request = ExtrudeRequest(operation_id="wall", sketch=SketchHandle(id="sk:1"), depth_mm=6.0, merge=merge)
    return _by_name(computer.compute(op, request, before, stub))["new_faces_count"]


def test_a_merged_extrude_from_a_plane_that_reaches_the_body_only_reports_the_face_count() -> None:
    faces = _wall_faces(PlaneName.XZ, BASE_PLATE)
    assert faces.kind is CheckKind.reported
    assert faces.value == 6
    assert "coplanar" in faces.note


def test_the_face_count_stays_asserted_for_the_first_solid_a_detached_body_or_a_clear_prism() -> None:
    assert _wall_faces(PlaneName.XZ, EMPTY).kind is CheckKind.asserted
    assert _wall_faces(PlaneName.XZ, BASE_PLATE, merge=False).kind is CheckKind.asserted
    far = StateSnapshot(mass=PLATE_MASS, bbox=BoundingBox(min=(0.0, 100.0, 0.0), max=(80.0, 160.0, 6.0)))
    assert _wall_faces(PlaneName.XZ, far).kind is CheckKind.asserted
