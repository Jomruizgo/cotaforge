# ruff: noqa: E501
"""``datum_dims``: the positions of the holes from two datum edges, as baseline dimensions or as a hole table (no
FreeCAD import).

The datums are the two edges of the view the positions are counted from: ``x_from`` (the vertical one) and ``y_from``
(the horizontal one) are each a face seen edge-on (its id) or ``min`` / ``max`` (the left / bottom or right / top
silhouette of the view). Every value is the measured distance from the hole's axis to that edge, in model millimetres.

``auto`` is the default location every sheet with holes carries (``drawing`` makes the item): it locates the holes no
other item locates, in the view that shows each one as a circle, for the measuring tools every shop has (a caliper, a
micrometer, a depth gauge, a dial indicator): per group of equal holes the spacing between its holes and ONE locator from
the real flat face of the part nearest to the group, seen edge-on (or from the datum edge the sheet declares across that
direction), in each direction of the view; the dimensions that do not touch share a row. Where the part has no such face (a
round outline) no locator is drawn and the review asks the designer. It is never a baseline from one datum (that needs a height gauge: ``datum_dims`` with ``x_from`` / ``y_from`` asks
for it) and never the symmetry of the part: the middle of a plate is dimensioned from an edge like any other place.
"""

from dataclasses import dataclass
from typing import Any

import sheet_pattern
from items_common import hole_mark, mark_once
from items_holes import describe
from sheet_context import (
    COMMON_FIELDS,
    MODEL_AXES,
    Context,
    HoleInfo,
    ItemError,
    ItemType,
    Point,
    ViewInfo,
    check_fields,
    dot,
    model_axis,
    parallel,
    v_bool,
    v_enum,
    v_ids,
    v_point2,
    v_points3,
)
from sheet_draw import exact_box, linear_dimension, table
from sheet_marks import NativeTable
from sheet_model import AXES, CENTRE_OVERSHOOT_MM, TEXT, Line, Sheet, Text, text_width
from sheet_units import NativeDim, NativeNote, Ref2

GAP_MM = 1.0
END_MM = 2.0
VALUE_HEIGHT = 3.5
POSITION_ON_DATUM_MM = 0.05  # a hole this near the datum line needs no dimension from it
LINK_OVERSHOOT_MM = CENTRE_OVERSHOOT_MM  # a centre line that orders a row or a column of holes goes this far past the last one
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


def _stack_sides(ctx: Context, view: ViewInfo, spec: dict[str, Any]) -> tuple[str, str]:
    """The side of the view the x dimensions go (``x_side``) and the y ones (``y_side``): the rows go on the side of the view
    that has no other view beside it -- below the top view and above the front one in first angle (the top view hangs under
    the front one), the other way round in third angle."""
    stacked_with_other = (
        {"front": "above", "top": "below"} if not ctx.third_angle else {"front": "below", "top": "above"}
    )
    default_x = stacked_with_other.get(view.name, "below") if (spec.get("auto") or ctx.third_angle) else "below"
    return spec.get("x_side", default_x), spec.get("y_side", "left")


def _ordinates(
    ctx: Context,
    view: ViewInfo,
    holes: list[HoleInfo],
    spec: dict[str, Any],
    dx: float,
    dy: float,
) -> str:
    """The positions of the holes as BASELINE dimensions (ISO 129-1) from the datum edges ``dx`` / ``dy``, per direction of
    the view: one dimension per distinct position, from the datum edge to the hole centre, each on its own line stacked
    outward, nearest first, all outside the part; holes that share a coordinate share its dimension, and a centre line
    joins them so the pattern reads as rows and columns. It is the scheme of a shop with a height gauge or a CMM, asked for
    by name (``datum_dims`` with ``x_from`` / ``y_from``): the default location is the caliper's (``_draw_auto``). They are
    TechDraw's own dimensions (``DrawViewDimension``): FreeCAD has no ordinate dimension, and what it cannot do the sheet does
    not do."""
    x_side, y_side = _stack_sides(ctx, view, spec)
    placed = [(h, view.project(h.centre)) for h in holes]
    item = str(ctx.extras["current"])
    values_x = _along_x(ctx, view, placed, dx, x_side, spec, "", item)
    values_y = _along_y(ctx, view, placed, dy, y_side, spec, "", item)
    said = [
        f"{label} from {', '.join(ctx.num(v) for v in values)}" for label, values in (("x", values_x), ("y", values_y))
    ]
    return f"{len(holes)} hole(s) in the {view.name} view; {'; '.join(said)} (mm, from the datum edges)"


# --- the default location: for the measuring tool of the shop ------------------------------------------------------------

CROSS_TOL_MM = 0.05  # an extension line this close to an end of another dimension only touches it
MAX_SPLIT = 12  # dimensions of one direction that are split between the two sides of the view by trying every way


@dataclass
class _Dim:
    """One dimension of the default location, before it is drawn. It spans ``lo`` .. ``hi`` along its direction (page mm,
    the coordinates of its two extension lines); ``first`` / ``second`` are the hole centres of the columns it runs between
    (``first`` is empty for a locator, which starts at the vertex of the edge it is counted from, ``edge``). The hole each
    end is drawn to is the one nearest the side the dimension stands on."""

    value: float  # model mm
    lo: float
    hi: float
    first: list[Point]
    second: list[Point]
    holes: list[HoleInfo]
    edge: float | None = None  # a locator: the page coordinate of the edge (or datum) it is counted from
    repeats: int = 0  # a repeated spacing is said once: ``3×`` before the pitch (TechDraw's own format keeps the value)
    reach: tuple[float, float] = (0.0, 0.0)  # what it takes along its direction, text and arrows included (page mm)

    @property
    def prefix(self) -> str:
        """The text before the measured value (the native dimension measures the value itself and only adds this)."""
        return f"{self.repeats}×" if self.repeats else ""

    @property
    def span(self) -> float:
        return self.hi - self.lo


def _group_key(pool: list[HoleInfo], rep: HoleInfo) -> tuple[float, ...]:
    """What makes the holes of two axes the same feature: the diameters of the stack on the axis (a counterbore is 9 and 14, a
    plain hole 9)."""
    return tuple(sorted(round(g.diameter, 3) for g in pool if parallel(g.direction, rep.direction) and rep.on_axis(g.centre)))


def _groups(ctx: Context, pool: list[HoleInfo], axes: list[HoleInfo]) -> dict[tuple[float, ...], list[HoleInfo]]:
    """The axes of ``pool`` by group of equal holes (a bore on the axis of revolution of the part has none: the axis says it)."""
    groups: dict[tuple[float, ...], list[HoleInfo]] = {}
    for rep in axes:
        if not _on_revolution_axis(ctx, rep):
            groups.setdefault(_group_key(pool, rep), []).append(rep)
    return groups


def _on_revolution_axis(ctx: Context, hole: HoleInfo) -> bool:
    """Whether ``hole`` lies on the axis of revolution of the part itself (the bore of a bush, a ring, a spacer): the axis
    is its reference, nothing to measure from an edge."""
    if ctx.assembly is not None or ctx.weldment is not None or ctx.geometry is None:
        return False
    found = ctx.geometry.round_extent()
    box = ctx.extras.get("model_box")
    if found is None or box is None:
        return False
    middle = tuple((lo + hi) / 2 for lo, hi in zip(box[0], box[1], strict=True))
    return parallel(hole.direction, found[0]) and hole.on_axis(middle)  # type: ignore[arg-type]


REACH_TOL_MM = 0.02  # the planes of the part are listed to 0.01 mm
AXIS_UNIT = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
ROW_GAP_MM = 1.0  # two dimensions on one row stand at least this far apart (text and arrows included)


def _reachable_edges(ctx: Context, view: ViewInfo, horizontal: bool) -> list[float]:
    """The page coordinates (x of a vertical edge, y of a horizontal one) a caliper can be set against across this direction
    of the view: the ends of the part's extent where the part has a REAL flat face square to that direction, seen edge-on as a
    straight edge. The bounding box says where the part ends, not that anything is there to measure from: the silhouette of a
    cylinder (a shaft across its axis) and the tangent point of a disc are no edge, and an end that is rounded or sloped has
    none either. Without a model to ask (a hand-made context, an assembly) the rectangle of the edges the view drew."""
    box = ctx.extras.get("model_box")
    if box is None:
        silhouette = _silhouette(ctx, view)
        return [silhouette[0], silhouette[2]] if horizontal else [silhouette[1], silhouette[3]]
    axis = model_axis(view.xdir if horizontal else view.ydir)
    if axis is None:
        return []
    index = "xyz".index(axis)
    ends = (box[0][index], box[1][index])
    planes = list(ends) if ctx.geometry is None else ctx.geometry.axial_planes(AXIS_UNIT[axis])
    middle = [(lo + hi) / 2 for lo, hi in zip(box[0], box[1], strict=True)]
    found: list[float] = []
    for end in ends:
        if any(abs(plane - end) <= REACH_TOL_MM for plane in planes):
            middle[index] = end
            page = view.project((middle[0], middle[1], middle[2]))
            found.append(page[0] if horizontal else page[1])
    return found


def _revolution_axis_at(ctx: Context, view: ViewInfo, horizontal: bool) -> float | None:
    """The page coordinate of the axis of revolution of the part when the view shows it as a line along the OTHER page direction
    (a shaft seen from the side: its axis runs along the page, at one height): a hole whose axis meets it there -- a radial
    hole drilled through the axis -- is located across the shaft by the axis itself, nothing to measure."""
    if ctx.assembly is not None or ctx.weldment is not None or ctx.geometry is None:
        return None
    found = ctx.geometry.round_extent()
    box = ctx.extras.get("model_box")
    if found is None or box is None or not parallel(found[0], view.ydir if horizontal else view.xdir):
        return None
    middle = tuple((lo + hi) / 2 for lo, hi in zip(box[0], box[1], strict=True))
    page = view.project(middle)  # type: ignore[arg-type]
    return page[0] if horizontal else page[1]


def _reach_of(dim: _Dim, horizontal: bool, text: str) -> tuple[float, float]:
    """What the dimension takes along its direction: its line, its arrows and its text, wherever the text goes when it does not
    fit (drawn once on a scratch line, as ``linear_dimension`` would)."""
    a, b = ((dim.lo, 0.0), (dim.hi, 0.0)) if horizontal else ((0.0, dim.lo), (0.0, dim.hi))
    overflow = "lo" if dim.edge is None or dim.edge <= (dim.lo + dim.hi) / 2 else "hi"
    entities, box = linear_dimension(a, b, "h" if horizontal else "v", 0.0, text, overflow)
    low, high = (box[0], box[2]) if horizontal else (box[1], box[3])
    for e in entities:
        if not isinstance(e, Line):
            continue
        if horizontal and abs(e.y1) < 1e-9 and abs(e.y2) < 1e-9:  # the dimension line itself
            low, high = min(low, e.x1, e.x2), max(high, e.x1, e.x2)
        elif not horizontal and abs(e.x1) < 1e-9 and abs(e.x2) < 1e-9:
            low, high = min(low, e.y1, e.y2), max(high, e.y1, e.y2)
    return min(low, dim.lo), max(high, dim.hi)


def _group_dims(
    ctx: Context,
    view: ViewInfo,
    placed: list[tuple[HoleInfo, Point]],
    horizontal: bool,
    datum: float | None,
) -> list[_Dim]:
    """The dimensions of ONE group of equal holes along one direction of the view (``horizontal``: page x): the spacing
    between its holes (a caliper measures a distance between two holes) and ONE locator, from the real flat face of the part
    nearest to the group (or from the datum edge the sheet declares across that direction), to its nearest hole. Never the
    symmetry of the part: a group in the middle is dimensioned from the edge like any other. Where the part has no edge to
    measure from across this direction (a round outline, a rounded end) no locator is drawn and the review asks the designer
    (``loc_missing``): the harness never counts from a place the caliper cannot reach. A group on the axis of revolution across
    the view is located by it."""
    i = 0 if horizontal else 1  # the page coordinate this direction measures
    scale = view.scale
    columns: dict[float, list[tuple[HoleInfo, Point]]] = {}
    for hole, p in placed:
        columns.setdefault(round(p[i] / scale, 2), []).append((hole, p))
    keys = sorted(columns)
    axis_name = model_axis(view.xdir if horizontal else view.ydir) or ("x" if horizontal else "y")
    through = _revolution_axis_at(ctx, view, horizontal)
    if through is not None and all(abs(k * scale - through) <= POSITION_ON_DATUM_MM * scale for k in keys):
        for hole, _ in placed:  # the axis says it: nothing to measure across the shaft
            _located(ctx, hole, view, horizontal)
            ctx.extras.setdefault("loc_edge", {}).setdefault(hole.index, set()).add(axis_name)
        return []
    out: list[_Dim] = []
    gaps = [round(b - a, 2) for a, b in zip(keys, keys[1:], strict=False)]
    if len(gaps) >= 2 and max(gaps) - min(gaps) <= 0.011:  # a row at one pitch: the pitch once, with how many
        pairs = [(keys[0], keys[1])]
        repeats = len(keys) - 1
    else:
        pairs = list(zip(keys, keys[1:], strict=False))
        repeats = 0
    for ka, kb in pairs:
        points = [[p for _, p in columns[k]] for k in (ka, kb)]
        holes = [h for k in (ka, kb) for h, _ in columns[k]]
        out.append(_Dim(round(kb - ka, 2), ka * scale, kb * scale, points[0], points[1], holes, repeats=repeats))
    edges = [datum] if datum is not None else _reachable_edges(ctx, view, horizontal)
    edge: float | None = None
    if edges:  # the nearest edge to the group (the low one when it is a tie)
        edge = min(edges, key=lambda e: (round(min(abs(k * scale - e) for k in keys), 3), e))
        key = min(keys, key=lambda k: abs(k * scale - edge))
        distance = round(abs(key * scale - edge) / scale, 2)
        if distance >= POSITION_ON_DATUM_MM:
            span = (min(edge, key * scale), max(edge, key * scale))
            out.append(_Dim(distance, *span, [], [p for _, p in columns[key]], [h for h, _ in columns[key]], edge=edge))
    for dim in out:
        dim.reach = _reach_of(dim, horizontal, dim.prefix + ctx.num(dim.value))
    for hole, p in placed:
        if edge is None:  # nothing to count from: the review asks the designer which face or edge it is
            ctx.extras.setdefault("loc_missing", {}).setdefault(hole.index, set()).add(axis_name)
            continue
        ctx.extras.setdefault("positions", {}).setdefault(hole.index, {})["x" if horizontal else "y"] = round(
            abs(p[i] - edge) / scale, 2
        )  # what the sheet says of each hole: how far from the edge it counted from
        _located(ctx, hole, view, horizontal)
        ctx.extras.setdefault("loc_edge", {}).setdefault(hole.index, set()).add(axis_name)
    return out


def _merged(dims: list[_Dim]) -> list[_Dim]:
    """The dimensions that say the same thing once: two groups whose holes stand on one line (the same distance between the same
    two lines of the part, the same locator from the same edge) share ONE dimension, whose centre line joins the holes of both."""
    out: list[_Dim] = []
    for dim in dims:
        same = next(
            (
                o
                for o in out
                if abs(o.lo - dim.lo) <= 0.01
                and abs(o.hi - dim.hi) <= 0.01
                and (o.edge is None) == (dim.edge is None)
                and o.repeats == dim.repeats
            ),
            None,
        )
        if same is None:
            out.append(dim)
        else:
            same.first, same.second, same.holes = same.first + dim.first, same.second + dim.second, same.holes + dim.holes
    return out


def _crossings(order: list[_Dim]) -> int:
    """How many pairs of ``order`` (nearest the part first) one of whose extension lines lies across the line of the other:
    the outer dimension's two extension lines, inside the span of the inner one."""
    count = 0
    for a, inner in enumerate(order):
        for outer in order[a + 1 :]:
            if any(inner.lo + CROSS_TOL_MM < e < inner.hi - CROSS_TOL_MM for e in (outer.lo, outer.hi)):
                count += 1
    return count


def _split(dims: list[_Dim]) -> tuple[list[_Dim], list[_Dim]]:
    """The dimensions of one direction on the usual side of the view and on the opposite one, so that no extension line crosses
    the line of another dimension (ISO 129): each side stacks the shorter dimensions nearest the part (the longer one stands
    outside the shorter ones it runs along), and two dimensions that overlap without one holding the other cannot share a side
    (an extension line of the outer one would cross the inner line), so the split is the one with the fewest crossings and,
    then, the fewest dimensions on the opposite side."""
    ranked = sorted(dims, key=lambda d: (d.value, d.lo))
    if len(ranked) > MAX_SPLIT or _crossings(ranked) == 0:
        return ranked, []
    best: tuple[int, int, int] | None = None
    chosen: tuple[list[_Dim], list[_Dim]] = (ranked, [])
    for mask in range(1 << len(ranked)):
        far = [d for k, d in enumerate(ranked) if mask >> k & 1]
        near = [d for k, d in enumerate(ranked) if not mask >> k & 1]
        cost = (_crossings(near) + _crossings(far), len(far), mask)
        if best is None or cost < best:
            best, chosen = cost, (near, far)
    return chosen


def _across(outer: _Dim, inner: _Dim) -> bool:
    """Whether an extension line of ``outer`` (it runs from the part out to its own line) lies across the line or the text of ``inner``,
    which it passes when ``inner`` stands nearer the part."""
    return any(inner.reach[0] + CROSS_TOL_MM < e < inner.reach[1] - CROSS_TOL_MM for e in (outer.lo, outer.hi))


def _runs_along(a: _Dim, b: _Dim) -> bool:
    """Whether two dimensions of one direction run along one stretch of the part (a chain does not: it shares no run)."""
    return min(a.hi, b.hi) - max(a.lo, b.lo) > CROSS_TOL_MM


def _fits(rows: list[list[_Dim]], row: int, dim: _Dim) -> bool:
    """Whether ``dim`` can stand on row ``row`` (0: nearest the part) of ``rows`` that hold the others: side by side with what is on
    the row (the texts do not touch), no extension line of an outer dimension across the line of an inner one (ISO 129), and the
    shorter of two that run along each other the nearer the part (ISO 129-1)."""
    for index, others in enumerate(rows):
        for o in others:
            if index == row:
                if dim.reach[0] < o.reach[1] + ROW_GAP_MM and o.reach[0] < dim.reach[1] + ROW_GAP_MM:
                    return False
            elif index < row:  # ``o`` is nearer the part
                if _across(dim, o) or (_runs_along(dim, o) and o.span > dim.span + CROSS_TOL_MM):
                    return False
            elif _across(o, dim) or (_runs_along(dim, o) and dim.span > o.span + CROSS_TOL_MM):
                return False
    return True


def _rows(dims: list[_Dim]) -> list[list[_Dim]]:
    """The dimensions of one side of a view as ROWS of dimension lines, nearest the part first: the shortest first, each on the
    nearest row it fits on, so dimensions that do not touch share a line (a locator and the spacing that goes on from it stand
    in line, as a draughtsman chains them) and a row of its own is made only when nothing before it leaves room."""
    rows: list[list[_Dim]] = []
    for dim in sorted(dims, key=lambda d: (d.value, d.lo)):
        row = next((r for r in range(len(rows)) if _fits(rows, r, dim)), len(rows))
        if row == len(rows):
            rows.append([dim])
        else:
            rows[row].append(dim)
    return rows


def _draw_dims(
    ctx: Context,
    view: ViewInfo,
    sides: list[tuple[str, list[_Dim]]],
    horizontal: bool,
    spec: dict[str, Any],
    tag: str,
    item: str,
) -> list[float]:
    """Draw the dimensions of one direction of ``view``: each side as rows stacked outward from the part (ISO 129-1), the
    dimensions that do not touch sharing a row, with a centre line that joins the holes of a column or a row that share one."""
    box = view.box
    i, j = (0, 1) if horizontal else (1, 0)
    values: list[float] = []
    linked: set[tuple[float, float, float, float]] = set()
    for side, dims in sides:
        low_side = side in ("below", "left")
        edge = (box[1] if horizontal else box[0]) if low_side else (box[3] if horizontal else box[2])

        def near(points: list[Point], low: bool = low_side, other: int = j) -> Point:
            return (min if low else max)(points, key=lambda q: q[other])

        for row in _rows(dims):
            distance = ctx.stack_distance(view.name, side, spec.get("offset_mm"))
            line = edge - distance if low_side else edge + distance
            for dim in row:
                if dim.edge is not None:
                    along = (box[1] if low_side else box[3]) if horizontal else (box[0] if low_side else box[2])
                    a = _real_point(view, dim.edge, along, vertical=horizontal)
                else:
                    a = near(dim.first)
                b = near(dim.second)
                exact = _exact(ctx, dim.holes)
                text = dim.prefix + ctx.num(dim.value)
                entities, taken = linear_dimension(a, b, "h" if horizontal else "v", line, text, "lo" if a[i] <= b[i] else "hi")
                if exact:
                    exact_box(entities)
                for p in (a, b):
                    same = [q for h in dim.holes if abs((q := view.project(h.centre))[i] - p[i]) <= 0.05]
                    far = max(same, key=lambda q: abs(q[j] - p[j]), default=None)
                    if far is not None and abs(far[j] - p[j]) > 0.05:
                        span = tuple(round(v, 2) for v in (*p, *far))
                        if span not in linked and (span[2], span[3], span[0], span[1]) not in linked:
                            linked.add(span)  # type: ignore[arg-type]
                            entities.append(_link(p, far, LINK_OVERSHOOT_MM))
                ctx.add(entities)
                ctx.reserve_dimension([e for e in entities if getattr(e, "layer", None) != AXES], taken)
                ctx.native(
                    NativeDim(
                        item=item,
                        name=f"{tag}{'x' if horizontal else 'y'}{len(values) + 1}",
                        kind="distance",
                        view=view.name,
                        refs=(Ref2("point", a), Ref2("point", b)),
                        label=((a[0] + b[0]) / 2, line) if horizontal else (line, (a[1] + b[1]) / 2),
                        expected=dim.value,
                        prefix=dim.prefix,
                        orient="h" if horizontal else "v",
                        rect=taken,
                        exact=exact,
                    )
                )
                values.append(dim.value)
                _keep_span(ctx, view, horizontal, dim, item)
    return values


def _keep_span(ctx: Context, view: ViewInfo, horizontal: bool, dim: _Dim, item: str) -> None:
    """Keep the span, in model coordinates, that a hole-position dimension (a spacing or a locator) measures: the review adds
    these to the lengths it chains to see whether they close on the overall size (8 + 44 + 8 = 60 is one dimension too many).
    Kept apart from ``dim_records``: those say 'the same length twice', which a spacing drawn once per column is not."""
    unit = view.xdir if horizontal else view.ydir
    name = model_axis(unit)
    if name is None:
        return
    axis = MODEL_AXES[name]
    origin = view.x if horizontal else view.y
    at = dot(view.centre, axis)
    ends = sorted(round(at + (p - origin) / view.scale * dot(unit, axis), 2) for p in (dim.lo, dim.hi))
    ctx.extras.setdefault("position_spans", []).append((item, name, ends[0], ends[1]))


STANDARD_VIEWS = {  # (direction towards the viewer, x direction) of the standard views, first angle
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}


def row_plan(
    holes: list[HoleInfo],
    view_name: str,
    box: tuple[tuple[float, float, float], tuple[float, float, float]],
    scale: float,
    geometry: Any = None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """How many ROWS of dimension lines the default location of ``holes`` takes on the usual side of the standard view
    ``view_name`` of a part with the model box ``box`` and on the opposite one, across (x) and up (y) the page: ``((near_x, far_x),
    (near_y, far_y))``, the room the layout keeps for them (``drawing``). The same arithmetic as ``_draw_auto``, on a scratch
    view at ``scale`` (the rows depend on it: a short dimension at a small scale has its text beside it and takes a row of its own);
    no datum is declared, which only moves a locator."""
    direction, xdir = STANDARD_VIEWS[view_name]
    centre = tuple((lo + hi) / 2 for lo, hi in zip(box[0], box[1], strict=True))
    view = ViewInfo(view_name, 0.0, 0.0, scale, direction, xdir, centre, (0.0, 0.0, 0.0, 0.0))  # type: ignore[arg-type]
    ctx = Context(Sheet(1.0, 1.0), {view_name: view}, holes, geometry, (0.0, 0.0, 1.0, 1.0))  # type: ignore[arg-type]
    ctx.extras["model_box"] = box
    pool = [h for h in holes if view.looks_along(h.direction)]
    out = []
    for horizontal in (True, False):
        dims: list[_Dim] = []
        for reps in _groups(ctx, pool, _axes(pool)).values():
            dims += _group_dims(ctx, view, [(h, view.project(h.centre)) for h in reps], horizontal, None)
        near, far = _split(_merged(dims))
        out.append((len(_rows(near)), len(_rows(far))))
    return out[0], out[1]


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


def _draw_auto(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """Locate every hole no other item locates, each in the view that shows it as a circle, the way the shop measures it with
    the tools every shop has (a caliper, a micrometer, a depth gauge): per GROUP of equal holes the spacing between its holes
    and ONE locator from the edge of the part nearest to it (or from the datum edge the sheet declares across that
    direction), in each direction of the view. Not everything from one datum baseline (that needs a height gauge: ask for it
    with ``datum_dims`` x_from / y_from) and never by the symmetry of the part (a hole in the middle is dimensioned too). A hole
    on the axis of revolution of a turned part has the axis as its reference."""
    taken = located_by_others(ctx, str(spec["id"]))
    details: list[str] = []
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
        axes = _axes(pool)
        own = {"auto": True, **({"offset_mm": spec["offset_mm"]} if "offset_mm" in spec else {})}
        x_side, y_side = _stack_sides(ctx, view, own)
        declared = declared_datums(ctx, view)
        datums = tuple(
            None if face is None else _datum_position(ctx, view, face, horizontal=not horizontal)
            for horizontal, face in ((True, declared[0]), (False, declared[1]))
        )
        groups = _groups(ctx, pool, axes)
        on_axis = [rep for rep in axes if _on_revolution_axis(ctx, rep)]
        for rep in on_axis:  # the axis of the part locates it in both directions: nothing to measure, and the review knows
            _located(ctx, rep, view, True)
            _located(ctx, rep, view, False)
        if on_axis and not groups:
            details.append(f"{len(on_axis)} hole(s) in the {name} view on the axis of the part: the axis locates them, nothing to measure")
        dims: dict[bool, list[_Dim]] = {True: [], False: []}
        for reps in groups.values():
            placed = [(h, view.project(h.centre)) for h in reps]
            dims[True] += _group_dims(ctx, view, placed, True, datums[0])
            dims[False] += _group_dims(ctx, view, placed, False, datums[1])
        if groups:
            tag = "" if first else f"{name}_"
            item = str(ctx.extras["current"])
            shown = []
            for horizontal, side in ((True, x_side), (False, y_side)):
                near, far = _split(_merged(dims[horizontal]))
                opposite = {"below": "above", "above": "below", "left": "right", "right": "left"}[side]
                values = _draw_dims(ctx, view, [(side, near), (opposite, far)], horizontal, own, tag, item)
                if values:
                    shown.append(f"{'x' if horizontal else 'y'} {', '.join(ctx.num(v) for v in values)}")
            details.append(
                f"{len(axes)} hole(s) in the {name} view; {'; '.join(shown)} (mm: the spacing in each group of equal holes "
                "and one locator from the nearest edge, as a caliper measures)"
            )
            first = False
        done = ctx.extras.setdefault("loc_axes", {})
        for rep in axes:  # a counterbore is two holes on one axis: they are located together
            for h in pool:
                if h.index != rep.index and parallel(h.direction, rep.direction) and rep.on_axis(h.centre):
                    if rep.index in done:
                        done.setdefault(h.index, set()).update(done[rep.index])
                        ctx.extras.setdefault("loc_edge", {}).setdefault(h.index, set()).update(
                            ctx.extras.get("loc_edge", {}).get(rep.index, set())
                        )
        ctx.extras.setdefault("auto_covered", set()).update(h.index for h in pool if h.index in done)
        for hole in axes:  # the axis of every hole, as a centre mark that reaches past the largest of its stack
            p = view.project(hole.centre)
            widest = max(g.diameter for g in pool if parallel(g.direction, hole.direction) and g.on_axis(hole.centre))
            if mark_once(ctx, view, p):
                ctx.add(hole_mark(ctx, view, hole, p, widest / 2 * view.scale))
    if not details:
        return "ok", "no hole to locate: every one is located by another item, or none is seen as a circle"
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
    numbers: list[NativeNote] = []
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
        numbers.append(NativeNote(str(spec["id"]), f"l{number}", (str(number),), (p[0] + r + 1.0, p[1] + r + 1.0 + 3.0), 3.0))
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
    ctx.native(
        *numbers,  # the number of each hole on the view: TechDraw annotations
        NativeNote(str(spec["id"]), "title", (title,), (rect[0] + 1.0, rect[3] - 6.0 + 1.8 + 4.0), 4.0),
        NativeTable(  # and the table: a spreadsheet the engineer edits, shown by a DrawViewSpreadsheet
            str(spec["id"]),
            "hole_table",
            (tuple(header), *(tuple(r) for r in rows)),
            tuple(widths),
            tuple(6.0 for _ in range(len(rows) + 1)),
            rect[2],
            rect[1],
            3.5,
        ),
    )
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
