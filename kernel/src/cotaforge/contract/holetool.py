"""The solid a hole removes, as a half section (contract/holetool.py).

A drilled hole is a solid of revolution about its axis: the cylinder of the diameter, plus the counterbore or the
countersink at the entry and the drill point at the bottom. ``hole_outline`` gives that half section as a closed polygon
of ``(t, r)`` points, ``t`` the distance from the entry plane along the drill direction and ``r`` the distance from the
axis. Both the verification (the volume the hole must remove) and the adapter (the tool it intersects with the part to
measure it) read the same polygon, so they describe one hole.

The depth rules are FreeCAD's (``PartDesign::Hole``, measured on 1.1.3): a blind hole's ``Depth`` runs from the entry
plane to the bottom of the cylinder and INCLUDES the counterbore; an angled drill point adds its cone beyond ``Depth``;
a countersink's diameter is the one at the entry plane and its angle is the included angle of the cone.
"""

import math

Point = tuple[float, float]

FIT_PATTERN = r"^[A-Za-z]{1,2}[0-9]{1,2}(/[A-Za-z]{1,2}[0-9]{1,2})?$"
"""A tolerance class (H7) or a fit (H7/g6) as a hole declares it."""
DEFAULT_COUNTERSINK_ANGLE_DEG = 90.0
"""FreeCAD's ``HoleCutCountersinkAngle`` default."""
DEFAULT_DRILL_POINT_ANGLE_DEG = 118.0
"""FreeCAD's ``DrillPointAngle`` default."""


def countersink_depth(hole_diameter: float, diameter: float, angle_deg: float) -> float:
    """Depth of the cone of a countersink: from ``diameter`` at the entry plane down to the hole's own diameter."""
    return (diameter - hole_diameter) / 2.0 / math.tan(math.radians(angle_deg) / 2.0)


def drill_point_length(hole_diameter: float, angle_deg: float) -> float:
    """Length of the cone at the bottom of a blind hole drilled with a point of ``angle_deg`` included angle."""
    return hole_diameter / 2.0 / math.tan(math.radians(angle_deg) / 2.0)


def hole_outline(
    diameter: float,
    length: float,
    *,
    counterbore: tuple[float, float] | None = None,
    countersink: tuple[float, float] | None = None,
    point_angle_deg: float | None = None,
) -> tuple[Point, ...]:
    """The closed half section of the hole in ``(t, r)``: ``length`` is the depth of the cylinder from the entry plane
    (counterbore included); ``counterbore`` is ``(diameter, depth)``, ``countersink`` is ``(diameter, angle_deg)`` and
    ``point_angle_deg`` adds a drill-point cone at the bottom. The polygon starts and ends on the axis."""
    r = diameter / 2.0
    points: list[Point] = [(0.0, 0.0)]
    if counterbore is not None:
        big = counterbore[0] / 2.0
        points += [(0.0, big), (counterbore[1], big), (counterbore[1], r)]
    elif countersink is not None:
        big = countersink[0] / 2.0
        points += [(0.0, big), (countersink_depth(diameter, countersink[0], countersink[1]), r)]
    else:
        points.append((0.0, r))
    points.append((length, r))
    if point_angle_deg is not None:
        points.append((length + drill_point_length(diameter, point_angle_deg), 0.0))
    else:
        points.append((length, 0.0))
    return tuple(points)


def outline_volume(outline: tuple[Point, ...] | list[Point]) -> float:
    """Volume of the solid of revolution of a closed half section (Pappus, edge by edge: a frustum per edge)."""
    total = 0.0
    for i, (t0, r0) in enumerate(outline):
        t1, r1 = outline[(i + 1) % len(outline)]
        total += (t1 - t0) * (r0 * r0 + r0 * r1 + r1 * r1) / 3.0
    return abs(math.pi * total)
