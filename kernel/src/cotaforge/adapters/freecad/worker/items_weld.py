# ruff: noqa: E501
"""``weld``: the weld symbol of a joint after ISO 2553 (no FreeCAD import).

The joint is measured, never guessed: ``between`` names the two bodies that touch (``sheet_joints``); the weld -- its
kind, the throat ``a`` (or the leg ``z``), the side, all round, field, intermittent -- is the designer's decision. One
item is one symbol; the joints that are the same job (``also``) share it: one reference line with an arrow to each of them (ISO 2553 allows several arrow lines on one reference line for identical welds), so the welder sees which joints are meant.

The symbol stands where the joint is SEEN: in the view whose visible edges carry the seam of the joint. It is drawn the ISO
way: an arrow line that ends on the seam, the continuous reference line, the elementary symbol under it for the arrow side
(above it for the other side, with the dashed identification line; a symbol on both sides needs no dashed line), the throat
before the symbol and the length after it, a circle at the arrow for a weld all round, a flag for a field weld. The symbol
does not touch another symbol, a dimension or the lines of the views.

The drawing is the sheet's own (lines and text), not TechDraw's weld object: FreeCAD 1.1's ``DrawWeldSymbol`` carries the AWS
and GOST symbols, no dashed identification line and no length text of ISO 2553.
"""

import math
from dataclasses import dataclass
from typing import Any

import sheet_joints as sj
from items_attach import Attach, _leader_free, _segment_distance, _segment_hits_rect
from items_common import VIEW_ORDER, asked
from sheet_context import (
    COMMON_FIELDS,
    Context,
    ItemError,
    ItemType,
    Point,
    ViewInfo,
    check_fields,
    v_bool,
    v_enum,
    v_num,
    v_point2,
)
from sheet_draw import arrow
from sheet_model import DIMENSIONS, Arc, Circle, Entity, Line, Polyline, Solid, Text, text_width

KINDS = ("fillet", "v", "square")
SIZE_KINDS = ("throat", "leg")
SIDES = ("arrow", "other", "both")
TEXT_MM = 4.5  # lettering of a drawn fragment renders smaller than TechDraw's own 3.5 mm dimension text: never below it on the sheet
SYMBOL_W = 5.0
SYMBOL_H = 5.0
DASH_GAP_MM = 5.0
PAD_MM = 1.5
MIN_REF_MM = 14.0
JUNCTION_MM = 3.5  # room at the arrow end of the reference line for the circle or the flag
CIRCLE_R = 1.8
FLAG_H = 5.0
MIN_SEEN_MM = 2.5  # a seam shorter than this on the page is seen end-on
VISIBLE_TOL_MM = 0.4
SAMPLES = 8
TIP_FRACTIONS = (0.5, 0.35, 0.65, 0.25, 0.75)  # the middle of a seam first, never its end (a corner)
DISTANCES = (8.0, 12.0, 18.0, 26.0, 36.0)
FAR_DISTANCES = (46.0, 58.0)  # a group of joints spread over a view needs the reference line farther from the first arrow
PARALLEL_DEG = 20.0
PICTORIAL = ("isometric", "iso", "exploded")  # the names of the views that are pictures, not projections
NEAR_TIP_MM = 0.9
SAME_TIP_MM = 1.0  # two joints whose seams project onto each other in a view share the arrow
Rect = tuple[float, float, float, float]
Segment = tuple[Point, Point]


def _pair(value: Any) -> list[str]:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2  # noqa: PLR2004
        or any(not isinstance(v, str) or not v.strip() for v in value)
        or value[0] == value[1]
    ):
        msg = 'must be the two bodies that touch, like ["body:1", "body:5"]'
        raise ItemError(msg)
    return [str(v).strip() for v in value]


def _pairs(value: Any) -> list[list[str]]:
    if not isinstance(value, list | tuple) or any(not isinstance(v, list | tuple) for v in value):
        msg = 'must be a list of pairs of bodies, like [["body:2", "body:5"], ["body:3", "body:5"]]'
        raise ItemError(msg)
    return [_pair(v) for v in value]


CHECKS = {
    "between": _pair,
    "also": _pairs,
    "kind": v_enum(*KINDS),
    "size_mm": v_num(0.5, 100.0),
    "size_kind": v_enum(*SIZE_KINDS),
    "sides": v_enum(*SIDES),
    "all_around": v_bool,
    "field": v_bool,
    "length_mm": v_num(1.0, 100000.0),
    "pitch_mm": v_num(1.0, 100000.0),
    "view": COMMON_FIELDS["view"],
    "side": COMMON_FIELDS["side"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
    "anchor_at": v_point2,  # where the global layout put the reference line (page mm): written by it, not asked for
}


def check(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, CHECKS, required=("between", "size_mm"))
    if "pitch_mm" in out and "length_mm" not in out:
        msg = "pitch_mm (the distance between the weld segments) goes with length_mm (the length of one)"
        raise ItemError(msg)
    if out.get("pitch_mm") is not None and out["pitch_mm"] < out["length_mm"]:
        msg = "pitch_mm is the distance between the centres of two segments: it cannot be shorter than length_mm"
        raise ItemError(msg)
    if out.get("size_kind") == "leg" and out.get("kind", "fillet") != "fillet":
        msg = "size_kind=leg is for a fillet weld: a butt weld gives its penetration"
        raise ItemError(msg)
    if out.get("all_around") and out.get("length_mm") is not None:
        msg = "a weld all round has no length: take all_around or length_mm"
        raise ItemError(msg)
    return out


# --- the joints the item names -------------------------------------------------------------------------------------------


def joints_named(ctx: Context, spec: dict[str, Any]) -> list[Any]:
    """The measured joints ``between`` and ``also`` name (the first is the item's own), or an ``ItemError`` that lists the
    joints there are."""
    found = {j.key: j for j in ctx.geometry.weld_joints()}
    if not found:
        msg = ctx.say(
            "esta pieza no tiene uniones medidas: ningún cuerpo toca a otro (¿la pieza es de un solo cuerpo, o los cuerpos no se tocan?)",
            "this part has no measured joints: no body touches another (is it a single body, or do the bodies not touch?)",
        )
        raise ItemError(msg)
    out: list[Any] = []
    for pair in [spec["between"], *spec.get("also", [])]:
        key = sj.joint_key(pair[0], pair[1])
        if key not in found:
            known = ", ".join(sorted(found, key=lambda k: [sj.natural(b) for b in k.split("+")]))
            msg = ctx.say(
                f"{pair[0]} y {pair[1]} no se tocan: no hay unión que soldar entre ellos. Uniones medidas: {known}",
                f"{pair[0]} and {pair[1]} do not touch: there is no joint to weld between them. Measured joints: {known}",
            )
            raise ItemError(msg)
        if found[key] not in out:
            out.append(found[key])
    return out


# --- where a joint is seen ---------------------------------------------------------------------------------------------


def _page_segments(view: ViewInfo, entities: list[Entity]) -> list[Segment]:
    def page(p: Point) -> Point:
        return (view.x + p[0] * view.scale, view.y + p[1] * view.scale)

    out: list[Segment] = []
    for e in entities:
        if isinstance(e, Line):
            out.append((page((e.x1, e.y1)), page((e.x2, e.y2))))
        elif isinstance(e, Polyline):
            pts = list(e.points) + ([e.points[0]] if e.closed and e.points else [])
            out += [(page(a), page(b)) for a, b in zip(pts, pts[1:], strict=False)]
        elif isinstance(e, Circle | Arc):
            start, end = (0.0, 360.0) if isinstance(e, Circle) else (e.start, e.end if e.end > e.start else e.end + 360.0)
            pts = [
                page((e.cx + e.r * math.cos(math.radians(start + (end - start) * i / 24)), e.cy + e.r * math.sin(math.radians(start + (end - start) * i / 24))))
                for i in range(25)
            ]
            out += list(zip(pts, pts[1:], strict=False))
    return out


def visible_segments(ctx: Context, view: ViewInfo) -> list[Segment] | None:
    """The visible edges of ``view`` on the page, ``None`` when the geometry cannot say (then nothing is judged hidden)."""
    cache: dict[str, list[Segment] | None] = ctx.extras.setdefault("weld_visible", {})
    if view.name not in cache:
        try:
            projection = ctx.geometry.view_projection(view.centre, view.xdir, view.direction)
            cache[view.name] = _page_segments(view, list(projection.visible))
        except ItemError:
            cache[view.name] = None
    return cache[view.name]


def _on_any(p: Point, segments: list[Segment]) -> bool:
    return any(_segment_distance(p, p, a, b) <= VISIBLE_TOL_MM for a, b in segments)


@dataclass
class Spot:
    """A visible stretch of a seam in one view: ``a`` to ``b`` on the page, of the joint ``joint``."""

    view: ViewInfo
    joint: Any
    a: Point
    b: Point

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    def at(self, fraction: float) -> Point:
        inset = min(0.3, self.length / 4)
        t = min(max(fraction, inset / self.length), 1 - inset / self.length)
        return (self.a[0] + (self.b[0] - self.a[0]) * t, self.a[1] + (self.b[1] - self.a[1]) * t)


def seen_spots(ctx: Context, view: ViewInfo, joint: Any) -> list[Spot]:
    """The stretches of the seam of ``joint`` that ``view`` shows (visible, not edge-on), the longest first."""
    visible = visible_segments(ctx, view)
    out: list[Spot] = []
    for line in joint.seams:
        for p, q in zip(line, line[1:], strict=False):
            a, b = view.project(p), view.project(q)
            if math.dist(a, b) < MIN_SEEN_MM:
                continue
            run: list[Point] = []
            for i in range(SAMPLES):
                lo = (a[0] + (b[0] - a[0]) * i / SAMPLES, a[1] + (b[1] - a[1]) * i / SAMPLES)
                hi = (a[0] + (b[0] - a[0]) * (i + 1) / SAMPLES, a[1] + (b[1] - a[1]) * (i + 1) / SAMPLES)
                mid = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
                at = (i + 0.5) / SAMPLES
                point = (p[0] + (q[0] - p[0]) * at, p[1] + (q[1] - p[1]) * at, p[2] + (q[2] - p[2]) * at)
                # on a visible edge of the view AND not behind a body (a seam can sit under the outline of a body in front)
                shown = (visible is None or _on_any(mid, visible)) and not ctx.geometry.seam_hidden(point, view.direction)
                if shown:
                    run = [lo, hi] if not run else [run[0], hi]
                if (not shown or i == SAMPLES - 1) and run:
                    if math.dist(run[0], run[1]) >= MIN_SEEN_MM:
                        out.append(Spot(view, joint, run[0], run[1]))
                    run = []
    return sorted(out, key=lambda s: -s.length)


def views_to_try(ctx: Context, wanted: str | None) -> list[ViewInfo]:
    """The views a joint may be shown in: the three standard (orthographic) ones, never the pictorial one: in an isometric view the
    seam is distorted and the arrow side of ISO 2553 is ambiguous. A joint no orthographic view shows needs a view or a section
    that does (the review says so); a view asked for by name is drawn as asked and the review flags it when it is pictorial."""
    if wanted is not None:
        try:
            return [ctx.view(wanted)]
        except ItemError:
            return [ctx.any_view(wanted)]
    return [ctx.views[n] for n in VIEW_ORDER if n in ctx.views]


def best_view(ctx: Context, joints: list[Any], wanted: str | None) -> tuple[ViewInfo, list[Spot]] | None:
    """The view that shows the seams of the group best (the most visible length), with the stretches it shows."""
    best: tuple[float, ViewInfo, list[Spot]] | None = None
    for view in views_to_try(ctx, wanted):
        spots = [s for joint in joints for s in seen_spots(ctx, view, joint)]
        total = sum(s.length for s in spots)
        if spots and (best is None or total > best[0] + 1e-6):
            best = (total, view, spots)
    return None if best is None else (best[1], best[2])


def seen_in(ctx: Context, joint: Any) -> str | None:
    """The name of the view that shows ``joint`` best, ``None`` when none of the standard views does."""
    found = best_view(ctx, [joint], None)
    return None if found is None else found[0].name


# --- the symbol ----------------------------------------------------------------------------------------------------------


def size_text(spec: dict[str, Any], comma: bool) -> str:
    from sheet_model import fmt  # noqa: PLC0415

    kind = spec.get("kind", "fillet")
    letter = "s" if kind != "fillet" else ("z" if spec.get("size_kind") == "leg" else "a")
    return f"{letter}{fmt(spec['size_mm'], comma)}"


def length_text(spec: dict[str, Any], comma: bool) -> str:
    from sheet_model import fmt  # noqa: PLC0415

    if spec.get("length_mm") is None:
        return ""
    text = fmt(spec["length_mm"], comma)
    if spec.get("pitch_mm") is not None:
        text += f" ({fmt(spec['pitch_mm'], comma)})"
    return text


def elementary(kind: str, x: float, y: float, below: bool) -> list[Entity]:
    """The elementary symbol with its left edge at ``x`` on the reference line at ``y``; ``below`` puts it under the line."""
    s = -1.0 if below else 1.0
    if kind == "fillet":  # a right triangle, the upright leg on the left, the base on the line
        return [Polyline(((x, y), (x, y + s * SYMBOL_H), (x + SYMBOL_W, y)), DIMENSIONS, closed=True)]
    if kind == "v":  # two lines opening away from the reference line... the V of a V-groove, the point on the line
        return [Polyline(((x, y + s * SYMBOL_H), (x + SYMBOL_W / 2, y), (x + SYMBOL_W, y + s * SYMBOL_H)), DIMENSIONS)]
    return [  # square (I) butt: two short parallel strokes on the line
        Line(x + SYMBOL_W * 0.3, y, x + SYMBOL_W * 0.3, y + s * SYMBOL_H, DIMENSIONS),
        Line(x + SYMBOL_W * 0.7, y, x + SYMBOL_W * 0.7, y + s * SYMBOL_H, DIMENSIONS),
    ]


def dashed(x0: float, x1: float, y: float, dash: float = 3.0, gap: float = 1.5) -> list[Entity]:
    out: list[Entity] = []
    x = x0
    while x < x1 - 1e-9:
        out.append(Line(x, y, min(x + dash, x1), y, DIMENSIONS))
        x += dash + gap
    return out


def symbol(anchor: Point, sign: float, spec: dict[str, Any], comma: bool, count: int) -> tuple[list[Entity], Rect]:
    """The symbol with its arrow end (the junction of the arrow line and the reference line) at ``anchor``; the reference
    line runs from there towards ``sign`` (+1 right, -1 left). Returns the entities and the rectangle they take."""
    ax, ay = anchor
    sides = spec.get("sides", "arrow")
    kind = spec.get("kind", "fillet")
    left = size_text(spec, comma)
    right = length_text(spec, comma)
    w_left = text_width(left, TEXT_MM) + 1.0
    w_right = text_width(right, TEXT_MM) + 1.0 if right else 0.0
    content = w_left + SYMBOL_W + w_right
    length = max(MIN_REF_MM, JUNCTION_MM + content + 2 * PAD_MM)
    x_lo, x_hi = (ax, ax + length) if sign > 0 else (ax - length, ax)
    out: list[Entity] = [Line(x_lo, ay, x_hi, ay, DIMENSIONS)]
    start = x_lo + (JUNCTION_MM if sign > 0 else 0.0) + PAD_MM
    start_at = start if sign > 0 else x_lo + PAD_MM
    sym_x = start_at + w_left
    rows = [True] if sides == "arrow" else [False] if sides == "other" else [True, False]
    for below in rows:
        out += elementary(kind, sym_x, ay, below)
        mid = ay + (-1.0 if below else 1.0) * SYMBOL_H / 2 - TEXT_MM * 0.36
        out.append(Text(sym_x - 0.6, mid, left, TEXT_MM, DIMENSIONS, "right"))
        if right:
            out.append(Text(sym_x + SYMBOL_W + 0.6, mid, right, TEXT_MM, DIMENSIONS, "left"))
    top = ay + (SYMBOL_H if not (sides == "arrow") else 0.0)
    if sides != "both":
        out += dashed(x_lo, x_hi, ay + DASH_GAP_MM)  # the identification line: only a one-sided weld needs it
        top = ay + DASH_GAP_MM
    bottom = ay - (SYMBOL_H if sides in ("arrow", "both") else 0.0)
    if spec.get("all_around"):
        out.append(Circle(ax, ay, CIRCLE_R, DIMENSIONS))
    if spec.get("field"):
        pole = ay + FLAG_H + (CIRCLE_R if spec.get("all_around") else 0.0)
        base = ay + (CIRCLE_R if spec.get("all_around") else 0.0)
        out.append(Line(ax, base, ax, pole, DIMENSIONS))
        out.append(Solid(((ax, pole), (ax, pole - FLAG_H / 2), (ax + sign * FLAG_H / 2, pole - FLAG_H / 4)), DIMENSIONS))
        top = max(top, pole)
    tail = x_hi if sign > 0 else x_lo
    x_far = tail + 1.0 if sign > 0 else tail - 1.0
    if count > 1:
        multiplier = f"{count}×"
        out.append(Text(x_far, ay - TEXT_MM * 0.36, multiplier, TEXT_MM, DIMENSIONS, "left" if sign > 0 else "right"))
        far = text_width(multiplier, TEXT_MM) + 1.0
        x_lo, x_hi = (x_lo, x_hi + far) if sign > 0 else (x_lo - far, x_hi)
    rect = (x_lo - 0.5, bottom - 0.8, x_hi + 0.5, top + 0.8)
    return out, rect


def _crossings(spot_segments: list[Segment] | None, tip: Point, anchor: Point) -> int:
    if not spot_segments:
        return 0
    n = 0
    for a, b in spot_segments:
        if _segment_distance(tip, tip, a, b) < NEAR_TIP_MM:
            continue
        if _segment_distance(tip, anchor, a, b) == 0.0:
            n += 1
    return n


def _directions(spot: Spot, tip: Point, wanted: str | None) -> list[tuple[float, float]]:
    """Unit directions the arrow may leave the seam in, the natural one (away from the middle of the view, across the seam)
    first; never along the seam (the arrow line is not parallel to the joint)."""
    box = spot.view.box
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    away = (tip[0] - centre[0], tip[1] - centre[1])
    if wanted is not None:
        base = {"right": (1.0, 0.0), "left": (-1.0, 0.0), "above": (0.0, 1.0), "below": (0.0, -1.0)}[wanted]
    else:
        n = math.hypot(*away)
        base = (away[0] / n, away[1] / n) if n > 1e-6 else (-1.0, 0.0)
    along = (spot.b[0] - spot.a[0], spot.b[1] - spot.a[1])
    along_n = math.hypot(*along)
    along = (along[0] / along_n, along[1] / along_n)
    angles = sorted((k * 45.0 for k in range(8)), key=lambda a: abs(((a - math.degrees(math.atan2(base[1], base[0])) + 180) % 360) - 180))
    out = []
    for a in angles:
        d = (math.cos(math.radians(a)), math.sin(math.radians(a)))
        cos_along = abs(d[0] * along[0] + d[1] * along[1])
        if cos_along > math.cos(math.radians(PARALLEL_DEG)):
            continue
        out.append(d)
    return out[:1] if wanted is not None else out


Member = tuple[Any, list[Spot]]  # a joint and the stretches of its seam a view shows


def split_by_view(ctx: Context, joints: list[Any], wanted: str | None) -> tuple[list[tuple[ViewInfo, list[Member]]], list[Any]]:
    """The joints shared out among the views, the fewest views first: each round takes the view that shows the most of the
    joints left (ties: the most seam length), in the order of ``views_to_try``. Returns the groups and the joints no view
    shows."""
    views = views_to_try(ctx, wanted)
    seen = {(v.name, j.key): seen_spots(ctx, v, j) for v in views for j in joints}
    left = list(joints)
    groups: list[tuple[ViewInfo, list[Member]]] = []
    while left:
        best: tuple[tuple[int, float], ViewInfo, list[Member]] | None = None
        for view in views:
            members = [(j, seen[(view.name, j.key)]) for j in left if seen[(view.name, j.key)]]
            score = (len(members), sum(s.length for _, spots in members for s in spots))
            if members and (best is None or score > (best[0][0], best[0][1] + 1e-6)):
                best = (score, view, members)
        if best is None:
            break
        groups.append((best[1], best[2]))
        taken = {j.key for j, _ in best[2]}
        left = [j for j in left if j.key not in taken]
    return groups, left


def _arrow_tip(
    ctx: Context, member: Member, anchor: Point, rect: Rect, visible: list[Segment] | None, allowed: int, taken: list[Point]
) -> Point | None:
    """The tip of the arrow of one more joint of the group, leaving ``anchor``: the nearest point of one of its seams (never
    its end) that the arrow reaches across no line of the view beyond ``allowed``, clear of the symbol, of the other arrows
    and not along the seam."""
    _, spots = member
    best: tuple[float, Point] | None = None
    for spot in sorted(spots, key=lambda s: -s.length)[:4]:
        att = Attach(spot.view, spot.at(0.5), (0.0, 1.0), "plane")
        along = _unit((spot.b[0] - spot.a[0], spot.b[1] - spot.a[1]))
        for fraction in TIP_FRACTIONS:
            tip = spot.at(fraction)
            same = next((t for t in taken if math.dist(t, tip) < SAME_TIP_MM), None)
            if same is not None:
                return same  # the seam of this joint is drawn on the one an arrow already marks
            length = math.dist(tip, anchor)
            if length < 6.0:  # noqa: PLR2004
                continue
            direction = _unit((tip[0] - anchor[0], tip[1] - anchor[1]))
            if abs(direction[0] * along[0] + direction[1] * along[1]) > math.cos(math.radians(PARALLEL_DEG)):
                continue
            start = (anchor[0] + direction[0] * 2.0, anchor[1] + direction[1] * 2.0)
            if rect[0] <= start[0] <= rect[2] and rect[1] <= start[1] <= rect[3]:
                continue  # the arrow would run back over the symbol
            others = [_unit((t[0] - anchor[0], t[1] - anchor[1])) for t in taken]
            if any(abs(direction[0] * o[1] - direction[1] * o[0]) < 0.17 and direction[0] * o[0] + direction[1] * o[1] > 0 for o in others):  # noqa: PLR2004
                continue  # two arrows nearly on one line
            att.tip = tip
            if not _leader_free(ctx, att, tip, anchor, crossing=allowed > 0) or _crossings(visible, tip, anchor) > allowed:
                continue
            if best is None or length < best[0]:
                best = (length, tip)
    return None if best is None else best[1]


def _with_arrows(entities: list[Entity], tips: list[Point], anchor: Point) -> list[Entity]:
    for t in tips:
        entities = [*entities, Line(t[0], t[1], anchor[0], anchor[1], DIMENSIONS), arrow(t, _unit((t[0] - anchor[0], t[1] - anchor[1])))]
    return entities


def _place_at(
    ctx: Context, members: list[Member], spec: dict[str, Any], count: int, anchor: Point
) -> tuple[list[Entity], list[Point], Point, Rect, bool] | None:
    """The symbol exactly where the global layout put its reference line (``anchor_at``): the arrow of the first joint ends on
    the nearest point of its seam (never its end, never along it), the others on theirs. ``None`` when the arrows cannot all be
    drawn from there (the automatic search takes over: a symbol never loses an arrow to a planned place)."""
    _, lead_spots = members[0]
    visible = visible_segments(ctx, lead_spots[0].view)
    best: tuple[float, Point] | None = None
    for spot in lead_spots:
        along = _unit((spot.b[0] - spot.a[0], spot.b[1] - spot.a[1]))
        for fraction in TIP_FRACTIONS:
            tip = spot.at(fraction)
            length = math.dist(tip, anchor)
            direction = _unit((tip[0] - anchor[0], tip[1] - anchor[1]))
            if length < 6.0 or abs(direction[0] * along[0] + direction[1] * along[1]) > math.cos(math.radians(PARALLEL_DEG)):  # noqa: PLR2004
                continue
            cost = length + 8.0 * _crossings(visible, tip, anchor)
            if best is None or cost < best[0]:
                best = (cost, tip)
    if best is None:
        return None
    tip = best[1]
    entities, rect = symbol(anchor, 1.0 if anchor[0] >= tip[0] else -1.0, spec, ctx.comma, count)
    tips = [tip]
    for member in members[1:]:
        other = _arrow_tip(ctx, member, anchor, rect, visible, 99, tips)
        if other is None:
            return None
        if other not in tips:
            tips.append(other)
    return _with_arrows(entities, tips, anchor), tips, anchor, rect, ctx.inside(rect) and ctx.free(rect)


def place(
ctx: Context, members: list[Member], spec: dict[str, Any], count: int = 1) -> tuple[list[Entity], list[Point], Point, Rect, bool]:
    """The first free place for ONE reference line with an arrow to each joint of ``members`` (ISO 2553: several arrow
    lines may leave one reference line when the welds are identical): the anchor, a direction and a distance such that the
    symbol is inside the frame and clear of everything on the sheet, and every arrow ends on a seam of its joint across
    no line of the view. Returns the entities, the tips, the anchor, the rectangle and whether it is clean (``False``: the
    least bad place was kept, with the lead arrow only)."""
    comma = ctx.comma
    explicit = asked(spec)
    distances = (spec["offset_mm"],) if spec.get("offset_mm") is not None else (DISTANCES[:1] if explicit else DISTANCES)
    _, lead_spots = members[0]
    visible = visible_segments(ctx, lead_spots[0].view)
    planned = spec.get("anchor_at")
    if planned is not None:
        found = _place_at(ctx, members, spec, count, (float(planned[0]), float(planned[1])))
        if found is not None:
            return found
    if len(members) > 1 and spec.get("offset_mm") is None and not explicit:
        distances = (*distances, *FAR_DISTANCES)
    for allowed in (0, 1, 2, 4, 6) if not explicit else (0, 99):
        best: tuple[float, list[Entity], list[Point], Point, Rect] | None = None
        for spot in lead_spots:
            att = Attach(spot.view, spot.at(0.5), (0.0, 1.0), "plane")
            for fraction in TIP_FRACTIONS:
                tip = spot.at(fraction)
                for direction in _directions(spot, tip, spec.get("side")):
                    for base in distances:
                        distance = base + (0.0 if explicit else ctx.nudge() * 4.0)
                        anchor = (tip[0] + direction[0] * distance, tip[1] + direction[1] * distance)
                        sign = 1.0 if direction[0] >= 0 else -1.0
                        entities, rect = symbol(anchor, sign, spec, comma, count)
                        if not ctx.inside(rect) or not ctx.free(rect):
                            continue
                        if visible and any(_segment_hits_rect(a, b, rect) for a, b in visible):
                            continue
                        att.tip = tip
                        if not _leader_free(ctx, att, tip, anchor, crossing=allowed > 0) or _crossings(visible, tip, anchor) > allowed:
                            continue
                        tips = [tip]
                        reached = True
                        for member in members[1:]:
                            other = _arrow_tip(ctx, member, anchor, rect, visible, allowed, tips)
                            if other is None:
                                reached = False
                                break
                            if other not in tips:
                                tips.append(other)
                        if not reached:
                            continue
                        cost = sum(math.dist(t, anchor) + 8.0 * _crossings(visible, t, anchor) for t in tips)  # short arrows, few crossings
                        if best is None or cost < best[0] - 1e-9:
                            best = (cost, entities, tips, anchor, rect)
                        if explicit:
                            break
        if best is not None:
            _, entities, tips, anchor, rect = best
            return _with_arrows(entities, tips, anchor), tips, anchor, rect, True
    # nothing clean: the lead joint at the natural direction, inside the frame, so the sheet shows where it should go
    spot = lead_spots[0]
    tip = spot.at(0.5)
    direction = _directions(spot, tip, spec.get("side"))[0]
    anchor = (tip[0] + direction[0] * DISTANCES[1], tip[1] + direction[1] * DISTANCES[1])
    entities, rect = symbol(anchor, 1.0 if direction[0] >= 0 else -1.0, spec, comma, count)
    return [*entities, Line(tip[0], tip[1], anchor[0], anchor[1], DIMENSIONS), arrow(tip, _unit((tip[0] - anchor[0], tip[1] - anchor[1])))], [tip], anchor, rect, False


def _unit(v: Point) -> Point:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-9 else (1.0, 0.0)


# --- the item -----------------------------------------------------------------------------------------------------------


def _label(spec: dict[str, Any], comma: bool) -> str:
    kind = {"fillet": "fillet", "v": "V butt", "square": "square butt"}[spec.get("kind", "fillet")]
    extras = [size_text(spec, comma), {"arrow": "arrow side", "other": "other side", "both": "both sides"}[spec.get("sides", "arrow")]]
    if spec.get("all_around"):
        extras.append("all round")
    if spec.get("field"):
        extras.append("field")
    if spec.get("length_mm") is not None:
        extras.append(length_text(spec, comma))
    return f"{kind} {', '.join(extras)}"


def draw(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    joints = joints_named(ctx, spec)
    groups, unseen = split_by_view(ctx, joints, spec.get("view"))
    names = " ; ".join(f"{j.bodies[0]} + {j.bodies[1]}" for j in joints)
    if not groups:
        msg = ctx.say(
            f"ninguna vista ortogonal muestra la unión {names}: su cordón queda oculto o de canto (un símbolo de soldadura no va en la isométrica). Añade una vista o un corte que lo muestre",
            f"no view shows the joint {names}: its seam is hidden or edge-on in every orthographic view (a weld symbol never goes on the isometric one). Add a view or a section that shows it",
        )
        raise ItemError(msg)
    parts: list[str] = []
    clean_all = True
    for view, members in groups:
        ctx.used_view(view.name)
        ctx.extras.setdefault("weld_views", {}).setdefault(str(spec["id"]), []).append(view.name)
        entities, tips, anchor, rect, clean = place(ctx, members, spec, len(joints) if unseen else 1)
        ctx.add(entities)
        ctx.reserve(rect)
        ctx.extras.setdefault("segments", []).extend((t, anchor) for t in tips)
        clean_all = clean_all and clean
        marked = ", ".join(f"{j.bodies[0]} + {j.bodies[1]}" for j, _ in (members if clean else members[:1]))
        parts.append(f"{view.name} view: {len(tips)} arrow(s) for {marked}")
    detail = f"{_label(spec, ctx.comma)} on {names}; " + "; ".join(parts)
    problems: list[str] = []
    if unseen:  # identical joints no view shows: the symbol states the count, the arrows mark the ones that are seen
        hidden = ", ".join(f"{j.bodies[0]} + {j.bodies[1]}" for j in unseen)
        detail += f"; the symbol says {len(joints)}x: {hidden} are hidden in every view, so they have no arrow of their own"
    if not clean_all:
        problems.append("the symbol crosses or overlaps other things: move it with side or offset_mm")
    if problems:
        return "warning", f"{detail}; " + "; ".join(problems)
    return "ok", detail


ITEMS = (ItemType("weld", "w", check, draw, frozenset(CHECKS), phase=2),)
