# ruff: noqa: E501
"""The bridge between a composed sheet and the global layout planner (no FreeCAD import, unit-testable on its own).

``sheet_plan_layout.plan_layout`` places views and annotations from their sizes and anchors; a composed sheet holds
the ink the items drew (``sheet_units``). This module reads that ink as planner items, runs the planner and says, for each
item the planner placed, what the item takes to stand exactly there (a dimension with a side and an offset goes where it is told).

What it feeds the planner:

* the views at the place the CAD put them (pinned: the planner never moves a view of the sheet, it only judges the room);
* every item that asked for no place (none of ``PLACE_KEYS``) and is of a type the planner places, read from its own ink.
  These are what the planner MOVES, and what each is told:

  - ``dim`` distance: ``side`` and ``offset_mm``; ``dim`` angle: ``offset_mm`` (the radius of the arc);
  - ``dim`` diameter, radius, chamfer: ``elbow_mm`` (from the tip, which the geometry fixes);
  - ``hole_dim``: ``leader_mm`` (tip and elbow, absolute: the tip goes round the circle of the hole, so the leader points at
    its centre);
  - ``datum`` of a face: ``side`` and ``offset_mm`` (the stem);
  - ``weld``: ``anchor_at`` (where the arrow lines meet the reference line; the drawer ends each arrow on its seam);

* the ink of every other item as pinned obstacles, each line with its kind (a leader, an arrow, a dimension line), so what is
  placed crosses them as little as it crosses a placed one. ``FIXED_REASONS`` says why the types that stay (overall,
  datum_dims, notes, frames, finish symbols, balloons, section labels) are not moved.

What it does not do (and says): an item whose ink cannot be read as one of those (a weld drawn in two views is two symbols, a
callout of several leaders) is an obstacle and is listed as unplaced; one the planner finds no place for stays where it is and
is reported as ``unplaced``.

A second run, with the views related as the sheet arranges them (not pinned), answers whether the views and the room their
annotations need fit the drawing area (``fits``, ``needed``): the caller steps the paper up when they do not.

Coordinates: the sheet and its units are page mm, y UP; the planner is y DOWN (``flip`` converts, the page height is the
axis)."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import sheet_collide
import sheet_model as model
import sheet_plan_layout as pl
import sheet_units

Rect = tuple[float, float, float, float]
Segment = tuple[float, float, float, float]

PARALLEL_TOL_MM = 0.05
ROUND_MM = 0.5
FOOT_MIN_MM = 4.0  # an extension line reaches farther than the edges of an arrowhead (they are at most 3.5 mm long)
MOVABLE_TYPES = frozenset({"dim", "hole_dim", "datum", "weld"})
PLACE_KEYS = ("side", "offset_mm", "elbow_mm", "leader_mm", "anchor_at")
"""The fields that say where an item stands: an item that has one was placed by its author (or by the planner before) and stays."""
LEADER_TYPES = frozenset({"dim", "hole_dim", "balloons", "gdt", "finish"})
REF_MIN_MM = 14.0  # the reference line of a weld symbol (``items_weld.MIN_REF_MM``)
ARROW_MIN_MM = 6.0  # an arrow line is longer than this; a flag pole or an arrowhead is not
LEADER_LINE_MM = 6.0
CENTRE_MARK_OVER_MM = 2.0  # a centre mark reaches this far past its circle (``sheet_draw.centre_mark``)
TIP_ON_CIRCLE_MM = 0.6
FIXED_REASONS = {
    "overall": "its width, height and depth are one item with one stack distance: they stand outermost, as the first lines of the stacks, and the planner places everything else around them",
    "datum_dims": "its chains and ordinates hang from the datum edges of the part and one item draws all of them: they are the frame of the sheet the others are placed against",
    "note": "the notes form one numbered block that stands last in the free corner the rest leaves",
    "gdt": "a feature control frame hangs from the callout or the face it controls and moves with it",
    "finish": "the finish symbol stands on the face it marks (the general one in the corner of the sheet)",
    "balloons": "a balloon leader ends on its part: the planner keeps clear of them, a balloon is not moved",
    "section": "a section label and its cutting-plane line belong to the view the section makes",
}
"""Item types that stay where their own drawer put them, and why: for the planner they are obstacles."""
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


def read_angle(entities: Sequence[model.Entity]) -> tuple[tuple[float, float], tuple[float, float], Rect] | None:
    """An angular dimension read from its entities (page coordinates, y up): ``(apex, (a0, a1), text box)`` or ``None``.
    The angles are those of the two rays, in degrees, counter-clockwise from ``a0`` to ``a1`` as the arc runs."""
    arcs = [e for e in entities if isinstance(e, model.Arc) and e.layer == model.DIMENSIONS]
    texts = [sheet_collide.text_box(e) for e in entities if isinstance(e, model.Text)]
    if len(arcs) != 1 or not texts:
        return None
    arc = arcs[0]
    return (arc.cx, arc.cy), (arc.start, arc.end), _union(texts)


def read_datum(ink: sheet_collide.Ink, entities: Sequence[model.Entity]) -> tuple[tuple[float, float], tuple[float, float], Rect] | None:
    """The symbol of a datum read from its entities (page coordinates, y up): ``(tip, normal, frame)``. The tip is the apex of
    the triangle on the part, ``normal`` the unit vector (along x or y) from there to its base, ``frame`` the lettered box."""
    triangles = [e for e in entities if isinstance(e, model.Solid) and len(e.points) == 3]
    if len(triangles) != 1 or len(ink.frames) != 1:
        return None
    tip, b1, b2 = triangles[0].points
    base = ((b1[0] + b2[0]) / 2, (b1[1] + b2[1]) / 2)
    dx, dy = base[0] - tip[0], base[1] - tip[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return None
    nx, ny = dx / length, dy / length
    if max(abs(nx), abs(ny)) < 0.99:
        return None
    return tip, (float(round(nx)), float(round(ny))), ink.frames[0]


def read_weld(entities: Sequence[model.Entity]) -> tuple[list[tuple[float, float]], tuple[float, float, float]] | None:
    """A weld symbol read from its entities (page coordinates, y up): ``(arrow ends, (reach, up, down))`` or ``None``. The
    reference line is the long horizontal line; the arrows are the lines that leave one of its ends. ``reach`` is how far the
    symbol runs from that end along the line (the other way for a line that ends there), ``up`` / ``down`` its extent over and
    under the line. Only a symbol with ONE reference line is read (an item drawn in two views is two symbols)."""
    lines = [e for e in entities if isinstance(e, model.Line) and e.layer == model.DIMENSIONS]
    heads = [e for e in entities if isinstance(e, model.Solid) and len(e.points) == 3 and e.layer == model.DIMENSIONS]
    arrow_lines: list[tuple[model.Line, tuple[float, float]]] = []  # an arrow line ends in an arrowhead: its tip
    for e in lines:
        if math.hypot(e.x2 - e.x1, e.y2 - e.y1) < ARROW_MIN_MM:
            continue
        for h in heads:
            if math.dist(h.points[0], (e.x1, e.y1)) <= 0.05 or math.dist(h.points[0], (e.x2, e.y2)) <= 0.05:
                arrow_lines.append((e, h.points[0]))
                break
    taken = {id(e) for e, _ in arrow_lines}
    refs = [e for e in lines if id(e) not in taken and abs(e.y1 - e.y2) <= PARALLEL_TOL_MM and abs(e.x2 - e.x1) >= REF_MIN_MM]
    if len(refs) != 1 or not arrow_lines:
        return None
    ref = refs[0]
    ends = [(ref.x1, ref.y1), (ref.x2, ref.y2)]
    joints = {
        i
        for e, tip in arrow_lines
        for i, end in enumerate(ends)
        if any(math.dist(p, end) <= 0.05 and math.dist(p, tip) > 0.05 for p in ((e.x1, e.y1), (e.x2, e.y2)))
    }
    if len(joints) != 1 or len({i for i in joints}) != 1:
        return None
    joint = joints.pop()
    anchor = ends[joint]
    tips = [tip for _, tip in arrow_lines]
    tip_ids = {id(h) for h in heads if any(math.dist(h.points[0], t) <= 0.05 for t in tips)}
    rest = [e for e in entities if id(e) not in tip_ids and id(e) not in taken]
    box = sheet_collide.extent(sheet_collide.ink_of("", rest))
    sign = 1.0 if joint == 0 else -1.0
    reach = (box[2] - anchor[0]) if sign > 0 else (anchor[0] - box[0])
    return tips, (reach + 0.5, box[3] - anchor[1] + 0.5, anchor[1] - box[1] + 0.5)


def item_type_of(spec: Mapping[str, Any], unit: sheet_units.Unit) -> str:
    return str(spec.get("type") or unit.item_type)


def read_hole(entities: Sequence[model.Entity], tip: tuple[float, float]) -> tuple[tuple[float, float], float] | None:
    """The circle of the hole a callout leads to, from the centre mark the same item drew (page coordinates, y up): ``(centre,
    radius)`` when the tip of the leader is on a circle the cross of a centre mark stands at (the mark reaches 2 mm past it)."""
    marks = [e for e in entities if isinstance(e, model.Line) and e.layer == model.AXES]
    for h in (e for e in marks if abs(e.y1 - e.y2) <= PARALLEL_TOL_MM < abs(e.x2 - e.x1)):
        cx, cy, half = (h.x1 + h.x2) / 2, h.y1, abs(h.x2 - h.x1) / 2
        for v in (e for e in marks if abs(e.x1 - e.x2) <= PARALLEL_TOL_MM < abs(e.y2 - e.y1)):
            if abs(v.x1 - cx) <= PARALLEL_TOL_MM and abs((v.y1 + v.y2) / 2 - cy) <= PARALLEL_TOL_MM and abs(half - abs(v.y2 - v.y1) / 2) <= PARALLEL_TOL_MM:
                radius = half - CENTRE_MARK_OVER_MM
                if radius > 0 and abs(math.dist(tip, (cx, cy)) - radius) <= TIP_ON_CIRCLE_MM:
                    return (cx, cy), radius
    return None


def _kind_of_lines(item_type: str, ink: sheet_collide.Ink) -> tuple[str, ...]:
    """The planner's kind of each line of a pinned item: a leader (or the arrow of a weld) costs a crossing with another
    leader, a dimension line with a leader less, an extension line almost nothing."""
    main = set(ink.dimension_lines)
    out: list[str] = []
    for s in ink.lines:
        long = math.hypot(s[2] - s[0], s[3] - s[1]) >= ARROW_EDGE_MM
        if s in main:
            out.append("dim")
        elif item_type == "weld" and long:
            out.append("arrow")
        elif item_type == "datum" and long:
            out.append("stem")
        elif item_type in LEADER_TYPES and long:
            out.append("leader")
        else:
            out.append("ext")
    return tuple(out)


class _Reader:
    """The views as the planner has them (page y up in ``boxes``, y down in ``flat``)."""

    def __init__(self, boxes: Mapping[str, Rect], flat: Mapping[str, Rect], height: float) -> None:
        self.boxes, self.flat, self.height = boxes, flat, height

    def local(self, view: str, p: tuple[float, float]) -> tuple[float, float]:
        vb = self.flat[view]
        return (p[0] - vb[0], (self.height - p[1]) - vb[1])


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
    the items as stored: an item that carries none of the keys that say where it stands (``side``, ``offset_mm``, ``elbow_mm``,
    ``leader_mm``, ``anchor_at``) is the planner's to move, when it is of a kind the planner places."""
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
    reader = _Reader(boxes, flat, height)
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
        item_type = str(spec.get("type") or unit.item_type)
        free_place = item_type in MOVABLE_TYPES and not any(spec.get(k) is not None for k in PLACE_KEYS)
        kind = _wants_kind(item_type, spec) if free_place else ""
        if kind:
            read = _movable(unit, spec, ink, kind, reader, tips)
            if read is not None:
                items.append(read)
                movable[unit.key] = dict(spec)
                continue
            unread.append((unit.key, f"its ink is not a {_WORDS[kind]} the planner can read: left where it is"))
        kinds = _kind_of_lines(item_type, ink)
        if ink.texts:
            first = True
            for rect, _ in ink.texts:
                segs = tuple(_flip_segment(s, height) for s in ink.lines) if first else ()
                items.append(
                    pl.Item(f"{unit.key}#{len(items)}", "callout", None, fixed_box=flip(rect, height), fixed_segments=segs, fixed_kinds=kinds if first else ())
                )
                first = False
        elif ink.lines:
            items.append(
                pl.Item(f"{unit.key}#{len(items)}", "callout", None, fixed_segments=tuple(_flip_segment(s, height) for s in ink.lines), fixed_kinds=kinds)
            )
    related = _related(views)
    return Prepared(sheet, views, items, movable, height, flat, related, unread, tips)


_WORDS = {
    "distance": "linear dimension",
    "leader": "leader",
    "angle": "angular dimension",
    "datum": "datum symbol",
    "weld": "weld symbol",
}


def _wants_kind(item_type: str, spec: Mapping[str, Any]) -> str:
    """What the planner places an item of this type as (``""``: it stays where its own drawer put it, see ``FIXED_REASONS``)."""
    if item_type == "dim":
        kind = spec.get("kind", "distance")
        return "leader" if kind in CALLOUT_KINDS else str(kind) if kind in ("distance", "angle") else ""
    if item_type == "datum":
        return "" if "on" in spec else "datum"  # the datum of an axis stands on the shelf of its callout by design
    return {"hole_dim": "leader", "weld": "weld"}.get(item_type, "")


def _movable(
    unit: sheet_units.Unit,
    spec: Mapping[str, Any],
    ink: sheet_collide.Ink,
    kind: str,
    reader: _Reader,
    tips: dict[str, tuple[float, float]],
) -> pl.Item | None:
    """The planner item of one unit, or ``None`` when its ink is not what the planner knows how to place."""
    boxes = reader.boxes
    if kind == "leader":
        callout = read_callout(ink)
        if callout is None or _leader_lines(ink) != 1:
            return None
        tip, _elbow, box = callout
        view = _nearest_view(boxes, spec, [tip])
        if view is None:
            return None
        tips[unit.key] = tip
        circle = read_hole(unit.entities, tip) if item_type_of(spec, unit) == "hole_dim" else None
        extra: tuple[float, ...] = ()
        if circle is not None:
            centre = reader.local(view, circle[0])
            extra = (centre[0], centre[1], circle[1])
        return pl.Item(unit.key, "leader", view, box[2] - box[0], box[3] - box[1], (reader.local(view, tip),), extra=extra)
    if kind == "distance":
        read = read_dimension(ink)
        if read is None:
            return None
        axis, feet, text = read
        view = _nearest_view(boxes, spec, feet)
        if view is None:
            return None
        local = tuple(reader.local(view, f) for f in feet)
        tw, th = text[2] - text[0], text[3] - text[1]
        w, h = (tw, th) if axis == "h" else (th, tw)
        return pl.Item(unit.key, "dim", view, w, h, local, axis)  # type: ignore[arg-type]
    if kind == "angle":
        angle = read_angle(unit.entities)
        if angle is None:
            return None
        apex, (a0, a1), text = angle
        view = _nearest_view(boxes, spec, [apex])
        if view is None:
            return None
        return pl.Item(unit.key, "angle", view, text[2] - text[0], text[3] - text[1], (reader.local(view, apex),), extra=(a0, a1))
    if kind == "datum":
        datum = read_datum(ink, unit.entities)
        if datum is None:
            return None
        tip, normal, frame = datum
        view = _nearest_view(boxes, spec, [tip])
        if view is None:
            return None
        return pl.Item(
            unit.key, "datum", view, frame[2] - frame[0], frame[3] - frame[1], (reader.local(view, tip),), normal=(normal[0], -normal[1])
        )
    if kind == "weld":
        weld = read_weld(unit.entities)
        if weld is None:
            return None
        ends, size = weld
        view = _nearest_view(boxes, spec, [ends[0]])
        if view is None:
            return None
        tips[unit.key] = ends[0]
        return pl.Item(unit.key, "weld", view, 0.0, 0.0, tuple(reader.local(view, e) for e in ends), extra=size)
    return None


def _leader_lines(ink: sheet_collide.Ink) -> int:
    """How many leaders an item drew: lines that are neither horizontal (a shelf) nor short (an arrowhead)."""
    return sum(
        1
        for s in ink.lines
        if math.hypot(s[2] - s[0], s[3] - s[1]) >= LEADER_LINE_MM and abs(s[1] - s[3]) > PARALLEL_TOL_MM
    )


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
        if p.id not in prepared.movable or p.view not in positions:
            continue
        found = _placement(prepared, p, positions[p.view])
        if found:
            placements[p.id] = found
    lost = [(u.id, u.reason) for u in pinned.unplaced if u.id in prepared.movable]
    kinds = {i.id: i.kind for i in moving}
    lost += [  # the room run only asks whether the views and their dimensions fit: the other items are judged where the views are
        (u.id, u.reason) for u in room.unplaced if u.id not in {x for x, _ in lost} and kinds.get(u.id) == "dim"
    ]
    lost += prepared.unread
    return Outcome(placements, tuple(lost), room.fits, room.needed)


def _placement(prepared: Prepared, p: pl.Placed, vp: pl.ViewPos) -> dict[str, Any]:
    """What an item says to stand where the planner put it (page mm, y up): its ``side`` and ``offset_mm``, the ``elbow_mm`` of
    its leader (from its tip), the absolute ``leader_mm`` of a hole callout, the ``anchor_at`` of a weld symbol."""
    height = prepared.height
    spec = prepared.movable[p.id]
    if p.kind == "leader":
        tip = prepared.tips[p.id]
        ex, ey = p.data[0], height - p.data[1]
        if spec.get("type") == "hole_dim":  # which hole it leaves is the drawer's choice: it is told the tip too
            tx, ty = (p.data[2], height - p.data[3]) if len(p.data) == 4 else tip
            return {"leader_mm": [round(tx, 2), round(ty, 2), round(ex, 2), round(ey, 2)]}
        return {"elbow_mm": [round(ex - tip[0], 1), round(ey - tip[1], 1)]}
    if p.kind == "angle":
        return {"offset_mm": round(p.data[0] / ROUND_MM) * ROUND_MM}
    if p.kind == "weld":
        return {"anchor_at": [round(p.data[0], 2), round(height - p.data[1], 2)]}
    if p.kind == "datum" and p.side is not None:
        return {"side": SIDE_OF[p.side], "offset_mm": round(max(p.data[0], 0.0) / ROUND_MM) * ROUND_MM}
    if p.side is None:
        return {}
    line = p.segments[0]
    distance = {
        "top": vp.y - line[1],
        "bottom": line[1] - (vp.y + vp.h),
        "left": vp.x - line[0],
        "right": line[0] - (vp.x + vp.w),
    }[p.side]
    return {"side": SIDE_OF[p.side], "offset_mm": round(max(distance, 0.0) / ROUND_MM) * ROUND_MM}
