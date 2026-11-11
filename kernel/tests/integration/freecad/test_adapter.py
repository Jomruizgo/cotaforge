"""FreeCADAdapter against the real worker inside ``freecadcmd``:
connect/canary/validate_api, frame parity, the plate and its through hole against hand numbers, undo, and
error mapping. Each number here is the same the fake returns."""

import math

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    ArcEntity,
    DocumentKind,
    Empty,
    EndCondition,
    ErrorCode,
    ExtrudeRequest,
    FaceInfo,
    FaceRequest,
    FeatureHandle,
    FeatureRequest,
    HoleRequest,
    HoleStandard,
    MassRequest,
    NewDocumentRequest,
    OperationResult,
    PlaneName,
    RectEntity,
    SketchRequest,
    SurfaceType,
    Vec3,
    frame_for_face,
    frame_for_plane,
)

pytestmark = pytest.mark.freecad

PLATE_VOLUME = 100.0 * 60.0 * 8.0  # 48000
HOLE_RADIUS = 3.3
PLATE_WITH_HOLE_VOLUME = PLATE_VOLUME - math.pi * HOLE_RADIUS**2 * 8.0  # 47726.304
AXES: tuple[Vec3, ...] = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _rect(name: str, center: tuple[float, float], width: float, height: float) -> RectEntity:
    return RectEntity(name=name, center=center, width=width, height=height)


def _close(a: Vec3, b: Vec3, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _new_part(adapter: FreeCADAdapter, name: str) -> None:
    adapter.new_document(NewDocumentRequest(operation_id=f"{name}.new_document", kind=DocumentKind.part, name=name))


def _plate(adapter: FreeCADAdapter, name: str) -> OperationResult:
    """100 x 60 x 8 plate on XY, in a fresh part document."""
    _new_part(adapter, name)
    sketch = adapter.sketch(
        SketchRequest(operation_id=f"{name}.sketch", on=PlaneName.XY, entities=(_rect("outline", (50, 30), 100, 60),))
    )
    assert sketch.profile_areas_mm2 == pytest.approx((6000.0,))
    return adapter.extrude(ExtrudeRequest(operation_id=f"{name}.base", sketch=sketch.sketch, depth_mm=8.0))


def _faces(adapter: FreeCADAdapter, feature: FeatureHandle | None, op: str) -> tuple[FaceInfo, ...]:
    assert feature is not None
    return adapter.faces_of(FeatureRequest(operation_id=op, feature=feature)).faces


def _face_with_normal(faces: tuple[FaceInfo, ...], normal: Vec3) -> FaceInfo:
    matches = [f for f in faces if f.normal is not None and _close(f.normal, normal)]
    assert len(matches) == 1, f"expected one face with normal {normal}, got {[f.handle.id for f in matches]}"
    return matches[0]


# --- session and health --------------------------------------------------------------------------


def test_connect_reports_freecad_version_and_a_healthy_session(adapter: FreeCADAdapter) -> None:
    assert adapter.worker_pid is not None
    canary = adapter.canary(Empty(operation_id="canary"))
    assert canary.healthy, canary
    assert set(canary.checks) >= {"plane", "box", "normal", "undo"}
    assert canary.duration_ms > 0
    validation = adapter.validate_api(Empty(operation_id="validate"))
    assert validation.ok, validation.problems
    assert validation.problems == ()


# --- frame parity ----------------------------------------------------------------------------------


@pytest.mark.parametrize("plane", [PlaneName.XY, PlaneName.YZ, PlaneName.XZ])
def test_sketch_frame_matches_frame_for_plane(adapter: FreeCADAdapter, plane: PlaneName) -> None:
    _new_part(adapter, f"frame_{plane.value}")
    result = adapter.sketch(
        SketchRequest(operation_id=f"frame.{plane.value}", on=plane, entities=(_rect("r", (10, 5), 20, 10),))
    )
    expected = frame_for_plane(plane)
    assert _close(result.frame.origin, expected.origin)
    assert _close(result.frame.u, expected.u)
    assert _close(result.frame.v, expected.v)
    assert _close(result.frame.n, expected.n)


@pytest.mark.parametrize("normal", AXES, ids=lambda n: "".join("+-0"[[1, -1, 0].index(c)] for c in n))
def test_sketch_frame_on_a_box_face_matches_frame_for_face(adapter: FreeCADAdapter, normal: Vec3) -> None:
    base = _plate(adapter, "frame_faces")
    face = _face_with_normal(_faces(adapter, base.feature, "faces"), normal)
    result = adapter.sketch(
        SketchRequest(operation_id=f"frame.face.{face.handle.id}", on=face.handle, entities=(_rect("r", (0, 0), 2, 2),))
    )
    expected = frame_for_face(face)
    assert _close(result.frame.origin, expected.origin, tol=1e-6)
    assert _close(result.frame.u, expected.u)
    assert _close(result.frame.v, expected.v)
    assert _close(result.frame.n, expected.n)


# --- plate and hole against hand numbers ------------------------------------------------------------


def test_plate_has_six_outward_faces_and_volume_48000(adapter: FreeCADAdapter) -> None:
    base = _plate(adapter, "plate")
    assert base.rebuild_ok
    assert base.feature is not None
    assert len(base.created_faces) == 6
    assert len(base.created_bodies) == 1
    mass = adapter.mass_properties(MassRequest(operation_id="mass"))
    assert mass.volume_mm3 == pytest.approx(PLATE_VOLUME, abs=1e-6)
    assert mass.centroid == pytest.approx((50.0, 30.0, 4.0), abs=1e-6)
    faces = _faces(adapter, base.feature, "faces")
    assert len(faces) == 6
    assert all(f.surface is SurfaceType.plane for f in faces)
    for axis in AXES:
        face = _face_with_normal(faces, axis)
        outward = sum((c - g) * n for c, g, n in zip(face.center, mass.centroid, face.normal or (0, 0, 0), strict=True))
        assert outward > 0, f"normal of {face.handle.id} points inward"
    bbox = adapter.bounding_box(MassRequest(operation_id="bbox"))
    assert bbox.min == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    assert bbox.max == pytest.approx((100.0, 60.0, 8.0), abs=1e-6)


def test_through_hole_removes_the_cylinder_volume(adapter: FreeCADAdapter) -> None:
    base = _plate(adapter, "plate_hole")
    top = _face_with_normal(_faces(adapter, base.feature, "faces"), (0, 0, 1))
    hole = adapter.hole(
        HoleRequest(
            operation_id="hole_1",
            face=top.handle,
            position=(-40.0, -20.0),
            standard=HoleStandard.iso_clearance,
            size="M6",
            diameter_mm=2 * HOLE_RADIUS,
            end_condition=EndCondition.through_all,
        )
    )
    assert hole.rebuild_ok
    assert hole.feature is not None
    assert len(hole.created_faces) == 1
    bore = adapter.face_info(FaceRequest(operation_id="fi", face=hole.created_faces[0]))
    assert bore.surface is SurfaceType.cylinder
    assert bore.radius == pytest.approx(HOLE_RADIUS, abs=1e-6)
    assert bore.axis is not None
    assert abs(bore.axis.direction[2]) == pytest.approx(1.0, abs=1e-9)
    assert bore.center == pytest.approx((10.0, 10.0, 4.0), abs=1e-6)
    assert {f.id for f in hole.modified_faces} >= {top.handle.id}
    mass = adapter.mass_properties(MassRequest(operation_id="mass"))
    assert mass.volume_mm3 == pytest.approx(PLATE_WITH_HOLE_VOLUME, abs=1e-3)


# --- undo ---------------------------------------------------------------------------------------------


def test_undo_last_restores_the_fingerprint_before_the_hole(adapter: FreeCADAdapter) -> None:
    base = _plate(adapter, "plate_undo")
    top = _face_with_normal(_faces(adapter, base.feature, "faces"), (0, 0, 1))
    before = (
        adapter.feature_tree(Empty(operation_id="tree")),
        adapter.mass_properties(MassRequest(operation_id="mass")),
        len(_faces(adapter, base.feature, "faces")),
    )
    hole = adapter.hole(
        HoleRequest(
            operation_id="hole_1",
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.simple,
            diameter_mm=10.0,
            end_condition=EndCondition.blind,
            depth_mm=4.0,
        )
    )
    assert hole.feature is not None
    undo = adapter.undo_last(Empty(operation_id="undo"))
    assert undo.reverted == hole.feature
    after = (
        adapter.feature_tree(Empty(operation_id="tree")),
        adapter.mass_properties(MassRequest(operation_id="mass")),
        len(_faces(adapter, base.feature, "faces")),
    )
    assert after[0] == before[0]
    assert after[1].volume_mm3 == pytest.approx(before[1].volume_mm3, abs=1e-6)
    assert after[2] == before[2] == 6
    assert _face_with_normal(_faces(adapter, base.feature, "faces"), (0, 0, 1)).handle == top.handle
    assert adapter.undo_last(Empty(operation_id="undo")).reverted == base.feature
    assert adapter.undo_last(Empty(operation_id="undo")).reverted is None


# --- error mapping --------------------------------------------------------------------------------------


def test_an_open_arc_chain_is_an_invalid_request_and_costs_nothing(adapter: FreeCADAdapter) -> None:
    _new_part(adapter, "arc")
    with pytest.raises(AdapterError) as info:
        adapter.sketch(
            SketchRequest(
                operation_id="arc_sketch",
                on=PlaneName.XY,
                entities=(ArcEntity(name="a", center=(0, 0), radius=5, start_deg=0, end_deg=90),),
            )
        )
    assert info.value.info.code is ErrorCode.invalid_request
    assert "open chain" in info.value.info.message
    assert info.value.info.operation_id == "arc_sketch"
    assert adapter.canary(Empty(operation_id="canary")).healthy  # a mapped error never costs the session
