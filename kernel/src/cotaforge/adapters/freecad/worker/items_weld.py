# ruff: noqa: E501
"""``weld``: the weld symbol of a joint after ISO 2553 (no FreeCAD import).

The joint is measured, never guessed: ``between`` names the two bodies that touch (``sheet_joints``); the weld -- its
kind, the throat ``a`` (or the leg ``z``), the side, all round, field, intermittent -- is the designer's decision. One
item is one symbol; the joints that are the same job (``also``) share it, and the symbol says how many (``4×``).

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
)
from sheet_draw import arrow
from sheet_model import DIMENSIONS, Arc, Circle, Entity, Line, Polyline, Solid, Text, text_width

KINDS = ("fillet", "v", "square")
SIZE_KINDS = ("throat", "leg")
SIDES = ("arrow", "other", "both")
TEXT_MM = 3.5
SYMBOL_W = 4.0
SYMBOL_H = 4.0
DASH_GAP_MM = 5.0
PAD_MM = 1.5
MIN_REF_MM = 14.0
JUNCTION_MM = 3.5  # room at the arrow end of the reference line for the circle or the flag
CIRCLE_R = 1.8
FLAG_H = 5.0
MIN_SEEN_MM = 2.5  # a seam shorter than this on the page is seen end-on
VISIBLE_TOL_MM = 0.4
SAMPLES = 8
TIP_FRACTIONS = (0.5, 0.0, 1.0, 0.25, 0.75)
DISTANCES = (8.0, 12.0, 18.0, 26.0, 36.0)
PARALLEL_DEG = 20.0
NEAR_TIP_MM = 0.9
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
    if wanted is not None:
        return [ctx.view(wanted)]
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


def place(ctx: Context, spots: list[Spot], spec: dict[str, Any], count: int) -> tuple[list[Entity], Spot, Point, Point, bool] | None:
    """The first free place for the symbol: a tip on a visible stretch of the seam, a direction and a distance such that the
    arrow crosses no line of the view, the symbol is inside the frame and clear of everything on the sheet. Returns the
    entities, the stretch, the tip, the anchor and whether it is clean (``False``: the least bad place was kept)."""
    comma = ctx.comma
    explicit = asked(spec)
    distances = (spec["offset_mm"],) if spec.get("offset_mm") is not None else (DISTANCES[:1] if explicit else DISTANCES)
    fallback = None
    for allowed in (0, 1, 2) if not explicit else (0, 99):
        for spot in spots:
            visible = visible_segments(ctx, spot.view)
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
                        if not _leader_free(ctx, att, tip, anchor):
                            continue
                        if _crossings(visible, tip, anchor) > allowed:
                            continue
                        line = [Line(tip[0], tip[1], anchor[0], anchor[1], DIMENSIONS), arrow(tip, _unit((tip[0] - anchor[0], tip[1] - anchor[1])))]
                        return entities + line, spot, tip, anchor, True
    # nothing clean: the first inside the frame, at the natural direction, so the sheet shows where it should go
    for spot in spots:
        tip = spot.at(0.5)
        direction = _directions(spot, tip, spec.get("side"))[0]
        anchor = (tip[0] + direction[0] * DISTANCES[1], tip[1] + direction[1] * DISTANCES[1])
        entities, rect = symbol(anchor, 1.0 if direction[0] >= 0 else -1.0, spec, comma, count)
        fallback = (entities + [Line(tip[0], tip[1], anchor[0], anchor[1], DIMENSIONS), arrow(tip, _unit((tip[0] - anchor[0], tip[1] - anchor[1])))], spot, tip, anchor, False)
        break
    return fallback


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
    found = best_view(ctx, joints, spec.get("view"))
    names = " ; ".join(f"{j.bodies[0]} + {j.bodies[1]}" for j in joints)
    if found is None:
        msg = ctx.say(
            f"ninguna vista muestra la unión {names}: su cordón queda oculto o de canto. Pide otra vista (view=) o añade una vista que la muestre",
            f"no view shows the joint {names}: its seam is hidden or edge-on. Ask for another view (view=) or add a view that shows it",
        )
        raise ItemError(msg)
    view, spots = found
    ctx.used_view(view.name)
    count = len(joints)
    placed = place(ctx, spots, spec, count)
    assert placed is not None
    entities, spot, tip, anchor, clean = placed
    ctx.add(entities)
    sign = 1.0 if anchor[0] >= tip[0] else -1.0
    _, rect = symbol(anchor, sign, spec, ctx.comma, count)
    ctx.reserve(rect)
    ctx.extras.setdefault("segments", []).append((tip, anchor))
    detail = f"{_label(spec, ctx.comma)} on {names} in the {view.name} view" + (f" (the same weld on {count} joints)" if count > 1 else "")
    if not clean:
        return "warning", detail + "; the symbol crosses or overlaps other things: move it with side or offset_mm"
    return "ok", detail


ITEMS = (ItemType("weld", "w", check, draw, frozenset(CHECKS), phase=2),)
