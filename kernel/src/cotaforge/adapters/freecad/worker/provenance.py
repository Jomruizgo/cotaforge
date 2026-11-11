"""Provenance engine: signatures, tolerant matching, face/edge geometry, convexity probe.

No FreeCAD element map is used; provenance is the diff between the body shape before and after an operation,
computed by ``registry.refresh`` with the signatures defined here.
"""

import math
from typing import Any

import geometry as g
import Part

PLANE_DOT = 1.0 - 1e-6
ANCHOR_TOL = 1e-3
RADIUS_TOL = 1e-4
EDGE_TOL = 1e-3


class FaceSig:
    __slots__ = ("surface", "direction", "anchor", "radius", "center")

    def __init__(
        self, surface: str, direction: g.Vec3 | None, anchor: g.Vec3 | None, radius: float | None, center: g.Vec3
    ) -> None:
        self.surface = surface
        self.direction = direction
        self.anchor = anchor
        self.radius = radius
        self.center = center  # CenterOfMass, used as a tie-breaker only


class EdgeSig:
    __slots__ = ("curve", "a", "b", "radius", "length")

    def __init__(self, curve: str, a: g.Vec3, b: g.Vec3, radius: float | None, length: float) -> None:
        self.curve = curve
        self.a = a
        self.b = b
        self.radius = radius
        self.length = length


def mid_params(face: Any) -> tuple[float, float]:
    u0, u1, v0, v1 = face.ParameterRange
    return (u0 + u1) / 2.0, (v0 + v1) / 2.0


def outward_normal(face: Any) -> g.Vec3:
    """``normalAt`` at the parameter midpoint already honours ``Orientation`` (verified); never negate it."""
    u, v = mid_params(face)
    return g.unit(g.vec(face.normalAt(u, v)))


def face_sig(face: Any) -> FaceSig:
    kind = g.surface_kind(face)
    com = g.vec(face.CenterOfMass)
    surf = face.Surface
    if kind == "plane":
        n = outward_normal(face)
        p = g.vec(surf.Position)
        anchor = g.sub(g.ORIGIN, g.scale(n, g.dot(g.sub(g.ORIGIN, p), n)))  # foot of the origin on the plane
        return FaceSig("plane", g.rounded(n, 6), g.rounded(anchor, 4), None, com)
    if kind in ("cylinder", "cone", "torus"):
        axis = g.canonical_direction(g.vec(surf.Axis))
        c = g.vec(surf.Center)
        anchor = g.sub(c, g.scale(axis, g.dot(c, axis)))  # axis point closest to the origin
        radius = float(getattr(surf, "Radius", 0.0)) if kind != "torus" else float(surf.MajorRadius)
        return FaceSig(kind, axis, g.rounded(anchor, 4), round(radius, 4), com)
    if kind == "sphere":
        return FaceSig("sphere", None, g.rounded(g.vec(surf.Center), 4), round(float(surf.Radius), 4), com)
    return FaceSig("other", None, g.rounded(com, 4), None, com)


def sig_match(a: FaceSig, b: FaceSig) -> bool:
    if a.surface != b.surface:
        return False
    if a.surface == "plane":
        assert a.direction is not None and b.direction is not None and a.anchor is not None and b.anchor is not None
        return g.dot(a.direction, b.direction) > PLANE_DOT and g.dist(a.anchor, b.anchor) <= ANCHOR_TOL
    if a.surface in ("cylinder", "cone", "torus"):
        assert a.direction is not None and b.direction is not None and a.anchor is not None and b.anchor is not None
        if abs(g.dot(a.direction, b.direction)) <= PLANE_DOT:
            return False
        if g.dist(a.anchor, b.anchor) > ANCHOR_TOL:
            return False
        return abs((a.radius or 0.0) - (b.radius or 0.0)) <= RADIUS_TOL
    if a.surface == "sphere":
        assert a.anchor is not None and b.anchor is not None
        return g.dist(a.anchor, b.anchor) <= ANCHOR_TOL and abs((a.radius or 0.0) - (b.radius or 0.0)) <= RADIUS_TOL
    assert a.anchor is not None and b.anchor is not None
    return g.dist(a.anchor, b.anchor) <= ANCHOR_TOL


def edge_ends(edge: Any) -> tuple[g.Vec3, g.Vec3]:
    """Where the edge starts and ends, read from its curve: ``firstVertex()``/``lastVertex()`` would each make a
    sub-shape wrapper (about 6 KB the CAD never gives back), which is gigabytes over the edges of a big file."""
    return g.vec(edge.valueAt(edge.FirstParameter)), g.vec(edge.valueAt(edge.LastParameter))


def edge_sig(edge: Any) -> EdgeSig:
    kind = g.curve_kind(edge)
    if kind == "circle":
        c = g.vec(edge.Curve.Center)
        return EdgeSig(
            kind,
            g.rounded(c, 3),
            g.canonical_direction(g.vec(edge.Curve.Axis)),
            round(edge.Curve.Radius, 4),
            round(edge.Length, 3),
        )
    p0, p1 = edge_ends(edge)
    a, b = sorted((g.rounded(p0, 3), g.rounded(p1, 3)))
    radius = round(float(edge.Curve.Radius), 4) if kind == "arc" else None
    return EdgeSig(kind, a, b, radius, round(edge.Length, 3))


def edge_sig_match(a: EdgeSig, b: EdgeSig) -> bool:
    if a.curve != b.curve:
        return False
    if a.curve == "circle":
        if g.dist(a.a, b.a) > EDGE_TOL or abs(g.dot(a.b, b.b)) <= PLANE_DOT:
            return False
        return abs((a.radius or 0.0) - (b.radius or 0.0)) <= RADIUS_TOL
    if g.dist(a.a, b.a) > EDGE_TOL or g.dist(a.b, b.b) > EDGE_TOL:
        return False
    if a.curve == "arc":
        return abs((a.radius or 0.0) - (b.radius or 0.0)) <= RADIUS_TOL
    return True


# --- face / edge geometry for the descriptors ------------------------------------------------------------------


def cylinder_axis(face: Any) -> tuple[g.Vec3, g.Vec3, float]:
    """(centre at the face's axial midpoint, axis direction as FreeCAD reports it, radius)."""
    surf = face.Surface
    _, _, v0, v1 = face.ParameterRange
    axis = g.unit(g.vec(surf.Axis))
    center = g.add(g.vec(surf.Center), g.scale(axis, (v0 + v1) / 2.0))
    return center, axis, float(surf.Radius)


def face_geometry(face: Any, axis_dir: g.Vec3 | None = None) -> dict[str, Any]:
    """Surface-dependent part of ``FaceInfo`` (surface, normal, axis, center, radius, area, bbox)."""
    kind = g.surface_kind(face)
    out: dict[str, Any] = {
        "surface": kind,
        "normal": None,
        "axis": None,
        "center": g.clean(g.vec(face.CenterOfMass)),
        "radius": None,
        "area_mm2": float(face.Area),
        "bbox": g.shape_bbox(face),
    }
    surf = face.Surface
    if kind == "plane":
        out["normal"] = g.clean(outward_normal(face))
    elif kind == "cylinder":
        center, axis, radius = cylinder_axis(face)
        if axis_dir is not None and abs(g.dot(axis, axis_dir)) > PLANE_DOT:
            axis = g.unit(axis_dir)
        out["axis"] = g.axis_dict(center, axis)
        out["center"] = g.clean(center)
        out["radius"] = radius
    elif kind in ("cone", "torus"):
        axis = g.unit(g.vec(surf.Axis))
        if axis_dir is not None and abs(g.dot(axis, axis_dir)) > PLANE_DOT:
            axis = g.unit(axis_dir)
        out["axis"] = g.axis_dict(g.vec(surf.Center), axis)
        out["radius"] = float(surf.MajorRadius) if kind == "torus" else None
        if kind == "torus":
            out["minor_radius"] = float(surf.MinorRadius)
    elif kind == "sphere":
        out["radius"] = float(surf.Radius)
    return out


def _tangent_into_face(face: Any, m: g.Vec3, e_dir: g.Vec3) -> g.Vec3 | None:
    """Unit direction from the edge midpoint into ``face`` (perpendicular to the edge, in the tangent plane)."""
    u, v = face.Surface.parameter(g.fcvec(m))
    n = g.unit(g.vec(face.normalAt(u, v)))
    t = g.cross(n, e_dir)
    if g.length(t) < 1e-9:
        return None
    t = g.unit(t)
    for cand in (t, g.scale(t, -1.0)):
        p = g.add(m, g.scale(cand, 0.01))
        if face.isInside(g.fcvec(p), 1e-4, True):
            return cand
    return None


def edge_normal_of_face(face: Any, m: g.Vec3) -> g.Vec3:
    u, v = face.Surface.parameter(g.fcvec(m))
    return g.unit(g.vec(face.normalAt(u, v)))


def edge_geometry(edge: Any, faces: list[Any], solid: Any) -> dict[str, Any]:
    """Curve-dependent part of ``EdgeInfo`` plus ``convex`` / ``dihedral_deg`` from the probe."""
    kind = g.curve_kind(edge)
    out: dict[str, Any] = {
        "curve": kind,
        "start": None,
        "end": None,
        "center": None,
        "axis": None,
        "radius": None,
        "length_mm": float(edge.Length),
        "bbox": g.shape_bbox(edge),
        "convex": None,
        "dihedral_deg": None,
    }
    if kind in ("line", "arc", "other"):
        out["start"] = g.clean(g.vec(edge.firstVertex().Point))
        out["end"] = g.clean(g.vec(edge.lastVertex().Point))
    if kind in ("circle", "arc"):
        c = edge.Curve
        out["center"] = g.clean(g.vec(c.Center))
        out["axis"] = g.axis_dict(g.vec(c.Center), g.vec(c.Axis))
        out["radius"] = float(c.Radius)
    if len(faces) == 2 and solid is not None and not solid.isNull():
        convex, dihedral = convexity(edge, faces[0], faces[1], solid)
        out["convex"] = convex
        out["dihedral_deg"] = dihedral
    return out


def convexity(edge: Any, fa: Any, fb: Any, solid: Any) -> tuple[bool | None, float | None]:
    tm = (edge.FirstParameter + edge.LastParameter) / 2.0
    m = g.vec(edge.valueAt(tm))
    try:
        e_dir = g.unit(g.vec(edge.tangentAt(tm)))
    except Exception:  # noqa: BLE001 - degenerate edges
        return None, None
    n_a = edge_normal_of_face(fa, m)
    n_b = edge_normal_of_face(fb, m)
    dihedral = math.degrees(math.acos(max(-1.0, min(1.0, g.dot(n_a, n_b)))))
    t_a = _tangent_into_face(fa, m, e_dir)
    t_b = _tangent_into_face(fb, m, e_dir)
    if t_a is None or t_b is None:
        return None, dihedral
    t = g.add(t_a, t_b)
    if g.length(t) < 1e-3:
        return False, 0.0  # tangent-continuous edge (fillet to plane): fake convention
    probe = g.add(m, g.scale(g.unit(t), 0.01))
    try:
        inside = bool(solid.isInside(g.fcvec(probe), 1e-7, True))
    except Exception:  # noqa: BLE001
        return None, dihedral
    return inside, dihedral


def make_face_from_wire(wire: Any) -> Any:
    return Part.Face(wire)
