"""Resolver: symbolic IR references -> concrete adapter entities.

Candidates come from the provenance of ``from_op``; every non-``None`` selector field filters; a
tie-breaker (``nearest_to``, ``largest``, ``smallest``, ``index``) may then pick one survivor; exactly one
candidate must remain, otherwise ``ResolutionError`` (a plan error: no retry, no guess) whose message lists
every candidate with the attributes that tell them apart, so the plan can be fixed in one turn.
"""

from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar, cast

from cotaforge.contract import (
    Adapter,
    AnyHandle,
    Axis,
    BodyHandle,
    ComponentHandle,
    CurveType,
    DatumPlaneTarget,
    EdgeHandle,
    EdgeInfo,
    EdgeRequest,
    EdgesBetweenRequest,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FeatureRequest,
    GeometricSignature,
    PersistentRef,
    PlaneName,
    ResolveRequest,
    SketchFrame,
    Vec3,
    VertexAtRequest,
    VertexInfo,
    frame_for_face,
    frame_for_plane,
)
from cotaforge.executor import edge_sets
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.reads import StepReads
from cotaforge.ir.refs import AxisRef, BodyRef, ComponentRef, EdgeRef, EdgeSet, FaceRef, PlaneRef, Range, VertexRef
from cotaforge.verification import datum
from cotaforge.verification.thresholds import Thresholds

T = TypeVar("T", FaceInfo, EdgeInfo, VertexInfo)


class Resolver:
    def __init__(self, adapter: Adapter, ctx: ExecutionContext, thresholds: Thresholds) -> None:
        self._adapter = adapter
        self._reads: StepReads | None = None
        self.ctx = ctx
        self.thresholds = thresholds
        self.resolved_refs: dict[str, str] = {}
        self.edge_set_reports: dict[str, tuple[str, ...]] = {}
        """Per edge-set path: how many edges it resolved to and one line for each (what the agent is shown)."""
        self.edge_set_edges: dict[str, EdgeInfo] = {}
        """Every edge an edge set resolved to in this step, by handle id (to describe the ones an op skipped)."""

    @property
    def adapter(self) -> Adapter:
        """What the resolver (and ``edge_sets``) reads through: the adapter, or while a step's reads are open
        (``begin_reads``) the stand-in that answers a repeated read from what it already holds."""
        return cast(Adapter, self._reads) if self._reads is not None else self._adapter

    @adapter.setter
    def adapter(self, adapter: Adapter) -> None:
        self._adapter = adapter

    def begin_step(self) -> None:
        self.resolved_refs = {}
        self.edge_set_reports = {}
        self.edge_set_edges = {}

    def begin_reads(self) -> None:
        """Open the reads of one step: until ``end_reads`` the same face, edge or listing is asked of the adapter once.
        Only for the stretch in which nothing changes the part (resolving the references and computing the checks,
        before the operation runs); the caller must close it before the operation is called."""
        self._reads = StepReads(self._adapter)

    def end_reads(self) -> None:
        self._reads = None

    # --- scalar / vector matching ------------------------------------------------------------------

    def _scalar_matches(self, selector: float | Range, value: float | None, unit: str) -> bool:
        if value is None:
            return False
        if isinstance(selector, Range):
            tol = self.thresholds.abs_mm if unit == "mm" else self.thresholds.abs_mm2
            lo_ok = selector.min is None or value >= selector.min - tol
            hi_ok = selector.max is None or value <= selector.max + tol
            return lo_ok and hi_ok
        return self.thresholds.close(selector, value, unit)  # type: ignore[arg-type]

    def _face_matches(self, face: FaceInfo, ref: FaceRef) -> bool:
        s = ref.select
        if s.surface is not None and face.surface != s.surface:
            return False
        if s.normal is not None and (face.normal is None or not self.thresholds.parallel(s.normal, face.normal)):
            return False
        if s.axis_direction is not None and (
            face.axis is None or not self.thresholds.parallel(s.axis_direction, face.axis.direction, either_sense=True)
        ):
            return False
        if s.radius is not None and not self._scalar_matches(s.radius, face.radius, "mm"):
            return False
        if s.area_mm2 is not None and not self._scalar_matches(s.area_mm2, face.area_mm2, "mm2"):
            return False
        return s.center is None or self.thresholds.close_position(s.center, face.center)

    def _edge_matches(self, edge: EdgeInfo, ref: EdgeRef) -> bool:
        if ref.curve is not None and edge.curve != ref.curve:
            return False
        if ref.radius is not None and not self._scalar_matches(ref.radius, edge.radius, "mm"):
            return False
        return ref.length_mm is None or self._scalar_matches(ref.length_mm, edge.length_mm, "mm")

    # --- tie-breakers -----------------------------------------------------------------------------------

    def _nearest(self, survivors: list[T], point: Vec3, position: Callable[[T], Vec3]) -> list[T]:
        """The survivor whose ``position`` is closest to ``point``; both when the two best tie within
        the position tolerance (the reference stays ambiguous rather than picking by luck)."""
        if len(survivors) < 2:
            return survivors
        ranked = sorted(((_distance(point, position(item)), item) for item in survivors), key=lambda pair: pair[0])
        (best_distance, best), (next_distance, runner_up) = ranked[0], ranked[1]
        if abs(best_distance - next_distance) <= self.thresholds.position_mm:
            return [best, runner_up]
        return [best]

    def _extreme_area(self, survivors: list[FaceInfo], *, largest: bool) -> list[FaceInfo]:
        if len(survivors) < 2:
            return survivors
        ranked = sorted(survivors, key=lambda face: face.area_mm2, reverse=largest)
        if self.thresholds.close(ranked[0].area_mm2, ranked[1].area_mm2, "mm2"):
            return [ranked[0], ranked[1]]
        return [ranked[0]]

    # --- single-survivor rule --------------------------------------------------------------------------

    def _one(self, candidates: Iterable[T], op_id: str, path: str, ref: object, what: str) -> T:
        survivors = list(candidates)
        if len(survivors) != 1:
            msg = f"{what} resolved to {len(survivors)} candidates (need exactly 1)"
            if len(survivors) > 1:
                msg += "; narrow the selector so exactly one matches (candidates below)"
            raise ResolutionError(
                op_id=op_id,
                path=path,
                ref=ref,
                candidates=len(survivors),
                message=msg,
                candidate_details=self.describe_candidates(survivors),
            )
        return survivors[0]

    def describe_candidates(self, candidates: Sequence[FaceInfo | EdgeInfo | VertexInfo]) -> tuple[str, ...]:
        """One line per candidate with its discriminating attributes (what ``faces_of`` would show)."""
        op_by_feature = {handle.id: op_id for op_id, handle in self.ctx.feature_by_op.items()}
        return tuple(describe_candidate(candidate, op_by_feature) for candidate in candidates)

    # --- public API -------------------------------------------------------------------------------------

    def _resolve_selection(self, token: str, kind: str, op_id: str, path: str) -> AnyHandle:
        """The concrete handle a ``get_selection`` token names now: builds the same
        ``PersistentRef`` shape the adapter's own ``feature_tree`` uses for features, through the
        adapter's own ``resolve_reference`` — a stale or foreign-adapter token surfaces as the adapter's
        own ``AdapterError`` (invalid_reference), same as any other adapter call the executor makes."""
        persistent_ref = PersistentRef(adapter=self.adapter.name, token=token, signature=GeometricSignature(kind=kind))
        return self.adapter.resolve_reference(ResolveRequest(operation_id=op_id, ref=persistent_ref)).entity

    def face(self, ref: FaceRef, op_id: str, path: str) -> FaceInfo:
        if ref.from_selection is not None:
            handle = self._resolve_selection(ref.from_selection, "face", op_id, path)
            face = self.adapter.face_info(FaceRequest(operation_id=op_id, face=FaceHandle(id=handle.id)))
            self.resolved_refs[path] = face.handle.id
            return face
        assert ref.from_op is not None  # FaceRef's own validator guarantees exactly one of the two
        feature = self.ctx.feature_by_op.get(ref.from_op)
        if feature is None:
            msg = f"operation {ref.from_op!r} produced no feature to select faces from"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
        faces = self.adapter.faces_of(FeatureRequest(operation_id=op_id, feature=feature, role=ref.select.role)).faces
        survivors = [f for f in faces if self._face_matches(f, ref)]
        select = ref.select
        if select.nearest_to is not None:
            survivors = self._nearest(survivors, select.nearest_to, lambda face: face.center)
        elif select.largest or select.smallest:
            survivors = self._extreme_area(survivors, largest=select.largest)
        elif select.index is not None:
            survivors.sort(key=_center_key)
            index = select.index
            survivors = [survivors[index]] if 0 <= index < len(survivors) else []
        if not survivors:
            # Nothing matched: list what ``from_op`` does offer (with ``role``), so the author can see whether
            # the selector is wrong (a stale exact area, a wrong normal) or the provenance is (``from_op`` must be
            # the op that created or modified the face — a fillet does not own the block's top face).
            msg = (
                f"face resolved to 0 candidates (need exactly 1); op {ref.from_op!r} offers {len(faces)} face(s) "
                f"with role {select.role.value!r} (listed below): fix the selector, or name the op that created or "
                "modified the face in from_op"
            )
            offered = self.describe_candidates(faces)
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg, candidate_details=offered)
        face = self._one(survivors, op_id, path, ref, "face")
        self.resolved_refs[path] = face.handle.id
        return face

    def edge(self, ref: EdgeRef, op_id: str, path: str) -> EdgeInfo:
        if ref.from_selection is not None:
            handle = self._resolve_selection(ref.from_selection, "edge", op_id, path)
            edge = self.adapter.edge_info(EdgeRequest(operation_id=op_id, edge=EdgeHandle(id=handle.id)))
            self.resolved_refs[path] = edge.handle.id
            return edge
        if ref.between is not None:
            a = self.face(ref.between[0], op_id, f"{path}.between[0]")
            b = self.face(ref.between[1], op_id, f"{path}.between[1]")
            edges = self.adapter.edges_between(EdgesBetweenRequest(operation_id=op_id, a=a.handle, b=b.handle)).edges
        else:
            assert ref.of is not None
            face = self.face(ref.of, op_id, f"{path}.of")
            edges = self.adapter.edges_of(FaceRequest(operation_id=op_id, face=face.handle)).edges
        survivors = [e for e in edges if self._edge_matches(e, ref)]
        if ref.nearest_to is not None:
            survivors = self._nearest(survivors, ref.nearest_to, _edge_midpoint)
        edge = self._one(survivors, op_id, path, ref, "edge")
        self.resolved_refs[path] = edge.handle.id
        return edge

    def edge_set(self, ref: EdgeSet, op_id: str, path: str) -> list[EdgeInfo]:
        """The edges an ``EdgeSet`` matches now (sorted; see ``executor.edge_sets``), reported in ``edge_set_reports``."""
        edges = edge_sets.resolve_edge_set(self, ref, op_id, path)
        self.resolved_refs[path] = ",".join(sorted((e.handle.id for e in edges), key=edge_sets._id_key))  # noqa: SLF001
        self.edge_set_reports[path] = edge_sets.report(path, ref, edges)
        self.edge_set_edges.update({e.handle.id: e for e in edges})
        return edges

    def vertex(self, ref: VertexRef, op_id: str, path: str) -> VertexInfo:
        if ref.faces is not None:
            handles = tuple(self.face(f, op_id, f"{path}.faces[{i}]").handle for i, f in enumerate(ref.faces))
            vertices = self.adapter.vertex_at(VertexAtRequest(operation_id=op_id, faces=handles)).vertices  # type: ignore[arg-type]
            vertex = self._one(vertices, op_id, path, ref, "vertex")
        else:
            assert ref.edge is not None and ref.end is not None
            edge = self.edge(ref.edge, op_id, f"{path}.edge")
            vertices = self.adapter.vertices_of(EdgeRequest(operation_id=op_id, edge=edge.handle)).vertices
            index = 0 if ref.end == "start" else 1
            if len(vertices) < 2:
                msg = f"edge {edge.handle.id} has {len(vertices)} vertices"
                raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=len(vertices), message=msg)
            vertex = vertices[index]
        self.resolved_refs[path] = vertex.handle.id
        return vertex

    def body(self, ref: BodyRef, op_id: str, path: str) -> BodyHandle:
        if ref.side is not None:
            pieces = self.ctx.body_sides.get(ref.from_op)
            if pieces is None:
                msg = f"body ref names side {ref.side!r} but operation {ref.from_op!r} is not a split that ran"
                raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
            piece = pieces.get(ref.side)
            if piece is None:
                msg = f"split {ref.from_op!r} made no {ref.side} piece (it made: {', '.join(sorted(pieces)) or 'none'})"
                raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
            self.resolved_refs[path] = piece.id
            return piece
        pieces = self.ctx.body_sides.get(ref.from_op)
        if pieces is not None and len(pieces) > 1:
            # plan rule V3 refuses this statically; a second guard so the positive piece is never picked silently
            msg = f"split {ref.from_op!r} made two bodies: name one with side 'positive' or 'negative'"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=len(pieces), message=msg)
        body = self.ctx.body_by_op.get(ref.from_op)
        if body is None:
            msg = f"operation {ref.from_op!r} produced no body"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
        self.resolved_refs[path] = body.id
        return body

    def component(self, ref: ComponentRef, op_id: str, path: str) -> ComponentHandle:
        """The component the step ``ref.from_op`` made, read from what the session holds NOW: a step that was undone
        (or is not an insertion) leaves nothing here, so a reference never reaches the CAD with a handle that is
        gone."""
        component = self.ctx.component_by_op.get(ref.from_op)
        if component is None:
            msg = f"operation {ref.from_op!r} produced no component (an insert_component that ran and was not undone?)"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
        self.resolved_refs[path] = component.id
        return component

    def plane_or_face(self, ref: PlaneRef | FaceRef, op_id: str, path: str) -> PlaneName | FaceInfo | DatumPlaneTarget:
        if isinstance(ref, PlaneRef):
            if ref.name is not None:
                self.resolved_refs[path] = ref.name.value
                return ref.name
            return self.datum_plane(ref, op_id, path)
        return self.face(ref, op_id, path)

    def datum_plane(self, ref: PlaneRef, op_id: str, path: str) -> DatumPlaneTarget:
        """The datum plane a ``ref_plane`` op produced (``PlaneRef.from_op``)."""
        assert ref.from_op is not None  # the caller checked ``name is None``; PlaneRef needs exactly one
        target = self.ctx.datum_plane_by_op.get(ref.from_op)
        if target is None:
            msg = f"operation {ref.from_op!r} produced no datum plane (a ref_plane or an adapter plane that ran)"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
        self.resolved_refs[path] = target.handle.id
        return target

    def plane_frame(self, ref: PlaneRef | FaceRef, op_id: str, path: str) -> SketchFrame:
        """The sketch frame of a base plane, a datum plane or a planar face (the base of a datum definition)."""
        target = self.plane_or_face(ref, op_id, path)
        if isinstance(target, PlaneName):
            return frame_for_plane(target)
        if isinstance(target, DatumPlaneTarget):
            return target.frame
        if target.normal is None:
            msg = f"face {target.handle.id} is not planar ({target.surface.value}); a datum plane needs a planar face"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=1, message=msg)
        return frame_for_face(target)

    def axis(self, ref: AxisRef | EdgeRef | FaceRef | Axis, op_id: str, path: str) -> Axis:
        """A datum axis (``AxisRef``), the axis of an edge or a cylindrical/conical face, or a literal ``Axis``."""
        if isinstance(ref, Axis):
            return ref
        if isinstance(ref, AxisRef):
            axis = self.ctx.datum_axis_by_op.get(ref.from_op)
            if axis is None:
                msg = f"operation {ref.from_op!r} produced no datum axis (is it a ref_axis that ran?)"
                raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=0, message=msg)
            self.resolved_refs[path] = f"datum_axis:{ref.from_op}"
            return axis
        return self.entity_axis(ref, op_id, path)

    def entity_axis(self, ref: FaceRef | EdgeRef, op_id: str, path: str) -> Axis:
        """The axis of a cylindrical/conical face, a linear edge (start -> end) or a circular edge, described
        by the point of its line nearest the world origin: adapters describe the same cylinder by different
        points (the fake by the base centre, FreeCAD by the face centre), so ``normal_to.at_mm`` measured from
        the raw origin would mean different planes. Literal axes and ``ref_axis`` point definitions are never
        re-anchored."""
        return datum.axis_nearest_origin(self._raw_entity_axis(ref, op_id, path))

    def _raw_entity_axis(self, ref: FaceRef | EdgeRef, op_id: str, path: str) -> Axis:
        if isinstance(ref, FaceRef):
            face = self.face(ref, op_id, path)
            if face.axis is None:
                msg = f"face {face.handle.id} ({face.surface.value}) has no axis; pick a cylindrical or conical face"
                raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=1, message=msg)
            return face.axis
        edge = self.edge(ref, op_id, path)
        if edge.curve is CurveType.line and edge.start is not None and edge.end is not None and edge.start != edge.end:
            return Axis(origin=edge.start, direction=_vector(edge.start, edge.end))
        if edge.axis is None:
            msg = f"edge {edge.handle.id} ({edge.curve.value}) has no axis; pick a linear or circular edge"
            raise ResolutionError(op_id=op_id, path=path, ref=ref, candidates=1, message=msg)
        return edge.axis


def _vector(start: Vec3, end: Vec3) -> Vec3:
    return (end[0] - start[0], end[1] - start[1], end[2] - start[2])


def _center_key(face: FaceInfo) -> Vec3:
    return face.center


def _distance(a: Vec3, b: Vec3) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _edge_midpoint(edge: EdgeInfo) -> Vec3:
    if edge.center is not None:
        return edge.center
    if edge.start is not None and edge.end is not None:
        return tuple((s + e) / 2.0 for s, e in zip(edge.start, edge.end, strict=True))  # type: ignore[return-value]
    return edge.bbox.center()


def _fmt(value: float) -> str:
    return f"{value + 0.0:.6g}" if abs(value) >= 5e-7 else "0"


def _fmt_vec(value: Vec3) -> str:
    return "(" + ", ".join(_fmt(v) for v in value) + ")"


def describe_candidate(candidate: FaceInfo | EdgeInfo | VertexInfo, op_by_feature: dict[str, str]) -> str:
    """``face:7 plane, normal (1, 0, 0), centre (60, 5, 10), area 100 mm2, created by op `base```."""
    if isinstance(candidate, FaceInfo):
        parts = [f"{candidate.handle.id} {candidate.surface.value}"]
        if candidate.normal is not None:
            parts.append(f"normal {_fmt_vec(candidate.normal)}")
        if candidate.axis is not None:
            parts.append(f"axis {_fmt_vec(candidate.axis.direction)}")
        parts.append(f"centre {_fmt_vec(candidate.center)}")
        if candidate.radius is not None:
            parts.append(f"radius {_fmt(candidate.radius)} mm")
        parts.append(f"area {_fmt(candidate.area_mm2)} mm2")
        created = candidate.provenance.created_by.id
        parts.append(f"created by {_op_name(created, op_by_feature)}")
        modified = [_op_name(h.id, op_by_feature) for h in candidate.provenance.modified_by]
        if modified:
            parts.append(f"modified by {', '.join(modified)}")
        return ", ".join(parts)
    if isinstance(candidate, EdgeInfo):
        parts = [f"{candidate.handle.id} {candidate.curve.value}", f"midpoint {_fmt_vec(_edge_midpoint(candidate))}"]
        if candidate.radius is not None:
            parts.append(f"radius {_fmt(candidate.radius)} mm")
        parts.append(f"length {_fmt(candidate.length_mm)} mm")
        parts.append(f"between faces {candidate.faces[0].id} and {candidate.faces[1].id}")
        return ", ".join(parts)
    return f"{candidate.handle.id} vertex at {_fmt_vec(candidate.point)}"


def _op_name(feature_id: str, op_by_feature: dict[str, str]) -> str:
    op_id = op_by_feature.get(feature_id)
    return f"op `{op_id}`" if op_id is not None else f"feature {feature_id}"
