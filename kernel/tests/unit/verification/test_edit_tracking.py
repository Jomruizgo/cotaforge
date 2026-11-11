"""The analytic shape tracked per body survives feature edits that cannot matter to a later boolean, and is forgotten
(only for the bodies concerned) when the edit is of a kind or in a place the tracker cannot bound."""

import pytest

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CopyBodyRequest,
    CutRequest,
    EndCondition,
    ExtrudeRequest,
    FaceHandle,
    FeatureHandle,
    HoleRequest,
    HoleStandard,
    LinearPatternRequest,
    PlaneName,
    RevolveRequest,
    SketchHandle,
    SketchRequest,
    Transform,
)
from cotaforge.ir.ops_features import (
    CutOperation,
    ExtrudeOperation,
    HoleOperation,
    LinearPatternOperation,
    RevolveOperation,
    SketchOperation,
)
from cotaforge.ir.ops_primitives import BooleanUnionOperation, BoxOperation, CopyBodyOperation
from cotaforge.ir.refs import BodyRef, FaceRef, PlaneRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.formulas import BoxShape
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import mass, planar_face

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
BLOCK = BoundingBox(min=(0, 0, 0), max=(10, 20, 30))
TWIN = BoundingBox(min=(8, 0, 0), max=(18, 20, 30))
DOC = StateSnapshot(mass=mass(12000.0), bbox=BoundingBox(min=(0, 0, 0), max=(18, 20, 30)))


def _stub() -> StubAdapter:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(6000.0, (5, 10, 15))
    stub.bbox_by_body["body:1"] = BLOCK
    stub.mass_by_body["body:2"] = mass(6000.0, (13, 10, 15))
    stub.bbox_by_body["body:2"] = TWIN
    stub.add_face(planar_face("face:top", (0, 0, 1), (5, 10, 30), 200.0, BoundingBox(min=(0, 0, 30), max=(10, 20, 30))))
    return stub


def _scene(stub: StubAdapter) -> ExpectedComputer:
    """A 10 x 20 x 30 block and its twin 8 mm along X: they share the slab x in [8, 10], 1200 mm3."""
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="block", intent="i", size=(10, 20, 30)),
        BoxRequest(operation_id="block", size=(10, 20, 30)),
        EMPTY,
        stub,
    )
    computer.compute(
        CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"), offset=(8, 0, 0)),
        CopyBodyRequest(operation_id="twin", source=BodyHandle(id="body:1"), transform=Transform(origin=(8, 0, 0))),
        StateSnapshot(mass=mass(6000.0), bbox=BLOCK),
        stub,
    )
    return computer


def _bore(x: float) -> tuple[HoleOperation, HoleRequest]:
    """A D4 through hole on the block top face, ``x`` mm from the face centre (5, 10) along X."""
    op = HoleOperation(
        id="bore",
        intent="i",
        face=FaceRef(from_op="block"),
        position=(x, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=4.0,
        end_condition=EndCondition.through_all,
    )
    request = HoleRequest(
        operation_id="bore",
        face=FaceHandle(id="face:top"),
        position=(x, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=4.0,
        end_condition=EndCondition.through_all,
    )
    return op, request


def _union() -> tuple[BooleanUnionOperation, BooleanRequest]:
    op = BooleanUnionOperation(id="both", intent="i", target=BodyRef(from_op="block"), tools=(BodyRef(from_op="twin"),))
    request = BooleanRequest(
        operation_id="both", kind=BooleanKind.union, target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:2"),)
    )
    return op, request


def _union_delta(computer: ExpectedComputer, stub: StubAdapter) -> tuple[CheckKind, float]:
    op, request = _union()
    delta = next(e for e in computer.compute(op, request, DOC, stub) if e.name == "volume_delta")
    return delta.kind, delta.value  # type: ignore[return-value]


def test_with_no_edit_the_overlap_of_a_block_and_its_twin_is_analytic() -> None:
    stub = _stub()
    kind, value = _union_delta(_scene(stub), stub)
    assert kind is CheckKind.asserted and value == pytest.approx(-1200.0)


def test_a_hole_outside_the_overlap_keeps_the_union_analytic() -> None:
    stub = _stub()
    computer = _scene(stub)
    op, request = _bore(-3.0)  # x in [0, 4]: nowhere near the slab x in [8, 10]
    computer.compute(op, request, DOC, stub)
    assert isinstance(computer._shape_of("block"), BoxShape)
    kind, value = _union_delta(computer, stub)
    assert kind is CheckKind.asserted and value == pytest.approx(-1200.0)


def test_a_hole_inside_the_overlap_makes_the_union_reported() -> None:
    stub = _stub()
    computer = _scene(stub)
    op, request = _bore(3.0)  # x in [6, 10]: its walls cross the slab
    computer.compute(op, request, DOC, stub)
    kind, _ = _union_delta(computer, stub)
    assert kind is CheckKind.reported


def test_a_hole_in_the_source_before_the_copy_travels_with_the_copy() -> None:
    """The hole at x in [6, 10] is copied 8 mm along X to [14, 18]: outside the slab for the twin, but the block's own
    hole still crosses it, so the union stays reported."""
    stub = _stub()
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="block", intent="i", size=(10, 20, 30)),
        BoxRequest(operation_id="block", size=(10, 20, 30)),
        EMPTY,
        stub,
    )
    op, request = _bore(3.0)
    computer.compute(op, request, DOC, stub)
    computer.compute(
        CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"), offset=(8, 0, 0)),
        CopyBodyRequest(operation_id="twin", source=BodyHandle(id="body:1"), transform=Transform(origin=(8, 0, 0))),
        StateSnapshot(mass=mass(6000.0), bbox=BLOCK),
        stub,
    )
    assert computer._edits["twin"][0].min[0] == pytest.approx(14.0)
    kind, _ = _union_delta(computer, stub)
    assert kind is CheckKind.reported


def test_a_pattern_forgets_only_the_body_of_its_seed() -> None:
    stub = _stub()
    stub.faces_of_feature["feature:5"] = []
    computer = _scene(stub)
    op, request = _bore(-3.0)
    computer.compute(op, request, DOC, stub)
    pattern = LinearPatternOperation(id="row", intent="i", features=["bore"], direction="X", spacing_mm=5, count=2)
    pattern_request = LinearPatternRequest(
        operation_id="row", features=(FeatureHandle(id="feature:5"),), direction=(1, 0, 0), spacing_mm=5, count=2
    )
    computer.compute(
        pattern, pattern_request, StateSnapshot(mass=mass(5000.0), bbox=DOC.bbox, seed_volume_delta=-1.0), stub
    )
    assert computer._shape_of("block") is None  # the pattern of the block's hole is not bounded by a region
    assert isinstance(computer._shape_of("twin"), BoxShape)  # the twin was never touched


def test_a_cut_from_a_plane_sketch_edits_only_the_bodies_it_meets() -> None:
    stub = _stub()
    computer = _scene(stub)
    slot = {"kind": "rect", "name": "slot", "center": [3, 10], "width": 2, "height": 4}
    sketch = SketchOperation(id="ss", intent="i", on=PlaneRef(name=PlaneName.XY), entities=[slot])
    computer.compute(sketch, SketchRequest(operation_id="ss", on=PlaneName.XY, entities=sketch.entities), DOC, stub)
    cut = CutOperation(id="slot_cut", intent="i", sketch="ss", end_condition=EndCondition.blind, depth_mm=5.0)
    computer.compute(cut, CutRequest(operation_id="slot_cut", sketch=SketchHandle(id="sk:1"), depth_mm=5.0), DOC, stub)
    # the slot (x in [2, 4], y in [9, 11], 5 mm deep from z = 0) lies in the block and misses the slab x in [8, 10]
    assert isinstance(computer._shape_of("block"), BoxShape) and isinstance(computer._shape_of("twin"), BoxShape)
    assert len(computer._edits["block"]) == 1
    assert not computer._edits.get("twin")
    kind, value = _union_delta(computer, stub)
    assert kind is CheckKind.asserted and value == pytest.approx(-1200.0)


# --- soundness: attribution through merged bosses, plane-sketch merges, unknown ops, through-all reach ------------


def _boss_scene(stub: StubAdapter) -> ExpectedComputer:
    """The block, then a 6 x 6 boss on its top face (op ``e1``, merged into the block)."""
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="block", intent="i", size=(10, 20, 30)),
        BoxRequest(operation_id="block", size=(10, 20, 30)),
        EMPTY,
        stub,
    )
    rect = {"kind": "rect", "name": "b", "center": [0, 0], "width": 6, "height": 6}
    sketch = SketchOperation(id="s1", intent="i", on=FaceRef(from_op="block"), entities=[rect])
    computer.compute(
        sketch, SketchRequest(operation_id="s1", on=FaceHandle(id="face:top"), entities=sketch.entities), DOC, stub
    )
    boss = ExtrudeOperation(id="e1", intent="i", sketch="s1", depth_mm=5.0)
    computer.compute(boss, ExtrudeRequest(operation_id="e1", sketch=SketchHandle(id="sk:1"), depth_mm=5.0), DOC, stub)
    return computer


def test_a_hole_through_a_boss_edits_the_body_the_boss_was_merged_into() -> None:
    stub = _stub()
    stub.add_face(
        planar_face("face:boss_top", (0, 0, 1), (5, 10, 35), 36.0, BoundingBox(min=(2, 7, 35), max=(8, 13, 35)))
    )
    computer = _boss_scene(stub)
    assert isinstance(computer._shape_of("block"), BoxShape)
    bore = HoleOperation(
        id="bore",
        intent="i",
        face=FaceRef(from_op="e1"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=2.0,
        end_condition=EndCondition.through_all,
    )
    request = HoleRequest(
        operation_id="bore",
        face=FaceHandle(id="face:boss_top"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=2.0,
        end_condition=EndCondition.through_all,
    )
    computer.compute(bore, request, DOC, stub)
    assert len(computer._edits["block"]) == 2  # the boss, then the bore through it: both belong to the block
    # a cube inside the bore (x in [4, 6], y in [9, 11], z in [31, 33]) sits in the block's region: no asserted overlap
    computer.compute(
        BoxOperation(id="bb", intent="i", size=(2, 2, 2), placement={"origin": (4, 9, 31)}),
        BoxRequest(operation_id="bb", size=(2, 2, 2), transform=Transform(origin=(4, 9, 31))),
        DOC,
        stub,
    )
    stub.mass_by_body["body:3"] = mass(8.0)
    stub.bbox_by_body["body:3"] = BoundingBox(min=(4, 9, 31), max=(6, 11, 33))
    op = BooleanUnionOperation(id="u2", intent="i", target=BodyRef(from_op="block"), tools=(BodyRef(from_op="bb"),))
    request = BooleanRequest(
        operation_id="u2", kind=BooleanKind.union, target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:3"),)
    )
    delta = next(e for e in computer.compute(op, request, DOC, stub) if e.name == "volume_delta")
    assert delta.kind is CheckKind.reported


def test_a_plane_sketch_through_all_cut_reaches_a_body_far_from_the_sketch_plane() -> None:
    stub = _stub()
    computer = ExpectedComputer(Thresholds())
    far = Transform(origin=(0, 0, 100))
    computer.compute(
        BoxOperation(id="block", intent="i", size=(20, 20, 20), placement={"origin": (0, 0, 100)}),
        BoxRequest(operation_id="block", size=(20, 20, 20), transform=far),
        EMPTY,
        stub,
    )
    doc = StateSnapshot(mass=mass(8000.0), bbox=BoundingBox(min=(0, 0, 100), max=(20, 20, 120)))
    slot = {"kind": "rect", "name": "slot", "center": [10, 10], "width": 4, "height": 4}
    sketch = SketchOperation(id="ss", intent="i", on=PlaneRef(name=PlaneName.XY), entities=[slot])
    computer.compute(sketch, SketchRequest(operation_id="ss", on=PlaneName.XY, entities=sketch.entities), doc, stub)
    cut = CutOperation(
        id="drill", intent="i", sketch="ss", end_condition=EndCondition.through_all, direction="positive"
    )
    by_name = {
        e.name: e
        for e in computer.compute(
            cut,
            CutRequest(operation_id="drill", sketch=SketchHandle(id="sk:1"), end_condition=EndCondition.through_all),
            doc,
            stub,
        )
    }
    assert len(computer._edits["block"]) == 1  # the region spans from the plane (z = 0) over the whole block
    assert by_name["cut_extent_along_n"].value == pytest.approx(20.0)  # 20 mm of material, not 120


def test_an_op_without_a_row_forgets_the_bodies_it_may_have_changed() -> None:
    """A vocabulary entry the computer does not know yet (a sweep before its row exists) must not leave a stale box."""

    class Mystery:
        op = "mystery"
        id = "m"

        def refs(self) -> tuple:
            return ()

    stub = _stub()
    computer = _scene(stub)
    assert isinstance(computer._shape_of("block"), BoxShape)
    assert (
        computer.compute(
            Mystery(),
            CopyBodyRequest(operation_id="m", source=BodyHandle(id="body:1"), transform=Transform.identity()),
            DOC,
            stub,
        )
        == ()
    )  # type: ignore[arg-type]
    assert computer._shape_of("block") is None and computer._shape_of("twin") is None


def test_two_identical_tools_are_not_asserted() -> None:
    stub = _stub()
    computer = _scene(stub)
    computer.compute(
        CopyBodyOperation(id="twin2", intent="i", source=BodyRef(from_op="twin")),
        CopyBodyRequest(operation_id="twin2", source=BodyHandle(id="body:2"), transform=Transform.identity()),
        DOC,
        stub,
    )
    stub.mass_by_body["body:3"] = mass(6000.0, (13, 10, 15))
    stub.bbox_by_body["body:3"] = TWIN
    op = BooleanUnionOperation(
        id="u2", intent="i", target=BodyRef(from_op="block"), tools=(BodyRef(from_op="twin"), BodyRef(from_op="twin2"))
    )
    request = BooleanRequest(
        operation_id="u2",
        kind=BooleanKind.union,
        target=BodyHandle(id="body:1"),
        tools=(BodyHandle(id="body:2"), BodyHandle(id="body:3")),
    )
    delta = next(e for e in computer.compute(op, request, DOC, stub) if e.name == "volume_delta")
    assert delta.kind is CheckKind.reported


# --- revolve regions, patterns of bosses, the allow-list of non-editing ops -------------------------------------


def test_a_revolve_records_its_box_on_the_tracked_bodies() -> None:
    stub = _stub()
    computer = _scene(stub)
    profile = {"kind": "rect", "name": "p", "center": [50, 10], "width": 10, "height": 4}  # u 45..55, v 8..12
    sketch = SketchOperation(id="rs", intent="i", on=PlaneRef(name=PlaneName.XY), entities=[profile])
    computer.compute(sketch, SketchRequest(operation_id="rs", on=PlaneName.XY, entities=sketch.entities), DOC, stub)
    axis = Axis(origin=(0, 0, 0), direction=(1, 0, 0))
    revolve = RevolveOperation(id="rev", intent="i", sketch="rs", axis="u")
    request = RevolveRequest(operation_id="rev", sketch=SketchHandle(id="sk:1"), axis=axis)
    computer.compute(revolve, request, DOC, stub)
    for key in ("block", "twin"):  # no named host: the adapter merges the revolve into whichever body it picks
        (region,) = computer._edits[key]
        assert region.min[0] == pytest.approx(45.0) and region.max[0] == pytest.approx(55.0)
        assert region.max[1] == pytest.approx(12.0) and region.max[2] == pytest.approx(12.0)


def test_a_pattern_of_a_boss_forgets_every_tracked_body() -> None:
    stub = _stub()
    stub.faces_of_feature["feature:7"] = []
    computer = _boss_scene(stub)
    computer.compute(  # a second, unrelated body
        BoxOperation(id="other", intent="i", size=(1, 1, 1), placement={"origin": (500, 0, 0)}),
        BoxRequest(operation_id="other", size=(1, 1, 1), transform=Transform(origin=(500, 0, 0))),
        DOC,
        stub,
    )
    assert isinstance(computer._shape_of("block"), BoxShape) and isinstance(computer._shape_of("other"), BoxShape)
    pattern = LinearPatternOperation(id="row", intent="i", features=["e1"], direction="X", spacing_mm=8, count=2)
    request = LinearPatternRequest(
        operation_id="row", features=(FeatureHandle(id="feature:7"),), direction=(1, 0, 0), spacing_mm=8, count=2
    )
    computer.compute(pattern, request, StateSnapshot(mass=mass(6000.0), bbox=DOC.bbox, seed_volume_delta=1.0), stub)
    assert computer._shape_of("block") is None and computer._shape_of("other") is None


def test_an_op_outside_the_allow_list_forgets_only_the_bodies_it_names() -> None:
    class Sweepish:
        op = "sweepish"
        id = "sw"

        def refs(self) -> tuple:
            return (FaceRef(from_op="block"),)

    stub = _stub()
    computer = _scene(stub)
    request = CopyBodyRequest(operation_id="sw", source=BodyHandle(id="body:1"), transform=Transform.identity())
    assert computer.compute(Sweepish(), request, DOC, stub) == ()  # type: ignore[arg-type]
    assert computer._shape_of("block") is None and isinstance(computer._shape_of("twin"), BoxShape)


def test_the_allow_list_is_exactly_the_ops_that_leave_bodies_alone() -> None:
    from cotaforge.verification.expected import _NON_EDITING_OPS

    assert {"box", "cylinder", "boolean_union", "translate", "rotate", "copy_body", "sketch", "ref_plane"} <= (
        _NON_EDITING_OPS
    )
    assert not {"extrude", "cut", "revolve", "hole", "chamfer", "fillet", "linear_pattern", "circular_pattern"} & (
        _NON_EDITING_OPS
    )
