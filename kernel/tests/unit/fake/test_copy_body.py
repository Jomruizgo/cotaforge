"""``copy_body`` on the fake adapter: a rigid, independent duplicate of a whole body (primitives, faces, edges,
vertices), placed by a transform, that the original never notices."""

import math

import pytest

from cotaforge.contract import (
    AdapterError,
    Axis,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CopyBodyRequest,
    CylinderRequest,
    EdgeRequest,
    ErrorCode,
    FaceRequest,
    MassRequest,
    MoveBodyRequest,
    OperationResult,
    Transform,
)
from tests.unit.fake.conftest import Session, cylindrical_faces, face_with_normal, vec_close
from tests.unit.fake.test_undo import _round_trip

Z = (0.0, 0.0, 1.0)


def _copy(session: Session, source: BodyHandle, transform: Transform, op: str = "copy") -> OperationResult:
    return session.adapter.copy_body(CopyBodyRequest(operation_id=op, source=source, transform=transform))


def _mass(session: Session, body: BodyHandle | None = None):  # noqa: ANN202
    return session.adapter.mass_properties(MassRequest(operation_id="m", body=body))


def _bbox(session: Session, body: BodyHandle | None = None) -> BoundingBox:
    return session.adapter.bounding_box(MassRequest(operation_id="b", body=body))


def _box(session: Session, size: tuple[float, float, float] = (10.0, 20.0, 30.0)) -> OperationResult:
    session.new_part()
    return session.adapter.box(BoxRequest(operation_id="box", size=size))


def test_a_translated_copy_is_a_new_body_that_doubles_the_document_volume(session: Session) -> None:
    box = _box(session)
    copy = _copy(session, box.created_bodies[0], Transform(origin=(0.0, 100.0, 0.0)))
    assert len(copy.created_bodies) == 1 and copy.created_bodies != box.created_bodies
    assert _mass(session).volume_mm3 == pytest.approx(2 * 6000.0)
    assert _mass(session, copy.created_bodies[0]).volume_mm3 == pytest.approx(6000.0)
    assert vec_close(_mass(session, copy.created_bodies[0]).centroid, (5.0, 110.0, 15.0))


def test_the_source_is_untouched_by_the_copy(session: Session) -> None:
    box = _box(session)
    source = box.created_bodies[0]
    mass_before = _mass(session, source)
    bbox_before = _bbox(session, source)
    copy = _copy(session, source, Transform(origin=(0.0, 100.0, 0.0)))
    assert _mass(session, source) == mass_before
    assert _bbox(session, source) == bbox_before
    assert copy.modified_faces == ()
    assert set(copy.created_faces).isdisjoint(box.created_faces)


def test_the_copy_owns_a_full_set_of_faces_edges_and_vertices(session: Session) -> None:
    box = _box(session)
    copy = _copy(session, box.created_bodies[0], Transform(origin=(0.0, 100.0, 0.0)))
    assert len(copy.created_faces) == 6 and len(copy.created_edges) == 12
    body = copy.created_bodies[0]
    top = face_with_normal(session.faces(copy.feature), Z)  # type: ignore[arg-type]
    assert top.center == pytest.approx((5.0, 110.0, 30.0))
    for edge_handle in copy.created_edges:
        info = session.adapter.edge_info(EdgeRequest(operation_id="e", edge=edge_handle))
        assert all(face in copy.created_faces for face in info.faces)
    assert _mass(session, body).surface_mm2 == pytest.approx(2 * (200 + 300 + 600))
    assert _mass(session, box.created_bodies[0]).surface_mm2 == pytest.approx(2 * (200 + 300 + 600))


@pytest.mark.parametrize("angle", [90.0, 30.0])
def test_a_rotated_copy_moves_the_centroid_rigidly(session: Session, angle: float) -> None:
    box = _box(session)
    transform = Transform.from_axis_angle(Z, angle, origin=(0.0, 100.0, 0.0))
    copy = _copy(session, box.created_bodies[0], transform)
    expected = transform.apply((5.0, 10.0, 15.0))
    assert vec_close(_mass(session, copy.created_bodies[0]).centroid, expected)
    assert _mass(session, copy.created_bodies[0]).volume_mm3 == pytest.approx(6000.0)


def test_a_quarter_turn_copy_has_the_rotated_bounding_box(session: Session) -> None:
    box = _box(session)
    copy = _copy(session, box.created_bodies[0], Transform.from_axis_angle(Z, 90.0))
    bbox = _bbox(session, copy.created_bodies[0])
    assert vec_close(bbox.min, (-20.0, 0.0, 0.0), tol=1e-9)
    assert vec_close(bbox.max, (0.0, 10.0, 30.0), tol=1e-9)


def test_the_copy_of_a_body_with_a_hole_keeps_the_hole_and_its_axis(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    from cotaforge.contract import EndCondition, HoleRequest, HoleStandard

    hole = session.adapter.hole(
        HoleRequest(
            operation_id="hole",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=10.0,
            end_condition=EndCondition.through_all,
        )
    )
    assert hole.feature is not None
    source = plate.created_bodies[0]
    source_mass = _mass(session, source)
    turn = Transform.from_axis_angle((0.0, 1.0, 0.0), 90.0, origin=(0.0, 200.0, 0.0))
    copy = _copy(session, source, turn)
    copy_mass = _mass(session, copy.created_bodies[0])
    assert copy_mass.volume_mm3 == pytest.approx(source_mass.volume_mm3)
    assert copy_mass.surface_mm2 == pytest.approx(source_mass.surface_mm2)
    (source_bore,) = cylindrical_faces(session.faces(hole.feature))  # type: ignore[arg-type]
    (copy_bore,) = cylindrical_faces(session.faces(copy.feature))  # type: ignore[arg-type]
    assert source_bore.axis is not None and copy_bore.axis is not None
    assert vec_close(copy_bore.axis.direction, turn.apply_direction(source_bore.axis.direction))
    assert copy_bore.radius == pytest.approx(5.0)


def test_a_cylinder_copy_keeps_the_face_axis_sense(session: Session) -> None:
    session.new_part()
    cyl = session.adapter.cylinder(
        CylinderRequest(operation_id="cyl", diameter_mm=20.0, height_mm=40.0, axis=Axis(origin=(0, 0, 0), direction=Z))
    )
    turn = Transform.from_axis_angle((1.0, 0.0, 0.0), 90.0)
    copy = _copy(session, cyl.created_bodies[0], turn)
    (side,) = cylindrical_faces(session.faces(copy.feature))  # type: ignore[arg-type]
    assert side.axis is not None
    assert vec_close(side.axis.direction, turn.apply_direction(Z))
    assert _mass(session, copy.created_bodies[0]).volume_mm3 == pytest.approx(math.pi * 100 * 40)


def test_moving_the_copy_moves_only_the_copy(session: Session) -> None:
    box = _box(session)
    source, copy = box.created_bodies[0], _copy(session, box.created_bodies[0], Transform(origin=(0, 100, 0)))
    session.adapter.move_body(
        MoveBodyRequest(operation_id="mv", body=copy.created_bodies[0], transform=Transform(origin=(0, 0, 50)))
    )
    assert vec_close(_mass(session, source).centroid, (5.0, 10.0, 15.0))
    assert vec_close(_mass(session, copy.created_bodies[0]).centroid, (5.0, 110.0, 65.0))


def test_the_original_and_its_copy_boolean_like_any_two_bodies(session: Session) -> None:
    box = _box(session, (10.0, 10.0, 10.0))
    source = box.created_bodies[0]
    copy = _copy(session, source, Transform(origin=(5.0, 0.0, 0.0)))  # half-overlapping twin
    session.adapter.boolean(
        BooleanRequest(operation_id="u", kind=BooleanKind.union, target=source, tools=copy.created_bodies)
    )
    assert _mass(session).volume_mm3 == pytest.approx(1500.0)  # 1000 + 1000 - 500 overlap


def test_the_copy_is_subtracted_from_its_source(session: Session) -> None:
    box = _box(session, (10.0, 10.0, 10.0))
    source = box.created_bodies[0]
    copy = _copy(session, source, Transform(origin=(5.0, 0.0, 0.0)))
    session.adapter.boolean(
        BooleanRequest(operation_id="s", kind=BooleanKind.subtract, target=source, tools=copy.created_bodies)
    )
    assert _mass(session).volume_mm3 == pytest.approx(500.0)


def test_undo_removes_the_copy_completely(session: Session) -> None:
    box = _box(session)
    _round_trip(session, lambda: _copy(session, box.created_bodies[0], Transform(origin=(0.0, 100.0, 0.0))))
    _round_trip(session, lambda: _copy(session, box.created_bodies[0], Transform.from_axis_angle(Z, 30.0)))


def test_copying_an_unknown_body_is_an_invalid_reference(session: Session) -> None:
    session.new_part()
    with pytest.raises(AdapterError) as info:
        _copy(session, BodyHandle(id="body:99"), Transform.identity())
    assert info.value.info.code is ErrorCode.invalid_reference


def test_copy_faces_resolve_by_handle_like_any_other_face(session: Session) -> None:
    box = _box(session)
    copy = _copy(session, box.created_bodies[0], Transform(origin=(0.0, 100.0, 0.0)))
    info = session.adapter.face_info(FaceRequest(operation_id="f", face=copy.created_faces[0]))
    assert info.provenance.created_by == copy.feature
