"""Builders with hand-computed numbers and provenance queries (7.3)."""

import math

import pytest

from cotaforge.contract import (
    AdapterError,
    Arc3Entity,
    ArcEntity,
    Axis,
    BodyHandle,
    BoundingBox,
    BoxRequest,
    ChamferRequest,
    CircleEntity,
    CircularPatternRequest,
    CurveType,
    CutRequest,
    CylinderRequest,
    Direction,
    EdgeRequest,
    Empty,
    EndCondition,
    ErrorCode,
    ExtrudeRequest,
    FaceHandle,
    FaceRequest,
    FeatureHandle,
    FeatureRequest,
    FilletRequest,
    HoleRequest,
    HoleStandard,
    LinearPatternRequest,
    LineEntity,
    MassRequest,
    MoveBodyRequest,
    NamedDimension,
    OperationResult,
    PlaneName,
    ProvenanceRole,
    RectEntity,
    RevolveRequest,
    SketchHandle,
    SketchRequest,
    SketchStatusRequest,
    SlotEntity,
    SquareEntity,
    SurfaceType,
    Transform,
    VertexAtRequest,
)
from tests.unit.fake.conftest import Session, close, cylindrical_faces, face_with_normal, vec_close

X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)
Z = (0.0, 0.0, 1.0)
NX = (-1.0, 0.0, 0.0)
NY = (0.0, -1.0, 0.0)
NZ = (0.0, 0.0, -1.0)
HOLE_VOLUME = math.pi * 3.3**2 * 8.0  # 273.694...


# --- extrude ----------------------------------------------------------------------------------------


def test_sketch_returns_frame_and_profile_areas(session: Session) -> None:
    session.new_part()
    result = session.adapter.sketch(
        SketchRequest(
            operation_id="s",
            on=PlaneName.XZ,
            entities=(
                CircleEntity(name="c", center=(0, 0), diameter=10),
                LineEntity(name="a", start=(20, 0), end=(30, 0)),
                LineEntity(name="b", start=(30, 0), end=(30, 5)),
                LineEntity(name="c2", start=(30, 5), end=(20, 0)),
            ),
        )
    )
    assert result.frame.n == Y
    assert result.profile_areas_mm2 == pytest.approx((math.pi * 25, 25.0))


def test_sketch_profiles_may_chain_lines_and_arcs(session: Session) -> None:
    session.new_part()
    result = session.adapter.sketch(
        SketchRequest(
            operation_id="s",
            on=PlaneName.XY,
            entities=(
                LineEntity(name="base", start=(-5, 0), end=(5, 0)),
                Arc3Entity(name="dome", start=(5, 0), through=(0, 5), end=(-5, 0)),
            ),
        )
    )
    assert result.profile_areas_mm2 == pytest.approx((math.pi * 25 / 2,))


def test_an_open_chain_with_an_arc_is_an_invalid_request(session: Session) -> None:
    session.new_part()
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.sketch(
            SketchRequest(
                operation_id="s",
                on=PlaneName.XY,
                entities=(ArcEntity(name="a", center=(0, 0), radius=5, start_deg=0, end_deg=90),),
            )
        )
    assert excinfo.value.info.code is ErrorCode.invalid_request
    assert "open chain" in excinfo.value.info.message


def test_a_slot_extrudes_and_cuts_with_the_analytic_volume(session: Session) -> None:
    session.new_part()
    plate, _ = session.rect_sketch("plate_sketch", PlaneName.XY, (50, 30), 100, 60)
    base = session.adapter.extrude(
        ExtrudeRequest(operation_id="plate", sketch=SketchHandle(id=plate), depth_mm=8.0)
    )
    slot = session.adapter.sketch(
        SketchRequest(
            operation_id="slot_sketch",
            on=PlaneName.XY,
            entities=(SlotEntity(name="slot", start=(30, 30), end=(70, 30), width=10),),
        )
    )
    slot_area = 40 * 10 + math.pi * 5**2
    assert slot.profile_areas_mm2 == pytest.approx((slot_area,))
    session.adapter.cut(
        CutRequest(
            operation_id="slot_cut",
            sketch=slot.sketch,
            end_condition=EndCondition.through_all,
            direction=Direction.positive,
        )
    )
    volume = session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3
    assert base.feature is not None
    assert volume == pytest.approx(100 * 60 * 8 - slot_area * 8, rel=1e-4)


def test_slots_and_arcs_report_their_dimensions_to_sketch_status(session: Session) -> None:
    session.new_part()
    slot = SketchRequest(
        operation_id="s",
        on=PlaneName.XY,
        entities=(SlotEntity(name="slot", start=(20, 0), end=(30, 0), width=4),),
        named_dimensions=(
            NamedDimension(name="w", entity="slot", attribute="width"),
            NamedDimension(name="l", entity="slot", attribute="length"),
        ),
    )
    handle = session.adapter.sketch(slot).sketch
    status = session.adapter.sketch_status(SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id=handle.id)))
    assert status.dof == 2 and not status.fully_constrained  # the slot's position (u, v) is still free

    arc = SketchRequest(
        operation_id="a",
        on=PlaneName.XY,
        entities=(
            Arc3Entity(name="dome", start=(5, 0), through=(0, 5), end=(-5, 0)),
            LineEntity(name="base", start=(-5, 0), end=(5, 0)),
        ),
        named_dimensions=(NamedDimension(name="r", entity="dome", attribute="radius"),),
    )
    handle = session.adapter.sketch(arc).sketch
    status = session.adapter.sketch_status(SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id=handle.id)))
    assert status.dof == 5  # the arc's centre (2) and the line's start and length (3): only the radius is dimensioned


def test_plate_extrude_volume_faces_and_edges(session: Session) -> None:
    session.new_part()
    result = session.plate()
    assert result.feature is not None and result.rebuild_ok
    assert len(result.created_faces) == 6
    assert len(result.created_edges) == 12
    assert len(result.created_bodies) == 1
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(48000.0)  # 100 * 60 * 8
    assert vec_close(mass.centroid, (50.0, 30.0, 4.0))
    assert mass.surface_mm2 == pytest.approx(2 * 6000 + 2 * 800 + 2 * 480)
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0)
    )
    faces = session.faces(result.feature, ProvenanceRole.created)
    expected_areas = {Z: 6000.0, NZ: 6000.0, X: 480.0, NX: 480.0, Y: 800.0, NY: 800.0}
    for normal, area in expected_areas.items():
        face = face_with_normal(faces, normal)
        assert face.area_mm2 == pytest.approx(area)
        assert face.provenance.created_by == result.feature
        assert face.provenance.modified_by == ()
    top = face_with_normal(faces, Z)
    assert vec_close(top.center, (50.0, 30.0, 8.0))
    assert top.bbox == BoundingBox(min=(0.0, 0.0, 8.0), max=(100.0, 60.0, 8.0))
    assert session.faces(result.feature, ProvenanceRole.modified).faces == ()


def test_edges_between_adjacent_plate_faces_is_one_line(session: Session) -> None:
    session.new_part()
    result = session.plate()
    assert result.feature is not None
    faces = session.faces(result.feature)
    top = face_with_normal(faces, Z)
    side = face_with_normal(faces, X)
    edges = session.edges_between(top, side)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.curve is CurveType.line
    assert edge.length_mm == pytest.approx(60.0)
    assert edge.convex is True
    assert edge.dihedral_deg == pytest.approx(90.0)
    assert set(edge.faces) == {top.handle, side.handle}
    assert session.edges_between(top, face_with_normal(faces, NZ)) == ()
    top_edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=top.handle)).edges
    assert len(top_edges) == 4
    assert sorted(e.length_mm for e in top_edges) == pytest.approx([60.0, 60.0, 100.0, 100.0])


def test_vertices_of_and_vertex_at(session: Session) -> None:
    session.new_part()
    result = session.plate()
    assert result.feature is not None
    faces = session.faces(result.feature)
    top, side_x, side_y = face_with_normal(faces, Z), face_with_normal(faces, X), face_with_normal(faces, Y)
    edge = session.edges_between(top, side_x)[0]
    vertices = session.adapter.vertices_of(EdgeRequest(operation_id="q", edge=edge.handle)).vertices
    assert len(vertices) == 2
    assert vertices[0].point == edge.start
    assert vertices[1].point == edge.end
    assert all(len(v.edges) == 3 for v in vertices)
    corner = session.adapter.vertex_at(
        VertexAtRequest(operation_id="q", faces=(top.handle, side_x.handle, side_y.handle))
    ).vertices
    assert len(corner) == 1
    assert vec_close(corner[0].point, (100.0, 60.0, 8.0))


def test_bodies_of_and_feature_tree(session: Session) -> None:
    session.new_part()
    result = session.plate()
    assert result.feature is not None
    bodies = session.adapter.bodies_of(FeatureRequest(operation_id="q", feature=result.feature)).bodies
    assert len(bodies) == 1
    assert bodies[0].volume_mm3 == pytest.approx(48000.0)
    assert bodies[0].face_count == 6
    assert bodies[0].provenance.created_by == result.feature
    tree = session.adapter.feature_tree(Empty(operation_id="t"))
    assert [n.kind for n in tree.nodes] == ["extrude"]
    assert tree.nodes[0].parameters["depth_mm"] == 8.0
    assert session.adapter.is_valid(Empty(operation_id="v")).valid is True


def test_square_sketch_is_analytic_and_reports_its_named_side(session: Session) -> None:
    """Area side², corner points like the equal-sided rect, and the ``side`` named dimension in the tree."""
    session.new_part()
    result = session.adapter.sketch(
        SketchRequest(
            operation_id="s",
            on=PlaneName.XY,
            entities=(SquareEntity(name="boss", center=(15.0, 15.0), side=20.0),),
            named_dimensions=(
                NamedDimension(name="boss_side", entity="boss", attribute="side"),
                NamedDimension(name="cx", entity="boss", attribute="u"),
                NamedDimension(name="nope", entity="boss", attribute="width"),  # not a square attribute: skipped
            ),
        )
    )
    assert result.profile_areas_mm2 == (pytest.approx(400.0),)
    extruded = session.extrude("e", result.sketch.id, 10.0)
    assert extruded.feature is not None
    body = session.adapter.bodies_of(FeatureRequest(operation_id="q", feature=extruded.feature)).bodies[0]
    assert body.volume_mm3 == pytest.approx(4000.0)
    assert vec_close(body.bbox.min, (5.0, 5.0, 0.0)) and vec_close(body.bbox.max, (25.0, 25.0, 10.0))
    tree = session.adapter.feature_tree(Empty(operation_id="t"))
    assert tree.nodes[0].named_dimensions == {"boss_side": 20.0, "cx": 15.0}


def test_extrude_negative_direction_and_both_directions(session: Session) -> None:
    session.new_part()
    sketch, _ = session.rect_sketch("s", PlaneName.XY, (0.0, 0.0), 10.0, 10.0)
    session.extrude("down", sketch, 5.0, direction=Direction.negative)
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(-5.0, -5.0, -5.0), max=(5.0, 5.0, 0.0)
    )
    session.adapter.undo_last(Empty(operation_id="u"))
    session.extrude("both", sketch, 5.0, both_directions=True, depth2_mm=3.0)
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(100 * 8.0)
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(-5.0, -5.0, -3.0), max=(5.0, 5.0, 5.0)
    )


def test_extrude_circle_makes_cylinder_with_circle_edges(session: Session) -> None:
    session.new_part()
    sketch = session.circle_sketch("s", PlaneName.XY, (0.0, 0.0), 80.0)
    result = session.extrude("disc", sketch, 10.0)
    assert result.feature is not None
    assert len(result.created_faces) == 3
    assert len(result.created_edges) == 2
    faces = session.faces(result.feature)
    lateral = cylindrical_faces(faces)
    assert len(lateral) == 1
    assert lateral[0].radius == pytest.approx(40.0)
    assert lateral[0].axis is not None and vec_close(lateral[0].axis.direction, Z)
    assert lateral[0].area_mm2 == pytest.approx(2 * math.pi * 40 * 10)
    edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=lateral[0].handle)).edges
    assert all(e.curve is CurveType.circle and e.radius == pytest.approx(40.0) for e in edges)
    assert session.adapter.vertices_of(EdgeRequest(operation_id="q", edge=edges[0].handle)).vertices == ()
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(math.pi * 1600 * 10)


def test_extrude_on_a_face_reduces_that_face_area_and_merges(session: Session) -> None:
    """A boss extruded on an existing face and joined (merge=True, the default) yields 5 new faces (4
    side walls + 1 top cap), not 6: the base merges into the host face instead of duplicating it as a
    new coincident face. Matches real FreeCAD."""
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    sketch, _ = session.rect_sketch("s", top.handle, (0.0, 0.0), 20.0, 10.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert boss.created_bodies == ()
    assert boss.modified_faces == (top.handle,)
    assert len(boss.created_faces) == 5  # 4 side walls + 1 top cap, NOT 6: no duplicate base face
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(5800.0)
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(49000.0)
    assert session.adapter.bounding_box(MassRequest(operation_id="b")).max == (100.0, 60.0, 13.0)
    created = session.faces(boss.feature, ProvenanceRole.created)  # type: ignore[arg-type]
    boss_top = face_with_normal(created, Z)
    assert vec_close(boss_top.center, (50.0, 30.0, 13.0))
    assert all(f.normal != NZ for f in created.faces)  # no new face at the base plane


def test_extrude_cylindrical_boss_on_a_face_yields_two_created_faces(session: Session) -> None:
    """Same rule as the rect case, for a circular boss: 1 cylindrical side + 1 top cap, not 3."""
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    sketch = session.circle_sketch("s", top.handle, (0.0, 0.0), 20.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert boss.created_bodies == ()
    assert boss.modified_faces == (top.handle,)
    assert len(boss.created_faces) == 2
    created = session.faces(boss.feature, ProvenanceRole.created)  # type: ignore[arg-type]
    lateral = cylindrical_faces(created)
    assert len(lateral) == 1
    assert lateral[0].radius == pytest.approx(10.0)
    assert all(f.normal != NZ for f in created.faces)


def test_extrude_on_a_face_not_merged_keeps_both_caps(session: Session) -> None:
    """merge=False: the boss is not joined to the host body, so it keeps its own base cap (a
    separate, non-merged solid) and the host face is left untouched."""
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    sketch, _ = session.rect_sketch("s", top.handle, (0.0, 0.0), 20.0, 10.0)
    boss = session.extrude("boss", sketch, 5.0, merge=False)
    assert boss.created_bodies != ()
    assert boss.modified_faces == ()
    assert len(boss.created_faces) == 6  # 4 side walls + 2 caps: a separate, non-merged solid
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(6000.0)


def _block(session: Session, op_prefix: str = "block") -> OperationResult:
    """20 x 20 x 8 block on XY, corner at the origin -- small enough that a same-footprint boss on its
    top face exactly ``covers`` it (contrast the 100 x 60 plate used above, where a 20x20 boss is
    strictly ``inside`` the top face)."""
    sketch, _ = session.rect_sketch(f"{op_prefix}_sketch", PlaneName.XY, (10.0, 10.0), 20.0, 20.0)
    return session.extrude(op_prefix, sketch, 8.0)


def test_extrude_boss_that_covers_the_host_face_removes_it_and_extends_the_sides(session: Session) -> None:
    """The ``covers`` coverage class (validated on real FreeCAD): a 20x20 boss on the 20x20 top face of a 20x20x8 block
    matches the host face's footprint exactly, so the host face is consumed entirely (removed -- a stale handle
    afterwards) instead of merely shrunk, and its 4 side faces are extended (modified) instead of new
    side walls being created."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    top = face_with_normal(session.faces(block.feature), Z)
    sketch, _ = session.rect_sketch("s", top.handle, (0.0, 0.0), 20.0, 20.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert boss.created_bodies == ()
    assert len(boss.created_faces) == 1  # only the far (top) cap; no new side walls
    assert len(boss.modified_faces) == 4  # the block's 4 side faces, extended instead of duplicated
    assert top.handle not in boss.modified_faces  # the host face itself is gone, not modified
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(5200.0)
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(0.0, 0.0, 0.0), max=(20.0, 20.0, 13.0)
    )
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle))
    assert excinfo.value.info.code is ErrorCode.invalid_reference


def test_extrude_boss_that_only_partially_overlaps_the_host_face_falls_back_to_the_inside_rule(
    session: Session,
) -> None:
    """The fake only special-cases the exact ``covers`` footprint; any profile that does not classify
    as ``covers`` (e.g. the ``overhangs`` class -- rejected upstream by ``ExpectedComputer._op_extrude``
    before the adapter is ever called, so the fake alone never needs to reject it) keeps the
    merge-into-host-face behaviour: the host face survives, only shrunk."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    top = face_with_normal(session.faces(block.feature), Z)
    sketch, _ = session.rect_sketch("s", top.handle, (5.0, 0.0), 20.0, 5.0)  # overhangs: not inside, not covering
    boss = session.extrude("boss", sketch, 5.0)
    assert boss.modified_faces == (top.handle,)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(300.0)


def test_extrude_boss_that_covers_the_x_host_face_extends_the_correct_neighbours(session: Session) -> None:
    """Regression: pairing a host-face edge with the new base edge by *index* alone (``host_face.edges[i]``
    with ``base[i]``) is wrong for a non-Z host face, whose own edge order does not, in general, match the
    new sketch's base loop order -- it silently swaps which neighbouring face receives which area delta
    (observed 260/440 instead of the correct 200/500 on this exact case before the fix). Pairing must be
    geometric (matching edge endpoints within tolerance), so each of the 4 side faces adjacent to the +X
    face of a 20x20x8 block is extended by the edge it actually shares with the host face."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    faces = session.faces(block.feature)
    host = face_with_normal(faces, X)  # 20 (y) x 8 (z) = 160
    top, bottom = face_with_normal(faces, Z), face_with_normal(faces, NZ)  # share the 20 mm (y) edges
    plus_y, minus_y = face_with_normal(faces, Y), face_with_normal(faces, NY)  # share the 8 mm (z) edges
    sketch, _ = session.rect_sketch("s", host.handle, (0.0, 0.0), 20.0, 8.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert len(boss.created_faces) == 1  # only the far cap; no new side walls
    assert len(boss.modified_faces) == 4
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(500.0)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=bottom.handle)).area_mm2 == pytest.approx(500.0)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=plus_y.handle)).area_mm2 == pytest.approx(200.0)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=minus_y.handle)).area_mm2 == pytest.approx(
        200.0
    )


def test_extrude_boss_that_covers_the_y_host_face_extends_the_correct_neighbours(session: Session) -> None:
    """Same regression as the +X case: +Y is also not a Z-normal face, so its
    own edge order does not match the new base loop's order either."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    faces = session.faces(block.feature)
    host = face_with_normal(faces, Y)  # 20 (x) x 8 (z) = 160
    top, bottom = face_with_normal(faces, Z), face_with_normal(faces, NZ)  # share the 20 mm (x) edges
    plus_x, minus_x = face_with_normal(faces, X), face_with_normal(faces, NX)  # share the 8 mm (z) edges
    sketch, _ = session.rect_sketch("s", host.handle, (0.0, 0.0), 20.0, 8.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert len(boss.created_faces) == 1
    assert len(boss.modified_faces) == 4
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(500.0)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=bottom.handle)).area_mm2 == pytest.approx(500.0)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=plus_x.handle)).area_mm2 == pytest.approx(200.0)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=minus_x.handle)).area_mm2 == pytest.approx(
        200.0
    )


def test_extrude_boss_covers_creates_cap_boundary_edges_to_each_extended_side(session: Session) -> None:
    """The ``covers`` merge must give the new cap face real topology (not ``edges == []``): one
    boundary edge between the cap and each of the 4 extended side faces, at the far end. A subsequent
    chamfer on one of those edges must then run like any other 90 degree edge."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    faces = session.faces(block.feature)
    top_before = face_with_normal(faces, Z)
    side = face_with_normal(faces, X)
    sketch, _ = session.rect_sketch("s", top_before.handle, (0.0, 0.0), 20.0, 20.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert len(boss.created_faces) == 1
    cap = session.faces(boss.feature, ProvenanceRole.created).faces[0]
    assert cap.normal is not None and vec_close(cap.normal, Z)
    cap_edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=cap.handle)).edges
    assert len(cap_edges) == 4
    for neighbour_normal in (X, NX, Y, NY):
        neighbour = face_with_normal(session.faces(block.feature), neighbour_normal)
        shared = session.edges_between(cap, neighbour)
        assert len(shared) == 1
    edge = session.edges_between(cap, side)[0]
    chamfer = session.adapter.chamfer(ChamferRequest(operation_id="ch", edges=(edge.handle,), distance1_mm=2.0))
    assert len(chamfer.created_faces) == 1
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    # block (3200) + boss (2000) - chamfer wedge (0.5 * 2 * 2 * 20 = 40)
    assert mass.volume_mm3 == pytest.approx(3200.0 + 2000.0 - 40.0)


def test_extrude_boss_covers_registers_cap_corner_vertices_for_z_host(session: Session) -> None:
    """Before this fix, ``vertex_at``/``vertices_of`` returned nothing
    for a covering boss's cap corners because the fake never registered vertices there, and each extended
    side face's two vertical edges kept dangling at the old (pre-merge) height. Matches real FreeCAD (see
    ``tests/integration/freecad/test_boss_on_face.py``): the cap gets 4 real corner vertices."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    top_before = face_with_normal(session.faces(block.feature), Z)
    sketch, _ = session.rect_sketch("s", top_before.handle, (0.0, 0.0), 20.0, 20.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert boss.feature is not None
    cap = face_with_normal(session.faces(boss.feature, ProvenanceRole.created), Z)
    side_x = face_with_normal(session.faces(block.feature), X)
    side_ny = face_with_normal(session.faces(block.feature), NY)
    side_nx = face_with_normal(session.faces(block.feature), NX)
    side_y = face_with_normal(session.faces(block.feature), Y)
    corner = session.adapter.vertex_at(
        VertexAtRequest(operation_id="q", faces=(cap.handle, side_x.handle, side_ny.handle))
    ).vertices
    assert len(corner) == 1
    assert vec_close(corner[0].point, (20.0, 0.0, 13.0))
    other_corner = session.adapter.vertex_at(
        VertexAtRequest(operation_id="q", faces=(cap.handle, side_nx.handle, side_y.handle))
    ).vertices
    assert len(other_corner) == 1
    assert vec_close(other_corner[0].point, (0.0, 20.0, 13.0))
    cap_edge = session.edges_between(cap, side_x)[0]
    vertices = session.adapter.vertices_of(EdgeRequest(operation_id="q", edge=cap_edge.handle)).vertices
    assert len(vertices) == 2


def test_extrude_boss_covers_registers_cap_corner_vertices_for_x_host(session: Session) -> None:
    """Same fix, non-Z host face: the host's own edge/vertex order does not match the new base loop's, so
    this exercises the geometric (not index-based) corner pairing alongside the vertex registration."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    faces = session.faces(block.feature)
    host = face_with_normal(faces, X)
    top, plus_y = face_with_normal(faces, Z), face_with_normal(faces, Y)
    sketch, _ = session.rect_sketch("s", host.handle, (0.0, 0.0), 20.0, 8.0)
    boss = session.extrude("boss", sketch, 5.0)
    assert boss.feature is not None
    cap = face_with_normal(session.faces(boss.feature, ProvenanceRole.created), X)
    corner = session.adapter.vertex_at(
        VertexAtRequest(operation_id="q", faces=(cap.handle, top.handle, plus_y.handle))
    ).vertices
    assert len(corner) == 1
    assert vec_close(corner[0].point, (25.0, 20.0, 8.0))
    cap_edge = session.edges_between(cap, top)[0]
    vertices = session.adapter.vertices_of(EdgeRequest(operation_id="q", edge=cap_edge.handle)).vertices
    assert len(vertices) == 2


def test_extrude_boss_covers_closes_the_extended_side_face_edge_loop(session: Session) -> None:
    """Before the fix, an extended side face's two vertical edges still ended at the old (pre-merge)
    height, leaving a non-closed edge loop. After the fix each side face has 4 edges: the untouched
    bottom edge, the two vertical edges now extended to the new (13 mm) height, and the new cap-boundary
    edge."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    top_before = face_with_normal(session.faces(block.feature), Z)
    sketch, _ = session.rect_sketch("s", top_before.handle, (0.0, 0.0), 20.0, 20.0)
    session.extrude("boss", sketch, 5.0)
    side_x = face_with_normal(session.faces(block.feature), X)
    edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=side_x.handle)).edges
    assert len(edges) == 4
    lengths = sorted(e.length_mm for e in edges)
    assert lengths == pytest.approx([13.0, 13.0, 20.0, 20.0])
    verticals = [e for e in edges if e.length_mm == pytest.approx(13.0)]
    assert len(verticals) == 2
    for vertical in verticals:
        vertices = session.adapter.vertices_of(EdgeRequest(operation_id="q", edge=vertical.handle)).vertices
        assert len(vertices) == 2
        zs = sorted(v.point[2] for v in vertices)
        assert zs == pytest.approx([0.0, 13.0])


def test_extrude_boss_covers_keeps_vertex_count_at_eight(session: Session) -> None:
    """The block has 8 corner vertices before and after a covering boss: the 4 old top corners are
    replaced by 4 new cap corners, not left dangling (unregistered) or duplicated alongside them."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    doc = session.adapter._active  # noqa: SLF001 - the fingerprint/vertex store is an internal test hook (7.7)
    assert doc is not None
    assert len(doc.vertices) == 8
    top_before = face_with_normal(session.faces(block.feature), Z)
    sketch, _ = session.rect_sketch("s", top_before.handle, (0.0, 0.0), 20.0, 20.0)
    session.extrude("boss", sketch, 5.0)
    assert len(doc.vertices) == 8


def test_extrude_boss_covers_undo_restores_topology_byte_for_byte(session: Session) -> None:
    """Undo (the same ``_construct``/``_revert`` path a forced mid-build failure
    would take) must restore the pre-merge document exactly: the extended vertical edges go back to their
    old height, the removed corner vertices come back, and the new cap corner vertices/edges are gone."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    doc = session.adapter._active  # noqa: SLF001 - fingerprint is an internal test hook by design (7.7)
    assert doc is not None
    top_before = face_with_normal(session.faces(block.feature), Z)
    # Sketching first and snapshotting after: undo_last only reverts the extrude *feature* (doc.features),
    # not the separate sketch() call, so a sketch created afterwards would sit outside the round trip and
    # make the comparison fail for a reason unrelated to this fix. Isolating to the extrude's own topology
    # (faces/edges/vertices/primitives) is what this test is actually about.
    sketch, _ = session.rect_sketch("s", top_before.handle, (0.0, 0.0), 20.0, 20.0)
    before = doc.fingerprint()
    boss = session.extrude("boss", sketch, 5.0)
    assert boss.feature is not None
    assert doc.fingerprint() != before
    reverted = session.adapter.undo_last(Empty(operation_id="u"))
    assert reverted.reverted == boss.feature
    assert doc.fingerprint() == before


def test_extrude_boss_covers_extends_only_the_shared_edges_bbox_for_z_host(session: Session) -> None:
    """Regression: unioning a side face's bbox with the *entire* cap loop (instead
    of only the two cap points on the edge that face actually shares with the host) made every side face
    report a bbox spanning the whole footprint. After a +Z covering boss on a 20x20x8 block, each side
    face's bbox must stay planar along its own normal, only growing in Z to the new height."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    top_before = face_with_normal(session.faces(block.feature), Z)
    sketch, _ = session.rect_sketch("s", top_before.handle, (0.0, 0.0), 20.0, 20.0)
    session.extrude("boss", sketch, 5.0)
    faces = session.faces(block.feature)
    side_x = face_with_normal(faces, X)
    assert side_x.bbox == BoundingBox(min=(20.0, 0.0, 0.0), max=(20.0, 20.0, 13.0))
    side_ny = face_with_normal(faces, NY)
    assert side_ny.bbox == BoundingBox(min=(0.0, 0.0, 0.0), max=(20.0, 0.0, 13.0))


def test_extrude_boss_covers_extends_only_the_shared_edges_bbox_for_x_host(session: Session) -> None:
    """Same regression, non-Z host face: after a +X covering boss (depth 4) on a 20x20x8 block, the top
    face's bbox grows only along +X to the new (24 mm) extent, and the +Y face's bbox grows the same way
    while staying planar at y = 20."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    faces = session.faces(block.feature)
    host = face_with_normal(faces, X)
    sketch, _ = session.rect_sketch("s", host.handle, (0.0, 0.0), 20.0, 8.0)
    session.extrude("boss", sketch, 4.0)
    faces = session.faces(block.feature)
    top = face_with_normal(faces, Z)
    assert top.bbox == BoundingBox(min=(0.0, 0.0, 8.0), max=(24.0, 20.0, 8.0))
    plus_y = face_with_normal(faces, Y)
    assert plus_y.bbox == BoundingBox(min=(0.0, 20.0, 0.0), max=(24.0, 20.0, 8.0))


def test_find_shared_line_edge_raises_geometry_failed_on_ambiguous_match(session: Session) -> None:
    """``_find_shared_line_edge`` (used to find the vertical corner edge to extend during a covering merge
    extrude) must not silently return an arbitrary match when a document has more than one line edge
    shared by the same two faces -- that would extend/remove the wrong edge instead of surfacing the
    inconsistent state."""
    from cotaforge.adapters.fake.builders import _find_shared_line_edge

    session.new_part()
    block = _block(session)
    assert block.feature is not None
    doc = session.adapter._active  # noqa: SLF001 - internal test hook, matches existing tests in this file
    assert doc is not None
    face_a, face_b = list(doc.faces)[:2]
    edges = list(doc.edges.values())
    edge1, edge2 = edges[0], edges[1]
    edge1.curve = CurveType.line
    edge1.faces = (face_a, face_b)
    edge2.curve = CurveType.line
    edge2.faces = (face_a, face_b)
    with pytest.raises(AdapterError) as excinfo:
        _find_shared_line_edge(doc, face_a, face_b, "op")
    assert excinfo.value.info.code is ErrorCode.geometry_failed
    assert "ambiguous shared edge" in excinfo.value.info.message


def test_extrude_boss_covers_x_host_then_overhanging_top_sketch_is_a_plan_error() -> None:
    """Executor-level regression: before the bbox fix above, ``ExpectedComputer``'s
    ``_op_sketch`` read the corrupted (whole-footprint) bbox via ``face_info`` and silently classified an
    overhanging rect as ``covers``/``inside``. After a +X covering boss (depth 4) on a 20x20x8 block, the
    real top face only extends to x = 24 (see the unit test above); a sketch on that face whose rect
    reaches x = 22 (half-width 11 centred at x = 11, i.e. up to 22 of the original 0..20 footprint) still
    overhangs the *original* 20 mm-wide top face and must fail as a ``plan_error``, not be silently
    accepted."""
    from cotaforge.adapters.fake.adapter import FakeAdapter
    from cotaforge.contract import ConnectRequest
    from cotaforge.executor.config import ExecutorConfig
    from cotaforge.executor.executor import Executor
    from cotaforge.executor.trace import ExecutorState, StepStatus
    from cotaforge.ir import parse_plan
    from cotaforge.verification.verifier import Verifier

    plan_dict = {
        "id": "block_x_cover_then_overhang",
        "kind": "part",
        "description": "20x20x8 block, +X covering boss, then an overhanging sketch on the top face",
        "operations": [
            {
                "id": "base_sketch",
                "op": "sketch",
                "intent": "Outline of the block on XY",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "outline", "center": [10, 10], "width": 20, "height": 20}],
            },
            {"id": "base", "op": "extrude", "intent": "Block thickness", "sketch": "base_sketch", "depth_mm": 8},
            {
                "id": "boss_sketch",
                "op": "sketch",
                "intent": "Rect covering the +X host face",
                "on": {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}},
                "entities": [{"kind": "rect", "name": "boss", "center": [0, 0], "width": 20, "height": 8}],
            },
            {
                "id": "boss",
                "op": "extrude",
                "intent": "Boss covering the +X host face",
                "sketch": "boss_sketch",
                "depth_mm": 4,
            },
            {
                "id": "overhang_sketch",
                "op": "sketch",
                "intent": "Rect overhanging the original top face",
                "on": {"kind": "face", "from_op": "boss", "select": {"normal": [0, 0, 1]}},
                "entities": [{"kind": "rect", "name": "overhang", "center": [11, 10], "width": 22, "height": 20}],
            },
            {
                "id": "overhang",
                "op": "extrude",
                "intent": "On-face extrude of the overhanging rect",
                "sketch": "overhang_sketch",
                "depth_mm": 2,
            },
        ],
    }
    plan = parse_plan(plan_dict)
    fake = FakeAdapter()
    fake.connect(ConnectRequest(operation_id="connect"))
    ex = Executor(plan, fake, Verifier(), ExecutorConfig(), {})
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    overhang = trace.step("overhang")
    assert overhang is not None and overhang.status == StepStatus.plan_error and overhang.attempts == ()
    assert overhang.plan_error is not None and "overhangs the host face" in overhang.plan_error


# --- cut ---------------------------------------------------------------------------------------------


def test_blind_cut_on_a_side_face(session: Session) -> None:
    session.new_part()
    bracket = session.l_bracket()
    assert bracket.feature is not None
    side = next(
        f for f in session.faces(bracket.feature).faces if f.normal == X and close(f.center[0], 60.0)
    )  # 10 x 20
    sketch, area = session.rect_sketch("s", side.handle, (0.0, 0.0), 4.0, 6.0)
    cut = session.adapter.cut(CutRequest(operation_id="pocket", sketch=SketchHandle(id=sketch), depth_mm=3.0))
    assert cut.feature is not None
    assert len(cut.created_faces) == 5
    assert len(cut.created_edges) == 12
    assert cut.modified_faces == (side.handle,)
    faces = session.faces(cut.feature, ProvenanceRole.created)
    bottom = face_with_normal(faces, X)  # pocket floor faces back out along +X
    assert vec_close(bottom.center, (57.0, 5.0, 10.0))
    inward = face_with_normal(faces, NY)  # wall at v = +2 (face frame u = Y, v = Z): 6 tall x 3 deep
    assert inward.area_mm2 == pytest.approx(6.0 * 3.0)
    assert vec_close(inward.center, (58.5, 7.0, 10.0))
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(18000.0 - 24 * 3.0)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=side.handle)).area_mm2 == pytest.approx(
        200 - 24
    )
    assert all(
        e.convex is False for e in session.adapter.edges_of(FaceRequest(operation_id="q", face=bottom.handle)).edges
    )


def test_blind_cut_rejects_a_smuggled_negative_depth(session: Session) -> None:
    """Defense in depth: CutRequest.depth_mm already has gt=0, but a negative value that bypasses
    request validation (e.g. via ``model_construct``) must not silently flip the cut into a boss and
    add material instead of removing it."""
    session.new_part()
    session.plate()
    sketch = session.circle_sketch("s", PlaneName.XY, (20.0, 20.0), 10.0)
    request = CutRequest.model_construct(operation_id="pocket", sketch=SketchHandle(id=sketch), depth_mm=-5.0)
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.cut(request)
    assert excinfo.value.info.code is ErrorCode.invalid_request
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(48000.0)
    assert len(session.adapter.feature_tree(Empty(operation_id="t")).nodes) == 1


def test_cut_scope_naming_the_sketch_own_body_is_accepted(session: Session) -> None:
    """``scope`` naming exactly the body the cut already targets is a no-op: the same geometry results
    as the same cut without ``scope`` at all (the fake, like FreeCAD, never had a second body to pick)."""
    from cotaforge.adapters.fake.adapter import FakeAdapter
    from cotaforge.contract import ConnectRequest

    baseline_adapter = FakeAdapter()
    baseline_adapter.connect(ConnectRequest(operation_id="connect"))
    baseline = Session(baseline_adapter)
    baseline.new_part()
    baseline.plate()
    baseline_sketch = baseline.circle_sketch("s", PlaneName.XY, (20.0, 20.0), 10.0)
    baseline.adapter.cut(CutRequest(operation_id="pocket", sketch=SketchHandle(id=baseline_sketch), depth_mm=3.0))
    expected = baseline.adapter.mass_properties(MassRequest(operation_id="m"))

    session.new_part()
    plate = session.plate()
    sketch = session.circle_sketch("s", PlaneName.XY, (20.0, 20.0), 10.0)
    cut = session.adapter.cut(
        CutRequest(
            operation_id="pocket",
            sketch=SketchHandle(id=sketch),
            depth_mm=3.0,
            scope=(plate.created_bodies[0],),
        )
    )
    assert cut.feature is not None
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(expected.volume_mm3)
    assert mass.volume_mm3 < 48000.0


def test_cut_scope_naming_a_different_body_is_rejected(session: Session) -> None:
    session.new_part()
    session.plate()
    sketch = session.circle_sketch("s", PlaneName.XY, (20.0, 20.0), 10.0)
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.cut(
            CutRequest(
                operation_id="pocket",
                sketch=SketchHandle(id=sketch),
                depth_mm=3.0,
                scope=(BodyHandle(id="body:nope"),),
            )
        )
    assert excinfo.value.info.code is ErrorCode.invalid_request
    assert len(session.adapter.feature_tree(Empty(operation_id="t")).nodes) == 1


def test_cut_that_removes_more_area_than_the_face_has_is_geometry_failed(session: Session) -> None:
    """``modify_face_area`` must guard against a face's ``area_mm2`` going
    negative -- a cut whose profile area exceeds its entry face's own area (the fake does not validate
    footprint containment) must raise ``geometry_failed`` rather than silently
    storing a negative area."""
    session.new_part()
    block = _block(session)
    assert block.feature is not None
    side = face_with_normal(session.faces(block.feature), X)  # 20 (y) x 8 (z) = 160 mm2
    sketch, area = session.rect_sketch("s", side.handle, (0.0, 0.0), 30.0, 30.0)  # 900 mm2 > 160 mm2
    assert area == pytest.approx(900.0)
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.cut(CutRequest(operation_id="oversized", sketch=SketchHandle(id=sketch), depth_mm=3.0))
    assert excinfo.value.info.code is ErrorCode.geometry_failed
    assert excinfo.value.info.recoverable is True


def test_through_all_cut_from_a_plane_spans_the_body(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    sketch = session.circle_sketch("s", PlaneName.XY, (20.0, 20.0), 10.0)
    cut = session.adapter.cut(
        CutRequest(
            operation_id="slot",
            sketch=SketchHandle(id=sketch),
            end_condition=EndCondition.through_all,
            direction=Direction.positive,
        )
    )
    assert cut.feature is not None
    lateral = cylindrical_faces(session.faces(cut.feature, ProvenanceRole.created))
    assert len(lateral) == 1
    assert lateral[0].bbox.extent_along(Z) == pytest.approx(8.0)
    assert len(cut.created_edges) == 2  # entry circle on the bottom face, exit circle on the top face
    plate_faces = session.faces(plate.feature)
    assert set(cut.modified_faces) == {
        face_with_normal(plate_faces, Z).handle,
        face_with_normal(plate_faces, NZ).handle,
    }
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(
        48000 - math.pi * 25 * 8
    )


def test_through_all_cut_both_directions_from_a_mid_plane(session: Session) -> None:
    session.new_part()
    sketch, _ = session.rect_sketch("s", PlaneName.XY, (0.0, 0.0), 20.0, 20.0)
    session.extrude("block", sketch, 10.0, both_directions=True)  # z in [-10, 10]
    slot = session.circle_sketch("slot_sketch", PlaneName.XY, (0.0, 0.0), 4.0)
    cut = session.adapter.cut(
        CutRequest(
            operation_id="slot",
            sketch=SketchHandle(id=slot),
            end_condition=EndCondition.through_all,
            both_directions=True,
        )
    )
    assert cut.feature is not None
    lateral = cylindrical_faces(session.faces(cut.feature, ProvenanceRole.created))[0]
    assert lateral.bbox.min[2] == pytest.approx(-10.0)
    assert lateral.bbox.max[2] == pytest.approx(10.0)
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(
        8000 - math.pi * 4 * 20
    )


def test_through_all_cut_with_no_material_is_geometry_failed(session: Session) -> None:
    session.new_part()
    session.plate()
    sketch = session.circle_sketch("s", PlaneName.XY, (20.0, 20.0), 10.0)
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.cut(
            CutRequest(operation_id="miss", sketch=SketchHandle(id=sketch), end_condition=EndCondition.through_all)
        )
    assert excinfo.value.info.code is ErrorCode.geometry_failed
    assert excinfo.value.info.recoverable is True
    assert len(session.adapter.feature_tree(Empty(operation_id="t")).nodes) == 1


# --- hole ----------------------------------------------------------------------------------------------


def _hole(session: Session, face: FaceHandle, position: tuple[float, float], **kwargs: object) -> tuple:
    fields: dict[str, object] = {
        "operation_id": "hole_1",
        "face": face,
        "position": position,
        "standard": HoleStandard.iso_clearance,
        "size": "M6",
        "diameter_mm": 6.6,
        "end_condition": EndCondition.through_all,
    }
    fields.update(kwargs)
    request = HoleRequest(**fields)  # type: ignore[arg-type]
    result = session.adapter.hole(request)
    assert result.feature is not None
    return result, session.faces(result.feature, ProvenanceRole.created)


def test_through_hole_geometry_and_provenance(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    result, faces = _hole(session, top.handle, (-40.0, -20.0))  # face frame origin = (50, 30, 8), u = X, v = Y
    assert len(faces.faces) == 1
    lateral = faces.faces[0]
    assert lateral.surface is SurfaceType.cylinder
    assert lateral.radius == pytest.approx(3.3)
    assert lateral.axis is not None
    assert vec_close(lateral.axis.direction, NZ)
    assert vec_close(lateral.axis.origin, (10.0, 10.0, 8.0))
    assert lateral.bbox.extent_along(Z) == pytest.approx(8.0)
    assert lateral.area_mm2 == pytest.approx(2 * math.pi * 3.3 * 8)
    assert top.handle in result.modified_faces
    assert len(result.created_edges) == 2
    assert all(
        e.curve is CurveType.circle
        for e in session.adapter.edges_of(FaceRequest(operation_id="q", face=lateral.handle)).edges
    )
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(48000 - HOLE_VOLUME)
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(
        6000 - math.pi * 3.3**2
    )
    top_after = session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle))
    assert top_after.provenance.modified_by == (result.feature,)


def test_blind_hole_has_bottom_face_and_one_circle(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    result, faces = _hole(session, top.handle, (0.0, 0.0), end_condition=EndCondition.blind, depth_mm=5.0)
    assert len(faces.faces) == 2
    bottom = face_with_normal(faces, Z)
    assert vec_close(bottom.center, (50.0, 30.0, 3.0))
    assert len(result.created_edges) == 2
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(
        48000 - math.pi * 3.3**2 * 5
    )


def test_hole_on_a_yz_face_uses_the_face_frame(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    side = face_with_normal(
        session.faces(plate.feature), X
    )  # center (100, 30, 4); u = normalise(Y - 0) = Y, v = X x Y = Z
    _, faces = _hole(
        session, side.handle, (10.0, -2.0), end_condition=EndCondition.blind, depth_mm=4.0, diameter_mm=2.0
    )
    lateral = cylindrical_faces(faces)[0]
    assert lateral.axis is not None
    assert vec_close(lateral.axis.origin, (100.0, 40.0, 2.0))
    assert vec_close(lateral.axis.direction, NX)


# --- patterns ---------------------------------------------------------------------------------------


def test_linear_pattern_2x2_of_a_hole(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    hole, _ = _hole(session, top.handle, (-40.0, -20.0))
    pattern = session.adapter.linear_pattern(
        LinearPatternRequest(
            operation_id="holes",
            features=(hole.feature,),
            direction=X,
            spacing_mm=80.0,
            count=2,
            direction2=Y,
            spacing2_mm=40.0,
            count2=2,
        )
    )
    assert pattern.instance_count == 4
    assert len(pattern.created_faces) == 3
    assert len(pattern.created_edges) == 6
    assert pattern.modified_faces == hole.modified_faces
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(48000 - 4 * HOLE_VOLUME)  # 46905.22
    assert vec_close(mass.centroid, (50.0, 30.0, 4.0))
    centers = sorted(
        (round(f.axis.origin[0], 6), round(f.axis.origin[1], 6))
        for f in session.faces(pattern.feature, ProvenanceRole.created).faces  # type: ignore[arg-type]
        if f.axis is not None
    )
    assert centers == [(10.0, 50.0), (90.0, 10.0), (90.0, 50.0)]
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(
        6000 - 4 * math.pi * 3.3**2
    )
    top_edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=top.handle)).edges
    assert sum(1 for e in top_edges if e.curve is CurveType.circle) == 4


def test_circular_pattern_about_a_cylindrical_face_axis(session: Session) -> None:
    session.new_part()
    disc = session.extrude("disc", session.circle_sketch("s", PlaneName.XY, (0.0, 0.0), 80.0), 10.0)
    assert disc.feature is not None
    top = face_with_normal(session.faces(disc.feature), Z)
    centre, centre_faces = _hole(session, top.handle, (0.0, 0.0), diameter_mm=20.0, operation_id="centre")
    bore = centre_faces.faces[0]
    bolt, _ = _hole(session, top.handle, (30.0, 0.0), diameter_mm=8.0, operation_id="bolt")
    assert bore.axis is not None
    pattern = session.adapter.circular_pattern(
        CircularPatternRequest(operation_id="bolts", features=(bolt.feature,), axis=bore.axis, count=6)
    )
    assert pattern.instance_count == 6
    assert len(pattern.created_faces) == 5
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(math.pi * 10 * (1600 - 100 - 6 * 16))  # 44108.4
    assert vec_close(mass.centroid, (0.0, 0.0, 5.0), tol=1e-6)
    origins = [f.axis.origin for f in session.faces(pattern.feature, ProvenanceRole.created).faces if f.axis]  # type: ignore[arg-type]
    assert all(math.hypot(o[0], o[1]) == pytest.approx(30.0) for o in origins)
    assert any(vec_close(o, (-30.0, 0.0, 10.0), tol=1e-9) for o in origins)


# --- revolve --------------------------------------------------------------------------------------------


def test_stepped_shaft_revolve_pappus_volume_faces_and_edges(session: Session) -> None:
    session.new_part()
    profile = [(0.0, 0.0), (40.0, 0.0), (40.0, 6.0), (20.0, 6.0), (20.0, 10.0), (0.0, 10.0)]
    sketch = session.polygon_sketch("s", PlaneName.XY, profile)
    result = session.adapter.revolve(
        RevolveRequest(operation_id="shaft", sketch=SketchHandle(id=sketch), axis=Axis(origin=(0, 0, 0), direction=X))
    )
    assert result.feature is not None
    faces = session.faces(result.feature, ProvenanceRole.created).faces
    assert len(faces) == 5
    lateral = cylindrical_faces(faces)
    assert sorted(f.radius for f in lateral) == pytest.approx([6.0, 10.0])  # type: ignore[type-var]
    big = next(f for f in lateral if f.radius == pytest.approx(10.0))
    assert big.axis is not None and vec_close(big.axis.direction, X)
    assert big.area_mm2 == pytest.approx(2 * math.pi * 10 * 20)
    assert vec_close(big.center, (10.0, 0.0, 0.0))
    plus_x = [f for f in faces if f.normal is not None and vec_close(f.normal, X)]
    assert sorted(f.area_mm2 for f in plus_x) == pytest.approx([math.pi * 36, math.pi * (100 - 36)])
    assert face_with_normal(faces, NX).area_mm2 == pytest.approx(math.pi * 100)
    assert len(result.created_edges) == 4
    assert all(
        e.curve is CurveType.circle
        for e in (session.adapter.edge_info(EdgeRequest(operation_id="q", edge=h)) for h in result.created_edges)
    )
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(math.pi * (100 * 20 + 36 * 20))  # 8545.13
    assert vec_close(mass.centroid, ((20000 + 21600) / 2720, 0.0, 0.0))
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(0.0, -10.0, -10.0), max=(40.0, 10.0, 10.0)
    )


def test_partial_revolve_scales_volume_and_adds_caps(session: Session) -> None:
    session.new_part()
    sketch, _ = session.rect_sketch("s", PlaneName.XY, (20.0, 5.0), 10.0, 10.0)  # square 15..25 x 0..10
    result = session.adapter.revolve(
        RevolveRequest(
            operation_id="ring",
            sketch=SketchHandle(id=sketch),
            axis=Axis(origin=(0, 0, 0), direction=Y),
            angle_deg=90.0,
        )
    )
    assert result.feature is not None
    assert len(result.created_faces) == 6  # 4 profile segments + 2 caps
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(2 * math.pi * 20 * 100 / 4)


@pytest.mark.parametrize(
    ("entities", "axis", "message"),
    [
        ((CircleEntity(name="c", center=(20, 0), diameter=10),), Axis(origin=(0, 0, 0), direction=X), "tori"),
        (
            (
                LineEntity(name="a", start=(0, 0), end=(10, 0)),
                LineEntity(name="b", start=(10, 0), end=(10, 5)),
                LineEntity(name="c", start=(10, 5), end=(0, 0)),
            ),
            Axis(origin=(0, 0, 0), direction=X),
            "cones",
        ),
        (
            (RectEntity(name="r", center=(20, 5), width=10, height=10),),
            Axis(origin=(0, 0, 0), direction=Z),
            "sketch plane",
        ),
        (
            (RectEntity(name="r", center=(0, 5), width=10, height=10),),
            Axis(origin=(0, 0, 0), direction=Y),
            "cross the axis",
        ),
    ],
)
def test_revolve_unsupported_cases(session: Session, entities: tuple, axis: Axis, message: str) -> None:
    session.new_part()
    sketch = session.adapter.sketch(SketchRequest(operation_id="s", on=PlaneName.XY, entities=entities)).sketch
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.revolve(RevolveRequest(operation_id="rev", sketch=sketch, axis=axis))
    assert excinfo.value.info.code is ErrorCode.not_supported
    assert message in excinfo.value.info.message
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == 0.0
    assert session.adapter.feature_tree(Empty(operation_id="t")).nodes == ()


# --- chamfer and fillet ------------------------------------------------------------------------------


def test_unequal_chamfer_on_a_plate_edge(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    faces = session.faces(plate.feature)
    top, side = face_with_normal(faces, Z), face_with_normal(faces, X)
    edge = session.edges_between(top, side)[0]  # 60 mm along Y at x = 100, z = 8
    result = session.adapter.chamfer(
        ChamferRequest(
            operation_id="ch", edges=(edge.handle,), distance1_mm=2.0, distance2_mm=3.0, distance1_face=top.handle
        )
    )
    assert result.feature is not None
    assert len(result.created_faces) == 1
    assert len(result.created_edges) == 2
    assert set(result.modified_faces) == {top.handle, side.handle}
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(48000 - 180.0)  # 2 * 3 / 2 * 60
    expected_centroid_x = (48000 * 50 - 180 * (100 - 2 / 3)) / (48000 - 180)
    expected_centroid_z = (48000 * 4 - 180 * (8 - 1.0)) / (48000 - 180)
    assert vec_close(mass.centroid, (expected_centroid_x, 30.0, expected_centroid_z))
    face = session.faces(result.feature, ProvenanceRole.created).faces[0]
    assert face.normal is not None
    assert vec_close(face.normal, (3 / math.sqrt(13), 0.0, 2 / math.sqrt(13)))  # d1 * n_top + d2 * n_side
    assert face.area_mm2 == pytest.approx(60 * math.sqrt(13))
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(
        6000 - 120
    )
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=side.handle)).area_mm2 == pytest.approx(
        480 - 180
    )
    assert session.edges_between(top, side) == ()
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.edge_info(EdgeRequest(operation_id="q", edge=edge.handle))
    assert excinfo.value.info.code is ErrorCode.invalid_reference
    new_edges = session.edges_between(top, face)
    assert len(new_edges) == 1 and new_edges[0].length_mm == pytest.approx(60.0)


def test_chamfer_distance1_face_must_be_adjacent_and_required_with_distance2(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    faces = session.faces(plate.feature)
    edge = session.edges_between(face_with_normal(faces, Z), face_with_normal(faces, X))[0]
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.chamfer(
            ChamferRequest(operation_id="ch", edges=(edge.handle,), distance1_mm=2.0, distance2_mm=3.0)
        )
    assert excinfo.value.info.code is ErrorCode.invalid_request
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.chamfer(
            ChamferRequest(
                operation_id="ch",
                edges=(edge.handle,),
                distance1_mm=2.0,
                distance2_mm=3.0,
                distance1_face=face_with_normal(faces, NZ).handle,
            )
        )
    assert excinfo.value.info.code is ErrorCode.invalid_request
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(48000.0)


def test_convex_fillet_on_a_plate_edge(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    faces = session.faces(plate.feature)
    top, side = face_with_normal(faces, Z), face_with_normal(faces, X)
    edge = session.edges_between(top, side)[0]
    result = session.adapter.fillet(FilletRequest(operation_id="f", edges=(edge.handle,), radius_mm=2.0))
    assert result.feature is not None
    face = session.faces(result.feature, ProvenanceRole.created).faces[0]
    assert face.surface is SurfaceType.cylinder
    assert face.radius == pytest.approx(2.0)
    assert face.axis is not None
    assert vec_close(face.axis.origin, (98.0, 0.0, 6.0))
    assert vec_close(face.axis.direction, Y)
    assert face.area_mm2 == pytest.approx(math.pi / 2 * 2 * 60)
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(48000 - (1 - math.pi / 4) * 4 * 60)  # -51.504
    new_edges = session.edges_between(top, face)
    assert len(new_edges) == 1 and new_edges[0].convex is False
    assert session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle)).area_mm2 == pytest.approx(
        6000 - 120
    )


def test_concave_fillet_on_the_inner_bracket_edge_adds_material(session: Session) -> None:
    session.new_part()
    bracket = session.l_bracket()
    assert bracket.feature is not None
    faces = session.faces(bracket.feature)
    leg_top = next(f for f in faces.faces if f.normal == Y and close(f.center[1], 10.0))  # top of the base leg
    plus_x = [f for f in faces.faces if f.normal is not None and vec_close(f.normal, X)]
    inner = next(f for f in plus_x if close(f.center[0], 10.0))
    edge = session.edges_between(leg_top, inner)[0]
    assert edge.convex is False
    assert edge.length_mm == pytest.approx(20.0)
    result = session.adapter.fillet(FilletRequest(operation_id="f", edges=(edge.handle,), radius_mm=2.0))
    assert result.feature is not None
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(18000 + (1 - math.pi / 4) * 4 * 20)
    face = session.faces(result.feature, ProvenanceRole.created).faces[0]
    assert face.axis is not None
    assert vec_close(face.axis.origin, (12.0, 12.0, 0.0))


def test_fillet_on_non_planar_or_circle_edge_is_not_supported(session: Session) -> None:
    session.new_part()
    disc = session.extrude("disc", session.circle_sketch("s", PlaneName.XY, (0.0, 0.0), 20.0), 5.0)
    assert disc.feature is not None
    circle = session.adapter.edge_info(EdgeRequest(operation_id="q", edge=disc.created_edges[0]))
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.fillet(FilletRequest(operation_id="f", edges=(circle.handle,), radius_mm=1.0))
    assert excinfo.value.info.code is ErrorCode.not_supported
    assert len(session.adapter.feature_tree(Empty(operation_id="t")).nodes) == 1


# --- box, cylinder, move_body -----------------------------------------------------------------------


def test_box_and_cylinder_are_separate_bodies(session: Session) -> None:
    session.new_part()
    box = session.adapter.box(BoxRequest(operation_id="box", size=(100.0, 60.0, 20.0)))
    cyl = session.adapter.cylinder(
        CylinderRequest(
            operation_id="cyl", diameter_mm=30.0, height_mm=20.0, axis=Axis(origin=(50, 30, 0), direction=Z)
        )
    )
    assert box.created_bodies != cyl.created_bodies
    assert len(box.created_faces) == 6 and len(cyl.created_faces) == 3
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(120000 + math.pi * 225 * 20)
    body_mass = session.adapter.mass_properties(MassRequest(operation_id="m", body=cyl.created_bodies[0]))
    assert body_mass.volume_mm3 == pytest.approx(math.pi * 225 * 20)
    assert vec_close(body_mass.centroid, (50.0, 30.0, 10.0))
    assert len(cylindrical_faces(session.faces(cyl.feature))) == 1  # type: ignore[arg-type]


def test_box_with_rotation_transform(session: Session) -> None:
    session.new_part()
    t = Transform.from_axis_angle(Z, 90.0, origin=(10.0, 0.0, 0.0))
    box = session.adapter.box(BoxRequest(operation_id="box", size=(20.0, 10.0, 5.0), transform=t))
    assert box.feature is not None
    bbox = session.adapter.bounding_box(MassRequest(operation_id="b"))
    assert vec_close(bbox.min, (0.0, 0.0, 0.0), tol=1e-9)
    assert vec_close(bbox.max, (10.0, 20.0, 5.0), tol=1e-9)
    faces = session.faces(box.feature)
    assert face_with_normal(faces, NX).area_mm2 == pytest.approx(100.0)  # the box's +Y face (20 x 5) now faces -X
    assert face_with_normal(faces, Y).area_mm2 == pytest.approx(50.0)  # the box's +X face (10 x 5) now faces +Y


def test_move_body_translates_everything_and_marks_faces_modified(session: Session) -> None:
    session.new_part()
    box = session.adapter.box(BoxRequest(operation_id="box", size=(10.0, 10.0, 10.0)))
    moved = session.adapter.move_body(
        MoveBodyRequest(operation_id="mv", body=box.created_bodies[0], transform=Transform(origin=(5.0, 0.0, 0.0)))
    )
    assert moved.created_faces == ()
    assert set(moved.modified_faces) == set(box.created_faces)
    assert session.adapter.bounding_box(MassRequest(operation_id="b")) == BoundingBox(
        min=(5.0, 0.0, 0.0), max=(15.0, 10.0, 10.0)
    )
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(1000.0)
    assert vec_close(mass.centroid, (10.0, 5.0, 5.0))
    edge = session.adapter.edge_info(EdgeRequest(operation_id="q", edge=box.created_edges[0]))
    assert edge.bbox.min[0] >= 5.0
