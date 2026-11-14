"""``list_holes``: the cylindrical holes of the bodies of a part, read from the B-rep.

A hole is a CONCAVE cylindrical face (the material lies outside the cylinder: the face normal points toward the axis).
Faces on one axis with one radius whose axial spans touch are one hole (a drilled cylinder arrives as two half faces
from
many exporters). Convex cylinders (pins, bosses) and arcs under half a circle (internal fillets) are not holes.

A face that is not an analytic cylinder (B-spline surfaces written by some exporters, or made by transformGeometry) is
tested by fitting a cylinder to it (``cylinder_fit``); the holes made of such faces are reported as ``fitted``.

Through or blind comes from the two rims of the hole: a rim is OPEN when the face on its other side is a plane normal to
the axis that surrounds it (the entry face) or a cone that widens outward (a chamfer, a countersink), CLOSED when that
plane is bounded by the rim (a flat bottom) or the cone narrows outward (a drill point), and UNKNOWN for anything else.
A counterbore is therefore a blind cylinder on top of the through cylinder it opens into.
"""

import math
from typing import Any

import cylinder_fit
import geometry as g
import Part
import provenance as prov
import registry as reg
from protocol import operation_id_of

AXIS_TOL = 1e-3  # mm: two cylinders are on the same axis
RADIUS_TOL = 1e-4
SPAN_TOL = 1e-3  # mm: two axial spans touch
RIM_TOL = 1e-3
MIN_ARC = math.pi - 1e-3  # a hole covers at least half a circle; an internal fillet (90 degrees) is not one
PARALLEL = 1.0 - 1e-6
FIT_DIRECTION_TOL = 1e-3  # two fitted axes are parallel
PLANAR_TOL = 1e-3  # mm: a non-analytic face this flat is a plane (an entry face or a bottom)


class _FaceRef:
    """The id and index of a face: all a hole needs of its record, so a body nobody built records for stays lazy."""

    def __init__(self, id_: str, index: int) -> None:
        self.id = id_
        self.index = index


class _Cut:
    """One concave cylindrical face: radius, axial span (along the canonical axis ``d``) and angular span.
    ``deviation`` is set when the face is not an analytic cylinder and the cylinder was fitted to it."""

    def __init__(
        self,
        rec: _FaceRef,
        face: Any,
        radius: float,
        direction: g.Vec3,
        anchor: g.Vec3,
        span: tuple[float, float],
        arc: float,
        deviation: float | None = None,
    ) -> None:
        self.rec = rec
        self.face = face
        self.radius = radius
        self.direction = direction
        self.anchor = anchor
        self.t0, self.t1 = span
        self.arc = arc
        self.deviation = deviation

    @property
    def fitted(self) -> bool:
        return self.deviation is not None


def _analytic_cut(rec: _FaceRef, face: Any) -> _Cut:
    surf = face.Surface
    axis = g.unit(g.vec(surf.Axis))
    center = g.vec(surf.Center)
    direction = g.canonical_direction(axis)
    sign = 1.0 if g.dot(axis, direction) > 0 else -1.0
    along = g.dot(center, direction)
    anchor = g.sub(center, g.scale(direction, along))
    u0, u1, v0, v1 = face.ParameterRange
    first, second = along + sign * v0, along + sign * v1
    return _Cut(rec, face, float(surf.Radius), direction, anchor, (min(first, second), max(first, second)), u1 - u0)


def _fitted_cut(rec: _FaceRef, face: Any) -> _Cut | None:
    """The concave cylinder a non-analytic face lies on, or ``None`` (not a cylinder, or a convex one)."""
    fit = cylinder_fit.fit_cylinder(face)
    if fit is None or not fit.concave:
        return None
    return _Cut(rec, face, fit.radius, fit.direction, fit.anchor, (fit.t0, fit.t1), fit.arc, fit.deviation)


def _is_concave(face: Any) -> bool:
    u, v = prov.mid_params(face)
    surf = face.Surface
    axis = g.unit(g.vec(surf.Axis))
    center = g.vec(surf.Center)
    point = g.vec(face.valueAt(u, v))
    foot = g.add(center, g.scale(axis, g.dot(g.sub(point, center), axis)))
    return g.dot(g.vec(face.normalAt(u, v)), g.sub(point, foot)) < 0


def _same_axis(a: _Cut, b: _Cut) -> bool:
    if a.fitted or b.fitted:  # fitted values carry the fit's error
        return (
            g.dist(a.direction, b.direction) < FIT_DIRECTION_TOL
            and g.dist(a.anchor, b.anchor) < cylinder_fit.FIT_TOL * 2.0
            and abs(a.radius - b.radius) < cylinder_fit.FIT_TOL * 2.0
        )
    return (
        g.dist(a.direction, b.direction) < 1e-6
        and g.dist(a.anchor, b.anchor) < AXIS_TOL
        and abs(a.radius - b.radius) < RADIUS_TOL
    )


def _holes_of_cuts(cuts: list[_Cut]) -> list[list[_Cut]]:
    """Cuts on one axis and radius, chained while their axial spans touch."""
    clusters: list[list[_Cut]] = []
    for cut in cuts:
        for cluster in clusters:
            if _same_axis(cluster[0], cut):
                cluster.append(cut)
                break
        else:
            clusters.append([cut])
    holes: list[list[_Cut]] = []
    for cluster in clusters:
        chain: list[_Cut] = []
        reach = -math.inf
        for cut in sorted(cluster, key=lambda c: (c.t0, c.t1)):
            if chain and cut.t0 > reach + SPAN_TOL:
                holes.append(chain)
                chain = []
                reach = -math.inf
            chain.append(cut)
            reach = max(reach, cut.t1)
        holes.append(chain)
    return holes


def _arc_of(chain: list[_Cut]) -> float:
    """The most of the circle the faces cover at any one axial position (halves add up, stacked faces do not)."""
    best = 0.0
    for cut in chain:
        middle = (cut.t0 + cut.t1) / 2.0
        best = max(best, sum(c.arc for c in chain if c.t0 - SPAN_TOL <= middle <= c.t1 + SPAN_TOL))
    return best


def _rim_state(shape: Any, chain: list[_Cut], d: g.Vec3, t_rim: float, at_high_end: bool) -> str:
    """``open``, ``closed`` or ``unknown`` for the rim at axial position ``t_rim``."""
    radius = chain[0].radius
    members = [c.face for c in chain]
    verdicts: set[str] = set()
    for cut in chain:
        for edge in cut.face.Edges:
            if not _is_rim_edge(cut, edge, d, radius, t_rim):
                continue
            for other in shape.ancestorsOfType(edge, Part.Face):
                if any(other.isSame(m) for m in members):
                    continue
                verdicts.add(_other_side(other, edge, d, t_rim, at_high_end))
    if "closed" in verdicts:
        return "closed"
    if "unknown" in verdicts or not verdicts:
        return "unknown"
    return "open"


def _is_rim_edge(cut: _Cut, edge: Any, d: g.Vec3, radius: float, t_rim: float) -> bool:
    """Whether the edge of a hole face is the circle of the hole at axial position ``t_rim``."""
    if cut.fitted:  # the edge of a B-spline face is a B-spline curve: check points along it
        tol = cylinder_fit.FIT_TOL * 2.0
        for point in edge.discretize(7):
            p = g.sub(g.vec(point), cut.anchor)
            if abs(g.dot(p, d) - t_rim) > tol or abs(g.length(g.perpendicular_part(p, d)) - radius) > tol:
                return False
        return True
    if type(edge.Curve).__name__ != "Circle":
        return False
    circle = edge.Curve
    if abs(abs(g.dot(g.unit(g.vec(circle.Axis)), d)) - 1.0) > 1e-6 or abs(circle.Radius - radius) > RIM_TOL:
        return False
    return abs(g.dot(g.vec(circle.Center), d) - t_rim) <= RIM_TOL


def _is_flat(face: Any) -> bool:
    """A plane, written as an analytic plane or as a flat B-spline surface."""
    if g.surface_kind(face) == "plane":
        return True
    surf = face.Surface
    return hasattr(surf, "isPlanar") and bool(surf.isPlanar(PLANAR_TOL))


def _other_side(other: Any, edge: Any, d: g.Vec3, t_rim: float, at_high_end: bool) -> str:
    kind = g.surface_kind(other)
    if _is_flat(other):
        if abs(g.dot(prov.outward_normal(other), d)) < PARALLEL:
            return "unknown"
        return "closed" if any(edge.isSame(e) for e in other.OuterWire.Edges) else "open"
    if kind == "cone":
        surf = other.Surface
        if abs(g.dot(g.unit(g.vec(surf.Axis)), d)) < PARALLEL:
            return "unknown"
        apex = g.dot(g.vec(surf.Apex), d)
        narrows_outward = apex > t_rim + RIM_TOL if at_high_end else apex < t_rim - RIM_TOL
        return "closed" if narrows_outward else "open"
    return "unknown"


def _tip_beyond(shape: Any, chain: list[_Cut], d: g.Vec3, t_rim: float, at_high_end: bool) -> float:
    """How far a drill point (a cone that narrows outward) reaches past the closed rim at ``t_rim``: its height, ``0.0``
    for a flat bottom. The centre line of a blind hole goes as far past the tip as past the entry face."""
    radius = chain[0].radius
    members = [c.face for c in chain]
    for cut in chain:
        for edge in cut.face.Edges:
            if not _is_rim_edge(cut, edge, d, radius, t_rim):
                continue
            for other in shape.ancestorsOfType(edge, Part.Face):
                if any(other.isSame(m) for m in members) or g.surface_kind(other) != "cone":
                    continue
                surf = other.Surface
                if abs(g.dot(g.unit(g.vec(surf.Axis)), d)) < PARALLEL:
                    continue
                apex = g.dot(g.vec(surf.Apex), d)
                beyond = apex - t_rim if at_high_end else t_rim - apex
                if beyond > RIM_TOL:
                    return float(beyond)
    return 0.0


def _countersink_at(shape: Any, chain: list[_Cut], d: g.Vec3, t_rim: float) -> tuple[float, float] | None:
    """``(diameter, included angle in degrees)`` of the cone that widens outward from the rim at ``t_rim``:
    a countersink.
    A drill point (a cone that narrows outward) has no larger circle and is not one."""
    radius = chain[0].radius
    members = [c.face for c in chain]
    for cut in chain:
        for edge in cut.face.Edges:
            if not _is_rim_edge(cut, edge, d, radius, t_rim):
                continue
            for other in shape.ancestorsOfType(edge, Part.Face):
                if any(other.isSame(m) for m in members) or g.surface_kind(other) != "cone":
                    continue
                surf = other.Surface
                if abs(g.dot(g.unit(g.vec(surf.Axis)), d)) < PARALLEL:
                    continue
                circles = [e.Curve.Radius for e in other.Edges if type(e.Curve).__name__ == "Circle"]
                if circles and max(circles) > radius + RIM_TOL:
                    return 2.0 * max(circles), math.degrees(2.0 * abs(surf.SemiAngle))
    return None


def _describe(body: reg.BodyRec, shape: Any, chain: list[_Cut]) -> dict[str, Any]:
    d = chain[0].direction
    t0, t1 = min(c.t0 for c in chain), max(c.t1 for c in chain)
    low = _rim_state(shape, chain, d, t0, at_high_end=False)
    high = _rim_state(shape, chain, d, t1, at_high_end=True)
    sink = _countersink_at(shape, chain, d, t0) or _countersink_at(shape, chain, d, t1)
    through: bool | None = None
    tip = 0.0
    start, direction = t0, d
    if low == "open" and high == "open":
        through = True
    elif {low, high} == {"open", "closed"}:
        through = False
        if high == "open":  # blind: the axis runs from the open end into the material
            start, direction = t1, g.scale(d, -1.0)
            tip = _tip_beyond(shape, chain, d, t0, at_high_end=False)
        else:
            tip = _tip_beyond(shape, chain, d, t1, at_high_end=True)
    anchor = chain[0].anchor
    origin = g.add(anchor, g.scale(d, start))
    recs = sorted({c.rec.id: c.rec for c in chain}.values(), key=lambda r: r.index)
    deviations = [c.deviation for c in chain if c.deviation is not None]
    return {
        "body": {"id": body.id},
        "diameter_mm": 2.0 * chain[0].radius,
        "axis": g.axis_dict(origin, direction),
        "depth_mm": t1 - t0,
        "tip_mm": tip,  # the height of the drill point of a blind hole (the cone past ``depth_mm``); 0: flat bottom
        "through": through,
        "faces": [{"id": r.id} for r in recs],
        "arc_deg": min(math.degrees(_arc_of(chain)), 360.0),
        "fitted": bool(deviations),
        "fit_deviation_mm": max(deviations) if deviations else None,
        "countersink_diameter_mm": sink[0] if sink else None,
        "countersink_angle_deg": sink[1] if sink else None,
    }


def _body_holes(doc: reg.DocumentRec, body: reg.BodyRec) -> list[dict[str, Any]]:
    shape = reg.world_shape(doc, body)
    if shape.isNull() or not shape.Solids:
        return []
    faces = shape.Faces
    cuts = []
    for face_id, index in body.live_faces():
        if index >= len(faces):
            continue
        rec = _FaceRef(face_id, index)
        face = faces[index]
        kind = g.surface_kind(face)
        if kind == "cylinder":
            if _is_concave(face):
                cuts.append(_analytic_cut(rec, face))
        elif kind == "other":
            cut = _fitted_cut(rec, face)
            if cut is not None:
                cuts.append(cut)
    holes = []
    for chain in _holes_of_cuts(cuts):
        if _arc_of(chain) >= MIN_ARC:
            holes.append(_describe(body, shape, chain))
    return holes


def list_holes(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    wanted = params.get("body")
    warnings: list[str] = []
    if wanted is not None:
        bodies = [reg.resolve_body(doc, reg.handle_id(wanted, op, "body"), op)]
    else:
        bodies = doc.live_bodies()
    holes: list[dict[str, Any]] = []
    for body in bodies:
        shape = reg.world_shape(doc, body)
        if not shape.isNull() and not shape.Solids:
            warnings.append(f"{body.id} has no solid (surfaces only): holes need material, so none is listed")
            continue
        holes.extend(_body_holes(doc, body))
    if not bodies and doc.components:
        warnings.append("this document is an assembly: switch to a part document to list its holes")
    return {"holes": holes, "warnings": warnings}
