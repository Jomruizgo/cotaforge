"""Level 2 for ``mirror`` (a pattern of two: the seeds and their reflection) and ``mirror_body`` (a copy reflected
across a plane), with the edit-tracking contract around both.

A reflection is exact only across an axis-aligned plane: there the reflected box of a seed (or a body) is the box of the
reflection, so the box rows are asserted; across an oblique plane the enclosing box is not the shape's box, so those
rows are REPORTED and only what a reflection preserves (volume, surface, the reflected centroid) is asserted."""

import math

import pytest

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BoundingBox,
    BoxRequest,
    CylinderRequest,
    FeatureHandle,
    MassProperties,
    MirrorBodyRequest,
    MirrorRequest,
    OperationResult,
    SketchFrame,
)
from cotaforge.ir.ops_features import MirrorBodyOperation, MirrorOperation
from cotaforge.ir.ops_primitives import BoxOperation, CylinderOperation
from cotaforge.ir.refs import BodyRef, PlaneRef
from cotaforge.verification import formulas as f
from cotaforge.verification.expected import _NON_EDITING_OPS, ExpectedComputer
from cotaforge.verification.formulas import BoxShape, CylinderShape
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot, gather_observation
from cotaforge.verification.results import CheckKind
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import PLATE_BBOX, PLATE_MASS, PLATE_RECT, computer_with_pad, cylindrical_face, mass, planar_face
from .test_level2 import _obs

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
FEATURE = FeatureHandle(id="feature:2")
S2 = 1 / math.sqrt(2.0)


def _frame(origin: tuple[float, float, float], normal: tuple[float, float, float]) -> SketchFrame:
    # u and v are irrelevant to a reflection; any two vectors will do
    return SketchFrame(origin=origin, u=(0.0, 1.0, 0.0), v=(0.0, 0.0, 1.0), n=normal)


def _by_name(expectations: tuple) -> dict:
    return {e.name: e for e in expectations}


# --- formulas ---------------------------------------------------------------------------------


def test_reflect_point_and_direction_across_a_plane() -> None:
    assert f.reflect_point((30.0, 5.0, 2.0), (20.0, 0.0, 0.0), (1.0, 0.0, 0.0)) == pytest.approx((10.0, 5.0, 2.0))
    assert f.reflect_point((3.0, 4.0, 5.0), (0.0, 0.0, 0.0), (0.0, 0.0, 2.0)) == pytest.approx((3.0, 4.0, -5.0))
    assert f.reflect_point((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (S2, S2, 0.0)) == pytest.approx((0.0, -1.0, 0.0))
    assert f.reflect_direction((1.0, 2.0, 3.0), (1.0, 0.0, 0.0)) == pytest.approx((-1.0, 2.0, 3.0))


def test_a_point_on_the_plane_does_not_move_and_reflecting_twice_is_the_identity() -> None:
    origin, normal = (20.0, 7.0, -3.0), (S2, 0.0, S2)
    on_plane = f.add(origin, (S2, 5.0, -S2))
    assert f.reflect_point(on_plane, origin, normal) == pytest.approx(on_plane)
    p = (11.0, -4.0, 9.0)
    assert f.reflect_point(f.reflect_point(p, origin, normal), origin, normal) == pytest.approx(p)


def test_only_a_world_axis_normal_is_axis_aligned() -> None:
    assert f.plane_axis_aligned((0.0, 0.0, -1.0))
    assert f.plane_axis_aligned((5.0, 0.0, 0.0))
    assert not f.plane_axis_aligned((S2, S2, 0.0))
    assert not f.plane_axis_aligned((1.0, 1e-4, 0.0))


def test_bbox_reflect_flips_the_box_across_an_axis_aligned_plane() -> None:
    box = BoundingBox(min=(6.0, 6.0, 8.0), max=(14.0, 14.0, 13.0))
    reflected = f.bbox_reflect(box, (20.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    assert reflected == BoundingBox(min=(26.0, 6.0, 8.0), max=(34.0, 14.0, 13.0))
    assert f.bbox_reflect(box, (0.0, 0.0, 10.0), (0.0, 0.0, 1.0)) == BoundingBox(
        min=(6.0, 6.0, 7.0), max=(14.0, 14.0, 12.0)
    )


def test_bbox_reflect_across_an_oblique_plane_is_the_enclosing_box_of_the_reflected_corners() -> None:
    box = BoundingBox(min=(0.0, 0.0, 0.0), max=(2.0, 2.0, 2.0))
    enclosing = f.bbox_reflect(box, (0.0, 0.0, 0.0), (S2, S2, 0.0))
    assert enclosing.min == pytest.approx((-2.0, -2.0, 0.0))
    assert enclosing.max == pytest.approx((0.0, 0.0, 2.0))


def test_reflect_shape_keeps_a_box_only_across_an_axis_aligned_plane_and_a_cylinder_always() -> None:
    box = BoxShape(min=(0.0, 0.0, 0.0), max=(10.0, 20.0, 30.0))
    assert f.reflect_shape(box, (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)) == BoxShape(
        min=(-10.0, 0.0, 0.0), max=(0.0, 20.0, 30.0)
    )
    assert f.reflect_shape(box, (0.0, 0.0, 0.0), (S2, S2, 0.0)) is None
    cylinder = CylinderShape(axis=Axis(origin=(5.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)), radius=2.0, height=7.0)
    turned = f.reflect_shape(cylinder, (0.0, 0.0, 0.0), (S2, S2, 0.0))
    assert isinstance(turned, CylinderShape)
    assert turned.axis.origin == pytest.approx((0.0, -5.0, 0.0))
    assert turned.axis.direction == pytest.approx((0.0, 0.0, 1.0))
    assert (turned.radius, turned.height) == (2.0, 7.0)


# --- mirror ---------------------------------------------------------------------------------------


def _boss_stub(box: BoundingBox) -> StubAdapter:
    """One feature (``feature:2``) that created a boss: a cylindrical wall and a top, spanning ``box``."""
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:wall",
            Axis(origin=(box.min[0] + 4, box.min[1] + 4, box.min[2]), direction=(0, 0, 1)),
            4.0,
            (box.min[0] + 4, box.min[1] + 4, (box.min[2] + box.max[2]) / 2),
            box,
            created_by="feature:2",
        ),
        feature_id="feature:2",
    )
    stub.add_face(
        planar_face(
            "face:cap",
            (0.0, 0.0, 1.0),
            (box.min[0] + 4, box.min[1] + 4, box.max[2]),
            50.0,
            BoundingBox(min=(box.min[0], box.min[1], box.max[2]), max=(box.max[0], box.max[1], box.max[2])),
            created_by="feature:2",
        ),
        feature_id="feature:2",
    )
    return stub


def _mirror(plane: SketchFrame) -> tuple[MirrorOperation, MirrorRequest]:
    op = MirrorOperation(id="mi", intent="i", features=["boss"], plane=PlaneRef(name="YZ"))
    return op, MirrorRequest(operation_id="mi", features=(FEATURE,), plane=plane)


BOSS_VOLUME = math.pi * 16.0 * 5.0
BOSS_BOX = BoundingBox(min=(6.0, 6.0, 8.0), max=(14.0, 14.0, 13.0))
BEFORE = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=BOSS_VOLUME)
X20 = _frame((20.0, 0.0, 0.0), (1.0, 0.0, 0.0))


def test_a_boss_mirrored_across_an_axis_aligned_plane_asserts_two_instances_and_the_added_volume() -> None:
    by = _by_name(ExpectedComputer(Thresholds()).compute(*_mirror(X20), BEFORE, _boss_stub(BOSS_BOX)))
    assert set(by) == {"instance_count", "created_faces_count", "volume_delta", "bbox_contains"}
    assert all(e.kind is CheckKind.asserted for e in by.values())
    assert by["instance_count"].value == 2
    assert by["created_faces_count"].value == 2  # the seed's two faces, reflected
    assert by["volume_delta"].value == pytest.approx(BOSS_VOLUME)  # (instances - 1) * seed delta
    assert by["bbox_contains"].value == BoundingBox(min=(26.0, 6.0, 8.0), max=(34.0, 14.0, 13.0))


def test_a_mirrored_cut_asserts_twice_the_removed_volume_and_has_no_bbox_row() -> None:
    delta = -math.pi * 3.3**2 * 8.0
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=delta)
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:cyl",
            Axis(origin=(10, 10, 8), direction=(0, 0, -1)),
            3.3,
            (10, 10, 4),
            BoundingBox(min=(6.7, 6.7, 0), max=(13.3, 13.3, 8)),
            created_by="feature:2",
        ),
        feature_id="feature:2",
    )
    by = _by_name(computer_with_pad([PLATE_RECT]).compute(*_mirror(X20), before, stub))
    assert by["volume_delta"].kind is CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(delta)  # (2 - 1) * seed delta
    assert "bbox_contains" not in by
    # the reflection lands off the plate (x = 100 + ...): reported, never guessed
    far = _frame((98.0, 0.0, 0.0), (1.0, 0.0, 0.0))
    by = _by_name(computer_with_pad([PLATE_RECT]).compute(*_mirror(far), before, stub))
    assert by["volume_delta"].kind is CheckKind.reported
    assert "exit" in by["volume_delta"].note


def test_a_seed_that_straddles_the_plane_overlaps_its_reflection_so_the_volume_is_reported() -> None:
    straddling = BoundingBox(min=(16.0, 6.0, 8.0), max=(24.0, 14.0, 13.0))
    by = _by_name(ExpectedComputer(Thresholds()).compute(*_mirror(X20), BEFORE, _boss_stub(straddling)))
    assert by["volume_delta"].kind is CheckKind.reported
    assert "overlap" in by["volume_delta"].note
    assert by["created_faces_count"].kind is CheckKind.reported
    assert by["instance_count"].kind is CheckKind.asserted


def test_a_cut_that_straddles_the_plane_is_reported_too() -> None:
    """Two overlapping cuts remove less than twice one: the pattern rule alone would assert it."""
    straddling = BoundingBox(min=(16.0, 6.0, 0.0), max=(24.0, 14.0, 8.0))
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=-500.0)
    by = _by_name(ExpectedComputer(Thresholds()).compute(*_mirror(X20), before, _boss_stub(straddling)))
    assert by["volume_delta"].kind is CheckKind.reported


def test_a_seed_touching_the_plane_keeps_the_volume_asserted_but_not_the_face_count() -> None:
    """Two bosses that meet on the plane fuse: the volume still adds up, but their faces on the plane merge."""
    touching = BoundingBox(min=(12.0, 6.0, 8.0), max=(20.0, 14.0, 13.0))
    by = _by_name(ExpectedComputer(Thresholds()).compute(*_mirror(X20), BEFORE, _boss_stub(touching)))
    assert by["volume_delta"].kind is CheckKind.asserted
    assert by["created_faces_count"].kind is CheckKind.reported
    assert "plane" in by["created_faces_count"].note


def test_an_oblique_plane_reports_the_volume_and_the_boxes() -> None:
    by = _by_name(
        ExpectedComputer(Thresholds()).compute(
            *_mirror(_frame((20.0, 0.0, 0.0), (S2, S2, 0.0))), BEFORE, _boss_stub(BOSS_BOX)
        )
    )
    assert by["instance_count"].kind is CheckKind.asserted and by["instance_count"].value == 2
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["volume_delta"].value == pytest.approx(BOSS_VOLUME)
    assert "bbox_contains" not in by
    assert by["created_faces_count"].kind is CheckKind.reported
    assert "oblique" in by["created_faces_count"].note


def test_an_unknown_seed_delta_is_reported() -> None:
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX)
    by = _by_name(ExpectedComputer(Thresholds()).compute(*_mirror(X20), before, _boss_stub(BOSS_BOX)))
    assert by["volume_delta"].kind is CheckKind.reported
    assert "bbox_contains" not in by


def test_a_mirror_is_a_body_edit_and_a_mirror_body_is_not() -> None:
    assert "mirror" not in _NON_EDITING_OPS
    assert "mirror_body" in _NON_EDITING_OPS
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="b", intent="i", size=(100, 60, 8)),
        BoxRequest(operation_id="b", size=(100, 60, 8)),
        EMPTY,
        stub,
    )
    assert isinstance(computer._shape_of("b"), BoxShape)
    op, request = _mirror(X20)
    computer.compute(op, request, BEFORE, _boss_stub(BOSS_BOX))
    assert computer._shape_of("b") is None  # the reflection landed somewhere the box no longer describes


# --- mirror_body ---------------------------------------------------------------------------------

BLOCK_BOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 20.0))
BLOCK_MASS = MassProperties(volume_mm3=120000.0, centroid=(50.0, 30.0, 10.0), surface_mm2=18400.0)
DOC = StateSnapshot(mass=BLOCK_MASS, bbox=BLOCK_BOX)


def _block_stub() -> StubAdapter:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = BLOCK_MASS
    stub.bbox_by_body["body:1"] = BLOCK_BOX
    return stub


def _computer_with_block(stub: StubAdapter) -> ExpectedComputer:
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="block", intent="i", size=(100, 60, 20)),
        BoxRequest(operation_id="block", size=(100, 60, 20)),
        EMPTY,
        stub,
    )
    return computer


def _mirror_body(plane: SketchFrame) -> tuple[MirrorBodyOperation, MirrorBodyRequest]:
    op = MirrorBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"), plane=PlaneRef(name="YZ"))
    return op, MirrorBodyRequest(operation_id="twin", source=BodyHandle(id="body:1"), plane=plane)


X120 = _frame((120.0, 0.0, 0.0), (1.0, 0.0, 0.0))


def test_a_body_mirrored_across_an_axis_aligned_plane_expects_the_source_volume_and_the_reflected_pose() -> None:
    stub = _block_stub()
    by = _by_name(_computer_with_block(stub).compute(*_mirror_body(X120), DOC, stub))
    assert set(by) == {"volume_delta", "copy_centroid", "copy_surface", "source_centroid", "copy_bbox"}
    assert all(e.kind is CheckKind.asserted for e in by.values())
    assert by["volume_delta"].value == pytest.approx(120000.0)
    assert by["copy_centroid"].value == pytest.approx((190.0, 30.0, 10.0))
    assert by["copy_surface"].value == pytest.approx(18400.0)
    assert by["source_centroid"].value == pytest.approx((50.0, 30.0, 10.0))
    assert by["copy_bbox"].value == BoundingBox(min=(140.0, 0.0, 0.0), max=(240.0, 60.0, 20.0))


def test_a_body_mirrored_across_an_oblique_plane_asserts_the_centroid_and_reports_the_box() -> None:
    stub = _block_stub()
    plane = _frame((0.0, 0.0, 0.0), (S2, S2, 0.0))
    by = _by_name(_computer_with_block(stub).compute(*_mirror_body(plane), DOC, stub))
    assert by["copy_bbox"].kind is CheckKind.reported
    assert "oblique" in by["copy_bbox"].note
    assert by["copy_centroid"].kind is CheckKind.asserted
    assert by["copy_centroid"].value == pytest.approx((-30.0, -50.0, 10.0))  # (x, y) -> (-y, -x)
    assert by["volume_delta"].kind is CheckKind.asserted


def test_a_source_without_a_surface_area_gets_no_surface_expectation() -> None:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(120000.0, (50.0, 30.0, 10.0))
    stub.bbox_by_body["body:1"] = BLOCK_BOX
    by = _by_name(_computer_with_block(stub).compute(*_mirror_body(X120), DOC, stub))
    assert "copy_surface" not in by


def test_the_mirrored_body_is_tracked_with_a_reflected_shape_and_the_source_keeps_its_own() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    source_shape = computer._shape_of("block")
    computer.compute(*_mirror_body(X120), DOC, stub)
    assert computer._shape_of("block") == source_shape
    assert computer._shape_of("twin") == BoxShape(min=(140.0, 0.0, 0.0), max=(240.0, 60.0, 20.0))
    assert computer._body_of_op["twin"] == "twin"


def test_an_oblique_mirror_forgets_a_box_but_keeps_a_cylinder() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer.compute(*_mirror_body(_frame((0.0, 0.0, 0.0), (S2, S2, 0.0))), DOC, stub)
    assert computer._shape_of("twin") is None
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(math.pi * 25.0 * 40.0, (0.0, 0.0, 20.0))
    stub.bbox_by_body["body:1"] = BoundingBox(min=(-5, -5, 0), max=(5, 5, 40))
    computer = ExpectedComputer(Thresholds())
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    computer.compute(
        CylinderOperation(id="block", intent="i", diameter_mm=10, height_mm=40),
        CylinderRequest(operation_id="block", diameter_mm=10, height_mm=40, axis=axis),
        EMPTY,
        stub,
    )
    computer.compute(*_mirror_body(_frame((10.0, 0.0, 0.0), (S2, S2, 0.0))), DOC, stub)
    turned = computer._shape_of("twin")
    assert isinstance(turned, CylinderShape)
    assert turned.axis.origin == pytest.approx((10.0, 10.0, 0.0))  # the origin is 10 mm from the plane


def test_the_mirror_of_an_edited_body_carries_the_reflected_edit_regions() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer._edits["block"] = [BoundingBox(min=(10.0, 0.0, 0.0), max=(20.0, 10.0, 20.0))]
    computer.compute(*_mirror_body(X120), DOC, stub)
    assert computer._edits["twin"] == [BoundingBox(min=(220.0, 0.0, 0.0), max=(230.0, 10.0, 20.0))]


# --- observe.py and level2.py -------------------------------------------------------------------------


def test_observation_reads_the_mirrored_body_and_the_source_by_body() -> None:
    stub = _block_stub()
    stub.mass_by_body["body:2"] = BLOCK_MASS.model_copy(update={"centroid": (190.0, 30.0, 10.0)})
    stub.bbox_by_body["body:2"] = BoundingBox(min=(140.0, 0.0, 0.0), max=(240.0, 60.0, 20.0))
    op, request = _mirror_body(X120)
    result = OperationResult(
        operation_id="twin", feature=FEATURE, rebuild_ok=True, created_bodies=(BodyHandle(id="body:2"),)
    )
    obs = gather_observation(stub, op, result, DOC, request)
    assert obs.copy_mass is not None and obs.copy_mass.centroid == (190.0, 30.0, 10.0)
    assert obs.copy_bbox == BoundingBox(min=(140.0, 0.0, 0.0), max=(240.0, 60.0, 20.0))
    assert obs.source_mass_after == BLOCK_MASS


def _good_observation() -> Observation:
    return _obs(
        mass_after=mass(240000.0), mass_before=mass(120000.0), bbox_after=BoundingBox(min=(0, 0, 0), max=(240, 60, 20))
    ).model_copy(
        update={
            "copy_mass": BLOCK_MASS.model_copy(update={"centroid": (190.0, 30.0, 10.0)}),
            "copy_bbox": BoundingBox(min=(140.0, 0.0, 0.0), max=(240.0, 60.0, 20.0)),
            "source_mass_after": BLOCK_MASS,
        }
    )


def _verdicts(observation: Observation) -> dict:
    stub = _block_stub()
    expectations = _computer_with_block(stub).compute(*_mirror_body(X120), DOC, stub)
    return {r.name: r for r in Level2Check(Thresholds()).check(expectations, observation)}


def test_a_correct_mirrored_body_passes_every_check() -> None:
    verdicts = _verdicts(_good_observation())
    assert {name: r.ok for name, r in verdicts.items()} == dict.fromkeys(verdicts, True)


def test_a_body_that_was_copied_instead_of_mirrored_is_caught() -> None:
    """A copy slid to the same place has the right box but not the mirrored centroid of a lopsided body."""
    translated = _good_observation().model_copy(
        update={"copy_mass": BLOCK_MASS.model_copy(update={"centroid": (190.0 - 60.0, 30.0, 10.0)})}
    )
    assert _verdicts(translated)["copy_centroid"].ok is False
    unmoved = _good_observation().model_copy(update={"copy_mass": BLOCK_MASS, "copy_bbox": BLOCK_BOX})
    verdicts = _verdicts(unmoved)
    assert verdicts["copy_centroid"].ok is False and verdicts["copy_bbox"].ok is False
