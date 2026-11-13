"""build_request / DISPATCH: every row of the dispatch table."""

import pytest

from cotaforge.contract import (
    Axis,
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
    DocumentHandle,
    DrillPoint,
    EdgeHandle,
    ExtrudeRequest,
    FaceHandle,
    FeatureHandle,
    FilletRequest,
    FixComponentRequest,
    HoleRequest,
    HoleThread,
    InsertComponentRequest,
    InterferenceRequest,
    LinearPatternRequest,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    MoveBodyRequest,
    PlaneName,
    RevolveRequest,
    SketchHandle,
    SketchRequest,
    SurfaceType,
    Transform,
    frame_for_plane,
)
from cotaforge.executor.build import DISPATCH, NON_FEATURE_OPS, build_request, placement_transform
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir import OP_NAMES, parse_plan
from cotaforge.ir.ops_features import RevolveOperation
from cotaforge.ir.ops_primitives import BoxOperation, CylinderOperation, Placement
from cotaforge.ir.plan import Plan
from cotaforge.verification.thresholds import Thresholds
from tests.unit.verification.helpers import cylindrical_face, line_edge, plate_faces

from .stub import StubAdapter

RECT = {"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
XMAX = {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}}


def _plan(ops: list[dict], kind: str = "part") -> Plan:
    return parse_plan({"id": "p", "kind": kind, "operations": ops})


def _env() -> tuple[StubAdapter, ExecutionContext, Resolver]:
    stub = StubAdapter()
    for face in plate_faces("feature:1"):
        stub.add_face(face, feature_id="feature:1")
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
    stub.add_edge(line_edge("edge:1", ("face:top", "face:xmax"), (100, 0, 8), (100, 60, 8)))
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"), documents={"plate": DocumentHandle(id="doc:plate")})
    ctx.sketch_by_op["base_sketch"] = SketchHandle(id="sk:1")
    ctx.sketch_frame_by_op["base_sketch"] = frame_for_plane(PlaneName.XY)
    ctx.feature_by_op["base"] = FeatureHandle(id="feature:1")
    ctx.feature_by_op["hole_1"] = FeatureHandle(id="feature:2")
    ctx.body_by_op["base"] = BodyHandle(id="body:1")
    ctx.body_by_op["tool"] = BodyHandle(id="body:2")
    ctx.component_by_op["ins"] = ComponentHandle(id="comp:1")
    return stub, ctx, Resolver(stub, ctx, Thresholds())


def test_dispatch_covers_the_whole_detail_vocabulary() -> None:
    assert set(DISPATCH) == OP_NAMES
    assert NON_FEATURE_OPS == {"sketch", "ref_plane", "ref_axis", "check_interference"}
    assert DISPATCH["boolean_subtract"][0] == "boolean"
    assert DISPATCH["rotate"][0] == "move_body"
    assert DISPATCH["copy_body"][0] == "copy_body"


def _copy_op(**extra: object) -> dict:
    return {"id": "twin", "op": "copy_body", "intent": "i", "source": {"kind": "body", "from_op": "base"}, **extra}


def test_copy_body_builds_a_translate_only_request() -> None:
    _, ctx, resolver = _env()
    plan = _plan([{"id": "base", "op": "box", "intent": "i", "size": [10, 10, 10]}, _copy_op(offset=[0, 100, 0])])
    request = build_request(plan.operations[1], ctx, resolver)
    assert request == CopyBodyRequest(
        operation_id="twin", source=BodyHandle(id="body:1"), transform=Transform(origin=(0, 100, 0))
    )


def test_copy_body_defaults_to_the_identity_transform() -> None:
    _, ctx, resolver = _env()
    plan = _plan([{"id": "base", "op": "box", "intent": "i", "size": [10, 10, 10]}, _copy_op()])
    request = build_request(plan.operations[1], ctx, resolver)
    assert isinstance(request, CopyBodyRequest)
    assert request.transform == Transform.identity()


def test_copy_body_rotates_about_the_point_first_then_offsets() -> None:
    _, ctx, resolver = _env()
    plan = _plan(
        [
            {"id": "base", "op": "box", "intent": "i", "size": [10, 10, 10]},
            _copy_op(offset=[0, 100, 0], rotate={"axis": "Z", "angle_deg": 90, "about": [10, 0, 0]}),
        ]
    )
    request = build_request(plan.operations[1], ctx, resolver)
    assert isinstance(request, CopyBodyRequest)
    # the pivot (10, 0, 0) stays put under the rotation, then the offset moves it: (10, 100, 0)
    assert request.transform.apply((10, 0, 0)) == pytest.approx((10, 100, 0))
    # (20, 0, 0) turns to (10, 10, 0) about the pivot, then +100 in Y
    assert request.transform.apply((20, 0, 0)) == pytest.approx((10, 110, 0))


def test_copy_body_of_an_unknown_body_is_a_resolution_error_on_source() -> None:
    _, ctx, resolver = _env()
    ghost = {"kind": "body", "from_op": "ghost"}
    plan = _plan(
        [{"id": "ghost", "op": "box", "intent": "i", "size": [1, 1, 1]}, _copy_op(source=ghost)]  # not built in ctx
    )
    with pytest.raises(ResolutionError) as info:
        build_request(plan.operations[1], ctx, resolver)
    assert info.value.path == "source"


def test_sketch_on_plane_and_on_face() -> None:
    _, ctx, resolver = _env()
    plan = _plan(
        [
            {"id": "s", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XZ"}, "entities": [RECT]},
            {"id": "b", "op": "extrude", "intent": "i", "sketch": "s", "depth_mm": 8},
            {
                "id": "s2",
                "op": "sketch",
                "intent": "i",
                "on": {"kind": "face", "from_op": "b", "select": {"normal": [0, 0, 1]}},
                "entities": [RECT],
            },
        ]
    )
    request = build_request(plan.operations[0], ctx, resolver)
    assert isinstance(request, SketchRequest)
    assert request.on == PlaneName.XZ
    assert request.operation_id == "s"
    assert len(request.entities) == 1
    ctx.feature_by_op["b"] = FeatureHandle(id="feature:1")
    request = build_request(plan.operations[2], ctx, resolver)
    assert request.on == FaceHandle(id="face:top")  # type: ignore[union-attr]
    assert resolver.resolved_refs["on"] == "face:top"


def test_extrude_cut_and_revolve() -> None:
    _, ctx, resolver = _env()
    ops = [
        {"id": "base_sketch", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XY"}, "entities": [RECT]},
        {
            "id": "e",
            "op": "extrude",
            "intent": "i",
            "sketch": "base_sketch",
            "depth_mm": 8,
            "direction": "negative",
            "both_directions": True,
            "depth2_mm": 2,
            "merge": False,
        },
        {
            "id": "c",
            "op": "cut",
            "intent": "i",
            "sketch": "base_sketch",
            "end_condition": "through_all",
            "both_directions": True,
        },
        {"id": "r", "op": "revolve", "intent": "i", "sketch": "base_sketch", "axis": "v", "angle_deg": 180},
    ]
    plan = _plan(ops)
    extrude = build_request(plan.operations[1], ctx, resolver)
    assert extrude == ExtrudeRequest(
        operation_id="e",
        sketch=SketchHandle(id="sk:1"),
        depth_mm=8,
        direction="negative",
        both_directions=True,
        depth2_mm=2,
        merge=False,
    )
    cut = build_request(plan.operations[2], ctx, resolver)
    assert cut == CutRequest(
        operation_id="c", sketch=SketchHandle(id="sk:1"), end_condition="through_all", both_directions=True
    )
    revolve = build_request(plan.operations[3], ctx, resolver)
    assert isinstance(revolve, RevolveRequest)
    assert revolve.axis == Axis(origin=(0, 0, 0), direction=(0, 1, 0))
    assert revolve.angle_deg == 180


def test_revolve_about_a_sketch_line_entity() -> None:
    _, ctx, resolver = _env()
    lines = [
        {"kind": "line", "name": "axis_line", "start": [0, 0], "end": [30, 0]},
        {"kind": "line", "name": "l2", "start": [30, 0], "end": [30, 10]},
        {"kind": "line", "name": "l3", "start": [30, 10], "end": [0, 10]},
        {"kind": "line", "name": "l4", "start": [0, 10], "end": [0, 0]},
    ]
    plan = _plan(
        [
            {
                "id": "base_sketch",
                "op": "sketch",
                "intent": "i",
                "on": {"kind": "plane", "name": "XZ"},
                "entities": lines,
            },
            {"id": "r", "op": "revolve", "intent": "i", "sketch": "base_sketch", "axis": "axis_line"},
        ]
    )
    ctx.sketch_frame_by_op["base_sketch"] = frame_for_plane(PlaneName.XZ)
    ctx.sketch_entities_by_op["base_sketch"] = plan.operations[0].entities  # type: ignore[union-attr]
    revolve = build_request(plan.operations[1], ctx, resolver)
    assert isinstance(revolve, RevolveRequest)
    assert revolve.axis.origin == (0, 0, 0)
    assert revolve.axis.direction == pytest.approx((1, 0, 0))
    del ctx.sketch_entities_by_op["base_sketch"]
    with pytest.raises(ResolutionError):
        build_request(plan.operations[1], ctx, resolver)
    ctx.sketch_entities_by_op["base_sketch"] = plan.operations[0].entities  # type: ignore[union-attr]
    bad = RevolveOperation(id="r", intent="i", sketch="base_sketch", axis="l9")
    with pytest.raises(ResolutionError):
        build_request(bad, ctx, resolver)


def test_missing_sketch_handle_is_a_resolution_error() -> None:
    _, ctx, resolver = _env()
    del ctx.sketch_by_op["base_sketch"]
    plan = _plan(
        [
            {
                "id": "base_sketch",
                "op": "sketch",
                "intent": "i",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [RECT],
            },
            {"id": "e", "op": "extrude", "intent": "i", "sketch": "base_sketch", "depth_mm": 8},
        ]
    )
    with pytest.raises(ResolutionError):
        build_request(plan.operations[1], ctx, resolver)


def test_hole_and_patterns() -> None:
    _, ctx, resolver = _env()
    ops = [
        {"id": "base_sketch", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XY"}, "entities": [RECT]},
        {"id": "base", "op": "extrude", "intent": "i", "sketch": "base_sketch", "depth_mm": 8},
        {
            "id": "hole_1",
            "op": "hole",
            "intent": "i",
            "face": TOP,
            "position": [-40, -20],
            "standard": "iso_clearance",
            "size": "M6",
            "diameter_mm": 6.6,
            "end_condition": "through_all",
        },
        {
            "id": "holes",
            "op": "linear_pattern",
            "intent": "i",
            "features": ["hole_1"],
            "direction": "X",
            "spacing_mm": 80,
            "count": 2,
            "direction2": [0, 2, 0],
            "spacing2_mm": 40,
            "count2": 2,
        },
        {
            "id": "ring",
            "op": "circular_pattern",
            "intent": "i",
            "features": ["hole_1"],
            "axis": {"kind": "face", "from_op": "hole_1", "select": {"surface": "cylinder"}},
            "count": 6,
        },
        {
            "id": "ring2",
            "op": "circular_pattern",
            "intent": "i",
            "features": ["hole_1"],
            "axis": {"origin": [1, 2, 3], "direction": [0, 0, 1]},
            "count": 3,
            "angle_deg": 90,
            "equal_spacing": False,
        },
    ]
    plan = _plan(ops)
    hole = build_request(plan.operations[2], ctx, resolver)
    assert hole == HoleRequest(
        operation_id="hole_1",
        face=FaceHandle(id="face:top"),
        position=(-40, -20),
        standard="iso_clearance",
        size="M6",
        diameter_mm=6.6,
        end_condition="through_all",
        drill_point=DrillPoint.flat,  # a through hole has no bottom
    )
    assert resolver.resolved_refs == {"face": "face:top"}
    linear = build_request(plan.operations[3], ctx, resolver)
    assert linear == LinearPatternRequest(
        operation_id="holes",
        features=(FeatureHandle(id="feature:2"),),
        direction=(1, 0, 0),
        spacing_mm=80,
        count=2,
        direction2=(0, 2, 0),
        spacing2_mm=40,
        count2=2,
    )
    ring = build_request(plan.operations[4], ctx, resolver)
    assert isinstance(ring, CircularPatternRequest)
    # the same line as the hole axis, described by its point nearest the world origin (like every derived axis)
    assert ring.axis == Axis(origin=(10, 10, 0), direction=(0, 0, -1))
    ring2 = build_request(plan.operations[5], ctx, resolver)
    assert ring2 == CircularPatternRequest(
        operation_id="ring2",
        features=(FeatureHandle(id="feature:2"),),
        axis=Axis(origin=(1, 2, 3), direction=(0, 0, 1)),
        count=3,
        angle_deg=90,
        equal_spacing=False,
    )


def test_chamfer_and_fillet_edge_refs_and_distance1_default() -> None:
    _, ctx, resolver = _env()
    edge = {"kind": "edge", "between": [TOP, XMAX]}
    ops = [
        {"id": "base_sketch", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XY"}, "entities": [RECT]},
        {"id": "base", "op": "extrude", "intent": "i", "sketch": "base_sketch", "depth_mm": 8},
        {
            "id": "ch",
            "op": "chamfer",
            "intent": "i",
            "edges": [edge],
            "distance1_mm": 4,
            "distance2_mm": 2,
            "distance1_on": XMAX,
        },
        {"id": "ch2", "op": "chamfer", "intent": "i", "edges": [edge], "distance1_mm": 2},
        {"id": "fi", "op": "fillet", "intent": "i", "edges": [edge], "radius_mm": 3},
    ]
    plan = _plan(ops)
    chamfer = build_request(plan.operations[2], ctx, resolver)
    assert chamfer == ChamferRequest(
        operation_id="ch",
        edges=(EdgeHandle(id="edge:1"),),
        distance1_mm=4,
        distance2_mm=2,
        distance1_face=FaceHandle(id="face:xmax"),
    )
    assert resolver.resolved_refs["edges[0]"] == "edge:1"
    assert resolver.resolved_refs["distance1_on"] == "face:xmax"
    equal = build_request(plan.operations[3], ctx, resolver)
    assert equal == ChamferRequest(operation_id="ch2", edges=(EdgeHandle(id="edge:1"),), distance1_mm=2)
    fillet = build_request(plan.operations[4], ctx, resolver)
    assert fillet == FilletRequest(operation_id="fi", edges=(EdgeHandle(id="edge:1"),), radius_mm=3)


def test_chamfer_without_distance1_on_leaves_distance1_face_none_even_with_distance2() -> None:
    """``ChamferOperation`` validates that ``distance2_mm`` requires ``distance1_on`` (V rule), so a
    validated op can never reach ``build_chamfer`` with ``distance2_mm`` set and ``distance1_on`` None.
    ``model_construct`` smuggles that combination past validation to prove ``build_chamfer`` no longer
    has a (dead) branch that infers a default face for it."""
    _, ctx, resolver = _env()
    from cotaforge.ir.ops_features import ChamferOperation
    from cotaforge.ir.refs import EdgeRef, FaceRef, FaceSelector

    edge = EdgeRef(
        between=(
            FaceRef(from_op="base", select=FaceSelector(normal=(1, 0, 0))),
            FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))),
        )
    )
    op = ChamferOperation.model_construct(
        id="ch", op="chamfer", intent="i", edges=(edge,), distance1_mm=4.0, distance2_mm=2.0, distance1_on=None
    )
    chamfer = build_request(op, ctx, resolver)
    assert chamfer.distance1_face is None  # type: ignore[union-attr]


def test_body_level_ops() -> None:
    _, ctx, resolver = _env()
    plan = _plan(
        [
            {
                "id": "block",
                "op": "box",
                "intent": "i",
                "size": [100, 60, 20],
                "placement": {"origin": [1, 2, 3], "axis": "Z", "angle_deg": 90},
            },
            {
                "id": "bore",
                "op": "cylinder",
                "intent": "i",
                "diameter_mm": 30,
                "height_mm": 20,
                "axis": "Y",
                "origin": [50, 30, 0],
            },
            {
                "id": "u",
                "op": "boolean_union",
                "intent": "i",
                "target": {"kind": "body", "from_op": "block"},
                "tools": [{"kind": "body", "from_op": "bore"}],
            },
            {
                "id": "s",
                "op": "boolean_subtract",
                "intent": "i",
                "target": {"kind": "body", "from_op": "block"},
                "tools": [{"kind": "body", "from_op": "bore"}],
            },
            {
                "id": "x",
                "op": "boolean_intersect",
                "intent": "i",
                "target": {"kind": "body", "from_op": "block"},
                "tools": [{"kind": "body", "from_op": "bore"}],
            },
            {
                "id": "t",
                "op": "translate",
                "intent": "i",
                "target": {"kind": "body", "from_op": "block"},
                "offset": [10, 0, 5],
            },
            {
                "id": "r",
                "op": "rotate",
                "intent": "i",
                "target": {"kind": "body", "from_op": "block"},
                "axis": "Z",
                "angle_deg": 90,
                "about": [10, 0, 0],
            },
        ]
    )
    ctx.body_by_op["block"] = BodyHandle(id="body:1")
    ctx.body_by_op["bore"] = BodyHandle(id="body:2")
    box = build_request(plan.operations[0], ctx, resolver)
    assert isinstance(box, BoxRequest)
    assert box.size == (100, 60, 20)
    assert box.transform.origin == (1, 2, 3)
    assert box.transform.apply((10, 0, 0)) == pytest.approx((1, 12, 3))
    cylinder = build_request(plan.operations[1], ctx, resolver)
    assert cylinder == CylinderRequest(
        operation_id="bore", diameter_mm=30, height_mm=20, axis=Axis(origin=(50, 30, 0), direction=(0, 1, 0))
    )
    for index, kind in ((2, BooleanKind.union), (3, BooleanKind.subtract), (4, BooleanKind.intersect)):
        request = build_request(plan.operations[index], ctx, resolver)
        assert isinstance(request, BooleanRequest)
        assert request.kind == kind
        assert request.target == BodyHandle(id="body:1")
        assert request.tools == (BodyHandle(id="body:2"),)
    translate = build_request(plan.operations[5], ctx, resolver)
    assert translate == MoveBodyRequest(
        operation_id="t", body=BodyHandle(id="body:1"), transform=Transform(origin=(10, 0, 5))
    )
    rotate = build_request(plan.operations[6], ctx, resolver)
    assert isinstance(rotate, MoveBodyRequest)
    # rotation about the point (10, 0, 0): that point stays fixed, (20, 0, 0) -> (10, 10, 0)
    assert rotate.transform.apply((10, 0, 0)) == pytest.approx((10, 0, 0))
    assert rotate.transform.apply((20, 0, 0)) == pytest.approx((10, 10, 0))


def test_placement_transform_rotates_about_world_origin_then_translates() -> None:
    t = placement_transform(Placement(origin=(5, 0, 0), axis="Z", angle_deg=90))
    assert t.apply((1, 0, 0)) == pytest.approx((5, 1, 0))
    assert placement_transform(Placement()) == Transform.identity()
    assert isinstance(BoxOperation(id="b", intent="i", size=(1, 1, 1)), BoxOperation)
    assert isinstance(CylinderOperation(id="c", intent="i", diameter_mm=1, height_mm=1), CylinderOperation)


def test_assembly_ops_and_missing_document() -> None:
    stub, ctx, resolver = _env()
    stub.add_face(
        cylindrical_face(
            "face:pa",
            Axis(origin=(0, 0, 0), direction=(0, 0, 1)),
            3.25,
            (0, 0, 10),
            BoundingBox(min=(0, 0, 0), max=(1, 1, 1)),
            created_by="feature:c1",
            component="comp:1",
        ),
        feature_id="feature:c1",
    )
    stub.add_face(
        cylindrical_face(
            "face:pb",
            Axis(origin=(9, 0, 0), direction=(0, 0, 1)),
            3.0,
            (9, 0, 10),
            BoundingBox(min=(0, 0, 0), max=(1, 1, 1)),
            created_by="feature:c2",
            component="comp:2",
        ),
        feature_id="feature:c2",
    )
    ctx.feature_by_op["ins_block"] = FeatureHandle(id="feature:c1")
    ctx.feature_by_op["ins_pin"] = FeatureHandle(id="feature:c2")
    ctx.component_by_op["ins_block"] = ComponentHandle(id="comp:1")
    ctx.component_by_op["ins_pin"] = ComponentHandle(id="comp:2")
    a = {"kind": "face", "from_op": "ins_block", "select": {"surface": "cylinder"}}
    b = {"kind": "face", "from_op": "ins_pin", "select": {"surface": "cylinder"}}
    ops = [
        {"id": "ins_block", "op": "insert_component", "intent": "i", "part": "plate", "placement": {}},
        {"id": "ins_pin", "op": "insert_component", "intent": "i", "part": "pin", "placement": {"origin": [0, 0, 50]}},
        {"id": "fix", "op": "fix_component", "intent": "i", "component": "ins_block"},
        {"id": "m1", "op": "mate_concentric", "intent": "i", "a": a, "b": b, "alignment": "anti_aligned"},
        {"id": "m2", "op": "mate_coincident", "intent": "i", "a": a, "b": b},
        {"id": "m3", "op": "mate_distance", "intent": "i", "a": a, "b": b, "distance_mm": 2, "side": "negative"},
        {"id": "chk", "op": "check_interference", "intent": "i", "components": ["ins_block", "ins_pin"]},
        {"id": "chk_all", "op": "check_interference", "intent": "i"},
    ]
    plan = _plan(ops, kind="assembly")
    insert = build_request(plan.operations[0], ctx, resolver)
    assert insert == InsertComponentRequest(
        operation_id="ins_block", part=DocumentHandle(id="doc:plate"), transform=Transform.identity()
    )
    assert resolver.resolved_refs["part"] == "doc:plate"
    with pytest.raises(ResolutionError) as exc:
        build_request(plan.operations[1], ctx, resolver)
    assert exc.value.path == "part"
    assert build_request(plan.operations[2], ctx, resolver) == FixComponentRequest(
        operation_id="fix", component=ComponentHandle(id="comp:1")
    )
    assert build_request(plan.operations[3], ctx, resolver) == MateConcentricRequest(
        operation_id="m1", a=FaceHandle(id="face:pa"), b=FaceHandle(id="face:pb"), alignment="anti_aligned"
    )
    assert build_request(plan.operations[4], ctx, resolver) == MateCoincidentRequest(
        operation_id="m2", a=FaceHandle(id="face:pa"), b=FaceHandle(id="face:pb")
    )
    assert build_request(plan.operations[5], ctx, resolver) == MateDistanceRequest(
        operation_id="m3", a=FaceHandle(id="face:pa"), b=FaceHandle(id="face:pb"), distance_mm=2, side="negative"
    )
    assert build_request(plan.operations[6], ctx, resolver) == InterferenceRequest(
        operation_id="chk", components=(ComponentHandle(id="comp:1"), ComponentHandle(id="comp:2"))
    )
    assert build_request(plan.operations[7], ctx, resolver) == InterferenceRequest(
        operation_id="chk_all", components=None
    )
    del ctx.component_by_op["ins_pin"]
    with pytest.raises(ResolutionError):
        build_request(plan.operations[6], ctx, resolver)


# --- hole: thread metadata and cylindrical entry faces ---------------------------------------------------------


def _hole_op(**extra: object) -> dict:
    return {
        "id": "hole_2",
        "op": "hole",
        "intent": "i",
        "face": TOP,
        "position": [10, 10],
        "standard": "iso_tapped",
        "size": "M6",
        "end_condition": "blind",
        "depth_mm": 8,
        **extra,
    }


def _hole_plan(**extra: object) -> Plan:
    ops = [
        {"id": "base_sketch", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XY"}, "entities": [RECT]},
        {"id": "base", "op": "extrude", "intent": "i", "sketch": "base_sketch", "depth_mm": 8},
        _hole_op(**extra),
    ]
    return _plan(ops)


def test_a_tapped_hole_request_carries_the_derived_diameter_and_the_thread() -> None:
    _, ctx, resolver = _env()
    request = build_request(_hole_plan(thread_depth_mm=6).operations[2], ctx, resolver)
    assert isinstance(request, HoleRequest)
    assert request.diameter_mm == 5.0
    assert request.thread == HoleThread(size="M6", depth_mm=6.0)


def test_a_tapped_hole_without_a_thread_depth_still_names_the_thread() -> None:
    _, ctx, resolver = _env()
    request = build_request(_hole_plan().operations[2], ctx, resolver)
    assert isinstance(request, HoleRequest)
    assert request.thread == HoleThread(size="M6", depth_mm=None)


@pytest.mark.parametrize("standard", ["iso_clearance", "simple"])
def test_only_a_tapped_hole_carries_a_thread(standard: str) -> None:
    _, ctx, resolver = _env()
    extra: dict = {"standard": standard, "diameter_mm": 6.6}
    if standard == "simple":
        extra["size"] = None
    request = build_request(_hole_plan(**extra).operations[2], ctx, resolver)
    assert isinstance(request, HoleRequest)
    assert request.thread is None


def test_a_hole_on_a_cylindrical_face_is_built_with_its_axial_and_angular_position() -> None:
    stub, ctx, resolver = _env()
    stub.add_face(
        cylindrical_face(
            "face:tube",
            Axis(origin=(0, 0, 0), direction=(0, 0, 1)),
            20.0,
            (0, 0, 50),
            BoundingBox(min=(-20, -20, 0), max=(20, 20, 100)),
            created_by="feature:1",
        ),
        feature_id="feature:1",
    )
    cylinder = {"kind": "face", "from_op": "base", "select": {"surface": "cylinder"}}
    plan = _hole_plan(face=cylinder, position=[5, 45], standard="simple", size=None, diameter_mm=4.0)
    request = build_request(plan.operations[2], ctx, resolver)
    assert isinstance(request, HoleRequest)
    assert request.face == FaceHandle(id="face:tube") and request.position == (5.0, 45.0)


def test_a_hole_on_a_cone_or_sphere_is_a_plan_error_before_the_adapter() -> None:
    stub, ctx, resolver = _env()
    stub.add_face(
        cylindrical_face(
            "face:cone",
            Axis(origin=(0, 0, 0), direction=(0, 0, 1)),
            4.0,
            (0, 0, 5),
            BoundingBox(min=(-4, -4, 0), max=(4, 4, 10)),
            created_by="feature:1",
        ).model_copy(update={"surface": SurfaceType.cone}),
        feature_id="feature:1",
    )
    face = {"kind": "face", "from_op": "base", "select": {"surface": "cone"}}
    plan = _hole_plan(face=face, standard="simple", size=None, diameter_mm=4.0)
    with pytest.raises(ResolutionError, match="planar or a cylindrical"):
        build_request(plan.operations[2], ctx, resolver)


def test_the_drill_point_defaults_to_freecads_angled_but_a_through_hole_has_none() -> None:
    _, ctx, resolver = _env()
    blind = build_request(_hole_plan().operations[2], ctx, resolver)
    assert isinstance(blind, HoleRequest) and blind.drill_point is DrillPoint.angled
    assert blind.drill_point_angle_deg == 118.0
    flat = build_request(_hole_plan(drill_point="flat").operations[2], ctx, resolver)
    assert isinstance(flat, HoleRequest) and flat.drill_point is DrillPoint.flat
    through = build_request(_hole_plan(end_condition="through_all", depth_mm=None).operations[2], ctx, resolver)
    assert isinstance(through, HoleRequest) and through.drill_point is DrillPoint.flat
