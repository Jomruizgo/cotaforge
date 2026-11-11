"""Verification by the real geometry: what a hole, a cut and a boss must change comes from one boolean of their tool
with the part (``tool_overlap``), asked of the adapter before the op runs. A stub adapter answers the boolean here; the
real one is exercised on FreeCAD (``tests/integration/freecad/test_hole_full.py``)."""

import math

import pytest

from cotaforge.contract import (
    AdapterError,
    CircleEntity,
    CutRequest,
    DrillPoint,
    ExtrudeRequest,
    FaceHandle,
    HoleCounterbore,
    HoleCountersink,
    HoleRequest,
    HoleStandard,
    MassProperties,
    PlaneName,
    RectEntity,
    SketchHandle,
    SketchRequest,
    ToolOverlapRequest,
    ToolOverlapResult,
    net_area,
    profile_depths,
    sketch_profiles,
)
from cotaforge.contract.holetool import hole_outline, outline_volume
from cotaforge.ir.ops_features import CutOperation, ExtrudeOperation, HoleOperation, SketchOperation
from cotaforge.ir.refs import FaceRef, PlaneRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot
from cotaforge.verification.results import CheckKind, Expectation
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import PLATE_BBOX, PLATE_MASS, plate_faces

PLATE = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX)
EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)


class ProbingStub(StubAdapter):
    """A stub that answers ``tool_overlap`` with what the test scripted and remembers what it was asked."""

    def __init__(self, tool: float, common: float, then: float | None = None) -> None:
        super().__init__()
        self.answer = ToolOverlapResult(tool_volume_mm3=tool, common_volume_mm3=common)
        self.later = ToolOverlapResult(tool_volume_mm3=tool, common_volume_mm3=then) if then is not None else None
        self.asked: list[ToolOverlapRequest] = []

    def tool_overlap(self, request: ToolOverlapRequest) -> ToolOverlapResult:
        self.asked.append(request)
        # a question after the first one (the shorter tool of a through hole) has its own answer when scripted
        return self.later if self.later is not None and len(self.asked) > 1 else self.answer


def _by_name(expectations: tuple[Expectation, ...]) -> dict[str, Expectation]:
    return {e.name: e for e in expectations}


def _observation(removed: float) -> Observation:
    after = MassProperties(volume_mm3=PLATE_MASS.volume_mm3 - removed, centroid=PLATE_MASS.centroid)
    return Observation(
        mass_after=after,
        bbox_after=PLATE_BBOX,
        created_faces=(),
        modified_faces=(),
        created_edges=(),
        instance_count=None,
        component_bbox=None,
        component_transform=None,
        interference=None,
        mass_before=PLATE_MASS,
    )


def _verdict(expectations: tuple[Expectation, ...], removed: float, name: str) -> bool | None:
    results = Level2Check(Thresholds()).check(expectations, _observation(removed))
    return next(r for r in results if r.name == name).ok


def _hole(counterbore: bool = False, end: str = "through_all", depth: float | None = None) -> tuple:
    extra = {"counterbore": {"diameter_mm": 14, "depth_mm": 3}} if counterbore else {}
    op = HoleOperation(
        id="hole_1",
        intent="i",
        face=FaceRef(from_op="base"),
        position=(-40.0, -20.0),
        standard="iso_clearance",
        size="M8",
        end_condition=end,
        depth_mm=depth,
        **extra,
    )
    request = HoleRequest(
        operation_id="hole_1",
        face=FaceHandle(id="face:top"),
        position=(-40.0, -20.0),
        standard=HoleStandard.iso_clearance,
        size="M8",
        diameter_mm=9.0,
        end_condition=end,
        depth_mm=depth,
        counterbore=HoleCounterbore(diameter_mm=14, depth_mm=3) if counterbore else None,
        drill_point=DrillPoint.flat,
    )
    return op, request


def _stub(tool: float, common: float, then: float | None = None) -> ProbingStub:
    stub = ProbingStub(tool, common, then)
    for face in plate_faces():
        stub.add_face(face)
    return stub


# --- hole ---------------------------------------------------------------------------------------------------------


def test_a_counterbored_hole_expects_the_boolean_and_is_judged_by_it() -> None:
    op, request = _hole(counterbore=True)
    closed = outline_volume(hole_outline(9.0, 8.0, counterbore=(14.0, 3.0)))  # the plate is 8 mm thick
    stub = _stub(tool=5000.0, common=closed)
    rows = ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub)
    by = _by_name(rows)
    assert stub.asked[0].mode == "cut" and stub.asked[0].hole is not None
    assert by["volume_delta"].kind is CheckKind.asserted and by["volume_delta"].value == pytest.approx(-closed)
    assert "real geometry" in by["volume_delta"].note
    # a hole in solid material: the structure of the faces it creates is asserted (hole wall, counterbore wall, floor)
    assert by["new_faces_count"].kind is CheckKind.asserted and by["new_faces_count"].value == 3
    assert _verdict(rows, closed, "volume_delta") is True


def test_a_hole_that_breaks_into_a_cavity_passes_with_the_volume_of_what_it_really_removes() -> None:
    op, request = _hole()
    closed = math.pi * 4.5**2 * 8.0
    # half of the cylinder was already air, and the half that is material is not the first half from the entry
    stub = _stub(tool=closed * 10, common=closed / 2.0, then=closed / 4.0)
    rows = ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub)
    by = _by_name(rows)
    assert by["volume_delta"].value == pytest.approx(-closed / 2.0)
    # ... so the faces of a hole in solid material are not promised
    for name in ("new_faces_count", "hole_radius", "hole_axis_position"):
        assert by[name].kind is CheckKind.reported, name
    # the walls still run between the material removed over the section and the reach of the body
    assert by["cut_extent_along_n"].kind is CheckKind.asserted and by["cut_extent_along_n"].value == pytest.approx(8.0)
    assert _verdict(rows, closed / 2.0, "volume_delta") is True


def test_a_wrong_result_still_fails() -> None:
    op, request = _hole()
    closed = math.pi * 4.5**2 * 8.0
    rows = ExpectedComputer(Thresholds()).compute(op, request, PLATE, _stub(tool=closed * 10, common=closed / 2.0))
    assert _verdict(rows, closed, "volume_delta") is False  # removed more than the tool can reach in material
    assert _verdict(rows, closed / 4.0, "volume_delta") is False  # removed less
    assert _verdict(rows, 0.0, "volume_delta") is False  # nothing removed
    assert _verdict(rows, 0.0, "volume_sign") is False


def test_a_hole_placed_in_air_expects_a_fall_and_fails_when_nothing_changes() -> None:
    op, request = _hole()
    rows = ExpectedComputer(Thresholds()).compute(op, request, PLATE, _stub(tool=1000.0, common=0.0))
    assert _verdict(rows, 0.0, "volume_sign") is False


def test_a_blind_countersunk_hole_asks_for_its_own_half_section() -> None:
    op = HoleOperation(
        id="hole_1",
        intent="i",
        face=FaceRef(from_op="base"),
        position=(-40.0, -20.0),
        standard="simple",
        diameter_mm=5.5,
        end_condition="blind",
        depth_mm=6.0,
        countersink={"diameter_mm": 10.4},
        drill_point="angled",
    )
    request = HoleRequest(
        operation_id="hole_1",
        face=FaceHandle(id="face:top"),
        position=(-40.0, -20.0),
        standard=HoleStandard.simple,
        diameter_mm=5.5,
        end_condition="blind",
        depth_mm=6.0,
        countersink=HoleCountersink(diameter_mm=10.4, angle_deg=90.0),
        drill_point=DrillPoint.angled,
    )
    stub = _stub(tool=1.0, common=1.0)
    ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub)
    outline = stub.asked[0].hole.outline  # type: ignore[union-attr]
    assert outline[0] == (0.0, 0.0) and outline[1] == pytest.approx((0.0, 5.2))
    assert outline[2] == pytest.approx((2.45, 2.75))  # the cone: 90 degrees, 5.2 down to 2.75
    assert outline[3] == pytest.approx((6.0, 2.75)) and outline[4][0] > 6.0  # the cylinder, then the drill point


def test_an_adapter_without_the_query_keeps_the_closed_form() -> None:
    op, request = _hole(end="blind", depth=5.0)
    stub = StubAdapter()  # tool_overlap is an optional tool: not supported
    for face in plate_faces():
        stub.add_face(face)
    with pytest.raises(AdapterError):
        stub.tool_overlap(ToolOverlapRequest(operation_id="x", mode="cut", sketch=SketchHandle(id="sketch:1"), hi_mm=1))
    by = _by_name(ExpectedComputer(Thresholds()).compute(op, request, PLATE, stub))
    assert by["volume_delta"].value == pytest.approx(-math.pi * 4.5**2 * 5.0)
    assert "volume_sign" not in by


# --- cut / extrude ------------------------------------------------------------------------------------------------


def _sketch_into(computer: ExpectedComputer, stub: StubAdapter, entities: list[dict], before: StateSnapshot) -> None:
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(name="XY"), entities=entities)
    request = SketchRequest(operation_id="s", on=PlaneName.XY, entities=sketch.entities)
    computer.compute(sketch, request, before, stub)


def test_a_cut_expects_what_its_tool_really_removes() -> None:
    stub = _stub(tool=2400.0, common=1500.0)  # the pocket is partly in air
    computer = ExpectedComputer(Thresholds())
    _sketch_into(computer, stub, [{"kind": "rect", "name": "r", "center": [50, 30], "width": 20, "height": 20}], PLATE)
    op = CutOperation(id="c", intent="i", sketch="s", depth_mm=6.0)
    request = CutRequest(operation_id="c", sketch=SketchHandle(id="sketch:1"), depth_mm=6.0)
    rows = computer.compute(op, request, PLATE, stub)
    by = _by_name(rows)
    assert by["volume_delta"].value == pytest.approx(-1500.0) and by["volume_delta"].kind is CheckKind.asserted
    asked = stub.asked[0]
    assert asked.mode == "cut" and (asked.lo_mm, asked.hi_mm) == (-6.0, 0.0)
    assert _verdict(rows, 1500.0, "volume_delta") is True
    assert _verdict(rows, 2400.0, "volume_delta") is False


def test_a_boss_adds_its_tool_minus_what_is_already_material() -> None:
    stub = _stub(tool=1200.0, common=200.0)
    computer = ExpectedComputer(Thresholds())
    _sketch_into(computer, stub, [{"kind": "rect", "name": "r", "center": [50, 30], "width": 20, "height": 20}], PLATE)
    op = ExtrudeOperation(id="e", intent="i", sketch="s", depth_mm=3.0)
    request = ExtrudeRequest(operation_id="e", sketch=SketchHandle(id="sketch:1"), depth_mm=3.0)
    by = _by_name(computer.compute(op, request, PLATE, stub))
    assert by["volume_delta"].value == pytest.approx(1000.0)
    assert by["new_faces_count"].kind is CheckKind.reported  # it overlaps material: faces merge unpredictably
    assert stub.asked[0].mode == "add"


# --- nesting ------------------------------------------------------------------------------------------------------


def test_a_loop_inside_a_loop_is_subtracted_by_the_even_odd_rule() -> None:
    ring = sketch_profiles(
        [CircleEntity(name="o", center=(0, 0), diameter=24), CircleEntity(name="i", center=(0, 0), diameter=16)]
    )
    assert profile_depths(ring) == [0, 1]
    assert net_area(ring) == pytest.approx(math.pi * (144.0 - 64.0))
    island = sketch_profiles(
        [
            RectEntity(name="a", center=(0, 0), width=40, height=40),
            RectEntity(name="b", center=(0, 0), width=20, height=20),
            RectEntity(name="c", center=(0, 0), width=6, height=6),
            RectEntity(name="d", center=(100, 0), width=6, height=6),
        ]
    )
    assert profile_depths(island) == [0, 1, 2, 0]
    assert net_area(island) == pytest.approx(1600.0 - 400.0 + 36.0 + 36.0)


def test_a_ring_extrude_expects_the_annulus_volume_and_its_faces() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    entities = [
        {"kind": "circle", "name": "o", "center": [0, 0], "diameter": 24},
        {"kind": "circle", "name": "i", "center": [0, 0], "diameter": 16},
    ]
    _sketch_into(computer, stub, entities, EMPTY)
    op = ExtrudeOperation(id="e", intent="i", sketch="s", depth_mm=5.0)
    request = ExtrudeRequest(operation_id="e", sketch=SketchHandle(id="sketch:1"), depth_mm=5.0)
    by = _by_name(computer.compute(op, request, EMPTY, stub))
    assert by["volume_delta"].value == pytest.approx(math.pi * 80.0 * 5.0)
    assert by["new_faces_count"].value == 4  # two caps, the outer wall and the inner wall: ONE region, not two
