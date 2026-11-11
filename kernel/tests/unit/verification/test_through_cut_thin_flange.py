"""A through cut or hole is judged against the material its profile sweeps through, never against the height of the
part: a 25 mm flange with an 80 mm hub on it is drilled through 25 mm of material, and that is a correct through hole.

The part: a 200 mm disc 25 mm thick (z 0..25) with a 60 mm hub 80 mm tall on top (z 25..105). The tool goes through the
flange outside the hub. A stub adapter answers ``tool_overlap`` from a column of material along z (the intervals that
are solid under the tool), the way the real boolean would."""

import math

import pytest

from cotaforge.contract import (
    BoundingBox,
    CutRequest,
    DrillPoint,
    FaceHandle,
    HoleRequest,
    HoleStandard,
    MassProperties,
    PlaneName,
    SketchHandle,
    SketchRequest,
    ToolOverlapRequest,
    ToolOverlapResult,
)
from cotaforge.ir.ops_features import CutOperation, HoleOperation, SketchOperation
from cotaforge.ir.refs import FaceRef, PlaneRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot
from cotaforge.verification.results import CheckKind, CheckResult, Expectation
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import StubAdapter

from .helpers import mass, planar_face

SIDE = 10.0
SECTION = SIDE * SIDE
HOLE_R = 4.5
HOLE_AREA = math.pi * HOLE_R**2


def _snapshot(low: float, high: float, volume: float = 1.0e6) -> StateSnapshot:
    return StateSnapshot(
        mass=mass(volume, (0.0, 0.0, (low + high) / 2.0)),
        bbox=BoundingBox(min=(-100.0, -100.0, low), max=(100.0, 100.0, high)),
    )


UP = _snapshot(0.0, 105.0)  # flange z 0..25, hub above it
DOWN = _snapshot(-105.0, 0.0)  # flange z -25..0, spigot below it
MIDDLE = _snapshot(-12.5, 92.5)  # flange z -12.5..12.5, hub above it


class ColumnStub(StubAdapter):
    """Answers ``tool_overlap`` for a tool whose axis runs along z through ``solid`` (z intervals of material)."""

    def __init__(self, solid: list[tuple[float, float]], *, top_face: float | None = None) -> None:
        super().__init__()
        self.solid = solid
        self.asked: list[ToolOverlapRequest] = []
        self.top_face = top_face

    def _inside(self, lo: float, hi: float) -> float:
        return sum(max(0.0, min(hi, b) - max(lo, a)) for a, b in self.solid)

    def tool_overlap(self, request: ToolOverlapRequest) -> ToolOverlapResult:
        self.asked.append(request)
        if request.hole is not None:
            length = max(t for t, _ in request.hole.outline)
            # a plain hole: the face is the flange bottom at z = 0 and the hole drills towards +z
            return ToolOverlapResult(
                tool_volume_mm3=HOLE_AREA * length, common_volume_mm3=HOLE_AREA * self._inside(0.0, length)
            )
        lo, hi = request.lo_mm, request.hi_mm
        return ToolOverlapResult(tool_volume_mm3=SECTION * (hi - lo), common_volume_mm3=SECTION * self._inside(lo, hi))


def _by_name(expectations: tuple[Expectation, ...]) -> dict[str, Expectation]:
    return {e.name: e for e in expectations}


def _cut_rows(
    before: StateSnapshot, stub: StubAdapter, direction: str = "positive", both: bool = False
) -> tuple[Expectation, ...]:
    computer = ExpectedComputer(Thresholds())
    slot = {"kind": "rect", "name": "slot", "center": [70, 0], "width": SIDE, "height": SIDE}
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(name="XY"), entities=[slot])
    computer.compute(sketch, SketchRequest(operation_id="s", on=PlaneName.XY, entities=sketch.entities), before, stub)
    op = CutOperation(
        id="c", intent="i", sketch="s", end_condition="through_all", direction=direction, both_directions=both
    )
    request = CutRequest(
        operation_id="c",
        sketch=SketchHandle(id="sketch:1"),
        end_condition="through_all",
        direction=direction,
        both_directions=both,
    )
    return computer.compute(op, request, before, stub)


def _hole_rows(before: StateSnapshot, stub: StubAdapter) -> tuple[Expectation, ...]:
    stub.add_face(
        planar_face(
            "face:bottom",
            (0.0, 0.0, -1.0),
            (0.0, 0.0, 0.0),
            31416.0,
            BoundingBox(min=(-100.0, -100.0, 0.0), max=(100.0, 100.0, 0.0)),
        )
    )
    op = HoleOperation(
        id="h",
        intent="i",
        face=FaceRef(from_op="base"),
        position=(70.0, 0.0),
        standard="iso_clearance",
        size="M8",
        end_condition="through_all",
    )
    request = HoleRequest(
        operation_id="h",
        face=FaceHandle(id="face:bottom"),
        position=(70.0, 0.0),
        standard=HoleStandard.iso_clearance,
        size="M8",
        diameter_mm=2 * HOLE_R,
        end_condition="through_all",
        drill_point=DrillPoint.flat,
    )
    return ExpectedComputer(Thresholds()).compute(op, request, before, stub)


def _observe(before: StateSnapshot, removed: float, wall_low: float, wall_high: float) -> Observation:
    """What the CAD reports: ``removed`` mm3 gone and the cut's walls between ``wall_low`` and ``wall_high`` in z."""
    wall = planar_face(
        "wall",
        (1.0, 0.0, 0.0),
        (75.0, 0.0, (wall_low + wall_high) / 2.0),
        100.0,
        BoundingBox(min=(75.0, -5.0, wall_low), max=(75.0, 5.0, wall_high)),
    )
    volume = before.mass.volume_mm3
    return Observation(
        mass_after=MassProperties(volume_mm3=volume - removed, centroid=before.mass.centroid),
        bbox_after=before.bbox,
        created_faces=(wall,),
        modified_faces=(),
        created_edges=(),
        instance_count=None,
        component_bbox=None,
        component_transform=None,
        interference=None,
        mass_before=before.mass,
    )


def _verdict(rows: tuple[Expectation, ...], observation: Observation) -> dict[str, bool | None]:
    return {r.name: r.ok for r in Level2Check(Thresholds()).check(rows, observation)}


def _results(rows: tuple[Expectation, ...], observation: Observation) -> dict[str, CheckResult]:
    return {r.name: r for r in Level2Check(Thresholds()).check(rows, observation)}


# --- a cut -------------------------------------------------------------------------------------------------------


def test_a_cut_up_through_a_flange_with_a_hub_passes_when_the_adapter_cannot_measure() -> None:
    """No ``tool_overlap`` (an external adapter): the 105 mm of the part is only the upper bound of the walls, which run the
    25 mm of the flange. It used to be the asserted value, and a correct cut was rejected."""
    rows = _cut_rows(UP, StubAdapter())
    by = _by_name(rows)
    assert by["cut_extent_along_n"].kind is CheckKind.asserted
    assert by["cut_extent_along_n"].value == pytest.approx(105.0)  # the reach of the body: an upper bound
    assert by["cut_area"].value == pytest.approx(SECTION)
    verdict = _results(rows, _observe(UP, SECTION * 25.0, 0.0, 25.0))["cut_extent_along_n"]
    assert verdict.ok is True and verdict.observed == pytest.approx(25.0)


def test_a_cut_whose_walls_run_past_the_part_fails() -> None:
    rows = _cut_rows(UP, StubAdapter())
    result = _results(rows, _observe(UP, SECTION * 25.0, 0.0, 130.0))["cut_extent_along_n"]
    assert result.ok is False and "past" in result.message


def test_a_cut_that_removed_more_than_its_walls_can_account_for_fails() -> None:
    """The cut removed 25 mm of material over its section but its walls span 10 mm: it says through and the model
    disagrees with itself."""
    rows = _cut_rows(UP, StubAdapter())
    result = _results(rows, _observe(UP, SECTION * 25.0, 0.0, 10.0))["cut_extent_along_n"]
    assert result.ok is False and "material" in result.message


def test_a_measured_cut_through_one_flange_expects_the_flange_thickness() -> None:
    stub = ColumnStub([(0.0, 25.0)])
    rows = _cut_rows(UP, stub)
    by = _by_name(rows)
    assert by["volume_delta"].value == pytest.approx(-SECTION * 25.0) and by["volume_delta"].kind is CheckKind.asserted
    assert by["cut_extent_along_n"].value == pytest.approx(25.0)  # the material, not the 105 mm of the part
    assert by["cut_extent_along_n"].kind is CheckKind.asserted
    assert len(stub.asked) == 2  # the whole reach, then the 25 mm that hold all of it
    verdicts = _verdict(rows, _observe(UP, SECTION * 25.0, 0.0, 25.0))
    assert verdicts["volume_delta"] is True and verdicts["cut_extent_along_n"] is True


def test_a_measured_cut_that_leaves_material_where_through_was_asked_fails() -> None:
    """The flange is 25 mm thick; the CAD removed only 10 mm of it: material is left, and the volume says so."""
    rows = _cut_rows(UP, ColumnStub([(0.0, 25.0)]))
    verdicts = _verdict(rows, _observe(UP, SECTION * 10.0, 0.0, 10.0))
    assert verdicts["volume_delta"] is False
    assert verdicts["cut_extent_along_n"] is False  # its walls (10 mm) do not reach the 25 mm of the block


def test_a_cut_downwards_through_a_flange_with_a_spigot_under_it() -> None:
    stub = ColumnStub([(-25.0, 0.0)])
    rows = _cut_rows(DOWN, stub, direction="negative")
    by = _by_name(rows)
    assert by["cut_extent_along_n"].value == pytest.approx(25.0)
    assert by["volume_delta"].value == pytest.approx(-SECTION * 25.0)
    assert (stub.asked[1].lo_mm, stub.asked[1].hi_mm) == pytest.approx((-25.0, 0.0))  # the window below the plane
    assert _verdict(rows, _observe(DOWN, SECTION * 25.0, -25.0, 0.0))["cut_extent_along_n"] is True


def test_a_cut_across_two_plates_at_different_heights_spans_both() -> None:
    """Two plates 10 mm thick with a gap between them: 20 mm of material, walls that run 40 mm. The reach of the part
    bounds the walls, the material removed is the lower bound, and the removed volume is exact."""
    stub = ColumnStub([(0.0, 10.0), (30.0, 40.0)])
    before = _snapshot(0.0, 60.0)
    rows = _cut_rows(before, stub)
    by = _by_name(rows)
    assert by["volume_delta"].value == pytest.approx(-SECTION * 20.0)
    assert by["cut_extent_along_n"].value == pytest.approx(60.0)  # not a solid block: only the bounds are promised
    assert "far side of the body" in by["cut_extent_along_n"].note
    ok = _verdict(rows, _observe(before, SECTION * 20.0, 0.0, 40.0))
    assert ok["volume_delta"] is True and ok["cut_extent_along_n"] is True
    only_first = _verdict(rows, _observe(before, SECTION * 10.0, 0.0, 10.0))
    assert only_first["volume_delta"] is False  # the second plate was not cut


def test_a_two_directions_cut_through_the_flange_is_judged_by_its_material() -> None:
    stub = ColumnStub([(-12.5, 12.5)])
    rows = _cut_rows(MIDDLE, stub, both=True)
    by = _by_name(rows)
    assert by["volume_delta"].value == pytest.approx(-SECTION * 25.0)
    verdicts = _verdict(rows, _observe(MIDDLE, SECTION * 25.0, -12.5, 12.5))
    assert verdicts["volume_delta"] is True and verdicts["cut_extent_along_n"] is True


def test_a_cut_across_a_part_as_tall_as_its_material_is_still_exact() -> None:
    """A plain 8 mm plate: the material under the profile is the whole height, the old exact expectation."""
    plate = _snapshot(0.0, 8.0)
    rows = _cut_rows(plate, ColumnStub([(0.0, 8.0)]))
    by = _by_name(rows)
    assert by["cut_extent_along_n"].value == pytest.approx(8.0) and by["cut_extent_along_n"].kind is CheckKind.asserted
    assert "far side" not in by["cut_extent_along_n"].note


# --- a hole ------------------------------------------------------------------------------------------------------


def test_a_hole_up_through_a_flange_with_a_hub_passes_when_the_adapter_cannot_measure() -> None:
    rows = _hole_rows(UP, StubAdapter())
    by = _by_name(rows)
    assert by["cut_extent_along_n"].kind is CheckKind.asserted
    assert by["cut_extent_along_n"].value == pytest.approx(105.0)
    assert by["cut_area"].value == pytest.approx(HOLE_AREA)
    verdict = _results(rows, _observe(UP, HOLE_AREA * 25.0, 0.0, 25.0))["cut_extent_along_n"]
    assert verdict.ok is True and verdict.observed == pytest.approx(25.0)
    assert _results(rows, _observe(UP, HOLE_AREA * 25.0, 0.0, 10.0))["cut_extent_along_n"].ok is False


def test_a_measured_hole_through_one_flange_expects_the_flange_thickness_and_its_faces() -> None:
    stub = ColumnStub([(0.0, 25.0)])
    by = _by_name(_hole_rows(UP, stub))
    assert by["volume_delta"].value == pytest.approx(-HOLE_AREA * 25.0)
    assert by["cut_extent_along_n"].value == pytest.approx(25.0)
    assert by["cut_extent_along_n"].kind is CheckKind.asserted
    # a hole in solid material for its whole length: one wall, the structure is asserted
    assert by["new_faces_count"].kind is CheckKind.asserted and by["new_faces_count"].value == 1
    assert by["hole_radius"].kind is CheckKind.asserted


def test_a_measured_hole_that_leaves_material_where_through_was_asked_fails() -> None:
    rows = _hole_rows(UP, ColumnStub([(0.0, 25.0)]))
    verdicts = _verdict(rows, _observe(UP, HOLE_AREA * 10.0, 0.0, 10.0))
    assert verdicts["volume_delta"] is False


def test_a_measured_hole_across_two_plates_keeps_the_bounds_and_reports_its_faces() -> None:
    stub = ColumnStub([(0.0, 10.0), (30.0, 40.0)])
    before = _snapshot(0.0, 60.0)
    by = _by_name(_hole_rows(before, stub))
    assert by["volume_delta"].value == pytest.approx(-HOLE_AREA * 20.0)
    assert by["cut_extent_along_n"].value == pytest.approx(60.0)
    assert by["cut_extent_along_n"].kind is CheckKind.asserted
    assert by["new_faces_count"].kind is CheckKind.reported  # two walls, not one
