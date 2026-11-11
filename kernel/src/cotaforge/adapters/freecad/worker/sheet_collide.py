# ruff: noqa: E501
"""Collisions between the objects of a sheet (no FreeCAD import, unit-testable on its own).

A drawing is read by what is where: a dimension text under a tolerance frame, a note over a dimension line, a callout
across the outline of the part are all errors a shop sends back. This module takes the units of a composed sheet (what
each item put on the page) plus the lines of the views, looks at their INK -- text boxes and line segments, each owned
by one unit -- and reports where the ink of one unit lies on the ink of another:

* a text box over a text box (>= ``MIN_OVERLAP_MM2``);
* a text box over a line, arrow or frame edge of another unit or of a view's outline, or inside / over the frame (a closed
  rectangle: a datum or tolerance frame) of another unit even when no edge crosses it;
* two dimension lines of different units lying on one another (collinear and overlapping).

A leader crossing a dimension line, an extension line crossing an outline and centre lines under anything are how a
drawing is made, so they are NOT collisions. The same check serves ``review`` (which names the items) and the placement
(``drawing.compose`` nudges the later item of each pair away and composes again).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sheet_clip import arc_points
from sheet_model import (
    AXES,
    CONTOUR,
    DIMENSIONS,
    HATCH,
    HIDDEN,
    TITLE_BLOCK,
    Arc,
    Circle,
    Entity,
    Glyph,
    Line,
    Polyline,
    Solid,
    Text,
    text_width,
)

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
Segment = tuple[float, float, float, float]

MIN_OVERLAP_MM2 = 0.6  # two text boxes share at least this area (the estimates of a width are not exact)
TEXT_SHRINK_MM = 0.25  # a line grazing the edge of a text box is not on the text
COLLINEAR_MM = 0.6
ARROW_TOL_MM = 0.05
LINE_END_TOL_MM = (
    0.3  # a dimension line ends this near its arrow tip (a CAD that reads the tips and the lines apart is not exact)
)
MIN_RUN_MM = 1.5
VIEW_LAYERS = frozenset({CONTOUR, HIDDEN})
IGNORED_LAYERS = frozenset(
    {AXES, HATCH, TITLE_BLOCK}
)  # centre lines go under everything; the title block is the frame's


@dataclass(frozen=True)
class Ink:
    """What one owner (an item, a view) put on the page: text boxes and line segments."""

    owner: str
    texts: tuple[tuple[Rect, str], ...]
    lines: tuple[Segment, ...]
    dimension_lines: tuple[Segment, ...]
    frames: tuple[
        Rect, ...
    ] = ()  # the closed rectangles (a datum or tolerance frame): a text wholly inside one has no edge across it


@dataclass(frozen=True)
class Collision:
    """One owner's ink on another's: ``a`` is the one that lies on ``b`` (a text, or a dimension line)."""

    a: str
    b: str
    kind: str  # text_text | text_line | dimension_lines
    at: Point
    what: str = ""


def text_box(e: Text) -> Rect:
    """The page rectangle of a text as ink: its estimated width by its height (rotated texts are boxes of their turned
    corners)."""
    width = text_width(e.text, e.height)
    x0 = e.x - (width / 2 if e.anchor == "center" else width if e.anchor == "right" else 0.0)
    corners = [
        (x0, e.y - e.height * 0.2),
        (x0 + width, e.y - e.height * 0.2),
        (x0 + width, e.y + e.height),
        (x0, e.y + e.height),
    ]
    if e.angle:
        rad = math.radians(e.angle)
        cos, sin = math.cos(rad), math.sin(rad)
        corners = [
            (e.x + (px - e.x) * cos - (py - e.y) * sin, e.y + (px - e.x) * sin + (py - e.y) * cos) for px, py in corners
        ]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _segments(e: Entity) -> list[Segment]:
    if isinstance(e, Line):
        return [(e.x1, e.y1, e.x2, e.y2)]
    if isinstance(e, Polyline):
        pts = list(e.points) + ([e.points[0]] if e.closed and e.points else [])
        return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:], strict=False)]
    if isinstance(e, Arc):
        pts = arc_points(e.cx, e.cy, e.r, e.start, e.end, 0.1)
        return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:], strict=False)]
    if isinstance(e, Circle):
        pts = arc_points(e.cx, e.cy, e.r, 0.0, 360.0, 0.1)
        return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:], strict=False)]
    if isinstance(e, Solid):
        pts = list(e.points) + [e.points[0]]
        return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:], strict=False)]
    if isinstance(e, Glyph):
        return [s for stroke in e.strokes() for s in _segments(stroke)]
    return []


def ink_of(owner: str, entities: "list[Entity] | tuple[Entity, ...]") -> Ink:
    """The ink of ``entities`` (everything on the layers that count). A dimension line is a line of the dimension layer
    that ends in an arrowhead (the extension lines of two dimensions from one datum lie on one another and should)."""
    texts: list[tuple[Rect, str]] = []
    lines: list[Segment] = []
    frames: list[Rect] = []
    candidates: list[Segment] = []
    tips: list[Point] = []
    for e in entities:
        layer = getattr(e, "layer", None)
        if layer in IGNORED_LAYERS:
            continue
        if isinstance(e, Text):
            texts.append((text_box(e), e.text))
            continue
        segs = _segments(e)
        lines += segs
        if isinstance(e, Polyline) and e.closed and len(e.points) == 4:
            xs, ys = [p[0] for p in e.points], [p[1] for p in e.points]
            if all(abs(p[0] - min(xs)) < 1e-6 or abs(p[0] - max(xs)) < 1e-6 for p in e.points) and all(
                abs(p[1] - min(ys)) < 1e-6 or abs(p[1] - max(ys)) < 1e-6 for p in e.points
            ):
                frames.append((min(xs), min(ys), max(xs), max(ys)))
        if isinstance(e, Solid) and layer == DIMENSIONS and e.points:
            tips.append(e.points[0])
        elif layer == DIMENSIONS and isinstance(e, Line):
            candidates += segs
    return Ink(owner, tuple(texts), tuple(lines), tuple(main_lines(candidates, tips)), tuple(frames))


def _on_segment(s: Segment, p: Point, tol: float) -> bool:
    """Whether ``p`` lies on the segment ``s`` (within ``tol`` mm of it, ends included)."""
    dx, dy = s[2] - s[0], s[3] - s[1]
    length2 = dx * dx + dy * dy
    if length2 < 1e-12:
        return math.hypot(p[0] - s[0], p[1] - s[1]) <= tol
    t = max(0.0, min(1.0, ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / length2))
    return math.hypot(p[0] - (s[0] + t * dx), p[1] - (s[1] + t * dy)) <= tol


def main_lines(lines: "Sequence[Segment]", tips: "Sequence[Point]", through: bool = False) -> list[Segment]:
    """The dimension lines among ``lines`` (the ones that carry arrows), given the tips of the arrowheads: a line that ends
    at a tip or, with ``through`` (for the tips of ONE dimension), that runs through two tips apart: the arrows of a
    dimension whose arrows are outside stand on a line that reaches past them, and an extension line passes through the tip
    of only one end. The tolerance is that of a CAD that gives the tips and the lines separately."""
    out: list[Segment] = []
    for s in lines:
        ends = any(
            math.hypot(s[0] - t[0], s[1] - t[1]) < LINE_END_TOL_MM
            or math.hypot(s[2] - t[0], s[3] - t[1]) < LINE_END_TOL_MM
            for t in tips
        )
        on_line = [t for t in tips if _on_segment(s, t, LINE_END_TOL_MM)]
        apart = any(math.dist(a, b) > 1.0 for i, a in enumerate(on_line) for b in on_line[i + 1 :])
        if ends or (through and apart):
            out.append(s)
    return out


def extent(ink: Ink) -> Rect:
    """The box of all the ink of one owner (its texts as set, not as the SVG fragment of a symbol pads them)."""
    xs = [v for t, _ in ink.texts for v in (t[0], t[2])] + [v for s in ink.lines for v in (s[0], s[2])]
    ys = [v for t, _ in ink.texts for v in (t[1], t[3])] + [v for s in ink.lines for v in (s[1], s[3])]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)


# --- geometry ---------------------------------------------------------------------------------------------------------


def _area(a: Rect, b: Rect) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _shrunk(r: Rect, by: float) -> Rect:
    return r[0] + by, r[1] + by, r[2] - by, r[3] - by


def segment_hits_rect(s: Segment, r: Rect) -> bool:
    """Whether the segment passes through the (open) rectangle: Liang-Barsky clipping."""
    x0, y0, x1, y1 = s
    if r[2] <= r[0] or r[3] <= r[1]:
        return False
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if abs(p) < 1e-12:
            if q <= 0:  # parallel to this side and outside it, or along it: not through the inside
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t1 - t0 > 1e-9


def segments_cross(a: Segment, b: Segment, shrink: float = 0.15) -> bool:
    """Whether two segments cross each other. Each is shortened by ``shrink`` mm at both ends first, so two segments that
    only meet at an end (a leader that starts on a circle, two that share a shelf end) do not count; collinear segments
    never cross (a leader that lies along another line is not a crossing, it is an overlap, which other checks see)."""

    def trimmed(s: Segment) -> Segment | None:
        length = math.hypot(s[2] - s[0], s[3] - s[1])
        if length <= 2 * shrink + 1e-9:
            return None
        ux, uy = (s[2] - s[0]) / length, (s[3] - s[1]) / length
        return (s[0] + ux * shrink, s[1] + uy * shrink, s[2] - ux * shrink, s[3] - uy * shrink)

    p, q = trimmed(a), trimmed(b)
    if p is None or q is None:
        return False
    d1x, d1y = p[2] - p[0], p[3] - p[1]
    d2x, d2y = q[2] - q[0], q[3] - q[1]
    denom = d1x * d2y - d1y * d2x
    if abs(denom) < 1e-9:
        return False
    t = ((q[0] - p[0]) * d2y - (q[1] - p[1]) * d2x) / denom
    u = ((q[0] - p[0]) * d1y - (q[1] - p[1]) * d1x) / denom
    return 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0


def segments_of(entities: "list[Entity] | tuple[Entity, ...]") -> list[Segment]:
    """Every straight piece of ``entities`` (arcs and circles discretised): what a leader is tested against."""
    out: list[Segment] = []
    for e in entities:
        out += _segments(e)
    return out


def _collinear_overlap(a: Segment, b: Segment) -> float:
    """How far (mm) two axis-parallel segments that lie within ``COLLINEAR_MM`` of one line run along each other."""
    for horizontal in (True, False):
        if horizontal and abs(a[1] - a[3]) < 1e-6 and abs(b[1] - b[3]) < 1e-6 and abs(a[1] - b[1]) <= COLLINEAR_MM:
            lo, hi = max(min(a[0], a[2]), min(b[0], b[2])), min(max(a[0], a[2]), max(b[0], b[2]))
            return max(0.0, hi - lo)
        if not horizontal and abs(a[0] - a[2]) < 1e-6 and abs(b[0] - b[2]) < 1e-6 and abs(a[0] - b[0]) <= COLLINEAR_MM:
            lo, hi = max(min(a[1], a[3]), min(b[1], b[3])), min(max(a[1], a[3]), max(b[1], b[3]))
            return max(0.0, hi - lo)
    return 0.0


# --- the check --------------------------------------------------------------------------------------------------------


def collisions(units: list[Ink], views: list[Ink] | None = None) -> list[Collision]:
    """Every place the ink of one owner lies on the ink of another. ``units`` are the items (a collision is reported as
    ``a`` over ``b``, ``a`` being the later one of the list); ``views`` are the outlines of the views (a text over them is
    a collision, a line over them is not)."""
    found: list[Collision] = []
    for i, later in enumerate(units):
        for earlier in units[:i]:
            found += _pair(later, earlier)
        for view in views or []:
            found += _against_view(later, view)
    return found


def _pair(a: Ink, b: Ink) -> list[Collision]:
    out: list[Collision] = []
    for ta, text_a in a.texts:
        for tb, text_b in b.texts:
            if _area(ta, tb) >= MIN_OVERLAP_MM2:
                out.append(
                    Collision(
                        a.owner,
                        b.owner,
                        "text_text",
                        ((max(ta[0], tb[0]) + min(ta[2], tb[2])) / 2, (max(ta[1], tb[1]) + min(ta[3], tb[3])) / 2),
                        f"{text_a!r} over {text_b!r}",
                    )
                )
        shrunk = _shrunk(ta, TEXT_SHRINK_MM)
        for seg in b.lines:
            if segment_hits_rect(seg, shrunk):
                out.append(
                    Collision(
                        a.owner,
                        b.owner,
                        "text_line",
                        ((ta[0] + ta[2]) / 2, (ta[1] + ta[3]) / 2),
                        f"{text_a!r} crossed by a line of {b.owner}",
                    )
                )
                break
    for tb, text_b in b.texts:
        shrunk = _shrunk(tb, TEXT_SHRINK_MM)
        for seg in a.lines:
            if segment_hits_rect(seg, shrunk):
                out.append(
                    Collision(
                        a.owner,
                        b.owner,
                        "line_text",
                        ((tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2),
                        f"a line of {a.owner} crosses {text_b!r}",
                    )
                )
                break
    for first, second in ((a, b), (b, a)):  # a text on the frame of a datum or a tolerance, edge or no edge across it
        for t, text in first.texts:
            for frame in second.frames:
                if _area(t, frame) >= MIN_OVERLAP_MM2:
                    out.append(
                        Collision(
                            first.owner,
                            second.owner,
                            "text_frame",
                            (
                                (max(t[0], frame[0]) + min(t[2], frame[2])) / 2,
                                (max(t[1], frame[1]) + min(t[3], frame[3])) / 2,
                            ),
                            f"{text!r} lies on a frame of {second.owner}",
                        )
                    )
                    break
    for sa in a.dimension_lines:
        for sb in b.dimension_lines:
            if _collinear_overlap(sa, sb) >= MIN_RUN_MM:
                out.append(
                    Collision(
                        a.owner,
                        b.owner,
                        "dimension_lines",
                        ((sa[0] + sa[2]) / 2, (sa[1] + sa[3]) / 2),
                        "two dimension lines lie on one another",
                    )
                )
                break
    return _unique(out)


def _against_view(a: Ink, view: Ink) -> list[Collision]:
    out: list[Collision] = []
    for ta, text_a in a.texts:
        shrunk = _shrunk(ta, TEXT_SHRINK_MM)
        if any(segment_hits_rect(seg, shrunk) for seg in view.lines):
            out.append(
                Collision(
                    a.owner,
                    view.owner,
                    "text_line",
                    ((ta[0] + ta[2]) / 2, (ta[1] + ta[3]) / 2),
                    f"{text_a!r} over the outline of {view.owner}",
                )
            )
    return out


def _unique(found: list[Collision]) -> list[Collision]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[Collision] = []
    for c in found:
        key = (c.a, c.b, c.kind, c.what)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out
