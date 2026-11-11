"""Pure vector math, the sketch-frame table and FreeCAD <-> contract conversions.

Everything reported to the client is a plain ``list``/``dict`` of floats so that ``json.dumps`` works and the
client's pydantic models validate it.
"""

import math
from typing import Any

import FreeCAD as App
import Part
from FreeCAD import Matrix, Placement, Rotation, Vector

Vec3 = tuple[float, float, float]

ORIGIN: Vec3 = (0.0, 0.0, 0.0)
X: Vec3 = (1.0, 0.0, 0.0)
Y: Vec3 = (0.0, 1.0, 0.0)
Z: Vec3 = (0.0, 0.0, 1.0)


# --- vector algebra ---------------------------------------------------------------------------------------


def vec(v: Any) -> Vec3:
    """Any FreeCAD ``Vector`` / sequence -> tuple of floats."""
    if isinstance(v, Vector):
        return (float(v.x), float(v.y), float(v.z))
    return (float(v[0]), float(v[1]), float(v[2]))


def fcvec(v: Vec3) -> Vector:
    return Vector(v[0], v[1], v[2])


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def dist(a: Vec3, b: Vec3) -> float:
    return length(sub(a, b))


def unit(a: Vec3) -> Vec3:
    n = length(a)
    if n < 1e-12:
        raise ValueError("cannot normalise a zero-length vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def perpendicular_part(v: Vec3, direction: Vec3) -> Vec3:
    d = unit(direction)
    return sub(v, scale(d, dot(v, d)))


def canonical_direction(d: Vec3, eps: float = 1e-9) -> Vec3:
    """Unit vector with the first component beyond ``eps`` made positive (either-sense axes)."""
    u = unit(d)
    for c in u:
        if abs(c) > eps:
            return u if c > 0 else scale(u, -1.0)
    return u


def rounded(v: Vec3, digits: int) -> Vec3:
    return (round(v[0], digits), round(v[1], digits), round(v[2], digits))


def clean(v: Vec3, eps: float = 1e-12) -> list[float]:
    """JSON form of a vector with numerical dust removed (-0.0, 1e-17)."""
    return [0.0 if abs(c) < eps else float(c) for c in v]


# --- frames -------------------------------------------------------------------------------------------------

PLANE_FRAMES: dict[str, tuple[Vec3, Vec3, Vec3]] = {
    "XY": (X, Y, Z),
    "YZ": (Y, Z, X),
    "XZ": (X, scale(Z, -1.0), Y),
}


def frame_for_plane(name: str) -> dict[str, Any]:
    u, v, n = PLANE_FRAMES[name]
    return {"origin": list(ORIGIN), "u": list(u), "v": list(v), "n": list(n)}


def frame_for_face(center: Vec3, normal: Vec3) -> dict[str, Any]:
    """The planar-face row of the sketch-frame table."""
    n = unit(normal)
    x_dot_n = dot(X, n)
    if abs(x_dot_n) < 0.999:
        u = unit(sub(X, scale(n, x_dot_n)))
    else:
        y_dot_n = dot(Y, n)
        u = unit(sub(Y, scale(n, y_dot_n)))
    v = cross(n, u)
    return {"origin": clean(center), "u": clean(u), "v": clean(v), "n": clean(n)}


def canonical_axis_direction(direction: Vec3) -> Vec3:
    """``direction`` with its sense fixed: the first component in the order Z, Y, X that is not zero (|c| > 1e-9) is
    positive (the worker's copy of ``contract.geometry.canonical_axis_direction``)."""
    for index in (2, 1, 0):
        if abs(direction[index]) > 1e-9:
            return direction if direction[index] > 0 else scale(direction, -1.0)
    return direction


def radial_footprint(
    entry: dict[str, Any], axis_dir: Vec3, cylinder_radius: float, hole_radius: float, count: int = 32
) -> list[tuple[Vec3, float]]:
    """Points where the wall of a radial hole of ``hole_radius`` meets the cylinder, with each point's axial offset from
    the entry: the hole circle seen from the axis. At lateral offset ``y = r sin t`` and axial offset ``z = r cos t``
    the point is ``axis_point + z axis + y tau + sqrt(R^2 - y^2) outward`` (``tau = axis x outward``)."""
    axis = canonical_axis_direction(unit(axis_dir))
    outward = vec(entry["outward"])
    axis_point = vec(entry["axis_point"])
    tau = unit(cross(axis, outward))
    points: list[tuple[Vec3, float]] = []
    for i in range(count):
        t = 2.0 * math.pi * i / count
        y, z = hole_radius * math.sin(t), hole_radius * math.cos(t)
        depth = math.sqrt(max(0.0, cylinder_radius * cylinder_radius - y * y))
        point = add(axis_point, add(scale(axis, z), add(scale(tau, y), scale(outward, depth))))
        points.append((point, z))
    return points


def radial_entry(
    center: Vec3, axis_origin: Vec3, axis_dir: Vec3, radius: float, position: tuple[float, float] | list[float]
) -> dict[str, Any]:
    """Where a hole at ``position = (axial_mm, angle_deg)`` enters a cylindrical face: ``axis_point`` (on the cylinder
    axis), ``outward`` (unit radial) and ``surface_point``. The worker's copy of ``contract.geometry.radial_entry`` (it
    imports nothing from cotaforge; ``tests/data/radial_entry_cases.json`` pins the two together): the axis is made
    canonical first (its first significant component in the order Z, Y, X positive: the sense a face reports follows how
    it was built), angle 0 is the ``u`` of ``frame_for_face(centre, axis)``, positive turns towards ``v = axis x u``,
    and a ``center`` off the axis is projected onto it."""
    direction = canonical_axis_direction(unit(axis_dir))
    centre = add(axis_origin, scale(direction, dot(sub(center, axis_origin), direction)))
    frame = frame_for_face(centre, direction)
    u, v = vec(frame["u"]), vec(frame["v"])
    axial, angle_deg = float(position[0]), math.radians(float(position[1]))
    outward = add(scale(u, math.cos(angle_deg)), scale(v, math.sin(angle_deg)))
    axis_point = add(centre, scale(direction, axial))
    return {
        "axis_point": clean(axis_point),
        "outward": clean(outward),
        "surface_point": clean(add(axis_point, scale(outward, float(radius)))),
    }


def radial_sketch_frame(entry: dict[str, Any], axis_dir: Vec3) -> dict[str, Any]:
    """The sketch frame of a radial hole: origin on the surface, ``n`` the outward radial (the hole runs along ``-n``,
    like a hole on a planar face), ``u`` the cylinder axis and ``v = n x u`` -- the plane tangent to the cylinder."""
    n = unit(vec(entry["outward"]))
    u = unit(axis_dir)
    return {"origin": list(entry["surface_point"]), "u": clean(u), "v": clean(cross(n, u)), "n": clean(n)}


def frame_to_world(frame: dict[str, Any], u: float, v: float) -> Vec3:
    o = vec(frame["origin"])
    return add(o, add(scale(vec(frame["u"]), u), scale(vec(frame["v"]), v)))


def rotation_from_frame(u: Vec3, v: Vec3, n: Vec3) -> Rotation:
    """Rotation whose columns are the frame axes (verified for XY/YZ/XZ and a generic normal)."""
    return Rotation(Matrix(u[0], v[0], n[0], 0, u[1], v[1], n[1], 0, u[2], v[2], n[2], 0, 0, 0, 0, 1))


def frame_placement(frame: dict[str, Any]) -> Placement:
    return Placement(
        fcvec(vec(frame["origin"])), rotation_from_frame(vec(frame["u"]), vec(frame["v"]), vec(frame["n"]))
    )


FRAME_TOL = 1e-6
"""Largest deviation from an orthonormal, right-handed ``(u, v, n)`` a datum plane frame may have."""


def realise_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Realise a datum plane pose through the same ``Placement`` a sketch on it gets, and read it back.

    ``frame`` must be orthonormal and right-handed (``v = n x u``, the contract's convention): a left-handed
    or skewed matrix is not a rotation and would silently become a different pose. Raises ``ValueError``
    naming the violated property (``KeyError`` / ``IndexError`` / ``TypeError`` for a malformed frame).
    The frame returned is what FreeCAD's ``Placement`` reproduces, so a mismatch between request and result
    would be a real (and level-2 visible) failure, not an echo.
    """
    origin, u, v, n = (vec(frame[key]) for key in ("origin", "u", "v", "n"))
    if not all(math.isfinite(c) for part in (origin, u, v, n) for c in part):
        raise ValueError("frame contains non-finite numbers")
    for name, axis in (("u", u), ("v", v), ("n", n)):
        if abs(length(axis) - 1.0) > FRAME_TOL:
            raise ValueError(f"frame.{name} is not a unit vector (length {length(axis):.6g})")
    if max(abs(dot(u, v)), abs(dot(u, n)), abs(dot(v, n))) > FRAME_TOL:
        raise ValueError("frame axes u, v, n are not mutually orthogonal")
    if length(sub(cross(n, u), v)) > FRAME_TOL:
        raise ValueError("frame is left-handed: v must equal n x u")
    rotation = frame_placement({"origin": origin, "u": u, "v": v, "n": n}).Rotation
    return {
        "origin": clean(origin),
        "u": clean(vec(rotation.multVec(fcvec(X)))),
        "v": clean(vec(rotation.multVec(fcvec(Y)))),
        "n": clean(vec(rotation.multVec(fcvec(Z)))),
    }


def rotation_between(a: Vec3, b: Vec3) -> Rotation:
    """Rotation taking direction ``a`` onto direction ``b`` (FreeCAD two-vector constructor)."""
    return Rotation(fcvec(a), fcvec(b))


# --- Transform (contract) <-> Placement (FreeCAD) --------------------------------------------------------------


def placement_from_transform(transform: dict[str, Any]) -> Placement:
    o = transform.get("origin", ORIGIN)
    r = transform.get("rotation", ((1, 0, 0), (0, 1, 0), (0, 0, 1)))
    m = Matrix(
        r[0][0],
        r[0][1],
        r[0][2],
        o[0],
        r[1][0],
        r[1][1],
        r[1][2],
        o[1],
        r[2][0],
        r[2][1],
        r[2][2],
        o[2],
        0,
        0,
        0,
        1,
    )
    return Placement(m)


def transform_from_placement(placement: Placement) -> dict[str, Any]:
    m = placement.toMatrix()
    rows = [[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]]
    return {"origin": clean(vec(placement.Base)), "rotation": [clean(tuple(row)) for row in rows]}


def is_identity_placement(placement: Placement, tol: float = 1e-12) -> bool:
    m = placement.toMatrix()
    ident = Matrix()
    return all(abs(m.A[i] - ident.A[i]) <= tol for i in range(16))


def reflect_point(p: Vec3, origin: Vec3, normal: Vec3) -> Vec3:
    """The mirror image of ``p`` across the plane through ``origin`` with ``normal`` (any length). The worker's copy of
    ``verification.formulas.reflect_point`` (the worker imports nothing from ``cotaforge``; a unit test keeps the two
    equal)."""
    n = unit(normal)
    return sub(p, scale(n, 2.0 * dot(sub(p, origin), n)))


def reflect_direction(d: Vec3, normal: Vec3) -> Vec3:
    """``d`` reflected across a plane with ``normal`` (the worker copy of ``formulas.reflect_direction``)."""
    n = unit(normal)
    return sub(d, scale(n, 2.0 * dot(d, n)))


def apply_placement(placement: Placement, p: Vec3) -> Vec3:
    return vec(placement.multVec(fcvec(p)))


def rotate_direction(placement: Placement, d: Vec3) -> Vec3:
    return vec(placement.Rotation.multVec(fcvec(d)))


# --- bounding boxes -------------------------------------------------------------------------------------------


def exact_box(shape: Any) -> Any:
    """The axis-aligned box of ``shape`` computed from its exact B-rep, never from a display mesh.

    ``Shape.BoundBox`` (OCC ``BRepBndLib::Add``) silently uses the shape's triangulation whenever one is
    attached, and only the GUI tessellates (to draw the object): on the visible worker a D40 rod came back
    as ``(-19.941, -19.985)..(20, 19.985)`` while the headless worker, with no mesh, answered the exact box.
    Without a mesh the same call falls back to a curve/surface control-point bound that is LOOSE for
    tori, sweeps and lofts. ``optimalBoundingBox(useTriangulation=False, useShapeTolerance=False)``
    (positional: FreeCAD 1.0.2 and 1.1.3 reject keywords) is tight and independent of mesh state, so the two
    workers agree. OCC may still pad a side by ``Precision::Confusion`` (1e-7 mm, e.g. 10.0000002 for a
    10 mm hole face): irrelevant to the verifier (``position_mm`` is 1e-2) but not bit-exact. A void shape
    (null/empty) makes it raise ``RuntimeError``; fall back to ``BoundBox``, which is then an invalid
    (``isValid() == False``) box.
    """
    try:
        return shape.optimalBoundingBox(False, False)
    except RuntimeError:
        return shape.BoundBox


def shape_bbox(shape: Any) -> dict[str, Any]:
    """:func:`exact_box` as the contract's ``{"min": [...], "max": [...]}``; a void shape is the zero box."""
    box = exact_box(shape)
    return bbox_dict(box) if box.isValid() else {"min": list(ZERO_BBOX["min"]), "max": list(ZERO_BBOX["max"])}


def bbox_dict(box: Any) -> dict[str, Any]:
    """FreeCAD ``BoundBox`` -> ``{"min": [...], "max": [...]}`` (rounded to 1e-9 mm: OCC boxes carry 1e-14 noise)."""
    lo = clean((round(box.XMin, 9), round(box.YMin, 9), round(box.ZMin, 9)))
    hi = clean((round(box.XMax, 9), round(box.YMax, 9), round(box.ZMax, 9)))
    return {"min": lo, "max": hi}


def bbox_union(a: dict[str, Any] | None, b: dict[str, Any]) -> dict[str, Any]:
    if a is None:
        return {"min": list(b["min"]), "max": list(b["max"])}
    return {
        "min": [min(a["min"][i], b["min"][i]) for i in range(3)],
        "max": [max(a["max"][i], b["max"][i]) for i in range(3)],
    }


ZERO_BBOX: dict[str, Any] = {"min": [0.0, 0.0, 0.0], "max": [0.0, 0.0, 0.0]}


def bbox_corners(box: dict[str, Any]) -> list[Vec3]:
    lo, hi = box["min"], box["max"]
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def bbox_contains_point(box: dict[str, Any], p: Vec3, tol: float = 1e-6) -> bool:
    return all(box["min"][i] - tol <= p[i] <= box["max"][i] + tol for i in range(3))


# --- sweep paths ------------------------------------------------------------------------------------------------

SWEEP_TOL = 1e-6


def _vector_source(p: Vec3) -> str:
    return f"Vector({p[0]!r}, {p[1]!r}, {p[2]!r})"


def sweep_wire(points: list[Vec3], corner_radius: float | None) -> tuple[Any, str]:
    """The spine of a sweep: a ``Part.Wire`` through the polyline, with one straight edge per segment (a mitred
    path: the corners are the wire's vertices) or, when ``corner_radius`` is set, lines shortened by the tangent
    length ``R tan(phi / 2)`` joined by a ``Part.Arc`` at every corner. Returns ``(wire, source)`` where ``source``
    is the expression that rebuilds the same wire in an exported macro. The fillet math is duplicated from
    ``verification/sweep.py`` on purpose (this worker imports nothing from cotaforge); ``check_sweep.py`` pins
    the path lengths against the same hand values as ``tests/unit/verification/test_sweep.py``.

    Raises ``ValueError`` (with the reason) for a path that cannot be built."""
    if len(points) < 2:
        raise ValueError("a path needs at least 2 points")
    dirs: list[Vec3] = []
    for i in range(len(points) - 1):
        delta = sub(points[i + 1], points[i])
        n = length(delta)
        if n <= SWEEP_TOL:
            raise ValueError(f"path points {i} and {i + 1} coincide")
        dirs.append(scale(delta, 1.0 / n))
    turns: list[float] = []
    for i in range(1, len(points) - 1):
        phi = math.atan2(length(cross(dirs[i - 1], dirs[i])), dot(dirs[i - 1], dirs[i]))
        if phi <= SWEEP_TOL:
            raise ValueError(f"path point {i} is collinear with its neighbours")
        if math.pi - phi <= SWEEP_TOL:
            raise ValueError(f"the path reverses on itself at point {i}")
        turns.append(phi)
    tangents = [0.0] * len(turns) if corner_radius is None else [corner_radius * math.tan(p / 2.0) for p in turns]
    for i in range(len(dirs)):
        t_in = tangents[i - 1] if i > 0 else 0.0
        t_out = tangents[i] if i < len(tangents) else 0.0
        if length(sub(points[i + 1], points[i])) - t_in - t_out <= SWEEP_TOL:
            raise ValueError(f"corner_radius_mm {corner_radius:g} does not fit path segment {i}")
    pieces: list[tuple[str, tuple[Vec3, ...]]] = []
    cursor = points[0]
    for i in range(1, len(points) - 1):
        if corner_radius is None:
            pieces.append(("line", (cursor, points[i])))
            cursor = points[i]
            continue
        t = tangents[i - 1]
        t1 = sub(points[i], scale(dirs[i - 1], t))
        t2 = add(points[i], scale(dirs[i], t))
        centre = add(points[i], scale(unit(sub(dirs[i], dirs[i - 1])), corner_radius / math.cos(turns[i - 1] / 2.0)))
        middle = add(centre, scale(unit(sub(points[i], centre)), corner_radius))
        pieces.append(("line", (cursor, t1)))
        pieces.append(("arc", (t1, middle, t2)))
        cursor = t2
    pieces.append(("line", (cursor, points[-1])))
    edges = []
    sources = []
    for kind, pts in pieces:
        if kind == "line":
            edges.append(Part.makeLine(fcvec(pts[0]), fcvec(pts[1])))
            sources.append(f"Part.makeLine({_vector_source(pts[0])}, {_vector_source(pts[1])})")
        else:
            edges.append(Part.Arc(fcvec(pts[0]), fcvec(pts[1]), fcvec(pts[2])).toShape())
            sources.append(
                f"Part.Arc({_vector_source(pts[0])}, {_vector_source(pts[1])}, {_vector_source(pts[2])}).toShape()"
            )
    return Part.Wire(edges), "Part.Wire([" + ", ".join(sources) + "])"


# --- misc ----------------------------------------------------------------------------------------------------


def axis_dict(origin: Vec3, direction: Vec3) -> dict[str, Any]:
    return {"origin": clean(origin), "direction": clean(unit(direction))}


def surface_kind(face: Any) -> str:
    name = type(face.Surface).__name__
    return {"Plane": "plane", "Cylinder": "cylinder", "Cone": "cone", "Sphere": "sphere", "Toroid": "torus"}.get(
        name, "other"
    )


def curve_kind(edge: Any) -> str:
    name = type(edge.Curve).__name__
    if name == "Line":
        return "line"
    if name == "Circle":
        return "circle" if edge.isClosed() else "arc"
    return "other"


def freecad_version() -> str:
    v = App.Version()
    return ".".join(str(x) for x in v[:3])


def shape_is_empty(shape: Any) -> bool:
    return shape is None or shape.isNull()


def make_compound(shapes: list[Any]) -> Any:
    if len(shapes) == 1:
        return shapes[0]
    return Part.makeCompound(shapes)
