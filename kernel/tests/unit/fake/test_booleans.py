"""Boolean containment rules through the adapter and the pure helpers."""

import math

import pytest

from cotaforge.adapters.fake.booleans import intersection_volume
from cotaforge.adapters.fake.model import Primitive, PrimitiveKind, bbox_of_points, cylinder_bbox
from cotaforge.contract import (
    AdapterError,
    Axis,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CylinderRequest,
    EdgeRequest,
    EdgesBetweenRequest,
    Empty,
    ErrorCode,
    FaceRequest,
    FeatureRequest,
    FilletRequest,
    MassRequest,
    MoveBodyRequest,
    ProvenanceRole,
    SurfaceType,
    Transform,
)
from tests.unit.fake.conftest import Session, vec_close

Z = (0.0, 0.0, 1.0)


def _box(prim_id: str, lo: tuple[float, float, float], hi: tuple[float, float, float], sign: int = 1) -> Primitive:
    polygon = ((lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]), (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]))
    size = (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])
    return Primitive(
        id=prim_id,
        kind=PrimitiveKind.prism,
        sign=sign,
        volume_mm3=size[0] * size[1] * size[2],
        centroid=((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2),
        bbox=BoundingBox(min=lo, max=hi),
        feature="f",
        polygon=polygon,
        direction=Z,
        depth=size[2],
    )


def _cyl(
    prim_id: str,
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    r: float,
    h: float,
    sign: int = 1,
) -> Primitive:
    return Primitive(
        id=prim_id,
        kind=PrimitiveKind.cylinder,
        sign=sign,
        volume_mm3=math.pi * r * r * h,
        centroid=origin,
        bbox=cylinder_bbox(origin, direction, r, h),
        feature="f",
        axis=Axis(origin=origin, direction=direction),
        radius=r,
        height=h,
    )


def test_box_box_intersection_is_interval_product() -> None:
    a = _box("a", (0, 0, 0), (10, 10, 10))
    b = _box("b", (5, 5, 5), (20, 20, 20))
    assert intersection_volume(a, b) == pytest.approx(125.0)
    assert intersection_volume(a, _box("c", (10, 0, 0), (20, 10, 10))) == 0.0


def test_coaxial_cylinders_use_min_radius_and_axial_overlap() -> None:
    pin = _cyl("pin", (15, 15, -20), Z, 3.0, 40.0)
    bore = _cyl("bore", (15, 15, 20), (0, 0, -1), 3.25, 20.0)
    assert intersection_volume(pin, bore) == pytest.approx(math.pi * 9 * 20)
    offset = _cyl("off", (16, 15, 0), Z, 3.0, 40.0)
    assert intersection_volume(pin, offset) is None
    tilted = _cyl("tilt", (15, 15, 0), (1, 0, 0), 3.0, 40.0)
    assert intersection_volume(pin, tilted) is None


def test_cylinder_in_box_footprint_rules() -> None:
    block = _box("block", (0, 0, 0), (30, 30, 20))
    inside = _cyl("in", (15, 15, -20), Z, 3.0, 40.0)
    assert intersection_volume(inside, block) == pytest.approx(math.pi * 9 * 20)
    assert intersection_volume(block, inside) == pytest.approx(math.pi * 9 * 20)
    disjoint = _cyl("out", (50, 15, 0), Z, 3.0, 40.0)
    assert intersection_volume(disjoint, block) == 0.0
    above = _cyl("above", (15, 15, 25), Z, 3.0, 5.0)
    assert intersection_volume(above, block) == 0.0
    straddling = _cyl("straddle", (29, 15, 0), Z, 3.0, 40.0)
    assert intersection_volume(straddling, block) is None


def test_unsupported_pairs_return_none() -> None:
    wedge = Primitive(
        id="w",
        kind=PrimitiveKind.chamfer_wedge,
        sign=-1,
        volume_mm3=1.0,
        centroid=(0, 0, 0),
        bbox=bbox_of_points(((0, 0, 0), (1, 1, 1))),
        feature="f",
    )
    assert intersection_volume(wedge, _box("b", (0, 0, 0), (1, 1, 1))) is None
    rotated = _box("r", (0, 0, 0), (1, 1, 1))
    rotated.polygon = ((0, 0, 0), (1, 1, 0), (0, 2, 0), (-1, 1, 0))
    assert intersection_volume(rotated, _box("b", (0, 0, 0), (1, 1, 1))) is None


def _concept_block(session: Session) -> tuple:
    session.new_part()
    box = session.adapter.box(BoxRequest(operation_id="box", size=(100.0, 60.0, 20.0)))
    cyl = session.adapter.cylinder(
        CylinderRequest(
            operation_id="cyl", diameter_mm=30.0, height_mm=20.0, axis=Axis(origin=(50, 30, 0), direction=Z)
        )
    )
    return box, cyl


def test_boolean_subtract_contained_cylinder(session: Session) -> None:
    box, cyl = _concept_block(session)
    result = session.adapter.boolean(
        BooleanRequest(
            operation_id="sub", kind=BooleanKind.subtract, target=box.created_bodies[0], tools=cyl.created_bodies
        )
    )
    assert result.feature is not None
    assert result.created_faces == ()
    assert set(result.modified_faces) == set(cyl.created_faces)
    assert result.modified_bodies == (box.created_bodies[0],)
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(120000 - math.pi * 225 * 20)  # 105862.83
    assert vec_close(mass.centroid, (50.0, 30.0, 10.0))
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 20.0)
    )
    flipped = session.adapter.face_info(FaceRequest(operation_id="q", face=cyl.created_faces[1]))
    assert flipped.body == box.created_bodies[0]
    assert flipped.provenance.modified_by == (result.feature,)
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.mass_properties(MassRequest(operation_id="m", body=cyl.created_bodies[0]))
    assert excinfo.value.info.code is ErrorCode.invalid_reference


def test_boolean_subtract_keep_tools_preserves_the_tool_body(session: Session) -> None:
    box, cyl = _concept_block(session)
    result = session.adapter.boolean(
        BooleanRequest(
            operation_id="sub",
            kind=BooleanKind.subtract,
            target=box.created_bodies[0],
            tools=cyl.created_bodies,
            keep_tools=True,
        )
    )
    assert result.feature is not None
    # the cavity is described by NEW faces (copies): the tool keeps its own originals untouched
    assert len(result.created_faces) == len(cyl.created_faces)
    assert result.modified_faces == ()
    # total document volume is unchanged: the tool is fully contained, so what target loses, the
    # now-separate tool body still has
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(120000.0)
    # the tool survives as its own live, queryable body -- no invalid_reference like the non-keep case
    tool_mass = session.adapter.mass_properties(MassRequest(operation_id="m", body=cyl.created_bodies[0]))
    assert tool_mass.volume_mm3 == pytest.approx(math.pi * 225.0 * 20.0)
    # its own original faces are untouched (still owned by the tool body, not reassigned/flipped)
    original = session.adapter.face_info(FaceRequest(operation_id="q", face=cyl.created_faces[1]))
    assert original.body == cyl.created_bodies[0]


def test_boolean_subtract_keep_tools_cavity_faces_have_edges(session: Session) -> None:
    """MEDIUM: a keep_tools cavity face must carry proper edges (matching the
    tool's own topology) so edges_of/edges_between/vertices_of work on it -- e.g. a fillet on the coped
    face after fish-mouth coping, which needs EdgeRef.of/between to name an edge there at all."""
    box, cyl = _concept_block(session)
    result = session.adapter.boolean(
        BooleanRequest(
            operation_id="sub",
            kind=BooleanKind.subtract,
            target=box.created_bodies[0],
            tools=cyl.created_bodies,
            keep_tools=True,
        )
    )
    cavity_faces = [session.adapter.face_info(FaceRequest(operation_id="q", face=f)) for f in result.created_faces]
    lateral = next(f for f in cavity_faces if f.surface == SurfaceType.cylinder)
    discs = [f for f in cavity_faces if f.surface == SurfaceType.plane]
    assert len(discs) == 2

    lateral_edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=lateral.handle)).edges
    assert len(lateral_edges) == 2  # same edge count as the original cylinder's own lateral face

    for disc in discs:
        between = session.adapter.edges_between(
            EdgesBetweenRequest(operation_id="q", a=lateral.handle, b=disc.handle)
        ).edges
        assert len(between) == 1
        assert between[0].curve.value == "circle"
        # a full circle edge has no discrete vertices in this fake -- matches the tool's own edges
        assert session.adapter.vertices_of(EdgeRequest(operation_id="q", edge=between[0].handle)).vertices == ()


def test_boolean_subtract_keep_tools_cavity_edges_support_a_fillet(session: Session) -> None:
    """Closes the loop with the task's own worked example: a fillet directly on an edge of the coped
    (cavity) face, proving the new edges are not just present but actually usable -- not just a box tool
    contained in a box target, which gives line edges a fillet can act on (a cylinder's are circular)."""
    session.new_part()
    target = session.adapter.box(BoxRequest(operation_id="target", size=(100.0, 60.0, 20.0)))
    tool = session.adapter.box(
        BoxRequest(operation_id="tool", size=(20.0, 20.0, 5.0), transform=Transform(origin=(10.0, 10.0, 5.0)))
    )
    result = session.adapter.boolean(
        BooleanRequest(
            operation_id="sub",
            kind=BooleanKind.subtract,
            target=target.created_bodies[0],
            tools=tool.created_bodies,
            keep_tools=True,
        )
    )
    cavity_faces = [session.adapter.face_info(FaceRequest(operation_id="q", face=f)) for f in result.created_faces]
    top = next(f for f in cavity_faces if f.surface == SurfaceType.plane)
    edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=top.handle)).edges
    assert len(edges) == 4

    fillet_result = session.adapter.fillet(FilletRequest(operation_id="fil", edges=(edges[0].handle,), radius_mm=1.0))
    assert fillet_result.feature is not None


def test_boolean_subtract_keep_tools_then_a_later_op_still_resolves_the_kept_tool() -> None:
    """The point of keep_tools: a later op can still reference the tool body by its own creating op
    (BodyRef.from_op) -- proven end to end through the executor, not just the raw adapter call."""
    from cotaforge.adapters.fake.adapter import FakeAdapter
    from cotaforge.contract import ConnectRequest
    from cotaforge.executor.executor import Executor
    from cotaforge.executor.trace import ExecutorState, StepStatus
    from cotaforge.ir import parse_plan
    from cotaforge.verification.verifier import Verifier

    plan = parse_plan(
        {
            "id": "coped",
            "kind": "part",
            "operations": [
                {"id": "block", "op": "box", "intent": "target tube stand-in", "size": [100.0, 60.0, 20.0]},
                {
                    "id": "bore",
                    "op": "cylinder",
                    "intent": "coping tool",
                    "diameter_mm": 30.0,
                    "height_mm": 20.0,
                    "origin": [50.0, 30.0, 0.0],
                },
                {
                    "id": "cutout",
                    "op": "boolean_subtract",
                    "intent": "cope the block, keep the tool for reuse",
                    "target": {"kind": "body", "from_op": "block"},
                    "tools": [{"kind": "body", "from_op": "bore"}],
                    "keep_tools": True,
                },
                {
                    "id": "nudge",
                    "op": "translate",
                    "intent": "prove the kept tool is still a live, referenceable body",
                    "target": {"kind": "body", "from_op": "bore"},
                    "offset": [0.0, 0.0, 5.0],
                },
            ],
        }
    )
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    ex = Executor(plan, adapter, Verifier())
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state is ExecutorState.completed
    assert [s.op_id for s in trace.steps] == ["block", "bore", "cutout", "nudge"]
    assert all(s.status is StepStatus.success for s in trace.steps)
    # document volume unchanged (fully-contained cope): block loses exactly what bore keeps
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(100.0 * 60.0 * 20.0)


def test_boolean_union_of_disjoint_and_contained_bodies(session: Session) -> None:
    session.new_part()
    a = session.adapter.box(BoxRequest(operation_id="a", size=(10.0, 10.0, 10.0)))
    b = session.adapter.box(
        BoxRequest(operation_id="b", size=(10.0, 10.0, 10.0), transform=Transform(origin=(20.0, 0.0, 0.0)))
    )
    c = session.adapter.box(
        BoxRequest(operation_id="c", size=(2.0, 2.0, 2.0), transform=Transform(origin=(1.0, 1.0, 1.0)))
    )
    result = session.adapter.boolean(
        BooleanRequest(
            operation_id="u",
            kind=BooleanKind.union,
            target=a.created_bodies[0],
            tools=(b.created_bodies[0], c.created_bodies[0]),
        )
    )
    assert result.created_faces == ()
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(2000.0)  # 1000 + 1000 + 8 - 8 (c inside a)
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(0.0, 0.0, 0.0), max=(30.0, 10.0, 10.0)
    )
    assert result.feature is not None
    bodies = session.adapter.bodies_of(
        FeatureRequest(operation_id="q", feature=result.feature, role=ProvenanceRole.modified)
    ).bodies
    assert [x.handle for x in bodies] == [a.created_bodies[0]]
    assert bodies[0].face_count == 18


def test_boolean_intersect_of_axis_aligned_boxes(session: Session) -> None:
    session.new_part()
    a = session.adapter.box(BoxRequest(operation_id="a", size=(10.0, 10.0, 10.0)))
    b = session.adapter.box(
        BoxRequest(operation_id="b", size=(10.0, 10.0, 10.0), transform=Transform(origin=(5.0, 5.0, 5.0)))
    )
    result = session.adapter.boolean(
        BooleanRequest(operation_id="i", kind=BooleanKind.intersect, target=a.created_bodies[0], tools=b.created_bodies)
    )
    assert result.feature is not None
    assert len(result.created_faces) == 6
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(125.0)
    assert vec_close(mass.centroid, (7.5, 7.5, 7.5))
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(5.0, 5.0, 5.0), max=(10.0, 10.0, 10.0)
    )
    for face in a.created_faces + b.created_faces:
        with pytest.raises(AdapterError):
            session.adapter.face_info(FaceRequest(operation_id="q", face=face))
    faces = session.faces(result.feature, ProvenanceRole.created)
    assert sum(f.area_mm2 for f in faces.faces) == pytest.approx(6 * 25.0)


def test_boolean_keep_tools_rejected_on_union_and_intersect(session: Session) -> None:
    session.new_part()
    a = session.adapter.box(BoxRequest(operation_id="a", size=(10.0, 10.0, 10.0)))
    b = session.adapter.box(
        BoxRequest(operation_id="b", size=(10.0, 10.0, 10.0), transform=Transform(origin=(20.0, 0.0, 0.0)))
    )
    for kind in (BooleanKind.union, BooleanKind.intersect):
        with pytest.raises(AdapterError) as excinfo:
            session.adapter.boolean(
                BooleanRequest(
                    operation_id=f"op_{kind.value}",
                    kind=kind,
                    target=a.created_bodies[0],
                    tools=b.created_bodies,
                    keep_tools=True,
                )
            )
        assert excinfo.value.info.code is ErrorCode.invalid_request


def test_boolean_not_supported_leaves_document_unchanged(session: Session) -> None:
    session.new_part()
    box = session.adapter.box(BoxRequest(operation_id="box", size=(30.0, 30.0, 20.0)))
    straddling = session.adapter.cylinder(
        CylinderRequest(operation_id="cyl", diameter_mm=6.0, height_mm=40.0, axis=Axis(origin=(29, 15, 0), direction=Z))
    )
    before = session.adapter.mass_properties(MassRequest(operation_id="m"))
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.boolean(
            BooleanRequest(
                operation_id="sub",
                kind=BooleanKind.subtract,
                target=box.created_bodies[0],
                tools=straddling.created_bodies,
            )
        )
    assert excinfo.value.info.code is ErrorCode.not_supported
    assert excinfo.value.info.recoverable is False
    assert session.adapter.mass_properties(MassRequest(operation_id="m")) == before
    assert len(session.adapter.feature_tree(Empty(operation_id="t")).nodes) == 2
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.boolean(
            BooleanRequest(
                operation_id="int",
                kind=BooleanKind.intersect,
                target=box.created_bodies[0],
                tools=straddling.created_bodies,
            )
        )
    assert excinfo.value.info.code is ErrorCode.not_supported


def test_translate_after_subtract_moves_the_cavity_too(session: Session) -> None:
    box, cyl = _concept_block(session)
    session.adapter.boolean(
        BooleanRequest(
            operation_id="sub", kind=BooleanKind.subtract, target=box.created_bodies[0], tools=cyl.created_bodies
        )
    )
    session.adapter.move_body(
        MoveBodyRequest(operation_id="mv", body=box.created_bodies[0], transform=Transform(origin=(10.0, 0.0, 0.0)))
    )
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(120000 - math.pi * 225 * 20)
    assert vec_close(mass.centroid, (60.0, 30.0, 10.0))
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(10.0, 0.0, 0.0), max=(110.0, 60.0, 20.0)
    )
