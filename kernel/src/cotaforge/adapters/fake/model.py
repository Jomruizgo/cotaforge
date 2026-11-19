"""In-memory representation of the fake adapter.

Everything here is plain dataclasses keyed by string ids of the form ``"{kind}:{n}"``
with a per-document counter. Vector helpers live here too so the other fake modules
share one implementation.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from cotaforge.contract import (
    Arc3Entity,
    ArcEndsEntity,
    ArcEntity,
    Axis,
    BoundingBox,
    CircleEntity,
    ConstraintConflict,
    CurveType,
    DocumentKind,
    JointType,
    LineEntity,
    NamedDimension,
    Profile,
    RectEntity,
    SketchConstraint,
    SketchEntity,
    SketchFrame,
    SlotEntity,
    SquareEntity,
    SurfaceType,
    Transform,
    Vec3,
)

TOLERANCE = 1e-6
FINGERPRINT_DIGITS = 6


# --- Vector helpers -----------------------------------------------------------------


def vadd(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vscale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vlength(a: Vec3) -> float:
    return math.sqrt(vdot(a, a))


def vunit(a: Vec3) -> Vec3:
    length = vlength(a)
    if length < 1e-12:
        msg = "cannot normalise a zero-length vector"
        raise ValueError(msg)
    return vscale(a, 1.0 / length)


def vclose(a: Vec3, b: Vec3, tol: float = TOLERANCE) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def vparallel(a: Vec3, b: Vec3, tol: float = 1e-9) -> bool:
    """True when the unit vectors are parallel in either sense."""
    return abs(abs(vdot(a, b)) - 1.0) <= tol


def vround(a: Vec3, digits: int = FINGERPRINT_DIGITS) -> Vec3:
    return (round(a[0], digits) + 0.0, round(a[1], digits) + 0.0, round(a[2], digits) + 0.0)


def bbox_of_points(points: tuple[Vec3, ...] | list[Vec3]) -> BoundingBox:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    return BoundingBox(min=(min(xs), min(ys), min(zs)), max=(max(xs), max(ys), max(zs)))


def bbox_union(a: BoundingBox | None, b: BoundingBox) -> BoundingBox:
    if a is None:
        return b
    return BoundingBox(
        min=tuple(min(x, y) for x, y in zip(a.min, b.min, strict=True)),  # type: ignore[arg-type]
        max=tuple(max(x, y) for x, y in zip(a.max, b.max, strict=True)),  # type: ignore[arg-type]
    )


def bbox_corners(box: BoundingBox) -> tuple[Vec3, ...]:
    return tuple(
        (x, y, z) for x in (box.min[0], box.max[0]) for y in (box.min[1], box.max[1]) for z in (box.min[2], box.max[2])
    )


def bbox_transformed(box: BoundingBox, t: Transform) -> BoundingBox:
    """Axis-aligned box of the transformed corners (exact for boxes rotated by 90 degree multiples)."""
    return bbox_of_points(tuple(t.apply(c) for c in bbox_corners(box)))


def cylinder_bbox(origin: Vec3, direction: Vec3, radius: float, height: float) -> BoundingBox:
    """Exact axis-aligned box of a cylinder of the given radius from ``origin`` along ``direction``."""
    d = vunit(direction)
    end = vadd(origin, vscale(d, height))
    mins = []
    maxs = []
    for i in range(3):
        spread = radius * math.sqrt(max(0.0, 1.0 - d[i] * d[i]))
        mins.append(min(origin[i], end[i]) - spread)
        maxs.append(max(origin[i], end[i]) + spread)
    return BoundingBox(min=(mins[0], mins[1], mins[2]), max=(maxs[0], maxs[1], maxs[2]))


def disc_bbox(center: Vec3, normal: Vec3, radius: float) -> BoundingBox:
    return cylinder_bbox(center, normal, radius, 0.0)


def transform_axis(axis: Axis, t: Transform) -> Axis:
    return Axis(origin=t.apply(axis.origin), direction=t.apply_direction(axis.direction))


def polygon_area_and_centroid(points: tuple[Vec3, ...], normal: Vec3) -> tuple[float, Vec3]:
    """Area and centroid of a planar polygon (world coordinates) with the given unit normal."""
    n = len(points)
    area_vec: Vec3 = (0.0, 0.0, 0.0)
    for i in range(n):
        area_vec = vadd(area_vec, vcross(points[i], points[(i + 1) % n]))
    area = 0.5 * vdot(area_vec, normal)
    origin = points[0]
    weighted: Vec3 = (0.0, 0.0, 0.0)
    for i in range(1, n - 1):
        a = vsub(points[i], origin)
        b = vsub(points[i + 1], origin)
        tri_area = 0.5 * vdot(vcross(a, b), normal)
        tri_centroid = vscale(vadd(vadd(origin, points[i]), points[i + 1]), 1.0 / 3.0)
        weighted = vadd(weighted, vscale(tri_centroid, tri_area))
    if abs(area) < 1e-12:
        return 0.0, origin
    return abs(area), vscale(weighted, 1.0 / area)


# --- Primitives -------------------------------------------------------------------------


class PrimitiveKind(str, Enum):  # noqa: UP042
    prism = "prism"
    cylinder = "cylinder"
    revolution = "revolution"
    chamfer_wedge = "chamfer_wedge"
    fillet_cut = "fillet_cut"
    sweep = "sweep"


@dataclass
class Primitive:
    id: str
    kind: PrimitiveKind
    sign: int
    volume_mm3: float
    centroid: Vec3
    bbox: BoundingBox
    feature: str
    # prism: base loop in world coordinates (counter-clockwise about ``direction``), extrusion direction and depth
    polygon: tuple[Vec3, ...] | None = None
    direction: Vec3 | None = None
    depth: float | None = None
    # cylinder (axis.origin = base centre, height along axis.direction) and revolution (profile in (a, r))
    axis: Axis | None = None
    radius: float | None = None
    height: float | None = None
    profile_ar: tuple[tuple[float, float], ...] | None = None
    angle_deg: float | None = None
    # support points for bounding boxes of wedges, fillet cuts and sweeps (a sweep keeps sampled boundary points of
    # its end faces: the box is exact when built and follows a move, exactly for quarter turns)
    points: tuple[Vec3, ...] = ()
    frame: SketchFrame | None = None

    def axial_range(self) -> tuple[float, float]:
        """Axial interval of a cylinder or revolution, measured along ``axis.direction`` from ``axis.origin``."""
        if self.kind is PrimitiveKind.cylinder:
            return (0.0, float(self.height or 0.0))
        if self.kind is PrimitiveKind.revolution and self.profile_ar:
            values = [a for a, _ in self.profile_ar]
            return (min(values), max(values))
        msg = f"primitive {self.id} has no axis"
        raise ValueError(msg)

    def transformed(self, t: Transform, new_id: str | None = None, feature: str | None = None) -> Primitive:
        """A copy of this primitive with all its geometry moved by ``t``."""
        prim = copy.deepcopy(self)
        prim.id = new_id or self.id
        prim.feature = feature or self.feature
        prim.centroid = t.apply(self.centroid)
        if self.polygon is not None and self.direction is not None and self.depth is not None:
            prim.polygon = tuple(t.apply(p) for p in self.polygon)
            prim.direction = t.apply_direction(self.direction)
            top = tuple(vadd(p, vscale(prim.direction, self.depth)) for p in prim.polygon)
            prim.bbox = bbox_of_points(prim.polygon + top)
        elif self.kind is PrimitiveKind.cylinder and self.axis is not None:
            prim.axis = transform_axis(self.axis, t)
            prim.bbox = cylinder_bbox(prim.axis.origin, prim.axis.direction, float(self.radius), float(self.height))
        elif self.kind is PrimitiveKind.revolution and self.axis is not None:
            prim.axis = transform_axis(self.axis, t)
            prim.bbox = revolution_bbox(prim.axis, self.profile_ar or ())
        else:
            prim.points = tuple(t.apply(p) for p in self.points)
            prim.bbox = bbox_of_points(prim.points) if prim.points else bbox_transformed(self.bbox, t)
        if self.frame is not None:
            prim.frame = SketchFrame(
                origin=t.apply(self.frame.origin),
                u=t.apply_direction(self.frame.u),
                v=t.apply_direction(self.frame.v),
                n=t.apply_direction(self.frame.n),
            )
        return prim

    def scaled(self, factor: float, about: Vec3) -> Primitive:
        """A copy of this primitive scaled uniformly by ``factor`` (> 0) about the point ``about``: lengths grow by
        ``factor``, the volume by ``factor`` cubed, and directions stay as they are."""
        prim = copy.deepcopy(self)
        prim.volume_mm3 = self.volume_mm3 * factor**3
        prim.centroid = scale_point(self.centroid, factor, about)
        prim.bbox = scale_bbox(self.bbox, factor, about)
        if self.polygon is not None:
            prim.polygon = tuple(scale_point(p, factor, about) for p in self.polygon)
        if self.depth is not None:
            prim.depth = self.depth * factor
        if self.axis is not None:
            prim.axis = Axis(origin=scale_point(self.axis.origin, factor, about), direction=self.axis.direction)
        if self.radius is not None:
            prim.radius = self.radius * factor
        if self.height is not None:
            prim.height = self.height * factor
        if self.profile_ar is not None:
            prim.profile_ar = tuple((a * factor, r * factor) for a, r in self.profile_ar)
        prim.points = tuple(scale_point(p, factor, about) for p in self.points)
        if self.frame is not None:
            prim.frame = SketchFrame(
                origin=scale_point(self.frame.origin, factor, about), u=self.frame.u, v=self.frame.v, n=self.frame.n
            )
        return prim


def scale_point(p: Vec3, factor: float, about: Vec3) -> Vec3:
    """``p`` scaled uniformly by ``factor`` about the point ``about``."""
    return vadd(about, vscale(vsub(p, about), factor))


def scale_bbox(box: BoundingBox, factor: float, about: Vec3) -> BoundingBox:
    """The box of a scaled body (a positive factor keeps the order of the corners)."""
    return BoundingBox(min=scale_point(box.min, factor, about), max=scale_point(box.max, factor, about))


def revolution_bbox(axis: Axis, profile_ar: tuple[tuple[float, float], ...]) -> BoundingBox:
    """Bounding box of a full revolution: a cylinder of the maximum radius over the axial extent."""
    a_values = [a for a, _ in profile_ar]
    r_max = max(abs(r) for _, r in profile_ar)
    a_min, a_max = min(a_values), max(a_values)
    return cylinder_bbox(vadd(axis.origin, vscale(axis.direction, a_min)), axis.direction, r_max, a_max - a_min)


# --- Topology -----------------------------------------------------------------------------


@dataclass
class FakeFace:
    id: str
    surface: SurfaceType
    normal: Vec3 | None
    axis: Axis | None
    center: Vec3
    radius: float | None
    area_mm2: float
    bbox: BoundingBox
    body: str
    created_by: str
    modified_by: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    polygon: list[Vec3] | None = None
    # cylindrical faces: axial extent from ``axis.origin``; planar discs and annuli: outer radius for the bbox
    length: float | None = None
    outer_radius: float | None = None

    def transformed(self, t: Transform, new_id: str | None = None) -> FakeFace:
        face = copy.deepcopy(self)
        face.id = new_id or self.id
        face.center = t.apply(self.center)
        if self.normal is not None:
            face.normal = t.apply_direction(self.normal)
        if self.axis is not None:
            face.axis = transform_axis(self.axis, t)
        if self.polygon is not None:
            face.polygon = [t.apply(p) for p in self.polygon]
            face.bbox = bbox_of_points(tuple(face.polygon))
        elif self.surface is SurfaceType.cylinder and face.axis is not None and self.length is not None:
            face.bbox = cylinder_bbox(face.axis.origin, face.axis.direction, float(self.radius), self.length)
        elif self.surface is SurfaceType.plane and face.normal is not None and self.outer_radius is not None:
            face.bbox = disc_bbox(face.center, face.normal, self.outer_radius)
        else:
            face.bbox = bbox_transformed(self.bbox, t)
        return face

    def scaled(self, factor: float, about: Vec3) -> FakeFace:
        """A copy scaled uniformly by ``factor`` (> 0) about ``about``: the area by ``factor`` squared, the lengths by
        ``factor``."""
        face = copy.deepcopy(self)
        face.center = scale_point(self.center, factor, about)
        face.area_mm2 = self.area_mm2 * factor**2
        face.bbox = scale_bbox(self.bbox, factor, about)
        if self.axis is not None:
            face.axis = Axis(origin=scale_point(self.axis.origin, factor, about), direction=self.axis.direction)
        if self.polygon is not None:
            face.polygon = [scale_point(p, factor, about) for p in self.polygon]
        for name in ("radius", "length", "outer_radius"):
            value = getattr(self, name)
            if value is not None:
                setattr(face, name, value * factor)
        return face


@dataclass
class FakeEdge:
    id: str
    curve: CurveType
    faces: tuple[str, str]
    start: Vec3 | None
    end: Vec3 | None
    center: Vec3 | None
    axis: Axis | None
    radius: float | None
    length_mm: float
    bbox: BoundingBox
    convex: bool | None
    dihedral_deg: float | None

    def transformed(self, t: Transform, new_id: str | None = None) -> FakeEdge:
        edge = copy.deepcopy(self)
        edge.id = new_id or self.id
        if self.start is not None and self.end is not None:
            edge.start = t.apply(self.start)
            edge.end = t.apply(self.end)
            edge.bbox = bbox_of_points((edge.start, edge.end))
        if self.center is not None:
            edge.center = t.apply(self.center)
        if self.axis is not None:
            edge.axis = transform_axis(self.axis, t)
            if edge.center is not None and self.radius is not None:
                edge.bbox = disc_bbox(edge.center, edge.axis.direction, self.radius)
        return edge

    def scaled(self, factor: float, about: Vec3) -> FakeEdge:
        """A copy scaled uniformly by ``factor`` (> 0) about ``about``."""
        edge = copy.deepcopy(self)
        edge.length_mm = self.length_mm * factor
        edge.bbox = scale_bbox(self.bbox, factor, about)
        if self.start is not None:
            edge.start = scale_point(self.start, factor, about)
        if self.end is not None:
            edge.end = scale_point(self.end, factor, about)
        if self.center is not None:
            edge.center = scale_point(self.center, factor, about)
        if self.axis is not None:
            edge.axis = Axis(origin=scale_point(self.axis.origin, factor, about), direction=self.axis.direction)
        if self.radius is not None:
            edge.radius = self.radius * factor
        return edge


@dataclass
class FakeVertex:
    id: str
    point: Vec3
    edges: list[str] = field(default_factory=list)

    def transformed(self, t: Transform, new_id: str | None = None) -> FakeVertex:
        return FakeVertex(id=new_id or self.id, point=t.apply(self.point), edges=list(self.edges))

    def scaled(self, factor: float, about: Vec3) -> FakeVertex:
        return FakeVertex(id=self.id, point=scale_point(self.point, factor, about), edges=list(self.edges))


@dataclass
class FakeSketch:
    id: str
    frame: SketchFrame
    entities: tuple[SketchEntity, ...]
    profiles: list[Profile]
    on_face: str | None = None
    constraints: tuple[SketchConstraint, ...] = ()
    named_dimensions: tuple[NamedDimension, ...] = ()
    # degrees of freedom the sketch is left with. 0 for every sketch the tool makes (as on the real adapters, whose
    # constraint set is complete); only the ``underdefined_sketch`` fault leaves some: a feature on it is refused
    free_dof: int = 0


@dataclass
class FakeDatumPlane:
    """A datum plane (``datum_plane``): a fixed pose, never a feature, never in the feature tree, never touching
    mass or bounding box."""

    id: str
    frame: SketchFrame


@dataclass
class FakeDatumAxis:
    id: str
    axis: Axis


@dataclass
class FakeFeature:
    id: str
    kind: str
    name: str
    parameters: dict[str, float | str | bool] = field(default_factory=dict)
    sketch: str | None = None
    primitives: list[str] = field(default_factory=list)
    created_faces: list[str] = field(default_factory=list)
    modified_faces: list[str] = field(default_factory=list)
    created_edges: list[str] = field(default_factory=list)
    created_bodies: list[str] = field(default_factory=list)
    removed_edges: list[str] = field(default_factory=list)
    face_area_deltas: dict[str, float] = field(default_factory=dict)
    instance_count: int | None = None
    face_count: int | None = None  # a component: the faces and edges of its part, counted instead of listed
    edge_count: int | None = None
    # undo support beyond the fields above
    created_vertices: list[str] = field(default_factory=list)
    modified_bodies: list[str] = field(default_factory=list)
    removed_edge_objects: dict[str, FakeEdge] = field(default_factory=dict)
    removed_faces: dict[str, FakeFace] = field(default_factory=dict)
    removed_vertices: dict[str, FakeVertex] = field(default_factory=dict)
    removed_primitives: dict[str, Primitive] = field(default_factory=dict)
    removed_bodies: dict[str, list[str]] = field(default_factory=dict)
    replaced: dict[str, Any] = field(default_factory=dict)  # entity id -> pre-feature copy
    named_dimensions: dict[str, float] = field(default_factory=dict)
    # assembly features
    component: str | None = None
    mate: str | None = None
    previous_transform: Transform | None = None
    previous_fixed: bool | None = None
    joint: str | None = None
    previous_value: float | None = None


@dataclass
class FakeJoint:
    """Component ``b`` is carried by the joint, ``a`` (``None``: the ground) stays; ``base`` is the pose of ``b`` at
    value 0 and the pose at a value is ``joint_delta(...)`` composed after it."""

    id: str
    joint_type: JointType
    a: str | None
    b: str
    axis: Axis | None
    base: Transform
    low: float | None = None
    high: float | None = None
    value: float = 0.0


@dataclass
class FakeComponent:
    id: str
    part: FakeDocument
    transform: Transform
    fixed: bool = False
    mates: list[str] = field(default_factory=list)
    part_id: str = ""
    """The document the part was taken from: a part document changed and saved updates its components on refresh."""


@dataclass
class HistoryEntry:
    """One successful mutating call, recorded so ``edit_feature`` can replay the document (``adapters.fake.editing``):
    the adapter method, the request, the id counters as they were just BEFORE it (a failed attempt consumes ids, and the
    replay must give every entity the id it has now) and the feature it made, if any."""

    method: str
    request: Any
    counters: dict[str, int]
    feature_id: str | None


@dataclass
class FakeDocument:
    id: str
    kind: DocumentKind
    name: str
    features: list[FakeFeature] = field(default_factory=list)
    sketches: dict[str, FakeSketch] = field(default_factory=dict)
    primitives: dict[str, Primitive] = field(default_factory=dict)
    faces: dict[str, FakeFace] = field(default_factory=dict)
    edges: dict[str, FakeEdge] = field(default_factory=dict)
    vertices: dict[str, FakeVertex] = field(default_factory=dict)
    bodies: dict[str, list[str]] = field(default_factory=dict)
    components: dict[str, FakeComponent] = field(default_factory=dict)
    joints: dict[str, FakeJoint] = field(default_factory=dict)
    datum_planes: dict[str, FakeDatumPlane] = field(default_factory=dict)
    datum_axes: dict[str, FakeDatumAxis] = field(default_factory=dict)
    datum_marks: dict[str, tuple[frozenset[str], frozenset[str]]] = field(default_factory=dict)
    """Feature id -> the (datum plane ids, datum axis ids) that existed when it started: undo forgets the datums made
    afterwards, like the FreeCAD worker's snapshot restore. Kept out of the features so fingerprints do not change."""
    counters: dict[str, int] = field(default_factory=dict)
    deterministic_ids: bool = True
    history: list[HistoryEntry] = field(default_factory=list)
    """Every successful mutating call, in order (``edit_feature`` replays it). Not part of the fingerprint."""
    hand_edited: bool = False
    """True once a test-only hand edit touched the document: it can no longer be reproduced by a replay."""
    material: str | None = None
    route: tuple[str, ...] = ()
    roles: dict[str, str] = field(default_factory=dict)
    title: str | None = None
    """The design attributes (``design_attributes``): kept in the saved file, outside the fingerprint."""

    def next_id(self, kind: str) -> str:
        self.counters[kind] = self.counters.get(kind, 0) + 1
        if self.deterministic_ids:
            return f"{kind}:{self.counters[kind]}"
        return f"{kind}:{uuid4().hex[:8]}"

    def body_of_face(self, face_id: str) -> str:
        return self.faces[face_id].body

    def body_primitives(self, body_id: str) -> list[Primitive]:
        return [self.primitives[p] for p in self.bodies.get(body_id, [])]

    def fingerprint(self) -> str:
        """Canonical string of sorted ids and rounded geometry; counters are excluded."""
        payload = {
            "kind": self.kind.value,
            "features": [_canonical(asdict(f)) for f in self.features],
            "sketches": {k: _canonical(asdict(v)) for k, v in sorted(self.sketches.items())},
            "primitives": {k: _canonical(asdict(v)) for k, v in sorted(self.primitives.items())},
            "faces": {k: _canonical(asdict(v)) for k, v in sorted(self.faces.items())},
            "edges": {k: _canonical(asdict(v)) for k, v in sorted(self.edges.items())},
            "vertices": {k: _canonical(asdict(v)) for k, v in sorted(self.vertices.items())},
            "bodies": {k: sorted(v) for k, v in sorted(self.bodies.items())},
            "components": {
                k: {"part": v.part.fingerprint(), "transform": _canonical(v.transform.model_dump()), "fixed": v.fixed}
                for k, v in sorted(self.components.items())
            },
        }
        if self.joints:
            payload["joints"] = {k: _canonical(asdict(v)) for k, v in sorted(self.joints.items())}
        if self.datum_planes or self.datum_axes:  # absent keys keep the fingerprint of datum-free documents
            payload["datum_planes"] = {k: _canonical(asdict(v)) for k, v in sorted(self.datum_planes.items())}
            payload["datum_axes"] = {k: _canonical(asdict(v)) for k, v in sorted(self.datum_axes.items())}
        return json.dumps(payload, sort_keys=True, default=str)


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, FINGERPRINT_DIGITS) + 0.0
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical(asdict(value))
    if hasattr(value, "model_dump"):
        return _canonical(value.model_dump())
    return value


# --- Sketch degrees of freedom (``sketch_status``) --------------------------------------------------

# Every entity contributes one "position" pair (``u``, ``v``: its centre, or a line's start point) plus one
# slot per size attribute -- exactly the attributes ``NamedDimension.attribute`` can name for that entity
# kind (see ``_named_dimension_values`` above). A circle's ``diameter`` and ``radius`` name the same
# physical unknown (its size), so both resolve to the single ``size`` slot below.
_RECT_SLOTS: frozenset[str] = frozenset({"u", "v", "width", "height"})
_SQUARE_SLOTS: frozenset[str] = frozenset({"u", "v", "side"})
_CIRCLE_SLOTS: frozenset[str] = frozenset({"u", "v", "size"})
_LINE_SLOTS: frozenset[str] = frozenset({"u", "v", "length"})
_ARC_SLOTS: frozenset[str] = frozenset({"u", "v", "radius"})
_SLOT_SLOTS: frozenset[str] = frozenset({"u", "v", "width", "length"})
_CIRCLE_SIZE_ATTRIBUTES: frozenset[str] = frozenset({"diameter", "radius"})


def _entity_slots(entity: SketchEntity) -> frozenset[str]:
    """The full set of DOF slots an entity carries before any dimension or constraint fixes them."""
    if isinstance(entity, RectEntity):
        return _RECT_SLOTS if entity.chamfer_mm is None else _RECT_SLOTS | {"chamfer"}
    if isinstance(entity, SquareEntity):
        return _SQUARE_SLOTS
    if isinstance(entity, CircleEntity):
        return _CIRCLE_SLOTS
    if isinstance(entity, LineEntity):
        return _LINE_SLOTS
    if isinstance(entity, ArcEntity | Arc3Entity | ArcEndsEntity):
        return _ARC_SLOTS
    if isinstance(entity, SlotEntity):
        return _SLOT_SLOTS
    return frozenset()


def _slot_for_attribute(entity: SketchEntity, attribute: str) -> str | None:
    """The slot a ``NamedDimension.attribute`` fixes on ``entity``, or ``None`` when it does not apply."""
    if isinstance(entity, CircleEntity) and attribute in _CIRCLE_SIZE_ATTRIBUTES:
        return "size"
    slots = _entity_slots(entity)
    return attribute if attribute in slots else None


@dataclass(frozen=True)
class SketchDofReport:
    """Deterministic sketch diagnostics computed from what the fake itself stored when the sketch was
    created (``FakeSketch.entities/constraints/named_dimensions``): no solver, arithmetic only
    (used by ``sketch_status``).

    A ``NamedDimension`` fixes the slot it names (see ``_slot_for_attribute``); a
    ``SketchConstraint(kind="coincident", entities=(name, "origin"))`` fixes that entity's position
    (``u``, ``v``). A second dimension or constraint that targets an already-fixed slot is a redundant
    duplicate: it does not reduce ``dof`` further, but it is reported as a ``ConstraintConflict`` and makes
    the sketch ``over_constrained``. Other constraint kinds (``horizontal``, ``vertical``, ``concentric``,
    ``equal``, ``tangent``, ``symmetric``) are counted but do not fix a slot in this deterministic model.
    """

    dof: int
    geometry_count: int
    constraint_count: int
    fully_constrained: bool
    over_constrained: bool
    conflicts: tuple[ConstraintConflict, ...]
    free_geometry: tuple[int, ...]
    solver_message: str


def sketch_dof_report(sketch: FakeSketch) -> SketchDofReport:
    entities = sketch.entities
    by_name: dict[str, list[int]] = {}
    for index, entity in enumerate(entities):
        by_name.setdefault(entity.name, []).append(index)

    fixed: list[set[str]] = [set() for _ in entities]
    conflicts: list[ConstraintConflict] = []
    seen_slots: set[tuple[int, str]] = set()
    constraint_index = 0

    for dimension in sketch.named_dimensions:
        for index in by_name.get(dimension.entity, ()):
            slot = _slot_for_attribute(entities[index], dimension.attribute)
            if slot is None:
                continue
            key = (index, slot)
            if key in seen_slots:
                conflicts.append(
                    ConstraintConflict(
                        kind="redundant",
                        constraint_index=constraint_index,
                        constraint_name=dimension.name,
                        involved=(index,),
                    )
                )
            else:
                seen_slots.add(key)
                fixed[index].add(slot)
        constraint_index += 1

    seen_constraints: set[tuple[str, tuple[str, ...]]] = set()
    for constraint in sketch.constraints:
        key = (constraint.kind, constraint.entities)
        if key in seen_constraints:
            involved = tuple(sorted({idx for name in constraint.entities for idx in by_name.get(name, ())}))
            conflicts.append(
                ConstraintConflict(
                    kind="redundant",
                    constraint_index=constraint_index,
                    constraint_name=constraint.kind,
                    involved=involved,
                )
            )
        else:
            seen_constraints.add(key)
            if constraint.kind == "coincident" and "origin" in constraint.entities:
                for name in constraint.entities:
                    if name == "origin":
                        continue
                    for index in by_name.get(name, ()):
                        fixed[index].update({"u", "v"})
        constraint_index += 1

    free_geometry: list[int] = []
    dof = 0
    for index, entity in enumerate(entities):
        remaining = _entity_slots(entity) - fixed[index]
        if remaining:
            free_geometry.append(index)
            dof += len(remaining)

    over_constrained = bool(conflicts)
    solver_message = "" if not conflicts else f"{len(conflicts)} redundant constraint(s) detected"

    return SketchDofReport(
        dof=dof,
        geometry_count=len(entities),
        constraint_count=len(sketch.constraints) + len(sketch.named_dimensions),
        fully_constrained=dof == 0 and not over_constrained,
        over_constrained=over_constrained,
        conflicts=tuple(conflicts),
        free_geometry=tuple(free_geometry),
        solver_message=solver_message,
    )
