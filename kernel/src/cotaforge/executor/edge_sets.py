"""Edge sets: ``EdgeSet`` (``ir.refs``) -> the concrete edges of a body that match its criteria.

Candidates are the edges shared by exactly two faces (what a chamfer or a fillet can take) of the body that the step
``from_op`` created or modified, or of the face ``where.on_face``. Resolution is deterministic (the survivors are sorted
by midpoint, then id) and reported: ``describe_edge`` gives one line per edge. A set that matches nothing raises a
``ResolutionError`` that says how many edges each criterion left.
"""

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

from cotaforge.contract import (
    AdapterError,
    AxisName,
    BodyHandle,
    CurveType,
    EdgeInfo,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FeatureRequest,
    ListHolesRequest,
    SurfaceType,
    Vec3,
)
from cotaforge.executor.errors import ResolutionError
from cotaforge.ir.refs import EdgeRef, EdgeSet, EdgeWhere, Range

if TYPE_CHECKING:
    from cotaforge.executor.resolver import Resolver

MAX_LISTED = 12
"""Edges described one by one in a report; the rest are counted."""

_AXES: dict[AxisName, Vec3] = {
    AxisName.X: (1.0, 0.0, 0.0),
    AxisName.Y: (0.0, 1.0, 0.0),
    AxisName.Z: (0.0, 0.0, 1.0),
}


# --- small vector helpers -------------------------------------------------------------------------------------------


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _midpoint(edge: EdgeInfo) -> Vec3:
    """Where the edge is: the midpoint of a line, the chord midpoint of an arc, the centre of a circle."""
    if edge.start is not None and edge.end is not None and edge.curve is not CurveType.circle:
        return tuple((s + e) / 2.0 for s, e in zip(edge.start, edge.end, strict=True))  # type: ignore[return-value]
    if edge.center is not None:
        return edge.center
    return edge.bbox.center()


def direction_of(edge: EdgeInfo) -> Vec3 | None:
    """Unit direction of a straight edge, sign-normalised (first non-zero component positive); ``None`` otherwise."""
    if edge.curve is not CurveType.line or edge.start is None or edge.end is None:
        return None
    vector = _sub(edge.end, edge.start)
    length = _norm(vector)
    if length == 0.0:
        return None
    unit = (vector[0] / length, vector[1] / length, vector[2] / length)
    for component in unit:
        if abs(component) > 1e-9:
            if component < 0:
                unit = (-unit[0], -unit[1], -unit[2])
            break
    return unit


def _distance_to_edge(edge: EdgeInfo, point: Vec3) -> float:
    """How far ``point`` is from the edge (a segment, a circle; any other curve: from its midpoint)."""
    if edge.curve is CurveType.line and edge.start is not None and edge.end is not None:
        segment = _sub(edge.end, edge.start)
        length2 = _dot(segment, segment)
        t = 0.0 if length2 == 0.0 else max(0.0, min(1.0, _dot(_sub(point, edge.start), segment) / length2))
        nearest = (
            edge.start[0] + t * segment[0],
            edge.start[1] + t * segment[1],
            edge.start[2] + t * segment[2],
        )
        return _norm(_sub(point, nearest))
    if edge.curve in (CurveType.circle, CurveType.arc) and edge.axis is not None and edge.radius is not None:
        # an arc is measured against its whole circle: a point past its ends counts as on it
        centre = edge.axis.origin if edge.center is None else edge.center
        direction = edge.axis.direction
        unit = _norm(direction) or 1.0
        axis = (direction[0] / unit, direction[1] / unit, direction[2] / unit)
        offset = _sub(point, centre)
        axial = _dot(offset, axis)
        radial = _norm((offset[0] - axial * axis[0], offset[1] - axial * axis[1], offset[2] - axial * axis[2]))
        return math.hypot(radial - edge.radius, axial)
    return _norm(_sub(point, _midpoint(edge)))


# --- report ---------------------------------------------------------------------------------------------------------


def describe_edge(edge: EdgeInfo) -> str:
    """``edge:12 line, midpoint (10, 0, 9.5), length 19 mm, direction (0, 0, 1), convex``."""
    from cotaforge.executor.resolver import _fmt, _fmt_vec  # noqa: PLC0415 - resolver imports this module

    place = "centre" if edge.curve is CurveType.circle else "midpoint"
    parts = [f"{edge.handle.id} {edge.curve.value}", f"{place} {_fmt_vec(_midpoint(edge))}"]
    if edge.radius is not None:
        parts.append(f"radius {_fmt(edge.radius)} mm")
    parts.append(f"length {_fmt(edge.length_mm)} mm")
    direction = direction_of(edge)
    if direction is not None:
        parts.append(f"direction {_fmt_vec(direction)}")
    elif edge.axis is not None:
        parts.append(f"axis {_fmt_vec(edge.axis.direction)}")
    if edge.convex is not None:
        parts.append("convex" if edge.convex else "concave")
    return ", ".join(parts)


def criteria_text(where: EdgeWhere) -> str:
    """The criteria of a set in a few words (what the report and an empty-set error say it asked for)."""
    parts: list[str] = []
    if where.on_face is not None:
        parts.append("on_face")
    if where.parallel_to is not None:
        parts.append(
            f"parallel_to {where.parallel_to.value if isinstance(where.parallel_to, AxisName) else list(where.parallel_to)}"
        )
    if where.length_mm is not None:
        parts.append(f"length_mm {_range_text(where.length_mm)}")
    if where.convex is not None:
        parts.append("convex" if where.convex else "concave")
    if where.planar is not None:
        parts.append("planar faces" if where.planar else "non-planar faces")
    if where.curve is not None:
        parts.append(f"curve {where.curve.value}")
    if where.exclude_holes:
        parts.append("exclude_holes")
    if where.except_:
        parts.append(f"except {len(where.except_)}")
    return ", ".join(parts) or "no criteria"


def _range_text(value: float | Range) -> str:
    if isinstance(value, Range):
        return f"[{value.min if value.min is not None else '-'}..{value.max if value.max is not None else '-'}]"
    return f"{value:g}"


def report(path: str, ref: EdgeSet, edges: list[EdgeInfo], *, limit: int = MAX_LISTED) -> tuple[str, ...]:
    lines = [f"{path}: {len(edges)} edge(s) ({criteria_text(ref.where)})"]
    lines.extend(f"  {describe_edge(e)}" for e in edges[:limit])
    if len(edges) > limit:
        lines.append(f"  ... {len(edges) - limit} more (raise limit)")
    return tuple(lines)


# --- candidates -----------------------------------------------------------------------------------------------------


def _id_key(handle_id: str) -> tuple[str, int, str]:
    kind, _, rest = handle_id.partition(":")
    return kind, int(rest) if rest.isdigit() else 0, rest


def _order_key(edge: EdgeInfo) -> tuple[tuple[float, ...], tuple[str, int, str]]:
    return tuple(round(c, 5) + 0.0 for c in _midpoint(edge)), _id_key(edge.handle.id)


def _body_of_step(resolver: "Resolver", ref: EdgeSet, op_id: str, path: str) -> BodyHandle:
    assert ref.from_op is not None  # EdgeSet's validator: from_op or where.on_face
    feature = resolver.ctx.feature_by_op.get(ref.from_op)
    if feature is None:
        msg = f"operation {ref.from_op!r} produced no feature to take edges from"
        raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
    bodies = resolver.adapter.bodies_of(FeatureRequest(operation_id=op_id, feature=feature)).bodies
    if len(bodies) != 1:
        found = ", ".join(b.handle.id for b in bodies) or "none"
        msg = (
            f"operation {ref.from_op!r} created or modified {len(bodies)} bodies ({found}); an edge set takes the edges "
            "of exactly one: name a step that touched one body, or use where.on_face"
        )
        raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=len(bodies), message=msg)
    return bodies[0].handle


def _faces_of_body(resolver: "Resolver", body: BodyHandle, op_id: str) -> dict[str, FaceInfo]:
    """Every live face of ``body``: what the steps in effect created or modified there (the same union ``inspect
    faces`` shows)."""
    faces: dict[str, FaceInfo] = {}
    seen_features: set[str] = set()
    for feature in resolver.ctx.feature_by_op.values():
        if feature.id in seen_features:
            continue
        seen_features.add(feature.id)
        try:
            found = resolver.adapter.faces_of(FeatureRequest(operation_id=op_id, feature=feature)).faces
        except AdapterError:
            continue  # a step with no faces to list (a sketch, a datum)
        for face in found:
            if face.body == body:
                faces.setdefault(face.handle.id, face)
    return faces


def _edges_of_faces(resolver: "Resolver", faces: dict[str, FaceInfo], op_id: str) -> list[EdgeInfo]:
    edges: dict[str, EdgeInfo] = {}
    for face in faces.values():
        for edge in resolver.adapter.edges_of(FaceRequest(operation_id=op_id, face=face.handle)).edges:
            edges.setdefault(edge.handle.id, edge)
    return list(edges.values())


def _hole_faces(resolver: "Resolver", body: BodyHandle, faces: dict[str, FaceInfo], op_id: str) -> set[str]:
    """Ids of the faces that are walls of holes. An adapter that cannot list holes: every cylindrical face is taken as
    one (a boss's wall is dropped too; say so by naming the edges you want instead)."""
    if "list_holes" in resolver.adapter.capabilities().tools:
        try:
            holes = resolver.adapter.list_holes(ListHolesRequest(operation_id=op_id, body=body)).holes
        except AdapterError:
            holes = None
        if holes is not None:
            return {face.id for hole in holes for face in hole.faces}
    return {fid for fid, face in faces.items() if face.surface is SurfaceType.cylinder}


# --- criteria -------------------------------------------------------------------------------------------------------


def resolve_edge_set(resolver: "Resolver", ref: EdgeSet, op_id: str, path: str) -> list[EdgeInfo]:
    """The edges ``ref`` matches, sorted. Raises ``ResolutionError`` when it matches none."""
    where = ref.where
    thresholds = resolver.thresholds
    faces: dict[str, FaceInfo] = {}
    if where.on_face is not None:
        face = resolver.face(where.on_face, op_id, f"{path}.where.on_face")
        body = face.body
        faces[face.handle.id] = face
        candidates = list(resolver.adapter.edges_of(FaceRequest(operation_id=op_id, face=face.handle)).edges)
        scope = f"face {face.handle.id}"
    else:
        body = _body_of_step(resolver, ref, op_id, path)
        faces = _faces_of_body(resolver, body, op_id)
        candidates = _edges_of_faces(resolver, faces, op_id)
        scope = f"body {body.id}"

    def face_of(handle: FaceHandle) -> FaceInfo:
        known = faces.get(handle.id)
        if known is None:
            known = resolver.adapter.face_info(FaceRequest(operation_id=op_id, face=handle))
            faces[handle.id] = known
        return known

    steps: list[tuple[str, Callable[[EdgeInfo], bool]]] = []
    if where.curve is not None:
        steps.append((f"curve {where.curve.value}", lambda e: e.curve == where.curve))
    if where.parallel_to is not None:
        wanted = _AXES[where.parallel_to] if isinstance(where.parallel_to, AxisName) else where.parallel_to
        if _norm(wanted) == 0.0:
            msg = "where.parallel_to is the zero vector"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)

        def parallel(e: EdgeInfo) -> bool:
            direction = direction_of(e)
            return direction is not None and thresholds.parallel(wanted, direction, either_sense=True)

        steps.append(("parallel_to", parallel))
    if where.length_mm is not None:
        steps.append(("length_mm", lambda e: resolver._scalar_matches(where.length_mm, e.length_mm, "mm")))  # type: ignore[arg-type]  # noqa: SLF001
    if where.convex is not None:
        steps.append(
            ("convex" if where.convex else "concave", lambda e: e.convex is not None and e.convex == where.convex)
        )
    if where.planar is not None:

        def planar(e: EdgeInfo) -> bool:
            return all(face_of(h).surface is SurfaceType.plane for h in e.faces) == where.planar

        steps.append(("planar", planar))
    if where.exclude_holes:
        holes = _hole_faces(resolver, body, faces, op_id)
        steps.append(("exclude_holes", lambda e: not any(h.id in holes for h in e.faces)))
    if where.except_:
        dropped: set[str] = set()
        points: list[Vec3] = []
        for index, item in enumerate(where.except_):
            if isinstance(item, EdgeRef):
                dropped.add(resolver.edge(item, op_id, f"{path}.where.except[{index}]").handle.id)
            else:
                points.append(item)

        def kept(e: EdgeInfo) -> bool:
            return e.handle.id not in dropped and all(_distance_to_edge(e, p) > thresholds.position_mm for p in points)

        steps.append(("except", kept))

    survivors = candidates
    funnel = [f"{len(candidates)} edge(s) shared by two faces on {scope}"]
    last_before: list[EdgeInfo] = candidates
    for label, test in steps:
        before = survivors
        survivors = [e for e in before if test(e)]
        funnel.append(f"after {label}: {len(survivors)}")
        if not survivors:
            last_before = before
            break
    if not survivors:
        shown = sorted(last_before, key=_order_key)[:MAX_LISTED]
        message = (
            f"edge set ({criteria_text(where)}) matched 0 edges: " + "; ".join(funnel) + ". "
            "The edges before the criterion that emptied it are listed below"
            if last_before
            else f"edge set ({criteria_text(where)}) matched 0 edges: " + "; ".join(funnel)
        )
        raise ResolutionError(
            op_id=op_id,
            path=path,
            ref=ref,
            candidates=0,
            message=message,
            candidate_details=tuple(describe_edge(e) for e in shown),
        )
    survivors.sort(key=_order_key)
    return survivors
