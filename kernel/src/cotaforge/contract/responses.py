"""Response models: OperationResult, session/health/construction results, provenance and query
results, reference results, undo/changes results (contract/responses.py).

Assembly-specific responses (InterferenceResult, InterferencePair) live in assembly.py.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field

from cotaforge.contract.descriptors import BodyInfo, EdgeInfo, FaceInfo, FeatureNode, VertexInfo
from cotaforge.contract.drawing_items import canonical_author
from cotaforge.contract.enums import CurveType, DocumentKind, JointType, SurfaceType
from cotaforge.contract.geometry import Axis, SketchFrame, Vec3
from cotaforge.contract.handles import (
    AnyHandle,
    BodyHandle,
    ComponentHandle,
    DatumAxisHandle,
    DatumPlaneHandle,
    DocumentHandle,
    EdgeHandle,
    FaceHandle,
    FeatureHandle,
    JointHandle,
    MateHandle,
    SketchHandle,
)


class Ack(BaseModel, frozen=True):
    pass


class JointInfo(BaseModel, frozen=True):
    """A joint as it is now: its ``axis`` in assembly axes, the ``value`` it is at and the limits of that value
    (``unit``: ``deg`` of a revolute, ``mm`` of a slider, empty for a fixed joint). ``a`` is ``None`` for the ground."""

    handle: JointHandle
    joint_type: JointType
    a: ComponentHandle | None = None
    b: ComponentHandle
    axis: Axis | None = None
    value: float = 0.0
    min_value: float | None = None
    max_value: float | None = None
    unit: str = ""


class SessionInfo(BaseModel, frozen=True):
    adapter: str
    version: str
    units: Literal["mm"]


class DocumentInfo(BaseModel, frozen=True):
    handle: DocumentHandle
    kind: DocumentKind
    name: str
    units: Literal["mm"] = "mm"


class CanaryResult(BaseModel, frozen=True):
    healthy: bool
    checks: dict[str, bool]
    duration_ms: float


class ApiValidation(BaseModel, frozen=True):
    ok: bool
    problems: tuple[str, ...]


class SketchResult(BaseModel, frozen=True):
    sketch: SketchHandle
    frame: SketchFrame
    profile_areas_mm2: tuple[float, ...]
    rebuild_ok: bool = True
    """Lets an adapter report a sketch op's own
    level-1 failure (e.g. the fake's injected ``underdefined_sketch`` fault) without a feature -- the
    executor's ``_normalise`` reads this instead of assuming ``True``. Defaults to ``True`` so every
    adapter that never sets it (FreeCAD) keeps today's behaviour."""
    warnings: tuple[str, ...] = ()
    """What the adapter wants the agent to read in the reply of the ``sketch`` step (a coincidence it noticed, a
    dimension it chose not to add): the executor carries it into ``OperationResult.warnings``, which the reply shows."""


class DatumPlaneResult(BaseModel, frozen=True):
    datum: DatumPlaneHandle
    frame: SketchFrame
    """The pose the adapter actually realised; Level 2 compares it with the requested one."""
    rebuild_ok: bool = True


class DatumAxisResult(BaseModel, frozen=True):
    datum: DatumAxisHandle
    axis: Axis
    """The axis the adapter actually realised; Level 2 compares it with the requested one."""
    rebuild_ok: bool = True


class FlatPattern(BaseModel, frozen=True):
    """What an unfolded sheet-metal part measures, read from the flat pattern itself (never from the folded part).
    ``size_mm`` is the bounding rectangle of the flat outline, longer side first; ``area_mm2`` is the area of the
    flat blank (holes excluded) and ``volume_mm3`` the volume of the flat solid."""

    size_mm: tuple[float, float]
    thickness_mm: float
    area_mm2: float
    volume_mm3: float
    valid: bool = True


class OperationResult(BaseModel, frozen=True):
    operation_id: str
    feature: FeatureHandle | None
    rebuild_ok: bool
    created_faces: tuple[FaceHandle, ...] = ()
    modified_faces: tuple[FaceHandle, ...] = ()
    created_edges: tuple[EdgeHandle, ...] = ()
    created_bodies: tuple[BodyHandle, ...] = ()
    modified_bodies: tuple[BodyHandle, ...] = ()
    sketch: SketchHandle | None = None
    place: SketchFrame | None = None
    """Set by a ``hole`` on a planar face: the frame its position was measured from (see ``HoleRequest.origin``). The
    executor keeps it so that a rebuild puts the hole where it stood."""
    component: ComponentHandle | None = None
    mate: MateHandle | None = None
    joint: JointInfo | None = None
    instance_count: int | None = None
    face_count: int | None = None
    edge_count: int | None = None
    """Set by ``insert_component`` only: the faces and edges of the part the component stands for, COUNTED. They are not
    in ``created_faces`` / ``created_edges``: a vendor part has tens of thousands, and the executor describes every
    listed entity after the step. ``faces_of`` of the step lists them when asked."""
    duration_ms: float = 0.0
    warnings: tuple[str, ...] = ()
    skipped_edges: tuple[EdgeHandle, ...] = ()
    """Set by a ``chamfer`` / ``fillet`` with ``skip_failing``: the requested edges the CAD could not take and left out
    (their ids as they were in the request)."""
    flat_pattern: FlatPattern | None = None
    """Set by ``unfold`` only: the measured facts of the flat pattern it made."""
    text: str = ""
    """Free-form text the operation reports (a list, a table, a status): the reply prints it line by line as what it
    is, not among the warnings. What an operation of the adapter that only reads (``OperationSpec.read_only``)
    answers with."""


class FacesResult(BaseModel, frozen=True):
    faces: tuple[FaceInfo, ...]


class EdgesResult(BaseModel, frozen=True):
    edges: tuple[EdgeInfo, ...]


class VerticesResult(BaseModel, frozen=True):
    vertices: tuple[VertexInfo, ...]


class BodiesResult(BaseModel, frozen=True):
    bodies: tuple[BodyInfo, ...]


class ValidityResult(BaseModel, frozen=True):
    valid: bool
    problems: tuple[str, ...]
    unchecked: tuple[str, ...] = ()
    checked: int | None = None
    """Bodies the cheap check skipped (big imported surface bodies): not reported as problems, not vouched for either;
    ValidityRequest(deep=True) checks them."""


class FeatureTree(BaseModel, frozen=True):
    nodes: tuple[FeatureNode, ...]


class GeometricSignature(BaseModel, frozen=True):
    kind: str
    surface: SurfaceType | None = None
    curve: CurveType | None = None
    normal: Vec3 | None = None
    axis: Axis | None = None
    center: Vec3 | None = None
    radius: float | None = None
    area_mm2: float | None = None
    length_mm: float | None = None


class PersistentRef(BaseModel, frozen=True):
    adapter: str
    token: str
    signature: GeometricSignature


class SheetRef(BaseModel, frozen=True):
    """An item of a drawing sheet that refers to a selected model entity (``SelectionEntry.on_sheet``): the sheet, the
    item id and type, and the text the sheet prints for it (``70``, ``2x Ø6 pasante``)."""

    sheet: str
    item: str
    item_type: str
    text: str = ""


class SheetHole(BaseModel, frozen=True):
    """A hole of the part as a sheet pick reports it (the entry of ``list_holes``): its size, axis and wall faces."""

    diameter_mm: float
    through: bool | None = None
    origin: Vec3
    direction: Vec3
    faces: tuple[str, ...] = ()


class SheetPick(BaseModel, frozen=True):
    """One thing of a drawing sheet the engineer selected in the sheet window (``get_selection`` on a TechDraw sheet):
    ``kind`` is ``item`` (a dimension, callout, symbol... the agent put on the sheet: ``item`` is its id and
    ``item_type`` its type), ``view`` (a whole view), ``edge`` / ``vertex`` (a line or a point of a view: ``view`` names
    it) or ``other``. ``text`` is what the sheet prints, ``detail`` what it measures or is. ``faces`` / ``edges`` /
    ``holes`` are the model elements it corresponds to, by the ids the rest of the tools use."""

    sheet: str
    kind: Literal["item", "view", "edge", "vertex", "other"]
    label: str
    item: str | None = None
    item_type: str | None = None
    view: str | None = None
    text: str = ""
    detail: str = ""
    faces: tuple[str, ...] = ()
    edges: tuple[str, ...] = ()
    holes: tuple[SheetHole, ...] = ()


class SelectionEntry(BaseModel, frozen=True):
    """One entity of the CAD's current UI selection (``get_selection``, the CAD-selection
    seam): the harness's stable ``ref`` is the identity the agent must act on -- ``label`` is the raw
    CAD name (e.g. FreeCAD's ``Face6``) purely for display, never for re-identifying the entity later
    (topological naming is not stable across recomputes). ``on_sheet`` lists the items of the drawing sheets that
    refer to the entity (empty when there is no sheet or nothing refers to it)."""

    ref: PersistentRef
    kind: Literal["face", "edge", "vertex"]
    label: str
    owner_label: str = Field(default="", description="Display name of the owning feature/body, when known")
    body: BodyHandle | None = None
    feature: FeatureHandle | None = None
    on_sheet: tuple[SheetRef, ...] = ()


class UnresolvedSelection(BaseModel, frozen=True):
    """One CAD UI selection item ``get_selection`` could not turn into a stable ref -- a sketch, a datum,
    an assembly component, or a whole-object selection (a sub-element the adapter does not resolve to a
    face/edge/vertex the harness tracks) -- reported instead of silently dropped so the agent/chip can say
    "1 face · 1 item not usable (sketch)" rather than under-count what the engineer actually selected."""

    label: str
    reason: str


class SelectionResult(BaseModel, frozen=True):
    entries: tuple[SelectionEntry, ...] = ()
    unresolved: tuple[UnresolvedSelection, ...] = ()
    sheet: tuple[SheetPick, ...] = ()


class WindowResult(BaseModel, frozen=True):
    """What a window command did or read: ``text`` for the agent; ``image_path`` when a capture was written."""

    text: str
    image_path: str | None = None


class ShowEntitiesResult(BaseModel, frozen=True):
    """How ``show_entities`` pointed the engineer at the entities (or why it could not): ``highlight`` --
    a temporary colour in the visible CAD's viewport, restored on the next call/turn; ``preview`` -- a PNG
    was rendered instead (headless CAD); ``unavailable`` -- neither was possible, with ``reason``. A
    ``clear``-only request (no entities) still reports the mechanism it used/would use."""

    shown: Literal["highlight", "preview", "unavailable"]
    reason: str | None = None
    preview_path: str | None = None
    detail: str = ""
    """What was painted and where (``3D: face:5; sheet Drawing: d3``); empty for a plain 3D highlight."""


class DrawingItemResult(BaseModel, frozen=True):
    """What one sheet item did when the sheet was drawn: ``ok``, ``warning`` (drawn, with something to know),
    ``error`` (stored but cannot be drawn against the part now) or ``refused`` (not stored)."""

    id: str
    type: str
    status: Literal["ok", "warning", "error", "refused"]
    detail: str = ""


class DrawingResult(BaseModel, frozen=True):
    """The drawing sheet as built or changed: ``scale`` is the ratio (0.5 = 1:2), ``views`` and ``dimensions`` say what
    is on it, ``items`` what each item did, ``sheets`` the names of every sheet of the document and ``waited_s`` how
    long the CAD took to finish computing the views (nothing is returned before they are done). ``removed`` is true when
    the call took the whole sheet away (the other fields then describe nothing)."""

    sheet: str
    template: str = ""
    language: str = "es"
    page_mm: tuple[float, float] = (0.0, 0.0)
    scale: float = 1.0
    views: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    sheets: tuple[str, ...] = ()
    items: tuple[DrawingItemResult, ...] = ()
    removed: bool = False
    waited_s: float = 0.0
    warnings: tuple[str, ...] = ()
    projection: Literal["techdraw", "hlr"] = "techdraw"
    angle: Literal["first", "third"] = "first"
    changed: tuple[str, ...] = ()  # ids of the items this call added or took off (the rest is the sheet as it was)
    window: str = ""  # what the call did to the objects of the CAD's sheet window ("1 new", "unchanged")
    kept: tuple[str, ...] = ()  # a rebuild: what it kept of the sheet it replaced (items, answers, reviewer)
    unresolved: tuple[str, ...] = ()  # a rebuild: the kept items that no longer resolve against the part, and why
    discarded: tuple[str, ...] = ()  # a rebuild with ``fresh``: what it threw away


class DrawingItemInfo(BaseModel, frozen=True):
    """A stored item (``spec``) and what it does against the part now."""

    id: str
    type: str
    status: Literal["ok", "warning", "error"]
    detail: str = ""
    spec: dict[str, Any] = {}


class DrawingAnswer(BaseModel, frozen=True):
    """What the agent answered to a finding or a question of the review, kept in the sheet: ``applies`` (it is so, and
    how) or ``not_applies`` (it does not apply to this part, and why)."""

    verdict: Literal["applies", "not_applies"]
    text: str
    ordered_by: str = ""
    """The engineer who ordered a ``not_applies`` the harness would not accept on its own (a measured finding or
    requirement): his name; his words are quoted in ``text``. Empty: the agent answered by itself."""


class DrawingFinding(BaseModel, frozen=True):
    """One finding of a drawing review: a stable ``code``, a ``severity`` (``warning``: a likely drawing error,
    ``info``: something to confirm), the ``message`` and the ids of the sheet items and part features it is about.
    ``id`` names it in an answer (empty: the code); ``blocking`` says it holds the export until it is fixed or answered
    (a notice about the CAD window does not); ``answer`` is the answer the sheet holds, if any."""

    code: str
    severity: Literal["warning", "info"]
    message: str
    items: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    id: str = ""
    blocking: bool = True
    answer: DrawingAnswer | None = None


class DrawingQuestion(BaseModel, frozen=True):
    """A question no program can answer (enough views, sections needed...), with the answer the sheet holds."""

    id: str
    text: str
    answer: DrawingAnswer | None = None


class DrawingRemark(BaseModel, frozen=True):
    """One point of a review report: ``id`` names it in an answer (``fab2.1``: the report's number and its position),
    ``kind`` says what kind of point it is (``missing``: cannot make it without; ``ambiguous``: can be read two ways;
    ``costly``: hard or expensive as drawn; ``other``), ``where`` is its place on the sheet, ``text`` the point and
    ``fix`` the smallest change that would close it."""

    id: str
    kind: Literal["missing", "ambiguous", "costly", "other"] = "other"
    where: str = ""
    text: str
    fix: str = ""


class DrawingReport(BaseModel, frozen=True):
    """What a reviewer of the sheet reported: ``by`` is ``fabricator`` (who makes or assembles the part with only the
    drawing in hand) or ``engineer``; ``n`` numbers the reports of the sheet; ``edit`` is the sheet's change
    counter when it was recorded (it may have changed since: ``DrawingSheetInfo.edits``).
    A report with no remarks is a report: the reviewer looked and found nothing."""

    n: int
    by: Annotated[Literal["fabricator", "engineer"], BeforeValidator(canonical_author)]
    at: float = 0.0
    remarks: tuple[DrawingRemark, ...] = ()
    edit: int = 0


class DrawingReviewRequest(BaseModel, frozen=True):
    """When the engineer was asked who reviews the sheet (``at``: epoch seconds, the server's clock). It belongs to the
    sheet, not to a version of it: a change of the sheet does not ask again nor restart the wait."""

    at: float


class DrawingMeasure(BaseModel, frozen=True):
    """A value one dimension item of the sheet gives DIRECTLY (a distance between two features, a diameter, a radius, the
    overall size, the diameter of a called-out hole), measured on the model; toleranced: it carries a fit or tolerance."""

    id: str
    kind: Literal["distance", "diameter", "radius", "overall", "hole"]
    value_mm: float
    toleranced: bool = False


class DrawingComponent(BaseModel, frozen=True):
    """One position of the parts list of an assembly: its designation and the reference (drawing number) the row prints."""

    name: str
    reference: str = ""


class DrawingSheetInfo(BaseModel, frozen=True):
    sheet: str
    template: str
    language: str
    page_mm: tuple[float, float]
    scale: float
    body: str | None = None
    projection: Literal["techdraw", "hlr"] = "techdraw"
    angle: Literal["first", "third"] = "first"
    views: tuple[str, ...] = ()
    fields: dict[str, str] = {}
    items: tuple[DrawingItemInfo, ...] = ()
    findings: tuple[DrawingFinding, ...] = ()
    questions: tuple[DrawingQuestion, ...] = ()
    """With ``review``: the questions of judgement of the sheet (``findings`` are the automatic ones)."""
    answers: dict[str, DrawingAnswer] = {}
    """Every answer the sheet holds, by the id of the finding, question or memory entry it answers."""
    approved_by: str = ""
    """The engineer in the ``Aprobó`` field of the title block (empty: nobody approved this sheet as it is)."""
    reports: dict[str, DrawingReport] = {}
    """The last report of each reviewer (``fabricator``, ``engineer``)."""
    edits: int = 0
    """How many times the sheet has changed: shown next to a report's ``edit``; it gates nothing."""
    review_request: DrawingReviewRequest | None = None
    review_choice: Annotated[Literal["", "engineer", "fabricator"], BeforeValidator(canonical_author)] = ""
    """Who the engineer chose to review this sheet (empty: no answer yet); it stays through the changes of the sheet."""
    printed: tuple[str, ...] = ()
    """The texts the sheet prints (dimensions, callouts, notes, title block): what a reader without the model has."""
    facts: dict[str, int] = {}
    """With ``review``: what the CAD measured on the part and found on the sheet that the table of requirements of
    a drawing reads (``cotaforge_mcp.drawing_requirements``): a count per fact name (threaded holes, fit holes, notes
    found, weld items...). Empty: this CAD does not report them and no requirement is evaluated."""
    measures: tuple[DrawingMeasure, ...] = ()
    """With review: the value each dimension item of the sheet states by itself (see DrawingMeasure)."""
    components: tuple[DrawingComponent, ...] = ()
    """With review, an assembly: each position of the parts list and the reference (drawing number) its row prints."""
    native: tuple[str, ...] = ()
    """The objects on the page that no item made (a view, a dimension, a note, a hatch added by a native script): they
    show in the capture and the PDF, but the review does not read them."""


class DescribeDrawingResult(BaseModel, frozen=True):
    sheets: tuple[DrawingSheetInfo, ...] = ()


class SheetView(BaseModel, frozen=True):
    """A view of a sheet and where it is: ``rect`` is its box on the page in mm (``x0, y0, x1, y1``, origin at the lower
    left, y up). ``kind``: ``standard`` (front, top, right, isometric), ``derived`` (section, detail, auxiliary: made by
    an item) or ``native`` (a view a native script added)."""

    name: str
    rect: tuple[float, float, float, float]
    kind: Literal["standard", "derived", "native"] = "standard"


class SheetOutput(BaseModel, frozen=True):
    """What a file written from a sheet (a PDF, a PNG, a DXF) holds, said the same way for each, so what the agent
    captures, what a reviewer sees and what the engineer opens can be compared. ``renderer``: ``techdraw`` (the sheet
    window's own scene: every object of the page, the ones the items made and the ones a native script added) or
    ``compositor`` (cotaforge's own rendering of the composed sheet: the headless CAD, whose TechDraw cannot paint, and
    every DXF). ``native`` lists the objects a native script put on the page; ``unrendered`` the ones this file does NOT
    draw (empty with ``techdraw``): nothing is left out silently. ``views`` are the views the page holds, with their
    boxes."""

    renderer: Literal["techdraw", "compositor"] = "compositor"
    page_mm: tuple[float, float] | None = None
    views: tuple[SheetView, ...] = ()
    native: tuple[str, ...] = ()
    unrendered: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class CaptureDrawingResult(SheetOutput, frozen=True):
    """The sheet image: its size in pixels and, with a ``region``, the part of the sheet it shows (page mm)."""

    size_px: tuple[int, int] = (0, 0)
    region: tuple[float, float, float, float] | None = None


class ExportDrawingResult(SheetOutput, frozen=True):
    """The file written (``paths`` holds exactly one). ``renderer`` says what wrote it: ``techdraw`` (the PDF of the
    visible CAD: TechDraw's own export of the sheet window, so it is what the engineer sees and edits) or
    ``compositor`` (cotaforge's own writer from the same sheet: the headless CAD, whose TechDraw cannot paint, and every
    DXF)."""

    paths: tuple[str, ...] = ()


class EntityResult(BaseModel, frozen=True):
    entity: AnyHandle


class UndoResult(BaseModel, frozen=True):
    reverted: FeatureHandle | None
    warnings: tuple[str, ...] = ()
    """What the revert did NOT honour of the engineer's hand work (an edit it overwrote, an object it could not restore,
    hand-made features that depended on what it deleted): never a refusal, the undo happened."""


class EventsResult(BaseModel, frozen=True):
    """Declared, optional, not implemented yet."""


class TreeDiff(BaseModel, frozen=True):
    """Declared, optional, not implemented yet."""


class ConstraintConflict(BaseModel, frozen=True):
    kind: Literal["conflicting", "redundant", "partially_redundant", "malformed"]
    constraint_index: int = Field(ge=0)
    constraint_name: str = ""
    involved: tuple[int, ...] = ()  # sketch geometry indices implicated by this constraint


class SketchStatusResult(BaseModel, frozen=True):
    """Degrees of freedom, over-constraint and conflicting/redundant constraints of a sketch (optional
    adapter tool ``sketch_status``). ``dof`` is the remaining, non-negative degrees of
    freedom (0 = fully defined); an adapter whose solver reports a negative value (over-constrained) clamps
    it to 0 here and signals that through ``over_constrained``/``conflicts`` instead.
    """

    sketch: FeatureHandle
    dof: int = Field(ge=0)
    geometry_count: int = Field(ge=0)
    constraint_count: int = Field(ge=0)
    fully_constrained: bool
    over_constrained: bool
    conflicts: tuple[ConstraintConflict, ...] = ()
    free_geometry: tuple[int, ...] = ()  # sketch geometry indices with remaining degrees of freedom
    solver_message: str = ""
