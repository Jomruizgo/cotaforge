"""``mirror`` and ``mirror_body`` on the fake adapter: exact reflections across an axis-aligned plane (the primitives
reflect by a +/-1 diagonal matrix, so their boxes stay exact), an honest ``not_supported`` across any other plane."""

import math

import pytest

from cotaforge.contract import (
    AdapterError,
    Axis,
    BodyHandle,
    BoundingBox,
    BoxRequest,
    CylinderRequest,
    EndCondition,
    ErrorCode,
    FeatureHandle,
    HoleRequest,
    HoleStandard,
    MassRequest,
    MirrorBodyRequest,
    MirrorRequest,
    OperationResult,
    PlaneName,
    ProvenanceRole,
    SketchFrame,
    frame_for_plane,
)
from tests.unit.fake.conftest import Session, cylindrical_faces, face_with_normal, vec_close
from tests.unit.fake.test_undo import _round_trip

Z = (0.0, 0.0, 1.0)
S2 = 1 / math.sqrt(2.0)


def _plane(origin: tuple[float, float, float], name: PlaneName = PlaneName.YZ) -> SketchFrame:
    return frame_for_plane(name).model_copy(update={"origin": origin})


def _mirror(
    session: Session, features: tuple[FeatureHandle, ...], plane: SketchFrame, op: str = "mi"
) -> OperationResult:
    return session.adapter.mirror(MirrorRequest(operation_id=op, features=features, plane=plane))


def _mirror_body(session: Session, source: BodyHandle, plane: SketchFrame, op: str = "mb") -> OperationResult:
    return session.adapter.mirror_body(MirrorBodyRequest(operation_id=op, source=source, plane=plane))


def _mass(session: Session, body: BodyHandle | None = None):  # noqa: ANN202
    return session.adapter.mass_properties(MassRequest(operation_id="m", body=body))


def _bbox(session: Session, body: BodyHandle | None = None) -> BoundingBox:
    return session.adapter.bounding_box(MassRequest(operation_id="b", body=body))


def _boss(session: Session, centre: tuple[float, float] = (10.0, 10.0), height: float = 5.0) -> OperationResult:
    """A 10 mm boss on the 100 x 60 x 8 plate: the seed of a mirror."""
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    sketch = session.circle_sketch("boss_sketch", top.handle, (centre[0] - 50.0, centre[1] - 30.0), 10.0)
    return session.extrude("boss", sketch, height)


# --- mirror ---------------------------------------------------------------------------------------


def test_a_boss_mirrored_about_a_plane_adds_exactly_its_own_volume_again(session: Session) -> None:
    boss = _boss(session)
    before = _mass(session).volume_mm3
    result = _mirror(session, (boss.feature,), _plane((50.0, 0.0, 0.0)))  # type: ignore[arg-type]
    seed_volume = math.pi * 25.0 * 5.0
    assert _mass(session).volume_mm3 - before == pytest.approx(seed_volume)
    assert result.instance_count == 2
    assert len(result.created_faces) == 2  # the reflected wall and cap, like the seed's
    (wall,) = cylindrical_faces(session.faces(result.feature, ProvenanceRole.created))  # type: ignore[arg-type]
    assert wall.axis is not None
    assert wall.axis.origin[0] == pytest.approx(90.0) and wall.axis.origin[1] == pytest.approx(10.0)
    assert vec_close(wall.axis.direction, Z)  # a reflection across x leaves a z axis alone


def test_the_reflected_boss_stands_where_the_reflection_puts_it(session: Session) -> None:
    boss = _boss(session)
    result = _mirror(session, (boss.feature,), _plane((50.0, 0.0, 0.0)))  # type: ignore[arg-type]
    box = _bbox(session)
    assert vec_close(box.min, (0.0, 0.0, 0.0), tol=1e-6)
    assert vec_close(box.max, (100.0, 60.0, 13.0), tol=1e-6)
    cap = face_with_normal(session.faces(result.feature, ProvenanceRole.created), Z)  # type: ignore[arg-type]
    assert vec_close(cap.center, (90.0, 10.0, 13.0), tol=1e-6)


def test_a_mirrored_through_hole_removes_its_volume_again(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    hole = session.adapter.hole(
        HoleRequest(
            operation_id="hole",
            face=top.handle,
            position=(-30.0, -20.0),
            standard=HoleStandard.simple,
            diameter_mm=6.0,
            end_condition=EndCondition.through_all,
        )
    )
    before = _mass(session).volume_mm3
    result = _mirror(session, (hole.feature,), _plane((50.0, 0.0, 0.0)))  # type: ignore[arg-type]
    assert _mass(session).volume_mm3 - before == pytest.approx(-math.pi * 9.0 * 8.0)
    (bore,) = cylindrical_faces(session.faces(result.feature, ProvenanceRole.created))  # type: ignore[arg-type]
    assert bore.axis is not None
    assert bore.axis.origin[0] == pytest.approx(80.0)


def test_mirroring_about_a_z_plane_flips_the_seed_through_it(session: Session) -> None:
    boss = _boss(session)
    _mirror(session, (boss.feature,), _plane((0.0, 0.0, 13.0), PlaneName.XY))  # type: ignore[arg-type]
    assert _bbox(session).max[2] == pytest.approx(18.0)  # the boss 8..13 reflected to 13..18


def test_an_oblique_plane_is_not_supported_with_the_fix(session: Session) -> None:
    boss = _boss(session)
    plane = SketchFrame(origin=(50.0, 0.0, 0.0), u=(0.0, 0.0, 1.0), v=(-S2, S2, 0.0), n=(S2, S2, 0.0))
    with pytest.raises(AdapterError) as info:
        _mirror(session, (boss.feature,), plane)  # type: ignore[arg-type]
    assert info.value.info.code is ErrorCode.not_supported
    assert "axis-aligned" in info.value.info.message
    assert "FreeCAD" in info.value.info.message


def test_a_feature_without_geometry_cannot_be_mirrored(session: Session) -> None:
    session.new_part()
    session.plate()
    with pytest.raises(AdapterError) as info:
        _mirror(session, (FeatureHandle(id="feature:99"),), _plane((50.0, 0.0, 0.0)))
    assert info.value.info.code is ErrorCode.invalid_reference


def test_undo_removes_the_mirror_completely(session: Session) -> None:
    boss = _boss(session)
    _round_trip(session, lambda: _mirror(session, (boss.feature,), _plane((50.0, 0.0, 0.0))))  # type: ignore[arg-type]


# --- mirror_body ---------------------------------------------------------------------------------------


def _box(session: Session, size: tuple[float, float, float] = (10.0, 20.0, 30.0)) -> OperationResult:
    session.new_part()
    return session.adapter.box(BoxRequest(operation_id="box", size=size))


def test_a_mirrored_body_is_a_new_body_with_the_reflected_pose_and_the_same_volume(session: Session) -> None:
    box = _box(session)
    result = _mirror_body(session, box.created_bodies[0], _plane((20.0, 0.0, 0.0)))
    assert len(result.created_bodies) == 1 and result.created_bodies != box.created_bodies
    copy = result.created_bodies[0]
    assert _mass(session, copy).volume_mm3 == pytest.approx(6000.0)
    assert vec_close(_mass(session, copy).centroid, (35.0, 10.0, 15.0))
    assert vec_close(_bbox(session, copy).min, (30.0, 0.0, 0.0))
    assert vec_close(_bbox(session, copy).max, (40.0, 20.0, 30.0))
    assert _mass(session).volume_mm3 == pytest.approx(12000.0)
    assert _mass(session, copy).surface_mm2 == pytest.approx(2 * (200 + 300 + 600))


def test_the_source_is_untouched_by_the_mirror(session: Session) -> None:
    box = _box(session)
    source = box.created_bodies[0]
    mass_before, bbox_before = _mass(session, source), _bbox(session, source)
    result = _mirror_body(session, source, _plane((20.0, 0.0, 0.0)))
    assert _mass(session, source) == mass_before
    assert _bbox(session, source) == bbox_before
    assert result.modified_faces == ()
    assert set(result.created_faces).isdisjoint(box.created_faces)


def test_the_mirrored_body_owns_faces_with_reflected_normals(session: Session) -> None:
    box = _box(session)
    result = _mirror_body(session, box.created_bodies[0], _plane((0.0, 0.0, 30.0), PlaneName.XY))
    assert len(result.created_faces) == 6 and len(result.created_edges) == 12
    faces = session.faces(result.feature)  # type: ignore[arg-type]
    top = face_with_normal(faces, (0.0, 0.0, -1.0))  # the source's top (z = 30) is the copy's underside
    assert top.center[2] == pytest.approx(30.0)
    assert face_with_normal(faces, Z).center[2] == pytest.approx(60.0)


def test_a_mirrored_cylinder_keeps_its_volume_and_reflects_its_axis(session: Session) -> None:
    session.new_part()
    cyl = session.adapter.cylinder(
        CylinderRequest(operation_id="cyl", diameter_mm=20.0, height_mm=40.0, axis=Axis(origin=(0, 0, 0), direction=Z))
    )
    result = _mirror_body(session, cyl.created_bodies[0], _plane((0.0, 0.0, 0.0), PlaneName.XY))
    (side,) = cylindrical_faces(session.faces(result.feature))  # type: ignore[arg-type]
    assert side.axis is not None
    assert vec_close(side.axis.direction, (0.0, 0.0, -1.0))
    assert _mass(session, result.created_bodies[0]).volume_mm3 == pytest.approx(math.pi * 100 * 40)
    assert vec_close(_bbox(session, result.created_bodies[0]).min, (-10.0, -10.0, -40.0), tol=1e-6)
    assert vec_close(_bbox(session, result.created_bodies[0]).max, (10.0, 10.0, 0.0), tol=1e-6)


def test_the_mirror_of_a_body_with_a_hole_keeps_the_hole(session: Session) -> None:
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
    result = _mirror_body(session, source, _plane((120.0, 0.0, 0.0)))
    assert _mass(session, result.created_bodies[0]).volume_mm3 == pytest.approx(_mass(session, source).volume_mm3)
    (bore,) = cylindrical_faces(session.faces(result.feature))  # type: ignore[arg-type]
    assert bore.axis is not None and bore.axis.origin[0] == pytest.approx(190.0)


def test_an_oblique_plane_is_not_supported_for_a_body_too(session: Session) -> None:
    box = _box(session)
    plane = SketchFrame(origin=(0.0, 0.0, 0.0), u=(0.0, 0.0, 1.0), v=(-S2, S2, 0.0), n=(S2, S2, 0.0))
    with pytest.raises(AdapterError) as info:
        _mirror_body(session, box.created_bodies[0], plane)
    assert info.value.info.code is ErrorCode.not_supported
    assert "axis-aligned" in info.value.info.message


def test_mirroring_an_unknown_body_is_an_invalid_reference(session: Session) -> None:
    session.new_part()
    with pytest.raises(AdapterError) as info:
        _mirror_body(session, BodyHandle(id="body:99"), _plane((0.0, 0.0, 0.0)))
    assert info.value.info.code is ErrorCode.invalid_reference


def test_undo_removes_the_mirrored_body_completely(session: Session) -> None:
    box = _box(session)
    _round_trip(session, lambda: _mirror_body(session, box.created_bodies[0], _plane((20.0, 0.0, 0.0))))
