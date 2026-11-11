"""Components, mates and interference on a pin-and-block assembly."""

import math

import pytest

from cotaforge.adapters.fake.model import vdot, vlength, vscale, vsub
from cotaforge.contract import (
    AdapterError,
    Axis,
    BoundingBox,
    BoxRequest,
    ComponentRequest,
    CylinderRequest,
    DocumentHandle,
    DocumentKind,
    Empty,
    EndCondition,
    ErrorCode,
    FaceHandle,
    FaceRequest,
    FixComponentRequest,
    HoleRequest,
    HoleStandard,
    InsertComponentRequest,
    InterferenceRequest,
    MassRequest,
    MateAlignment,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    OperationResult,
    ProvenanceRole,
    SetActiveDocumentRequest,
    Side,
    SurfaceType,
    Transform,
)
from tests.unit.fake.conftest import Session, close, cylindrical_faces, face_with_normal, vec_close

Z = (0.0, 0.0, 1.0)
NZ = (0.0, 0.0, -1.0)
BLOCK_VOLUME = 30.0 * 30.0 * 20.0 - math.pi * 3.25**2 * 20.0  # 17336.34
PIN_VOLUME = math.pi * 9.0 * 40.0  # 1130.97


def _block(session: Session, *, bore: bool = True) -> str:
    doc = session.new_part("block")
    box = session.adapter.box(BoxRequest(operation_id="block_box", size=(30.0, 30.0, 20.0)))
    if bore:
        assert box.feature is not None
        top = face_with_normal(session.faces(box.feature), Z)
        session.adapter.hole(
            HoleRequest(
                operation_id="bore",
                face=top.handle,
                position=(0.0, 0.0),
                standard=HoleStandard.simple,
                diameter_mm=6.5,
                end_condition=EndCondition.through_all,
            )
        )
    return doc


def _pin(session: Session) -> str:
    doc = session.new_part("pin")
    session.adapter.cylinder(
        CylinderRequest(
            operation_id="pin_cyl", diameter_mm=6.0, height_mm=40.0, axis=Axis(origin=(0, 0, 0), direction=Z)
        )
    )
    return doc


def _assembly(
    session: Session, *, bore: bool = True, pin_transform: Transform | None = None
) -> tuple[OperationResult, OperationResult]:
    pin_transform = pin_transform or Transform.identity()
    block, pin = _block(session, bore=bore), _pin(session)
    session.new_part("asm", DocumentKind.assembly)
    block_c = session.adapter.insert_component(
        InsertComponentRequest(operation_id="ins_block", part=DocumentHandle(id=block), transform=Transform.identity())
    )
    assert block_c.component is not None
    session.adapter.fix_component(FixComponentRequest(operation_id="fix", component=block_c.component))
    pin_c = session.adapter.insert_component(
        InsertComponentRequest(operation_id="ins_pin", part=DocumentHandle(id=pin), transform=pin_transform)
    )
    return block_c, pin_c


def test_insert_component_exposes_prefixed_faces_and_adds_volume(session: Session) -> None:
    block_c, pin_c = _assembly(session)
    assert block_c.component is not None and pin_c.component is not None
    assert block_c.component.id != pin_c.component.id
    faces = session.faces(block_c.feature)  # type: ignore[arg-type]
    assert len(faces.faces) == 7
    assert all(f.handle.id.startswith(f"{block_c.component.id}/face:") for f in faces.faces)
    assert all(f.component == block_c.component for f in faces.faces)
    assert all(f.provenance.created_by == block_c.feature for f in faces.faces)
    assert session.faces(block_c.feature, ProvenanceRole.modified).faces == ()  # type: ignore[arg-type]
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(BLOCK_VOLUME + PIN_VOLUME)
    pin_mass = session.adapter.mass_properties(MassRequest(operation_id="m", component=pin_c.component))
    assert pin_mass.volume_mm3 == pytest.approx(PIN_VOLUME)
    assert session.adapter.component_transform(ComponentRequest(operation_id="q", component=pin_c.component)) == (
        Transform.identity()
    )
    tree = session.adapter.feature_tree(Empty(operation_id="t"))
    assert [n.kind for n in tree.nodes] == ["component", "fix_component", "component"]


def test_insert_component_counts_the_faces_and_edges_of_its_part_instead_of_listing_them(session: Session) -> None:
    """As the CAD adapter: a part has tens of thousands of faces for a vendor part, and the executor describes every
    face and edge a step lists; ``faces_of`` of the step lists them when asked."""
    block_c, _ = _assembly(session)
    assert block_c.created_faces == () and block_c.created_edges == ()
    assert block_c.face_count == 7 and block_c.edge_count is not None and block_c.edge_count > 0
    assert len(block_c.created_bodies) == 1
    assert len(session.faces(block_c.feature).faces) == 7  # type: ignore[arg-type]


def test_insert_component_with_transform_moves_bbox(session: Session) -> None:
    block = _block(session)
    session.new_part("asm", DocumentKind.assembly)
    result = session.adapter.insert_component(
        InsertComponentRequest(
            operation_id="ins", part=DocumentHandle(id=block), transform=Transform(origin=(100.0, 0.0, 5.0))
        )
    )
    assert result.component is not None
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=result.component))
    assert box == BoundingBox(min=(100.0, 0.0, 5.0), max=(130.0, 30.0, 25.0))
    top = face_with_normal(session.faces(result.feature), Z)  # type: ignore[arg-type]
    assert vec_close(top.center, (115.0, 15.0, 25.0))
    bore = cylindrical_faces(session.faces(result.feature))[0]  # type: ignore[arg-type]
    assert bore.axis is not None and vec_close(bore.axis.origin, (115.0, 15.0, 25.0))
    edges = session.adapter.edges_of(FaceRequest(operation_id="q", face=top.handle)).edges
    assert len(edges) == 5  # 4 lines + the bore circle
    assert all(e.handle.id.startswith(result.component.id) for e in edges)


def _mate_pin_into_bore(session: Session, block_c: OperationResult, pin_c: OperationResult) -> tuple:
    block_faces = session.faces(block_c.feature)  # type: ignore[arg-type]
    pin_faces = session.faces(pin_c.feature)  # type: ignore[arg-type]
    bore = cylindrical_faces(block_faces)[0]
    pin_side = cylindrical_faces(pin_faces)[0]
    concentric = session.adapter.mate_concentric(
        MateConcentricRequest(
            operation_id="conc", a=bore.handle, b=pin_side.handle, alignment=MateAlignment.anti_aligned
        )
    )
    block_top = face_with_normal(block_faces, Z)
    pin_top = face_with_normal(pin_faces, Z)
    coincident = session.adapter.mate_coincident(
        MateCoincidentRequest(
            operation_id="coin", a=block_top.handle, b=pin_top.handle, alignment=MateAlignment.aligned
        )
    )
    return concentric, coincident


def test_concentric_and_coincident_mates_translate_the_pin(session: Session) -> None:
    block_c, pin_c = _assembly(session)
    assert pin_c.component is not None
    concentric, coincident = _mate_pin_into_bore(session, block_c, pin_c)
    assert concentric.mate is not None and coincident.mate is not None
    assert concentric.component == pin_c.component
    transform = session.adapter.component_transform(ComponentRequest(operation_id="q", component=pin_c.component))
    assert vec_close(transform.origin, (15.0, 15.0, -20.0))
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert vec_close(box.min, (12.0, 12.0, -20.0)) and vec_close(box.max, (18.0, 18.0, 20.0))
    block_box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=block_c.component))  # type: ignore[arg-type]
    assert block_box == BoundingBox(min=(0.0, 0.0, 0.0), max=(30.0, 30.0, 20.0))
    mass = session.adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass.volume_mm3 == pytest.approx(BLOCK_VOLUME + PIN_VOLUME)  # no overlap subtraction


def test_interference_pin_in_bore_is_zero_and_pin_in_solid_block_is_positive(session: Session) -> None:
    block_c, pin_c = _assembly(session)
    _mate_pin_into_bore(session, block_c, pin_c)
    result = session.adapter.check_interference(InterferenceRequest(operation_id="chk"))
    assert len(result.pairs) == 1
    assert result.total_volume_mm3 == pytest.approx(0.0, abs=1e-9)
    assert {result.pairs[0].a, result.pairs[0].b} == {block_c.component, pin_c.component}

    solid = Session(session.adapter.__class__())
    solid_block, solid_pin = _assembly(solid, bore=False, pin_transform=Transform(origin=(15.0, 15.0, 0.0)))
    assert solid_pin.component is not None
    block_top = face_with_normal(solid.faces(solid_block.feature), Z)  # type: ignore[arg-type]
    pin_top = face_with_normal(solid.faces(solid_pin.feature), Z)  # type: ignore[arg-type]
    solid.adapter.mate_coincident(
        MateCoincidentRequest(
            operation_id="coin", a=block_top.handle, b=pin_top.handle, alignment=MateAlignment.aligned
        )
    )
    interference = solid.adapter.check_interference(
        InterferenceRequest(operation_id="chk", components=(solid_block.component, solid_pin.component))  # type: ignore[arg-type]
    )
    assert interference.total_volume_mm3 == pytest.approx(math.pi * 9 * 20)


def test_mate_distance_places_b_plane_on_the_requested_side(session: Session) -> None:
    block_c, pin_c = _assembly(session)
    assert pin_c.component is not None
    block_top = face_with_normal(session.faces(block_c.feature), Z)  # type: ignore[arg-type]
    pin_bottom = face_with_normal(session.faces(pin_c.feature), NZ)  # type: ignore[arg-type]
    session.adapter.mate_distance(
        MateDistanceRequest(operation_id="dist", a=block_top.handle, b=pin_bottom.handle, distance_mm=5.0)
    )
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(25.0)
    session.adapter.undo_last(Empty(operation_id="u"))
    session.adapter.mate_distance(
        MateDistanceRequest(
            operation_id="dist", a=block_top.handle, b=pin_bottom.handle, distance_mm=5.0, side=Side.negative
        )
    )
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(15.0)
    session.adapter.undo_last(Empty(operation_id="u"))
    session.adapter.mate_distance(
        MateDistanceRequest(operation_id="dist", a=block_top.handle, b=pin_bottom.handle, distance_mm=5.0, flip=True)
    )
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(15.0)


def test_coincident_flip_puts_the_component_on_the_other_side(session: Session) -> None:
    block_c, pin_c = _assembly(session)
    assert pin_c.component is not None
    block_top = face_with_normal(session.faces(block_c.feature), Z)  # type: ignore[arg-type]
    pin_bottom = face_with_normal(session.faces(pin_c.feature), NZ)  # type: ignore[arg-type]
    session.adapter.mate_coincident(
        MateCoincidentRequest(operation_id="coin", a=block_top.handle, b=pin_bottom.handle, flip=True)
    )
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(-20.0) and box.max[2] == pytest.approx(20.0)


@pytest.mark.parametrize(
    ("alignment", "use_bottom"),
    [(MateAlignment.anti_aligned, False), (MateAlignment.aligned, True)],
)
def test_coincident_alignment_mismatch_is_mate_failed(
    session: Session, alignment: MateAlignment, use_bottom: bool
) -> None:
    block_c, pin_c = _assembly(session)
    block_top = face_with_normal(session.faces(block_c.feature), Z)  # type: ignore[arg-type]
    pin_face = face_with_normal(session.faces(pin_c.feature), NZ if use_bottom else Z)  # type: ignore[arg-type]
    before = session.adapter.component_transform(ComponentRequest(operation_id="q", component=pin_c.component))  # type: ignore[arg-type]
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.mate_coincident(
            MateCoincidentRequest(operation_id="coin", a=block_top.handle, b=pin_face.handle, alignment=alignment)
        )
    assert excinfo.value.info.code is ErrorCode.mate_failed
    assert excinfo.value.info.recoverable is True
    assert session.adapter.component_transform(ComponentRequest(operation_id="q", component=pin_c.component)) == before  # type: ignore[arg-type]
    assert len(session.adapter.feature_tree(Empty(operation_id="t")).nodes) == 3


def test_concentric_alignment_mismatch_is_mate_failed_and_non_parallel_axes_rotate(session: Session) -> None:
    """Parallel-but-wrong-sense axes still fail: no rotation is attempted in that bucket,
    so the placement and the error stay exactly what they were before the general case existed. Genuinely skew
    axes now rotate the moving component onto ``a``'s axis instead of raising ``not_supported``."""
    block_c, pin_c = _assembly(session)
    bore = cylindrical_faces(session.faces(block_c.feature))[0]  # type: ignore[arg-type]
    pin_side = cylindrical_faces(session.faces(pin_c.feature))[0]  # type: ignore[arg-type]
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.mate_concentric(MateConcentricRequest(operation_id="conc", a=bore.handle, b=pin_side.handle))
    assert excinfo.value.info.code is ErrorCode.mate_failed
    tilted = session.new_part("tilted")
    session.adapter.cylinder(
        CylinderRequest(
            operation_id="t", diameter_mm=6.0, height_mm=10.0, axis=Axis(origin=(0, 0, 0), direction=(1, 0, 0))
        )
    )
    session.adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=DocumentHandle(id="doc:3")))
    tilted_c = session.adapter.insert_component(
        InsertComponentRequest(operation_id="ins_t", part=DocumentHandle(id=tilted), transform=Transform.identity())
    )
    tilted_side = cylindrical_faces(session.faces(tilted_c.feature))[0]  # type: ignore[arg-type]
    assert bore.axis is not None
    result = session.adapter.mate_concentric(
        MateConcentricRequest(operation_id="conc2", a=bore.handle, b=tilted_side.handle)
    )
    assert result.component == tilted_c.component
    moved = session.adapter.face_info(FaceRequest(operation_id="q", face=tilted_side.handle))
    assert moved.axis is not None
    # default alignment is "aligned": the rotated axis direction matches a's, and the two axis lines coincide.
    assert vec_close(moved.axis.direction, bore.axis.direction, 1e-9)
    to_moved = vsub(moved.axis.origin, bore.axis.origin)
    lateral = vsub(to_moved, vscale(bore.axis.direction, vdot(to_moved, bore.axis.direction)))
    assert close(vlength(lateral), 0.0, 1e-9)


def test_mating_a_fixed_component_or_the_same_component_fails(session: Session) -> None:
    block_c, pin_c = _assembly(session)
    block_faces = session.faces(block_c.feature)  # type: ignore[arg-type]
    pin_top = face_with_normal(session.faces(pin_c.feature), Z)  # type: ignore[arg-type]
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.mate_coincident(
            MateCoincidentRequest(operation_id="coin", a=pin_top.handle, b=face_with_normal(block_faces, Z).handle)
        )
    assert excinfo.value.info.code is ErrorCode.mate_failed
    assert "fixed" in excinfo.value.info.message
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.mate_coincident(
            MateCoincidentRequest(
                operation_id="coin",
                a=face_with_normal(block_faces, Z).handle,
                b=face_with_normal(block_faces, NZ).handle,
            )
        )
    assert excinfo.value.info.code is ErrorCode.mate_failed
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.mate_coincident(
            MateCoincidentRequest(operation_id="coin", a=FaceHandle(id="comp:9/face:1"), b=pin_top.handle)
        )
    assert excinfo.value.info.code is ErrorCode.invalid_reference


def test_undo_restores_component_transform_and_fix_state(session: Session) -> None:
    block_c, pin_c = _assembly(session)
    assert pin_c.component is not None and block_c.component is not None
    _mate_pin_into_bore(session, block_c, pin_c)
    reverted = session.adapter.undo_last(Empty(operation_id="u"))
    assert reverted.reverted is not None
    transform = session.adapter.component_transform(ComponentRequest(operation_id="q", component=pin_c.component))
    assert vec_close(transform.origin, (15.0, 15.0, 0.0))
    session.adapter.undo_last(Empty(operation_id="u"))
    assert session.adapter.component_transform(ComponentRequest(operation_id="q", component=pin_c.component)) == (
        Transform.identity()
    )
    session.adapter.undo_last(Empty(operation_id="u"))  # insert pin
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(BLOCK_VOLUME)
    session.adapter.undo_last(Empty(operation_id="u"))  # fix
    pin_doc = "doc:2"
    session.adapter.insert_component(
        InsertComponentRequest(operation_id="ins_pin2", part=DocumentHandle(id=pin_doc), transform=Transform.identity())
    )
    pin_faces = session.faces(session.adapter.feature_tree(Empty(operation_id="t")).nodes[-1].handle)
    session.adapter.mate_coincident(
        MateCoincidentRequest(
            operation_id="coin2",
            a=face_with_normal(pin_faces, Z).handle,
            b=face_with_normal(session.faces(block_c.feature), NZ).handle,  # type: ignore[arg-type]
        )
    )  # the block is no longer fixed, so it moves
    block_box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=block_c.component))
    assert block_box.min[2] == pytest.approx(40.0)


def test_signature_and_persistent_reference_round_trip_on_component_faces(session: Session) -> None:
    from cotaforge.contract import EntityRequest, ResolveRequest

    block_c, _ = _assembly(session)
    bore = cylindrical_faces(session.faces(block_c.feature))[0]  # type: ignore[arg-type]
    ref = session.adapter.persistent_reference(EntityRequest(operation_id="r", entity=bore.handle))
    assert ref.adapter == "fake"
    assert ref.token == bore.handle.id
    assert ref.signature.kind == "face"
    assert ref.signature.surface is SurfaceType.cylinder
    assert ref.signature.radius == pytest.approx(3.25)
    resolved = session.adapter.resolve_reference(ResolveRequest(operation_id="r", ref=ref))
    assert resolved.entity == bore.handle
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.resolve_reference(
            ResolveRequest(operation_id="r", ref=ref.model_copy(update={"token": "comp:1/face:99"}))
        )
    assert excinfo.value.info.code is ErrorCode.invalid_reference
