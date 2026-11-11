"""Pure analytic formulas used by the Level 2 expectations.

Nothing here touches an adapter or the IR: every function takes numbers, vectors, boxes or the
light-weight shape descriptors defined below and returns numbers/boxes. The intersection rules of
7.4 are re-implemented here independently from the fake adapter on purpose.
"""

import math
from collections.abc import Iterable, Sequence

from pydantic import BaseModel

from cotaforge.contract import Axis, BoundingBox, Profile, SketchFrame, Transform, Vec3

Point2 = tuple[float, float]

_EPS = 1e-9


# --- vector helpers -----------------------------------------------------------------


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def normalise(a: Vec3) -> Vec3:
    n = length(a)
    if n < _EPS:
        msg = "cannot normalise a zero-length vector"
        raise ValueError(msg)
    return scale(a, 1.0 / n)


def perpendicular_part(v: Vec3, direction: Vec3) -> Vec3:
    """Component of ``v`` perpendicular to the (unit) ``direction``."""
    d = normalise(direction)
    return sub(v, scale(d, dot(v, d)))


def closest_point_on_axis(p: Vec3, axis: Axis) -> Vec3:
    rel = sub(p, axis.origin)
    return add(axis.origin, scale(axis.direction, dot(rel, axis.direction)))


def distance_point_to_axis(p: Vec3, axis: Axis) -> float:
    return length(perpendicular_part(sub(p, axis.origin), axis.direction))


def parallel(a: Vec3, b: Vec3, *, tol: float = 1e-6) -> bool:
    """Directions parallel in either sense."""
    return length(cross(normalise(a), normalise(b))) <= tol


# --- volumes --------------------------------------------------------------------------


def extrude_volume(area: float, depth: float, depth2: float | None = None, both_directions: bool = False) -> float:
    """``+A * depth`` (``A * (depth + depth2)`` for both directions; ``depth2`` None = symmetric)."""
    if not both_directions:
        return area * depth
    return area * (depth + (depth if depth2 is None else depth2))


def cut_volume(area: float, depth: float, depth2: float | None = None, both_directions: bool = False) -> float:
    return -extrude_volume(area, depth, depth2, both_directions)


def revolve_volume(area: float, centroid_distance: float, angle_deg: float = 360.0) -> float:
    """Pappus: ``2 * pi * d * A`` scaled by ``angle / 360``."""
    return 2.0 * math.pi * centroid_distance * area * (angle_deg / 360.0)


def hole_volume(radius: float, depth: float) -> float:
    return -math.pi * radius * radius * depth


def radial_hole_volume(
    outer_radius: float, inner_radius: float, radius: float, depth: float | None = None, steps: int = 2000
) -> float:
    """Volume (positive, mm3) a round hole of ``radius`` removes from a tube of ``outer_radius`` / ``inner_radius``
    (0 for a solid cylinder) when it runs along a radius through the tube axis, starting at the tangent plane on
    the outer surface and ``depth`` deep (``None``: through everything, both walls).

    There is no elementary closed form for two crossing cylinders, so this integrates the exact removed length along
    the hole axis over the hole's cross-section: with the hole axis along x and ``y = radius * sin(t)``, a point at
    lateral offset ``y`` crosses material over ``x in [xi, xo]`` (near wall) and ``[-xo, -xi]`` (far wall), where
    ``xo = sqrt(Ro^2 - y^2)`` and ``xi = sqrt(Ri^2 - y^2)`` (0 past the bore), clipped to ``x >= Ro - depth``; the
    chord of the hole disc at that offset is ``2 r cos t`` and ``dy = r cos t dt``. Composite Simpson over
    ``t in [-pi/2, pi/2]`` (the integrand has kinks, so the error is O(h^2): ~1e-12 relative at 2000 steps).
    A hole wider than the tube is not meaningful; the lateral reach is clamped to the tube."""
    if outer_radius <= 0 or radius <= 0 or inner_radius < 0 or inner_radius >= outer_radius:
        msg = "radial_hole_volume needs 0 <= inner_radius < outer_radius and a positive hole radius"
        raise ValueError(msg)
    floor = -math.inf if depth is None else outer_radius - depth

    def removed_length(y: float) -> float:
        xo = math.sqrt(max(0.0, outer_radius**2 - y * y))
        xi = math.sqrt(max(0.0, inner_radius**2 - y * y))
        near = max(0.0, xo - max(xi, floor))
        far = max(0.0, -xi - max(-xo, floor))
        return near + far

    def integrand(t: float) -> float:
        return 2.0 * radius * math.cos(t) * removed_length(radius * math.sin(t)) * radius * math.cos(t)

    count = steps + steps % 2
    a, b = -math.pi / 2.0, math.pi / 2.0
    h = (b - a) / count
    total = integrand(a) + integrand(b)
    for i in range(1, count):
        total += (4.0 if i % 2 else 2.0) * integrand(a + i * h)
    return total * h / 3.0


def chamfer_volume(distance1: float, distance2: float, edge_length: float, convex: bool = True) -> float:
    """``d1 * d2 / 2 * L``; negative (material removed) for convex edges, positive for concave."""
    wedge = distance1 * distance2 / 2.0 * edge_length
    return -wedge if convex else wedge


def fillet_volume(radius: float, edge_length: float, convex: bool = True) -> float:
    """``(1 - pi/4) * r^2 * L``; negative for convex edges, positive for concave."""
    cut = (1.0 - math.pi / 4.0) * radius * radius * edge_length
    return -cut if convex else cut


def pattern_volume(instances: int, seed_delta: float) -> float:
    return (instances - 1) * seed_delta


# --- sheet metal ------------------------------------------------------------------------


def bend_allowance(angle_deg: float, thickness: float, inner_radius: float, k_factor: float) -> float:
    """Length of the neutral line through a bend: ``angle * (r + K * t)`` (ANSI K-factor: the neutral line sits
    ``K * t`` from the inside of the bend)."""
    return math.radians(angle_deg) * (inner_radius + k_factor * thickness)


def outside_setback(angle_deg: float, thickness: float, inner_radius: float) -> float:
    """Distance from the tangent line of a bend to its outer virtual sharp: ``(r + t) * tan(angle / 2)``."""
    return (inner_radius + thickness) * math.tan(math.radians(angle_deg) / 2.0)


def edge_flange_flat_gain(
    angle_deg: float,
    thickness: float,
    inner_radius: float,
    k_factor: float,
    length: float,
    *,
    material_inside: bool,
    from_outer_sharp: bool,
) -> float:
    """How much one edge flange adds to the flat length of the sheet in the direction across its edge, measured against
    the sheet's own extent there (edge to edge, flange not yet bent): the bend allowance plus the straight leg, minus
    ``r + t`` when the bend goes inside the sheet's footprint (the bend starts that far in from the edge, whatever the
    angle: the sheet loses that much). The leg is the length itself when it is the straight part, or the length minus
    the outside setback when it is measured to the outer virtual sharp. At 90 degrees the two-flange total is the usual
    bend deduction rule ``D1 + D2 - (2 * setback - BA)``. Measured against the SheetMetal add-on (see the
    integration tests), which is what this must agree with."""
    setback = outside_setback(angle_deg, thickness, inner_radius)
    leg = length - setback if from_outer_sharp else length
    gain = bend_allowance(angle_deg, thickness, inner_radius, k_factor) + leg
    return gain - (inner_radius + thickness) if material_inside else gain


def box_volume(size: Vec3) -> float:
    return size[0] * size[1] * size[2]


def cylinder_volume(radius: float, height: float) -> float:
    return math.pi * radius * radius * height


def drafted_box_volume(
    extents: tuple[float, float], drafted: tuple[int, int], angle_deg: float, w_lo: float, w_hi: float
) -> float:
    """Volume of a box whose walls ``drafted`` (how many of the two opposite faces on each lateral axis lean: 0, 1 or
    2) are tilted by ``angle_deg`` about the neutral plane. ``w`` is the height along the pull measured from that plane,
    the box spans ``w_lo .. w_hi``, and a drafted face sits ``w * tan(angle)`` further INSIDE at height ``w`` (the wall
    narrows along the pull, widens against it), so the section there is ``(a - k1 t w) (b - k2 t w)`` and the volume
    its integral over the height. Valid while both sections stay positive (the caller checks the far end)."""
    a, b = extents
    k1, k2 = drafted
    t = math.tan(math.radians(angle_deg))
    h = w_hi - w_lo
    first = (w_hi**2 - w_lo**2) / 2.0
    second = (w_hi**3 - w_lo**3) / 3.0
    return a * b * h - (a * k2 + b * k1) * t * first + k1 * k2 * t * t * second


def drafted_cylinder_volume(radius: float, angle_deg: float, w_lo: float, w_hi: float) -> float:
    """Volume of a right cylinder whose lateral face is drafted into a cone of semi-angle ``angle_deg`` about the
    neutral plane (``w`` as in ``drafted_box_volume``): the radius at height ``w`` is ``R - w tan(angle)``."""
    t = math.tan(math.radians(angle_deg))
    h = w_hi - w_lo
    first = (w_hi**2 - w_lo**2) / 2.0
    second = (w_hi**3 - w_lo**3) / 3.0
    return math.pi * (radius * radius * h - 2.0 * radius * t * first + t * t * second)


def centroid_after(volume: float, centroid: Vec3, deltas: Iterable[tuple[float, Vec3]]) -> Vec3:
    """Centroid after adding signed volumes ``(dv, c)`` to a body of ``volume`` at ``centroid``."""
    total = volume
    moment = scale(centroid, volume)
    for dv, c in deltas:
        total += dv
        moment = add(moment, scale(c, dv))
    if abs(total) < _EPS:
        msg = "zero total volume"
        raise ValueError(msg)
    return scale(moment, 1.0 / total)


# --- bounding boxes ---------------------------------------------------------------------


def bbox_of_points(points: Iterable[Vec3]) -> BoundingBox:
    pts = list(points)
    if not pts:
        msg = "bbox_of_points needs at least one point"
        raise ValueError(msg)
    mins = tuple(min(p[i] for p in pts) for i in range(3))
    maxs = tuple(max(p[i] for p in pts) for i in range(3))
    return BoundingBox(min=mins, max=maxs)  # type: ignore[arg-type]


def bbox_corners(box: BoundingBox) -> tuple[Vec3, ...]:
    return tuple(
        (x, y, z) for x in (box.min[0], box.max[0]) for y in (box.min[1], box.max[1]) for z in (box.min[2], box.max[2])
    )


def bbox_union(a: BoundingBox, b: BoundingBox) -> BoundingBox:
    return bbox_of_points(bbox_corners(a) + bbox_corners(b))


def bbox_translate(box: BoundingBox, offset: Vec3) -> BoundingBox:
    return BoundingBox(min=add(box.min, offset), max=add(box.max, offset))


def bbox_transform(box: BoundingBox, transform: Transform) -> BoundingBox:
    """Axis-aligned box of the transformed corners (exact for principal rotations)."""
    return bbox_of_points(transform.apply(c) for c in bbox_corners(box))


def bbox_volume(box: BoundingBox) -> float:
    s = box.size()
    return max(s[0], 0.0) * max(s[1], 0.0) * max(s[2], 0.0)


def bboxes_overlap(a: BoundingBox, b: BoundingBox, *, tol: float = 1e-6) -> bool:
    """Strict overlap: boxes that merely touch do not overlap.

    A dimension in which both boxes are flat (e.g. two coplanar faces) counts as overlapping when
    the two flat values coincide.
    """
    for i in range(3):
        overlap = min(a.max[i], b.max[i]) - max(a.min[i], b.min[i])
        both_flat = a.max[i] - a.min[i] <= tol and b.max[i] - b.min[i] <= tol
        if both_flat:
            if abs(a.min[i] - b.min[i]) > tol:
                return False
        elif overlap <= tol:
            return False
    return True


def bbox_contains(outer: BoundingBox, inner: BoundingBox, *, tol: float = 1e-6) -> bool:
    return all(inner.min[i] >= outer.min[i] - tol and inner.max[i] <= outer.max[i] + tol for i in range(3))


# --- reflection ----------------------------------------------------------------------------


def reflect_point(p: Vec3, origin: Vec3, normal: Vec3) -> Vec3:
    """The mirror image of ``p`` across the plane through ``origin`` with ``normal`` (any length): ``p - 2 d n``, ``d``
    being the signed distance of ``p`` to the plane."""
    n = normalise(normal)
    return sub(p, scale(n, 2.0 * dot(sub(p, origin), n)))


def reflect_direction(v: Vec3, normal: Vec3) -> Vec3:
    """``v`` reflected across a plane with ``normal`` (the plane's position does not matter for a direction)."""
    n = normalise(normal)
    return sub(v, scale(n, 2.0 * dot(v, n)))


def plane_axis_aligned(normal: Vec3, *, tol: float = _EPS) -> bool:
    """Whether a plane's normal is a world axis (either sense): the only planes across which an axis-aligned box stays
    an axis-aligned box, so the only ones where the box of a reflection is the reflection of the box."""
    n = normalise(normal)
    return sum(1 for c in n if abs(c) > tol) == 1


def bbox_reflect(box: BoundingBox, origin: Vec3, normal: Vec3) -> BoundingBox:
    """Axis-aligned box of the reflected corners: the box of the reflected shape only across an axis-aligned plane
    (``plane_axis_aligned``), otherwise a box that merely ENCLOSES it."""
    return bbox_of_points(reflect_point(c, origin, normal) for c in bbox_corners(box))


# --- uniform scaling -------------------------------------------------------------------------


def scale_point(p: Vec3, factor: float, about: Vec3) -> Vec3:
    """``p`` scaled uniformly by ``factor`` about the point ``about``: ``about + factor * (p - about)``."""
    return add(about, scale(sub(p, about), factor))


def bbox_scale(box: BoundingBox, factor: float, about: Vec3) -> BoundingBox:
    """The box of a body scaled uniformly (a positive factor keeps an axis-aligned box axis-aligned and exact)."""
    return bbox_of_points(scale_point(c, factor, about) for c in bbox_corners(box))


def swept_profile_bbox(frame: SketchFrame, bbox_uv: tuple[Point2, Point2], start: float, end: float) -> BoundingBox:
    """Box of a profile's uv-box swept along the frame normal from offset ``start`` to ``end``."""
    (u0, v0), (u1, v1) = bbox_uv
    corners: list[Vec3] = []
    for u in (u0, u1):
        for v in (v0, v1):
            base = frame.to_world(u, v)
            for t in (start, end):
                corners.append(add(base, scale(frame.n, t)))
    return bbox_of_points(corners)


def swept_profile_shape_bbox(frame: SketchFrame, profile: Profile, start: float, end: float) -> BoundingBox:
    """Exact box of a profile swept along the frame normal from offset ``start`` to ``end``.

    ``swept_profile_bbox`` sweeps the profile's uv-box, which is exact only while the frame is axis-aligned
    (or the profile is a rectangle): on a tilted frame (a datum plane at an angle) the true box of a circle
    is the cylinder's, smaller than the swept corners of its bounding square, and a polygon's is the box of
    its vertices. Rectangles keep the corner rule (their uv-box corners ARE their vertices)."""
    if profile.kind == "circle" and profile.center is not None and profile.radius is not None:
        axis = Axis(origin=frame.to_world(*profile.center), direction=frame.n)
        return cylinder_bbox(axis, profile.radius, start, end)
    if profile.kind == "polygon" and profile.points:
        corners = [add(frame.to_world(u, v), scale(frame.n, t)) for u, v in profile.points for t in (start, end)]
        return bbox_of_points(corners)
    return swept_profile_bbox(frame, profile.bbox_uv, start, end)


def face_footprint_area(box: BoundingBox) -> float:
    """Area of a planar face's bounding box: the product of its two largest extents (the third is the plane's
    thickness, ~0 for an axis-aligned face). Equals the face's own area exactly when the face IS that
    rectangle; larger for any other outline (disc, L-shape, a face with holes, a tilted face)."""
    extents = sorted(box.size(), reverse=True)
    return extents[0] * extents[1]


def disc_footprint_bbox(axis: Axis, radius: float, center: Vec3, normal: Vec3) -> BoundingBox:
    """Bounding box of the disc of ``radius`` where ``axis`` pierces the plane through ``center`` with ``normal``
    (a flat box); ``ValueError`` when the axis is parallel to the plane."""
    denominator = dot(axis.direction, normal)
    if abs(denominator) < 1e-9:
        msg = "axis is parallel to the face plane"
        raise ValueError(msg)
    t = dot(sub(center, axis.origin), normal) / denominator
    point = add(axis.origin, scale(axis.direction, t))
    return cylinder_bbox(Axis(origin=point, direction=normal), radius, 0.0, 0.0)


def cylinder_bbox(axis: Axis, radius: float, start: float, end: float) -> BoundingBox:
    """Exact axis-aligned box of a cylinder of ``radius`` from axial offset ``start`` to ``end``."""
    d = axis.direction
    p0 = add(axis.origin, scale(d, start))
    p1 = add(axis.origin, scale(d, end))
    mins = []
    maxs = []
    for i in range(3):
        lateral = radius * math.sqrt(max(0.0, 1.0 - d[i] * d[i]))
        mins.append(min(p0[i], p1[i]) - lateral)
        maxs.append(max(p0[i], p1[i]) + lateral)
    return BoundingBox(min=tuple(mins), max=tuple(maxs))  # type: ignore[arg-type]


def revolve_bbox(axis: Axis, points: Sequence[Vec3]) -> BoundingBox:
    """Box of a full (360 degrees) revolution of a polygon given by world points."""
    axial = [dot(sub(p, axis.origin), axis.direction) for p in points]
    radius = max(distance_point_to_axis(p, axis) for p in points)
    return cylinder_bbox(axis, radius, min(axial), max(axial))


def is_principal_rotation(transform: Transform, *, tol: float = 1e-9) -> bool:
    """True when every rotation entry is 0 or +/-1 (multiples of 90 degrees about a principal axis)."""
    return all(min(abs(x), abs(abs(x) - 1.0)) <= tol for row in transform.rotation for x in row)


def rotation_about_point(axis: Vec3, angle_deg: float, about: Vec3) -> Transform:
    """Rotation about a line through ``about``: ``p' = R (p - about) + about``."""
    rotation = Transform.from_axis_angle(axis, angle_deg)
    return Transform(origin=sub(about, rotation.apply_direction(about)), rotation=rotation.rotation)


# --- shapes and intersection volumes (7.4) -------------------------------------------------


class BoxShape(BaseModel, frozen=True):
    """Axis-aligned box in world coordinates."""

    min: Vec3
    max: Vec3


class CylinderShape(BaseModel, frozen=True):
    """Right cylinder: base disc centred at ``axis.origin``, growing ``height`` along ``axis.direction``."""

    axis: Axis
    radius: float
    height: float


Shape = BoxShape | CylinderShape


def shape_volume(shape: Shape) -> float:
    if isinstance(shape, BoxShape):
        return box_volume(sub(shape.max, shape.min))
    return cylinder_volume(shape.radius, shape.height)


def shape_bbox(shape: Shape) -> BoundingBox:
    if isinstance(shape, BoxShape):
        return BoundingBox(min=shape.min, max=shape.max)
    return cylinder_bbox(shape.axis, shape.radius, 0.0, shape.height)


def transform_shape(shape: Shape, transform: Transform) -> Shape | None:
    """Move a shape; a box survives only principal rotations (it must stay axis-aligned)."""
    if isinstance(shape, CylinderShape):
        origin = transform.apply(shape.axis.origin)
        direction = transform.apply_direction(shape.axis.direction)
        return CylinderShape(axis=Axis(origin=origin, direction=direction), radius=shape.radius, height=shape.height)
    if not is_principal_rotation(transform):
        return None
    box = bbox_transform(BoundingBox(min=shape.min, max=shape.max), transform)
    return BoxShape(min=box.min, max=box.max)


def scale_shape(shape: Shape, factor: float, about: Vec3) -> Shape:
    """Scale a tracked primitive uniformly about a point: a box stays a box, a cylinder a right cylinder."""
    if isinstance(shape, CylinderShape):
        return CylinderShape(
            axis=Axis(origin=scale_point(shape.axis.origin, factor, about), direction=shape.axis.direction),
            radius=shape.radius * factor,
            height=shape.height * factor,
        )
    return BoxShape(min=scale_point(shape.min, factor, about), max=scale_point(shape.max, factor, about))


def reflect_shape(shape: Shape, origin: Vec3, normal: Vec3) -> Shape | None:
    """Mirror a tracked primitive: a cylinder stays a right cylinder across any plane, a box only across an
    axis-aligned one (``None`` otherwise: it would no longer be axis-aligned)."""
    if isinstance(shape, CylinderShape):
        return CylinderShape(
            axis=Axis(
                origin=reflect_point(shape.axis.origin, origin, normal),
                direction=reflect_direction(shape.axis.direction, normal),
            ),
            radius=shape.radius,
            height=shape.height,
        )
    if not plane_axis_aligned(normal):
        return None
    box = bbox_reflect(BoundingBox(min=shape.min, max=shape.max), origin, normal)
    return BoxShape(min=box.min, max=box.max)


def _interval_overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _principal_axis_index(direction: Vec3) -> int | None:
    for i in range(3):
        if abs(abs(direction[i]) - 1.0) <= 1e-9:
            return i
    return None


def _cylinder_axial_range(c: CylinderShape, index: int) -> tuple[float, float]:
    o = c.axis.origin[index]
    e = o + c.axis.direction[index] * c.height
    return (min(o, e), max(o, e))


def _box_box(a: BoxShape, b: BoxShape) -> float:
    v = 1.0
    for i in range(3):
        v *= _interval_overlap(a.min[i], a.max[i], b.min[i], b.max[i])
    return v


def _cylinder_cylinder(a: CylinderShape, b: CylinderShape) -> float | None:
    if not parallel(a.axis.direction, b.axis.direction):
        return None
    if distance_point_to_axis(b.axis.origin, a.axis) > 1e-6:
        return None
    d = a.axis.direction
    a0 = dot(sub(a.axis.origin, a.axis.origin), d)
    a1 = a0 + a.height
    b_start = dot(sub(b.axis.origin, a.axis.origin), d)
    b_end = b_start + b.height * dot(b.axis.direction, d)
    overlap = _interval_overlap(a0, a1, min(b_start, b_end), max(b_start, b_end))
    return math.pi * min(a.radius, b.radius) ** 2 * overlap


def _cylinder_box(c: CylinderShape, box: BoxShape) -> float | None:
    index = _principal_axis_index(c.axis.direction)
    if index is None:
        return None
    axial = _interval_overlap(*_cylinder_axial_range(c, index), box.min[index], box.max[index])
    lateral = [i for i in range(3) if i != index]
    centre = c.axis.origin
    disc_inside = all(
        centre[i] - c.radius >= box.min[i] - 1e-9 and centre[i] + c.radius <= box.max[i] + 1e-9 for i in lateral
    )
    if disc_inside:
        return math.pi * c.radius * c.radius * axial
    if axial <= 0.0:
        return 0.0
    disc_disjoint = any(centre[i] + c.radius <= box.min[i] or centre[i] - c.radius >= box.max[i] for i in lateral)
    if disc_disjoint:
        return 0.0
    return None


def intersection_volume(p: Shape, q: Shape) -> float | None:
    """Intersection volume for the exact cases; ``None`` = not computable."""
    if isinstance(p, BoxShape) and isinstance(q, BoxShape):
        return _box_box(p, q)
    if isinstance(p, CylinderShape) and isinstance(q, CylinderShape):
        return _cylinder_cylinder(p, q)
    if isinstance(p, CylinderShape) and isinstance(q, BoxShape):
        return _cylinder_box(p, q)
    if isinstance(p, BoxShape) and isinstance(q, CylinderShape):
        return _cylinder_box(q, p)
    return None
