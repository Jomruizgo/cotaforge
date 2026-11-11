# ruff: noqa: E501
"""Three drawing rules a shop reader expects, as checks on plain sheet data (no FreeCAD import, adapter-neutral: the
FreeCAD review and the external adapter's review both build these facts and call the functions below).

* ``dimension_side``: a dimension sits on the side of the feature it measures, the side where its extension lines cross the
  least of the part. A spigot at the bottom is dimensioned below; the extension lines do not run across the whole part when
  the other side is free.
* ``redundant_views``: a view that only carries a cutting line, or whose dimensions another view repeats, goes when another
  view that stays can carry the line. On a turned part the cutting line goes on the circular view and the section carries
  the diameters and lengths.
* ``short_dimensions``: on a short dimension whose text and two arrows do not fit between the extension lines, the arrows
  go outside.

Everything is in sheet millimetres, y up. The functions return plain findings; the adapters turn them into review rows.
"""

import math
from collections.abc import Collection, Mapping, Sequence
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
    """The dimensions whose extension lines cross the part of their own view while the opposite side crosses at most half as
    much. ``outlines``: the visible outline of each view as segments; ``boxes``: the rectangle of each view (the opposite side
    is followed to its edge). A dimension that can only go across (the other side crosses as much) is left alone, and so is
    one whose view has no outline."""
    out: list[SideFinding] = []
    for dim in dims:
        outline, box = outlines.get(dim.view), boxes.get(dim.view)
        axis = _axis(dim)
        if not outline or box is None or axis is None or dim.line_at is None or not dim.extension:
            continue
        now = 0
        other = 0
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
        if now >= 1 and other * 2 <= now:
            names = ("above", "below") if horizontal else ("right", "left")
            first, second = names if direction > 0 else names[::-1]
            out.append(SideFinding(dim.owner, dim.view, first, second, now, other))
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
