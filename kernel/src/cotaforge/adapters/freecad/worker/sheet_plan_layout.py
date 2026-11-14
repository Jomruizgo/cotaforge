# ruff: noqa: E501
"""Global layout of a drawing sheet: where every view and every annotation goes, decided at once (no FreeCAD import).

Placing the items of a sheet one by one makes the later ones give way to the earlier ones: a callout cramped at the tip, a
dimension line through the notes, a datum on top of a location dimension, leaders across the part. This module takes the
whole sheet and decides everything together, deterministically and fast (a sheet of 60 items in a few tens of ms).

One entry point::

    plan_layout(sheet: Sheet, views: Sequence[View], items: Sequence[Item], *, min_view_gap: float = 12.0) -> Plan

Coordinates are page millimetres, y DOWN (as ``sheet_layout``). Anchors of an item are in the LOCAL frame of its view
(origin at the top left of the view's bounding box, y down), so the views can still be moved by the planner.

Input
-----
``Sheet``    paper size, ``frame``, ``title_block`` and ``parts_list`` rectangles (x0, y0, x1, y1) and the drawing ``area``
             (default: the frame down to the title block). Nothing is placed over the title block or the parts list.
``View``     ``id``, bounding box size ``w`` x ``h`` at the sheet's scale, and where it goes: ``relation=(ref_id, dir)`` with
             ``dir`` in below|above|left|right (centred on the reference view, the gap between them is made from what the
             annotations of both facing sides need), ``at=(x, y)`` to pin it, or neither (a free view, e.g. the isometric one:
             it takes the free room that is left). ``outline`` (optional) are the view's visible lines, local, used to avoid
             leaders crossing the part.
``Item``     ``kind``: ``dim`` (two anchors; ``axis`` ``h`` measures along x, ``v`` along y; ``w``/``h`` the text size),
             ``callout`` (one anchor, text box ``w`` x ``h``, leader), ``balloon`` (as callout, ``w`` is the diameter),
             ``datum`` (anchor on an edge, ``normal`` pointing out, ``slide`` mm it may move along the edge, ``w`` x ``h`` the
             frame), ``section_label`` (above its view), ``surface`` (the general surface symbol, top right) and ``note``
             (``order`` is its number; all notes form ONE block in number order), ``leader`` (an oblique line from its tip to an
             elbow and a horizontal shelf the text stands on, ISO 128-22; ``anchors[0]`` is the tip, ``extra`` the centre and radius
             of the circle it points at, when it leaves a hole), ``angle`` (an angular dimension: ``anchors[0]`` the apex, ``extra``
             the angles of its two rays) and ``weld`` (a weld symbol: ``anchors`` the ends of its arrows, ``extra`` the room the
             symbol takes about its anchor). ``sides`` restricts or orders the allowed
             sides (top|bottom|left|right). ``fixed_box`` / ``fixed_segments`` pin an item the engineer placed by hand: it is
             not moved and is an obstacle for everything else.

Output (``Plan``)
-----------------
``views``     the position (x, y of the top left corner) of every placed view.
``items``     for every placed item its ``side``, ``level`` (dimension stack position, 0 = nearest the view), ``text_box``,
              the ``segments`` it draws (dimension line, extension lines, leader, datum stem) and its ``cost``.
``unplaced``  every item (or view) that could not be placed and why. Nothing is dropped silently.
``fits``      False when the views with the room their annotations need do not fit the area; ``needed`` is (w, h) of that.

Strategy
--------
1. Room per view side: dimensions are stacked tentatively (shorter inside, ISO 129: first line 10 mm from the outline, 7 mm
   between lines) and callouts, datums and labels add their columns; the gap between related views is made from it.
2. The views are arranged from those gaps and centred in the area; free views take the free room (nearest the top right).
3. Items are placed in priority order (dimensions by span, angles, section labels, datums, weld symbols, leaders, callouts
   and balloons, the surface symbol, the notes block), each by scoring candidate positions against the ink already on the sheet: text over text or over
   a line, collinear lines, anything over a view or the title block is forbidden; leader length and the centre lines a leader
   crosses cost. A leader (ISO 128-22) crosses no dimension line, no extension line, no datum stem, no arrow line and no other
   leader, and the outline of a view once at most: a candidate that does is refused, not paid for. Its text keeps a text
   height from every other item (``TEXT_CLEAR_MM``; ``TEXT_CLEAR_MIN_MM`` when no place has that much). A leader with no such
   place outside its view may stand inside it (``Item.inside``: its text on an area with no line of the part, a dimension or a
   centre line); with none at all it is reported as unplaced (never drawn across a dimension), and a callout that found no
   place chooses first in the next run. The arrows of a weld symbol cross no leader and no other arrow (that costs more than
   anything else: a weld with no such place is reported, never drawn across a leader).
4. If the notes block or the surface symbol find no free room, the area is reduced by them and everything is redone. The notes
   block keeps a text height off the lines and texts of the sheet, and moves to where it does when it does not.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
Segment = tuple[float, float, float, float]

DIM_FIRST_MM = 10.0  # first dimension line from the outline (ISO 129-1)
DIM_STEP_MM = 7.0  # between parallel dimension lines
MIN_PARALLEL_GAP_MM = 6.5
"""Two parallel dimension lines that run along each other are never closer than this (a text height, 3.5 mm, plus the room
for the arrows and the text of the one outside): the planner stacks them ``DIM_STEP_MM`` apart, the review holds a sheet whose
lines are closer (``dimension_gap``)."""
STACK_TOL_MM = 1.0  # two dimensions run along each other when they share more than this; their lengths differ when they differ by more
EXT_GAP_MM = 1.0  # extension line starts this far from the feature
EXT_OVER_MM = 2.0  # ... and goes this far past the dimension line
TEXT_GAP_MM = 0.8
ARROW_MM = 3.0
CLEAR_MM = 0.8  # clearance between two text boxes
LINE_CLEAR_MM = 0.25  # a line grazing a text box
STEM_MIN_MM = 5.0
ELBOW_ROOM_MM = 1.5  # the text of a leader keeps this far from the lines on the side its elbow is
TEXT_CLEAR_MM = 3.5  # a text keeps one text height (``sheet_model.TEXT_HEIGHT_MM``) from every other item (ISO 128-22: a leader and its text stand clear)
TEXT_CLEAR_MIN_MM = 1.0  # ... and when no place has that much, the least that still reads as apart
INSIDE_CLEAR_MM = 2.0  # a callout inside a view keeps this far from the lines of the part, a centre mark reaches 2 mm past its circle
INSIDE_PENALTY = 8.0  # a callout inside the view is the last resort: any place outside, clean, is better
INSIDE_STEP_MM = 2.0
INSIDE_MAX_MM = 70.0
CENTRE_CROSS = 1.5  # a leader across a centre line: allowed (a hole inside the part cannot be reached otherwise), the fewer the better
MAX_LEADER_CUTS = 1  # a leader goes through the outline of the part once: from the feature to the outside
LEADER_MIN_MM = 6.0
GRID_MM = 20.0
MAX_LEVELS = 8
JUDGE_BUDGET = 25
NOTE_GAP_MM = 1.0
SIDES = ("top", "bottom", "left", "right")
ARROW_CROSS = 200.0  # an arrow line across a leader or another arrow line: a place that has it is not taken when any other exists
LEADER_ANGLES = (45, 135, 225, 315, 30, 60, 120, 150, 210, 240, 300, 330)
"""The angles (degrees, counter-clockwise on the page) a leader may leave its tip at, the 45 degree ones first: ISO 128-22
keeps a leader off the horizontal and the vertical (it would read as a centre line or a dimension line)."""
ANGLE_RADII_MM = (12.0, 16.0, 20.0, 25.0, 31.0, 38.0)  # the arc of an angular dimension (ISO 129-1: outside the part)
ANGLE_STEP_MM = 5.0  # between the radii an arc is tried at when it has a least radius to clear
WELD_DISTANCES_MM = (8.0, 12.0, 18.0, 26.0, 36.0, 46.0, 58.0)
WELD_DIRECTIONS = 24
MAX_CANDIDATES = 400
DEFAULT_PRIORITY = {
    "dim": 0,
    "angle": 0,
    "section_label": 1,
    "datum": 2,
    "weld": 3,
    "leader": 3,
    "callout": 3,
    "balloon": 3,
    "surface": 4,
    "note": 5,
}
_NORMALS = {"top": (0.0, -1.0), "bottom": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}


@dataclass(frozen=True)
class Sheet:
    paper: tuple[float, float]
    frame: Rect
    title_block: Rect | None = None
    parts_list: Rect | None = None
    area: Rect | None = None


@dataclass(frozen=True)
class View:
    id: str
    w: float
    h: float
    relation: tuple[str, str] | None = None
    at: Point | None = None
    outline: tuple[Segment, ...] = ()
    centre: tuple[Segment, ...] = ()
    """The centre lines and marks of the view's holes (local, as ``outline``), also those that run out past the part: a callout keeps its text off them and crosses as few as it can."""


@dataclass(frozen=True)
class Item:
    id: str
    kind: str
    view: str | None = None
    w: float = 0.0
    h: float = 0.0
    anchors: tuple[Point, ...] = ()
    axis: str = "h"
    sides: tuple[str, ...] = ()
    priority: int | None = None
    normal: Point | None = None
    slide: float = 0.0
    order: int = 0
    fixed_box: Rect | None = None
    fixed_segments: tuple[Segment, ...] = ()
    fixed_kinds: tuple[str, ...] = ()
    """The kind of each fixed segment (``ext`` when not given): a pinned leader costs a crossing like a placed one."""
    inside: bool = False
    """``leader``: the drawer accepts its text on an area of the view that holds no line (a hole callout does)."""
    extra: tuple[float, ...] = ()
    """Kind specific numbers. ``angle``: the two ray angles (degrees, counter-clockwise on the page, y UP). ``leader``: the centre of the circle the
    leader points at and its radius (local, y down): the tip moves round it. ``weld``: the
    symbol's reach along the reference line, above it and below it, for a symbol that runs to the right of its anchor."""


@dataclass(frozen=True)
class ViewPos:
    id: str
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class Placed:
    id: str
    kind: str
    view: str | None
    side: str | None
    level: int
    text_box: Rect
    segments: tuple[Segment, ...] = ()
    cost: float = 0.0
    data: tuple[float, ...] = ()
    """What the item needs to be drawn there. ``angle``: the radius of the arc. ``weld``: the anchor (x, y, page, y down).
    ``leader``: the elbow and the tip (x, y, x, y: page, y down). ``datum``: the stem length."""


@dataclass(frozen=True)
class Unplaced:
    id: str
    reason: str


@dataclass(frozen=True)
class Plan:
    views: tuple[ViewPos, ...]
    items: tuple[Placed, ...]
    unplaced: tuple[Unplaced, ...]
    fits: bool
    needed: tuple[float, float]


# --- geometry ------------------------------------------------------------------------------------------------------------


def _inflate(r: Rect, m: float) -> Rect:
    return (r[0] - m, r[1] - m, r[2] + m, r[3] + m)


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside(r: Rect, frame: Rect) -> bool:
    return r[0] >= frame[0] and r[1] >= frame[1] and r[2] <= frame[2] and r[3] <= frame[3]


def _seg_rect(s: Segment, r: Rect) -> bool:
    """Whether the segment touches the rectangle (Liang-Barsky)."""
    x0, y0, x1, y1 = s
    if max(x0, x1) < r[0] or min(x0, x1) > r[2] or max(y0, y1) < r[1] or min(y0, y1) > r[3]:
        return False
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                if t > t1:
                    return False
                t0 = max(t0, t)
            else:
                if t < t0:
                    return False
                t1 = min(t1, t)
    return True


def _inside_len(s: Segment, r: Rect) -> float:
    """Length of the segment inside the rectangle."""
    x0, y0, x1, y1 = s
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if abs(p) < 1e-12:
            if q < 0:
                return 0.0
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return max(0.0, t1 - t0) * math.hypot(dx, dy)


def _ray_exit(box: Rect, p: Point, u: Point) -> float:
    """How far the ray from ``p`` along ``u`` runs before it leaves ``box``."""
    ts = []
    if u[0] > 1e-9:
        ts.append((box[2] - p[0]) / u[0])
    elif u[0] < -1e-9:
        ts.append((box[0] - p[0]) / u[0])
    if u[1] > 1e-9:
        ts.append((box[3] - p[1]) / u[1])
    elif u[1] < -1e-9:
        ts.append((box[1] - p[1]) / u[1])
    return max(0.0, min(ts)) if ts else 0.0


def _far(a: Segment, b: Segment) -> bool:
    return (
        max(a[0], a[2]) < min(b[0], b[2]) - 1
        or max(b[0], b[2]) < min(a[0], a[2]) - 1
        or max(a[1], a[3]) < min(b[1], b[3]) - 1
        or max(b[1], b[3]) < min(a[1], a[3]) - 1
    )


def _cross(a: Segment, b: Segment) -> bool:
    ax, ay = a[2] - a[0], a[3] - a[1]
    bx, by = b[2] - b[0], b[3] - b[1]
    det = ax * by - ay * bx
    if abs(det) < 1e-9:
        return False
    ex, ey = b[0] - a[0], b[1] - a[1]
    t = (ex * by - ey * bx) / det
    u = (ex * ay - ey * ax) / det
    eps = 0.02
    return eps < t < 1 - eps and eps < u < 1 - eps


def _collinear_overlap(a: Segment, b: Segment, tol: float = 0.6) -> float:
    dx, dy = a[2] - a[0], a[3] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0
    ux, uy = dx / length, dy / length
    ts = []
    for px, py in ((b[0], b[1]), (b[2], b[3])):
        if abs((px - a[0]) * uy - (py - a[1]) * ux) > tol:
            return 0.0
        ts.append((px - a[0]) * ux + (py - a[1]) * uy)
    return min(length, max(ts)) - max(0.0, min(ts))


# --- the ink on the sheet ------------------------------------------------------------------------------------------------


@dataclass
class _Ink:
    """What one owner puts on the page: text-like boxes (texts, frames), lines with a kind, and solids (views, title block)."""

    owner: str
    texts: list[Rect] = field(default_factory=list)
    segs: list[tuple[Segment, str]] = field(default_factory=list)
    solids: list[Rect] = field(default_factory=list)
    box: Rect = (0.0, 0.0, 0.0, 0.0)  # the bounding box (inflated) once on the sheet
    skip_solid: str | None = None  # the view whose body the lines of this ink may cross (its own)
    text_in_view: bool = False  # ... and whose body its texts may stand over, when no line of the part is under them (an angle)
    strict: bool = False  # a leader: its lines cross no dimension line, extension line, leader or datum stem, and the outline of a view once at most
    clear: float = 0.0  # the room its texts keep from the lines and texts of the others (and the lines of this ink from theirs)

    def bbox(self) -> Rect:
        xs: list[float] = []
        ys: list[float] = []
        for r in (*self.texts, *self.solids):
            xs += [r[0], r[2]]
            ys += [r[1], r[3]]
        for s, _ in self.segs:
            xs += [s[0], s[2]]
            ys += [s[1], s[3]]
        return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)


class _Index:
    def __init__(self) -> None:
        self.cells: dict[tuple[int, int], list[_Ink]] = {}

    def _cells(self, b: Rect) -> list[tuple[int, int]]:
        return [
            (i, j)
            for i in range(int(b[0] // GRID_MM) - 0, int(b[2] // GRID_MM) + 1)
            for j in range(int(b[1] // GRID_MM) - 0, int(b[3] // GRID_MM) + 1)
        ]

    def add(self, ink: _Ink) -> None:
        ink.box = _inflate(ink.bbox(), 1.0)
        for c in self._cells(ink.box):
            self.cells.setdefault(c, []).append(ink)

    def near(self, b: Rect) -> list[_Ink]:
        seen: set[int] = set()
        found: list[_Ink] = []
        for c in self._cells(_inflate(b, 1.0)):
            for ink in self.cells.get(c, ()):
                if id(ink) not in seen:
                    seen.add(id(ink))
                    found.append(ink)
        return found


# Cost of a crossing of two kinds of line (a leader through a leader is the worst; an extension line over a dimension line
# is how a drawing is made).
_CROSS_COST = {
    frozenset(["leader"]): 40.0,
    frozenset(["leader", "stem"]): 40.0,
    frozenset(["stem"]): 40.0,
    frozenset(["leader", "dim"]): 10.0,
    frozenset(["stem", "dim"]): 10.0,
    frozenset(["leader", "outline"]): 8.0,
    frozenset(["leader", "ext"]): 3.0,
    frozenset(["leader", "centre"]): CENTRE_CROSS,
    frozenset(["stem", "ext"]): 3.0,
    frozenset(["ext", "dim"]): 6.0,  # an extension line across a dimension line (ISO 129): another side, when there is one, is better
    # the arrow line of a weld symbol: it crosses no leader (ISO 2553: the arrow line is read as the one that points at the joint)
    frozenset(["arrow"]): ARROW_CROSS,
    frozenset(["arrow", "leader"]): ARROW_CROSS,
    frozenset(["arrow", "stem"]): ARROW_CROSS,
    frozenset(["arrow", "dim"]): 10.0,
    frozenset(["arrow", "outline"]): 8.0,
    frozenset(["arrow", "ext"]): 3.0,
}
_LEADER_NEVER_CROSSES = frozenset(
    frozenset(pair)
    for pair in (("leader",), ("leader", "dim"), ("leader", "ext"), ("leader", "stem"), ("leader", "arrow"))
)
"""What a leader (ISO 128-22) never crosses: another leader, a dimension line, an extension line, a datum stem, an arrow line."""
CENTRE_OWNER = "centre:"
_NO_COLLINEAR = frozenset(["ext"])  # two extension lines on one another is a shared extension line


class _Ctx:
    def __init__(self, sheet: Sheet) -> None:
        self.frame = sheet.frame
        self.index = _Index()
        self.forbidden: list[Rect] = [r for r in (sheet.title_block, sheet.parts_list) if r]
        sheet_ink = _Ink("sheet", solids=list(self.forbidden))
        if self.forbidden:
            self.index.add(sheet_ink)
        self.inks: dict[str, _Ink] = {}

    def commit(self, ink: _Ink) -> None:
        self.index.add(ink)


def _text_free(r: Rect, owner: str, ctx: _Ctx, own_view: str | None = None, clear: float = 0.0, strict: bool = False) -> str | None:
    """Cheap first test of a text box alone: why it cannot be there."""
    if not _inside(r, ctx.frame):
        return "outside the frame"
    pad = _inflate(r, max(CLEAR_MM / 2, clear))
    inner = _inflate(r, clear - LINE_CLEAR_MM if clear else -LINE_CLEAR_MM)
    for other in ctx.index.near(pad):
        if other.owner == owner or (other.owner.startswith(CENTRE_OWNER) and not strict):
            continue
        for r2 in other.texts:
            if (
                r2[0] - CLEAR_MM / 2 < pad[2]
                and pad[0] < r2[2] + CLEAR_MM / 2
                and r2[1] - CLEAR_MM / 2 < pad[3]
                and pad[1] < r2[3] + CLEAR_MM / 2
            ):
                return f"text over {other.owner}"
        for r2 in other.solids:
            if _overlap(r, r2) and other.owner != f"view:{own_view}":
                return f"text over {other.owner}"
        for s2, _k in other.segs:
            if _seg_rect(s2, inner):
                return f"text over a line of {other.owner}"
    return None


def _judge(ink: _Ink, ctx: _Ctx) -> tuple[str | None, float]:
    """(why it cannot be there, cost of the crossings and the like). A reason ends the candidate."""
    for r in ink.texts:
        if not _inside(r, ctx.frame):
            return "outside the frame", 0.0
    for s, _ in ink.segs:
        if not (_inside((s[0], s[1], s[0], s[1]), ctx.frame) and _inside((s[2], s[3], s[2], s[3]), ctx.frame)):
            return "outside the frame", 0.0
    cost = 0.0
    cuts = 0
    reach = max(ink.clear, 1.0)
    mine = _inflate(ink.bbox(), reach)
    for other in ctx.index.near(mine):
        if other.owner == ink.owner or not _overlap(mine, other.box):
            continue
        if other.owner.startswith(CENTRE_OWNER) and not ink.strict:
            continue  # the centre lines are only the callouts to keep clear of
        spare = ink.clear - LINE_CLEAR_MM if ink.clear else -LINE_CLEAR_MM
        for r in ink.texts:
            for r2 in other.texts:
                if _overlap(_inflate(r, max(CLEAR_MM / 2, ink.clear)), _inflate(r2, CLEAR_MM / 2)):
                    return f"text over {other.owner}", 0.0
            for r2 in other.solids:
                if _overlap(r, r2) and not (ink.text_in_view and other.owner == f"view:{ink.skip_solid}"):
                    return f"text over {other.owner}", 0.0
            for s2, _k in other.segs:
                if _seg_rect(s2, _inflate(r, spare)):
                    return f"text over a line of {other.owner}", 0.0
        for r2 in other.texts:
            for s, _ in ink.segs:
                if _seg_rect(s, _inflate(r2, spare)):
                    return f"line through the text of {other.owner}", 0.0
        if other.solids:
            for s, _ in ink.segs:
                for r2 in other.solids:
                    if other.owner != f"view:{ink.skip_solid}" and _seg_rect(s, _inflate(r2, -0.3)):
                        return f"line across {other.owner}", 0.0
        for s, k in ink.segs:
            for s2, k2 in other.segs:
                if _far(s, s2):
                    continue
                kinds = frozenset([k, k2])
                if "outline" not in kinds and "centre" not in kinds and kinds != _NO_COLLINEAR and _collinear_overlap(s, s2) > 0.5:
                    return f"line on a line of {other.owner}", 0.0
                if _cross(s, s2):
                    if ink.strict and kinds in _LEADER_NEVER_CROSSES:
                        return f"leader across a {'dimension line' if k2 == 'dim' else 'line'} ({k2}) of {other.owner}", 0.0
                    if ink.strict and k == "leader" and k2 == "outline":
                        cuts += 1
                        if cuts > MAX_LEADER_CUTS:
                            return f"leader through the outline of {other.owner} more than once", 0.0
                    cost += _CROSS_COST.get(kinds, 0.0)
    return None, cost


# --- the rooms and the views ---------------------------------------------------------------------------------------------


def _dim_sides(it: Item) -> tuple[str, ...]:
    default = ("top", "bottom") if it.axis == "h" else ("left", "right")
    return tuple(s for s in it.sides if s in default) or default


def _edge_distance(view: View, p: Point, side: str) -> float:
    return {"top": p[1], "bottom": view.h - p[1], "left": p[0], "right": view.w - p[0]}[side]


def _ext_length(view: View, it: Item, side: str) -> float:
    return sum(_edge_distance(view, a, side) for a in it.anchors) / max(1, len(it.anchors))


def _offset(level: int, h: float) -> float:
    return max(DIM_FIRST_MM, h + 4.0) + level * max(DIM_STEP_MM, h + 3.0)


def _span(it: Item) -> float:
    k = 0 if it.axis == "h" else 1
    return abs(it.anchors[0][k] - it.anchors[1][k])


def _levels(entries: list[tuple[str, float, float]]) -> dict[str, int]:
    """Stack of one side: shorter inside; dimensions whose intervals do not overlap share a level."""
    taken: list[list[tuple[float, float]]] = []
    out: dict[str, int] = {}
    for key, lo, hi in sorted(entries, key=lambda e: (e[2] - e[1], e[1], e[0])):
        lvl = 0
        while lvl < len(taken) and any(lo < b + 1.0 and a < hi + 1.0 for a, b in taken[lvl]):
            lvl += 1
        if lvl == len(taken):
            taken.append([])
        taken[lvl].append((lo, hi))
        out[key] = lvl
    return out


def _rooms(views: dict[str, View], items: Sequence[Item]) -> dict[tuple[str, str], float]:
    """Room each view side needs for its annotations, estimated before the views are placed."""
    stacks: dict[tuple[str, str], list[tuple[str, float, float]]] = {}
    heights: dict[tuple[str, str], float] = {}
    cols: dict[tuple[str, str], float] = {}
    mins: dict[tuple[str, str], float] = {}
    for it in items:
        v = views.get(it.view or "")
        if v is None or not it.anchors and it.kind != "section_label":
            continue
        if it.kind == "dim" and len(it.anchors) == 2:
            side = min(_dim_sides(it), key=lambda s, v=v, it=it: _ext_length(v, it, s))
            k = 0 if it.axis == "h" else 1
            c = (it.anchors[0][k] + it.anchors[1][k]) / 2
            half = max(_span(it), it.w + 2 * ARROW_MM) / 2
            stacks.setdefault((v.id, side), []).append((it.id, c - half, c + half))
            heights[(v.id, side)] = max(heights.get((v.id, side), 0.0), it.h)
        elif it.kind in ("callout", "balloon", "leader") and it.anchors:
            side = min(it.sides or SIDES, key=lambda s, v=v, it=it: _edge_distance(v, it.anchors[0], s))
            ext = (it.h if side in ("top", "bottom") else it.w) + LEADER_MIN_MM
            cols[(v.id, side)] = max(cols.get((v.id, side), 0.0), ext)
        elif it.kind == "datum" and it.normal:
            side = next(s for s in SIDES if _NORMALS[s] == (float(it.normal[0]), float(it.normal[1])))
            ext = STEM_MIN_MM + (it.h if side in ("top", "bottom") else it.w) + 2.0
            mins[(v.id, side)] = max(mins.get((v.id, side), 0.0), ext)
        elif it.kind == "section_label":
            mins[(v.id, "top")] = max(mins.get((v.id, "top"), 0.0), it.h + 5.0)
    rooms: dict[tuple[str, str], float] = {}
    for key, entries in stacks.items():
        n = max(_levels(entries).values()) + 1
        rooms[key] = _offset(n - 1, heights[key]) + 2.0
    for key, ext in mins.items():
        rooms[key] = max(rooms.get(key, 0.0), ext)
    for key, ext in cols.items():
        rooms[key] = rooms.get(key, 0.0) + ext
    return rooms


def _arrange(
    views: Sequence[View], rooms: dict[tuple[str, str], float], min_gap: float
) -> tuple[dict[str, Point], list[Unplaced]]:
    """Relative positions of the related views and of the pinned ones."""
    by_id = {v.id: v for v in views}
    pos: dict[str, Point] = {}
    failed: list[Unplaced] = []
    pending = [v for v in views if v.relation is not None]
    for v in views:
        if v.relation is None and v.at is not None:
            pos[v.id] = v.at
    roots = [
        v
        for v in views
        if v.relation is None and v.at is None and any(w.relation and w.relation[0] == v.id for w in views)
    ]
    for r in roots:
        pos[r.id] = (0.0, 0.0)
    progress = True
    while pending and progress:
        progress = False
        for v in list(pending):
            ref_id, direction = v.relation or ("", "")
            if ref_id not in pos or ref_id not in by_id:
                continue
            ref = by_id[ref_id]
            rx, ry = pos[ref_id]
            if v.at is not None:
                pos[v.id] = v.at
            elif direction == "below":
                gap = max(min_gap, rooms.get((ref.id, "bottom"), 0) + rooms.get((v.id, "top"), 0) + 4)
                pos[v.id] = (rx + (ref.w - v.w) / 2, ry + ref.h + gap)
            elif direction == "above":
                gap = max(min_gap, rooms.get((ref.id, "top"), 0) + rooms.get((v.id, "bottom"), 0) + 4)
                pos[v.id] = (rx + (ref.w - v.w) / 2, ry - v.h - gap)
            elif direction == "left":
                gap = max(min_gap, rooms.get((ref.id, "left"), 0) + rooms.get((v.id, "right"), 0) + 4)
                pos[v.id] = (rx - v.w - gap, ry + (ref.h - v.h) / 2)
            elif direction == "right":
                gap = max(min_gap, rooms.get((ref.id, "right"), 0) + rooms.get((v.id, "left"), 0) + 4)
                pos[v.id] = (rx + ref.w + gap, ry + (ref.h - v.h) / 2)
            else:
                failed.append(Unplaced(v.id, f"unknown relation direction {direction!r}"))
            pending.remove(v)
            progress = True
    failed += [Unplaced(v.id, "its reference view is missing or the relations are circular") for v in pending]
    return pos, failed


def _footprint(v: View, p: Point, rooms: dict[tuple[str, str], float]) -> Rect:
    return (
        p[0] - rooms.get((v.id, "left"), 0.0),
        p[1] - rooms.get((v.id, "top"), 0.0),
        p[0] + v.w + rooms.get((v.id, "right"), 0.0),
        p[1] + v.h + rooms.get((v.id, "bottom"), 0.0),
    )


# --- the items -----------------------------------------------------------------------------------------------------------


def _abs(vb: Rect, p: Point) -> Point:
    return vb[0] + p[0], vb[1] + p[1]


def _dim_ink(it: Item, vb: Rect, side: str, level: int) -> tuple[_Ink, Rect, tuple[Segment, ...]]:
    (ax, ay), (bx, by) = _abs(vb, it.anchors[0]), _abs(vb, it.anchors[1])
    off = _offset(level, it.h)
    segs: list[tuple[Segment, str]] = []
    if it.axis == "h":
        ly = vb[1] - off if side == "top" else vb[3] + off
        sign = -1.0 if side == "top" else 1.0
        lo, hi = min(ax, bx), max(ax, bx)
        over = ARROW_MM if hi - lo < it.w + 2 * ARROW_MM else 0.0
        line = (lo - over, ly, hi + over, ly)
        exts = [
            (x, y + sign * (-EXT_GAP_MM if sign < 0 else EXT_GAP_MM), x, ly + sign * EXT_OVER_MM)
            for x, y in ((ax, ay), (bx, by))
        ]
        cx = (ax + bx) / 2
        box = (cx - it.w / 2, ly - TEXT_GAP_MM - it.h, cx + it.w / 2, ly - TEXT_GAP_MM)
    else:
        lx = vb[0] - off if side == "left" else vb[2] + off
        sign = -1.0 if side == "left" else 1.0
        lo, hi = min(ay, by), max(ay, by)
        over = ARROW_MM if hi - lo < it.w + 2 * ARROW_MM else 0.0
        line = (lx, lo - over, lx, hi + over)
        exts = [
            (x + sign * (-EXT_GAP_MM if sign < 0 else EXT_GAP_MM), y, lx + sign * EXT_OVER_MM, y)
            for x, y in ((ax, ay), (bx, by))
        ]
        cy = (ay + by) / 2
        box = (lx - TEXT_GAP_MM - it.h, cy - it.w / 2, lx - TEXT_GAP_MM, cy + it.w / 2)
    segs = [(line, "dim"), *[(e, "ext") for e in exts]]
    ink = _Ink(it.id, texts=[box], segs=segs, skip_solid=it.view)
    return ink, box, tuple(s for s, _ in segs)


def _fixed_ink(it: Item) -> _Ink:
    kinds = it.fixed_kinds if len(it.fixed_kinds) == len(it.fixed_segments) else ("ext",) * len(it.fixed_segments)
    ink = _Ink(it.id, segs=list(zip(it.fixed_segments, kinds, strict=True)))
    if it.fixed_box:
        ink.texts.append(it.fixed_box)
    return ink


def _text_box_at(side: str, vb: Rect, dist: float, along: float, w: float, h: float) -> Rect:
    if side == "right":
        return (vb[2] + dist, along - h / 2, vb[2] + dist + w, along + h / 2)
    if side == "left":
        return (vb[0] - dist - w, along - h / 2, vb[0] - dist, along + h / 2)
    if side == "top":
        return (along - w / 2, vb[1] - dist - h, along + w / 2, vb[1] - dist)
    return (along - w / 2, vb[3] + dist, along + w / 2, vb[3] + dist + h)


def _attach(side: str, box: Rect) -> Point:
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    return {"right": (box[0], cy), "left": (box[2], cy), "top": (cx, box[3]), "bottom": (cx, box[1])}[side]


class _Run:
    def __init__(
        self, sheet: Sheet, views: Sequence[View], items: Sequence[Item], min_gap: float, area: Rect, first: Sequence[str] = ()
    ) -> None:
        self.first = frozenset(first)
        """The callouts a run before this one could not place: they choose their place before the other callouts."""
        self.sheet = sheet
        self.views = list(views)
        self.items = list(items)
        self.min_gap = min_gap
        self.area = area
        self.ctx = _Ctx(sheet)
        self.vpos: dict[str, ViewPos] = {}
        self.placed: list[Placed] = []
        self.unplaced: list[Unplaced] = []
        self.fits = True
        self.needed = (0.0, 0.0)
        self.stack_ext: dict[tuple[str, str], float] = {}
        self.dim_lines: dict[tuple[str, str], list[tuple[float, float, float, str]]] = {}
        """The dimension lines already on each side of each view: ``(from, to, distance from the edge of the view, owner)``."""

    # views
    def place_views(self) -> None:
        by_id = {v.id: v for v in self.views}
        rooms = _rooms(by_id, self.items)
        rel, failed = _arrange(self.views, rooms, self.min_gap)
        self.unplaced += failed
        bad = {u.id for u in failed}
        ax0, ay0, ax1, ay1 = self.area
        group = [v for v in self.views if v.id in rel and v.id not in bad]
        free = [v for v in self.views if v.relation is None and v.at is None and v.id not in rel and v.id not in bad]
        pinned = [v for v in group if v.at is not None]
        if group:
            fps = [_footprint(v, rel[v.id], rooms) for v in group]
            gx0, gy0 = min(f[0] for f in fps), min(f[1] for f in fps)
            gx1, gy1 = max(f[2] for f in fps), max(f[3] for f in fps)
            gw, gh = gx1 - gx0, gy1 - gy0
            if pinned:
                dx, dy = pinned[0].at[0] - rel[pinned[0].id][0], pinned[0].at[1] - rel[pinned[0].id][1]  # type: ignore[index]
            else:
                dx = ax0 - gx0 if free else ax0 + max(0.0, (ax1 - ax0 - gw) / 2) - gx0
                dy = ay0 + max(0.0, (ay1 - ay0 - gh) / 2) - gy0
            if gw > ax1 - ax0 + 0.01 or gh > ay1 - ay0 + 0.01:
                self.fits = False
                self.needed = (gw, gh)
            for v in group:
                x, y = rel[v.id][0] + dx, rel[v.id][1] + dy
                self.vpos[v.id] = ViewPos(v.id, x, y, v.w, v.h)
        for v in self.views:
            if v.id not in self.vpos and v.at is not None and v.relation is None:
                self.vpos[v.id] = ViewPos(v.id, v.at[0], v.at[1], v.w, v.h)
        for v in free:
            self._place_free(v, rooms)
        for v in self.views:
            vp = self.vpos.get(v.id)
            if vp is None:
                continue
            ink = _Ink(f"view:{v.id}", solids=[(vp.x, vp.y, vp.x + vp.w, vp.y + vp.h)])
            ink.segs = [((vp.x + s[0], vp.y + s[1], vp.x + s[2], vp.y + s[3]), "outline") for s in v.outline]
            self.ctx.commit(ink)
            if v.centre:
                self.ctx.commit(
                    _Ink(f"{CENTRE_OWNER}{v.id}", segs=[((vp.x + s[0], vp.y + s[1], vp.x + s[2], vp.y + s[3]), "centre") for s in v.centre])
                )

    def _place_free(self, v: View, rooms: dict[tuple[str, str], float]) -> None:
        ax0, ay0, ax1, ay1 = self.area
        taken = [_footprint(w, (p.x, p.y), rooms) for w in self.views if (p := self.vpos.get(w.id))]
        left, top = rooms.get((v.id, "left"), 0.0), rooms.get((v.id, "top"), 0.0)
        fw = v.w + left + rooms.get((v.id, "right"), 0.0)
        fh = v.h + top + rooms.get((v.id, "bottom"), 0.0)
        best: tuple[float, float, float] | None = None
        step = 5.0
        y = ay0
        while y + fh <= ay1 + 0.01:
            x = ax0
            while x + fw <= ax1 + 0.01:
                box = (x, y, x + fw, y + fh)
                if not any(_overlap(_inflate(box, 2.0), t) for t in taken):
                    cost = (ax1 - (x + fw)) + (y - ay0) * 0.5  # nearest the top right
                    if best is None or cost < best[0]:
                        best = (cost, x, y)
                x += step
            y += step
        if best is None:
            self.fits = False
            self.needed = (max(self.needed[0], fw), max(self.needed[1], fh))
            self.unplaced.append(
                Unplaced(v.id, f"no free room for the view ({fw:.0f} x {fh:.0f} mm with its annotations)")
            )
            return
        self.vpos[v.id] = ViewPos(v.id, best[1] + left, best[2] + top, v.w, v.h)

    def vbox(self, view_id: str | None) -> Rect | None:
        vp = self.vpos.get(view_id or "")
        return (vp.x, vp.y, vp.x + vp.w, vp.y + vp.h) if vp else None

    def fail(self, it: Item, why: dict[str, int] | str) -> None:
        if isinstance(why, dict):
            top = sorted(why.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
            why = (
                "no free place: " + "; ".join(f"{k} ({n} candidates)" for k, n in top) if top else "no candidate place"
            )
        self.unplaced.append(Unplaced(it.id, why))

    # items
    def place_items(self) -> None:
        for it in self.items:
            if it.fixed_box or it.fixed_segments:
                self.ctx.commit(_fixed_ink(it))
                self._register_fixed_dims(it)
        todo =[it for it in self.items if not (it.fixed_box or it.fixed_segments)]
        views = {v.id: v for v in self.views}
        for it in sorted(
            (i for i in todo if i.kind == "dim"), key=lambda i: (_span(i) if len(i.anchors) == 2 else 0.0, i.id)
        ):
            self._dim(it, views)
        for it in sorted((i for i in todo if i.kind == "angle"), key=lambda i: (i.view or "", i.id)):
            self._angle(it)
        for kind in ("section_label", "datum"):
            for it in sorted((i for i in todo if i.kind == kind), key=lambda i: (i.priority or 0, i.view or "", i.id)):
                self._label(it) if kind == "section_label" else self._datum(it)
        for it in sorted((i for i in todo if i.kind == "weld"), key=lambda i: (i.view or "", i.id)):
            self._weld(it)
        rest = [i for i in todo if i.kind in ("callout", "balloon", "leader")]
        for it in sorted(
            rest,
            key=lambda i: (
                0 if i.id in self.first else 1,
                i.priority if i.priority is not None else 3,
                i.view or "",
                i.anchors[0][1] if i.anchors else 0,
                i.anchors[0][0] if i.anchors else 0,
                i.id,
            ),
        ):
            self._leader(it) if it.kind == "leader" else self._callout(it)
        for it in sorted((i for i in todo if i.kind == "surface"), key=lambda i: i.id):
            self._corner(it)
        self._notes(sorted((i for i in todo if i.kind == "note"), key=lambda i: (i.order, i.id)))
        known = {"dim", "angle", "section_label", "datum", "weld", "callout", "balloon", "leader", "surface", "note"}
        for it in todo:
            if it.kind not in known:
                self.fail(it, f"unknown item kind {it.kind!r}")

    def _commit(
        self, it: Item, ink: _Ink, side: str | None, level: int, box: Rect, cost: float, data: tuple[float, ...] = ()
    ) -> None:
        self.ctx.commit(ink)
        self.placed.append(
            Placed(it.id, it.kind, it.view, side, level, box, tuple(s for s, _ in ink.segs), round(cost, 3), data)
        )

    def _register_fixed_dims(self, it: Item) -> None:
        """The dimension lines a pinned item draws are part of the stacks of the views they stand beside: what is placed after
        them keeps their order (the longer outside the shorter) and their gap."""
        if len(it.fixed_kinds) != len(it.fixed_segments):
            return
        for s, kind in zip(it.fixed_segments, it.fixed_kinds, strict=True):
            if kind != "dim":
                continue
            horizontal = abs(s[1] - s[3]) <= 0.05 < abs(s[0] - s[2])
            vertical = abs(s[0] - s[2]) <= 0.05 < abs(s[1] - s[3])
            if not (horizontal or vertical):
                continue
            lo, hi = (min(s[0], s[2]), max(s[0], s[2])) if horizontal else (min(s[1], s[3]), max(s[1], s[3]))
            best: tuple[float, tuple[str, str]] | None = None
            for v in self.views:
                vb = self.vbox(v.id)
                if vb is None:
                    continue
                if horizontal and min(hi, vb[2]) - max(lo, vb[0]) > STACK_TOL_MM:
                    found = ((vb[1] - s[1], "top") if s[1] < vb[1] else (s[1] - vb[3], "bottom")) if not vb[1] <= s[1] <= vb[3] else None
                elif vertical and min(hi, vb[3]) - max(lo, vb[1]) > STACK_TOL_MM:
                    found = ((vb[0] - s[0], "left") if s[0] < vb[0] else (s[0] - vb[2], "right")) if not vb[0] <= s[0] <= vb[2] else None
                else:
                    continue
                if found is not None and found[0] <= 120.0 and (best is None or found[0] < best[0]):
                    best = (found[0], (v.id, found[1]))
            if best is not None:
                self.dim_lines.setdefault(best[1], []).append((lo, hi, best[0], it.id))

    def _order_problem(self, key: tuple[str, str], lo: float, hi: float, dist: float) -> str | None:
        """Why a dimension line from ``lo`` to ``hi`` at ``dist`` from the view cannot stand there: it runs along a line that is
        already there and would be on the wrong side of it (the shorter stands nearest the part, the longer outside, ISO 129-1)
        or closer to it than ``MIN_PARALLEL_GAP_MM``."""
        for lo2, hi2, d2, owner in self.dim_lines.get(key, ()):
            if min(hi, hi2) - max(lo, lo2) <= STACK_TOL_MM:
                continue  # they share no run: they may stand on one line or apart
            span, span2 = hi - lo, hi2 - lo2
            if span > span2 + STACK_TOL_MM and dist < d2 + MIN_PARALLEL_GAP_MM:
                return f"stack order (longer than {owner}, it stands outside it)"
            if span < span2 - STACK_TOL_MM and dist + MIN_PARALLEL_GAP_MM > d2:
                return f"stack order (shorter than {owner}, it stands inside it)"
            if abs(span - span2) <= STACK_TOL_MM and abs(dist - d2) < MIN_PARALLEL_GAP_MM:
                return f"gap to {owner} below {MIN_PARALLEL_GAP_MM:g} mm"
        return None

    def _dim(self, it: Item, views: dict[str, View]) -> None:
        vb, v = self.vbox(it.view), views.get(it.view or "")
        if vb is None or v is None or len(it.anchors) != 2:
            self.fail(
                it,
                "its view is not placed or it has not two anchors"
                if vb is None or v is None
                else "a dimension needs two anchors",
            )
            return
        why: dict[str, int] = {}
        best: tuple[float, _Ink, Rect, str, int] | None = None
        order = sorted(_dim_sides(it), key=lambda s: (_ext_length(v, it, s), _dim_sides(it).index(s)))
        k = 0 if it.axis == "h" else 1
        lo, hi = sorted(_abs(vb, a)[k] for a in it.anchors)
        for pref, side in enumerate(order):
            for level in range(MAX_LEVELS):
                ink, box, _ = _dim_ink(it, vb, side, level)
                bad = self._order_problem((it.view or "", side), lo, hi, _offset(level, it.h))
                cost = 0.0
                if bad is None:
                    bad, cost = _judge(ink, self.ctx)
                if bad:
                    why[bad] = why.get(bad, 0) + 1
                    continue
                total = cost + level * 3.0 + pref * 2.0 + _ext_length(v, it, side) * 0.05
                if best is None or total < best[0]:
                    best = (total, ink, box, side, level)
                break
        if best is None:
            self.fail(it, why)
            return
        total, ink, box, side, level = best
        key = (it.view or "", side)
        self.stack_ext[key] = max(self.stack_ext.get(key, 0.0), _offset(level, it.h) + 2.0)
        self.dim_lines.setdefault(key, []).append((lo, hi, _offset(level, it.h), it.id))
        self._commit(it, ink, side, level, box, total)

    def _label(self, it: Item) -> None:
        vb = self.vbox(it.view)
        if vb is None:
            self.fail(it, "its view is not placed")
            return
        why: dict[str, int] = {}
        for side in it.sides or ("top",):
            if side not in ("top", "bottom"):
                continue
            base = self.stack_ext.get((it.view or "", side), 0.0)
            for gap in (3.0, base + 1.0):
                cx0 = (vb[0] + vb[2]) / 2
                for k in range(0, 41):
                    for sgn in (1, -1) if k else (1,):
                        cx = cx0 + sgn * k * 2.0
                        box = _text_box_at(side, vb, gap, cx, it.w, it.h)
                        ink = _Ink(it.id, texts=[box])
                        bad, cost = _judge(ink, self.ctx)
                        if bad:
                            why[bad] = why.get(bad, 0) + 1
                            continue
                        self._commit(it, ink, side, 0, box, cost + k)
                        return
        self.fail(it, why)

    def _datum(self, it: Item) -> None:
        vb = self.vbox(it.view)
        if vb is None or not it.anchors or it.normal is None:
            self.fail(it, "its view is not placed" if vb is None else "a datum needs an anchor and a normal")
            return
        nx, ny = float(it.normal[0]), float(it.normal[1])
        side = next((s for s in SIDES if _NORMALS[s] == (nx, ny)), None)
        if side is None:
            self.fail(it, "the normal of a datum must be one of the four axis directions")
            return
        ax, ay = _abs(vb, it.anchors[0])
        tx, ty = (1.0, 0.0) if ny else (0.0, 1.0)  # along the edge
        half = (it.h if ny else it.w) / 2
        ext = self.stack_ext.get((it.view or "", side), 0.0)
        why: dict[str, int] = {}
        best: tuple[float, _Ink, Rect, float] | None = None
        slides = sorted(
            {0.0, *[s * 2.0 * sg for s in range(1, int(it.slide // 2) + 1) for sg in (1, -1)]},
            key=lambda s: (abs(s), s),
        )
        for stem in (STEM_MIN_MM, STEM_MIN_MM + 3.0, ext + 3.0, ext + 9.0):
            for t in slides:
                px, py = ax + tx * t, ay + ty * t
                cx, cy = px + nx * (stem + half), py + ny * (stem + half)
                box = (cx - it.w / 2, cy - it.h / 2, cx + it.w / 2, cy + it.h / 2)
                ink = _Ink(
                    it.id, texts=[box], segs=[((px, py, px + nx * stem, py + ny * stem), "stem")], skip_solid=it.view
                )
                bad, cost = _judge(ink, self.ctx)
                if bad:
                    why[bad] = why.get(bad, 0) + 1
                    continue
                total = cost + stem * 0.3 + abs(t) * 0.4
                if best is None or total < best[0]:
                    best = (total, ink, box, stem)
            if best is not None:
                break
        if best is None:
            self.fail(it, why)
            return
        self._commit(it, best[1], side, 0, best[2], best[0], (best[3],))

    def _callout(self, it: Item) -> None:
        vb = self.vbox(it.view)
        if vb is None or not it.anchors:
            self.fail(it, "its view is not placed" if vb is None else "a callout needs an anchor")
            return
        ax, ay = _abs(vb, it.anchors[0])
        w, h = it.w, (it.w if it.kind == "balloon" else it.h)
        why: dict[str, int] = {}
        best: tuple[float, _Ink, Rect, str] | None = None
        side_pen = {s: i * 1.0 for i, s in enumerate(it.sides)} if it.sides else {}
        cands: list[tuple[float, str, Rect, Segment]] = []
        for side in it.sides or SIDES:
            ext = self.stack_ext.get((it.view or "", side), 0.0)
            base_along = ay if side in ("left", "right") else ax
            for dist in sorted({LEADER_MIN_MM, ext + 3.0, ext + 3.0 + (w if side in ("left", "right") else h)}):
                for k in range(0, 31):
                    for sgn in (1, -1) if k else (1,):
                        box = _text_box_at(side, vb, dist, base_along + sgn * k * 2.0, w, h)
                        att = _attach(side, box)
                        seg = (ax, ay, att[0], att[1])
                        base = (
                            math.hypot(att[0] - ax, att[1] - ay) * 0.2
                            + k * 0.6
                            + _inside_len(seg, vb) * 0.15
                            + side_pen.get(side, 0.0)
                        )
                        cands.append((base, side, box, seg))
        cands.sort(key=lambda c: (c[0], SIDES.index(c[1]), c[2]))  # cheapest first: the crossings only add to the base
        tries, best_cross = 0, 0.0
        budget = JUDGE_BUDGET  # full judgements once a place is known: the crossings rarely change the order much
        for base, side, box, seg in cands:
            if best is not None and (base >= best[0] or budget <= 0 or tries > 8 * JUDGE_BUDGET):
                break
            bad = _text_free(box, it.id, self.ctx)
            if bad:
                why[bad] = why.get(bad, 0) + 1
                continue
            ink = _Ink(it.id, texts=[box], segs=[(seg, "leader")], skip_solid=it.view)
            bad, cost = _judge(ink, self.ctx)
            if bad:
                why[bad] = why.get(bad, 0) + 1
                continue
            tries += 1
            if best is not None and best_cross < 10.0:
                budget -= 1
            if best is None or base + cost < best[0]:
                best = (base + cost, ink, box, side)
                best_cross = cost
        if best is None:
            self.fail(it, why)
            return
        self._commit(it, best[1], best[3], 0, best[2], best[0])

    def _choose(self, cands: list[tuple[float, _Ink, Any]], why: dict[str, int]) -> tuple[float, _Ink, Any] | None:
        """The cheapest candidate that can stand on the sheet. ``cands`` are ``(base cost, ink, what the caller keeps)``;
        the cheapest are judged first and the search ends once a place is known and the rest cost more."""
        order = sorted(range(len(cands)), key=lambda i: (cands[i][0], i))[:MAX_CANDIDATES]
        best: tuple[float, _Ink, Any] | None = None
        tries, best_cross, budget = 0, 0.0, JUDGE_BUDGET
        for i in order:
            base, ink, meta = cands[i]
            if best is not None and (base >= best[0] or budget <= 0 or tries > 8 * JUDGE_BUDGET):
                break
            bad = next(
                (
                    b
                    for r in ink.texts
                    if (b := _text_free(r, ink.owner, self.ctx, ink.skip_solid if ink.text_in_view else None, ink.clear, ink.strict))
                ),
                None,
            )
            if bad is None:
                bad, cost = _judge(ink, self.ctx)
            else:
                cost = 0.0
            if bad:
                why[bad] = why.get(bad, 0) + 1
                continue
            tries += 1
            if best is not None and best_cross < 10.0:
                budget -= 1
            if best is None or base + cost < best[0]:
                best = (base + cost, ink, meta)
                best_cross = cost
        return best

    def _leader(self, it: Item) -> None:
        """A leader with its text on a shelf (ISO 128-22): an oblique line from the tip on the part to an elbow, then a
        horizontal shelf the text stands on. The angles are the preferred ones (never along or across a centre line); the
        elbow goes outside the view as near as the other annotations leave room."""
        vb = self.vbox(it.view)
        if vb is None or not it.anchors:
            self.fail(it, "its view is not placed" if vb is None else "a leader needs an anchor (its tip)")
            return
        ax, ay = _abs(vb, it.anchors[0])
        circle = (*_abs(vb, (it.extra[0], it.extra[1])), it.extra[2]) if len(it.extra) == 3 else None
        why: dict[str, int] = {}
        best: tuple[float, _Ink, Any] | None = None
        for clear in (TEXT_CLEAR_MM, TEXT_CLEAR_MIN_MM):  # the text stands a text height clear of everything, or at least apart
            cands: list[tuple[float, _Ink, Any]] = []
            for angle in LEADER_ANGLES:
                rad = math.radians(angle)
                ux, uy = math.cos(rad), -math.sin(rad)
                if circle is not None:  # the leader of a hole points at its centre: its tip is where that ray meets the circle
                    ax, ay = circle[0] + ux * circle[2], circle[1] + uy * circle[2]
                reach = _ray_exit(vb, (ax, ay), (ux, uy))
                sx = 1.0 if ux > 0 else -1.0
                for step in range(14):
                    length = max(LEADER_MIN_MM, reach + 3.0) + step * 3.0
                    ex, ey = ax + ux * length, ay + uy * length
                    box = (ex, ey - it.h, ex + it.w, ey) if sx > 0 else (ex - it.w, ey - it.h, ex, ey)
                    seg = (ax, ay, ex, ey)
                    shelf = (ex, ey, ex + sx * it.w, ey)
                    base = length * 0.2 + abs((angle % 90) - 45) * 0.08 + _inside_len(seg, vb) * 0.15
                    # the elbow keeps a little room off the lines already there (a dimension line it would stand against)
                    judged = (ex - ELBOW_ROOM_MM, box[1], box[2], box[3]) if sx > 0 else (box[0], box[1], ex + ELBOW_ROOM_MM, box[3])
                    ink = _Ink(it.id, texts=[judged], segs=[(seg, "leader"), (shelf, "leader")], skip_solid=it.view, strict=True, clear=clear)
                    cands.append((base, ink, (ex, ey, box, "right" if sx > 0 else "left", ax, ay)))
            best = self._choose(cands, why)
            if best is not None:
                break
        if best is None and it.inside:  # no clean place outside: the text goes inside the view, on a clear area of it
            best = self._choose(self._inside_leader(it, vb, circle, (ax, ay)), why)
        if best is None:
            self.fail(it, why)
            return
        ex, ey, box, side, tx, ty = best[2]
        self._commit(it, best[1], side, 0, box, best[0], (ex, ey, tx, ty))

    def _inside_leader(self, it: Item, vb: Rect, circle: tuple[float, float, float] | None, tip: Point) -> list[tuple[float, _Ink, Any]]:
        """Leaders whose elbow and text stand INSIDE their own view, on an area that holds no line of the part, of a dimension
        or of another callout: the place when every side outside is closed by dimension lines (a plate dimensioned on all four
        sides)."""
        out: list[tuple[float, _Ink, Any]] = []
        ax, ay = tip
        margin = INSIDE_CLEAR_MM
        for angle in LEADER_ANGLES:
            rad = math.radians(angle)
            ux, uy = math.cos(rad), -math.sin(rad)
            if circle is not None:
                ax, ay = circle[0] + ux * circle[2], circle[1] + uy * circle[2]
            sx = 1.0 if ux > 0 else -1.0
            length = LEADER_MIN_MM
            while length <= INSIDE_MAX_MM:
                ex, ey = ax + ux * length, ay + uy * length
                box = (ex, ey - it.h, ex + it.w, ey) if sx > 0 else (ex - it.w, ey - it.h, ex, ey)
                length += INSIDE_STEP_MM
                if not _inside(box, _inflate(vb, -margin)) or not _inside((ex, ey, ex, ey), _inflate(vb, -margin)):
                    continue
                seg = (ax, ay, ex, ey)
                shelf = (ex, ey, ex + sx * it.w, ey)
                base = INSIDE_PENALTY + math.hypot(ex - ax, ey - ay) * 0.2 + abs((angle % 90) - 45) * 0.08
                judged = (ex - ELBOW_ROOM_MM, box[1], box[2], box[3]) if sx > 0 else (box[0], box[1], ex + ELBOW_ROOM_MM, box[3])
                ink = _Ink(it.id, texts=[judged], segs=[(seg, "leader"), (shelf, "leader")], skip_solid=it.view, text_in_view=True, strict=True, clear=INSIDE_CLEAR_MM)
                out.append((base, ink, (ex, ey, box, "right" if sx > 0 else "left", ax, ay)))
        return out

    def _angle(self, it: Item) -> None:
        """An angular dimension: an arc about the apex between its two rays (ISO 129-1), the text outside it; the arc takes
        the smallest of a few radii at which nothing else is crossed or covered."""
        vb = self.vbox(it.view)
        if vb is None or not it.anchors or len(it.extra) not in (2, 3):
            self.fail(it, "its view is not placed" if vb is None else "an angle needs its apex and the angles of its rays")
            return
        ax, ay = _abs(vb, it.anchors[0])
        a0, a1 = it.extra[:2]
        # a third number is the radius the arc must clear (the pitch circle and its holes: the arc stands outside them, never
        # over the bore): the radii are that one and the steps beyond it
        radii = ANGLE_RADII_MM if len(it.extra) == 2 else tuple(it.extra[2] + k * ANGLE_STEP_MM for k in range(len(ANGLE_RADII_MM)))
        span = (a1 - a0) % 360.0
        mid = math.radians((a0 + span / 2) % 360.0)
        cands: list[tuple[float, _Ink, Any]] = []
        for k, radius in enumerate(radii):
            n = max(4, int(span // 8))
            pts = [
                (ax + radius * math.cos(math.radians(a0 + span * i / n)), ay - radius * math.sin(math.radians(a0 + span * i / n)))
                for i in range(n + 1)
            ]
            segs: list[tuple[Segment, str]] = [((p[0], p[1], q[0], q[1]), "dim") for p, q in zip(pts, pts[1:], strict=False)]
            for a in (a0, a1):
                r2 = radius + EXT_OVER_MM
                segs.append(((ax, ay, ax + r2 * math.cos(math.radians(a)), ay - r2 * math.sin(math.radians(a))), "ext"))
            tx, ty = ax + (radius + 4.0) * math.cos(mid), ay - (radius + 4.0) * math.sin(mid)
            x0 = tx if math.cos(mid) >= 0 else tx - it.w
            box = (x0, ty - it.h + 1.2, x0 + it.w, ty + 1.2)
            cands.append((k * 2.0, _Ink(it.id, texts=[box], segs=segs, skip_solid=it.view, text_in_view=True), (radius, box)))
        why: dict[str, int] = {}
        best = self._choose(cands, why)
        if best is None:
            self.fail(it, why)
            return
        radius, box = best[2]
        self._commit(it, best[1], None, 0, box, best[0], (radius,))

    def _weld(self, it: Item) -> None:
        """A weld symbol: its reference line goes where the arrows are short and cross nothing. ``anchors`` are the ends of
        the arrows (the first one is the joint the symbol leads), ``extra`` the room the symbol takes about its anchor. The
        anchor is looked for around the first joint and, when there are several, around the middle of all of them."""
        vb = self.vbox(it.view)
        if vb is None or not it.anchors or len(it.extra) != 3:
            self.fail(it, "its view is not placed" if vb is None else "a weld needs the ends of its arrows and its size")
            return
        tips = [_abs(vb, a) for a in it.anchors]
        reach, up, down = it.extra
        lead = tips[0]
        middle = (sum(t[0] for t in tips) / len(tips), sum(t[1] for t in tips) / len(tips))
        origins = [lead] if len(tips) == 1 else [lead, middle]
        cands: list[tuple[float, _Ink, Any]] = []
        seen: set[tuple[int, int]] = set()
        for origin in origins:
            for n in range(WELD_DIRECTIONS):
                rad = 2 * math.pi * n / WELD_DIRECTIONS
                for d in WELD_DISTANCES_MM:
                    anchor = (origin[0] + d * math.cos(rad), origin[1] + d * math.sin(rad))
                    key = (round(anchor[0] * 2), round(anchor[1] * 2))
                    if key in seen:
                        continue
                    seen.add(key)
                    lengths = [math.hypot(t[0] - anchor[0], t[1] - anchor[1]) for t in tips]
                    if min(lengths) < LEADER_MIN_MM:
                        continue
                    sign = 1.0 if anchor[0] >= lead[0] else -1.0
                    x0, x1 = (anchor[0], anchor[0] + reach) if sign > 0 else (anchor[0] - reach, anchor[0])
                    rect = (x0, anchor[1] - up, x1, anchor[1] + down)
                    segs: list[tuple[Segment, str]] = [((t[0], t[1], anchor[0], anchor[1]), "arrow") for t in tips]
                    ink = _Ink(it.id, texts=[rect], segs=segs, skip_solid=it.view)
                    cands.append((sum(lengths) * 0.3, ink, anchor))
        why: dict[str, int] = {}
        best = self._choose(cands, why)
        if best is None:
            self.fail(it, why)
            return
        if best[0] >= ARROW_CROSS:
            self.fail(it, "every place it can stand has an arrow across a leader or another arrow")
            return
        anchor = best[2]
        self._commit(it, best[1], None, 0, best[1].texts[0], best[0], (anchor[0], anchor[1]))

    def _corner(self, it: Item) -> None:
        fx0, fy0, fx1, fy1 = self.sheet.frame
        pref = (fx1 - it.w - 5.0, fy0 + 5.0)
        found = self._scan([(it.w, it.h)], pref, it.id)
        if found is None:
            self.fail(it, "no free room in the frame for the symbol")
            return
        x, y, why = found
        box = (x, y, x + it.w, y + it.h)
        self._commit(it, _Ink(it.id, texts=[box]), None, 0, box, 0.0)

    def _scan(
        self, sizes: list[tuple[float, float]], pref: Point, owner: str, clear: float = 0.0
    ) -> tuple[float, float, dict[str, int]] | None:
        bw = max(s[0] for s in sizes)
        bh = sum(s[1] for s in sizes) + NOTE_GAP_MM * (len(sizes) - 1)
        fx0, fy0, fx1, fy1 = self.sheet.frame
        cands = []
        y = fy0 + 2.0
        while y + bh <= fy1 - 2.0:
            x = fx0 + 2.0
            while x + bw <= fx1 - 2.0:
                cands.append((abs(x - pref[0]) + abs(y - pref[1]), y, x))
                x += 4.0
            y += 4.0
        cands.sort()
        why: dict[str, int] = {}
        for _c, y, x in cands:
            ink = _Ink(owner, texts=[(x, y, x + bw, y + bh)], clear=clear)
            bad, _ = _judge(ink, self.ctx)
            if bad:
                why[bad] = why.get(bad, 0) + 1
                continue
            return x, y, why
        return None

    def _notes(self, notes: list[Item]) -> None:
        if not notes:
            return
        ax0, ay0, ax1, ay1 = self.area
        sizes = [(n.w, n.h) for n in notes]
        bh = sum(s[1] for s in sizes) + NOTE_GAP_MM * (len(sizes) - 1)
        found = None
        for clear in (TEXT_CLEAR_MM, TEXT_CLEAR_MIN_MM, 0.0):  # the block keeps a text height off the lines and texts, or less when the sheet has no room
            found = self._scan(sizes, (ax0, ay1 - bh), "notes", clear)
            if found is not None:
                break
        if found is None:
            for n in notes:
                self.fail(n, f"no free room for the notes block ({max(s[0] for s in sizes):.0f} x {bh:.0f} mm)")
            return
        x, y, _ = found
        bw = max(s[0] for s in sizes)
        self.ctx.commit(_Ink("notes", texts=[(x, y, x + bw, y + bh)]))
        for n in notes:
            box = (x, y, x + n.w, y + n.h)
            self.placed.append(Placed(n.id, n.kind, None, None, 0, box, (), 0.0))
            y += n.h + NOTE_GAP_MM


def _default_area(sheet: Sheet) -> Rect:
    x0, y0, x1, y1 = sheet.frame
    if sheet.title_block:
        y1 = min(y1, sheet.title_block[1] - 5.0)
    return (x0 + 5.0, y0 + 5.0, x1 - 5.0, y1)


def plan_layout(sheet: Sheet, views: Sequence[View], items: Sequence[Item], *, min_view_gap: float = 12.0) -> Plan:
    """Place every view and item of a sheet at once. See the module docstring."""
    area = sheet.area or _default_area(sheet)
    callouts = {i.id for i in items if i.kind in ("callout", "balloon", "leader")}

    def attempt(first: Sequence[str]) -> _Run:
        reserve_bottom = 0.0
        reserve_top = 0.0
        best: _Run | None = None
        for _ in range(3):
            run = _Run(
                sheet,
                views,
                items,
                min_view_gap,
                (area[0], area[1] + reserve_top, area[2], area[3] - reserve_bottom),
                first,
            )
            run.place_views()
            run.place_items()
            if best is None or len(run.unplaced) < len(best.unplaced):
                best = run
            lost = {u.id for u in run.unplaced}
            notes = [i for i in items if i.kind == "note" and i.id in lost]
            symbols = [i for i in items if i.kind == "surface" and i.id in lost]
            if not notes and not symbols:
                break
            if notes:
                reserve_bottom += sum(n.h for n in [i for i in items if i.kind == "note"]) + 4.0
            if symbols:
                reserve_top += max(s.h for s in symbols) + 4.0
        assert best is not None
        return best

    best = attempt(())
    first: tuple[str, ...] = ()
    for _ in range(3):  # a callout that found no clean place picks first: the ones placed before it may have taken its room
        stuck = tuple(u.id for u in best.unplaced if u.id in callouts and u.id not in first)
        if not stuck:
            break
        first = (*first, *stuck)
        again = attempt(first)
        if len(again.unplaced) >= len(best.unplaced):
            break
        best = again
    return Plan(
        views=tuple(sorted(best.vpos.values(), key=lambda v: v.id)),
        items=tuple(best.placed),
        unplaced=tuple(best.unplaced),
        fits=best.fits,
        needed=best.needed,
    )
