"""``shell`` on the fake adapter: exact for the shapes it can model (an untouched axis-aligned box or a right cylinder,
inward, one removed face or two opposite ones), an honest ``not_supported`` for everything else."""

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
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FeatureRequest,
    HoleRequest,
    HoleStandard,
    MassRequest,
    MoveBodyRequest,
    OperationResult,
    ProvenanceRole,
    ShellRequest,
    SurfaceType,
    Transform,
)
from tests.unit.fake.conftest import Session, face_with_normal, vec_close
from tests.unit.fake.test_undo import _round_trip

BOX = (40.0, 30.0, 20.0)
Z = (0.0, 0.0, 1.0)


def _box(session: Session, size: tuple[float, float, float] = BOX, op: str = "box") -> OperationResult:
    return session.adapter.box(BoxRequest(operation_id=op, size=size))


def _cylinder(session: Session, op: str = "cyl") -> OperationResult:
    return session.adapter.cylinder(
        CylinderRequest(operation_id=op, diameter_mm=40.0, height_mm=30.0, axis=Axis(origin=(0, 0, 0), direction=Z))
    )


def _faces(session: Session, result: OperationResult) -> tuple[FaceInfo, ...]:
    return session.adapter.faces_of(FeatureRequest(operation_id="f", feature=result.feature)).faces  # type: ignore[arg-type]


def _shell(
    session: Session, faces: tuple[FaceHandle, ...], t: float = 2.0, *, op: str = "shell", **extra: object
) -> OperationResult:
    return session.adapter.shell(ShellRequest(operation_id=op, faces=faces, thickness_mm=t, **extra))  # type: ignore[arg-type]


def _volume(session: Session, body: BodyHandle | None = None) -> float:
    return session.adapter.mass_properties(MassRequest(operation_id="m", body=body)).volume_mm3


def _bbox(session: Session) -> BoundingBox:
    return session.adapter.bounding_box(MassRequest(operation_id="b"))


def _total_faces(session: Session) -> int:
    doc = session.adapter._active  # noqa: SLF001
    return len(doc.faces)


def _code(excinfo: pytest.ExceptionInfo[AdapterError]) -> ErrorCode:
    return excinfo.value.info.code


# --- a box ------


def test_one_removed_face_leaves_the_exact_walls_and_the_same_box(session: Session) -> None:
    """40 x 30 x 20, top removed, 2 mm: cavity 36 x 26 x 18 -> V = 24000 - 16848 = 7152 (what FreeCAD gives)."""
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z)
    result = _shell(session, (top.handle,))
    assert _volume(session) == pytest.approx(7152.0)
    assert _bbox(session) == BoundingBox(min=(0, 0, 0), max=BOX)
    assert _total_faces(session) == 11  # 6 + four inner walls and the floor, as on FreeCAD
    assert len(result.created_faces) == 5
    assert [f.id for f in result.modified_faces] == [top.handle.id]


def test_the_cavity_walls_point_into_the_cavity_and_the_removed_face_keeps_only_its_rim(session: Session) -> None:
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z)
    result = _shell(session, (top.handle,))
    created = [session.adapter.face_info(FaceRequest(operation_id="i", face=f)) for f in result.created_faces]
    floor = face_with_normal(tuple(created), (0.0, 0.0, 1.0))
    assert vec_close(floor.center, (20.0, 15.0, 2.0)) and floor.area_mm2 == pytest.approx(36.0 * 26.0)
    # an inner wall faces the cavity: the one at x = 2 looks along +X
    inner_x = face_with_normal(tuple(created), (1.0, 0.0, 0.0))
    assert inner_x.center[0] == pytest.approx(2.0)
    rim = session.adapter.face_info(FaceRequest(operation_id="r", face=top.handle))
    assert rim.area_mm2 == pytest.approx(40 * 30 - 36 * 26)


def test_two_opposite_faces_make_a_through_cavity(session: Session) -> None:
    session.new_part()
    made = _box(session)
    faces = _faces(session, made)
    top, bottom = face_with_normal(faces, Z), face_with_normal(faces, (0.0, 0.0, -1.0))
    result = _shell(session, (top.handle, bottom.handle))
    assert _volume(session) == pytest.approx(5280.0)  # cavity 36 x 26 x 20
    assert _total_faces(session) == 10
    assert len(result.created_faces) == 4
    assert {f.id for f in result.modified_faces} == {top.handle.id, bottom.handle.id}


def test_a_thick_wall_that_still_leaves_a_cavity_is_exact(session: Session) -> None:
    session.new_part()
    made = _box(session)
    _shell(session, (face_with_normal(_faces(session, made), Z).handle,), 14.0)
    assert _volume(session) == pytest.approx(24000.0 - 12 * 2 * 6)  # cavity 12 x 2 x 6


def test_a_shell_works_on_a_translated_body(session: Session) -> None:
    session.new_part()
    made = _box(session)
    session.adapter.move_body(
        MoveBodyRequest(operation_id="mv", body=made.created_bodies[0], transform=Transform(origin=(100.0, 0.0, 0.0)))
    )
    top = face_with_normal(_faces(session, made), Z)
    _shell(session, (top.handle,))
    assert _volume(session) == pytest.approx(7152.0)
    assert _bbox(session).min == pytest.approx((100.0, 0.0, 0.0))


def test_a_shell_round_trips_through_undo(session: Session) -> None:
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z).handle
    _round_trip(session, lambda: _shell(session, (top,)))
    _round_trip(session, lambda: _shell(session, (top,), op="again"))


# --- a cylinder ------


def test_a_cylinder_with_the_top_cap_removed(session: Session) -> None:
    session.new_part()
    made = _cylinder(session)
    top = face_with_normal(_faces(session, made), Z)
    result = _shell(session, (top.handle,))
    assert _volume(session) == pytest.approx(math.pi * (400 * 30 - 18**2 * 28))  # 9198.5833, as FreeCAD
    assert _total_faces(session) == 5
    assert len(result.created_faces) == 2
    inner = [
        f
        for f in (session.adapter.face_info(FaceRequest(operation_id="i", face=h)) for h in result.created_faces)
        if f.surface is SurfaceType.cylinder
    ]
    assert len(inner) == 1 and inner[0].radius == pytest.approx(18.0)


def test_a_cylinder_with_both_caps_removed_is_a_tube(session: Session) -> None:
    session.new_part()
    made = _cylinder(session)
    faces = _faces(session, made)
    top, bottom = face_with_normal(faces, Z), face_with_normal(faces, (0.0, 0.0, -1.0))
    _shell(session, (top.handle, bottom.handle))
    assert _volume(session) == pytest.approx(math.pi * (400 - 18**2) * 30)  # 7162.8313, as FreeCAD
    assert _total_faces(session) == 4


def test_a_cylinder_with_the_base_cap_removed_opens_the_other_end(session: Session) -> None:
    session.new_part()
    made = _cylinder(session)
    bottom = face_with_normal(_faces(session, made), (0.0, 0.0, -1.0))
    _shell(session, (bottom.handle,))
    assert _volume(session) == pytest.approx(math.pi * (400 * 30 - 18**2 * 28))


# --- refusals ------


@pytest.mark.parametrize(
    ("extra", "word"),
    [({"outward": True}, "outward"), ({"join": "arc"}, "arc")],
)
def test_outward_and_arc_joins_are_not_supported(session: Session, extra: dict, word: str) -> None:
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z).handle
    with pytest.raises(AdapterError) as info:
        _shell(session, (top,), **extra)
    assert _code(info) is ErrorCode.not_supported and word in info.value.info.message
    assert _volume(session) == pytest.approx(24000.0)  # nothing was built


def test_adjacent_removed_faces_are_not_supported(session: Session) -> None:
    session.new_part()
    made = _box(session)
    faces = _faces(session, made)
    with pytest.raises(AdapterError) as info:
        _shell(session, (face_with_normal(faces, Z).handle, face_with_normal(faces, (1.0, 0.0, 0.0)).handle))
    assert _code(info) is ErrorCode.not_supported and "adjacent" in info.value.info.message


def test_removing_the_side_wall_of_a_cylinder_is_not_supported(session: Session) -> None:
    session.new_part()
    made = _cylinder(session)
    side = next(f for f in _faces(session, made) if f.surface is SurfaceType.cylinder)
    with pytest.raises(AdapterError) as info:
        _shell(session, (side.handle,))
    assert _code(info) is ErrorCode.not_supported


def test_an_edited_body_is_not_supported(session: Session) -> None:
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z)
    session.adapter.hole(
        HoleRequest(
            operation_id="hole",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=4.0,
            end_condition=EndCondition.through_all,
        )
    )
    with pytest.raises(AdapterError) as info:
        _shell(session, (top.handle,))
    assert _code(info) is ErrorCode.not_supported and "untouched" in info.value.info.message


def test_a_second_shell_of_the_same_body_is_not_supported(session: Session) -> None:
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z).handle
    _shell(session, (top,))
    with pytest.raises(AdapterError) as info:
        _shell(session, (top,), op="again")
    assert _code(info) is ErrorCode.not_supported


@pytest.mark.parametrize(("t", "axis"), [(15.0, "Y"), (16.0, "Y")])
def test_a_wall_that_leaves_no_cavity_fails_and_changes_nothing(session: Session, t: float, axis: str) -> None:
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z).handle
    with pytest.raises(AdapterError) as info:
        _shell(session, (top,), t)
    assert _code(info) is ErrorCode.geometry_failed
    assert "cavity" in info.value.info.message and axis in info.value.info.message
    assert _volume(session) == pytest.approx(24000.0)
    assert _total_faces(session) == 6


def test_a_wall_as_thick_as_the_cylinder_radius_fails(session: Session) -> None:
    session.new_part()
    made = _cylinder(session)
    top = face_with_normal(_faces(session, made), Z).handle
    with pytest.raises(AdapterError) as info:
        _shell(session, (top,), 20.0)
    assert _code(info) is ErrorCode.geometry_failed and "radius" in info.value.info.message


def test_faces_of_two_bodies_are_invalid(session: Session) -> None:
    session.new_part()
    first = _box(session)
    second = _box(session, op="box2")
    a = face_with_normal(_faces(session, first), Z).handle
    b = face_with_normal(_faces(session, second), Z).handle
    with pytest.raises(AdapterError) as info:
        _shell(session, (a, b))
    assert _code(info) is ErrorCode.invalid_request


def test_an_unknown_face_is_an_invalid_reference(session: Session) -> None:
    session.new_part()
    _box(session)
    with pytest.raises(AdapterError) as info:
        _shell(session, (FaceHandle(id="face:nope"),))
    assert _code(info) is ErrorCode.invalid_reference


def test_the_new_faces_carry_the_shell_as_provenance(session: Session) -> None:
    session.new_part()
    made = _box(session)
    top = face_with_normal(_faces(session, made), Z).handle
    result = _shell(session, (top,))
    created = session.adapter.faces_of(
        FeatureRequest(operation_id="p", feature=result.feature, role=ProvenanceRole.created)
    )  # type: ignore[arg-type]
    modified = session.adapter.faces_of(
        FeatureRequest(operation_id="p", feature=result.feature, role=ProvenanceRole.modified)  # type: ignore[arg-type]
    )
    assert len(created.faces) == 5 and [f.handle for f in modified.faces] == [top]
