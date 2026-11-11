"""Components, mates and interference of the fake adapter.

Component entities are addressed from the assembly with ids prefixed by the component id
(``comp:2/face:7``). Mates only translate the component that owns face ``b``.
"""

from __future__ import annotations

import copy
import math

from cotaforge.adapters.fake.booleans import as_box, signed_intersection_sum
from cotaforge.adapters.fake.model import (
    FakeComponent,
    FakeDocument,
    FakeFace,
    FakeJoint,
    Primitive,
    vadd,
    vcross,
    vdot,
    vlength,
    vscale,
    vsub,
    vunit,
)
from cotaforge.contract import (
    AdapterError,
    ErrorCode,
    ErrorInfo,
    Mat3,
    MateAlignment,
    Transform,
    Vec3,
    joint_delta,
)

PREFIX_SEPARATOR = "/"
OVERLAP_TOL_MM3 = 1e-6

PARALLEL = 1.0 - 1e-6


def mate_failed(message: str, operation_id: str | None = None) -> AdapterError:
    return AdapterError(
        ErrorInfo(code=ErrorCode.mate_failed, message=message, operation_id=operation_id, recoverable=True)
    )


def prefixed(component_id: str, local_id: str) -> str:
    return f"{component_id}{PREFIX_SEPARATOR}{local_id}"


def split_prefixed(entity_id: str) -> tuple[str, str] | None:
    """``("comp:2", "face:7")`` for a component entity id, ``None`` for a plain document id."""
    if PREFIX_SEPARATOR not in entity_id:
        return None
    component_id, local_id = entity_id.split(PREFIX_SEPARATOR, 1)
    return component_id, local_id


def insert_component(doc: FakeDocument, part: FakeDocument, transform: Transform) -> FakeComponent:
    component = FakeComponent(
        id=doc.next_id("comp"), part=copy.deepcopy(part), transform=transform, part_id=part.id
    )
    doc.components[component.id] = component
    return component


def component_face(component: FakeComponent, local_id: str) -> FakeFace:
    """A component's face in assembly space, with the prefixed id and body."""
    face = component.part.faces[local_id].transformed(component.transform, new_id=prefixed(component.id, local_id))
    face.body = prefixed(component.id, face.body)
    return face


def component_primitives(component: FakeComponent) -> list[Primitive]:
    return [p.transformed(component.transform) for p in component.part.primitives.values()]


def translate_component(component: FakeComponent, offset: Vec3) -> None:
    component.transform = Transform(
        origin=vadd(component.transform.origin, offset), rotation=component.transform.rotation
    )


def transform_component(component: FakeComponent, delta: Transform) -> None:
    """Apply a world-space ``delta`` AFTER the component's current transform (``delta.compose(transform)``):
    a pure translation delta reduces to ``translate_component`` exactly (rotation stays ``IDENTITY``)."""
    component.transform = delta.compose(component.transform)


def _perpendicular_offset(a_origin: Vec3, pivot: Vec3, direction: Vec3) -> Vec3:
    """Perpendicular part of ``a_origin - pivot`` with respect to ``direction`` (6.2). Independent of the sign
    of ``direction``, so it is the same formula whether or not a rotation precedes it."""
    offset = vsub(a_origin, pivot)
    return vsub(offset, vscale(direction, vdot(offset, direction)))


def _stable_perpendicular(u: Vec3) -> Vec3:
    """A deterministic unit vector perpendicular to ``u``, for the exactly-antiparallel rotation case: world X
    unless ``u`` is nearly parallel to X (then world Y) -- the same pick FreeCAD's own two-vector ``Rotation``
    constructor makes (verified against 1.0.2 and 1.1.3 in the freecad worker's tests)."""
    candidate = vcross(u, (1.0, 0.0, 0.0))
    if vlength(candidate) < 1e-6:
        candidate = vcross(u, (0.0, 1.0, 0.0))
    return vunit(candidate)


def _rotation_onto(u: Vec3, v: Vec3) -> Mat3:
    """Rotation matrix mapping unit direction ``u`` onto unit direction ``v`` (Rodrigues, via axis + angle and
    ``Transform.from_axis_angle``). ``u`` exactly or nearly antiparallel to ``v`` (a degenerate cross product)
    rotates 180 degrees about ``_stable_perpendicular(u)`` instead of an undefined/noisy axis."""
    cos_angle = max(-1.0, min(1.0, vdot(u, v)))
    cross = vcross(u, v)
    if vlength(cross) < 1e-9:
        if cos_angle > 0.0:
            return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        return Transform.from_axis_angle(_stable_perpendicular(u), 180.0).rotation
    axis = vunit(cross)
    angle_deg = math.degrees(math.acos(cos_angle))
    return Transform.from_axis_angle(axis, angle_deg).rotation


def concentric_alignment(a: FakeFace, b: FakeFace, alignment: MateAlignment, operation_id: str | None) -> Transform:
    """World-space delta (5.6, generalised) for a concentric mate between two cylindrical faces.

    Axes already (anti-)parallel: unchanged from the original fake -- a pure translation, and the same
    ``mate_failed`` when the requested ``alignment`` does not match the axes' actual sense (no rotation is
    computed for this bucket, so an already-matching mate keeps its exact placement).

    Otherwise (skew axes, any angle): rotate the component owning face ``b`` about ``b``'s axis origin so its
    axis maps onto ``a``'s axis direction (or its negative, for ``anti_aligned``), then translate by the same
    perpendicular offset as the parallel case.
    """
    if a.axis is None or b.axis is None:
        raise mate_failed("mate_concentric requires two faces with an axis", operation_id)
    da = a.axis.direction
    db = b.axis.direction
    sense = vdot(da, db)
    wanted = 1.0 if alignment is MateAlignment.aligned else -1.0
    if abs(sense) > PARALLEL:
        if sense * wanted < 0:
            raise mate_failed(
                f"axes are {'anti-parallel' if sense < 0 else 'parallel'} but {alignment.value} was requested",
                operation_id,
            )
        return Transform(origin=_perpendicular_offset(a.axis.origin, b.axis.origin, da))
    target = da if wanted > 0 else vscale(da, -1.0)
    rotation = _rotation_onto(db, target)
    pivot = b.axis.origin
    rotated_pivot = Transform(rotation=rotation).apply_direction(pivot)
    rotate_origin = vsub(pivot, rotated_pivot)
    offset = _perpendicular_offset(a.axis.origin, pivot, da)
    return Transform(origin=vadd(rotate_origin, offset), rotation=rotation)


def planar_translation(
    a: FakeFace, b: FakeFace, alignment: MateAlignment, distance: float, operation_id: str | None
) -> Vec3:
    """``((a.center - b.center)·n_a + distance)·n_a`` after checking ``n_b = ∓n_a`` per alignment."""
    if a.normal is None or b.normal is None:
        raise mate_failed("planar mates require two planar faces", operation_id)
    sense = vdot(a.normal, b.normal)
    wanted = 1.0 if alignment is MateAlignment.aligned else -1.0
    if abs(sense - wanted) > 1e-6:
        raise mate_failed(
            f"face normals are not {alignment.value} (n_a·n_b = {sense:.3f})",
            operation_id,
        )
    gap = vdot(vsub(a.center, b.center), a.normal) + distance
    return vscale(a.normal, gap)


def mirrored_translation(component: FakeComponent, offset: Vec3, plane_point: Vec3, normal: Vec3) -> Vec3:
    """Extra translation that puts the component, once moved by ``offset``, on the other side of the plane."""
    moved = FakeComponent(
        id=component.id,
        part=component.part,
        transform=Transform(origin=vadd(component.transform.origin, offset), rotation=component.transform.rotation),
    )
    lo = float("inf")
    hi = float("-inf")
    for prim in component_primitives(moved):
        if prim.sign <= 0:
            continue
        for corner in _corners(prim):
            s = vdot(vsub(corner, plane_point), normal)
            lo, hi = min(lo, s), max(hi, s)
    if lo == float("inf"):
        return (0.0, 0.0, 0.0)
    return vscale(normal, -(lo + hi))


def _corners(prim: Primitive) -> tuple[Vec3, ...]:
    box = prim.bbox
    return tuple(
        (x, y, z) for x in (box.min[0], box.max[0]) for y in (box.min[1], box.max[1]) for z in (box.min[2], box.max[2])
    )


def component_distance(a: list[Primitive], b: list[Primitive]) -> float | None:
    """Minimum distance between two unions of positive axis-aligned boxes (None: another shape is involved)."""
    boxes_a = [as_box(p) if p.sign > 0 else None for p in a]
    boxes_b = [as_box(p) if p.sign > 0 else None for p in b]
    if not boxes_a or not boxes_b or None in boxes_a or None in boxes_b:
        return None
    best = math.inf
    for p in boxes_a:
        for q in boxes_b:
            assert p is not None and q is not None
            gaps = [max(0.0, q.min[k] - p.max[k], p.min[k] - q.max[k]) for k in range(3)]
            best = min(best, math.sqrt(sum(gap * gap for gap in gaps)))
    return best


def check_interference(
    doc: FakeDocument, component_ids: list[str], operation_id: str | None
) -> list[tuple[str, str, float]]:
    """Pairwise ``Σ sign_p·sign_q·V(p'∩q')`` with primitives in assembly space (7.4)."""
    pairs: list[tuple[str, str, float]] = []
    prims = {cid: component_primitives(doc.components[cid]) for cid in component_ids}
    for i, a in enumerate(component_ids):
        for b in component_ids[i + 1 :]:
            volume = signed_intersection_sum(prims[a], prims[b])
            if volume is None:
                raise AdapterError(
                    ErrorInfo(
                        code=ErrorCode.not_supported,
                        message=f"interference between {a} and {b} is not computable by the fake",
                        operation_id=operation_id,
                        recoverable=False,
                    )
                )
            pairs.append((a, b, volume))
    return pairs


def joint_pose(joint: FakeJoint, value: float) -> Transform:
    """The pose of the component a joint carries at ``value``: the motion composed after its pose at value 0."""
    return joint_delta(joint.joint_type, joint.axis, value).compose(joint.base)


def measure_components(a: FakeComponent, b: FakeComponent) -> tuple[float | None, float | None, str]:
    """Common volume and minimum distance of two components; ``None`` for what the fake cannot compute (a rotation that
    is not a multiple of 90 degrees turns a box into a shape it does not model), with the reason."""
    prims_a, prims_b = component_primitives(a), component_primitives(b)
    volume = signed_intersection_sum(prims_a, prims_b)
    if volume is None:
        return None, None, "the fake measures axis-aligned boxes only (a rotation that is not a multiple of 90 degrees)"
    if volume > OVERLAP_TOL_MM3:
        return volume, 0.0, ""
    distance = component_distance(prims_a, prims_b)
    return 0.0, distance, "" if distance is not None else "the fake measures distances between axis-aligned boxes only"
