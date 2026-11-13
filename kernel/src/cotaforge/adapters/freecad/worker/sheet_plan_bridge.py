# ruff: noqa: E501
"""The bridge between a composed sheet and the global layout planner (no FreeCAD import, unit-testable on its own).

``sheet_plan_layout.plan_layout`` places views and annotations from their sizes and anchors; a composed sheet holds
the ink the items drew (``sheet_units``). This module reads that ink as planner items, runs the planner and says, for each
item the planner placed, the ``side`` and ``offset_mm`` the item takes to stand exactly there (every drawn item takes them:
a dimension with a side and an offset goes where it is told).

What it feeds the planner:

* the views at the place the CAD put them (pinned: the planner never moves a view of the sheet, it only judges the room);
* every linear ``dim`` item that asked for no place: its two anchors (the feet of its extension lines), its text size and
  its axis, read from its own ink. These are what the planner MOVES;
* the ink of every other item (callouts, datums, notes, the dimensions of ``overall``...) as pinned obstacles.

What it does not do (and says): an item that is not a linear dimension is not moved (callouts keep their own diagonal
leaders, which no ``side``/``offset_mm`` pair can reproduce from a planned position); a dimension whose ink cannot be read
is an obstacle, and one the planner finds no place for stays where it is and is reported as ``unplaced``.

A second run, with the views related as the sheet arranges them (not pinned), answers whether the views and the room their
annotations need fit the drawing area (``fits``, ``needed``): the caller steps the paper up when they do not.

Coordinates: the sheet and its units are page mm, y UP; the planner is y DOWN (``flip`` converts, the page height is the
axis)."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import sheet_collide
import sheet_plan_layout as pl
import sheet_units

Rect = tuple[float, float, float, float]
Segment = tuple[float, float, float, float]

PARALLEL_TOL_MM = 0.05
ROUND_MM = 0.5
FOOT_MIN_MM = 4.0  # an extension line reaches farther than the edges of an arrowhead (they are at most 3.5 mm long)
MOVABLE_TYPES = frozenset({"dim"})
CALLOUT_KINDS = frozenset({"diameter", "radius", "chamfer"})  # the ``dim`` kinds that are a leader and a text
ARROW_EDGE_MM = 3.6  # the edges of an arrowhead are shorter than this; a leader and a shelf are not
SIDE_OF = {"top": "above", "bottom": "below", "left": "left", "right": "right"}
"""What the planner calls a side of a view, and what an item calls it."""


@dataclass
class Prepared:
    """A sheet read for the planner."""

    sheet: pl.Sheet
    views: list[pl.View]
    items: list[pl.Item]
    movable: dict[str, dict[str, Any]]
    height: float
    boxes: dict[str, Rect]
    related: list[pl.View] = field(default_factory=list)
    unread: list[tuple[str, str]] = field(default_factory=list)
    """Dimensions that asked for no place but whose ink could not be read: left where they are (obstacles)."""
    tips: dict[str, tuple[float, float]] = field(default_factory=dict)
    """Where each movable leader touches the part (page mm, y up): its elbow is told as the vector from there."""


@dataclass(frozen=True)
class Outcome:
    placements: dict[str, dict[str, Any]]
    """The ``side`` and ``offset_mm`` of each item the planner placed, by item id."""
    unplaced: tuple[tuple[str, str], ...]
    fits: bool
    needed: tuple[float, float]


def flip(rect: Rect, height: float) -> Rect:
    return (rect[0], height - rect[3], rect[2], height - rect[1])


def _flip_segment(s: Segment, height: float) -> Segment:
    return (s[0], height - s[1], s[2], height - s[3])


def _union(rects: Sequence[Rect]) -> Rect:
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def _nearest_view(boxes: Mapping[str, Rect], spec: Mapping[str, Any], points: Sequence[tuple[float, float]]) -> str | None:
    """The view a dimension belongs to: the one it names, else the nearest to the feet of its extension lines."""
    named = spec.get("view")
    if named in boxes:
        return str(named)
    best: tuple[float, str] | None = None
    for name, b in boxes.items():
        d = sum(max(b[0] - x, 0.0, x - b[2]) ** 2 + max(b[1] - y, 0.0, y - b[3]) ** 2 for x, y in points)
        if best is None or d < best[0]:
            best = (d, name)
    return best[1] if best else None


def read_dimension(ink: sheet_collide.Ink) -> tuple[str, list[tuple[float, float]], Rect] | None:
    """A linear dimension read from its ink (page coordinates): ``(axis, feet, text box)`` or ``None``. ``axis`` is ``h``
    when the dimension line runs along x. The feet are the ends of the extension lines that are farther from the line."""
    if len(ink.dimension_lines) != 1 or not ink.texts:
        return None
    x0, y0, x1, y1 = ink.dimension_lines[0]
    if abs(y1 - y0) <= PARALLEL_TOL_MM < abs(x1 - x0):
        axis, along, level = "h", (min(x0, x1), max(x0, x1)), y0
    elif abs(x1 - x0) <= PARALLEL_TOL_MM < abs(y1 - y0):
        axis, along, level = "v", (min(y0, y1), max(y0, y1)), x0
    else:
        return None
    feet: list[tuple[float, float]] = []
    for s in ink.lines:
        if s == ink.dimension_lines[0]:
            continue
        if axis == "h" and abs(s[0] - s[2]) <= PARALLEL_TOL_MM and abs(s[1] - s[3]) > PARALLEL_TOL_MM:
            far = s[1] if abs(s[1] - level) > abs(s[3] - level) else s[3]
            if along[0] - 3.5 <= s[0] <= along[1] + 3.5 and min(s[1], s[3]) - 0.5 <= level <= max(s[1], s[3]) + 0.5 and abs(far - level) >= FOOT_MIN_MM:
                feet.append((s[0], far))
        elif axis == "v" and abs(s[1] - s[3]) <= PARALLEL_TOL_MM and abs(s[0] - s[2]) > PARALLEL_TOL_MM:
            far = s[0] if abs(s[0] - level) > abs(s[2] - level) else s[2]
            if along[0] - 3.5 <= s[1] <= along[1] + 3.5 and min(s[0], s[2]) - 0.5 <= level <= max(s[0], s[2]) + 0.5 and abs(far - level) >= FOOT_MIN_MM:
                feet.append((far, s[1]))
    if len(feet) != 2:
        return None
    box = _union([r for r, _ in ink.texts])
    return axis, feet, box


def read_callout(ink: sheet_collide.Ink) -> tuple[tuple[float, float], tuple[float, float], Rect] | None:
    """A leader with its text read from its ink (page coordinates): ``(tip, elbow, box)`` or ``None``. The shelf is the
    horizontal line under the text, the leader the longest other line; ``box`` is the shelf with the text over it."""
    if not ink.texts:
        return None
    text = _union([r for r, _ in ink.texts])
    lines = [s for s in ink.lines if math.hypot(s[2] - s[0], s[3] - s[1]) >= ARROW_EDGE_MM]
    shelves = [s for s in lines if abs(s[1] - s[3]) <= PARALLEL_TOL_MM and abs(s[1] - text[1]) <= 1.5]
    others = [s for s in lines if s not in shelves]
    if not shelves or not others:
        return None
    shelf = max(shelves, key=lambda s: abs(s[2] - s[0]))
    leader = max(others, key=lambda s: math.hypot(s[2] - s[0], s[3] - s[1]))
    ends = [(leader[0], leader[1]), (leader[2], leader[3])]
    near = [(math.dist(e, (shelf[0], shelf[1])), math.dist(e, (shelf[2], shelf[3])), e) for e in ends]
    elbow = min(ends, key=lambda e: min(math.dist(e, (shelf[0], shelf[1])), math.dist(e, (shelf[2], shelf[3]))))
    tip = ends[1] if elbow == ends[0] else ends[0]
    if min(near[0][0], near[0][1], near[1][0], near[1][1]) > 1.0:
        return None
    box = (min(shelf[0], shelf[2]), shelf[1], max(shelf[0], shelf[2]), max(text[3], shelf[1] + 1.0))
    return tip, elbow, box


def prepare(
    page: tuple[float, float],
    area: Rect,
    title_block: Rect | None,
    boxes: Mapping[str, Rect],
    units: Sequence[sheet_units.Unit],
    specs: Sequence[Mapping[str, Any]],
    parts_list: Rect | None = None,
    outline: Sequence[Segment] = (),
) -> Prepared:
    """Read a composed sheet. ``boxes`` are the views' rectangles (page mm, y up), ``units`` what each item drew, ``specs``
    the items as stored: a ``dim`` that carries neither ``side`` nor ``offset_mm`` is the planner's to move."""
    height = page[1]
    sheet = pl.Sheet(
        paper=page,
        frame=flip(area, height),
        title_block=flip(title_block, height) if title_block else None,
        parts_list=flip(parts_list, height) if parts_list else None,
    )
    flat = {name: flip(b, height) for name, b in boxes.items()}
    views = [
        pl.View(name, b[2] - b[0], b[3] - b[1], at=(b[0], b[1]), outline=_outline_in(boxes[name], flat[name], outline, height))
        for name, b in sorted(flat.items())
    ]
    by_id = {str(s.get("id")): s for s in specs}
    movable: dict[str, dict[str, Any]] = {}
    items: list[pl.Item] = []
    unread: list[tuple[str, str]] = []
    tips: dict[str, tuple[float, float]] = {}
    for unit in units:
        if unit.kind != sheet_units.KIND_ITEM or not unit.entities:
            continue
        spec = by_id.get(unit.key, {})
        ink = sheet_collide.ink_of(unit.key, unit.entities)
        free_place = (
            spec.get("type") in MOVABLE_TYPES
            and spec.get("side") is None
            and spec.get("offset_mm") is None
            and spec.get("elbow_mm") is None
        )
        wants_move = free_place and spec.get("kind", "distance") == "distance"
        wants_leader = free_place and spec.get("kind") in CALLOUT_KINDS
        if wants_leader:
            callout = read_callout(ink)
            if callout is not None:
                tip, elbow, box = callout
                view = _nearest_view(boxes, spec, [tip])
                if view is not None:
                    vb = flat[view]
                    w, h = box[2] - box[0], box[3] - box[1]
                    items.append(
                        pl.Item(
                            unit.key, "callout", view, w, h, ((tip[0] - vb[0], (height - tip[1]) - vb[1]),), sides=("top", "left", "right")
                        )
                    )
                    movable[unit.key] = dict(spec)
                    tips[unit.key] = tip
                    continue
            unread.append((unit.key, "its ink is not a leader the planner can read: left where it is"))
        if wants_move:
            read = read_dimension(ink)
            if read is not None:
                axis, feet, text = read
                view = _nearest_view(boxes, spec, feet)
                if view is not None:
                    vb = flat[view]
                    local = tuple((x - vb[0], (height - y) - vb[1]) for x, y in feet)
                    tw, th = text[2] - text[0], text[3] - text[1]
                    w, h = (tw, th) if axis == "h" else (th, tw)
                    items.append(pl.Item(unit.key, "dim", view, w, h, local, axis))  # type: ignore[arg-type]
                    movable[unit.key] = dict(spec)
                    continue
            unread.append((unit.key, "its ink is not a linear dimension the planner can read: left where it is"))
        if ink.texts:
            first = True
            for rect, _ in ink.texts:
                segs = tuple(_flip_segment(s, height) for s in ink.lines) if first else ()
                items.append(pl.Item(f"{unit.key}#{len(items)}", "callout", None, fixed_box=flip(rect, height), fixed_segments=segs))
                first = False
        elif ink.lines:
            items.append(pl.Item(f"{unit.key}#{len(items)}", "callout", None, fixed_segments=tuple(_flip_segment(s, height) for s in ink.lines)))
    related = _related(views)
    return Prepared(sheet, views, items, movable, height, flat, related, unread, tips)


def _outline_in(box: Rect, flat: Rect, segments: Sequence[Segment], height: float) -> tuple[Segment, ...]:
    """The visible lines that lie inside the view ``box`` (page, y up), as the planner reads them: local to the view, y down."""
    inside = [
        s
        for s in segments
        if all(box[0] - 0.5 <= x <= box[2] + 0.5 and box[1] - 0.5 <= y <= box[3] + 0.5 for x, y in ((s[0], s[1]), (s[2], s[3])))
    ]
    return tuple((s[0] - flat[0], (height - s[1]) - flat[1], s[2] - flat[0], (height - s[3]) - flat[1]) for s in inside)


def _related(views: Sequence[pl.View]) -> list[pl.View]:
    """The views as the sheet arranges them: each one centred on the one it lines up with (above, below, left, right of it);
    the others free. Used only to ask whether everything fits the paper."""
    out: list[pl.View] = []
    done: list[pl.View] = []
    for v in views:
        relation: tuple[str, str] | None = None
        if v.at is not None:
            for r in done:
                if r.at is None:
                    continue
                cx, cy = v.at[0] + v.w / 2, v.at[1] + v.h / 2
                rx, ry = r.at[0] + r.w / 2, r.at[1] + r.h / 2
                if abs(cx - rx) <= 1.0 and abs(cy - ry) > 1.0:
                    relation = (r.id, "below" if cy > ry else "above")
                elif abs(cy - ry) <= 1.0 and abs(cx - rx) > 1.0:
                    relation = (r.id, "right" if cx > rx else "left")
                if relation:
                    break
        out.append(pl.View(v.id, v.w, v.h, relation=relation))
        done.append(v)
    return out


def run(prepared: Prepared, *, min_view_gap: float = 12.0) -> Outcome:
    """Plan: the movable dimensions among the pinned obstacles, and whether everything fits the paper."""
    pinned = pl.plan_layout(prepared.sheet, prepared.views, prepared.items, min_view_gap=min_view_gap)
    moving = [i for i in prepared.items if i.id in prepared.movable]
    room = pl.plan_layout(prepared.sheet, prepared.related, moving, min_view_gap=min_view_gap)
    positions = {v.id: v for v in pinned.views}
    placements: dict[str, dict[str, Any]] = {}
    for p in pinned.items:
        if p.id not in prepared.movable or p.side is None or p.view not in positions:
            continue
        vp = positions[p.view]
        if p.kind == "callout":  # a leader: where it ends, as the vector from its tip (the shelf is the box's lower edge)
            tip = prepared.tips[p.id]
            at = p.segments[0]
            box = p.text_box
            elbow = (box[0] if p.side == "top" else at[2], prepared.height - box[3])  # a text above its leader starts at the elbow
            placements[p.id] = {"elbow_mm": [round(elbow[0] - tip[0], 1), round(elbow[1] - tip[1], 1)]}
            continue
        line = p.segments[0]
        distance = {
            "top": vp.y - line[1],
            "bottom": line[1] - (vp.y + vp.h),
            "left": vp.x - line[0],
            "right": line[0] - (vp.x + vp.w),
        }[p.side]
        placements[p.id] = {"side": SIDE_OF[p.side], "offset_mm": round(max(distance, 0.0) / ROUND_MM) * ROUND_MM}
    lost = [(u.id, u.reason) for u in pinned.unplaced if u.id in prepared.movable]
    lost += [(u.id, u.reason) for u in room.unplaced if u.id not in {x for x, _ in lost} and u.id in prepared.movable]
    lost += prepared.unread
    return Outcome(placements, tuple(lost), room.fits, room.needed)
