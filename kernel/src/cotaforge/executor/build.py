"""Request building: IR op + context + resolver -> resolved adapter request."""

import math
from collections.abc import Callable, Mapping
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from cotaforge.contract import (
    AddJointRequest,
    Axis,
    AxisName,
    BaseFlangeRequest,
    BooleanKind,
    BooleanRequest,
    BoxRequest,
    ChamferRequest,
    CircularPatternRequest,
    ComponentHandle,
    CopyBodyRequest,
    CutRequest,
    CylinderRequest,
    DatumAxisRequest,
    DatumPlaneRequest,
    DatumPlaneTarget,
    DraftRequest,
    DrillPoint,
    EdgeFlangeRequest,
    EdgeInfo,
    EndCondition,
    ExtensionRequest,
    ExtrudeRequest,
    FaceHandle,
    FeatureHandle,
    FilletRequest,
    FixComponentRequest,
    HoleCounterbore,
    HoleCountersink,
    HoleRequest,
    HoleStandard,
    HoleThread,
    InsertComponentRequest,
    InterferenceRequest,
    JointType,
    LinearPatternRequest,
    LineEntity,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    MirrorBodyRequest,
    MirrorRequest,
    MoveBodyRequest,
    OperationSpec,
    PlaceComponentRequest,
    PlaneName,
    Request,
    RevolveRequest,
    SetJointRequest,
    ShellRequest,
    SketchFrame,
    SketchRequest,
    SplitRequest,
    SurfaceType,
    SweepRequest,
    Transform,
    UnfoldRequest,
    Vec3,
    frame_for_face,
    plain_op_name,
    sketch_profiles,
    slide_onto_face,
)
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir.extension import FeatureStep, SketchStep, is_extension_op
from cotaforge.ir.intent import NON_FEATURE_OP_NAMES
from cotaforge.ir.refs import AxisRef, BodyRef, ComponentRef, EdgeRef, EdgeSet, FaceRef, PlaneRef, VertexRef
from cotaforge.verification import datum
from cotaforge.verification import formulas as f
from cotaforge.verification import sweep as sweep_math

if TYPE_CHECKING:
    from cotaforge.ir.ops_primitives import OperationBase

Builder = Callable[[Any, ExecutionContext, Resolver], Request]

_AXIS_VECTORS: dict[AxisName, Vec3] = {
    AxisName.X: (1.0, 0.0, 0.0),
    AxisName.Y: (0.0, 1.0, 0.0),
    AxisName.Z: (0.0, 0.0, 1.0),
}

# Ops whose adapter method returns something other than an OperationResult with a feature.
NON_FEATURE_OPS = NON_FEATURE_OP_NAMES


def axis_vector(direction: AxisName | Vec3) -> Vec3:
    if isinstance(direction, AxisName):
        return _AXIS_VECTORS[direction]
    return tuple(float(x) for x in direction)  # type: ignore[return-value]


def placement_transform(placement: Any) -> Transform:
    """``Placement(origin, axis, angle_deg)`` -> rotation about the world origin, then translation."""
    return Transform.from_axis_angle(axis_vector(placement.axis), placement.angle_deg, tuple(placement.origin))


def _missing(op: Any, path: str, what: str) -> ResolutionError:
    return ResolutionError(op_id=op.id, path=path, ref=getattr(op, path, None), candidates=0, message=what)


def _sketch_handle(op: Any, ctx: ExecutionContext) -> Any:
    handle = ctx.sketch_by_op.get(op.sketch)
    if handle is None:
        raise _missing(op, "sketch", f"sketch operation {op.sketch!r} has not produced a sketch")
    return handle


def _frame(op: Any, ctx: ExecutionContext) -> SketchFrame:
    frame = ctx.sketch_frame_by_op.get(op.sketch)
    if frame is None:
        raise _missing(op, "sketch", f"no sketch frame recorded for {op.sketch!r}")
    return frame


def _feature(op: Any, ctx: ExecutionContext, op_id: str, path: str) -> FeatureHandle:
    feature = ctx.feature_by_op.get(op_id)
    if feature is None:
        raise _missing(op, path, f"operation {op_id!r} produced no feature")
    return feature


def _component(op: Any, ctx: ExecutionContext, op_id: str, path: str) -> ComponentHandle:
    component = ctx.component_by_op.get(op_id)
    if component is None:
        raise _missing(op, path, f"operation {op_id!r} produced no component")
    return component


# --- builders -----------------------------------------------------------------------------------------


def build_sketch(op: Any, ctx: ExecutionContext, resolver: Resolver) -> SketchRequest:
    target = resolver.plane_or_face(op.on, op.id, "on")
    on: PlaneName | FaceHandle | DatumPlaneTarget
    origin: Vec3 | None = None
    if isinstance(target, PlaneName | DatumPlaneTarget):
        on = target
    else:
        on = target.handle
        origin = _held_origin(ctx, op.id, target)
    return SketchRequest(
        operation_id=op.id,
        on=on,
        entities=op.entities,
        constraints=op.constraints,
        named_dimensions=op.named_dimensions,
        origin=origin,
    )


def _held_origin(ctx: ExecutionContext, op_id: str, face: Any) -> Vec3 | None:
    """Where the frame of ``op_id`` on ``face`` has its origin when a rebuild keeps the place the op had: the origin it
    had, slid onto the plane of the face. ``None``: nothing is held for it, or the face no longer has that
    orientation (only the face can say where the op goes then)."""
    held = ctx.held_places.get(op_id)
    if held is None or face.normal is None:
        return None
    if sum(a * b for a, b in zip(held.n, face.normal, strict=True)) < 1.0 - 1e-9:
        return None
    return slide_onto_face(held.origin, face)


def _datum_error(op: Any, exc: datum.DatumError) -> ResolutionError:
    return ResolutionError(op_id=op.id, path="define", ref=op.define, candidates=0, message=str(exc))


def build_ref_plane(op: Any, ctx: ExecutionContext, resolver: Resolver) -> DatumPlaneRequest:
    """Compute the datum plane's pose here (``verification.datum``); the adapter only realises it."""
    d = op.define
    try:
        if d.how == "offset":
            frame = datum.plane_offset(resolver.plane_frame(d.base, op.id, "define.base"), d.distance_mm)
        elif d.how == "angle":
            base = resolver.plane_frame(d.base, op.id, "define.base")
            frame = datum.plane_angle(base, resolver.axis(d.about, op.id, "define.about"), d.angle_deg)
        elif d.how == "three_points":
            frame = datum.plane_three_points(*d.points)
        elif d.how == "normal_to":
            frame = datum.plane_normal_to(resolver.axis(d.axis, op.id, "define.axis"), d.at_mm)
        else:
            assert d.how == "midplane"
            frame = datum.plane_midplane(
                resolver.plane_frame(d.a, op.id, "define.a"), resolver.plane_frame(d.b, op.id, "define.b")
            )
    except datum.DatumError as exc:
        raise _datum_error(op, exc) from exc
    return DatumPlaneRequest(operation_id=op.id, how=d.how, frame=frame)


def build_ref_axis(op: Any, ctx: ExecutionContext, resolver: Resolver) -> DatumAxisRequest:
    d = op.define
    try:
        if d.how == "two_points":
            axis = datum.axis_two_points(*d.points)
        elif d.how == "point_direction":
            axis = datum.axis_point_direction(d.point, axis_vector(d.direction))
        elif d.how == "entity":
            axis = resolver.entity_axis(d.entity, op.id, "define.entity")
        else:
            assert d.how == "plane_intersection"
            axis = datum.axis_plane_intersection(
                resolver.plane_frame(d.a, op.id, "define.a"), resolver.plane_frame(d.b, op.id, "define.b")
            )
    except datum.DatumError as exc:
        raise _datum_error(op, exc) from exc
    return DatumAxisRequest(operation_id=op.id, how=d.how, axis=axis)


def build_extrude(op: Any, ctx: ExecutionContext, resolver: Resolver) -> ExtrudeRequest:
    return ExtrudeRequest(
        operation_id=op.id,
        sketch=_sketch_handle(op, ctx),
        depth_mm=op.depth_mm,
        direction=op.direction,
        both_directions=op.both_directions,
        depth2_mm=op.depth2_mm,
        merge=op.merge,
    )


def build_cut(op: Any, ctx: ExecutionContext, resolver: Resolver) -> CutRequest:
    scope = op.scope
    return CutRequest(
        operation_id=op.id,
        sketch=_sketch_handle(op, ctx),
        end_condition=op.end_condition,
        depth_mm=op.depth_mm,
        direction=op.direction,
        both_directions=op.both_directions,
        depth2_mm=op.depth2_mm,
        scope=None if scope is None else tuple(resolver.body(b, op.id, f"scope[{i}]") for i, b in enumerate(scope)),
    )


def build_revolve(op: Any, ctx: ExecutionContext, resolver: Resolver) -> RevolveRequest:
    sketch = _sketch_handle(op, ctx)
    frame = _frame(op, ctx)
    if op.axis == "u":
        axis = Axis(origin=frame.origin, direction=frame.u)
    elif op.axis == "v":
        axis = Axis(origin=frame.origin, direction=frame.v)
    else:
        sketch_op = _sketch_op_entities(op, ctx)
        line = next((e for e in sketch_op if isinstance(e, LineEntity) and e.name == op.axis), None)
        if line is None:
            raise _missing(op, "axis", f"revolve axis entity {op.axis!r} is not a line of sketch {op.sketch!r}")
        start = frame.to_world(*line.start)
        end = frame.to_world(*line.end)
        axis = Axis(origin=start, direction=f.sub(end, start))
    return RevolveRequest(operation_id=op.id, sketch=sketch, axis=axis, angle_deg=op.angle_deg)


def build_sweep(op: Any, ctx: ExecutionContext, resolver: Resolver) -> SweepRequest:
    """Refuse a sweep the kernel cannot build cleanly BEFORE the adapter is called: one closed profile, plane
    normal to the first segment, centroid on the path start, corners that fit the section."""
    sketch = _sketch_handle(op, ctx)
    frame = _frame(op, ctx)
    try:
        profiles = sketch_profiles(_sketch_op_entities(op, ctx))
    except ValueError as exc:
        raise _missing(op, "sketch", f"sketch {op.sketch!r} has no closed profile: {exc}") from exc
    if len(profiles) != 1:
        raise _missing(
            op,
            "sketch",
            f"sweep needs exactly ONE closed profile; sketch {op.sketch!r} has {len(profiles)} "
            "(a ring section is two profiles: not supported in v1)",
        )
    profile = profiles[0]
    points = tuple((float(p[0]), float(p[1]), float(p[2])) for p in op.path.points)
    radius = op.path.corner_radius_mm
    try:
        analysis = sweep_math.analyse_path(points, radius)
        sweep_math.check_placement(
            frame,
            points,
            frame.to_world(*profile.centroid),
            resolver.thresholds,
            reach=sweep_math.section_reach(profile),
        )
        sweep_math.check_clearance(analysis, profile, frame)
    except sweep_math.SweepError as exc:
        raise ResolutionError(op_id=op.id, path="path", ref=op.path, candidates=0, message=str(exc)) from exc
    return SweepRequest(operation_id=op.id, sketch=sketch, path=points, corner_radius_mm=radius, merge=op.merge)


def _sketch_op_entities(op: Any, ctx: ExecutionContext) -> tuple:
    entities = ctx.sketch_entities_by_op.get(op.sketch)
    if entities is None:
        raise _missing(op, "sketch", f"no sketch entities recorded for {op.sketch!r}")
    return entities


def _hole_position_from_point(op: Any, face: Any, resolver: Resolver) -> tuple[float, float]:
    """``(u, v)`` of the world point ``op.at`` projected onto the planar ``face`` (along its normal)."""
    if face.normal is None:
        raise ResolutionError(
            op_id=op.id,
            path="at",
            ref=op.at,
            candidates=1,
            message=(
                f"at (a world point) needs a planar face, but {face.handle.id} is {face.surface.value}; "
                "on the outer wall of a tube or shaft use position = [axial_mm, angle_deg]"
            ),
        )
    frame = frame_for_face(face)
    u, v, _ = frame.to_local(tuple(float(c) for c in op.at))
    point = frame.to_world(u, v)
    box = face.bbox
    excess = [max(lo - p, p - hi, 0.0) for p, lo, hi in zip(point, box.min, box.max, strict=True)]
    outside = math.sqrt(sum(e * e for e in excess))
    if outside > resolver.thresholds.position_mm:
        raise ResolutionError(
            op_id=op.id,
            path="at",
            ref=op.at,
            candidates=1,
            message=(
                f"at {list(op.at)} lands at ({point[0]:.3f}, {point[1]:.3f}, {point[2]:.3f}) on face {face.handle.id}, "
                f"{outside:.3f} mm outside it (the face spans ({box.min[0]:.3f}, {box.min[1]:.3f}, {box.min[2]:.3f}) "
                f"to ({box.max[0]:.3f}, {box.max[1]:.3f}, {box.max[2]:.3f}))"
            ),
        )
    return (u, v)


def build_hole(op: Any, ctx: ExecutionContext, resolver: Resolver) -> HoleRequest:
    face = resolver.face(op.face, op.id, "face")
    if face.surface not in (SurfaceType.plane, SurfaceType.cylinder):
        raise ResolutionError(
            op_id=op.id,
            path="face",
            ref=op.face,
            candidates=1,
            message=(
                f"a hole needs a planar or a cylindrical face, but {face.handle.id} is {face.surface.value}; "
                "pick a flat face (position = [u, v]) or the outer wall of a tube or shaft "
                "(position = [axial_mm, angle_deg])"
            ),
        )
    thread = HoleThread(size=op.size, depth_mm=op.thread_depth_mm) if op.standard is HoleStandard.iso_tapped else None
    position = op.position if op.at is None else _hole_position_from_point(op, face, resolver)
    origin = _held_origin(ctx, op.id, face) if op.at is None and face.surface is SurfaceType.plane else None
    up_to = resolver.face(op.up_to_face, op.id, "up_to_face").handle if op.up_to_face is not None else None
    # FreeCAD's default point (angled) applies to a hole with a bottom on a flat face: a through hole has none, and a
    # hole across a tube wall is a plain cylinder; only an explicit request for a point there is passed on (and refused)
    drill_point = op.drill_point
    if "drill_point" not in op.model_fields_set and (
        op.end_condition is EndCondition.through_all or face.surface is SurfaceType.cylinder
    ):
        drill_point = DrillPoint.flat
    return HoleRequest(
        operation_id=op.id,
        face=face.handle,
        position=position,
        origin=origin,
        standard=op.standard,
        size=op.size,
        diameter_mm=op.diameter_mm,
        end_condition=op.end_condition,
        depth_mm=op.depth_mm,
        thread=thread,
        fit=op.fit,
        counterbore=(
            HoleCounterbore(diameter_mm=op.counterbore.diameter_mm, depth_mm=op.counterbore.depth_mm)
            if op.counterbore is not None
            else None
        ),
        countersink=(
            HoleCountersink(diameter_mm=op.countersink.diameter_mm, angle_deg=op.countersink.angle_deg)
            if op.countersink is not None
            else None
        ),
        drill_point=drill_point,
        drill_point_angle_deg=op.drill_point_angle_deg,
        up_to_face=up_to,
    )


def _features(op: Any, ctx: ExecutionContext) -> tuple[FeatureHandle, ...]:
    return tuple(_feature(op, ctx, op_id, f"features[{i}]") for i, op_id in enumerate(op.features))


def _pattern_direction(value: Any, op: Any, resolver: Resolver, path: str) -> Vec3:
    if isinstance(value, AxisRef):
        return resolver.axis(value, op.id, path).direction
    return axis_vector(value)


def build_linear_pattern(op: Any, ctx: ExecutionContext, resolver: Resolver) -> LinearPatternRequest:
    return LinearPatternRequest(
        operation_id=op.id,
        features=_features(op, ctx),
        direction=_pattern_direction(op.direction, op, resolver, "direction"),
        spacing_mm=op.spacing_mm,
        count=op.count,
        direction2=_pattern_direction(op.direction2, op, resolver, "direction2") if op.direction2 is not None else None,
        spacing2_mm=op.spacing2_mm,
        count2=op.count2,
    )


def build_circular_pattern(op: Any, ctx: ExecutionContext, resolver: Resolver) -> CircularPatternRequest:
    # a rotation about a line does not depend on which of its points describes it, so the datum-op rules apply:
    # literal axes as given; datum axes, faces and edges (a straight edge included) through the resolver
    axis = op.axis if isinstance(op.axis, Axis) else resolver.axis(op.axis, op.id, "axis")
    return CircularPatternRequest(
        operation_id=op.id,
        features=_features(op, ctx),
        axis=axis,
        count=op.count,
        angle_deg=op.angle_deg,
        equal_spacing=op.equal_spacing,
    )


def _dress_up_edges(op: Any, resolver: Resolver) -> tuple[EdgeInfo, ...]:
    """The edges of a chamfer or fillet: single edge refs (exactly one edge each) and edge sets (every match), in the
    order given, each edge once."""
    found: dict[str, EdgeInfo] = {}
    for index, ref in enumerate(op.edges):
        path = f"edges[{index}]"
        resolved = (
            resolver.edge_set(ref, op.id, path) if isinstance(ref, EdgeSet) else [resolver.edge(ref, op.id, path)]
        )
        for edge in resolved:
            found.setdefault(edge.handle.id, edge)
    return tuple(found.values())


def build_chamfer(op: Any, ctx: ExecutionContext, resolver: Resolver) -> ChamferRequest:
    edges = _dress_up_edges(op, resolver)
    if op.distance1_on is not None:
        distance1_face: FaceHandle | None = resolver.face(op.distance1_on, op.id, "distance1_on").handle
    else:
        # ChamferOperation validates that distance2_mm requires distance1_on, so a validated op can
        # never reach this branch with distance2_mm set and distance1_on None.
        distance1_face = None
    return ChamferRequest(
        operation_id=op.id,
        edges=tuple(e.handle for e in edges),
        distance1_mm=op.distance1_mm,
        distance2_mm=op.distance2_mm,
        distance1_face=distance1_face,
        skip_failing=op.skip_failing,
    )


def build_fillet(op: Any, ctx: ExecutionContext, resolver: Resolver) -> FilletRequest:
    edges = _dress_up_edges(op, resolver)
    return FilletRequest(
        operation_id=op.id, edges=tuple(e.handle for e in edges), radius_mm=op.radius_mm, skip_failing=op.skip_failing
    )


def build_shell(op: Any, ctx: ExecutionContext, resolver: Resolver) -> ShellRequest:
    """Resolve the faces to remove and refuse, before the adapter is called, a list that cannot name ONE body: a
    face twice (the kernel would take it as a mistake of the author) or faces of different bodies. Whether the
    thickness fits the part is for the adapter to say (the kernel judges it)."""
    faces = tuple(resolver.face(ref, op.id, f"faces[{i}]") for i, ref in enumerate(op.faces))
    seen: set[str] = set()
    for index, face in enumerate(faces):
        if face.handle.id in seen:
            raise _missing(
                op, f"faces[{index}]", f"shell lists face {face.handle.id} twice: name each removed face once"
            )
        seen.add(face.handle.id)
        if face.body != faces[0].body:
            raise _missing(
                op,
                f"faces[{index}]",
                f"shell faces belong to different bodies ({faces[0].body.id} and {face.body.id}): "
                "a shell hollows one body, so pick the removed faces from the same one",
            )
    return ShellRequest(
        operation_id=op.id,
        faces=tuple(face.handle for face in faces),
        thickness_mm=op.thickness_mm,
        outward=op.direction == "outward",
        join=op.join,
    )


_PULL_TOLERANCE_DEG = 1.0
"""How far a draft's ``pull`` may stray from the neutral plane's normal: a hand-typed vector is rarely exact, and the
request carries the snapped, exactly perpendicular one."""


_FACING_PULL = math.cos(math.radians(_PULL_TOLERANCE_DEG))


def _g(value: float) -> str:
    return f"{value + 0.0:.6g}"


def build_draft(op: Any, ctx: ExecutionContext, resolver: Resolver) -> DraftRequest:
    """Resolve the drafted faces (the same refusals as ``build_shell``: one body, each face once) and the neutral plane,
    and check ``pull`` against it: the kernel needs the pull along the neutral plane's normal (its own pull-direction
    handle is unusable), so a pull that is not within a degree of it, either sense, is refused with the fix. The request
    carries the neutral normal pointing the way the author's pull does, so every adapter sees an exact pull."""
    faces = tuple(resolver.face(ref, op.id, f"faces[{i}]") for i, ref in enumerate(op.faces))
    seen: set[str] = set()
    for index, face in enumerate(faces):
        if face.handle.id in seen:
            raise _missing(
                op, f"faces[{index}]", f"draft lists face {face.handle.id} twice: name each drafted face once"
            )
        seen.add(face.handle.id)
        if face.body != faces[0].body:
            raise _missing(
                op,
                f"faces[{index}]",
                f"draft faces belong to different bodies ({faces[0].body.id} and {face.body.id}): "
                "a draft tapers one body, so pick the faces from the same one",
            )
    neutral = resolver.plane_frame(op.neutral, op.id, "neutral")
    pull = f.normalise(axis_vector(op.pull))
    normal = f.normalise(neutral.n)
    along = f.dot(pull, normal)
    off = math.degrees(math.acos(min(1.0, abs(along))))
    if off > _PULL_TOLERANCE_DEG:
        raise _missing(
            op,
            "pull",
            f"pull must be perpendicular to the neutral plane: pull ({_g(pull[0])}, {_g(pull[1])}, {_g(pull[2])}) is "
            f"{_g(off)} deg off the plane's normal ({_g(normal[0])}, {_g(normal[1])}, {_g(normal[2])}); pull along "
            "that normal (either sign) or choose a neutral plane perpendicular to the pull",
        )
    for index, face in enumerate(faces):
        if face.normal is not None and abs(f.dot(f.normalise(face.normal), pull)) >= _FACING_PULL:
            raise _missing(
                op,
                f"faces[{index}]",
                f"draft face {face.handle.id} faces the pull (its normal is within {_PULL_TOLERANCE_DEG:g} deg of it): "
                "a face facing the pull, or lying in the neutral plane, cannot be drafted (the kernel either fails "
                "or silently leaves it alone); name the walls that run along the pull instead",
            )
    return DraftRequest(
        operation_id=op.id,
        faces=tuple(face.handle for face in faces),
        neutral=neutral,
        pull=f.scale(normal, 1.0 if along >= 0 else -1.0),
        angle_deg=op.angle_deg,
    )


def build_mirror(op: Any, ctx: ExecutionContext, resolver: Resolver) -> MirrorRequest:
    """The seeds as feature handles and the mirror plane as a frame (a base plane, a datum plane or a planar face; only
    its origin and normal matter). Whether a seed can be mirrored is for the adapter to say."""
    return MirrorRequest(
        operation_id=op.id,
        features=_features(op, ctx),
        plane=resolver.plane_frame(op.plane, op.id, "plane"),
    )


def build_mirror_body(op: Any, ctx: ExecutionContext, resolver: Resolver) -> MirrorBodyRequest:
    return MirrorBodyRequest(
        operation_id=op.id,
        source=resolver.body(op.source, op.id, "source"),
        plane=resolver.plane_frame(op.plane, op.id, "plane"),
    )


def build_box(op: Any, ctx: ExecutionContext, resolver: Resolver) -> BoxRequest:
    return BoxRequest(operation_id=op.id, size=op.size, transform=placement_transform(op.placement))


def build_cylinder(op: Any, ctx: ExecutionContext, resolver: Resolver) -> CylinderRequest:
    axis = Axis(origin=op.origin, direction=axis_vector(op.axis))
    return CylinderRequest(operation_id=op.id, diameter_mm=op.diameter_mm, height_mm=op.height_mm, axis=axis)


def _build_boolean(kind: BooleanKind) -> Builder:
    def build(op: Any, ctx: ExecutionContext, resolver: Resolver) -> BooleanRequest:
        target = resolver.body(op.target, op.id, "target")
        tools = tuple(resolver.body(t, op.id, f"tools[{i}]") for i, t in enumerate(op.tools))
        keep_tools = bool(getattr(op, "keep_tools", False))
        return BooleanRequest(operation_id=op.id, kind=kind, target=target, tools=tools, keep_tools=keep_tools)

    return build


def build_translate(op: Any, ctx: ExecutionContext, resolver: Resolver) -> MoveBodyRequest:
    body = resolver.body(op.target, op.id, "target")
    return MoveBodyRequest(operation_id=op.id, body=body, transform=Transform(origin=tuple(op.offset)))


def build_rotate(op: Any, ctx: ExecutionContext, resolver: Resolver) -> MoveBodyRequest:
    body = resolver.body(op.target, op.id, "target")
    transform = f.rotation_about_point(axis_vector(op.axis), op.angle_deg, tuple(op.about))
    return MoveBodyRequest(operation_id=op.id, body=body, transform=transform)


def build_copy_body(op: Any, ctx: ExecutionContext, resolver: Resolver) -> CopyBodyRequest:
    """Rotate the copy about ``rotate.about`` first, then translate it by ``offset``."""
    source = resolver.body(op.source, op.id, "source")
    transform = Transform(origin=tuple(op.offset))
    if op.rotate is not None:
        spin = f.rotation_about_point(axis_vector(op.rotate.axis), op.rotate.angle_deg, tuple(op.rotate.about))
        transform = transform.compose(spin)
    return CopyBodyRequest(
        operation_id=op.id,
        source=source,
        transform=transform,
        scale=op.scale,
        scale_about=tuple(op.scale_about),
    )


def build_split(op: Any, ctx: ExecutionContext, resolver: Resolver) -> SplitRequest:
    """The source piece as a body handle and the parting plane as a frame (a base plane, a datum plane or a planar
    face; only its origin and normal matter)."""
    return SplitRequest(
        operation_id=op.id,
        source=resolver.body(op.source, op.id, "source"),
        plane=resolver.plane_frame(op.plane, op.id, "plane"),
        keep=op.keep,
    )


def build_base_flange(op: Any, ctx: ExecutionContext, resolver: Resolver) -> BaseFlangeRequest:
    return BaseFlangeRequest(
        operation_id=op.id, sketch=_sketch_handle(op, ctx), thickness_mm=op.thickness_mm, direction=op.direction
    )


def build_edge_flange(op: Any, ctx: ExecutionContext, resolver: Resolver) -> EdgeFlangeRequest:
    edge = resolver.edge(op.edge, op.id, "edge")
    return EdgeFlangeRequest(
        operation_id=op.id,
        edge=edge.handle,
        length_mm=op.length_mm,
        angle_deg=op.angle_deg,
        bend_radius_mm=op.bend_radius_mm,
        position=op.position,
        length_from=op.length_from,
    )


def build_unfold(op: Any, ctx: ExecutionContext, resolver: Resolver) -> UnfoldRequest:
    face = resolver.face(op.face, op.id, "face")
    if face.surface is not SurfaceType.plane:
        raise ResolutionError(
            op_id=op.id,
            path="face",
            ref=op.face,
            candidates=1,
            message=(
                f"unfold keeps one flat face of the sheet fixed, but {face.handle.id} is {face.surface.value}; "
                "pick one of the two big flat faces"
            ),
        )
    return UnfoldRequest(operation_id=op.id, face=face.handle, k_factor=op.k_factor)


def build_insert_component(op: Any, ctx: ExecutionContext, resolver: Resolver) -> InsertComponentRequest:
    document = ctx.documents.get(op.part)
    if document is None:
        raise _missing(op, "part", f"no document for part plan {op.part!r} (run the part plan first)")
    resolver.resolved_refs["part"] = document.id
    return InsertComponentRequest(operation_id=op.id, part=document, transform=placement_transform(op.placement))


def build_fix_component(op: Any, ctx: ExecutionContext, resolver: Resolver) -> FixComponentRequest:
    return FixComponentRequest(operation_id=op.id, component=_component(op, ctx, op.component, "component"))


def build_place_component(op: Any, ctx: ExecutionContext, resolver: Resolver) -> PlaceComponentRequest:
    return PlaceComponentRequest(
        operation_id=op.id,
        component=_component(op, ctx, op.component, "component"),
        transform=placement_transform(op.placement),
    )


def build_add_joint(op: Any, ctx: ExecutionContext, resolver: Resolver) -> AddJointRequest:
    axis = None if op.axis is None else resolver.axis(op.axis, op.id, "axis")
    return AddJointRequest(
        operation_id=op.id,
        joint_type=JointType(op.type),
        a=None if op.a is None else _component(op, ctx, op.a, "a"),
        b=_component(op, ctx, op.b, "b"),
        axis=axis,
        min_value=op.min,
        max_value=op.max,
    )


def build_set_joint(op: Any, ctx: ExecutionContext, resolver: Resolver) -> SetJointRequest:
    joint = ctx.joint_by_op.get(op.joint)
    if joint is None:
        raise _missing(op, "joint", f"operation {op.joint!r} produced no joint")
    return SetJointRequest(operation_id=op.id, joint=joint, value=op.value)


def build_mate_concentric(op: Any, ctx: ExecutionContext, resolver: Resolver) -> MateConcentricRequest:
    a = resolver.face(op.a, op.id, "a")
    b = resolver.face(op.b, op.id, "b")
    return MateConcentricRequest(operation_id=op.id, a=a.handle, b=b.handle, alignment=op.alignment)


def build_mate_coincident(op: Any, ctx: ExecutionContext, resolver: Resolver) -> MateCoincidentRequest:
    a = resolver.face(op.a, op.id, "a")
    b = resolver.face(op.b, op.id, "b")
    return MateCoincidentRequest(operation_id=op.id, a=a.handle, b=b.handle, alignment=op.alignment)


def build_mate_distance(op: Any, ctx: ExecutionContext, resolver: Resolver) -> MateDistanceRequest:
    a = resolver.face(op.a, op.id, "a")
    b = resolver.face(op.b, op.id, "b")
    return MateDistanceRequest(
        operation_id=op.id, a=a.handle, b=b.handle, distance_mm=op.distance_mm, alignment=op.alignment, side=op.side
    )


def build_check_interference(op: Any, ctx: ExecutionContext, resolver: Resolver) -> InterferenceRequest:
    components = None
    if op.components is not None:
        components = tuple(_component(op, ctx, c, f"components[{i}]") for i, c in enumerate(op.components))
    return InterferenceRequest(operation_id=op.id, components=components)


DISPATCH: dict[str, tuple[str, Builder]] = {
    "sketch": ("sketch", build_sketch),
    "ref_plane": ("datum_plane", build_ref_plane),
    "ref_axis": ("datum_axis", build_ref_axis),
    "extrude": ("extrude", build_extrude),
    "cut": ("cut", build_cut),
    "revolve": ("revolve", build_revolve),
    "sweep": ("sweep", build_sweep),
    "hole": ("hole", build_hole),
    "linear_pattern": ("linear_pattern", build_linear_pattern),
    "circular_pattern": ("circular_pattern", build_circular_pattern),
    "chamfer": ("chamfer", build_chamfer),
    "fillet": ("fillet", build_fillet),
    "shell": ("shell", build_shell),
    "draft": ("draft", build_draft),
    "mirror": ("mirror", build_mirror),
    "mirror_body": ("mirror_body", build_mirror_body),
    "split": ("split", build_split),
    "box": ("box", build_box),
    "cylinder": ("cylinder", build_cylinder),
    "boolean_union": ("boolean", _build_boolean(BooleanKind.union)),
    "boolean_subtract": ("boolean", _build_boolean(BooleanKind.subtract)),
    "boolean_intersect": ("boolean", _build_boolean(BooleanKind.intersect)),
    "translate": ("move_body", build_translate),
    "rotate": ("move_body", build_rotate),
    "copy_body": ("copy_body", build_copy_body),
    "base_flange": ("base_flange", build_base_flange),
    "edge_flange": ("edge_flange", build_edge_flange),
    "unfold": ("unfold", build_unfold),
    "insert_component": ("insert_component", build_insert_component),
    "fix_component": ("fix_component", build_fix_component),
    "place_component": ("place_component", build_place_component),
    "add_joint": ("add_joint", build_add_joint),
    "set_joint": ("set_joint", build_set_joint),
    "mate_concentric": ("mate_concentric", build_mate_concentric),
    "mate_coincident": ("mate_coincident", build_mate_coincident),
    "mate_distance": ("mate_distance", build_mate_distance),
    "check_interference": ("check_interference", build_check_interference),
}


def _resolve_value(value: Any, op: Any, ctx: ExecutionContext, resolver: Resolver, path: str) -> Any:
    """One argument of an adapter operation as the adapter gets it: a reference becomes the JSON of the handle (or of
    the datum) it resolves to, a model or a container is walked, anything else is JSON already."""
    if isinstance(value, FaceRef):
        return resolver.face(value, op.id, path).handle.model_dump(mode="json")
    if isinstance(value, EdgeRef):
        return resolver.edge(value, op.id, path).handle.model_dump(mode="json")
    if isinstance(value, EdgeSet):
        return [e.handle.model_dump(mode="json") for e in resolver.edge_set(value, op.id, path)]
    if isinstance(value, VertexRef):
        return resolver.vertex(value, op.id, path).handle.model_dump(mode="json")
    if isinstance(value, BodyRef):
        return resolver.body(value, op.id, path).model_dump(mode="json")
    if isinstance(value, PlaneRef):
        plane = resolver.plane_or_face(value, op.id, path)
        return plane.value if isinstance(plane, PlaneName) else plane.model_dump(mode="json")
    if isinstance(value, AxisRef):
        return resolver.axis(value, op.id, path).model_dump(mode="json")
    if isinstance(value, ComponentRef):
        return resolver.component(value, op.id, path).model_dump(mode="json")
    if isinstance(value, SketchStep):
        sketch = ctx.sketch_by_op.get(value.root)
        if sketch is None:
            raise _missing(op, path, f"step {value.root!r} has not produced a sketch")
        resolver.resolved_refs[path] = sketch.id
        return sketch.model_dump(mode="json")
    if isinstance(value, FeatureStep):
        feature = ctx.feature_by_op.get(value.root)
        if feature is None:
            raise _missing(op, path, f"step {value.root!r} produced no feature")
        resolver.resolved_refs[path] = feature.id
        return feature.model_dump(mode="json")
    if isinstance(value, BaseModel):
        out = {}
        for name, field in type(value).model_fields.items():
            out[field.alias or name] = _resolve_value(getattr(value, name), op, ctx, resolver, f"{path}.{name}")
        return out
    if isinstance(value, tuple | list):
        return [_resolve_value(item, op, ctx, resolver, f"{path}[{i}]") for i, item in enumerate(value)]
    if isinstance(value, dict):
        return {key: _resolve_value(item, op, ctx, resolver, f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, Enum):
        return value.value
    return value


def build_extension(op: Any, ctx: ExecutionContext, resolver: Resolver, spec: OperationSpec) -> ExtensionRequest:
    """The request of an operation the adapter declared: its arguments are checked against the adapter's model again (a
    plan that was saved and reopened comes through here too) and every reference in them is resolved."""
    try:
        model = spec.params.model_validate(op.params)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise ResolutionError(
            op_id=op.id,
            path="params",
            ref=None,
            candidates=0,
            message=f"{spec.name} no longer accepts its arguments ({where or 'params'}: {first['msg']})",
        ) from exc
    params = {
        field.alias or name: _resolve_value(getattr(model, name), op, ctx, resolver, name)
        for name, field in type(model).model_fields.items()
    }
    return ExtensionRequest(operation_id=op.id, name=spec.name, params=params)


def build_request(
    op: "OperationBase",
    ctx: ExecutionContext,
    resolver: Resolver,
    extensions: Mapping[str, OperationSpec] | None = None,
) -> Request:
    """Resolve every reference of ``op`` and return the adapter request; raises ``ResolutionError``. ``extensions`` are
    the operations the adapter declared, by their name inside a plan (``ext:<name>``)."""
    spec = (extensions or {}).get(op.op)
    if spec is not None:
        return build_extension(op, ctx, resolver, spec)
    if is_extension_op(op.op):
        msg = f"the adapter no longer declares the operation {plain_op_name(op.op)!r}"
        raise ResolutionError(op_id=op.id, path="op", ref=op.op, candidates=0, message=msg)
    try:
        _, builder = DISPATCH[op.op]
    except KeyError as exc:
        raise ResolutionError(op_id=op.id, path="op", ref=op.op, candidates=0, message=f"unknown op {op.op!r}") from exc
    return builder(op, ctx, resolver)
