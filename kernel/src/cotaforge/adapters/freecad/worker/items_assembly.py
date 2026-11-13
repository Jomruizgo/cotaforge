# ruff: noqa: E501
"""``parts_list`` and ``balloons`` of an assembly drawing (no FreeCAD import).

* ``parts_list`` (ISO 7573): a table above the title block, header at the bottom and the positions numbered upward, one
  row per PART (the components that link to the same part file are one row with their quantity). Positions and
  quantities are always computed from the assembly; the text of a row comes from the part document (name, drawing number
  or source file, material) and the engineer may override the text of a row (``rows``), never its position or quantity.
* ``balloons`` (ISO 6433): one balloon per position, its leader ending in a dot on a face the chosen view shows of one
  instance of the part. The balloons stand in rows and columns just outside the view, leaders straight and never
  crossing; an instance no view of the part shows is reported, not drawn.

The component data and the visibility come from ``Context.assembly`` and ``Geometry.instance_anchors``; the numbers are
the same for both items because both read the same ``Assembly``.
"""

import math
import textwrap
from collections.abc import Sequence
from typing import Any

from items_attach import _point_segment, _segment_distance, _segment_hits_rect
from items_views import room_ok
from sheet_context import (
    Assembly,
    Context,
    ItemError,
    ItemType,
    PartGroup,
    Point,
    Rect,
    ViewInfo,
    check_fields,
    v_enum,
    v_str,
)
from sheet_layout import FRAME_MARGIN_MM, TITLE_BLOCK_TOP_MM, TITLE_BLOCK_WIDTH_MM
from sheet_model import DIMENSIONS, TEXT, TITLE_BLOCK, Circle, Entity, Line, Solid, Text, text_width

# --- the parts list -----------------------------------------------------------------------------------------------------

COLUMNS_MM = (10.0, 12.0, 44.0, 46.0, 34.0, 34.0)  # as wide as the title block (180)
HEADERS = {
    "es": ("Pos.", "Cant.", "Denominación", "Nº de plano / Referencia", "Material", "Observaciones"),
    "en": ("Item", "Qty", "Description", "Drawing no. / Reference", "Material", "Remarks"),
}
CUT_HEADERS = {  # the cut list of a weldment: what the welder cuts, and the length or blank of each piece
    "es": ("Pos.", "Cant.", "Designación", "Longitud / medida (mm)", "Material", "Observaciones"),
    "en": ("Item", "Qty", "Designation", "Length / size (mm)", "Material", "Remarks"),
}
UNNAMED = {"es": "(por definir)", "en": "(to define)"}
CELL_HEIGHT_MM = 3.0
CHAR_MM = CELL_HEIGHT_MM * 0.6
LINE_STEP_MM = 3.6
ROW_MIN_MM = 6.0
CELL_PAD_MM = 1.0
MAX_CELL_LINES = 3
ROW_FIELDS = ("name", "reference", "material", "remarks")
MAX_ROWS = 60


def v_rows(value: Any) -> list[dict[str, str]]:
    """Per-position overrides of the parts list: ``[{"component": "comp:3", "name"?, "reference"?, "material"?,
    "remarks"?}]``; the component names the row (any instance of the part), the quantity is never given."""
    if not isinstance(value, list | tuple) or not value or len(value) > MAX_ROWS:
        msg = f'must be a list of 1 to {MAX_ROWS} rows like {{"component": "comp:3", "name": "..."}}'
        raise ItemError(msg)
    out: list[dict[str, str]] = []
    for row in value:
        if not isinstance(row, dict) or not isinstance(row.get("component"), str) or not row["component"]:
            msg = 'every row needs "component": the id of one component of the position (comp:N)'
            raise ItemError(msg)
        unknown = sorted(set(row) - {"component", *ROW_FIELDS})
        if unknown:
            msg = f"unknown row field(s) {', '.join(unknown)}; a row takes component, {', '.join(ROW_FIELDS)}"
            raise ItemError(msg)
        clean = {"component": row["component"]}
        for name in ROW_FIELDS:
            if name in row:
                clean[name] = v_str(80)(row[name]) if row[name] != "" else ""
        if len(clean) == 1:
            msg = f"row {row['component']!r} overrides nothing: give name, reference, material or remarks"
            raise ItemError(msg)
        out.append(clean)
    return out


PARTS_CHECKS = {"rows": v_rows}


def check_parts_list(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, PARTS_CHECKS)


def _assembly(ctx: Context, what: str) -> Assembly:
    """The things the list and the balloons number: the components of an assembly, or the bodies of a weldment."""
    found = ctx.assembly if ctx.assembly is not None and ctx.assembly.groups else ctx.weldment
    if found is None or not found.groups:
        msg = f"{what} belong to an assembly drawing (or a welded part of several bodies): this document has no components or welded bodies (insert parts, or model the bodies, first)"
        raise ItemError(msg)
    return found


def _wrap(text: str, width_mm: float) -> list[str]:
    chars = max(2, int((width_mm - 2 * CELL_PAD_MM) / CHAR_MM))
    lines = textwrap.wrap(text, chars, break_long_words=True) or [""]
    if len(lines) > MAX_CELL_LINES:
        lines = [*lines[: MAX_CELL_LINES - 1], lines[MAX_CELL_LINES - 1][: max(1, chars - 1)] + "…"]
    return lines


def rows_of(ctx: Context, spec: dict[str, Any]) -> list[tuple[PartGroup, list[str]]]:
    """Each group with its six cells: the computed texts, with the engineer's overrides on top."""
    assembly = _assembly(ctx, "the parts list and the balloons")
    overrides: dict[int, dict[str, str]] = {}
    for row in spec.get("rows", []):
        position = assembly.position_of(row["component"])
        if position is None:
            known = ", ".join(assembly.instances())
            msg = f"{row['component']!r} is not a component or body of this drawing (they are: {known})"
            raise ItemError(msg)
        overrides.setdefault(position, {}).update({k: v for k, v in row.items() if k != "component"})
    out = []
    for group in assembly.groups:
        shown = {
            "name": group.name,
            "reference": group.reference,
            "material": group.material,
            "remarks": group.remarks,
            **overrides.get(group.position, {}),
        }
        out.append((group, [str(group.position), str(group.quantity), *(shown[f] for f in ROW_FIELDS)]))
    return out


def draw_parts_list(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    if ctx.extras.get("parts_list") is not None:
        msg = "the sheet has a parts list already: one list per sheet (change its rows with remove and add)"
        raise ItemError(msg)
    rows = rows_of(ctx, spec)
    welded = ctx.assembly is None and ctx.weldment is not None
    if welded:  # an empty designation stays visible as a question, never a file or object name
        rows = [(g, [*cells[:2], cells[2] or UNNAMED[ctx.language], *cells[3:]]) for g, cells in rows]
    header = (CUT_HEADERS if welded else HEADERS)[ctx.language]
    right = ctx.sheet.width - FRAME_MARGIN_MM
    widths = fit_widths(ctx, header, [cells for _, cells in rows], right) if welded else COLUMNS_MM
    left = right - sum(widths)
    bottom = TITLE_BLOCK_TOP_MM
    wrapped = [[_wrap(cell, w) for cell, w in zip(cells, widths, strict=True)] for _, cells in rows]
    head_lines = [_wrap(cell, w) for cell, w in zip(header, widths, strict=True)]
    heights = [_row_height(head_lines)] + [_row_height(r) for r in wrapped]  # header first: the list grows upward
    edges = [bottom]
    for h in heights:
        edges.append(edges[-1] + h)
    top = edges[-1]
    out: list[Entity] = []
    for index, y in enumerate(edges):
        out.append(Line(left, y, right, y, TITLE_BLOCK, 0.35 if index in (0, 1, len(edges) - 1) else 0.25))
    x = left
    for edge in (0.0, *widths):
        x += edge
        out.append(Line(x, bottom, x, top, TITLE_BLOCK, 0.35 if x in (left, right) else 0.25))
    for cells_lines, (y0, y1) in zip([head_lines, *wrapped], zip(edges, edges[1:], strict=False), strict=True):
        out += _cells(cells_lines, left, y0, y1, widths)
    ctx.add(out)
    rect: Rect = (left, bottom, right, top)
    ctx.reserve(rect)
    ctx.extras["parts_list"] = {
        "rect": rect,
        "positions": [g.position for g, _ in rows],
        "unnamed": [g.position for g, cells in rows if welded and cells[2] == UNNAMED[ctx.language]],
        "names": {g.position: cells[2] for g, cells in rows if welded and cells[2] != UNNAMED[ctx.language]},
    }
    ctx.table_bottom = bottom
    note = f"{len(rows)} position(s), {sum(g.quantity for g, _ in rows)} component(s), above the title block"
    if top > ctx.area[3]:
        return "warning", f"{note}; the list is taller than the free area of the sheet: use A3 or fewer positions"
    return "ok", note


FIT_MIN_MM = 16.0  # the narrowest a text column gets
FIT_MAX_MM = 70.0
GUIDE_CLEAR_MM = 7.0  # the sheet window pulls a symbol whose centre is within 6 mm of a view's origin onto it
CUT_FIXED_MM = COLUMNS_MM[:2]


def _guides_x(ctx: Context) -> list[float]:
    views = [*ctx.views.values(), *ctx.extras.get("views_extra", {}).values()]
    return [v.x for v in views]


def fit_widths(ctx: Context, header: Sequence[str], rows: list[list[str]], right: float) -> tuple[float, ...]:
    """The columns of a cut list: the two narrow ones as always, the four text ones as wide as their longest text (headers
    included) up to ``FIT_MAX_MM``, all of it never wider than the title block (what is left over wraps). The right edge stays
    on the frame; if the centre of the table would sit where the sheet window snaps a symbol onto a view's origin, the
    designation column takes a few millimetres more (the table grows to the left) so the centre is clear of it."""
    texts = [[header[c], *(r[c] for r in rows)] for c in range(2, 6)]
    wanted = [
        min(FIT_MAX_MM, max(FIT_MIN_MM, max(text_width(t, CELL_HEIGHT_MM) for t in col) + 2 * CELL_PAD_MM + 1.0))
        for col in texts
    ]
    room = TITLE_BLOCK_WIDTH_MM - sum(CUT_FIXED_MM)
    if sum(wanted) > room:  # shrink the longest ones first, the rest keep what they need
        wanted = _shrink(wanted, room)
    widths = [*CUT_FIXED_MM, *wanted]
    guides = _guides_x(ctx)
    grow = 0.0
    while grow < TITLE_BLOCK_WIDTH_MM - sum(widths) and any(abs(right - (sum(widths) + grow) / 2 - g) <= GUIDE_CLEAR_MM for g in guides):
        grow += 1.0
    widths[2] += grow
    return tuple(widths)


def _shrink(wanted: list[float], room: float) -> list[float]:
    out = list(wanted)
    while sum(out) > room + 1e-9:
        biggest = max(range(len(out)), key=lambda i: out[i])
        if out[biggest] <= FIT_MIN_MM:
            break
        out[biggest] = max(FIT_MIN_MM, out[biggest] - min(1.0, sum(out) - room))
    return out


def _row_height(cells: list[list[str]]) -> float:
    return max(ROW_MIN_MM, 2.4 + LINE_STEP_MM * max(len(c) for c in cells))


def _cells(cells: list[list[str]], left: float, y0: float, y1: float, widths: tuple[float, ...]) -> list[Entity]:
    out: list[Entity] = []
    x = left
    for column, (lines, width) in enumerate(zip(cells, widths, strict=True)):
        centred = column < 2
        for n, line in enumerate(lines):
            baseline = y1 - 1.0 - CELL_HEIGHT_MM - n * LINE_STEP_MM
            if centred:
                out.append(Text(x + width / 2, baseline, line, CELL_HEIGHT_MM, TITLE_BLOCK, "center"))
            else:
                out.append(Text(x + CELL_PAD_MM, baseline, line, CELL_HEIGHT_MM, TITLE_BLOCK, "left"))
        x += width
    return out


# --- balloons -----------------------------------------------------------------------------------------------------------

BALLOON_R_MM = 5.0
BALLOON_TEXT_MM = 5.0
DOT_R_MM = 0.9
FIRST_GAP_MM = 9.0  # from the view to the nearest edge of the first row of balloons
ROW_STEP_MM = 2 * BALLOON_R_MM + 3.0
SLIDE_MM = 2.5
SLIDES = 60
MAX_ROWS_OUT = 4
LEADER_CLEAR_MM = 1.2
DOT_CLEAR_MM = 2.0
MARGIN_PAGE_MM = 2.5  # the same instance all round the dot, on the sheet
BALLOON_VIEWS = ("front", "top", "right", "isometric", "exploded")
SIDES = ("above", "below", "left", "right")

BALLOON_CHECKS = {"view": v_enum(*BALLOON_VIEWS), "side": v_enum(*SIDES)}


def check_balloons(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, BALLOON_CHECKS)


def _dot(p: Point) -> Solid:
    n = 10
    return Solid(
        tuple(
            (p[0] + DOT_R_MM * math.cos(2 * math.pi * i / n), p[1] + DOT_R_MM * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ),
        DIMENSIONS,
    )


def _balloon(centre: Point, dot: Point, number: str) -> list[Entity]:
    dx, dy = dot[0] - centre[0], dot[1] - centre[1]
    length = math.hypot(dx, dy)
    start = (centre[0] + dx / length * BALLOON_R_MM, centre[1] + dy / length * BALLOON_R_MM)
    return [
        Circle(centre[0], centre[1], BALLOON_R_MM, DIMENSIONS),
        Line(start[0], start[1], dot[0], dot[1], DIMENSIONS),
        _dot(dot),
        Text(centre[0], centre[1] - BALLOON_TEXT_MM / 2 + 0.2, number, BALLOON_TEXT_MM, TEXT, "center"),
    ]


def _sides_by_distance(box: Rect, p: Point, wanted: str | None) -> list[str]:
    near = {"left": p[0] - box[0], "right": box[2] - p[0], "below": p[1] - box[1], "above": box[3] - p[1]}
    order = sorted(near, key=lambda s: near[s])
    if wanted:
        order.remove(wanted)
        order.insert(0, wanted)
    return order


def _candidate(box: Rect, side: str, along: float, ring: int) -> Point:
    out = FIRST_GAP_MM + BALLOON_R_MM + ring * ROW_STEP_MM
    if side == "above":
        return along, box[3] + out
    if side == "below":
        return along, box[1] - out
    if side == "left":
        return box[0] - out, along
    return box[2] + out, along


def _slides(start: float) -> list[float]:
    """Positions along a row, the wanted one first, then alternately either side of it."""
    out = [start]
    for n in range(1, SLIDES + 1):
        out += [start + n * SLIDE_MM, start - n * SLIDE_MM]
    return out


def _leader_ok(ctx: Context, centre: Point, dot: Point, dots: list[Point], strict: bool = True) -> bool:
    """Whether a leader from ``centre`` to ``dot`` crosses no other leader and no other dot and (``strict``) none of the
    dimensions and texts already on the sheet."""
    for a, b in ctx.extras.get("segments", []):
        if _segment_distance(centre, dot, a, b) < LEADER_CLEAR_MM:
            return False
    for rect in ctx.occupied if strict else ():
        if rect[0] <= dot[0] <= rect[2] and rect[1] <= dot[1] <= rect[3]:
            continue  # the leader starts inside this one (the view the balloon belongs to): it has to cross it
        if _segment_hits_rect(centre, dot, rect):
            return False
    return not any(other != dot and _point_segment(other, centre, dot) < DOT_CLEAR_MM for other in dots)


def place_balloons(
    ctx: Context, view: ViewInfo, dots: dict[int, Point], wanted: str | None
) -> tuple[dict[int, Point], list[int], list[int]]:
    """The centre of each balloon (by position), the positions that found no free place and those whose leader had to
    cross a dimension or a text (only tried when no clean place exists). Balloons stand in rows (or
    columns) outside the view's box, as near as possible to the dot they belong to, in order along their side so that
    leaders do not cross; a place is taken when it is free of everything on the sheet and its leader crosses no leader
    and no other dot."""
    box = view.box
    placed: dict[int, Point] = {}
    failed: list[int] = []
    crossing: list[int] = []
    everyone = list(dots.values())
    first_side = {n: _sides_by_distance(box, p, wanted)[0] for n, p in dots.items()}
    order = sorted(
        dots, key=lambda n: (first_side[n], dots[n][0] if first_side[n] in ("above", "below") else dots[n][1])
    )
    for number in order:
        dot = dots[number]
        found = None
        for strict in (True, False):  # a clean leader first; one that has to cross a dimension line only if none exists
            for side in _sides_by_distance(box, dot, wanted):
                target = dot[0] if side in ("above", "below") else dot[1]
                for ring in range(MAX_ROWS_OUT):
                    best: tuple[float, Point] | None = None
                    for along in _slides(target):
                        centre = _candidate(box, side, along, ring)
                        rect = (centre[0] - BALLOON_R_MM - 1, centre[1] - BALLOON_R_MM - 1,
                                centre[0] + BALLOON_R_MM + 1, centre[1] + BALLOON_R_MM + 1)  # fmt: skip
                        if not room_ok(ctx, rect) or not _leader_ok(ctx, centre, dot, everyone, strict):
                            continue
                        cost = abs(along - target) + 6.0 * ring
                        if best is None or cost < best[0]:
                            best = (cost, centre)
                        if abs(along - target) > 4 * ROW_STEP_MM and best is not None:
                            break
                    if best is not None:
                        found = best[1]
                        break
                if found is not None:
                    break
            if found is not None:
                if not strict:
                    crossing.append(number)
                break
        if found is None:
            failed.append(number)
            continue
        placed[number] = found
        ctx.reserve(
            (
                found[0] - BALLOON_R_MM - 1,
                found[1] - BALLOON_R_MM - 1,
                found[0] + BALLOON_R_MM + 1,
                found[1] + BALLOON_R_MM + 1,
            )
        )
        edge = math.dist(found, dot)
        start = (
            found[0] + (dot[0] - found[0]) / edge * BALLOON_R_MM,
            found[1] + (dot[1] - found[1]) / edge * BALLOON_R_MM,
        )
        ctx.extras.setdefault("segments", []).append((start, dot))
    return placed, failed, crossing


def _view_name(ctx: Context, spec: dict[str, Any]) -> str:
    if spec.get("view"):
        return spec["view"]
    extra = ctx.extras.get("views_extra", {})  # the pictorial views: the balloons read best on them (ISO 6433 practice)
    return "exploded" if "exploded" in extra else "isometric" if "isometric" in extra else "front"


def visible_dots(
    ctx: Context, assembly: Assembly, view: ViewInfo, offsets: dict[str, Any]
) -> tuple[dict[int, tuple[str, Point]], dict[int, str]]:
    """For each position the instance whose dot is drawn (the first instance the view shows) and its page point, and the
    positions the view shows no instance of (with their name)."""
    frame = (view.centre, view.xdir, view.direction)
    margin = MARGIN_PAGE_MM / view.scale
    pending = {g.position: list(g.instances) for g in assembly.groups}
    names = {g.position: g.label for g in assembly.groups}
    shown: dict[int, tuple[str, Point]] = {}
    while pending:
        batch = [instances[0] for instances in pending.values()]
        found = ctx.geometry.instance_anchors(batch, frame, offsets, margin, outward=True)
        for position in list(pending):
            first = pending[position][0]
            points = found.get(first) or []
            if points:
                shown[position] = (first, view.project(points[0]))
                del pending[position]
                continue
            pending[position].pop(0)
            if not pending[position]:
                del pending[position]
    return shown, {g.position: names[g.position] for g in assembly.groups if g.position not in shown}


def draw_balloons(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    assembly = _assembly(ctx, "the balloons")
    name = _view_name(ctx, spec)
    view = ctx.any_view(name)
    offsets = ctx.extras.get("explosion", {}).get("offsets", {}) if name == "exploded" else {}
    shown, hidden = visible_dots(ctx, assembly, view, offsets)
    dots = {n: p for n, (_, p) in shown.items()}
    placed, failed, crossing = place_balloons(ctx, view, dots, spec.get("side"))
    entities: list[Entity] = []
    for number, centre in placed.items():
        entities += _balloon(centre, dots[number], str(number))
    ctx.add(entities)
    book = ctx.extras.setdefault("balloons", {"drawn": set(), "hidden": {}, "view": {}})
    book["drawn"] |= set(placed)
    book["hidden"].update({n: (w, name) for n, w in hidden.items()})
    for number in placed:
        book["view"][number] = name
    pointed = {n: first for n, (first, _) in shown.items()}
    shares = ctx.geometry.visible_share(list(pointed.values()), (view.centre, view.xdir, view.direction), offsets) if pointed else {}
    book.setdefault("share", {}).update({n: shares[i] for n, i in pointed.items() if i in shares})  # how much of each part the view shows
    problems = []
    if hidden:
        listed = ", ".join(f"{n} ({w})" for n, w in sorted(hidden.items()))
        problems.append(
            f"no balloon for position(s) {listed}: the {name} view shows no instance of them; choose a view that does"
        )
    if failed:
        problems.append(
            f"no free place for the balloon(s) of position(s) {', '.join(str(n) for n in sorted(failed))}: move or shrink what is around the view"
        )
    if crossing:
        problems.append(
            f"the leader of position(s) {', '.join(str(n) for n in sorted(crossing))} crosses a dimension or a text: "
            "no cleaner place was free (side= or another view may help)"
        )
    detail = f"{len(placed)} balloon(s) in the {name} view"
    if problems:
        return "warning", f"{detail}; " + "; ".join(problems)
    return "ok", detail


ITEMS = (
    ItemType("parts_list", "l", check_parts_list, draw_parts_list, frozenset(PARTS_CHECKS), phase=0),
    ItemType("balloons", "b", check_balloons, draw_balloons, frozenset(BALLOON_CHECKS), phase=4),
)
