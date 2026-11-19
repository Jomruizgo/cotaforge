# ruff: noqa: E501
"""Flat faces of a turned part, marked with their diagonals (ISO 128-30 / ISO 129-1: no FreeCAD import).

When a view carries a centre line, the reader takes the part for a body of revolution. A flat of that part seen face-on
(a square flange on a washer, a square shaft end, wrench flats) has a rectangle for outline, which reads as a cylinder.
The drawing office rule is to draw the two diagonals of that face as thin continuous lines.

``find_flats`` reads the flats off the facts the worker measured (no geometry of its own): a planar face parallel to the
axis of a turned part, nearer to the axis than the largest round of that axis -- so it cuts into the round. That leaves out the
plates (a flat tangent to a fillet is at the radius, not inside it), the end faces of a shaft (perpendicular to the axis)
and the walls and bottom of a keyway (the rule is for the flats of the round, not for a slot cut into it). ``diagonals``
draws them in one view of the sheet; ``missing`` says which a sheet lacks (the review).
"""

import math
from dataclasses import dataclass
from typing import Any

from sheet_context import Context, ItemError, ViewInfo, dot, unit
from sheet_model import FLAT_FACES, Line

Vec3 = tuple[float, float, float]

TURNED_ARC_RAD = 1.6  # a convex cylinder that covers this much round (92 deg) of its turn is a turned surface: a fillet (90 deg) is not
INSIDE_ROUND_MM = 0.05  # the flat is nearer to the axis than the round by at least this: a flat tangent to its round is not a flat of it
PARALLEL_TOL = 0.02  # |cos| of the normal of the flat with the axis
AXIAL_SLACK_MM = 0.5
FACE_ON = 0.9995  # cos of the angle between the view direction and the normal of a flat seen face-on
CORNER_DEG = 20.0  # a turn of the outline this sharp is a corner (the arcs of a discretised round turn less per step)
MIN_SIDE_MM = 0.5  # on the page: a flat thinner than this has no diagonals to read
JOIN_TOL_MM = 0.6  # a drawn line starts and ends at the corners of the face within this


@dataclass(frozen=True)
class Turned:
    """The axis of a turned part: a point on it, its direction (unit), the largest convex round about it (``radius``) and the
    stretch of it along the axis (``lo`` to ``hi``, measured as the dot product with ``axis``); ``turned`` is true when a
    convex cylinder about it covers enough of the turn to read as round."""

    axis: Vec3
    point: Vec3
    radius: float
    lo: float
    hi: float
    turned: bool


@dataclass(frozen=True)
class Flat:
    """A planar face as the facts hand it over: the unit normal (any sense) and the centre of mass."""

    face_id: str
    normal: Vec3
    centre: Vec3


@dataclass(frozen=True)
class FlatFace:
    """A flat of a turned part: its face, the outward unit normal, the axis it lies along and its distance to it."""

    face_id: str
    normal: Vec3
    axis: Vec3
    distance: float


def find_flats(turned: list[Turned], flats: list[Flat], excluded: frozenset[str] = frozenset()) -> list[FlatFace]:
    """The flats of the round of ``turned`` axes among ``flats`` (``excluded``: faces that belong to a keyway)."""
    out: dict[str, FlatFace] = {}
    for flat in flats:
        if flat.face_id in excluded or flat.face_id in out:
            continue
        for t in turned:
            if not t.turned or abs(dot(flat.normal, t.axis)) > PARALLEL_TOL:
                continue
            offset = dot(tuple(c - p for c, p in zip(flat.centre, t.point, strict=True)), flat.normal)  # type: ignore[arg-type]
            distance = abs(offset)
            if distance < INSIDE_ROUND_MM or distance > t.radius - INSIDE_ROUND_MM:
                continue  # through the axis, or not inside the round
            along = dot(flat.centre, t.axis)
            if not t.lo - AXIAL_SLACK_MM <= along <= t.hi + AXIAL_SLACK_MM:
                continue
            sign = 1.0 if offset > 0 else -1.0  # outward: away from the axis
            normal = tuple(sign * c for c in unit(flat.normal))
            out[flat.face_id] = FlatFace(flat.face_id, normal, t.axis, distance)  # type: ignore[arg-type]
            break
    return list(out.values())


# --- the diagonals in a view ----------------------------------------------------------------------------------------------


def seen_in(ctx: Context, flat: FlatFace) -> list[ViewInfo]:
    """The standard views that see the flat face-on, from the side that has material (the normal points at the viewer): the part has
    its centre line there, because the axis lies in the plane of any view across the normal."""
    return [v for v in ctx.views.values() if not v.derived and dot(v.direction, flat.normal) >= FACE_ON]


def _corners(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = [p for i, p in enumerate(points) if i == 0 or math.dist(p, points[i - 1]) > 1e-6]
    if len(pts) > 1 and math.dist(pts[0], pts[-1]) < 1e-6:
        pts.pop()
    n = len(pts)
    out = []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        v1, v2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        turn = math.degrees(math.acos(max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))))
        if turn >= CORNER_DEG:
            out.append(b)
    return out


def diagonals(ctx: Context, view: ViewInfo, flat: FlatFace) -> tuple[list[Line], str] | None:
    """The two diagonals of ``flat`` on the page in ``view`` (thin continuous, the ``CARAS_PLANAS`` layer) and how they were taken:
    the corners of the face when its outline has four, else the box of the outline along and across the axis (a flat that ends
    in a cutter's run-out, or in the arc of a shoulder). ``None`` when the face has no outline here or is too thin to read."""
    try:
        loops = ctx.geometry.face_outline(flat.face_id, view.centre, view.xdir, view.direction)
    except ItemError:
        return None
    if not loops:
        return None
    page = [[(view.x + p[0] * view.scale, view.y + p[1] * view.scale) for p in loop] for loop in loops]
    outer = max(page, key=lambda loop: _box_area(loop))
    corners = _corners(outer)
    how = "its four corners"
    if len(corners) == 4:
        quad = corners
        # the corners come in order round the outline: the diagonals join the first to the third and the second to the fourth
    else:
        ax, ay = view.axis(unit(flat.axis))
        size = math.hypot(ax, ay)
        if size < 1e-9:
            return None
        u, v = (ax / size, ay / size), (-ay / size, ax / size)
        lo_u, hi_u = min(p[0] * u[0] + p[1] * u[1] for p in outer), max(p[0] * u[0] + p[1] * u[1] for p in outer)
        lo_v, hi_v = min(p[0] * v[0] + p[1] * v[1] for p in outer), max(p[0] * v[0] + p[1] * v[1] for p in outer)
        quad = [
            (a * u[0] + b * v[0], a * u[1] + b * v[1])
            for a, b in ((lo_u, lo_v), (hi_u, lo_v), (hi_u, hi_v), (lo_u, hi_v))
        ]
        how = f"the box of its outline along and across the axis ({len(corners)} corners or curved ends)"
    if min(math.dist(quad[i], quad[(i + 1) % 4]) for i in range(4)) < MIN_SIDE_MM:
        return None
    return [Line(*quad[0], *quad[2], FLAT_FACES), Line(*quad[1], *quad[3], FLAT_FACES)], how


def _box_area(loop: list[tuple[float, float]]) -> float:
    xs, ys = [p[0] for p in loop], [p[1] for p in loop]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def flats_of(ctx: Context) -> list[FlatFace]:
    facts = ctx.extras.get("facts")
    return list(getattr(facts, "flats", ()) or ()) if facts is not None and not facts.assembly else []


def _joined(sheet_lines: list[Line], wanted: Line) -> bool:
    for line in sheet_lines:
        for a, b in (((line.x1, line.y1), (line.x2, line.y2)), ((line.x2, line.y2), (line.x1, line.y1))):
            if (
                math.dist(a, (wanted.x1, wanted.y1)) <= JOIN_TOL_MM
                and math.dist(b, (wanted.x2, wanted.y2)) <= JOIN_TOL_MM
            ):
                return True
    return False


def missing(ctx: Context, entities: list[Any]) -> list[tuple[ViewInfo, FlatFace]]:
    """The (view, flat) pairs that ``entities`` (what the sheet draws) lacks a diagonal of."""
    drawn = [e for e in entities if isinstance(e, Line) and e.layer == FLAT_FACES]
    out: list[tuple[ViewInfo, FlatFace]] = []
    for flat in flats_of(ctx):
        for view in seen_in(ctx, flat):
            got = diagonals(ctx, view, flat)
            if got is not None and not all(_joined(drawn, d) for d in got[0]):
                out.append((view, flat))
    return out
