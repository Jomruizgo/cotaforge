# ruff: noqa: E501
"""``overall`` and ``dim``: the overall size of the part and dimensions between features (no FreeCAD import).

``dim`` has four kinds -- ``distance`` (linear, between two references: points of the part, faces, edges or hole
centres, which also gives centre-to-centre), ``radius`` (a cylindrical face), ``angle`` (between two planar faces) and
``chamfer`` (a flat chamfer face: leg x angle). The value is always measured on the model; the item only says WHAT to
measure and where to put it.
"""

import math
from dataclasses import dataclass
from typing import Any

import sheet_interface
import sheet_thread
import sheet_tol
import sheet_viewrules as rules
from items_attach import attach_face
from items_common import asked, outward_diagonal, place_leader
from sheet_collide import segment_hits_rect, segments_of
from sheet_context import (
    COMMON_FIELDS,
    Context,
    ItemError,
    ItemType,
    Point,
    Vec3,
    ViewInfo,
    add,
    check_fields,
    cross,
    dot,
    model_axis,
    norm,
    parallel,
    perpendicular,
    scale,
    sub,
    unit,
    v_bool,
    v_enum,
    v_ids,
    v_point2,
    v_ref,
    v_view_or_section,
)
from sheet_draw import angle_dimension, exact_box, linear_dimension
from sheet_model import CONTOUR, Entity, text_width
from sheet_units import NativeDim, Ref2

AXES: dict[str, Vec3] = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}
OVERALL = "overall"  # the key of the outer diameter of a body of revolution, given by ``overall`` and by a ``dim`` on its face
VIEW_PREFERENCE = {"x": ("front", "top"), "y": ("top", "right"), "z": ("front", "right")}
DEFAULT_SIDE = {("h", "front"): "above", ("h", "top"): "below", ("h", "right"): "above"}
DEFAULT_SIDE_V = {"front": "right", "top": "right", "right": "left"}
ANGLE_RADIUS_MM = 12.0
POSITION_EPS_MM = 0.05

CHECKS = {
    "kind": v_enum("distance", "radius", "angle", "chamfer", "diameter"),
    "from": v_ref,
    "to": v_ref,
    "along": v_enum("x", "y", "z"),
    "arrows": v_enum("auto", "inside", "outside"),
    "exact": v_bool,
    "elbow_mm": v_point2,  # where the global layout ended the leader (the vector from its tip): written by it, not asked for
    "face": v_ids("face"),
    "faces": v_ids("face"),
    **sheet_tol.CHECKS,
    **COMMON_FIELDS,
    "view": v_view_or_section,
}


# --- overall ---------------------------------------------------------------------------------------------------------


def check_overall(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, {"offset_mm": COMMON_FIELDS["offset_mm"], "side": v_enum("above", "below")})


THREAD_SPAN_TOL_MM = 0.5  # a thread this near to the whole length of the part is the whole length


def part_length_along(ctx: Context, axis: Vec3) -> float | None:
    """The overall length of the part along ``axis`` (the model box, else the view that shows that axis in its plane)."""
    box = ctx.extras.get("model_box")
    if box is not None:
        return sum(abs(hi - lo) * abs(a) for lo, hi, a in zip(box[0], box[1], axis, strict=True))
    for view in ctx.views.values():
        if not view.shows_axis_along(axis):
            continue
        ax, ay = view.axis(axis)
        if abs(ax) > 0.99:
            return (view.box[2] - view.box[0]) / view.scale
        if abs(ay) > 0.99:
            return (view.box[3] - view.box[1]) / view.scale
    return None


def thread_states_diameter(ctx: Context, diameter: float) -> bool:
    """Whether a callout of the sheet already says the outer diameter ``diameter`` of the WHOLE part as the major diameter of
    a thread (M12 carries Ø12): a ``dim`` of kind diameter on a cylinder an ``external_thread`` threaded, of that diameter,
    when the thread runs the whole length of the part. A thread that covers only an end leaves a plain part of the same
    diameter beside it, and the callout of the thread says nothing of that one: it keeps its own Ø."""
    for spec in ctx.extras.get("items", []):
        if spec.get("type") != "dim" or spec.get("kind") != "diameter" or not spec.get("face"):
            continue
        face_id = spec["face"][0]
        try:
            face = ctx.geometry.face(face_id)
        except ItemError:
            continue
        if face.radius is None or abs(2 * face.radius - diameter) >= 0.05:
            continue
        threads = sheet_thread.threads_on_face(ctx, face_id, face)
        if not threads:
            continue
        axis = face.axis_dir if face.axis_dir is not None else threads[0].direction
        length = part_length_along(ctx, unit(axis))
        if length is None or sum(t.length for t in threads) >= length - THREAD_SPAN_TOL_MM:
            return True
    return False


def diameter_dimensioned(ctx: Context, view: ViewInfo, diameter: float) -> bool:
    """Whether a ``dim`` of kind diameter of the sheet gives ``diameter`` between the two silhouette lines of a step of a turned
    part on ``view`` (``_turned_diameter``): the overall height of the part is that same diameter, and one feature has one
    dimension, so ``overall`` leaves its own out."""
    if ctx.geometry.round_extent() is None:
        return False
    for spec in ctx.extras.get("items", []):
        if spec.get("type") != "dim" or spec.get("kind") != "diameter" or not spec.get("face"):
            continue
        if spec.get("view") not in (None, view.name) or any(spec.get(k) is not None for k in ("side", "offset_mm", "elbow_mm")):
            continue
        face_id = spec["face"][0]
        try:
            face = ctx.geometry.face(face_id)
        except ItemError:
            continue
        if face.kind != "cylinder" or face.radius is None or face.axis_dir is None or abs(2 * face.radius - diameter) >= 0.05:
            continue
        if any(face_id in hole.faces for hole in ctx.holes) or sheet_thread.threads_on_face(ctx, face_id, face):
            continue
        if view.shows_axis_along(face.axis_dir) and max(abs(c) for c in view.axis(face.axis_dir)) >= 0.99:
            return True
    return False


def _threaded_end(ctx: Context, view: ViewInfo, diameter: float) -> Point | None:
    """The page direction from the middle of ``view`` to the threads of this diameter that cover only part of the part, or
    ``None`` (no such thread): the plain part, whose Ø the overall dimension is, lies the other way."""
    if thread_states_diameter(ctx, diameter):
        return None
    mids = [
        view.project(tuple(s + d * t.length / 2 for s, d in zip(t.start, t.direction, strict=True)))  # type: ignore[arg-type]
        for t in ctx.threads
        if abs(t.major - diameter) < 0.05
    ]
    if not mids:
        return None
    cx, cy = (view.box[0] + view.box[2]) / 2, (view.box[1] + view.box[3]) / 2
    return sum(m[0] for m in mids) / len(mids) - cx, sum(m[1] for m in mids) / len(mids) - cy


def view_outline(ctx: Context, box: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """The visible outline (contour layer) of the part's ink that lies in ``box`` (a view of the sheet), as segments."""
    ink = [e for e in ctx.extras.get("view_ink", []) if getattr(e, "layer", None) == CONTOUR]
    x0, y0, x1, y1 = box
    return [s for s in segments_of(ink) if x0 - 1 <= (s[0] + s[2]) / 2 <= x1 + 1 and y0 - 1 <= (s[1] + s[3]) / 2 <= y1 + 1]


def extent_runs(box: tuple[float, float, float, float]) -> list[tuple[float, float, float, float]]:
    """The extension lines of TechDraw's own width dimension (its extent dimension) over the view ``box``: they stand on the
    two lower corners of the view and rise to the dimension line, whatever the part is (measured on a sheet of two plates held
    apart by spacers: the line crosses the gap between them). Only what lies inside the view is returned."""
    return [(box[0], box[1], box[0], box[3]), (box[2], box[1], box[2], box[3])]


def _extent_gap(ctx: Context, box: tuple[float, float, float, float], side: str) -> float:
    """How far the extension lines of the width dimension hang over no line of the part when it stands ``side`` of the view
    (0 below: they go away from the part)."""
    if side != "above":
        return 0.0
    outline = view_outline(ctx, box)
    if not outline:
        return 0.0
    gaps = rules.extent_gaps([("o1", "view", side, extent_runs(box))], {"view": outline})
    return gaps[0].gap_mm if gaps else 0.0


def _record_extent(ctx: Context, view: str, box: tuple[float, float, float, float], line: float, side: str) -> None:
    """Keep the extension lines of an overall width dimension as TechDraw draws them (the review reads them)."""
    ctx.extras.setdefault("extent_dims", []).append(
        (str(ctx.extras.get("current") or ""), view, side, extent_runs(box) if side == "above" else [])
    )


def _is_diameter(view: ViewInfo, axis: Vec3 | None) -> tuple[bool, bool]:
    """Whether the (width, height) a view shows of a body of revolution about ``axis`` are its diameter."""
    if axis is None:
        return False, False
    if view.looks_along(axis):
        return True, True
    ax, ay = view.axis(axis)
    return (abs(ay) > 0.99, abs(ax) > 0.99)


def _record(ctx: Context, name: str, view: str, orient: str, line: float, mid: float, text: str) -> None:
    """Keep where an overall dimension was drawn (page mm, y up): TechDraw's own object for it is put on the same line
    (``sheet_mirror``)."""
    ctx.extras.setdefault("overall_dims", {})[name] = {
        "view": view,
        "orient": orient,
        "line": line,
        "mid": mid,
        "text": text,
    }


def _record_overall(ctx: Context, view: ViewInfo, horizontal: bool, diameter: float | None = None) -> None:
    """Keep that an overall dimension says the part's whole extent along the model axis a page direction of ``view`` runs
    along -- and, when it is the diameter of a body of revolution, that diameter (the review flags the same size given again
    in another view)."""
    box = ctx.extras.get("model_box")
    axis = model_axis(view.xdir if horizontal else view.ydir)
    if box is not None and axis is not None:
        i = "xyz".index(axis)
        ctx.record_dimension(("len", axis, round(box[0][i], 2), round(box[1][i], 2)), view.name)
    if diameter is not None:
        ctx.record_dimension(("dia", round(diameter, 2), OVERALL), view.name)


def draw_overall(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """Width and height on the front view, depth on the top view: measured on the projected geometry. A body of
    revolution gets its diameter ONCE, as ``Ø`` on the first view that shows it across (front, else top); the other
    view does not repeat it."""
    out: list[str] = []
    skipped: list[str] = []
    sizes: dict[str, float] = {}
    front, top = ctx.views.get("front"), ctx.views.get("top")
    # a sheet of ONE standard view that is not the front one (a plate seen from above): that view gives the width and the
    # height, and the thickness is a note
    primary = front or top or ctx.views.get("right")
    only = front is None
    if only:
        top = None
    offset = spec.get("offset_mm")
    round_part = ctx.geometry.round_extent()
    axis = round_part[0] if round_part else None
    given = False
    if primary is not None:
        name = primary.name
        ctx.used_view(name)
        b = primary.box
        width, height = (b[2] - b[0]) / primary.scale, (b[3] - b[1]) / primary.scale
        w_round, h_round = _is_diameter(primary, axis)
        by_thread_w = w_round and (  # the thread callout (or the dimension of the step) says it: Ø once
            thread_states_diameter(ctx, width) or (not h_round and diameter_dimensioned(ctx, primary, width))
        )
        by_thread_h = h_round and thread_states_diameter(ctx, height)
        by_step_h = h_round and not w_round and diameter_dimensioned(ctx, primary, height)  # the step's own Ø says it: once
        below = ctx.third_angle and not only  # third angle: the top view hangs above the front one, the width goes below
        asked_side = spec.get("side")
        if asked_side in ("above", "below"):
            below = asked_side == "below"
        elif not below:
            below = bool(_extent_gap(ctx, b, "above"))  # its extension lines would cross the view over nothing
        given = w_round or h_round
        toward = _threaded_end(ctx, primary, width if w_round else height) if (w_round or h_round) else None
        if toward is not None and asked_side is None and w_round and not by_thread_w:
            below = toward[1] > 0  # the Ø goes at the plain end, away from the thread callout (axis along the page y)
        if by_thread_w:
            skipped.append(f"width Ø{ctx.num(width)} (the thread callout carries it)")
        else:
            dist = ctx.stack_distance(name, "below" if below else "above", offset)
            shown = f"Ø{ctx.num(width)}" if w_round else ctx.num(width)
            edge, line = (b[1], b[1] - dist) if below else (b[3], b[3] + dist)
            entities, taken = linear_dimension((b[0], edge), (b[2], edge), "h", line, shown)
            ctx.add(entities)
            ctx.reserve_dimension(entities, taken)
            _record(ctx, "width", name, "h", line, (b[0] + b[2]) / 2, shown)
            _record_overall(ctx, primary, True, width if w_round else None)
            _record_extent(ctx, name, b, line, "below" if below else "above")
            out.append(f"width {shown}" + (" (below the view: above it its extension lines cross the part)" if below and not ctx.third_angle and asked_side is None else ""))
            sizes["width"] = width
        if not (h_round and w_round):  # a circle's height is its width: say it once
            if by_step_h:
                skipped.append(f"height Ø{ctx.num(height)} (the diameter dimension of its step carries it)")
            elif by_thread_h:
                skipped.append(f"height Ø{ctx.num(height)} (the thread callout carries it)")
            else:
                left = h_round and toward is not None and toward[0] > 0  # thread at the right end: the Ø goes at the plain left one
                dist = ctx.stack_distance(name, "left" if left else "right", offset)
                shown_h = f"Ø{ctx.num(height)}" if h_round else ctx.num(height)
                x_line = b[0] - dist if left else b[2] + dist
                entities, taken = linear_dimension((b[0] if left else b[2], b[1]), (b[0] if left else b[2], b[3]), "v", x_line, shown_h)
                ctx.add(entities)
                ctx.reserve_dimension(entities, taken)
                _record(ctx, "height", name, "v", x_line, (b[1] + b[3]) / 2, shown_h)
                _record_overall(ctx, primary, False, height if h_round else None)
                out.append(f"height {shown_h}")
                sizes["height"] = height
    if top is not None:
        b = top.box
        depth = (b[3] - b[1]) / top.scale
        d_round = _is_diameter(top, axis)[1]
        if not (d_round and given):
            ctx.used_view("top")  # only a dimension drawn on the view gives it a job: a view the overall size skips has none
            dist = ctx.stack_distance("top", "right", offset)
            shown_d = f"Ø{ctx.num(depth)}" if d_round else ctx.num(depth)
            entities, taken = linear_dimension((b[2], b[1]), (b[2], b[3]), "v", b[2] + dist, shown_d)
            ctx.add(entities)
            ctx.reserve_dimension(entities, taken)
            _record(ctx, "depth", "top", "v", b[2] + dist, (b[1] + b[3]) / 2, shown_d)
            _record_overall(ctx, top, False, depth if d_round else None)
            out.append(f"depth {shown_d}")
            sizes["depth"] = depth
    right = ctx.views.get("right")
    if top is None and right is not None and not only:  # no top view: the depth is the width of the right view
        ctx.used_view("right")
        b = right.box
        depth = (b[2] - b[0]) / right.scale
        dist = ctx.stack_distance("right", "above", offset)
        entities, taken = linear_dimension((b[0], b[3]), (b[2], b[3]), "h", b[3] + dist, ctx.num(depth))
        ctx.add(entities)
        ctx.reserve_dimension(entities, taken)
        _record(ctx, "depth", "right", "h", b[3] + dist, (b[0] + b[2]) / 2, ctx.num(depth))
        _record_overall(ctx, right, True)
        out.append(f"depth {ctx.num(depth)}")
        sizes["depth"] = depth
    if not out and not skipped:
        msg = "the sheet has no front or top view to take the overall size from"
        raise ItemError(msg)
    general = sheet_tol.general_notes(ctx, "linear", sizes)
    left_out = f"; not drawn: {', '.join(skipped)}" if skipped else ""
    return "ok", ", ".join(out) + " mm" + general + left_out


# --- references -------------------------------------------------------------------------------------------------------


@dataclass
class Ref:
    """A place on the part a dimension reaches: ``point`` is where it is (a point, a hole centre, the middle of a
    face or of a straight edge), ``kind`` what it is. ``normal`` belongs to planes and circles, ``direction`` to
    lines and axes, ``radius`` to holes, circles and cylinders."""

    kind: str
    point: Vec3
    label: str
    normal: Vec3 | None = None
    direction: Vec3 | None = None
    radius: float | None = None


def resolve_ref(ctx: Context, ref: dict[str, Any]) -> Ref:
    if "at" in ref:
        p: Vec3 = tuple(ref["at"])  # type: ignore[assignment]
        for hole in ctx.holes:
            if hole.on_axis(p):
                d = unit(hole.direction)
                foot = add(hole.centre, scale(d, dot(sub(p, hole.centre), d)))
                return Ref("hole", foot, f"the hole at {_pt(ctx, p)}", direction=d, radius=hole.diameter / 2)
        snapped = ctx.geometry.on_part(p)
        if snapped is None:
            msg = (
                f"the point {_pt(ctx, p)} is not on the part: a dimension measures the model, so its references "
                "must be "
                "points of it (a vertex, a point on an edge or a face) or the axis of a hole; use face=/edge= ids to "
                "avoid typing coordinates"
            )
            raise ItemError(msg)
        return Ref("point", snapped, f"the point {_pt(ctx, p)}")
    if "face" in ref:
        face = ctx.geometry.face(ref["face"])
        if face.kind == "plane" and face.normal is not None:
            return Ref("plane", face.point, ref["face"], normal=unit(face.normal))
        if face.kind == "cylinder" and face.axis_point is not None and face.axis_dir is not None:
            return Ref("axis", face.axis_point, ref["face"], direction=unit(face.axis_dir), radius=face.radius)
        msg = f"{ref['face']} is a {face.kind} face: a dimension reference needs a plane or a cylinder"
        raise ItemError(msg)
    edge = ctx.geometry.edge(ref["edge"])
    if edge.kind == "line" and edge.start is not None and edge.end is not None:
        mid = tuple((a + b) / 2 for a, b in zip(edge.start, edge.end, strict=True))
        return Ref("line", mid, ref["edge"], direction=unit(sub(edge.end, edge.start)))  # type: ignore[arg-type]
    if edge.kind == "circle" and edge.centre is not None:
        return Ref("circle", edge.centre, ref["edge"], normal=edge.axis_dir, radius=edge.radius)
    msg = f"{ref['edge']} is a {edge.kind} edge: a dimension reference needs a straight or a circular edge"
    raise ItemError(msg)


def _pt(ctx: Context, p: Vec3) -> str:
    return "[" + ", ".join(ctx.num(c) for c in p) + "]"


def coordinate(ref: Ref, axis: Vec3, name: str) -> float:
    """Where ``ref`` is along ``axis``; an error when the reference has no single place along it."""
    if ref.kind == "plane" and ref.normal is not None and not parallel(ref.normal, axis):
        msg = f"{ref.label} is a face that is not perpendicular to the {name} axis, so it has no single {name} position"
        raise ItemError(msg)
    if ref.kind == "line" and ref.direction is not None and not perpendicular(ref.direction, axis):
        msg = (
            f"{ref.label} is an edge that is not perpendicular to the {name} axis, so it has no single {name} position"
        )
        raise ItemError(msg)
    if ref.kind in ("hole", "axis") and ref.direction is not None and parallel(ref.direction, axis):
        msg = f"{ref.label} is an axis along {name}: it has no {name} position; use a face or an edge across it"
        raise ItemError(msg)
    return dot(ref.point, axis)


def _axis_name(v: Vec3) -> str | None:
    return next((name for name, a in AXES.items() if parallel(a, v)), None)


def _along(ctx: Context, a: Ref, b: Ref, wanted: str | None) -> str:
    if wanted is not None:
        return wanted
    planes = [r for r in (a, b) if r.kind == "plane" and r.normal is not None]
    names = {_axis_name(r.normal) for r in planes if r.normal is not None}
    if names:
        if None in names or len(names) > 1:
            msg = "the faces are not perpendicular to one x, y or z axis; say along=x|y|z or use other references"
            raise ItemError(msg)
        return str(names.pop())
    delta = sub(b.point, a.point)
    big = [name for name, axis in AXES.items() if abs(dot(delta, axis)) > POSITION_EPS_MM]
    if len(big) != 1:
        apart = ", ".join(big) or "no axis"
        msg = (
            f"the two references are apart along {apart}: say along=x|y|z to choose the direction the dimension "
            "measures"
        )
        raise ItemError(msg)
    return big[0]


def _view_for_axis(ctx: Context, axis_name: str, wanted: str | None, refs: tuple[Ref, ...] = ()) -> ViewInfo:
    axis = AXES[axis_name]
    if wanted:
        ctx.view(wanted)  # a view the sheet no longer has is said so, not blamed on the axis
    names = [wanted] if wanted else list(VIEW_PREFERENCE[axis_name])
    rims = [r.direction if r.direction is not None else r.normal for r in refs if r.radius is not None]
    if wanted is None and rims and rims[0] is not None:  # holes: the view that shows them as circles comes first
        names.sort(key=lambda n: 0 if n in ctx.views and ctx.views[n].looks_along(rims[0]) else 1)
    for name in names:
        view = ctx.find_view(name)
        if view is not None and view.shows_axis_along(axis):
            return view
    msg = (
        f"no view shows the {axis_name} axis"
        if wanted is None
        else f"the {wanted} view does not show the {axis_name} axis (it looks along it)"
    )
    raise ItemError(msg)


def _edge_start(ref: Ref, view: ViewInfo, p: Point, towards: float, orient: str) -> Point:
    """Where the extension line of ``ref`` starts: at the rim of a hole that shows as a circle, else at the point."""
    axis = ref.direction if ref.direction is not None else ref.normal
    if ref.radius is None or axis is None or not view.looks_along(axis):
        return p
    r = ref.radius * view.scale
    sign = 1.0 if towards >= (p[1] if orient == "h" else p[0]) else -1.0
    return (p[0], p[1] + sign * r) if orient == "h" else (p[0] + sign * r, p[1])


# --- dim --------------------------------------------------------------------------------------------------------------


def check(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, CHECKS, required=("kind",))
    kind = out["kind"]
    if kind == "distance":
        if "from" not in out or "to" not in out:
            msg = 'distance needs from and to, each {"at": [x,y,z]}, {"face": "face:N"} or {"edge": "edge:N"}'
            raise ItemError(msg)
    elif kind in ("radius", "chamfer", "diameter"):
        if "face" not in out or len(out["face"]) != 1:
            msg = f"{kind} needs face (one face id)"
            raise ItemError(msg)
    elif "faces" not in out or len(out["faces"]) != 2:
        msg = "angle needs faces: two planar face ids"
        raise ItemError(msg)
    if out.get("exact") and kind != "distance":
        msg = "exact (theoretically exact, boxed) is for a distance dimension: the location of a toleranced feature"
        raise ItemError(msg)
    sheet_tol.check_combination(out)
    if ("fit" in out or "tol" in out) and kind == "chamfer":
        msg = "a chamfer takes no fit or tolerance here: its general tolerance is the chamfer height of ISO 2768"
        raise ItemError(msg)
    if "fit" in out and kind in ("radius", "angle"):
        msg = f"a fit (ISO 286) is for a size, not for a {kind}: use tol"
        raise ItemError(msg)
    return out


def _toleranced(
    ctx: Context, spec: dict[str, Any], value: float, general: str, prefix: str = "", unit: str = ""
) -> tuple[sheet_tol.Rendered, str]:
    """(the text of a dimension, what to add to its result): the value with its tolerance, or -- when it has none --
    the general tolerance the sheet's ISO 2768 class gives a dimension of this ``general`` kind (``linear``, ``radius``
    or ``angle``)."""
    res = sheet_tol.resolve(spec, value)
    if res is None:
        return sheet_tol.Rendered(f"{prefix}{ctx.num(value)}{unit}"), sheet_tol.general_note(ctx, general, value)
    rendered = sheet_tol.render(ctx, value, res, prefix, unit, stackable=general == "linear" and not prefix)
    stacked = f" (deviations stacked: {rendered.stack[0]} over {rendered.stack[1]})" if rendered.stack else ""
    return rendered, f"; toleranced{stacked}"


def distance_span(ctx: Context, spec: dict[str, Any]) -> float | None:
    """How long a ``dim`` of kind ``distance`` is (mm), or ``None`` when it cannot be measured (``_distance`` then says why).
    The drawing order stacks the lines outward, so the shorter ones are drawn first (``sheet_items.draw_items``)."""
    try:
        a, b = resolve_ref(ctx, spec["from"]), resolve_ref(ctx, spec["to"])
        name = _along(ctx, a, b, spec.get("along"))
        axis = AXES[name]
        return abs(coordinate(b, axis, name) - coordinate(a, axis, name))
    except (ItemError, KeyError):
        return None


def _distance(ctx: Context, spec: dict[str, Any]) -> str:
    a, b = resolve_ref(ctx, spec["from"]), resolve_ref(ctx, spec["to"])
    name = _along(ctx, a, b, spec.get("along"))
    axis = AXES[name]
    ca, cb = coordinate(a, axis, name), coordinate(b, axis, name)
    value = abs(cb - ca)
    if value < POSITION_EPS_MM:
        msg = f"the two references are at the same {name} position (distance 0)"
        raise ItemError(msg)
    view = _view_for_axis(ctx, name, spec.get("view"), (a, b))
    ax, ay = view.axis(axis)
    orient = "h" if abs(ax) >= abs(ay) else "v"
    side = spec.get("side") or (
        DEFAULT_SIDE.get(("h", view.name), "above") if orient == "h" else DEFAULT_SIDE_V.get(view.name, "right")
    )
    if (orient == "h") != (side in ("above", "below")):
        msg = (
            f"this dimension is {'horizontal' if orient == 'h' else 'vertical'} in the {view.name} view: side must be "
            + ("above or below" if orient == "h" else "left or right")
        )
        raise ItemError(msg)
    box = view.box
    dist = ctx.stack_distance(view.name, side, spec.get("offset_mm"))
    line = {"above": box[3] + dist, "below": box[1] - dist, "right": box[2] + dist, "left": box[0] - dist}[side]
    centres = (view.project(a.point), view.project(b.point))
    if (
        view.derived
    ):  # in a section a face is a line of the cut: the extension line starts at its end, not at its middle
        centres = (_trace_end(ctx, view, a, centres[0], side), _trace_end(ctx, view, b, centres[1], side))
    pa, pb = _edge_start(a, view, centres[0], line, orient), _edge_start(b, view, centres[1], line, orient)
    rendered, extra = _toleranced(ctx, spec, value, "linear")
    entities, taken = linear_dimension(pa, pb, orient, line, rendered.text, arrows=spec.get("arrows", "auto"))
    if spec.get("exact"):
        exact_box(entities)
    if rendered.stack is not None:
        sheet_tol.stack_entities(entities, rendered.stack, horizontal=orient == "h")
        grow = max(text_width(s, 2.5) for s in rendered.stack) + 2.0
        taken = (
            taken[0],
            taken[1],
            taken[2] + (grow if orient == "h" else 0.0),
            taken[3] + (grow if orient == "v" else 0.0),
        )
    ctx.add(entities)
    ctx.reserve_dimension(entities, taken)
    res = sheet_tol.resolve(spec, value)
    label = (
        ((centres[0][0] + centres[1][0]) / 2, line) if orient == "h" else (line, (centres[0][1] + centres[1][1]) / 2)
    )
    ctx.used_view(view.name)
    ctx.record_dimension(("len", name, round(min(ca, cb), 2), round(max(ca, cb), 2)), view.name)
    if not view.derived:  # a section is made of sheet entities: TechDraw has no view of it to measure in
        ctx.native(
            NativeDim(
                item=str(ctx.extras["current"]),
                name="d1",
                kind="distance",
                view=view.name,
                refs=(Ref2("point", centres[0]), Ref2("point", centres[1])),
                label=label,
                expected=value,
                # a reference given as a point is placed on the page to a hundredth of a millimetre: TechDraw would measure 4,01
                # between two places 4 apart, so the text is the model's value, not TechDraw's measure
                text=None if (res is None and "at" not in (spec["from"] | spec["to"])) or rendered.stack is not None else rendered.text,
                orient=orient,
                tol=(res.upper, res.lower) if res is not None and rendered.stack is not None else None,
                rect=taken,
                arrows=str(spec.get("arrows", "auto")),
                exact=bool(spec.get("exact")),
            )
        )
    return f"{ctx.num(value)} mm along {name} in the {view.name} view ({a.label} to {b.label}){extra}"


def _trace_end(ctx: Context, view: ViewInfo, ref: "Ref", centre: Point, side: str) -> Point:
    """Where the extension line of a planar face seen edge-on in a section starts: the end of the face's trace on the side
    the dimension goes to (``centre``, the middle of the face, when the geometry cannot say)."""
    if ref.kind != "plane" or not ref.label.startswith("face:"):
        return centre
    try:
        loops = ctx.geometry.face_outline(ref.label, view.centre, view.xdir, view.direction)
    except ItemError:
        return centre
    points = [(view.x + p[0] * view.scale, view.y + p[1] * view.scale) for loop in loops for p in loop]
    if not points:
        return centre
    pick = {"right": lambda p: p[0], "left": lambda p: -p[0], "above": lambda p: p[1], "below": lambda p: -p[1]}[side]
    best = max(points, key=pick)
    # keep the coordinate along the measured direction (the face's own position), take the other from the trace end
    return (best[0], centre[1]) if side in ("right", "left") else (centre[0], best[1])


def _radius(ctx: Context, spec: dict[str, Any]) -> str:
    face = ctx.geometry.face(spec["face"][0])
    if face.kind != "cylinder" or face.axis_dir is None or face.axis_point is None or face.surface_point is None:
        msg = f"{spec['face'][0]} is a {face.kind} face: radius needs a cylindrical face (a fillet, a boss, a bore)"
        raise ItemError(msg)
    view = _view_looking_along_axis(ctx, face.axis_dir, spec.get("view"), spec["face"][0])
    centre, tip = view.project(face.axis_point), view.project(face.surface_point)
    dx, dy = tip[0] - centre[0], tip[1] - centre[1]
    diagonal = (1.0 if dx >= 0 else -1.0, 1.0 if dy >= 0 else -1.0)
    if spec.get("side") in ("right", "left", "above", "below"):
        diagonal = outward_diagonal(view, tip, spec["side"])
    rendered, extra = _toleranced(ctx, spec, face.radius or 0.0, "radius", prefix="R")
    ctx.add(place_leader(ctx, view, tip, diagonal, [rendered.text], spec.get("offset_mm"), asked(spec)))
    ctx.used_view(view.name)
    ctx.native(
        NativeDim(
            item=str(ctx.extras["current"]),
            name="r1",
            kind="radius",
            view=view.name,
            refs=(Ref2("circle", (centre[0], centre[1], (face.radius or 0.0) * view.scale)),),
            label=_label_of(ctx),
            expected=face.radius,
            text=rendered.text if sheet_tol.resolve(spec, face.radius or 0.0) is not None else None,
            leader=True,
            rect=_anchor_rect(ctx),
        )
    )
    return f"{rendered.text} in the {view.name} view{extra}"


def _anchor_rect(ctx: Context) -> Any:
    """The rectangle of the leader text the item being drawn has just placed."""
    return ctx.extras["anchors"][ctx.extras["current"]][-1]["rect"]


def _label_of(ctx: Context) -> Point:
    x0, y0, x1, y1 = _anchor_rect(ctx)
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def _diameter(ctx: Context, spec: dict[str, Any]) -> str:
    """The diameter of a cylinder that is NOT the wall of a hole (that is ``hole_dim``'s): a leader to its silhouette in
    a view across its axis, or to its circle in a view along it."""
    face_id = spec["face"][0]
    face = ctx.geometry.face(face_id)
    if face.kind != "cylinder" or face.radius is None:
        msg = f"{face_id} is a {face.kind} face: diameter needs a cylindrical face (a shaft, a boss)"
        raise ItemError(msg)
    if any(face_id in hole.faces for hole in ctx.holes):
        msg = f"{face_id} is the wall of a hole: dimension it with hole_dim (it takes fit and tol too)"
        raise ItemError(msg)
    att = attach_face(ctx, face_id, spec.get("view"), spec.get("side"))
    threads = sheet_thread.threads_on_face(ctx, face_id, face)
    if threads and spec.get("side") is None and att.circle is None and att.normal[1] < -0.9:
        # the callout of a thread on a horizontal shaft leaves upwards: the lengths of the part stack up below it, and a leader
        # that crosses them to reach the free room under the stack is a leader nobody can follow
        att = attach_face(ctx, face_id, spec.get("view"), "above")
    diagonal = (1.0 if att.normal[0] >= 0 else -1.0, 1.0 if att.normal[1] >= 0 else -1.0)
    if threads:  # a shaft end an ``external_thread`` threaded: its callout is the thread, not the plain diameter
        if "fit" in spec or "tol" in spec:
            msg = f"{face_id} is threaded ({threads[0].size}): a threaded diameter takes no fit or tolerance, the thread class is part of its size"
            raise ItemError(msg)
        lines = sheet_thread.external_callout(ctx, threads)
        rendered, extra = sheet_tol.Rendered(" / ".join(lines)), f"; thread {threads[0].size}"
    else:
        lines = None
        rendered, extra = _toleranced(ctx, spec, 2 * face.radius, "linear", prefix="Ø")
    ctx.record_dimension(("dia", round(2 * face.radius, 2), face_id), att.view.name)
    round_part = ctx.geometry.round_extent()
    if (
        round_part is not None and abs(round_part[1] - 2 * face.radius) < 0.01
    ):  # the outer diameter ``overall`` gives too
        ctx.record_dimension(("dia", round(2 * face.radius, 2), OVERALL), att.view.name)
    if att.view.derived and att.circle is None and att.kind == "surface":
        return _section_diameter(ctx, spec, att, face.radius, rendered.text, extra)
    if not threads and turned_across(ctx, att) and not any(spec.get(k) is not None for k in ("side", "offset_mm", "elbow_mm")):
        return _turned_diameter(ctx, att, face.radius, rendered.text, extra)
    # a thread seen across a HORIZONTAL axis: the callout leaves straight up (or down) from the middle of the thread, so its
    # dimension line stands on the threaded length, clear of the end and of the chamfer's callout
    horizontal = abs(att.normal[1]) > 0.9
    straight = att.normal if threads and att.circle is None and horizontal and spec.get("side") is None else None
    ctx.add(
        place_leader(
            ctx, att.view, att.tip, diagonal, lines or [rendered.text], spec.get("offset_mm"), asked(spec), straight
        )
    )
    ctx.used_view(att.view.name)
    radius = face.radius * att.view.scale
    if att.circle is not None:  # seen along its axis: a circle of the view
        refs = (Ref2("circle", (att.circle[0][0], att.circle[0][1], radius)),)
        kind = "diameter"
    else:  # seen across its axis: the two silhouette lines, measured across (an aligned distance)
        far = (att.tip[0] - 2 * radius * att.normal[0], att.tip[1] - 2 * radius * att.normal[1])
        refs = (Ref2("point", att.tip), Ref2("point", far))
        kind = "distance"
    if not att.view.derived:
        ctx.native(
            NativeDim(
                item=str(ctx.extras["current"]),
                name="d1",
                kind=kind,
                view=att.view.name,
                refs=refs,
                label=_label_of(ctx),
                expected=2 * face.radius,
                text=rendered.text,
                orient="a",
                leader=True,
                rect=_anchor_rect(ctx),
            )
        )
    return f"{rendered.text} in the {att.view.name} view{extra}"


CUT_AFTER_MM = 19.0  # the cutting line of a section keeps this much of the strip beside it, on both sides, clear of a diameter text (its arrows and letters)


def turned_across(ctx: Context, att: Any) -> bool:
    """Whether a diameter is one of a turned part (a body of revolution) seen across its axis, the axis along the page's x or y:
    its two silhouette lines are on the page and the diameter is dimensioned between them, on the view."""
    return (
        att.circle is None
        and not att.view.derived
        and att.kind == "surface"
        and att.band is not None
        and ctx.geometry.round_extent() is not None
        and (abs(att.normal[0]) >= 0.99 or abs(att.normal[1]) >= 0.99)
    )


def _cut_bands(ctx: Context, view: ViewInfo, index: int) -> list[tuple[float, float]]:
    """The strips of the page, along the axis of a turned part, where the cutting line of a section of the sheet (with its arrows
    and letters) stands: a diameter text keeps out of them (``index``: the page coordinate that runs along the axis)."""
    out: list[tuple[float, float]] = []
    for item in ctx.extras.get("items", []):
        if item.get("type") != "section" or item.get("at") is None or item.get("view") not in (None, view.name):
            continue
        at = view.project(tuple(item["at"]))[index]
        out.append((at - CUT_AFTER_MM, at + CUT_AFTER_MM))  # its arrows and letters stand on the side the section is looked at from: either
    return out


def _turned_diameter(ctx: Context, att: Any, radius_mm: float, text: str, extra: str) -> str:
    """The diameter of a step of a turned part seen across its axis: a LINEAR dimension with the Ø in front of the value, between
    its two silhouette lines, on the step itself (ISO 129-1: a shaft is dimensioned on its long view, not by a leader to one
    side of it). Its place along the axis is chosen on the step, where its text (which stands beyond the upper or right arrow when
    it is longer than the diameter) touches nothing already on the sheet and clear of the cutting line of a section, and the
    texts of the steps spread along the shaft so they never meet."""
    view, n = att.view, att.normal
    r = radius_mm * view.scale
    tip = att.tip
    far = (tip[0] - 2 * r * n[0], tip[1] - 2 * r * n[1])
    (bx0, by0), (bx1, by1) = att.band
    vertical = abs(n[1]) >= 0.99  # the silhouette lines are horizontal: the dimension line is vertical
    index = 0 if vertical else 1
    lo, hi = sorted(((bx0, bx1) if vertical else (by0, by1)))
    margin_lo, margin_hi = (4.8, 1.5) if vertical else (1.0, 4.8)  # the rotated text takes 4.3 mm to the left of the line, the other 4.3 above it
    first, last = lo + margin_lo, hi - margin_hi
    mid = (lo + hi) / 2
    cuts = _cut_bands(ctx, view, index)
    steps = [first + k * 1.5 for k in range(int((last - first) / 1.5) + 1)] if last > first else [mid]

    def made(at: float) -> tuple[list[Entity], Any, Point, Point]:
        if vertical:
            a, b = (at, far[1]), (at, tip[1])
        else:
            a, b = (far[0], at), (tip[0], at)
        entities, taken = linear_dimension(a, b, "v" if vertical else "h", at, text)
        return entities, taken, a, b

    drawn = [tuple(s) for s in ctx.extras.get("dimension_lines", [])]  # the lines (and extension lines) of the dimensions already on the sheet

    def worse(at: float) -> tuple[int, int, int, float]:
        _, taken, _, _ = made(at)
        low, high = (taken[0], taken[2]) if vertical else (taken[1], taken[3])
        in_cut = any(low < c1 and c0 < high for c0, c1 in cuts)
        crossed = sum(1 for s in drawn if segment_hits_rect(s, taken))
        return (int(in_cut), int(not ctx.free(taken) or not ctx.inside(taken)), crossed, abs(at - mid))

    chosen = min(steps, key=worse)
    entities, taken, a, b = made(chosen)
    ctx.add(entities)
    ctx.reserve_dimension(entities, taken)
    ctx.used_view(view.name)
    # TechDraw's own dimension (a DrawViewDimension between the two silhouette points); its text is put beyond the upper (right)
    # arrow, where the sheet's ink would put it, clear of the outline
    reach = text_width(text, 3.5)
    label = (chosen, taken[3] - reach / 2) if vertical else (taken[2] - reach / 2, chosen)
    ctx.native(
        NativeDim(
            item=str(ctx.extras["current"]),
            name="d1",
            kind="distance",
            view=view.name,
            refs=(Ref2("point", a), Ref2("point", b)),
            label=label,
            expected=2 * radius_mm,
            text=text,
            orient="v" if vertical else "h",
            rect=taken,
        )
    )
    return f"{text} in the {view.name} view, between its two silhouette lines on the step{extra}"


def _section_diameter(ctx: Context, spec: dict[str, Any], att: Any, radius_mm: float, text: str, extra: str) -> str:
    """The diameter of a cylinder seen across its axis in a section: a LINEAR dimension with the Ø in front of the value,
    between its two silhouette lines, outside the view, the way a draughtsman dimensions a diameter in a section. A leader to
    one silhouette line says nothing a shop can measure (it points at one side of a thing that has two). The extension lines
    start at the end of the silhouette lines (the corner where that diameter stops) on the side the dimension goes to."""
    view, n = att.view, att.normal
    if abs(n[0]) >= 0.99:  # the silhouette lines are vertical on the page: the dimension line is horizontal
        orient, sides = "h", ("above", "below")
    elif abs(n[1]) >= 0.99:
        orient, sides = "v", ("left", "right")
    else:
        msg = f"the cylinder is oblique in the {view.name} view: a diameter dimension across it needs its axis horizontal or vertical there"
        raise ItemError(msg)
    r = radius_mm * view.scale
    tip = att.tip
    far = (tip[0] - 2 * r * n[0], tip[1] - 2 * r * n[1])
    ends = list(att.band) if att.band is not None else [tip, tip]
    box = view.box
    side = spec.get("side")
    if side is not None and side not in sides:
        msg = f"this diameter is {'horizontal' if orient == 'h' else 'vertical'} in the {view.name} view: side must be {' or '.join(sides)}"
        raise ItemError(msg)
    index = 1 if orient == "h" else 0  # the page coordinate along the axis of the cylinder
    edges = {"above": box[3], "below": box[1], "right": box[2], "left": box[0]}
    if side is None and orient == "h":  # above: the label of the view hangs below it
        side = "above"
    elif side is None:  # the end of the silhouette line nearest an edge of the view: the dimension goes out that way
        side = min(sides, key=lambda s: min(abs(e[index] - edges[s]) for e in ends))
    high = side in ("above", "right")
    end = max(ends, key=lambda e: e[index]) if high else min(ends, key=lambda e: e[index])
    dist = ctx.stack_distance(view.name, side, spec.get("offset_mm"))
    line = edges[side] + dist if high else edges[side] - dist
    if orient == "h":
        a, b = (far[0], end[1]), (tip[0], end[1])
    else:
        a, b = (end[0], far[1]), (end[0], tip[1])
    entities, taken = linear_dimension(a, b, orient, line, text, arrows=spec.get("arrows", "auto"))
    ctx.add(entities)
    ctx.reserve_dimension(entities, taken)
    return f"{text} in the {view.name} view, between its two silhouette lines{extra}"


def _view_looking_along_axis(ctx: Context, axis: Vec3, wanted: str | None, what: str) -> ViewInfo:
    names = [wanted] if wanted else ["top", "front", "right"]
    for name in names:
        view = ctx.views.get(name)
        if view is not None and view.looks_along(axis):
            return view
    msg = f"no {'selected ' if wanted else ''}view looks along the axis of {what}: it is not parallel to x, y or z"
    raise ItemError(msg)


def _angle(ctx: Context, spec: dict[str, Any]) -> str:
    fa, fb = (ctx.geometry.face(i) for i in spec["faces"])
    if fa.kind != "plane" or fb.kind != "plane" or fa.normal is None or fb.normal is None:
        msg = "angle needs two planar faces"
        raise ItemError(msg)
    na, nb = unit(fa.normal), unit(fb.normal)
    if norm(cross(na, nb)) < 1e-6:
        msg = "the two faces are parallel: they make no angle"
        raise ItemError(msg)
    view = None
    for name in [spec["view"]] if "view" in spec else ["front", "top", "right"]:
        candidate = ctx.find_view(name)
        if candidate is not None and candidate.shows_axis_along(na) and candidate.shows_axis_along(nb):
            view = candidate
            break
    if view is None:
        msg = "no view shows both faces edge-on (their normals must lie in the plane of a front, top or right view)"
        raise ItemError(msg)
    traces = []
    for face, normal in ((fa, na), (fb, nb)):
        nx, ny = view.axis(normal)
        length = math.hypot(nx, ny)
        traces.append((view.project(face.point), (-ny / length, nx / length)))
    (p1, t1), (p2, t2) = traces
    denom = t1[0] * t2[1] - t1[1] * t2[0]
    s = ((p2[0] - p1[0]) * t2[1] - (p2[1] - p1[1]) * t2[0]) / denom
    apex = (p1[0] + s * t1[0], p1[1] + s * t1[1])
    rays = []
    for p, t in ((p1, t1), (p2, t2)):
        away = (p[0] - apex[0]) * t[0] + (p[1] - apex[1]) * t[1]
        rays.append(t if away >= 0 else (-t[0], -t[1]))
    (r1, r2) = rays
    theta = math.degrees(math.acos(max(-1.0, min(1.0, r1[0] * r2[0] + r1[1] * r2[1]))))
    a0, a1 = math.degrees(math.atan2(r1[1], r1[0])), math.degrees(math.atan2(r2[1], r2[0]))
    if (a1 - a0) % 360.0 > 180.0:
        a0, a1 = a1, a0
    rendered, extra = _toleranced(ctx, spec, round(theta, 1), "angle", unit="°")
    text = rendered.text
    entities, taken = angle_dimension(apex, a0, a1, spec.get("offset_mm") or ANGLE_RADIUS_MM, text)
    ctx.add(entities)
    ctx.reserve(taken)
    ctx.used_view(view.name)
    reach = 20.0  # any two points of the trace say which line it is
    if not view.derived:
        ctx.native(
            NativeDim(
                item=str(ctx.extras["current"]),
                name="a1",
                kind="angle",
                view=view.name,
                refs=tuple(
                    Ref2("line", (p[0], p[1], p[0] + t[0] * reach, p[1] + t[1] * reach))
                    for p, t in ((p1, t1), (p2, t2))
                ),
                label=((taken[0] + taken[2]) / 2, (taken[1] + taken[3]) / 2),
                expected=round(theta, 1),
                text=text if sheet_tol.resolve(spec, round(theta, 1)) is not None else None,
                rect=taken,
            )
        )
    return f"{text} between {spec['faces'][0]} and {spec['faces'][1]} in the {view.name} view{extra}"


def _cone_chamfer(ctx: Context, spec: dict[str, Any], cone: Any) -> str:
    """The callout (``1 × 45°``) of the chamfer on a round edge, on its profile line in a view across the axis: its leader ends in
    the middle of that line and comes from outside the shaft, perpendicular to it (ISO 129)."""
    wanted = spec.get("view")
    views = [ctx.view(wanted)] if wanted else [ctx.views[n] for n in ("front", "right", "top") if n in ctx.views]
    view = next((v for v in views if v.shows_axis_along(cone.axis)), None)
    if view is None:
        msg = "no view of the sheet shows the axis of this chamfer's shaft across the view: say view= (front, top or right)"
        raise ItemError(msg)
    # the profile of the shaft is seen on one side of its axis: the side the page's x points to when the axis is vertical on
    # the page, the upper side when it is horizontal (a turned part is drawn as it is machined)
    axis_page = (view.xdir[0] * cone.axis[0] + view.xdir[1] * cone.axis[1] + view.xdir[2] * cone.axis[2],
                 view.ydir[0] * cone.axis[0] + view.ydir[1] * cone.axis[1] + view.ydir[2] * cone.axis[2])
    side = (-axis_page[1], axis_page[0])
    if side[0] < -1e-9 or (abs(side[0]) <= 1e-9 and side[1] < 0):
        side = (-side[0], -side[1])
    reach = math.hypot(*side) or 1.0
    side = (side[0] / reach, side[1] / reach)
    across = tuple(side[0] * x + side[1] * y for x, y in zip(view.xdir, view.ydir, strict=True))  # model direction
    ends = [
        view.project(tuple(c + a * radius for c, a in zip(centre, across, strict=True)))  # type: ignore[arg-type]
        for centre, radius in (cone.small, cone.big)
    ]
    tip = ((ends[0][0] + ends[1][0]) / 2, (ends[0][1] + ends[1][1]) / 2)
    run = math.dist(ends[0], ends[1])
    if run < 0.3:
        msg = "the chamfer is too small to dimension at this scale: use a note"
        raise ItemError(msg)
    line = ((ends[1][0] - ends[0][0]) / run, (ends[1][1] - ends[0][1]) / run)
    normal = (line[1], -line[0])
    if normal[0] * side[0] + normal[1] * side[1] < 0:
        normal = (-normal[0], -normal[1])  # away from the axis: toward the side the profile is on
    diagonal = outward_diagonal(view, tip, spec.get("side"))
    text = f"{ctx.num(round(cone.leg_a, 2))} × {ctx.num(round(cone.angle_deg, 1))}°"
    along = normal if spec.get("side") is None else None
    ctx.add(place_leader(ctx, view, tip, diagonal, [text], spec.get("offset_mm"), asked(spec), along))
    ctx.extras.setdefault("chamfer_marks", {})[str(ctx.extras.get("current"))] = (view.name, tip, normal)
    ctx.used_view(view.name)
    general = sheet_tol.general_note(ctx, "radius", cone.leg_a)
    return f"chamfer {text} in the {view.name} view (legs {ctx.num(cone.leg_a)} and {ctx.num(cone.leg_b)} mm){general}"


def _chamfer(ctx: Context, spec: dict[str, Any]) -> str:
    cone = ctx.geometry.cone_chamfer(spec["face"][0])
    if cone is not None:  # a chamfer on a round edge (a shaft end): its profile line, not a straight edge
        return _cone_chamfer(ctx, spec, cone)
    info = ctx.geometry.chamfer(spec["face"][0])
    view = _view_looking_along_axis(ctx, info.edge_dir, spec.get("view"), "the chamfer edge")
    tip = view.project(info.middle)
    diagonal = outward_diagonal(view, tip, spec.get("side"))
    text = f"{ctx.num(round(info.leg_a, 2))} × {ctx.num(round(info.angle_deg, 1))}°"
    # the leader ends in the middle of the chamfer's own line and goes out along its outward normal (ISO 129: from outside,
    # perpendicular to the line); a ``side`` the item asks for keeps the old diagonal, and a geometry with no normal too
    normal = None
    if info.normal is not None:
        nx, ny = view.axis(info.normal)
        if math.hypot(nx, ny) > 1e-6:
            normal = (nx / math.hypot(nx, ny), ny / math.hypot(nx, ny))
    along = normal if spec.get("side") is None else None
    ctx.add(place_leader(ctx, view, tip, diagonal, [text], spec.get("offset_mm"), asked(spec), along))
    if normal is not None:
        ctx.extras.setdefault("chamfer_marks", {})[str(ctx.extras.get("current"))] = (view.name, tip, normal)
    general = sheet_tol.general_note(ctx, "radius", info.leg_a)
    return f"chamfer {text} in the {view.name} view (legs {ctx.num(info.leg_a)} and {ctx.num(info.leg_b)} mm){general}"


def draw(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    kind = spec["kind"]
    steps = {"distance": _distance, "radius": _radius, "angle": _angle, "chamfer": _chamfer, "diameter": _diameter}
    detail = steps[kind](ctx, spec)
    return "ok", detail


def check_hole_positions(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, {"auto": v_bool})


def draw_hole_positions(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """The through holes of an assembly: their diameter, their centre distance and their distance from an edge of the plate,
    on the view that sees them as circles (see ``sheet_interface``)."""
    del spec
    if ctx.assembly is None:
        msg = "hole_positions is for an assembly sheet: a part's holes are located by datum_dims and called out by hole_dim"
        raise ItemError(msg)
    for name in ("top", "front", "right"):
        view = ctx.views.get(name)
        done = sheet_interface.draw(ctx, view) if view is not None else []
        if done:
            return "ok", f"in the {name} view: " + "; ".join(done)
    return "ok", "no view of the sheet sees a through hole of the assembly as a circle"


ITEMS = (
    ItemType("overall", "o", check_overall, draw_overall, frozenset({"offset_mm", "side"}), phase=0),
    ItemType("dim", "d", check, draw, frozenset(CHECKS), phase=0),
    ItemType("hole_positions", "hp", check_hole_positions, draw_hole_positions, frozenset({"auto"}), phase=0),
)
