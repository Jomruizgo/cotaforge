# ruff: noqa: E501
"""Plane geometry for the derived views of a sheet (no FreeCAD import): moving entities from a view's own coordinates to
the page, clipping them to a circle (a detail) or a rectangle (a partial view), and hatching a cut face.

Everything is in millimetres and y up, like ``sheet_model``. A curve that has to be cut by the clip region is turned
into a polyline whose sag stays under ``SAG_MM``; a curve that lies wholly inside is kept as it is (a circle stays a
circle in the DXF).
"""

import math
from collections.abc import Callable
from dataclasses import replace

from sheet_model import HATCH, Arc, Circle, Entity, Line, Point, Polyline

SAG_MM = 0.02  # chord deviation of a curve written as a polyline
HATCH_ANGLE_DEG = 45.0  # ISO 128-50: parallel thin lines at 45 degrees
MIN_HATCH_MM = 1.5  # ISO 128-50 asks 1 to 10 mm; it grows with the area
MAX_HATCH_MM = 4.0
HATCH_FRACTION = 0.04  # of the longer side of the hatched area
EPS = 1e-9

Rect = tuple[float, float, float, float]


def hatch_spacing(width: float, height: float) -> float:
    """The distance between hatch lines for a cut area ``width`` x ``height`` mm on the page."""
    return min(MAX_HATCH_MM, max(MIN_HATCH_MM, HATCH_FRACTION * max(width, height)))


# --- moving entities ---------------------------------------------------------------------------------------------------


def place(entities: list[Entity], ox: float, oy: float, k: float) -> list[Entity]:
    """``entities`` (a view's own coordinates, scale 1) at the page: ``(x, y) -> (ox + k x, oy + k y)``. Only the
    geometric kinds a view holds (line, polyline, arc, circle) are handled."""
    out: list[Entity] = []
    for e in entities:
        if isinstance(e, Line):
            out.append(Line(ox + k * e.x1, oy + k * e.y1, ox + k * e.x2, oy + k * e.y2, e.layer, e.width))
        elif isinstance(e, Polyline):
            out.append(Polyline(tuple((ox + k * x, oy + k * y) for x, y in e.points), e.layer, e.closed, e.width))
        elif isinstance(e, Arc):
            out.append(Arc(ox + k * e.cx, oy + k * e.cy, k * e.r, e.start, e.end, e.layer, e.width))
        elif isinstance(e, Circle):
            out.append(Circle(ox + k * e.cx, oy + k * e.cy, k * e.r, e.layer, e.width))
        else:
            out.append(e)
    return out


def relayer(entities: list[Entity], layer: str) -> list[Entity]:
    """The same entities on another layer."""
    out: list[Entity] = []
    for e in entities:
        if isinstance(e, Line | Polyline | Arc | Circle):
            out.append(replace(e, layer=layer))
        else:
            out.append(e)
    return out


def arc_points(cx: float, cy: float, r: float, start: float, end: float, sag: float = SAG_MM) -> list[Point]:
    """Points along the arc from ``start`` to ``end`` degrees (counter-clockwise), a chord at most ``sag`` off it."""
    sweep = (end - start) % 360.0
    if sweep == 0.0:
        sweep = 360.0
    step = 2.0 * math.acos(max(0.0, 1.0 - sag / r)) if r > sag else math.pi / 2
    count = max(2, math.ceil(math.radians(sweep) / max(step, 1e-3)))
    return [
        (
            cx + r * math.cos(math.radians(start + sweep * i / count)),
            cy + r * math.sin(math.radians(start + sweep * i / count)),
        )
        for i in range(count + 1)
    ]


def _polyline_of(e: Entity) -> tuple[list[Point], bool] | None:
    """The points of a geometric entity and whether they close on themselves."""
    if isinstance(e, Line):
        return [(e.x1, e.y1), (e.x2, e.y2)], False
    if isinstance(e, Polyline):
        points = list(e.points)
        return (points + [points[0]] if e.closed and points else points), False
    if isinstance(e, Arc):
        return arc_points(e.cx, e.cy, e.r, e.start, e.end), False
    if isinstance(e, Circle):
        return arc_points(e.cx, e.cy, e.r, 0.0, 360.0), True
    return None


def bbox(entities: list[Entity]) -> Rect | None:
    """The box of the geometric entities (arcs and circles by their points), or ``None`` when there are none."""
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        got = _polyline_of(e)
        if got is not None:
            xs += [p[0] for p in got[0]]
            ys += [p[1] for p in got[0]]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


# --- clipping ----------------------------------------------------------------------------------------------------------


def _clip_segment_circle(a: Point, b: Point, cx: float, cy: float, r: float) -> tuple[float, float] | None:
    """The parameter range ``[t0, t1]`` of the segment ``a -> b`` inside the circle, or ``None``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    fx, fy = a[0] - cx, a[1] - cy
    qa = dx * dx + dy * dy
    if qa < EPS:
        return (0.0, 1.0) if fx * fx + fy * fy <= r * r else None
    qb = 2.0 * (fx * dx + fy * dy)
    qc = fx * fx + fy * fy - r * r
    disc = qb * qb - 4.0 * qa * qc
    if disc < 0.0:
        return None
    root = math.sqrt(disc)
    t0, t1 = (-qb - root) / (2.0 * qa), (-qb + root) / (2.0 * qa)
    lo, hi = max(0.0, t0), min(1.0, t1)
    return (lo, hi) if lo <= hi else None


def _clip_segment_rect(a: Point, b: Point, rect: Rect) -> tuple[float, float] | None:
    """Liang-Barsky."""
    x0, y0, x1, y1 = rect
    dx, dy = b[0] - a[0], b[1] - a[1]
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < EPS:
            if q < 0.0:
                return None
            continue
        t = q / p
        if p < 0.0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo > hi:
            return None
    return lo, hi


def _clip_points(points: list[Point], clip: Callable[[Point, Point], tuple[float, float] | None]) -> list[list[Point]]:
    """The pieces of the polyline ``points`` that ``clip(a, b)`` keeps (as parameter ranges), joined when consecutive."""
    pieces: list[list[Point]] = []
    current: list[Point] = []
    for a, b in zip(points, points[1:], strict=False):
        span = clip(a, b)
        if span is None:
            if len(current) > 1:
                pieces.append(current)
            current = []
            continue
        t0, t1 = span
        p0 = (a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0)
        p1 = (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)
        if current and math.dist(current[-1], p0) > 1e-6:
            if len(current) > 1:
                pieces.append(current)
            current = []
        if not current:
            current = [p0]
        if math.dist(current[-1], p1) > 1e-9:
            current.append(p1)
    if len(current) > 1:
        pieces.append(current)
    return pieces


def _as_entities(pieces: list[list[Point]], like: Entity) -> list[Entity]:
    layer = like.layer  # type: ignore[union-attr]
    width = getattr(like, "width", None)
    out: list[Entity] = []
    for piece in pieces:
        if len(piece) == 2:
            out.append(Line(piece[0][0], piece[0][1], piece[1][0], piece[1][1], layer, width))
        else:
            out.append(Polyline(tuple(piece), layer, False, width))
    return out


def _clip(
    entities: list[Entity],
    inside: Callable[[Entity], bool],
    clip: Callable[[Point, Point], tuple[float, float] | None],
) -> list[Entity]:
    out: list[Entity] = []
    for e in entities:
        if isinstance(e, Circle | Arc):
            if inside(e):
                out.append(e)
                continue
        got = _polyline_of(e)
        if got is None:
            out.append(e)
            continue
        out += _as_entities(_clip_points(got[0], clip), e)
    return out


def clip_to_circle(entities: list[Entity], cx: float, cy: float, r: float) -> list[Entity]:
    """The parts of ``entities`` inside the circle ``(cx, cy, r)``; a curve wholly inside is kept as it is."""

    def inside(e: Entity) -> bool:
        if isinstance(e, Circle):
            return math.hypot(e.cx - cx, e.cy - cy) + e.r <= r
        assert isinstance(e, Arc)
        return all(math.hypot(x - cx, y - cy) <= r for x, y in arc_points(e.cx, e.cy, e.r, e.start, e.end))

    return _clip(entities, inside, lambda a, b: _clip_segment_circle(a, b, cx, cy, r))


def clip_to_rect(entities: list[Entity], rect: Rect) -> list[Entity]:
    """The parts of ``entities`` inside ``rect``; a curve wholly inside is kept as it is."""
    x0, y0, x1, y1 = rect

    def inside(e: Entity) -> bool:
        if isinstance(e, Circle):
            return x0 <= e.cx - e.r and e.cx + e.r <= x1 and y0 <= e.cy - e.r and e.cy + e.r <= y1
        assert isinstance(e, Arc)
        return all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in arc_points(e.cx, e.cy, e.r, e.start, e.end))

    return _clip(entities, inside, lambda a, b: _clip_segment_rect(a, b, rect))


# --- hatching ------------------------------------------------------------------------------------------------------------


def hatch(
    loops: list[list[Point]], spacing: float, angle_deg: float = HATCH_ANGLE_DEG, layer: str = HATCH
) -> list[Line]:
    """Parallel lines ``spacing`` mm apart at ``angle_deg`` filling the area the closed ``loops`` bound (even-odd: a loop
    inside another is a hole). The lines sit on a grid fixed to the page, so the hatch of two cut areas lines up."""
    if not loops or spacing <= 0.0:
        return []
    ux, uy = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    vx, vy = -uy, ux  # across the lines
    offsets = [x * vx + y * vy for loop in loops for x, y in loop]
    first = math.ceil((min(offsets) + EPS) / spacing)
    last = math.floor((max(offsets) - EPS) / spacing)
    lines: list[Line] = []
    for n in range(first, last + 1):
        s = n * spacing + 1e-7  # off the grid by a hair so a vertex never sits exactly on a line
        along: list[float] = []
        for loop in loops:
            count = len(loop)
            for i in range(count):
                a, b = loop[i], loop[(i + 1) % count]
                sa, sb = a[0] * vx + a[1] * vy, b[0] * vx + b[1] * vy
                if (sa <= s < sb) or (sb <= s < sa):
                    f = (s - sa) / (sb - sa)
                    px, py = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                    along.append(px * ux + py * uy)
        along.sort()
        for t0, t1 in zip(along[0::2], along[1::2], strict=False):
            if t1 - t0 < 1e-6:
                continue
            base = s - 1e-7
            lines.append(
                Line(t0 * ux + base * vx, t0 * uy + base * vy, t1 * ux + base * vx, t1 * uy + base * vy, layer)
            )
    return lines
