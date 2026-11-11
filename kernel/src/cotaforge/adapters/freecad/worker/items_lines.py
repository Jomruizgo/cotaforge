# ruff: noqa: E501
"""``line``: an axis or a line between two points of the part, in an ISO 128 line type (no FreeCAD import).

The sheet draws its own lines (the ``EJES`` layer is the ISO 128-20 long-dash dotted line, ``OCULTAS`` the dashed one,
``COTAS`` the thin continuous one) and TechDraw shows them as the picture of the item, so what the window and the PDF paint
is exactly the line type asked for. TechDraw's own cosmetic lines cannot be used for this: in 1.1.3 every ``style`` number
paints the same ISO dashed line (measured), so a centre line made with ``makeCosmeticLine`` is never a chain.

``axis=<cylinder face>`` draws the centre line of a shaft, a boss or a bore along its whole length (plus a few mm beyond), in
every standard view that sees it from the side, and the centre cross in the views that look along it; ``view`` says where
(a section view too). ``from`` / ``to`` draw a line between two places of the part (a point of it, a face, an edge, a hole
axis), thin continuous by default. Everything is placed from the model; nothing is typed.
"""

import math
from typing import Any

from items_common import VIEW_ORDER
from items_dims import resolve_ref
from sheet_context import (
    Context,
    ItemError,
    ItemType,
    ViewInfo,
    check_fields,
    dot,
    sub,
    unit,
    v_enum,
    v_ids,
    v_num,
    v_ref,
    v_view_or_section,
)
from sheet_draw import centre_mark
from sheet_model import AXES, DIMENSIONS, HIDDEN, Entity, Line

STYLES = {"chain": AXES, "dashed": HIDDEN, "continuous": DIMENSIONS}
DEFAULT_REACH_MM = 3.0  # an axis goes this far beyond the ends of its cylinder

CHECKS = {
    "style": v_enum(*STYLES),
    "axis": v_ids("face"),
    "from": v_ref,
    "to": v_ref,
    "view": v_view_or_section,
    "extend_mm": v_num(0.0, 50.0),
}


def check(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, CHECKS)
    by_axis = "axis" in out
    by_points = "from" in out or "to" in out
    if by_axis == by_points:
        msg = "line needs axis (a cylindrical face: its centre line) or from + to (two places of the part), not both"
        raise ItemError(msg)
    if by_axis and len(out["axis"]) != 1:
        msg = "axis takes one face id"
        raise ItemError(msg)
    if by_points and ("from" not in out or "to" not in out):
        msg = "a line between two places needs both from and to"
        raise ItemError(msg)
    return out


def _views(ctx: Context, wanted: str | None) -> list[ViewInfo]:
    if wanted is not None:
        return [ctx.view(wanted)]
    return [ctx.views[n] for n in VIEW_ORDER if n in ctx.views]


def _axis_line(ctx: Context, view: ViewInfo, face_id: str, reach: float, layer: str) -> list[Entity]:
    """The centre line of the cylinder in ``view``: through its axis, as long as the face is in the view plus ``reach``;
    the centre cross when the view looks along the axis."""
    face = ctx.geometry.face(face_id)
    if face.kind != "cylinder" or face.axis_point is None or face.axis_dir is None or face.radius is None:
        msg = f"{face_id} is a {face.kind} face: an axis needs a cylindrical face (a shaft, a boss, a bore)"
        raise ItemError(msg)
    centre = view.project(face.axis_point)
    if view.looks_along(face.axis_dir):
        return centre_mark(centre[0], centre[1], face.radius * view.scale)
    if not view.shows_axis_along(face.axis_dir):
        return []
    ax, ay = view.axis(unit(face.axis_dir))
    size = math.hypot(ax, ay)
    u = (ax / size, ay / size)
    loops = ctx.geometry.face_outline(face_id, view.centre, view.xdir, view.direction)
    along = [
        (view.x + p[0] * view.scale - centre[0]) * u[0] + (view.y + p[1] * view.scale - centre[1]) * u[1]
        for loop in loops
        for p in loop
    ]
    if not along:
        msg = f"{face_id} has nothing to measure its length from in the {view.name} view"
        raise ItemError(msg)
    lo, hi = min(along) - reach, max(along) + reach
    return [Line(centre[0] + u[0] * lo, centre[1] + u[1] * lo, centre[0] + u[0] * hi, centre[1] + u[1] * hi, layer)]


def draw(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    style = spec.get("style", "chain" if "axis" in spec else "continuous")
    layer = STYLES[style]
    reach = float(spec.get("extend_mm", DEFAULT_REACH_MM if "axis" in spec else 0.0))
    drawn: list[str] = []
    if "axis" in spec:
        face_id = spec["axis"][0]
        for view in _views(ctx, spec.get("view")):
            entities = _axis_line(ctx, view, face_id, reach, layer)
            if entities:
                ctx.add(entities)
                ctx.used_view(view.name)
                drawn.append(view.name)
        if not drawn:
            where = f"the {spec['view']} view" if "view" in spec else "front, top or right"
            msg = f"{face_id}: its axis is neither along nor across {where}: no view shows it"
            raise ItemError(msg)
        return "ok", f"{style} line of the axis of {face_id} in {', '.join(drawn)}"
    a, b = resolve_ref(ctx, spec["from"]), resolve_ref(ctx, spec["to"])
    chord = sub(b.point, a.point)
    if math.sqrt(dot(chord, chord)) < 0.05:
        msg = "the two places are the same point: there is no line between them"
        raise ItemError(msg)
    candidates = _views(ctx, spec.get("view"))
    for view in candidates:
        if view.shows_axis_along(chord):  # the line is seen at full length
            pa, pb = view.project(a.point), view.project(b.point)
            size = math.dist(pa, pb)
            ux, uy = (pb[0] - pa[0]) / size, (pb[1] - pa[1]) / size
            ctx.add([Line(pa[0] - ux * reach, pa[1] - uy * reach, pb[0] + ux * reach, pb[1] + uy * reach, layer)])
            ctx.used_view(view.name)
            return "ok", f"{style} line from {a.label} to {b.label} in the {view.name} view"
    where = f"the {spec['view']} view" if "view" in spec else "front, top or right"
    msg = f"the line between {a.label} and {b.label} is seen end-on in {where}: choose a view across it"
    raise ItemError(msg)


ITEMS = (ItemType("line", "ln", check, draw, frozenset(CHECKS), phase=0),)
