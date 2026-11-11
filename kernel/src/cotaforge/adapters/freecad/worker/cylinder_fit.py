"""Recognise a cylinder in a face whose surface is not an analytic ``Part.Cylinder``.

Some exporters write cylinders as B-spline surfaces, and geometry moved with ``transformGeometry`` becomes B-spline too.
FreeCAD does not expose OCC's surface-to-analytic conversion to Python (a B-spline surface offers no ``isCylinder`` or
analytic conversion), so the face is sampled on a parameter grid and a cylinder is fitted to it: the axis is the
direction every sampled normal is perpendicular to (the smallest eigenvector of the normals' second-moment matrix),
the radius and axis point come from a least squares circle through the points projected on the plane normal to it. The
face is a cylinder when no sample is farther than ``FIT_TOL`` from the fitted surface.
"""

import math
from dataclasses import dataclass
from typing import Any

import geometry as g

FIT_TOL = 0.01  # mm: largest radial distance of a sampled point from the fitted cylinder
GRID = 11  # samples per parameter direction
NORMAL_SPREAD = 0.1  # the normals must spread over a plane (a flat or slightly bent face has one dominant direction)
PERPENDICULAR = 1e-3  # share of the normals' energy allowed along the axis
FIRST_COMPONENT = 1e-3  # a direction component below this is noise when the sense of an either-sense axis is chosen


@dataclass(frozen=True)
class CylinderFit:
    direction: g.Vec3  # canonical unit axis
    anchor: g.Vec3  # point of the axis closest to the origin
    radius: float
    t0: float  # axial span along ``direction``
    t1: float
    arc: float  # radians of the circle the face covers
    concave: bool  # the normal points toward the axis (material outside the cylinder)
    deviation: float  # mm


def _smallest_axis(matrix: list[list[float]]) -> tuple[g.Vec3, float, float]:
    """Jacobi eigen-decomposition of a symmetric 3 x 3: (eigenvector of the smallest eigenvalue, smallest, middle)."""
    a = [row[:] for row in matrix]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(30):
        p, q = max(((0, 1), (0, 2), (1, 2)), key=lambda ij: abs(a[ij[0]][ij[1]]))
        if abs(a[p][q]) < 1e-14:
            break
        theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
        t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
        c = 1.0 / math.sqrt(t * t + 1.0)
        s = t * c
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p], v[k][q] = c * vkp - s * vkq, s * vkp + c * vkq
    order = sorted(range(3), key=lambda i: a[i][i])
    low = order[0]
    return (v[0][low], v[1][low], v[2][low]), a[low][low], a[order[1]][order[1]]


def _solve3(m: list[list[float]], rhs: list[float]) -> list[float] | None:
    rows = [m[i][:] + [rhs[i]] for i in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(3):
            if r != col:
                f = rows[r][col] / rows[col][col]
                rows[r] = [x - f * y for x, y in zip(rows[r], rows[col], strict=True)]
    return [rows[i][3] / rows[i][i] for i in range(3)]


def _sweep(angles: list[float]) -> float:
    """Total angle swept walking along ``angles`` (each step taken the short way round)."""
    total = 0.0
    for before, after in zip(angles, angles[1:], strict=False):
        total += (after - before + math.pi) % (2.0 * math.pi) - math.pi
    return abs(total)


FLAT_DOT = 0.999  # normals within about 2.6 degrees of each other: nothing for a cylinder to bend through


def _looks_flat(face: Any, u0: float, u1: float, v0: float, v1: float) -> bool:
    """Is the face flat (the normals at 9 points all point the same way)? A full fit samples 121 points, and a model of
    thousands of B-spline faces is mostly planes: the fit would reject them for their ``NORMAL_SPREAD`` anyway."""
    first = None
    for i in range(3):
        for j in range(3):
            n = face.normalAt(u0 + (u1 - u0) * i / 2.0, v0 + (v1 - v0) * j / 2.0)
            unit = (n.x, n.y, n.z)
            if first is None:
                first = unit
            elif g.dot(first, unit) < FLAT_DOT:
                return False
    return True


def fit_cylinder(face: Any) -> CylinderFit | None:
    """The cylinder a face lies on within ``FIT_TOL``, or ``None`` when it is not one."""
    u0, u1, v0, v1 = face.ParameterRange
    if _looks_flat(face, u0, u1, v0, v1):
        return None
    points: list[g.Vec3] = []
    normals: list[g.Vec3] = []
    for i in range(GRID):
        u = u0 + (u1 - u0) * i / (GRID - 1)
        for j in range(GRID):
            v = v0 + (v1 - v0) * j / (GRID - 1)
            p, n = face.valueAt(u, v), face.normalAt(u, v)
            points.append((p.x, p.y, p.z))
            normals.append((n.x, n.y, n.z))
    count = float(len(normals))
    moments = [[sum(n[r] * n[c] for n in normals) / count for c in range(3)] for r in range(3)]
    axis, along, spread = _smallest_axis(moments)
    if along > PERPENDICULAR or spread < NORMAL_SPREAD:
        return None
    axis = g.unit(axis)
    direction = g.canonical_direction(axis, FIRST_COMPONENT)
    # circle in the plane normal to the axis, coordinates relative to the first sample (conditioning)
    e1 = g.unit(g.cross(direction, (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)))
    e2 = g.cross(direction, e1)
    base = points[0]
    xy = []
    for p in points:
        r = g.sub(p, base)
        xy.append((g.dot(r, e1), g.dot(r, e2)))
    sums = [[0.0] * 3 for _ in range(3)]
    rhs = [0.0] * 3
    for x, y in xy:
        row = (2.0 * x, 2.0 * y, 1.0)
        z = x * x + y * y
        for r in range(3):
            rhs[r] += row[r] * z
            for c in range(3):
                sums[r][c] += row[r] * row[c]
    solution = _solve3(sums, rhs)
    if solution is None:
        return None
    a, b, c = solution
    radius_sq = c + a * a + b * b
    if radius_sq <= 0.0:
        return None
    radius = math.sqrt(radius_sq)
    deviation = max(abs(math.hypot(x - a, y - b) - radius) for x, y in xy)
    if deviation > FIT_TOL:
        return None
    centre = g.add(base, g.add(g.scale(e1, a), g.scale(e2, b)))
    anchor = g.sub(centre, g.scale(direction, g.dot(centre, direction)))
    angles = [math.atan2(y - b, x - a) for x, y in xy]
    sweep = max(_sweep(angles[GRID * (GRID // 2) : GRID * (GRID // 2 + 1)]), _sweep(angles[GRID // 2 :: GRID]))
    axial = [g.dot(p, direction) for p in points]
    return CylinderFit(
        direction=direction,
        anchor=anchor,
        radius=radius,
        t0=min(axial),
        t1=max(axial),
        arc=min(sweep, 2.0 * math.pi),
        concave=_concave(normals, xy, a, b, e1, e2),
        deviation=deviation,
    )


def _concave(normals: list[g.Vec3], xy: list[tuple[float, float]], a: float, b: float, e1: g.Vec3, e2: g.Vec3) -> bool:
    """Whether the sampled normals point toward the axis (the radial part of a normal is taken on ``e1``, ``e2``)."""
    pull = 0.0
    for n, (x, y) in zip(normals, xy, strict=True):
        pull += g.dot(n, e1) * (x - a) + g.dot(n, e2) * (y - b)
    return pull < 0.0
