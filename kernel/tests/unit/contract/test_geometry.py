"""Tests for cotaforge.contract.geometry: Vec3/Mat3 helpers, Axis, BoundingBox, Transform, SketchFrame."""

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from cotaforge.contract.descriptors import FaceInfo, Provenance
from cotaforge.contract.enums import PlaneName, SurfaceType
from cotaforge.contract.geometry import (
    Axis,
    BoundingBox,
    Transform,
    frame_for_face,
    frame_for_plane,
    radial_entry,
)
from cotaforge.contract.handles import BodyHandle, FaceHandle, FeatureHandle


def test_axis_normalises_direction() -> None:
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 5.0))
    assert axis.direction == pytest.approx((0.0, 0.0, 1.0))


def test_axis_rejects_zero_direction() -> None:
    with pytest.raises(ValidationError):
        Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 0.0))


def test_bounding_box_size_center_extent() -> None:
    bbox = BoundingBox(min=(0.0, 0.0, 0.0), max=(10.0, 20.0, 30.0))
    assert bbox.size() == pytest.approx((10.0, 20.0, 30.0))
    assert bbox.center() == pytest.approx((5.0, 10.0, 15.0))
    assert bbox.extent_along((0.0, 0.0, 1.0)) == pytest.approx(30.0)
    assert bbox.extent_along((1.0, 0.0, 0.0)) == pytest.approx(10.0)


def test_transform_identity_is_noop() -> None:
    t = Transform.identity()
    p = (1.0, 2.0, 3.0)
    assert t.apply(p) == pytest.approx(p)
    assert t.apply_direction(p) == pytest.approx(p)


def test_transform_from_axis_angle_z_90() -> None:
    t = Transform.from_axis_angle(axis=(0.0, 0.0, 1.0), angle_deg=90.0)
    result = t.apply((1.0, 0.0, 0.0))
    assert result[0] == pytest.approx(0.0, abs=1e-9)
    assert result[1] == pytest.approx(1.0, abs=1e-9)
    assert result[2] == pytest.approx(0.0, abs=1e-9)


def test_transform_rotation_is_orthonormal_det_plus_one() -> None:
    t = Transform.from_axis_angle(axis=(1.0, 1.0, 1.0), angle_deg=37.0)
    rows = t.rotation
    # orthonormal: each row unit length, rows mutually orthogonal
    for row in rows:
        length = math.sqrt(sum(c * c for c in row))
        assert length == pytest.approx(1.0, abs=1e-9)
    for i in range(3):
        for j in range(i + 1, 3):
            dot = sum(rows[i][k] * rows[j][k] for k in range(3))
            assert dot == pytest.approx(0.0, abs=1e-9)
    det = (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )
    assert det == pytest.approx(1.0, abs=1e-9)


def test_transform_apply_with_origin() -> None:
    t = Transform(origin=(1.0, 2.0, 3.0))
    assert t.apply((0.0, 0.0, 0.0)) == pytest.approx((1.0, 2.0, 3.0))
    # apply_direction ignores origin
    assert t.apply_direction((1.0, 0.0, 0.0)) == pytest.approx((1.0, 0.0, 0.0))


def test_transform_compose() -> None:
    t1 = Transform.from_axis_angle(axis=(0.0, 0.0, 1.0), angle_deg=90.0, origin=(1.0, 0.0, 0.0))
    t2 = Transform.from_axis_angle(axis=(0.0, 0.0, 1.0), angle_deg=90.0)
    composed = t2.compose(t1)
    # composed.apply(p) should equal t2.apply(t1.apply(p))
    p = (2.0, 3.0, 4.0)
    expected = t2.apply(t1.apply(p))
    assert composed.apply(p) == pytest.approx(expected, abs=1e-9)


def test_transform_to_matrix4_identity() -> None:
    t = Transform.identity()
    m = t.to_matrix4()
    assert len(m) == 16
    expected = (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    )
    assert m == pytest.approx(expected)


def test_transform_to_matrix4_with_origin() -> None:
    t = Transform(origin=(1.0, 2.0, 3.0))
    m = t.to_matrix4()
    assert m[3] == pytest.approx(1.0)
    assert m[7] == pytest.approx(2.0)
    assert m[11] == pytest.approx(3.0)


def test_frame_for_plane_xy() -> None:
    frame = frame_for_plane(PlaneName.XY)
    assert frame.origin == pytest.approx((0.0, 0.0, 0.0))
    assert frame.u == pytest.approx((1.0, 0.0, 0.0))
    assert frame.v == pytest.approx((0.0, 1.0, 0.0))
    assert frame.n == pytest.approx((0.0, 0.0, 1.0))


def test_frame_for_plane_yz() -> None:
    frame = frame_for_plane(PlaneName.YZ)
    assert frame.u == pytest.approx((0.0, 1.0, 0.0))
    assert frame.v == pytest.approx((0.0, 0.0, 1.0))
    assert frame.n == pytest.approx((1.0, 0.0, 0.0))


def test_frame_for_plane_xz() -> None:
    frame = frame_for_plane(PlaneName.XZ)
    assert frame.u == pytest.approx((1.0, 0.0, 0.0))
    assert frame.v == pytest.approx((0.0, 0.0, -1.0))
    assert frame.n == pytest.approx((0.0, 1.0, 0.0))


def test_frame_for_plane_to_world() -> None:
    frame = frame_for_plane(PlaneName.XY)
    assert frame.to_world(3.0, 4.0) == pytest.approx((3.0, 4.0, 0.0))


def _face_info(normal: tuple[float, float, float], center: tuple[float, float, float]) -> FaceInfo:
    return FaceInfo(
        handle=FaceHandle(id="face:1"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.plane,
        normal=normal,
        center=center,
        area_mm2=100.0,
        bbox=BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0)),
        provenance=Provenance(created_by=FeatureHandle(id="feature:1")),
    )


def test_frame_for_face_normal_not_parallel_to_x() -> None:
    face = _face_info(normal=(0.0, 0.0, 1.0), center=(5.0, 5.0, 10.0))
    frame = frame_for_face(face)
    assert frame.origin == pytest.approx((5.0, 5.0, 10.0))
    assert frame.n == pytest.approx((0.0, 0.0, 1.0))
    # u = normalise(X - (X.n)n) = X projected, since X.n = 0 here -> u = X
    assert frame.u == pytest.approx((1.0, 0.0, 0.0))
    assert frame.v == pytest.approx((0.0, 1.0, 0.0))


def test_frame_for_face_normal_parallel_to_x_falls_back_to_y() -> None:
    face = _face_info(normal=(1.0, 0.0, 0.0), center=(0.0, 0.0, 0.0))
    frame = frame_for_face(face)
    assert frame.n == pytest.approx((1.0, 0.0, 0.0))
    # X.n = 1 (>= 0.999) -> fall back to Y projected: Y - (Y.n)n = (0,1,0)
    assert frame.u == pytest.approx((0.0, 1.0, 0.0))
    assert frame.v == pytest.approx((0.0, 0.0, 1.0))


# --- radial_entry: where a hole enters a cylindrical face ------------------------------------------------


def _tube_face(
    direction: tuple[float, float, float], center: tuple[float, float, float] = (0.0, 0.0, 50.0)
) -> FaceInfo:
    return FaceInfo(
        handle=FaceHandle(id="face:tube"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.cylinder,
        axis=Axis(origin=center, direction=direction),
        center=center,
        radius=20.0,
        area_mm2=1.0,
        bbox=BoundingBox(min=(-20.0, -20.0, 0.0), max=(20.0, 20.0, 100.0)),
        provenance=Provenance(created_by=FeatureHandle(id="feature:1")),
    )


def test_radial_entry_on_a_vertical_tube_measures_the_angle_from_world_x() -> None:
    face = _tube_face((0.0, 0.0, 1.0))
    entry = radial_entry(face, (10.0, 0.0))
    assert entry.axis_point == pytest.approx((0.0, 0.0, 60.0))
    assert entry.outward == pytest.approx((1.0, 0.0, 0.0))
    assert entry.surface_point == pytest.approx((20.0, 0.0, 60.0))
    assert entry.drill_direction == pytest.approx((-1.0, 0.0, 0.0))
    quarter = radial_entry(face, (-5.0, 90.0))  # positive angles turn towards v = axis x u
    assert quarter.outward == pytest.approx((0.0, 1.0, 0.0))
    assert quarter.surface_point == pytest.approx((0.0, 20.0, 45.0))


def test_radial_entry_on_a_tilted_tube_uses_the_frame_of_the_axis() -> None:
    n = (0.0, -0.5, math.sqrt(3.0) / 2.0)
    entry = radial_entry(_tube_face(n, (0.0, -25.0, 43.30127018922193)), (0.0, 90.0))
    assert entry.outward == pytest.approx((0.0, math.sqrt(3.0) / 2.0, 0.5))
    assert abs(sum(a * b for a, b in zip(entry.outward, n, strict=True))) < 1e-12  # radial: perpendicular to the axis
    assert entry.surface_point == pytest.approx(
        tuple(c + 20.0 * o for c, o in zip((0.0, -25.0, 43.30127018922193), entry.outward, strict=True))
    )


def test_radial_entry_does_not_depend_on_the_sign_of_the_reported_axis() -> None:
    """A tube built along +Z and the same tube built along -Z report opposite axis senses: the same plan must drill
    the same side of the same tube, so the axis is canonicalised (first significant component in the order Z, Y, X
    positive), for the angle AND for the axial offset."""
    for direction in (
        (0.0, 0.0, 1.0),
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, -0.5, 0.8660254037844386),
        (0.3, 0.4, -0.5),
    ):
        flipped = tuple(-c for c in direction)
        for position in ((10.0, 0.0), (-5.0, 90.0), (3.0, 215.0)):
            a = radial_entry(_tube_face(direction), position)
            b = radial_entry(_tube_face(flipped), position)
            assert a.axis_point == pytest.approx(b.axis_point, abs=1e-9), (direction, position)
            assert a.outward == pytest.approx(b.outward, abs=1e-9), (direction, position)
            assert a.surface_point == pytest.approx(b.surface_point, abs=1e-9), (direction, position)


def test_radial_entry_measures_the_angle_about_the_axis_that_points_up() -> None:
    entry = radial_entry(_tube_face((0.0, 0.0, -1.0)), (0.0, 90.0))
    assert entry.outward == pytest.approx((0.0, 1.0, 0.0))  # right-hand rule about +Z, from world +X
    assert radial_entry(_tube_face((0.0, -1.0, 0.0)), (0.0, 90.0)).outward == pytest.approx(
        (0.0, 0.0, -1.0)
    )  # about +Y
    assert radial_entry(_tube_face((-1.0, 0.0, 0.0)), (0.0, 90.0)).outward == pytest.approx((0.0, 0.0, 1.0))  # about +X


def test_radial_entry_on_an_axis_along_x_falls_back_to_world_y() -> None:
    entry = radial_entry(_tube_face((1.0, 0.0, 0.0)), (0.0, 0.0))
    assert entry.outward == pytest.approx((0.0, 1.0, 0.0))
    assert radial_entry(_tube_face((1.0, 0.0, 0.0)), (0.0, 90.0)).outward == pytest.approx((0.0, 0.0, 1.0))


def test_radial_entry_projects_a_centre_that_is_off_the_axis_onto_it() -> None:
    face = _tube_face((0.0, 0.0, 1.0)).model_copy(update={"center": (7.0, 3.0, 50.0)})
    assert radial_entry(face, (0.0, 0.0)).axis_point == pytest.approx((0.0, 0.0, 50.0))


def test_radial_entry_needs_a_cylinder() -> None:
    plane = FaceInfo(
        handle=FaceHandle(id="face:top"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.plane,
        normal=(0.0, 0.0, 1.0),
        center=(0.0, 0.0, 0.0),
        area_mm2=1.0,
        bbox=BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 0.0)),
        provenance=Provenance(created_by=FeatureHandle(id="feature:1")),
    )
    with pytest.raises(ValueError, match="cylindrical"):
        radial_entry(plane, (0.0, 0.0))


def test_radial_entry_matches_the_shared_cases_the_freecad_worker_is_checked_against() -> None:
    """``tests/data/radial_entry_cases.json`` is also run by ``tests/freecad_worker/check_holes.py`` against the
    worker's own copy of the math (it imports nothing from cotaforge): the two cannot drift apart unnoticed."""
    cases = json.loads((Path(__file__).resolve().parents[2] / "data" / "radial_entry_cases.json").read_text())["cases"]
    assert len(cases) >= 6
    for case in cases:
        face = FaceInfo(
            handle=FaceHandle(id="face:1"),
            body=BodyHandle(id="body:1"),
            surface=SurfaceType.cylinder,
            axis=Axis(origin=tuple(case["axis_origin"]), direction=tuple(case["axis_dir"])),
            center=tuple(case["center"]),
            radius=case["radius"],
            area_mm2=1.0,
            bbox=BoundingBox(min=(-100.0, -100.0, -100.0), max=(100.0, 100.0, 100.0)),
            provenance=Provenance(created_by=FeatureHandle(id="feature:1")),
        )
        entry = radial_entry(face, tuple(case["position"]))
        assert entry.axis_point == pytest.approx(case["axis_point"], abs=1e-9), case["name"]
        assert entry.outward == pytest.approx(case["outward"], abs=1e-9), case["name"]
        assert entry.surface_point == pytest.approx(case["surface_point"], abs=1e-9), case["name"]
