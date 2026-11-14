"""Request models: base Request/Empty and the session/health/construction/query/provenance/
references/undo/changes/view/export request surface (contract/requests.py).

Assembly requests live in assembly.py.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from cotaforge.contract import drawing_items
from cotaforge.contract.enums import (
    BooleanKind,
    Direction,
    DocumentKind,
    DrillPoint,
    EndCondition,
    HoleStandard,
    PlaneName,
    ProvenanceRole,
)
from cotaforge.contract.geometry import Axis, SketchFrame, Transform, Vec3
from cotaforge.contract.handles import (
    AnyHandle,
    BodyHandle,
    ComponentHandle,
    DatumPlaneHandle,
    DocumentHandle,
    EdgeHandle,
    FaceHandle,
    FeatureHandle,
    SketchHandle,
)
from cotaforge.contract.holetool import DEFAULT_COUNTERSINK_ANGLE_DEG, DEFAULT_DRILL_POINT_ANGLE_DEG, FIT_PATTERN
from cotaforge.contract.responses import PersistentRef
from cotaforge.contract.sketch import NamedDimension, SketchConstraint, SketchEntity

SHEET_NAME_PATTERN = drawing_items.SHEET_NAME.pattern


class Request(BaseModel, frozen=True, allow_inf_nan=False):
    """Base of every request. ``nan`` and ``inf`` are never a valid number for a CAD (they pass ``> 0`` checks or
    slip through ``<= 0`` ones), so no request carries them."""

    operation_id: str = Field(min_length=1)


class Empty(Request):
    pass


class ValidityRequest(Request):
    """Validate the active document's geometry. ``deep`` also runs the CAD's slow self-intersection check on bodies
    that came in from a customer's file and were not changed since (the cheap validity check is always made): by
    default those are taken as the supplier delivered them."""

    deep: bool = False


# --- Session -----------------------------------------------------------------


class ConnectRequest(Request):
    timeout_s: float = 60.0


class NewDocumentRequest(Request):
    kind: DocumentKind
    name: str


class SetActiveDocumentRequest(Request):
    """Make ``document`` the active one. ``refresh`` (an assembly only): first bring its components up to date with the
    part documents they are instances of (a part changed and saved since), as a recompute of the assembly does.

    A document without a window (loaded hidden, or its window closed once it was part of an assembly) gets its window
    when it becomes the active one, unless ``window`` is False (the caller only wants to reach it, to close it, say)."""

    document: DocumentHandle
    refresh: bool = False
    window: bool = True


class SetTimeoutRequest(Request):
    timeout_s: float


# --- Construction --------------------------------------------------------------


class DatumPlaneTarget(BaseModel, frozen=True, allow_inf_nan=False):
    """A datum plane as a sketch target: the handle ``datum_plane`` returned plus the frame the executor
    computed for it. The frame travels with the handle so a sketch on a datum needs no registry lookup in
    the adapter (or in the expectation computer): ``frame`` is exactly the ``SketchResult.frame`` the
    adapter must report for a sketch on it."""

    handle: DatumPlaneHandle
    frame: SketchFrame


class SketchRequest(Request):
    on: PlaneName | FaceHandle | DatumPlaneTarget
    entities: tuple[SketchEntity, ...]
    constraints: tuple[SketchConstraint, ...] = ()
    named_dimensions: tuple[NamedDimension, ...] = ()
    origin: Vec3 | None = None
    """On a planar face: where the frame of the sketch has its origin (a point of the plane of the face); ``None`` = the
    centre of the face. Set when a rebuild must put the sketch where it stood: the centre of a face moves with every
    hole in it, and the coordinates of the entities are measured from this origin."""


DatumPlaneHow = Literal["offset", "angle", "three_points", "normal_to", "midplane"]
DatumAxisHow = Literal["two_points", "point_direction", "entity", "plane_intersection"]


class DatumPlaneRequest(Request):
    """Realise a fixed-pose datum plane. The executor has already resolved the IR definition (``how``) into
    ``frame``; the adapter reproduces that pose exactly (origin, u, v, n) and returns it in
    ``DatumPlaneResult``. It creates no feature, changes no geometry and adds no feature-tree node."""

    how: DatumPlaneHow
    frame: SketchFrame


class DatumAxisRequest(Request):
    """Realise a fixed-pose datum axis (``axis`` resolved by the executor); same contract as
    ``DatumPlaneRequest``."""

    how: DatumAxisHow
    axis: Axis


class ExtrudeRequest(Request):
    sketch: SketchHandle
    depth_mm: float = Field(gt=0)
    direction: Direction = Direction.positive
    both_directions: bool = False
    depth2_mm: float | None = Field(default=None, gt=0)
    merge: bool = True


class SweepRequest(Request):
    """Move the single closed profile of ``sketch`` along the polyline ``path`` (world mm).

    The executor has already checked that the profile plane is normal to the first segment through the first point
    with the centroid on it. corner_radius_mm None mitres the corners, a value rounds each with an arc."""

    sketch: SketchHandle
    path: tuple[Vec3, ...] = Field(min_length=2)
    corner_radius_mm: float | None = Field(default=None, gt=0)
    merge: bool = True


class CutRequest(Request):
    sketch: SketchHandle
    end_condition: EndCondition = EndCondition.blind
    depth_mm: float | None = Field(default=None, gt=0)
    direction: Direction = Direction.negative
    both_directions: bool = False
    depth2_mm: float | None = Field(default=None, gt=0)
    scope: tuple[BodyHandle, ...] | None = None


class RevolveRequest(Request):
    sketch: SketchHandle
    axis: Axis
    angle_deg: float = 360.0


class HoleThread(BaseModel, frozen=True, allow_inf_nan=False):
    """The cosmetic thread of a tapped hole (metadata only: the geometry is the plain tap-drill cylinder)."""

    size: str = Field(min_length=1)
    depth_mm: float | None = Field(default=None, gt=0)


class HoleCounterbore(BaseModel, frozen=True, allow_inf_nan=False):
    """FreeCAD's ``HoleCutType=Counterbore``: a larger cylinder at the entry, ``depth_mm`` deep from the entry plane."""

    diameter_mm: float = Field(gt=0)
    depth_mm: float = Field(gt=0)


class HoleCountersink(BaseModel, frozen=True, allow_inf_nan=False):
    """FreeCAD's ``HoleCutType=Countersink``: a cone at the entry, ``diameter_mm`` wide at the entry plane, with an
    included angle of ``angle_deg`` (FreeCAD's default: 90)."""

    diameter_mm: float = Field(gt=0)
    angle_deg: float = Field(default=DEFAULT_COUNTERSINK_ANGLE_DEG, gt=0, lt=180)


class HoleRequest(Request):
    """Drill into the body from ``face``.

    On a planar face ``position`` is ``(u, v)`` in the face frame. On a CONVEX cylindrical face (the outer wall of a
    tube or shaft) it is ``(axial_mm, angle_deg)`` -- see ``contract.geometry.radial_entry`` -- and the hole runs
    along the inward radial; ``depth_mm`` is measured from the surface point on the hole's own axis.

    ``counterbore`` / ``countersink`` (at most one) widen the entry; ``drill_point`` ``angled`` closes a blind hole with
    a cone of ``drill_point_angle_deg`` (``depth_mm`` stays the length of the cylinder). ``end_condition`` up_to_face
    ends the hole at the plane of ``up_to_face`` (a planar face normal to the hole axis); the adapter drills it blind
    to that distance."""

    face: FaceHandle
    position: tuple[float, float]
    positions: tuple[tuple[float, float], ...] | None = None
    """Several holes of the same kind in one feature (planar face only): every ``(u, v)``, the first one equal to
    ``position``. ``None`` = one hole at ``position``."""
    origin: Vec3 | None = None
    """On a planar face: the point ``position`` is measured from (a point of the plane of the face); ``None`` = the
    centre of the face. The executor always sends the stable origin of the face (``stable_face_origin``). See
    ``SketchRequest.origin``."""
    standard: HoleStandard
    size: str | None = None
    diameter_mm: float = Field(gt=0)
    end_condition: EndCondition
    depth_mm: float | None = Field(default=None, gt=0)
    thread: HoleThread | None = None
    fit: str | None = Field(default=None, pattern=FIT_PATTERN)
    """The hole-basis tolerance class the hole is made to (``H7``): IT8 or finer makes it a precision hole. The colour
    convention reads it (``cotaforge.conventions``); a drawing's ``hole_dim`` takes it as its default fit."""
    colour: tuple[int, int, int] | None = None
    hole_class: str | None = None
    precision_grade: int | None = None
    """Filled by the adapter from the convention in force, never by the caller: the colour (0..255) the faces of the
    hole take, the class it comes from (``precision`` / ``plain`` / ``threaded``) and the IT grade that convention called
    precision. ``None``: the convention is off."""
    counterbore: HoleCounterbore | None = None
    countersink: HoleCountersink | None = None
    drill_point: DrillPoint = DrillPoint.angled
    drill_point_angle_deg: float = Field(default=DEFAULT_DRILL_POINT_ANGLE_DEG, gt=0, lt=180)
    up_to_face: FaceHandle | None = None

    @model_validator(mode="after")
    def _check_hole_shape(self) -> "HoleRequest":
        if self.counterbore is not None and self.countersink is not None:
            msg = "a hole takes a counterbore or a countersink, not both"
            raise ValueError(msg)
        if (self.end_condition is EndCondition.up_to_face) != (self.up_to_face is not None):
            msg = "up_to_face names the target face exactly when end_condition is up_to_face"
            raise ValueError(msg)
        return self


class LinearPatternRequest(Request):
    features: tuple[FeatureHandle, ...]
    direction: Vec3
    spacing_mm: float
    count: int = Field(ge=2)
    direction2: Vec3 | None = None
    spacing2_mm: float | None = None
    count2: int | None = None


class CircularPatternRequest(Request):
    features: tuple[FeatureHandle, ...]
    axis: Axis
    count: int = Field(ge=2)
    angle_deg: float = 360.0
    equal_spacing: bool = True


class ChamferRequest(Request):
    edges: tuple[EdgeHandle, ...]
    distance1_mm: float
    distance2_mm: float | None = None
    distance1_face: FaceHandle | None = None
    skip_failing: bool = False
    """When the CAD cannot chamfer every edge: isolate the ones it cannot, apply the rest and report the skipped ones
    (``OperationResult.skipped_edges``). Default: fail naming the edges."""


class FilletRequest(Request):
    edges: tuple[EdgeHandle, ...]
    radius_mm: float
    skip_failing: bool = False


class ShellRequest(Request):
    """Hollow the single body the ``faces`` belong to: the faces are REMOVED (left open) and every other face gets a
    wall of ``thickness_mm``. ``outward`` False grows the walls into the part (the outer size is kept), True grows a
    skin around it; ``join`` shapes the corners of the result ("intersection": sharp, "arc": rounded).

    The executor has already checked that every face lies on one body. A thickness the kernel cannot build (thicker
    than the part allows) must fail, never leave the shape unchanged."""

    faces: tuple[FaceHandle, ...] = Field(min_length=1)
    thickness_mm: float = Field(gt=0)
    outward: bool = False
    join: Literal["intersection", "arc"] = "intersection"


class DraftRequest(Request):
    """Tilt the ``faces`` by ``angle_deg`` about the ``neutral`` plane (the section of each face on it keeps its size).
    ``pull`` is the unit direction the mould half forming these faces moves when it opens, parallel (either sense) to
    the neutral plane's normal: after the op every drafted face's OUTWARD normal has a component sin(angle) along it.

    The executor has already checked that ``pull`` is perpendicular to the neutral plane. An angle the kernel cannot
    build, a result whose faces lean the wrong way and a shape left unchanged must fail, never be reported as done."""

    faces: tuple[FaceHandle, ...] = Field(min_length=1)
    neutral: SketchFrame
    pull: Vec3
    angle_deg: float = Field(gt=0, le=30)

    @model_validator(mode="after")
    def _check_pull_has_a_direction(self) -> "DraftRequest":
        length = sum(c * c for c in self.pull) ** 0.5
        if not 1e-9 < length < float("inf"):
            msg = "DraftRequest.pull must be a finite, non-zero vector"
            raise ValueError(msg)
        return self


class MirrorRequest(Request):
    """Reflect the ``features`` across ``plane`` (only its origin and normal ``n`` matter) and fuse the
    reflection into the same body: one new feature, the seeds stay. The executor has resolved a base plane, a datum
    plane or a planar face to the frame; a seed that cannot be mirrored (not a feature of a feature-based body) must
    be an error, never a silent no-op."""

    features: tuple[FeatureHandle, ...] = Field(min_length=1)
    plane: SketchFrame


class MirrorBodyRequest(Request):
    """Reflect ``source`` across ``plane`` into a NEW independent body without feature history; the source stays."""

    source: BodyHandle
    plane: SketchFrame


BendPosition = Literal["material_inside", "material_outside"]
FlangeLengthFrom = Literal["outer_sharp", "leg"]


class BaseFlangeRequest(Request):
    """Start a sheet-metal part: the closed profiles of ``sketch`` become a flat sheet ``thickness_mm`` thick, on the
    side of the sketch normal that ``direction`` names (a NEW body; it is never merged into another). The largest
    profile is the outline and every other one a hole inside it (the executor has checked that). Needs the sheet-metal
    capability."""

    sketch: SketchHandle
    thickness_mm: float = Field(gt=0)
    direction: Direction = Direction.positive


class EdgeFlangeRequest(Request):
    """Bend a wall out of one straight ``edge`` of a sheet-metal body (the IR ``edge_flange``). ``angle_deg`` is the
    bend angle (90: perpendicular to the sheet); the wall rises from the face the edge belongs to, on the side of that
    face's normal. ``position`` says where the bend goes: ``material_inside`` keeps the outer face of the wall in the
    plane of the sheet's edge (the bend eats into the sheet), ``material_outside`` bends beyond the edge and leaves
    the sheet as it is. ``length_mm`` is measured to the outer virtual sharp (the corner the two outer surfaces would
    make) or, with ``length_from=leg``, as the straight part after the bend. An edge of a body that is not sheet metal
    must be an error."""

    edge: EdgeHandle
    length_mm: float = Field(gt=0)
    angle_deg: float = Field(gt=0, lt=180)
    bend_radius_mm: float = Field(gt=0)
    position: BendPosition = "material_inside"
    length_from: FlangeLengthFrom = "outer_sharp"


class UnfoldRequest(Request):
    """Make the flat pattern of a sheet-metal body, keeping ``face`` (a planar face of the sheet) fixed. ``k_factor``
    is the ANSI K-factor: the neutral line sits ``k_factor * thickness`` from the inside of the bend. The flat pattern
    is NOT part of the document's solid (mass, bounding box and validity keep describing the folded part); its facts
    come back in ``OperationResult.flat_pattern`` and ``ExportDxfRequest.flat_pattern`` exports it."""

    face: FaceHandle
    k_factor: float = Field(gt=0, lt=1)


class BoxRequest(Request):
    size: Vec3
    transform: Transform = Transform.identity()


class CylinderRequest(Request):
    diameter_mm: float
    height_mm: float
    axis: Axis


class BooleanRequest(Request):
    kind: BooleanKind
    target: BodyHandle
    tools: tuple[BodyHandle, ...]
    keep_tools: bool = False


class MoveBodyRequest(Request):
    body: BodyHandle
    transform: Transform


class CopyBodyRequest(Request):
    """Duplicate ``source`` into a NEW body; the source is left where it is. The copy is first scaled uniformly by
    ``scale`` about the point ``scale_about`` (world coordinates), then placed by ``transform`` (rotate, then offset):
    ``transform(scale_about + scale * (p - scale_about))``. A scale of 1 is a rigid copy."""

    source: BodyHandle
    transform: Transform
    scale: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    scale_about: Vec3 = (0.0, 0.0, 0.0)


class ShrinkBodyRequest(Request):
    """Derive a NEW body from ``source``, LINKED to it (it follows the source when the source changes), scaled by
    ``scale`` per axis (the mould's cavity of a shrinking plastic: 1.006 = the 0.6 % the plastic will shrink). The
    scaling is about the source body's own origin. The source is untouched. A scale equal on all three axes is exact
    (planes stay planes, cylinders stay cylinders); an unequal one is affine and turns curved faces into B-spline
    surfaces, as in every CAD."""

    source: BodyHandle
    scale: Vec3

    @field_validator("scale")
    @classmethod
    def _scale_positive(cls, value: Vec3) -> Vec3:
        if not all(c > 0.0 for c in value):
            msg = "scale must be above 0 on every axis"
            raise ValueError(msg)
        return value


class SplitRequest(Request):
    """Cut ``source`` in two along ``plane`` (only its origin and normal ``n`` matter). ``positive`` is the piece on
    the side ``n`` points to, ``negative`` the other. The source is CONSUMED (it no longer exists afterwards) and the
    pieces are NEW independent bodies without feature history, reported in ``created_bodies`` as (positive, negative);
    ``keep`` positive or negative creates only that piece. A plane that does not cut the body must be an error."""

    source: BodyHandle
    plane: SketchFrame
    keep: Literal["both", "positive", "negative"] = "both"


# --- Provenance ------------------------------------------------------------------


class FeatureRequest(Request):
    feature: FeatureHandle
    role: ProvenanceRole = ProvenanceRole.any


class EdgesBetweenRequest(Request):
    a: FaceHandle
    b: FaceHandle


class FaceRequest(Request):
    face: FaceHandle


class EdgeRequest(Request):
    edge: EdgeHandle


class FacesInfoRequest(Request):
    """``Adapter.faces_info``: the descriptors of several faces, in the order asked."""

    faces: tuple[FaceHandle, ...]


class EdgesInfoRequest(Request):
    """``Adapter.edges_info``: the descriptors of several edges, in the order asked."""

    edges: tuple[EdgeHandle, ...]


class VertexAtRequest(Request):
    faces: tuple[FaceHandle, FaceHandle, FaceHandle]


# --- Query ----------------------------------------------------------------------


class MassRequest(Request):
    body: BodyHandle | None = None
    component: ComponentHandle | None = None
    surface: bool = True
    """False: do not measure the surface area (a pass over every face; ``surface_mm2`` stays ``None``)."""
    centroid: bool = True
    """False: do not take the centre of mass nor the inertia (another pass over every solid; ``centroid`` is then the
    origin and means nothing)."""
    exact_box: bool = True
    """False: ``bounding_box`` may answer a quick box (``BoundingBox.exact`` False), not the exact one."""


class SketchStatusRequest(Request):
    sketch: FeatureHandle


# --- References ------------------------------------------------------------------


class EntityRequest(Request):
    entity: AnyHandle


class ResolveRequest(Request):
    ref: PersistentRef


# --- Changes (optional) -----------------------------------------------------------


class TreeDiffRequest(Request):
    since: FeatureHandle | None = None


# --- View (optional) --------------------------------------------------------------


ViewName = Literal["iso", "front", "top", "right", "rear", "left", "bottom", "dimetric", "trimetric"]

COLOR_NAMES: dict[str, str] = {
    "red": "#d32f2f",
    "green": "#388e3c",
    "blue": "#1976d2",
    "yellow": "#fbc02d",
    "orange": "#f57c00",
    "gray": "#9e9e9e",
    "grey": "#9e9e9e",
    "black": "#212121",
    "white": "#fafafa",
}


def parse_color(text: str) -> str:
    """``#rrggbb`` (or ``rrggbb``) or one of ``COLOR_NAMES`` as a lowercase ``#rrggbb``; ``ValueError`` otherwise."""
    value = text.strip().lower()
    if value in COLOR_NAMES:
        return COLOR_NAMES[value]
    digits = value.removeprefix("#")
    if len(digits) == 6 and all(c in "0123456789abcdef" for c in digits):
        return "#" + digits
    msg = f"unknown colour {text!r}: use #rrggbb or one of {', '.join(sorted(COLOR_NAMES))}"
    raise ValueError(msg)


class ViewRequest(Request):
    """Orient the 3D view to a standard ``name`` and/or fit the model in it; ``bodies`` fits just those bodies (empty:
    everything). Without ``name`` the orientation is kept."""

    name: ViewName | None = None
    bodies: tuple[BodyHandle, ...] = ()


class StyleRequest(Request):
    """How ``bodies`` look in the 3D view (display only: nothing of the model or the file changes): ``transparency``
    0 (opaque) to 100 (invisible), ``color`` (``#rrggbb`` or a name) and/or ``visible``."""

    bodies: tuple[BodyHandle, ...] = Field(min_length=1)
    transparency: int | None = Field(default=None, ge=0, le=100)
    color: str | None = None
    visible: bool | None = None

    @field_validator("color")
    @classmethod
    def _normalise_color(cls, value: str | None) -> str | None:
        return None if value is None else parse_color(value)

    @model_validator(mode="after")
    def _check_style(self) -> "StyleRequest":
        if self.transparency is None and self.color is None and self.visible is None:
            msg = "StyleRequest needs transparency, color and/or visible"
            raise ValueError(msg)
        return self


class SectionRequest(Request):
    """A clipping plane in the 3D view: everything beyond ``offset_mm`` along the ``axis`` (+ side) is cut away so the
    inside shows. ``axis`` omitted removes the plane. Display only."""

    axis: Literal["x", "y", "z"] | None = None
    offset_mm: float = 0.0


class WindowRequest(Request):
    """The CAD's own window on the engineer's screen (not the model): ``info`` reads where it is, ``place`` moves it
    to ``monitor`` (and to ``area``, fractions left/top/right/bottom of that monitor), ``tile`` shares the screen out
    between the CAD windows the harness started, ``minimize``/``restore``, ``capture`` writes a PNG of the window to
    ``path``. Never part of the plan, undo or a saved document."""

    command: Literal["info", "place", "tile", "minimize", "restore", "capture"]
    monitor: int | None = Field(default=None, ge=1)
    area: tuple[float, float, float, float] | None = None
    path: str | None = None


class CaptureRequest(Request):
    path: str
    width: int = Field(default=1280, gt=0)
    height: int = Field(default=800, gt=0)


# --- Drawings (optional) ----------------------------------------------------------


class DrawingRequest(Request):
    """Build a drawing sheet of the active document: a first-angle front/top/side group plus an isometric view of
    every body (or of ``body`` alone), the overall width/height/depth dimensions and the title block; a body of
    revolution gets the views its profile needs instead (see ``views``). ``scale`` is the drawing scale as a ratio
    (0.5 = 1:2); omitted, the largest standard scale that fits. ``sheet`` names the sheet (a name that exists is
    REBUILT and keeps what is still valid in it: its items, placed again on the new views, the answers to its review,
    who reviews it, its title block; ``fresh`` starts it from scratch instead); a new name makes another sheet of the
    same document; omitted, the first sheet. ``language`` ``es`` (default) is the Spanish ISO 7200 title block with a
    revision table, ``en`` TechDraw's own English one."""

    template: Literal["A4", "A3", "A2", "A1", "A0", "Letter"] = "A4"
    title: str | None = None
    author: str | None = None
    date: str | None = None
    material: str | None = None
    scale: float | None = Field(default=None, gt=0)
    title_block: dict[str, str] = {}
    """More title-block fields by the name the template gives them (``document_type``, ``legal_owner_2``,
    ``fixed_note_1`` to ``fixed_note_3``...): the template is a TechDraw template stored in the document, so the
    engineer edits the same fields in FreeCAD."""
    angle: Literal["first", "third"] = "first"
    """Projection angle: ``first`` (ISO: the top view under the front one, the right view to its left) or ``third``
    (the top view above, the right view to the right); the title block carries the matching symbol."""
    language: Literal["es", "en"] = "es"
    drawing_number: str | None = None
    revision: str | None = None
    general_tolerances: str = "ISO 2768-mK"
    company: str | None = None
    sheet: str | None = Field(default=None, pattern=SHEET_NAME_PATTERN)
    body: BodyHandle | None = None
    isometric: bool = True  # False: no standard isometric view (an exploded view of an assembly replaces it)
    views: tuple[Literal["front", "top", "right", "iso", "isometric"], ...] | None = None
    """The views the sheet has (``front`` is always one): ``("front", "top", "iso")`` makes a sheet without the right
    view, laid out at the largest standard scale that fits. Omitted: chosen from the part -- all four
    (``isometric=False`` drops the iso one) unless it is a body of revolution about x, y or z, which gets the view that
    looks along its axis plus a section through the axis (with a bore) or the one view of its profile (solid); name all
    four to have them. On a rebuild, omitted keeps the views the sheet has. A view can be added or taken off later with
    ``annotate_drawing`` (``add_view`` / ``remove_view``)."""
    fresh: bool = False
    """A sheet of that name that exists is replaced from scratch: its items, its answers and its chosen reviewer are
    thrown away (the reply says which)."""
    symmetry: bool | None = None
    """The design intent declares the part symmetric: the plane of symmetry is then a reference the positions of the
    holes are counted from and its centre line is drawn. Never inferred from the shape: a plate can be symmetric by
    chance. ``None`` leaves a rebuilt sheet as it was (no symmetry on a new one)."""
    unset: tuple[str, ...] = ()
    """On a rebuild: the settings the caller did not state (``template``, ``angle``, ``language``, ``scale``,
    ``projection``, ``body``), which stay as the sheet has them instead of taking the default."""
    iso_colour: bool = False
    """The isometric view is shaded in colour (the holes show how they are made). Filled by the adapter from the
    convention in force, not by the caller; the other views stay line drawings."""
    projection: Literal["techdraw", "hlr"] = "techdraw"
    """How the front / top / right / isometric views are made. techdraw (FreeCAD's own default): TechDraw view
    objects (slow on a big assembly). hlr: our own hidden-line projection (TechDraw.projectEx, the one the derived
    views use); the PDF / PNG / DXF are the same sheet and the views in the FreeCAD window are drawings of that
    projection (symbols, not TechDraw view objects). A choice of the engineer: never made silently."""


class AnnotateDrawingRequest(Request):
    """Change what a drawing sheet shows. ``add`` puts ``items`` on it (hole callouts, dimensions, datum dimensions,
    notes, revisions: see ``cotaforge.contract.drawing_items``); an item that cannot be drawn against the part is
    reported and not stored. ``remove`` takes the item ``ids`` off (and only those: it needs at least one);
    ``remove_sheet`` is the one way to delete the whole sheet. ``update`` lays the views out again after the part
    changed, keeping every item. ``remove_view`` / ``add_view`` take one of the standard views (``view``: front, top,
    right or iso) off the sheet or put it back: the views left are laid out again over the room and the scale grows to
    the largest standard one that fits. Items are resolved against the current geometry
    each time the sheet is drawn, so the dimension values always come from the model.

    ``answer`` stores in the sheet (so it survives save and reopen) the answer ``text`` (``verdict`` applies |
    not_applies) to the finding or question ``id`` of the review; ``memory`` says ``id`` names a memory entry the
    caller checked itself. ``approve`` puts the engineer ``by`` in the title block, quoting his words in ``text``: the
    one way that field is written, and any later change of the sheet empties it. An ``answer`` may carry ``by`` too: the
    engineer who ordered a ``not_applies`` the harness does not accept on its own (his words are in ``text``).

    ``review_report`` stores a reviewer's report in the sheet (``by``: fabricator | engineer; ``remarks``: the points,
    possibly none; ``at``: epoch seconds, the server's clock); ``request_review`` stores when the engineer was asked
    to review the sheet (``at``); ``review_choice`` stores who he chose to review it (``by``: engineer | fabricator).
    None of them changes the sheet and none is made old by a later change of it.

    ``layout`` places the annotations of the whole sheet at once (``drawing_flow: sheet``): the dimensions and leaders
    that asked for no place are given a side and an offset (or an elbow) by the global layout planner, which sees every
    view and item; what it cannot place is listed in the result (``layout_unplaced``), never dropped, and the paper is
    not touched (the caller steps it up when ``layout_fits`` is false)."""

    iso_colour: bool | None = None
    """Filled by the adapter from the convention in force (see DrawingRequest.iso_colour); None leaves the sheet as
    it is."""
    action: Literal[
        "add",
        "remove",
        "remove_sheet",
        "update",
        "add_view",
        "remove_view",
        "answer",
        "approve",
        "request_review",
        "review_choice",
        "review_report",
        "layout",
    ]
    sheet: str | None = Field(default=None, pattern=SHEET_NAME_PATTERN)
    items: tuple[dict[str, Any], ...] = ()
    remarks: tuple[dict[str, Any], ...] = ()
    at: float | None = None
    ids: tuple[str, ...] = ()
    view: Literal["front", "top", "right", "iso", "isometric"] | None = None
    title_block: dict[str, str] = {}
    """``update`` only: title-block fields to set by name (see ``DrawingRequest.title_block``); "" clears one."""
    symmetry: bool | None = None
    """``update`` only: whether the part's symmetry is part of the design intent (see ``DrawingRequest.symmetry``)."""
    id: str | None = Field(default=None, min_length=1)
    verdict: Literal["applies", "not_applies"] | None = None
    text: str | None = Field(default=None, min_length=1)
    by: str | None = Field(default=None, min_length=1)
    memory: bool = False

    @model_validator(mode="before")
    @classmethod
    def _old_author(cls, data: Any) -> Any:
        """``by`` of a review call may still be ``fabricante`` / ``ingeniero`` (the names before 0.10)."""
        if isinstance(data, dict) and data.get("action") in ("review_report", "review_choice"):
            return {**data, "by": drawing_items.canonical_author(data.get("by"))}
        return data

    @model_validator(mode="after")
    def _check_items(self) -> "AnnotateDrawingRequest":
        if self.title_block and self.action != "update":
            msg = f"{self.action} takes no title_block (create and update do)"
            raise ValueError(msg)
        if self.symmetry is not None and self.action != "update":
            msg = f"{self.action} takes no symmetry (create and update do)"
            raise ValueError(msg)
        self._check_review_fields()
        if self.action == "add":
            if not self.items:
                msg = "add needs items"
                raise ValueError(msg)
            found = drawing_items.problems(self.items)
            if found:
                raise ValueError("; ".join(found))
        elif self.items:
            msg = f"{self.action} takes no items (remove takes ids)"
            raise ValueError(msg)
        if self.ids and self.action != "remove":
            msg = f"{self.action} takes no ids"
            raise ValueError(msg)
        if self.action == "remove" and not self.ids:
            msg = "remove needs the ids of the items to take off (remove_sheet deletes the whole sheet)"
            raise ValueError(msg)
        if self.action in ("add_view", "remove_view") and self.view is None:
            msg = f"{self.action} needs view: front, top, right or iso"
            raise ValueError(msg)
        if self.view is not None and self.action not in ("add_view", "remove_view"):
            msg = f"{self.action} takes no view"
            raise ValueError(msg)
        return self

    def _check_review_fields(self) -> None:
        if self.action == "answer":
            if self.id is None or self.verdict is None or self.text is None:
                msg = "answer needs id, verdict (applies | not_applies) and text"
                raise ValueError(msg)
        elif self.action == "approve":
            if self.by is None or self.text is None:
                msg = "approve needs by (the engineer) and text (his words)"
                raise ValueError(msg)
            if self.id is not None or self.verdict is not None or self.memory:
                msg = "approve takes only sheet, by and text"
                raise ValueError(msg)
        elif self.action == "review_report":
            if self.by not in drawing_items.REPORT_AUTHORS or self.at is None:
                msg = f"review_report needs by ({' | '.join(drawing_items.REPORT_AUTHORS)}) and at"
                raise ValueError(msg)
            if any((self.id, self.verdict, self.text, self.memory)):
                msg = "review_report takes only sheet, by, remarks and at"
                raise ValueError(msg)
            found = drawing_items.remark_problems(self.remarks)
            if found:
                raise ValueError("; ".join(found))
        elif self.action == "review_choice":
            if self.by not in drawing_items.REPORT_AUTHORS:
                msg = f"review_choice needs by: {' | '.join(drawing_items.REPORT_AUTHORS)}"
                raise ValueError(msg)
            if any((self.id, self.verdict, self.text, self.memory, self.remarks)):
                msg = "review_choice takes only sheet and by"
                raise ValueError(msg)
        elif self.action == "request_review":
            if self.at is None:
                msg = "request_review needs at"
                raise ValueError(msg)
            if any((self.id, self.verdict, self.text, self.by, self.memory, self.remarks)):
                msg = "request_review takes only sheet and at"
                raise ValueError(msg)
        elif any((self.id, self.verdict, self.text, self.by, self.memory)):
            msg = f"{self.action} takes no id, verdict, text, by or memory"
            raise ValueError(msg)
        if self.remarks and self.action != "review_report":
            msg = f"{self.action} takes no remarks"
            raise ValueError(msg)
        if self.at is not None and self.action not in ("review_report", "request_review"):
            msg = f"{self.action} takes no at"
            raise ValueError(msg)


class DescribeDrawingRequest(Request):
    """Every drawing sheet of the active document (or ``sheet``), with its title-block fields and its items as stored,
    each with what it does now against the part. With ``review`` each sheet also carries the findings of a deterministic
    review (holes and features without a dimension, over-dimensioning, undeclared threads, stale ids, layout)."""

    sheet: str | None = Field(default=None, pattern=SHEET_NAME_PATTERN)
    review: bool = False


class CaptureDrawingRequest(Request):
    """Render a drawing sheet (``sheet``, default the first) to a PNG at ``path``, ``width`` pixels wide. It is the same
    rendering the PDF comes from, native objects included. ``region`` (``x0, y0, x1, y1`` in mm on the sheet, origin at
    the lower left, y up) shows only that part, ``width`` pixels wide: a zoom into a dimension or a line type."""

    path: str
    width: int = Field(default=1400, ge=64, le=6000)
    sheet: str | None = Field(default=None, pattern=SHEET_NAME_PATTERN)
    region: tuple[float, float, float, float] | None = None
    crops: bool = False
    """Also write zoomed crops (``<path stem>-crop<n>.png``) of the annotated area of each view and of every measured
    finding, at about 10 px per mm, capped in number and size. Whole sheet only: ignored with a ``region``."""
    crops_given_at: int | None = None
    """The change counter (``edits``) of the sheet at which its crops were last handed over: with ``crops``, a sheet that is
    still at that counter draws none (the same images again are tokens for nothing and seconds of painting)."""


class ExportDrawingRequest(Request):
    """Write ONE drawing sheet to ``path`` as ``pdf`` or ``dxf``: one drawing, one sheet, one file. ``sheet`` names it;
    it may be left out only when the document has a single sheet (with several the CAD refuses and lists them)."""

    path: str
    format: Literal["pdf", "dxf"]
    sheet: str | None = Field(default=None, pattern=SHEET_NAME_PATTERN)


# --- Selection (optional) -----------------------------------------------------------


class ShowEntitiesRequest(Request):
    """Point the engineer at already-resolved entities (``Adapter.show_entities``, optional group
    SELECTION -- the agent-to-engineer counterpart of ``get_selection``). ``entities`` is empty only when
    ``clear`` asks to drop a previous highlight without showing anything new."""

    entities: tuple[FaceHandle | EdgeHandle, ...] = ()
    note: str | None = None
    clear: bool = False
    items: tuple[str, ...] = ()
    """Things of a drawing sheet: item ids (``d3``) or views (``view:top``) -- shown on the sheet."""
    sheet: str | None = Field(default=None, pattern=SHEET_NAME_PATTERN)
    where: Literal["auto", "3d", "sheet", "both"] = "auto"
    """``auto`` shows entities in 3D and items on the sheet; ``sheet`` also shows what the entities are on the
    sheet (the items that refer to them and the lines of the views that draw them); ``3d`` also shows the faces the
    items refer to in the 3D view; ``both`` does both."""

    @model_validator(mode="after")
    def _check_entities_or_clear(self) -> "ShowEntitiesRequest":
        if not self.entities and not self.items and not self.clear:
            msg = "ShowEntitiesRequest.entities may only be empty when items are given or clear=True"
            raise ValueError(msg)
        return self


# --- Export (optional) -------------------------------------------------------------


class ExportRequest(Request):
    path: str


class ExportMeshRequest(Request):
    """Tessellated mesh export (STL) for previews; optional tool."""

    path: str
    linear_deflection_mm: float = 0.1


class ExportDxfRequest(Request):
    """2D DXF export for cutting shops; optional tool. The source of the geometry is the one field that is set:
    ``face`` is a PLANAR face whose whole outline (outer contour and holes) is projected into its own plane at real
    scale, in mm, with its lower-left corner at the origin; ``flat_pattern`` is the feature of an ``unfold`` and
    exports the outline of that flat pattern the same way. Exactly one must be given."""

    path: str
    face: FaceHandle | None = None
    flat_pattern: FeatureHandle | None = None

    @model_validator(mode="after")
    def _check_exactly_one_source(self) -> "ExportDxfRequest":
        if (self.face is None) == (self.flat_pattern is None):
            msg = "ExportDxfRequest needs exactly one source: face or flat_pattern"
            raise ValueError(msg)
        return self
