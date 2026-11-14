# ruff: noqa: E501
"""The centre lines of holes and cylinders seen from the side (no FreeCAD import).

A centre line goes a short, fixed distance (``CENTRE_OVERSHOOT_MM``, 2 to 3 mm at sheet scale, ISO 128-24) past the feature it
marks, the SAME distance at both ends: past both faces of a through hole, past the entry face AND past the drill point of a
blind one. It never reaches a dimension line or a dimension text: the sheet is drawn with the full overshoot, and once every
item is drawn ``trim`` shortens a line whose way out is closed (the same amount at both ends, so it stays centred on the
feature). The review (``crossings``) names what is still crossed.

``side_line`` makes a line and remembers the feature it marks (its two ends, page mm); ``trim`` and ``crossings`` read those.
"""

import math
from dataclasses import dataclass
from typing import Any

from sheet_model import AXES, CENTRE_CLEAR_MM, CENTRE_OVERSHOOT_MM, Line

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
Segment = tuple[float, float, float, float]

OVERSHOOT_TOL_MM = 0.05  # a line this near the set overshoot has it
KEY = "centre_lines"


@dataclass
class CentreLine:
    """A centre line and the feature it marks: ``a`` and ``b`` are the ends of the feature on the page, ``line`` what is drawn
    (``a`` and ``b`` plus ``over`` at each end). ``fixed``: the engineer asked for its length (``extend_mm``), so it is not
    trimmed; ``reason`` says why a long one is long."""

    line: Line
    a: Point
    b: Point
    view: str
    item: str
    fixed: bool = False
    reason: str = ""


def side_line(ctx: Any, view: str, a: Point, b: Point, over: float = CENTRE_OVERSHOOT_MM, *, fixed: bool = False, reason: str = "") -> Line | None:
    """The chain line of a feature that runs from ``a`` to ``b`` on the page, ``over`` mm past each end; ``None`` when the
    feature is a point there. The line is remembered for ``trim`` and the review."""
    length = math.dist(a, b)
    if length < 1e-6:
        return None
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    line = Line(a[0] - ux * over, a[1] - uy * over, b[0] + ux * over, b[1] + uy * over, AXES)
    ctx.extras.setdefault(KEY, []).append(CentreLine(line, a, b, view, str(ctx.extras.get("current") or ""), fixed, reason))
    return line


def overshoots(rec: CentreLine) -> tuple[float, float]:
    """How far the line goes past the feature at its ``a`` end and at its ``b`` end."""
    length = math.dist(rec.a, rec.b)
    ux, uy = (rec.b[0] - rec.a[0]) / length, (rec.b[1] - rec.a[1]) / length
    line = rec.line
    first = (rec.a[0] - line.x1) * ux + (rec.a[1] - line.y1) * uy
    last = (line.x2 - rec.b[0]) * ux + (line.y2 - rec.b[1]) * uy
    return first, last


def _obstacles(ctx: Any) -> tuple[list[Rect], list[Segment]]:
    """What a centre line stops short of: the rectangles of the dimension texts and strips and of every callout text (the
    room a label took), and the dimension lines (the ones with arrows)."""
    rects: list[Rect] = [*ctx.occupied, *ctx.extras.get("texts", [])]
    return rects, list(ctx.extras.get("dimension_main_lines", []))


def _enters(rect: Rect, p: Point, u: Point, reach: float) -> float | None:
    """The distance along the ray from ``p`` in direction ``u`` at which it enters ``rect`` (0 when it starts inside), within
    ``reach``; ``None`` when it does not."""
    near, far = 0.0, reach
    for lo, hi, o, d in ((rect[0], rect[2], p[0], u[0]), (rect[1], rect[3], p[1], u[1])):
        if abs(d) < 1e-12:
            if not lo <= o <= hi:
                return None
            continue
        t0, t1 = (lo - o) / d, (hi - o) / d
        near, far = max(near, min(t0, t1)), min(far, max(t0, t1))
        if near > far:
            return None
    return near


def _crosses(seg: Segment, p: Point, u: Point, reach: float) -> float | None:
    """The distance along the ray from ``p`` in direction ``u`` at which it crosses the segment ``seg``, within ``reach``."""
    sx, sy = seg[2] - seg[0], seg[3] - seg[1]
    denom = u[0] * sy - u[1] * sx
    if abs(denom) < 1e-12:
        return None
    qx, qy = seg[0] - p[0], seg[1] - p[1]
    t = (qx * sy - qy * sx) / denom
    s = (qx * u[1] - qy * u[0]) / denom
    return t if 0.0 <= t <= reach and 0.0 <= s <= 1.0 else None


def _room(end: Point, u: Point, reach: float, rects: list[Rect], segments: list[Segment], clear: float) -> float:
    """How far a centre line may go from the end of its feature along ``u`` before it is within ``clear`` of an obstacle."""
    room = reach
    for r in rects:
        grown = (r[0] - clear, r[1] - clear, r[2] + clear, r[3] + clear)
        hit = _enters(grown, end, u, reach)
        if hit is not None:
            room = min(room, hit)
    for s in segments:
        hit = _crosses(s, end, u, reach + clear)
        if hit is not None:
            room = min(room, max(0.0, hit - clear))
    return max(0.0, room)


def trim(ctx: Any) -> None:
    """Shorten the centre lines whose way out is closed by a dimension or a text, the same amount at both ends. Called once the
    items of the sheet are drawn."""
    found: list[CentreLine] = ctx.extras.get(KEY, [])
    if not found:
        return
    rects, segments = _obstacles(ctx)
    if not rects and not segments:
        return
    where = {id(e): i for i, e in enumerate(ctx.sheet.entities) if isinstance(e, Line) and e.layer == AXES}
    for rec in found:
        index = where.get(id(rec.line))
        if rec.fixed or index is None:
            continue
        length = math.dist(rec.a, rec.b)
        ux, uy = (rec.b[0] - rec.a[0]) / length, (rec.b[1] - rec.a[1]) / length
        first, last = overshoots(rec)
        reach = max(first, last)
        room_a = _room(rec.a, (-ux, -uy), reach, rects, segments, CENTRE_CLEAR_MM)
        room_b = _room(rec.b, (ux, uy), reach, rects, segments, CENTRE_CLEAR_MM)
        over = min(first, last, room_a, room_b)
        if over >= min(first, last) - 1e-9:
            continue
        over = over if over > OVERSHOOT_TOL_MM else 0.0
        line = Line(rec.a[0] - ux * over, rec.a[1] - uy * over, rec.b[0] + ux * over, rec.b[1] + uy * over, AXES)
        ctx.sheet.entities[index] = line
        rec.line = line


def crossings(ctx: Any) -> list[tuple[CentreLine, str]]:
    """The centre lines of the sheet that cross a dimension line or a text, and the ones longer than the set overshoot without
    a reason: ``(line, why)`` with ``why`` ``crosses`` or ``long``."""
    rects, segments = _obstacles(ctx)
    out: list[tuple[CentreLine, str]] = []
    drawn = {id(e) for e in ctx.sheet.entities if isinstance(e, Line) and e.layer == AXES}  # an item that failed left none
    for rec in ctx.extras.get(KEY, []):
        if id(rec.line) not in drawn:
            continue
        length = math.dist(rec.a, rec.b)
        ux, uy = (rec.b[0] - rec.a[0]) / length, (rec.b[1] - rec.a[1]) / length
        first, last = overshoots(rec)
        crossed = False
        for end, direction, reach in ((rec.a, (-ux, -uy), first), (rec.b, (ux, uy), last)):  # only what is past the feature counts
            if reach <= OVERSHOOT_TOL_MM:
                continue
            crossed = crossed or any(_enters(r, end, direction, reach) is not None for r in rects)
            crossed = crossed or any(_crosses(s, end, direction, reach) is not None for s in segments)
        if crossed:
            out.append((rec, "crosses"))
        elif max(first, last) > CENTRE_OVERSHOOT_MM + OVERSHOOT_TOL_MM and not rec.reason:
            out.append((rec, "long"))
    return out
