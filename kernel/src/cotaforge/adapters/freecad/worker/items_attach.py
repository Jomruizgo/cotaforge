"""Where a label attaches to a face (no FreeCAD import): the datum triangle, the feature control frame and the
surface-finish symbol all point at a face of the part from outside it. This module finds that point on the sheet -- the
view that shows the face, the place on its outline and the direction away from the part -- and looks for free room for
the label along that direction. It is not an item type (it exports no ``ITEMS``).

* A plane is attached to in a view that shows it EDGE-ON (its normal lies in the view plane): the point is the middle of
  its trace and the direction is its outward normal on the page.
* A cylinder is attached to as a SURFACE (the silhouette in a view that shows its axis, the circle in a view along it).
  Its AXIS is not attached to here: a datum or a frame for an axis hangs from the callout of its dimension.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass

from items_common import VIEW_ORDER, why_not
from sheet_collide import segments_of
from sheet_context import Context, ItemError, Point, Vec3, ViewInfo, cross, dot, scale, sub, unit
from sheet_model import Entity

Rect = tuple[float, float, float, float]
CARDINAL: dict[str, tuple[float, float]] = {
    "right": (1.0, 0.0),
    "left": (-1.0, 0.0),
    "above": (0.0, 1.0),
    "below": (0.0, -1.0),
}
FIRST_DISTANCE_MM = 9.0  # the label's anchor is this far from the feature, then farther while the place is taken
NUDGE_MM = 6.0
TRIES = 12
SLIDES = (0.5, 0.25, 0.75, 0.12, 0.88, 0.06, 0.94, 0.38, 0.62)  # fractions of the trace of a plane a label may touch (the last ones clear the extension lines of a dimension along the whole edge)
SLIDE_TRIES = 5  # distances tried at each place along the trace before the next one
TRACE_MARGIN_MM = 2.0  # a label touches a plane at least this far from the end of its trace


@dataclass
class Attach:
    """The place a label attaches to: ``tip`` on the feature (page mm), ``normal`` the unit direction away from the
    part, ``kind`` ``plane`` / ``surface``."""

    view: ViewInfo
    tip: Point
    normal: tuple[float, float]
    kind: str
    circle: tuple[Point, float] | None = None  # a cylinder seen along its axis: any direction away from it will do
    trace: tuple[Point, Point] | None = None  # a plane seen edge-on: its segment on the page (the label may slide)
    band: tuple[Point, Point] | None = None  # a cylinder seen across its axis: the two ends of its silhouette line

    def tip_towards(self, direction: tuple[float, float]) -> Point:
        """The point the label touches when it leaves along ``direction``: on the circle's edge, else the fixed tip."""
        if self.circle is None:
            return self.tip
        (cx, cy), radius = self.circle
        return (cx + direction[0] * radius, cy + direction[1] * radius)

    def tips(self) -> list[Point]:
        """Where the label may touch a plane: its own tip first, then 1/4, 3/4 and near the ends of its trace (so a
        leader can clear the text of a dimension that stands at the middle). A feature with no trace has one place."""
        if self.trace is None:
            return [self.tip]
        (x0, y0), (x1, y1) = self.trace
        return [self.tip, *((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f) for f in SLIDES[1:])]


def _unit2(v: tuple[float, float]) -> tuple[float, float]:
    length = math.hypot(v[0], v[1])
    if length < 1e-9:
        return (1.0, 0.0)
    return (v[0] / length, v[1] / length)


def _side_sign(vector: tuple[float, float], side: str | None, away: tuple[float, float]) -> float:
    """+1 or -1: which way along ``vector`` the label goes -- the side asked for, else away from the view's middle."""
    wanted = CARDINAL[side] if side else away
    return -1.0 if vector[0] * wanted[0] + vector[1] * wanted[1] < 0 else 1.0


def _views(ctx: Context, wanted: str | None) -> list[ViewInfo]:
    if wanted is not None:
        return [ctx.view(wanted)]  # a standard view or a section view (A-A)
    return [ctx.views[n] for n in VIEW_ORDER if n in ctx.views]


def _trace(
    ctx: Context, face_id: str, view: ViewInfo, tip: Point, out: tuple[float, float]
) -> tuple[Point, Point] | None:
    """The segment a plane seen edge-on is on the page: along the line through ``tip`` perpendicular to its outward
    direction, as far as the outline of the face reaches (less ``TRACE_MARGIN_MM`` at each end), ``None`` when the
    geometry cannot say."""
    try:
        loops = ctx.geometry.face_outline(face_id, view.centre, view.xdir, view.direction)
    except ItemError:
        return None
    along = (-out[1], out[0])
    reach = [
        ((view.x + p[0] * view.scale - tip[0]) * along[0] + (view.y + p[1] * view.scale - tip[1]) * along[1])
        for loop in loops
        for p in loop
    ]
    if not reach:
        return None
    low, high = min(reach) + TRACE_MARGIN_MM, max(reach) - TRACE_MARGIN_MM
    if high - low < 1.0:
        return None
    return (tip[0] + along[0] * low, tip[1] + along[1] * low), (tip[0] + along[0] * high, tip[1] + along[1] * high)


def _plane(ctx: Context, face_id: str, wanted: str | None, normal: tuple[float, float, float]) -> Attach:
    face = ctx.geometry.face(face_id)
    for view in _views(ctx, wanted):
        if view.shows_axis_along(normal):
            tip, out = view.project(face.point), _unit2(view.axis(unit(normal)))
            return Attach(view, tip, out, "plane", trace=_trace(ctx, face_id, view, tip, out))
    where = f"the {wanted} view" if wanted else "the front, top or right view"
    msg = (
        f"{face_id} is seen face-on in {where}: attach to a face that shows edge-on (its normal across the view), "
        "or ask for another view"
    )
    raise ItemError(msg)


def attach_face(ctx: Context, face_id: str, wanted: str | None, side: str | None) -> Attach:
    """Where a label for ``face_id`` attaches: on its outline (a plane seen edge-on) or on its surface (a cylinder)."""
    att = _attach_face(ctx, face_id, wanted, side)
    ctx.used_view(att.view.name)
    return att


def _attach_face(ctx: Context, face_id: str, wanted: str | None, side: str | None) -> Attach:
    face = ctx.geometry.face(face_id)
    if face.kind == "plane" and face.normal is not None:
        att = _plane(ctx, face_id, wanted, face.normal)
        if side is not None and CARDINAL[side][0] * att.normal[0] + CARDINAL[side][1] * att.normal[1] < 0.5:
            outward = (
                next(n for n, v in CARDINAL.items() if v[0] * att.normal[0] + v[1] * att.normal[1] > 0.5)
                if any(v[0] * att.normal[0] + v[1] * att.normal[1] > 0.5 for v in CARDINAL.values())
                else "outward"
            )
            ctx.note(
                ctx.say(
                    f"side={side} no se aplica: {face_id} mira hacia {outward} en la vista {att.view.name} y la marca "
                    "siempre queda fuera de la pieza; offset_mm sí la aleja",
                    f"side={side} does not apply: {face_id} faces {outward} in the {att.view.name} view and the mark "
                    "always stays outside the part; offset_mm does move it",
                )
            )
        return att
    if face.kind != "cylinder" or face.axis_point is None or face.axis_dir is None or face.radius is None:
        msg = f"{face_id} is a {face.kind} face: a label attaches to a plane or a cylinder"
        raise ItemError(msg)
    axis = unit(face.axis_dir)
    along = dot(sub(face.point, face.axis_point), axis)
    concave = any(face_id in hole.faces for hole in ctx.holes)
    circle = [v for v in _views(ctx, wanted) if v.looks_along(axis)]
    side_on = [v for v in _views(ctx, wanted) if v.shows_axis_along(axis)]
    order = circle + side_on if concave else side_on + circle
    if not order:
        where = f"the {wanted} view" if wanted else "the front, top or right view"
        msg = f"{face_id}: its axis is neither along nor across {where}: no view shows it"
        raise ItemError(msg)
    view = order[0]
    radius = face.radius * view.scale
    centre = view.project(_on_axis(face.axis_point, axis, along))  # across the axis it is refined below
    box = view.box
    middle = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    away = _unit2((centre[0] - middle[0], centre[1] - middle[1]))
    if view in circle:
        if side:
            direction = CARDINAL[side]
        elif face.surface_point is not None:
            tip = view.project(face.surface_point)
            direction = _unit2((tip[0] - centre[0], tip[1] - centre[1]))
        else:
            direction = (1.0, 0.0)
        tip = (centre[0] + direction[0] * radius, centre[1] + direction[1] * radius)
        return Attach(view, tip, direction, "surface", circle=(centre, radius))
    side_line = unit(cross(view.direction, axis))
    across = _unit2(view.axis(side_line))
    sign = _side_sign(across, side, away)
    direction = (across[0] * sign, across[1] * sign)
    # the surface is not always there: a flat or a groove may have taken the middle of the face (its centre of mass is
    # then in the air). Where the silhouette line of that side really exists is where a label may touch it.
    band = None
    found = ctx.geometry.cylinder_span(face_id, scale(side_line, sign))
    named = [t for t in ctx.threads if t.cylinder == face_id]
    if found is not None and named:  # a threaded shaft: the callout points at the thread, not the plain part
        spans = []
        for t in named:
            start = dot(sub(t.start, face.axis_point), axis)
            spans.append(tuple(sorted((start, start + t.length * dot(t.direction, axis)))))
        narrowed = (max(found[0], min(a for a, _ in spans)), min(found[1], max(b for _, b in spans)))
        if narrowed[1] - narrowed[0] > 1e-6:
            found = narrowed
    if found is not None:
        low, high = found
        centre = view.project(_on_axis(face.axis_point, axis, (low + high) / 2))
        ends = [view.project(_on_axis(face.axis_point, axis, s)) for s in (low, high)]
        band = tuple((p[0] + direction[0] * radius, p[1] + direction[1] * radius) for p in ends)
    return Attach(
        view,
        (centre[0] + direction[0] * radius, centre[1] + direction[1] * radius),
        direction,
        "surface",
        band=band,  # type: ignore[arg-type]
    )


def _on_axis(origin: Vec3, axis: Vec3, distance: float) -> Vec3:
    """The point of the axis through ``origin`` along ``axis``, ``distance`` mm from ``origin``."""
    return (origin[0] + axis[0] * distance, origin[1] + axis[1] * distance, origin[2] + axis[2] * distance)


def clear_of_views(ctx: Context, rect: Rect) -> bool:
    """Whether a label rectangle stays off the drawn views (touching their outline is fine): a label belongs beside the
    part, not on it."""
    for view in ctx.views.values():
        x0, y0, x1, y1 = view.box
        inner = (x0 + 0.5, y0 + 0.5, x1 - 0.5, y1 - 0.5)
        if rect[0] < inner[2] and inner[0] < rect[2] and rect[1] < inner[3] and inner[1] < rect[3]:
            return False
    return True


def _directions(att: Attach) -> list[tuple[float, float]]:
    """The directions a label may leave in: the natural one, then -- only for a circle, which has no outside normal --
    the rest of the compass, nearest the natural one first."""
    if att.circle is None:
        return [att.normal]
    base = math.atan2(att.normal[1], att.normal[0])
    turns = (0, 1, -1, 2, -2, 3, -3, 4)
    return [(math.cos(base + t * math.pi / 4), math.sin(base + t * math.pi / 4)) for t in turns]


def place_label(
    ctx: Context,
    att: Attach,
    build: Callable[[Point, tuple[float, float]], tuple[list[Entity], Rect]],
    offset_mm: float | None = None,
    explicit: bool = False,
) -> tuple[list[Entity], bool]:
    """Draw a label by ``build(anchor, direction)`` (which returns its entities and the rectangle the label itself
    takes) with its anchor ``offset_mm`` (default ``FIRST_DISTANCE_MM``) from the feature along the direction away from
    the part, farther out while the rectangle lands on something, on a view or outside the frame, and -- for a feature
    seen as a circle -- in the other directions too. When the item asked for its place (``explicit``: ``side`` or
    ``offset_mm``) there is one try, exactly there. Returns the entities and whether the place was free (the first try
    is kept, and reserved, when none is; a label that cannot be inside the frame says so in the item's result)."""
    start = (FIRST_DISTANCE_MM if offset_mm is None else offset_mm) + (0.0 if explicit else ctx.nudge() * NUDGE_MM)
    first: tuple[list[Entity], Rect] | None = None
    inside_first: tuple[list[Entity], Rect] | None = None
    home = att.tip
    # a leader that crosses nothing first; then one that may cross the LINE of a dimension (never its text); a label
    # that slides along the plane it points at (``Attach.tips``) before it goes farther out
    for crossing, strict in ((False, True), (True, True), (False, False), (True, False)) if not explicit else ((False, False),):
        for direction, tip, step in _candidates(att, explicit):
            att.tip = tip
            distance = start + step * NUDGE_MM
            anchor = (tip[0] + direction[0] * distance, tip[1] + direction[1] * distance)
            entities, rect = build(anchor, direction)
            fits = ctx.inside(rect)
            if first is None:
                first = (entities, rect)
            if fits and inside_first is None:
                inside_first = (entities, rect)
            if (
                fits
                and ctx.free(rect)
                and clear_of_views(ctx, rect)
                and _leader_free(ctx, att, tip, anchor, crossing)
                and not (strict and _on_dimension_lines(ctx, entities, rect))
            ):
                ctx.reserve(rect)
                ctx.extras.setdefault("segments", []).append((tip, anchor))
                return entities, True
    att.tip = home
    assert first is not None
    kept = inside_first or first
    if inside_first is None:
        ctx.note(why_not(ctx, kept[1], False, True))
    ctx.reserve(kept[1])
    return kept[0], False


def _candidates(att: Attach, explicit: bool) -> list[tuple[tuple[float, float], Point, int]]:
    """The (direction, touching point, outward step) a label is tried at, in the order of preference: each direction in
    turn (a circle has the whole compass), at the middle of a plane's trace farther and farther out, after sliding along
    the trace at the first few distances."""
    out: list[tuple[tuple[float, float], Point, int]] = []
    for direction in _directions(att)[:1] if explicit else _directions(att):
        tips = [att.tip_towards(direction)] if att.circle is not None or explicit else att.tips()
        for tip in tips:
            out += [
                (direction, tip, step) for step in range(1 if explicit else (SLIDE_TRIES if len(tips) > 1 else TRIES))
            ]
        if len(tips) > 1:
            out += [(direction, tips[0], step) for step in range(SLIDE_TRIES, TRIES)]
    return out


def _segment_distance(a: Point, b: Point, c: Point, d: Point) -> float:
    """The smallest distance between the segments ``ab`` and ``cd`` (0 when they cross)."""

    def side(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
        return 0.0
    return min(_point_segment(p, q, r) for p, q, r in ((a, c, d), (b, c, d), (c, a, b), (d, a, b)))


def _point_segment(p: Point, a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _segment_hits_rect(a: Point, b: Point, rect: Rect) -> bool:
    x0, y0, x1, y1 = rect
    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    if x0 < a[0] < x1 and y0 < a[1] < y1 or x0 < b[0] < x1 and y0 < b[1] < y1:
        return True
    return any(_segment_distance(a, b, corners[i], corners[(i + 1) % 4]) == 0.0 for i in range(4))


LEADER_CLEARANCE_MM = 1.5
DIMENSION_LINE_CLEARANCE_MM = 0.6  # a label keeps this far from the extension and dimension lines already drawn


def _on_dimension_lines(ctx: Context, entities: list[Entity], rect: Rect) -> bool:
    """Whether a label (its entities and the rectangle it takes) touches a dimension or extension line already on the sheet:
    a datum, a frame or a symbol does not stand on the lines of another item (ISO 129-1; the review flags it)."""
    lines = ctx.extras.get("dimension_lines", [])
    if not lines:
        return False
    mine = segments_of(entities)
    for a in lines:
        start, end = (a[0], a[1]), (a[2], a[3])
        if _segment_hits_rect(start, end, rect):
            return True
        if any(_segment_distance(start, end, (b[0], b[1]), (b[2], b[3])) < DIMENSION_LINE_CLEARANCE_MM for b in mine):
            return True
    return False


def _leader_free(ctx: Context, att: Attach, tip: Point, anchor: Point, crossing: bool = False) -> bool:
    """Whether the leader from ``tip`` to ``anchor`` keeps off the other leaders drawn so far, off the text and frames
    already placed, and -- in the view it starts in -- off the other holes. With ``crossing`` the leader may go through
    the strip a dimension takes (its line, arrows and extension lines) as long as it misses the dimension's text."""
    for start, end in ctx.extras.get("segments", []):
        if _segment_distance(tip, anchor, start, end) < LEADER_CLEARANCE_MM:
            return False
    strips = ctx.extras.get("strips", []) if crossing else []
    if any(_segment_hits_rect(tip, anchor, rect) for rect in ctx.occupied if rect not in strips):
        return False
    if crossing and any(_segment_hits_rect(tip, anchor, rect) for rect in ctx.extras.get("texts", [])):
        return False
    for hole in ctx.holes:
        if not att.view.looks_along(hole.direction):
            continue
        centre = att.view.project(hole.centre)
        if math.hypot(centre[0] - tip[0], centre[1] - tip[1]) < 0.5 + (att.circle[1] if att.circle else 0.0):
            continue  # the feature itself
        if _point_segment(centre, tip, anchor) < hole.diameter / 2 * att.view.scale + 1.0:
            return False
    return True
