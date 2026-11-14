# ruff: noqa: E501
"""Helpers the item modules share: choosing a view for a direction and putting a leader with its text where it is
free (no FreeCAD import)."""

import math
from typing import Any

import sheet_pattern
from sheet_collide import segment_hits_rect, segments_cross, segments_of
from sheet_context import Context, HoleInfo, Point, Vec3, ViewInfo
from sheet_draw import centre_mark, leader
from sheet_model import AXES, CONTOUR, Entity, Line

VIEW_ORDER = ("top", "front", "right")  # which view shows a hole first when several look along its axis
CALLOUT_GAP_MM = 8.0  # the elbow of a leader is this far beyond the edge of the view
NUDGE_MM = 6.0  # a callout that lands on another is moved this much farther out, up to NUDGE_TRIES times
NUDGE_TRIES = 10
MIN_LEADER_MM = 6.0
RADIAL_OVER_MM = 2.0  # a radial centre mark reaches this far past its circle (as sheet_draw.centre_mark)
RADIAL_MIN_RUN_MM = 0.5  # the chain line from the axis to a hole is drawn when it is longer than this
RADIAL_CLEAR_DEG = 8.0  # a leader keeps this far (degrees) from the radial centre line of its hole


def view_looking_along(ctx: Context, direction: Vec3, wanted: str | None) -> ViewInfo | None:
    """The view whose projection direction is ``direction`` (so a hole with that axis shows as a circle): the asked one
    when it does, else the first of top / front / right that does, else ``None``."""
    if wanted is not None:
        view = ctx.views.get(wanted)
        return view if view is not None and view.looks_along(direction) else None
    for name in VIEW_ORDER:
        view = ctx.views.get(name)
        if view is not None and view.looks_along(direction):
            return view
    return None


def mark_once(ctx: Context, view: ViewInfo, p: Point) -> bool:
    """Whether a centre mark at ``p`` of ``view`` is new: the hole callout and ``view_lines`` both draw them, and a hole
    gets one mark whichever item comes first."""
    done: set[tuple[str, float, float]] = ctx.extras.setdefault("centre_marks", set())
    key = (view.name, round(p[0], 1), round(p[1], 1))
    if key in done:
        return False
    done.add(key)
    return True


def hole_mark(ctx: Context, view: ViewInfo, hole: HoleInfo, p: Point, radius: float) -> list[Entity]:
    """The centre mark of a hole at page point ``p`` (``radius`` its circle on the page): the cross of its centre lines, or, when
    the hole is in a circular pattern seen along its axis, the cross turned along its radius (ISO 128: a radial and a tangential
    bar) with the chain line that runs from the axis of the pattern to it."""
    pattern = sheet_pattern.pattern_of(ctx, hole)
    if pattern is None or not view.looks_along(pattern.direction):
        return centre_mark(p[0], p[1], radius)
    c = view.project(pattern.centre)
    distance = math.dist(c, p)
    if distance < 1e-6:
        return centre_mark(p[0], p[1], radius)
    ux, uy = (p[0] - c[0]) / distance, (p[1] - c[1]) / distance
    reach = radius + RADIAL_OVER_MM
    out: list[Entity] = [
        Line(p[0] - ux * reach, p[1] - uy * reach, p[0] + ux * reach, p[1] + uy * reach, AXES),
        Line(p[0] + uy * reach, p[1] - ux * reach, p[0] - uy * reach, p[1] + ux * reach, AXES),
    ]
    if distance - reach > RADIAL_MIN_RUN_MM:
        out.insert(0, Line(c[0], c[1], p[0] - ux * reach, p[1] - uy * reach, AXES))
    return out


def outward_diagonal(view: ViewInfo, point: Point, side: str | None) -> tuple[float, float]:
    """The diagonal ``(sx, sy)`` (each +-1) a leader from ``point`` takes: away from the middle of the view, unless
    ``side`` says on which side of the feature the callout goes."""
    cx, cy = (view.box[0] + view.box[2]) / 2, (view.box[1] + view.box[3]) / 2
    sx = 1.0 if point[0] - cx >= 0 else -1.0
    sy = 1.0 if point[1] - cy >= 0 else -1.0
    if side == "right":
        sx = 1.0
    elif side == "left":
        sx = -1.0
    elif side == "above":
        sy = 1.0
    elif side == "below":
        sy = -1.0
    return sx, sy


def asked(spec: dict[str, Any]) -> bool:
    """Whether the item asked for a place (``side`` or ``offset_mm``): then it goes exactly there, or says why not."""
    return spec.get("side") is not None or spec.get("offset_mm") is not None


def why_not(ctx: Context, rect: tuple[float, float, float, float], fits: bool, free: bool) -> str:
    """What is wrong with a label rectangle that cannot be put where it was asked, in the sheet's language."""
    problems = []
    if not fits:
        problems.append(
            ctx.say("queda fuera del marco o sobre el rótulo", "is outside the frame or over the title block")
        )
    if not free:
        problems.append(ctx.say("se solapa con otra cosa de la hoja", "overlaps something else on the sheet"))
    where = f"[{ctx.num(rect[0])}, {ctx.num(rect[1])}] - [{ctx.num(rect[2])}, {ctx.num(rect[3])}]"
    return ctx.say(
        f"la llamada ({where}) {' y '.join(problems)}: muévela con offset_mm o side",
        f"the callout ({where}) {' and '.join(problems)}: move it with offset_mm or side",
    )


FRAME_CELL_MM = 7.0  # the height of a feature control frame


def frame_slot(ctx: Context, elbow: Point, direction: float) -> tuple[float, float, float, float] | None:
    """The room a tolerance frame needs under the shelf of the callout being placed (``None`` when none hangs from it): the
    frames join the shelf at the elbow, one under the other, running the way the shelf runs (``ctx.extras["frame_room"]``
    maps an item id to the width and the number of its frames; ``items_gdt`` fills it)."""
    need = ctx.extras.get("frame_room", {}).get(ctx.extras.get("current"))
    if not need:
        return None
    width, count = need
    x0 = elbow[0] if direction > 0 else elbow[0] - width
    return (x0, elbow[1] - count * FRAME_CELL_MM, x0 + width, elbow[1] - 0.05)


def _slot_ok(ctx: Context, elbow: Point, direction: float) -> bool:
    slot = frame_slot(ctx, elbow, direction)
    return slot is None or (ctx.inside(slot) and ctx.free(slot) and _clear_of_views(ctx, slot))


def _clear_of_views(ctx: Context, rect: tuple[float, float, float, float]) -> bool:
    """Whether a callout's text stays off every view of the sheet (the isometric one too): a text belongs beside the
    part, not on it."""
    boxes = [v.box for v in ctx.views.values()] + list(ctx.extras.get("view_boxes", []))
    return not any(
        rect[0] < b[2] - 0.5 and b[0] + 0.5 < rect[2] and rect[1] < b[3] - 0.5 and b[1] + 0.5 < rect[3] for b in boxes
    )


def _leader_clear(ctx: Context, tip: Point, elbow: Point) -> bool:
    """Whether the leader from ``tip`` to ``elbow`` goes through no text already placed (the callouts, the texts of the
    dimensions): it may cross the lines of a dimension, never a text."""
    strips = ctx.extras.get("strips", [])
    boxes = [r for r in ctx.occupied if r not in strips] + list(ctx.extras.get("texts", []))
    return not any(segment_hits_rect((tip[0], tip[1], elbow[0], elbow[1]), box) for box in boxes)


Segment = tuple[float, float, float, float]


def _bounds_miss(a: Segment, b: Segment) -> bool:
    """Whether the boxes of two segments do not even touch (a cheap way out before the exact test)."""
    return (
        max(a[0], a[2]) < min(b[0], b[2])
        or max(b[0], b[2]) < min(a[0], a[2])
        or max(a[1], a[3]) < min(b[1], b[3])
        or max(b[1], b[3]) < min(a[1], a[3])
    )


def _count_crossings(seg: Segment, others: list[Segment]) -> int:
    return sum(1 for o in others if not _bounds_miss(seg, o) and segments_cross(seg, o))


def _leader_parts(tip: Point, elbow: Point, rect: tuple[float, float, float, float], direction: float) -> list[Segment]:
    """The two straight pieces of a leader: the line from the feature to the elbow and the shelf under its text."""
    end = elbow[0] + direction * (rect[2] - rect[0])
    return [(tip[0], tip[1], elbow[0], elbow[1]), (elbow[0], elbow[1], end, elbow[1])]


def _crosses_leaders(ctx: Context, tip: Point, elbow: Point, rect: tuple[float, float, float, float], sx: float) -> int:
    """How many times a leader about to be placed crosses a leader (or a shelf) already placed."""
    earlier = ctx.extras.get("leaders", [])
    others = [(t["tip"][0], t["tip"][1], t["elbow"][0], t["elbow"][1]) for t in earlier] + [
        (t["elbow"][0], t["elbow"][1], t["end"][0], t["end"][1]) for t in earlier
    ]
    return sum(_count_crossings(part, others) for part in _leader_parts(tip, elbow, rect, sx))


def view_segments(ctx: Context, view: ViewInfo) -> list[Segment]:
    """The visible outline of the views as straight pieces (what a leader may cross once), or the four sides of ``view``'s
    box when the context carries no projected edges (a hand-made one)."""
    cached = ctx.extras.get("view_segments")
    if cached is None:
        ink = [e for e in ctx.extras.get("view_ink", []) if getattr(e, "layer", None) == CONTOUR]
        cached = segments_of(ink)
        ctx.extras["view_segments"] = cached
    if cached:
        return cached
    x0, y0, x1, y1 = view.box
    return [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]


LEADER_ANGLES = (45, 135, 225, 315, 30, 60, 120, 150, 210, 240, 300, 330)  # never along a centre line: no 0 / 90
MAX_CALLOUT_HOLES = 12
DIMENSION_CROSS_COST_MM = (
    8.0  # a leader that crosses a line of a dimension (extension or dimension line) costs this much length
)
DIMENSION_LINE_COST_MM = (
    30.0  # and one more for each dimension line proper (the one with arrows): it is a cut through a dimension
)
ANGLE_COST_PER_DEG = 0.08  # a leader at 45 degrees is preferred to a flatter or steeper one of the same length


ELBOW_CLEARANCE_MM = 3.0  # the elbow of a leader stays this far from a line of a dimension


def _point_segment_distance(p: Point, s: Segment) -> float:
    """How far (mm) ``p`` is from the segment ``s``."""
    dx, dy = s[2] - s[0], s[3] - s[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / length2))
    return math.hypot(p[0] - (s[0] + t * dx), p[1] - (s[1] + t * dy))


def _ray_exit(box: tuple[float, float, float, float], p: Point, u: Point) -> float:
    """How far (mm) the ray from ``p`` along ``u`` runs before it leaves ``box``."""
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


def place_callout(
    ctx: Context,
    view: ViewInfo,
    holes: list[tuple[Point, float]],
    lines: list[str],
    offset_mm: float | None = None,
    radial: list[float | None] | None = None,
) -> tuple[list[Entity], int]:
    """The callout of a group of equal holes (each ``(centre, radius)`` on the page): ONE leader, short, to the hole and
    the side where it is cleanest. Every hole of the group, at every angle that is no centre line, is tried; the leader is
    kept that (a) crosses no other leader, (b) crosses the outline of the part at most once, (c) puts its text outside
    the part, on free room inside the frame; among those the shortest wins, with a little against the ones that cross a
    dimension's line. Returns the entities and the index (into ``holes``) of the hole the leader goes to. When no angle
    is clean the least bad one is kept and the item's result says what is wrong with it."""
    gap = (CALLOUT_GAP_MM + ctx.nudge() * NUDGE_MM) if offset_mm is None else offset_mm
    box = view.box
    outline = view_segments(ctx, view)
    dimension_lines = ctx.extras.get("dimension_lines", [])
    main_lines = ctx.extras.get("dimension_main_lines", [])
    order = sorted(
        range(len(holes)),
        key=lambda i: min(
            holes[i][0][0] - box[0], box[2] - holes[i][0][0], holes[i][0][1] - box[1], box[3] - holes[i][0][1]
        ),
    )[:MAX_CALLOUT_HOLES]
    best: (
        tuple[tuple[int, int, float], list[Entity], tuple[float, float, float, float], Point, Point, float, int, str]
        | None
    ) = None
    for index in order:
        centre, radius = holes[index]
        for angle in LEADER_ANGLES:
            if radial is not None and radial[index] is not None and abs(((angle - radial[index]) + 90.0) % 180.0 - 90.0) < RADIAL_CLEAR_DEG:
                continue  # a leader never runs along the radial centre line of a hole of a circular pattern
            rad = math.radians(angle)
            u = (math.cos(rad), math.sin(rad))
            tip = (centre[0] + u[0] * radius, centre[1] + u[1] * radius)
            sx = 1.0 if u[0] > 0 else -1.0
            start = _ray_exit(box, tip, u) + gap
            for step in range(NUDGE_TRIES):
                length = max(MIN_LEADER_MM, start + step * NUDGE_MM)
                elbow = (tip[0] + u[0] * length, tip[1] + u[1] * length)
                entities, rect = leader(tip, elbow, lines, "right" if sx > 0 else "left")
                fits = ctx.inside(rect)
                free = (
                    ctx.free(rect)
                    and _clear_of_views(ctx, rect)
                    and _leader_clear(ctx, tip, elbow)
                    # the text and its shelf lie on no dimension line, and the elbow is clear of them too
                    and not any(
                        segment_hits_rect(s, (rect[0], rect[1] - 0.6, rect[2], rect[3])) for s in dimension_lines
                    )
                    and all(_point_segment_distance(elbow, s) >= ELBOW_CLEARANCE_MM for s in dimension_lines)
                )
                crossing = _crosses_leaders(ctx, tip, elbow, rect, sx)
                cuts = _count_crossings((tip[0], tip[1], elbow[0], elbow[1]), outline)
                dims = _count_crossings((tip[0], tip[1], elbow[0], elbow[1]), dimension_lines)
                mains = _count_crossings((tip[0], tip[1], elbow[0], elbow[1]), main_lines)
                cost = (
                    length
                    + DIMENSION_CROSS_COST_MM * dims
                    + DIMENSION_LINE_COST_MM * mains
                    + ANGLE_COST_PER_DEG * abs((angle % 90) - 45)
                )
                slot_bad = 0 if _slot_ok(ctx, elbow, sx) else 1  # no room for the frames: second to a clean place
                bad = (0 if fits else 4) + 3 * crossing + 2 * max(0, cuts - 1) + (0 if free else 1)
                problem = ""
                if crossing:
                    problem = "crosses another leader"
                elif cuts > 1:
                    problem = f"crosses the outline of the part {cuts} times"
                elif not fits or not free:
                    problem = why_not(ctx, rect, fits, free)
                if best is None or (bad, slot_bad, cost) < best[0]:
                    best = ((bad, slot_bad, cost), entities, rect, tip, elbow, sx, index, problem)
                if bad == 0 and not slot_bad:
                    break  # the shortest length of this hole and angle that is clean
    assert best is not None
    (bad, _, _), entities, rect, tip, elbow, sx, index, problem = best
    if bad:
        ctx.note(problem or why_not(ctx, rect, ctx.inside(rect), ctx.free(rect)))
    ctx.reserve(rect)
    _remember(ctx, tip, elbow, sx, rect)
    return entities, index


def planned_elbow(ctx: Context) -> Point | None:
    """The ``elbow_mm`` of the item being drawn: the vector from the tip of its leader to the elbow the global layout chose."""
    current = ctx.extras.get("current")
    for spec in ctx.extras.get("items", []):
        if spec.get("id") == current and spec.get("elbow_mm") is not None:
            return (float(spec["elbow_mm"][0]), float(spec["elbow_mm"][1]))
    return None


def leader_at(ctx: Context, tip: Point, elbow: Point, lines: list[str]) -> list[Entity]:
    """A leader from ``tip`` to exactly ``elbow``, its shelf running the way the leader goes; when the place is not clean
    the item's result says why (it is the layout's decision, not a search)."""
    sx = 1.0 if elbow[0] - tip[0] >= 0 else -1.0
    entities, rect = leader(tip, elbow, lines, "right" if sx > 0 else "left")
    fits = ctx.inside(rect)
    free = (
        ctx.free(rect)
        and _clear_of_views(ctx, rect)
        and _leader_clear(ctx, tip, elbow)
        and not _crosses_leaders(ctx, tip, elbow, rect, sx)
    )
    if not (fits and free):
        ctx.note(why_not(ctx, rect, fits, free))
    ctx.reserve(rect)
    _remember(ctx, tip, elbow, sx, rect)
    return entities


def place_leader(
    ctx: Context,
    view: ViewInfo,
    tip: Point,
    diagonal: tuple[float, float],
    lines: list[str],
    offset_mm: float | None,
    explicit: bool = False,
    along: tuple[float, float] | None = None,
) -> list[Entity]:
    """A leader from ``tip`` along ``diagonal`` to an elbow beyond the edge of ``view`` (``offset_mm`` farther, default
    ``CALLOUT_GAP_MM``), its text on a shelf. ``along``: an exact direction (page vector, any length) the leader takes instead
    of a diagonal: only that direction is tried (the elbow is nudged outward along it), ``diagonal`` and the other
    diagonals are ignored (a chamfer callout goes along the outward normal of the chamfer line). When the text would land on something already placed or outside the
    frame it is moved outward, and when that does not help the other diagonals are tried (the one asked for comes
    first) -- unless the item asked for its place (``explicit``: ``side`` / ``offset_mm``): then it goes exactly there
    and, when that is not a clean place, the item's result says why. The text's rectangle is reserved so the next
    callout avoids it; a callout that finds no clean place at all is reported too. An item that carries ``elbow_mm`` (the
    global layout put it there: the vector from the tip to the elbow, page mm, y up) ends exactly there."""
    planned = planned_elbow(ctx)
    if planned is not None:
        return leader_at(ctx, tip, (tip[0] + planned[0], tip[1] + planned[1]), lines)
    gap = (CALLOUT_GAP_MM + ctx.nudge() * NUDGE_MM) if offset_mm is None else offset_mm
    box = view.box
    sx0, sy0 = diagonal
    step: Point | None = None  # the exact direction asked for, as a unit vector
    if along is not None and math.hypot(*along) > 1e-9:
        reach = math.hypot(*along)
        step = (along[0] / reach, along[1] / reach)
        sx0, sy0 = (1.0 if step[0] >= 0 else -1.0), (1.0 if step[1] >= 0 else -1.0)
    candidates = [(sx0, sy0)] if explicit or step is not None else [(sx0, sy0), (-sx0, sy0), (sx0, -sy0), (-sx0, -sy0)]
    best: tuple[list[Entity], tuple[float, float, float, float], Point, float, bool, bool] | None = None
    fallback = best  # the first clean place, kept when no clean place has room for the frames hung from the callout
    for sx, sy in candidates:
        need_x = (box[2] - tip[0] if sx > 0 else tip[0] - box[0]) + gap
        need_y = (box[3] - tip[1] if sy > 0 else tip[1] - box[1]) + gap
        distance = max(MIN_LEADER_MM, min(need_x, need_y))
        if step is not None:
            distance = max(MIN_LEADER_MM, _ray_exit(box, tip, step) + gap)
        vx, vy = step if step is not None else (sx, sy)
        for _ in range(1 if explicit else NUDGE_TRIES):
            elbow = (tip[0] + vx * distance, tip[1] + vy * distance)
            entities, rect = leader(tip, elbow, lines, "right" if sx > 0 else "left")
            fits = ctx.inside(rect)
            free = (
                ctx.free(rect)
                and _clear_of_views(ctx, rect)
                and _leader_clear(ctx, tip, elbow)
                and not _crosses_leaders(ctx, tip, elbow, rect, sx)
            )
            roomy = _slot_ok(ctx, elbow, sx)
            if best is None or (fits and not best[4]):  # the first try, or the first that is inside the frame
                best = (entities, rect, elbow, sx, fits, free)
            if fits and free:
                if fallback is None:
                    fallback = (entities, rect, elbow, sx, fits, free)
                if roomy:
                    ctx.reserve(rect)
                    _remember(ctx, tip, elbow, sx, rect)
                    return entities
            distance += NUDGE_MM
    assert best is not None
    entities, rect, elbow, sx, fits, free = fallback or best
    if not (fits and free):
        ctx.note(why_not(ctx, rect, fits, free))
    ctx.reserve(rect)
    _remember(ctx, tip, elbow, sx, rect)
    return entities


def _remember(
    ctx: Context, tip: Point, elbow: Point, direction: float, rect: tuple[float, float, float, float]
) -> None:
    """Keep where the callout of the item being drawn ended up, so a feature control frame can hang from it
    (``ctx.extras["anchors"][item id]`` is a list of ``{"tip", "elbow", "dir", "rect"}``; ``dir`` is +1 when the shelf
    runs to the right and -1 to the left)."""
    segments = ctx.extras.setdefault("segments", [])  # the lines later labels keep clear of
    segments.append((tip, elbow))
    segments.append((elbow, (elbow[0] + direction * (rect[2] - rect[0]), elbow[1])))
    ctx.extras.setdefault("leaders", []).append(
        {
            "item": ctx.extras.get("current"),
            "tip": tip,
            "elbow": elbow,
            "end": (elbow[0] + direction * (rect[2] - rect[0]), elbow[1]),
        }
    )
    item = ctx.extras.get("current")
    slot = frame_slot(ctx, elbow, direction)
    if slot is not None and item is not None:
        ctx.reserve(slot)
        ctx.extras.setdefault("frame_slots", {})[item] = slot
    if item is not None:
        ctx.extras.setdefault("anchors", {}).setdefault(item, []).append(
            {"tip": tip, "elbow": elbow, "dir": direction, "rect": rect}
        )
