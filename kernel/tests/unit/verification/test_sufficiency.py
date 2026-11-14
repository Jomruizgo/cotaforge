"""Sufficiency: every detail op emits at least one asserted expectation from its row.

``sketch`` has no row in table 6.2 (a sketch changes no geometry) and is exercised in test_expected.
"""

import math
from collections.abc import Callable

import pytest

from cotaforge.contract import (
    AddJointRequest,
    Axis,
    BaseFlangeRequest,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    ChamferRequest,
    CircularPatternRequest,
    ComponentHandle,
    CopyBodyRequest,
    CutRequest,
    CylinderRequest,
    DatumAxisRequest,
    DatumPlaneRequest,
    DocumentHandle,
    DocumentKind,
    DraftRequest,
    EdgeFlangeRequest,
    EdgeHandle,
    ExtrudeRequest,
    FaceHandle,
    FeatureHandle,
    FilletRequest,
    FixComponentRequest,
    HoleRequest,
    InsertComponentRequest,
    InterferenceRequest,
    JointHandle,
    JointInfo,
    JointType,
    LinearPatternRequest,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    MirrorBodyRequest,
    MirrorRequest,
    MouldSplitRequest,
    MoveBodyRequest,
    NewDocumentRequest,
    PlaceComponentRequest,
    PlaneName,
    Request,
    RevolveRequest,
    SetJointRequest,
    ShellRequest,
    ShrinkBodyRequest,
    SketchHandle,
    SketchRequest,
    SplitRequest,
    SweepRequest,
    Transform,
    UnfoldRequest,
    frame_for_plane,
)
from cotaforge.ir.ops_features import (
    OP_NAMES,
    AddJointOperation,
    BaseFlangeOperation,
    ChamferOperation,
    CheckInterferenceOperation,
    CircularPatternOperation,
    CutOperation,
    DraftOperation,
    EdgeFlangeOperation,
    ExtrudeOperation,
    FilletOperation,
    FixComponentOperation,
    HoleOperation,
    InsertComponentOperation,
    LinearPatternOperation,
    MateCoincidentOperation,
    MateConcentricOperation,
    MateDistanceOperation,
    MirrorBodyOperation,
    MirrorOperation,
    MouldSplitOperation,
    PlaceComponentOperation,
    RefAxisOperation,
    RefPlaneOperation,
    RevolveOperation,
    SetJointOperation,
    ShellOperation,
    ShrinkBodyOperation,
    SketchOperation,
    SplitOperation,
    SweepOperation,
    UnfoldOperation,
)
from cotaforge.ir.ops_primitives import (
    BooleanIntersectOperation,
    BooleanSubtractOperation,
    BooleanUnionOperation,
    BoxOperation,
    CopyBodyOperation,
    CylinderOperation,
    OperationBase,
    RotateOperation,
    TranslateOperation,
)
from cotaforge.ir.refs import BodyRef, EdgeRef, FaceRef, PlaneRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import PLATE_BBOX, PLATE_MASS, cylindrical_face, line_edge, mass, planar_face, plate_faces

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
PLATE = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX)
RECT = {"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}
EDGE_REF = EdgeRef(between=(FaceRef(from_op="base"), FaceRef(from_op="base")))

# Distinguishing check names per op row.
ROW_NAMES: dict[str, set[str]] = {
    "ref_plane": {"datum_origin", "datum_u", "datum_normal"},
    "ref_axis": {"datum_axis_position", "datum_axis_direction"},
    "extrude": {"volume_delta", "bbox_after", "new_faces_count"},
    "cut": {"volume_delta", "bbox_after", "bbox_within", "cut_extent_along_n"},
    "revolve": {"volume_delta", "new_faces_count", "bbox_after"},
    "sweep": {"volume_delta", "new_faces_count", "bbox_contains"},
    "hole": {"hole_axis_direction", "hole_axis_position", "hole_radius", "volume_delta", "cut_extent_along_n"},
    "linear_pattern": {"instance_count", "created_faces_count", "volume_delta"},
    "circular_pattern": {"instance_count", "created_faces_count", "volume_delta"},
    "chamfer": {"volume_delta", "created_faces_count", "centroid_after"},
    "fillet": {"volume_delta", "created_faces_count", "fillet_radius"},
    "shell": {"volume_sign", "bbox_within"},
    "draft": {"draft_angle"},
    "mirror": {"instance_count", "created_faces_count", "volume_delta"},
    "base_flange": {"volume_delta", "bbox_after"},
    "edge_flange": {"bbox_contains", "volume_delta"},
    "unfold": {"flat_length_mm", "flat_width_mm", "flat_thickness_mm", "flat_pattern_valid"},
    "box": {"volume_delta", "bbox_after", "new_faces_count"},
    "cylinder": {"volume_delta", "bbox_after", "cylinder_axis_direction", "cylinder_radius"},
    "boolean_union": {"volume_delta", "bbox_after"},
    "boolean_subtract": {"volume_delta", "bbox_within"},
    "boolean_intersect": {"volume_delta", "bbox_within"},
    "translate": {"volume_delta", "bbox_after"},
    "rotate": {"volume_delta", "bbox_after"},
    "copy_body": {"volume_delta", "copy_centroid", "source_centroid", "copy_bbox"},
    "mirror_body": {"volume_delta", "copy_centroid", "source_centroid", "copy_bbox"},
    "shrink_body": {"volume_delta", "copy_centroid", "source_centroid", "copy_bbox"},
    "mould_split": {"volume_delta"},
    "split": {"volume_delta", "split_volume_sum", "split_centroid", "split_sides"},
    "insert_component": {"volume_delta", "component_bbox", "component_transform"},
    "fix_component": {"volume_delta", "component_transform"},
    "place_component": {"volume_delta", "component_transform"},
    "add_joint": {"volume_delta", "component_transform"},
    "set_joint": {"volume_delta", "component_transform"},
    "mate_concentric": {"volume_delta", "component_bbox"},
    "mate_coincident": {"volume_delta", "component_bbox"},
    "mate_distance": {"volume_delta", "component_bbox"},
    "check_interference": {"interference_total", "assembly_volume"},
}

Case = tuple[ExpectedComputer, StubAdapter, OperationBase, Request, StateSnapshot]


def _with_plate_sketch(stub: StubAdapter) -> ExpectedComputer:
    computer = ExpectedComputer(Thresholds())
    sketch = SketchOperation(id="base_sketch", intent="i", on=PlaneRef(name="XY"), entities=[RECT])
    computer.compute(
        sketch, SketchRequest(operation_id="base_sketch", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub
    )
    return computer


def _plate_stub() -> StubAdapter:
    stub = StubAdapter()
    for face in plate_faces():
        stub.add_face(face, feature_id="feature:1")
    stub.add_edge(line_edge("edge:1", ("face:top", "face:xmax"), (100.0, 0.0, 8.0), (100.0, 60.0, 8.0)))
    return stub


def _ref_plane() -> Case:
    op = RefPlaneOperation(
        id="dp", intent="i", define={"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 5.0}
    )
    request = DatumPlaneRequest(operation_id="dp", how="offset", frame=frame_for_plane(PlaneName.XY))
    return ExpectedComputer(Thresholds()), StubAdapter(), op, request, EMPTY


def _ref_axis() -> Case:
    op = RefAxisOperation(id="da", intent="i", define={"how": "two_points", "points": ((0, 0, 0), (0, 0, 5))})
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    return (
        ExpectedComputer(Thresholds()),
        StubAdapter(),
        op,
        DatumAxisRequest(operation_id="da", how="two_points", axis=axis),
        EMPTY,
    )


def _extrude() -> Case:
    stub = StubAdapter()
    return (
        _with_plate_sketch(stub),
        stub,
        ExtrudeOperation(id="base", intent="i", sketch="base_sketch", depth_mm=8.0),
        ExtrudeRequest(operation_id="base", sketch=SketchHandle(id="s"), depth_mm=8.0),
        EMPTY,
    )


def _cut() -> Case:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    slot = {"kind": "rect", "name": "slot", "center": [50, 30], "width": 10, "height": 4}
    sketch = SketchOperation(id="ss", intent="i", on=PlaneRef(name="XY"), entities=[slot])
    computer.compute(sketch, SketchRequest(operation_id="ss", on=PlaneName.XY, entities=sketch.entities), PLATE, stub)
    return (
        computer,
        stub,
        CutOperation(id="c", intent="i", sketch="ss", end_condition="through_all", direction="positive"),
        CutRequest(operation_id="c", sketch=SketchHandle(id="s"), end_condition="through_all", direction="positive"),
        PLATE,
    )


def _revolve() -> Case:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    rect = {"kind": "rect", "name": "r", "center": [15, 15], "width": 30, "height": 10}
    sketch = SketchOperation(id="rs", intent="i", on=PlaneRef(name="XY"), entities=[rect])
    computer.compute(sketch, SketchRequest(operation_id="rs", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub)
    axis = Axis(origin=(0, 0, 0), direction=(1, 0, 0))
    return (
        computer,
        stub,
        RevolveOperation(id="rev", intent="i", sketch="rs", axis="u"),
        RevolveRequest(operation_id="rev", sketch=SketchHandle(id="s"), axis=axis),
        EMPTY,
    )


def _sweep() -> Case:
    stub = StubAdapter()
    computer = ExpectedComputer(Thresholds())
    circle = {"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}
    sketch = SketchOperation(id="ws", intent="i", on=PlaneRef(name="XY"), entities=[circle])
    computer.compute(sketch, SketchRequest(operation_id="ws", on=PlaneName.XY, entities=sketch.entities), EMPTY, stub)
    path = ((0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0))
    return (
        computer,
        stub,
        SweepOperation(id="sw", intent="i", sketch="ws", path={"points": path}),
        SweepRequest(operation_id="sw", sketch=SketchHandle(id="s"), path=path),
        EMPTY,
    )


def _sheet() -> tuple[ExpectedComputer, StubAdapter]:
    """A 100 x 60 sheet of 2 mm already recorded by a base flange, and a stub that knows its top edge at y=0."""
    stub = StubAdapter()
    computer = _with_plate_sketch(stub)
    computer.compute(
        BaseFlangeOperation(id="sheet", intent="i", sketch="base_sketch", thickness_mm=2.0),
        BaseFlangeRequest(operation_id="sheet", sketch=SketchHandle(id="s"), thickness_mm=2.0),
        EMPTY,
        stub,
    )
    stub.add_edge(line_edge("edge:1", ("face:top", "face:side"), (0.0, 0.0, 2.0), (100.0, 0.0, 2.0)))
    return computer, stub


SHEET = StateSnapshot(
    mass=mass(12000.0, (50, 30, 1)), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 2))
)  # the sheet after its base flange
SHEET_EDGE = EdgeRef(between=(FaceRef(from_op="sheet"), FaceRef(from_op="sheet")))


def _base_flange() -> Case:
    stub = StubAdapter()
    return (
        _with_plate_sketch(stub),
        stub,
        BaseFlangeOperation(id="sheet", intent="i", sketch="base_sketch", thickness_mm=2.0),
        BaseFlangeRequest(operation_id="sheet", sketch=SketchHandle(id="s"), thickness_mm=2.0),
        EMPTY,
    )


def _edge_flange() -> Case:
    computer, stub = _sheet()
    op = EdgeFlangeOperation(id="lip", intent="i", edge=SHEET_EDGE, length_mm=20.0, bend_radius_mm=3.0)
    request = EdgeFlangeRequest(
        operation_id="lip", edge=EdgeHandle(id="edge:1"), length_mm=20.0, angle_deg=90.0, bend_radius_mm=3.0
    )
    return computer, stub, op, request, SHEET


def _unfold() -> Case:
    computer, stub = _sheet()
    lip = EdgeFlangeOperation(id="lip", intent="i", edge=SHEET_EDGE, length_mm=20.0, bend_radius_mm=3.0)
    request = EdgeFlangeRequest(
        operation_id="lip", edge=EdgeHandle(id="edge:1"), length_mm=20.0, angle_deg=90.0, bend_radius_mm=3.0
    )
    computer.compute(lip, request, SHEET, stub)
    op = UnfoldOperation(id="flat", intent="i", face=FaceRef(from_op="sheet"), k_factor=0.4)
    return computer, stub, op, UnfoldRequest(operation_id="flat", face=FaceHandle(id="face:top"), k_factor=0.4), SHEET


def _hole() -> Case:
    stub = _plate_stub()
    op = HoleOperation(
        id="hole_1",
        intent="i",
        face=FaceRef(from_op="base"),
        position=(-40, -20),
        standard="simple",
        diameter_mm=6.6,
        end_condition="through_all",
    )
    request = HoleRequest(
        operation_id="hole_1",
        face=FaceHandle(id="face:top"),
        position=(-40, -20),
        standard="simple",
        diameter_mm=6.6,
        end_condition="through_all",
    )
    return ExpectedComputer(Thresholds()), stub, op, request, PLATE


def _seed_stub() -> StubAdapter:
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
    return stub


def _linear_pattern() -> Case:
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=-math.pi * 3.3**2 * 8.0)
    op = LinearPatternOperation(id="holes", intent="i", features=["hole_1"], direction="X", spacing_mm=80, count=2)
    request = LinearPatternRequest(
        operation_id="holes", features=(FeatureHandle(id="feature:2"),), direction=(1, 0, 0), spacing_mm=80, count=2
    )
    return ExpectedComputer(Thresholds()), _seed_stub(), op, request, before


def _circular_pattern() -> Case:
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=-math.pi * 3.3**2 * 8.0)
    axis = Axis(origin=(50, 30, 0), direction=(0, 0, 1))
    op = CircularPatternOperation(id="holes", intent="i", features=["hole_1"], axis=axis, count=4)
    request = CircularPatternRequest(
        operation_id="holes", features=(FeatureHandle(id="feature:2"),), axis=axis, count=4
    )
    return ExpectedComputer(Thresholds()), _seed_stub(), op, request, before


def _mirror() -> Case:
    before = StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX, seed_volume_delta=-math.pi * 3.3**2 * 8.0)
    op = MirrorOperation(id="mi", intent="i", features=["hole_1"], plane=PlaneRef(name="YZ"))
    plane = frame_for_plane(PlaneName.YZ).model_copy(update={"origin": (50.0, 0.0, 0.0)})
    request = MirrorRequest(operation_id="mi", features=(FeatureHandle(id="feature:2"),), plane=plane)
    return ExpectedComputer(Thresholds()), _seed_stub(), op, request, before


def _chamfer() -> Case:
    op = ChamferOperation(
        id="ch", intent="i", edges=[EDGE_REF], distance1_mm=4.0, distance2_mm=2.0, distance1_on=FaceRef(from_op="base")
    )
    request = ChamferRequest(
        operation_id="ch",
        edges=(EdgeHandle(id="edge:1"),),
        distance1_mm=4.0,
        distance2_mm=2.0,
        distance1_face=FaceHandle(id="face:top"),
    )
    return ExpectedComputer(Thresholds()), _plate_stub(), op, request, PLATE


def _fillet() -> Case:
    op = FilletOperation(id="fi", intent="i", edges=[EDGE_REF], radius_mm=2.0)
    request = FilletRequest(operation_id="fi", edges=(EdgeHandle(id="edge:1"),), radius_mm=2.0)
    return ExpectedComputer(Thresholds()), _plate_stub(), op, request, PLATE


def _shell() -> Case:
    op = ShellOperation(id="sh", intent="i", faces=[FaceRef(from_op="base")], thickness_mm=2.0)
    request = ShellRequest(operation_id="sh", faces=(FaceHandle(id="face:top"),), thickness_mm=2.0)
    return ExpectedComputer(Thresholds()), _plate_stub(), op, request, PLATE


def _draft() -> Case:
    op = DraftOperation(
        id="dr",
        intent="i",
        faces=[FaceRef(from_op="base")],
        neutral=PlaneRef(name="XY"),
        pull=(0.0, 0.0, 1.0),
        angle_deg=3.0,
    )
    request = DraftRequest(
        operation_id="dr",
        faces=(FaceHandle(id="face:xmax"),),
        neutral=frame_for_plane(PlaneName.XY),
        pull=(0.0, 0.0, 1.0),
        angle_deg=3.0,
    )
    return ExpectedComputer(Thresholds()), _plate_stub(), op, request, PLATE


def _box() -> Case:
    return (
        ExpectedComputer(Thresholds()),
        StubAdapter(),
        BoxOperation(id="b", intent="i", size=(100, 60, 20)),
        BoxRequest(operation_id="b", size=(100, 60, 20)),
        EMPTY,
    )


def _cylinder() -> Case:
    op = CylinderOperation(id="c", intent="i", diameter_mm=30, height_mm=20)
    request = CylinderRequest(
        operation_id="c", diameter_mm=30, height_mm=20, axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1))
    )
    return ExpectedComputer(Thresholds()), StubAdapter(), op, request, EMPTY


def _concept_stub() -> tuple[ExpectedComputer, StubAdapter, StateSnapshot]:
    stub = StubAdapter()
    stub.mass_by_body["body:1"] = mass(120000.0, (50, 30, 10))
    stub.mass_by_body["body:2"] = mass(math.pi * 225.0 * 20.0)
    stub.bbox_by_body["body:1"] = BoundingBox(min=(0, 0, 0), max=(100, 60, 20))
    stub.bbox_by_body["body:2"] = BoundingBox(min=(35, 15, 0), max=(65, 45, 20))
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        BoxOperation(id="block", intent="i", size=(100, 60, 20)),
        BoxRequest(operation_id="block", size=(100, 60, 20)),
        EMPTY,
        stub,
    )
    cyl = CylinderOperation(id="bore", intent="i", diameter_mm=30, height_mm=20, origin=(50, 30, 0))
    computer.compute(
        cyl,
        CylinderRequest(
            operation_id="bore", diameter_mm=30, height_mm=20, axis=Axis(origin=(50, 30, 0), direction=(0, 0, 1))
        ),
        EMPTY,
        stub,
    )
    both = StateSnapshot(mass=mass(120000.0 + math.pi * 4500.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    return computer, stub, both


def _boolean(kind: str) -> Case:
    computer, stub, both = _concept_stub()
    cls = {
        "union": BooleanUnionOperation,
        "subtract": BooleanSubtractOperation,
        "intersect": BooleanIntersectOperation,
    }[kind]
    op = cls(id="bool", intent="i", target=BodyRef(from_op="block"), tools=[BodyRef(from_op="bore")])
    request = BooleanRequest(
        operation_id="bool", kind=BooleanKind(kind), target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:2"),)
    )
    return computer, stub, op, request, both


def _translate() -> Case:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(120000.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = TranslateOperation(id="mv", intent="i", target=BodyRef(from_op="block"), offset=(10, 0, 0))
    request = MoveBodyRequest(operation_id="mv", body=BodyHandle(id="body:1"), transform=Transform(origin=(10, 0, 0)))
    return computer, stub, op, request, block


def _rotate() -> Case:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(120000.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = RotateOperation(id="rot", intent="i", target=BodyRef(from_op="block"), axis="Z", angle_deg=90.0)
    request = MoveBodyRequest(
        operation_id="rot", body=BodyHandle(id="body:1"), transform=Transform.from_axis_angle((0, 0, 1), 90.0)
    )
    return computer, stub, op, request, block


def _copy_body() -> Case:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(120000.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = CopyBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"), offset=(0, 100, 0))
    request = CopyBodyRequest(
        operation_id="twin", source=BodyHandle(id="body:1"), transform=Transform(origin=(0, 100, 0))
    )
    return computer, stub, op, request, block


def _mirror_body() -> Case:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(120000.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = MirrorBodyOperation(id="twin", intent="i", source=BodyRef(from_op="block"), plane=PlaneRef(name="YZ"))
    plane = frame_for_plane(PlaneName.YZ).model_copy(update={"origin": (120.0, 0.0, 0.0)})
    request = MirrorBodyRequest(operation_id="twin", source=BodyHandle(id="body:1"), plane=plane)
    return computer, stub, op, request, block


def _shrink_body() -> Case:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(120000.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = ShrinkBodyOperation(
        id="cavity", intent="i", source=BodyRef(from_op="block"), shrinkage_percent=0.6, factor_source="datasheet"
    )
    request = ShrinkBodyRequest(operation_id="cavity", source=BodyHandle(id="body:1"), scale=(1.006, 1.006, 1.006))
    return computer, stub, op, request, block


def _mould_split() -> Case:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(120000.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = MouldSplitOperation(id="inserts", intent="i", source=BodyRef(from_op="block"), pull="Z", margin_mm=10)
    request = MouldSplitRequest(
        operation_id="inserts", source=BodyHandle(id="body:1"), pull=(0, 0, 1), margin_mm=10.0, slides="none"
    )
    return computer, stub, op, request, block


def _split() -> Case:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(120000.0), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = SplitOperation(id="halves", intent="i", source=BodyRef(from_op="block"), plane=PlaneRef(name="XY"))
    plane = frame_for_plane(PlaneName.XY).model_copy(update={"origin": (0.0, 0.0, 5.0)})
    request = SplitRequest(operation_id="halves", source=BodyHandle(id="body:1"), plane=plane)
    return computer, stub, op, request, block


def _insert_component() -> Case:
    stub = StubAdapter()
    stub.new_document(NewDocumentRequest(operation_id="x", kind=DocumentKind.assembly, name="asm"))
    stub.mass_by_document["doc:part"] = mass(48000.0, (50, 30, 4))
    stub.bbox_by_document["doc:part"] = PLATE_BBOX
    op = InsertComponentOperation(id="ins", intent="i", part="plate", placement={})
    request = InsertComponentRequest(
        operation_id="ins", part=DocumentHandle(id="doc:part"), transform=Transform.identity()
    )
    return ExpectedComputer(Thresholds()), stub, op, request, EMPTY


def _fix_component() -> Case:
    stub = StubAdapter()
    op = FixComponentOperation(id="fix", intent="i", component="ins")
    return (
        ExpectedComputer(Thresholds()),
        stub,
        op,
        FixComponentRequest(operation_id="fix", component=ComponentHandle(id="comp:1")),
        EMPTY,
    )


def _place_component() -> Case:
    stub = StubAdapter()
    op = PlaceComponentOperation(id="place", intent="i", component="ins", placement={"origin": [10, 0, 0]})
    request = PlaceComponentRequest(
        operation_id="place", component=ComponentHandle(id="comp:1"), transform=Transform(origin=(10, 0, 0))
    )
    return ExpectedComputer(Thresholds()), stub, op, request, EMPTY


HINGE = Axis(origin=(0, 0, 10), direction=(0, 1, 0))


def _joint_stub(value: float = 0.0) -> StubAdapter:
    stub = StubAdapter()
    stub.component_transforms["comp:2"] = Transform(origin=(0, 0, 10))
    stub.joints["joint:1"] = JointInfo(
        handle=JointHandle(id="joint:1"),
        joint_type=JointType.revolute,
        b=ComponentHandle(id="comp:2"),
        axis=HINGE,
        value=value,
    )
    return stub


def _add_joint() -> Case:
    stub = _joint_stub()
    op = AddJointOperation(id="hinge", intent="i", type="revolute", b="lid", axis=HINGE)
    request = AddJointRequest(
        operation_id="hinge", joint_type=JointType.revolute, b=ComponentHandle(id="comp:2"), axis=HINGE
    )
    return ExpectedComputer(Thresholds()), stub, op, request, EMPTY


def _set_joint() -> Case:
    stub = _joint_stub()
    op = SetJointOperation(id="open", intent="i", joint="hinge", value=30)
    request = SetJointRequest(operation_id="open", joint=JointHandle(id="joint:1"), value=30)
    return ExpectedComputer(Thresholds()), stub, op, request, EMPTY


def test_set_joint_expects_the_component_where_the_joint_motion_takes_it() -> None:
    """The expected pose is computed from the joint's axis and its present value, not read back from the adapter."""
    stub = _joint_stub(value=10.0)
    computer, _, op, request, before = _set_joint()
    (volume, pose) = computer.compute(op, request, before, stub)
    assert volume.name == "volume_delta"
    expected = pose.value
    assert isinstance(expected, Transform)
    # 20 more degrees about +Y through (0, 0, 10) from a lid whose origin sits on the axis: origin stays, x turns
    assert expected.origin == pytest.approx((0, 0, 10), abs=1e-9)
    turned = expected.apply_direction((1, 0, 0))
    assert turned == pytest.approx((math.cos(math.radians(20)), 0, -math.sin(math.radians(20))), abs=1e-9)


def _mate_stub() -> tuple[StubAdapter, StateSnapshot]:
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:a", Axis(origin=(50, 30, 0), direction=(0, 0, 1)), 3.25, (50, 30, 10), PLATE_BBOX, component="comp:1"
        )
    )
    stub.add_face(
        cylindrical_face(
            "face:b", Axis(origin=(200, 0, 0), direction=(0, 0, -1)), 3.0, (200, 0, 20), PLATE_BBOX, component="comp:2"
        )
    )
    stub.add_face(planar_face("face:pa", (0, 0, 1), (50, 30, 20), 6000.0, PLATE_BBOX, component="comp:1"))
    stub.add_face(planar_face("face:pb", (0, 0, -1), (200, 0, 5), 28.0, PLATE_BBOX, component="comp:2"))
    before = StateSnapshot(
        mass=PLATE_MASS, bbox=PLATE_BBOX, component_bbox_before=BoundingBox(min=(197, -3, 5), max=(203, 3, 45))
    )
    return stub, before


def _mate_concentric() -> Case:
    stub, before = _mate_stub()
    op = MateConcentricOperation(id="m", intent="i", a=FaceRef(from_op="x"), b=FaceRef(from_op="y"))
    return (
        ExpectedComputer(Thresholds()),
        stub,
        op,
        MateConcentricRequest(operation_id="m", a=FaceHandle(id="face:a"), b=FaceHandle(id="face:b")),
        before,
    )


def _mate_coincident() -> Case:
    stub, before = _mate_stub()
    op = MateCoincidentOperation(id="m", intent="i", a=FaceRef(from_op="x"), b=FaceRef(from_op="y"))
    return (
        ExpectedComputer(Thresholds()),
        stub,
        op,
        MateCoincidentRequest(operation_id="m", a=FaceHandle(id="face:pa"), b=FaceHandle(id="face:pb")),
        before,
    )


def _mate_distance() -> Case:
    stub, before = _mate_stub()
    op = MateDistanceOperation(id="m", intent="i", a=FaceRef(from_op="x"), b=FaceRef(from_op="y"), distance_mm=2.0)
    request = MateDistanceRequest(
        operation_id="m", a=FaceHandle(id="face:pa"), b=FaceHandle(id="face:pb"), distance_mm=2.0
    )
    return ExpectedComputer(Thresholds()), stub, op, request, before


def _check_interference() -> Case:
    computer, stub, op, request, before = _insert_component()
    computer.compute(op, request, before, stub)
    return (
        computer,
        stub,
        CheckInterferenceOperation(id="chk", intent="i"),
        InterferenceRequest(operation_id="chk"),
        EMPTY,
    )


CASES: dict[str, Callable[[], Case]] = {
    "ref_plane": _ref_plane,
    "ref_axis": _ref_axis,
    "extrude": _extrude,
    "cut": _cut,
    "revolve": _revolve,
    "sweep": _sweep,
    "hole": _hole,
    "linear_pattern": _linear_pattern,
    "circular_pattern": _circular_pattern,
    "chamfer": _chamfer,
    "fillet": _fillet,
    "shell": _shell,
    "draft": _draft,
    "mirror": _mirror,
    "base_flange": _base_flange,
    "edge_flange": _edge_flange,
    "unfold": _unfold,
    "box": _box,
    "cylinder": _cylinder,
    "boolean_union": lambda: _boolean("union"),
    "boolean_subtract": lambda: _boolean("subtract"),
    "boolean_intersect": lambda: _boolean("intersect"),
    "translate": _translate,
    "rotate": _rotate,
    "copy_body": _copy_body,
    "mirror_body": _mirror_body,
    "shrink_body": _shrink_body,
    "mould_split": _mould_split,
    "split": _split,
    "insert_component": _insert_component,
    "fix_component": _fix_component,
    "place_component": _place_component,
    "add_joint": _add_joint,
    "set_joint": _set_joint,
    "mate_concentric": _mate_concentric,
    "mate_coincident": _mate_coincident,
    "mate_distance": _mate_distance,
    "check_interference": _check_interference,
}


def test_every_detail_op_has_a_canonical_case() -> None:
    assert set(CASES) == OP_NAMES - {"sketch"}
    assert set(ROW_NAMES) == set(CASES)


@pytest.mark.parametrize("op_name", sorted(OP_NAMES - {"sketch"}))
def test_op_emits_an_asserted_expectation_from_its_row(op_name: str) -> None:
    computer, stub, op, request, before = CASES[op_name]()
    assert op.op == op_name
    expectations = computer.compute(op, request, before, stub)
    asserted = {e.name for e in expectations if e.kind == CheckKind.asserted}
    assert asserted & ROW_NAMES[op_name], f"{op_name}: asserted={asserted}"


@pytest.mark.parametrize("end_condition", ["blind", "through_all"])
def test_a_hole_on_a_cylindrical_face_still_emits_asserted_expectations_from_the_hole_row(end_condition: str) -> None:
    """The radial hole has no exact volume (reported), so its asserted rows are the axis, the radius and the bound."""
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:tube",
            Axis(origin=(0, 0, 50), direction=(0, 0, 1)),
            20.0,
            (0, 0, 50),
            BoundingBox(min=(-20, -20, 0), max=(20, 20, 100)),
        )
    )
    depth = 8.0 if end_condition == "blind" else None
    op = HoleOperation(
        id="cross",
        intent="i",
        face=FaceRef(from_op="tube"),
        position=(0, 0),
        standard="simple",
        diameter_mm=6.0,
        end_condition=end_condition,
        depth_mm=depth,
    )
    request = HoleRequest(
        operation_id="cross",
        face=FaceHandle(id="face:tube"),
        position=(0, 0),
        standard="simple",
        diameter_mm=6.0,
        end_condition=end_condition,
        depth_mm=depth,
    )
    before = StateSnapshot(mass=mass(125663.7), bbox=BoundingBox(min=(-20, -20, 0), max=(20, 20, 100)))
    expectations = ExpectedComputer(Thresholds()).compute(op, request, before, stub)
    asserted = {e.name for e in expectations if e.kind == CheckKind.asserted}
    assert {"hole_axis_direction", "hole_axis_position", "hole_radius", "cut_volume_bound"} <= asserted
    assert "volume_delta" not in asserted  # the kernel's cylinder-cylinder volumes are ~1e-3 off
