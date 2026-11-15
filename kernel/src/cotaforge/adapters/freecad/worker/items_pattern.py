# ruff: noqa: E501
"""``pattern_dim``: a circular pattern of holes located by polar coordinates (ISO 129-1), not by the X / Y of every hole (no
FreeCAD import).

The pattern is the one that holds the hole whose axis passes through ``at`` (``sheet_pattern`` finds the bolt circles of the
part; a lone hole counted from an axis -- a pin or a screw on a radius -- is a pattern of one, about ``about`` or the axis of the
part). Seen along its axis the item draws the pitch circle as a thin chain circle through the centres, the radial centre lines
from the axis through each hole, and each centre mark turned along its radius. ``kind``:

* ``diameter`` (the default): the diameter of the pitch circle, ``Ø70``, as a leader whose arrow is on the circle between two
  holes. The count and the size of the holes are the ``hole_dim`` callout (``6× M8``): equal spacing is the drawing's, so no
  angle is given between equally spaced holes;
* ``angle``: the angle of the first hole from the nearest centre line of the part, when it is not on one (nothing is drawn
  when it is). An uneven pattern has one item per hole (``at`` names it), each from its nearest centre line.

Every sheet whose part has a circular pattern carries these items by itself (``pd1``, ``pd1a``...); the holes of the pattern are
then not given X / Y positions by the default location. The review says ``pattern_not_polar`` when a pattern is located by
coordinates or its pitch circle is in a note only.
"""

import math
from typing import Any

import sheet_pattern
from items_common import (  # noqa: PLC2701 - the leader helpers of the same package
    CALLOUT_GAP_MM,
    _count_crossings,
    _ray_exit,
    asked,
    hole_mark,
    mark_once,
    outward_diagonal,
    place_leader,
    view_looking_along,
    view_segments,
)
from items_holes import native_callout
from sheet_context import (
    COMMON_FIELDS,
    Context,
    ItemError,
    ItemType,
    Point,
    check_fields,
    parallel,
    v_bool,
    v_enum,
    v_point2,
    v_point3,
)
from sheet_draw import angle_dimension
from sheet_model import AXES, Circle, Line
from sheet_units import NativeCosmetic, NativeDim, Ref2

CHECKS = {
    "kind": v_enum("diameter", "angle"),
    "at": v_point3,
    "about": v_point3,
    "auto": v_bool,
    "elbow_mm": v_point2,  # where the global layout ended the leader (the vector from its tip): written by it, not asked for
    "view": COMMON_FIELDS["view"],
    "offset_mm": COMMON_FIELDS["offset_mm"],  # the radius of the arc of an angle
    "side": COMMON_FIELDS["side"],
}
ARC_OVER_MM = 6.0  # the arc of an angle is this far outside the holes of the pattern
ARC_NEST_MM = 7.0  # the arc of a lone hole stands this much beyond the arc of the ring of holes: their texts never meet
LEADER_DIAGONALS = (45.0, 135.0, 225.0, 315.0)
RAY_SKEW_DEG = 0.37
REF_OVER_MM = 4.0  # the centre line a first-hole angle is counted from reaches this far past the holes


def check(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, CHECKS, required=("at",))
    if out.get("kind") == "diameter" and "offset_mm" in out:
        msg = "offset_mm is the radius of the arc of an angle: a diameter takes side or nothing (the layout places it)"
        raise ItemError(msg)
    return out


def _entity_key(view: str, pattern: sheet_pattern.Pattern, what: str) -> tuple[Any, ...]:
    return (what, view, *(round(c, 1) for c in pattern.centre), round(pattern.radius, 2))


def _once(ctx: Context, key: tuple[Any, ...]) -> bool:
    done: set[tuple[Any, ...]] = ctx.extras.setdefault("pattern_drawn", set())
    if key in done:
        return False
    done.add(key)
    return True


def _marks(ctx: Context, view: Any, pattern: sheet_pattern.Pattern, holes: dict[int, Any]) -> None:
    """The centre mark of every place of the pattern, turned along its radius (a hole another item marked first keeps that one)."""
    for index, stack in zip(pattern.reps, pattern.members, strict=True):
        hole = holes[index]
        p = view.project(hole.centre)
        widest = max(holes[i].diameter for i in stack)
        if mark_once(ctx, view, p):
            ctx.add(hole_mark(ctx, view, hole, p, widest / 2 * view.scale))


def _pitch_circle(ctx: Context, view: Any, pattern: sheet_pattern.Pattern) -> None:
    if _once(ctx, _entity_key(view.name, pattern, "circle")):
        c = view.project(pattern.centre)
        ctx.add([Circle(c[0], c[1], pattern.radius * view.scale, AXES)])


def _tip_angle(ctx: Context, view: Any, c: Point, radius: float, angles: list[float], clear: float) -> float:
    """The page angle (degrees) the leader of the diameter leaves the circle at: in a gap between two holes (its middle, or a
    quarter of the way in when the leader still passes ``clear`` mm from the hole; for a lone hole, a quarter turn from it), where
    the leader to the outside of the view crosses the fewest lines of the part (a hole of the part in its way is two more), then
    not along a centre line of the part, then nearest to a diagonal, where a leader is cleanest."""
    ordered = sorted(angles)
    if len(ordered) == 1:
        candidates = [(ordered[0] + q) % 360.0 for q in (90.0, 180.0, 270.0)]
    else:
        candidates = []
        for i, start in enumerate(ordered):
            gap = ((ordered[(i + 1) % len(ordered)] - start) % 360.0) or 360.0
            for fraction in (0.25, 0.5, 0.75):
                if fraction == 0.5 or radius * math.radians(gap * min(fraction, 1 - fraction)) >= clear:
                    candidates.append((start + gap * fraction) % 360.0)
    outline = view_segments(ctx, view)

    def cost(a: float) -> tuple[int, bool, float, float]:
        aside = math.radians(a + RAY_SKEW_DEG)  # a ray that hits a vertex of the outline touches two segments: counted slightly off it
        u = (math.cos(aside), math.sin(aside))
        tip = (c[0] + u[0] * radius, c[1] + u[1] * radius)
        reach = _ray_exit(view.box, tip, u) + CALLOUT_GAP_MM  # to where the elbow stands, past the outline
        cuts = _count_crossings((tip[0], tip[1], tip[0] + u[0] * reach, tip[1] + u[1] * reach), outline)
        along_a_centre_line = sheet_pattern.on_centre_line(a)  # never along a centre line of the part
        return cuts, along_a_centre_line, round(min(abs(((a - d) + 180.0) % 360.0 - 180.0) for d in LEADER_DIAGONALS), 1), round(a, 1)

    return min(candidates, key=cost)


def _diameter(ctx: Context, spec: dict[str, Any], pattern: sheet_pattern.Pattern, view: Any, holes: dict[int, Any]) -> str:
    c = view.project(pattern.centre)
    radius = pattern.radius * view.scale
    _marks(ctx, view, pattern, holes)
    _pitch_circle(ctx, view, pattern)
    angles = [sheet_pattern.view_angle(view, pattern.centre, holes[i].centre) for i in pattern.reps]
    widest = max(h.diameter for h in holes.values() if h.index in pattern.indexes) / 2 * view.scale
    theta = math.radians(_tip_angle(ctx, view, c, radius, angles, widest + 6.0))
    u = (math.cos(theta), math.sin(theta))
    tip: Point = (c[0] + u[0] * radius, c[1] + u[1] * radius)
    text = f"Ø{ctx.num(round(pattern.diameter, 2))}"
    diagonal = outward_diagonal(view, tip, spec.get("side"))
    ctx.add(place_leader(ctx, view, tip, diagonal, [text], None, asked(spec), along=u))
    native_callout(ctx, view.name, view.scale, c, pattern.diameter, [text])  # FreeCAD measures the pitch circle (a cosmetic circle)
    polar = ctx.extras.setdefault("polar", {}).setdefault(min(pattern.reps), {"diameter": False, "need": 0, "given": 0})
    polar["diameter"] = True
    if pattern.even:
        need = [] if sheet_pattern.on_centre_line(sheet_pattern.first_hole(view, pattern, holes)[1]) else [0]
    else:
        need = [a for a in angles if not sheet_pattern.on_centre_line(a)]
    polar["need"] = len(need)
    ctx.extras.setdefault("polar_located", set()).update(pattern.indexes)
    count = "" if pattern.count == 1 else f", {pattern.count} holes {'equally spaced' if pattern.even else 'unevenly spaced'}"
    return f"{text} pitch circle in the {view.name} view{count}"


def _native_angle(ctx: Context, view: Any, c: Point, ref: float, angle: float, off: float, arc: float, text: str, reach: float) -> None:
    """The angle just drawn as TechDraw's own angle dimension between two centre lines of the view: the one the angle is counted
    from and the radial one through the hole (a cosmetic line made for it). FreeCAD measures it."""
    item = str(ctx.extras["current"])
    ctx.native(NativeCosmetic(item, view.name, "line", (c[0], c[1], c[0] + reach * math.cos(math.radians(angle)), c[1] + reach * math.sin(math.radians(angle))), "centre"))
    mid = math.radians((ref + ((angle - ref) % 360.0) / 2) % 360.0) if off > 0 else math.radians((angle + ((ref - angle) % 360.0) / 2) % 360.0)
    label = (c[0] + (arc + 4.0) * math.cos(mid), c[1] + (arc + 4.0) * math.sin(mid))

    def ray(a: float) -> Ref2:
        return Ref2("line", (c[0], c[1], c[0] + 10.0 * math.cos(math.radians(a)), c[1] + 10.0 * math.sin(math.radians(a))))

    ctx.native(
        NativeDim(
            item=item,
            name=f"a{len(ctx.extras.get('natives', {}).get(item, [])) + 1}",
            kind="angle",
            view=view.name,
            refs=(ray(ref), ray(angle)),
            label=label,
            expected=abs(off),
            text=text,
        )
    )


def _angle(ctx: Context, spec: dict[str, Any], pattern: sheet_pattern.Pattern, view: Any, holes: dict[int, Any]) -> str:
    c = view.project(pattern.centre)
    if pattern.even and not pattern.lone:
        index, angle = sheet_pattern.first_hole(view, pattern, holes)
    else:
        named = next(h for h in holes.values() if h.on_axis(tuple(spec["at"])))
        index, angle = named.index, sheet_pattern.view_angle(view, pattern.centre, named.centre)
        index = min(i for i, stack in zip(pattern.reps, pattern.members, strict=True) if named.index in stack)
    ctx.extras.setdefault("polar_located", set()).update(pattern.indexes)
    if sheet_pattern.on_centre_line(angle):
        return f"the hole at {angle:g} degrees is on a centre line of the {view.name} view: no angle to give"
    _marks(ctx, view, pattern, holes)
    _pitch_circle(ctx, view, pattern)
    off = sheet_pattern.offset_from_centre_line(angle)
    ref = (angle - off) % 360.0
    stack = pattern.members[pattern.reps.index(index)]
    r_hole = max(holes[i].diameter for i in stack) / 2 * view.scale
    reach = pattern.radius * view.scale + r_hole + REF_OVER_MM
    if _once(ctx, _entity_key(view.name, pattern, f"ref{ref:g}")):  # the centre line the angle is counted from
        ctx.add([Line(c[0], c[1], c[0] + reach * math.cos(math.radians(ref)), c[1] + reach * math.sin(math.radians(ref)), AXES)])
    reach = pattern.radius * view.scale + r_hole + ARC_OVER_MM
    if pattern.lone:  # the arc of a lone hole stands outside every ring of holes of the part, where the text of a hole of a ring never lies
        for ring in sheet_pattern.patterns_of(ctx):
            if parallel(ring.direction, pattern.direction):
                widest = max(holes[i].diameter for i in ring.indexes) / 2 * view.scale
                reach = max(reach, ring.radius * view.scale + widest + ARC_OVER_MM + ARC_NEST_MM)
    arc = spec.get("offset_mm") or reach
    a0, a1 = (ref, angle) if off > 0 else (angle, ref)
    text = f"{ctx.num(round(abs(off), 2))}°"
    entities, taken = angle_dimension(c, a0, a1, arc, text)
    ctx.add(entities)
    ctx.reserve(taken)
    _native_angle(ctx, view, c, ref, angle, off, arc, text, pattern.radius * view.scale + r_hole + REF_OVER_MM)
    polar = ctx.extras.setdefault("polar", {}).setdefault(min(pattern.reps), {"diameter": False, "need": 0, "given": 0})
    polar["given"] += 1
    return f"{text} from the centre line to the hole, in the {view.name} view"


def draw(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    pattern = sheet_pattern.pattern_for_item(ctx, spec)
    view = view_looking_along(ctx, pattern.direction, spec.get("view"))
    if view is None:
        where = f"the {spec['view']} view" if "view" in spec else "the front, top or right view"
        msg = f"the axis of the pattern is not along the direction of {where}: no view shows its holes as circles"
        raise ItemError(msg)
    holes = {h.index: h for h in ctx.holes}
    ctx.used_view(view.name)
    if spec.get("kind", "diameter") == "angle":
        return "ok", _angle(ctx, spec, pattern, view, holes)
    return "ok", _diameter(ctx, spec, pattern, view, holes)


ITEMS = (ItemType("pattern_dim", "pd", check, draw, frozenset(CHECKS), phase=1),)
