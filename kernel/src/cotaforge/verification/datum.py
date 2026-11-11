"""Datum plane / datum axis math (ir ops ``ref_plane`` and ``ref_axis``).

Pure functions over contract geometry (``SketchFrame``, ``Axis``, ``Vec3``): the *executor* computes the pose
of a datum from already-resolved inputs, the adapters only realise it and Level 2 re-checks it. Built on
``verification.formulas`` like the rest of the analytic layer.

Frame convention :

* ``offset``, ``angle`` and ``midplane`` INHERIT the frame of their base plane: an offset XZ plane keeps
  ``u=+X, v=-Z`` (``contract.geometry._PLANE_FRAMES``); ``angle`` rotates ``u, v, n`` rigidly about the axis;
  ``midplane`` keeps the frame of its first plane.
* ``three_points`` and ``normal_to`` have no base frame: they use the planar-face rule
  (``contract.frame_for_normal``), i.e. the frame a face with the same normal would get.

Violations of geometric preconditions raise ``DatumError`` (a ``ValueError``); the executor turns them into
``ResolutionError`` so they reach the agent as plan errors with an actionable message.
"""

import math

from cotaforge.contract import Axis, SketchFrame, Transform, Vec3, frame_for_normal, points_collinear
from cotaforge.verification import formulas as f

__all__ = [
    "DatumError",
    "axis_nearest_origin",
    "axis_plane_intersection",
    "axis_point_direction",
    "axis_two_points",
    "plane_angle",
    "plane_midplane",
    "plane_normal_to",
    "plane_offset",
    "plane_three_points",
    "points_collinear",
]

_PARALLEL_SIN = 1e-6
"""Largest sine of the angle between two directions still treated as parallel."""

_IN_PLANE_SIN = 1e-3
"""Largest sine of the angle between an axis and a plane for the axis to still ``lie in`` it (0.06 degrees):
agents write directions rounded to three decimals."""

_IN_PLANE_MM = 1e-2
"""Largest distance from a plane an axis origin may have and still ``lie in`` it: agents round to two decimals."""

_ZERO = 1e-9


class DatumError(ValueError):
    """A datum definition whose resolved inputs violate a geometric precondition."""


# --- planes -----------------------------------------------------------------------------


def plane_offset(base: SketchFrame, distance_mm: float) -> SketchFrame:
    """Parallel plane ``distance_mm`` along the base normal (negative goes against it); frame inherited."""
    origin = f.add(base.origin, f.scale(base.n, distance_mm))
    return SketchFrame(origin=origin, u=base.u, v=base.v, n=base.n)


def plane_angle(base: SketchFrame, about: Axis, angle_deg: float) -> SketchFrame:
    """The base plane rotated ``angle_deg`` (right-hand rule about ``about.direction``) around an axis that
    lies in it (within ``_IN_PLANE_SIN`` / ``_IN_PLANE_MM``: it is projected onto the plane); the frame is the
    base frame rotated rigidly, so the datum contains the axis. When the axis comes from geometry the tolerance
    applies to its re-anchored point (the one nearest the world origin), which can be far from where the geometry is."""
    along_normal = f.dot(about.direction, base.n)
    distance = f.dot(f.sub(about.origin, base.origin), base.n)
    if abs(along_normal) > _IN_PLANE_SIN or abs(distance) > _IN_PLANE_MM:
        msg = (
            "angle.about axis does not lie in base plane "
            f"(axis direction . plane normal = {along_normal:.3g}, axis origin is {distance:.3g} mm from the plane)"
        )
        raise DatumError(msg)
    # within the tolerance the axis is taken to lie in the plane: project it there so the result is an exact,
    # right-handed frame whatever rounding the caller applied to the numbers
    direction = f.normalise(f.sub(about.direction, f.scale(base.n, along_normal)))
    axis_origin = f.sub(about.origin, f.scale(base.n, distance))
    rotation = Transform.from_axis_angle(direction, angle_deg)
    origin = f.add(axis_origin, rotation.apply_direction(f.sub(base.origin, axis_origin)))
    return SketchFrame(
        origin=origin,
        u=rotation.apply_direction(base.u),
        v=rotation.apply_direction(base.v),
        n=rotation.apply_direction(base.n),
    )


def plane_three_points(p0: Vec3, p1: Vec3, p2: Vec3) -> SketchFrame:
    """Plane through three points, origin ``p0``, normal ``(p1 - p0) x (p2 - p0)`` (right-hand rule)."""
    if points_collinear(p0, p1, p2):
        msg = "three_points: the points are collinear or coincident: choose three non-collinear points"
        raise DatumError(msg)
    normal = f.normalise(f.cross(f.sub(p1, p0), f.sub(p2, p0)))
    return frame_for_normal(p0, normal)


def plane_normal_to(axis: Axis, at_mm: float) -> SketchFrame:
    """Plane perpendicular to ``axis`` at distance ``at_mm`` along it from ``axis.origin``."""
    origin = f.add(axis.origin, f.scale(axis.direction, at_mm))
    return frame_for_normal(origin, axis.direction)


def plane_midplane(a: SketchFrame, b: SketchFrame) -> SketchFrame:
    """The plane halfway between two parallel planes; frame of ``a``."""
    sine = f.length(f.cross(a.n, b.n))
    if sine > _PARALLEL_SIN:
        angle = math.degrees(math.asin(min(1.0, sine)))
        if f.dot(a.n, b.n) < 0.0:
            angle = 180.0 - angle
        msg = f"midplane: planes are not parallel (angle {angle:.3g} deg)"
        raise DatumError(msg)
    gap = f.dot(f.sub(b.origin, a.origin), a.n)
    origin = f.add(a.origin, f.scale(a.n, gap / 2.0))
    return SketchFrame(origin=origin, u=a.u, v=a.v, n=a.n)


# --- axes -------------------------------------------------------------------------------


def axis_two_points(start: Vec3, end: Vec3) -> Axis:
    """Axis through two points, from ``start`` towards ``end``."""
    direction = f.sub(end, start)
    if f.length(direction) < _ZERO:
        msg = "two_points: the points are coincident: choose two distinct points"
        raise DatumError(msg)
    return Axis(origin=start, direction=direction)


def axis_point_direction(point: Vec3, direction: Vec3) -> Axis:
    if f.length(direction) < _ZERO:
        msg = "point_direction: direction is the zero vector"
        raise DatumError(msg)
    return Axis(origin=point, direction=direction)


def axis_nearest_origin(axis: Axis) -> Axis:
    """The same line described by the point closest to the world origin.

    Adapters describe the axis of a cylinder by different points of it (the fake by the base centre, FreeCAD by
    the face centre), so ``normal_to.at_mm`` measured from ``axis.origin`` would land on different planes.
    ``ref_axis.entity`` re-anchors its axis here so the distance means the same on every adapter."""
    along = f.dot(axis.origin, axis.direction)
    return Axis(origin=f.sub(axis.origin, f.scale(axis.direction, along)), direction=axis.direction)


def axis_plane_intersection(a: SketchFrame, b: SketchFrame) -> Axis:
    """Line where two planes meet: direction ``n_a x n_b``, origin the line's point closest to the world origin."""
    direction = f.cross(a.n, b.n)
    denominator = f.dot(direction, direction)
    if math.sqrt(denominator) <= _PARALLEL_SIN:
        msg = "plane_intersection: the planes are parallel and never meet"
        raise DatumError(msg)
    d_a = f.dot(a.n, a.origin)
    d_b = f.dot(b.n, b.origin)
    weighted = f.add(f.scale(f.cross(b.n, direction), d_a), f.scale(f.cross(direction, a.n), d_b))
    point = f.scale(weighted, 1.0 / denominator)
    return Axis(origin=point, direction=direction)
