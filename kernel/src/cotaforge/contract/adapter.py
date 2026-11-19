"""Adapter ABC and the @tool registration decorator (contract/adapter.py)."""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from typing import Any, ClassVar, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from cotaforge.contract.assembly import (
    AddJointRequest,
    ComponentRequest,
    FixComponentRequest,
    InsertComponentRequest,
    InterferenceRequest,
    InterferenceResult,
    JointInfo,
    JointRequest,
    JointSweepResult,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    PlaceComponentRequest,
    SetJointRequest,
    SweepJointRequest,
)
from cotaforge.contract.descriptors import EdgeInfo, FaceInfo, MassProperties
from cotaforge.contract.document import (
    DesignAttributes,
    OpenDocumentRequest,
    OpenDocumentResult,
    SaveDocumentRequest,
    SaveDocumentResult,
    SetDesignAttributesRequest,
)
from cotaforge.contract.edit import (
    BodyInterferenceRequest,
    BodyInterferenceResult,
    EditFeatureRequest,
    EditResult,
    MeasureBodiesResult,
)
from cotaforge.contract.enums import DocumentKind, ErrorCode
from cotaforge.contract.errors import AdapterError, ErrorInfo
from cotaforge.contract.extension import OperationSpec
from cotaforge.contract.features import (
    FeatureList,
    FocusRequest,
    FocusResult,
    ListFeaturesRequest,
    ReferenceVisibilityRequest,
    ReferenceVisibilityResult,
    RemoveFeatureRequest,
    SetFeaturePropertiesRequest,
)
from cotaforge.contract.geometry import BoundingBox, Transform
from cotaforge.contract.importing import (
    ImportGeometryRequest,
    ImportGeometryResult,
    ListHolesRequest,
    ListHolesResult,
)
from cotaforge.contract.mould import (
    DraftAnalysisRequest,
    DraftAnalysisResult,
    MouldSplitRequest,
    PartingAnalysisRequest,
    PartingAnalysisResult,
)
from cotaforge.contract.mould_parts import MouldComponentRequest, SlideMechanismRequest
from cotaforge.contract.native import RunNativeRequest, RunNativeResult
from cotaforge.contract.probe import ToolOverlapRequest, ToolOverlapResult
from cotaforge.contract.requests import (
    AnnotateDrawingRequest,
    BaseFlangeRequest,
    BooleanRequest,
    BoxRequest,
    CaptureDrawingRequest,
    CaptureRequest,
    ChamferRequest,
    CircularPatternRequest,
    ConnectRequest,
    CopyBodyRequest,
    CutRequest,
    CylinderRequest,
    DatumAxisRequest,
    DatumPlaneRequest,
    DescribeDrawingRequest,
    DraftRequest,
    DrawingRequest,
    EdgeFlangeRequest,
    EdgeRequest,
    EdgesBetweenRequest,
    EdgesInfoRequest,
    Empty,
    EntityRequest,
    ExportDrawingRequest,
    ExportDxfRequest,
    ExportMeshRequest,
    ExportRequest,
    ExtrudeRequest,
    FaceRequest,
    FacesInfoRequest,
    FeatureRequest,
    FilletRequest,
    HoleRequest,
    LinearPatternRequest,
    MassRequest,
    MirrorBodyRequest,
    MirrorRequest,
    MoveBodyRequest,
    NewDocumentRequest,
    ResolveRequest,
    RevolveRequest,
    SectionRequest,
    SetActiveDocumentRequest,
    SetTimeoutRequest,
    ShellRequest,
    ShowEntitiesRequest,
    ShrinkBodyRequest,
    SketchRequest,
    SketchStatusRequest,
    SplitRequest,
    StyleRequest,
    SweepRequest,
    TreeDiffRequest,
    UnfoldRequest,
    ValidityRequest,
    VertexAtRequest,
    ViewRequest,
    WindowRequest,
)
from cotaforge.contract.responses import (
    Ack,
    ApiValidation,
    BodiesResult,
    CanaryResult,
    CaptureDrawingResult,
    DatumAxisResult,
    DatumPlaneResult,
    DescribeDrawingResult,
    DocumentInfo,
    DrawingResult,
    EdgesResult,
    EntityResult,
    EventsResult,
    ExportDrawingResult,
    FacesResult,
    FeatureTree,
    GeometricSignature,
    OperationResult,
    PersistentRef,
    SelectionResult,
    SessionInfo,
    ShowEntitiesResult,
    SketchResult,
    SketchStatusResult,
    TreeDiff,
    UndoResult,
    ValidityResult,
    VerticesResult,
    WindowResult,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group, ToolSpec

F = TypeVar("F", bound=Callable[..., object])


def _joints_not_supported(name: str) -> AdapterError:
    return AdapterError(
        ErrorInfo(
            code=ErrorCode.not_supported,
            message=f"{name}: this adapter has no joints; place the components with place_component",
            recoverable=False,
        )
    )


SelectionListener = Callable[[SelectionResult], None]


@runtime_checkable
class SelectionProducer(Protocol):
    """Optional adapter capability, separate from the ``@tool`` request/response surface: an adapter
    that can notice the CAD's live UI selection changing OUT OF BAND (never waiting for a tool call).

    A session layer checks ``isinstance(adapter, SelectionProducer)`` right after
    a session starts and, when true, calls ``set_selection_listener`` exactly once with
    its own callback. The adapter then calls that listener every time the live selection
    changes -- for the fake adapter this is ``FakeAdapter.set_selection`` (test-only, drives the
    listener synchronously); for FreeCAD this is the seam a ``Gui.Selection`` observer on the worker
    side must call into once wired (out of scope here, FreeCAD-side wiring)."""

    def set_selection_listener(self, listener: SelectionListener) -> None: ...


class ManualEditNotice(BaseModel, frozen=True):
    """A single change of the DESIGN the engineer made by hand in the CAD, reported out of band
    (``ManualEditProducer``): the raw fact, for a notice that reaches the agent with its next reply. Only changes
    of the model count (a property, an object added, removed or renamed, an undo, a sketch); the camera, the
    selection, visibility and colours never produce one.

    ``ref`` is the harness feature that owns the object (``artefact_kind`` ``feature``) or the CAD's own name of
    an object the harness never made (``object``); ``name`` is always the CAD name; ``document`` the document it
    is in; ``action`` is ``edited``, ``added``, ``removed`` or ``more`` (the closing notice of a long burst:
    ``changes`` says how many objects were left out)."""

    artefact_kind: str
    ref: str
    by: str
    reason: str
    changes: tuple[str, ...]
    document: str = ""
    name: str = ""
    action: str = "edited"


ManualEditListener = Callable[[ManualEditNotice], None]


class ProgressNotice(BaseModel, frozen=True):
    """How far a long request has got (``ProgressProducer``). ``phase`` names what the adapter is doing (``reading`` the
    file, ``preparing`` and ``building`` bodies, ``adopting`` and ``checking`` them, ``saving``); ``current`` and
    ``total`` count the items of the phase and are ``None`` when the CAD gives no count. ``elapsed_s``, ``cpu_s`` and
    ``memory_mb`` are measured from outside the CAD process by the adapter (the CAD may be busy inside one call for
    minutes); ``heartbeat`` marks a periodic notice that carries them, as opposed to one the CAD itself sent."""

    phase: str
    message: str = ""
    current: int | None = None
    total: int | None = None
    elapsed_s: float | None = None
    cpu_s: float | None = None
    memory_mb: float | None = None
    heartbeat: bool = False


ProgressListener = Callable[[ProgressNotice], None]


@runtime_checkable
class ProgressProducer(Protocol):
    """Optional adapter capability: an adapter whose long requests (``import_geometry``) can say how far they are. The
    session layer sets a listener before such a request and clears it (``None``) afterwards. The listener may be called
    from any thread and must not block."""

    def set_progress_listener(self, listener: ProgressListener | None) -> None: ...


@runtime_checkable
class ManualEditProducer(Protocol):
    """Optional adapter capability, same shape as ``SelectionProducer``: an adapter that can notice a
    hand edit to the live document out of band and push it without waiting for a tool call. The fake
    adapter implements this via ``notify_manual_edit`` (test-only); FreeCAD wires it from a document
    change observer on the visible worker. The listener may be called from any thread (FreeCAD's reader thread)
    and must not block; the MCP server queues the notices and puts them at the start of the next tool reply."""

    def set_manual_edit_listener(self, listener: ManualEditListener) -> None: ...


@runtime_checkable
class ConnectCancellable(Protocol):
    """Optional adapter capability: an adapter whose ``connect`` is slow (it starts a CAD process) can be told from
    another thread to give up. The CAD process being started is stopped, so none is left behind, and ``connect`` raises
    ``AdapterError``. Final: the adapter does not connect again afterwards."""

    def cancel_connect(self) -> None: ...


def tool(name: str, group: Group, *, optional: bool = False) -> Callable[[F], F]:
    """Register the decorated Adapter method in TOOL_REGISTRY using its annotations
    (request type, return type, docstring).
    """

    def decorator(func: F) -> F:
        signature = inspect.signature(func)
        request_type = signature.parameters["request"].annotation
        response_type = signature.return_annotation
        description = inspect.getdoc(func) or ""
        TOOL_REGISTRY[name] = ToolSpec(
            name=name,
            group=group,
            description=description.strip(),
            optional=optional,
            request_model=request_type,
            response_model=response_type,
        )
        return func

    return decorator


class Capabilities(BaseModel, frozen=True):
    tools: frozenset[str]


class Adapter(ABC):
    name: ClassVar[str]

    cad_key: ClassVar[str | None] = None
    """One stable key for the CAD software (lowercase, e.g. ``freecad``): the memory entries marked for a software are
    shown only to the adapter with that key. ``None`` (the default): the adapter still works, but sees only the entries
    that hold for every software."""

    conventions: Any = None
    """The conventions of the harness in force (a ``cotaforge.conventions.Conventions``): the server sets it; an adapter
    that applies one reads it when it makes the thing (a hole) and never keeps its own copy. ``None``: the defaults of
    the product."""

    # --- Session -----------------------------------------------------------

    @tool("connect", Group.SESSION)
    @abstractmethod
    def connect(self, request: ConnectRequest) -> SessionInfo:
        """Establish a session with the CAD application."""

    @tool("disconnect", Group.SESSION)
    @abstractmethod
    def disconnect(self, request: Empty) -> Ack:
        """Close the session."""

    @tool("new_document", Group.SESSION)
    @abstractmethod
    def new_document(self, request: NewDocumentRequest) -> DocumentInfo:
        """Create a new document and make it the active document."""

    @tool("set_active_document", Group.SESSION)
    @abstractmethod
    def set_active_document(self, request: SetActiveDocumentRequest) -> DocumentInfo:
        """Switch the active document."""

    @tool("active_document", Group.SESSION)
    @abstractmethod
    def active_document(self, request: Empty) -> DocumentInfo:
        """Return the currently active document."""

    @tool("set_timeout", Group.SESSION)
    @abstractmethod
    def set_timeout(self, request: SetTimeoutRequest) -> Ack:
        """Set the adapter's request timeout."""

    @tool("suppress_interaction", Group.SESSION)
    @abstractmethod
    def suppress_interaction(self, request: Empty) -> Ack:
        """Suppress interactive UI prompts for the remainder of the session."""

    @tool("restore_interaction", Group.SESSION)
    @abstractmethod
    def restore_interaction(self, request: Empty) -> Ack:
        """Restore interactive UI prompts."""

    display_name: ClassVar[str] = ""
    """How texts for the engineer name this CAD ('FreeCAD'); empty says 'this CAD'."""

    document_suffix: ClassVar[str] = ".bin"
    """File suffix of the adapter's native document (``save_document`` writes ``models/<plan_id><suffix>``)."""

    def suffix_for(self, kind: DocumentKind) -> str:
        """File suffix of a document of ``kind``. One suffix for every kind unless the CAD gives parts and assemblies
        different files (then override this; ``document_suffix`` stays the suffix of a part)."""
        return self.document_suffix

    @tool("save_document", Group.SESSION, optional=True)
    def save_document(self, request: SaveDocumentRequest) -> SaveDocumentResult:
        """Write the active document to ``path`` ATOMICALLY (a reader never sees a half-written file) with the neutral
        identity ``stamp`` inside it, and return the file's sha256 and size plus an opaque ``adapter_state`` (with its
        ``schema``) the caller keeps to re-bind handles when the file is reopened. When the file on disk is not the one
        ``expected_sha256`` names and the adapter cannot attribute the change to the engineer saving this same document,
        it refuses with ``details.reason=conflict`` instead of overwriting it. A failed save leaves the live document
        untouched and is recoverable. Adapters that cannot save say so; the caller then recovers progress by
        rebuilding."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="save_document: this adapter cannot save its document; progress is recovered by rebuilding",
                recoverable=False,
            )
        )

    @tool("open_document", Group.SESSION, optional=True)
    def open_document(self, request: OpenDocumentRequest) -> OpenDocumentResult:
        """Open a native file as the active document WITHOUT rebuilding it. With the ``adapter_state`` a previous
        ``save_document`` of that file returned, the adapter's handles are re-bound so the ids the caller recorded name
        the same entities again, and the re-bind report says what no longer matches (drift made by hand, objects the
        registry does not know). Without it (``adapter_state`` omitted) ANY native file is adopted, stamped or not: the
        adapter mints fresh handles for what the file contains (one feature record per solid body, listed by
        ``feature_tree`` and ``bodies_of``) and the report only carries notes. Never writes the file. Adapters that
        cannot reopen their documents say so; the caller then recovers progress by rebuilding."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="open_document: this adapter cannot reopen a saved document; progress is recovered by "
                "rebuilding",
                recoverable=False,
            )
        )

    @tool("import_geometry", Group.SESSION, optional=True)
    def import_geometry(self, request: ImportGeometryRequest) -> ImportGeometryResult:
        """Read a STEP or IGES file into a NEW document (``request.name``) that becomes the active one. The geometry
        keeps the coordinates of the file (no recentring) and is converted to millimetres; every solid is a body of its
        own (a STEP assembly gives several), and loose shells or faces are bodies too, reported as not closed. The
        bodies are references: they carry no history, like the bodies of an adopted file (``feature_tree`` lists one
        feature record per body). The result reports what was measured per body (solids, shells, faces, volume, box,
        valid, closed) and the length unit the file declared when it can be told. Nothing is written to disk. Adapters
        that cannot read foreign formats say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="import_geometry: this adapter cannot import STEP or IGES files",
                recoverable=False,
            )
        )

    @tool("close_document", Group.SESSION, optional=True)
    def close_document(self, request: Empty) -> Ack:
        """Close the active document in the CAD, discarding unsaved changes (the caller saves first), and forget its
        handles. Afterwards there is no active document until ``new_document`` or ``open_document``."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="close_document", recoverable=False))

    @tool("design_attributes", Group.SESSION, optional=True)
    def design_attributes(self, request: Empty) -> DesignAttributes:
        """Read the material and the process route the active document carries (stored inside the document, so they
        come back after a reopen and show any change made by hand or by a native fragment)."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="design_attributes", recoverable=False))

    @tool("set_design_attributes", Group.SESSION, optional=True)
    def set_design_attributes(self, request: SetDesignAttributesRequest) -> DesignAttributes:
        """Assign the material and/or the process route of the active document and return what it carries now."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="set_design_attributes", recoverable=False))

    # --- Health --------------------------------------------------------------

    @tool("canary", Group.HEALTH)
    @abstractmethod
    def canary(self, request: Empty) -> CanaryResult:
        """Run a lightweight health check against the CAD session."""

    @tool("validate_api", Group.HEALTH)
    @abstractmethod
    def validate_api(self, request: Empty) -> ApiValidation:
        """Validate that the adapter's API surface is usable."""

    # --- Construction ----------------------------------------------------------

    @tool("sketch", Group.CONSTRUCTION)
    @abstractmethod
    def sketch(self, request: SketchRequest) -> SketchResult:
        """Create a sketch of closed profiles on a plane or a planar face."""

    @tool("extrude", Group.CONSTRUCTION)
    @abstractmethod
    def extrude(self, request: ExtrudeRequest) -> OperationResult:
        """Extrude the closed profiles of a sketch along the sketch normal."""

    @tool("cut", Group.CONSTRUCTION)
    @abstractmethod
    def cut(self, request: CutRequest) -> OperationResult:
        """Cut material using the closed profiles of a sketch."""

    @tool("revolve", Group.CONSTRUCTION)
    @abstractmethod
    def revolve(self, request: RevolveRequest) -> OperationResult:
        """Revolve the closed profiles of a sketch about an axis."""

    @tool("hole", Group.CONSTRUCTION)
    @abstractmethod
    def hole(self, request: HoleRequest) -> OperationResult:
        """Create a hole into a planar face."""

    @tool("linear_pattern", Group.CONSTRUCTION)
    @abstractmethod
    def linear_pattern(self, request: LinearPatternRequest) -> OperationResult:
        """Pattern features along one or two linear directions."""

    @tool("circular_pattern", Group.CONSTRUCTION)
    @abstractmethod
    def circular_pattern(self, request: CircularPatternRequest) -> OperationResult:
        """Pattern features about an axis."""

    @tool("chamfer", Group.CONSTRUCTION)
    @abstractmethod
    def chamfer(self, request: ChamferRequest) -> OperationResult:
        """Chamfer the given edges."""

    @tool("fillet", Group.CONSTRUCTION)
    @abstractmethod
    def fillet(self, request: FilletRequest) -> OperationResult:
        """Fillet the given edges."""

    @tool("box", Group.CONSTRUCTION)
    @abstractmethod
    def box(self, request: BoxRequest) -> OperationResult:
        """Create a rectangular prism body."""

    @tool("cylinder", Group.CONSTRUCTION)
    @abstractmethod
    def cylinder(self, request: CylinderRequest) -> OperationResult:
        """Create a cylindrical body."""

    @tool("boolean", Group.CONSTRUCTION)
    @abstractmethod
    def boolean(self, request: BooleanRequest) -> OperationResult:
        """Combine bodies with a boolean operation."""

    @tool("move_body", Group.CONSTRUCTION)
    @abstractmethod
    def move_body(self, request: MoveBodyRequest) -> OperationResult:
        """Move a body by a transform."""

    @tool("copy_body", Group.CONSTRUCTION, optional=True)
    def copy_body(self, request: CopyBodyRequest) -> OperationResult:
        """Duplicate a body into a new, independent body placed by a transform (the IR ``copy_body``). The
        result carries the new body in ``created_bodies`` with its faces and edges; the source is untouched."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message=("copy_body: this adapter cannot duplicate bodies; rebuild the body with its own operations"),
                recoverable=False,
            )
        )

    @tool("shrink_body", Group.CONSTRUCTION, optional=True)
    def shrink_body(self, request: ShrinkBodyRequest) -> OperationResult:
        """Derive a NEW body from a body, scaled by a factor per axis (a mould cavity for a shrinking plastic) and LINKED to
        its source: when the source changes, the derived body follows. The result carries the new body in
        ``created_bodies``; the source is untouched. Adapters that cannot keep a link cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="shrink_body: this adapter cannot derive a scaled body linked to its source",
                recoverable=False,
            )
        )

    @tool("mould_split", Group.CONSTRUCTION, optional=True)
    def mould_split(self, request: MouldSplitRequest) -> OperationResult:
        """Cut the negative of a (shrunk) part out of an insert block and split it along the parting surface into two NEW
        solids, the cavity and the core, plus one new body per accepted slide. The source is untouched. The result
        carries the new bodies in ``created_bodies`` (cavity, core, then the slides in the order of their regions) and
        the parting line, the method and the checks in ``text``. Adapters that cannot build a parting surface cannot
        say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="mould_split: this adapter cannot split a block along a parting surface into core and cavity",
                recoverable=False,
            )
        )

    @tool("mould_component", Group.CONSTRUCTION, optional=True)
    def mould_component(self, request: MouldComponentRequest) -> OperationResult:
        """Build each part of a catalogue component (a plate, a pin, a bush, a ring) as a NEW body made of the CAD's own
        sketches and features, with its catalogue reference as properties. The result carries the new bodies in
        ``created_bodies`` (in the order of the parts). Adapters that cannot keep parametric history cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="mould_component: this adapter cannot build catalogue parts as native parametric bodies",
                recoverable=False,
            )
        )

    @tool("slide_mechanism", Group.CONSTRUCTION, optional=True)
    def slide_mechanism(self, request: SlideMechanismRequest) -> OperationResult:
        """Add the carrier, gibs, angle pin, locking heel, detent and wear plate of a slide of a ``mould_split`` step, as
        NEW native bodies, after checking the stroke, the wedge angle and the closed and open positions. The result
        carries the bodies in ``created_bodies`` and the checks in ``text``."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="slide_mechanism: this adapter cannot build the mechanism of a mould slide",
                recoverable=False,
            )
        )

    @tool("sweep", Group.CONSTRUCTION, optional=True)
    def sweep(self, request: SweepRequest) -> OperationResult:
        """Move the single closed profile of a sketch along a polyline path (the IR ``sweep``): the volume added is
        the profile area times the path length. Adapters that cannot build it say so instead of approximating."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="sweep: this adapter cannot sweep; build the shape from extrude/revolve segments",
                recoverable=False,
            )
        )

    @tool("draft", Group.CONSTRUCTION, optional=True)
    def draft(self, request: DraftRequest) -> OperationResult:
        """Tilt faces of a body by an angle about a neutral plane so a mould half can release the part (the IR
        ``draft``). After the op every drafted face's outward normal has a component sin(angle) along the pull
        direction; a shape left unchanged, an angle the kernel cannot build or faces leaning the wrong way must be an
        error. Adapters that cannot do it say so instead of approximating."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="draft: this adapter cannot taper faces; draw the taper into the sketch profile instead",
                recoverable=False,
            )
        )

    @tool("mirror", Group.CONSTRUCTION, optional=True)
    def mirror(self, request: MirrorRequest) -> OperationResult:
        """Reflect features across a plane into the same body (the IR ``mirror``): one new feature, the
        seeds stay. A seed that is not a feature of a feature-based body, or a reflection that changes nothing, must
        be an error. Adapters that cannot do it say so instead of approximating."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="mirror: this adapter cannot mirror features; repeat the feature with its own operations",
                recoverable=False,
            )
        )

    @tool("mirror_body", Group.CONSTRUCTION, optional=True)
    def mirror_body(self, request: MirrorBodyRequest) -> OperationResult:
        """Reflect a whole body across a plane into a new, independent body (the IR ``mirror_body``). The result
        carries the new body in ``created_bodies`` with its faces and edges; the source is untouched."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="mirror_body: this adapter cannot mirror a body; rebuild the mirrored half with its own steps",
                recoverable=False,
            )
        )

    @tool("split", Group.CONSTRUCTION, optional=True)
    def split(self, request: SplitRequest) -> OperationResult:
        """Cut a body in two along a plane (the IR ``split``). The source is consumed; the pieces are new, independent
        bodies reported in ``created_bodies`` as (positive, negative) -- only the kept one for ``keep`` positive or
        negative -- each with its faces and edges. A plane that does not cut the body must be an error. Adapters that
        cannot do it say so instead of approximating."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="split: this adapter cannot split a body; build each half with its own operations",
                recoverable=False,
            )
        )

    @tool("shell", Group.CONSTRUCTION, optional=True)
    def shell(self, request: ShellRequest) -> OperationResult:
        """Hollow the body the given faces belong to (the IR ``shell``): the faces are removed and every other face
        gets a wall of the given thickness, inward or outward. A thickness the kernel cannot build must be an
        error, never a silent no-op. Adapters that cannot do it say so instead of approximating."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="shell: this adapter cannot hollow a body; build the walls from extrude and cut instead",
                recoverable=False,
            )
        )

    @tool("edit_feature", Group.CONSTRUCTION, optional=True)
    def edit_feature(self, request: EditFeatureRequest) -> EditResult:
        """Edit the properties of EXISTING features in place from the full new request of each op, in ONE batch, and
        let the CAD recompute what depends on them instead of rebuilding everything after.
        An edit that breaks the model (an object the CAD flags invalid, a body that vanished or came apart into more
        solids than before, a placement that drifted) is rolled back and reported with a ``failure_kind``, never left
        half applied; effects that may be intended (a cut that no longer cuts, a new overlap between bodies) are flags
        on an applied edit: reported, not refused. A
        feature the adapter cannot edit in place (a static copy among its dependents, a property bound to an
        expression, an op kind it does not map) is refused with a reason per feature and nothing is touched: the
        caller rebuilds instead. ``dry_run`` only checks eligibility. Adapters that cannot edit in place say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message=(
                    "edit_feature: this adapter cannot edit features in place; rebuild the operations after the "
                    "one to change (undo_to + extend_plan) instead"
                ),
                recoverable=False,
            )
        )

    @tool("list_features", Group.QUERY, optional=True)
    def list_features(self, request: ListFeaturesRequest) -> FeatureList:
        """List the features of the active document as the CAD holds them, body by body with its Tip: name, type,
        label, whether it is the Tip, suppressed or shown, the parameters that matter, the edges or faces a dress-up
        works on and the harness record (step) that made it, if any. A feature made by hand, by ``run_native`` or in
        a file that was opened is listed like any other. ``feature`` asks for one in detail."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="list_features: this adapter cannot list the CAD's own features; use feature_tree",
                recoverable=False,
            )
        )

    @tool("remove_feature", Group.CONSTRUCTION, optional=True)
    def remove_feature(self, request: RemoveFeatureRequest) -> RunNativeResult:
        """Delete one feature in any position, the CAD's own way (a body's Tip moves back when it was the Tip, what is
        built on it is recomputed) as ONE undoable step. Other objects that need it are named and the delete is
        refused unless ``cascade``; a result that is not valid is rolled back and explained. Adapters that cannot do
        it say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="remove_feature: this adapter cannot delete a feature; undo the steps back to it instead",
                recoverable=False,
            )
        )

    @tool("set_feature_properties", Group.CONSTRUCTION, optional=True)
    def set_feature_properties(self, request: SetFeaturePropertiesRequest) -> RunNativeResult:
        """Set properties of an existing feature (the CAD's own names: Radius, Size, Length, Reversed, ...) whoever
        made it, as ONE undoable step. The value is validated against the property, the model is recomputed and
        checked, and a result that is not valid is rolled back and explained."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="set_feature_properties: this adapter cannot edit the properties of a feature",
                recoverable=False,
            )
        )

    @tool("focus_model", Group.VIEW, optional=True)
    def focus_model(self, request: FocusRequest) -> FocusResult:
        """Show each body's last solid feature (its Tip) and hide its sketches and intermediate features. Display
        only."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="focus_model: this adapter cannot change what the 3D view shows",
                recoverable=False,
            )
        )

    @tool("set_reference_visibility", Group.VIEW, optional=True)
    def set_reference_visibility(self, request: ReferenceVisibilityRequest) -> ReferenceVisibilityResult:
        """Show or hide reference geometry (datum planes, axes, points, sketches) by name. Display only. The adapter
        hides a reference by itself once a feature has used it, except what the engineer showed by hand; this is how
        the agent looks at one again."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="set_reference_visibility: this adapter cannot change what the 3D view shows",
                recoverable=False,
            )
        )

    # --- Sheet metal (optional) -----------------------------------------------------

    @tool("base_flange", Group.SHEET_METAL, optional=True)
    def base_flange(self, request: BaseFlangeRequest) -> OperationResult:
        """Start a sheet-metal part (the IR ``base_flange``): the closed profiles of a sketch (the largest is the
        outline, the others holes) become a flat sheet of the given thickness, as a NEW body that later sheet-metal ops
        bend. Adapters without sheet-metal support, or whose sheet-metal add-on is not installed, say so instead of
        approximating."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="base_flange: this adapter has no sheet-metal support; model the sheet as an extrude",
                recoverable=False,
            )
        )

    @tool("edge_flange", Group.SHEET_METAL, optional=True)
    def edge_flange(self, request: EdgeFlangeRequest) -> OperationResult:
        """Bend a wall out of one straight edge of a sheet-metal body (the IR ``edge_flange``): angle, bend radius and
        length are the given ones, the bend is a true cylindrical bend. An edge of a body that is not sheet metal, or
        a wall the kernel cannot build, must be an error."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="edge_flange: this adapter has no sheet-metal support",
                recoverable=False,
            )
        )

    @tool("unfold", Group.SHEET_METAL, optional=True)
    def unfold(self, request: UnfoldRequest) -> OperationResult:
        """Make the flat pattern of a sheet-metal body (the IR ``unfold``) with the given K-factor, keeping the given
        planar face fixed. The flat pattern is not part of the document's solid: its measured size comes back in
        ``OperationResult.flat_pattern``, and ``export_dxf`` takes the feature as its source."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="unfold: this adapter has no sheet-metal support",
                recoverable=False,
            )
        )

    # --- Assemblies -------------------------------------------------------------

    @tool("insert_component", Group.ASSEMBLIES)
    @abstractmethod
    def insert_component(self, request: InsertComponentRequest) -> OperationResult:
        """Insert a part document as a component of the active assembly."""

    @tool("fix_component", Group.ASSEMBLIES)
    @abstractmethod
    def fix_component(self, request: FixComponentRequest) -> OperationResult:
        """Fix a component in place."""

    @tool("place_component", Group.ASSEMBLIES, optional=True)
    def place_component(self, request: PlaceComponentRequest) -> OperationResult:
        """Move a component to an absolute pose in assembly space (origin plus rotation), keeping the part and its
        mates' record. Adapters that cannot say so; the caller then inserts the part again at the new pose."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="place_component: this adapter cannot move a component; insert it again at the new pose",
                recoverable=False,
            )
        )

    @tool("add_joint", Group.ASSEMBLIES, optional=True)
    def add_joint(self, request: AddJointRequest) -> OperationResult:
        """Join a component to another (or to the ground): fixed, revolute about an axis or slider along a direction,
        with limits on the value. The component keeps its pose, which is the value 0 of the joint."""
        raise _joints_not_supported("add_joint")

    @tool("set_joint", Group.ASSEMBLIES, optional=True)
    def set_joint(self, request: SetJointRequest) -> OperationResult:
        """Move a joint to a value (degrees of a revolute, mm of a slider): the joined component is placed by it."""
        raise _joints_not_supported("set_joint")

    @tool("joint_info", Group.ASSEMBLIES, optional=True)
    def joint_info(self, request: JointRequest) -> JointInfo:
        """A joint as it is now."""
        raise _joints_not_supported("joint_info")

    @tool("sweep_joint", Group.ASSEMBLIES, optional=True)
    def sweep_joint(self, request: SweepJointRequest) -> JointSweepResult:
        """Drive a joint through its positions and measure interference and distance at each; read-only (the joint
        ends at the value it had). At most ``MAX_SWEEP_POSITIONS`` positions."""
        raise _joints_not_supported("sweep_joint")

    @tool("mate_concentric", Group.ASSEMBLIES)
    @abstractmethod
    def mate_concentric(self, request: MateConcentricRequest) -> OperationResult:
        """Mate two cylindrical/conical faces concentrically."""

    @tool("mate_coincident", Group.ASSEMBLIES)
    @abstractmethod
    def mate_coincident(self, request: MateCoincidentRequest) -> OperationResult:
        """Mate two planar faces coincident."""

    @tool("mate_distance", Group.ASSEMBLIES)
    @abstractmethod
    def mate_distance(self, request: MateDistanceRequest) -> OperationResult:
        """Mate two planar faces at a fixed distance."""

    @tool("check_interference", Group.ASSEMBLIES)
    @abstractmethod
    def check_interference(self, request: InterferenceRequest) -> InterferenceResult:
        """Check interference between components."""

    @tool("component_bounding_box", Group.ASSEMBLIES)
    @abstractmethod
    def component_bounding_box(self, request: ComponentRequest) -> BoundingBox:
        """Return the bounding box of a component in assembly space."""

    @tool("component_transform", Group.ASSEMBLIES)
    @abstractmethod
    def component_transform(self, request: ComponentRequest) -> Transform:
        """Return the transform of a component in assembly space."""

    # --- Provenance -------------------------------------------------------------

    @tool("faces_of", Group.PROVENANCE)
    @abstractmethod
    def faces_of(self, request: FeatureRequest) -> FacesResult:
        """Return the faces created and/or modified by a feature."""

    @tool("edges_between", Group.PROVENANCE)
    @abstractmethod
    def edges_between(self, request: EdgesBetweenRequest) -> EdgesResult:
        """Return the edges shared by two faces."""

    @tool("edges_of", Group.PROVENANCE)
    @abstractmethod
    def edges_of(self, request: FaceRequest) -> EdgesResult:
        """Return the edges bounding a face."""

    @tool("vertices_of", Group.PROVENANCE)
    @abstractmethod
    def vertices_of(self, request: EdgeRequest) -> VerticesResult:
        """Return the start and end vertices of an edge."""

    @tool("vertex_at", Group.PROVENANCE)
    @abstractmethod
    def vertex_at(self, request: VertexAtRequest) -> VerticesResult:
        """Return the vertex shared by three faces."""

    @tool("bodies_of", Group.PROVENANCE)
    @abstractmethod
    def bodies_of(self, request: FeatureRequest) -> BodiesResult:
        """Return the bodies created and/or modified by a feature."""

    # --- Query -----------------------------------------------------------------

    @tool("is_valid", Group.QUERY)
    @abstractmethod
    def is_valid(self, request: ValidityRequest) -> ValidityResult:
        """Validate the active document's geometry."""

    @tool("mass_properties", Group.QUERY)
    @abstractmethod
    def mass_properties(self, request: MassRequest) -> MassProperties:
        """Return mass properties of a body, component or the whole document."""

    @tool("bounding_box", Group.QUERY)
    @abstractmethod
    def bounding_box(self, request: MassRequest) -> BoundingBox:
        """Return the bounding box of a body, component or the whole document."""

    @tool("feature_tree", Group.QUERY)
    @abstractmethod
    def feature_tree(self, request: Empty) -> FeatureTree:
        """Return the active document's feature tree."""

    @tool("measure_bodies", Group.QUERY, optional=True)
    def measure_bodies(self, request: Empty) -> MeasureBodiesResult:
        """READ-ONLY reading of the whole active part: per live body its volume, extent, solids, faces and edges and
        whether its shape is valid; how much each subtraction removes; which bodies overlap. A rebuild's per-operation
        steps only check their own operation's result, so the caller reads the part before and after a change and
        compares (a body that came apart, a cut that no longer cuts, a new overlap). Adapters that cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="measure_bodies: this adapter cannot read the part body by body",
                recoverable=False,
            )
        )

    @tool("body_interference", Group.QUERY, optional=True)
    def body_interference(self, request: BodyInterferenceRequest) -> BodyInterferenceResult:
        """READ-ONLY: every pair of live bodies of the active part (or of ``bodies``) measured against each other, the
        volume they share and their minimum distance, so a part made of several bodies can be checked for overlap,
        contact and clearance (``check_interference`` covers the components of an assembly). A value the adapter cannot
        measure for a pair is ``None`` with a note, never 0. Adapters that cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="body_interference: this adapter cannot measure the bodies of a part against each other",
                recoverable=False,
            )
        )

    @tool("list_holes", Group.QUERY, optional=True)
    def list_holes(self, request: ListHolesRequest) -> ListHolesResult:
        """READ-ONLY: the cylindrical holes of a body (or of every live body): concave cylindrical faces (material
        outside the cylinder) on one axis, with diameter, axis, depth along the axis, whether the hole goes through
        (both ends open), is blind (one end closed) or cannot be told, and the ids of the faces that make it. A convex
        cylinder (a pin, a boss) is not a hole. Adapters that cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="list_holes: this adapter cannot list holes; inspect the cylindrical faces instead",
                recoverable=False,
            )
        )

    @tool("draft_analysis", Group.QUERY, optional=True)
    def draft_analysis(self, request: DraftAnalysisRequest) -> DraftAnalysisResult:
        """READ-ONLY: the draft of every face of a body (or of every live body) against a pull direction: the smallest
        lean of each face from it, its area, and whether it can be reached from the pull direction at all (an
        undercut). Planes are exact, curved faces are sampled (the result says how). With ``paint`` the faces are
        coloured on screen. Adapters that cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="draft_analysis: this adapter cannot measure the draft of the faces of a body",
                recoverable=False,
            )
        )

    @tool("parting_analysis", Group.QUERY, optional=True)
    def parting_analysis(self, request: PartingAnalysisRequest) -> PartingAnalysisResult:
        """READ-ONLY: the parting line of a moulded part against a pull direction (the edge loops between the faces the
        core forms and the ones the cavity forms), the walls with no draft that need the engineer's decision, the faces
        the silhouette runs across, and one slide proposal per undercut region. Adapters that cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="parting_analysis: this adapter cannot find the parting line of a body",
                recoverable=False,
            )
        )

    @tool("tool_overlap", Group.QUERY, optional=True)
    def tool_overlap(self, request: ToolOverlapRequest) -> ToolOverlapResult:
        """READ-ONLY: the volume of a tool solid (a sketch swept along its normal, or a hole) and the part of it inside
        the body it would cut or join, from one boolean with the part as it is now. Adapters that cannot say so."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message="tool_overlap: this adapter cannot intersect a tool with the part",
                recoverable=False,
            )
        )

    @tool("face_info", Group.QUERY)
    @abstractmethod
    def face_info(self, request: FaceRequest) -> FaceInfo:
        """Return descriptor information about a face."""

    @tool("edge_info", Group.QUERY)
    @abstractmethod
    def edge_info(self, request: EdgeRequest) -> EdgeInfo:
        """Return descriptor information about an edge."""

    def faces_info(self, request: FacesInfoRequest) -> FacesResult:
        """OPTIONAL BATCH HOOK, not a tool: the descriptors of several faces in one call, in the order asked, each as
        ``face_info`` would give it. The default asks ``face_info`` once per face, so an adapter that cannot do better
        needs to override nothing; an adapter for which every call costs (a CAD behind a process boundary or a COM
        thread) answers all of them in one round trip. It raises what ``face_info`` raises for the first face it
        cannot describe."""
        return FacesResult(
            faces=tuple(
                self.face_info(FaceRequest(operation_id=request.operation_id, face=handle)) for handle in request.faces
            )
        )

    def edges_info(self, request: EdgesInfoRequest) -> EdgesResult:
        """OPTIONAL BATCH HOOK, not a tool: the descriptors of several edges in one call, in the order asked, each as
        ``edge_info`` would give it (see ``faces_info``)."""
        return EdgesResult(
            edges=tuple(
                self.edge_info(EdgeRequest(operation_id=request.operation_id, edge=handle)) for handle in request.edges
            )
        )

    # --- References --------------------------------------------------------------

    @tool("persistent_reference", Group.REFERENCES)
    @abstractmethod
    def persistent_reference(self, request: EntityRequest) -> PersistentRef:
        """Return a persistent, adapter-specific reference token for an entity."""

    @tool("resolve_reference", Group.REFERENCES)
    @abstractmethod
    def resolve_reference(self, request: ResolveRequest) -> EntityResult:
        """Resolve a persistent reference back to a live handle."""

    @tool("signature", Group.REFERENCES)
    @abstractmethod
    def signature(self, request: EntityRequest) -> GeometricSignature:
        """Return the geometric signature of an entity."""

    # --- Undo ------------------------------------------------------------------

    @tool("undo_last", Group.UNDO)
    @abstractmethod
    def undo_last(self, request: Empty) -> UndoResult:
        """Undo the last feature."""

    # --- Construction (optional) -----------------------------------------------------

    @tool("datum_plane", Group.CONSTRUCTION, optional=True)
    def datum_plane(self, request: DatumPlaneRequest) -> DatumPlaneResult:
        """Create a fixed-pose datum plane (the IR ``ref_plane``): reproduce the requested frame exactly and
        return its handle and the frame realised. Creates no feature and no feature-tree node; a sketch
        may then target it."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message=(
                    "datum_plane: this adapter cannot create datum planes, so ref_plane is not available; "
                    "sketch on a base plane or an existing face instead"
                ),
                recoverable=False,
            )
        )

    @tool("datum_axis", Group.CONSTRUCTION, optional=True)
    def datum_axis(self, request: DatumAxisRequest) -> DatumAxisResult:
        """Create a fixed-pose datum axis (the IR ``ref_axis``): reproduce the requested axis exactly and
        return its handle and the axis realised. Creates no feature and no feature-tree node."""
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.not_supported,
                message=(
                    "datum_axis: this adapter cannot create datum axes, so ref_axis is not available; "
                    "pass a cylindrical/conical face, an edge or a literal {origin, direction} axis directly where the "
                    "operation takes an axis (ref_plane normal_to/angle, circular_pattern) instead"
                ),
                recoverable=False,
            )
        )

    # --- Query (optional) --------------------------------------------------------

    @tool("sketch_status", Group.QUERY, optional=True)
    def sketch_status(self, request: SketchStatusRequest) -> SketchStatusResult:
        """Degrees of freedom, over-constraint and conflicting/redundant constraints of a sketch the plan
        created. Read-only; never edits the sketch."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="sketch_status", recoverable=False))

    # --- Changes (optional) --------------------------------------------------------

    @tool("document_events", Group.CHANGES, optional=True)
    def document_events(self, request: Empty) -> EventsResult:
        """Return document change events since the last poll."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="document_events", recoverable=False))

    @tool("tree_diff", Group.CHANGES, optional=True)
    def tree_diff(self, request: TreeDiffRequest) -> TreeDiff:
        """Return the feature-tree diff since a given feature."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="tree_diff", recoverable=False))

    # --- View (optional) -------------------------------------------------------------

    @tool("set_view", Group.VIEW, optional=True)
    def set_view(self, request: ViewRequest) -> Ack:
        """Set the active viewport orientation and/or fit the model (or just some bodies) in it."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="set_view", recoverable=False))

    @tool("capture", Group.VIEW, optional=True)
    def capture(self, request: CaptureRequest) -> Ack:
        """Capture a screenshot of the active viewport."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="capture", recoverable=False))

    @tool("style_bodies", Group.VIEW, optional=True)
    def style_bodies(self, request: StyleRequest) -> Ack:
        """Display only: set the transparency, colour and/or visibility of bodies in the viewport (nothing of the model
        or the saved file changes). Adapters without a viewport say so."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="style_bodies", recoverable=False))

    @tool("section_plane", Group.VIEW, optional=True)
    def section_plane(self, request: SectionRequest) -> Ack:
        """Display only: a clipping plane in the viewport (axis + offset) that cuts the model away beyond it so the
        inside shows, or none (axis omitted) to remove it. Adapters without a viewport say so."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="section_plane", recoverable=False))

    @tool("window", Group.VIEW, optional=True)
    def window(self, request: WindowRequest) -> WindowResult:
        """Display only: where the CAD's window is on the screen, moving it, sharing the screen between several CAD
        windows, and a capture of the window itself. Nothing of the model or the plan changes."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="window", recoverable=False))

    # --- Drawings (optional) -------------------------------------------------------------

    @tool("create_drawing", Group.DRAWING, optional=True)
    def create_drawing(self, request: DrawingRequest) -> DrawingResult:
        """Build (or rebuild from scratch) a drawing sheet of the active document from a bundled template: first-angle
        front, top and side views plus an isometric one, the overall width/height/depth dimensions and the title block
        (Spanish by default, with a revision table). A sheet name makes another sheet of the same document. It returns
        only once the CAD has FINISHED computing every view (never earlier: working on a view still being computed
        crashes some CADs); a view that does not finish in time is an error naming it."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="create_drawing", recoverable=False))

    @tool("annotate_drawing", Group.DRAWING, optional=True)
    def annotate_drawing(self, request: AnnotateDrawingRequest) -> DrawingResult:
        """Add or remove the items of a drawing sheet (hole callouts, dimensions between features, datum dimensions,
        notes, revisions), or lay its views out again after the part changed. The values of every dimension are
        measured on the model each time the sheet is drawn, never taken from the item."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="annotate_drawing", recoverable=False))

    @tool("describe_drawing", Group.DRAWING, optional=True)
    def describe_drawing(self, request: DescribeDrawingRequest) -> DescribeDrawingResult:
        """List the drawing sheets of the active document with their title-block fields and items, and what each item
        does against the part now."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="describe_drawing", recoverable=False))

    @tool("capture_drawing", Group.DRAWING, optional=True)
    def capture_drawing(self, request: CaptureDrawingRequest) -> CaptureDrawingResult:
        """Render a drawing sheet as it is (views, dimensions, annotations, title block) to a PNG file, from the same
        rendering as the exported PDF; the result says what it drew and what it could not."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="capture_drawing", recoverable=False))

    @tool("export_drawing", Group.DRAWING, optional=True)
    def export_drawing(self, request: ExportDrawingRequest) -> ExportDrawingResult:
        """Write ONE drawing sheet to a PDF or a DXF (one drawing, one sheet, one file); it never depends on a sheet
        being open in a window."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="export_drawing", recoverable=False))

    # --- Selection (optional) ---------------------------------------------------------------

    @tool("get_selection", Group.SELECTION, optional=True)
    def get_selection(self, request: Empty) -> SelectionResult:
        """Current CAD UI selection (face/edge/vertex), as stable entity refs the agent must act on --
        never the CAD's raw sub-element names, which are not stable across recomputes. Empty when
        nothing is selected. ``not_supported`` on an adapter with no UI selection to read (headless)."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="get_selection", recoverable=False))

    @tool("show_entities", Group.SELECTION, optional=True)
    def show_entities(self, request: ShowEntitiesRequest) -> ShowEntitiesResult:
        """Point the engineer at already-resolved entities -- the agent-to-engineer counterpart of
        ``get_selection``. Never touches the CAD's own UI selection (the engineer's pick stays whatever it
        was). ``not_supported`` on an adapter with no way at all to show anything;
        an adapter that can only sometimes show them (e.g. FreeCAD headless) still returns a
        ``ShowEntitiesResult`` with ``shown="unavailable"`` rather than raising."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="show_entities", recoverable=False))

    # --- Export (optional) ------------------------------------------------------------

    @tool("export_macro", Group.EXPORT, optional=True)
    def export_macro(self, request: ExportRequest) -> Ack:
        """Export a replay macro of the session."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="export_macro", recoverable=False))

    @tool("export_step", Group.EXPORT, optional=True)
    def export_step(self, request: ExportRequest) -> Ack:
        """Export the active document as STEP."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="export_step", recoverable=False))

    @tool("export_parasolid", Group.EXPORT, optional=True)
    def export_parasolid(self, request: ExportRequest) -> Ack:
        """Export the active document (a part or an assembly) as Parasolid: the file extension picks text (``.x_t``) or
        binary (``.x_b``)."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="export_parasolid", recoverable=False))

    @tool("export_mesh", Group.EXPORT, optional=True)
    def export_mesh(self, request: ExportMeshRequest) -> Ack:
        """Export the active document as a tessellated mesh (STL) for previews."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="export_mesh", recoverable=False))

    @tool("export_dxf", Group.EXPORT, optional=True)
    def export_dxf(self, request: ExportDxfRequest) -> Ack:
        """Export 2D geometry as a DXF for a cutting shop, at real scale in mm with its lower-left corner at the
        origin. The source is the outline of a PLANAR face (every loop: the outer contour and the holes, arcs and
        circles kept as such where the CAD allows) projected into the face's own plane, or the flat pattern of an
        ``unfold``; a face that is not planar is an error, never approximated."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="export_dxf", recoverable=False))

    # --- Native (optional) ------------------------------------------------------------------

    @tool("run_native", Group.NATIVE, optional=True)
    def run_native(self, request: RunNativeRequest) -> RunNativeResult:
        """Run a fragment of the CAD's own scripting language as ONE undoable step. Never rejects code for
        style, size or a missing intent (those are warnings); a fragment that raises or leaves something
        broken is rolled back and explained. Returns what was created/modified/removed, the volume and
        box change, anything left broken, the captured output and the error with a hint."""
        raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="run_native", recoverable=False))

    def profile_hints(self) -> tuple[str, ...]:
        """Short ``prefer: ...`` / ``avoid: ...`` lines about this CAD, for the agent's always-loaded profile."""
        return ()

    def known_traps(self) -> tuple[str, ...]:
        """Known pitfalls of this CAD (one sentence each), for the on-demand guide."""
        return ()

    def guide_topic(self, topic: str) -> str | None:
        """The text of the guide topic ``topic`` (``"drawing"``, ``"drawing:items"``, ``"terms"``...) as it is true of
        THIS CAD, or ``None`` to use the server's default text for it. The server asks before it answers
        ``cad_guide``; a returned text replaces the default whole. ``note:<id>`` keys are sentences that go inside the
        server's own text, ``sentence:<id>`` ones replace a sentence of the ``traps`` and ``native`` topics that
        describes FreeCAD (``""`` drops it; see ``guide.TRAP_SENTENCES``), and ``tool:<name>`` the description of an
        MCP tool of that name."""
        return None

    def import_suffixes(self) -> tuple[str, ...]:
        """File suffixes (lowercase, with the dot) ``import_geometry`` reads beyond STEP and IGES, e.g. ``.x_t``. The
        server lets such a file through to the adapter and refuses the rest. Empty (the default) adds nothing."""
        return ()

    def save_methods(self) -> tuple[str, ...]:
        """Names of the calls a ``run_native`` fragment saves a document with in this CAD (``SaveAs3``...), beyond
        FreeCAD's ``saveAs``, ``saveCopy`` and ``saveDocument``, which the safety scan always knows. The scan asks where
        such a call writes. Empty (the default) adds nothing."""
        return ()

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        """Operations this CAD adds to the common vocabulary, or replaces one with (same name): each has its own
        model of arguments, a guide text and the adapter method that runs it. They go through the same ``op`` tool, so
        they are validated, undone, recorded in the plan, saved and resumed like a common operation. Empty (the
        default) changes nothing the agent sees."""
        return ()

    def check_notes(self) -> tuple[str, ...]:
        """Lines this CAD adds to the ``check`` report about the active document, measured from it (one line each);
        they inform and never block an export. Adapters with nothing to add return nothing."""
        return ()

    def notices(self) -> Sequence[str]:
        """What this adapter did by itself behind the engineer's back since the last time it was asked (a dialog it
        closed, say), one sentence each. The server asks after connecting and after every call, shows each sentence
        to the agent once and does not ask for it again, so the adapter hands them over and forgets them (a second call
        returns only what came after). It must be quick and never raise. Nothing by default."""
        return ()

    def session_alive(self) -> bool:
        """Cheap check, no round trip: False once the CAD process this adapter launched has ended (a crash, or a kill
        after a timeout). The caller may then ``connect`` again; everything that was not saved is gone. Adapters
        without a process of their own are always alive."""
        return True

    def native_environment(self) -> tuple[str, ...]:
        """What a ``run_native`` fragment can use without importing (names and helpers) and the CAD-specific rules
        for writing one, one line each, for the on-demand guide. Empty for an adapter without ``run_native``."""
        return ()

    def unavailable_tools(self) -> dict[str, str]:
        """Optional tools this CAD could offer but cannot right now (a plugin the engineer has not installed), tool
        name -> why, and how to get it. They are absent from ``capabilities()``; the server uses this to explain the
        gap instead of leaving the agent with an op that silently does not exist."""
        return {}

    def assumed_tools(self) -> frozenset[str]:
        """Tools ``capabilities()`` lists on trust, because only the running CAD can confirm them (an add-on that may
        not be installed). What the server tells the agent before the CAD is up must not state them as a fact."""
        return frozenset()

    def capabilities(self) -> Capabilities:
        """Registry minus the optional tools that were not overridden by this adapter."""
        tools: set[str] = set()
        for tool_name, spec in TOOL_REGISTRY.items():
            if not spec.optional:
                tools.add(tool_name)
                continue
            base_method = getattr(Adapter, tool_name, None)
            own_method = getattr(type(self), tool_name, None)
            if own_method is not None and own_method is not base_method:
                tools.add(tool_name)
        return Capabilities(tools=frozenset(tools))
