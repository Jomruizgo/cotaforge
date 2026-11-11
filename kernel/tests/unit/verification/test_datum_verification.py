"""Level 2 for the datum ops: expected.py rows, observe.py capture and level2.py pose checks."""

import pytest

from cotaforge.contract import (
    Axis,
    BoundingBox,
    CutRequest,
    DatumAxisHandle,
    DatumAxisRequest,
    DatumAxisResult,
    DatumPlaneHandle,
    DatumPlaneRequest,
    DatumPlaneResult,
    DatumPlaneTarget,
    ExtrudeRequest,
    MassProperties,
    OperationResult,
    PlaneName,
    SketchFrame,
    SketchHandle,
    SketchRequest,
    frame_for_plane,
)
from cotaforge.ir.ops_features import (
    CutOperation,
    ExtrudeOperation,
    RefAxisOperation,
    RefPlaneOperation,
    SketchOperation,
)
from cotaforge.ir.refs import PlaneRef
from cotaforge.verification import datum
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import StateSnapshot, gather_observation
from cotaforge.verification.results import CheckKind, Expectation
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .test_level2 import _obs

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
FRAME = datum.plane_offset(frame_for_plane(PlaneName.XY), 10.0)
RECT = {"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}


def _plane_op() -> RefPlaneOperation:
    return RefPlaneOperation(
        id="dp", intent="i", define={"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 10.0}
    )


def _axis_op() -> RefAxisOperation:
    return RefAxisOperation(id="da", intent="i", define={"how": "two_points", "points": ((0, 0, 0), (0, 0, 5))})


def _by_name(expectations: tuple) -> dict:
    return {e.name: e for e in expectations}


# --- expected.py ----------------------------------------------------------------------------


def test_ref_plane_expects_no_volume_change_and_the_requested_pose() -> None:
    computer = ExpectedComputer(Thresholds())
    request = DatumPlaneRequest(operation_id="dp", how="offset", frame=FRAME)
    out = _by_name(computer.compute(_plane_op(), request, EMPTY, StubAdapter()))
    assert out["volume_delta"].kind is CheckKind.asserted
    assert out["volume_delta"].value == 0.0
    assert out["datum_origin"].value == (0.0, 0.0, 10.0)
    assert out["datum_u"].value == (1.0, 0.0, 0.0)
    assert out["datum_normal"].value == (0.0, 0.0, 1.0)
    assert all(e.kind is CheckKind.asserted for e in out.values())


def test_ref_axis_expects_no_volume_change_and_the_requested_axis() -> None:
    computer = ExpectedComputer(Thresholds())
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    out = _by_name(
        computer.compute(
            _axis_op(), DatumAxisRequest(operation_id="da", how="two_points", axis=axis), EMPTY, StubAdapter()
        )
    )
    assert out["volume_delta"].value == 0.0
    assert out["datum_axis_position"].value == (0.0, 0.0, 0.0)
    assert out["datum_axis_direction"].value == (0.0, 0.0, 1.0)


def test_sketch_on_a_datum_uses_its_frame_and_is_not_on_a_face() -> None:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    target = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=FRAME)
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(from_op="dp"), entities=[RECT])
    out = computer.compute(sketch, SketchRequest(operation_id="s", on=target, entities=sketch.entities), EMPTY, stub)
    assert [(e.name, e.kind) for e in out] == [("volume_delta", CheckKind.reported)]
    extrude = ExtrudeOperation(id="e", intent="i", sketch="s", depth_mm=8.0)
    by = _by_name(
        computer.compute(
            extrude, ExtrudeRequest(operation_id="e", sketch=SketchHandle(id="sk:1"), depth_mm=8.0), EMPTY, stub
        )
    )
    assert by["volume_delta"].value == pytest.approx(48000.0)
    box = by["bbox_after"].value
    assert box.min == pytest.approx((0.0, 0.0, 10.0))
    assert box.max == pytest.approx((100.0, 60.0, 18.0))
    assert by["new_faces_count"].value == 6  # a datum sketch is never "on a face": both caps are new


BODY = StateSnapshot(
    mass=MassProperties(volume_mm3=1000.0, centroid=(0.0, 0.0, 0.0), surface_mm2=0.0, inertia=None),
    bbox=BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0)),
)


def _through_all_cut(frame: SketchFrame) -> dict:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    target = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=frame)
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(from_op="dp"), entities=[RECT])
    computer.compute(sketch, SketchRequest(operation_id="s", on=target, entities=sketch.entities), BODY, stub)
    op = CutOperation(id="c", intent="i", sketch="s", end_condition="through_all")
    request = CutRequest(operation_id="c", sketch=SketchHandle(id="sk:1"), end_condition="through_all")
    return _by_name(computer.compute(op, request, BODY, stub))


def test_through_all_cut_extent_is_asserted_along_a_world_axis() -> None:
    by = _through_all_cut(datum.plane_offset(frame_for_plane(PlaneName.XY), 0.0))
    assert by["cut_extent_along_n"].kind is CheckKind.asserted


def test_through_all_cut_extent_is_only_reported_along_an_oblique_normal() -> None:
    """The extent is a bounding-box estimate on both sides of the comparison (expected: body box, observed: box
    of the created faces); along an oblique direction both overshoot by different amounts, so asserting it would
    reject a correct cut (real FreeCAD: inclined tube, expected 167.3 vs observed 126.0 for a true 100 mm)."""
    tilted = datum.plane_angle(frame_for_plane(PlaneName.XY), Axis(origin=(0, 0, 0), direction=(1, 0, 0)), 30.0)
    by = _through_all_cut(tilted)
    assert by["cut_extent_along_n"].kind is CheckKind.reported
    assert "oblique" in by["cut_extent_along_n"].note


# --- observe.py -----------------------------------------------------------------------------


def _snapshot() -> StateSnapshot:
    return StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)


def test_observation_captures_the_realised_datum_from_the_raw_result() -> None:
    stub = StubAdapter()
    plane_raw = DatumPlaneResult(datum=DatumPlaneHandle(id="datum_plane:1"), frame=FRAME)
    result = OperationResult(operation_id="dp", feature=None, rebuild_ok=True)
    obs = gather_observation(stub, _plane_op(), result, _snapshot(), None, raw_result=plane_raw)
    assert obs.datum_frame == FRAME
    assert obs.datum_axis is None
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    axis_raw = DatumAxisResult(datum=DatumAxisHandle(id="datum_axis:1"), axis=axis)
    obs = gather_observation(stub, _axis_op(), result, _snapshot(), None, raw_result=axis_raw)
    assert obs.datum_axis == axis
    assert obs.datum_frame is None


# --- level2.py ------------------------------------------------------------------------------


def _plane_expectations(frame: SketchFrame = FRAME) -> tuple:
    request = DatumPlaneRequest(operation_id="dp", how="offset", frame=frame)
    return ExpectedComputer(Thresholds()).compute(_plane_op(), request, EMPTY, StubAdapter())


def _observation_with(frame: SketchFrame | None = None, axis: Axis | None = None):  # noqa: ANN202
    obs = _obs(mass_after=EMPTY_MASS, bbox_after=EMPTY_BBOX, mass_before=EMPTY_MASS)
    return obs.model_copy(update={"datum_frame": frame, "datum_axis": axis})


def test_level2_passes_when_the_realised_frame_matches() -> None:
    results = {r.name: r for r in Level2Check().check(_plane_expectations(), _observation_with(frame=FRAME))}
    assert results["volume_delta"].ok is True
    assert results["datum_origin"].ok is True
    assert results["datum_u"].ok is True
    assert results["datum_normal"].ok is True


def test_level2_catches_a_shifted_origin_and_a_tilted_normal() -> None:
    shifted = FRAME.model_copy(update={"origin": (0.0, 0.0, 12.0)})
    results = {r.name: r for r in Level2Check().check(_plane_expectations(), _observation_with(frame=shifted))}
    assert results["datum_origin"].ok is False
    assert results["datum_normal"].ok is True
    tilted = FRAME.model_copy(update={"n": (0.0, 0.5, 0.8660254037844386)})
    results = {r.name: r for r in Level2Check().check(_plane_expectations(), _observation_with(frame=tilted))}
    assert results["datum_normal"].ok is False
    flipped = FRAME.model_copy(update={"n": (0.0, 0.0, -1.0)})
    results = {r.name: r for r in Level2Check().check(_plane_expectations(), _observation_with(frame=flipped))}
    assert results["datum_normal"].ok is False


def test_level2_reports_a_missing_frame_as_a_failed_check() -> None:
    results = {r.name: r for r in Level2Check().check(_plane_expectations(), _observation_with())}
    assert results["datum_origin"].ok is False
    assert "not observed" in results["datum_origin"].message


def test_level2_axis_checks() -> None:
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    request = DatumAxisRequest(operation_id="da", how="two_points", axis=axis)
    expectations = ExpectedComputer(Thresholds()).compute(_axis_op(), request, EMPTY, StubAdapter())
    ok = {r.name: r for r in Level2Check().check(expectations, _observation_with(axis=axis))}
    assert ok["datum_axis_position"].ok is True and ok["datum_axis_direction"].ok is True
    # the same line described from another point on it still matches
    same_line = Axis(origin=(0.0, 0.0, 7.0), direction=(0.0, 0.0, 1.0))
    ok = {r.name: r for r in Level2Check().check(expectations, _observation_with(axis=same_line))}
    assert ok["datum_axis_position"].ok is True
    off = Axis(origin=(1.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    bad = {r.name: r for r in Level2Check().check(expectations, _observation_with(axis=off))}
    assert bad["datum_axis_position"].ok is False
    reversed_axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, -1.0))
    bad = {r.name: r for r in Level2Check().check(expectations, _observation_with(axis=reversed_axis))}
    assert bad["datum_axis_direction"].ok is False


# --- MAJOR A: a merged extrude from a datum that may touch a body face ----------------------------


def _extrude_from_datum(offset: float, *, merge: bool = True) -> dict:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    frame = datum.plane_offset(frame_for_plane(PlaneName.XY), offset)
    target = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=frame)
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(from_op="dp"), entities=[RECT])
    computer.compute(sketch, SketchRequest(operation_id="s", on=target, entities=sketch.entities), BODY, stub)
    op = ExtrudeOperation(id="e", intent="i", sketch="s", depth_mm=5.0, merge=merge)
    request = ExtrudeRequest(operation_id="e", sketch=SketchHandle(id="sk:1"), depth_mm=5.0, merge=merge)
    return _by_name(computer.compute(op, request, BODY, stub))


def test_merged_extrude_from_a_datum_inside_the_body_extent_does_not_assert_the_face_count() -> None:
    """BODY spans z in [0, 8]: a datum at z = 8 (or 4) may be coplanar with a face, and then FreeCAD merges the
    near cap (5 new faces, not 6)."""
    for offset in (8.0, 4.0, 0.0):
        by = _extrude_from_datum(offset)
        assert by["new_faces_count"].kind is CheckKind.reported, offset
        assert "coplanar" in by["new_faces_count"].note
        assert by["volume_delta"].kind is CheckKind.asserted
        assert by["bbox_after"].kind is CheckKind.asserted


def test_merged_extrude_from_a_datum_clear_of_the_body_or_a_detached_body_keeps_the_face_count_asserted() -> None:
    assert _extrude_from_datum(20.0)["new_faces_count"].kind is CheckKind.asserted
    assert _extrude_from_datum(-20.0)["new_faces_count"].kind is CheckKind.asserted  # sweeps z in [-20, -15]
    assert _extrude_from_datum(8.0, merge=False)["new_faces_count"].kind is CheckKind.asserted
    assert _extrude_from_datum(8.0, merge=False)["new_faces_count"].value == 6


# --- MAJOR B: oblique through-all cuts ------------------------------------------------------------


def test_oblique_through_all_cut_reports_the_bbox_and_asserts_a_bounded_volume_removal() -> None:
    tilted = datum.plane_angle(frame_for_plane(PlaneName.XY), Axis(origin=(0, 0, 0), direction=(1, 0, 0)), 30.0)
    by = _through_all_cut(tilted)
    assert by["bbox_within"].kind is CheckKind.reported
    bound = by["cut_volume_bound"]
    assert bound.kind is CheckKind.asserted
    extent = BODY.bbox.extent_along(tilted.n)
    assert bound.value == pytest.approx(6000.0 * extent)  # profile area x the body extent along n


def test_axis_aligned_through_all_cut_still_asserts_bbox_within() -> None:
    by = _through_all_cut(datum.plane_offset(frame_for_plane(PlaneName.XY), 0.0))
    assert by["bbox_within"].kind is CheckKind.asserted
    assert "cut_volume_bound" not in by


def test_cut_volume_bound_needs_material_removed_but_not_more_than_the_bound() -> None:
    bound = Expectation(name="cut_volume_bound", kind=CheckKind.asserted, unit="mm3", value=100.0)

    def run(removed: float) -> bool | None:
        obs = _obs(
            mass_after=MassProperties(volume_mm3=1000.0 - removed, centroid=(0, 0, 0), surface_mm2=0.0, inertia=None),
            mass_before=BODY.mass,
        )
        return Level2Check(Thresholds()).check((bound,), obs)[0].ok

    assert run(40.0) is True
    assert run(100.0) is True
    assert run(0.0) is False  # nothing was cut
    assert run(-5.0) is False  # material was added
    assert run(140.0) is False  # more than profile area x extent can explain


# --- review: the swept interval, not only the datum offset ---------------------------------------------------


def _extrude_from(offset: float, depth: float, direction: str = "positive") -> dict:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    frame = datum.plane_offset(frame_for_plane(PlaneName.XY), offset)
    target = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=frame)
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(from_op="dp"), entities=[RECT])
    computer.compute(sketch, SketchRequest(operation_id="s", on=target, entities=sketch.entities), BODY, stub)
    op = ExtrudeOperation(id="e", intent="i", sketch="s", depth_mm=depth, direction=direction)
    request = ExtrudeRequest(operation_id="e", sketch=SketchHandle(id="sk:1"), depth_mm=depth, direction=direction)
    return _by_name(computer.compute(op, request, BODY, stub))


def test_a_sweep_that_ends_on_or_inside_the_body_may_merge_its_far_cap() -> None:
    """BODY spans z in [0, 8]. A datum 10 mm below it whose +10 sweep ends on the bottom face merges there."""
    assert _extrude_from(-10.0, 10.0)["new_faces_count"].kind is CheckKind.reported
    assert _extrude_from(-10.0, 14.0)["new_faces_count"].kind is CheckKind.reported  # ends inside
    assert _extrude_from(20.0, 12.0, "negative")["new_faces_count"].kind is CheckKind.reported  # ends on the top
    assert "coplanar" in _extrude_from(-10.0, 10.0)["new_faces_count"].note


def test_a_sweep_clear_of_the_body_keeps_the_face_count_asserted() -> None:
    assert _extrude_from(-10.0, 5.0)["new_faces_count"].kind is CheckKind.asserted
    assert _extrude_from(20.0, 5.0)["new_faces_count"].kind is CheckKind.asserted
    assert _extrude_from(20.0, 5.0, "negative")["new_faces_count"].kind is CheckKind.asserted


def test_cut_volume_bound_lower_slack_is_the_absolute_tolerance_only() -> None:
    bound = Expectation(name="cut_volume_bound", kind=CheckKind.asserted, unit="mm3", value=1000.0)
    obs = _obs(
        mass_after=MassProperties(volume_mm3=1000.0 - 0.005, centroid=(0, 0, 0), surface_mm2=0.0, inertia=None),
        mass_before=BODY.mass,
    )
    # 0.005 mm3 removed: above abs_mm3 (1e-3) but below the old abs + rel x bound (0.101)
    assert Level2Check(Thresholds()).check((bound,), obs)[0].ok is True
    nothing = _obs(
        mass_after=MassProperties(volume_mm3=1000.0 - 0.0005, centroid=(0, 0, 0), surface_mm2=0.0, inertia=None),
        mass_before=BODY.mass,
    )
    assert Level2Check(Thresholds()).check((bound,), nothing)[0].ok is False
