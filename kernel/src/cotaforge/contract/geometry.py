"""Geometry primitives shared by the contract, IR, executor, verification and fake adapter.

Vec3/Mat3/Axis/BoundingBox/Transform and the sketch frames.
"""

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, model_validator

from cotaforge.contract.enums import PlaneName

if TYPE_CHECKING:
    from cotaforge.contract.descriptors import FaceInfo

Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]

IDENTITY: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

_ZERO_TOLERANCE = 1e-12


def _length(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def _normalise(v: Vec3) -> Vec3:
    length = _length(v)
    if length < _ZERO_TOLERANCE:
        msg = "cannot normalise a zero-length vector"
        raise ValueError(msg)
    return (v[0] / length, v[1] / length, v[2] / length)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _matmul3(a: Mat3, b: Mat3) -> Mat3:
    """Row-major 3x3 matrix multiplication: a @ b."""
    b_cols = tuple(zip(*b, strict=True))
    return tuple(tuple(_dot(row, col) for col in b_cols) for row in a)  # type: ignore[return-value]


def _matvec3(m: Mat3, v: Vec3) -> Vec3:
    return (_dot(m[0], v), _dot(m[1], v), _dot(m[2], v))


def points_collinear(p0: Vec3, p1: Vec3, p2: Vec3, *, sin_tolerance: float = 1e-6) -> bool:
    """True when three points do not span a plane (collinear or coincident): the sine of the angle at ``p0``
    between ``p1 - p0`` and ``p2 - p0`` is within ``sin_tolerance`` of zero."""
    a = _sub(p1, p0)
    b = _sub(p2, p0)
    la = _length(a)
    lb = _length(b)
    if la < _ZERO_TOLERANCE or lb < _ZERO_TOLERANCE:
        return True
    return _length(_cross(a, b)) / (la * lb) <= sin_tolerance


class Axis(BaseModel, frozen=True, allow_inf_nan=False):
    origin: Vec3
    direction: Vec3

    @model_validator(mode="after")
    def _normalise_direction(self) -> "Axis":
        normalised = _normalise(self.direction)
        object.__setattr__(self, "direction", normalised)
        return self


class BoundingBox(BaseModel, frozen=True):
    min: Vec3
    max: Vec3
    exact: bool = True
    """False for a quick box (a bound that may enclose a little more than the shape; the exact one costs a pass over
    every surface)."""
    left_out: tuple[str, ...] = ()
    """Bodies whose box leaves out faces that have no real size (an unbounded or garbage face of an invalid shape),
    each as ``body:3: 2 faces``."""

    def size(self) -> Vec3:
        return _sub(self.max, self.min)

    def center(self) -> Vec3:
        return _scale(_add(self.min, self.max), 0.5)

    def extent_along(self, direction: Vec3) -> float:
        """Length of the box's projection onto the (not necessarily unit) direction."""
        unit = _normalise(direction)
        corners = (
            (self.min[0], self.min[1], self.min[2]),
            (self.min[0], self.min[1], self.max[2]),
            (self.min[0], self.max[1], self.min[2]),
            (self.min[0], self.max[1], self.max[2]),
            (self.max[0], self.min[1], self.min[2]),
            (self.max[0], self.min[1], self.max[2]),
            (self.max[0], self.max[1], self.min[2]),
            (self.max[0], self.max[1], self.max[2]),
        )
        projections = [_dot(corner, unit) for corner in corners]
        return max(projections) - min(projections)


class Transform(BaseModel, frozen=True, allow_inf_nan=False):
    origin: Vec3 = (0.0, 0.0, 0.0)
    rotation: Mat3 = IDENTITY

    @classmethod
    def identity(cls) -> "Transform":
        return cls(origin=(0.0, 0.0, 0.0), rotation=IDENTITY)

    @classmethod
    def from_axis_angle(cls, axis: Vec3, angle_deg: float, origin: Vec3 = (0.0, 0.0, 0.0)) -> "Transform":
        """Rodrigues' rotation formula about a unit axis through the world origin."""
        unit = _normalise(axis)
        angle_rad = math.radians(angle_deg)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        x, y, z = unit
        # K = skew-symmetric cross-product matrix of the axis
        k: Mat3 = ((0.0, -z, y), (z, 0.0, -x), (-y, x, 0.0))
        outer: Mat3 = tuple(tuple(unit[i] * unit[j] for j in range(3)) for i in range(3))  # type: ignore[assignment]
        rotation = tuple(
            tuple(IDENTITY[i][j] * cos_a + k[i][j] * sin_a + outer[i][j] * (1.0 - cos_a) for j in range(3))
            for i in range(3)
        )
        return cls(origin=origin, rotation=rotation)  # type: ignore[arg-type]

    def apply(self, p: Vec3) -> Vec3:
        return _add(_matvec3(self.rotation, p), self.origin)

    def apply_direction(self, d: Vec3) -> Vec3:
        return _matvec3(self.rotation, d)

    def compose(self, other: "Transform") -> "Transform":
        """Self after other: ``self.compose(other).apply(p) == self.apply(other.apply(p))``."""
        new_rotation = _matmul3(self.rotation, other.rotation)
        new_origin = self.apply(other.origin)
        return Transform(origin=new_origin, rotation=new_rotation)

    def to_matrix4(self) -> tuple[float, ...]:
        """16 values, row-major, translation in the last column of each row."""
        r = self.rotation
        o = self.origin
        return (
            r[0][0],
            r[0][1],
            r[0][2],
            o[0],
            r[1][0],
            r[1][1],
            r[1][2],
            o[1],
            r[2][0],
            r[2][1],
            r[2][2],
            o[2],
            0.0,
            0.0,
            0.0,
            1.0,
        )


class SketchFrame(BaseModel, frozen=True, allow_inf_nan=False):
    origin: Vec3
    u: Vec3
    v: Vec3
    n: Vec3

    def to_world(self, u: float, v: float) -> Vec3:
        return _add(self.origin, _add(_scale(self.u, u), _scale(self.v, v)))

    def to_local(self, point: Vec3) -> Vec3:
        """The world ``point`` as (u, v, distance along the normal): ``to_world``'s inverse for a point on the plane."""
        d = _sub(point, self.origin)
        return (_dot(d, self.u), _dot(d, self.v), _dot(d, self.n))


_WORLD_X: Vec3 = (1.0, 0.0, 0.0)
_WORLD_Y: Vec3 = (0.0, 1.0, 0.0)
_WORLD_Z: Vec3 = (0.0, 0.0, 1.0)

_PLANE_FRAMES: dict[PlaneName, tuple[Vec3, Vec3, Vec3]] = {
    PlaneName.XY: (_WORLD_X, _WORLD_Y, _WORLD_Z),
    PlaneName.YZ: (_WORLD_Y, _WORLD_Z, _WORLD_X),
    PlaneName.XZ: (_WORLD_X, _scale(_WORLD_Z, -1.0), _WORLD_Y),
}


def frame_for_plane(plane: PlaneName) -> SketchFrame:
    u, v, n = _PLANE_FRAMES[plane]
    return SketchFrame(origin=(0.0, 0.0, 0.0), u=u, v=v, n=n)


def frame_for_face(face: "FaceInfo", origin: Vec3 | None = None) -> SketchFrame:
    """Build the sketch frame on a planar face, with its origin at ``origin`` (slid onto the plane of the face along
    its normal) or, without one, at the centre of the face."""
    if face.normal is None:
        msg = "frame_for_face requires a planar face with a normal"
        raise ValueError(msg)
    if origin is None:
        return frame_for_normal(face.center, face.normal)
    return frame_for_normal(slide_onto_face(origin, face), face.normal)


def slide_onto_face(point: Vec3, face: "FaceInfo") -> Vec3:
    """``point`` moved along the normal of the planar ``face`` onto its plane (what keeps a feature on the face when
    the face moves across its plane, a plate made thicker)."""
    assert face.normal is not None
    n = _normalise(face.normal)
    return _add(point, _scale(n, _dot(_sub(face.center, point), n)))


class RadialEntry(BaseModel, frozen=True, allow_inf_nan=False):
    """Where a radial hole enters a cylindrical face (``radial_entry``)."""

    axis_point: Vec3
    """The point of the cylinder axis the hole crosses."""
    outward: Vec3
    """Unit radial direction from the axis to the entry point."""
    surface_point: Vec3
    """The entry point on the cylinder surface (the hole's own axis meets the surface here)."""

    @property
    def drill_direction(self) -> Vec3:
        """The hole runs inward, along ``-outward``."""
        return _scale(self.outward, -1.0)


def canonical_axis_direction(direction: Vec3) -> Vec3:
    """``direction`` with its sense fixed: the first component in the order Z, Y, X that is not zero (|c| > 1e-9) is
    positive. A cylinder reports its axis along the way it was built; a hole position must not depend on that."""
    for index in (2, 1, 0):
        if abs(direction[index]) > 1e-9:
            return direction if direction[index] > 0 else _scale(direction, -1.0)
    return direction


def radial_entry(face: "FaceInfo", position: tuple[float, float]) -> RadialEntry:
    """Where a hole at ``position = (axial_mm, angle_deg)`` enters a cylindrical face.

    ``axial_mm`` is measured along the CANONICAL axis from ``FaceInfo.center`` (the axial midpoint of the face, which an
    adapter reports on the axis; a centre off the axis is projected onto it). The sense a cylinder reports for its axis
    follows how it was built (a tube extruded along +Z and the same tube along -Z disagree), so the axis is
    canonicalised first: its first significant world component in the order Z, Y, X is made positive (|c| > 1e-9).
    ``angle_deg`` is measured about that axis by the right-hand rule: 0 is the ``u`` of ``frame_for_normal(center,
    axis)`` (world +X projected perpendicular to the axis, or world +Y when ``|axis . X| >= 0.999``) and a positive
    angle turns towards ``v = axis x u``. The radial direction is ``cos(a) u + sin(a) v``."""
    if face.axis is None or face.radius is None:
        msg = "radial_entry requires a cylindrical face with an axis and a radius"
        raise ValueError(msg)
    direction = canonical_axis_direction(face.axis.direction)
    along = _dot(_sub(face.center, face.axis.origin), direction)
    centre = _add(face.axis.origin, _scale(direction, along))
    frame = frame_for_normal(centre, direction)
    axial, angle_deg = position
    angle = math.radians(angle_deg)
    outward = _add(_scale(frame.u, math.cos(angle)), _scale(frame.v, math.sin(angle)))
    axis_point = _add(centre, _scale(direction, axial))
    return RadialEntry(
        axis_point=axis_point, outward=outward, surface_point=_add(axis_point, _scale(outward, face.radius))
    )


def frame_for_normal(origin: Vec3, normal: Vec3) -> SketchFrame:
    """The planar-face rule for any plane given by a point and a unit normal.

    ``u`` is world X projected onto the plane (world Y when the plane is nearly perpendicular to X) and
    ``v = n x u``. Datum planes that do not inherit a base frame (``three_points``, ``normal_to``) use it,
    so they get exactly the frame a face with the same normal would get."""
    n = _normalise(normal)
    x_dot_n = _dot(_WORLD_X, n)
    if abs(x_dot_n) < 0.999:
        u = _normalise(_sub(_WORLD_X, _scale(n, x_dot_n)))
    else:
        y_dot_n = _dot(_WORLD_Y, n)
        u = _normalise(_sub(_WORLD_Y, _scale(n, y_dot_n)))
    v = _cross(n, u)
    return SketchFrame(origin=origin, u=u, v=v, n=n)
