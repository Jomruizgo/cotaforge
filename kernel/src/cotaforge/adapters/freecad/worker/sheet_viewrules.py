# ruff: noqa: E501
"""Drawing rules a shop reader expects, as checks on plain sheet data (no FreeCAD import, adapter-neutral: the
FreeCAD review and the external adapter's review both build these facts and call the functions below).

* ``dimension_side``: a dimension sits on the side of the feature it measures, the side where its extension lines cross the
  least of the part. A spigot at the bottom is dimensioned below; the extension lines do not run across the whole part when
  the other side is free.
* ``dimension_crossings``: no extension line of a dimension crosses the dimension line of another one (ISO 129).
* ``datum_on_line``: the triangle and the frame of a datum do not lie on a line of another item.
* ``datum_unused``: a datum item whose letter no tolerance frame (``gdt`` item) cites.
* ``chamfer_leader_problems``: the leader of a chamfer callout ends on the chamfer's own line, comes from outside the part
  and is close to perpendicular to that line.
* ``redundant_views``: a view that only carries a cutting line, or whose dimensions another view repeats, goes when another
  view that stays can carry the line. On a turned part the cutting line goes on the circular view and the section carries
  the diameters and lengths.
* ``short_dimensions``: on a short dimension whose text and two arrows do not fit between the extension lines, the arrows
  go outside.
* ``sheet_underused``: the views and their dimensions cover too little of the free drawing area while a larger scale or a
  smaller paper would hold them, or they sit in a corner with most of the room on one side.

Everything is in sheet millimetres, y up. The functions return plain findings; the adapters turn them into review rows.
"""

import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field

Segment = tuple[float, float, float, float]
Rect = tuple[float, float, float, float]

ARROW_MM = 3.0  # the length of an arrowhead (ISO 129: about as long as the dimension text is high)
GAP_MM = 1.0  # an extension line starts this far from the feature
SAME_MM = 0.05  # two crossings this close are one point (a vertex where two outline segments meet)
START_MM = 0.3  # a crossing this close to the feature is the feature's own edge
AXIS_TOL = 0.05  # an extension line this far from vertical / horizontal over its length is not axis-aligned
ARROW_ARROWS = 2  # arrows per dimension line


# --- rule 2: the side of a dimension ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class DimShape:
    """One linear dimension as drawn: its extension lines, where its dimension line is, and what the arrows do.

    ``extension``: the extension lines; ``line_at``: the coordinate of the dimension line across them (y for vertical
    extension lines, x for horizontal ones); ``span``: the distance between the two extension lines; ``text_width``: the width of
    its text; ``arrows_inside``: the arrowheads stand between the extension lines (``None``: not known); ``pinned``: the
    engineer or the agent fixed the arrow side (a check does not argue with it)."""

    owner: str
    view: str
    extension: tuple[Segment, ...] = ()
    line_at: float | None = None
    span: float | None = None
    text_width: float = 0.0
    arrows_inside: bool | None = None
    pinned: bool = False
    arrow_length: float = ARROW_MM


@dataclass(frozen=True)
class SideFinding:
    owner: str
    view: str
    now: str  # above | below | left | right
    better: str
    crossings_now: int
    crossings_better: int
    reason: str = (
        "crossings"  # crossings: the other side crosses at most half as often | length: the same count, half the run
    )
    length_now: float = 0.0  # mm of the extension lines that run inside the part, on the side the dimension is on
    length_better: float = 0.0  # and on the other side (to the edge of the view)


def _hit(p: tuple[float, float], q: tuple[float, float], s: Segment) -> float | None:
    """Where the path ``p``-``q`` crosses the segment ``s``: the distance from ``p`` along the path, or ``None``."""
    rx, ry = q[0] - p[0], q[1] - p[1]
    sx, sy = s[2] - s[0], s[3] - s[1]
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-12:  # parallel (or collinear: an edge along the line is not crossed)
        return None
    qpx, qpy = s[0] - p[0], s[1] - p[1]
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return t * math.hypot(rx, ry)
    return None


def crossings(
    p: tuple[float, float], q: tuple[float, float], outline: Sequence[Segment], skip: float = START_MM
) -> int:
    """How many places the path ``p``-``q`` crosses the outline (a vertex where two segments meet counts once; what lies
    within ``skip`` of ``p`` is the feature's own edge)."""
    at = sorted(d for s in outline if (d := _hit(p, q, s)) is not None and d > skip)
    count = 0
    last = -1.0
    for d in at:
        if d - last > SAME_MM:
            count += 1
        last = d
    return count


def run_inside(
    p: tuple[float, float], q: tuple[float, float], outline: Sequence[Segment], skip: float = START_MM
) -> float:
    """How far (mm) the path ``p``-``q`` runs before it leaves the part for good: the distance from ``p`` to the LAST place
    it crosses the outline (0 when it crosses nothing). It is what an extension line draws over the part."""
    return max((d for s in outline if (d := _hit(p, q, s)) is not None and d > skip), default=0.0)


LENGTH_RATIO = (
    0.5  # on a tie of crossings the other side wins when its run over the part is at most this share of the one now
)
MIN_GAIN_MM = 5.0  # ... and when it is at least this much shorter (a few millimetres is not worth a move)


def _axis(dim: DimShape) -> str | None:
    """``h``: the dimension line is horizontal (the extension lines are vertical); ``v``: the other way; ``None``: the extension
    lines are not axis-aligned or are of both kinds."""
    kinds: set[str] = set()
    for x1, y1, x2, y2 in dim.extension:
        length = math.hypot(x2 - x1, y2 - y1)
        if length < 1e-6:
            continue
        if abs(x2 - x1) <= AXIS_TOL * length:
            kinds.add("h")
        elif abs(y2 - y1) <= AXIS_TOL * length:
            kinds.add("v")
        else:
            return None
    return kinds.pop() if len(kinds) == 1 else None


def dimension_side(
    dims: Sequence[DimShape], outlines: Mapping[str, Sequence[Segment]], boxes: Mapping[str, Rect]
) -> list[SideFinding]:
    """The dimensions whose extension lines cross the part of their own view while the opposite side is clearly better.
    ``outlines``: the visible outline of each view as segments; ``boxes``: the rectangle of each view (the opposite side is
    followed to its edge). Two ways to be clearly better, each summed over the extension lines of the dimension:

    * ``crossings``: the opposite side crosses the outline at most half as often (a spigot dimensioned across the whole part
      while the free side is one step away);
    * ``length`` (only when both sides cross at least once, so a hole inside the part is not simply "across"): the extension
      lines run over the part (from the feature to the last crossing of the outline) at most ``LENGTH_RATIO`` as far on the
      other side and at least ``MIN_GAIN_MM`` less. Two holes 15 mm from the left edge and 85 mm from the right one are
      dimensioned on the left, where the extension lines run 15 mm over the part instead of 85.

    A dimension that can only go across (the other side is as bad) is left alone, and so is one whose view has no outline."""
    out: list[SideFinding] = []
    for dim in dims:
        outline, box = outlines.get(dim.view), boxes.get(dim.view)
        axis = _axis(dim)
        if not outline or box is None or axis is None or dim.line_at is None or not dim.extension:
            continue
        now = 0
        other = 0
        run_now = 0.0
        run_other = 0.0
        direction = 1.0
        horizontal = axis == "h"  # the dimension line is horizontal: the extension lines run along y
        for x1, y1, x2, y2 in dim.extension:
            ends = ((y1, (x1, y1)), (y2, (x2, y2))) if horizontal else ((x1, (x1, y1)), (x2, (x2, y2)))
            along, far = max(ends, key=lambda e: abs(e[0] - dim.line_at))  # the end away from the dimension line
            direction = 1.0 if dim.line_at >= along else -1.0
            if horizontal:
                feature = (far[0], far[1] - direction * GAP_MM)
                to_line = (far[0], dim.line_at)
                to_edge = (far[0], box[1] if direction > 0 else box[3])
            else:
                feature = (far[0] - direction * GAP_MM, far[1])
                to_line = (dim.line_at, far[1])
                to_edge = (box[0] if direction > 0 else box[2], far[1])
            now += crossings(feature, to_line, outline)
            other += crossings(feature, to_edge, outline)
            run_now += run_inside(feature, to_line, outline)
            run_other += run_inside(feature, to_edge, outline)
        by_count = now >= 1 and other * 2 <= now
        by_length = (
            now >= 1 and other >= 1 and run_other <= LENGTH_RATIO * run_now and run_now - run_other >= MIN_GAIN_MM
        )
        if by_count or by_length:
            names = ("above", "below") if horizontal else ("right", "left")
            first, second = names if direction > 0 else names[::-1]
            out.append(
                SideFinding(
                    dim.owner,
                    dim.view,
                    first,
                    second,
                    now,
                    other,
                    "crossings" if by_count else "length",
                    round(run_now, 2),
                    round(run_other, 2),
                )
            )
    return out


EXTENT_GAP_MM = 1.0  # an extension line of an overall dimension that hangs this long over no line of the part is across the view


def uncovered_run(p: tuple[float, float], q: tuple[float, float], outline: Sequence[Segment], tol: float = 0.05) -> float:
    """How much of the path ``p``-``q`` (an axis-aligned extension line, as long as the view it crosses) lies over NO line of
    the part: the length not covered by the ``outline`` segments that lie on the same line (within ``tol``). A box dimensioned
    over its width has its extension lines on its own two sides (0); two plates held apart by spacers have them across the
    gap between the plates (the height of the gap): the reader takes that line for an edge of the part."""
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    if length < 1e-9:
        return 0.0
    ux, uy = (q[0] - p[0]) / length, (q[1] - p[1]) / length
    spans: list[tuple[float, float]] = []
    for s in outline:
        d1 = abs((s[0] - p[0]) * uy - (s[1] - p[1]) * ux)
        d2 = abs((s[2] - p[0]) * uy - (s[3] - p[1]) * ux)
        if d1 > tol or d2 > tol:
            continue
        a = (s[0] - p[0]) * ux + (s[1] - p[1]) * uy
        b = (s[2] - p[0]) * ux + (s[3] - p[1]) * uy
        lo, hi = max(0.0, min(a, b)), min(length, max(a, b))
        if hi > lo:
            spans.append((lo, hi))
    covered = 0.0
    reach = 0.0
    for lo, hi in sorted(spans):
        lo = max(lo, reach)
        if hi > lo:
            covered += hi - lo
            reach = hi
    return max(0.0, length - covered)


@dataclass(frozen=True)
class ExtentGap:
    owner: str
    view: str
    now: str  # above | below
    gap_mm: float  # what its extension lines hang over no line of the part


def extent_gaps(
    extents: Sequence[tuple[str, str, str, Sequence[Segment]]], outlines: Mapping[str, Sequence[Segment]]
) -> list[ExtentGap]:
    """The overall dimensions whose extension lines cross the view of the part over nothing: ``extents`` are
    ``(owner, view, side, runs)`` with ``runs`` the extension lines as TechDraw draws them (from the far edge of the view to
    the edge of the view the dimension is on); ``outlines`` is the visible outline of each view."""
    out: list[ExtentGap] = []
    for owner, view, side, runs in extents:
        outline = outlines.get(view)
        if not outline:
            continue
        gap = sum(uncovered_run((r[0], r[1]), (r[2], r[3]), outline) for r in runs)
        if gap >= EXTENT_GAP_MM:
            out.append(ExtentGap(owner, view, side, round(gap, 2)))
    return out


FREE_REACH_MM = 12.0  # how far out from the view the free strip of the other side is looked for (a row and its text)
EDGE_MM = 0.2  # a dimension line this near the edge of its view is not outside it


def _rects_meet(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def free_side(dim: DimShape, box: Rect, taken: Sequence[Rect], area: Rect | None = None) -> str | None:
    """The side of its view where ``dim`` could stand without meeting anything: the opposite side to the one it is on, when
    the strip beside the view that its extension lines and its text would take (``FREE_REACH_MM`` deep) holds none of the
    ``taken`` rectangles (the other ink of the sheet) and lies in the drawing ``area``. ``None``: no free place is known
    (the dimension is inside its view, its extension lines are not axis-aligned, or the other side is taken)."""
    axis = _axis(dim)
    if axis is None or dim.line_at is None or not dim.extension:
        return None
    horizontal = axis == "h"  # the dimension line is horizontal: it stands above or below the view
    lo_edge, hi_edge = (box[1], box[3]) if horizontal else (box[0], box[2])
    if dim.line_at >= hi_edge - EDGE_MM:  # on the high side (above / right): the free place is the low one
        other = "below" if horizontal else "left"
    elif dim.line_at <= lo_edge + EDGE_MM:
        other = "above" if horizontal else "right"
    else:
        return None
    across = [v for s in dim.extension for v in ((s[0], s[2]) if horizontal else (s[1], s[3]))]
    half = dim.text_width / 2 + 1.0
    start, end = min(across) - half, max(across) + half
    if other in ("above", "right"):
        near, far = hi_edge, hi_edge + FREE_REACH_MM
    else:
        near, far = lo_edge - FREE_REACH_MM, lo_edge
    strip = (start, near, end, far) if horizontal else (near, start, far, end)
    if area is not None and not (
        area[0] <= strip[0] and strip[2] <= area[2] and area[1] <= strip[1] and strip[3] <= area[3]
    ):
        return None
    if any(_rects_meet(strip, t) for t in taken):
        return None
    return other


# --- an extension line across the dimension line of another dimension (ISO 129) ----------------------------------------


@dataclass(frozen=True)
class CrossingFinding:
    owner: str  # the dimension whose extension line crosses
    other: str  # the dimension whose dimension line is crossed
    view: str
    at: tuple[float, float]  # where (sheet mm)


CROSS_MARGIN_MM = (
    0.5  # a crossing this close to an end (the arrow tip of the line, the end of the extension line) is a touch
)


def dimension_line(dim: DimShape) -> Segment | None:
    """The dimension line of ``dim`` as a segment: from the first to the last of its extension lines at ``line_at`` (the
    arrows stand on it), longer by two arrow lengths at each end when the arrows are outside (the line runs on past them).
    ``None``: not axis-aligned, no line position, or fewer than two extension lines (nothing says where it ends)."""
    axis = _axis(dim)
    if axis is None or dim.line_at is None or len(dim.extension) < 2:
        return None
    horizontal = axis == "h"
    across = sorted((s[0] + s[2]) / 2 if horizontal else (s[1] + s[3]) / 2 for s in dim.extension)
    lo, hi = across[0], across[-1]
    if hi - lo < 1e-6:
        return None
    tail = _tail(dim)
    if horizontal:
        return (lo - tail, dim.line_at, hi + tail, dim.line_at)
    return (dim.line_at, lo - tail, dim.line_at, hi + tail)


def _tail(dim: DimShape) -> float:
    """How far the dimension line runs past each arrow tip: two arrow lengths when the arrows are outside, else nothing."""
    return 2 * dim.arrow_length if dim.arrows_inside is False else 0.0


def _crossing_point(
    a: Segment, b: Segment, a_margin: float, b_ends: tuple[float, float, float]
) -> tuple[float, float] | None:
    """Where ``a`` crosses ``b`` (``None`` when parallel or apart), unless the crossing lies within ``a_margin`` of an end
    of ``a`` or within ``CROSS_MARGIN_MM`` of the end of ``b`` or of its two arrow tips: ``b_ends`` = (tail, span, tail) of
    ``b`` along its length."""
    rx, ry = a[2] - a[0], a[3] - a[1]
    sx, sy = b[2] - b[0], b[3] - b[1]
    denom = rx * sy - ry * sx
    la, lb = math.hypot(rx, ry), math.hypot(sx, sy)
    if abs(denom) < 1e-12 or la < 1e-9 or lb < 1e-9:
        return None
    qpx, qpy = b[0] - a[0], b[1] - a[1]
    t = (qpx * sy - qpy * sx) / denom
    u = (qpx * ry - qpy * rx) / denom
    if not (0.0 <= t <= 1.0 and 0.0 <= u <= 1.0):
        return None
    along_a, along_b = t * la, u * lb
    if along_a < a_margin or la - along_a < a_margin:
        return None
    tail_lo, span, _ = b_ends
    tips = (tail_lo, tail_lo + span)  # the places the arrows stand: where another dimension's extension line is a touch
    if (
        along_b < CROSS_MARGIN_MM
        or lb - along_b < CROSS_MARGIN_MM
        or any(abs(along_b - tip) < CROSS_MARGIN_MM for tip in tips)
    ):
        return None
    return (a[0] + t * rx, a[1] + t * ry)


def dimension_crossings(dims: Sequence[DimShape]) -> list[CrossingFinding]:
    """The places where an extension line of one dimension crosses the dimension line of ANOTHER dimension of the same view
    (ISO 129 asks to avoid it: the reader takes the crossing for the end of a measure). Not counted: an extension line that
    only touches the dimension line at its end (the arrow tip: two dimensions from one datum), the extension lines of the
    dimension itself, and a crossing within ``CROSS_MARGIN_MM`` of the end of either line. One finding per pair (the
    dimension that crosses, the one crossed): the first crossing found."""
    lines = {id(d): dimension_line(d) for d in dims}
    out: list[CrossingFinding] = []
    for x in dims:
        for y in dims:
            line = lines[id(y)]
            if (
                x is y or x.view != y.view or line is None
            ):  # (one item may draw several dimensions: they are judged apart)
                continue
            tail = _tail(y)
            ends = (tail, math.hypot(line[2] - line[0], line[3] - line[1]) - 2 * tail, tail)
            for ext in x.extension:
                found = _crossing_point(ext, line, CROSS_MARGIN_MM, ends)
                if found is not None:
                    out.append(CrossingFinding(x.owner, y.owner, x.view, found))
                    break
    seen: set[tuple[str, str]] = set()
    unique: list[CrossingFinding] = []
    for f in out:
        if (f.owner, f.other) not in seen:
            seen.add((f.owner, f.other))
            unique.append(f)
    return unique


# --- the datum symbol on a line of another item -----------------------------------------------------------------------


@dataclass(frozen=True)
class DatumInk:
    """The ink of a datum symbol: the points of its filled ``triangle``, the rectangles of its ``frames`` (the lettered box)
    and the ``lines`` that join them (its stem)."""

    owner: str
    triangle: tuple[tuple[float, float], ...] = ()
    frames: tuple[Rect, ...] = ()
    lines: tuple[Segment, ...] = ()


@dataclass(frozen=True)
class DatumOnLine:
    owner: str  # the datum item
    over: str  # the item whose line it lies on
    part: str  # triangle | frame | stem
    at: tuple[float, float]


DATUM_SHRINK_MM = 0.2  # the ink of the datum is taken this much smaller: a line touching its edge is not on it
SAME_LINE_MM = 0.3  # two parallel lines this close lie on one another (the stem along an extension line)
MIN_ALONG_MM = 1.0  # ... when they run along each other this far


def _trim(s: Segment, by: float) -> Segment | None:
    length = math.hypot(s[2] - s[0], s[3] - s[1])
    if length <= 2 * by + 1e-9:
        return None
    ux, uy = (s[2] - s[0]) / length, (s[3] - s[1]) / length
    return (s[0] + ux * by, s[1] + uy * by, s[2] - ux * by, s[3] - uy * by)


def _segments_cross(a: Segment, b: Segment) -> tuple[float, float] | None:
    """The point where ``a`` and ``b`` cross (each one trimmed by ``DATUM_SHRINK_MM`` at both ends), ``None`` otherwise."""
    p, q = _trim(a, DATUM_SHRINK_MM), _trim(b, DATUM_SHRINK_MM)
    if p is None or q is None:
        return None
    at = _hit((p[0], p[1]), (p[2], p[3]), q)
    if at is None:
        return None
    length = math.hypot(p[2] - p[0], p[3] - p[1])
    return (p[0] + (p[2] - p[0]) * at / length, p[1] + (p[3] - p[1]) * at / length)


def _in_triangle(p: tuple[float, float], tri: Sequence[tuple[float, float]]) -> bool:
    """Whether ``p`` lies inside the triangle (farther than ``DATUM_SHRINK_MM`` from its sides)."""
    signs = []
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        edge = math.hypot(b[0] - a[0], b[1] - a[1])
        if edge < 1e-9:
            return False
        signs.append(((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) / edge)
    return all(s > DATUM_SHRINK_MM for s in signs) or all(s < -DATUM_SHRINK_MM for s in signs)


def _on_polygon(
    shape: Sequence[Segment], inside: "Callable[[tuple[float, float]], bool]", line: Segment
) -> tuple[float, float] | None:
    for side in shape:
        found = _segments_cross(side, line)
        if found is not None:
            return found
    for end in ((line[0], line[1]), (line[2], line[3])):
        if inside(end):
            return end
    return None


def _along(a: Segment, b: Segment) -> bool:
    """Whether two segments lie on one line (within ``SAME_LINE_MM``) and run along each other at least ``MIN_ALONG_MM``."""
    la = math.hypot(a[2] - a[0], a[3] - a[1])
    if la < 1e-9:
        return False
    ux, uy = (a[2] - a[0]) / la, (a[3] - a[1]) / la

    def lateral(x: float, y: float) -> float:
        return abs((x - a[0]) * uy - (y - a[1]) * ux)

    def along(x: float, y: float) -> float:
        return (x - a[0]) * ux + (y - a[1]) * uy

    if lateral(b[0], b[1]) > SAME_LINE_MM or lateral(b[2], b[3]) > SAME_LINE_MM:
        return False
    lo, hi = sorted((along(b[0], b[1]), along(b[2], b[3])))
    return min(hi, la) - max(lo, 0.0) >= MIN_ALONG_MM


def datum_on_line(datums: Sequence[DatumInk], others: Mapping[str, Sequence[Segment]]) -> list[DatumOnLine]:
    """The datum symbols whose triangle, frame or stem lie on a line of another item: an extension line, a dimension line or a
    leader that passes through the triangle or the lettered box (or ends inside it), or runs along the stem. ``others`` are
    the line segments of each other item by id; the outline of the part the triangle rests on is not among them, so
    resting on it is fine. One finding per (datum, item): the first part found."""
    out: list[DatumOnLine] = []
    for datum in datums:
        tri_sides: list[Segment] = []
        if len(datum.triangle) == 3:
            tri = datum.triangle
            tri_sides = [(tri[i][0], tri[i][1], tri[(i + 1) % 3][0], tri[(i + 1) % 3][1]) for i in range(3)]
        for owner, segments in others.items():
            if owner == datum.owner:
                continue
            found: DatumOnLine | None = None
            for line in segments:
                at = _on_polygon(tri_sides, lambda p, t=datum.triangle: _in_triangle(p, t), line) if tri_sides else None
                if at is not None:
                    found = DatumOnLine(datum.owner, owner, "triangle", at)
                    break
                for x0, y0, x1, y1 in datum.frames:
                    box = (x0 + DATUM_SHRINK_MM, y0 + DATUM_SHRINK_MM, x1 - DATUM_SHRINK_MM, y1 - DATUM_SHRINK_MM)
                    sides = [
                        (box[0], box[1], box[2], box[1]),
                        (box[2], box[1], box[2], box[3]),
                        (box[2], box[3], box[0], box[3]),
                        (box[0], box[3], box[0], box[1]),
                    ]
                    at = (
                        _on_polygon(sides, lambda p, b=box: b[0] < p[0] < b[2] and b[1] < p[1] < b[3], line)
                        if box[2] > box[0] and box[3] > box[1]
                        else None
                    )
                    if at is not None:
                        found = DatumOnLine(datum.owner, owner, "frame", at)
                        break
                if found is not None:
                    break
                stem = next((s for s in datum.lines if _along(s, line) or _segments_cross(s, line) is not None), None)
                if stem is not None:
                    found = DatumOnLine(datum.owner, owner, "stem", ((stem[0] + stem[2]) / 2, (stem[1] + stem[3]) / 2))
                    break
            if found is not None:
                out.append(found)
    return out


# --- a datum no tolerance frame uses --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DatumUnused:
    owner: str  # the id of the datum item
    letter: str


def datum_unused(datums: Sequence[tuple[str, str]], cited: Collection[Sequence[str]]) -> list[DatumUnused]:
    """The datum items (``(item id, letter)``) whose letter no tolerance frame cites. ``cited`` holds the ``datums`` list of
    each ``gdt`` item of the sheet (up to three letters); letters compare in capitals, and several frames may cite one datum.
    A datum symbol means nothing on its own: it is there for a frame."""
    used = {str(letter).upper() for letters in cited for letter in letters}
    return [DatumUnused(owner, letter) for owner, letter in datums if letter.upper() not in used]


# --- the leader of a chamfer callout ------------------------------------------------------------------------------------

CHAMFER_TOL_MM = 0.5  # the end of the leader is on the chamfer line when it is this near it
CHAMFER_ANGLE_DEG = (
    30.0  # a leader may deviate this much from the normal of the chamfer line (close to perpendicular to it)
)
PARALLEL_DEG = 5.0  # a segment of the outline is the chamfer line when it is this near parallel to it


def _distance_to_segment(p: tuple[float, float], s: Segment) -> float:
    dx, dy = s[2] - s[0], s[3] - s[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / length2))
    return math.hypot(p[0] - (s[0] + t * dx), p[1] - (s[1] + t * dy))


def chamfer_leader_problems(
    tip: tuple[float, float],
    elbow: tuple[float, float],
    point: tuple[float, float],
    normal: tuple[float, float],
    outline: Sequence[Segment] = (),
) -> list[str]:
    """What is wrong with the leader of a chamfer callout, as codes: ``off_line`` (its end is not on the chamfer's own line:
    the line through ``point`` perpendicular to ``normal``, and, when the ``outline`` of the view is known, a segment of it
    parallel to the chamfer within ``CHAMFER_TOL_MM`` of the end: it points at a neighbouring face), ``from_inside`` (it
    comes from the side of the part, against ``normal``, the outward normal of the chamfer) and ``oblique`` (more than
    ``CHAMFER_ANGLE_DEG`` off the normal: it should be close to perpendicular to the chamfer line). Empty: the leader is right."""
    length = math.hypot(normal[0], normal[1])
    if length < 1e-9:
        return []
    nx, ny = normal[0] / length, normal[1] / length
    problems: list[str] = []
    on_line = abs((tip[0] - point[0]) * nx + (tip[1] - point[1]) * ny) <= CHAMFER_TOL_MM
    if on_line and outline:
        sin_tol = math.sin(math.radians(PARALLEL_DEG))
        on_line = any(
            abs((s[2] - s[0]) * nx + (s[3] - s[1]) * ny) <= sin_tol * math.hypot(s[2] - s[0], s[3] - s[1])
            and _distance_to_segment(tip, s) <= CHAMFER_TOL_MM
            for s in outline
            if math.hypot(s[2] - s[0], s[3] - s[1]) > 1e-9
        )
    if not on_line:
        problems.append("off_line")
    dx, dy = elbow[0] - tip[0], elbow[1] - tip[1]
    reach = math.hypot(dx, dy)
    if reach > 1e-9:
        cos = (dx * nx + dy * ny) / reach
        if cos < 0.0:
            problems.append("from_inside")
        elif math.degrees(math.acos(min(1.0, cos))) > CHAMFER_ANGLE_DEG:
            problems.append("oblique")
    return problems


# --- rule 3: arrows outside on a short dimension ----------------------------------------------------------------------


def short_dimensions(dims: Sequence[DimShape]) -> list[DimShape]:
    """The dimensions whose span is less than the text and two arrows and whose arrows stand inside anyway (and were not
    fixed by hand)."""
    return [
        d
        for d in dims
        if d.arrows_inside is True
        and not d.pinned
        and d.span is not None
        and d.span < ARROW_ARROWS * d.arrow_length + d.text_width
    ]


# --- rule 1: a view that only carries a cutting line ------------------------------------------------------------------


@dataclass(frozen=True)
class Cut:
    """A cutting line on a view: ``label`` of the section (``A-A``) and the other views that could carry the same line
    (the ones the cutting plane is seen edge-on in)."""

    label: str
    alternatives: tuple[str, ...] = ()


@dataclass(frozen=True)
class ViewUse:
    """What a view gives the sheet: ``dims`` (a hashable key for what each dimension measures, the same in every view),
    ``other`` (anything else it carries that nothing else repeats: callouts, balloons, tolerance frames, marks the engineer
    added by hand), ``cuts`` (cutting lines drawn on it) and ``pictorial`` (an isometric: never judged), ``keep`` (never judged, but its dimensions and views stay)."""

    name: str
    dims: frozenset[Collection[object]] = field(default_factory=frozenset)
    other: bool = False
    cuts: tuple[Cut, ...] = ()
    pictorial: bool = False
    unread: bool = False
    keep: bool = False  # it counts for what stays (and carries what it carries) but is never judged: a section view


@dataclass(frozen=True)
class RedundantView:
    name: str
    reason: str  # cut_only | repeated
    cuts: tuple[Cut, ...]
    repeated_in: tuple[str, ...] = ()


def redundant_views(views: Sequence[ViewUse]) -> list[RedundantView]:
    """The views that can go. A view is redundant when it carries nothing of its own (no callout, no dimension another view
    does not repeat) and every cutting line on it has another view that stays to carry it: either it only holds the line
    (``cut_only``) or its dimensions are all repeated elsewhere (``repeated``). Views are weighed one at a time, the poorest
    first, against the ones that stay, so two views that repeat each other never both go."""
    staying = {v.name for v in views if not v.pictorial}
    out: list[RedundantView] = []
    by_name = {v.name: v for v in views}
    candidates = sorted(
        (v for v in views if not v.pictorial and not v.unread and not v.keep), key=lambda v: (len(v.dims), v.name)
    )
    for view in candidates:
        if view.other or len(staying) <= 1:
            continue
        rest = [by_name[n] for n in staying if n != view.name]
        if any(r.unread for r in rest):
            continue
        elsewhere = {d for r in rest for d in r.dims}
        if view.dims and not set(view.dims) <= elsewhere:
            continue
        if not view.dims and not view.cuts:
            continue  # a view with nothing at all is the other check's (``view_adds_nothing``)
        if not all(any(a in staying and a != view.name for a in cut.alternatives) for cut in view.cuts):
            continue
        repeated = tuple(r.name for r in rest if view.dims and set(view.dims) & set(r.dims))
        out.append(RedundantView(view.name, "repeated" if view.dims else "cut_only", view.cuts, repeated))
        staying.discard(view.name)
    return out


# --- the paper a sheet leaves blank -----------------------------------------------------------------------------------------

LOW_COVERAGE = 0.35  # the views and their dimensions cover less than this share of the free drawing area: too small
LOW_INK = 0.3  # the views THEMSELVES (not their dimensions, not the gaps) cover less than this share of it: too small
CORNER_RATIO = 3.0  # the free room on one side is more than this many times the other ...
CORNER_MIN_MM = 60.0  # ... and bigger than this: the block of views sits in a corner
FIT_MARGIN_MM = 2.0  # a larger scale / a smaller paper must leave this much room round the block to be proposed


@dataclass(frozen=True)
class Underused:
    """A sheet that wastes its paper: ``reason`` is ``small`` (the block covers ``coverage`` of the free area while a larger
    scale or a smaller paper would hold it) or ``corner`` (the free room on one ``side`` is much bigger than on the opposite
    one). ``scale`` is the largest standard scale at which the block still fits (``None``: none larger does), ``paper`` the
    smallest paper that holds it at the scale it has (``None``: none smaller does)."""

    reason: str  # small | corner
    coverage: float
    block: tuple[float, float]  # width and height of the block of views and their dimensions
    area: tuple[float, float]  # width and height of the free drawing area
    scale: float | None = None
    paper: str | None = None
    side: str = ""  # corner: the side with the free room (left | right | above | below)
    free_mm: float = 0.0  # corner: how much room is there
    opposite_mm: float = 0.0  # corner: how much on the opposite side
    ink: float = 0.0  # small: the share of the area the views themselves cover
    fewer: str = ""  # small: what to take off so a larger standard scale fits ("views=[top]")
    fewer_scale: float | None = None  # ... and the scale it would take


def _hull(rects: Sequence[Rect]) -> Rect | None:
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def sheet_underused(
    rects: Sequence[Rect],
    area: Rect,
    scale: float,
    page: tuple[float, float] | None = None,
    corner: bool = True,
    scales: Sequence[float] = (),
    papers: Mapping[str, tuple[float, float]] | None = None,
    views: Sequence[Rect] = (),
    fewer: Sequence[tuple[str, float]] = (),
) -> Underused | None:
    """Whether the views of a sheet, with their dimensions (``rects``: the rectangles of the views and of the dimension
    strips, y up), waste the free drawing ``area``: ``None`` when the sheet is balanced.

    * ``small``: the hull of ``rects`` covers less than ``LOW_COVERAGE`` of the area AND a larger standard scale (from
      ``scales``) would still fit it, or a smaller paper (from ``papers``: name -> (width, height) of its free area, at the
      scale the block has; ``page`` is the free area of the paper the sheet is on) would hold it. The block is judged as it is, not redrawn: a sheet whose scale was asked for and is already the
      largest that fits is not small, it is a small part.
    * ``small`` also when the views themselves (``views``: their rectangles, no dimension strips and no gaps) cover less
      than ``LOW_INK`` of the area and fewer views would take a larger standard scale (``fewer``: ``(what to take off, the
      scale it allows)``, the caller's layout arithmetic): a plate drawn in four views at 1:1 on A3 has a block that spans the
      sheet and views that are a fifth of it, and one view at 2:1 is the same drawing read better.
    * ``corner`` (only when ``corner``: an assembly keeps room for its parts list and the exploded view): the free room on one
      side of the hull is more than ``CORNER_RATIO`` times the opposite one and bigger than ``CORNER_MIN_MM``."""
    hull = _hull(rects)
    if hull is None or scale <= 0:
        return None
    width, height = hull[2] - hull[0], hull[3] - hull[1]
    area_w, area_h = area[2] - area[0], area[3] - area[1]
    if width <= 0 or height <= 0 or area_w <= 0 or area_h <= 0:
        return None
    coverage = width * height / (area_w * area_h)
    larger = [
        s
        for s in scales
        if s > scale * (1 + 1e-9)
        and width * (s / scale) + 2 * FIT_MARGIN_MM <= area_w
        and height * (s / scale) + 2 * FIT_MARGIN_MM <= area_h
    ]
    smaller_paper = _smaller_paper(width, height, page, papers or {})
    block, free = (round(width, 1), round(height, 1)), (round(area_w, 1), round(area_h, 1))
    ink = sum(max(0.0, v[2] - v[0]) * max(0.0, v[3] - v[1]) for v in views) / (area_w * area_h) if views else coverage
    gain = next(((what, s) for what, s in fewer if s > scale * (1 + 1e-9)), None)
    if coverage < LOW_COVERAGE and (larger or smaller_paper):
        return Underused(
            "small", coverage, block, free, max(larger) if larger else None, smaller_paper, ink=ink,
            fewer=gain[0] if gain else "", fewer_scale=gain[1] if gain else None,
        )  # fmt: skip
    if ink < LOW_INK and gain is not None:
        return Underused(
            "small", coverage, block, free, max(larger) if larger else None, smaller_paper, ink=ink,
            fewer=gain[0], fewer_scale=gain[1],
        )  # fmt: skip
    if not corner:
        return None
    gaps = {
        "left": hull[0] - area[0],
        "right": area[2] - hull[2],
        "below": hull[1] - area[1],
        "above": area[3] - hull[3],
    }
    for first, second in (("left", "right"), ("below", "above")):
        a, b = gaps[first], gaps[second]
        if a < 0 or b < 0:  # the block reaches past the area: not a matter of room
            continue
        more, less, side = (a, b, first) if a >= b else (b, a, second)
        if more > CORNER_MIN_MM and more > CORNER_RATIO * less:
            return Underused(
                "corner",
                coverage,
                block,
                free,
                max(larger) if larger else None,
                smaller_paper,
                side,
                round(more, 1),
                round(less, 1),
            )
    return None


def _smaller_paper(
    width: float, height: float, page: tuple[float, float] | None, papers: Mapping[str, tuple[float, float]]
) -> str | None:
    """The smallest of ``papers`` (name: (width, height) of the free area) that holds a block of ``width`` x ``height`` and is
    smaller than the sheet's own free area (``page``: the free area of the paper it is on)."""
    if page is None:
        return None
    fitting = [
        (w * h, name)
        for name, (w, h) in papers.items()
        if w * h < page[0] * page[1] - 1e-6 and width + 2 * FIT_MARGIN_MM <= w and height + 2 * FIT_MARGIN_MM <= h
    ]
    return min(fitting)[1] if fitting else None
