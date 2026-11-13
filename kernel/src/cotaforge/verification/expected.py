"""ExpectedComputer: analytic Level 2 expectations per IR op.

The computer is stateful per plan: as ``compute`` is called in plan order it remembers sketch
models (entities + frame), the analytic shape of concept bodies and the volumes of inserted parts,
because later ops only reference those by op id. ``adapter`` is used for read queries only.
"""

import math
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from cotaforge.contract import (
    Adapter,
    AdapterError,
    Axis,
    BodyHandle,
    BoundingBox,
    ComponentRequest,
    CurveType,
    DatumPlaneTarget,
    EdgeInfo,
    EdgeRequest,
    EdgesInfoRequest,
    Empty,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FacesInfoRequest,
    FeatureHandle,
    FeatureRequest,
    HoleTool,
    JointRequest,
    MassRequest,
    PlaneName,
    Profile,
    ProvenanceRole,
    Request,
    SetActiveDocumentRequest,
    Side,
    SketchFrame,
    SurfaceType,
    ToolOverlapRequest,
    ToolOverlapResult,
    Transform,
    Vec3,
    canonical_axis_direction,
    frame_for_face,
    frame_for_plane,
    holetool,
    joint_delta,
    net_area,
    profile_depths,
    radial_entry,
    sketch_profiles,
)
from cotaforge.contract.threads import TRANSCRIPTION_NOTE, thread
from cotaforge.verification import formulas as f
from cotaforge.verification import sheetmetal
from cotaforge.verification import sweep as sweep_math
from cotaforge.verification.formulas import BoxShape, CylinderShape, Point2, Shape
from cotaforge.verification.level2 import CUT_AREA, CUT_DIRECTION, DRAFT_PULL
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind, Expectation, Unit, Value
from cotaforge.verification.thresholds import Thresholds

if TYPE_CHECKING:
    from cotaforge.ir.ops_primitives import OperationBase


class SketchModel(BaseModel, frozen=True):
    frame: SketchFrame
    profiles: tuple[Profile, ...]
    unsupported: str = ""
    on_face: bool = False
    """True when the sketch was placed on an existing planar face (``FaceRef``), not a datum ``PlaneName``."""
    on_datum: bool = False
    """True when the sketch was placed on a datum plane (``ref_plane``): it may still happen to be coplanar with a
    face of the body it extrudes into, which no request field says (see ``_op_extrude``)."""
    host_face: FaceHandle | None = None
    """The face the sketch was placed on, set only when ``on_face``; used to query the host face's own
    edge count for the ``covers`` coverage class's ``modified_faces_count`` (see ``_op_extrude``)."""
    host_face_bbox_uv: tuple[Point2, Point2] | None = None
    """The host face's own footprint in this sketch's frame (``u``, ``v``), set only when ``on_face`` and the
    host face is planar; used to classify an on-face extrude's coverage (see ``_classify_coverage``)."""


class ExtrudeOverhangsHostFaceError(ValueError):
    """An on-face, merged extrude's profile partially overlaps its host face — neither fully inside it
    nor fully covering it. Out of scope; the executor turns this into a
    ``plan_error`` because it is raised while computing expectations, before the adapter is called."""


class ExtrudeIntoHostFaceError(ValueError):
    """An on-face, merged extrude whose sweep is not purely in the face's outward (positive, single)
    direction would extrude into the host body from its own face, which is not supported (use a
    ``cut`` instead). Raised while computing expectations, before the adapter is called."""


class HoleLeavesFaceError(ValueError):
    """A radial hole whose circle provably crosses the axial end of its cylindrical face (a face along a world axis has
    an exact box, so its axial extent is known): PartDesign would silently cut a notch from the edge. Raised while
    computing expectations, before the adapter is called; the FreeCAD worker refuses the same case (and the angular
    edges of a trimmed face, which the face descriptor cannot prove) with its own message."""


class ExtrudeCoverageNotSupportedError(ValueError):
    """The ``covers`` coverage class is only supported for a rectangular profile congruent with (or
    containing) a rectangular host face. Any other profile kind (circle,
    polygon, multiple profiles) that would classify as ``covers`` is out of scope. Raised while computing
    expectations, before the adapter is ever called, so the fake and FreeCAD worker stay in parity by
    construction — neither is ever asked to build this case."""


class ShellThicknessError(ValueError):
    """A ``shell`` wall that cannot fit the primitive it hollows (the cavity would have no size). The geometry is known
    exactly there, so the plan is refused while the expectations are computed, before the adapter is called: the kernel
    would otherwise leave the shape unchanged or return an invalid one and still report success."""


class DraftAngleError(ValueError):
    """A ``draft`` whose angle collapses the primitive it tapers (a wall narrows to nothing before the far end of the
    body). The geometry is known exactly there, so the plan is refused while the expectations are computed, before the
    adapter is called: the kernel would otherwise return an invalid shape."""


def _exp(name: str, unit: Unit, value: Value, *, asserted: bool = True, note: str = "") -> Expectation:
    kind = CheckKind.asserted if asserted else CheckKind.reported
    return Expectation(name=name, kind=kind, unit=unit, value=value, note=note)


def _is_empty(before: StateSnapshot) -> bool:
    return before.mass.volume_mm3 == 0.0


def _union_with_before(box: BoundingBox, before: StateSnapshot) -> BoundingBox:
    return box if _is_empty(before) else f.bbox_union(before.bbox, box)


def _boxes_touch(a: BoundingBox, b: BoundingBox, tol: float) -> bool:
    """Whether two boxes overlap or merely touch (within ``tol``), unlike ``f.bboxes_overlap`` which is strict."""
    return all(a.min[i] <= b.max[i] + tol and a.max[i] >= b.min[i] - tol for i in range(3))


def _profile_sides(profile: Profile) -> int:
    if profile.kind == "rect":
        return 4
    if profile.kind == "circle":
        return 1
    if profile.sides is not None:
        return profile.sides
    return len(profile.points or ())


_ARC_FACES_NOTE = "profile with arcs: how many faces a curved wall is split into depends on the CAD's kernel"


def _curved(profiles: Sequence[Profile]) -> bool:
    return any(p.sides is not None for p in profiles)


def _profile_world_points(profile: Profile, frame: SketchFrame, axis: Axis | None = None) -> list[Vec3]:
    if profile.kind == "polygon" and profile.points:
        return [frame.to_world(u, v) for u, v in profile.points]
    if profile.kind == "rect":
        (u0, v0), (u1, v1) = profile.bbox_uv
        return [frame.to_world(u, v) for u in (u0, u1) for v in (v0, v1)]
    centre = frame.to_world(*profile.centroid)
    radius = profile.radius or 0.0
    if axis is None:
        return [f.add(centre, f.scale(d, s * radius)) for d in (frame.u, frame.v) for s in (-1.0, 1.0)]
    perp = f.normalise(f.cross(frame.n, axis.direction))
    return [f.add(centre, f.scale(d, s * radius)) for d in (perp, axis.direction) for s in (-1.0, 1.0)]


def _extent_from_plane(box: BoundingBox, origin: Vec3, direction: Vec3) -> float:
    """Length of ``box`` that lies ahead of the plane through ``origin`` along ``direction``: from the plane (or from
    the near side of the box when the plane lies outside it) to the far side. ``box`` is the box of the body that is
    cut, not of the whole document."""
    heights = [f.dot(f.sub(c, origin), direction) for c in f.bbox_corners(box)]
    return max(0.0, max(heights) - max(0.0, min(heights)))


def _face_footprint_uv(face: FaceInfo, frame: SketchFrame) -> tuple[Point2, Point2]:
    """The face's own world-space bounding box, projected onto the sketch frame's (``u``, ``v``) axes
    relative to ``frame.origin`` (the face centre). Exact for an axis-aligned rectangular planar face
    (the only tested shape); a conservative approximation otherwise."""
    us: list[float] = []
    vs: list[float] = []
    for corner in f.bbox_corners(face.bbox):
        rel = f.sub(corner, frame.origin)
        us.append(f.dot(rel, frame.u))
        vs.append(f.dot(rel, frame.v))
    return (min(us), min(vs)), (max(us), max(vs))


def _classify_coverage(profile: Profile, host_bbox_uv: tuple[Point2, Point2], tol: float) -> str:
    """Classify an on-face extrude profile against its host face's footprint:

    - ``inside``: the profile lies strictly within the host face (the FreeCAD-validated rule, e.g. a boss on a
      plate): the host face is only *modified* (shrunk).
    - ``covers``: the profile's footprint equals or contains the host face's footprint (e.g.
      a two-tier block): the host face is entirely consumed (removed on FreeCAD,
      stale handle) and only its adjacent side faces are modified (extended).
    - ``flush``: the profile lies within the host face including its boundary (within ``tol``) — it touches one or
      more face edges without exceeding them, e.g. a wall drawn flush with the plate's edge. The host face is
      modified like ``inside``, but side faces of the new prism may be coplanar with the body's and merge into them.
    - ``overhangs``: neither of the above — the profile extends beyond the host face without covering
      it. Out of scope (``ExtrudeOverhangsHostFaceError``).

    For a circle profile, ``covers`` requires the host face's corners to be inscribed in the circle
    (bounding-box containment alone is not sufficient for a circle).
    """
    (p_u0, p_v0), (p_u1, p_v1) = profile.bbox_uv
    (h_u0, h_v0), (h_u1, h_v1) = host_bbox_uv
    if p_u0 > h_u0 + tol and p_v0 > h_v0 + tol and p_u1 < h_u1 - tol and p_v1 < h_v1 - tol:
        return "inside"
    if profile.kind == "circle" and profile.center is not None and profile.radius is not None:
        cu, cv = profile.center
        radius = profile.radius
        corners = ((h_u0, h_v0), (h_u0, h_v1), (h_u1, h_v0), (h_u1, h_v1))
        covers = all(math.hypot(cu - hu, cv - hv) <= radius + tol for hu, hv in corners)
    else:
        covers = p_u0 <= h_u0 + tol and p_v0 <= h_v0 + tol and p_u1 >= h_u1 - tol and p_v1 >= h_v1 - tol
    if covers:
        return "covers"
    within = p_u0 >= h_u0 - tol and p_v0 >= h_v0 - tol and p_u1 <= h_u1 + tol and p_v1 <= h_v1 + tol
    return "flush" if within else "overhangs"


_NON_EDITING_OPS = frozenset(
    {
        # primitives, booleans and rigid moves build, combine or place whole bodies (the booleans and moves keep
        # the tracked shapes consistent in their own rows)
        "box",
        "cylinder",
        "boolean_union",
        "boolean_subtract",
        "boolean_intersect",
        "translate",
        "rotate",
        "copy_body",
        "mirror_body",
        # no geometry at all
        "sketch",
        "ref_plane",
        "ref_axis",
        # the flat pattern lives outside the solid: every body stays as it was
        "unfold",
        # assembly ops move or check components, never a part body
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
"""The ONLY ops that leave every tracked body alone. Every other op (extrude, cut, revolve, hole, patterns, chamfer,
fillet, and whatever the vocabulary grows next) is recorded as a body edit, with or without a row: over-recording only
downgrades an asserted boolean check to a reported one."""

_POSE_CHANGING_OPS = frozenset({"translate", "rotate", "copy_body", "mirror_body"})
"""Ops after which the poses recorded for the host's outline and features (``_pad_host``, ``_feature_regions``) can no
longer be trusted."""

_ADDITIVE_OPS = frozenset({"extrude", "revolve", "sweep"})
"""Edits that ADD material wherever their region is (a boss the adapter merges into some body). A new additive op (a
sweep) must be listed here to get ``_merged_into`` (features on its faces then edit the body it merged into) and the
record-on-every-tracked-body rule when it names no host."""

HOLE_EDGE_CLEARANCE_MM = 0.05
"""A radial hole keeps this far from the boundary of its face (the FreeCAD worker enforces the same number): a circle
tangent to an edge makes a degenerate edge in the kernel that fails later in observation."""

_MERGE_SHORTFALL_NOTE = (
    "a merged extrude adds area x depth only where its profile is clear of the body: if less was added, the profile "
    "overlaps existing material (a rib or boss pushed into a wall or floor) - redraw it flush against the surface it "
    "meets, touching but never crossing it"
)

_LOOSE_BOX_NOTE = (
    "hole axis off the world axes: the kernel's bounding box of a cylindrical wall is loose, so the box of the whole "
    "body can grow past the one before the hole"
)

_EXTENT_RANGE_NOTE = (
    "the walls of a through cut run from the sketch plane through the material under the profile: no more than the "
    "far side of the body, no less than the material it removed over its section. The body can be taller than that "
    "material (a thin flange under a hub), so the height of the part is only the upper bound"
)


def _union_boxes(boxes: Sequence[BoundingBox]) -> BoundingBox:
    box = boxes[0]
    for other in boxes[1:]:
        box = f.bbox_union(box, other)
    return box


def _body_ref_id(ref: Any) -> str:
    """The id a body ref is registered under in ``_body_of_op``: the op it names, except for the negative piece of a
    split that kept both, which lives under ``<split id>#negative`` (the positive piece, and the one piece of a split
    that kept one, are registered under the split's own id)."""
    return f"{ref.from_op}#negative" if getattr(ref, "side", None) == "negative" else ref.from_op


def _copy_state(value: Any) -> Any:
    """Copy the containers of the computer's tracking state; the models and tuples inside are immutable."""
    if isinstance(value, dict):
        return {key: _copy_state(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy_state(item) for item in value]
    if isinstance(value, set):
        return set(value)
    return value


def _meets(a: BoundingBox, b: BoundingBox, tol: float) -> bool:
    """Whether two boxes share a point (touching counts, within ``tol``)."""
    return all(a.min[i] <= b.max[i] + tol and b.min[i] <= a.max[i] + tol for i in range(3))


def forget_geometry_in(state: dict[str, Any]) -> None:
    """``ExpectedComputer.forget_geometry`` on a plain state dict (also the copies kept for ``undo_to``)."""
    shapes = state.get("_shape_by_body")
    if isinstance(shapes, dict):
        for key in shapes:
            shapes[key] = None
    if "_pad_host" in state:
        state["_pad_host"] = None
    regions = state.get("_feature_regions")
    if isinstance(regions, dict):
        for key in regions:
            regions[key] = None


class ExpectedComputer:
    def __init__(self, thresholds: Thresholds) -> None:
        self.thresholds = thresholds
        self.reset()

    def reset(self) -> None:
        self._sketches: dict[str, SketchModel] = {}
        self._body_of_op: dict[str, str] = {}
        self._shape_by_body: dict[str, Shape | None] = {}
        self._part_volumes: dict[str, float] = {}
        self._sketch_host: dict[str, str | None] = {}
        """Sketch op id -> tracked body key of the face it was drawn on (``None``: a plane or an untracked body)."""
        self._edits: dict[str, list[BoundingBox]] = {}
        """Tracked body key -> boxes (world) where feature ops (hole, cut, merged extrude) edited the body: the
        primitive shape stays valid everywhere else, so a boolean whose overlap misses every box is still analytic."""
        self._feature_keys: dict[str, set[str]] = {}
        """Feature op id -> tracked body keys it edited, so a pattern of it edits the same bodies."""
        self._additive_features: set[str] = set()
        """Ids of the additive feature ops (bosses) seen so far: a pattern of one forgets every tracked body."""
        self._consumed: set[str] = set()
        """Body keys a boolean consumed (a tool that did not survive): no longer live bodies of the document."""
        self._merged_into: dict[str, set[str]] = {}
        """Merged extrude/revolve op id -> the tracked bodies it was merged into (its faces belong to them)."""
        self._split_pieces: dict[str, tuple[str, ...]] = {}
        """Split op id -> the body keys of the pieces it made: a face ref to the split may be on either of them."""
        self._region: BoundingBox | None = None
        """Set by the op being computed: the box its edit covers (``None``: unknown)."""
        self._feature_regions: dict[str, BoundingBox | None] = {}
        """Editing op id -> the box its edit covers (``None``: unknown), for EVERY editing op after the base pad or
        revolve, tracked body or not: a PartDesign host records no ``_edits``, yet an instance of a pattern or a mirror
        that lands on one of these features changes the volume less (or more) than its seed did."""
        self._pad_host: tuple[SketchFrame, Profile] | None = None
        """The frame and the single closed profile of the base pad (the first extrude on an empty document): the
        outline of the material, so a cut instance can be told to land on it or in a void."""
        self._sheets: dict[str, sheetmetal.Sheet] = {}
        """Base flange op id -> what is exactly known of that sheet (``verification.sheetmetal``)."""
        self._sheet_of_op: dict[str, str] = {}
        """Sheet-metal op id -> the sheet (base flange op id) it works on, so a face of a flange finds its sheet."""

    # --- public --------------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """A copy of everything ``compute`` has learned so far, to hand back to ``restore``: ``compute`` records an op
        BEFORE the adapter runs it, so an op that then fails (or is undone) must be able to take its record back."""
        return {name: _copy_state(value) for name, value in vars(self).items() if name != "thresholds"}

    def restore(self, state: dict[str, Any]) -> None:
        for name, value in state.items():
            setattr(self, name, _copy_state(value))

    def forget_geometry(self) -> None:
        """After an in-place edit the analytic shapes tracked for the bodies describe the document as it WAS: drop them
        (and the regions measured on it), so a later boolean is checked against what the adapter reports instead of a
        stale model. Sound by the rule of ``_record_edit``: forgetting only downgrades an asserted check to a
        reported one."""
        forget_geometry_in(vars(self))

    def compute(
        self, op: "OperationBase", request: Request, before: StateSnapshot, adapter: Adapter
    ) -> tuple[Expectation, ...]:
        method = getattr(self, f"_op_{op.op}", None)
        self._region = None
        expectations = () if method is None else tuple(method(op, request, before, adapter))
        # Every op outside ``_NON_EDITING_OPS`` is a body edit, handler or not (never fail open on a new vocabulary
        # entry). A new additive op sets ``self._region`` (a sweep: the path box inflated by the profile's largest
        # radius) in its own ``_op_<name>`` and joins ``_ADDITIVE_OPS``; without a region the bodies it names, or all
        # tracked bodies, are forgotten. An extrude or sweep that does not merge builds a separate body.
        separate = op.op == "base_flange" or (op.op in ("extrude", "sweep") and not getattr(op, "merge", True))
        if op.op not in _NON_EDITING_OPS and not separate:
            self._record_edit(op)
        if op.op not in _NON_EDITING_OPS:
            if op.op in ("extrude", "revolve") and _is_empty(before):
                self._remember_pad_host(op)  # the base is the host itself, not a feature standing on it
            else:
                self._feature_regions[op.id] = self._region
        if op.op in _POSE_CHANGING_OPS:
            # the outline and the regions were measured where the host WAS: after it moved (or a copy or a reflection
            # of it appeared) none can be located soundly, and "unknown" makes every instance suspect
            self._pad_host = None
            self._feature_regions = dict.fromkeys(self._feature_regions)
        return expectations

    def _remember_pad_host(self, op: Any) -> None:
        if op.op != "extrude":
            return
        model = self._sketch_model(op.sketch)
        if model is not None and len(model.profiles) == 1:
            self._pad_host = (model.frame, model.profiles[0])

    def _tracked_keys(self) -> set[str]:
        return {key for key, shape in self._shape_by_body.items() if shape is not None}

    def _expand(self, keys: set[str]) -> set[str]:
        """``keys`` plus, transitively, every body a merged extrude/revolve in them was merged into: a feature on a
        boss face edits the body the boss belongs to."""
        seen: set[str] = set()
        todo = list(keys)
        while todo:
            key = todo.pop()
            if key not in seen:
                seen.add(key)
                todo.extend(self._merged_into.get(key, ()))
        return seen

    def _explicit_keys(self, op: Any) -> set[str] | None:
        """Bodies the op names itself: a pattern's seed features, its face/edge/body refs, the face its sketch was
        drawn on (``None``: a pattern of a feature that recorded nothing)."""
        features = getattr(op, "features", None)
        if features is not None:
            if any(feature in self._additive_features for feature in features):
                return None  # the instances of a boss land wherever the adapter merges them: any tracked body
            found = [self._feature_keys.get(feature) for feature in features]
            return None if any(keys is None for keys in found) else set().union(*found)  # type: ignore[arg-type]
        keys: set[str] = set()
        refs = op.refs() if hasattr(op, "refs") else ()
        for ref in refs:
            from_op = getattr(ref, "from_op", None)
            if from_op is None:
                continue
            body_id = _body_ref_id(ref)
            if body_id in self._body_of_op:
                keys.add(self._body_of_op[body_id])
            if getattr(ref, "kind", None) != "body":
                # a face, edge or vertex of a split lies on one of its pieces and nothing says which: record them all
                keys.update(self._split_pieces.get(from_op, ()))
        host = self._sketch_host.get(getattr(op, "sketch", ""))
        if host is not None:
            keys.add(host)
        return keys

    def _record_edit(self, op: Any) -> None:
        """A feature op edits bodies, so the analytic primitive shape tracked for them (a box, a cylinder, a moved copy
        of one) no longer describes them everywhere. The edited bodies are the ones the op names (through merged
        bosses, transitively) PLUS every tracked body its region meets -- a cut or a merged extrude acts on whatever
        lies in its path -- and, for an additive op with no named host, every tracked body (the adapter merges it into
        whichever body it picks). With a known region the shape is kept and the region remembered (a boolean whose
        overlap misses it is still exact, see ``_overlap``); otherwise the shape is forgotten. When in doubt record:
        over-recording only downgrades an asserted check to a reported one, under-recording asserts a wrong value."""
        region = self._region
        explicit = self._explicit_keys(op)
        tracked = self._tracked_keys()
        if explicit is None:
            touched = set(tracked)
        else:
            touched = set(explicit)
            if region is None:
                touched = touched or set(tracked)
            else:
                tol = self.thresholds.position_mm
                touched |= {k for k in tracked if _meets(f.shape_bbox(self._shape_by_body[k]), region, tol)}  # type: ignore[arg-type]
                if op.op in _ADDITIVE_OPS and not explicit:
                    touched |= tracked
        touched = self._expand(touched)
        self._feature_keys[op.id] = set(touched)
        if op.op in _ADDITIVE_OPS:
            self._additive_features.add(op.id)
        if op.op in _ADDITIVE_OPS and getattr(op, "merge", True):
            self._merged_into[op.id] = set(touched)
        for key in touched:
            if region is None:
                self._shape_by_body[key] = None
            else:
                self._edits.setdefault(key, []).append(region)

    def _live_body_keys(self) -> set[str]:
        return {k for k in self._body_of_op.values() if k not in self._consumed and not self._merged_into.get(k)}

    def _live_body_count(self) -> int:
        """Bodies the document has, as far as the plan tells: created bodies minus consumed tools and bosses merged
        into another body. An over-count is safe (it only makes the host box 'unknown', downgrading to reported); it
        cannot see bodies the plan did not build (``StateSnapshot.untracked_bodies``): ``_alone`` asks for that too."""
        return len(self._live_body_keys())

    def _alone(self, before: StateSnapshot) -> bool:
        """Whether the plan's one live body is surely the only body of the document."""
        return self._live_body_count() <= 1 and not before.untracked_bodies

    def _host_box(
        self, op: Any, body: BodyHandle | None, before: StateSnapshot, adapter: Adapter
    ) -> BoundingBox | None:
        """The box of the body an op cuts: the named body's own, else the document's when it has a single body, else
        ``None`` (several bodies and none named: asserting an extent against the whole document would be wrong)."""
        if body is not None:
            try:
                return self._body_bbox(op, body, adapter)
            except AdapterError:
                pass
        return before.bbox if self._alone(before) else None

    def _reach(self, before: StateSnapshot) -> float:
        """A length that spans the whole body (a through-all edit): the diagonal of the document box."""
        return f.length(f.sub(before.bbox.max, before.bbox.min))

    def _through_span(self, before: StateSnapshot, frame: SketchFrame) -> tuple[float, float]:
        """Offsets along the sketch normal, from its plane, that span the whole document box (either direction), so a
        through-all region reaches the bodies even when the sketch plane lies far from them."""
        heights = [f.dot(f.sub(c, frame.origin), frame.n) for c in f.bbox_corners(before.bbox)]
        tol = self.thresholds.position_mm
        return min(0.0, min(heights)) - tol, max(0.0, max(heights)) + tol

    def _extent_box(self, op_id: str) -> BoundingBox | None:
        """A tracked body's primitive box grown by every recorded edit: where the body may really have material."""
        shape = self._shape_of(op_id)
        if shape is None:
            return None
        box = f.shape_bbox(shape)
        for region in self._edits.get(self._body_of_op.get(op_id, ""), ()):
            box = f.bbox_union(box, region)
        return box

    def _tools_disjoint(self, tool_ops: list[str]) -> bool:
        """Whether no two tools can overlap each other (the pairwise overlaps with the target then add up)."""
        boxes = [self._extent_box(t) for t in tool_ops]
        if len(tool_ops) < 2:
            return True
        return all(
            a is not None and b is not None and not f.bboxes_overlap(a, b)
            for i, a in enumerate(boxes)
            for b in boxes[i + 1 :]
        )

    def _overlap(self, target_op: str, tool_op: str) -> float | None:
        """Intersection volume of two tracked primitives, unless a feature edit touches the shared region."""
        target_key, tool_key = self._body_of_op.get(target_op), self._body_of_op.get(tool_op)
        target, tool = self._shape_of(target_op), self._shape_of(tool_op)
        if target is None or tool is None:
            return None
        tol = self.thresholds.position_mm
        target_box, tool_box = f.shape_bbox(target), f.shape_bbox(tool)
        target_edits, tool_edits = self._edits.get(target_key or "", []), self._edits.get(tool_key or "", [])
        # an edit matters only where it meets the other body: a hole's walls inside the overlap, or a boss that
        # grows into the other body (or into its own edits); elsewhere the primitive overlap is still exact
        if (
            any(_meets(region, tool_box, tol) for region in target_edits)
            or any(_meets(region, target_box, tol) for region in tool_edits)
            or any(_meets(a, b, tol) for a in target_edits for b in tool_edits)
        ):
            return None
        return f.intersection_volume(target, tool)

    # --- sketch / extrude / cut / revolve ------------------------------------------------------

    def _op_sketch(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        on_face = isinstance(request.on, FaceHandle)
        host_face: FaceHandle | None = None
        host_face_bbox_uv: tuple[Point2, Point2] | None = None
        if isinstance(request.on, PlaneName):
            frame = frame_for_plane(request.on)
        elif isinstance(request.on, DatumPlaneTarget):
            frame = request.on.frame  # the datum carries its own frame: not a face, no host to merge into
        else:
            face_info = adapter.face_info(FaceRequest(operation_id=op.id, face=request.on))
            frame = frame_for_face(face_info, request.origin)
            host_face_bbox_uv = _face_footprint_uv(face_info, frame)
            host_face = request.on
        try:
            profiles = tuple(sketch_profiles(op.entities))
            unsupported = ""
        except ValueError as exc:
            profiles = ()
            unsupported = str(exc)
        self._sketch_host[op.id] = self._body_of_op.get(getattr(op.on, "from_op", None) or "") if on_face else None
        self._sketches[op.id] = SketchModel(
            frame=frame,
            profiles=profiles,
            unsupported=unsupported,
            on_face=on_face,
            on_datum=isinstance(request.on, DatumPlaneTarget),
            host_face=host_face,
            host_face_bbox_uv=host_face_bbox_uv,
        )
        return [_exp("volume_delta", "mm3", 0.0, asserted=False, note="a sketch never changes the volume")]

    # --- datum planes and axes ---------------------------------------------------------------------

    def _op_ref_plane(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A datum builds no geometry (volume unchanged) and must be realised at exactly the pose the
        executor computed: origin, ``u`` and normal of the requested frame.

        These pose checks guard ADAPTER DRIFT only (the adapter reproducing what it was asked for); they say
        nothing about whether the executor computed the right pose. The independent oracle is downstream: the
        volume, bounding box and face info of whatever is sketched and built on the datum."""
        frame = request.frame
        return [
            _exp("volume_delta", "mm3", 0.0, note="a datum plane never changes the volume"),
            _exp("datum_origin", "vec", frame.origin),
            _exp("datum_u", "vec", frame.u),
            _exp("datum_normal", "vec", frame.n),
        ]

    def _op_ref_axis(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """Same contract as ``_op_ref_plane``: adapter-drift guard only, the oracle is what is built on the axis."""
        axis = request.axis
        return [
            _exp("volume_delta", "mm3", 0.0, note="a datum axis never changes the volume"),
            _exp("datum_axis_position", "vec", axis.origin),
            _exp("datum_axis_direction", "vec", axis.direction),
        ]

    def _sketch_model(self, sketch_op_id: str) -> SketchModel | None:
        model = self._sketches.get(sketch_op_id)
        if model is None or model.unsupported:
            return None
        return model

    def _sweep_range(self, op: Any) -> tuple[float, float]:
        primary = 1.0 if op.direction.value == "positive" else -1.0
        depth = float(op.depth_mm or 0.0)
        depth2 = float(op.depth2_mm) if op.depth2_mm is not None else depth
        lo, hi = min(0.0, primary * depth), max(0.0, primary * depth)
        if op.both_directions:
            lo, hi = min(lo, -primary * depth2), max(hi, -primary * depth2)
        return lo, hi

    def _op_extrude(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """``new_faces_count`` rule (validated on real FreeCAD):

        An extrude normally caps both ends of the sweep with a new planar face (``2 + n_side_faces``).
        But when the sketch lies ON an existing planar face of the body it joins (``op.merge`` and the
        sketch was placed on a ``FaceRef``), the near cap does not become a new face. The profile is classified against
        the host face's own footprint in three coverage classes (see ``_classify_coverage``):

        - ``inside`` (the profile is strictly within the host face): the near cap merges into the host
          face, whose surface is only *modified* (its area shrinks by the profile area — see
          ``adapters/fake/adapter.py::extrude`` and ``modify_face_area``). Only the far cap is new, so
          the count is ``1 + n_side_faces``.
        - ``flush`` (the profile lies within the host face but touches its boundary): handled like ``inside``, except
          that ``new_faces_count`` is reported, not asserted (side faces coplanar with the body's merge into them).
        - ``covers`` (the profile's footprint equals or contains the host face's): supported only
          for a rectangular profile covering a rectangular host face — any other profile kind (circle,
          polygon, multiple profiles) raises ``ExtrudeCoverageNotSupportedError``. The host face is
          entirely consumed — it is *removed*, not modified (a stale handle on both FreeCAD and the
          fake), and its adjacent side faces are *modified* (extended) instead of new side faces being
          created. ``new_faces_count`` is ``1`` (the far cap only); ``modified_faces_count`` is the host
          face's own edge count (``adapter.edges_of`` on the host face), asserted separately.
        - ``overhangs`` (neither of the above): out of scope, raises ``ExtrudeOverhangsHostFaceError``
          (converted to a ``plan_error`` by the executor before the adapter is ever called, since
          expectations are computed first — ``executor/executor.py::next_operation``).

        A merge extrude whose sweep is not purely in the face's own positive direction (``direction``
        other than ``positive``, or ``both_directions``) would extrude into the host body from its own
        face; this is not supported either and raises ``ExtrudeIntoHostFaceError`` the same way.
        """
        model = self._sketch_model(op.sketch)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = None
        if model is None:
            return [_exp("volume_delta", "mm3", 0.0, asserted=False, note="sketch model unknown or unsupported")]
        area = net_area(model.profiles)  # a loop inside a loop is a hole in the boss (even-odd), not more material
        volume = f.extrude_volume(area, op.depth_mm, op.depth2_mm, op.both_directions)
        lo, hi = self._sweep_range(op)
        boxes = [f.swept_profile_shape_bbox(model.frame, p, lo, hi) for p in model.profiles]
        box = _union_boxes(boxes)
        self._region = box
        overlap_note = ""
        overlapped = False
        if op.merge and not _is_empty(before):
            # what a merged boss ADDS is its tool minus the part of it already in material: one boolean with the
            # previous solid, not area x depth (a boss pushed into a wall adds less, and is still the boss asked for)
            probed = self._tool_overlap(adapter, op, mode="add", sketch=request.sketch, lo_mm=lo, hi_mm=hi)
            if probed is not None:
                volume = probed.tool_volume_mm3 - probed.common_volume_mm3
                overlapped = probed.common_volume_mm3 > self.thresholds.abs_mm3
                overlap_note = (
                    f"from the real geometry: the new solid ({probed.tool_volume_mm3:.6g} mm3) minus "
                    f"{probed.common_volume_mm3:.6g} mm3 of it that is already material (one boolean with the "
                    "previous solid)"
                )

        merge_on_face = op.merge and model.on_face
        coverage: str | None = None
        if merge_on_face:
            if op.direction.value != "positive" or op.both_directions:
                msg = "extruding into the host body from its own face is not supported; use a cut"
                raise ExtrudeIntoHostFaceError(msg)
            if model.host_face_bbox_uv is not None and len(model.profiles) == 1:
                coverage = _classify_coverage(model.profiles[0], model.host_face_bbox_uv, self.thresholds.position_mm)
                if coverage == "overhangs":
                    msg = "extrude profile overhangs the host face; not supported"
                    raise ExtrudeOverhangsHostFaceError(msg)
                if coverage == "covers" and model.profiles[0].kind != "rect":
                    msg = "only a rectangular profile may cover a rectangular host face"
                    raise ExtrudeCoverageNotSupportedError(msg)

        if coverage == "covers":
            assert model.host_face is not None
            side_count = len(adapter.edges_of(FaceRequest(operation_id=op.id, face=model.host_face)).edges)
            return [
                _exp("volume_delta", "mm3", volume),
                _exp("bbox_after", "bbox", _union_with_before(box, before)),
                _exp("new_faces_count", "count", 1),
                _exp("modified_faces_count", "count", side_count),
            ]

        caps = 1 if merge_on_face else 2
        depths = profile_depths(model.profiles)
        regions = sum(1 for depth in depths if depth % 2 == 0)  # a ring is ONE region
        faces = caps * regions + sum(_profile_sides(p) for p in model.profiles)
        if merge_on_face:
            # the host face is cut in two by every loop that is a hole in the boss: the piece inside it is a new face
            faces += sum(1 for depth in depths if depth % 2 == 1)
        # PartDesign refines the fused solid: a face of the new prism coplanar with a face of the body it touches
        # merges into it, so the isolated-prism count is only an upper bound. A sketch on a face keeps its validated
        # rule (profile strictly inside the host face); a sketch on a datum or a base plane may sit flush with
        # existing faces whenever the swept prism reaches the body.
        if model.on_datum:
            maybe_coplanar = op.merge and self._sweep_touches_body(model.frame, before, lo, hi)
        elif coverage == "flush":
            maybe_coplanar = True  # profile touches the host face boundary: side faces may merge with the body's
        else:
            maybe_coplanar = (
                op.merge
                and not model.on_face
                and not _is_empty(before)
                and _boxes_touch(box, before.bbox, self.thresholds.position_mm)
            )
        joins_a_body = op.merge and not _is_empty(before)
        maybe_coplanar = maybe_coplanar or overlapped
        return [
            _exp(
                "volume_delta",
                "mm3",
                volume,
                note=overlap_note or (_MERGE_SHORTFALL_NOTE if joins_a_body else ""),
            ),
            _exp("bbox_after", "bbox", _union_with_before(box, before)),
            _exp(
                "new_faces_count",
                "count",
                faces,
                asserted=not maybe_coplanar and not _curved(model.profiles),
                note=(
                    "the new solid reaches the body: faces coplanar with existing ones merge into them, so the count "
                    "can be lower (the datum may be coplanar with a body face)"
                    if maybe_coplanar
                    else (_ARC_FACES_NOTE if _curved(model.profiles) else "")
                ),
            ),
        ]

    def _sweep_touches_body(self, frame: SketchFrame, before: StateSnapshot, lo: float, hi: float) -> bool:
        """Whether the swept interval ``[lo, hi]`` along the datum normal meets the extent of the body along that
        normal (plus or minus the position tolerance): only then can either cap be coplanar with, or lie inside,
        a face of the body and merge into it (the near cap on the datum, or the far cap where the sweep ends)."""
        if _is_empty(before):
            return False
        offset = f.dot(frame.origin, frame.n)
        heights = [f.dot(corner, frame.n) for corner in f.bbox_corners(before.bbox)]
        tol = self.thresholds.position_mm
        return offset + hi >= min(heights) - tol and offset + lo <= max(heights) + tol

    def _op_cut(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        model = self._sketch_model(op.sketch)
        if model is None:
            return [_exp("volume_delta", "mm3", 0.0, asserted=False, note="sketch model unknown or unsupported")]
        area = net_area(model.profiles)  # a loop inside a loop is an island the cut leaves standing (even-odd)
        frame = model.frame
        primary = 1.0 if op.direction.value == "positive" else -1.0
        out: list[Expectation] = []
        through = op.end_condition.value != "blind"
        lo_r, hi_r = self._through_span(before, frame) if through else self._sweep_range(op)
        self._region = _union_boxes([f.swept_profile_shape_bbox(frame, p, lo_r, hi_r) for p in model.profiles])
        host_body = None
        if model.host_face is not None:
            host_body = adapter.face_info(FaceRequest(operation_id=op.id, face=model.host_face)).body
        host_box = self._host_box(op, host_body, before, adapter)  # None: several bodies and none named
        known = host_box is not None
        body_box = host_box if host_box is not None else before.bbox
        host_note = "" if known else "several bodies and the cut names none: the body extent is unknown"
        # what a cut removes is the part of its tool that lies in material: one boolean with the previous solid
        if not through:
            lo_t, hi_t = lo_r, hi_r
        elif op.both_directions:
            lo_t, hi_t = lo_r, hi_r
        else:
            lo_t, hi_t = (0.0, hi_r) if primary > 0 else (lo_r, 0.0)
        probed = (
            self._tool_overlap(adapter, op, mode="cut", sketch=request.sketch, lo_mm=lo_t, hi_mm=hi_t)
            if hi_t - lo_t > 1e-9
            else None
        )
        how = (
            ""
            if probed is None
            else (
                f"from the real geometry: the cut's tool intersected with the part before the op (one boolean): "
                f"{probed.common_volume_mm3:.6g} mm3 of the part lies inside it"
            )
        )
        simple = True  # the removed volume is that of a prism through solid material
        if not through:
            lo, hi = self._sweep_range(op)
            boxes = [f.swept_profile_shape_bbox(frame, p, lo, hi) for p in model.profiles]
            inside = known and all(f.bbox_contains(body_box, b, tol=self.thresholds.position_mm) for b in boxes)
            volume = f.cut_volume(area, op.depth_mm, op.depth2_mm, op.both_directions)
            if probed is not None:
                simple = self.thresholds.close(-volume, probed.common_volume_mm3, "mm3")
                out.append(_exp("volume_delta", "mm3", -probed.common_volume_mm3, note=how))
                out.append(_exp("volume_sign", "count", -1, note="a cut that removes nothing is a cut in air"))
            else:
                note = "" if inside else (host_note or "prism exits body")
                out.append(_exp("volume_delta", "mm3", volume, asserted=inside, note=note))
        else:
            direction = f.scale(frame.n, primary)
            if op.both_directions:
                extent = body_box.extent_along(frame.n)
                direction = frame.n
            else:
                extent = _extent_from_plane(body_box, frame.origin, direction)
            if probed is not None:
                simple = self.thresholds.close(area * extent, probed.common_volume_mm3, "mm3")
                if not simple and not op.both_directions:
                    # the body is taller than the material the profile crosses (a thin flange under a hub): when the
                    # material under the whole profile is one solid block from the sketch plane, that block is the span
                    span = self._solid_span(
                        probed.common_volume_mm3,
                        area,
                        extent,
                        lambda length: area * length,
                        lambda length: self._tool_overlap(
                            adapter,
                            op,
                            mode="cut",
                            sketch=request.sketch,
                            lo_mm=0.0 if primary > 0 else -length,
                            hi_mm=length if primary > 0 else 0.0,
                        ),
                    )
                    if span is not None:
                        extent, simple = span, True
                out.append(_exp("volume_delta", "mm3", -probed.common_volume_mm3, note=how))
                out.append(_exp("volume_sign", "count", -1, note="a cut that removes nothing is a cut in air"))
            else:
                out.append(
                    _exp(
                        "volume_delta",
                        "mm3",
                        -area * extent,
                        asserted=False,
                        note="through-all extent taken at bbox level",
                    )
                )
            out.append(
                _exp(CUT_DIRECTION, "vec", direction, asserted=False, note="sketch normal used by cut_extent_along_n")
            )
            if probed is None or not simple:  # a measured solid block is exact: the walls run exactly its length
                out.append(
                    _exp(
                        CUT_AREA,
                        "mm2",
                        area,
                        asserted=False,
                        note="section of the cut: its walls run at least the removed volume / it",
                    )
                )
            oblique = max(abs(c) for c in frame.n) < 1.0 - 1e-6
            out.append(
                _exp(
                    "cut_extent_along_n",
                    "mm",
                    extent,
                    asserted=not oblique and known,
                    note=(
                        "oblique cut direction: bounding-box extents are not exact"
                        if oblique
                        else host_note or ("" if simple and probed is not None else _EXTENT_RANGE_NOTE)
                    ),
                )
            )
        oblique = through and max(abs(c) for c in frame.n) < 1.0 - 1e-6
        out.append(
            _exp(
                "bbox_within",
                "bbox",
                before.bbox,
                asserted=not oblique,
                note="oblique cut: the bounding box of a cylindrical wall is loose" if oblique else "",
            )
        )
        if oblique:
            # the independent check that stands in for the two bounding-box ones: material was removed, and no
            # more than the profile can sweep through the body along the cut normal
            out.append(
                _exp(
                    "cut_volume_bound",
                    "mm3",
                    area * before.bbox.extent_along(frame.n),
                    note=(
                        "coarse sanity bound (removed volume positive and at most profile area x body extent "
                        "along n); the final mass is the real guard"
                    ),
                )
            )
        return out

    def _op_revolve(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        model = self._sketch_model(op.sketch)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = None
        if model is None:
            return [_exp("volume_delta", "mm3", 0.0, asserted=False, note="sketch model unknown or unsupported")]
        axis: Axis = request.axis
        frame = model.frame
        volume = 0.0
        points: list[Vec3] = []
        crossing = False
        for profile in model.profiles:
            centroid = frame.to_world(*profile.centroid)
            volume += f.revolve_volume(profile.area_mm2, f.distance_point_to_axis(centroid, axis), op.angle_deg)
            profile_points = _profile_world_points(profile, frame, axis)
            signs = {
                math.copysign(1.0, f.dot(f.cross(f.sub(p, axis.origin), axis.direction), frame.n))
                for p in profile_points
                if f.distance_point_to_axis(p, axis) > self.thresholds.position_mm
            }
            crossing = crossing or len(signs) > 1
            points.extend(profile_points)
        full_turn = abs(op.angle_deg - 360.0) <= 1e-9
        self._region = f.revolve_bbox(axis, points)  # a full turn: a superset of any partial revolve
        box = _union_with_before(self._region, before)
        faces = sum(self._revolved_segments(p, frame, axis) for p in model.profiles)
        return [
            _exp(
                "volume_delta",
                "mm3",
                volume,
                asserted=not crossing,
                note="profile crosses the axis" if crossing else "",
            ),
            _exp(
                "new_faces_count",
                "count",
                faces,
                asserted=not _curved(model.profiles),
                note=_ARC_FACES_NOTE if _curved(model.profiles) else "",
            ),
            _exp("bbox_after", "bbox", box, asserted=full_turn, note="" if full_turn else "partial revolve"),
        ]

    def _revolved_segments(self, profile: Profile, frame: SketchFrame, axis: Axis) -> int:
        """Profile segments that sweep a surface: a segment lying on the axis creates no face."""
        if profile.kind == "circle":
            return 1
        if profile.kind == "rect":
            (u0, v0), (u1, v1) = profile.bbox_uv
            loop: list[Point2] = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        elif profile.sides is not None:
            return profile.sides  # arcs: the outline points are samples, not the edges (the count is only reported)
        else:
            loop = list(profile.points or ())
        tol = self.thresholds.position_mm
        count = 0
        for i, start in enumerate(loop):
            end = loop[(i + 1) % len(loop)]
            on_axis = all(f.distance_point_to_axis(frame.to_world(*p), axis) <= tol for p in (start, end))
            count += 0 if on_axis else 1
        return count

    def _op_sweep(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A constant section moved along a polyline: the volume is exactly ``area x path length`` (the executor
        already checked placement and clearance, see ``verification/sweep.py``). Like an extrude it assumes the
        new material does not overlap what is already there.

        - ``new_faces_count`` is asserted for circular sections on a new body (``2 + segments`` mitred, plus one
          toroidal face per corner when filleted); polygon sections and a merge into an existing body (faces
          may join or be modified) only report it.
        - a single segment has an exact box, asserted as ``bbox_after``; a bent path has an exact analytic box
          only when mitred, and the kernel's own box is loose on curved swept faces, so the multi-segment box is
          REPORTED and what is asserted is ``bbox_contains``: the observed box must contain the lower bound."""
        model = self._sketch_model(op.sketch)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = None
        if model is None or len(model.profiles) != 1:
            return [_exp("volume_delta", "mm3", 0.0, asserted=False, note="sketch model unknown or not one profile")]
        profile = model.profiles[0]
        try:
            analysis = sweep_math.analyse_path(request.path, request.corner_radius_mm)
        except sweep_math.SweepError as exc:
            return [_exp("volume_delta", "mm3", 0.0, asserted=False, note=str(exc))]
        # A bent path can carry new material anywhere along it, so the edit region is a CONSERVATIVE world box: the box
        # of the path points inflated by the section's full reach in every direction (never the straight-extrude box of
        # the profile along its normal, which a bend leaves behind). Fillet arcs stay inside the hull of the points.
        path_box = f.bbox_of_points(request.path)
        reach = sweep_math.section_reach(profile)
        self._region = BoundingBox(
            min=tuple(c - reach for c in path_box.min),  # type: ignore[arg-type]
            max=tuple(c + reach for c in path_box.max),  # type: ignore[arg-type]
        )
        out = [
            _exp(
                "volume_delta",
                "mm3",
                sweep_math.swept_volume(profile.area_mm2, analysis.length),
                note=(
                    "a sweep adds area x length: its path must not run through existing material "
                    "(start the path on the face and leave it)"
                ),
            )
        ]
        joins = bool(op.merge) and not _is_empty(before)
        if profile.kind == "circle":
            count: int | None = sweep_math.circle_face_count(analysis)
        elif analysis.segment_count == 1:
            count = 2 + _profile_sides(profile)  # two caps and one flat face per side: nothing to merge
        else:
            # rectangular faces merge across coplanar bends and split on others (measured: an L mitre of a
            # rectangle has 8 faces, not 10): no count is right for every path, so none is expected
            count = None
        if count is not None:
            note = "merged into an existing body: faces may join or be modified" if joins else ""
            note = note or (_ARC_FACES_NOTE if _curved([profile]) else "")
            out.append(_exp("new_faces_count", "count", count, asserted=not note, note=note))
        mitred = request.corner_radius_mm is None
        if analysis.segment_count == 1:
            box = sweep_math.mitre_bbox(request.path, profile, model.frame)
            out.append(_exp("bbox_after", "bbox", _union_with_before(box, before)))
            return out
        if mitred:
            box = sweep_math.mitre_bbox(request.path, profile, model.frame)
            out.append(
                _exp(
                    "bbox_after",
                    "bbox",
                    _union_with_before(box, before),
                    asserted=False,
                    note="exact analytic box of the mitred sweep; the kernel's box is loose on curved swept faces",
                )
            )
        lower = sweep_math.lower_bbox(request.path, request.corner_radius_mm, profile, model.frame)
        out.append(
            _exp(
                "bbox_contains",
                "bbox",
                lower,
                note="lower bound: the observed box must contain the centre line, the start cap and the end cap",
            )
        )
        return out

    # --- sheet metal ---------------------------------------------------------------------------------

    def _sheet_of(self, op: Any) -> tuple[str | None, sheetmetal.Sheet | None]:
        """The sheet a flange or an unfold works on: the one its face and edge refs trace to."""
        for ref in op.refs():
            key = self._sheet_of_op.get(getattr(ref, "from_op", None) or "")
            if key is not None:
                return key, self._sheets.get(key)
        return None, None

    def _op_base_flange(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A flat sheet: (outline - holes) area x thickness exactly, in a box one thickness deep on the chosen side of
        the sketch (the box of the outline: holes never reach out of it)."""
        model = self._sketch_model(op.sketch)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = None
        if model is None or not model.profiles:
            return [_exp("volume_delta", "mm3", 0.0, asserted=False, note="sketch model unknown or unsupported")]
        outline = sheetmetal.outline_of(model.profiles)
        sign = 1.0 if op.direction.value == "positive" else -1.0
        thickness = float(op.thickness_mm)
        box = f.swept_profile_shape_bbox(model.frame, outline, min(0.0, sign * thickness), max(0.0, sign * thickness))
        self._region = box
        self._sheets[op.id] = sheetmetal.new_sheet(model.frame, model.profiles, thickness, sign)
        self._sheet_of_op[op.id] = op.id
        return [
            _exp("volume_delta", "mm3", sheetmetal.blank_area(model.profiles) * thickness),
            _exp("bbox_after", "bbox", _union_with_before(box, before)),
        ]

    def _op_edge_flange(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A bent wall only ever ADDS reach: the old box stays inside the new one (asserted). The volume it adds has a
        closed form (``sheetmetal.flange_volume``, measured against the add-on) that is asserted only for the exact
        case: a whole straight side of a rectangular sheet with no flange on a perpendicular side yet (a corner relief
        or miter would change it); otherwise it is reported with the reason."""
        key, sheet = self._sheet_of(op)
        out = [_exp("bbox_contains", "bbox", before.bbox, note="a flange never takes reach away from the sheet")]
        note = "the sheet this edge belongs to is not tracked (adopted or edited by hand)"
        volume: float | None = None
        if sheet is not None and key is not None:
            self._sheet_of_op[op.id] = key
            edge = adapter.edge_info(EdgeRequest(operation_id=op.id, edge=request.edge))
            volume = sheetmetal.add_flange(
                sheet,
                edge.start,
                edge.end,
                angle_deg=request.angle_deg,
                radius=request.bend_radius_mm,
                length=request.length_mm,
                material_inside=request.position == "material_inside",
                from_outer_sharp=request.length_from == "outer_sharp",
            )
            note = "closed form not covered: not a whole side of a rectangular sheet, or a neighbour flange exists"
        if volume is None:
            out.append(_exp("volume_delta", "mm3", 0.0, asserted=False, note=note))
        else:
            out.append(_exp("volume_delta", "mm3", volume))
        return out

    def _op_unfold(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """The flat pattern is a separate solid: the document's volume stays. Its own facts are checked against the
        closed form of the bend allowance (asserted for the exact case, reported otherwise): thickness is always the
        sheet's, the flat solid must be valid."""
        _, sheet = self._sheet_of(op)
        out = [
            _exp("volume_delta", "mm3", 0.0, note="the flat pattern is not part of the solid"),
            _exp("flat_pattern_valid", "count", 1),
        ]
        if sheet is not None:
            out.append(_exp("flat_thickness_mm", "mm", float(sheet["t"])))
        size = None if sheet is None else sheetmetal.flat_size(sheet, request.k_factor)
        note = "" if size is not None else "flat size not predicted: not a rectangular sheet with whole-side flanges"
        for name, value in zip(("flat_length_mm", "flat_width_mm"), size or (0.0, 0.0), strict=True):
            out.append(_exp(name, "mm", value, asserted=size is not None, note=note))
        return out

    # --- shell ---------------------------------------------------------------------------------------

    def _host_shape(self, refs: Sequence[Any]) -> Shape | None:
        """The analytic primitive a shell or a draft works on, only when every face ``refs`` traces to ONE tracked body
        that no edit has touched (an edit's walls change the volume the closed forms describe); ``None`` otherwise.
        Must be read before ``compute`` records the op itself as an edit."""
        keys: set[str] = set()
        for ref in refs:
            from_op = getattr(ref, "from_op", None)
            key = self._body_of_op.get(_body_ref_id(ref)) if from_op is not None else None
            if key is None:
                return None  # a live selection, or an op that built no tracked body: the host is unknown
            keys.add(key)
        keys = self._expand(keys)
        if len(keys) != 1:
            return None
        (key,) = keys
        if key in self._consumed or self._edits.get(key):
            return None
        return self._shape_by_body.get(key)

    def _box_shell(
        self, shape: BoxShape, faces: list[FaceInfo], thickness: float, outward: bool
    ) -> tuple[float, BoundingBox | None] | None:
        """``(volume_delta of the body, box after the shell or None)`` of hollowing an axis-aligned box through the
        removed ``faces``, or ``None`` when a face is not a whole face of this box. Every axis keeps one wall per face
        that is NOT removed: the cavity is ``extent - walls * t`` long (inward) and the outer box ``extent + walls * t``
        (outward, where the original box becomes the cavity and only the walls remain)."""
        tol = self.thresholds.position_mm
        extents = f.sub(shape.max, shape.min)
        removed: list[set[str]] = [set(), set(), set()]
        for face in faces:
            if face.surface is not SurfaceType.plane or face.normal is None:
                return None
            axis = max(range(3), key=lambda i: abs(face.normal[i]))  # type: ignore[index]
            if abs(abs(face.normal[axis]) - 1.0) > 1e-6:  # type: ignore[index]
                return None
            high = face.normal[axis] > 0  # type: ignore[index]
            if abs(face.center[axis] - (shape.max[axis] if high else shape.min[axis])) > tol:
                return None
            lateral = [i for i in range(3) if i != axis]
            if not self.thresholds.close(face.area_mm2, extents[lateral[0]] * extents[lateral[1]], "mm2"):
                return None
            removed[axis].add("hi" if high else "lo")
        walls = [2 - len(r) for r in removed]
        if sum(walls) == 0:
            return None
        volume = f.box_volume(extents)
        if outward:
            grown = tuple(extents[i] + walls[i] * thickness for i in range(3))
            box = BoundingBox(
                min=tuple(shape.min[i] - (0.0 if "lo" in removed[i] else thickness) for i in range(3)),  # type: ignore[arg-type]
                max=tuple(shape.max[i] + (0.0 if "hi" in removed[i] else thickness) for i in range(3)),  # type: ignore[arg-type]
            )
            return f.box_volume(grown) - volume - volume, box  # type: ignore[arg-type]
        cavity = tuple(extents[i] - walls[i] * thickness for i in range(3))
        for i, size in enumerate(cavity):
            if size <= tol:
                msg = (
                    f"a {thickness:g} mm wall leaves no cavity along {'XYZ'[i]}: the box is {extents[i]:g} mm long "
                    f"there with {walls[i]} wall(s) left standing; use a thinner wall or remove another face"
                )
                raise ShellThicknessError(msg)
        return -f.box_volume(cavity), None  # type: ignore[arg-type]

    def _cylinder_shell(
        self, shape: CylinderShape, faces: list[FaceInfo], thickness: float, outward: bool
    ) -> float | None:
        """``volume_delta`` of hollowing a right cylinder through removed END CAPS (the side wall is never removed
        here: that has no simple closed form), or ``None`` when a face is not a whole cap of this cylinder."""
        tol = self.thresholds.position_mm
        direction = f.normalise(shape.axis.direction)
        removed: set[str] = set()
        for face in faces:
            if face.surface is not SurfaceType.plane or face.normal is None:
                return None
            sense = f.dot(face.normal, direction)
            if abs(abs(sense) - 1.0) > 1e-6:
                return None
            along = f.dot(f.sub(face.center, shape.axis.origin), direction)
            if abs(along) <= tol and sense < 0:
                removed.add("base")
            elif abs(along - shape.height) <= tol and sense > 0:
                removed.add("top")
            else:
                return None
            if not self.thresholds.close(face.area_mm2, math.pi * shape.radius**2, "mm2"):
                return None
        walls = 2 - len(removed)
        volume = f.cylinder_volume(shape.radius, shape.height)
        if outward:
            skin = f.cylinder_volume(shape.radius + thickness, shape.height + walls * thickness) - volume
            return skin - volume
        if shape.radius - thickness <= tol:
            msg = (
                f"a {thickness:g} mm wall leaves no cavity: the cylinder radius is {shape.radius:g} mm; "
                "use a thinner wall"
            )
            raise ShellThicknessError(msg)
        if shape.height - walls * thickness <= tol:
            msg = (
                f"a {thickness:g} mm wall leaves no cavity along the axis: the cylinder is {shape.height:g} mm long "
                f"with {walls} end wall(s) left standing; use a thinner wall or remove another cap"
            )
            raise ShellThicknessError(msg)
        return -f.cylinder_volume(shape.radius - thickness, shape.height - walls * thickness)

    def _op_shell(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """What is certain about hollowing a body, and what only a tracked primitive can say exactly.

        ``Thickness`` builds the WALLS alone. Inward, the cavity is the offset of the body: the outer size stays and the
        volume always falls (``volume_sign`` -1 asserted, ``bbox_within`` the old box). Outward, the original body
        becomes the cavity and the walls grow outside it: the volume falls for an ordinary part but RISES for a thin
        one, and can even equal the old one, so no sign is certain: ``volume_sign`` 0 is only REPORTED and the
        adapter's own post-check refuses the kernel's silent no-op (an empty face list, a wall that does not fit).

        ``volume_delta`` is asserted only for a tracked, untouched box or cylinder (closed forms, measured on FreeCAD
        1.0.2 and 1.1.3); the arc join, an edited or untracked body and a removed side wall are REPORTED, never
        guessed. The shell registers no body and leaves the tracked shape to ``compute`` (any shell is an edit)."""
        thickness = float(request.thickness_mm)
        outward = bool(request.outward)
        faces = list(adapter.faces_info(FacesInfoRequest(operation_id=op.id, faces=tuple(request.faces))).faces)
        out = [
            _exp(
                "volume_sign",
                "count",
                0 if outward else -1,
                asserted=not outward,
                note=(
                    "outward builds the walls around the old body (it becomes the cavity): the volume may fall or "
                    "rise, and a skin that happens to weigh as much as the old body is legitimate, so the change is "
                    "only reported (the worker refuses a shell that changed nothing)"
                    if outward
                    else "an inward shell removes material"
                ),
            )
        ]
        shape = self._host_shape(op.refs())
        delta: float | None = None
        box: BoundingBox | None = None
        if request.join == "arc":
            note = "arc join: the rounded corners have no closed form"
        elif shape is None:
            note = "the body's analytic shape is unknown (edited, or not traced to one tracked primitive)"
        else:
            note = "the removed faces are not whole faces of the tracked primitive"
            if isinstance(shape, BoxShape):
                modelled = self._box_shell(shape, faces, thickness, outward)
                if modelled is not None:
                    delta, box = modelled
            else:
                delta = self._cylinder_shell(shape, faces, thickness, outward)
        if delta is None:
            out.append(_exp("volume_delta", "mm3", 0.0, asserted=False, note=note))
        else:
            out.append(_exp("volume_delta", "mm3", delta))
        if outward:
            if box is not None and self._live_body_count() == 1 and not before.untracked_bodies:
                out.append(_exp("bbox_after", "bbox", box))
        else:
            out.append(_exp("bbox_within", "bbox", before.bbox))
        return out

    # --- draft ---------------------------------------------------------------------------------------

    def _box_draft_delta(self, shape: BoxShape, faces: list[FaceInfo], request: Any) -> float | None:
        """``volume_delta`` of drafting whole side faces of an axis-aligned box about a plane perpendicular to the pull
        (``formulas.drafted_box_volume``), or ``None`` when the pull is not a world axis or a face is not a whole face
        of this box lying along the pull."""
        tol = self.thresholds.position_mm
        pull = request.pull
        axis_p = max(range(3), key=lambda i: abs(pull[i]))
        if abs(abs(pull[axis_p]) - 1.0) > 1e-6:
            return None
        extents = f.sub(shape.max, shape.min)
        lateral = [i for i in range(3) if i != axis_p]
        removed: dict[int, set[str]] = {i: set() for i in lateral}
        for face in faces:
            if face.surface is not SurfaceType.plane or face.normal is None:
                return None
            axis = max(range(3), key=lambda i: abs(face.normal[i]))  # type: ignore[index]
            if axis == axis_p or abs(abs(face.normal[axis]) - 1.0) > 1e-6:  # type: ignore[index]
                return None
            high = face.normal[axis] > 0  # type: ignore[index]
            if abs(face.center[axis] - (shape.max[axis] if high else shape.min[axis])) > tol:
                return None
            other = next(i for i in lateral if i != axis)
            if not self.thresholds.close(face.area_mm2, extents[axis_p] * extents[other], "mm2"):
                return None
            removed[axis].add("hi" if high else "lo")
        rising = pull[axis_p] > 0
        w_min, w_max = (shape.min[axis_p], shape.max[axis_p]) if rising else (-shape.max[axis_p], -shape.min[axis_p])
        w_neutral = f.dot(pull, request.neutral.origin)
        w_lo, w_hi = w_min - w_neutral, w_max - w_neutral
        angle = float(request.angle_deg)
        t = math.tan(math.radians(angle))
        drafted = (len(removed[lateral[0]]), len(removed[lateral[1]]))
        for k, axis in zip(drafted, lateral, strict=True):
            if k and extents[axis] - k * t * w_hi <= tol:
                msg = (
                    f"a {angle:g} deg draft narrows the {extents[axis]:g} mm wide box along {'XYZ'[axis]} to nothing "
                    f"within {w_hi:g} mm of the neutral plane; use a smaller angle or move the neutral plane"
                )
                raise DraftAngleError(msg)
        volume = f.drafted_box_volume((extents[lateral[0]], extents[lateral[1]]), drafted, angle, w_lo, w_hi)
        return volume - f.box_volume(extents)

    def _cylinder_draft_delta(self, shape: CylinderShape, faces: list[FaceInfo], request: Any) -> float | None:
        """``volume_delta`` of drafting the lateral face of a right cylinder into a cone about a plane perpendicular to
        its axis, or ``None`` when the face is not that lateral face or the pull is not along the axis."""
        tol = self.thresholds.position_mm
        pull = request.pull
        direction = f.normalise(shape.axis.direction)
        if abs(abs(f.dot(pull, direction)) - 1.0) > 1e-6 or len(faces) != 1:
            return None
        (face,) = faces
        if (
            face.surface is not SurfaceType.cylinder
            or face.axis is None
            or face.radius is None
            or not self.thresholds.close(shape.radius, face.radius, "mm")
            or not self.thresholds.parallel(direction, face.axis.direction, either_sense=True)
            or f.distance_point_to_axis(face.axis.origin, shape.axis) > tol
        ):
            return None
        w_neutral = f.dot(pull, request.neutral.origin)
        far = f.add(shape.axis.origin, f.scale(direction, shape.height))
        ends = (f.dot(pull, shape.axis.origin), f.dot(pull, far))
        w_lo, w_hi = min(ends) - w_neutral, max(ends) - w_neutral
        angle = float(request.angle_deg)
        if shape.radius - math.tan(math.radians(angle)) * w_hi <= tol:
            msg = (
                f"a {angle:g} deg draft narrows the radius {shape.radius:g} mm to nothing within {w_hi:g} mm of the "
                "neutral plane; use a smaller angle or move the neutral plane"
            )
            raise DraftAngleError(msg)
        volume = f.drafted_cylinder_volume(shape.radius, angle, w_lo, w_hi)
        return volume - f.cylinder_volume(shape.radius, shape.height)

    def _op_draft(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """What is certain about tapering faces, and what only a tracked primitive can say exactly.

        After the op every drafted face's OUTWARD normal has a component sin(angle) along the pull, so ``draft_angle``
        wants an observed planar face at that tilt. It is asserted only when a requested face is a plane lying along the
        pull (an already tilted face, or a cone, has no tilt the observation can vouch for), and it never states a
        volume sign: drafting about a mid-height neutral plane adds and removes equal wedges, and a pocket wall's draft
        can add material or remove it.

        ``volume_delta`` is asserted only for a tracked, untouched box (whole side faces) or cylinder (its lateral
        face) with the pull along a world axis / its axis (closed forms measured on FreeCAD 1.0.2 and 1.1.3); a pocket
        wall, an edited or untracked body and anything else are REPORTED. The draft registers no body and leaves the
        tracked shape to ``compute`` (any draft is an edit)."""
        angle = float(request.angle_deg)
        pull = f.normalise(request.pull)
        faces = list(adapter.faces_info(FacesInfoRequest(operation_id=op.id, faces=tuple(request.faces))).faces)
        along_pull = math.sin(math.radians(1.0))
        along = any(
            face.surface is SurfaceType.plane
            and face.normal is not None
            and abs(f.dot(f.normalise(face.normal), pull)) <= along_pull
            for face in faces
        )
        out = [_exp(DRAFT_PULL, "vec", pull, asserted=False, note="pull direction used by draft_angle")]
        if along:
            note = "a drafted planar face leans so its outward normal has component sin(angle) along the pull"
            if not all(
                face.surface is SurfaceType.plane
                and face.normal is not None
                and abs(f.dot(f.normalise(face.normal), pull)) <= along_pull
                for face in faces
            ):
                note += (
                    "; requested faces that are tilted against the pull or curved are unchecked (built, but no lean "
                    "is verified for them)"
                )
        else:
            note = (
                "no drafted planar face lies along the pull (a cone's semi-angle, or an already tilted face, is not "
                "described by the face observations)"
            )
        out.append(_exp("draft_angle", "deg", angle, asserted=along, note=note))
        shape = self._host_shape(op.faces)
        delta: float | None = None
        if shape is None:
            note = (
                "the body's analytic shape is unknown (edited, or not traced to one tracked primitive; a pocket wall's "
                "draft can add or remove material)"
            )
        else:
            note = "the faces are not whole side faces of the tracked primitive taken along the pull"
            if isinstance(shape, BoxShape):
                delta = self._box_draft_delta(shape, faces, request)
            else:
                delta = self._cylinder_draft_delta(shape, faces, request)
        if delta is None:
            out.append(_exp("volume_delta", "mm3", 0.0, asserted=False, note=note))
        else:
            out.append(_exp("volume_delta", "mm3", delta))
        return out

    # --- hole ---------------------------------------------------------------------------------------

    def _op_hole(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        face = adapter.face_info(FaceRequest(operation_id=op.id, face=request.face))
        if face.surface is SurfaceType.cylinder and face.axis is not None and face.radius is not None:
            out = self._radial_hole(op, request.position, face, before, adapter)
        elif face.normal is None:
            return [_exp("volume_delta", "mm3", 0.0, asserted=False, note="hole face is not planar")]
        else:
            out = self._planar_hole(op, request, face, before, adapter)
        out.extend(self._thread_rows(request))
        return out

    def _thread_rows(self, request: Any) -> list[Expectation]:
        """The cosmetic thread of a tapped hole is metadata: nothing in the geometry (the hole is the plain tap-drill
        cylinder, whose diameter and volume the rows above already assert) can confirm it, so it is only reported."""
        thread_request = getattr(request, "thread", None)
        if thread_request is None:
            return []
        spec = thread(thread_request.size)
        if spec is None:
            return []
        depth = "" if thread_request.depth_mm is None else f", thread depth {thread_request.depth_mm:g} mm"
        note = (
            f"cosmetic {spec.designation} thread, pitch {spec.pitch_mm:g} mm{depth}: metadata only, no geometry "
            f"({TRANSCRIPTION_NOTE['en']})"
        )
        return [_exp("hole_thread_metadata", "mm", spec.major_mm, asserted=False, note=note)]

    def _radial_hole(
        self, op: Any, position: tuple[float, float], face: FaceInfo, before: StateSnapshot, adapter: Adapter
    ) -> list[Expectation]:
        """A hole entering the CONVEX wall of a tube or shaft along an inward radial (``position = (axial, angle)``,
        ``contract.geometry.radial_entry``). The hole axis crosses the cylinder axis at right angles, so its
        direction and position are exact and asserted. What the kernel cuts is not: two crossing cylinders have no
        closed form and OCC's cylinder-cylinder volumes are 5e-4..9e-4 low (relative), above the 1e-4 volume
        tolerance, so ``volume_delta`` is REPORTED (the oracle is ``formulas.radial_hole_volume``, exact for a
        solid cylinder and an upper bound for a tube) and what is asserted instead is ``cut_volume_bound``: material
        left, and no more than the hole's disc sweeps through the body along its own axis. The face count depends
        on the (unknown) bore, so it is reported too. ``hole_within_face`` is not emitted: the hole's footprint on a
        curved face has no flat box to compare with."""
        assert face.axis is not None and face.radius is not None
        entry = radial_entry(face, position)
        direction = entry.drill_direction
        radius = op.diameter_mm / 2.0
        axis = canonical_axis_direction(face.axis.direction)
        if max(abs(c) for c in axis) >= 1.0 - 1e-6:  # a world axis: the box of the face is exact along it
            half = face.bbox.extent_along(axis) / 2.0
            if abs(position[0]) + radius + HOLE_EDGE_CLEARANCE_MM > half + self.thresholds.position_mm:
                msg = (
                    f"the D{op.diameter_mm:g} hole at axial_mm {position[0]:g} crosses the axial end of face "
                    f"{face.handle.id}, or leaves less than {HOLE_EDGE_CLEARANCE_MM:g} mm of clearance to it (the "
                    f"face spans +-{half:g} mm from its centre): keep |axial_mm| <= "
                    f"{max(half - radius - HOLE_EDGE_CLEARANCE_MM, 0.0):g}"
                )
                raise HoleLeavesFaceError(msg)
        blind = op.end_condition.value == "blind"
        depth = float(op.depth_mm) if blind else None
        oblique = max(abs(c) for c in direction) < 1.0 - 1e-6
        reach = depth if depth is not None else self._reach(before)
        self._region = f.cylinder_bbox(Axis(origin=entry.surface_point, direction=direction), radius, 0.0, reach)
        solid = f.radial_hole_volume(face.radius, 0.0, radius, depth)
        span = before.bbox.extent_along(direction)
        if depth is not None:
            span = min(span, depth)
        out = [
            _exp(
                "volume_delta",
                "mm3",
                -solid,
                asserted=False,
                note=(
                    "radial hole through a curved wall: exact only for a solid cylinder (a tube removes less) and "
                    "the kernel's cylinder-cylinder volumes carry ~1e-3 relative noise"
                ),
            ),
            _exp(
                "cut_volume_bound",
                "mm3",
                math.pi * radius * radius * span,
                note=(
                    "coarse sanity bound (removed volume positive and at most hole area x length along its axis); "
                    "the final mass is the real guard"
                ),
            ),
            _exp("hole_axis_direction", "vec", direction),
            _exp("hole_axis_position", "vec", entry.axis_point),
            _exp("hole_radius", "mm", radius),
            _exp(
                "new_faces_count",
                "count",
                2,
                asserted=False,
                note="wall plus floor (blind) or one wall per tube wall (through-all); depends on the unknown bore",
            ),
            _exp(
                "bbox_within",
                "bbox",
                before.bbox,
                asserted=not oblique,
                note=_LOOSE_BOX_NOTE if oblique else "",
            ),
        ]
        if blind:
            out.append(_exp("hole_entry_face_modified", "bool", True))
        return out

    def _planar_hole(
        self, op: Any, request: Any, face: FaceInfo, before: StateSnapshot, adapter: Adapter
    ) -> list[Expectation]:
        """A hole on a planar face. What it must remove is the REAL boolean of its tool (the half section of
        ``contract.holetool``: cylinder, counterbore or countersink, drill point) with the part as it is now, asked of
        the adapter (``tool_overlap``): a hole that breaks into an existing cavity, or ends in one, removes less than
        its own volume and is still right. The structure rows (the faces it creates, their count and extent) describe a
        hole drilled into solid material; they are asserted only when the boolean says it was
        (the volume removed equals the closed form), and reported otherwise."""
        assert face.normal is not None
        position = request.position
        frame = frame_for_face(face, request.origin)
        point = frame.to_world(*position)
        direction = f.scale(face.normal, -1.0)
        radius = op.diameter_mm / 2.0
        cbore = (request.counterbore.diameter_mm, request.counterbore.depth_mm) if request.counterbore else None
        csink = (request.countersink.diameter_mm, request.countersink.angle_deg) if request.countersink else None
        widest = max(radius, (cbore or csink or (0.0,))[0] / 2.0)
        through = op.end_condition.value == "through_all"
        up_to = op.end_condition.value == "up_to_face"
        depth: float | None = None
        if op.end_condition.value == "blind":
            depth = float(op.depth_mm)
        elif not through:
            depth = self._depth_to_face(request, point, direction, adapter)
            if depth is None:
                return self._unmeasurable_hole(request, "up_to_face is not a planar face normal to the hole axis")
        tip = request.drill_point_angle_deg if request.drill_point.value == "angled" and not through else None
        reach = depth if depth is not None else self._reach(before)
        self._region = f.cylinder_bbox(Axis(origin=point, direction=direction), widest, 0.0, reach)
        host_box = self._host_box(op, face.body, before, adapter)
        body_box = host_box if host_box is not None else before.bbox
        oblique = max(abs(c) for c in direction) < 1.0 - 1e-6
        extent = _extent_from_plane(body_box, point, direction) if through else depth
        assert extent is not None
        closed_form = holetool.outline_volume(
            holetool.hole_outline(op.diameter_mm, extent, counterbore=cbore, countersink=csink, point_angle_deg=tip)
        )
        probe_length = self._reach(before) + 1.0 if through else extent
        probed = self._tool_overlap(
            adapter,
            op,
            mode="cut",
            hole=HoleTool(
                face=request.face,
                position=position,
                outline=holetool.hole_outline(
                    op.diameter_mm, probe_length, counterbore=cbore, countersink=csink, point_angle_deg=tip
                ),
            ),
        )
        out: list[Expectation] = []
        if probed is not None:
            removed = probed.common_volume_mm3
            # a through-all closed form takes the bbox extent, loose on an oblique axis: there the boolean is the judge
            simple = (through and oblique and removed > 0.0) or self.thresholds.close(closed_form, removed, "mm3")
            if through and not simple and not oblique:
                # the body is taller than the material the hole crosses (a thin flange under a hub): when the
                # material under the whole hole is one solid block from the entry face, that block is the extent

                def tool_volume(length: float) -> float:
                    return holetool.outline_volume(
                        holetool.hole_outline(op.diameter_mm, length, counterbore=cbore, countersink=csink)
                    )

                span = self._solid_span(
                    removed,
                    math.pi * radius * radius,
                    extent,
                    tool_volume,
                    lambda length: self._tool_overlap(
                        adapter,
                        op,
                        mode="cut",
                        hole=HoleTool(
                            face=request.face,
                            position=position,
                            outline=holetool.hole_outline(op.diameter_mm, length, counterbore=cbore, countersink=csink),
                        ),
                    ),
                )
                if span is not None:
                    extent, simple = span, True
            how = (
                f"from the real geometry: the hole's tool intersected with the part before the op (one boolean): "
                f"{removed:.6g} mm3 of the part lies inside it"
            )
            out.append(_exp("volume_delta", "mm3", -removed, note=how))
            out.append(
                _exp(
                    "volume_sign",
                    "count",
                    -1,
                    note="a hole that removes nothing is a hole placed in air",
                )
            )
        else:
            simple = True
            plain = cbore is None and csink is None and tip is None
            inside = f.bbox_contains(
                body_box,
                f.cylinder_bbox(Axis(origin=point, direction=direction), widest, 0.0, extent),
                tol=self.thresholds.position_mm,
            )
            if through:
                exact, note = False, "through-all extent taken at bbox level"
            elif not inside:
                exact, note = False, "hole exits body"
            elif oblique:
                exact, note = False, "oblique entry face: containment is not provable"
            else:
                exact, note = plain, "" if plain else "closed form of the tool, not measured"
            out.append(_exp("volume_delta", "mm3", -closed_form, asserted=exact, note=note))
        structure = (
            ""
            if simple
            else (
                "the hole meets an existing cavity or leaves the part, so the faces it creates are not those of a hole "
                "in solid material"
            )
        )
        if not through:
            out.append(_exp("hole_entry_face_modified", "bool", True))
        else:
            out.append(
                _exp(CUT_DIRECTION, "vec", direction, asserted=False, note="hole axis used by cut_extent_along_n")
            )
            if probed is None or not simple:  # a measured solid block is exact: the wall runs exactly its length
                out.append(
                    _exp(
                        CUT_AREA,
                        "mm2",
                        math.pi * widest * widest,
                        asserted=False,
                        note="widest section of the hole: its wall runs at least the removed volume / it",
                    )
                )
            out.append(
                _exp(
                    "cut_extent_along_n",
                    "mm",
                    extent,
                    asserted=not oblique,
                    note=(
                        "oblique hole axis: bounding-box extents are not exact"
                        if oblique
                        else ("" if simple and probed is not None else _EXTENT_RANGE_NOTE)
                    ),
                )
            )
        # The footprint check is exact only when the entry face IS its bounding rectangle (area equal to the
        # box's area): a disc, an L-shaped face or a face already holed is larger than its outline, so the
        # box-level containment is then reported, not asserted.
        rectangular = self.thresholds.close(face.area_mm2, f.face_footprint_area(face.bbox), "mm2")
        faces = (1 if through else 2) + (2 if cbore else 0) + (1 if csink else 0)
        out.extend(
            [
                _exp("hole_axis_direction", "vec", direction, asserted=simple, note=structure),
                _exp("hole_axis_position", "vec", point, asserted=simple, note=structure),
                _exp("hole_radius", "mm", radius, asserted=simple, note=structure),
                _exp(
                    "hole_within_face",
                    "bbox",
                    face.bbox,
                    asserted=rectangular and simple,
                    note=structure
                    or ("" if rectangular else "entry face is not a full rectangle: bounding-box containment only"),
                ),
                _exp(
                    "new_faces_count",
                    "count",
                    faces,
                    asserted=simple and not up_to,
                    note=structure
                    or (
                        "a hole that ends at the plane of another face has a bottom only if there is material "
                        "beyond that plane"
                        if up_to
                        else ""
                    ),
                ),
                _exp(
                    "bbox_within",
                    "bbox",
                    before.bbox,
                    asserted=not oblique,
                    note=_LOOSE_BOX_NOTE if oblique else "",
                ),
            ]
        )
        if oblique:
            # the independent check that stands in for the exact ones: material was removed, and no more than the
            # hole can sweep through the body along its own axis (the depth, for a blind hole)
            span = before.bbox.extent_along(direction)
            if not through:
                span = min(span, extent)
            out.append(
                _exp(
                    "cut_volume_bound",
                    "mm3",
                    math.pi * widest * widest * span,
                    note=(
                        "coarse sanity bound (removed volume positive and at most hole area x length along its axis); "
                        "the final mass is the real guard"
                    ),
                )
            )
        return out

    def _depth_to_face(self, request: Any, point: Vec3, direction: Vec3, adapter: Adapter) -> float | None:
        """Distance from the entry point to the plane of the ``up_to_face`` target along the hole axis (the depth the
        adapter drills), or ``None`` when the target is not a planar face normal to the axis and ahead of the entry."""
        try:
            target = adapter.face_info(FaceRequest(operation_id=request.operation_id, face=request.up_to_face))
        except AdapterError:
            return None
        if target.normal is None or abs(f.dot(f.normalise(target.normal), direction)) < 1.0 - 1e-6:
            return None
        depth = f.dot(f.sub(target.center, point), direction)
        return depth if depth > self.thresholds.abs_mm else None

    def _unmeasurable_hole(self, request: Any, why: str) -> list[Expectation]:
        return [_exp("volume_delta", "mm3", 0.0, asserted=False, note=f"{why}: the adapter will refuse the hole")]

    def _solid_span(
        self,
        removed: float,
        section: float,
        reach: float,
        volume_at: Callable[[float], float],
        window: Callable[[float], ToolOverlapResult | None],
    ) -> float | None:
        """The length of the one solid block a through tool crosses when the body is taller than that block (a thin
        flange under a hub), or ``None``. ``volume_at(length)`` is the tool's volume over its first ``length`` mm;
        ``reach`` is the length to the far side of the body, ``section`` the section the tool has past its head and
        ``removed`` what the boolean with the part says it removes. The block is that long when the tool of that
        length alone already holds all of ``removed`` (``window(length)``) and is as full as its own volume: then there
        is material under the whole tool for that length from the start and none after. Two plates with a gap
        between them, or a profile half over the edge, fail that and give ``None`` (the bounds judge those)."""
        if section <= 0.0 or removed <= self.thresholds.abs_mm3:
            return None
        span = reach - (volume_at(reach) - removed) / section
        if span <= self.thresholds.abs_mm or span >= reach - self.thresholds.abs_mm:
            return None
        if not self.thresholds.close(volume_at(span), removed, "mm3"):
            return None
        inside = window(span)
        if inside is None or not self.thresholds.close(removed, inside.common_volume_mm3, "mm3"):
            return None
        return span

    def _tool_overlap(self, adapter: Adapter, op: Any, **fields: Any) -> ToolOverlapResult | None:
        """One ``tool_overlap`` question to the adapter, or ``None`` when it cannot answer (an adapter without the
        optional tool, or a tool it cannot build): the callers then fall back to the closed form."""
        try:
            return adapter.tool_overlap(ToolOverlapRequest(operation_id=op.id, **fields))
        except AdapterError:
            return None

    # --- patterns -----------------------------------------------------------------------------------

    def _seed_faces(
        self, op: Any, features: tuple[FeatureHandle, ...], adapter: Adapter
    ) -> tuple[int, BoundingBox | None]:
        count = 0
        box: BoundingBox | None = None
        for feature in features:
            faces = adapter.faces_of(
                FeatureRequest(operation_id=op.id, feature=feature, role=ProvenanceRole.created)
            ).faces
            count += len(faces)
            for face in faces:
                box = face.bbox if box is None else f.bbox_union(box, face.bbox)
        return count, box

    def _point_on_pad(self, frame: SketchFrame, profile: Profile, point: Vec3) -> bool:
        tol = self.thresholds.position_mm
        rel = f.sub(point, frame.origin)
        u, v = f.dot(rel, frame.u), f.dot(rel, frame.v)
        if profile.kind == "rect":
            (u0, v0), (u1, v1) = profile.bbox_uv
            return u0 - tol <= u <= u1 + tol and v0 - tol <= v <= v1 + tol
        if profile.kind == "circle" and profile.center is not None and profile.radius is not None:
            return math.hypot(u - profile.center[0], v - profile.center[1]) <= profile.radius + tol
        if profile.kind == "polygon" and profile.points:
            inside = False
            pts = profile.points
            for i, (ax, ay) in enumerate(pts):
                bx, by = pts[(i + 1) % len(pts)]
                if (ay > v) != (by > v) and u < (bx - ax) * (v - ay) / (by - ay) + ax:
                    inside = not inside
                # on an edge counts as inside
                length = math.hypot(bx - ax, by - ay)
                if length > 0 and abs((bx - ax) * (ay - v) - (ax - u) * (by - ay)) / length <= tol:
                    if min(ax, bx) - tol <= u <= max(ax, bx) + tol and min(ay, by) - tol <= v <= max(ay, by) + tol:
                        return True
            return inside
        return False

    def _host_holds(self, box: BoundingBox) -> bool:
        """Whether the material of the host surely surrounds ``box`` (as seen along the pad): the base pad's outline
        (a rectangle, a circle or a polygon with no vertex inside the box), or the box of a single untouched box
        primitive. Anything else is not known."""
        tol = self.thresholds.position_mm
        if self._pad_host is not None:
            frame, profile = self._pad_host
            if not all(self._point_on_pad(frame, profile, corner) for corner in f.bbox_corners(box)):
                return False
            if profile.kind == "polygon" and profile.points:
                projected = [
                    (f.dot(f.sub(c, frame.origin), frame.u), f.dot(f.sub(c, frame.origin), frame.v))
                    for c in f.bbox_corners(box)
                ]
                u0, u1 = min(p[0] for p in projected), max(p[0] for p in projected)
                v0, v1 = min(p[1] for p in projected), max(p[1] for p in projected)
                if any(u0 + tol < x < u1 - tol and v0 + tol < y < v1 - tol for x, y in profile.points):
                    return False
            return True
        live = [k for k in self._tracked_keys() if k not in self._consumed]
        if len(live) == 1 and not self._edits.get(live[0]):
            shape = self._shape_by_body[live[0]]
            if isinstance(shape, BoxShape):
                return f.bbox_contains(f.shape_bbox(shape), box, tol=tol)
        return False

    def _landing_hazard(self, op: Any, instance_boxes: list[BoundingBox] | None, delta: float) -> str:
        """Why the volume and the face count of the new instances cannot be vouched for, or ``""``.

        A pattern (or a mirror) assumes each new instance does to the host what its seed did. That fails when the
        instance lands on an earlier feature (a bore on a bore removes less, a boss on a boss adds less and merges its
        faces) or, for a cut, where the host has no material (a void of the outline). PartDesign hosts record no
        edit regions, so the regions of the earlier editing ops are kept for this; an op that recorded none could be
        anywhere. The seeds themselves and the base pad are not earlier features. The SEED's own box counts as much as
        the new instances': a bore drilled through a post does more than its reflection across the bare plate will."""
        tol = self.thresholds.position_mm
        own = set(getattr(op, "features", ()))
        fresh = instance_boxes[1:] if instance_boxes else []
        for other, region in self._feature_regions.items():
            if other in own or other == op.id:
                continue
            if instance_boxes is None or region is None or any(_meets(region, box, tol) for box in instance_boxes):
                return (
                    f"the seed or an instance may stand on the earlier feature {other!r}: they would then change the "
                    "volume and the faces differently from each other"
                )
        if delta < 0 and fresh and not all(self._host_holds(box) for box in fresh):
            return (
                "the outline of the host is not modelled where an instance lands: a cut there may fall in a void and "
                "remove nothing"
            )
        return ""

    def _pattern_expectations(
        self, op: Any, before: StateSnapshot, instances: int, seed_faces: int, instance_boxes: list[BoundingBox] | None
    ) -> list[Expectation]:
        out = [
            _exp("instance_count", "count", instances),
            _exp("created_faces_count", "count", (instances - 1) * seed_faces),
        ]
        delta = before.seed_volume_delta
        if delta is None:
            out.append(_exp("volume_delta", "mm3", 0.0, asserted=False, note="seed volume delta unknown"))
            return out
        volume = f.pattern_volume(instances, delta)
        note = ""
        landing = self._landing_hazard(op, instance_boxes, delta)
        if landing:
            out[1] = _exp(out[1].name, "count", out[1].value, asserted=False, note=landing)
        if instance_boxes is None:
            note = "seed geometry unknown"
        elif delta < 0 and not all(
            f.bbox_contains(before.bbox, b, tol=self.thresholds.position_mm) for b in instance_boxes
        ):
            note = "instances exit body"
        elif delta > 0 and any(
            f.bboxes_overlap(a, b) for i, a in enumerate(instance_boxes) for b in instance_boxes[i + 1 :]
        ):
            note = "instances overlap"
        note = note or landing
        out.append(_exp("volume_delta", "mm3", volume, asserted=not note, note=note))
        return out

    def _op_linear_pattern(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        seed_faces, seed_box = self._seed_faces(op, request.features, adapter)
        count2 = request.count2 or 1
        instances = request.count * count2
        boxes: list[BoundingBox] | None = None
        if seed_box is not None:
            d1 = f.normalise(request.direction)
            d2 = f.normalise(request.direction2) if request.direction2 is not None else (0.0, 0.0, 0.0)
            s2 = request.spacing2_mm or 0.0
            boxes = [
                f.bbox_translate(seed_box, f.add(f.scale(d1, i * request.spacing_mm), f.scale(d2, j * s2)))
                for i in range(request.count)
                for j in range(count2)
            ]
        return self._pattern_expectations(op, before, instances, seed_faces, boxes)

    def _op_circular_pattern(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        seed_faces, seed_box = self._seed_faces(op, request.features, adapter)
        axis: Axis = request.axis
        count = request.count
        if request.equal_spacing:
            step = (
                request.angle_deg / count if abs(request.angle_deg - 360.0) <= 1e-9 else request.angle_deg / (count - 1)
            )
        else:
            step = request.angle_deg
        boxes: list[BoundingBox] | None = None
        if seed_box is not None:
            boxes = [
                f.bbox_transform(seed_box, f.rotation_about_point(axis.direction, i * step, axis.origin))
                for i in range(count)
            ]
        return self._pattern_expectations(op, before, count, seed_faces, boxes)

    # --- mirror ---------------------------------------------------------------------------------------------

    def _op_mirror(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A mirror is a pattern of two: the seed features and their reflection across the plane.

        The volume is the pattern's (``(2 - 1) * seed delta``), asserted under the pattern's own conditions plus one of
        its own: an instance that overlaps its reflection (a seed that straddles the plane, whether it adds material or
        removes it) fuses, so only the pattern's "no overlap" holds. Whether the reflected seed lands where the host is
        solid is the same unknown as for a linear pattern: only the host's box is checked, so a mirror onto an earlier
        edit of the host is not detected.

        The boxes are exact only across an axis-aligned plane, where the reflected seed box is the box of the reflected
        seed: there the document box must CONTAIN it when the seed adds material (a lower bound, like the sweep's).
        Across an oblique plane only ``instance_count`` and the (reported) volume remain. Two instances that meet ON
        the plane lose the faces they share there, so the face count is asserted only when they are apart."""
        plane: SketchFrame = request.plane
        aligned = f.plane_axis_aligned(plane.n)
        seed_faces, seed_box = self._seed_faces(op, request.features, adapter)
        reflected = f.bbox_reflect(seed_box, plane.origin, plane.n) if seed_box is not None and aligned else None
        boxes = [seed_box, reflected] if seed_box is not None and reflected is not None else None
        rows = {e.name: e for e in self._pattern_expectations(op, before, 2, seed_faces, boxes)}
        out = [rows["instance_count"]]
        faces = rows["created_faces_count"]
        if not aligned:
            faces = _exp(
                faces.name,
                "count",
                faces.value,
                asserted=False,
                note="oblique mirror plane: whether the reflected faces merge with the seed's is not known",
            )
        elif seed_box is None:
            faces = _exp(faces.name, "count", faces.value, asserted=False, note="seed geometry unknown")
        elif _meets(seed_box, reflected, self.thresholds.position_mm):  # type: ignore[arg-type]
            faces = _exp(
                faces.name,
                "count",
                faces.value,
                asserted=False,
                note="the seed meets its reflection on the mirror plane: the faces they share there merge",
            )
        out.append(faces)
        volume = rows["volume_delta"]
        if volume.kind is CheckKind.asserted and boxes is not None and f.bboxes_overlap(boxes[0], boxes[1]):
            volume = _exp(
                volume.name, "mm3", volume.value, asserted=False, note="instances overlap: the seed straddles the plane"
            )
        elif volume.kind is CheckKind.reported and not aligned and before.seed_volume_delta is not None:
            volume = _exp(
                volume.name,
                "mm3",
                volume.value,
                asserted=False,
                note="oblique mirror plane: the reflected instance's extent is not known",
            )
        out.append(volume)
        delta = before.seed_volume_delta
        if reflected is not None and delta is not None and delta > 0:
            out.append(
                _exp(
                    "bbox_contains",
                    "bbox",
                    reflected,
                    note="lower bound: the document box must contain the reflected seed's box",
                )
            )
        return out

    # --- chamfer / fillet -------------------------------------------------------------------------------

    def _edges(self, op: Any, request: Any, adapter: Adapter) -> list[EdgeInfo]:
        return list(adapter.edges_info(EdgesInfoRequest(operation_id=op.id, edges=tuple(request.edges))).edges)

    def _edge_is_square(self, edge: EdgeInfo) -> bool:
        return (
            edge.curve == CurveType.line
            and edge.convex is not None
            and edge.dihedral_deg is not None
            and abs(edge.dihedral_deg - 90.0) <= self.thresholds.angle_deg
        )

    def _share_vertex(self, edges: list[EdgeInfo]) -> bool:
        """Whether two of the edges meet at a point (the corner blends of a set make its volume and its face count differ
        from the sum of independent edges)."""
        ends = [(index, point) for index, e in enumerate(edges) for point in (e.start, e.end) if point is not None]
        tolerance = self.thresholds.position_mm
        return any(
            ends[i][0] != ends[j][0] and math.dist(ends[i][1], ends[j][1]) <= tolerance
            for i in range(len(ends))
            for j in range(i + 1, len(ends))
        )

    def _dress_up_basis(self, request: Any, edges: list[EdgeInfo]) -> tuple[bool, str, list[Expectation]]:
        """For a chamfer or fillet on several edges: whether the sum of independent edges is an exact prediction
        (``exact``), the note when it is not and the checks that still hold (the volume moves the way the edges say).
        Edges that meet at a corner blend there; ``skip_failing`` may leave some out. Either way the exact volume and
        the face count are only reported, never asserted."""
        adjacent = len(edges) > 1 and self._share_vertex(edges)
        skipping = bool(getattr(request, "skip_failing", False))
        if not adjacent and not skipping:
            return True, "", []
        note = "edges meet at a corner" if adjacent else "skip_failing may leave edges out"
        flags = [e.convex for e in edges]
        sign = -1 if all(flag is True for flag in flags) else (1 if all(flag is False for flag in flags) else 0)
        return False, note, [_exp("volume_sign", "count", sign)]

    def _op_chamfer(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        edges = self._edges(op, request, adapter)
        d1 = float(request.distance1_mm)
        d2 = float(request.distance2_mm) if request.distance2_mm is not None else d1
        square = all(self._edge_is_square(e) for e in edges)
        exact, why_not, bounds = self._dress_up_basis(request, edges)
        deltas: list[tuple[float, Vec3]] = []
        for edge in edges:
            convex = bool(edge.convex) if edge.convex is not None else True
            dv = f.chamfer_volume(d1, d2, edge.length_mm, convex)
            deltas.append((dv, self._wedge_centroid(op, edge, request.distance1_face, d1, d2, convex, adapter)))
        volume = sum(dv for dv, _ in deltas)
        out = [
            _exp(
                "volume_delta",
                "mm3",
                volume,
                asserted=square and exact,
                note="" if square and exact else (why_not or "edge not planar/planar at 90 degrees"),
            ),
            _exp("created_faces_count", "count", len(edges), asserted=exact, note=why_not),
            *bounds,
        ]
        if d1 != d2 and square and exact:
            centroid = f.centroid_after(before.mass.volume_mm3, before.mass.centroid, deltas)
            out.append(_exp("centroid_after", "vec", centroid))
        return out

    def _wedge_centroid(
        self, op: Any, edge: EdgeInfo, distance1_face: Any, d1: float, d2: float, convex: bool, adapter: Adapter
    ) -> Vec3:
        if edge.start is None or edge.end is None:
            return edge.bbox.center()
        faces = list(adapter.faces_info(FacesInfoRequest(operation_id=op.id, faces=tuple(edge.faces))).faces)
        if distance1_face is not None and faces[1].handle == distance1_face:
            faces.reverse()
        n1 = faces[0].normal or (0.0, 0.0, 0.0)
        n2 = faces[1].normal or (0.0, 0.0, 0.0)
        sign = -1.0 if convex else 1.0
        mid = f.scale(f.add(edge.start, edge.end), 0.5)
        return f.add(mid, f.add(f.scale(n2, sign * d1 / 3.0), f.scale(n1, sign * d2 / 3.0)))

    def _op_fillet(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        edges = self._edges(op, request, adapter)
        square = all(self._edge_is_square(e) for e in edges)
        exact, why_not, bounds = self._dress_up_basis(request, edges)
        volume = sum(
            f.fillet_volume(request.radius_mm, e.length_mm, bool(e.convex) if e.convex is not None else True)
            for e in edges
        )
        return [
            _exp(
                "volume_delta",
                "mm3",
                volume,
                asserted=square and exact,
                note="" if square and exact else (why_not or "edge not planar/planar at 90 degrees"),
            ),
            _exp("created_faces_count", "count", len(edges), asserted=exact, note=why_not),
            # a corner blend of meeting fillets is a sphere or a torus patch, not a cylinder of the radius
            _exp("fillet_radius", "mm", float(request.radius_mm), asserted=exact, note=why_not),
            *bounds,
        ]

    # --- concept: box / cylinder / booleans / moves ---------------------------------------------------------

    def _op_box(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        size: Vec3 = request.size
        transform: Transform = request.transform
        local = BoundingBox(min=(0.0, 0.0, 0.0), max=size)
        box = f.bbox_transform(local, transform)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = f.transform_shape(BoxShape(min=local.min, max=local.max), transform)
        return [
            _exp("volume_delta", "mm3", f.box_volume(size)),
            _exp("bbox_after", "bbox", _union_with_before(box, before)),
            _exp("new_faces_count", "count", 6),
        ]

    def _op_cylinder(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        axis: Axis = request.axis
        radius = request.diameter_mm / 2.0
        height = float(request.height_mm)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = CylinderShape(axis=axis, radius=radius, height=height)
        return [
            _exp("volume_delta", "mm3", f.cylinder_volume(radius, height)),
            _exp("bbox_after", "bbox", _union_with_before(f.cylinder_bbox(axis, radius, 0.0, height), before)),
            _exp("new_faces_count", "count", 3),
            _exp("cylinder_axis_direction", "vec", axis.direction),
            _exp("cylinder_radius", "mm", radius),
        ]

    def _shape_of(self, op_id: str) -> Shape | None:
        body = self._body_of_op.get(op_id)
        return None if body is None else self._shape_by_body.get(body)

    def _body_volume(self, op: Any, body: BodyHandle, adapter: Adapter) -> float:
        return adapter.mass_properties(MassRequest(operation_id=op.id, body=body)).volume_mm3

    def _body_bbox(self, op: Any, body: BodyHandle, adapter: Adapter) -> BoundingBox:
        return adapter.bounding_box(MassRequest(operation_id=op.id, body=body))

    def _boolean(self, kind: str, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        keep_tools = kind == "subtract" and bool(getattr(op, "keep_tools", False))
        target_id = _body_ref_id(op.target)
        tool_ids = [_body_ref_id(t) for t in op.tools]
        target_shape = self._shape_of(target_id)
        tool_shapes = [self._shape_of(t) for t in tool_ids]
        v_target = self._body_volume(op, request.target, adapter)
        v_tools = sum(self._body_volume(op, t, adapter) for t in request.tools)
        overlaps: list[float | None] = [self._overlap(target_id, t) for t in tool_ids]
        # the pairwise overlaps with the target only add up when the tools cannot overlap each other
        analytic = (
            all(v is not None for v in overlaps)
            and (kind != "intersect" or len(overlaps) == 1)
            and self._tools_disjoint(tool_ids)
        )
        v_overlap = sum(v or 0.0 for v in overlaps)
        # Document-level deltas (volume_delta = mass_after - mass_before of the whole document, and the
        # tool bodies are already counted in mass_before):
        #   union:     after = V_target + V_tools - V_overlap -> delta = -V_overlap
        #   subtract:  after = V_target - V_overlap           -> delta = -(V_tools + V_overlap)
        #   subtract, keep_tools: the tool bodies are NOT removed from the document, so their volume is
        #                         counted in both mass_before and mass_after -> delta = -V_overlap (same
        #                         shape as union: only the overlap disappears)
        #   intersect: after = V_overlap                      -> delta = V_overlap - V_target - V_tools
        if kind == "union" or keep_tools:
            volume = -v_overlap if analytic else 0.0
        elif kind == "subtract":
            volume = -(v_tools + v_overlap) if analytic else -2.0 * v_tools
        else:
            volume = v_overlap - v_target - v_tools if analytic else -v_tools
        note = "" if analytic else "intersection volume not analytic"
        out = [_exp("volume_delta", "mm3", volume, asserted=analytic, note=note)]
        target_box = self._body_bbox(op, request.target, adapter)
        if kind == "union":
            box = target_box
            for tool in request.tools:
                box = f.bbox_union(box, self._body_bbox(op, tool, adapter))
            out.append(_exp("bbox_after", "bbox", _union_with_before(box, before)))
        else:
            # The document box is observed, not the target's: a kept tool is still in it, and so is any body the op does
            # not name (an over-count of live bodies only widens the box, which stays sound). A subtract or an
            # intersect never reaches past the box the document had before.
            named = {self._body_of_op.get(i, i) for i in (target_id, *tool_ids)}
            others = bool(self._live_body_keys() - named) or before.untracked_bodies
            if others:
                limit, note = before.bbox, "other bodies stand in the document: the box of the whole document bounds it"
            elif keep_tools:
                limit, note = target_box, ""
                for tool in request.tools:
                    limit = f.bbox_union(limit, self._body_bbox(op, tool, adapter))
            else:
                limit, note = target_box, ""
            out.append(_exp("bbox_within", "bbox", limit, note=note))
        # register: the target body keeps its identity, its analytic shape is now unknown
        target_body = self._body_of_op.get(target_id, target_id)
        self._body_of_op[op.id] = target_body
        new_shape: Shape | None = None
        edited = any(self._edits.get(self._body_of_op.get(t, ""), ()) for t in (target_id, *tool_ids))
        if (
            kind == "intersect"
            and not edited
            and isinstance(target_shape, BoxShape)
            and len(tool_shapes) == 1
            and isinstance(tool_shapes[0], BoxShape)
        ):
            tool_box = tool_shapes[0]
            lo = tuple(max(a, b) for a, b in zip(target_shape.min, tool_box.min, strict=True))
            hi = tuple(min(a, b) for a, b in zip(target_shape.max, tool_box.max, strict=True))
            new_shape = BoxShape(min=lo, max=hi)  # type: ignore[arg-type]
        self._shape_by_body[target_body] = new_shape
        if not keep_tools:
            for tool_id in tool_ids:
                body = self._body_of_op.get(tool_id)
                if body is not None:
                    self._shape_by_body[body] = None
                    self._consumed.add(body)
        return out

    def _op_boolean_union(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        return self._boolean("union", op, request, before, adapter)

    def _op_boolean_subtract(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        return self._boolean("subtract", op, request, before, adapter)

    def _op_boolean_intersect(
        self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter
    ) -> list[Expectation]:
        return self._boolean("intersect", op, request, before, adapter)

    def _move(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        transform: Transform = request.transform
        body_box = self._body_bbox(op, request.body, adapter)
        # A box that equals the document's does not make the body alone in it (another body may stand inside it, as the
        # big piece of an oblique split does): the plan has to say there is no other live body too.
        single_body = self.thresholds.close_bbox(before.bbox, body_box) and self._alone(before)
        principal = f.is_principal_rotation(transform)
        asserted = single_body and principal
        note = (
            ""
            if asserted
            else ("rotation is not a multiple of 90 degrees" if single_body else "document has several bodies")
        )
        target_id = _body_ref_id(op.target)
        target_body = self._body_of_op.get(target_id, target_id)
        self._body_of_op[op.id] = target_body
        shape = self._shape_by_body.get(target_body)
        self._shape_by_body[target_body] = None if shape is None else f.transform_shape(shape, transform)
        self._edits[target_body] = [f.bbox_transform(box, transform) for box in self._edits.get(target_body, [])]
        return [
            _exp("volume_delta", "mm3", 0.0),
            _exp("bbox_after", "bbox", f.bbox_transform(body_box, transform), asserted=asserted, note=note),
        ]

    def _op_translate(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        return self._move(op, request, before, adapter)

    def _op_rotate(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        return self._move(op, request, before, adapter)

    def _op_copy_body(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A rigid duplicate: the document gains exactly the source's volume, and the NEW body (queried by
        body, since the document totals cannot tell a copy from a move) sits at the source's pose moved by
        the transform with the source's surface area, while the source stays where it was.

        A scaled copy (``scale`` s about ``scale_about``, then the rigid ``transform``) gains s^3 times the volume, has
        s^2 times the surface and its centroid is the source's, scaled and then moved; its box is the source's box
        scaled and then moved (exact for a principal rotation, as for an unscaled copy)."""
        transform: Transform = request.transform
        factor, about = float(request.scale), request.scale_about
        scaled = factor != 1.0
        source_mass = adapter.mass_properties(MassRequest(operation_id=op.id, body=request.source))
        source_box = self._body_bbox(op, request.source, adapter)
        if scaled:
            source_box = f.bbox_scale(source_box, factor, about)
        principal = f.is_principal_rotation(transform)
        source_id = _body_ref_id(op.source)
        source_shape = self._shape_of(source_id)
        if scaled and source_shape is not None:
            source_shape = f.scale_shape(source_shape, factor, about)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = None if source_shape is None else f.transform_shape(source_shape, transform)
        source_edits = self._edits.get(self._body_of_op.get(source_id, ""), [])
        if scaled:
            source_edits = [f.bbox_scale(box, factor, about) for box in source_edits]
        self._edits[op.id] = [f.bbox_transform(box, transform) for box in source_edits]
        centroid = f.scale_point(source_mass.centroid, factor, about) if scaled else source_mass.centroid
        out = [
            _exp("volume_delta", "mm3", source_mass.volume_mm3 * factor**3),
            _exp("copy_centroid", "vec", transform.apply(centroid)),
            _exp("source_centroid", "vec", source_mass.centroid),
            _exp(
                "copy_bbox",
                "bbox",
                f.bbox_transform(source_box, transform),
                asserted=principal,
                note="" if principal else "rotation is not a multiple of 90 degrees",
            ),
        ]
        if source_mass.surface_mm2 is not None:
            out.insert(2, _exp("copy_surface", "mm2", source_mass.surface_mm2 * factor**2))
        return out

    def _op_mirror_body(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A reflected duplicate: the document gains exactly the source's volume, and the NEW body (queried by body)
        has the source's surface area and sits at the source's centroid reflected across the plane, while the source
        stays where it was. Its box is the source's box reflected only across an axis-aligned plane; across an oblique
        one it is REPORTED (an enclosing box is not the body's box)."""
        plane: SketchFrame = request.plane
        aligned = f.plane_axis_aligned(plane.n)
        source_mass = adapter.mass_properties(MassRequest(operation_id=op.id, body=request.source))
        source_box = self._body_bbox(op, request.source, adapter)
        source_id = _body_ref_id(op.source)
        source_shape = self._shape_of(source_id)
        self._body_of_op[op.id] = op.id
        self._shape_by_body[op.id] = (
            None if source_shape is None else f.reflect_shape(source_shape, plane.origin, plane.n)
        )
        source_edits = self._edits.get(self._body_of_op.get(source_id, ""), [])
        self._edits[op.id] = [f.bbox_reflect(box, plane.origin, plane.n) for box in source_edits]
        out = [
            _exp("volume_delta", "mm3", source_mass.volume_mm3),
            _exp("copy_centroid", "vec", f.reflect_point(source_mass.centroid, plane.origin, plane.n)),
            _exp("source_centroid", "vec", source_mass.centroid),
            _exp(
                "copy_bbox",
                "bbox",
                f.bbox_reflect(source_box, plane.origin, plane.n),
                asserted=aligned,
                note="" if aligned else "oblique mirror plane: the reflected body's box is not the enclosing box",
            ),
        ]
        if source_mass.surface_mm2 is not None:
            out.insert(2, _exp("copy_surface", "mm2", source_mass.surface_mm2))
        return out

    def _kept_box_volume(self, shape: BoxShape, plane: SketchFrame, side: str) -> float | None:
        """Volume of the part of an axis-aligned box on ``side`` of an axis-aligned plane, ``None`` for any other plane
        (the part would not be a box)."""
        if not f.plane_axis_aligned(plane.n):
            return None
        normal = f.normalise(plane.n)
        axis = max(range(3), key=lambda i: abs(normal[i]))
        low, high = shape.min[axis], shape.max[axis]
        cut = min(max(plane.origin[axis], low), high)
        positive = (high - cut) if normal[axis] > 0 else (cut - low)
        length = positive if side == "positive" else (high - low) - positive
        return f.box_volume(f.sub(shape.max, shape.min)) * length / (high - low)

    def _op_split(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A cut loses nothing and invents nothing: the pieces add up to the source (volume and volume-weighted
        centroid, read from the source BEFORE the op), each one lies on the side of the plane it claims, and the
        document volume does not move. That holds for any plane and any source, so it is asserted.

        What a ``keep`` positive or negative leaves is one piece, whose volume is known only for an untouched tracked
        box and an axis-aligned plane (an earlier edit changes the volume the closed form describes): otherwise it is
        REPORTED. The source is consumed and neither piece keeps a tracked shape; the positive piece is registered under
        the op id and the negative one under ``<op id>#negative`` (a split that keeps one piece registers it under the
        op id)."""
        plane: SketchFrame = request.plane
        keep: str = request.keep
        source_id = _body_ref_id(op.source)
        source_key = self._body_of_op.get(source_id)
        source_mass = adapter.mass_properties(MassRequest(operation_id=op.id, body=request.source))
        host = self._host_shape([op.source])
        sides_row = _exp("split_sides", "bool", True)
        out: list[Expectation]
        if keep == "both":
            out = [
                _exp("volume_delta", "mm3", 0.0),
                _exp("split_volume_sum", "mm3", source_mass.volume_mm3),
                _exp("split_centroid", "vec", source_mass.centroid),
                sides_row,
            ]
        else:
            kept = self._kept_box_volume(host, plane, keep) if isinstance(host, BoxShape) else None
            if kept is not None:
                volume = _exp("volume_delta", "mm3", kept - source_mass.volume_mm3)
            else:
                volume = _exp(
                    "volume_delta",
                    "mm3",
                    0.0,
                    asserted=False,
                    note=(
                        "the kept piece's volume is known only for an untouched box cut by an axis-aligned plane; "
                        "the observed change is the removed piece's volume"
                    ),
                )
            out = [volume, sides_row]
        keys = [op.id] if keep != "both" else [op.id, f"{op.id}#negative"]
        source_edits = list(self._edits.get(source_key or "", ()))
        for key in keys:
            self._body_of_op[key] = key
            self._shape_by_body[key] = None
            self._edits[key] = list(source_edits)  # an edit of the source may lie on either piece
        self._split_pieces[op.id] = tuple(keys)
        # the outline of the base pad and the regions of the earlier features describe the host as it was: the pieces
        # start with none, and every earlier region may meet anything (a body that is cut is no longer that host)
        self._pad_host = None
        self._feature_regions = dict.fromkeys(self._feature_regions)
        if source_key is not None:
            self._shape_by_body[source_key] = None
            self._consumed.add(source_key)
        return out

    # --- assembly ---------------------------------------------------------------------------------------------

    def _op_insert_component(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        current = adapter.active_document(Empty(operation_id=op.id))
        adapter.set_active_document(SetActiveDocumentRequest(operation_id=op.id, document=request.part))
        try:
            part_mass = adapter.mass_properties(MassRequest(operation_id=op.id, surface=False, centroid=False))
            # the quick box, as the component's own is (a part brought in from a file answers it at no cost)
            part_box = adapter.bounding_box(MassRequest(operation_id=op.id, exact_box=False))
        finally:
            adapter.set_active_document(SetActiveDocumentRequest(operation_id=op.id, document=current.handle))
        transform: Transform = request.transform
        principal = f.is_principal_rotation(transform)
        self._part_volumes[op.id] = part_mass.volume_mm3
        return [
            _exp("volume_delta", "mm3", part_mass.volume_mm3),
            _exp(
                "component_bbox",
                "bbox",
                f.bbox_transform(part_box, transform),
                asserted=principal,
                note="" if principal else "rotation is not a multiple of 90 degrees",
            ),
            _exp("component_transform", "transform", transform),
        ]

    def _op_fix_component(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        transform = adapter.component_transform(ComponentRequest(operation_id=op.id, component=request.component))
        return [_exp("volume_delta", "mm3", 0.0), _exp("component_transform", "transform", transform)]

    def _op_place_component(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        return [_exp("volume_delta", "mm3", 0.0), _exp("component_transform", "transform", request.transform)]

    def _op_add_joint(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """A joint is made at the pose the component has: nothing moves."""
        transform = adapter.component_transform(ComponentRequest(operation_id=op.id, component=request.b))
        return [_exp("volume_delta", "mm3", 0.0), _exp("component_transform", "transform", transform)]

    def _op_set_joint(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        """The component ends where the joint's own motion from its present value takes it (computed here from the
        joint's axis, not read back from the adapter)."""
        joint = adapter.joint_info(JointRequest(operation_id=op.id, joint=request.joint))
        now = adapter.component_transform(ComponentRequest(operation_id=op.id, component=joint.b))
        moved = joint_delta(joint.joint_type, joint.axis, request.value - joint.value).compose(now)
        return [_exp("volume_delta", "mm3", 0.0), _exp("component_transform", "transform", moved)]

    def _mate_faces(self, op: Any, request: Any, adapter: Adapter) -> tuple[FaceInfo, FaceInfo]:
        a = adapter.face_info(FaceRequest(operation_id=op.id, face=request.a))
        b = adapter.face_info(FaceRequest(operation_id=op.id, face=request.b))
        return a, b

    def _component_bbox_expectation(self, before: StateSnapshot, translation: Vec3 | None, note: str) -> Expectation:
        base = before.component_bbox_before
        if base is None:
            return _exp(
                "component_bbox", "bbox", before.bbox, asserted=False, note="component bbox before the mate unknown"
            )
        if translation is None:
            return _exp("component_bbox", "bbox", base, asserted=False, note=note)
        return _exp("component_bbox", "bbox", f.bbox_translate(base, translation))

    def _op_mate_concentric(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        a, b = self._mate_faces(op, request, adapter)
        translation: Vec3 | None = None
        note = "faces have no parallel axes"
        if (
            a.axis is not None
            and b.axis is not None
            and self.thresholds.parallel(a.axis.direction, b.axis.direction, either_sense=True)
        ):
            translation = f.perpendicular_part(f.sub(a.axis.origin, b.axis.origin), a.axis.direction)
        return [_exp("volume_delta", "mm3", 0.0), self._component_bbox_expectation(before, translation, note)]

    def _planar_mate(
        self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter, distance: float
    ) -> list[Expectation]:
        a, b = self._mate_faces(op, request, adapter)
        translation: Vec3 | None = None
        if a.normal is not None:
            along = f.dot(f.sub(a.center, b.center), a.normal) + distance
            translation = f.scale(a.normal, along)
        return [
            _exp("volume_delta", "mm3", 0.0),
            self._component_bbox_expectation(before, translation, "face a is not planar"),
        ]

    def _op_mate_coincident(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        return self._planar_mate(op, request, before, adapter, 0.0)

    def _op_mate_distance(self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter) -> list[Expectation]:
        side = 1.0 if request.side == Side.positive else -1.0
        return self._planar_mate(op, request, before, adapter, side * float(request.distance_mm))

    def _op_check_interference(
        self, op: Any, request: Any, before: StateSnapshot, adapter: Adapter
    ) -> list[Expectation]:
        expect_zero = bool(op.expect_zero)
        known = bool(self._part_volumes)
        return [
            _exp("volume_delta", "mm3", 0.0),
            _exp(
                "interference_total",
                "mm3",
                0.0,
                asserted=expect_zero,
                note="" if expect_zero else "expect_zero is false",
            ),
            _exp(
                "assembly_volume",
                "mm3",
                sum(self._part_volumes.values()),
                asserted=known,
                note="" if known else "no insert_component seen by this verifier",
            ),
        ]
