"""The reads of one step before the CAD is asked to change anything.

Resolving the references of a step and computing what the step should do ask the adapter for the same faces and edges
again and again: the face a chamfer sits on is listed once per reference that names it, then described once more to
find the wedge it takes, the edge the resolver just described is described again to read its length. Nothing changes
the part until the operation itself runs, so those answers cannot differ. ``StepReads`` stands in front of the adapter
for that stretch and answers a repeat from what it already holds; what it has not seen goes to the adapter in one batch
(``Adapter.faces_info`` / ``edges_info``), not one call per entity.

It must not outlive the stretch: the executor closes it (``Resolver.end_reads``) before the operation is called, and
everything after that (the observation, the checks, a repair) reads the adapter itself.
"""

from collections.abc import Sequence
from typing import Any

from cotaforge.contract import (
    Adapter,
    EdgeHandle,
    EdgeInfo,
    EdgeRequest,
    EdgesBetweenRequest,
    EdgesInfoRequest,
    EdgesResult,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FacesInfoRequest,
    FacesResult,
    FeatureRequest,
)


class StepReads:
    """Answers ``face_info``, ``edge_info``, ``faces_info``, ``edges_info``, ``faces_of``, ``edges_between`` and
    ``edges_of`` from what was already read in this stretch; every other attribute is the adapter's own."""

    def __init__(self, adapter: Adapter) -> None:
        self._adapter = adapter
        self._faces: dict[str, FaceInfo] = {}
        self._edges: dict[str, EdgeInfo] = {}
        self._listed: dict[tuple[str, str], FacesResult] = {}
        self._between: dict[tuple[str, str], EdgesResult] = {}
        self._of_face: dict[str, EdgesResult] = {}
        self.hits = 0
        """Reads answered without asking the adapter (what the stretch saved)."""

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    def _keep_faces(self, faces: Sequence[FaceInfo]) -> None:
        for face in faces:
            self._faces.setdefault(face.handle.id, face)

    def _keep_edges(self, edges: Sequence[EdgeInfo]) -> None:
        for edge in edges:
            self._edges.setdefault(edge.handle.id, edge)

    # --- one entity ------------------------------------------------------------------------------------------------

    def face_info(self, request: FaceRequest) -> FaceInfo:
        held = self._faces.get(request.face.id)
        if held is not None:
            self.hits += 1
            return held
        info = self._adapter.face_info(request)
        self._faces[request.face.id] = info
        return info

    def edge_info(self, request: EdgeRequest) -> EdgeInfo:
        held = self._edges.get(request.edge.id)
        if held is not None:
            self.hits += 1
            return held
        info = self._adapter.edge_info(request)
        self._edges[request.edge.id] = info
        return info

    # --- several entities: only what is not held is asked for, in one batch ---------------------------------------

    def faces_info(self, request: FacesInfoRequest) -> FacesResult:
        missing: list[FaceHandle] = []
        for handle in request.faces:
            if handle.id not in self._faces and all(handle.id != m.id for m in missing):
                missing.append(handle)
        if missing:
            asked = self._adapter.faces_info(FacesInfoRequest(operation_id=request.operation_id, faces=tuple(missing)))
            for handle, info in zip(missing, asked.faces, strict=True):
                self._faces[handle.id] = info
        self.hits += len(request.faces) - len(missing)
        return FacesResult(faces=tuple(self._faces[handle.id] for handle in request.faces))

    def edges_info(self, request: EdgesInfoRequest) -> EdgesResult:
        missing: list[EdgeHandle] = []
        for handle in request.edges:
            if handle.id not in self._edges and all(handle.id != m.id for m in missing):
                missing.append(handle)
        if missing:
            asked = self._adapter.edges_info(EdgesInfoRequest(operation_id=request.operation_id, edges=tuple(missing)))
            for handle, info in zip(missing, asked.edges, strict=True):
                self._edges[handle.id] = info
        self.hits += len(request.edges) - len(missing)
        return EdgesResult(edges=tuple(self._edges[handle.id] for handle in request.edges))

    # --- listings: a descriptor in one is the descriptor ``face_info`` / ``edge_info`` gives ------------------------

    def faces_of(self, request: FeatureRequest) -> FacesResult:
        key = (request.feature.id, request.role.value)
        held = self._listed.get(key)
        if held is not None:
            self.hits += 1
            return held
        result = self._adapter.faces_of(request)
        self._listed[key] = result
        self._keep_faces(result.faces)
        return result

    def edges_between(self, request: EdgesBetweenRequest) -> EdgesResult:
        key = (request.a.id, request.b.id)
        held = self._between.get(key)
        if held is not None:
            self.hits += 1
            return held
        result = self._adapter.edges_between(request)
        self._between[key] = result
        self._keep_edges(result.edges)
        return result

    def edges_of(self, request: FaceRequest) -> EdgesResult:
        held = self._of_face.get(request.face.id)
        if held is not None:
            self.hits += 1
            return held
        result = self._adapter.edges_of(request)
        self._of_face[request.face.id] = result
        self._keep_edges(result.edges)
        return result
