"""Level 2 for ``copy_body``: expected.py row, observe.py body-scoped capture and level2.py evaluators."""

import math

import pytest

from cotaforge.contract import (
    BodyHandle,
    BoundingBox,
    BoxRequest,
    CopyBodyRequest,
    FeatureHandle,
    MassProperties,
    OperationResult,
    Transform,
)
from cotaforge.ir.ops_primitives import BoxOperation, CopyBodyOperation, RotateSpec
from cotaforge.ir.refs import BodyRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot, gather_observation
from cotaforge.verification.results import CheckKind
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import mass
from .test_level2 import _obs

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
BLOCK_BOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 20.0))
BLOCK_MASS = MassProperties(volume_mm3=120000.0, centroid=(50.0, 30.0, 10.0), surface_mm2=18400.0)


def _by_name(expectations: tuple) -> dict:
    return {e.name: e for e in expectations}


def _stub(source_mass: MassProperties = BLOCK_MASS) -> StubAdapter:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = source_mass
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


def _copy(transform: Transform, op: CopyBodyOperation | None = None) -> tuple[CopyBodyOperation, CopyBodyRequest]:
    op = op or CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"))
    return op, CopyBodyRequest(operation_id="twin", source=BodyHandle(id="body:1"), transform=transform)


FEATURE = FeatureHandle(id="feature:9")
DOC = StateSnapshot(mass=BLOCK_MASS, bbox=BLOCK_BOX)


# --- expected.py ----------------------------------------------------------------------------


def test_translated_copy_expects_the_source_volume_and_a_rigidly_moved_pose() -> None:
    stub = _stub()
    computer = _computer_with_block(stub)
    op, request = _copy(Transform(origin=(0.0, 100.0, 0.0)))
    by = _by_name(computer.compute(op, request, DOC, stub))
    assert set(by) == {"volume_delta", "copy_centroid", "copy_surface", "source_centroid", "copy_bbox"}
    assert all(e.kind is CheckKind.asserted for e in by.values())
    assert by["volume_delta"].value == pytest.approx(120000.0)
    assert by["copy_centroid"].value == pytest.approx((50.0, 130.0, 10.0))
    assert by["copy_surface"].value == pytest.approx(18400.0)
    assert by["source_centroid"].value == pytest.approx((50.0, 30.0, 10.0))
    assert by["copy_bbox"].value == BoundingBox(min=(0.0, 100.0, 0.0), max=(100.0, 160.0, 20.0))


def test_quarter_turn_copy_asserts_the_rotated_bbox() -> None:
    stub = _stub()
    computer = _computer_with_block(stub)
    op, request = _copy(Transform.from_axis_angle((0.0, 0.0, 1.0), 90.0))
    by = _by_name(computer.compute(op, request, DOC, stub))
    assert by["copy_bbox"].kind is CheckKind.asserted
    box = by["copy_bbox"].value
    assert box.min == pytest.approx((-60.0, 0.0, 0.0)) and box.max == pytest.approx((0.0, 100.0, 20.0))  # type: ignore[union-attr]
    assert by["copy_centroid"].value == pytest.approx((-30.0, 50.0, 10.0))


def test_oblique_turn_copy_reports_the_bbox_but_still_asserts_the_centroid() -> None:
    stub = _stub()
    computer = _computer_with_block(stub)
    op, request = _copy(Transform.from_axis_angle((0.0, 0.0, 1.0), 30.0))
    by = _by_name(computer.compute(op, request, DOC, stub))
    assert by["copy_bbox"].kind is CheckKind.reported
    assert "90" in by["copy_bbox"].note
    assert by["copy_centroid"].kind is CheckKind.asserted
    c = math.cos(math.radians(30.0))
    s = math.sin(math.radians(30.0))
    assert by["copy_centroid"].value == pytest.approx((50.0 * c - 30.0 * s, 50.0 * s + 30.0 * c, 10.0))


def test_a_source_without_a_surface_area_gets_no_surface_expectation() -> None:
    stub = _stub(mass(120000.0, (50.0, 30.0, 10.0)))
    computer = _computer_with_block(stub)
    op, request = _copy(Transform(origin=(1.0, 0.0, 0.0)))
    assert "copy_surface" not in _by_name(computer.compute(op, request, DOC, stub))


def test_the_copy_is_tracked_as_its_own_body_with_a_moved_analytic_shape() -> None:
    stub = _stub()
    computer = _computer_with_block(stub)
    source_shape = computer._shape_of("block")
    op, request = _copy(Transform(origin=(0.0, 100.0, 0.0)))
    computer.compute(op, request, DOC, stub)
    assert computer._shape_of("block") == source_shape  # the source keeps its place
    copy_shape = computer._shape_of("twin")
    assert copy_shape is not None and copy_shape != source_shape
    assert copy_shape.min == pytest.approx((0.0, 100.0, 0.0))  # type: ignore[union-attr]
    assert computer._body_of_op["twin"] == "twin" != computer._body_of_op["block"]


def test_a_copy_of_a_body_with_no_analytic_shape_has_none() -> None:
    stub = _stub()
    computer = ExpectedComputer(Thresholds())  # never saw "block": its shape is unknown
    op, request = _copy(Transform(origin=(0.0, 100.0, 0.0)))
    computer.compute(op, request, DOC, stub)
    assert computer._shape_of("twin") is None


def test_rotate_spec_is_not_needed_to_build_the_expectations() -> None:
    """The request carries the composed transform; the IR ``rotate`` field never reaches the verifier."""
    stub = _stub()
    computer = _computer_with_block(stub)
    op = CopyBodyOperation(
        id="twin",
        intent="i",
        source=BodyRef(from_op="block"),
        offset=(5.0, 0.0, 0.0),
        rotate=RotateSpec(axis="Z", angle_deg=90.0),  # type: ignore[arg-type]
    )
    _, request = _copy(Transform(origin=(5.0, 0.0, 0.0)).compose(Transform.from_axis_angle((0.0, 0.0, 1.0), 90.0)), op)
    by = _by_name(computer.compute(op, request, DOC, stub))
    assert by["copy_centroid"].value == pytest.approx((-25.0, 50.0, 10.0))


# --- observe.py -------------------------------------------------------------------------------


def test_observation_reads_the_copy_and_the_source_by_body() -> None:
    stub = _stub()
    stub.mass_by_body["body:2"] = BLOCK_MASS.model_copy(update={"centroid": (50.0, 130.0, 10.0)})
    stub.bbox_by_body["body:2"] = BoundingBox(min=(0.0, 100.0, 0.0), max=(100.0, 160.0, 20.0))
    op = CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"))
    _, request = _copy(Transform(origin=(0.0, 100.0, 0.0)))
    result = OperationResult(
        operation_id="twin", feature=FEATURE, rebuild_ok=True, created_bodies=(BodyHandle(id="body:2"),)
    )
    obs = gather_observation(stub, op, result, DOC, request)
    assert obs.copy_mass is not None and obs.copy_mass.centroid == (50.0, 130.0, 10.0)
    assert obs.copy_bbox == BoundingBox(min=(0.0, 100.0, 0.0), max=(100.0, 160.0, 20.0))
    assert obs.source_mass_after == BLOCK_MASS


def test_observation_without_a_created_body_leaves_the_copy_unobserved() -> None:
    stub = _stub()
    op = CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"))
    _, request = _copy(Transform(origin=(0.0, 100.0, 0.0)))
    result = OperationResult(operation_id="twin", feature=FEATURE, rebuild_ok=True)
    obs = gather_observation(stub, op, result, DOC, request)
    assert obs.copy_mass is None and obs.copy_bbox is None
    assert obs.source_mass_after == BLOCK_MASS


def test_other_ops_leave_the_copy_fields_empty() -> None:
    stub = _stub()
    op = BoxOperation(id="b", intent="i", size=(1, 1, 1))
    result = OperationResult(
        operation_id="b", feature=FEATURE, rebuild_ok=True, created_bodies=(BodyHandle(id="body:1"),)
    )
    obs = gather_observation(stub, op, result, DOC, BoxRequest(operation_id="b", size=(1, 1, 1)))
    assert (obs.copy_mass, obs.copy_bbox, obs.source_mass_after) == (None, None, None)


# --- level2.py --------------------------------------------------------------------------------


def _verdicts(stub: StubAdapter, computer: ExpectedComputer, observation: Observation) -> dict:
    op, request = _copy(Transform(origin=(0.0, 100.0, 0.0)))
    expectations = computer.compute(op, request, DOC, stub)
    return {r.name: r for r in Level2Check(Thresholds()).check(expectations, observation)}


def _good_observation() -> Observation:
    return _obs(
        mass_after=mass(240000.0), mass_before=mass(120000.0), bbox_after=BoundingBox(min=(0, 0, 0), max=(100, 160, 20))
    ).model_copy(
        update={
            "copy_mass": BLOCK_MASS.model_copy(update={"centroid": (50.0, 130.0, 10.0)}),
            "copy_bbox": BoundingBox(min=(0.0, 100.0, 0.0), max=(100.0, 160.0, 20.0)),
            "source_mass_after": BLOCK_MASS,
        }
    )


def test_a_correct_copy_passes_every_check() -> None:
    stub = _stub()
    verdicts = _verdicts(stub, _computer_with_block(stub), _good_observation())
    assert {name: r.ok for name, r in verdicts.items()} == dict.fromkeys(verdicts, True)


def test_a_copy_made_as_a_move_is_caught_by_the_source_centroid() -> None:
    stub = _stub()
    moved = _good_observation().model_copy(
        update={"source_mass_after": BLOCK_MASS.model_copy(update={"centroid": (50.0, 130.0, 10.0)})}
    )
    verdicts = _verdicts(stub, _computer_with_block(stub), moved)
    assert verdicts["source_centroid"].ok is False
    assert verdicts["copy_centroid"].ok is True


def test_a_copy_left_in_place_is_caught_by_the_copy_centroid_and_bbox() -> None:
    stub = _stub()
    in_place = _good_observation().model_copy(update={"copy_mass": BLOCK_MASS, "copy_bbox": BLOCK_BOX})
    verdicts = _verdicts(stub, _computer_with_block(stub), in_place)
    assert verdicts["copy_centroid"].ok is False
    assert verdicts["copy_bbox"].ok is False


def test_a_copy_with_the_wrong_surface_is_caught() -> None:
    stub = _stub()
    good = _good_observation()
    assert good.copy_mass is not None
    wrong = good.model_copy(update={"copy_mass": good.copy_mass.model_copy(update={"surface_mm2": 9000.0})})
    assert _verdicts(stub, _computer_with_block(stub), wrong)["copy_surface"].ok is False


def test_a_document_that_did_not_gain_the_source_volume_is_caught() -> None:
    stub = _stub()
    half = _good_observation().model_copy(update={"mass_after": mass(180000.0)})
    assert _verdicts(stub, _computer_with_block(stub), half)["volume_delta"].ok is False


def test_an_unobserved_copy_fails_the_body_scoped_checks_with_a_reason() -> None:
    stub = _stub()
    blind = _good_observation().model_copy(update={"copy_mass": None, "copy_bbox": None, "source_mass_after": None})
    verdicts = _verdicts(stub, _computer_with_block(stub), blind)
    for name in ("copy_centroid", "copy_surface", "copy_bbox", "source_centroid"):
        assert verdicts[name].ok is False
        assert "not observed" in verdicts[name].message
