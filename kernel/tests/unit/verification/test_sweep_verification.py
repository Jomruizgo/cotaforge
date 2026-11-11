"""Level 2 expectations of ``sweep`` (verification/expected.py ``_op_sweep``) and the ``bbox_contains`` evaluator.

Every number is derived by hand: circle D10 (A = 25 pi = 78.5398), an L path (0,0,0) -> (0,0,100) -> (100,0,100)
(mitre length 200; R20 fillet length 200 - 40 + 10 pi = 191.4159)."""

import math

import pytest

from cotaforge.contract import (
    BoundingBox,
    CircleEntity,
    LineEntity,
    MassProperties,
    PlaneName,
    RectEntity,
    SketchHandle,
    SketchRequest,
    SweepRequest,
)
from cotaforge.ir.ops_features import PolylinePath, SketchOperation, SweepOperation
from cotaforge.ir.refs import PlaneRef
from cotaforge.verification import formulas as f
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.level2 import CHECK_NAMES, Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot
from cotaforge.verification.results import CheckKind, Expectation
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
AREA = 25.0 * math.pi
STRAIGHT = ((0.0, 0.0, 0.0), (0.0, 0.0, 100.0))
L_PATH = ((0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0))


def _computer(*entities: object) -> tuple[ExpectedComputer, StubAdapter]:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    entities = entities or (CircleEntity(name="c", center=(0.0, 0.0), diameter=10.0),)
    sketch = SketchOperation(id="s", intent="i", on=PlaneRef(name="XY"), entities=list(entities))
    computer.compute(sketch, SketchRequest(operation_id="s", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub)
    return computer, stub


def _expectations(
    path: tuple, radius: float | None = None, merge: bool = True, before: StateSnapshot = EMPTY, *entities: object
) -> dict[str, Expectation]:
    computer, stub = _computer(*entities)
    op = SweepOperation(
        id="w", intent="i", sketch="s", path=PolylinePath(points=path, corner_radius_mm=radius), merge=merge
    )
    request = SweepRequest(
        operation_id="w", sketch=SketchHandle(id="sk:1"), path=path, corner_radius_mm=radius, merge=merge
    )
    return {e.name: e for e in computer.compute(op, request, before, stub)}


def _kinds(expected: dict[str, Expectation]) -> dict[str, CheckKind]:
    return {name: e.kind for name, e in expected.items()}


# --- straight tube -----------------------------------------------------------------------------------------------


def test_straight_tube_asserts_volume_box_and_face_count() -> None:
    expected = _expectations(STRAIGHT)
    assert expected["volume_delta"].value == pytest.approx(AREA * 100.0)
    assert expected["volume_delta"].value == pytest.approx(7853.98, abs=0.01)
    assert expected["new_faces_count"].value == 3
    box = expected["bbox_after"].value
    assert isinstance(box, BoundingBox)
    assert box.min == pytest.approx((-5.0, -5.0, 0.0))
    assert box.max == pytest.approx((5.0, 5.0, 100.0))
    assert _kinds(expected) == {
        "volume_delta": CheckKind.asserted,
        "new_faces_count": CheckKind.asserted,
        "bbox_after": CheckKind.asserted,
    }  # a single segment has an exact box: no lower bound needed


def test_straight_tube_box_follows_the_sweep_direction_when_the_frame_normal_opposes_it() -> None:
    # XY frame normal is +Z; a path going -Z starts at the origin and ends at z = -100
    expected = _expectations(((0.0, 0.0, 0.0), (0.0, 0.0, -100.0)))
    box = expected["bbox_after"].value
    assert isinstance(box, BoundingBox)
    assert box.min == pytest.approx((-5.0, -5.0, -100.0))
    assert box.max == pytest.approx((5.0, 5.0, 0.0))


# --- L mitre, circle -----------------------------------------------------------------------------------------------


def test_mitred_circle_volume_faces_and_boxes() -> None:
    expected = _expectations(L_PATH)
    assert expected["volume_delta"].value == pytest.approx(AREA * 200.0)
    assert expected["new_faces_count"].value == 4  # 2 caps + 2 lateral
    assert _kinds(expected)["new_faces_count"] is CheckKind.asserted
    exact = expected["bbox_after"]
    assert exact.kind is CheckKind.reported  # the kernel's box is loose on curved swept faces
    assert exact.value.min == pytest.approx((-5.0, -5.0, 0.0))  # type: ignore[union-attr]
    assert exact.value.max == pytest.approx((100.0, 5.0, 105.0))  # type: ignore[union-attr]
    lower = expected["bbox_contains"]
    assert lower.kind is CheckKind.asserted
    assert isinstance(lower.value, BoundingBox)
    assert f.bbox_contains(exact.value, lower.value)  # type: ignore[arg-type]


def test_filleted_circle_has_an_arc_face_and_no_exact_box() -> None:
    expected = _expectations(L_PATH, radius=20.0)
    assert expected["volume_delta"].value == pytest.approx(AREA * (200.0 - 40.0 + 10.0 * math.pi))
    assert expected["volume_delta"].value == pytest.approx(15033.77, abs=0.05)
    assert expected["new_faces_count"].value == 5  # 2 caps + 2 lateral + 1 torus
    assert "bbox_after" not in expected
    assert _kinds(expected)["bbox_contains"] is CheckKind.asserted


# --- polygon sections and merges -----------------------------------------------------------------------------------


def test_a_bent_polygon_section_has_no_face_count_expectation() -> None:
    # rectangular faces merge across coplanar bends (measured: an L mitre gives 8 faces, not 10) and split on
    # others, so no count is right for every path: the expectation is left out instead of being reported wrong
    rect = RectEntity(name="r", center=(0.0, 0.0), width=10.0, height=4.0)
    expected = _expectations(L_PATH, None, True, EMPTY, rect)
    assert expected["volume_delta"].value == pytest.approx(40.0 * 200.0)
    assert "new_faces_count" not in expected
    assert expected["bbox_after"].kind is CheckKind.reported
    assert expected["bbox_after"].value.max == pytest.approx((100.0, 2.0, 105.0))  # type: ignore[union-attr]


def test_a_single_segment_polygon_section_asserts_two_caps_plus_one_face_per_side() -> None:
    rect = RectEntity(name="r", center=(0.0, 0.0), width=10.0, height=4.0)
    faces = _expectations(STRAIGHT, None, True, EMPTY, rect)["new_faces_count"]
    assert (faces.value, faces.kind) == (6, CheckKind.asserted)
    triangle = [
        LineEntity(name="a", start=(-2, -2), end=(4, -2)),
        LineEntity(name="b", start=(4, -2), end=(-2, 4)),
        LineEntity(name="c", start=(-2, 4), end=(-2, -2)),
    ]
    faces = _expectations(STRAIGHT, None, True, EMPTY, *triangle)["new_faces_count"]
    assert (faces.value, faces.kind) == (5, CheckKind.asserted)


def test_the_volume_expectation_warns_about_overlap_with_existing_material() -> None:
    note = _expectations(STRAIGHT)["volume_delta"].note
    assert "must not run through existing material" in note and "start the path on the face" in note


def test_a_failed_volume_check_carries_the_sweep_hint() -> None:
    expectation = _expectations(STRAIGHT)["volume_delta"]
    observation = _observation(BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0))).model_copy(
        update={"mass_before": MassProperties(volume_mm3=0.0, centroid=(0.0, 0.0, 0.0), surface_mm2=0.0)}
    )
    (result,) = Level2Check().check((expectation,), observation)
    assert result.ok is False
    assert "must not run through existing material" in result.message


def test_a_merge_into_an_existing_body_reports_the_face_count_and_unions_the_box() -> None:
    before = StateSnapshot(
        mass=MassProperties(volume_mm3=1000.0, centroid=(0.0, 0.0, 0.0), surface_mm2=1.0),
        bbox=BoundingBox(min=(-20.0, -20.0, -10.0), max=(20.0, 20.0, 0.0)),
    )
    expected = _expectations(STRAIGHT, None, True, before)
    assert expected["new_faces_count"].kind is CheckKind.reported
    box = expected["bbox_after"].value
    assert isinstance(box, BoundingBox)
    assert box.min == pytest.approx((-20.0, -20.0, -10.0))
    assert box.max == pytest.approx((20.0, 20.0, 100.0))
    assert expected["volume_delta"].kind is CheckKind.asserted


def test_a_separate_body_keeps_the_asserted_face_count() -> None:
    before = StateSnapshot(
        mass=MassProperties(volume_mm3=1000.0, centroid=(0.0, 0.0, 0.0), surface_mm2=1.0),
        bbox=BoundingBox(min=(-20.0, -20.0, -10.0), max=(20.0, 20.0, -1.0)),
    )
    assert _expectations(STRAIGHT, None, False, before)["new_faces_count"].kind is CheckKind.asserted


def test_an_unknown_sketch_model_reports_instead_of_asserting() -> None:
    computer = ExpectedComputer(Thresholds())
    op = SweepOperation(id="w", intent="i", sketch="ghost", path=PolylinePath(points=STRAIGHT))
    request = SweepRequest(operation_id="w", sketch=SketchHandle(id="sk:1"), path=STRAIGHT)
    (only,) = computer.compute(op, request, EMPTY, StubAdapter())
    assert only.name == "volume_delta"
    assert only.kind is CheckKind.reported


def test_the_swept_body_is_registered_under_the_op_id() -> None:
    computer, stub = _computer()
    op = SweepOperation(id="w", intent="i", sketch="s", path=PolylinePath(points=STRAIGHT))
    request = SweepRequest(operation_id="w", sketch=SketchHandle(id="sk:1"), path=STRAIGHT)
    computer.compute(op, request, EMPTY, stub)
    assert computer._body_of_op["w"] == "w"
    assert computer._shape_by_body["w"] is None


# --- level 2: bbox_contains ------------------------------------------------------------------------------------------


def _observation(box: BoundingBox) -> Observation:
    return Observation(
        mass_after=MassProperties(volume_mm3=1.0, centroid=(0.0, 0.0, 0.0), surface_mm2=1.0),
        bbox_after=box,
        created_faces=(),
        modified_faces=(),
        created_edges=(),
        instance_count=None,
        component_bbox=None,
        component_transform=None,
        interference=None,
    )


LOWER = BoundingBox(min=(-5.0, -5.0, 0.0), max=(100.0, 5.0, 105.0))


def _check(observed: BoundingBox) -> tuple[bool | None, str]:
    expectation = Expectation(name="bbox_contains", kind=CheckKind.asserted, unit="bbox", value=LOWER)
    (result,) = Level2Check().check((expectation,), _observation(observed))
    return result.ok, result.message


def test_bbox_contains_is_a_known_check_name() -> None:
    assert "bbox_contains" in CHECK_NAMES


def test_bbox_contains_accepts_a_loose_observed_box() -> None:
    # the kernel's box is looser than the true one on curved swept faces but always contains it
    assert _check(BoundingBox(min=(-7.06, -5.0, -1.0), max=(107.06, 5.0, 107.06)))[0] is True


def test_bbox_contains_accepts_the_exact_box_and_the_position_tolerance() -> None:
    assert _check(LOWER)[0] is True
    assert _check(BoundingBox(min=(-5.005, -5.0, 0.0), max=(100.0, 5.0, 105.0)))[0] is True


def test_bbox_contains_rejects_a_box_that_misses_part_of_the_lower_bound() -> None:
    ok, message = _check(BoundingBox(min=(-5.0, -5.0, 0.0), max=(100.0, 5.0, 60.0)))  # the sweep never went up
    assert ok is False
    assert message
