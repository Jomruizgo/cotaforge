"""Common protocol of every construction/assembly op: one transaction, recompute, state
check, shape diff, registry refresh, ``OperationResult`` JSON. Also sketch materialisation and body selection.
"""

import math
import time
import traceback
from collections.abc import Callable
from typing import Any

import fc
import geometry as g
import journal
import Part
import registry as reg
import sketch_defined
import sketches
from FreeCAD import Placement, Vector
from protocol import WorkerError, error, geometry_failed, invalid_request, not_supported, unrecoverable

FcObject = Any
Point2 = tuple[float, float]
POINT_TOL = 1e-6


# --- profiles (parity with contract.sketch_profiles) ------------------------------------------------------------


TWO_PI = 2.0 * math.pi
ARC_KINDS = ("arc", "arc3", "arc_ends")
ARC_STEP_RAD = math.radians(1.0)
ARC_ON_CIRCLE_MM = 1e-5
Arc = tuple[Point2, float, float, float]  # centre, radius, start angle, counter-clockwise sweep (radians)
Segment = tuple[Point2, Point2, Arc | None, bool]  # start, end, arc, whether start -> end turns counter-clockwise


def _pt(p: Any) -> Point2:
    return (float(p[0]), float(p[1]))


def arc_geometry(e: dict[str, Any]) -> Arc:
    """The normal form of an arc entity (parity with ``contract.arc_geometry``); ``ValueError`` with the reason."""
    kind, name = e["kind"], e.get("name")
    if kind == "arc":
        radius = float(e["radius"])
        if radius <= 0:
            raise ValueError(f"arc {name}: radius must be positive")
        sweep = math.radians(float(e["end_deg"]) - float(e["start_deg"])) % TWO_PI
        if sweep < 1e-9 or TWO_PI - sweep < 1e-9:
            raise ValueError(f"arc {name}: start_deg and end_deg coincide (use a circle for a full turn)")
        return _pt(e["center"]), radius, math.radians(float(e["start_deg"])), sweep
    if kind == "arc3":
        a, b, c = _pt(e["start"]), _pt(e["through"]), _pt(e["end"])
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(cross) < 1e-9 * max(1.0, math.dist(a, b) * math.dist(a, c)):
            raise ValueError(f"the three points {a}, {b}, {c} are collinear: that is a line, not an arc")
        a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
        d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
        center = (
            (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
            (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d,
        )
        first, middle, last = (math.atan2(p[1] - center[1], p[0] - center[0]) for p in (a, b, c))
        sweep = (last - first) % TWO_PI
        if (middle - first) % TWO_PI < sweep:
            return center, math.dist(center, a), first, sweep
        return center, math.dist(center, a), last, (first - last) % TWO_PI
    center, start, end = _pt(e["center"]), _pt(e["start"]), _pt(e["end"])
    radius, radius_end = math.dist(center, start), math.dist(center, end)
    if radius < 1e-9 or abs(radius - radius_end) > ARC_ON_CIRCLE_MM * max(1.0, radius):
        raise ValueError(f"arc {name}: start and end must be the same distance from center, not at it")
    first, last = (math.atan2(p[1] - center[1], p[0] - center[0]) for p in (start, end))
    ccw = bool(e.get("ccw", True))
    sweep = (last - first) % TWO_PI if ccw else (first - last) % TWO_PI
    if sweep < 1e-9 or TWO_PI - sweep < 1e-9:
        raise ValueError(f"arc {name}: start and end coincide (use a circle for a full turn)")
    return center, radius, first if ccw else last, sweep


def arc_point(arc: Arc, angle: float) -> Point2:
    return (arc[0][0] + arc[1] * math.cos(angle), arc[0][1] + arc[1] * math.sin(angle))


def arc_segment(e: dict[str, Any]) -> Segment:
    arc = arc_geometry(e)
    if e["kind"] == "arc":
        return arc_point(arc, arc[2]), arc_point(arc, arc[2] + arc[3]), arc, True
    start, end = _pt(e["start"]), _pt(e["end"])
    if e["kind"] == "arc3":  # the normal form starts at whichever end it turns counter-clockwise from
        return start, end, arc, math.dist(arc_point(arc, arc[2]), start) <= 1e-6 * max(1.0, arc[1])
    return start, end, arc, bool(e.get("ccw", True))


def slot_geometry(e: dict[str, Any]) -> tuple[Arc, Arc, tuple[Point2, Point2, Point2, Point2]]:
    """The arcs at the start and end centres and the corners a, b, c, d (a, b on the left of the axis, c, d on the
    right): a-b and c-d are the lines, the start arc runs a -> d and the end arc c -> b (counter-clockwise)."""
    s, t = _pt(e["start"]), _pt(e["end"])
    length, r = math.dist(s, t), float(e["width"]) / 2.0
    if r <= 0:
        raise ValueError(f"slot {e.get('name')}: width must be positive")
    if length < 1e-9:
        raise ValueError(f"slot {e.get('name')}: the end centres coincide (use a circle)")
    dx, dy = (t[0] - s[0]) / length, (t[1] - s[1]) / length
    nx, ny = -dy * r, dx * r
    heading = math.atan2(dy, dx)
    corners = (
        (s[0] + nx, s[1] + ny),
        (t[0] + nx, t[1] + ny),
        (t[0] - nx, t[1] - ny),
        (s[0] - nx, s[1] - ny),
    )
    return (s, r, heading + math.pi / 2, math.pi), (t, r, heading - math.pi / 2, math.pi), corners


def slot_segments(e: dict[str, Any]) -> list[Segment]:
    at_start, at_end, (a, b, c, d) = slot_geometry(e)
    return [(a, b, None, True), (b, c, at_end, False), (c, d, None, True), (a, d, at_start, True)]


def chain_loops(segments: list[Segment]) -> list[list[tuple[Segment, bool]]]:
    """Closed loops of segments by shared endpoints; each is paired with whether the loop runs it end to start."""
    remaining = list(segments)
    loops: list[list[tuple[Segment, bool]]] = []
    while remaining:
        first = remaining.pop(0)
        current = first[1]
        loop = [(first, False)]
        while math.dist(current, first[0]) > POINT_TOL:
            found = None
            for i, other in enumerate(remaining):
                if math.dist(other[0], current) <= POINT_TOL:
                    found = (i, False)
                    break
                if math.dist(other[1], current) <= POINT_TOL:
                    found = (i, True)
                    break
            if found is None:
                raise ValueError(f"open chain of line/arc entities: no entity continues from point {current}")
            other = remaining.pop(found[0])
            current = other[0] if found[1] else other[1]
            loop.append((other, found[1]))
        loops.append(loop)
    return loops


def arc_sample_angles(start: float, delta: float) -> list[float]:
    """Angles strictly inside an arc where its outline is sampled (parity with ``contract.arc_sample_angles``)."""
    steps = max(2, math.ceil(abs(delta) / ARC_STEP_RAD))
    angles = [start + delta * k / steps for k in range(1, steps)]
    low, high = sorted((start, start + delta))
    quarter = math.pi / 2.0
    for k in range(math.ceil(low / quarter), math.floor(high / quarter) + 1):
        angle = k * quarter
        if low + 1e-9 < angle < high - 1e-9 and all(abs(angle - a) > 1e-9 for a in angles):
            angles.append(angle)
    return sorted(angles, reverse=delta < 0)


def loop_profile(loop: list[tuple[Segment, bool]]) -> dict[str, Any]:
    """Exact area and centroid of a closed loop of lines and arcs (Green's theorem, edge by edge; parity with
    ``contract.sketch_profiles``), its outline counter-clockwise as points with arcs sampled every degree."""
    area2 = mx = my = 0.0
    points: list[Point2] = []
    for (start, end, arc, ccw), reverse in loop:
        p, q = (end, start) if reverse else (start, end)
        dx, dy = q[0] - p[0], q[1] - p[1]
        points.append(p)
        if arc is None:
            area2 += p[0] * q[1] - q[0] * p[1]
            mx += dy * (p[0] ** 2 + p[0] * dx + dx**2 / 3.0)
            my += dx * (p[1] ** 2 + p[1] * dy + dy**2 / 3.0)
            continue
        (cx, cy), r = arc[0], arc[1]
        forward = ccw != reverse
        t0 = arc[2] if forward else arc[2] + arc[3]
        delta = arc[3] if forward else -arc[3]
        t1 = t0 + delta
        s0, s1, c0, c1 = math.sin(t0), math.sin(t1), math.cos(t0), math.cos(t1)
        area2 += r * r * delta + r * cx * (s1 - s0) - r * cy * (c1 - c0)
        mx += r * (
            cx * cx * (s1 - s0)
            + 2 * cx * r * (delta / 2 + (math.sin(2 * t1) - math.sin(2 * t0)) / 4)
            + r * r * ((s1 - s0) - (s1**3 - s0**3) / 3)
        )
        my -= r * (
            cy * cy * (c0 - c1)
            + 2 * cy * r * (delta / 2 - (math.sin(2 * t1) - math.sin(2 * t0)) / 4)
            + r * r * ((c0 - c1) + (c1**3 - c0**3) / 3)
        )
        points.extend(arc_point(arc, angle) for angle in arc_sample_angles(t0, delta))
    area = area2 / 2.0
    if abs(area) < 1e-12:
        raise ValueError("degenerate polygon: zero area")
    pts = points if area > 0 else list(reversed(points))
    profile = {"kind": "polygon", "area": abs(area), "centroid": (mx / 2.0 / area, -my / 2.0 / area), "points": pts}
    if any(segment[2] is not None for segment, _ in loop):
        profile["sides"] = len(loop)
    return profile


def line_segment(e: dict[str, Any]) -> Segment:
    return _pt(e["start"]), _pt(e["end"]), None, True


def rect_chamfer(e: dict[str, Any], operation_id: str | None) -> float | None:
    """The 45 degree chamfer of a rect entity (``None`` when its corners are sharp); it must be positive and smaller
    than half of the shorter side."""
    value = e.get("chamfer_mm")
    if value is None:
        return None
    chamfer = float(value)
    limit = min(float(e["width"]), float(e["height"])) / 2.0
    if not 0 < chamfer < limit:
        raise invalid_request(
            f"rect {e.get('name')}: chamfer_mm must be greater than 0 and smaller than half of the shorter side "
            f"({limit:g} mm), got {chamfer:g}",
            operation_id,
        )
    return chamfer


def chamfer_points(corners: list[Point2], c: float) -> list[Point2]:
    """The eight vertices, counter-clockwise from the bottom edge, of the rect with corners ``corners`` (bottom left,
    bottom right, top right, top left) cut by ``c`` at 45 degrees. Vertex ``2 i`` and ``2 i + 1`` bound side ``i``."""
    (x0, y0), (x1, _), (_, y1), _ = corners
    return [
        (x0 + c, y0),
        (x1 - c, y0),
        (x1, y0 + c),
        (x1, y1 - c),
        (x1 - c, y1),
        (x0 + c, y1),
        (x0, y1 - c),
        (x0, y0 + c),
    ]


def sketch_profiles(entities: list[dict[str, Any]], operation_id: str | None) -> list[dict[str, Any]]:
    """Closed profiles of a sketch as dicts: kind, area, centroid, points/center/radius (2D, sketch coords)."""
    profiles: list[dict[str, Any]] = []
    for e in entities:
        if e["kind"] == "rect":
            cx, cy = float(e["center"][0]), float(e["center"][1])
            w, h = float(e["width"]), float(e["height"])
            if w <= 0 or h <= 0:
                raise invalid_request(f"rect {e.get('name')} needs positive width and height", operation_id)
            pts = [
                (cx - w / 2, cy - h / 2),
                (cx + w / 2, cy - h / 2),
                (cx + w / 2, cy + h / 2),
                (cx - w / 2, cy + h / 2),
            ]
            chamfer = rect_chamfer(e, operation_id)
            if chamfer is not None:  # eight corners, counter-clockwise: a polygon like the contract's
                profiles.append(
                    {
                        "kind": "polygon",
                        "area": w * h - 2.0 * chamfer * chamfer,
                        "centroid": (cx, cy),
                        "points": chamfer_points(pts, chamfer),
                    }
                )
                continue
            profiles.append(
                {"kind": "rect", "area": w * h, "centroid": (cx, cy), "points": pts, "width": w, "height": h}
            )
        elif e["kind"] == "square":
            # geometrically a rect profile (contract parity): the intent only changes the constraint set
            cx, cy = float(e["center"][0]), float(e["center"][1])
            s = float(e["side"])
            if s <= 0:
                raise invalid_request(f"square {e.get('name')} needs a positive side", operation_id)
            h = s / 2
            pts = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)]
            profiles.append(
                {"kind": "rect", "area": s * s, "centroid": (cx, cy), "points": pts, "width": s, "height": s}
            )
        elif e["kind"] == "circle":
            cx, cy = float(e["center"][0]), float(e["center"][1])
            r = float(e["diameter"]) / 2.0
            if r <= 0:
                raise invalid_request(f"circle {e.get('name')} needs a positive diameter", operation_id)
            profiles.append(
                {"kind": "circle", "area": math.pi * r * r, "centroid": (cx, cy), "center": (cx, cy), "radius": r}
            )
    try:
        for e in (e for e in entities if e["kind"] == "slot"):
            profiles.extend(loop_profile(loop) for loop in chain_loops(slot_segments(e)))
        chain = [
            line_segment(e) if e["kind"] == "line" else arc_segment(e)
            for e in entities
            if e["kind"] == "line" or e["kind"] in ARC_KINDS
        ]
        profiles.extend(loop_profile(loop) for loop in chain_loops(chain))
    except ValueError as exc:
        raise invalid_request(str(exc), operation_id) from exc
    if not profiles:
        raise invalid_request("sketch has no closed profile", operation_id)
    return profiles


# --- sketch materialisation -----------------------------------------------------------------------------------


def _v(p: Point2) -> Vector:
    return Vector(float(p[0]), float(p[1]), 0.0)


def to_body_frame(fc_body: FcObject, world: Placement) -> Placement:
    """A world-frame placement expressed in the frame of a PartDesign body. Features live in body-local coordinates
    (``Tip.Shape`` carries no ``Body.Placement``), so anything written into a body-local object -- a sketch, a datum
    line -- must have the body's own pose taken out; identical to ``world`` for an unmoved body."""
    body_pose = fc_body.Placement
    return world if g.is_identity_placement(body_pose) else body_pose.inverse().multiply(world)


def dir_to_body_frame(fc_body: FcObject, direction: g.Vec3) -> g.Vec3:
    """A world direction in body-local terms (axis senses are stored body-local for PartDesign bodies)."""
    body_pose = fc_body.Placement
    if g.is_identity_placement(body_pose):
        return direction
    return g.vec(body_pose.Rotation.inverted().multVec(g.fcvec(direction)))


def mirror_plane(
    params: dict[str, Any], operation_id: str | None, what: str = "mirror"
) -> tuple[dict[str, Any], g.Vec3, g.Vec3]:
    """The realised frame of a mirror (or ``what``, e.g. split) plane and its origin and unit normal (a reflection or a
    cut ignores ``u`` and ``v``, but the frame is still checked: a skewed or non-unit one is a mistake of the caller,
    not a plane)."""
    try:
        frame = g.realise_frame(params["plane"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise invalid_request(f"invalid {what} plane frame ({exc})", operation_id) from exc
    return frame, g.vec(frame["origin"]), g.vec(frame["n"])


def materialise_sketch(
    doc: reg.DocumentRec, body: FcObject | None, rec: reg.SketchRec, name: str, label: str | None = None
) -> FcObject:
    """Create the ``Sketcher::SketchObject`` of a ``SketchRec`` inside ``body`` (``None``: at document level) with a
    fully-defining constraint set and the named dimensions; ``solve() == 0`` and points unchanged are checked.
    ``label`` is the tree text of the sketch (the consuming op names it)."""
    operation_id = rec.spec.get("operation_id")
    if body is None:  # a sheet-metal base: the sketch is a document-level object, placed in world terms
        sk = fc.add_object(doc.fc_doc, "Sketcher::SketchObject", name, label=label)
        fc.set_prop(sk, "MapMode", "Deactivated")
        fc.set_prop(sk, "Placement", g.frame_placement(rec.frame))
    else:
        sk = fc.new_in_body(body, "Sketcher::SketchObject", name, label=label)
        fc.set_prop(sk, "MapMode", "Deactivated")
        fc.set_prop(sk, "Placement", to_body_frame(body, g.frame_placement(rec.frame)))
    entities = list(rec.spec.get("entities", ()))
    by_name: dict[str, dict[str, Any]] = {}
    # entity name -> {"geo": [indices], "constraints": {attribute: constraint index}}
    expected_points: list[tuple[int, int, Point2]] = []  # (geo index, pos 1|2|3, expected point)
    vertex_fixed: dict[tuple[float, float], bool] = {}
    chamfered: list[str] = []  # rect entities cut at the corners: their chamfer size is always named

    def fix_point(geo: int, pos: int, p: Point2) -> tuple[int, int]:
        cx = fc.add_constraint(sk, "DistanceX", -1, 1, geo, pos, float(p[0]))
        cy = fc.add_constraint(sk, "DistanceY", -1, 1, geo, pos, float(p[1]))
        return cx, cy

    arc_vertices: list[tuple[int, int, Point2]] = []  # the two ends of every arc: (geo index, 1|2, point)

    def add_arc(arc: Arc, info: dict[str, Any]) -> tuple[int, Point2, Point2]:
        """An arc pinned on its own: centre and start point fixed plus its span, which leaves no freedom (five
        parameters, five constraints) and no redundancy with whatever is made coincident with its ends."""
        center, radius, start, sweep = arc
        a0 = start % TWO_PI
        circle = Part.Circle(_v(center), Vector(0, 0, 1), radius)
        geo = fc.call(sk, "addGeometry", Part.ArcOfCircle(circle, a0, a0 + sweep), False)
        first, last = arc_point(arc, a0), arc_point(arc, a0 + sweep)
        info["constraints"]["u"], info["constraints"]["v"] = fix_point(geo, 3, center)
        fix_point(geo, 1, first)
        fc.add_constraint(sk, "Angle", geo, sweep)
        expected_points.extend([(geo, 3, center), (geo, 1, first), (geo, 2, last)])
        return geo, first, last

    for e in entities:
        info: dict[str, Any] = {"geo": [], "constraints": {}}
        by_name[str(e["name"])] = info
        kind = e["kind"]
        if kind == "rect" and e.get("chamfer_mm") is not None:
            cx, cy = float(e["center"][0]), float(e["center"][1])
            w, h = float(e["width"]), float(e["height"])
            chamfer = rect_chamfer(e, operation_id) or 0.0
            corners = [
                (cx - w / 2, cy - h / 2),
                (cx + w / 2, cy - h / 2),
                (cx + w / 2, cy + h / 2),
                (cx - w / 2, cy + h / 2),
            ]
            pts = chamfer_points(corners, chamfer)
            # 8 lines: 0 bottom, 1 bottom-right chamfer, 2 right, 3 top-right chamfer, 4 top, 5 top-left chamfer,
            # 6 left, 7 bottom-left chamfer; line i runs from pts[i] to pts[i + 1]
            idx = [
                fc.call(sk, "addGeometry", Part.LineSegment(_v(pts[i]), _v(pts[(i + 1) % 8])), False) for i in range(8)
            ]
            info["geo"] = idx
            for i in range(8):
                fc.add_constraint(sk, "Coincident", idx[i], 2, idx[(i + 1) % 8], 1)
                expected_points.append((idx[i], 1, pts[i]))
            for i in (0, 4):
                fc.add_constraint(sk, "Horizontal", idx[i])
            for i in (2, 6):
                fc.add_constraint(sk, "Vertical", idx[i])
            # the chamfers: ONE driving size (the horizontal leg of the first), the others equal to it and every one at
            # 45 degrees (line angles are measured from +u, along the way the line is drawn)
            size = fc.add_constraint(sk, "DistanceX", idx[1], 1, idx[1], 2, chamfer)
            fc.call(sk, "renameConstraint", size, f"C_{e['name']}")
            for i in (3, 5, 7):
                fc.add_constraint(sk, "Equal", idx[1], idx[i])
            for i, angle in ((1, 45.0), (3, 135.0), (5, -135.0), (7, -45.0)):
                fc.add_constraint(sk, "Angle", idx[i], math.radians(angle))
            info["constraints"]["chamfer"] = size
            info["constraints"]["width"] = fc.add_constraint(sk, "DistanceX", idx[6], 1, idx[2], 1, w)
            info["constraints"]["height"] = fc.add_constraint(sk, "DistanceY", idx[0], 1, idx[4], 1, h)
            fc.add_constraint(sk, "DistanceX", -1, 1, idx[6], 1, pts[6][0])
            fc.add_constraint(sk, "DistanceY", -1, 1, idx[0], 1, pts[0][1])
            info["center"] = (cx, cy)
            info["center_pair"] = (idx[7], idx[3])  # two points symmetric about the centre
            chamfered.append(e["name"])
        elif kind in ("rect", "square"):
            cx, cy = float(e["center"][0]), float(e["center"][1])
            w, h = (float(e["side"]), float(e["side"])) if kind == "square" else (float(e["width"]), float(e["height"]))
            pts = [
                (cx - w / 2, cy - h / 2),
                (cx + w / 2, cy - h / 2),
                (cx + w / 2, cy + h / 2),
                (cx - w / 2, cy + h / 2),
            ]
            idx = [
                fc.call(sk, "addGeometry", Part.LineSegment(_v(pts[i]), _v(pts[(i + 1) % 4])), False) for i in range(4)
            ]
            info["geo"] = idx
            for i in range(4):
                fc.add_constraint(sk, "Coincident", idx[i], 2, idx[(i + 1) % 4], 1)
                expected_points.append((idx[i], 1, pts[i]))
            fc.add_constraint(sk, "Horizontal", idx[0])
            fc.add_constraint(sk, "Horizontal", idx[2])
            fc.add_constraint(sk, "Vertical", idx[1])
            fc.add_constraint(sk, "Vertical", idx[3])
            if kind == "square":
                # one driving side (named S_<entity> unless a named dimension renames it) plus Equal between two
                # adjacent sides: the square stays a square when the side is edited
                side_idx = fc.add_constraint(sk, "DistanceX", idx[0], 1, idx[0], 2, w)
                fc.add_constraint(sk, "Equal", idx[0], idx[1])
                fc.call(sk, "renameConstraint", side_idx, f"S_{e['name']}")
                info["constraints"]["side"] = side_idx
            else:
                info["constraints"]["width"] = fc.add_constraint(sk, "DistanceX", idx[0], 1, idx[0], 2, w)
                info["constraints"]["height"] = fc.add_constraint(sk, "DistanceY", idx[1], 1, idx[1], 2, h)
            fix_point(idx[0], 1, pts[0])
            info["center"] = (cx, cy)
        elif kind == "circle":
            cx, cy = float(e["center"][0]), float(e["center"][1])
            d = float(e["diameter"])
            ci = fc.call(sk, "addGeometry", Part.Circle(Vector(cx, cy, 0.0), Vector(0, 0, 1), d / 2.0), False)
            info["geo"] = [ci]
            info["constraints"]["diameter"] = fc.add_constraint(sk, "Diameter", ci, d)
            cxi, cyi = fix_point(ci, 3, (cx, cy))
            info["constraints"]["u"] = cxi
            info["constraints"]["v"] = cyi
            expected_points.append((ci, 3, (cx, cy)))
        elif kind == "line":
            s = (float(e["start"][0]), float(e["start"][1]))
            t = (float(e["end"][0]), float(e["end"][1]))
            li = fc.call(sk, "addGeometry", Part.LineSegment(_v(s), _v(t)), False)
            info["geo"] = [li]
            expected_points.append((li, 1, s))
            expected_points.append((li, 2, t))
        elif kind in ARC_KINDS:
            try:
                geo, first, last = add_arc(arc_geometry(e), info)
            except ValueError as exc:
                raise invalid_request(str(exc), operation_id) from exc
            info["geo"] = [geo]
            arc_vertices.extend([(geo, 1, first), (geo, 2, last)])
        elif kind == "slot":
            try:
                at_start, at_end, (a, b, c, d) = slot_geometry(e)
            except ValueError as exc:
                raise invalid_request(str(exc), operation_id) from exc
            start_arc = add_arc(at_start, info)[0]  # the slot's u, v are its start centre
            end_arc = add_arc(at_end, {"constraints": {}})[0]
            ab = fc.call(sk, "addGeometry", Part.LineSegment(_v(a), _v(b)), False)
            cd = fc.call(sk, "addGeometry", Part.LineSegment(_v(c), _v(d)), False)
            joints = ((ab, 1, start_arc, 1), (ab, 2, end_arc, 2), (cd, 1, end_arc, 1), (cd, 2, start_arc, 2))
            for line, pos, arc_geo, arc_pos in joints:
                fc.add_constraint(sk, "Coincident", line, pos, arc_geo, arc_pos)
            info["geo"] = [start_arc, end_arc, ab, cd]
        else:
            raise not_supported(f"sketch entity kind {kind} is not supported", operation_id)

    # line chains: coincident between consecutive coincident points, every distinct vertex fixed once; the ends of an
    # arc are already pinned by the arc itself, so a line that meets one is only made coincident with it
    line_geos: list[tuple[int, Point2, Point2, dict[str, Any]]] = []
    for e in entities:
        if e["kind"] == "line":
            info = by_name[str(e["name"])]
            line_geos.append(
                (
                    info["geo"][0],
                    (float(e["start"][0]), float(e["start"][1])),
                    (float(e["end"][0]), float(e["end"][1])),
                    info,
                )
            )
    seen: list[tuple[int, int, Point2]] = list(arc_vertices)  # first occurrence of each distinct vertex
    for li, s, t, info in line_geos:
        for pos, p in ((1, s), (2, t)):
            match = None
            for gi, gpos, q in seen:
                if g.dist((*p, 0.0), (*q, 0.0)) <= POINT_TOL:
                    match = (gi, gpos)
                    break
            if match is None:
                seen.append((li, pos, p))
                key = (round(p[0], 9), round(p[1], 9))
                if key not in vertex_fixed:
                    cxi, cyi = fix_point(li, pos, p)
                    vertex_fixed[key] = True
                    if pos == 1:
                        info["constraints"]["u"] = cxi
                        info["constraints"]["v"] = cyi
            else:
                fc.add_constraint(sk, "Coincident", match[0], match[1], li, pos)

    # named dimensions
    named: dict[str, float] = {}
    for nd in rec.spec.get("named_dimensions", ()):
        ename = str(nd["entity"])
        attr = str(nd["attribute"])
        dim_name = str(nd["name"])
        info = by_name.get(ename)
        if info is None:
            raise invalid_request(f"named dimension {dim_name} refers to unknown entity {ename}", operation_id)
        idx = info["constraints"].get(attr)
        if idx is None:
            ent = next(e for e in entities if str(e["name"]) == ename)
            if attr in ("u", "v") and ent["kind"] in ("rect", "square"):
                # FreeCAD has no rectangle centre: a construction point kept symmetric to two opposite corners
                # carries reference DistanceX/DistanceY constraints so getDatum(name) reads the centre (fake parity)
                if "center_point" not in info:
                    ccx, ccy = info["center"]
                    pt = fc.call(sk, "addGeometry", Part.Point(Vector(ccx, ccy, 0.0)), True)
                    first, second = info.get("center_pair") or (info["geo"][0], info["geo"][2])
                    fc.add_constraint(sk, "Symmetric", first, 1, second, 1, pt, 1)
                    info["center_point"] = pt
                pt = info["center_point"]
                coord = info["center"][0] if attr == "u" else info["center"][1]
                idx = fc.add_constraint(sk, "DistanceX" if attr == "u" else "DistanceY", -1, 1, pt, 1, coord)
            elif attr in ("u", "v") and ent["kind"] == "line":
                coord = float(ent["start"][0] if attr == "u" else ent["start"][1])
                idx = fc.add_constraint(
                    sk, "DistanceX" if attr == "u" else "DistanceY", -1, 1, info["geo"][0], 1, coord
                )
            elif attr == "length" and ent["kind"] == "line":
                s, t = ent["start"], ent["end"]
                value = math.dist((float(s[0]), float(s[1])), (float(t[0]), float(t[1])))
                idx = fc.add_constraint(sk, "Distance", info["geo"][0], value)
            elif attr == "radius" and ent["kind"] == "circle":
                value = float(ent["diameter"]) / 2.0
                idx = fc.add_constraint(sk, "Radius", info["geo"][0], value)
            elif attr == "radius" and ent["kind"] in ARC_KINDS:
                idx = fc.add_constraint(sk, "Radius", info["geo"][0], arc_geometry(ent)[1])
            elif attr == "width" and ent["kind"] == "slot":
                idx = fc.add_constraint(sk, "Diameter", info["geo"][0], float(ent["width"]))
            elif attr == "length" and ent["kind"] == "slot":
                value = math.dist(_pt(ent["start"]), _pt(ent["end"]))
                idx = fc.add_constraint(sk, "Distance", info["geo"][0], 3, info["geo"][1], 3, value)
            else:
                raise invalid_request(
                    f"named dimension {dim_name}: attribute {attr} does not apply to entity {ename}", operation_id
                )
            fc.call(sk, "setDriving", idx, False)
        fc.call(sk, "renameConstraint", idx, dim_name)
        named[dim_name] = float(sk.Constraints[idx].Value)

    for ename in chamfered:  # C_<entity> (or the name the engineer gave it) is listed with the named dimensions
        index = by_name[ename]["constraints"]["chamfer"]
        named[str(sk.Constraints[index].Name)] = float(sk.Constraints[index].Value)

    rc = fc.call(sk, "solve")
    if rc != 0:
        raise geometry_failed(f"sketch {rec.id} does not solve (code {rc})", operation_id, sketch=rec.id)
    for gi, pos, p in expected_points:
        actual = sk.getPoint(gi, pos)
        if math.dist((actual.x, actual.y), (float(p[0]), float(p[1]))) > 1e-6:
            raise geometry_failed(f"sketch {rec.id} moved a point while solving", operation_id, sketch=rec.id)
    # the rule of every feature: its sketch ends fully defined by dimensions. The sets above are complete by
    # construction; this keeps a new entity kind or a changed set from slipping an under defined sketch in.
    verdict = sketch_defined.judge(sk)
    if verdict is not None:
        raise error(
            "invalid_request",
            verdict.text(f"the sketch {rec.id}"),
            operation_id,
            sketch=rec.id,
            reason=verdict.reason,
            dof=verdict.dof,
        )
    fc.recompute(doc.fc_doc)  # features read the profile shape when their properties are set
    rec.fc_name = sk.Name
    rec.named_dimensions = named
    return sk


def profiles_world_centroid(rec: reg.SketchRec) -> list[g.Vec3]:
    return [g.frame_to_world(rec.frame, *p["centroid"]) for p in rec.profiles]


# --- body selection --------------------------------------------------------------------------------------------


def boss_body(doc: reg.DocumentRec, sketch: reg.SketchRec, merge: bool, feature: reg.FeatureRec) -> reg.BodyRec:
    bodies = doc.live_bodies()
    if merge and sketch.on_face is not None:
        rec, comp = reg.resolve_face(doc, sketch.on_face, feature.params.get("operation_id"))
        if comp is not None:
            raise not_supported("construction on a component face is not supported", feature.params.get("operation_id"))
        return reg.resolve_body(doc, rec.body, feature.params.get("operation_id"))
    if merge and len(bodies) == 1:
        return bodies[0]
    return new_partdesign_body(doc, feature)


def cut_body(doc: reg.DocumentRec, sketch: reg.SketchRec, operation_id: str | None) -> reg.BodyRec:
    bodies = doc.live_bodies()
    if sketch.on_face is not None:
        rec, comp = reg.resolve_face(doc, sketch.on_face, operation_id)
        if comp is not None:
            raise not_supported("cut on a component face is not supported", operation_id)
        return reg.resolve_body(doc, rec.body, operation_id)
    if len(bodies) == 1:
        return bodies[0]
    for point in profiles_world_centroid(sketch):
        for body in bodies:
            shape = reg.world_shape(doc, body)
            if not shape.isNull() and g.bbox_contains_point(g.shape_bbox(shape), point):
                return body
    raise invalid_request("cut on a plane needs exactly one body or a profile inside a body", operation_id)


def role_label(feature: reg.FeatureRec, role: str | None = None) -> str:
    """The tree label of an object an op creates: the op's own label for the object that is its feature,
    ``<label>_<role>`` for any other (sketch, datum, helper body, one piece of several...)."""
    return feature.label if role is None else f"{feature.label}_{role}"


def new_partdesign_body(doc: reg.DocumentRec, feature: reg.FeatureRec) -> reg.BodyRec:
    fc_body = fc.add_object(doc.fc_doc, "PartDesign::Body", "Body", label=role_label(feature, "body"))
    body = reg.BodyRec(doc.next_id("body"), "partdesign", fc_body.Name, fc_body.Name, feature.id)
    doc.bodies[body.id] = body
    feature.created_bodies.append(body.id)
    feature.fc_names.append(fc_body.Name)
    return body


def ensure_partdesign(doc: reg.DocumentRec, body: reg.BodyRec, feature: reg.FeatureRec) -> FcObject:
    """Return the ``PartDesign::Body`` of ``body``, converting a Part body once (base feature = shape copy)."""
    if body.sheet_metal:
        raise not_supported(
            "a sheet-metal body takes only sheet-metal ops: put holes and cut-outs in the base_flange sketch (extra "
            "closed profiles inside the outline); cut, hole, fillet and the other solid features cannot work on it",
            feature.params.get("operation_id"),
        )
    if body.kind == "partdesign":
        return doc.fc_doc.getObject(body.fc_body)
    shape = reg.world_shape(doc, body)
    if not g.is_identity_placement(shape.Placement):
        # PartDesign reads a base feature's shape in body coordinates and drops its Placement, so a moved or copied
        # Part body would jump back to the origin (and every later profile would miss it): bake the pose into the
        # geometry instead (identity Placement, world coordinates; element order is kept)
        matrix = shape.Placement.toMatrix()
        shape = shape.copy()
        shape.Placement = Placement()
        shape = shape.transformShape(matrix, True)
    base = fc.add_object(doc.fc_doc, "Part::Feature", "Base", label=role_label(feature, "base"))
    fc.set_prop(base, "Shape", shape.copy())
    fc_body = fc.add_object(doc.fc_doc, "PartDesign::Body", "Body", label=role_label(feature, "body"))
    fc.set_prop(fc_body, "BaseFeature", base)
    # FreeCAD itself puts a ``PartDesign::FeatureBase`` into the body to carry the link: it shows in the tree too
    link = next((o for o in fc_body.Group if o.TypeId == "PartDesign::FeatureBase"), None)
    if link is not None:
        fc.set_label(link, role_label(feature, "base_feature"))
        feature.fc_names.append(link.Name)  # registered, or the feature tree lists it as a hand-made feature
    fc.recompute(doc.fc_doc)
    body.kind = "partdesign"
    body.fc_body = fc_body.Name
    body.result_obj = fc_body.Name
    feature.fc_names.extend([base.Name, fc_body.Name])
    body.invalidate()
    # the base feature shape is a copy: re-key every record by index (same order)
    reg.refresh_by_index(doc, body, reg.topo_shape(doc, body), feature.id)
    return fc_body


# --- feature records and results -------------------------------------------------------------------------------


def flatten_params(params: dict[str, Any]) -> dict[str, float | str | bool]:
    out: dict[str, float | str | bool] = {}
    for key, value in params.items():
        if key == "operation_id" or value is None:
            continue
        if key == "thread" and isinstance(value, dict):
            # a tapped hole's cosmetic thread, flattened so the feature tree reads it (fake parity)
            out["thread_size"] = str(value["size"])
            if value.get("depth_mm") is not None:
                out["thread_depth_mm"] = float(value["depth_mm"])
            continue
        if key in ("counterbore", "countersink") and isinstance(value, dict):
            for part, number in value.items():
                out[f"{key}_{part}"] = float(number)
            continue
        if isinstance(value, bool | int | float | str):
            out[key] = value
        elif isinstance(value, dict) and "id" in value:
            out[key] = str(value["id"])
        else:
            out[key] = str(value)
    return out


def new_feature(doc: reg.DocumentRec, kind: str, params: dict[str, Any]) -> reg.FeatureRec:
    """The feature record; its label starts as the operation id (what the plan and the feature tree show, as in the
    other workers), ``<kind>_<n>`` when a direct caller gave none. The op asks FreeCAD for that label on the
    object that is its feature (``OpContext.adopt`` then follows FreeCAD if it had to de-duplicate it) and for
    ``<label>_<role>`` on its helpers."""
    feature_id = doc.next_id("feature")
    number = feature_id.split(":", 1)[1]
    return reg.FeatureRec(feature_id, kind, str(params.get("operation_id") or f"{kind}_{number}"), params)


def handles(ids: list[str]) -> list[dict[str, str]]:
    return [{"id": i} for i in ids]


def result_dict(feature: reg.FeatureRec, operation_id: str, rebuild_ok: bool, started: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "operation_id": operation_id,
        "feature": {"id": feature.id},
        "rebuild_ok": rebuild_ok,
        "created_faces": handles(feature.created_faces),
        "modified_faces": handles(feature.modified_faces),
        "created_edges": handles(feature.created_edges),
        "created_bodies": handles(feature.created_bodies),
        "modified_bodies": handles(feature.modified_bodies),
        "sketch": {"id": feature.sketch} if feature.sketch else None,
        "component": {"id": feature.component} if feature.component else None,
        "mate": {"id": feature.mate} if feature.mate else None,
        "instance_count": feature.instance_count,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "warnings": list(feature.warnings),
    }
    if feature.face_count is not None:  # a component: its faces and edges are counted, not listed
        out["face_count"] = feature.face_count
        out["edge_count"] = feature.edge_count
    return out


def feature_state_ok(obj: FcObject) -> bool:
    try:
        state = list(obj.State)
    except Exception:  # noqa: BLE001
        return True
    if "Invalid" in state:
        return False
    try:
        return bool(obj.isValid())
    except Exception:  # noqa: BLE001
        return True


def status_of(obj: FcObject) -> str:
    try:
        return str(obj.getStatusString())
    except Exception:  # noqa: BLE001
        return "invalid"


class OpContext:
    """What a build callback receives: the document record, the feature record and the operation id."""

    def __init__(self, doc: reg.DocumentRec, feature: reg.FeatureRec, operation_id: str) -> None:
        self.doc = doc
        self.feature = feature
        self.operation_id = operation_id
        self.bodies: list[reg.BodyRec] = []  # bodies whose shape diff gives the provenance, in order
        self.by_index: bool = False  # move_body: match by index instead of signatures
        self.axis_hint: g.Vec3 | None = None  # intended axis sense for created cylindrical faces
        self.check_objects: list[FcObject] = []  # FreeCAD objects whose State must be valid after recompute
        self.post_check: Callable[[], None] | None = None  # runs after recompute, before the diff (may raise)
        self.expect_volume: str | None = None  # "increase" | "decrease": the op must change the body volume
        self.no_material_hint: str = ""  # appended to the "added no material" error (what to change)

    def label(self, role: str | None = None) -> str:
        """Tree label to ask for when this op creates an object: ``role=None`` is the op's own label, for the one object
        that IS the op's feature; ``<label>_<role>`` is for a helper (sketch, datum, pattern direction...) or for one
        of several pieces of an op with no single feature object (split sides, mirror boss/cut, chamfer groups)."""
        return role_label(self.feature, role)

    def adopt(self, obj: FcObject) -> None:
        """Make ``feature.label`` the label ``obj`` really got. FreeCAD keeps Labels unique in a document, so an op id
        that another object already owns (a helper of an earlier op, or an object named by hand) ends up as
        ``<label>001``; the feature tree must report that, not a name the model tree does not show. Only the
        digit suffix FreeCAD itself appends is adopted: a piece labelled ``<label>_<role>`` leaves the label alone."""
        actual = str(obj.Label)
        label = self.feature.label
        if actual != label and actual.startswith(label) and actual[len(label) :].isdigit():
            self.feature.label = actual


def run_op(
    doc: reg.DocumentRec, kind: str, params: dict[str, Any], build: Callable[[OpContext], None]
) -> dict[str, Any]:
    """The 8-step protocol of every construction op. ``build`` creates the FreeCAD objects and fills ``ctx.bodies``."""
    started = time.perf_counter()
    operation_id = str(params.get("operation_id", ""))
    feature = new_feature(doc, kind, params)
    ctx = OpContext(doc, feature, operation_id)
    snapshot = doc.snapshot()
    before_volume = {b.id: _volume(reg.topo_shape(doc, b)) for b in doc.live_bodies()}
    before_solids = {b.id: _solids(reg.topo_shape(doc, b)) for b in doc.live_bodies()}
    document_before = journal.capture(doc.fc_doc)
    consumed_before = sketches.consumed_names(doc.fc_doc)  # what the engineer shows of these is his own business
    fc.open_transaction(doc.fc_doc, operation_id or kind)
    try:
        build(ctx)
        fc.recompute(doc.fc_doc)
        bad = [o for o in ctx.check_objects if not feature_state_ok(o)]
        if bad:
            status = status_of(bad[0])
            failure = geometry_failed(f"{bad[0].Name}: {status}", operation_id, feature=bad[0].Name)
            if "Failed to make thick solid" in status:  # Thickness: the same wall on the same body fails again
                unrecoverable(failure)
            raise failure
        if ctx.expect_volume is not None:
            _check_volume_change(doc, ctx, before_volume, before_solids)
        if ctx.post_check is not None:
            ctx.post_check()
        # inside the transaction: an undo shows them again, as they were
        sketches.hide_consumed(doc.fc_doc, consumed_before)
        # The journal is sealed INSIDE the transaction's try: a failure here rolls the op back like any other, instead
        # of leaving a committed op in the document without a FeatureRec (which could never be reverted).
        feature.journal = journal.seal(doc.fc_doc, document_before)
    except WorkerError:
        _rollback(doc, snapshot)
        raise
    except Exception as exc:  # FreeCAD / OCC failures inside the transaction
        _rollback(doc, snapshot)
        raise geometry_failed(f"{type(exc).__name__}: {exc}", operation_id, traceback=traceback.format_exc()) from exc
    fc.commit_transaction(doc.fc_doc)
    # every object the op created belongs to it, including the ones FreeCAD makes on its own (a Body's origin, the
    # ``BaseFeature`` link object of a converted body): the registry accounts for all of them (feature_tree shows no
    # phantom 'manual' node) and ``undo_last`` deletes exactly these
    feature.fc_names.extend(n for n in feature.journal.created if n not in feature.fc_names)
    doc.undo_stack.append(snapshot)
    doc.features.append(feature)

    rebuild_ok = True
    for body in ctx.bodies:
        body.invalidate()
        after = reg.topo_shape(doc, body)
        if after.isNull() or not after.isValid():
            rebuild_ok = False
            continue
        if ctx.by_index:
            diff = reg.refresh_by_index(doc, body, after, feature.id)
        else:
            diff = reg.refresh(doc, body, after, feature.id)
        feature.created_faces.extend(diff.created_faces)
        feature.modified_faces.extend(diff.modified_faces)
        feature.created_edges.extend(diff.created_edges)
        if body.id not in feature.created_bodies and body.id not in feature.modified_bodies:
            feature.modified_bodies.append(body.id)
        if feature.id not in body.modified_by and body.id not in feature.created_bodies:
            body.modified_by.append(feature.id)
        if ctx.axis_hint is not None:
            for fid in diff.created_faces:
                rec = body.faces[fid]
                if rec.sig.surface in ("cylinder", "cone"):
                    rec.axis_dir = ctx.axis_hint
    return result_dict(feature, operation_id, rebuild_ok, started)


def _volume(shape: Any) -> float:
    try:
        return 0.0 if shape.isNull() else float(shape.Volume)
    except Exception:  # noqa: BLE001
        return 0.0


def _solids(shape: Any) -> int:
    try:
        return 0 if shape.isNull() else len(shape.Solids)
    except Exception:  # noqa: BLE001
        return 0


def _check_volume_change(
    doc: reg.DocumentRec, ctx: OpContext, before: dict[str, float], before_solids: dict[str, int]
) -> None:
    """PartDesign silently drops disjoint solids (a boss that does not touch the body) and a pocket outside the
    material removes nothing: both are reported as ``geometry_failed`` instead of a silent no-op."""
    for body in ctx.bodies:
        body.invalidate()
        shape = reg.topo_shape(doc, body)
        after = _volume(shape)
        delta = after - before.get(body.id, 0.0)
        grew_solids = ctx.expect_volume == "increase" and _solids(shape) > max(before_solids.get(body.id, 0), 1)
        if ctx.expect_volume == "increase" and (delta <= 1e-9 or grew_solids):
            raise geometry_failed(
                "feature added no material (PartDesign keeps a single solid: the profile does not touch the body)"
                + (f". {ctx.no_material_hint}" if ctx.no_material_hint else ""),
                ctx.operation_id,
            )
        if ctx.expect_volume == "decrease" and delta >= -1e-9:
            raise geometry_failed("feature removed no material (profile outside the body)", ctx.operation_id)


def _rollback(doc: reg.DocumentRec, snapshot: reg.Snapshot) -> None:
    fc.abort_transaction(doc.fc_doc)
    fc.recompute(doc.fc_doc)
    doc.restore(snapshot)
    for body in doc.bodies.values():
        body.invalidate()
