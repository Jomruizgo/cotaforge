# ruff: noqa: E501
"""The indications of a moulded part's drawing, after ISO 10135 (no FreeCAD import): ``parting_line``, ``pull_direction``,
``mould_zone`` (gate, ejector marks) and ``no_draft``. Until now they were notes of plain text.

WHAT FOLLOWS THE STANDARD, AND WHAT DOES NOT. ISO 10135 is about the indications a drawing of a moulded part gives the mould
maker: where the mould parts, the direction it opens and the draft, where the gate may be, where ejector marks are allowed, and
the faces that take no draft. The harness draws those five indications. The text of the standard was not at hand when this was
written: no clause is quoted, and the SHAPE of each symbol below is the harness's reading of the convention, to be checked
against the standard by whoever has it (``drawing_requirements`` says the same about every standard it names). Each symbol is
made of the sheet's own lines, as the weld symbol of ISO 2553 is (``items_weld``).

* ``parting_line``: the line where the two mould halves meet, in its OWN line type (thick, one long dash and two short ones:
  layer ``PARTICION``). In a view across the pull direction it is the straight line at the level of the parting plane; in the view
  that looks along the pull it is the outline of the part at that level. The pull direction (which half moves where) and the level
  come from the model when the item gives none: the axis the part is shallowest along, towards the side its mass is away from (the
  open side of a cavity), and the plane where the outline of the section is widest, put on the planar face that is there (a shoulder).
* ``pull_direction``: the direction the moving half opens, an arrow with a bar at its tail, and the general draft with its symbol
  (a wedge) and its value (``1,5°``): the draft the drawing asks of every face that parallels the pull and says nothing else.
* ``mould_zone``: a permitted zone, hatched, with its symbol joined by a leader: ``gate`` (circle with ``G``: where the melt may
  enter) and ``ejector`` (square with ``E``: where ejector marks are allowed). Seen face-on the zone is a hatched circle; seen
  edge-on, a thick band on the surface.
* ``no_draft``: an exception to the general draft: a leader with ``0°`` (a face that takes none) or ``R0`` (a sharp edge).

Every one is the designer's decision except the parting line and the pull arrow, which are measured.
"""

import math
from typing import Any

from items_common import VIEW_ORDER, _ray_exit
from sheet_context import (
    COMMON_FIELDS,
    Context,
    ItemError,
    ItemType,
    Point,
    Vec3,
    ViewInfo,
    check_fields,
    v_enum,
    v_num,
    v_point3,
    v_points3,
)
from sheet_draw import arrow
from sheet_model import DIMENSIONS, HATCH, PARTING, TEXT, ZONE, Circle, Entity, Line, Polyline, Text, text_width

PULLS = ("+x", "-x", "+y", "-y", "+z", "-z", "x", "y", "z")
ZONE_KINDS = ("gate", "ejector")
EXCEPTION_KINDS = ("no_draft", "sharp_edge")
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
TIE = 0.02  # two sizes within this share are the same size (the axis a part is shallowest along: the tie goes to z)
PLATEAU = 0.005  # a section whose area is within this share of the widest one is as wide
SNAP_MM = 1.5  # the parting plane is put on a planar face this near it
END_MM = 4.0  # the parting line reaches this far past the part
ARROW_MM = 16.0
SYMBOL_MM = 7.0  # the box of the symbol of a zone (a circle's diameter, a square's side)
HATCH_STEP_MM = 1.6
DISTANCES = (9.0, 13.0, 18.0, 25.0, 34.0)
DIRECTIONS = (45, 135, 315, 225, 30, 60, 120, 150, 330, 300, 240, 210)
OFFSET_MM = 1.4  # the parting line in the plan view runs this far outside the outline of the part


def _v_pull(value: object) -> str:
    return v_enum(*PULLS)(value)


def _v_axis(value: object) -> str:
    return v_enum("x", "y", "z")(value)


CHECKS_PARTING = {"pull": _v_pull, "level_mm": v_num(-100000.0, 100000.0), "view": COMMON_FIELDS["view"]}
CHECKS_PULL = {
    "pull": _v_pull,
    "draft_deg": v_num(0.0, 45.0),
    "view": COMMON_FIELDS["view"],
    "side": COMMON_FIELDS["side"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
}
CHECKS_ZONE = {
    "kind": v_enum(*ZONE_KINDS),
    "at": v_points3,
    "radius_mm": v_num(0.1, 500.0),
    "normal": _v_axis,
    "view": COMMON_FIELDS["view"],
}
CHECKS_EXCEPTION = {
    "kind": v_enum(*EXCEPTION_KINDS),
    "at": v_point3,
    "view": COMMON_FIELDS["view"],
    "side": COMMON_FIELDS["side"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
}


def check_parting(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, CHECKS_PARTING)


def check_pull(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, CHECKS_PULL)


def check_zone(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, CHECKS_ZONE, required=("kind", "at"))


def check_exception(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, CHECKS_EXCEPTION, required=("at",))


# --- what the model proposes (pure) ---------------------------------------------------------------------------------------


def pull_vector(text: str) -> Vec3:
    """The unit vector of ``+z``, ``-x``, ``y``..."""
    out = [0.0, 0.0, 0.0]
    out[AXIS_INDEX[text.lstrip("+-")]] = -1.0 if text.startswith("-") else 1.0
    return (out[0], out[1], out[2])


def shortest_axis(low: Vec3, high: Vec3) -> int:
    """The axis the part is shallowest along (the shortest draw of the mould); the tie goes to z, then y, then x."""
    sizes = [h - lo for lo, h in zip(low, high, strict=True)]
    smallest = min(sizes)
    return next(i for i in (2, 1, 0) if sizes[i] <= smallest * (1 + TIE) + 1e-9)


def open_sign(low: Vec3, high: Vec3, centre: Vec3, axis: int) -> float:
    """+1 when the mass of the part lies below the middle of its box along ``axis`` (the cavity opens upwards), -1 above."""
    return 1.0 if (low[axis] + high[axis]) / 2 - centre[axis] >= -0.5 else -1.0


def parting_level(samples: list[tuple[float, float]], planes: list[float], sign: float) -> float | None:
    """Where the parting plane goes: the coordinate where the outline of the section is widest (``samples``: (coordinate, area)),
    the end of the plateau on the open side when several are as wide, put on the planar face (``planes``) within ``SNAP_MM``."""
    if not samples:
        return None
    best = max(area for _, area in samples)
    plateau = [c for c, area in samples if area >= best * (1 - PLATEAU)]
    level = max(plateau) if sign > 0 else min(plateau)
    near = [p for p in planes if abs(p - level) <= SNAP_MM]
    return round(min(near, key=lambda p: abs(p - level)) if near else level, 3)


class Measured:
    """The pull direction and the level of the parting plane of the part, from the item or from the model."""

    def __init__(self, pull: Vec3, level: float | None, source: str) -> None:
        self.pull = pull
        self.level = level
        self.source = source


def resolve(ctx: Context, spec: dict[str, Any]) -> Measured:
    """The pull direction (``pull`` of the item, else the one of an item of the sheet, else the model's proposal) and the level of the
    parting plane (``level_mm``, else the model's)."""
    items = {str(i.get("id")): i for i in ctx.extras.get("sheet_items", []) if isinstance(i, dict)}
    asked = spec.get("pull") or next(
        (i["pull"] for i in items.values() if i.get("type") in ("parting_line", "pull_direction") and i.get("pull")), None
    )
    box = ctx.geometry.mould_box()
    if asked is not None:
        text = str(asked)
        pull = pull_vector(text if text[0] in "+-" else "+" + text)
        if text[0] not in "+-" and box is not None:  # an axis without a sign: the open side
            axis = AXIS_INDEX[text]
            pull = pull_vector(("+" if open_sign(*box, axis) > 0 else "-") + text)
        source = "given"
    elif box is not None:
        axis = shortest_axis(box[0], box[1])
        sign = open_sign(*box, axis)
        pull = pull_vector(("+" if sign > 0 else "-") + "xyz"[axis])
        source = "measured: the part is shallowest along " + "xyz"[axis].upper() + " and its mass lies away from the " + ("+" if sign > 0 else "-") + "side"
    else:
        msg = ctx.say(
            "la pieza no da medidas para proponer la dirección de desmoldeo: da pull=+z|-z|+x|-x|+y|-y",
            "the part gives no measures to propose the pull direction: give pull=+z|-z|+x|-x|+y|-y",
        )
        raise ItemError(msg)
    level = spec.get("level_mm")
    if level is None:
        level = next((i["level_mm"] for i in items.values() if i.get("type") == "parting_line" and i.get("level_mm") is not None), None)
    if level is None and box is not None:
        axis = max(range(3), key=lambda k: abs(pull[k]))
        sign = pull[axis]
        cache: dict[Vec3, tuple[list[tuple[float, float]], list[float]]] = ctx.extras.setdefault("mould_cache", {})
        if pull not in cache:  # the sections of the part are sampled once for the whole sheet (every mould item asks)
            cache[pull] = (ctx.geometry.outline_areas(pull), ctx.geometry.axial_planes(pull))
        found = parting_level(cache[pull][0], cache[pull][1], sign)
        if found is not None:
            level = found
            source += "; level: where the outline of the section is widest"
    return Measured(pull, None if level is None else float(level), source)


# --- placing on the page -------------------------------------------------------------------------------------------------------


def _unit(v: Point) -> Point:
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-9 else (1.0, 0.0)


def _across(ctx: Context, pull: Vec3, wanted: str | None) -> list[ViewInfo]:
    """The standard views that show the pull direction in their plane (the parting plane is a line there), the asked one first."""
    names = [wanted] if wanted else [n for n in ("front", "right", "top") if n in ctx.views]
    return [ctx.views[n] for n in names if n in ctx.views and ctx.views[n].shows_axis_along(pull)]


def _along(ctx: Context, pull: Vec3, wanted: str | None) -> list[ViewInfo]:
    names = [wanted] if wanted else [n for n in VIEW_ORDER if n in ctx.views]
    return [ctx.views[n] for n in names if n in ctx.views and ctx.views[n].looks_along(pull)]


def _clear_of_views(ctx: Context, rect: tuple[float, float, float, float]) -> bool:
    boxes = [v.box for v in ctx.views.values()] + list(ctx.extras.get("view_boxes", []))
    return not any(rect[0] < b[2] - 0.5 and b[0] + 0.5 < rect[2] and rect[1] < b[3] - 0.5 and b[1] + 0.5 < rect[3] for b in boxes)


def _bounds(entities: list[Entity]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        if isinstance(e, Line):
            xs += [e.x1, e.x2]
            ys += [e.y1, e.y2]
        elif isinstance(e, Polyline):
            xs += [p[0] for p in e.points]
            ys += [p[1] for p in e.points]
        elif isinstance(e, Circle):
            xs += [e.cx - e.r, e.cx + e.r]
            ys += [e.cy - e.r, e.cy + e.r]
        elif isinstance(e, Text):
            w = text_width(e.text, e.height)
            x0 = e.x - (w / 2 if e.anchor == "center" else w if e.anchor == "right" else 0.0)
            xs += [x0, x0 + w]
            ys += [e.y - e.height * 0.2, e.y + e.height]
        elif hasattr(e, "points"):
            xs += [p[0] for p in e.points]
            ys += [p[1] for p in e.points]
    return (min(xs), min(ys), max(xs), max(ys))


SNAP_X_MM = 7.5  # the window pulls a symbol whose centre comes within 6 mm (across the page) of a view origin or another symbol ON it
NOTE_GUIDE_MM = 87.5  # the notes block is made of lines centred this far from its left edge (``sheet_native.NOTE_BLOCK_MM`` / 2)


def _x_guides(ctx: Context) -> list[float]:
    """The places across the page the window pulls a symbol onto when its centre is near them: the origins of the views, the
    middle of the notes block, the symbols of the mould items already placed. A symbol cannot be shifted across the page to get
    clear of them (``sheet_mirror.shift_for`` shifts only up the page), so its own drawing has to stand clear."""
    xs = [v.x for v in ctx.views.values()] + [v.x for v in ctx.extras.get("views_extra", {}).values()]
    if ctx.extras.get("note_lines"):
        xs.append(float(ctx.extras.get("note_x", ctx.area[0] + 2.0)) + NOTE_GUIDE_MM)
    return [*xs, *ctx.extras.get("mould_centres", [])]


def _free_place(
    ctx: Context, build: Any, anchors: list[Point], spec: dict[str, Any]
) -> tuple[list[Entity], tuple[float, float, float, float]]:
    """The first of ``anchors`` where ``build(anchor)`` -- the entities and the rectangle of the symbol itself (a leader may cross
    the part, the symbol may not stand on it) -- lies inside the frame, on free paper and off the views, and whose drawing as a
    whole has its centre clear of the places the window snaps a symbol to; the least bad one (inside the frame, first) when none is
    clean, and the item's result says so."""
    guides = _x_guides(ctx)
    best: tuple[list[Entity], tuple[float, float, float, float], bool] | None = None
    for anchor in anchors:
        entities, rect = build(anchor)
        box = _bounds(entities)
        snapped = any(abs((box[0] + box[2]) / 2 - g) <= SNAP_X_MM for g in guides)
        fits, free = ctx.inside(rect), ctx.free(rect) and _clear_of_views(ctx, rect)
        if fits and free and not snapped:
            ctx.extras.setdefault("mould_centres", []).append((box[0] + box[2]) / 2)
            return entities, rect
        if best is None or (fits and not best[2]):
            best = (entities, rect, fits)
    assert best is not None
    entities, rect, _ = best
    ctx.note(ctx.say("el símbolo no tiene un sitio limpio: muévelo con offset_mm o side", "the symbol has no clean place: move it with offset_mm or side"))
    return entities, rect


# --- parting_line ---------------------------------------------------------------------------------------------------------------


def _label(ctx: Context) -> str:
    return ctx.say("LP", "PL")  # línea de partición / parting line


def _label_at(ctx: Context, text: str, spots: list[tuple[float, float, str]]) -> Text:
    """The label of the parting line at the first of ``spots`` ((x, baseline y, anchor)) where its box is inside the frame, on free
    paper and across no dimension line; the first one when none is clean. Its box is reserved: the symbols placed later keep off it."""
    from sheet_collide import segment_hits_rect  # noqa: PLC0415

    lines = ctx.extras.get("dimension_lines", [])
    chosen: Text | None = None
    for x, y, anchor in spots:
        label = Text(x, y, text, 3.5, TEXT, anchor)
        box = _bounds([label])
        if chosen is None:
            chosen = label
        if ctx.inside(box) and ctx.free(box) and not any(segment_hits_rect(line, box) for line in lines):
            chosen = label
            break
    assert chosen is not None
    ctx.reserve(_bounds([chosen]))
    return chosen


def _offset_loop(points: list[Point], distance: float) -> list[Point]:
    """``points`` (a closed loop) moved ``distance`` outward along the average normal of each corner."""
    area = sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points))) / 2
    sign = 1.0 if area > 0 else -1.0  # counter-clockwise: the outside is to the right of each edge
    out: list[Point] = []
    count = len(points)
    for i, p in enumerate(points):
        a, b = points[i - 1], points[(i + 1) % count]
        n1 = _unit((p[1] - a[1], -(p[0] - a[0])))
        n2 = _unit((b[1] - p[1], -(b[0] - p[0])))
        normal = _unit((n1[0] + n2[0], n1[1] + n2[1]))
        out.append((p[0] + sign * normal[0] * distance, p[1] + sign * normal[1] * distance))
    return out


def draw_parting(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    found = resolve(ctx, spec)
    if found.level is None:
        msg = ctx.say(
            "no se pudo medir el nivel del plano de partición en el modelo: da level_mm (la coordenada, en mm, a lo largo de la dirección de desmoldeo)",
            "the level of the parting plane could not be measured on the model: give level_mm (the coordinate, in mm, along the pull direction)",
        )
        raise ItemError(msg)
    pull = found.pull
    axis = [abs(c) for c in pull].index(1.0)
    drawn: list[str] = []
    for view in [*_across(ctx, pull, spec.get("view")), *_along(ctx, pull, spec.get("view"))]:
        at = tuple(found.level if i == axis else c for i, c in enumerate(view.centre))  # the level is the coordinate on the axis
        entities: list[Entity]
        if view.shows_axis_along(pull):
            px, py = view.project(at)  # type: ignore[arg-type]
            a = _unit(view.axis(pull))
            t = (-a[1], a[0])
            if t[0] < -1e-9 or (abs(t[0]) <= 1e-9 and t[1] < 0):  # drawn left to right (or upwards), the label at the far end
                t = (-t[0], -t[1])
            x0, y0, x1, y1 = view.box
            along = [((cx - px) * t[0] + (cy - py) * t[1]) for cx, cy in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
            lo, hi = min(along) - END_MM, max(along) + END_MM
            start, end = (px + t[0] * lo, py + t[1] * lo), (px + t[0] * hi, py + t[1] * hi)
            entities = [Line(start[0], start[1], end[0], end[1], PARTING)]
            word = _label(ctx)
            far, near = (1.5, "left" if t[0] >= 0 else "right"), (-1.5, "right" if t[0] >= 0 else "left")
            spots = [
                (end[0] + far[0] * t[0], end[1] + far[0] * t[1] - 1.2, far[1]),  # at the far end of the line
                (start[0] + near[0] * t[0], start[1] + near[0] * t[1] - 1.2, near[1]),  # else at the other end
                (end[0] + far[0] * t[0], end[1] + far[0] * t[1] + 2.5, far[1]),  # else just above the far end
                (start[0] + near[0] * t[0], start[1] + near[0] * t[1] - 5.0, near[1]),  # else just below the other
            ]
            entities.append(_label_at(ctx, word, spots))
            drawn.append(f"{view.name} view: line at {'xyz'[axis]}={found.level:g}")
        else:
            loop = ctx.geometry.section_outline(pull, found.level, view.centre, view.xdir, view.direction)
            if not loop:
                continue
            page = [(view.x + p[0] * view.scale, view.y + p[1] * view.scale) for p in loop]
            ring = _offset_loop(page, OFFSET_MM)
            entities = [Polyline(tuple(ring), PARTING, closed=True)]
            top = max(ring, key=lambda p: p[1])
            low = min(ring, key=lambda p: p[1])
            spots = [
                (top[0] + 2.0, top[1] + 1.0, "left"),
                (top[0] - 2.0, top[1] + 1.0, "right"),
                (low[0] + 2.0, low[1] - 4.5, "left"),
                (low[0] - 2.0, low[1] - 4.5, "right"),
            ]
            entities.append(_label_at(ctx, _label(ctx), spots))
            drawn.append(f"{view.name} view: outline of the part at {'xyz'[axis]}={found.level:g}")
        ctx.add(entities)
        ctx.used_view(view.name)
    if not drawn:
        msg = ctx.say(
            "ninguna vista de la hoja puede llevar la línea de partición con esa dirección de desmoldeo",
            "no view of the sheet can carry the parting line with that pull direction",
        )
        raise ItemError(msg)
    ctx.extras["mould"] = {"pull": pull, "level": found.level}
    sign = "+" if pull[axis] > 0 else "-"
    return "ok", f"parting line, pull {sign}{'xyz'[axis]} ({found.source}); " + "; ".join(drawn)


# --- pull_direction -------------------------------------------------------------------------------------------------------------


def _wedge(x: float, y: float) -> list[Entity]:
    """The draft symbol: a thin wedge (a right triangle whose hypotenuse leans off the pull direction), its foot at (x, y)."""
    return [Polyline(((x, y), (x + 9.0, y), (x + 9.0, y + 2.4)), DIMENSIONS, closed=True)]


def _pull_symbol(ctx: Context, anchor: Point, a: Point, draft: float | None) -> list[Entity]:
    """The arrow of the opening direction (a bar at its tail, the head at the end) with the wedge and the value of the draft beside it."""
    tail = anchor
    tip = (anchor[0] + a[0] * ARROW_MM, anchor[1] + a[1] * ARROW_MM)
    n = (-a[1], a[0])
    out: list[Entity] = [
        Line(tail[0], tail[1], tip[0], tip[1], DIMENSIONS),
        arrow(tip, a),
        Line(tail[0] - n[0] * 2.5, tail[1] - n[1] * 2.5, tail[0] + n[0] * 2.5, tail[1] + n[1] * 2.5, DIMENSIONS),
    ]
    if draft is not None:
        base = (tail[0] + n[0] * 4.0 - 0.0, tail[1] + n[1] * 4.0)
        wx, wy = base[0] + 2.0, base[1] + 1.0
        out += _wedge(wx, wy)
        out.append(Text(wx + 10.5, wy - 0.4, f"{ctx.num(draft)}°", 3.5, TEXT, "left"))
    out.append(Text(tip[0] + n[0] * 2.5, tip[1] + n[1] * 2.5, ctx.say("Apertura", "Opens"), 3.5, TEXT, "left"))
    return out


def draw_pull(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    found = resolve(ctx, spec)
    pull = found.pull
    views = _across(ctx, pull, spec.get("view"))
    if not views:
        msg = ctx.say(
            "ninguna vista de la hoja muestra la dirección de desmoldeo en su plano: añade una vista de frente o de lado",
            "no view of the sheet shows the pull direction in its plane: add a front or side view",
        )
        raise ItemError(msg)
    view = views[0]
    a = _unit(view.axis(pull))
    draft = spec.get("draft_deg")
    x0, y0, x1, y1 = view.box
    sides = [spec["side"]] if spec.get("side") else ["left", "right", "above", "below"]
    gap = spec.get("offset_mm")

    def anchors() -> list[Point]:
        out: list[Point] = []
        for side in sides:
            for extra in ((gap,) if gap is not None else (14.0, 22.0, 30.0, 40.0)):
                if side == "left":
                    out.append((x0 - extra - 10.0, (y0 + y1) / 2 - ARROW_MM / 2 * a[1]))
                elif side == "right":
                    out.append((x1 + extra + 4.0, (y0 + y1) / 2 - ARROW_MM / 2 * a[1]))
                elif side == "above":
                    out.append(((x0 + x1) / 2 - ARROW_MM / 2 * a[0], y1 + extra))
                else:
                    out.append(((x0 + x1) / 2 - ARROW_MM / 2 * a[0], y0 - extra - ARROW_MM))
        return out

    entities, rect = _free_place(ctx, lambda anchor: (lambda e: (e, _bounds(e)))(_pull_symbol(ctx, anchor, a, draft)), anchors(), spec)
    ctx.add(entities)
    ctx.reserve(rect)
    ctx.used_view(view.name)
    axis = [abs(c) for c in pull].index(1.0)
    ctx.extras.setdefault("mould", {}).update({"pull": pull})
    sign = "+" if pull[axis] > 0 else "-"
    said = f", draft {ctx.num(draft)} deg" if draft is not None else ""
    return "ok", f"opening direction {sign}{'xyz'[axis]} ({found.source}){said}, on the {view.name} view"


# --- mould_zone -----------------------------------------------------------------------------------------------------------------


def _hatch_circle(c: Point, r: float) -> list[Entity]:
    """Diagonal hatching (45 degrees) clipped to a circle."""
    out: list[Entity] = []
    k = -2 * r
    while k <= 2 * r:  # lines x - y = k
        d = k / math.sqrt(2)  # distance of the line from the centre (signed)
        if abs(d) < r:
            half = math.sqrt(r * r - d * d)
            mx, my = c[0] + d / math.sqrt(2), c[1] - d / math.sqrt(2)
            ux, uy = 1 / math.sqrt(2), 1 / math.sqrt(2)
            out.append(Line(mx - ux * half, my - uy * half, mx + ux * half, my + uy * half, HATCH))
        k += HATCH_STEP_MM * math.sqrt(2)
    return out


def _symbol(kind: str, centre: Point) -> list[Entity]:
    """The symbol of a zone: a circle with ``G`` (gate) or a square with ``E`` (ejector marks), 7 mm."""
    letter = "G" if kind == "gate" else "E"
    r = SYMBOL_MM / 2
    shape: Entity = (
        Circle(centre[0], centre[1], r, ZONE)
        if kind == "gate"
        else Polyline(((centre[0] - r, centre[1] - r), (centre[0] + r, centre[1] - r), (centre[0] + r, centre[1] + r), (centre[0] - r, centre[1] + r)), ZONE, closed=True)
    )
    return [shape, Text(centre[0], centre[1] - 1.2, letter, 3.5, TEXT, "center")]


def _zone_view(ctx: Context, spec: dict[str, Any], normal: Vec3, outer: bool) -> ViewInfo:
    """The view a zone is drawn in: the asked one; else, for a zone on the outside of the part, the view across its normal (the zone
    is a band on the surface), for one inside, the view along it (a hatched disc)."""
    if spec.get("view"):
        return ctx.view(spec["view"])
    along = _along(ctx, normal, None)
    across = [ctx.views[n] for n in ("front", "right", "top") if n in ctx.views and ctx.views[n].shows_axis_along(normal)]
    order = (across + along) if outer else (along + across)
    if not order:
        msg = ctx.say("ninguna vista muestra la zona: indica view", "no view shows the zone: give view")
        raise ItemError(msg)
    return order[0]


def _on_outside(ctx: Context, p: Vec3, normal: Vec3) -> bool:
    box = ctx.geometry.mould_box()
    if box is None:
        return False
    axis = [abs(c) for c in normal].index(1.0)
    return min(abs(p[axis] - box[0][axis]), abs(p[axis] - box[1][axis])) <= 0.5


def draw_zone(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    kind = spec["kind"]
    radius = float(spec.get("radius_mm", 4.0))
    resolved = resolve(ctx, {})
    normal = pull_vector("+" + spec["normal"]) if spec.get("normal") else resolved.pull
    taken: list[str] = []
    for index, p in enumerate(spec["at"]):
        outer = _on_outside(ctx, p, normal)
        view = _zone_view(ctx, spec, normal, outer)
        centre = view.project(p)
        r = radius * view.scale
        entities: list[Entity]
        if view.looks_along(normal):
            entities = [Circle(centre[0], centre[1], r, ZONE), *_hatch_circle(centre, r)]
            shape = "disc"
        else:
            a = _unit(view.axis(normal))
            t = (-a[1], a[0])
            end0, end1 = (centre[0] - t[0] * r, centre[1] - t[1] * r), (centre[0] + t[0] * r, centre[1] + t[1] * r)
            entities = [Line(end0[0], end0[1], end1[0], end1[1], ZONE, 1.0)]
            for end in (end0, end1):
                entities.append(Line(end[0] - a[0] * 1.5, end[1] - a[1] * 1.5, end[0] + a[0] * 1.5, end[1] + a[1] * 1.5, ZONE))
            shape = "band"
        away = (centre[0] - (view.box[0] + view.box[2]) / 2, centre[1] - (view.box[1] + view.box[3]) / 2)
        if outer and not view.looks_along(normal):  # a zone on the outside, seen edge-on: outwards is along the normal, away from the middle
            a = _unit(view.axis(normal))
            hub = a if a[0] * away[0] + a[1] * away[1] >= 0 else (-a[0], -a[1])
        else:
            hub = _unit(away)

        def build(
            anchor: Point, centre: Point = centre, kind: str = kind, entities: list[Entity] = entities
        ) -> tuple[list[Entity], tuple[float, float, float, float]]:
            join = _unit((anchor[0] - centre[0], anchor[1] - centre[1]))
            edge = (anchor[0] - join[0] * SYMBOL_MM / 2, anchor[1] - join[1] * SYMBOL_MM / 2)
            symbol = _symbol(kind, anchor)
            return [*entities, Line(centre[0], centre[1], edge[0], edge[1], ZONE), *symbol], _bounds(symbol)

        anchors: list[Point] = []
        for angle in DIRECTIONS:
            rad = math.radians(angle)
            d = (math.cos(rad), math.sin(rad))
            if outer and d[0] * hub[0] + d[1] * hub[1] < -0.2:
                continue  # a zone on the outside: its symbol on the outside too
            leave = _ray_exit(view.box, centre, d) + SYMBOL_MM  # a zone inside the part: the symbol beyond the view, the leader across it
            for distance in sorted({*DISTANCES, *(leave + k * 6.0 for k in range(4))}):
                anchors.append((centre[0] + d[0] * distance, centre[1] + d[1] * distance))
        anchors.sort(key=lambda q: (math.dist(q, centre), -((q[0] - centre[0]) * hub[0] + (q[1] - centre[1]) * hub[1])))
        symbol, rect = _free_place(ctx, build, anchors, spec)
        ctx.add(symbol)
        ctx.reserve(rect)
        ctx.used_view(view.name)
        taken.append(f"{kind} zone {index + 1} (radius {ctx.num(radius)} mm) as a {shape} on the {view.name} view")
    return "ok", "; ".join(taken)


# --- no_draft ---------------------------------------------------------------------------------------------------------------------


def draw_exception(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    from items_common import asked, outward_diagonal, place_leader  # noqa: PLC0415 - the leader every callout shares

    kind = spec.get("kind", "no_draft")
    p = spec["at"]
    resolved = resolve(ctx, {})
    views = _across(ctx, resolved.pull, spec.get("view")) or [ctx.views[n] for n in ("front", "right", "top") if n in ctx.views]
    if not views:
        msg = ctx.say("la hoja no tiene vistas estándar", "the sheet has no standard views")
        raise ItemError(msg)
    view = views[0]
    tip = view.project(p)
    text = "0°" if kind == "no_draft" else "R0"
    note = ctx.say("sin desmoldeo", "no draft") if kind == "no_draft" else ctx.say("arista viva", "sharp edge")
    lines = [f"{text} {note}"]
    diagonal = outward_diagonal(view, tip, spec.get("side"))
    ctx.add(place_leader(ctx, view, tip, diagonal, lines, spec.get("offset_mm"), asked(spec)))
    ctx.used_view(view.name)
    return "ok", f"{kind.replace('_', ' ')}: {lines[0]} on the {view.name} view"


ITEMS = (
    ItemType("parting_line", "pl", check_parting, draw_parting, frozenset(CHECKS_PARTING), phase=1),
    ItemType("pull_direction", "pu", check_pull, draw_pull, frozenset(CHECKS_PULL), phase=2),
    ItemType("mould_zone", "mz", check_zone, draw_zone, frozenset(CHECKS_ZONE), phase=2),
    ItemType("no_draft", "nd", check_exception, draw_exception, frozenset(CHECKS_EXCEPTION), phase=1),
)
