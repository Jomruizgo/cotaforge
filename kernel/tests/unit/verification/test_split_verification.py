"""Level 2 for ``split`` (two pieces that must add up to the source, on the sides the plane says) and for the scaled
``copy_body``, with the edit-tracking contract around both.

A split is exact for every plane the kernel can cut, so the sum of the pieces, their volume-weighted centroid and the
side each one sits on are asserted whatever the plane. What stays at the analytic level is the volume of the ONE piece
a ``keep`` positive or negative leaves: asserted only for a tracked, untouched box cut by an axis-aligned plane."""

import math

import pytest

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BoundingBox,
    BoxRequest,
    CopyBodyRequest,
    CylinderRequest,
    FeatureHandle,
    MassProperties,
    OperationResult,
    SketchFrame,
    SplitRequest,
    Transform,
)
from cotaforge.ir.ops_features import CutOperation, SplitOperation
from cotaforge.ir.ops_primitives import BoxOperation, CopyBodyOperation, CylinderOperation
from cotaforge.ir.refs import BodyRef, PlaneRef
from cotaforge.verification import formulas as f
from cotaforge.verification.expected import _NON_EDITING_OPS, ExpectedComputer
from cotaforge.verification.formulas import BoxShape, CylinderShape
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot, gather_observation
from cotaforge.verification.results import CheckKind
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import mass
from .test_level2 import _obs

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
FEATURE = FeatureHandle(id="feature:2")
S2 = 1 / math.sqrt(2.0)

BLOCK_BOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 20.0))
BLOCK_MASS = MassProperties(volume_mm3=120000.0, centroid=(50.0, 30.0, 10.0), surface_mm2=18400.0)
DOC = StateSnapshot(mass=BLOCK_MASS, bbox=BLOCK_BOX)
UP = BodyHandle(id="body:2")
DOWN = BodyHandle(id="body:3")


def _frame(origin: tuple[float, float, float], normal: tuple[float, float, float]) -> SketchFrame:
    return SketchFrame(origin=origin, u=(1.0, 0.0, 0.0), v=(0.0, 1.0, 0.0), n=normal)


Z5 = _frame((0.0, 0.0, 5.0), (0.0, 0.0, 1.0))
Z5_DOWN = _frame((0.0, 0.0, 5.0), (0.0, 0.0, -1.0))


def _by_name(expectations: tuple) -> dict:
    return {e.name: e for e in expectations}


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


def _split(plane: SketchFrame, keep: str = "both") -> tuple[SplitOperation, SplitRequest]:
    op = SplitOperation(
        id="sp",
        intent="i",
        source=BodyRef(from_op="block"),
        plane=PlaneRef(name="XY"),
        keep=keep,  # type: ignore[arg-type]
    )
    return op, SplitRequest(operation_id="sp", source=BodyHandle(id="body:1"), plane=plane, keep=keep)  # type: ignore[arg-type]


# --- formulas -------------------------------------------------------------------------------------------


def test_scale_point_and_bbox_scale_grow_about_a_centre() -> None:
    assert f.scale_point((12.0, 5.0, 0.0), 2.0, (10.0, 5.0, 0.0)) == pytest.approx((14.0, 5.0, 0.0))
    box = BoundingBox(min=(0.0, 0.0, 0.0), max=(10.0, 20.0, 30.0))
    assert f.bbox_scale(box, 1.5, (0.0, 0.0, 0.0)) == BoundingBox(min=(0.0, 0.0, 0.0), max=(15.0, 30.0, 45.0))
    assert f.bbox_scale(box, 0.5, (10.0, 20.0, 30.0)) == BoundingBox(min=(5.0, 10.0, 15.0), max=(10.0, 20.0, 30.0))


def test_scale_shape_scales_a_box_and_a_cylinder_about_the_centre() -> None:
    box = BoxShape(min=(0.0, 0.0, 0.0), max=(10.0, 20.0, 30.0))
    assert f.scale_shape(box, 2.0, (0.0, 0.0, 0.0)) == BoxShape(min=(0.0, 0.0, 0.0), max=(20.0, 40.0, 60.0))
    cylinder = CylinderShape(axis=Axis(origin=(5.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)), radius=2.0, height=7.0)
    grown = f.scale_shape(cylinder, 2.0, (1.0, 0.0, 0.0))
    assert isinstance(grown, CylinderShape)
    assert grown.axis.origin == pytest.approx((9.0, 0.0, 0.0))
    assert (grown.radius, grown.height) == (4.0, 14.0)


# --- split: keep both -----------------------------------------------------------------------------------


def test_a_split_that_keeps_both_pieces_asserts_the_sum_the_centroid_and_the_sides() -> None:
    stub = _block_stub()
    by = _by_name(_computer_with_block(stub).compute(*_split(Z5), DOC, stub))
    assert set(by) == {"volume_delta", "split_volume_sum", "split_centroid", "split_sides"}
    assert all(e.kind is CheckKind.asserted for e in by.values())
    assert by["volume_delta"].value == 0.0
    assert by["split_volume_sum"].value == pytest.approx(120000.0)
    assert by["split_centroid"].value == pytest.approx((50.0, 30.0, 10.0))
    assert by["split_sides"].value is True


def test_the_sum_and_the_centroid_are_asserted_across_an_oblique_plane_too() -> None:
    stub = _block_stub()
    plane = _frame((50.0, 30.0, 10.0), (S2, 0.0, S2))
    by = _by_name(_computer_with_block(stub).compute(*_split(plane), DOC, stub))
    assert all(e.kind is CheckKind.asserted for e in by.values())


def test_the_pieces_are_registered_under_the_op_id_and_its_negative_key_with_no_tracked_shape() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer.compute(*_split(Z5), DOC, stub)
    assert computer._body_of_op["sp"] == "sp"
    assert computer._body_of_op["sp#negative"] == "sp#negative"
    assert computer._shape_of("sp") is None and computer._shape_by_body["sp#negative"] is None
    assert computer._shape_of("block") is None
    assert "block" in computer._consumed
    assert computer._live_body_count() == 2


def test_a_split_that_keeps_one_piece_registers_only_that_piece_under_the_op_id() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer.compute(*_split(Z5, "negative"), DOC, stub)
    assert "sp#negative" not in computer._body_of_op
    assert computer._body_of_op["sp"] == "sp"
    assert "block" in computer._consumed
    assert computer._live_body_count() == 1


# --- split: keep one piece ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plane", "keep", "kept_height"),
    [(Z5, "positive", 15.0), (Z5, "negative", 5.0), (Z5_DOWN, "positive", 5.0), (Z5_DOWN, "negative", 15.0)],
)
def test_the_volume_of_the_kept_piece_is_asserted_for_a_tracked_box_and_an_axis_aligned_plane(
    plane: SketchFrame, keep: str, kept_height: float
) -> None:
    stub = _block_stub()
    by = _by_name(_computer_with_block(stub).compute(*_split(plane, keep), DOC, stub))
    assert set(by) == {"volume_delta", "split_sides"}
    assert by["volume_delta"].kind is CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(100.0 * 60.0 * kept_height - 120000.0)
    assert by["split_sides"].kind is CheckKind.asserted


def test_the_kept_volume_is_only_reported_across_an_oblique_plane() -> None:
    stub = _block_stub()
    plane = _frame((50.0, 30.0, 10.0), (S2, 0.0, S2))
    by = _by_name(_computer_with_block(stub).compute(*_split(plane, "positive"), DOC, stub))
    assert by["volume_delta"].kind is CheckKind.reported
    assert "axis-aligned" in by["volume_delta"].note


def test_the_kept_volume_is_only_reported_for_a_cylinder() -> None:
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
    by = _by_name(computer.compute(*_split(_frame((0.0, 0.0, 10.0), (0.0, 0.0, 1.0)), "positive"), DOC, stub))
    assert by["volume_delta"].kind is CheckKind.reported


def test_the_kept_volume_is_only_reported_when_an_earlier_edit_touched_the_source() -> None:
    """A hole through the block changes the volume the box closed form describes: asserting it would be false."""
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer._edits["block"] = [BoundingBox(min=(10.0, 10.0, 0.0), max=(20.0, 20.0, 20.0))]
    by = _by_name(computer.compute(*_split(Z5, "positive"), DOC, stub))
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["split_sides"].kind is CheckKind.asserted


def test_the_kept_volume_is_only_reported_when_the_source_is_not_tracked() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer._shape_by_body["block"] = None
    by = _by_name(computer.compute(*_split(Z5, "positive"), DOC, stub))
    assert by["volume_delta"].kind is CheckKind.reported


# --- edit tracking pins -----------------------------------------------------------------------------------


def test_split_is_recorded_as_an_edit_not_listed_among_the_non_editing_ops() -> None:
    assert "split" not in _NON_EDITING_OPS
    assert "copy_body" in _NON_EDITING_OPS


def test_a_ref_with_a_side_names_that_piece_for_the_edit_tracking() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer.compute(*_split(Z5), DOC, stub)
    cut = CutOperation(
        id="pocket",
        intent="i",
        sketch="s",
        depth_mm=2.0,
        scope=(BodyRef(from_op="sp", side="negative"),),
    )
    assert computer._explicit_keys(cut) == {"sp#negative"}
    positive = cut.model_copy(update={"scope": (BodyRef(from_op="sp", side="positive"),)})
    assert computer._explicit_keys(positive) == {"sp"}


def test_an_edit_on_the_negative_piece_is_recorded_against_it_and_not_the_positive_one() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer.compute(*_split(Z5), DOC, stub)
    cut = CutOperation(
        id="pocket",
        intent="i",
        sketch="s",
        depth_mm=2.0,
        scope=(BodyRef(from_op="sp", side="negative"),),
    )
    computer.compute(cut, None, DOC, stub)  # type: ignore[arg-type]
    assert computer._feature_keys["pocket"] == {"sp#negative"}


def test_a_face_of_a_split_may_belong_to_either_piece_so_both_are_recorded() -> None:
    from cotaforge.ir.ops_features import HoleOperation  # noqa: F401  (shape of a face-based op below)
    from cotaforge.ir.refs import FaceRef

    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer.compute(*_split(Z5), DOC, stub)

    class _FaceOp:
        id = "h"
        op = "hole"

        def refs(self) -> tuple[FaceRef, ...]:
            return (FaceRef(from_op="sp"),)

    assert computer._explicit_keys(_FaceOp()) == {"sp", "sp#negative"}


def test_later_ops_on_a_piece_resolve_to_the_piece_key_not_the_positive_one() -> None:
    from cotaforge.contract import BooleanKind, BooleanRequest
    from cotaforge.ir.ops_primitives import BooleanSubtractOperation

    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer.compute(*_split(Z5), DOC, stub)
    stub.mass_by_body["body:3"] = mass(30000.0, (50.0, 30.0, 2.5))
    stub.bbox_by_body["body:3"] = BoundingBox(min=(0, 0, 0), max=(100, 60, 5))
    stub.mass_by_body["body:9"] = mass(1000.0, (10.0, 10.0, 2.5))
    stub.bbox_by_body["body:9"] = BoundingBox(min=(5, 5, 0), max=(15, 15, 5))
    op = BooleanSubtractOperation(
        id="cavity",
        intent="i",
        target=BodyRef(from_op="sp", side="negative"),
        tools=(BodyRef(from_op="sp", side="positive"),),
    )
    request = BooleanRequest(
        operation_id="cavity", kind=BooleanKind.subtract, target=DOWN, tools=(UP,), keep_tools=False
    )
    stub.mass_by_body["body:2"] = mass(90000.0, (50.0, 30.0, 12.5))
    stub.bbox_by_body["body:2"] = BoundingBox(min=(0, 0, 5), max=(100, 60, 20))
    computer.compute(op, request, DOC, stub)
    assert computer._body_of_op["cavity"] == "sp#negative"
    assert "sp" in computer._consumed and "sp#negative" not in computer._consumed


# --- observe.py and level2.py -----------------------------------------------------------------------------


def _both_stub() -> StubAdapter:
    stub = _block_stub()
    stub.mass_by_body["body:2"] = MassProperties(volume_mm3=90000.0, centroid=(50.0, 30.0, 12.5), surface_mm2=14000.0)
    stub.bbox_by_body["body:2"] = BoundingBox(min=(0.0, 0.0, 5.0), max=(100.0, 60.0, 20.0))
    stub.mass_by_body["body:3"] = MassProperties(volume_mm3=30000.0, centroid=(50.0, 30.0, 2.5), surface_mm2=9000.0)
    stub.bbox_by_body["body:3"] = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 5.0))
    return stub


def test_observation_reads_each_created_piece_by_body_with_the_plane_and_their_sides() -> None:
    stub = _both_stub()
    op, request = _split(Z5)
    result = OperationResult(operation_id="sp", feature=FEATURE, rebuild_ok=True, created_bodies=(UP, DOWN))
    obs = gather_observation(stub, op, result, DOC, request)
    assert obs.split_masses is not None and [m.volume_mm3 for m in obs.split_masses] == [90000.0, 30000.0]
    assert obs.split_sides == ("positive", "negative")
    assert obs.split_plane == Z5


def test_observation_labels_the_single_piece_of_a_keep_with_that_side() -> None:
    stub = _both_stub()
    op, request = _split(Z5, "negative")
    result = OperationResult(operation_id="sp", feature=FEATURE, rebuild_ok=True, created_bodies=(DOWN,))
    obs = gather_observation(stub, op, result, DOC, request)
    assert obs.split_sides == ("negative",)
    assert obs.split_masses is not None and len(obs.split_masses) == 1


def _good_observation() -> Observation:
    stub = _both_stub()
    return _obs(mass_after=mass(120000.0), mass_before=mass(120000.0), bbox_after=BLOCK_BOX).model_copy(
        update={
            "split_masses": (stub.mass_by_body["body:2"], stub.mass_by_body["body:3"]),
            "split_sides": ("positive", "negative"),
            "split_plane": Z5,
        }
    )


def _verdicts(observation: Observation, plane: SketchFrame = Z5) -> dict:
    stub = _block_stub()
    expectations = _computer_with_block(stub).compute(*_split(plane), DOC, stub)
    return {r.name: r for r in Level2Check(Thresholds()).check(expectations, observation)}


def test_a_correct_split_passes_every_check() -> None:
    verdicts = _verdicts(_good_observation())
    assert {name: r.ok for name, r in verdicts.items()} == dict.fromkeys(verdicts, True)


def test_pieces_that_do_not_add_up_to_the_source_are_caught() -> None:
    lost = _good_observation()
    short = lost.split_masses[1].model_copy(update={"volume_mm3": 20000.0})  # type: ignore[index]
    verdicts = _verdicts(lost.model_copy(update={"split_masses": (lost.split_masses[0], short)}))  # type: ignore[index]
    assert verdicts["split_volume_sum"].ok is False


def test_a_centroid_that_drifts_from_the_source_is_caught() -> None:
    obs = _good_observation()
    moved = obs.split_masses[0].model_copy(update={"centroid": (50.0, 30.0, 16.0)})  # type: ignore[index]
    verdicts = _verdicts(obs.model_copy(update={"split_masses": (moved, obs.split_masses[1])}))  # type: ignore[index]
    assert verdicts["split_centroid"].ok is False


def test_pieces_reported_on_the_wrong_sides_are_caught() -> None:
    obs = _good_observation()
    swapped = obs.model_copy(update={"split_masses": (obs.split_masses[1], obs.split_masses[0])})  # type: ignore[index]
    verdicts = _verdicts(swapped)
    assert verdicts["split_sides"].ok is False
    assert verdicts["split_volume_sum"].ok is True  # the sum alone cannot tell the sides apart


def test_a_source_that_survives_the_split_is_caught_by_the_document_volume() -> None:
    doubled = _good_observation().model_copy(update={"mass_after": mass(240000.0)})
    assert _verdicts(doubled)["volume_delta"].ok is False


def test_a_missing_piece_is_unobservable_not_a_pass() -> None:
    one = _good_observation().model_copy(update={"split_masses": (_good_observation().split_masses[0],)})  # type: ignore[index]
    verdicts = _verdicts(one)
    assert verdicts["split_volume_sum"].ok is False and "2" in verdicts["split_volume_sum"].message
    assert verdicts["split_centroid"].ok is False


def test_a_single_kept_piece_is_judged_on_its_side_only() -> None:
    stub = _block_stub()
    expectations = _computer_with_block(stub).compute(*_split(Z5, "negative"), DOC, stub)
    kept = _both_stub().mass_by_body["body:3"]
    good = _obs(mass_after=mass(30000.0), mass_before=mass(120000.0), bbox_after=BLOCK_BOX).model_copy(
        update={"split_masses": (kept,), "split_sides": ("negative",), "split_plane": Z5}
    )
    verdicts = {r.name: r for r in Level2Check(Thresholds()).check(expectations, good)}
    assert verdicts["split_sides"].ok is True
    assert verdicts["volume_delta"].ok is True  # 30000 - 120000 = -90000
    wrong_side = good.model_copy(update={"split_masses": (_both_stub().mass_by_body["body:2"],)})
    verdicts = {r.name: r for r in Level2Check(Thresholds()).check(expectations, wrong_side)}
    assert verdicts["split_sides"].ok is False


# --- copy_body with a scale -------------------------------------------------------------------------------


def _scaled_copy(
    scale: float, about: tuple[float, float, float], transform: Transform
) -> tuple[CopyBodyOperation, CopyBodyRequest]:
    op = CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"), scale=scale, scale_about=about)
    request = CopyBodyRequest(
        operation_id="twin", source=BodyHandle(id="body:1"), transform=transform, scale=scale, scale_about=about
    )
    return op, request


def test_a_scaled_copy_expects_the_cubed_volume_the_squared_surface_and_the_scaled_centroid() -> None:
    stub = _block_stub()
    shift = Transform(origin=(200.0, 0.0, 0.0))
    by = _by_name(_computer_with_block(stub).compute(*_scaled_copy(2.0, (0.0, 0.0, 0.0), shift), DOC, stub))
    assert all(e.kind is CheckKind.asserted for e in by.values())
    assert by["volume_delta"].value == pytest.approx(8.0 * 120000.0)
    assert by["copy_surface"].value == pytest.approx(4.0 * 18400.0)
    assert by["copy_centroid"].value == pytest.approx((300.0, 60.0, 20.0))
    assert by["source_centroid"].value == pytest.approx((50.0, 30.0, 10.0))
    assert by["copy_bbox"].value == BoundingBox(min=(200.0, 0.0, 0.0), max=(400.0, 120.0, 40.0))


def test_a_scale_about_the_centroid_keeps_the_centroid_where_it_was() -> None:
    stub = _block_stub()
    by = _by_name(_computer_with_block(stub).compute(*_scaled_copy(1.5, (50.0, 30.0, 10.0), Transform()), DOC, stub))
    assert by["copy_centroid"].value == pytest.approx((50.0, 30.0, 10.0))
    assert by["copy_bbox"].value == BoundingBox(min=(-25.0, -15.0, -5.0), max=(125.0, 75.0, 25.0))


def test_the_scale_comes_before_the_rotation_and_the_offset() -> None:
    """Scale about the origin, then a quarter turn about Z, then the offset: p -> R(s p) + t."""
    stub = _block_stub()
    quarter = f.rotation_about_point((0.0, 0.0, 1.0), 90.0, (0.0, 0.0, 0.0)).model_copy(
        update={"origin": (10.0, 0.0, 0.0)}
    )
    by = _by_name(_computer_with_block(stub).compute(*_scaled_copy(2.0, (0.0, 0.0, 0.0), quarter), DOC, stub))
    assert by["copy_centroid"].value == pytest.approx((10.0 - 60.0, 100.0, 20.0))
    assert by["copy_bbox"].kind is CheckKind.asserted  # a quarter turn is principal


def test_an_oblique_turn_still_reports_the_box_of_a_scaled_copy() -> None:
    stub = _block_stub()
    turn = f.rotation_about_point((0.0, 0.0, 1.0), 30.0, (0.0, 0.0, 0.0))
    by = _by_name(_computer_with_block(stub).compute(*_scaled_copy(1.006, (0.0, 0.0, 0.0), turn), DOC, stub))
    assert by["copy_bbox"].kind is CheckKind.reported
    assert by["volume_delta"].value == pytest.approx(1.006**3 * 120000.0)


def test_a_scaled_copy_tracks_the_scaled_shape_and_its_edit_regions() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    computer._edits["block"] = [BoundingBox(min=(10.0, 0.0, 0.0), max=(20.0, 10.0, 20.0))]
    computer.compute(*_scaled_copy(2.0, (0.0, 0.0, 0.0), Transform(origin=(200.0, 0.0, 0.0))), DOC, stub)
    assert computer._shape_of("twin") == BoxShape(min=(200.0, 0.0, 0.0), max=(400.0, 120.0, 40.0))
    assert computer._edits["twin"] == [BoundingBox(min=(220.0, 0.0, 0.0), max=(240.0, 20.0, 40.0))]


def test_an_unscaled_copy_keeps_its_old_expectations() -> None:
    stub = _block_stub()
    by = _by_name(_computer_with_block(stub).compute(*_scaled_copy(1.0, (0.0, 0.0, 0.0), Transform()), DOC, stub))
    assert by["volume_delta"].value == pytest.approx(120000.0)
    assert by["copy_centroid"].value == pytest.approx((50.0, 30.0, 10.0))


# --- a split consumes its host: nothing known about the host's outline survives ----------------------------------


def test_a_split_forgets_the_host_outline_and_every_earlier_feature_region() -> None:
    """The base pad's outline and the regions of the features on it describe the host as it was: once it is cut in two
    the pieces start with no outline and regions that may meet anything, so a later pattern or mirror reports."""
    from .helpers import PLATE_RECT, computer_with_pad

    computer = computer_with_pad([PLATE_RECT])
    computer._feature_regions["bore"] = BoundingBox(min=(5.0, 5.0, 0.0), max=(15.0, 15.0, 8.0))
    assert computer._pad_host is not None
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(48000.0, (50.0, 30.0, 4.0))
    stub.bbox_by_body["body:1"] = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0))
    op = SplitOperation(id="sp", intent="i", source=BodyRef(from_op="base"), plane=PlaneRef(name="XY"))
    request = SplitRequest(operation_id="sp", source=BodyHandle(id="body:1"), plane=_frame((0.0, 0.0, 4.0), (0, 0, 1)))
    computer.compute(op, request, StateSnapshot(mass=mass(48000.0), bbox=BLOCK_BOX), stub)
    assert computer._pad_host is None
    assert computer._feature_regions["bore"] is None
    assert computer._feature_regions["sp"] is None


# --- soundness: a box that equals the document box proves nothing --------------------------------------------------


def test_the_big_piece_of_an_oblique_split_is_not_alone_in_a_document_whose_box_it_equals() -> None:
    """After an oblique cut the large piece's box IS the document's box, and the small piece stands inside it."""
    from cotaforge.contract import MoveBodyRequest
    from cotaforge.ir.ops_primitives import TranslateOperation

    stub = _block_stub()
    computer = _computer_with_block(stub)
    plane = _frame((50.0, 30.0, 10.0), (S2, 0.0, S2))
    computer.compute(*_split(plane), DOC, stub)
    stub.bbox_by_body["body:2"] = BLOCK_BOX  # the big piece reaches every extreme of the document box
    move = TranslateOperation(id="mv", intent="i", target=BodyRef(from_op="sp", side="positive"), offset=(200, 0, 0))
    request = MoveBodyRequest(operation_id="mv", body=UP, transform=Transform(origin=(200.0, 0.0, 0.0)))
    by = _by_name(computer.compute(move, request, DOC, stub))
    assert by["bbox_after"].kind is CheckKind.reported
    assert "several bodies" in by["bbox_after"].note


def test_a_lone_body_whose_box_equals_the_document_box_is_still_asserted_when_moved() -> None:
    from cotaforge.contract import MoveBodyRequest
    from cotaforge.ir.ops_primitives import TranslateOperation

    stub = _block_stub()
    computer = _computer_with_block(stub)
    move = TranslateOperation(id="mv", intent="i", target=BodyRef(from_op="block"), offset=(200, 0, 0))
    request = MoveBodyRequest(
        operation_id="mv", body=BodyHandle(id="body:1"), transform=Transform(origin=(200.0, 0.0, 0.0))
    )
    by = _by_name(computer.compute(move, request, DOC, stub))
    assert by["bbox_after"].kind is CheckKind.asserted


def test_snapshot_and_restore_hand_back_exactly_the_tracking_state_of_the_moment() -> None:
    stub = _block_stub()
    computer = _computer_with_block(stub)
    saved = computer.snapshot()
    computer.compute(*_split(Z5), DOC, stub)
    assert "a" not in saved["_consumed"] and computer._split_pieces
    computer.restore(saved)
    assert computer._consumed == set() and computer._split_pieces == {}
    assert computer._shape_of("block") is not None
    computer._edits["block"] = [BLOCK_BOX]  # changing the restored state must not touch the saved copy
    assert saved["_edits"] == {}


def test_a_merged_boss_as_a_tool_does_not_hide_a_third_live_body_from_the_limit() -> None:
    """The tool's key is not a live body (it was merged into one), so ``1 + len(tools)`` overstates the bodies the op
    names: the third body must still be counted among the others."""
    from cotaforge.contract import BooleanKind, BooleanRequest
    from cotaforge.ir.ops_primitives import BooleanSubtractOperation

    stub = _block_stub()
    computer = _computer_with_block(stub)
    for op_id, size in (("tool", (10, 10, 10)), ("far", (10, 10, 10))):
        computer.compute(
            BoxOperation(id=op_id, intent="i", size=size),
            BoxRequest(operation_id=op_id, size=size, transform=Transform(origin=(300.0, 0.0, 0.0))),
            EMPTY,
            stub,
        )
    computer._merged_into["tool"] = {"block"}
    stub.mass_by_body["body:5"] = mass(1000.0, (305.0, 5.0, 5.0))
    stub.bbox_by_body["body:5"] = BoundingBox(min=(300, 0, 0), max=(310, 10, 10))
    op = BooleanSubtractOperation(
        id="cut", intent="i", target=BodyRef(from_op="block"), tools=(BodyRef(from_op="tool"),)
    )
    request = BooleanRequest(
        operation_id="cut", kind=BooleanKind.subtract, target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:5"),)
    )
    wide = StateSnapshot(mass=mass(1.0), bbox=BoundingBox(min=(0, 0, 0), max=(400, 60, 20)))
    by = _by_name(computer.compute(op, request, wide, stub))
    assert by["bbox_within"].value == wide.bbox
    assert "other bodies" in by["bbox_within"].note


def test_untracked_bodies_in_the_document_make_the_limit_the_whole_document_box() -> None:
    from cotaforge.contract import MoveBodyRequest
    from cotaforge.ir.ops_primitives import TranslateOperation

    stub = _block_stub()
    computer = _computer_with_block(stub)
    move = TranslateOperation(id="mv", intent="i", target=BodyRef(from_op="block"), offset=(200, 0, 0))
    request = MoveBodyRequest(
        operation_id="mv", body=BodyHandle(id="body:1"), transform=Transform(origin=(200.0, 0.0, 0.0))
    )
    adopted = DOC.model_copy(update={"untracked_bodies": True})
    by = _by_name(computer.compute(move, request, adopted, stub))
    assert by["bbox_after"].kind is CheckKind.reported
