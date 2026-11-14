# ruff: noqa: E501
"""Special views and the lines of the standard ones (no FreeCAD import): ``section`` (a cutting plane, its line in the
parent view and the cut view with its hatched faces), ``detail`` (a circle of a view enlarged), ``aux_view`` (the part
looked at square-on to an inclined face) and ``view_lines`` (hidden edges and centre lines in the standard views).

The geometry comes from ``Geometry.projection`` (hidden-line removal and the cut faces, in a view's own coordinates at
scale 1); this module moves it to the page, hatches the cut faces (``sheet_clip``), draws the cutting-plane line and the
letters, and finds room for the new view. A derived view takes the first free place in the direction it is projected to
(first-angle projection puts a view on the side the viewer looks towards), then any free place on the sheet.
"""

import math
from typing import Any

import sheet_centre
import sheet_flats
import sheet_hatch
import sheet_section
import sheet_seen
from items_common import VIEW_ORDER, hole_mark, mark_once
from items_holes import _stacks
from sheet_clip import HATCH_ANGLE_DEG, bbox, clip_to_circle, clip_to_rect, hatch, hatch_spacing, place, relayer
from sheet_context import (
    COMMON_FIELDS,
    Context,
    HoleInfo,
    ItemError,
    ItemType,
    Point,
    Rect,
    Vec3,
    ViewInfo,
    check_fields,
    cross,
    dot,
    perpendicular,
    scale,
    sub,
    unit,
    v_bool,
    v_ids,
    v_num,
    v_point2,
    v_point3,
    v_points3,
)
from sheet_draw import arrow, centre_mark, linear_dimension
from sheet_model import AXES, CONTOUR, DIMENSIONS, HIDDEN, TEXT, Circle, Entity, Line, Text, fmt
from sheet_units import NativeFlag

LABEL_HEIGHT_MM = 5.0  # the letters of a view are bigger than the dimension text
LABEL_ROOM_MM = 9.0  # below a view: its label
ROOM_MARGIN_MM = 4.0  # clear space around a new view
ROOM_GAP_MM = 18.0  # between the parent and a view placed beside it (dimensions go there)
ROOM_STEP_MM = 8.0
ROOM_TRIES = 30
SCAN_STEP_MM = 10.0
CUT_END_MM = 12.0  # the thick ends of a cutting-plane line
CUT_OVERSHOOT_MM = 4.0
CUT_ARROW_MM = 9.0
STANDARD_ENLARGEMENTS = (2.0, 5.0, 10.0, 20.0, 50.0)  # ISO 5455
MAX_DETAIL_MM = 70.0
LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"  # no I, O or Q: they read as digits
HIDDEN_DEFAULT = ("front", "right")
PHASE_VIEWS = 3  # after dimensions and callouts have taken their room
PERP_TOL = 1e-3
PARTIAL_MARGIN_MM = 4.0  # a partial (auxiliary) view reaches this far beyond the face it shows


def v_letter(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 1 or value not in LETTERS:
        msg = f"must be one capital letter ({', '.join(LETTERS)}); I, O and Q are not used"
        raise ItemError(msg)
    return value


def v_hidden(value: Any) -> list[str]:
    """``true`` (front and right), ``false`` (none) or a list of view names."""
    if isinstance(value, bool):
        return list(HIDDEN_DEFAULT) if value else []
    if isinstance(value, list | tuple) and all(v in ("front", "top", "right") for v in value):
        return list(dict.fromkeys(value))
    msg = "must be true, false or a list of views: front, top, right"
    raise ItemError(msg)


def v_holes(value: Any) -> Any:
    """``"all"``, ``"axis"`` (the part's own axis of revolution) or the points on the axes of the holes the plane goes
    through."""
    if value in ("all", "axis"):
        return value
    return [list(p) for p in v_points3(value)]


# --- letters and room ---------------------------------------------------------------------------------------------------


def _used(ctx: Context) -> set[str]:
    return ctx.extras.setdefault("letters", set())


def reserve_letters(ctx: Context, group: list[dict[str, Any]]) -> None:
    for spec in group:
        if spec.get("letter"):
            _used(ctx).add(spec["letter"])


def take_letter(ctx: Context, wanted: str | None) -> str:
    """The letter of a view: the one asked for, else the next free one (``A``, ``B``...)."""
    used = _used(ctx)
    if wanted:
        used.add(wanted)
        return wanted
    owned = ctx.extras.setdefault("letters_given", set())
    for letter in LETTERS:
        if letter not in used and letter not in owned:
            owned.add(letter)
            return letter
    msg = "no free letter left for another view"
    raise ItemError(msg)


def _inflate(rect: Rect, by: float) -> Rect:
    return (rect[0] - by, rect[1] - by, rect[2] + by, rect[3] + by)


def _overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def room_ok(ctx: Context, rect: Rect) -> bool:
    """Whether ``rect`` lies inside the drawing area and clear of the views and of everything already placed."""
    x0, y0, x1, y1 = ctx.area
    if rect[0] < x0 or rect[1] < y0 or rect[2] > x1 or rect[3] > y1:
        return False
    blocked = [_inflate(r, 1.0) for r in ctx.extras.get("view_boxes", [])]
    blocked += list(ctx.extras.get("view_rects", {}).values())
    return ctx.free(rect) and not any(_overlap(rect, b) for b in blocked)


def find_room(
    ctx: Context, width: float, height: float, parent: Rect, directions: list[Point], align: Point
) -> Point | None:
    """The centre for a new view ``width`` x ``height`` mm: beside ``parent`` in the first of ``directions`` that has
    room (aligned with ``align``, the page position the view is projected from), else any free place near the parent."""
    for dx, dy in directions:
        for step in range(ROOM_TRIES):
            reach = ROOM_GAP_MM + step * ROOM_STEP_MM
            if abs(dx) >= abs(dy):
                cx = parent[2] + reach + width / 2 if dx > 0 else parent[0] - reach - width / 2
                cy = align[1]
            else:
                cy = parent[3] + reach + height / 2 if dy > 0 else parent[1] - reach - height / 2
                cx = align[0]
            rect = (cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2)
            if room_ok(ctx, rect):
                return cx, cy
    x0, y0, x1, y1 = ctx.area
    middle = ((parent[0] + parent[2]) / 2, (parent[1] + parent[3]) / 2)
    best: tuple[float, Point] | None = None
    y = y1 - height / 2
    while y >= y0 + height / 2:
        x = x0 + width / 2
        while x <= x1 - width / 2:
            rect = (x - width / 2, y - height / 2, x + width / 2, y + height / 2)
            if room_ok(ctx, rect):
                distance = math.dist((x, y), middle)
                if best is None or distance < best[0]:
                    best = (distance, (x, y))
            x += SCAN_STEP_MM
        y -= SCAN_STEP_MM
    return best[1] if best else None


def _no_room() -> ItemError:
    return ItemError(
        "there is no free place on the sheet for this view: use a larger sheet (A3), a smaller scale, remove "
        "something, or say where it goes with at_mm=[x, y] (the centre of the view, from the bottom-left corner)"
    )


def claim(ctx: Context, item_id: str, rect: Rect) -> None:
    """Take a view's place (its label included) so the next one and the review know it."""
    ctx.reserve(rect)
    ctx.extras.setdefault("view_rects", {})[item_id] = rect


# --- shared geometry --------------------------------------------------------------------------------------------------


def _parent(ctx: Context, name: str | None, test: Any, why: str = "shows what this view needs") -> ViewInfo:
    """The asked view, else the first of top / front / right for which ``test(view)`` holds."""
    if name is not None:
        view = ctx.view(name)
        if not test(view):
            msg = f"the {name} view does not satisfy this: {why}"
            raise ItemError(msg)
        ctx.used_view(view.name)
        return view
    for candidate in VIEW_ORDER:
        view = ctx.views.get(candidate)
        if view is not None and test(view):
            ctx.used_view(view.name)
            return view
    msg = f"no view of the sheet satisfies this ({why}): say view= and check the plane / face"
    raise ItemError(msg)


def _page_unit(p: Point) -> Point:
    n = math.hypot(*p)
    if n < 1e-9:
        msg = "that direction is along the line of sight of the view: it has no direction on the sheet"
        raise ItemError(msg)
    return p[0] / n, p[1] / n


def _snap(p: Point) -> Point:
    """A direction within a degree of a page axis is that axis (so views line up)."""
    x, y = p
    if abs(y) < math.sin(math.radians(1.0)):
        return math.copysign(1.0, x), 0.0
    if abs(x) < math.sin(math.radians(1.0)):
        return 0.0, math.copysign(1.0, y)
    return p


def _frame_for(direction_to_viewer: Vec3, up_hint: Vec3, fallbacks: tuple[Vec3, ...]) -> tuple[Vec3, Vec3]:
    """The view axes ``(xdir, direction)`` for a view looking against ``direction_to_viewer``, its page-up as close to
    ``up_hint`` as the direction allows (``fallbacks`` when the hint is along the line of sight)."""
    d = unit(direction_to_viewer)
    for hint in (up_hint, *fallbacks):
        if abs(dot(unit(hint), d)) < 0.9:
            up = unit(sub(hint, scale(d, dot(hint, d))))
            return unit(cross(up, d)), d
    msg = "cannot orient the view"
    raise ItemError(msg)


WORLD_Z: Vec3 = (0.0, 0.0, 1.0)
WORLD_Y: Vec3 = (0.0, 1.0, 0.0)


def _local_box(entities: list[Entity], extra: list[Entity] | None = None) -> Rect:
    box = bbox(list(entities) + list(extra or []))
    if box is None:
        msg = "nothing of the part is seen in this view"
        raise ItemError(msg)
    return box


def _place_at(box: Rect, k: float, centre: Point) -> Point:
    """The page origin that puts the centre of the local ``box`` (scaled by ``k``) at ``centre``."""
    return centre[0] - (box[0] + box[2]) / 2 * k, centre[1] - (box[1] + box[3]) / 2 * k


def _label(x: float, y: float, text: str) -> Text:
    return Text(x, y, text, LABEL_HEIGHT_MM, TEXT, "center")


def _arrow_with_letter(tip_base: Point, direction: Point, letter: str) -> list[Entity]:
    """The arrow of a cutting-plane line or a view arrow: a stem from ``tip_base`` along ``direction``, the head at its end
    pointing the same way, and the letter beyond it."""
    tip = (tip_base[0] + direction[0] * CUT_ARROW_MM, tip_base[1] + direction[1] * CUT_ARROW_MM)
    out: list[Entity] = [Line(tip_base[0], tip_base[1], tip[0], tip[1], DIMENSIONS), arrow(tip, direction)]
    at = (tip[0] + direction[0] * 4.0, tip[1] + direction[1] * 4.0)
    out.append(Text(at[0], at[1] - LABEL_HEIGHT_MM / 2, letter, LABEL_HEIGHT_MM, TEXT, "center"))
    return out


# --- section ------------------------------------------------------------------------------------------------------------

SECTION_CHECKS = {
    "view": COMMON_FIELDS["view"],
    "at": v_point3,
    "normal": v_point3,
    "holes": v_holes,
    "letter": v_letter,
    "flip": v_bool,
    "at_mm": v_point2,
    "hatch": lambda value: v_hatch(value),
    "in_place": v_bool,
}


def v_hatch(value: Any) -> dict[str, tuple[float, float]]:
    """``{"body 2": {"turn": 90, "spacing": 1.5}}``: the angle (a turn in degrees from the standard hatch: 90 makes 45 degrees
    into 135) and the spacing (a factor of the usual one) a body of the section is hatched with; the pattern is the
    material's and cannot be set."""
    if not isinstance(value, dict) or not value:
        msg = 'must be {"body 1": {"turn": 90, "spacing": 1.5}, ...}: the angle and spacing of the bodies to hatch differently'
        raise ItemError(msg)
    out: dict[str, tuple[float, float]] = {}
    for body, style in value.items():
        if not isinstance(style, dict) or not set(style) <= {"turn", "spacing"} or not style:
            msg = f"the hatch of {body!r} takes turn (degrees) and spacing (a factor), nothing else: the pattern is the material's"
            raise ItemError(msg)
        turn = v_num(-180.0, 180.0)(style["turn"]) if "turn" in style else 0.0
        spacing = v_num(0.25, 8.0)(style["spacing"]) if "spacing" in style else 1.0
        out[str(body)] = (float(turn), float(spacing))
    return out


def check_section(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, SECTION_CHECKS)
    if "holes" in out and ("at" in out or "normal" in out):
        msg = "section takes either holes (the plane goes through the axes of holes) or at + normal, not both"
        raise ItemError(msg)
    if "holes" not in out and ("at" not in out or "normal" not in out):
        msg = (
            'section needs holes="all" (or the points on the axes of the holes the plane goes through) or at=[x,y,z] '
            "(a point of the plane) with normal=[x,y,z] (the direction the viewer looks)"
        )
        raise ItemError(msg)
    if "normal" in out:
        unit(out["normal"])
    if out.get("in_place") and out.get("holes") != "axis":
        msg = 'in_place takes holes="axis": the section through the axis of the part stands in its main view'
        raise ItemError(msg)
    return out


def _holes_for(ctx: Context, spec: dict[str, Any], parent_name: str | None) -> tuple[ViewInfo, list[HoleInfo]]:
    if not ctx.holes:
        msg = "the part has no holes to cut through (inspect(action=holes) lists them)"
        raise ItemError(msg)
    chosen: list[HoleInfo]
    if spec["holes"] == "all":
        chosen = list(ctx.holes)
    else:
        chosen = []
        for point in spec["holes"]:
            hits = [h for h in ctx.holes if h.on_axis(tuple(point))]  # type: ignore[arg-type]
            if not hits:
                msg = f"no hole has its axis through {point} (0.05 mm); inspect(action=holes) lists them"
                raise ItemError(msg)
            chosen += [h for h in hits if h not in chosen]
    view = _parent(
        ctx,
        parent_name,
        lambda v: any(v.looks_along(h.direction) for h in chosen),
        "it must see the holes as circles",
    )
    chosen = [h for h in chosen if view.looks_along(h.direction)]
    if not chosen:
        msg = f"no chosen hole is seen as a circle in the {view.name} view"
        raise ItemError(msg)
    return view, chosen


def _plane_through_holes(view: ViewInfo, holes: list[HoleInfo], flip: bool) -> tuple[Vec3, Vec3]:
    """A point of the plane and the viewing direction (model) for a plane through the axes of ``holes``, which the view
    sees as circles: the line through their centres on the sheet, the viewer looking down it (``flip`` reverses)."""
    pts = [view.project(h.centre) for h in holes]
    far = max(pts, key=lambda p: math.dist(p, pts[0]))
    if math.dist(far, pts[0]) < 0.5:
        line: Point = (1.0, 0.0)  # one hole: a horizontal cut through it
    else:
        line = _snap(_page_unit((far[0] - pts[0][0], far[1] - pts[0][1])))
        for p in pts:
            off = abs((p[0] - pts[0][0]) * -line[1] + (p[1] - pts[0][1]) * line[0])
            if off > 0.1 * max(view.scale, 0.1) + 0.05:
                msg = (
                    "the holes are not on one straight line in the "
                    f"{view.name} view, so no single plane goes through them: name the ones to cut with holes=[...]"
                )
                raise ItemError(msg)
    gaze = (line[1], -line[0])  # a quarter turn clockwise: down for a horizontal line, left for a vertical one

    def normal_of(g: Point) -> Vec3:
        return unit(tuple(g[0] * a + g[1] * b for a, b in zip(view.xdir, view.ydir, strict=True)))  # type: ignore[arg-type]

    def mirrored(g: Point) -> bool:
        x, _ = _frame_for(scale(normal_of(g), -1.0), view.ydir, (WORLD_Z, WORLD_Y))
        return dot(x, view.xdir) < -0.1

    if mirrored(gaze) and not mirrored((-gaze[0], -gaze[1])):  # look from the side that does not mirror the part
        gaze = (-gaze[0], -gaze[1])
    if flip:
        gaze = (-gaze[0], -gaze[1])
    normal = normal_of(gaze)
    return holes[0].centre, unit(normal)  # type: ignore[arg-type]


def _through_axis(ctx: Context, spec: dict[str, Any]) -> tuple[ViewInfo, Vec3, Vec3]:
    """The cutting plane through the part's axis of revolution (``holes="axis"``): the view that looks along the axis carries
    the cutting line, the plane goes through the middle of the part's box in the direction of the view's x."""
    found = ctx.geometry.round_extent()
    if found is None:
        msg = (
            "the part has no axis of revolution about x, y or z for the plane to go through: use holes=[...] (points on "
            "the axes of holes) or at + normal"
        )
        raise ItemError(msg)
    axis = found[0]
    parent = _parent(ctx, spec.get("view"), lambda v: v.looks_along(axis), "it must look along the axis of the part")
    box = ctx.extras.get("model_box")
    middle: Vec3 = (
        tuple((lo + hi) / 2 for lo, hi in zip(box[0], box[1], strict=True)) if box is not None else parent.centre  # type: ignore[assignment]
    )
    on_axis = HoleInfo(0, middle, axis, 1.0, 1.0, None)
    at, normal = _plane_through_holes(parent, [on_axis], bool(spec.get("flip")))
    return parent, at, normal


def _in_place_axis(ctx: Context, spec: dict[str, Any]) -> tuple[ViewInfo, Vec3, Vec3]:
    """The section through the axis that stands IN the main view (``in_place``): the plane contains the axis and is square
    to the line of sight of the view, so the section is seen face-on in that very frame, at that very place and scale."""
    found = ctx.geometry.round_extent()
    if found is None:
        msg = "the part has no axis of revolution about x, y or z for the plane to go through"
        raise ItemError(msg)
    axis = found[0]
    parent = _parent(
        ctx, spec.get("view"), lambda v: not v.derived and v.shows_axis_along(axis), "the axis must lie in the view"
    )
    box = ctx.extras.get("model_box")
    at: Vec3 = (
        tuple((lo + hi) / 2 for lo, hi in zip(box[0], box[1], strict=True)) if box is not None else parent.centre  # type: ignore[assignment]
    )
    return parent, at, scale(parent.direction, -1.0)  # the viewer looks along -direction; his side is the one cut away


def _draw_in_place(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """A section drawn exactly over the main view it stands for (``in_place``): same frame, same origin, same scale, so every
    item that measures in the main view still lands on the right line. No cutting-plane line and no letters are drawn: the
    cutting plane is the plane of symmetry of the part (its axis) and the section is the main view, in direct projection
    with the isometric one, which ISO 128-44 leaves unmarked. The view the section stands for is not drawn (``hidden_views``)."""
    item_id = spec.get("id", "section")
    parent, at, normal = _in_place_axis(ctx, spec)
    letter = take_letter(ctx, spec.get("letter"))
    label = f"{letter}-{letter}"
    projection = ctx.geometry.projection(parent.centre, parent.xdir, parent.direction, (at, normal))
    k = parent.scale
    local = _local_box(projection.visible)
    ox, oy = parent.x, parent.y
    page_loops = [[(ox + k * x, oy + k * y) for x, y in loop] for loop in projection.cut_loops]
    cut_x = [x for loop in page_loops for x, _ in loop]
    cut_y = [y for loop in page_loops for _, y in loop]
    spacing = hatch_spacing(max(cut_x) - min(cut_x), max(cut_y) - min(cut_y))
    ink = place(projection.visible, ox, oy, k)
    ctx.add(ink)
    if "view_ink_shown" in ctx.extras:  # the outline items see is now the section's, not the outside view that is not drawn
        ctx.extras["view_ink"] = [*ctx.extras["view_ink_shown"], *ink]
        ctx.extras["view_ink_shown"] = ctx.extras["view_ink"]
    groups = _bodies_of_cut(page_loops, projection.cut_owner)
    touching = sheet_hatch.touching_bodies(groups)
    styles = sheet_hatch.plan(list(groups), touching, spec.get("hatch"))
    ctx.add(hatch_bodies(groups, styles, spacing))
    ctx.extras.setdefault("hatches", {})[label] = (
        [sheet_hatch.HatchFact(b, "", "", styles[b][0], styles[b][1]) for b in groups],
        touching,
    )
    box = (ox + k * local[0], oy + k * local[1], ox + k * local[2], oy + k * local[3])
    info = ViewInfo(label, ox, oy, k, parent.direction, parent.xdir, parent.centre, box, derived=True)
    ctx.extras.setdefault("sections", {})[label] = info
    ctx.extras.setdefault("section_items", {})[label] = item_id
    ctx.extras.setdefault("section_planes", {})[label] = (at, normal)
    ctx.extras.setdefault("in_place_views", {})[label] = parent.name
    axes, axis_count = sheet_section.centre_lines(ctx, info, at, normal)
    threads, thread_count = sheet_section.threads_cut(ctx, info, at, normal)
    ctx.add(axes + threads)
    extra = f", {axis_count} centre line(s)" if axis_count else ""
    if thread_count:
        extra += f", thread lines of {thread_count} threaded bore(s)"
    return "ok", f"{label} in place of the {parent.name} view, through the axis of the part, no cutting line or letters (the plane is the part's axis); cut faces hatched{extra}"


def draw_section(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    if spec.get("in_place"):
        return _draw_in_place(ctx, spec)
    item_id = spec.get("id", "section")
    note = ""
    if spec.get("holes") == "axis":
        parent, at, normal = _through_axis(ctx, spec)
        note = "through the axis of the part"
    elif "holes" in spec:
        parent, chosen = _holes_for(ctx, spec, spec.get("view"))
        at, normal = _plane_through_holes(parent, chosen, bool(spec.get("flip")))
        note = f"through the axes of {len(chosen)} hole(s)"
    else:
        at, normal = spec["at"], unit(spec["normal"])
        if spec.get("flip"):
            normal = scale(normal, -1.0)
        parent = _parent(
            ctx,
            spec.get("view"),
            lambda v: perpendicular(v.direction, normal, PERP_TOL),
            "the plane must be seen edge-on there: its normal has to be perpendicular to the direction the view "
            "looks along",
        )
    gaze_page = _snap(_page_unit(parent.axis(normal)))
    line = (-gaze_page[1], gaze_page[0])
    letter = take_letter(ctx, spec.get("letter"))
    label = f"{letter}-{letter}"

    # the cut view: looking along the normal, page-up as close to the parent's up as the cut allows
    xdir, direction = _frame_for(scale(normal, -1.0), parent.ydir, (WORLD_Z, WORLD_Y))
    projection = ctx.geometry.projection(parent.centre, xdir, direction, (at, normal))
    k = parent.scale
    local = _local_box(projection.visible)
    width, height = (local[2] - local[0]) * k, (local[3] - local[1]) * k
    anchor = parent.project(at)
    parent_rect = parent.box
    room = (width + 2 * ROOM_MARGIN_MM, height + LABEL_ROOM_MM + 2 * ROOM_MARGIN_MM)
    if "at_mm" in spec:
        centre: Point | None = spec["at_mm"]
    else:
        align = ((parent_rect[0] + parent_rect[2]) / 2, (parent_rect[1] + parent_rect[3]) / 2)
        directions = [gaze_page, (-gaze_page[0], -gaze_page[1]), line, (-line[0], -line[1])]
        centre = find_room(ctx, room[0], room[1], parent_rect, directions, align)
    if centre is None:
        raise _no_room()
    # the box includes the label room below: shift the view up half of it
    view_centre = (centre[0], centre[1] + LABEL_ROOM_MM / 2)
    ox, oy = _place_at(local, k, view_centre)
    page_loops = [[(ox + k * x, oy + k * y) for x, y in loop] for loop in projection.cut_loops]
    cut_x = [x for loop in page_loops for x, _ in loop]
    cut_y = [y for loop in page_loops for _, y in loop]
    spacing = hatch_spacing(max(cut_x) - min(cut_x), max(cut_y) - min(cut_y))
    ctx.add(place(projection.visible, ox, oy, k))
    groups = _bodies_of_cut(page_loops, projection.cut_owner)
    touching = sheet_hatch.touching_bodies(groups)
    styles = sheet_hatch.plan(list(groups), touching, spec.get("hatch"))
    ctx.add(hatch_bodies(groups, styles, spacing))
    ctx.extras.setdefault("hatches", {})[label] = (
        [sheet_hatch.HatchFact(b, "", "", styles[b][0], styles[b][1]) for b in groups],
        touching,
    )
    rect = (view_centre[0] - width / 2 - ROOM_MARGIN_MM, view_centre[1] - height / 2 - LABEL_ROOM_MM - ROOM_MARGIN_MM,
            view_centre[0] + width / 2 + ROOM_MARGIN_MM, view_centre[1] + height / 2 + ROOM_MARGIN_MM)  # fmt: skip
    ctx.add([_label(view_centre[0], view_centre[1] - height / 2 - LABEL_ROOM_MM + 1.5, label)])
    claim(ctx, item_id, rect)
    ctx.add(_cutting_line(parent, anchor, line, gaze_page, letter))
    # the section is a view the other items may name (view="A-A"): its dimensions, notes and callouts go on it
    box = (ox + k * local[0], oy + k * local[1], ox + k * local[2], oy + k * local[3])
    info = ViewInfo(label, ox, oy, k, direction, xdir, parent.centre, box, derived=True)
    ctx.extras.setdefault("sections", {})[label] = info
    ctx.extras.setdefault("section_items", {})[label] = item_id
    ctx.extras.setdefault("section_planes", {})[label] = (at, normal)
    if ctx.assembly is not None and not {"front", "right"} & set(ctx.views):
        # the section of an assembly is its main view when no outside view shows the height: it carries the overall height
        dist = ctx.stack_distance(label, "right", None)
        text = ctx.num((box[3] - box[1]) / k)
        entities, taken = linear_dimension((box[2], box[1]), (box[2], box[3]), "v", box[2] + dist, text)
        ctx.add(entities)
        ctx.reserve_dimension(entities, taken)
        ctx.used_view(label)
    axes, axis_count = sheet_section.centre_lines(ctx, info, at, normal)
    threads, thread_count = sheet_section.threads_cut(ctx, info, at, normal)
    ctx.add(axes + threads)
    extra = ""
    if axis_count:
        extra += f", {axis_count} centre line(s)"
    if thread_count:
        extra += f", thread lines of {thread_count} threaded bore(s)"
    if len(groups) > 1:
        extra += "; hatched by body: " + ", ".join(
            f"{b} {fmt(45.0 + styles[b][0])}°{'' if styles[b][1] == 1.0 else f' x{fmt(styles[b][1])}'}" for b in groups
        )
    return "ok", f"{label} in the {parent.name} view{', ' + note if note else ''}; cut faces hatched{extra}"


def _bodies_of_cut(page_loops: list[list[Point]], owners: list[int]) -> dict[str, list[list[Point]]]:
    """The cut loops grouped by the body they belong to, named ``body 1``, ``body 2``... by the order of the bodies of the
    sheet (a part of one body has one group, so nothing changes for it)."""
    if len(owners) != len(page_loops):
        return {"body 1": page_loops}
    groups: dict[str, list[list[Point]]] = {}
    for loop, owner in zip(page_loops, owners, strict=True):
        groups.setdefault(f"body {owner + 1}", []).append(loop)
    return groups


def hatch_bodies(
    groups: dict[str, list[list[Point]]], styles: dict[str, tuple[float, float]], spacing: float
) -> list[Line]:
    """The hatch lines of every body of a section: the standard angle turned by the body's turn, the spacing times its
    factor. Different angle or spacing tells a body from its neighbours; the pattern is the same (parallel lines: the
    pattern of the one material of the sheet)."""
    out: list[Line] = []
    for body, loops in groups.items():
        turn, factor = styles.get(body, (0.0, 1.0))
        out += hatch(loops, spacing * factor, HATCH_ANGLE_DEG + turn)
    return out


def _cutting_line(parent: ViewInfo, anchor: Point, line: Point, gaze: Point, letter: str) -> list[Entity]:
    """The cutting-plane line (ISO 128-40): thick at the ends, thin chain between, an arrow and the letter at each end."""
    box = parent.box
    corners = [(box[0], box[1]), (box[0], box[3]), (box[2], box[1]), (box[2], box[3])]
    along = [(c[0] - anchor[0]) * line[0] + (c[1] - anchor[1]) * line[1] for c in corners]
    lo, hi = min(along) - CUT_OVERSHOOT_MM, max(along) + CUT_OVERSHOOT_MM
    thick = min(CUT_END_MM, (hi - lo) / 3)

    def at(t: float) -> Point:
        return anchor[0] + line[0] * t, anchor[1] + line[1] * t

    out: list[Entity] = []
    for a, b in ((lo, lo + thick), (hi - thick, hi)):
        p, q = at(a), at(b)
        out.append(Line(p[0], p[1], q[0], q[1], CONTOUR))
    p, q = at(lo + thick), at(hi - thick)
    out.append(Line(p[0], p[1], q[0], q[1], AXES))
    for end in (lo, hi):
        out += _arrow_with_letter(at(end), gaze, letter)
    return out


# --- detail -------------------------------------------------------------------------------------------------------------

DETAIL_CHECKS = {
    "view": COMMON_FIELDS["view"],
    "at": v_point3,
    "radius_mm": v_num(0.1, 1000.0),
    "scale": v_num(0.01, 100.0),
    "letter": v_letter,
    "at_mm": v_point2,
}


def check_detail(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, DETAIL_CHECKS, required=("at", "radius_mm"))


def _detail_scale(parent_scale: float, radius: float) -> float:
    """The enlargement (ISO 5455) that fills about 50 mm of sheet and is clearly bigger than the parent view."""
    options = [s for s in STANDARD_ENLARGEMENTS if s >= 1.9 * parent_scale]
    fitting = [s for s in options if 2 * radius * s <= MAX_DETAIL_MM]
    if fitting:
        return max(fitting)
    return min(options) if options else max(2.0, 2 * parent_scale)


def draw_detail(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    item_id = spec.get("id", "detail")
    parent = ctx.view(spec.get("view") or "front")
    at, radius = spec["at"], float(spec["radius_mm"])
    k = float(spec.get("scale") or _detail_scale(parent.scale, radius))
    if k <= parent.scale:
        msg = f"a detail enlarges: its scale ({fmt(k)}:1) must be bigger than the view's ({fmt(parent.scale)}:1)"
        raise ItemError(msg)
    d = sub(at, parent.centre)
    lx, ly = dot(d, parent.xdir), dot(d, parent.ydir)
    projection = ctx.geometry.projection(parent.centre, parent.xdir, parent.direction)
    entities = list(projection.visible)
    if parent.name in ctx.extras.get("hidden_views", set()):
        entities += projection.hidden
    clipped = clip_to_circle(entities, lx, ly, radius)
    if not clipped:
        msg = f"nothing of the part lies inside the circle of radius {fmt(radius)} mm at {list(at)}"
        raise ItemError(msg)
    letter = take_letter(ctx, spec.get("letter"))
    label = f"{letter} ({fmt(k)}:1)"
    page_r = radius * k
    size = 2 * page_r
    room = (size + 2 * ROOM_MARGIN_MM, size + LABEL_ROOM_MM + 2 * ROOM_MARGIN_MM)
    anchor = parent.project(at)
    if "at_mm" in spec:
        centre: Point | None = spec["at_mm"]
    else:
        pbox = parent.box
        away = (
            1.0 if anchor[0] >= (pbox[0] + pbox[2]) / 2 else -1.0,
            1.0 if anchor[1] >= (pbox[1] + pbox[3]) / 2 else -1.0,
        )
        directions = [(away[0], 0.0), (0.0, away[1]), (-away[0], 0.0), (0.0, -away[1])]
        centre = find_room(ctx, room[0], room[1], pbox, directions, anchor)
    if centre is None:
        raise _no_room()
    view_centre = (centre[0], centre[1] + LABEL_ROOM_MM / 2)
    ox, oy = view_centre[0] - lx * k, view_centre[1] - ly * k
    ctx.add(place(clipped, ox, oy, k))
    ctx.add([Circle(view_centre[0], view_centre[1], page_r, DIMENSIONS)])
    ctx.add([_label(view_centre[0], view_centre[1] - page_r - LABEL_ROOM_MM + 1.5, label)])
    rect = (view_centre[0] - page_r - ROOM_MARGIN_MM, view_centre[1] - page_r - LABEL_ROOM_MM - ROOM_MARGIN_MM,
            view_centre[0] + page_r + ROOM_MARGIN_MM, view_centre[1] + page_r + ROOM_MARGIN_MM)  # fmt: skip
    claim(ctx, item_id, rect)
    # the circle in the parent view, with its letter
    pr = radius * parent.scale
    ctx.add([Circle(anchor[0], anchor[1], pr, DIMENSIONS)])
    ctx.add(
        [Text(anchor[0] + (pr + 1.5) * 0.7071, anchor[1] + (pr + 1.5) * 0.7071, letter, LABEL_HEIGHT_MM, TEXT, "left")]
    )
    return "ok", f"{label} of the {parent.name} view, circle radius {fmt(radius)} mm"


# --- auxiliary view -------------------------------------------------------------------------------------------------------

AUX_CHECKS = {
    "face": v_ids("face"),
    "view": COMMON_FIELDS["view"],
    "letter": v_letter,
    "at_mm": v_point2,
}


def check_aux(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, AUX_CHECKS, required=("face",))
    if len(out["face"]) != 1:
        msg = "aux_view takes one face id"
        raise ItemError(msg)
    return out


def draw_aux(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    item_id = spec.get("id", "aux")
    face_id = spec["face"][0]
    face = ctx.geometry.face(face_id)
    if face.kind != "plane" or face.normal is None:
        msg = f"{face_id} is a {face.kind} face: an auxiliary view looks square-on to a PLANE"
        raise ItemError(msg)
    n = unit(face.normal)
    standard = [v.name for v in ctx.views.values() if v.looks_along(n)]
    if standard:
        msg = f"{face_id} is parallel to the {standard[0]} view: that view already shows it square-on"
        raise ItemError(msg)
    parent = _parent(
        ctx,
        spec.get("view"),
        lambda v: perpendicular(v.direction, n, PERP_TOL),
        f"{face_id} must be seen edge-on there (its normal must lie in the plane of the view)",
    )
    # looking at the face from outside: the viewer is on the face's normal; page-up along the face's trace in the parent
    trace = unit(cross(parent.direction, n))
    if dot(trace, parent.ydir) < -PERP_TOL or (
        abs(dot(trace, parent.ydir)) <= PERP_TOL and dot(trace, parent.xdir) < 0
    ):
        trace = scale(trace, -1.0)
    direction = n
    xdir = unit(cross(trace, direction))
    projection = ctx.geometry.projection(parent.centre, xdir, direction)
    outline = ctx.geometry.face_outline(face_id, parent.centre, xdir, direction)
    xs = [p[0] for loop in outline for p in loop]
    ys = [p[1] for loop in outline for p in loop]
    if not xs:
        msg = f"{face_id} has no outline to look at"
        raise ItemError(msg)
    margin = PARTIAL_MARGIN_MM / parent.scale
    window = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
    entities = list(projection.visible)
    if parent.name in ctx.extras.get("hidden_views", set()):
        entities += projection.hidden
    shown = clip_to_rect(entities, window)
    letter = take_letter(ctx, spec.get("letter"))
    k = parent.scale
    width, height = (window[2] - window[0]) * k, (window[3] - window[1]) * k
    room = (width + 2 * ROOM_MARGIN_MM, height + LABEL_ROOM_MM + 2 * ROOM_MARGIN_MM)
    gaze_page = _snap(_page_unit(parent.axis(scale(n, -1.0))))
    anchor = parent.project(face.point)
    if "at_mm" in spec:
        centre: Point | None = spec["at_mm"]
    else:
        pbox = parent.box
        directions = [
            gaze_page,
            (-gaze_page[0], -gaze_page[1]),
            (-gaze_page[1], gaze_page[0]),
            (gaze_page[1], -gaze_page[0]),
        ]
        centre = find_room(ctx, room[0], room[1], pbox, directions, anchor)
    if centre is None:
        raise _no_room()
    view_centre = (centre[0], centre[1] + LABEL_ROOM_MM / 2)
    ox, oy = _place_at(window, k, view_centre)
    ctx.add(place(shown, ox, oy, k))
    ctx.add([_label(view_centre[0], view_centre[1] - height / 2 - LABEL_ROOM_MM + 1.5, letter)])
    rect = (view_centre[0] - width / 2 - ROOM_MARGIN_MM, view_centre[1] - height / 2 - LABEL_ROOM_MM - ROOM_MARGIN_MM,
            view_centre[0] + width / 2 + ROOM_MARGIN_MM, view_centre[1] + height / 2 + ROOM_MARGIN_MM)  # fmt: skip
    claim(ctx, item_id, rect)
    # the view arrow in the parent: outside the face, pointing at it
    outward = _snap(_page_unit(parent.axis(n)))
    start = (anchor[0] + outward[0] * 4.0, anchor[1] + outward[1] * 4.0)
    tail = (start[0] + outward[0] * CUT_ARROW_MM, start[1] + outward[1] * CUT_ARROW_MM)
    out: list[Entity] = [
        Line(tail[0], tail[1], start[0], start[1], DIMENSIONS),
        arrow(start, (-outward[0], -outward[1])),
    ]
    out.append(
        Text(
            tail[0] + outward[0] * 4.0,
            tail[1] + outward[1] * 4.0 - LABEL_HEIGHT_MM / 2,
            letter,
            LABEL_HEIGHT_MM,
            TEXT,
            "center",
        )
    )
    ctx.add(out)
    return "ok", f"view {letter} square-on to {face_id}, seen from the {parent.name} view"


# --- hidden edges and centre lines ------------------------------------------------------------------------------------------

LINES_CHECKS = {"hidden": v_hidden, "centres": v_bool}


def check_lines(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, LINES_CHECKS)


def _flat_diagonals(ctx: Context) -> list[str]:
    """The two diagonals (thin continuous) of every flat of a turned part that a standard view sees face-on: with a centre line
    in the view, a flat's rectangle would read as a cylinder (ISO 128-30). Says what it drew, view by view."""
    done: set[tuple[str, str]] = ctx.extras.setdefault("flat_diagonals", set())
    said: list[str] = []
    for flat in sheet_flats.flats_of(ctx):
        for view in sheet_flats.seen_in(ctx, flat):
            if (view.name, flat.face_id) in done:
                continue
            got = sheet_flats.diagonals(ctx, view, flat)
            if got is None:
                continue
            done.add((view.name, flat.face_id))
            ctx.add(got[0])
            ctx.used_view(view.name)
            said.append(f"{flat.face_id} in {view.name} ({got[1]})")
    return said


def draw_lines(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    said: list[str] = []
    hidden_views = list(HIDDEN_DEFAULT) if "hidden" not in spec else list(spec["hidden"])
    drawn_hidden: list[str] = []
    for name in hidden_views:
        view = ctx.views.get(name)
        if view is None:
            continue
        shown = ctx.geometry.projection(view.centre, view.xdir, view.direction).hidden
        if shown:
            ctx.add(place(relayer(shown, HIDDEN), view.x, view.y, view.scale))
            ctx.native(NativeFlag(str(ctx.extras["current"]), name, "HardHidden"))  # TechDraw shows them itself
            ctx.used_view(name)
            drawn_hidden.append(name)
        ctx.extras.setdefault("hidden_views", set()).add(name)
    if drawn_hidden:
        said.append(f"hidden lines in {', '.join(drawn_hidden)}")
    elif hidden_views:
        said.append("no hidden edges to show")
    marks = axes = 0
    if spec.get("centres", True):
        for stack in _stacks(list(ctx.holes)):
            d = stack[0].direction
            radius_mm = stack[-1].diameter / 2
            for view in ctx.views.values():
                if view.looks_along(d):
                    c = view.project(stack[0].centre)
                    if mark_once(ctx, view, c):
                        ctx.add(hole_mark(ctx, view, stack[0], c, radius_mm * view.scale))
                        marks += 1
                    continue
                origin = stack[0].centre
                reach = [
                    dot(sub(p, origin), unit(d))
                    for h in stack
                    for p in (  # the wall of each hole and, in a blind one, its drill point: the line is centred on all of it
                        h.centre,
                        tuple(c + u * h.length for c, u in zip(h.centre, unit(h.direction), strict=True)),
                    )
                ]
                pa = view.project(tuple(o + u * min(reach) for o, u in zip(origin, unit(d), strict=True)))  # type: ignore[arg-type]
                pb = view.project(tuple(o + u * max(reach) for o, u in zip(origin, unit(d), strict=True)))  # type: ignore[arg-type]
                if math.dist(pa, pb) < 0.5:
                    continue
                line = sheet_centre.side_line(ctx, view.name, pa, pb)  # the same overshoot at both ends
                if line is not None:
                    ctx.add([line])
                    ctx.used_view(view.name)
                    axes += 1
        facts = ctx.extras.get("facts")  # the whole cylinders of the part (pins, shafts, bosses) get theirs too
        for boss in facts.bosses if facts is not None else ():
            d = unit(boss.axis_dir)
            hidden_behind = facts.assembly  # the cylinders of an assembly: only where the view really shows them
            for view in ctx.views.values():
                if view.looks_along(d):
                    if hidden_behind and not sheet_seen.circle_seen(ctx, view, boss):
                        continue
                    c = view.project(boss.axis_point)
                    if mark_once(ctx, view, c):
                        ctx.add(centre_mark(c[0], c[1], boss.radius * view.scale))
                        marks += 1
                elif boss.length > 0 and view.shows_axis_along(d):
                    if boss.bore or (hidden_behind and not sheet_seen.side_on_seen(ctx, view, boss)):
                        continue  # a bore is inside the material; a cylinder behind another part shows no sides
                    half = boss.length / 2
                    pa = view.project(tuple(p - u * half for p, u in zip(boss.axis_point, d, strict=True)))  # type: ignore[arg-type]
                    pb = view.project(tuple(p + u * half for p, u in zip(boss.axis_point, d, strict=True)))  # type: ignore[arg-type]
                    if math.dist(pa, pb) < 0.5:
                        continue
                    drawn = ctx.extras.setdefault("axis_lines", set())
                    key = (view.name, *sorted(((round(pa[0], 1), round(pa[1], 1)), (round(pb[0], 1), round(pb[1], 1)))))
                    if key in drawn:
                        continue
                    drawn.add(key)
                    line = sheet_centre.side_line(ctx, view.name, pa, pb)
                    if line is not None:
                        ctx.add([line])
                        ctx.used_view(view.name)
                        axes += 1
        if marks or axes:
            said.append(f"centre marks on {marks} hole view(s), centre lines on {axes}")
        crossed = _flat_diagonals(ctx)
        if crossed:
            said.append("diagonals on the flat faces seen face-on: " + "; ".join(crossed))
    if not said:
        return "warning", "nothing to draw: no hidden edges and no holes"
    return "ok", "; ".join(said)


ITEMS = (
    ItemType("view_lines", "v", check_lines, draw_lines, frozenset(LINES_CHECKS), phase=0),
    ItemType(
        "section",
        "s",
        check_section,
        draw_section,
        frozenset(SECTION_CHECKS),
        prepare=reserve_letters,
        phase=PHASE_VIEWS,
    ),
    ItemType(
        "detail", "e", check_detail, draw_detail, frozenset(DETAIL_CHECKS), prepare=reserve_letters, phase=PHASE_VIEWS
    ),
    ItemType("aux_view", "x", check_aux, draw_aux, frozenset(AUX_CHECKS), prepare=reserve_letters, phase=PHASE_VIEWS),
)
