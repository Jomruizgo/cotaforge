"""StubAdapter: a scripted Adapter for executor/verification unit tests.

It never touches ``cotaforge.adapters.*``. Construction and assembly calls pop scripted
``Step``s per ``operation_id`` (one per attempt); provenance/query calls answer from hand-built
tables. ``undo_last`` restores the mass/bbox recorded before the last construction call.
"""

from dataclasses import dataclass, field

from cotaforge.contract import (
    Ack,
    Adapter,
    AdapterError,
    ApiValidation,
    BodiesResult,
    BoundingBox,
    CanaryResult,
    ComponentRequest,
    ConnectRequest,
    DatumAxisResult,
    DatumPlaneResult,
    DocumentHandle,
    DocumentInfo,
    DocumentKind,
    EdgeHandle,
    EdgeInfo,
    EdgeRequest,
    EdgesBetweenRequest,
    EdgesResult,
    Empty,
    EntityRequest,
    EntityResult,
    ErrorCode,
    ErrorInfo,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FacesResult,
    FeatureRequest,
    FeatureTree,
    GeometricSignature,
    InterferenceResult,
    JointInfo,
    JointRequest,
    MassProperties,
    MassRequest,
    NewDocumentRequest,
    OperationResult,
    PersistentRef,
    ProvenanceRole,
    Request,
    ResolveRequest,
    SessionInfo,
    SetActiveDocumentRequest,
    SetTimeoutRequest,
    SketchResult,
    Transform,
    UndoResult,
    ValidityResult,
    VertexAtRequest,
    VertexInfo,
    VerticesResult,
)

StepResult = OperationResult | SketchResult | InterferenceResult | DatumPlaneResult | DatumAxisResult | AdapterError

EMPTY_MASS = MassProperties(volume_mm3=0.0, centroid=(0.0, 0.0, 0.0), surface_mm2=0.0)
EMPTY_BBOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(0.0, 0.0, 0.0))


@dataclass
class Step:
    """One scripted attempt of a construction call."""

    result: StepResult
    mass_after: MassProperties | None = None
    bbox_after: BoundingBox | None = None
    healthy_after: bool | None = None
    component_bbox_after: dict[str, BoundingBox] = field(default_factory=dict)
    component_transform_after: dict[str, Transform] = field(default_factory=dict)


def error(
    code: ErrorCode = ErrorCode.geometry_failed, recoverable: bool = True, message: str = "scripted"
) -> AdapterError:
    return AdapterError(ErrorInfo(code=code, message=message, recoverable=recoverable))


class StubAdapter(Adapter):
    name = "stub"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Request]] = []
        self.scripts: dict[str, list[Step]] = {}
        self.mass: MassProperties = EMPTY_MASS
        self.bbox: BoundingBox = EMPTY_BBOX
        self.mass_by_body: dict[str, MassProperties] = {}
        self.bbox_by_body: dict[str, BoundingBox] = {}
        self.mass_by_component: dict[str, MassProperties] = {}
        self.mass_by_document: dict[str, MassProperties] = {}
        self.bbox_by_document: dict[str, BoundingBox] = {}
        self.faces: dict[str, FaceInfo] = {}
        self.edges: dict[str, EdgeInfo] = {}
        self.vertices: dict[str, VertexInfo] = {}
        self.faces_of_feature: dict[str, list[FaceInfo]] = {}
        self.edges_between_map: dict[frozenset[str], list[EdgeInfo]] = {}
        self.edges_of_map: dict[str, list[EdgeInfo]] = {}
        self.vertex_at_map: dict[frozenset[str], list[VertexInfo]] = {}
        self.vertices_of_map: dict[str, list[VertexInfo]] = {}
        self.component_bboxes: dict[str, BoundingBox] = {}
        self.component_transforms: dict[str, Transform] = {}
        self.joints: dict[str, JointInfo] = {}
        self.interference: InterferenceResult = InterferenceResult(pairs=(), total_volume_mm3=0.0)
        self.healthy: bool = True
        self.documents: list[DocumentInfo] = []
        self.active: DocumentInfo | None = None
        self._undo_stack: list[tuple[MassProperties, BoundingBox, dict[str, BoundingBox], dict[str, Transform]]] = []
        self._document_counter = 0

    # --- scripting helpers ------------------------------------------------------------

    def script(self, operation_id: str, *steps: Step) -> None:
        self.scripts.setdefault(operation_id, []).extend(steps)

    def add_face(self, face: FaceInfo, feature_id: str | None = None) -> FaceInfo:
        self.faces[face.handle.id] = face
        if feature_id is not None:
            self.faces_of_feature.setdefault(feature_id, []).append(face)
        return face

    def add_edge(self, edge: EdgeInfo) -> EdgeInfo:
        self.edges[edge.handle.id] = edge
        self.edges_between_map.setdefault(frozenset(f.id for f in edge.faces), []).append(edge)
        for face in edge.faces:
            self.edges_of_map.setdefault(face.id, []).append(edge)
        return edge

    def calls_to(self, method: str) -> list[Request]:
        return [request for name, request in self.calls if name == method]

    def construction_calls(self) -> list[tuple[str, Request]]:
        return [(name, request) for name, request in self.calls if name in CONSTRUCTION_METHODS]

    def _construct(self, method: str, request: Request) -> StepResult:
        self.calls.append((method, request))
        steps = self.scripts.get(request.operation_id)
        if not steps:
            msg = f"no scripted step for {request.operation_id} ({method})"
            raise AssertionError(msg)
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step.result, AdapterError):
            if step.healthy_after is not None:
                self.healthy = step.healthy_after
            raise step.result
        self._undo_stack.append((self.mass, self.bbox, dict(self.component_bboxes), dict(self.component_transforms)))
        if step.mass_after is not None:
            self.mass = step.mass_after
        if step.bbox_after is not None:
            self.bbox = step.bbox_after
        self.component_bboxes.update(step.component_bbox_after)
        self.component_transforms.update(step.component_transform_after)
        if step.healthy_after is not None:
            self.healthy = step.healthy_after
        return step.result

    def _log(self, method: str, request: Request) -> None:
        self.calls.append((method, request))

    # --- Session -----------------------------------------------------------------------

    def connect(self, request: ConnectRequest) -> SessionInfo:
        self._log("connect", request)
        return SessionInfo(adapter=self.name, version="0", units="mm")

    def disconnect(self, request: Empty) -> Ack:
        self._log("disconnect", request)
        return Ack()

    def new_document(self, request: NewDocumentRequest) -> DocumentInfo:
        self._log("new_document", request)
        self._document_counter += 1
        info = DocumentInfo(
            handle=DocumentHandle(id=f"doc:{self._document_counter}"), kind=request.kind, name=request.name
        )
        self.documents.append(info)
        self.active = info
        return info

    def set_active_document(self, request: SetActiveDocumentRequest) -> DocumentInfo:
        self._log("set_active_document", request)
        for info in self.documents:
            if info.handle == request.document:
                self.active = info
                return info
        info = DocumentInfo(handle=request.document, kind=DocumentKind.part, name=request.document.id)
        self.documents.append(info)
        self.active = info
        return info

    def active_document(self, request: Empty) -> DocumentInfo:
        self._log("active_document", request)
        if self.active is None:
            raise AdapterError(ErrorInfo(code=ErrorCode.no_active_document, message="none", recoverable=False))
        return self.active

    def set_timeout(self, request: SetTimeoutRequest) -> Ack:
        self._log("set_timeout", request)
        return Ack()

    def suppress_interaction(self, request: Empty) -> Ack:
        self._log("suppress_interaction", request)
        return Ack()

    def restore_interaction(self, request: Empty) -> Ack:
        self._log("restore_interaction", request)
        return Ack()

    # --- Health -------------------------------------------------------------------------

    def canary(self, request: Empty) -> CanaryResult:
        self._log("canary", request)
        return CanaryResult(healthy=self.healthy, checks={"plane": self.healthy}, duration_ms=0.1)

    def validate_api(self, request: Empty) -> ApiValidation:
        self._log("validate_api", request)
        return ApiValidation(ok=True, problems=())

    # --- Construction (all scripted) ----------------------------------------------------------

    def sketch(self, request: Request) -> SketchResult:  # type: ignore[override]
        return self._construct("sketch", request)  # type: ignore[return-value]

    def datum_plane(self, request: Request) -> DatumPlaneResult:  # type: ignore[override]
        return self._construct("datum_plane", request)  # type: ignore[return-value]

    def datum_axis(self, request: Request) -> DatumAxisResult:  # type: ignore[override]
        return self._construct("datum_axis", request)  # type: ignore[return-value]

    def extrude(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("extrude", request)  # type: ignore[return-value]

    def cut(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("cut", request)  # type: ignore[return-value]

    def revolve(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("revolve", request)  # type: ignore[return-value]

    def hole(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("hole", request)  # type: ignore[return-value]

    def linear_pattern(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("linear_pattern", request)  # type: ignore[return-value]

    def circular_pattern(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("circular_pattern", request)  # type: ignore[return-value]

    def chamfer(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("chamfer", request)  # type: ignore[return-value]

    def fillet(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("fillet", request)  # type: ignore[return-value]

    def box(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("box", request)  # type: ignore[return-value]

    def cylinder(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("cylinder", request)  # type: ignore[return-value]

    def boolean(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("boolean", request)  # type: ignore[return-value]

    def move_body(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("move_body", request)  # type: ignore[return-value]

    def copy_body(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("copy_body", request)  # type: ignore[return-value]

    def sweep(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("sweep", request)  # type: ignore[return-value]

    def shell(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("shell", request)  # type: ignore[return-value]

    def mirror(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("mirror", request)  # type: ignore[return-value]

    def mirror_body(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("mirror_body", request)  # type: ignore[return-value]

    def split(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("split", request)  # type: ignore[return-value]

    def draft(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("draft", request)  # type: ignore[return-value]

    # --- Assemblies --------------------------------------------------------------------------

    def insert_component(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("insert_component", request)  # type: ignore[return-value]

    def fix_component(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("fix_component", request)  # type: ignore[return-value]

    def mate_concentric(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("mate_concentric", request)  # type: ignore[return-value]

    def mate_coincident(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("mate_coincident", request)  # type: ignore[return-value]

    def mate_distance(self, request: Request) -> OperationResult:  # type: ignore[override]
        return self._construct("mate_distance", request)  # type: ignore[return-value]

    def check_interference(self, request: Request) -> InterferenceResult:  # type: ignore[override]
        self._log("check_interference", request)
        steps = self.scripts.get(request.operation_id)
        if steps:
            step = steps.pop(0) if len(steps) > 1 else steps[0]
            if isinstance(step.result, AdapterError):
                raise step.result
            return step.result  # type: ignore[return-value]
        return self.interference

    def component_bounding_box(self, request: ComponentRequest) -> BoundingBox:
        self._log("component_bounding_box", request)
        return self.component_bboxes[request.component.id]

    def component_transform(self, request: ComponentRequest) -> Transform:
        self._log("component_transform", request)
        return self.component_transforms.get(request.component.id, Transform.identity())

    def joint_info(self, request: JointRequest) -> JointInfo:
        self._log("joint_info", request)
        return self.joints[request.joint.id]

    # --- Provenance ----------------------------------------------------------------------------

    def faces_of(self, request: FeatureRequest) -> FacesResult:
        self._log("faces_of", request)
        faces = self.faces_of_feature.get(request.feature.id, [])
        if request.role == ProvenanceRole.created:
            faces = [f for f in faces if f.provenance.created_by == request.feature]
        elif request.role == ProvenanceRole.modified:
            faces = [f for f in faces if request.feature in f.provenance.modified_by]
        return FacesResult(faces=tuple(faces))

    def edges_between(self, request: EdgesBetweenRequest) -> EdgesResult:
        self._log("edges_between", request)
        return EdgesResult(edges=tuple(self.edges_between_map.get(frozenset({request.a.id, request.b.id}), [])))

    def edges_of(self, request: FaceRequest) -> EdgesResult:
        self._log("edges_of", request)
        return EdgesResult(edges=tuple(self.edges_of_map.get(request.face.id, [])))

    def vertices_of(self, request: EdgeRequest) -> VerticesResult:
        self._log("vertices_of", request)
        return VerticesResult(vertices=tuple(self.vertices_of_map.get(request.edge.id, [])))

    def vertex_at(self, request: VertexAtRequest) -> VerticesResult:
        self._log("vertex_at", request)
        return VerticesResult(vertices=tuple(self.vertex_at_map.get(frozenset(f.id for f in request.faces), [])))

    def bodies_of(self, request: FeatureRequest) -> BodiesResult:
        self._log("bodies_of", request)
        return BodiesResult(bodies=())

    # --- Query --------------------------------------------------------------------------------

    def is_valid(self, request: Empty) -> ValidityResult:
        self._log("is_valid", request)
        return ValidityResult(valid=True, problems=())

    def mass_properties(self, request: MassRequest) -> MassProperties:
        self._log("mass_properties", request)
        if request.body is not None:
            return self.mass_by_body[request.body.id]
        if request.component is not None:
            return self.mass_by_component[request.component.id]
        if self.active is not None and self.active.handle.id in self.mass_by_document:
            return self.mass_by_document[self.active.handle.id]
        return self.mass

    def bounding_box(self, request: MassRequest) -> BoundingBox:
        self._log("bounding_box", request)
        if request.body is not None:
            if (
                request.body.id not in self.bbox_by_body
            ):  # an unscripted body: what a real adapter says of an unknown one
                raise AdapterError(
                    ErrorInfo(
                        code=ErrorCode.invalid_reference, message=f"unknown body {request.body.id}", recoverable=False
                    )
                )
            return self.bbox_by_body[request.body.id]
        if request.component is not None:
            return self.component_bboxes[request.component.id]
        if self.active is not None and self.active.handle.id in self.bbox_by_document:
            return self.bbox_by_document[self.active.handle.id]
        return self.bbox

    def feature_tree(self, request: Empty) -> FeatureTree:
        self._log("feature_tree", request)
        return FeatureTree(nodes=())

    def face_info(self, request: FaceRequest) -> FaceInfo:
        self._log("face_info", request)
        return self.faces[request.face.id]

    def edge_info(self, request: EdgeRequest) -> EdgeInfo:
        self._log("edge_info", request)
        return self.edges[request.edge.id]

    # --- References ------------------------------------------------------------------------------

    def persistent_reference(self, request: EntityRequest) -> PersistentRef:
        self._log("persistent_reference", request)
        return PersistentRef(adapter=self.name, token=request.entity.id, signature=GeometricSignature(kind="stub"))

    def resolve_reference(self, request: ResolveRequest) -> EntityResult:
        """Tokens are the handle id verbatim (from_selection tests): a token found in ``self.faces``/``self.edges``
        resolves to that kind's handle, anything else is ``invalid_reference`` (a stale/foreign token)."""
        self._log("resolve_reference", request)
        token = request.ref.token
        if token in self.faces:
            return EntityResult(entity=FaceHandle(id=token))
        if token in self.edges:
            return EntityResult(entity=EdgeHandle(id=token))
        info = ErrorInfo(code=ErrorCode.invalid_reference, message=f"stale reference {token}", recoverable=False)
        raise AdapterError(info)

    def signature(self, request: EntityRequest) -> GeometricSignature:
        self._log("signature", request)
        return GeometricSignature(kind="stub")

    # --- Undo ---------------------------------------------------------------------------------------

    def undo_last(self, request: Empty) -> UndoResult:
        self._log("undo_last", request)
        if not self._undo_stack:
            return UndoResult(reverted=None)
        self.mass, self.bbox, self.component_bboxes, self.component_transforms = self._undo_stack.pop()
        return UndoResult(reverted=None)


CONSTRUCTION_METHODS = frozenset(
    {
        "sketch",
        "datum_plane",
        "datum_axis",
        "extrude",
        "cut",
        "revolve",
        "hole",
        "linear_pattern",
        "circular_pattern",
        "chamfer",
        "fillet",
        "box",
        "cylinder",
        "boolean",
        "move_body",
        "copy_body",
        "sweep",
        "shell",
        "draft",
        "mirror",
        "mirror_body",
        "split",
        "insert_component",
        "fix_component",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
        "check_interference",
    }
)
