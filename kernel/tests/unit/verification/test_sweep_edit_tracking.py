"""A merged sweep edits the body it grows from: its region is a CONSERVATIVE world box (the path points' box inflated by
the section's full reach in every direction), so a later boolean with a tool lying anywhere near the swept solid is
not asserted, while one far away stays analytic; a feature on a sweep face edits the host."""

import pytest

from cotaforge.contract import (
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    EndCondition,
    FaceHandle,
    HoleRequest,
    HoleStandard,
    SketchHandle,
    SketchRequest,
    SweepRequest,
    Transform,
)
from cotaforge.ir.ops_features import HoleOperation, PolylinePath, SketchOperation, SweepOperation
from cotaforge.ir.ops_primitives import BooleanUnionOperation, BoxOperation
from cotaforge.ir.refs import BodyRef, FaceRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.formulas import BoxShape
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import mass, planar_face

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
BLOCK = BoundingBox(min=(0, 0, 0), max=(10, 20, 30))
DOC = StateSnapshot(mass=mass(6000.0), bbox=BLOCK)
REACH = 2.0  # the D4 section
# up 20 mm from the block top face centre (5, 10, 30), then 55 mm along +X at z = 50
BENT_PATH = ((5.0, 10.0, 30.0), (5.0, 10.0, 50.0), (60.0, 10.0, 50.0))


def _stub() -> StubAdapter:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(6000.0, (5, 10, 15))
    stub.bbox_by_body["body:1"] = BLOCK
    stub.add_face(planar_face("face:top", (0, 0, 1), (5, 10, 30), 200.0, BoundingBox(min=(0, 0, 30), max=(10, 20, 30))))
    stub.add_face(
        planar_face("face:cap", (1, 0, 0), (60, 10, 50), 12.0, BoundingBox(min=(60, 8, 48), max=(60, 12, 52)))
    )
    return stub


def _with_sweep(stub: StubAdapter, merge: bool = True) -> ExpectedComputer:
    """The block, then a D4 tube swept from its top face (op ``tube``)."""
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="block", intent="i", size=(10, 20, 30)),
        BoxRequest(operation_id="block", size=(10, 20, 30)),
        EMPTY,
        stub,
    )
    circle = {"kind": "circle", "name": "c", "center": [0, 0], "diameter": 4}
    sketch = SketchOperation(id="s1", intent="i", on=FaceRef(from_op="block"), entities=[circle])
    computer.compute(
        sketch, SketchRequest(operation_id="s1", on=FaceHandle(id="face:top"), entities=sketch.entities), DOC, stub
    )
    sweep = SweepOperation(id="tube", intent="i", sketch="s1", path=PolylinePath(points=BENT_PATH), merge=merge)
    request = SweepRequest(operation_id="tube", sketch=SketchHandle(id="sk:1"), path=BENT_PATH, merge=merge)
    computer.compute(sweep, request, DOC, stub)
    return computer


def _tool_union(computer: ExpectedComputer, stub: StubAdapter, origin: tuple, size: tuple) -> CheckKind:
    """Union of the block with a box tool at ``origin``; returns the kind of the volume_delta expectation."""
    tool_box = BoundingBox(min=origin, max=tuple(o + s for o, s in zip(origin, size, strict=True)))
    computer.compute(
        BoxOperation(id="tool", intent="i", size=size, placement={"origin": origin}),
        BoxRequest(operation_id="tool", size=size, transform=Transform(origin=origin)),
        DOC,
        stub,
    )
    stub.mass_by_body["body:3"] = mass(size[0] * size[1] * size[2])
    stub.bbox_by_body["body:3"] = tool_box
    op = BooleanUnionOperation(id="u", intent="i", target=BodyRef(from_op="block"), tools=(BodyRef(from_op="tool"),))
    request = BooleanRequest(
        operation_id="u", kind=BooleanKind.union, target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:3"),)
    )
    delta = next(e for e in computer.compute(op, request, DOC, stub) if e.name == "volume_delta")
    return delta.kind


def test_the_sweep_region_is_the_path_box_inflated_by_the_full_reach() -> None:
    stub = _stub()
    computer = _with_sweep(stub)
    (region,) = computer._edits["block"]
    assert region.min == pytest.approx((5 - REACH, 10 - REACH, 30 - REACH))
    assert region.max == pytest.approx((60 + REACH, 10 + REACH, 50 + REACH))
    assert isinstance(computer._shape_of("block"), BoxShape)  # region known: the primitive shape is kept


def test_a_tool_the_bent_sweep_passes_through_is_not_asserted() -> None:
    """The straight-extrude box of the profile (x 3..7, up to z = 50) would miss this tool; the swept solid does not."""
    stub = _stub()
    computer = _with_sweep(stub)
    assert _tool_union(computer, stub, (20.0, 0.0, 45.0), (20.0, 20.0, 10.0)) is CheckKind.reported


def test_a_tool_far_from_the_sweep_stays_analytic() -> None:
    stub = _stub()
    computer = _with_sweep(stub)
    assert _tool_union(computer, stub, (100.0, 0.0, 0.0), (10.0, 20.0, 10.0)) is CheckKind.asserted


def test_a_feature_on_a_sweep_face_edits_the_host() -> None:
    stub = _stub()
    computer = _with_sweep(stub)
    bore = HoleOperation(
        id="bore",
        intent="i",
        face=FaceRef(from_op="tube"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=1.0,
        end_condition=EndCondition.through_all,
    )
    request = HoleRequest(
        operation_id="bore",
        face=FaceHandle(id="face:cap"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=1.0,
        end_condition=EndCondition.through_all,
    )
    computer.compute(bore, request, DOC, stub)
    assert len(computer._edits["block"]) == 2  # the sweep, then the bore through its end cap: both belong to the block


def test_a_sweep_that_is_not_merged_leaves_the_block_alone() -> None:
    stub = _stub()
    computer = _with_sweep(stub, merge=False)
    assert not computer._edits.get("block")
    assert _tool_union(computer, stub, (20.0, 0.0, 45.0), (20.0, 20.0, 10.0)) is CheckKind.asserted


# --- the allowlist / body count hooks ------------------------------------------------------------------------------


def test_sweep_is_a_body_edit_and_an_additive_one() -> None:
    from cotaforge.verification.expected import _ADDITIVE_OPS, _NON_EDITING_OPS

    assert "sweep" not in _NON_EDITING_OPS  # every op outside the allowlist records its edit
    assert "sweep" in _ADDITIVE_OPS  # features on its faces edit the body it merged into


def test_a_merged_sweep_is_not_a_new_body_but_a_separate_one_is() -> None:
    stub = _stub()
    merged = _with_sweep(stub)
    assert merged._live_body_count() == 1  # the tube grew out of the block, like a merged boss
    separate = _with_sweep(_stub(), merge=False)
    assert separate._live_body_count() == 2  # block + the swept body
