"""Sketch entities, annotations and the shared profile helper (contract/sketch.py).

Entities are plain geometry (no references) and are re-exported by the IR.
Profile rule: for extrude/cut/revolve a sketch contains
one or more closed, disjoint profiles: a rect, a square, a circle, a slot, or a closed
chain of line/arc entities (arcs of any of the three forms below chain with lines and with each other).

A ``square`` is declared intent, never inferred from ``width == height``: it is
dimensioned by ONE driving ``side`` (adapters add an equal relation between two
adjacent sides), where a rect always carries two driving dimensions.

Arcs are circular and come in three equivalent forms: ``arc`` (centre, radius, start/end angle, counter-clockwise),
``arc3`` (start, a point on the arc, end) and ``arc_ends`` (centre, start, end, direction). ``arc_geometry``
reduces each to one normal form, so every adapter builds the same arc. A ``slot`` is a stadium: two semicircles of
diameter ``width`` centred on ``start`` and ``end``, joined by two lines.
"""

import math
from typing import Annotated, Literal, NamedTuple

from pydantic import BaseModel, Field, model_validator

Point2 = tuple[float, float]

ARC_STEP_RAD = math.radians(1.0)
"""An arc is sampled at this angle where a profile needs points (areas and the fake's solids stay within 1e-4)."""
_ARC_ON_CIRCLE_MM = 1e-5
_TWO_PI = 2.0 * math.pi


class RectEntity(BaseModel, frozen=True, allow_inf_nan=False):
    kind: Literal["rect"] = "rect"
    name: str
    center: Point2
    width: float
    height: float
    chamfer_mm: float | None = None
    """Cuts the four corners at 45 degrees, ``chamfer_mm`` along each side (one driving dimension, equal on all
    corners). ``None`` (the default) leaves the corners sharp. Its profile is then a polygon, not a ``rect``."""

    @model_validator(mode="after")
    def _check_chamfer(self) -> "RectEntity":
        if self.chamfer_mm is None:
            return self
        limit = min(self.width, self.height) / 2.0
        if self.chamfer_mm <= 0 or self.chamfer_mm >= limit:
            msg = (
                f"rect {self.name}: chamfer_mm must be greater than 0 and smaller than half of the shorter side "
                f"({limit:.6g} mm), got {self.chamfer_mm:.6g}"
            )
            raise ValueError(msg)
        return self


class SquareEntity(BaseModel, frozen=True, allow_inf_nan=False):
    kind: Literal["square"] = "square"
    name: str
    center: Point2
    side: float


class CircleEntity(BaseModel, frozen=True, allow_inf_nan=False):
    kind: Literal["circle"] = "circle"
    name: str
    center: Point2
    diameter: float


class LineEntity(BaseModel, frozen=True, allow_inf_nan=False):
    kind: Literal["line"] = "line"
    name: str
    start: Point2
    end: Point2


class ArcEntity(BaseModel, frozen=True, allow_inf_nan=False):
    """Circular arc by centre and radius, counter-clockwise from ``start_deg`` to ``end_deg`` (angles from +u)."""

    kind: Literal["arc"] = "arc"
    name: str
    center: Point2
    radius: float
    start_deg: float
    end_deg: float

    @model_validator(mode="after")
    def _check_geometry(self) -> "ArcEntity":
        arc_geometry(self)
        return self


class Arc3Entity(BaseModel, frozen=True, allow_inf_nan=False):
    """Circular arc from ``start`` through ``through`` (any point of the arc between the ends) to ``end``."""

    kind: Literal["arc3"] = "arc3"
    name: str
    start: Point2
    through: Point2
    end: Point2

    @model_validator(mode="after")
    def _check_geometry(self) -> "Arc3Entity":
        arc_geometry(self)
        return self


class ArcEndsEntity(BaseModel, frozen=True, allow_inf_nan=False):
    """Circular arc about ``center`` from ``start`` to ``end`` (both the same distance from the centre), going
    counter-clockwise unless ``ccw`` is false."""

    kind: Literal["arc_ends"] = "arc_ends"
    name: str
    center: Point2
    start: Point2
    end: Point2
    ccw: bool = True

    @model_validator(mode="after")
    def _check_geometry(self) -> "ArcEndsEntity":
        arc_geometry(self)
        return self


class SlotEntity(BaseModel, frozen=True, allow_inf_nan=False):
    """Stadium profile: ``start`` and ``end`` are the centres of the two end semicircles, ``width`` their diameter."""

    kind: Literal["slot"] = "slot"
    name: str
    start: Point2
    end: Point2
    width: float

    @model_validator(mode="after")
    def _check_geometry(self) -> "SlotEntity":
        if self.width <= 0:
            msg = f"slot {self.name}: width must be positive"
            raise ValueError(msg)
        if math.dist(self.start, self.end) < 1e-9:
            msg = f"slot {self.name}: the end centres coincide (use a circle)"
            raise ValueError(msg)
        return self


SketchEntity = Annotated[
    RectEntity | SquareEntity | CircleEntity | LineEntity | ArcEntity | Arc3Entity | ArcEndsEntity | SlotEntity,
    Field(discriminator="kind"),
]

_ArcLike = ArcEntity | Arc3Entity | ArcEndsEntity


class SketchConstraint(BaseModel, frozen=True):
    kind: Literal["horizontal", "vertical", "coincident", "concentric", "equal", "tangent", "symmetric"]
    entities: tuple[str, ...]


class NamedDimension(BaseModel, frozen=True):
    name: str
    entity: str
    attribute: Literal["width", "height", "side", "diameter", "radius", "length", "chamfer", "u", "v"]


class Profile(BaseModel, frozen=True):
    kind: Literal["rect", "circle", "polygon"]
    area_mm2: float
    centroid: Point2
    bbox_uv: tuple[Point2, Point2]
    points: tuple[Point2, ...] | None = None
    """The outline of a polygon; an arc is sampled finely (``ARC_STEP_RAD``), so these are not its edges."""
    center: Point2 | None = None
    radius: float | None = None
    sides: int | None = None
    """Boundary edges (lines and arcs) of a polygon that has arcs; ``None`` when every point is a corner."""


_POINT_TOLERANCE = 1e-6


def _points_close(a: Point2, b: Point2, tol: float = _POINT_TOLERANCE) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


# --- arcs -------------------------------------------------------------------------------------------------------


class ArcGeometry(NamedTuple):
    """The normal form of an arc: from ``start_angle`` counter-clockwise by ``sweep`` (radians, in (0, 2 pi))."""

    center: Point2
    radius: float
    start_angle: float
    sweep: float

    def point(self, angle: float) -> Point2:
        return (self.center[0] + self.radius * math.cos(angle), self.center[1] + self.radius * math.sin(angle))


def _angle(center: Point2, p: Point2) -> float:
    return math.atan2(p[1] - center[1], p[0] - center[0])


def _circumcentre(a: Point2, b: Point2, c: Point2) -> Point2:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if abs(cross) < 1e-9 * max(1.0, math.dist(a, b) * math.dist(a, c)):
        msg = f"the three points {a}, {b}, {c} are collinear: that is a line, not an arc"
        raise ValueError(msg)
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    return (
        (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
        (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d,
    )


def arc_geometry(entity: "_ArcLike") -> ArcGeometry:
    """The normal form of any arc entity; ``ValueError`` (with the reason) when it is not a valid arc."""
    if isinstance(entity, ArcEntity):
        if entity.radius <= 0:
            msg = f"arc {entity.name}: radius must be positive"
            raise ValueError(msg)
        sweep = math.radians(entity.end_deg - entity.start_deg) % _TWO_PI
        if sweep < 1e-9 or _TWO_PI - sweep < 1e-9:
            msg = f"arc {entity.name}: start_deg and end_deg coincide (use a circle for a full turn)"
            raise ValueError(msg)
        return ArcGeometry(entity.center, entity.radius, math.radians(entity.start_deg), sweep)
    if isinstance(entity, Arc3Entity):
        center = _circumcentre(entity.start, entity.through, entity.end)
        first, middle, last = (_angle(center, p) for p in (entity.start, entity.through, entity.end))
        radius = math.dist(center, entity.start)
        ccw_sweep = (last - first) % _TWO_PI
        if (middle - first) % _TWO_PI < ccw_sweep:
            return ArcGeometry(center, radius, first, ccw_sweep)
        return ArcGeometry(center, radius, last, (first - last) % _TWO_PI)
    radius, radius_end = math.dist(entity.center, entity.start), math.dist(entity.center, entity.end)
    if radius < 1e-9:
        msg = f"arc {entity.name}: start coincides with the centre"
        raise ValueError(msg)
    if abs(radius - radius_end) > _ARC_ON_CIRCLE_MM * max(1.0, radius):
        msg = (
            f"arc {entity.name}: start and end must be the same distance from center "
            f"({radius:.6g} and {radius_end:.6g} mm)"
        )
        raise ValueError(msg)
    first, last = _angle(entity.center, entity.start), _angle(entity.center, entity.end)
    ccw_sweep = (last - first) % _TWO_PI if entity.ccw else (first - last) % _TWO_PI
    if ccw_sweep < 1e-9 or _TWO_PI - ccw_sweep < 1e-9:
        msg = f"arc {entity.name}: start and end coincide (use a circle for a full turn)"
        raise ValueError(msg)
    if entity.ccw:
        return ArcGeometry(entity.center, radius, first, ccw_sweep)
    return ArcGeometry(entity.center, radius, last, ccw_sweep)


# --- profiles ---------------------------------------------------------------------------------------------------


def _rect_profile(entity: RectEntity) -> Profile:
    cx, cy = entity.center
    half_w, half_h = entity.width / 2.0, entity.height / 2.0
    bbox_uv = ((cx - half_w, cy - half_h), (cx + half_w, cy + half_h))
    if entity.chamfer_mm is not None:  # eight corners, counter-clockwise: a polygon, not a rect
        c = entity.chamfer_mm
        (u0, v0), (u1, v1) = bbox_uv
        points = (
            (u0 + c, v0),
            (u1 - c, v0),
            (u1, v0 + c),
            (u1, v1 - c),
            (u1 - c, v1),
            (u0 + c, v1),
            (u0, v1 - c),
            (u0, v0 + c),
        )
        return Profile(
            kind="polygon",
            area_mm2=entity.width * entity.height - 2.0 * c * c,
            centroid=(cx, cy),
            bbox_uv=bbox_uv,
            points=points,
        )
    return Profile(
        kind="rect",
        area_mm2=entity.width * entity.height,
        centroid=(cx, cy),
        bbox_uv=bbox_uv,
        center=(cx, cy),
    )


def _square_profile(entity: SquareEntity) -> Profile:
    """A square's profile is geometrically a rect (``kind="rect"``): downstream area, point and coverage
    rules do not depend on how the sketch is dimensioned. The intent lives on the entity only."""
    cx, cy = entity.center
    half = entity.side / 2.0
    bbox_uv = ((cx - half, cy - half), (cx + half, cy + half))
    return Profile(
        kind="rect",
        area_mm2=entity.side * entity.side,
        centroid=(cx, cy),
        bbox_uv=bbox_uv,
        center=(cx, cy),
    )


def _circle_profile(entity: CircleEntity) -> Profile:
    cx, cy = entity.center
    radius = entity.diameter / 2.0
    bbox_uv = ((cx - radius, cy - radius), (cx + radius, cy + radius))
    return Profile(
        kind="circle",
        area_mm2=3.141592653589793 * radius * radius,
        centroid=(cx, cy),
        bbox_uv=bbox_uv,
        center=(cx, cy),
        radius=radius,
    )


class _Segment(NamedTuple):
    """One edge of a chain: ``start`` to ``end`` as the entity states them; for an arc, ``ccw`` says whether going from
    ``start`` to ``end`` turns counter-clockwise about the centre of ``arc``."""

    start: Point2
    end: Point2
    arc: ArcGeometry | None = None
    ccw: bool = True


def _segment(entity: "LineEntity | _ArcLike") -> _Segment:
    if isinstance(entity, LineEntity):
        return _Segment(entity.start, entity.end)
    geometry = arc_geometry(entity)
    if isinstance(entity, ArcEntity):
        first = geometry.point(geometry.start_angle)
        return _Segment(first, geometry.point(geometry.start_angle + geometry.sweep), geometry)
    if isinstance(entity, Arc3Entity):  # the normal form starts at whichever end it turns counter-clockwise from
        forward = _points_close(geometry.point(geometry.start_angle), entity.start, 1e-6 * max(1.0, geometry.radius))
        return _Segment(entity.start, entity.end, geometry, forward)
    return _Segment(entity.start, entity.end, geometry, entity.ccw)


def _slot_segments(slot: SlotEntity) -> list[_Segment]:
    length = math.dist(slot.start, slot.end)
    dx, dy = (slot.end[0] - slot.start[0]) / length, (slot.end[1] - slot.start[1]) / length
    r = slot.width / 2.0
    nx, ny = -dy * r, dx * r  # the left normal of the slot axis, radius long
    a, b = (slot.start[0] + nx, slot.start[1] + ny), (slot.end[0] + nx, slot.end[1] + ny)
    c, d = (slot.end[0] - nx, slot.end[1] - ny), (slot.start[0] - nx, slot.start[1] - ny)
    heading = math.atan2(dy, dx)
    at_end = ArcGeometry(slot.end, r, heading - math.pi / 2, math.pi)  # from c, through the end, to b
    at_start = ArcGeometry(slot.start, r, heading + math.pi / 2, math.pi)  # from a, through the back, to d
    return [_Segment(a, b), _Segment(b, c, at_end, False), _Segment(c, d), _Segment(a, d, at_start, True)]


def _chain_into_loops(segments: list[_Segment]) -> list[list[tuple[_Segment, bool]]]:
    """Group segments into closed loops by matching shared endpoints; each segment is paired with whether the loop runs
    it end to start."""
    remaining = list(segments)
    loops: list[list[tuple[_Segment, bool]]] = []
    while remaining:
        first = remaining.pop(0)
        start, current = first.start, first.end
        loop = [(first, False)]
        while not _points_close(current, start):
            found: tuple[int, bool] | None = None
            for index, other in enumerate(remaining):
                if _points_close(other.start, current):
                    found = (index, False)
                    break
                if _points_close(other.end, current):
                    found = (index, True)
                    break
            if found is None:
                msg = f"open chain of line/arc entities: no entity continues from point {current}"
                raise ValueError(msg)
            other = remaining.pop(found[0])
            current = other.start if found[1] else other.end
            loop.append((other, found[1]))
        loops.append(loop)
    return loops


def arc_sample_angles(start: float, delta: float) -> list[float]:
    """The angles strictly between the ends of an arc (``delta`` is signed, from ``start``) at which its outline is
    sampled: every ``ARC_STEP_RAD`` and every quarter turn, so the extremes of the arc are points of the outline."""
    steps = max(2, math.ceil(abs(delta) / ARC_STEP_RAD))
    angles = [start + delta * k / steps for k in range(1, steps)]
    low, high = sorted((start, start + delta))
    quarter = math.pi / 2.0
    for k in range(math.ceil(low / quarter), math.floor(high / quarter) + 1):
        angle = k * quarter
        if low + 1e-9 < angle < high - 1e-9 and all(abs(angle - a) > 1e-9 for a in angles):
            angles.append(angle)
    return sorted(angles, reverse=delta < 0)


def _loop_profile(loop: list[tuple[_Segment, bool]]) -> Profile:
    """Exact area and centroid of a closed loop of lines and arcs (Green's theorem, edge by edge) and its outline as
    points, arcs sampled every ``ARC_STEP_RAD``."""
    area2 = mx = my = 0.0  # twice the signed area, the integrals of x^2 dy and of y^2 dx
    points: list[Point2] = []
    for segment, reverse in loop:
        p, q = (segment.end, segment.start) if reverse else (segment.start, segment.end)
        dx, dy = q[0] - p[0], q[1] - p[1]
        points.append(p)
        if segment.arc is None:
            area2 += p[0] * q[1] - q[0] * p[1]
            mx += dy * (p[0] ** 2 + p[0] * dx + dx**2 / 3.0)
            my += dx * (p[1] ** 2 + p[1] * dy + dy**2 / 3.0)
            continue
        cx, cy = segment.arc.center
        r = segment.arc.radius
        forward = segment.ccw != reverse
        t0 = segment.arc.start_angle if forward else segment.arc.start_angle + segment.arc.sweep
        delta = segment.arc.sweep if forward else -segment.arc.sweep
        t1 = t0 + delta
        sin0, sin1, cos0, cos1 = math.sin(t0), math.sin(t1), math.cos(t0), math.cos(t1)
        area2 += r * r * delta + r * cx * (sin1 - sin0) - r * cy * (cos1 - cos0)
        mx += r * (
            cx * cx * (sin1 - sin0)
            + 2 * cx * r * (delta / 2 + (math.sin(2 * t1) - math.sin(2 * t0)) / 4)
            + r * r * ((sin1 - sin0) - (sin1**3 - sin0**3) / 3)
        )
        my -= r * (
            cy * cy * (cos0 - cos1)
            + 2 * cy * r * (delta / 2 - (math.sin(2 * t1) - math.sin(2 * t0)) / 4)
            + r * r * ((cos0 - cos1) + (cos1**3 - cos0**3) / 3)
        )
        points.extend(segment.arc.point(angle) for angle in arc_sample_angles(t0, delta))
    signed_area = area2 / 2.0
    if abs(signed_area) < 1e-12:
        msg = "degenerate profile: zero area"
        raise ValueError(msg)
    centroid = (mx / 2.0 / signed_area, -my / 2.0 / signed_area)
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return Profile(
        kind="polygon",
        area_mm2=abs(signed_area),
        centroid=centroid,
        bbox_uv=((min(xs), min(ys)), (max(xs), max(ys))),
        points=tuple(points),
        sides=len(loop) if any(segment.arc is not None for segment, _ in loop) else None,
    )


def sketch_profiles(entities: "tuple[SketchEntity, ...] | list") -> list[Profile]:
    """Compute closed profiles from a sketch's entities.

    Rects, squares, circles and slots are each their own profile. Lines and arcs are chained into
    closed loops by matching endpoints (``ValueError`` names the point where a chain breaks). Disjointness beyond
    distinct entities is not checked.
    """
    profiles = [_rect_profile(e) for e in entities if isinstance(e, RectEntity)]
    profiles.extend(_square_profile(e) for e in entities if isinstance(e, SquareEntity))
    profiles.extend(_circle_profile(e) for e in entities if isinstance(e, CircleEntity))
    for slot in (e for e in entities if isinstance(e, SlotEntity)):
        profiles.extend(_loop_profile(loop) for loop in _chain_into_loops(_slot_segments(slot)))
    chain = [_segment(e) for e in entities if isinstance(e, LineEntity | ArcEntity | Arc3Entity | ArcEndsEntity)]
    profiles.extend(_loop_profile(loop) for loop in _chain_into_loops(chain))
    return profiles


# --- nesting (even-odd) -----------------------------------------------------------------------------------------


def _boundary_point(profile: Profile) -> Point2:
    """A point ON the outline of ``profile`` (used to ask which other profiles contain it)."""
    if profile.kind == "circle" and profile.center is not None and profile.radius is not None:
        return (profile.center[0] + profile.radius, profile.center[1])
    if profile.points:
        return profile.points[0]
    return profile.bbox_uv[0]


def _contains(outer: Profile, point: Point2) -> bool:
    if outer.kind == "circle" and outer.center is not None and outer.radius is not None:
        return math.dist(outer.center, point) < outer.radius
    if outer.kind == "rect" or not outer.points:
        (u0, v0), (u1, v1) = outer.bbox_uv
        return u0 < point[0] < u1 and v0 < point[1] < v1
    inside = False
    pts = outer.points
    for i, (x0, y0) in enumerate(pts):
        x1, y1 = pts[(i + 1) % len(pts)]
        if (y0 > point[1]) != (y1 > point[1]) and point[0] < (x1 - x0) * (point[1] - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def profile_depths(profiles: "list[Profile] | tuple[Profile, ...]") -> list[int]:
    """How many other profiles of the sketch enclose each one: 0 is an outer loop (material), 1 a loop inside it (a hole
    in the material), 2 an island inside that hole, and so on. Even depths add material, odd depths take it away
    (the even-odd rule: an annulus is an outer circle with a circle in it). A profile only counts as enclosed by a
    LARGER one, so two loops never enclose each other."""
    depths: list[int] = []
    for i, inner in enumerate(profiles):
        point = _boundary_point(inner)
        depths.append(
            sum(
                1
                for j, outer in enumerate(profiles)
                if j != i and outer.area_mm2 > inner.area_mm2 and _contains(outer, point)
            )
        )
    return depths


def net_area(profiles: "list[Profile] | tuple[Profile, ...]") -> float:
    """The area of the region the profiles bound by the even-odd rule (an annulus is outer minus inner)."""
    return sum(p.area_mm2 * (1 if d % 2 == 0 else -1) for p, d in zip(profiles, profile_depths(profiles), strict=True))


def has_nested(profiles: "list[Profile] | tuple[Profile, ...]") -> bool:
    return any(d > 0 for d in profile_depths(profiles))
