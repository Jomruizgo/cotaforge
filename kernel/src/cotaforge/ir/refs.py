"""Symbolic references (ir/refs.py).

An IR operation never carries a concrete adapter handle. Instead it names the
*earlier operation* that produced the geometry (``from_op``) plus a selector
that must resolve to exactly one candidate at execution time (5.3). This
module has no notion of "resolution" itself — it only defines the reference
shapes and their own-field validators.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cotaforge.contract import AxisName, CurveType, PlaneName, ProvenanceRole, SurfaceType, Vec3

OpId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]

SelectionToken = Annotated[str, Field(min_length=1)]
"""A ``get_selection`` entry's ``PersistentRef.token`` (``contract.responses.SelectionEntry.ref.token``):
free-form, adapter-defined text (e.g. FreeCAD's ``"<doc>|<body>|<entity>"``) that an ``OpId`` cannot carry —
``OpId``'s pattern forbids ``:``/``|``, and a selection token is not an operation id in the first place
(so the agent's ``get_selection`` result is usable as a ``FaceRef``/``EdgeRef``)."""


class IRModel(BaseModel):
    """Shared base for every IR model: immutable, and rejects unknown fields.

    Without ``extra="forbid"``, a mistyped or renamed field (e.g. ``nrml`` for ``normal``) was
    silently dropped by pydantic instead of raising — a selector meant to narrow a face match would
    quietly fall back to its default (unconstrained) and match every candidate, or worse, resolve to the
    wrong one. Every model in ``cotaforge.ir`` must derive from this (or set the same ``model_config``)
    so a plan document's shape is checked exactly, not guessed at.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class Range(IRModel):
    min: float | None = None
    max: float | None = None


class FaceSelector(IRModel):
    """Attribute filters (every non-``None`` one must match) plus at most ONE tie-breaker among the survivors:
    ``nearest_to`` (the candidate whose centre is closest to the point), ``largest`` / ``smallest`` (by area)
    or ``index`` (last resort: position in the survivors sorted by centre). A tie-breaker that cannot
    separate its two best candidates (equal within tolerance) leaves the reference ambiguous."""

    surface: SurfaceType | None = None
    normal: Vec3 | None = None
    axis_direction: Vec3 | None = None
    radius: float | Range | None = None
    area_mm2: float | Range | None = None
    center: Vec3 | None = None
    role: ProvenanceRole = ProvenanceRole.any
    nearest_to: Vec3 | None = None
    largest: bool = False
    smallest: bool = False
    index: int | None = None

    @model_validator(mode="after")
    def _check_one_tie_breaker(self) -> "FaceSelector":
        chosen = [
            name
            for name, active in (
                ("nearest_to", self.nearest_to is not None),
                ("largest", self.largest),
                ("smallest", self.smallest),
                ("index", self.index is not None),
            )
            if active
        ]
        if len(chosen) > 1:
            msg = f"FaceSelector allows at most one of nearest_to / largest / smallest / index, got {', '.join(chosen)}"
            raise ValueError(msg)
        return self


class FaceRef(IRModel):
    """Names a face by provenance (``from_op`` + ``select``) or by a live UI selection (``from_selection``): exactly one
    of the two. ``from_selection`` carries the ``token`` of a ``get_selection`` entry verbatim
    (``executor.resolver.Resolver.face`` resolves it through the adapter's own ``resolve_reference``/``face_info``, the
    same seam the feature tree uses) — it names one already-picked face directly, so ``select`` (which narrows a *set*
    of candidates down to one) does not apply together with it.
    """

    kind: Literal["face"] = "face"
    from_op: OpId | None = None
    select: FaceSelector = FaceSelector()
    from_selection: SelectionToken | None = None

    @model_validator(mode="after")
    def _check_from_op_xor_from_selection(self) -> "FaceRef":
        if (self.from_op is None) == (self.from_selection is None):
            msg = "FaceRef requires exactly one of 'from_op' or 'from_selection'"
            raise ValueError(msg)
        if self.from_selection is not None and self.select != FaceSelector():
            msg = "FaceRef.select cannot be combined with from_selection (it already names one face)"
            raise ValueError(msg)
        return self


class EdgeRef(IRModel):
    kind: Literal["edge"] = "edge"
    between: tuple[FaceRef, FaceRef] | None = None
    of: FaceRef | None = None
    curve: CurveType | None = None
    radius: float | Range | None = None
    length_mm: float | Range | None = None
    nearest_to: Vec3 | None = None
    """Tie-breaker among the surviving edges: the one whose midpoint (``EdgeInfo.center`` or the midpoint of
    ``start``/``end``) is closest to the point; two equally close edges stay ambiguous."""
    from_selection: SelectionToken | None = None
    """Names an edge by a live UI selection instead of by provenance (mirrors ``FaceRef``): exactly
    one of ``between`` / ``of`` / ``from_selection``, and none of the filter fields above combine with it."""

    @model_validator(mode="after")
    def _check_between_xor_of(self) -> "EdgeRef":
        chosen = sum((self.between is not None, self.of is not None, self.from_selection is not None))
        if chosen != 1:
            msg = "EdgeRef requires exactly one of 'between', 'of' or 'from_selection'"
            raise ValueError(msg)
        if self.of is not None and self.curve is None:
            msg = "EdgeRef.of requires 'curve'"
            raise ValueError(msg)
        filters_given = any(value is not None for value in (self.curve, self.radius, self.length_mm, self.nearest_to))
        if self.from_selection is not None and filters_given:
            msg = "EdgeRef filters (curve/radius/length_mm/nearest_to) cannot be combined with from_selection"
            raise ValueError(msg)
        return self


class EdgeWhere(IRModel):
    """Criteria of an ``EdgeSet`` (every one given must hold; none given = every edge of the candidates). Only edges
    shared by exactly two faces are candidates (the ones a chamfer or a fillet can take)."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    parallel_to: AxisName | Vec3 | None = None
    """Straight edges parallel (either sense) to X|Y|Z (any case) or to a vector; a curved edge never matches."""

    @field_validator("parallel_to", mode="before")
    @classmethod
    def _axis_name_any_case(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    length_mm: float | Range | None = None
    convex: bool | None = None
    """True: material angle below 180 degrees (an outside corner); False: an inside corner."""
    planar: bool | None = None
    """True: both faces of the edge are planar (no hole wall, no round); False: at least one is not."""
    curve: CurveType | None = None
    on_face: FaceRef | None = None
    """Only the edges of this face (its contour); the body is then the face's."""
    exclude_holes: bool = False
    """Drop the edges of the walls of holes (concave cylinders): the mouth, the bottom and the steps of a counterbore."""
    except_: tuple[Vec3 | EdgeRef, ...] = Field(default=(), alias="except")
    """Edges to drop: a point (the edge that passes through it, within the position tolerance) or an edge ref."""


class EdgeSet(IRModel):
    """Several edges named by what they are, resolved when the op runs on the geometry as it stands then (so it follows
    the part when an earlier step changes). ``from_op``: the step whose body supplies the edges (the one it created or
    modified); it may be omitted when ``where.on_face`` names the face. A set that matches nothing is a plan error that
    says how many edges each criterion left."""

    kind: Literal["edges"]
    from_op: OpId | None = None
    where: EdgeWhere = EdgeWhere()

    @model_validator(mode="after")
    def _check_source(self) -> "EdgeSet":
        if self.from_op is None and self.where.on_face is None:
            msg = "EdgeSet needs 'from_op' (the step whose body it takes the edges from) or where.on_face"
            raise ValueError(msg)
        return self


class VertexRef(IRModel):
    kind: Literal["vertex"] = "vertex"
    faces: tuple[FaceRef, FaceRef, FaceRef] | None = None
    edge: EdgeRef | None = None
    end: Literal["start", "end"] | None = None

    @model_validator(mode="after")
    def _check_faces_xor_edge(self) -> "VertexRef":
        if (self.edge is None) != (self.end is None):
            msg = "VertexRef.edge and VertexRef.end must be set together"
            raise ValueError(msg)
        has_faces = self.faces is not None
        has_edge = self.edge is not None
        if has_faces == has_edge:
            msg = "VertexRef requires exactly one of 'faces' or ('edge' and 'end')"
            raise ValueError(msg)
        return self


class PlaneRef(IRModel):
    """A plane: a base plane by ``name`` (XY/YZ/XZ) or a datum plane made by an earlier ``ref_plane`` op
    (``from_op``): exactly one of the two."""

    kind: Literal["plane"] = "plane"
    name: PlaneName | None = None
    from_op: OpId | None = None

    @model_validator(mode="after")
    def _check_name_xor_from_op(self) -> "PlaneRef":
        if (self.name is None) == (self.from_op is None):
            msg = "PlaneRef requires exactly one of 'name' or 'from_op'"
            raise ValueError(msg)
        return self


class AxisRef(IRModel):
    """A datum axis made by an earlier ``ref_axis`` op."""

    kind: Literal["axis_ref"] = "axis_ref"
    from_op: OpId


class BodyRef(IRModel):
    """A body made by an earlier op. ``side`` picks one piece of a ``split`` that kept both: ``positive`` is the piece
    on the side the split plane's normal points to, ``negative`` the other. It is required for a ref to such a split
    and forbidden for every other ref (plan rule V3)."""

    kind: Literal["body"] = "body"
    from_op: OpId
    side: Literal["positive", "negative"] | None = None


class ComponentRef(IRModel):
    """A component of an assembly made by an earlier step (``insert_component``, or an operation of the adapter that
    returns one). Only an operation the adapter declared takes it; the common vocabulary names components by step id."""

    kind: Literal["component"] = "component"
    from_op: OpId


Ref = FaceRef | EdgeRef | EdgeSet | VertexRef | PlaneRef | AxisRef | BodyRef | ComponentRef
REF_TYPES: tuple[type, ...] = (FaceRef, EdgeRef, EdgeSet, VertexRef, PlaneRef, AxisRef, BodyRef, ComponentRef)
