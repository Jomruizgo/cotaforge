"""Signed volumes and exact pairwise intersections.

``intersection_volume`` returns ``None`` when the pair is outside the supported containment
rules; callers translate ``None`` into ``AdapterError(code=not_supported)``.
"""

from __future__ import annotations

import math

from cotaforge.adapters.fake.model import (
    Primitive,
    PrimitiveKind,
    cylinder_bbox,
    vadd,
    vdot,
    vparallel,
    vscale,
    vsub,
)
from cotaforge.contract import Axis, BoundingBox

_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_EPS = 1e-9


def principal_axis(direction: tuple[float, float, float]) -> int | None:
    """Index of the world axis the unit direction is parallel to, or None."""
    for i, axis in enumerate(_AXES):
        if vparallel(direction, axis):
            return i
    return None


def as_box(prim: Primitive) -> BoundingBox | None:
    """The axis-aligned box of a prism whose base is an axis-aligned rectangle swept along a world axis."""
    if prim.kind is not PrimitiveKind.prism or prim.polygon is None or prim.direction is None:
        return None
    if len(prim.polygon) != 4 or principal_axis(prim.direction) is None:
        return None
    for i in range(4):
        edge = vsub(prim.polygon[(i + 1) % 4], prim.polygon[i])
        nonzero = [abs(c) > _EPS for c in edge]
        if sum(nonzero) != 1:
            return None
    return prim.bbox


def _interval_overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _axial_interval(prim: Primitive, reference: Axis) -> tuple[float, float]:
    """Axial range of a cylinder projected onto the reference axis (parallel axes assumed)."""
    assert prim.axis is not None
    lo, hi = prim.axial_range()
    base = vdot(vsub(prim.axis.origin, reference.origin), reference.direction)
    sense = vdot(prim.axis.direction, reference.direction)
    a, b = base + sense * lo, base + sense * hi
    return (min(a, b), max(a, b))


def _box_intersection(a: BoundingBox, b: BoundingBox) -> BoundingBox | None:
    lo = tuple(max(x, y) for x, y in zip(a.min, b.min, strict=True))
    hi = tuple(min(x, y) for x, y in zip(a.max, b.max, strict=True))
    if any(h - lo_i <= _EPS for lo_i, h in zip(lo, hi, strict=True)):
        return None
    return BoundingBox(min=lo, max=hi)  # type: ignore[arg-type]


def _cylinder_segment(reference: Axis, radius: float, interval: tuple[float, float], prim_id: str) -> Primitive:
    origin = vadd(reference.origin, vscale(reference.direction, interval[0]))
    height = interval[1] - interval[0]
    return Primitive(
        id=prim_id,
        kind=PrimitiveKind.cylinder,
        sign=1,
        volume_mm3=math.pi * radius * radius * height,
        centroid=vadd(origin, vscale(reference.direction, height / 2.0)),
        bbox=cylinder_bbox(origin, reference.direction, radius, height),
        feature="",
        axis=Axis(origin=origin, direction=reference.direction),
        radius=radius,
        height=height,
    )


def box_primitive(box: BoundingBox, prim_id: str) -> Primitive:
    """A positive axis-aligned box primitive (a prism on the box's floor, grown along +Z)."""
    size = box.size()
    polygon = (
        (box.min[0], box.min[1], box.min[2]),
        (box.max[0], box.min[1], box.min[2]),
        (box.max[0], box.max[1], box.min[2]),
        (box.min[0], box.max[1], box.min[2]),
    )
    return Primitive(
        id=prim_id,
        kind=PrimitiveKind.prism,
        sign=1,
        volume_mm3=size[0] * size[1] * size[2],
        centroid=box.center(),
        bbox=box,
        feature="",
        polygon=polygon,
        direction=(0.0, 0.0, 1.0),
        depth=size[2],
    )


def intersection_primitive(p: Primitive, q: Primitive, prim_id: str = "") -> Primitive | None:
    """Exact intersection of two primitives as a new positive primitive; ``None`` = not computable.

    A zero-volume result (disjoint pair) is returned as ``None``-free: check ``volume_mm3 == 0``.
    """
    box_p, box_q = as_box(p), as_box(q)
    if box_p is not None and box_q is not None:
        inter = _box_intersection(box_p, box_q)
        if inter is None:
            return _empty(prim_id)
        return box_primitive(inter, prim_id)

    if p.kind is PrimitiveKind.cylinder and q.kind is PrimitiveKind.cylinder:
        assert p.axis is not None and q.axis is not None and p.radius is not None and q.radius is not None
        if not vparallel(p.axis.direction, q.axis.direction):
            return None
        offset = vsub(q.axis.origin, p.axis.origin)
        perpendicular = vsub(offset, vscale(p.axis.direction, vdot(offset, p.axis.direction)))
        if math.sqrt(vdot(perpendicular, perpendicular)) > 1e-6:
            return None
        overlap_lo = max(_axial_interval(p, p.axis)[0], _axial_interval(q, p.axis)[0])
        overlap_hi = min(_axial_interval(p, p.axis)[1], _axial_interval(q, p.axis)[1])
        if overlap_hi - overlap_lo <= _EPS:
            return _empty(prim_id)
        return _cylinder_segment(p.axis, min(p.radius, q.radius), (overlap_lo, overlap_hi), prim_id)

    if p.kind is PrimitiveKind.cylinder and box_q is not None:
        return _cylinder_box(p, box_q, prim_id)
    if q.kind is PrimitiveKind.cylinder and box_p is not None:
        return _cylinder_box(q, box_p, prim_id)
    return None


def _empty(prim_id: str) -> Primitive:
    zero = BoundingBox(min=(0.0, 0.0, 0.0), max=(0.0, 0.0, 0.0))
    return Primitive(
        id=prim_id, kind=PrimitiveKind.prism, sign=1, volume_mm3=0.0, centroid=(0.0, 0.0, 0.0), bbox=zero, feature=""
    )


def _cylinder_box(cyl: Primitive, box: BoundingBox, prim_id: str) -> Primitive | None:
    assert cyl.axis is not None and cyl.radius is not None
    axis_index = principal_axis(cyl.axis.direction)
    if axis_index is None:
        return None
    lo, hi = cyl.bbox.min[axis_index], cyl.bbox.max[axis_index]
    axial = _interval_overlap((lo, hi), (box.min[axis_index], box.max[axis_index]))
    footprint_inside = True
    footprint_disjoint = False
    for i in range(3):
        if i == axis_index:
            continue
        c = cyl.axis.origin[i]
        r = cyl.radius
        if c - r < box.min[i] - _EPS or c + r > box.max[i] + _EPS:
            footprint_inside = False
        if c + r <= box.min[i] + _EPS or c - r >= box.max[i] - _EPS:
            footprint_disjoint = True
    if axial <= _EPS or footprint_disjoint:
        return _empty(prim_id)
    if not footprint_inside:
        return None
    start = max(lo, box.min[axis_index])
    reference = Axis(origin=cyl.axis.origin, direction=_AXES[axis_index])
    base = vdot(cyl.axis.origin, reference.direction)
    return _cylinder_segment(reference, cyl.radius, (start - base, start - base + axial), prim_id)


def intersection_volume(p: Primitive, q: Primitive) -> float | None:
    """Volume of ``p ∩ q`` for the supported pairs of 7.4, ``None`` when not computable."""
    prim = intersection_primitive(p, q)
    return None if prim is None else prim.volume_mm3


def signed_intersection_sum(a: list[Primitive], b: list[Primitive]) -> float | None:
    """``Σ sign_p·sign_q·V(p ∩ q)`` over all pairs; ``None`` if any pair is not computable."""
    total = 0.0
    for p in a:
        for q in b:
            volume = intersection_volume(p, q)
            if volume is None:
                return None
            total += p.sign * q.sign * volume
    return total
