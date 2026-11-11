"""``finish``: surface-finish symbols after ISO 1302 / ISO 21920 (no FreeCAD import).

The symbol is the check mark with a bar from the top of its long leg and the Ra value under the bar; its form says the
machining: a closed triangle (material removal required), a circle in the V (removal prohibited, the surface stays as
made) or the plain V (any process). It stands upright on the sheet, attached to a face of the part: touching the outline
of a plane that faces up, otherwise at the end of a leader with an arrow on the feature. ``general=true`` draws the
sheet-wide symbol -- the value followed by the basic symbol in parentheses, "all the other surfaces" -- near the title
block.
"""

from typing import Any

from items_attach import attach_face, clear_of_views, place_label
from items_common import asked
from sheet_context import COMMON_FIELDS, Context, ItemError, ItemType, Point, check_fields, v_bool, v_enum, v_ids, v_num
from sheet_draw import arrow
from sheet_model import DIMENSIONS, Circle, Entity, Line, Polyline, Text, text_width

LONG_RISE_MM = 5.0
SHORT_RISE_MM = 2.5
TAN60 = 1.7320508
TEXT_MM = 3.5
BAR_PAD_MM = 0.8
ARROW_MIN_MM = 4.0
RECT = tuple[float, float, float, float]

MACHINING = ("required", "forbidden", "any")


def _one_face(value: Any) -> list[str]:
    ids = v_ids("face")(value)
    if len(ids) != 1:
        msg = "must be ONE face id"
        raise ItemError(msg)
    return ids


CHECKS = {
    "ra": v_num(0.0001, 1000.0),
    "machining": v_enum(*MACHINING),
    "face": _one_face,
    "general": v_bool,
    "view": COMMON_FIELDS["view"],
    "side": COMMON_FIELDS["side"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
}


def check(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, CHECKS)
    if ("face" in out) == bool(out.get("general")):
        msg = "finish needs exactly one of face (a face id) or general=true (the symbol for all the other surfaces)"
        raise ItemError(msg)
    if "ra" not in out and out.get("machining") != "forbidden":
        msg = "ra is required (the roughness in micrometres), except for machining=forbidden"
        raise ItemError(msg)
    return out


def fmt_ra(value: float, comma: bool) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if comma else text


def symbol(vertex: Point, machining: str, text: str | None) -> tuple[list[Entity], RECT, dict[str, Point]]:
    """The symbol with its V at ``vertex``. Returns the entities, the rectangle it takes and its attachment points
    (``vertex``, ``top`` = the top of the long leg, ``end`` = the end of the bar)."""
    vx, vy = vertex
    half = LONG_RISE_MM / TAN60
    long_top = (vx + half, vy + LONG_RISE_MM)
    out: list[Entity] = []
    if machining == "required":
        out.append(Polyline(((vx, vy), (vx - half, vy + LONG_RISE_MM), long_top), DIMENSIONS, closed=True))
    else:
        short_top = (vx - SHORT_RISE_MM / TAN60, vy + SHORT_RISE_MM)
        out.append(Polyline((short_top, (vx, vy), long_top), DIMENSIONS))
    if machining == "forbidden":
        radius = SHORT_RISE_MM * 0.48
        out.append(Circle(vx, vy + SHORT_RISE_MM * 0.96, radius, DIMENSIONS))
    width = text_width(text, TEXT_MM) + 2 * BAR_PAD_MM if text else 0.0
    end = (long_top[0] + width, long_top[1])
    if text:
        out.append(Line(long_top[0], long_top[1], end[0], end[1], DIMENSIONS))
        out.append(
            Text(long_top[0] + BAR_PAD_MM, vy + LONG_RISE_MM - BAR_PAD_MM - 2.5, text, TEXT_MM, DIMENSIONS, "left")
        )
    rect = (vx - half, vy, end[0] + 0.5, vy + LONG_RISE_MM + 0.3)
    return out, rect, {"vertex": (vx, vy), "top": long_top, "end": end}


def _text(ctx: Context, spec: dict[str, Any]) -> str | None:
    return f"Ra {fmt_ra(spec['ra'], ctx.comma)}" if "ra" in spec else None


def _attach_point(points: dict[str, Point], direction: tuple[float, float]) -> Point:
    """Which point of the symbol the leader (or the outline) joins: its vertex when it stands to the right of or above
    the feature, the top of its long leg when below it, the end of its bar when to the left."""
    if direction[1] < -0.7:
        return points["top"]
    if direction[1] > 0.7 or direction[0] >= 0:
        return points["vertex"]
    return points["end"]


def draw_face(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    face_id = spec["face"][0]
    machining = spec.get("machining", "required")
    text = _text(ctx, spec)
    att = attach_face(ctx, face_id, spec.get("view"), spec.get("side"))
    label = f"Ra {fmt_ra(spec['ra'], ctx.comma)}" if "ra" in spec else machining

    def build(anchor: Point, direction: tuple[float, float]) -> tuple[list[Entity], RECT]:
        _, _, points = symbol((0.0, 0.0), machining, text)
        join = _attach_point(points, direction)
        entities, rect, _ = symbol((anchor[0] - join[0], anchor[1] - join[1]), machining, text)
        tip = att.tip_towards(direction)
        if (anchor[0] - tip[0]) ** 2 + (anchor[1] - tip[1]) ** 2 > ARROW_MIN_MM**2:
            entities += [
                Line(tip[0], tip[1], anchor[0], anchor[1], DIMENSIONS),
                arrow(tip, (-direction[0], -direction[1])),
            ]
        return entities, rect

    if att.kind == "plane" and att.normal[1] > 0.9 and spec.get("offset_mm") is None:
        entities, rect = build(att.tip, att.normal)  # standing on the outline
        if ctx.free(rect) and clear_of_views(ctx, rect):
            ctx.reserve(rect)
            ctx.add(entities)
            return "ok", f"{label} ({machining}) on {face_id}, touching its outline in the {att.view.name} view"
    entities, free = place_label(ctx, att, build, spec.get("offset_mm"), asked(spec))
    ctx.add(entities)
    text_out = f"{label} ({machining}) on {face_id} in the {att.view.name} view"
    return (
        ("ok", text_out)
        if free
        else ("warning", f"{text_out}; it overlaps other things: move it with offset_mm or side")
    )


def draw_general(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    machining = spec.get("machining", "required")
    text = _text(ctx, spec)
    left, low, right, top = ctx.area

    def build(x: float, y: float) -> tuple[list[Entity], RECT]:
        entities, rect, _ = symbol((x, y), machining, text)
        paren = 6.0
        x_open = rect[2] + 1.0
        entities.append(Text(x_open, y + 0.3, "(", paren, DIMENSIONS, "left"))
        basic, brect, _ = symbol((x_open + 4.5, y), "any", None)
        entities += basic
        x_close = brect[2] + 0.5
        entities.append(Text(x_close, y + 0.3, ")", paren, DIMENSIONS, "left"))
        return entities, (rect[0], y - 0.5, x_close + 2.5, rect[3] + 1.0)

    probe = build(0.0, 0.0)[1]
    width = probe[2] - probe[0]
    candidates = [
        (right - width - 2.0 - probe[0], low + 3.0),
        (right - width - 2.0 - probe[0], top - 12.0),
        (left + 4.0, top - 12.0),
    ]
    for x, y in candidates:
        entities, rect = build(x, y)
        if ctx.free(rect):
            ctx.reserve(rect)
            ctx.add(entities)
            return "ok", f"general finish {text or machining} for all the other surfaces, near the title block"
    entities, rect = build(*candidates[0])
    ctx.reserve(rect)
    ctx.add(entities)
    return "warning", f"general finish {text or machining} overlaps other things: no free place near the title block"


def draw(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    return draw_general(ctx, spec) if spec.get("general") else draw_face(ctx, spec)


ITEMS = (ItemType("finish", "f", check, draw, frozenset(CHECKS), phase=2),)
