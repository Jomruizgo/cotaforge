# ruff: noqa: E501
"""``exploded``: the assembly drawn with its components pulled apart (no FreeCAD import).

The displacement is a DRAWING-ONLY transformation: each component is moved by an offset while the assembly is projected;
the document, the poses and the registry are never touched. The offsets come from a rule (``mode`` radial: each
component moves away from the base component's centre by ``factor`` of its distance; ``axis``: only along ``axis``)
and the engineer may replace any of them (``moves``). Thin chain lines (``trails``) run along each displacement, across
the gap between the assembled and the drawn place only (never over the component itself), and are left out when they
would cross another part.

It is an item and not an option of the standard views because it is a derived view like a section: it takes the free
room of the sheet, has its own scale and label, and the balloons can be placed on it (``view="exploded"``).
"""

import math
from typing import Any

from items_views import LABEL_ROOM_MM, ROOM_MARGIN_MM, _label, _no_room, _place_at, claim, find_room
from sheet_clip import bbox, place
from sheet_context import (
    Context,
    ItemError,
    ItemType,
    Point,
    Vec3,
    ViewInfo,
    add,
    check_fields,
    dot,
    scale,
    sub,
    unit,
    v_bool,
    v_enum,
    v_num,
    v_point2,
    v_point3,
    v_str,
)
from sheet_layout import ISO_DIRECTION, ISO_XDIRECTION, STANDARD_SCALES
from sheet_model import AXES, Line, fmt_scale

MAX_MOVES = 60
MIN_TRAIL_MM = 6.0
SKIN_MM = 0.5  # a trail that only grazes a box does not cross it


def v_moves(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list | tuple) or not value or len(value) > MAX_MOVES:
        msg = f'must be a list of 1 to {MAX_MOVES} moves like {{"component": "comp:3", "by": [0, 40, 0]}}'
        raise ItemError(msg)
    out = []
    for move in value:
        if not isinstance(move, dict) or set(move) != {"component", "by"}:
            msg = 'every move is {"component": "comp:N", "by": [dx, dy, dz]} (mm, the displacement drawn)'
            raise ItemError(msg)
        out.append({"component": v_str(32)(move["component"]), "by": list(v_point3(move["by"]))})
    return out


EXPLODED_CHECKS = {
    "view": v_enum("isometric", "front", "top", "right"),
    "mode": v_enum("radial", "axis"),
    "axis": v_point3,
    "factor": v_num(0.0, 5.0),
    "base": v_str(32),
    "moves": v_moves,
    "trails": v_bool,
    "at_mm": v_point2,
}


def check_exploded(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, EXPLODED_CHECKS)
    if out.get("mode") == "axis" and "axis" not in out:
        msg = "mode=axis needs axis=[x,y,z], the direction the components are pulled along"
        raise ItemError(msg)
    if "axis" in out:
        unit(out["axis"])
    return out


def _centre(box: tuple[Vec3, Vec3]) -> Vec3:
    return tuple((a + b) / 2 for a, b in zip(*box, strict=True))  # type: ignore[return-value]


def _volume(box: tuple[Vec3, Vec3]) -> float:
    return math.prod(max(b - a, 1e-6) for a, b in zip(*box, strict=True))


def offsets_of(ctx: Context, spec: dict[str, Any]) -> tuple[dict[str, Vec3], dict[str, Vec3]]:
    """``(offsets, centres)``: the displacement of every component and its centre in the assembly. Components that stay
    have no entry."""
    assembly = ctx.assembly
    if assembly is None or not assembly.groups:
        msg = "an exploded view belongs to an assembly drawing: this document has no components"
        raise ItemError(msg)
    ids = assembly.instances()
    boxes = {cid: ctx.geometry.instance_box(cid) for cid in ids}
    centres = {cid: _centre(box) for cid, box in boxes.items()}
    base = spec.get("base") or max(ids, key=lambda c: _volume(boxes[c]))
    if base not in centres:
        msg = f"base {base!r} is not a component of this assembly (its components: {', '.join(ids)})"
        raise ItemError(msg)
    factor = float(spec.get("factor", 0.6))
    mode = spec.get("mode", "radial")
    axis = unit(tuple(spec["axis"])) if mode == "axis" else None  # type: ignore[arg-type]
    offsets: dict[str, Vec3] = {}
    for cid in ids:
        if cid == base:
            continue
        away = sub(centres[cid], centres[base])
        offsets[cid] = scale(away, factor) if axis is None else scale(axis, factor * dot(away, axis))
    for move in spec.get("moves", []):
        if move["component"] not in centres:
            msg = f"move of {move['component']!r}: not a component of this assembly (its components: {', '.join(ids)})"
            raise ItemError(msg)
        offsets[move["component"]] = tuple(move["by"])  # type: ignore[assignment]
    return {c: o for c, o in offsets.items() if math.sqrt(dot(o, o)) > 1e-9}, centres


def _moved(box: tuple[Vec3, Vec3], offset: Vec3 | None) -> tuple[Vec3, Vec3]:
    if offset is None:
        return box
    return add(box[0], offset), add(box[1], offset)


def trail_between(box: tuple[Vec3, Vec3], offset: Vec3) -> tuple[Vec3, Vec3] | None:
    """The trail of a displaced component: along its displacement, through the middle of its box, and only across the GAP
    between the place it was assembled and the place it is drawn (the box is cleared at both ends). ``None`` when the
    two places touch."""
    length = math.sqrt(dot(offset, offset))
    if length < 1e-9:
        return None
    direction = scale(offset, 1.0 / length)
    centre = tuple((a + b) / 2 for a, b in zip(*box, strict=True))
    half = tuple((b - a) / 2 for a, b in zip(*box, strict=True))
    reach = sum(abs(d) * h for d, h in zip(direction, half, strict=True))  # half the box along the displacement
    if length - 2 * reach < MIN_TRAIL_MM / 4:
        return None
    start = add(centre, scale(direction, reach))  # type: ignore[arg-type]
    return start, add(start, scale(direction, length - 2 * reach))


def _crosses(segment: tuple[Vec3, Vec3], box: tuple[Vec3, Vec3]) -> bool:
    """Whether the segment passes through the inside of the box (slab test; a graze of the surface does not count)."""
    a, b = segment
    t0, t1 = 0.0, 1.0
    for i in range(3):
        d = b[i] - a[i]
        low, high = box[0][i] + SKIN_MM, box[1][i] - SKIN_MM
        if abs(d) < 1e-12:
            if a[i] < low or a[i] > high:
                return False
            continue
        n, f = (low - a[i]) / d, (high - a[i]) / d
        t0, t1 = max(t0, min(n, f)), min(t1, max(n, f))
        if t0 > t1:
            return False
    return True


def _frame(ctx: Context, name: str) -> tuple[Vec3, Vec3]:
    """``(xdir, direction)`` of the view the explosion is drawn in."""
    if name == "isometric":
        return unit(ISO_XDIRECTION), unit(ISO_DIRECTION)
    view = ctx.view(name)
    return view.xdir, view.direction


def _scales(main: float) -> list[float]:
    return [s for s in STANDARD_SCALES if s <= main + 1e-9] or [main]


def draw_exploded(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    item_id = spec.get("id", "exploded")
    if "exploded" in ctx.extras.get("views_extra", {}):
        msg = "the sheet has an exploded view already: one per sheet"
        raise ItemError(msg)
    offsets, centres = offsets_of(ctx, spec)
    name = spec.get("view", "isometric")
    xdir, direction = _frame(ctx, name)
    shown = {cid: add(centres[cid], offsets.get(cid, (0.0, 0.0, 0.0))) for cid in centres}
    mid: Vec3 = tuple((min(p[i] for p in shown.values()) + max(p[i] for p in shown.values())) / 2 for i in range(3))  # type: ignore[assignment]
    projection = ctx.geometry.assembly_projection(mid, xdir, direction, offsets)
    local = bbox(list(projection.visible))
    if local is None:
        msg = "nothing of the assembly is seen in this view"
        raise ItemError(msg)
    main = ctx.extras.get("main_scale") or (next(iter(ctx.views.values())).scale if ctx.views else 1.0)
    parent = ctx.extras.get("iso_box") or (next(iter(ctx.views.values())).box if ctx.views else ctx.area)
    centre: Point | None = None
    k = main
    for k in _scales(main):
        width, height = (local[2] - local[0]) * k, (local[3] - local[1]) * k
        room = (width + 2 * ROOM_MARGIN_MM, height + LABEL_ROOM_MM + 2 * ROOM_MARGIN_MM)
        if "at_mm" in spec:
            centre = spec["at_mm"]
        else:
            centre = find_room(
                ctx, room[0], room[1], parent, [], ((parent[0] + parent[2]) / 2, (parent[1] + parent[3]) / 2)
            )
        if centre is not None:
            break
    if centre is None:
        raise _no_room()
    view_centre = (centre[0], centre[1] + LABEL_ROOM_MM / 2)
    ox, oy = _place_at(local, k, view_centre)
    ctx.add(place(list(projection.visible), ox, oy, k))
    page_box = (ox + local[0] * k, oy + local[1] * k, ox + local[2] * k, oy + local[3] * k)
    info = ViewInfo("exploded", ox, oy, k, direction, xdir, mid, page_box)
    ctx.extras.setdefault("views_extra", {})["exploded"] = info
    ctx.extras["explosion"] = {"offsets": offsets, "view": name}
    trails = skipped = 0
    if spec.get("trails", True):
        boxes = {cid: _moved(ctx.geometry.instance_box(cid), offsets.get(cid)) for cid in centres}
        for cid, offset in offsets.items():
            segment = trail_between(ctx.geometry.instance_box(cid), offset)
            if segment is None:
                continue
            if any(_crosses(segment, box) for other, box in boxes.items() if other != cid):
                skipped += 1  # it would run over another part: better no trail than one that confuses the view
                continue
            a, b = info.project(segment[0]), info.project(segment[1])
            if math.dist(a, b) >= MIN_TRAIL_MM:
                ctx.add([Line(a[0], a[1], b[0], b[1], AXES)])
                trails += 1
    label = f"{ctx.say('Vista explosionada', 'Exploded view')} ({fmt_scale(k)})"
    ctx.add([_label(view_centre[0], page_box[1] - LABEL_ROOM_MM + 1.5, label)])
    width, height = page_box[2] - page_box[0], page_box[3] - page_box[1]
    claim(
        ctx,
        item_id,
        (
            view_centre[0] - width / 2 - ROOM_MARGIN_MM,
            page_box[1] - LABEL_ROOM_MM - ROOM_MARGIN_MM,
            view_centre[0] + width / 2 + ROOM_MARGIN_MM,
            page_box[3] + ROOM_MARGIN_MM,
        ),
    )
    note = f"{len(offsets)} component(s) displaced ({spec.get('mode', 'radial')}), {trails} trail line(s), scale {fmt_scale(k)}"
    if skipped:
        note += f"; {skipped} trail(s) left out because they would cross another part"
    if k < main - 1e-9:
        return "warning", f"{note}; drawn smaller than the sheet's {fmt_scale(main)} to fit the free room"
    return "ok", note


ITEMS = (ItemType("exploded", "xv", check_exploded, draw_exploded, frozenset(EXPLODED_CHECKS), phase=3),)
