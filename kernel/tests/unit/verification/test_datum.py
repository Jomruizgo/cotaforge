"""Hand-computed tests for cotaforge.verification.datum (pure datum plane / axis math)."""

import math

import pytest

from cotaforge.contract import Axis, PlaneName, SketchFrame, frame_for_normal, frame_for_plane
from cotaforge.verification import datum


def _close(a: tuple[float, ...], b: tuple[float, ...], tol: float = 1e-9) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def test_frame_for_normal_matches_face_rule() -> None:
    frame = frame_for_normal((1.0, 2.0, 3.0), (0.0, 0.0, 2.0))
    assert frame.origin == (1.0, 2.0, 3.0)
    assert _close(frame.n, (0.0, 0.0, 1.0))
    assert _close(frame.u, (1.0, 0.0, 0.0))
    assert _close(frame.v, (0.0, 1.0, 0.0))


# --- offset ------------------------------------------------------------------------


def test_offset_xy_moves_origin_along_normal() -> None:
    frame = datum.plane_offset(frame_for_plane(PlaneName.XY), 10.0)
    assert _close(frame.origin, (0.0, 0.0, 10.0))
    assert _close(frame.n, (0.0, 0.0, 1.0))
    assert _close(frame.u, (1.0, 0.0, 0.0))
    assert _close(frame.v, (0.0, 1.0, 0.0))


def test_offset_xz_keeps_the_inherited_frame() -> None:
    # XZ has u=+X, v=-Z, n=+Y (contract._PLANE_FRAMES): the datum inherits it verbatim.
    frame = datum.plane_offset(frame_for_plane(PlaneName.XZ), 5.0)
    assert _close(frame.origin, (0.0, 5.0, 0.0))
    assert _close(frame.u, (1.0, 0.0, 0.0))
    assert _close(frame.v, (0.0, 0.0, -1.0))
    assert _close(frame.n, (0.0, 1.0, 0.0))


def test_offset_negative_goes_against_the_normal() -> None:
    frame = datum.plane_offset(frame_for_plane(PlaneName.YZ), -4.0)
    assert _close(frame.origin, (-4.0, 0.0, 0.0))


def test_offset_zero_is_coplanar() -> None:
    base = frame_for_plane(PlaneName.XY)
    assert datum.plane_offset(base, 0.0) == base


# --- angle -------------------------------------------------------------------------


def test_angle_30_about_x_of_xy() -> None:
    about = Axis(origin=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0))
    frame = datum.plane_angle(frame_for_plane(PlaneName.XY), about, 30.0)
    assert _close(frame.n, (0.0, -math.sin(math.radians(30)), math.cos(math.radians(30))))
    assert _close(frame.u, (1.0, 0.0, 0.0))
    assert _close(frame.origin, (0.0, 0.0, 0.0))
    # frame stays orthonormal and right handed
    assert _close(frame.v, (0.0, math.cos(math.radians(30)), math.sin(math.radians(30))))


def test_angle_about_axis_offset_in_plane_rotates_origin_about_it() -> None:
    base = datum.plane_offset(frame_for_plane(PlaneName.XY), 0.0)
    about = Axis(origin=(0.0, 10.0, 0.0), direction=(1.0, 0.0, 0.0))
    frame = datum.plane_angle(base, about, 90.0)
    # rotating the XY plane 90 deg about the line y=10 (in the plane) gives a plane with normal -Y
    assert _close(frame.n, (0.0, -1.0, 0.0))
    # the datum contains the axis
    assert abs(sum((c - o) * n for c, o, n in zip(about.origin, frame.origin, frame.n, strict=True))) < 1e-9


def test_angle_axis_not_in_base_plane_is_rejected() -> None:
    lifted = Axis(origin=(0.0, 0.0, 3.0), direction=(1.0, 0.0, 0.0))
    with pytest.raises(datum.DatumError, match="does not lie in base plane"):
        datum.plane_angle(frame_for_plane(PlaneName.XY), lifted, 30.0)
    skew = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    with pytest.raises(datum.DatumError, match="does not lie in base plane"):
        datum.plane_angle(frame_for_plane(PlaneName.XY), skew, 30.0)


# --- three points ------------------------------------------------------------------


def test_three_points_normal_follows_right_hand_rule() -> None:
    frame = datum.plane_three_points((0.0, 0.0, 5.0), (1.0, 0.0, 5.0), (0.0, 1.0, 5.0))
    assert _close(frame.n, (0.0, 0.0, 1.0))
    assert _close(frame.origin, (0.0, 0.0, 5.0))


def test_three_points_collinear_raises() -> None:
    with pytest.raises(datum.DatumError, match="collinear"):
        datum.plane_three_points((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (2.0, 2.0, 2.0))


def test_three_points_coincident_raises() -> None:
    with pytest.raises(datum.DatumError, match="collinear"):
        datum.plane_three_points((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (2.0, 2.0, 2.0))


# --- normal_to ---------------------------------------------------------------------


def test_normal_to_tilted_axis_has_the_axis_direction_as_normal() -> None:
    tilt = math.radians(30)
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(math.sin(tilt), 0.0, math.cos(tilt)))
    frame = datum.plane_normal_to(axis, 20.0)
    assert _close(frame.n, axis.direction)
    assert _close(frame.origin, (20 * math.sin(tilt), 0.0, 20 * math.cos(tilt)))
    # orthonormal and right handed
    assert abs(sum(a * b for a, b in zip(frame.u, frame.n, strict=True))) < 1e-9
    assert _close(
        frame.v,
        (
            frame.n[1] * frame.u[2] - frame.n[2] * frame.u[1],
            frame.n[2] * frame.u[0] - frame.n[0] * frame.u[2],
            frame.n[0] * frame.u[1] - frame.n[1] * frame.u[0],
        ),
    )


def test_normal_to_z_axis_uses_the_face_rule_frame() -> None:
    frame = datum.plane_normal_to(Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)), 7.0)
    assert _close(frame.u, (1.0, 0.0, 0.0))
    assert _close(frame.v, (0.0, 1.0, 0.0))
    assert _close(frame.origin, (0.0, 0.0, 7.0))


# --- midplane ----------------------------------------------------------------------


def test_midplane_is_halfway_and_inherits_the_first_frame() -> None:
    a = frame_for_plane(PlaneName.XZ)
    b = datum.plane_offset(a, 30.0)
    mid = datum.plane_midplane(a, b)
    assert _close(mid.origin, (0.0, 15.0, 0.0))
    assert _close(mid.n, a.n)
    assert _close(mid.v, a.v)


def test_midplane_of_opposite_normals_still_halfway() -> None:
    a = frame_for_plane(PlaneName.XY)
    b = datum.plane_offset(a, 8.0)
    b = b.model_copy(update={"n": (0.0, 0.0, -1.0)})
    assert _close(datum.plane_midplane(a, b).origin, (0.0, 0.0, 4.0))


def test_midplane_of_non_parallel_planes_raises() -> None:
    with pytest.raises(datum.DatumError, match="not parallel"):
        datum.plane_midplane(frame_for_plane(PlaneName.XY), frame_for_plane(PlaneName.XZ))


# --- axes --------------------------------------------------------------------------


def test_axis_two_points() -> None:
    axis = datum.axis_two_points((1.0, 1.0, 1.0), (1.0, 1.0, 6.0))
    assert axis.origin == (1.0, 1.0, 1.0)
    assert _close(axis.direction, (0.0, 0.0, 1.0))


def test_axis_two_points_coincident_raises() -> None:
    with pytest.raises(datum.DatumError, match="coincident"):
        datum.axis_two_points((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))


def test_axis_point_direction_normalises() -> None:
    axis = datum.axis_point_direction((0.0, 2.0, 0.0), (0.0, 0.0, 5.0))
    assert _close(axis.direction, (0.0, 0.0, 1.0))
    assert axis.origin == (0.0, 2.0, 0.0)


def test_axis_nearest_origin_moves_the_origin_to_the_foot_of_the_perpendicular_from_the_world_origin() -> None:
    axis = datum.axis_nearest_origin(Axis(origin=(10.0, 10.0, 8.0), direction=(0.0, 0.0, -1.0)))
    assert _close(axis.origin, (10.0, 10.0, 0.0))
    assert _close(axis.direction, (0.0, 0.0, -1.0))
    # an axis through the world origin keeps it whatever the point it was described by
    tilted = datum.axis_nearest_origin(
        Axis(origin=(0.0, -25.0, 43.30127018922193), direction=(0.0, -0.5, 0.8660254037844386))
    )
    assert _close(tilted.origin, (0.0, 0.0, 0.0), 1e-9)


def test_axis_point_direction_zero_raises() -> None:
    with pytest.raises(datum.DatumError, match="zero"):
        datum.axis_point_direction((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def test_axis_of_plane_intersection() -> None:
    # z=3 (XY offset) meets x=2 (YZ offset): the line x=2, z=3 running along Y
    a = datum.plane_offset(frame_for_plane(PlaneName.XY), 3.0)
    b = datum.plane_offset(frame_for_plane(PlaneName.YZ), 2.0)
    axis = datum.axis_plane_intersection(a, b)
    assert _close(axis.origin, (2.0, 0.0, 3.0))
    assert abs(abs(axis.direction[1]) - 1.0) < 1e-9


def test_axis_of_parallel_planes_raises() -> None:
    a = frame_for_plane(PlaneName.XY)
    with pytest.raises(datum.DatumError, match="parallel"):
        datum.axis_plane_intersection(a, datum.plane_offset(a, 5.0))


# --- validation helper -------------------------------------------------------------


def test_points_are_collinear_predicate() -> None:
    assert datum.points_collinear((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (5.0, 0.0, 0.0))
    assert not datum.points_collinear((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))


def test_angle_accepts_the_rounded_numbers_an_agent_writes() -> None:
    """The mid-plane of a 30-degree tube: normal (0, -0.5, 0.8660254), centre (0, -25, 43.30127). An agent rounds
    the in-plane direction to 3 decimals and the centre to 2: still an axis lying in the plane for its purposes."""
    section = datum.plane_normal_to(Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, -0.5, 0.8660254037844386)), 50.0)
    rounded = Axis(origin=(0.0, -25.0, 43.30), direction=(1.0, 0.0, 0.0))
    tilted_direction = Axis(origin=(0.0, -25.0, 43.30127018922193), direction=(0.0, 0.866, 0.5))
    for about in (rounded, tilted_direction):
        frame = datum.plane_angle(section, about, 90.0)
        exact = datum.plane_angle(
            section, Axis(origin=section.origin, direction=frame_about_direction(section, about)), 90.0
        )
        assert _close(frame.n, exact.n, 1e-3)
    # the result is a proper frame even so: unit, orthogonal, right-handed
    assert abs(math.sqrt(sum(c * c for c in frame.n)) - 1.0) < 1e-9
    assert _close(frame.v, tuple(datum.f.cross(frame.n, frame.u)), 1e-9)


def frame_about_direction(base: SketchFrame, about: Axis) -> tuple[float, ...]:  # ``about`` projected into the plane
    d = about.direction
    along = sum(a * b for a, b in zip(d, base.n, strict=True))
    projected = tuple(a - along * b for a, b in zip(d, base.n, strict=True))
    length = math.sqrt(sum(c * c for c in projected))
    return tuple(c / length for c in projected)


def test_angle_still_rejects_an_axis_clearly_outside_the_base_plane() -> None:
    base = frame_for_plane(PlaneName.XY)
    with pytest.raises(datum.DatumError, match="does not lie in base plane"):
        datum.plane_angle(base, Axis(origin=(0.0, 0.0, 0.5), direction=(1.0, 0.0, 0.0)), 30.0)  # 0.5 mm above
    with pytest.raises(datum.DatumError, match="does not lie in base plane"):
        datum.plane_angle(base, Axis(origin=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.05)), 30.0)  # 2.9 degrees off
