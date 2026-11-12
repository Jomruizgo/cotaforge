# ruff: noqa: E501
"""Keyways read from the shape of the part (no FreeCAD import: plain arithmetic on what ``sheet_facts`` hands over).

A keyway is a slot with two parallel flat walls cut along the axis of a cylindrical face: in a shaft (the walls reach the
cylinder from outside, the slot is cut into the convex face) or in a hub (the walls run out from the bore). What tells it from
any other pair of parallel faces is the cylinder: the walls are parallel to its axis, one on each side of it at the same
distance, the plane half way between them contains the axis, the width between them is a fraction of the diameter and they
lie along the cylinder's own length.
"""

import math
from dataclasses import dataclass

Vec3 = tuple[float, float, float]

PARALLEL = 0.02  # |cos| of a wall's normal with the axis: it is parallel to the axis within this
SAME_PLANE = 0.98  # |cos| of the normals of the two walls: they are parallel
MIN_WIDTH_MM = 1.0
MAX_WIDTH_RATIO = 1.2  # the width between the walls, over the radius of the cylinder
SYMMETRY_MM = 0.5  # the slot is centred on the axis within this, plus 15 % of its width (a sketch is placed by hand)
AXIAL_SLACK_MM = 0.5
FACES_LIMIT = 4000  # a part with more faces is not read: the pairs would cost more than the fact is worth


@dataclass(frozen=True)
class Cylinder:
    """A cylindrical face (or the faces of one cylinder): its axis (unit), a point on it, the radius, whether the material is
    outside (a bore) and where it lies along the axis (``lo`` to ``hi``, measured as the dot product with the axis)."""

    axis: Vec3
    point: Vec3
    radius: float
    concave: bool
    lo: float
    hi: float


@dataclass(frozen=True)
class Flat:
    """A planar face: the unit normal and the centre of mass."""

    normal: Vec3
    centre: Vec3


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _radial(c: Cylinder, p: Vec3) -> float:
    d = _sub(p, c.point)
    along = _dot(d, c.axis)
    return math.sqrt(max(0.0, _dot(d, d) - along * along))


def _walls(c: Cylinder, a: Flat, b: Flat) -> bool:
    if abs(_dot(a.normal, c.axis)) > PARALLEL or abs(_dot(b.normal, c.axis)) > PARALLEL:
        return False  # not parallel to the axis
    if abs(_dot(a.normal, b.normal)) < SAME_PLANE:
        return False  # not parallel to each other
    side_a, side_b = _dot(_sub(a.centre, c.point), a.normal), _dot(_sub(b.centre, c.point), a.normal)
    if side_a * side_b >= 0:
        return False  # both on one side of the axis: a flat, a step, not a slot around the axis
    width = abs(side_a - side_b)
    if not MIN_WIDTH_MM <= width <= MAX_WIDTH_RATIO * c.radius:
        return False
    if abs(side_a + side_b) > SYMMETRY_MM + 0.15 * width:
        return False  # the plane half way between the walls does not hold the axis
    lateral = _cross(c.axis, a.normal)
    if abs(_dot(_sub(a.centre, b.centre), lateral)) > 1.0 + 0.1 * width:
        return False  # the walls are not face to face: they belong to two slots
    for wall in (a, b):
        reach = _radial(c, wall.centre)
        inside = reach <= c.radius * 1.02 + 0.2 if not c.concave else c.radius * 0.98 - 0.2 <= reach <= c.radius * 2.5 + 1.0
        along = _dot(wall.centre, c.axis)
        if not inside or not c.lo - AXIAL_SLACK_MM <= along <= c.hi + AXIAL_SLACK_MM:
            return False
    return True


def count_keyways(cylinders: list[Cylinder], flats: list[Flat]) -> int:
    """How many keyways the part has: the pairs of walls that one cylinder holds as described above, each counted once even
    when a shaft and the bore of a hub on it both hold it."""
    if len(flats) < 2 or len(flats) > FACES_LIMIT:
        return 0
    found: set[frozenset[int]] = set()
    for cylinder in cylinders:
        for i, a in enumerate(flats):
            if abs(_dot(a.normal, cylinder.axis)) > PARALLEL:
                continue
            for j in range(i + 1, len(flats)):
                if _walls(cylinder, a, flats[j]):
                    found.add(frozenset((i, j)))
    return len(found)
