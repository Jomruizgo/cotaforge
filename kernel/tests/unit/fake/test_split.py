"""``split`` and the scaled ``copy_body`` on the fake adapter: exact for a single axis-aligned box or cylinder cut by an
axis-aligned plane (the clipped primitives are exact), an honest ``not_supported`` for anything else."""

import math

import pytest

from cotaforge.contract import (
    AdapterError,
    Axis,
    BodyHandle,
    BoundingBox,
    BoxRequest,
    CopyBodyRequest,
    CylinderRequest,
    EndCondition,
    ErrorCode,
    FaceRequest,
    HoleRequest,
    HoleStandard,
    MassRequest,
    OperationResult,
    PlaneName,
    SketchFrame,
    SplitRequest,
    Transform,
    frame_for_plane,
)
from tests.unit.fake.conftest import Session, cylindrical_faces, face_with_normal, vec_close
from tests.unit.fake.test_undo import _round_trip

Z = (0.0, 0.0, 1.0)
S2 = 1 / math.sqrt(2.0)


def _plane(z: float, normal_z: float = 1.0) -> SketchFrame:
    return frame_for_plane(PlaneName.XY).model_copy(update={"origin": (0.0, 0.0, z), "n": (0.0, 0.0, normal_z)})


def _split(
    session: Session, source: BodyHandle, plane: SketchFrame, keep: str = "both", op: str = "sp"
) -> OperationResult:
    return session.adapter.split(SplitRequest(operation_id=op, source=source, plane=plane, keep=keep))  # type: ignore[arg-type]


def _mass(session: Session, body: BodyHandle | None = None):  # noqa: ANN202
    return session.adapter.mass_properties(MassRequest(operation_id="m", body=body))


def _bbox(session: Session, body: BodyHandle | None = None) -> BoundingBox:
    return session.adapter.bounding_box(MassRequest(operation_id="b", body=body))


def _block(session: Session) -> OperationResult:
    session.new_part()
    return session.adapter.box(BoxRequest(operation_id="block", size=(100.0, 60.0, 20.0)))


def test_a_box_split_in_two_makes_a_positive_and_a_negative_piece_that_add_up(session: Session) -> None:
    block = _block(session)
    result = _split(session, block.created_bodies[0], _plane(5.0))
    up, down = result.created_bodies
    assert _mass(session, up).volume_mm3 == pytest.approx(90000.0)
    assert _mass(session, down).volume_mm3 == pytest.approx(30000.0)
    assert vec_close(_mass(session, up).centroid, (50.0, 30.0, 12.5))
    assert vec_close(_mass(session, down).centroid, (50.0, 30.0, 2.5))
    assert vec_close(_bbox(session, up).min, (0.0, 0.0, 5.0)) and vec_close(_bbox(session, up).max, (100.0, 60.0, 20.0))
    assert vec_close(_bbox(session, down).max, (100.0, 60.0, 5.0))
    assert _mass(session).volume_mm3 == pytest.approx(120000.0)


def test_the_normal_of_the_plane_decides_which_piece_is_positive(session: Session) -> None:
    block = _block(session)
    up, down = _split(session, block.created_bodies[0], _plane(5.0, -1.0)).created_bodies
    assert _mass(session, up).volume_mm3 == pytest.approx(30000.0)  # the positive side is now z < 5
    assert _mass(session, down).volume_mm3 == pytest.approx(90000.0)


def test_the_source_is_consumed(session: Session) -> None:
    block = _block(session)
    source = block.created_bodies[0]
    result = _split(session, source, _plane(5.0))
    assert source not in result.created_bodies
    assert source in result.modified_bodies
    with pytest.raises(AdapterError) as info:
        _mass(session, source)
    assert info.value.info.code is ErrorCode.invalid_reference
    with pytest.raises(AdapterError):
        _split(session, source, _plane(5.0), op="again")


def test_each_piece_owns_a_closed_set_of_faces_and_edges(session: Session) -> None:
    block = _block(session)
    result = _split(session, block.created_bodies[0], _plane(5.0))
    assert len(result.created_faces) == 12 and len(result.created_edges) == 24
    faces = session.faces(result.feature)  # type: ignore[arg-type]
    by_body = {body: [f for f in faces.faces if f.body == body] for body in result.created_bodies}
    assert all(len(found) == 6 for found in by_body.values())
    up_faces = by_body[result.created_bodies[0]]
    cut = [f for f in up_faces if f.normal is not None and vec_close(f.normal, (0.0, 0.0, -1.0))]
    assert len(cut) == 1 and cut[0].center[2] == pytest.approx(5.0)
    assert sum(f.area_mm2 for f in up_faces) == pytest.approx(_mass(session, result.created_bodies[0]).surface_mm2)


@pytest.mark.parametrize("keep", ["positive", "negative"])
def test_keeping_one_piece_creates_only_that_piece(session: Session, keep: str) -> None:
    block = _block(session)
    result = _split(session, block.created_bodies[0], _plane(5.0), keep)
    (piece,) = result.created_bodies
    expected = 90000.0 if keep == "positive" else 30000.0
    assert _mass(session, piece).volume_mm3 == pytest.approx(expected)
    assert _mass(session).volume_mm3 == pytest.approx(expected)


def test_a_piece_can_be_split_again(session: Session) -> None:
    block = _block(session)
    up, _ = _split(session, block.created_bodies[0], _plane(5.0)).created_bodies
    x_plane = frame_for_plane(PlaneName.YZ).model_copy(update={"origin": (25.0, 0.0, 0.0)})
    again = _split(session, up, x_plane, op="again")
    assert [round(_mass(session, b).volume_mm3) for b in again.created_bodies] == [67500, 22500]
    assert _mass(session).volume_mm3 == pytest.approx(120000.0)


def test_a_cylinder_cut_across_its_axis_makes_two_shorter_cylinders(session: Session) -> None:
    session.new_part()
    cyl = session.adapter.cylinder(
        CylinderRequest(operation_id="cyl", diameter_mm=20.0, height_mm=40.0, axis=Axis(origin=(0, 0, 0), direction=Z))
    )
    result = _split(session, cyl.created_bodies[0], _plane(15.0))
    up, down = result.created_bodies
    assert _mass(session, up).volume_mm3 == pytest.approx(math.pi * 100 * 25)
    assert _mass(session, down).volume_mm3 == pytest.approx(math.pi * 100 * 15)
    assert vec_close(_bbox(session, up).min, (-10.0, -10.0, 15.0), tol=1e-6)
    assert len(cylindrical_faces(session.faces(result.feature))) == 2  # type: ignore[arg-type]


def test_a_plane_that_misses_the_body_is_a_geometry_error_that_is_not_retried(session: Session) -> None:
    block = _block(session)
    for z in (25.0, 0.0, -3.0):
        with pytest.raises(AdapterError) as info:
            _split(session, block.created_bodies[0], _plane(z))
        assert info.value.info.code is ErrorCode.geometry_failed
        assert info.value.info.recoverable is False
        assert "plane does not cut the body" in info.value.info.message
    assert _mass(session).volume_mm3 == pytest.approx(120000.0)  # the failed attempts left the source alone


def test_an_oblique_plane_is_not_supported(session: Session) -> None:
    block = _block(session)
    plane = SketchFrame(origin=(50.0, 30.0, 10.0), u=(0.0, 1.0, 0.0), v=(-S2, 0.0, S2), n=(S2, 0.0, S2))
    with pytest.raises(AdapterError) as info:
        _split(session, block.created_bodies[0], plane)
    assert info.value.info.code is ErrorCode.not_supported
    assert "axis-aligned" in info.value.info.message


def test_a_body_with_features_is_not_supported(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    session.adapter.hole(
        HoleRequest(
            operation_id="hole",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=10.0,
            end_condition=EndCondition.through_all,
        )
    )
    with pytest.raises(AdapterError) as info:
        _split(session, plate.created_bodies[0], _plane(4.0))
    assert info.value.info.code is ErrorCode.not_supported
    assert "FreeCAD" in info.value.info.message


def test_a_cylinder_cut_along_its_length_is_not_supported(session: Session) -> None:
    session.new_part()
    cyl = session.adapter.cylinder(
        CylinderRequest(operation_id="cyl", diameter_mm=20.0, height_mm=40.0, axis=Axis(origin=(0, 0, 0), direction=Z))
    )
    plane = frame_for_plane(PlaneName.YZ).model_copy(update={"origin": (0.0, 0.0, 0.0)})
    with pytest.raises(AdapterError) as info:
        _split(session, cyl.created_bodies[0], plane)
    assert info.value.info.code is ErrorCode.not_supported


def test_splitting_an_unknown_body_is_an_invalid_reference(session: Session) -> None:
    session.new_part()
    with pytest.raises(AdapterError) as info:
        _split(session, BodyHandle(id="body:99"), _plane(5.0))
    assert info.value.info.code is ErrorCode.invalid_reference


@pytest.mark.parametrize("keep", ["both", "positive", "negative"])
def test_undo_restores_the_source_completely(session: Session, keep: str) -> None:
    block = _block(session)
    _round_trip(session, lambda: _split(session, block.created_bodies[0], _plane(5.0), keep))


# --- copy_body with a scale -------------------------------------------------------------------------------------


def _copy(session: Session, source: BodyHandle, **fields: object) -> OperationResult:
    request = CopyBodyRequest(operation_id="cp", source=source, transform=Transform(), **fields)  # type: ignore[arg-type]
    return session.adapter.copy_body(request)


def test_a_scaled_copy_has_the_cubed_volume_the_squared_surface_and_the_scaled_pose(session: Session) -> None:
    block = _block(session)
    result = _copy(session, block.created_bodies[0], scale=2.0)
    copy = result.created_bodies[0]
    assert _mass(session, copy).volume_mm3 == pytest.approx(8 * 120000.0)
    assert _mass(session, copy).surface_mm2 == pytest.approx(4 * _mass(session, block.created_bodies[0]).surface_mm2)
    assert vec_close(_mass(session, copy).centroid, (100.0, 60.0, 20.0))
    assert vec_close(_bbox(session, copy).max, (200.0, 120.0, 40.0))
    assert _mass(session, block.created_bodies[0]).volume_mm3 == pytest.approx(120000.0)


def test_a_scale_about_a_point_keeps_that_point_fixed(session: Session) -> None:
    block = _block(session)
    result = _copy(session, block.created_bodies[0], scale=0.5, scale_about=(50.0, 30.0, 10.0))
    copy = result.created_bodies[0]
    assert vec_close(_bbox(session, copy).min, (25.0, 15.0, 5.0)) and vec_close(
        _bbox(session, copy).max, (75.0, 45.0, 15.0)
    )
    assert vec_close(_mass(session, copy).centroid, (50.0, 30.0, 10.0))


def test_the_scale_comes_before_the_transform(session: Session) -> None:
    block = _block(session)
    request = CopyBodyRequest(
        operation_id="cp",
        source=block.created_bodies[0],
        transform=Transform(origin=(200.0, 0.0, 0.0)),
        scale=2.0,
        scale_about=(0.0, 0.0, 0.0),
    )
    copy = session.adapter.copy_body(request).created_bodies[0]
    assert vec_close(_bbox(session, copy).min, (200.0, 0.0, 0.0))
    assert vec_close(_bbox(session, copy).max, (400.0, 120.0, 40.0))


def test_a_scaled_copy_scales_its_faces_edges_and_cylinders(session: Session) -> None:
    session.new_part()
    cyl = session.adapter.cylinder(
        CylinderRequest(operation_id="cyl", diameter_mm=20.0, height_mm=40.0, axis=Axis(origin=(0, 0, 0), direction=Z))
    )
    result = _copy(session, cyl.created_bodies[0], scale=1.5)
    (side,) = cylindrical_faces(session.faces(result.feature))  # type: ignore[arg-type]
    assert side.radius == pytest.approx(15.0)
    assert side.area_mm2 == pytest.approx(2 * math.pi * 15.0 * 60.0)
    assert _mass(session, result.created_bodies[0]).volume_mm3 == pytest.approx(math.pi * 100 * 40 * 1.5**3)
    circles = session.adapter.edges_of(FaceRequest(operation_id="e", face=side.handle)).edges
    assert len(circles) == 2 and all(e.radius == pytest.approx(15.0) for e in circles)
    assert sorted(e.center[2] for e in circles if e.center is not None) == pytest.approx([0.0, 60.0])


def test_a_scaled_copy_of_a_body_with_a_hole_scales_the_hole(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    session.adapter.hole(
        HoleRequest(
            operation_id="hole",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=10.0,
            end_condition=EndCondition.through_all,
        )
    )
    source = plate.created_bodies[0]
    result = _copy(session, source, scale=2.0)
    assert _mass(session, result.created_bodies[0]).volume_mm3 == pytest.approx(8 * _mass(session, source).volume_mm3)
    (bore,) = cylindrical_faces(session.faces(result.feature))  # type: ignore[arg-type]
    assert bore.radius == pytest.approx(10.0)


def test_an_unscaled_copy_is_exactly_what_it_was(session: Session) -> None:
    block = _block(session)
    plain = _copy(session, block.created_bodies[0])
    assert _mass(session, plain.created_bodies[0]).volume_mm3 == pytest.approx(120000.0)
    assert vec_close(_bbox(session, plain.created_bodies[0]).max, (100.0, 60.0, 20.0))


def test_undo_removes_a_scaled_copy_completely(session: Session) -> None:
    block = _block(session)
    _round_trip(session, lambda: _copy(session, block.created_bodies[0], scale=1.006, scale_about=(1.0, 2.0, 3.0)))
