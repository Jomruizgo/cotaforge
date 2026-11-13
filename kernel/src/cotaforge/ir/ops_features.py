"""Full operation vocabulary (ir/ops_features.py).

``Operation`` is the discriminated union of every operation: the body-level
primitives imported from ``ops_primitives`` plus the feature, pattern and assembly
ops defined here. ``OP_NAMES`` is the set of all their ``op`` names.
"""

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from cotaforge.contract import (
    Axis,
    AxisName,
    BendPosition,
    Direction,
    DrillPoint,
    EndCondition,
    FlangeLengthFrom,
    HoleStandard,
    MateAlignment,
    NamedDimension,
    Side,
    SketchConstraint,
    SketchEntity,
    Vec3,
)
from cotaforge.contract.holetool import (
    DEFAULT_COUNTERSINK_ANGLE_DEG,
    DEFAULT_DRILL_POINT_ANGLE_DEG,
    FIT_PATTERN,
    countersink_depth,
)
from cotaforge.contract.threads import FINE_THREADS, thread, thread_sizes
from cotaforge.ir.ops_primitives import (
    BooleanIntersectOperation,
    BooleanSubtractOperation,
    BooleanUnionOperation,
    BoxOperation,
    CopyBodyOperation,
    CylinderOperation,
    OperationBase,
    Placement,
    RotateOperation,
    TranslateOperation,
)
from cotaforge.ir.refs import AxisRef, BodyRef, EdgeRef, EdgeSet, FaceRef, IRModel, OpId, PlaneRef

__all__ = [
    "OP_CATEGORY",
    "OP_NAMES",
    "AxisEntity",
    "AxisPlaneIntersection",
    "AxisPointDirection",
    "AxisTwoPoints",
    "AddJointOperation",
    "SetJointOperation",
    "BaseFlangeOperation",
    "ChamferOperation",
    "CheckInterferenceOperation",
    "CircularPatternOperation",
    "CutOperation",
    "DraftOperation",
    "EdgeFlangeOperation",
    "ExtrudeOperation",
    "FilletOperation",
    "FixComponentOperation",
    "HoleOperation",
    "InsertComponentOperation",
    "LinearPatternOperation",
    "MateCoincidentOperation",
    "MateConcentricOperation",
    "MateDistanceOperation",
    "MirrorBodyOperation",
    "MirrorOperation",
    "OpCategory",
    "Operation",
    "Placement",
    "PlaneAngle",
    "PlaneMidplane",
    "PlaneNormalTo",
    "PlaneOffset",
    "PlaceComponentOperation",
    "PlaneThreePoints",
    "PolylinePath",
    "RefAxisOperation",
    "RefPlaneOperation",
    "RevolveOperation",
    "ShellOperation",
    "SketchOperation",
    "SplitOperation",
    "SweepOperation",
    "UnfoldOperation",
]


class SketchOperation(OperationBase):
    op: Literal["sketch"] = "sketch"
    on: PlaneRef | FaceRef
    entities: tuple[SketchEntity, ...] = Field(min_length=1)
    constraints: tuple[SketchConstraint, ...] = ()
    named_dimensions: tuple[NamedDimension, ...] = ()


# --- datum planes and axes (ref_plane / ref_axis) ------------------------------------------------------
# Non-feature ops like ``sketch``: they build no geometry and no feature-tree node, they only name a pose
# (a plane frame or an axis) that later ops reference through ``PlaneRef.from_op`` / ``AxisRef``.


def _reject_zero_axis(value: object) -> object:
    """A literal ``{origin, direction}`` axis with a zero direction says so, instead of the union noise
    ("AxisRef.from_op: Field required") pydantic reports when the contract ``Axis`` refuses it."""
    if isinstance(value, dict) and isinstance(value.get("direction"), list | tuple):
        try:
            length = sum(float(c) ** 2 for c in value["direction"]) ** 0.5
        except (TypeError, ValueError):
            return value
        if length < 1e-9:
            msg = "axis direction must be non-zero"
            raise ValueError(msg)
    return value


class PlaneOffset(IRModel):
    """Parallel to ``base``, ``distance_mm`` along its normal (signed; 0 with a face = coplanar datum)."""

    how: Literal["offset"] = "offset"
    base: PlaneRef | FaceRef
    distance_mm: float


class PlaneAngle(IRModel):
    """``base`` rotated ``angle_deg`` (right-hand rule) about ``about``, an axis lying in the base plane."""

    how: Literal["angle"] = "angle"
    base: PlaneRef | FaceRef
    about: AxisRef | EdgeRef | FaceRef | Axis
    """A datum axis, a straight/circular edge, a cylindrical/conical face (its axis) or a literal axis."""
    angle_deg: float

    _check_about = field_validator("about", mode="before")(_reject_zero_axis)


class PlaneThreePoints(IRModel):
    how: Literal["three_points"] = "three_points"
    points: tuple[Vec3, Vec3, Vec3]


class PlaneNormalTo(IRModel):
    """Perpendicular to ``axis`` at ``at_mm`` along it (from the axis origin)."""

    how: Literal["normal_to"] = "normal_to"
    axis: AxisRef | EdgeRef | FaceRef | Axis
    at_mm: float

    _check_axis = field_validator("axis", mode="before")(_reject_zero_axis)


class PlaneMidplane(IRModel):
    """Halfway between two parallel planes."""

    how: Literal["midplane"] = "midplane"
    a: PlaneRef | FaceRef
    b: PlaneRef | FaceRef


class AxisTwoPoints(IRModel):
    how: Literal["two_points"] = "two_points"
    points: tuple[Vec3, Vec3]


class AxisPointDirection(IRModel):
    how: Literal["point_direction"] = "point_direction"
    point: Vec3
    direction: AxisName | Vec3


class AxisEntity(IRModel):
    """The axis of a cylindrical/conical face, a linear edge, or a circular edge."""

    how: Literal["entity"] = "entity"
    entity: FaceRef | EdgeRef


class AxisPlaneIntersection(IRModel):
    how: Literal["plane_intersection"] = "plane_intersection"
    a: PlaneRef | FaceRef
    b: PlaneRef | FaceRef


class RefPlaneOperation(OperationBase):
    op: Literal["ref_plane"] = "ref_plane"
    define: Annotated[
        PlaneOffset | PlaneAngle | PlaneThreePoints | PlaneNormalTo | PlaneMidplane, Field(discriminator="how")
    ]


class RefAxisOperation(OperationBase):
    op: Literal["ref_axis"] = "ref_axis"
    define: Annotated[
        AxisTwoPoints | AxisPointDirection | AxisEntity | AxisPlaneIntersection, Field(discriminator="how")
    ]


class ExtrudeOperation(OperationBase):
    op: Literal["extrude"] = "extrude"
    sketch: OpId
    depth_mm: float = Field(gt=0)
    direction: Direction = Direction.positive
    both_directions: bool = False
    depth2_mm: float | None = Field(default=None, gt=0)
    merge: bool = True


class RevolveOperation(OperationBase):
    op: Literal["revolve"] = "revolve"
    sketch: OpId
    axis: Literal["u", "v"] | str
    angle_deg: float = 360.0


class PolylinePath(IRModel):
    """A path made of straight segments through ``points`` (world mm), with mitred corners or, when
    ``corner_radius_mm`` is set, a circular arc of that radius at every corner. ``kind`` is a discriminator so a
    later reference-to-a-sketch path can join it in a union."""

    kind: Literal["polyline"] = "polyline"
    points: tuple[Vec3, ...] = Field(min_length=2)
    corner_radius_mm: float | None = Field(default=None, gt=0)


class SweepOperation(OperationBase):
    """Move ONE closed profile along ``path``. The profile plane must be normal to the first segment, through
    the first point, with the profile centroid on that point; the result is ``area x path length`` of new material."""

    op: Literal["sweep"] = "sweep"
    sketch: OpId
    path: PolylinePath
    merge: bool = True


class CutOperation(OperationBase):
    op: Literal["cut"] = "cut"
    sketch: OpId
    end_condition: EndCondition = EndCondition.blind
    depth_mm: float | None = Field(default=None, gt=0)
    direction: Direction = Direction.negative
    both_directions: bool = False
    depth2_mm: float | None = Field(default=None, gt=0)
    scope: tuple[BodyRef, ...] | None = None
    """Feature scope: in a multibody part, restricts the cut to these bodies only. FreeCAD and
    the fake always confine a cut to the single body its sketch's face/plane belongs to (PartDesign has no
    concept of a cut spanning several bodies) -- there ``scope``, if given, is validated to name exactly
    that one body rather than used to pick a different one."""

    @model_validator(mode="after")
    def _check_blind_requires_depth(self) -> "CutOperation":
        if self.end_condition == EndCondition.blind and self.depth_mm is None:
            msg = "cut with end_condition=blind requires depth_mm"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_no_up_to_face(self) -> "CutOperation":
        if self.end_condition == EndCondition.up_to_face:
            msg = "cut end_condition is blind or through_all; up_to_face is for hole only"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_scope_not_empty(self) -> "CutOperation":
        if self.scope is not None and len(self.scope) == 0:
            msg = "cut.scope must not be empty when given (omit the field instead)"
            raise ValueError(msg)
        return self


_THREAD_SIZES_HINT = (
    f"coarse M2..M68: {', '.join(thread_sizes())}; fine as M<diameter>x<pitch> of ISO 261, "
    f"{len(FINE_THREADS)} sizes from M1x0.2 to M100x6, e.g. M45x1.5"
)


def _hole_schema(schema: dict[str, Any]) -> None:
    """``diameter_mm`` is filled from the thread table before validation for iso_tapped/iso_clearance, so a document
    that omits it is valid: the published JSON schema must not claim otherwise (``simple`` still names it)."""
    required = schema.get("required")
    if isinstance(required, list) and "diameter_mm" in required:
        required.remove("diameter_mm")


class Counterbore(IRModel):
    diameter_mm: float = Field(gt=0)
    depth_mm: float = Field(gt=0)


class Countersink(IRModel):
    diameter_mm: float = Field(gt=0)
    angle_deg: float = Field(default=DEFAULT_COUNTERSINK_ANGLE_DEG, gt=0, lt=180)
    """Included angle of the cone (FreeCAD's default: 90)."""


class HoleOperation(OperationBase):
    model_config = ConfigDict(json_schema_extra=_hole_schema)

    op: Literal["hole"] = "hole"
    face: FaceRef
    position: tuple[float, float] | None = None
    """(u, v) from the face centre in the face frame (a convex cylindrical face: axial_mm, angle_deg). Exactly one of
    ``position`` and ``at``."""
    at: Vec3 | None = None
    """A world point (mm) instead of ``position``: it is projected onto the planar ``face`` along its normal and must
    land inside the face (its bounding box); the hole is stored as this world point."""
    standard: HoleStandard
    size: str | None = None
    diameter_mm: float = Field(gt=0)
    """The drilled diameter. Omitted for ``iso_tapped`` (the coarse tap drill of ``size``) and ``iso_clearance``
    (the ISO 273 medium clearance of ``size``): the table in ``contract.threads`` fills it in before validation, so
    the field is always present afterwards. ``simple`` always names it."""
    end_condition: EndCondition
    depth_mm: float | None = Field(default=None, gt=0)
    fit: str | None = Field(default=None, pattern=FIT_PATTERN)
    """Hole-basis tolerance class the hole is made to (``H7``). IT8 or finer makes it a precision hole (its colour,
    and the default fit of its ``hole_dim``). A thread makes the hole threaded whatever its fit."""
    thread_depth_mm: float | None = Field(default=None, gt=0)
    """Cosmetic thread length of an ``iso_tapped`` hole (<= ``depth_mm``): metadata only, it changes no geometry."""
    counterbore: Counterbore | None = None
    """FreeCAD's counterbore: a larger cylinder at the entry (``HoleCutDiameter`` x ``HoleCutDepth``). ``depth_mm`` of a
    blind hole is measured from the entry plane and includes it. Not together with ``countersink``."""
    countersink: Countersink | None = None
    """FreeCAD's countersink: a cone at the entry (``HoleCutDiameter`` at the entry plane, ``HoleCutCountersinkAngle``
    included angle, default 90). Not together with ``counterbore``."""
    drill_point: DrillPoint = DrillPoint.angled
    """Bottom of a blind hole: ``angled`` (FreeCAD's default: a cone of ``drill_point_angle_deg`` added beyond
    ``depth_mm``) or ``flat``. A through_all hole has no bottom and ignores it."""
    drill_point_angle_deg: float = Field(default=DEFAULT_DRILL_POINT_ANGLE_DEG, gt=0, lt=180)
    """Included angle of an ``angled`` drill point (FreeCAD's ``DrillPointAngle`` default: 118)."""
    up_to_face: FaceRef | None = None
    """With ``end_condition: up_to_face``: the face whose plane ends the hole. It must be planar and normal to the hole
    axis (PartDesign::Hole cannot end on an oblique face); the hole is drilled blind to that distance, so it breaks into
    whatever cavity lies between the entry and that plane."""

    @model_validator(mode="before")
    @classmethod
    def _fill_diameter_from_the_thread_table(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("diameter_mm") is not None:
            known = thread(str(data["size"])) if data.get("size") is not None else None
            return {**data, "size": known.designation} if known is not None else data  # M45x1,5 -> M45x1.5
        standard, size = data.get("standard"), data.get("size")
        if standard not in (HoleStandard.iso_tapped, HoleStandard.iso_clearance):
            return data  # simple: the diameter is required and the schema says so
        tapped = HoleStandard(standard) is HoleStandard.iso_tapped
        if size is None:
            msg = (
                f"size is missing: a {HoleStandard(standard).value} hole needs size (e.g. M6 or M45x1.5; "
                f"{_THREAD_SIZES_HINT}); its diameter is derived from it"
            )
            raise ValueError(msg)
        spec = thread(str(size))
        if spec is None:
            if tapped:
                # a tapped hole is drilled at the tap drill of its size (V12 refuses any other diameter): giving a
                # diameter does not help, only a size the table knows does
                msg = f"size {size!r} is not supported for a tapped hole; supported sizes: {_THREAD_SIZES_HINT}"
            else:
                msg = f"size {size!r} is not in the ISO metric thread table ({_THREAD_SIZES_HINT}): name diameter_mm"
            raise ValueError(msg)
        diameter = spec.tap_drill_mm if tapped else spec.clearance_medium_mm
        if diameter is None:
            msg = f"the thread table has no ISO 273 clearance hole for {spec.designation}: name diameter_mm"
            raise ValueError(msg)
        return {**data, "size": spec.designation, "diameter_mm": diameter}

    @model_validator(mode="after")
    def _check_position_xor_at(self) -> "HoleOperation":
        if (self.position is None) == (self.at is None):
            msg = (
                "hole needs exactly one of position ((u, v) from the face centre) and at (a world point [x, y, z] mm), "
                + ("got both" if self.position is not None else "got neither")
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_blind_requires_depth(self) -> "HoleOperation":
        if self.end_condition == EndCondition.blind and self.depth_mm is None:
            msg = "hole with end_condition=blind requires depth_mm"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_iso_requires_size(self) -> "HoleOperation":
        if self.standard in (HoleStandard.iso_clearance, HoleStandard.iso_tapped) and self.size is None:
            msg = "hole with standard=iso_clearance/iso_tapped requires size"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_end_condition_fields(self) -> "HoleOperation":
        if (self.end_condition == EndCondition.up_to_face) != (self.up_to_face is not None):
            msg = (
                "hole.up_to_face (the face that ends the hole) goes with end_condition=up_to_face and with nothing else"
            )
            raise ValueError(msg)
        if self.end_condition == EndCondition.up_to_face and self.depth_mm is not None:
            msg = "hole with end_condition=up_to_face takes no depth_mm: the target face sets the depth"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _check_entry_shape(self) -> "HoleOperation":
        """Counterbore / countersink / drill point: the numbers must describe a hole that exists."""
        if self.counterbore is not None and self.countersink is not None:
            msg = "hole takes a counterbore or a countersink, not both"
            raise ValueError(msg)
        wide = self.counterbore or self.countersink
        if wide is not None and wide.diameter_mm <= self.diameter_mm:
            kind = "counterbore" if self.counterbore is not None else "countersink"
            msg = (
                f"hole.{kind}.diameter_mm ({wide.diameter_mm:g}) must be larger than the hole's diameter_mm "
                f"({self.diameter_mm:g})"
            )
            raise ValueError(msg)
        if self.end_condition == EndCondition.blind and self.depth_mm is not None:
            if self.counterbore is not None and self.counterbore.depth_mm >= self.depth_mm:
                msg = (
                    f"hole.counterbore.depth_mm ({self.counterbore.depth_mm:g}) must be less than depth_mm "
                    f"({self.depth_mm:g}): depth_mm is measured from the entry face and includes the counterbore"
                )
                raise ValueError(msg)
            if self.countersink is not None:
                cone = countersink_depth(self.diameter_mm, self.countersink.diameter_mm, self.countersink.angle_deg)
                if cone >= self.depth_mm:
                    msg = (
                        f"the countersink cone is {cone:.4g} mm deep, which does not fit in depth_mm "
                        f"({self.depth_mm:g}): narrow the countersink or drill deeper"
                    )
                    raise ValueError(msg)
        return self


class LinearPatternOperation(OperationBase):
    op: Literal["linear_pattern"] = "linear_pattern"
    features: tuple[OpId, ...] = Field(min_length=1)
    direction: AxisName | Vec3 | AxisRef
    spacing_mm: float = Field(gt=0)
    count: int = Field(ge=2)
    direction2: AxisName | Vec3 | AxisRef | None = None
    spacing2_mm: float | None = None
    count2: int | None = None

    @model_validator(mode="after")
    def _check_second_axis_all_or_none(self) -> "LinearPatternOperation":
        present = (self.direction2 is not None, self.spacing2_mm is not None, self.count2 is not None)
        if any(present) and not all(present):
            msg = "linear_pattern direction2/spacing2_mm/count2 must be all set or all omitted"
            raise ValueError(msg)
        return self


class CircularPatternOperation(OperationBase):
    op: Literal["circular_pattern"] = "circular_pattern"
    features: tuple[OpId, ...] = Field(min_length=1)
    axis: FaceRef | EdgeRef | Axis | AxisRef
    count: int = Field(ge=2)

    _check_axis = field_validator("axis", mode="before")(_reject_zero_axis)

    angle_deg: float = 360.0
    equal_spacing: bool = True


class ChamferOperation(OperationBase):
    """``edges``: single edges and/or edge sets (``kind: edges``, resolved when the op runs). ``skip_failing``: when the
    CAD cannot chamfer some of the edges, apply the rest and report the skipped ones (default: fail naming them)."""

    op: Literal["chamfer"] = "chamfer"
    edges: tuple[EdgeRef | EdgeSet, ...] = Field(min_length=1)
    distance1_mm: float = Field(gt=0)
    distance2_mm: float | None = None
    distance1_on: FaceRef | None = None
    skip_failing: bool = False

    @model_validator(mode="after")
    def _check_distance2_requires_distance1_on(self) -> "ChamferOperation":
        if self.distance2_mm is not None and self.distance1_on is None:
            msg = "chamfer with distance2_mm requires distance1_on"
            raise ValueError(msg)
        return self


class FilletOperation(OperationBase):
    """Same ``edges`` and ``skip_failing`` as the chamfer."""

    op: Literal["fillet"] = "fillet"
    edges: tuple[EdgeRef | EdgeSet, ...]
    radius_mm: float = Field(gt=0)
    skip_failing: bool = False


class DraftOperation(OperationBase):
    """Tilt the ``faces`` (walls that lie along the pull direction) by ``angle_deg`` so a mould half can release the
    part. ``neutral`` is the plane the faces pivot about: the section of each face on it keeps its size. ``pull`` is the
    direction the mould half that forms these faces moves when it opens (a vector, or an axis name for +X/+Y/+Z); it
    must be perpendicular to the neutral plane (parallel to its normal, either sense). After the op every drafted
    face's OUTWARD normal has a component sin(angle) along ``pull``, so that mould half slides off the face."""

    op: Literal["draft"] = "draft"
    faces: tuple[FaceRef, ...] = Field(min_length=1)
    neutral: PlaneRef | FaceRef
    pull: AxisName | Vec3
    angle_deg: float = Field(gt=0, le=30, allow_inf_nan=False)


class ShellOperation(OperationBase):
    """Hollow the body the ``faces`` belong to: those faces are REMOVED (left open) and every other face becomes
    a wall of ``thickness_mm``. The result is the WALLS only. ``direction`` inward keeps the outer size (the wall
    grows into the part, so the volume falls); outward keeps the original shape as the CAVITY and grows the walls
    outside it (a cover or enclosure for the part: the outer size grows, and the volume can rise or fall). ``join``
    is how the walls meet at the edges: ``intersection`` extends them to a sharp corner, ``arc`` rounds them."""

    op: Literal["shell"] = "shell"
    faces: tuple[FaceRef, ...] = Field(min_length=1)
    thickness_mm: float = Field(gt=0, allow_inf_nan=False)
    direction: Literal["inward", "outward"] = "inward"
    join: Literal["intersection", "arc"] = "intersection"


class MirrorOperation(OperationBase):
    """Reflect the named ``features`` (extrudes, cuts, holes or revolves; a pattern, a sweep or a dress-up is
    not a seed: mirror the features it is made of) across ``plane`` and fuse the reflection into the SAME body: one new
    feature, the seeds stay. The feature is reflected as a whole, axis included (a hole's axis is reflected too).
    ``plane`` is a base plane, a datum plane or a planar face; the mirror is across that plane wherever it sits (it is
    not required to pass through the origin)."""

    op: Literal["mirror"] = "mirror"
    features: tuple[OpId, ...] = Field(min_length=1)
    plane: PlaneRef | FaceRef


class MirrorBodyOperation(OperationBase):
    """Reflect a whole body across ``plane`` into a NEW independent body (its own ref, no feature history); the source
    stays where it is. For a part that is its own mirror image (a left-hand and a right-hand bracket, two mould
    halves)."""

    op: Literal["mirror_body"] = "mirror_body"
    source: BodyRef
    plane: PlaneRef | FaceRef


class SplitOperation(OperationBase):
    """Cut a body in two along ``plane`` (a base plane, a datum plane or a planar face; it is not required to pass
    through the origin). The source is CONSUMED. ``positive`` is the piece on the side the plane's normal points to,
    ``negative`` the other. ``keep`` both (default) makes two bodies you address with ``BodyRef.side`` (required for
    every later ref to this split); ``keep`` positive or negative discards the other piece and the single survivor is
    addressed without a side. A plane that does not cut the body is an error. For the two halves of a mould."""

    op: Literal["split"] = "split"
    source: BodyRef
    plane: PlaneRef | FaceRef
    keep: Literal["both", "positive", "negative"] = "both"


class BaseFlangeOperation(OperationBase):
    """Start a sheet-metal part: the closed profiles of ``sketch`` become a flat sheet ``thickness_mm`` thick, as a NEW
    body, on the side of the sketch normal that ``direction`` names. The largest profile is the outline; every other
    closed profile inside it is a hole or cut-out (a sheet takes no cut or hole step afterwards). Sheet-metal ops exist
    only when the CAD has its sheet-metal capability."""

    op: Literal["base_flange"] = "base_flange"
    sketch: OpId
    thickness_mm: float = Field(gt=0)
    direction: Direction = Direction.positive


class EdgeFlangeOperation(OperationBase):
    """Bend a wall (a flange, "labio") out of one straight ``edge`` of a sheet-metal body. ``angle_deg`` is the bend
    angle (90: perpendicular to the sheet) and the wall rises from the face the edge belongs to, on the side of that
    face's normal. ``position`` says where the bend goes: ``material_inside`` keeps the outer face of the wall in the
    plane of the sheet's edge (the bend eats into the sheet), ``material_outside`` bends beyond the edge and leaves
    the sheet as it is. ``length_mm`` is measured to the outer virtual sharp (the corner the two outer surfaces would
    make) or, with ``length_from=leg``, as the straight part after the bend."""

    op: Literal["edge_flange"] = "edge_flange"
    edge: EdgeRef
    length_mm: float = Field(gt=0)
    angle_deg: float = Field(default=90.0, gt=0, lt=180)
    bend_radius_mm: float = Field(gt=0)
    position: BendPosition = "material_inside"
    length_from: FlangeLengthFrom = "outer_sharp"


class UnfoldOperation(OperationBase):
    """Make the flat pattern ("desarrollo plano") of a sheet-metal body, keeping ``face`` (a planar face of the sheet)
    fixed. ``k_factor`` is REQUIRED (ANSI convention: the neutral line sits ``k_factor * thickness`` from the inside of
    the bend; it depends on material, thickness, radius and tooling, so there is no safe default). The flat pattern is
    not part of the solid: mass and bounding box keep describing the folded part; its size comes back as a measured
    fact and ``export(action=dxf, flat_pattern=<this step id>)`` writes it for the cutting shop."""

    op: Literal["unfold"] = "unfold"
    face: FaceRef
    k_factor: float = Field(gt=0, lt=1)


class InsertComponentOperation(OperationBase):
    """Insert an instance of a part DOCUMENT into the assembly at ``placement`` (origin plus rotation, in the assembly
    axes). ``part`` is the name of an open, saved part document, or the file of a saved part that is not open (it is
    loaded for the insert, with no window); it stays editable in its own document and axes, and the assembly shows it as
    saved. The same part can be inserted any number of times (one instance each)."""

    op: Literal["insert_component"] = "insert_component"
    part: str
    placement: Placement


class FixComponentOperation(OperationBase):
    op: Literal["fix_component"] = "fix_component"
    component: OpId


class PlaceComponentOperation(OperationBase):
    """Move an instance of an inserted part to placement (absolute: origin plus rotation about it, in the
    assembly axes). It sets the pose, it does not add to it; the part is untouched."""

    op: Literal["place_component"] = "place_component"
    component: OpId
    placement: Placement


class AddJointOperation(OperationBase):
    """Join component ``b`` to component ``a`` (omit ``a``: the ground of the assembly) at the pose ``b`` has now,
    which is the value 0 of the joint. ``revolute`` turns ``b`` about ``axis`` (the axis of a cylindrical face or
    circular edge, a linear edge start to end, a datum axis or a literal ``{origin, direction}``; the value is in
    degrees, positive turning right-handed about the axis direction); ``slider`` moves ``b`` along ``axis`` (the value
    is in mm); ``fixed`` holds ``b`` rigid to ``a``. ``min`` / ``max`` limit the value (none: unlimited)."""

    op: Literal["add_joint"] = "add_joint"
    type: Literal["fixed", "revolute", "slider"]
    b: OpId
    a: OpId | None = None
    axis: AxisRef | EdgeRef | FaceRef | Axis | None = None
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _check_joint(self) -> "AddJointOperation":
        if self.type == "fixed" and (self.axis is not None or self.min is not None or self.max is not None):
            msg = "a fixed joint has no axis and no limits"
            raise ValueError(msg)
        if self.type != "fixed" and self.axis is None:
            msg = f"a {self.type} joint needs the axis it turns about / slides along"
            raise ValueError(msg)
        if self.min is not None and self.max is not None and self.min > self.max:
            msg = "min must not exceed max"
            raise ValueError(msg)
        if self.a is not None and self.a == self.b:
            msg = "a joint joins two different components"
            raise ValueError(msg)
        return self


class SetJointOperation(OperationBase):
    """Move the joint made by the step ``joint`` to ``value`` (degrees of a revolute, mm of a slider), absolute from
    the pose the joint was made at; the component it joins is placed by it (and `place_component` is refused for it)."""

    op: Literal["set_joint"] = "set_joint"
    joint: OpId
    value: float


class MateConcentricOperation(OperationBase):
    op: Literal["mate_concentric"] = "mate_concentric"
    a: FaceRef
    b: FaceRef
    alignment: MateAlignment = MateAlignment.aligned


class MateCoincidentOperation(OperationBase):
    op: Literal["mate_coincident"] = "mate_coincident"
    a: FaceRef
    b: FaceRef
    alignment: MateAlignment = MateAlignment.anti_aligned


class MateDistanceOperation(OperationBase):
    op: Literal["mate_distance"] = "mate_distance"
    a: FaceRef
    b: FaceRef
    distance_mm: float = Field(ge=0)
    alignment: MateAlignment = MateAlignment.anti_aligned
    side: Side = Side.positive


class CheckInterferenceOperation(OperationBase):
    op: Literal["check_interference"] = "check_interference"
    components: tuple[OpId, ...] | None = None
    expect_zero: bool = True


OP_NAMES: frozenset[str] = frozenset(
    {
        "box",
        "cylinder",
        "boolean_union",
        "boolean_subtract",
        "boolean_intersect",
        "translate",
        "rotate",
        "copy_body",
        "sketch",
        "ref_plane",
        "ref_axis",
        "extrude",
        "revolve",
        "sweep",
        "cut",
        "hole",
        "linear_pattern",
        "circular_pattern",
        "chamfer",
        "fillet",
        "shell",
        "draft",
        "mirror",
        "mirror_body",
        "split",
        "base_flange",
        "edge_flange",
        "unfold",
        "insert_component",
        "fix_component",
        "place_component",
        "add_joint",
        "set_joint",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
        "check_interference",
    }
)

OpCategory = Literal["everyday", "multibody", "sheet_metal", "assembly"]

OP_CATEGORY: dict[str, OpCategory] = {
    # everyday (the default an agent should reach for first): sketch-based feature ops, the way most CAD
    # engineers -- and PartDesign-style workflows -- actually model. A hole is `hole` (an ISO
    # standard, editable hole feature), a slot/pocket is `cut`, never a boolean subtraction (in a concept sketch of
    # a part, box/cylinder + booleans are fine; the discipline applies to the detail).
    "sketch": "everyday",
    "ref_plane": "everyday",
    "ref_axis": "everyday",
    "extrude": "everyday",
    "cut": "everyday",
    "revolve": "everyday",
    "sweep": "everyday",
    "hole": "everyday",
    "fillet": "everyday",
    "chamfer": "everyday",
    "shell": "everyday",
    "draft": "everyday",
    "mirror": "everyday",
    "linear_pattern": "everyday",
    "circular_pattern": "everyday",
    # multibody: body-to-body operations (primitives feeding a boolean, rigid moves of a whole body,
    # `cut.scope` usage) -- correct when the part IS actually built from independent bodies (fish-mouth
    # coping with `keep_tools`, welded structures), wrong as a substitute for an everyday feature.
    "box": "multibody",
    "cylinder": "multibody",
    "boolean_union": "multibody",
    "boolean_subtract": "multibody",
    "boolean_intersect": "multibody",
    "translate": "multibody",
    "rotate": "multibody",
    "copy_body": "multibody",
    "mirror_body": "multibody",
    "split": "multibody",
    # sheet_metal: only offered by a CAD that has the sheet-metal capability (a bent part, its flat pattern).
    "base_flange": "sheet_metal",
    "edge_flange": "sheet_metal",
    "unfold": "sheet_metal",
    # assembly: multi-part operations (insert, fix, mate, interference).
    "insert_component": "assembly",
    "fix_component": "assembly",
    "place_component": "assembly",
    "add_joint": "assembly",
    "set_joint": "assembly",
    "mate_concentric": "assembly",
    "mate_coincident": "assembly",
    "mate_distance": "assembly",
    "check_interference": "assembly",
}
"""Operation-vocabulary tiers (never a field the agent writes into a plan -- purely a classification
for a client to group the operations by, and to name the tier of a suggested alternative)."""

Operation = Annotated[
    BoxOperation
    | CylinderOperation
    | BooleanUnionOperation
    | BooleanSubtractOperation
    | BooleanIntersectOperation
    | TranslateOperation
    | RotateOperation
    | CopyBodyOperation
    | SketchOperation
    | RefPlaneOperation
    | RefAxisOperation
    | ExtrudeOperation
    | RevolveOperation
    | SweepOperation
    | CutOperation
    | HoleOperation
    | LinearPatternOperation
    | CircularPatternOperation
    | ChamferOperation
    | FilletOperation
    | ShellOperation
    | DraftOperation
    | MirrorOperation
    | MirrorBodyOperation
    | SplitOperation
    | BaseFlangeOperation
    | EdgeFlangeOperation
    | UnfoldOperation
    | InsertComponentOperation
    | FixComponentOperation
    | PlaceComponentOperation
    | AddJointOperation
    | SetJointOperation
    | MateConcentricOperation
    | MateCoincidentOperation
    | MateDistanceOperation
    | CheckInterferenceOperation,
    Field(discriminator="op"),
]
