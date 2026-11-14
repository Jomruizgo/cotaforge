"""``draft_analysis``: the draft of every face of the bodies of a part against a pull direction, read from the B-rep.

What is exact and what is sampled (the result says it too):

* a PLANE has one normal: its lean from the pull is exact;
* any other face (cylinder, cone, sphere, torus, B-spline) is read on a grid of its own (u, v) parameters, ``GRID_U`` by
  ``GRID_V`` steps over the parameter range (only the points inside the face's trimming), and the three least-leaning
  points are refined by a pattern search that halves its step ``REFINE_STEPS`` times. A cylinder or a cone has a
  line of zero lean when it turns away from the pull, and the refinement lands on it within a fraction of a degree,
  where a plain grid (7.5 degrees apart) would miss it;
* the shadow test casts a ray from up to ``MAX_SHADOW_SAMPLES`` points of each face (``SHADOW_OFFSET`` mm off it) along
  the pull, in the sense the mould half that forms the face moves, against the triangles of the whole body
  (tessellated to ``DEFLECTION`` mm). A face with a blocked ray cannot be released along the pull: an undercut.
"""

import math
from typing import Any

import draft_math as dm
import geometry as g
import gui
import registry as reg
from protocol import invalid_request, operation_id_of

GRID_U = 48
GRID_V = 6
REFINE_STEPS = 16
REFINE_BEST = 3
MAX_SHADOW_SAMPLES = 36
DEFLECTION = 0.02  # mm: the tessellation the shadow test casts against
SHADOW_OFFSET = 0.05  # mm: more than twice the deflection, so a ray never starts inside the chord of its own face

PAINT = {  # RGBA (FreeCAD: alpha is transparency, 0 opaque)
    "ok": (0.2, 0.7, 0.25, 0.0),
    "low": (0.95, 0.8, 0.1, 0.0),
    "zero": (0.85, 0.15, 0.15, 0.0),
    "undercut": (0.85, 0.15, 0.15, 0.0),
}

METHOD = (
    f"planes: exact from the face normal; other faces: a {GRID_U}x{GRID_V} grid over the face's own (u, v) parameters "
    f"(points inside the trimming only), the {REFINE_BEST} least-leaning points refined; undercut: a ray from up to "
    f"{MAX_SHADOW_SAMPLES} points per face along the pull, in the sense that releases that face, against the model "
    f"tessellated to {DEFLECTION} mm"
)


def _outward(face: Any, u: float, v: float) -> g.Vec3:
    return g.unit(g.vec(face.normalAt(u, v)))


def _plane_samples(face: Any) -> tuple[g.Vec3, list[tuple[g.Vec3, g.Vec3]]]:
    """The exact outward normal of a plane and points on it (centres of its triangles), for the shadow test."""
    u, v = face.Surface.parameter(face.CenterOfMass)
    normal = _outward(face, u, v)
    vertices, triangles = face.tessellate(DEFLECTION)
    points = [g.vec((vertices[i] + vertices[j] + vertices[k]) * (1.0 / 3.0)) for i, j, k in triangles]
    if not points:
        points = [g.vec(face.CenterOfMass)]
    stride = max(1, len(points) // MAX_SHADOW_SAMPLES)
    return normal, [(p, normal) for p in points[::stride]]


def _lean_at(face: Any, u: float, v: float, pull: g.Vec3) -> float:
    return abs(dm.dot(_outward(face, u, v), pull))


def _refine(face: Any, u: float, v: float, du: float, dv: float, pull: g.Vec3) -> tuple[float, float, float]:
    """A pattern search for the least ``|normal . pull|`` near ``(u, v)`` inside the face: ``(u, v, value)``."""
    best = _lean_at(face, u, v, pull)
    for _ in range(REFINE_STEPS):
        moved = False
        for cu, cv in ((du, 0.0), (-du, 0.0), (0.0, dv), (0.0, -dv)):
            nu, nv = u + cu, v + cv
            if face.isPartOfDomain(nu, nv):
                value = _lean_at(face, nu, nv, pull)
                if value < best:
                    best, u, v, moved = value, nu, nv, True
        if not moved:
            du, dv = du / 2.0, dv / 2.0
    return u, v, best


def _curved_samples(face: Any, pull: g.Vec3) -> tuple[float, list[tuple[g.Vec3, g.Vec3]], int]:
    """``(least |normal . pull|, points for the shadow test, points read)`` of a face that is not a plane."""
    umin, umax, vmin, vmax = face.ParameterRange
    du, dv = (umax - umin) / GRID_U, (vmax - vmin) / GRID_V
    read: list[tuple[float, float, float]] = []
    for i in range(GRID_U + 1):
        for j in range(GRID_V + 1):
            u, v = umin + i * du, vmin + j * dv
            if face.isPartOfDomain(u, v):
                read.append((_lean_at(face, u, v, pull), u, v))
    if not read:  # a sliver the grid never lands in: read it at its centre
        u, v = face.Surface.parameter(face.CenterOfMass)
        read.append((_lean_at(face, u, v, pull), u, v))
    read.sort()
    refined = [_refine(face, u, v, du, dv, pull) for _, u, v in read[:REFINE_BEST]]
    least = min([r[2] for r in refined] + [read[0][0]])
    stride = max(1, len(read) // MAX_SHADOW_SAMPLES)
    spots = [(u, v) for _, u, v in read[::stride]] + [(u, v) for u, v, _ in refined]
    samples = [(g.vec(face.valueAt(u, v)), _outward(face, u, v)) for u, v in spots]
    return least, samples, len(read) + REFINE_STEPS * 4 * len(refined)


def _paint(doc: Any, graded: list[tuple[Any, str]]) -> bool:
    """Colour the graded faces ((body, face id, index), status rows) on screen, until something else is shown."""
    import queries  # noqa: PLC0415 - queries imports a great deal; only a painted analysis needs it

    if not gui.available():
        return False
    targets: list[tuple[Any, int, Any]] = []
    for (body, _face_id, index), status in graded:
        obj_name = queries._owner_object_name(doc, body)  # noqa: SLF001 - the object that owns the body
        obj = doc.fc_doc.getObject(obj_name) if obj_name is not None else None
        if obj is not None and obj.TypeId == "PartDesign::Body" and obj.Tip is not None:
            obj = obj.Tip  # the window draws the Tip; the body only holds it
        if obj is not None:
            targets.append((obj, index, PAINT[status]))
    return gui.paint_faces(doc.fc_doc, targets)


def draft_analysis(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    try:
        pull = dm.unit(g.vec(params["pull"]))
    except (KeyError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise invalid_request(f"draft_analysis: invalid pull direction ({exc})", op) from exc
    minimum = float(params.get("min_draft_deg", 1.0))
    wanted = params.get("body")
    bodies = [reg.resolve_body(doc, reg.handle_id(wanted, op, "body"), op)] if wanted is not None else doc.live_bodies()
    rows: list[dict[str, Any]] = []
    graded: list[tuple[Any, str]] = []
    warnings: list[str] = []
    for body in bodies:
        shape = reg.world_shape(doc, body)
        if shape.isNull() or not shape.Solids:
            warnings.append(f"{body.id} has no solid (surfaces only): a draft needs material, so none is measured")
            continue
        vertices, triangles = shape.tessellate(DEFLECTION)
        caster = dm.ShadowCaster([g.vec(v) for v in vertices], [tuple(t) for t in triangles], pull)
        faces = shape.Faces
        for face_id, index in body.live_faces():
            if index >= len(faces):
                continue
            face = faces[index]
            kind = g.surface_kind(face)
            if kind == "plane":
                normal, samples = _plane_samples(face)
                draft, count, exact = dm.lean_deg(normal, pull), 1, True
            else:
                least, samples, count = _curved_samples(face, pull)
                draft, exact = math.degrees(math.asin(min(1.0, least))), False
            share = dm.shadowed_share(caster, samples, SHADOW_OFFSET)
            status = dm.classify(draft, share > 0.0, minimum)
            rows.append(
                {
                    "face": {"id": face_id},
                    "body": {"id": body.id},
                    "surface": kind,
                    "area_mm2": float(face.Area),
                    "draft_deg": draft,
                    "shadowed": share,
                    "samples": count,
                    "exact": exact,
                    "status": status,
                }
            )
            graded.append(((body, face_id, index), status))
    painted = _paint(doc, graded) if params.get("paint") and graded else False
    if params.get("paint") and graded and not painted:
        warnings.append("the faces were not painted: this CAD has no visible window")
    return {"faces": rows, "method": METHOD, "shadow_tested": True, "painted": painted, "warnings": warnings}
