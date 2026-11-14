# ruff: noqa: E501
"""``datum_dims``: the positions of the holes from two datum edges, as baseline dimensions or as a hole table (no
FreeCAD import).

The datums are the two edges of the view the positions are counted from: ``x_from`` (the vertical one) and ``y_from``
(the horizontal one) are each a face seen edge-on (its id) or ``min`` / ``max`` (the left / bottom or right / top
silhouette of the view). Every value is the measured distance from the hole's axis to that edge, in model millimetres.

``auto`` is the default location every sheet with holes carries (``drawing`` makes the item): it locates the holes no
other item locates, in the view that shows each one as a circle, and in each direction of that view from a reference
the sheet HAS: the datums it declares (the ``datum`` items: the first faces seen edge-on across x and across y) or the
part's own plane of symmetry across that direction (measured on the shape): a hole on the plane needs no dimension, the
holes either side of it are one dimension between each pair, and the plane is drawn as a centre line. It never counts
from the corner of the part's box: where no reference exists it draws nothing for that direction and says which
direction lacks one, so the agent asks the engineer which face or edge the positions are counted from.
"""

from dataclasses import dataclass
from typing import Any

import sheet_pattern
from items_common import hole_mark, mark_once
from items_holes import describe
from sheet_context import (
    COMMON_FIELDS,
    Context,
    HoleInfo,
    ItemError,
    ItemType,
    Point,
    ViewInfo,
    check_fields,
    model_axis,
    parallel,
    v_bool,
    v_enum,
    v_ids,
    v_point2,
    v_points3,
)
from sheet_draw import exact_box, linear_dimension, table
from sheet_model import AXES, TEXT, Line, Text, text_width
from sheet_units import NativeDim, Ref2

GAP_MM = 1.0
END_MM = 2.0
VALUE_HEIGHT = 3.5
POSITION_ON_DATUM_MM = 0.05  # a hole this near the datum line needs no dimension from it
LINK_OVERSHOOT_MM = 3.0  # a centre line that orders a row or a column of holes goes this far past the last one
AUTO_VIEWS = ("top", "front", "right")  # the order a hole is looked for as a circle, as hole_dim does


def _holes(value: Any) -> Any:
    if value == "all":
        return "all"
    return v_points3(value)


def _datum(value: Any) -> str:
    if value in ("min", "max"):
        return str(value)
    return v_ids("face")(value)[0]


CHECKS = {
    "mode": v_enum("ordinate", "table"),
    "x_from": _datum,
    "y_from": _datum,
    "holes": _holes,
    "x_side": v_enum("above", "below"),
    "y_side": v_enum("left", "right"),
    "at_mm": v_point2,
    "view": COMMON_FIELDS["view"],
    "offset_mm": COMMON_FIELDS["offset_mm"],
    "auto": v_bool,
}


def check(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, CHECKS)
    if out.get("auto") and (out.get("mode", "ordinate") != "ordinate" or "holes" in out):
        msg = "auto is the default location of the holes no item locates: it takes no mode=table and no holes"
        raise ItemError(msg)
    return out


# --- which holes and which datums -------------------------------------------------------------------------------------


def _axes(holes: list[HoleInfo]) -> list[HoleInfo]:
    """One hole per axis (a counterbore is two holes on one axis: the smallest stands for it)."""
    out: list[HoleInfo] = []
    for hole in sorted(holes, key=lambda h: (h.diameter, h.index)):
        if not any(parallel(h.direction, hole.direction) and h.on_axis(hole.centre) for h in out):
            out.append(hole)
    return out


def _select(ctx: Context, view: ViewInfo, spec: dict[str, Any]) -> list[HoleInfo]:
    wanted = spec.get("holes", "all")
    pool = [h for h in ctx.holes if view.looks_along(h.direction)]
    if wanted != "all":
        pool = [h for h in pool if any(h.on_axis(p) for p in wanted)]
    if not pool:
        msg = (
            f"no hole (of those asked for) looks along the {view.name} view: its dimensions go on the view that shows "
            "them as circles"
        )
        raise ItemError(msg)
    return _axes(pool)


def _silhouette(ctx: Context, view: ViewInfo) -> tuple[float, float, float, float]:
    """The page rectangle the part's silhouette has in ``view``: from the part's own bounding box when the sheet knows it
    (projected the same way the holes are, so a position from the edge is exact and the same in every kind of view), else
    the rectangle of the edges the view drew."""
    corners = ctx.extras.get("model_box")
    if corners is None:
        return view.box
    (x0, y0, z0), (x1, y1, z1) = corners
    points = [view.project((x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    return (
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    )


def _datum_position(ctx: Context, view: ViewInfo, value: str, horizontal: bool) -> float:
    """The page coordinate of a datum edge: x of the vertical one (``horizontal`` False), y of the horizontal one."""
    box = _silhouette(ctx, view)
    if value == "min":
        return box[1] if horizontal else box[0]
    if value == "max":
        return box[3] if horizontal else box[2]
    face = ctx.geometry.face(value)
    axis = view.ydir if horizontal else view.xdir
    if face.kind != "plane" or face.normal is None or not parallel(face.normal, axis):
        which = "y (up the page)" if horizontal else "x (across the page)"
        msg = (
            f"{value} is not a plane seen edge-on that is perpendicular to the view's {which} direction: it cannot be "
            "that datum"
        )
        raise ItemError(msg)
    point = view.project(face.point)
    return point[1] if horizontal else point[0]


# --- ordinate ---------------------------------------------------------------------------------------------------------


def _signed(values: list[float]) -> bool:
    return any(v > 0 for v in values) and any(v < 0 for v in values)


def _real_point(view: ViewInfo, value: float, edge: float, vertical: bool) -> Point:
    """Where a dimension from the datum line starts: a VERTEX of the view on that line (``x = value`` when ``vertical``, else
    ``y = value``) as near as possible to ``edge`` -- never a theoretical point (the corner of a chamfered part is not a
    vertex) -- or, when the view has no vertex list (a hand-made context), the point of the line at ``edge``."""
    on_line = [v for v in view.vertices if abs((v[0] if vertical else v[1]) - value) <= 0.05]
    if not on_line:
        return (value, edge) if vertical else (edge, value)
    return min(on_line, key=lambda v: abs((v[1] if vertical else v[0]) - edge))


def _exact(ctx: Context, holes: Any) -> bool:
    """Whether one of ``holes`` has a position tolerance: the dimension that locates it is theoretically exact (boxed)."""
    toleranced = ctx.extras.get("ted_holes", ())
    return any(h.index in toleranced for h in holes)


def _link(a: Point, b: Point, tail: float) -> Line:
    """The centre line that joins two holes of one row or column, ``tail`` mm past ``b``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = (dx * dx + dy * dy) ** 0.5 or 1.0
    return Line(a[0], a[1], b[0] + dx / length * tail, b[1] + dy / length * tail, AXES)


def _along_x(
    ctx: Context,
    view: ViewInfo,
    placed: list[tuple[HoleInfo, Point]],
    dx: float,
    side: str,
    spec: dict[str, Any],
    tag: str,
    item: str,
) -> list[float]:
    """The x positions from the datum edge ``dx``: one baseline dimension per distinct value, each on its own line stacked
    outward, nearest first; the holes that share a value share its dimension."""
    box = view.box
    signed = _signed([(p[0] - dx) / view.scale for _, p in placed])
    seen: dict[float, tuple[HoleInfo, Point]] = {}
    columns: dict[float, list[Point]] = {}
    for hole, p in placed:
        key = round((p[0] - dx) / view.scale, 2)
        ctx.extras.setdefault("positions", {}).setdefault(hole.index, {})["x"] = abs(key) if not signed else key
        _located(ctx, hole, view, True)
        columns.setdefault(key, []).append(p)
        current = seen.get(key)
        if current is None or (p[1] < current[1][1] if side == "below" else p[1] > current[1][1]):
            seen[key] = (hole, p)
    values: list[float] = []
    edge_y = box[1] if side == "below" else box[3]
    start = _real_point(view, dx, edge_y, vertical=True)
    for key in sorted(seen, key=abs):
        _, p = seen[key]
        if abs(key) < POSITION_ON_DATUM_MM:  # a hole on the datum line has no distance from it
            continue
        distance = ctx.stack_distance(view.name, side, spec.get("offset_mm"))
        line = edge_y - distance if side == "below" else edge_y + distance
        text = ctx.num(key if signed else abs(key))
        exact = _exact(ctx, (h for h, q in placed if round((q[0] - dx) / view.scale, 2) == key))
        entities, taken = linear_dimension(start, p, "h", line, text, "lo" if dx <= p[0] else "hi")
        if exact:
            exact_box(entities)
        far = max(columns[key], key=lambda q: abs(q[1] - p[1]))
        if abs(far[1] - p[1]) > 0.05:
            entities.append(_link(p, far, LINK_OVERSHOOT_MM))
        ctx.add(entities)
        ctx.reserve_dimension([e for e in entities if getattr(e, "layer", None) != AXES], taken)
        ctx.native(
            NativeDim(
                item=item,
                name=f"{tag}x{len(values) + 1}",
                kind="distance",
                view=view.name,
                refs=(Ref2("point", start), Ref2("point", p)),
                label=((start[0] + p[0]) / 2, line),
                expected=abs(key),
                orient="h",
                rect=taken,
                exact=exact,
            )
        )
        values.append(key)
    return values


def _along_y(
    ctx: Context,
    view: ViewInfo,
    placed: list[tuple[HoleInfo, Point]],
    dy: float,
    side: str,
    spec: dict[str, Any],
    tag: str,
    item: str,
) -> list[float]:
    """The y positions from the datum edge ``dy``, as ``_along_x`` does for x (the dimensions go left or right)."""
    box = view.box
    signed = _signed([(p[1] - dy) / view.scale for _, p in placed])
    seen: dict[float, tuple[HoleInfo, Point]] = {}
    rows: dict[float, list[Point]] = {}
    for hole, p in placed:
        key = round((p[1] - dy) / view.scale, 2)
        ctx.extras.setdefault("positions", {}).setdefault(hole.index, {})["y"] = abs(key) if not signed else key
        _located(ctx, hole, view, False)
        rows.setdefault(key, []).append(p)
        current = seen.get(key)
        if current is None or (p[0] < current[1][0] if side == "left" else p[0] > current[1][0]):
            seen[key] = (hole, p)
    values: list[float] = []
    edge_x = box[0] if side == "left" else box[2]
    start = _real_point(view, dy, edge_x, vertical=False)
    for key in sorted(seen, key=abs):
        _, p = seen[key]
        if abs(key) < POSITION_ON_DATUM_MM:
            continue
        distance = ctx.stack_distance(view.name, side, spec.get("offset_mm"))
        line = edge_x - distance if side == "left" else edge_x + distance
        text = ctx.num(key if signed else abs(key))
        exact = _exact(ctx, (h for h, q in placed if round((q[1] - dy) / view.scale, 2) == key))
        entities, taken = linear_dimension(start, p, "v", line, text, "lo" if dy <= p[1] else "hi")
        if exact:
            exact_box(entities)
        far = max(rows[key], key=lambda q: abs(q[0] - p[0]))
        if abs(far[0] - p[0]) > 0.05:
            entities.append(_link(p, far, LINK_OVERSHOOT_MM))
        ctx.add(entities)
        ctx.reserve_dimension([e for e in entities if getattr(e, "layer", None) != AXES], taken)
        ctx.native(
            NativeDim(
                item=item,
                name=f"{tag}y{len(values) + 1}",
                kind="distance",
                view=view.name,
                refs=(Ref2("point", start), Ref2("point", p)),
                label=(line, (start[1] + p[1]) / 2),
                expected=abs(key),
                orient="v",
                rect=taken,
                exact=exact,
            )
        )
        values.append(key)
    return values


def _located(ctx: Context, hole: HoleInfo, view: ViewInfo, horizontal: bool) -> None:
    """Keep that the default location positions ``hole`` along the model axis the page direction ``horizontal`` runs
    along: the review reads it (a hole located in one direction only is a finding, a hole in none is another)."""
    axis = model_axis(view.xdir if horizontal else view.ydir)
    if axis is not None:
        ctx.extras.setdefault("loc_axes", {}).setdefault(hole.index, set()).add(axis)


def _mirror_pairs(
    ctx: Context,
    view: ViewInfo,
    placed: list[tuple[HoleInfo, Point]],
    plane: float,
    horizontal: bool,
    side: str,
    spec: dict[str, Any],
    tag: str,
    item: str,
) -> list[float]:
    """A direction the part is symmetric across: ``plane`` (the page coordinate of its plane of symmetry) is the reference.
    A hole on the plane needs no dimension; the holes either side come in pairs (the part is mirror-symmetric) and the pair
    is ONE dimension between the two centres, on its own line stacked outward. The plane itself is drawn by the caller."""
    i, j = (0, 1) if horizontal else (1, 0)  # i: the coordinate this direction measures, j: the other one
    low_side = side in ("below", "left")
    pairs: dict[float, dict[int, tuple[HoleInfo, Point]]] = {}
    lines: dict[float, list[Point]] = {}
    for hole, p in placed:
        key = round((p[i] - plane) / view.scale, 2)
        ctx.extras.setdefault("positions", {}).setdefault(hole.index, {})["x" if horizontal else "y"] = abs(key)
        if abs(key) < POSITION_ON_DATUM_MM:  # on the plane: located by symmetry, nothing to say
            _located(ctx, hole, view, horizontal)
            continue
        lines.setdefault(key, []).append(p)
        sign = 1 if key > 0 else -1
        slot = pairs.setdefault(abs(key), {})
        current = slot.get(sign)
        if current is None or (p[j] < current[1][j] if low_side else p[j] > current[1][j]):
            slot[sign] = (hole, p)
    box = view.box
    edge = (box[1] if horizontal else box[0]) if low_side else (box[3] if horizontal else box[2])
    values: list[float] = []
    for value in sorted(pairs):
        slot = pairs[value]
        if len(slot) != 2:
            continue  # a hole with no mirror image: the symmetry does not locate it
        pa, pb = slot[-1][1], slot[1][1]
        distance = ctx.stack_distance(view.name, side, spec.get("offset_mm"))
        line = edge - distance if low_side else edge + distance
        span = round(abs(pb[i] - pa[i]) / view.scale, 2)
        exact = _exact(ctx, (h for h, q in placed if round(abs(q[i] - plane) / view.scale, 2) == value))
        entities, taken = linear_dimension(pa, pb, "h" if horizontal else "v", line, ctx.num(span), "lo")
        if exact:
            exact_box(entities)
        for key in (-value, value):
            _, p = slot[1 if key > 0 else -1]
            far = max(lines[key], key=lambda q: abs(q[j] - p[j]))
            if abs(far[j] - p[j]) > 0.05:
                entities.append(_link(p, far, LINK_OVERSHOOT_MM))
            for hole, q in placed:
                if round((q[i] - plane) / view.scale, 2) == key:
                    _located(ctx, hole, view, horizontal)
        ctx.add(entities)
        ctx.reserve_dimension([e for e in entities if getattr(e, "layer", None) != AXES], taken)
        mid = (pa[i] + pb[i]) / 2
        ctx.native(
            NativeDim(
                item=item,
                name=f"{tag}{'x' if horizontal else 'y'}{len(values) + 1}",
                kind="distance",
                view=view.name,
                refs=(Ref2("point", pa), Ref2("point", pb)),
                label=(mid, line) if horizontal else (line, mid),
                expected=span,
                orient="h" if horizontal else "v",
                rect=taken,
                exact=exact,
            )
        )
        values.append(span)
    return values


def _symmetry_line(ctx: Context, view: ViewInfo, plane: float, horizontal: bool) -> None:
    """The centre line of a plane of symmetry across the view (a chain line past the outline at both ends), once."""
    done: set[tuple[str, bool]] = ctx.extras.setdefault("symmetry_lines", set())
    if (view.name, horizontal) in done:
        return
    done.add((view.name, horizontal))
    box = view.box
    reach = LINK_OVERSHOOT_MM
    if horizontal:  # the plane is the vertical line x = plane
        ctx.add([Line(plane, box[1] - reach, plane, box[3] + reach, AXES)])
    else:
        ctx.add([Line(box[0] - reach, plane, box[2] + reach, plane, AXES)])


def _ordinates(
    ctx: Context,
    view: ViewInfo,
    holes: list[HoleInfo],
    spec: dict[str, Any],
    dx: float,
    dy: float,
    tag: str = "",
    modes: tuple[str | None, str | None] = ("datum", "datum"),
) -> str:
    """The positions of the holes as BASELINE dimensions (ISO 129-1), per direction of the view: ``modes`` says what each
    is counted from -- ``datum`` (the edge ``dx`` / ``dy``: one dimension per distinct position, from the datum edge to the
    hole centre, each on its own line stacked outward, nearest first, all outside the part; holes that share a coordinate
    share its dimension, and a centre line joins them so the pattern reads as rows and columns), ``symmetry`` (the plane
    of symmetry at ``dx`` / ``dy``: see ``_mirror_pairs``) or ``None`` (nothing: no reference, the caller says so). They
    are TechDraw's own dimensions (``DrawViewDimension``): FreeCAD has no ordinate dimension, and what it cannot do the
    sheet does not do."""
    # the rows go on the side of the view that has no other view beside it: below the top view and above the front one in
    # first angle (the top view hangs under the front one), the other way round in third angle
    stacked_with_other = (
        {"front": "above", "top": "below"} if not ctx.third_angle else {"front": "below", "top": "above"}
    )
    default_x = stacked_with_other.get(view.name, "below") if (spec.get("auto") or ctx.third_angle) else "below"
    x_side, y_side = spec.get("x_side", default_x), spec.get("y_side", "left")
    placed = [(h, view.project(h.centre)) for h in holes]
    item = str(ctx.extras["current"])
    values_x: list[float] = []
    values_y: list[float] = []
    if modes[0] == "datum":
        values_x = _along_x(ctx, view, placed, dx, x_side, spec, tag, item)
    elif modes[0] == "symmetry":
        _symmetry_line(ctx, view, dx, True)
        values_x = _mirror_pairs(ctx, view, placed, dx, True, x_side, spec, tag, item)
    if modes[1] == "datum":
        values_y = _along_y(ctx, view, placed, dy, y_side, spec, tag, item)
    elif modes[1] == "symmetry":
        _symmetry_line(ctx, view, dy, False)
        values_y = _mirror_pairs(ctx, view, placed, dy, False, y_side, spec, tag, item)
    said = []
    for label, mode, values in (("x", modes[0], values_x), ("y", modes[1], values_y)):
        shown = ", ".join(ctx.num(v) for v in values)
        if mode == "datum":
            said.append(f"{label} from {shown}")
        elif mode == "symmetry":
            said.append(f"{label} about the symmetry line" + (f" between {shown}" if values else ""))
    where = "from the datum edges" if "datum" in modes else "from the symmetry lines"
    return f"{len(holes)} hole(s) in the {view.name} view; {'; '.join(said)} (mm, {where})"


# --- the default location ------------------------------------------------------------------------------------------------


def _holes_of_ref(ctx: Context, ref: dict[str, Any]) -> list[HoleInfo]:
    """The holes a dimension reference stands for: the axis through ``at``, a hole wall face, the centre of a circular edge."""
    if "at" in ref:
        return [h for h in ctx.holes if h.on_axis(tuple(ref["at"]))]  # type: ignore[arg-type]
    if "face" in ref:
        return [h for h in ctx.holes if ref["face"] in h.faces]
    try:
        edge = ctx.geometry.edge(ref["edge"])
    except ItemError:
        return []
    if edge.kind == "circle" and edge.centre is not None:
        return [h for h in ctx.holes if h.on_axis(edge.centre)]
    return []


def located_by_others(ctx: Context, own_id: str) -> set[int]:
    """The holes some item other than ``own_id`` locates: a ``datum_dims`` that is not the default one, or a distance
    ``dim`` that starts or ends on the hole (the engineer chose how those are dimensioned)."""
    located: set[int] = set()
    for item in ctx.extras.get("items", []):
        if item.get("type") == "pattern_dim":  # a bolt circle (or a hole on a radius) is located by its radius and angle
            located |= sheet_pattern.holes_of_item(ctx, item)
        if item.get("id") == own_id or item.get("auto"):
            continue
        if item.get("type") == "datum_dims":
            view = ctx.views.get(item.get("view", "top"))
            wanted = item.get("holes", "all")
            for h in ctx.holes:
                if view is not None and view.looks_along(h.direction):
                    if wanted == "all" or any(h.on_axis(p) for p in wanted):
                        located.add(h.index)
        elif item.get("type") == "dim" and item.get("kind") == "distance":
            for end in ("from", "to"):
                located |= {h.index for h in _holes_of_ref(ctx, item[end])}
    return located


def declared_datums(ctx: Context, view: ViewInfo) -> tuple[str | None, str | None]:
    """The datums of ``view`` the sheet declares: ``(x_from, y_from)``, each a face id or ``None`` (no datum across that
    direction). They are the first ``datum`` items (by letter) whose face is a plane seen edge-on across the x and across
    the y of the view; a datum perpendicular to the view (the bottom of a plate seen from above) says nothing about a
    position in it."""
    found: dict[str, str] = {}
    datums = sorted(
        (i for i in ctx.extras.get("items", []) if i.get("type") == "datum" and i.get("face")),
        key=lambda i: str(i.get("letter", "")),
    )
    for item in datums:
        face_id = item["face"][0]
        try:
            face = ctx.geometry.face(face_id)
        except ItemError:
            continue
        if face.kind != "plane" or face.normal is None:
            continue
        if "x" not in found and parallel(face.normal, view.xdir):
            found["x"] = face_id
        elif "y" not in found and parallel(face.normal, view.ydir):
            found["y"] = face_id
    return found.get("x"), found.get("y")


@dataclass(frozen=True)
class Reference:
    """What the positions of one direction of a view are counted from: ``datum`` (a declared face, ``value`` its id) or
    ``symmetry`` (the part's plane of symmetry across the direction); ``axis`` is the model axis the direction runs along."""

    kind: str
    axis: str
    position: float  # the page coordinate of the datum edge / the plane of symmetry in this direction
    value: str = ""


def _reference(ctx: Context, view: ViewInfo, horizontal: bool, face: str | None) -> Reference | None:
    """The reference of one direction of ``view`` (page x when ``horizontal``): the datum the sheet declares across it, else
    the part's plane of symmetry across it, else none -- a corner of the box is not a reference, the engineer says which
    edge matters."""
    vector = view.xdir if horizontal else view.ydir
    axis = model_axis(vector) or ""
    if face is not None:
        return Reference("datum", axis, _datum_position(ctx, view, face, horizontal=not horizontal), face)
    plane = ctx.extras.get("symmetry", {}).get(axis) if axis else None
    if plane is None:
        return None
    point = list(view.centre)
    point["xyz".index(axis)] = plane
    page = view.project((point[0], point[1], point[2]))
    return Reference("symmetry", axis, page[0] if horizontal else page[1])


def _draw_auto(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """Locate every hole no other item locates, each in the view that shows it as a circle, in each direction from the
    reference the sheet has (a declared datum, else the plane of symmetry); a direction with none is said, not guessed."""
    taken = located_by_others(ctx, str(spec["id"]))
    details: list[str] = []
    missing: dict[str, int] = {}
    first = True
    for name in AUTO_VIEWS:
        view = ctx.views.get(name)
        if view is None or spec.get("view", name) != name:
            continue
        pool = [
            h for h in ctx.holes if view.looks_along(h.direction) and h.index not in taken and _seen_here(ctx, h, name)
        ]
        if not pool:
            continue
        ctx.used_view(name)
        declared = declared_datums(ctx, view)
        refs = (_reference(ctx, view, True, declared[0]), _reference(ctx, view, False, declared[1]))
        own = {"auto": True, **({"offset_mm": spec["offset_mm"]} if "offset_mm" in spec else {})}
        axes = _axes(pool)
        if any(refs):
            modes = tuple(r.kind if r is not None else None for r in refs)
            dx, dy = (r.position if r is not None else 0.0 for r in refs)
            details.append(_ordinates(ctx, view, axes, own, dx, dy, "" if first else f"{name}_", modes))  # type: ignore[arg-type]
            first = False
        for horizontal, ref in ((True, refs[0]), (False, refs[1])):
            if ref is None:
                axis = model_axis(view.xdir if horizontal else view.ydir) or ("x" if horizontal else "y")
                missing[axis] = missing.get(axis, 0) + len(axes)
                for h in pool:  # the review says which holes lack a reference, and along which axis
                    ctx.extras.setdefault("loc_missing", {}).setdefault(h.index, set()).add(axis)
        done = ctx.extras.setdefault("loc_axes", {})
        for rep in axes:  # a counterbore is two holes on one axis: they are located together
            for h in pool:
                if h.index != rep.index and parallel(h.direction, rep.direction) and rep.on_axis(h.centre):
                    if rep.index in done:
                        done.setdefault(h.index, set()).update(done[rep.index])
        ctx.extras.setdefault("auto_covered", set()).update(h.index for h in pool if h.index in done)
        for hole in axes:  # the axis of every hole, as a centre mark that reaches past the largest of its stack
            p = view.project(hole.centre)
            widest = max(g.diameter for g in pool if parallel(g.direction, hole.direction) and g.on_axis(hole.centre))
            if mark_once(ctx, view, p):
                ctx.add(hole_mark(ctx, view, hole, p, widest / 2 * view.scale))
    if not details and not missing:
        return "ok", "no hole to locate: every one is located by another item, or none is seen as a circle"
    if missing:
        where = " and ".join(sorted(missing))
        asked = (
            f"no reference to count the position along {where} from: the part is not symmetric across it and no datum "
            "item faces across it. Ask the engineer which face or edge those positions are counted from, declare it with "
            "a datum item (face=...) and the location follows"
        )
        return "warning", "; ".join([*details, asked])
    return "ok", "; ".join(details)


def _seen_here(ctx: Context, hole: HoleInfo, name: str) -> bool:
    """Whether ``name`` is the view that shows ``hole`` as a circle first (top, front, right: a hole shows in one)."""
    for other in AUTO_VIEWS:
        view = ctx.views.get(other)
        if view is not None and view.looks_along(hole.direction):
            return other == name
    return False


# --- hole table -------------------------------------------------------------------------------------------------------


def _table(
    ctx: Context, view: ViewInfo, holes: list[HoleInfo], spec: dict[str, Any], dx: float, dy: float
) -> tuple[str, str]:
    placed = sorted(((h, view.project(h.centre)) for h in holes), key=lambda hp: (-round(hp[1][1], 1), hp[1][0]))
    rows: list[list[str]] = []
    labels: list[Any] = []
    for number, (hole, p) in enumerate(placed, start=1):
        rows.append(
            [
                str(number),
                ctx.num(round((p[0] - dx) / view.scale, 2)),
                ctx.num(round((p[1] - dy) / view.scale, 2)),
                " / ".join(describe(ctx, hole)),
            ]
        )
        r = hole.diameter / 2 * view.scale
        labels.append(Text(p[0] + r + 1.0, p[1] + r + 1.0, str(number), 3.0, TEXT, "left"))
    header = ["N", "X", "Y", ctx.say("Descripción", "Description")]
    widths = [9.0, 18.0, 18.0, max(30.0, max(text_width(r[3], 3.0) for r in rows) + 3.0)]
    title = ctx.say("TABLA DE AGUJEROS", "HOLE TABLE")
    height = (len(rows) + 2) * 6.0
    _, low, right, _ = ctx.area
    total = sum(widths)
    if "at_mm" in spec:
        x, y_top = spec["at_mm"]
    else:
        x, y_top = right - total, low + height
    entities, rect = table(x, y_top, widths, header, rows, title)
    room = ctx.free(rect) or "at_mm" in spec
    for step in range(1, 11):  # not free: try higher up, and when nothing is free stay where it was asked to be
        if room:
            break
        higher = table(x, y_top + 8.0 * step, widths, header, rows, title)
        if ctx.free(higher[1]):
            (entities, rect), room = higher, True
    ctx.reserve(rect)
    ctx.add([*labels, *entities])
    text = f"table of {len(rows)} hole(s) with x and y from the datum edges (mm), numbered on the {view.name} view"
    if not room:
        return "warning", f"{text}; it overlaps other things at the bottom right: place it with at_mm=[x, y] (top left)"
    return "ok", text


def draw(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    if spec.get("auto"):
        return _draw_auto(ctx, spec)
    view = ctx.view(spec.get("view", "top"))
    ctx.used_view(view.name)
    holes = _select(ctx, view, spec)
    dx = _datum_position(ctx, view, spec.get("x_from", "min"), horizontal=False)
    dy = _datum_position(ctx, view, spec.get("y_from", "min"), horizontal=True)
    if spec.get("mode", "ordinate") == "table":
        return _table(ctx, view, holes, spec, dx, dy)
    return "ok", _ordinates(ctx, view, holes, spec, dx, dy)


ITEMS = (ItemType("datum_dims", "g", check, draw, frozenset(CHECKS), phase=0),)
