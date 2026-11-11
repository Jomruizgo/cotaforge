"""The seed's own box, and the poses of a host that moved, copied or mirrored: ``ExpectedComputer`` level pins."""

import pytest

from cotaforge.contract import BodyHandle, CopyBodyRequest, MirrorBodyRequest, MoveBodyRequest, Transform
from cotaforge.ir.ops_features import MirrorBodyOperation
from cotaforge.ir.ops_primitives import CopyBodyOperation, RotateOperation, TranslateOperation
from cotaforge.ir.refs import BodyRef, PlaneRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind
from tests.unit.executor.stub import StubAdapter
from tests.unit.verification.test_mirror_verification import X20
from tests.unit.verification.test_pattern_host import (
    PLATE_BBOX,
    RECT,
    _before,
    _by_name,
    _computer_with_pad,
    _hole_stub,
    _row,
    _with_boss,
)

from .helpers import mass

SNAPSHOT = StateSnapshot(mass=mass(48000.0), bbox=PLATE_BBOX)


def _stub() -> StubAdapter:
    stub = StubAdapter()
    stub.bbox_by_body["body:1"] = PLATE_BBOX
    stub.mass_by_body["body:1"] = mass(48000.0)
    return stub


def test_a_seed_that_stands_on_an_earlier_feature_makes_its_instances_suspect_too() -> None:
    """A post at (10, 30) and a bore through it: the seed crosses post and plate, an instance far from the post only the
    plate, so repeating the seed's change would be wrong."""
    op, request = _row(40.0, 2)
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    _with_boss(computer, (10.0, 30.0))
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["created_faces_count"].kind is CheckKind.reported
    assert by["instance_count"].kind is CheckKind.asserted


def _move(computer: ExpectedComputer, op_name: str) -> None:
    target = BodyRef(from_op="base")
    if op_name == "translate":
        op = TranslateOperation(id="mv", intent="i", target=target, offset=(50.0, 0.0, 0.0))
        transform = Transform(origin=(50.0, 0.0, 0.0))
    else:
        op = RotateOperation(id="mv", intent="i", target=target, axis="Z", angle_deg=90.0)
        transform = Transform.from_axis_angle((0.0, 0.0, 1.0), 90.0)
    computer.compute(
        op, MoveBodyRequest(operation_id="mv", body=BodyHandle(id="body:1"), transform=transform), SNAPSHOT, _stub()
    )


@pytest.mark.parametrize("op_name", ["translate", "rotate"])
def test_a_moved_host_forgets_its_outline_and_the_regions_of_its_features(op_name: str) -> None:
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    _with_boss(computer, (80.0, 50.0))
    assert computer._pad_host is not None  # noqa: SLF001
    _move(computer, op_name)
    assert computer._pad_host is None  # noqa: SLF001
    assert computer._feature_regions == {"boss2": None}  # noqa: SLF001 - recorded, but no longer locatable
    op, request = _row(30.0, 2)
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["instance_count"].kind is CheckKind.asserted


def test_a_copy_or_a_reflection_of_the_host_also_forgets_what_was_recorded() -> None:
    copied = _computer_with_pad([RECT], PLATE_BBOX)
    _with_boss(copied, (80.0, 50.0))
    copied.compute(
        CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="base"), offset=(0.0, 100.0, 0.0)),
        CopyBodyRequest(operation_id="twin", source=BodyHandle(id="body:1"), transform=Transform(origin=(0, 100, 0))),
        SNAPSHOT,
        _stub(),
    )
    assert copied._pad_host is None  # noqa: SLF001
    assert copied._feature_regions == {"boss2": None}  # noqa: SLF001

    mirrored = _computer_with_pad([RECT], PLATE_BBOX)
    _with_boss(mirrored, (80.0, 50.0))
    mirrored.compute(
        MirrorBodyOperation(id="twin", intent="i", source=BodyRef(from_op="base"), plane=PlaneRef(name="YZ")),
        MirrorBodyRequest(operation_id="twin", source=BodyHandle(id="body:1"), plane=X20),
        SNAPSHOT,
        _stub(),
    )
    assert mirrored._pad_host is None  # noqa: SLF001
    assert mirrored._feature_regions == {"boss2": None}  # noqa: SLF001
