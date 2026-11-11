"""``sweep`` on the fake adapter: exact volume (area x path length), analytic faces for circular sections, an
exact box for mitred paths, and honest refusals for what it cannot model (booleans on swept bodies, filleted
polygon sections)."""

import math

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AdapterError,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CircleEntity,
    CopyBodyRequest,
    DatumPlaneRequest,
    DatumPlaneTarget,
    Empty,
    ErrorCode,
    FaceRequest,
    MassRequest,
    MoveBodyRequest,
    OperationResult,
    PlaneName,
    RectEntity,
    SketchHandle,
    SketchRequest,
    SurfaceType,
    SweepRequest,
    Transform,
    frame_for_normal,
)
from tests.unit.fake.conftest import Session, cylindrical_faces, vec_close
from tests.unit.fake.test_undo import _round_trip

AREA = 25.0 * math.pi
Z_PATH = ((0.0, 0.0, 0.0), (0.0, 0.0, 100.0))
L_PATH = ((0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0))


def _sweep(
    session: Session,
    path: tuple,
    *,
    sketch: str | None = None,
    radius: float | None = None,
    merge: bool = True,
    op: str = "sweep",
) -> OperationResult:
    if sketch is None:
        sketch = session.circle_sketch("sec", PlaneName.XY, (0.0, 0.0), 10.0)
    return session.adapter.sweep(
        SweepRequest(operation_id=op, sketch=SketchHandle(id=sketch), path=path, corner_radius_mm=radius, merge=merge)
    )


def _mass(session: Session, body: BodyHandle | None = None):  # noqa: ANN202
    return session.adapter.mass_properties(MassRequest(operation_id="m", body=body))


def _bbox(session: Session, body: BodyHandle | None = None) -> BoundingBox:
    return session.adapter.bounding_box(MassRequest(operation_id="b", body=body))


def _face_infos(session: Session, result: OperationResult):  # noqa: ANN202
    return [session.adapter.face_info(FaceRequest(operation_id="f", face=f)) for f in result.created_faces]


# --- straight tube ---------------------------------------------------------------------------------------------


def test_a_straight_tube_has_exact_volume_centroid_box_and_three_faces(session: Session) -> None:
    session.new_part()
    result = _sweep(session, Z_PATH)
    mass = _mass(session)
    assert mass.volume_mm3 == pytest.approx(AREA * 100.0)
    assert vec_close(mass.centroid, (0.0, 0.0, 50.0))
    box = _bbox(session)
    assert vec_close(box.min, (-5.0, -5.0, 0.0)) and vec_close(box.max, (5.0, 5.0, 100.0))
    faces = _face_infos(session, result)
    assert len(faces) == 3 and len(result.created_bodies) == 1
    (lateral,) = cylindrical_faces(tuple(faces))
    assert lateral.radius == pytest.approx(5.0)
    assert lateral.area_mm2 == pytest.approx(2.0 * math.pi * 5.0 * 100.0)
    assert mass.surface_mm2 == pytest.approx(2.0 * math.pi * 5.0 * 100.0 + 2.0 * AREA)
    assert len(result.created_edges) == 2  # the two cap boundaries


# --- bends ---------------------------------------------------------------------------------------------------------


def test_an_l_mitre_has_two_lateral_faces_and_the_exact_box(session: Session) -> None:
    session.new_part()
    result = _sweep(session, L_PATH)
    assert _mass(session).volume_mm3 == pytest.approx(AREA * 200.0)
    assert len(result.created_faces) == 4
    box = _bbox(session)
    assert vec_close(box.min, (-5.0, -5.0, 0.0)) and vec_close(box.max, (100.0, 5.0, 105.0))


def test_the_centroid_of_a_bent_sweep_is_the_length_weighted_path_midpoint(session: Session) -> None:
    session.new_part()
    _sweep(session, L_PATH)
    # documented approximation: two 100 mm segments, midpoints (0,0,50) and (50,0,100)
    assert vec_close(_mass(session).centroid, (25.0, 0.0, 75.0))


def test_a_fillet_shortens_the_path_and_adds_a_torus_face(session: Session) -> None:
    session.new_part()
    result = _sweep(session, L_PATH, radius=20.0)
    length = 200.0 - 40.0 + 10.0 * math.pi
    assert _mass(session).volume_mm3 == pytest.approx(AREA * length)
    assert _mass(session).volume_mm3 == pytest.approx(15033.77, abs=0.05)
    faces = _face_infos(session, result)
    assert len(faces) == 5
    (torus,) = [f for f in faces if f.surface is SurfaceType.torus]
    assert torus.radius == pytest.approx(20.0)  # major (bend) radius, as the FreeCAD worker reports a torus
    assert torus.area_mm2 == pytest.approx(2.0 * math.pi * 5.0 * (20.0 * math.pi / 2.0))
    assert len(cylindrical_faces(tuple(faces))) == 2
    # the straight parts are 80 mm long each (100 - the 20 mm tangent length)
    assert (
        sorted(round(f.area_mm2, 3) for f in cylindrical_faces(tuple(faces)))
        == [round(2.0 * math.pi * 5.0 * 80.0, 3)] * 2
    )


def test_a_fillet_box_contains_the_centre_line_and_both_end_discs(session: Session) -> None:
    session.new_part()
    _sweep(session, L_PATH, radius=20.0)
    box = _bbox(session)
    assert vec_close(box.min, (-5.0, -5.0, 0.0), tol=1e-6)
    assert vec_close(box.max, (100.0, 5.0, 105.0), tol=1e-6)


# --- polygon sections --------------------------------------------------------------------------------------------


def test_a_rectangular_section_mitres_with_planar_faces(session: Session) -> None:
    session.new_part()
    sketch, area = session.rect_sketch("sec", PlaneName.XY, (0.0, 0.0), 10.0, 4.0)
    result = _sweep(session, L_PATH, sketch=sketch)
    assert area == pytest.approx(40.0)
    assert _mass(session).volume_mm3 == pytest.approx(40.0 * 200.0)
    assert len(result.created_faces) == 2 + 2 * 4  # 2 caps + 4 sides on each of the 2 segments
    box = _bbox(session)
    assert vec_close(box.min, (-5.0, -2.0, 0.0)) and vec_close(box.max, (100.0, 2.0, 105.0))


def test_a_triangular_section_has_the_polygon_area_volume(session: Session) -> None:
    session.new_part()
    sketch = session.polygon_sketch("sec", PlaneName.XY, [(-2.0, -2.0), (4.0, -2.0), (-2.0, 4.0)])
    _sweep(session, Z_PATH, sketch=sketch)
    assert _mass(session).volume_mm3 == pytest.approx(18.0 * 100.0)


def test_a_filleted_polygon_section_is_not_supported(session: Session) -> None:
    session.new_part()
    sketch, _ = session.rect_sketch("sec", PlaneName.XY, (0.0, 0.0), 10.0, 4.0)
    with pytest.raises(AdapterError) as info:
        _sweep(session, L_PATH, sketch=sketch, radius=20.0)
    assert info.value.info.code is ErrorCode.not_supported
    assert "circular" in info.value.info.message


# --- oblique frames and bodies -----------------------------------------------------------------------------------


def test_an_oblique_datum_sweep_matches_the_hand_box(session: Session) -> None:
    session.new_part()
    frame = frame_for_normal((0.0, 0.0, 0.0), (0.6, 0.0, 0.8))
    handle = session.adapter.datum_plane(DatumPlaneRequest(operation_id="d", how="offset", frame=frame)).datum
    sketch = session.adapter.sketch(
        SketchRequest(
            operation_id="sec",
            on=DatumPlaneTarget(handle=handle, frame=frame),
            entities=(CircleEntity(name="c", center=(0.0, 0.0), diameter=10.0),),
        )
    ).sketch.id
    _sweep(session, ((0.0, 0.0, 0.0), (60.0, 0.0, 80.0), (60.0, 50.0, 80.0)), sketch=sketch)
    assert _mass(session).volume_mm3 == pytest.approx(AREA * 150.0)
    box = _bbox(session)
    assert vec_close(box.min, (-4.0, -5.0, -3.0)) and vec_close(box.max, (65.0, 50.0, 85.0))


def test_a_merged_sweep_joins_the_existing_body_and_a_separate_one_does_not(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    body = plate.created_bodies[0]
    sketch = session.circle_sketch("sec", PlaneName.XY, (50.0, 30.0), 10.0)
    merged = _sweep(session, ((50.0, 30.0, 0.0), (50.0, 30.0, 40.0)), sketch=sketch)
    assert merged.created_bodies == ()
    assert _mass(session, body).volume_mm3 == pytest.approx(48000.0 + AREA * 40.0)
    separate = _sweep(session, ((50.0, 30.0, 0.0), (50.0, 30.0, 40.0)), sketch=sketch, merge=False, op="s2")
    assert len(separate.created_bodies) == 1


def test_a_swept_body_can_be_moved_and_copied(session: Session) -> None:
    session.new_part()
    swept = _sweep(session, L_PATH)
    body = swept.created_bodies[0]
    session.adapter.move_body(MoveBodyRequest(operation_id="mv", body=body, transform=Transform(origin=(0, 20, 0))))
    box = _bbox(session, body)
    assert vec_close(box.min, (-5.0, 15.0, 0.0)) and vec_close(box.max, (100.0, 25.0, 105.0))
    copy = session.adapter.copy_body(
        CopyBodyRequest(operation_id="cp", source=body, transform=Transform(origin=(0, 100, 0)))
    )
    assert _mass(session, copy.created_bodies[0]).volume_mm3 == pytest.approx(AREA * 200.0)
    assert _mass(session).volume_mm3 == pytest.approx(2 * AREA * 200.0)


def test_undo_removes_the_swept_body_completely(session: Session) -> None:
    session.new_part()
    sketch = session.circle_sketch("sec", PlaneName.XY, (0.0, 0.0), 10.0)
    _round_trip(session, lambda: _sweep(session, L_PATH, sketch=sketch, radius=20.0))
    _round_trip(session, lambda: _sweep(session, L_PATH, sketch=sketch, op="again"))


# --- refusals ------------------------------------------------------------------------------------------------------


def test_a_boolean_with_a_swept_body_is_not_supported(session: Session) -> None:
    session.new_part()
    swept = _sweep(session, Z_PATH)
    other = session.adapter.box(BoxRequest(operation_id="b", size=(4.0, 4.0, 4.0)))
    with pytest.raises(AdapterError) as info:
        session.adapter.boolean(
            BooleanRequest(
                operation_id="u",
                kind=BooleanKind.union,
                target=other.created_bodies[0],
                tools=(swept.created_bodies[0],),
            )
        )
    assert info.value.info.code is ErrorCode.not_supported
    assert "swept" in info.value.info.message


def test_the_profile_must_sit_on_the_path_start(session: Session) -> None:
    session.new_part()
    sketch = session.circle_sketch("sec", PlaneName.XY, (10.0, 0.0), 10.0)
    with pytest.raises(AdapterError) as info:
        _sweep(session, Z_PATH, sketch=sketch)
    assert info.value.info.code is ErrorCode.invalid_request
    assert "centroid" in info.value.info.message


def test_a_sketch_with_two_profiles_is_rejected(session: Session) -> None:
    session.new_part()
    two = session.adapter.sketch(
        SketchRequest(
            operation_id="ring",
            on=PlaneName.XY,
            entities=(
                CircleEntity(name="a", center=(0.0, 0.0), diameter=10.0),
                RectEntity(name="b", center=(30.0, 0.0), width=4.0, height=4.0),
            ),
        )
    ).sketch.id
    with pytest.raises(AdapterError) as info:
        _sweep(session, Z_PATH, sketch=two)
    assert info.value.info.code is ErrorCode.invalid_request


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        (((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 5.0)), "coincide"),
        (((0.0, 0.0, 0.0), (0.0, 0.0, 50.0), (0.0, 0.0, 100.0)), "collinear"),
        (((0.0, 0.0, 0.0), (0.0, 0.0, 50.0), (0.0, 0.0, 10.0)), "reverses"),
        (((0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0)), "does not fit"),
    ],
)
def test_degenerate_paths_are_refused_with_a_reason(session: Session, path: tuple, needle: str) -> None:
    session.new_part()
    radius = 150.0 if needle == "does not fit" else None
    with pytest.raises(AdapterError) as info:
        _sweep(session, path, radius=radius)
    assert info.value.info.code in (ErrorCode.invalid_request, ErrorCode.geometry_failed)
    assert needle in info.value.info.message
    assert _mass(session).volume_mm3 == 0.0
    assert session.adapter.undo_last(Empty(operation_id="u")).reverted is None


def test_a_one_point_path_is_refused_by_the_request_model() -> None:
    with pytest.raises(ValidationError):
        SweepRequest(operation_id="s", sketch=SketchHandle(id="sk:1"), path=((0.0, 0.0, 0.0),))
