"""Pure math of the ``sweep`` operation: a constant section moved along a polyline.

Nothing here touches an adapter: every function takes numbers, vectors, profiles and frames. The executor
runs the placement and clearance checks BEFORE the adapter is called (a bad sweep never reaches the CAD
kernel), and the expected-value computer derives the volume, face count and bounding boxes from the same
analysis.

Model. The path is a polyline ``P0 .. Pn`` with unit segment directions ``d_i``. The section is a closed
profile lying in the plane through ``P0`` normal to ``d_0``, with its centroid at ``P0``. Corners are either

- mitred (``corner_radius=None``): consecutive segment prisms meet on the bisector plane of the corner, or
- filleted (``corner_radius=R``): a circular arc of radius ``R`` tangent to both segments replaces the
  corner; the tangent length is ``R tan(phi / 2)`` with ``phi`` the turn angle.

In both cases the volume is exactly ``area x path length`` (Pappus / prismatoid: the section stays normal
to the path and its centroid stays on it), with the path length of a filleted path shortened by
``2 R tan(phi/2) - R phi`` per corner.
"""

import math
from dataclasses import dataclass

from cotaforge.contract import Axis, BoundingBox, Profile, SketchFrame, Vec3
from cotaforge.verification import formulas as f
from cotaforge.verification.thresholds import DEFAULT_THRESHOLDS, Thresholds

_TOL = 1e-6
_PARALLEL_SIN = 1e-6
FILLET_RADIUS_FACTOR = 1.02
"""A filleted corner needs ``R > FILLET_RADIUS_FACTOR x extent``, the extent being how far the (transported) section
reaches toward the bend centre: at ``R`` equal to the extent the inner side of the bend degenerates (a horn
torus) and the kernel returns an invalid shape (measured on FreeCAD 1.0.2 and 1.1.3)."""


class SweepError(ValueError):
    """A sweep the harness refuses before any adapter call; the message says what to change."""


@dataclass(frozen=True)
class PathAnalysis:
    """Segments and corners of a polyline path; ``length`` is the length of the swept centre line."""

    points: tuple[Vec3, ...]
    corner_radius: float | None
    segment_lengths: tuple[float, ...]
    directions: tuple[Vec3, ...]
    turn_angles: tuple[float, ...]
    """Radians, one per interior vertex: the angle between the incoming and the outgoing direction."""
    tangent_lengths: tuple[float, ...]
    """``R tan(phi/2)`` per interior vertex for a filleted path, zeros for a mitred one."""
    length: float

    @property
    def segment_count(self) -> int:
        return len(self.segment_lengths)

    @property
    def corner_count(self) -> int:
        return len(self.turn_angles)


def _fmt(p: Vec3) -> str:
    return "(" + ", ".join(f"{c:g}" for c in p) + ")"


def analyse_path(points: tuple[Vec3, ...] | list[Vec3], corner_radius: float | None = None) -> PathAnalysis:
    """Segment lengths, turn angles and centre-line length; ``SweepError`` for a path that cannot be swept.

    A filleted path also needs every segment to be long enough to hold the tangent lengths of its two
    ends (``t_i + t_{i+1} < |segment|``)."""
    pts = tuple((float(p[0]), float(p[1]), float(p[2])) for p in points)
    if len(pts) < 2:
        msg = "a sweep path needs at least 2 points"
        raise SweepError(msg)
    if corner_radius is not None and not (math.isfinite(corner_radius) and corner_radius > 0.0):
        msg = f"corner_radius_mm must be a positive number, got {corner_radius!r}"
        raise SweepError(msg)
    lengths: list[float] = []
    directions: list[Vec3] = []
    for i in range(len(pts) - 1):
        delta = f.sub(pts[i + 1], pts[i])
        seg = f.length(delta)
        if seg <= _TOL:
            msg = f"path points {i} and {i + 1} coincide at {_fmt(pts[i])}: remove the duplicate point"
            raise SweepError(msg)
        lengths.append(seg)
        directions.append(f.scale(delta, 1.0 / seg))
    turns: list[float] = []
    for i in range(1, len(pts) - 1):
        d_in, d_out = directions[i - 1], directions[i]
        cos = max(-1.0, min(1.0, f.dot(d_in, d_out)))
        sin = f.length(f.cross(d_in, d_out))
        phi = math.atan2(sin, cos)
        if phi <= _PARALLEL_SIN:
            msg = (
                f"path point {i} {_fmt(pts[i])} is collinear with its neighbours: drop the redundant point "
                "(a straight run is one segment)"
            )
            raise SweepError(msg)
        if math.pi - phi <= _PARALLEL_SIN:
            msg = f"the path reverses on itself at point {i} {_fmt(pts[i])}: a sweep cannot fold back over a segment"
            raise SweepError(msg)
        turns.append(phi)
    if corner_radius is None:
        tangents = [0.0] * len(turns)
    else:
        tangents = [corner_radius * math.tan(phi / 2.0) for phi in turns]
        for i, seg in enumerate(lengths):
            start_t = tangents[i - 1] if i > 0 else 0.0
            end_t = tangents[i] if i < len(tangents) else 0.0
            if start_t + end_t >= seg - _TOL:
                msg = (
                    f"corner_radius_mm {corner_radius:g} does not fit path segment {i} "
                    f"({_fmt(pts[i])} -> {_fmt(pts[i + 1])}, length {seg:g} mm): its two corners need "
                    f"{start_t + end_t:g} mm of tangent length. Use a smaller radius or lengthen the segment"
                )
                raise SweepError(msg)
    length = sum(lengths)
    if corner_radius is not None:
        length += sum(corner_radius * phi - 2.0 * t for phi, t in zip(turns, tangents, strict=True))
    return PathAnalysis(
        points=pts,
        corner_radius=corner_radius,
        segment_lengths=tuple(lengths),
        directions=tuple(directions),
        turn_angles=tuple(turns),
        tangent_lengths=tuple(tangents),
        length=length,
    )


def path_length(points: tuple[Vec3, ...] | list[Vec3], corner_radius: float | None = None) -> float:
    return analyse_path(points, corner_radius).length


def swept_volume(area: float, length: float) -> float:
    """``area x path length``."""
    return area * length


def circle_face_count(analysis: PathAnalysis) -> int:
    """Faces a circular section creates: 2 caps, one lateral face per segment and, for a filleted path,
    one toroidal face per corner. (Polygon sections merge or split lateral faces unpredictably.)"""
    arcs = analysis.corner_count if analysis.corner_radius is not None else 0
    return 2 + analysis.segment_count + arcs


def section_reach(profile: Profile) -> float:
    """Distance from the profile's centroid to its farthest boundary point (the section's half-width in the
    worst direction). A circle's is its radius, a rectangle's its half-diagonal."""
    cu, cv = profile.centroid
    if profile.kind == "circle" and profile.radius is not None:
        return profile.radius
    (u0, v0), (u1, v1) = profile.bbox_uv
    if profile.kind == "polygon" and profile.points:
        corners = list(profile.points)
    else:
        corners = [(u0, v0), (u0, v1), (u1, v0), (u1, v1)]
    return max(math.hypot(u - cu, v - cv) for u, v in corners)


def check_placement(
    frame: SketchFrame,
    points: tuple[Vec3, ...] | list[Vec3],
    centroid_world: Vec3,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    reach: float = 0.0,
) -> None:
    """The section must sit on the path start: plane normal to the first segment, through ``P0``, with its
    centroid at ``P0``. ``SweepError`` otherwise, naming the change that fixes it.

    A small tilt is only tolerated while the far side of the section (``reach`` away) stays within the position
    tolerance of where an exact normal would put it: ``reach x sin(angle) <= position_mm``. The expectations and
    the boxes are computed along the path direction, so a larger drift would fail verification later."""
    start = points[0]
    delta = f.sub(points[1], start)
    if f.length(delta) <= _TOL:
        msg = f"path points 0 and 1 coincide at {_fmt(start)}"
        raise SweepError(msg)
    direction = f.normalise(delta)
    if not thresholds.parallel(frame.n, direction, either_sense=True):
        cos = abs(f.dot(f.normalise(frame.n), direction))
        angle = math.degrees(math.acos(min(1.0, cos)))
        msg = (
            f"the profile plane must be normal to the first path segment: the sketch normal {_fmt(frame.n)} "
            f"is {angle:.1f} deg off the segment direction {_fmt(direction)}. Sketch on a datum plane normal to "
            "the path start (ref_axis two_points along the first segment, then ref_plane normal_to at_mm: 0)"
        )
        raise SweepError(msg)
    cos = abs(f.dot(f.normalise(frame.n), direction))
    tilt = math.acos(min(1.0, cos))
    if reach * math.sin(tilt) > thresholds.position_mm:
        msg = (
            f"the path direction is {math.degrees(tilt):.2f} deg off the sketch normal, which moves the far side of "
            f"the section (reach {reach:g} mm) by {reach * math.sin(tilt):.3g} mm along the path: make the first "
            "segment exactly along the normal (sketch on a datum from ref_axis two_points along that segment, "
            "then ref_plane normal_to at_mm: 0)"
        )
        raise SweepError(msg)
    off_plane = abs(f.dot(f.sub(start, frame.origin), f.normalise(frame.n)))
    if off_plane > thresholds.position_mm:
        msg = (
            f"the profile plane does not pass through the path start {_fmt(start)}: it is {off_plane:.3g} mm "
            f"away. Move the plane to the path start (its origin is {_fmt(frame.origin)})"
        )
        raise SweepError(msg)
    if not thresholds.close_position(start, centroid_world):
        msg = (
            f"the profile centroid {_fmt(centroid_world)} is not at the path start {_fmt(start)}: move the "
            "profile so its centroid (u=0, v=0 for a centred circle or rect) sits at the path start, or start "
            f"the path at world {_fmt(centroid_world)}"
        )
        raise SweepError(msg)


def _segment_distance(p1: Vec3, q1: Vec3, p2: Vec3, q2: Vec3) -> float:
    """Minimum distance between the 3D segments ``p1 q1`` and ``p2 q2`` (both non-degenerate)."""
    d1 = f.sub(q1, p1)
    d2 = f.sub(q2, p2)
    r = f.sub(p1, p2)
    a, e = f.dot(d1, d1), f.dot(d2, d2)
    b, c, g = f.dot(d1, d2), f.dot(d1, r), f.dot(d2, r)
    denom = a * e - b * b
    s = min(1.0, max(0.0, (b * g - c * e) / denom)) if denom > 1e-12 * a * e else 0.0
    t = (b * s + g) / e
    if t < 0.0:
        t = 0.0
        s = min(1.0, max(0.0, -c / a))
    elif t > 1.0:
        t = 1.0
        s = min(1.0, max(0.0, (b - c) / a))
    return f.length(f.sub(f.add(p1, f.scale(d1, s)), f.add(p2, f.scale(d2, t))))


def corner_extents(analysis: PathAnalysis, profile: Profile, frame: SketchFrame) -> tuple[float, ...]:
    """Per corner, how far the section reaches toward the bend centre (the inner side of the bend).

    That is what limits a fillet: the inner wall has radius ``R - extent``. A circle reaches its radius; a polygon
    or rectangle reaches its farthest vertex along the in-plane direction pointing at the centre, for the section as
    parallel-transported through the earlier bends (the mirror across each bisector plane, the same transport
    ``_polygon_face_points`` uses). A 5 x 40 strap bent on its thin side reaches 2.5, on its wide side 20."""
    if analysis.corner_count == 0:
        return ()
    if profile.kind == "circle" and profile.radius is not None:
        return (profile.radius,) * analysis.corner_count
    current = _section_offsets(profile, frame, analysis.points[0], analysis.directions[0])
    extents: list[float] = []
    for j in range(1, len(analysis.points) - 1):
        d_in, d_out = analysis.directions[j - 1], analysis.directions[j]
        toward_centre = f.normalise(f.perpendicular_part(d_out, d_in))
        extents.append(max(0.0, max(f.dot(o, toward_centre) for o in current)))
        m = f.normalise(f.add(d_in, d_out))
        current = [f.sub(o, f.scale(m, 2.0 * f.dot(o, m))) for o in current]
    return tuple(extents)


def check_clearance(analysis: PathAnalysis, profile: Profile, frame: SketchFrame) -> None:
    """The swept solid must not fold onto itself: ``SweepError`` when the geometry cannot be built cleanly.

    - fillet: at every corner the radius must exceed ``FILLET_RADIUS_FACTOR x`` the section's extent toward the bend
      centre (orientation-aware, see ``corner_extents``), else the inner side of the bend collapses (the kernel then
      returns an invalid shape while still reporting the right volume);
    - mitre: at every segment the mitre overhangs of its two ends (``reach x tan(phi/2)`` each) must fit in
      the segment length. This one, and the spacing rule below, use the section's full ``reach`` (centroid to the
      farthest boundary point) whatever its orientation: conservative for a flat section;
    - segments that are not neighbours must stay farther apart than the section is wide (``2 x reach``)."""
    reach = section_reach(profile)
    radius = analysis.corner_radius
    if radius is None:
        for i, seg in enumerate(analysis.segment_lengths):
            start = reach * math.tan(analysis.turn_angles[i - 1] / 2.0) if i > 0 else 0.0
            end = reach * math.tan(analysis.turn_angles[i] / 2.0) if i < analysis.corner_count else 0.0
            if start + end >= seg - _TOL:
                msg = (
                    f"path segment {i} ({_fmt(analysis.points[i])} -> {_fmt(analysis.points[i + 1])}, "
                    f"{seg:g} mm) is too short for the mitred corners at its ends: they overhang by "
                    f"{start + end:g} mm (section reach {reach:g} mm). Lengthen the segment, use corner_radius_mm, "
                    "or reduce the section"
                )
                raise SweepError(msg)
    else:
        for corner, extent in enumerate(corner_extents(analysis, profile, frame), start=1):
            if radius <= FILLET_RADIUS_FACTOR * extent:
                msg = (
                    f"corner_radius_mm {radius:g} is too small for the section at corner {corner}: the section "
                    f"reaches {extent:g} mm toward the bend centre there, so the radius must exceed "
                    f"{FILLET_RADIUS_FACTOR * extent:.3g} mm. Use a larger radius, omit corner_radius_mm to mitre, "
                    "reduce the section, or rotate the profile so its narrow side faces the bend"
                )
                raise SweepError(msg)
    pts = analysis.points
    for i in range(analysis.segment_count):
        for j in range(i + 2, analysis.segment_count):
            gap = _segment_distance(pts[i], pts[i + 1], pts[j], pts[j + 1])
            if gap <= 2.0 * reach + _TOL:
                msg = (
                    f"path segments {i} and {j} come within {gap:.3g} mm of each other but the section is "
                    f"{2.0 * reach:g} mm wide (reach {reach:g} mm): the swept solid would touch itself. "
                    "Route the path farther apart or reduce the section"
                )
                raise SweepError(msg)


# --- bounding boxes ---------------------------------------------------------------------------------------


def _section_offsets(profile: Profile, frame: SketchFrame, start: Vec3, direction: Vec3) -> list[Vec3]:
    """Vertices of a polygonal/rectangular section as offsets from ``start`` inside the plane normal to
    ``direction`` (the component along it, from a frame that is only parallel within tolerance, is dropped)."""
    if profile.kind == "polygon" and profile.points:
        corners = list(profile.points)
    else:
        (u0, v0), (u1, v1) = profile.bbox_uv
        corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    offsets: list[Vec3] = []
    centroid = frame.to_world(*profile.centroid)
    for u, v in corners:
        rel = f.sub(frame.to_world(u, v), centroid)
        offsets.append(f.perpendicular_part(rel, direction))
    return offsets


def _circle_half_extent(radius: float, a: Vec3, b: Vec3) -> Vec3:
    """Half-extent along each world axis of the ellipse ``c + cos(t) a + sin(t) b`` (``|a|``, ``|b|`` the semi
    axes, orthogonal): ``sqrt(a_k^2 + b_k^2)``."""
    return (
        radius * math.sqrt(a[0] ** 2 + b[0] ** 2),
        radius * math.sqrt(a[1] ** 2 + b[1] ** 2),
        radius * math.sqrt(a[2] ** 2 + b[2] ** 2),
    )


def _perpendicular_pair(direction: Vec3) -> tuple[Vec3, Vec3]:
    seed = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = f.normalise(f.perpendicular_part(seed, direction))
    return e1, f.cross(direction, e1)


def _ellipse_boxes(analysis: PathAnalysis, radius: float) -> list[tuple[Vec3, Vec3]]:
    """(centre, half-extent) of every end face of a mitred circular tube: the two caps and one mitre ellipse
    per corner. A segment is the convex hull of its two end faces, so these ellipses carry the whole box."""
    pts = analysis.points
    dirs = analysis.directions
    out: list[tuple[Vec3, Vec3]] = []
    e1, e2 = _perpendicular_pair(dirs[0])
    out.append((pts[0], _circle_half_extent(radius, e1, e2)))
    for j in range(1, len(pts) - 1):
        d_in, d_out = dirs[j - 1], dirs[j]
        m = f.normalise(f.add(d_in, d_out))
        in_plane = f.perpendicular_part(m, d_in)
        e1 = f.normalise(in_plane)
        tan_half = math.tan(analysis.turn_angles[j - 1] / 2.0)
        a = f.sub(e1, f.scale(d_in, tan_half))
        b = f.cross(d_in, e1)
        out.append((pts[j], _circle_half_extent(radius, a, b)))
    e1, e2 = _perpendicular_pair(dirs[-1])
    out.append((pts[-1], _circle_half_extent(radius, e1, e2)))
    return out


def _polygon_face_points(analysis: PathAnalysis, offsets: list[Vec3]) -> list[Vec3]:
    """World vertices of every end face of a mitred prismatic tube. The section vertices ``o`` (offsets
    perpendicular to the incoming direction) land on the mitre plane at ``P + o - (o.m / d.m) d_in`` and the
    next segment continues from their mirror image across that plane (which is the parallel transport)."""
    pts = analysis.points
    dirs = analysis.directions
    faces: list[Vec3] = [f.add(pts[0], o) for o in offsets]
    current = offsets
    for j in range(1, len(pts) - 1):
        d_in, d_out = dirs[j - 1], dirs[j]
        m = f.normalise(f.add(d_in, d_out))
        dm = f.dot(d_in, m)
        faces.extend(f.add(pts[j], f.sub(o, f.scale(d_in, f.dot(o, m) / dm))) for o in current)
        current = [f.sub(o, f.scale(m, 2.0 * f.dot(o, m))) for o in current]
    faces.extend(f.add(pts[-1], o) for o in current)
    return faces


def mitre_bbox(
    points: tuple[Vec3, ...] | list[Vec3], profile: Profile, frame: SketchFrame, corner_radius: float | None = None
) -> BoundingBox:
    """Exact axis-aligned box of a MITRED sweep (also of a single straight segment).

    Circles: the union of the end ellipses (caps and mitre faces). Polygons and rectangles: the box of the
    transported section vertices on every end face."""
    if corner_radius is not None:
        msg = "mitre_bbox is exact for mitred corners only; use lower_bbox for a filleted path"
        raise ValueError(msg)
    analysis = analyse_path(points)
    if profile.kind == "circle" and profile.radius is not None:
        lows: list[Vec3] = []
        highs: list[Vec3] = []
        for centre, half in _ellipse_boxes(analysis, profile.radius):
            lows.append(f.sub(centre, half))
            highs.append(f.add(centre, half))
        return f.bbox_of_points(lows + highs)
    offsets = _section_offsets(profile, frame, analysis.points[0], analysis.directions[0])
    return f.bbox_of_points(_polygon_face_points(analysis, offsets))


def _arc_points(analysis: PathAnalysis, corner: int, samples: int) -> list[Vec3]:
    """Points of the fillet arc at interior vertex ``corner + 1``, tangent to tangent (both included)."""
    j = corner + 1
    d_in, d_out = analysis.directions[j - 1], analysis.directions[j]
    radius = analysis.corner_radius
    assert radius is not None
    phi = analysis.turn_angles[corner]
    t = analysis.tangent_lengths[corner]
    vertex = analysis.points[j]
    t1 = f.sub(vertex, f.scale(d_in, t))
    t2 = f.add(vertex, f.scale(d_out, t))
    towards_centre = f.normalise(f.sub(d_out, d_in))
    centre = f.add(vertex, f.scale(towards_centre, radius / math.cos(phi / 2.0)))
    r1 = f.sub(t1, centre)
    r2 = f.sub(t2, centre)
    out: list[Vec3] = []
    for k in range(samples + 1):
        s = k / samples
        w1 = math.sin((1.0 - s) * phi) / math.sin(phi)
        w2 = math.sin(s * phi) / math.sin(phi)
        out.append(f.add(centre, f.add(f.scale(r1, w1), f.scale(r2, w2))))
    return out


def centreline_points(analysis: PathAnalysis, arc_samples: int = 16) -> list[Vec3]:
    """Points of the swept centre line: the path points for a mitred path (the corner point is on the mitre
    face), the tangent points and arc samples for a filleted one (the corner point itself is cut away)."""
    if analysis.corner_radius is None:
        return list(analysis.points)
    out: list[Vec3] = [analysis.points[0]]
    for corner in range(analysis.corner_count):
        out.extend(_arc_points(analysis, corner, arc_samples))
    out.append(analysis.points[-1])
    return out


def _centroid_inside(profile: Profile) -> bool:
    if profile.kind != "polygon" or not profile.points:
        return True
    cu, cv = profile.centroid
    inside = False
    pts = profile.points
    for i, (u0, v0) in enumerate(pts):
        u1, v1 = pts[(i + 1) % len(pts)]
        if (v0 > cv) != (v1 > cv) and cu < (u1 - u0) * (cv - v0) / (v1 - v0) + u0:
            inside = not inside
    return inside


def lower_bbox(
    points: tuple[Vec3, ...] | list[Vec3], corner_radius: float | None, profile: Profile, frame: SketchFrame
) -> BoundingBox:
    """A box the swept solid is guaranteed to contain (its true box is at least this large).

    Built from points that are certainly in the solid: the centre line (when the centroid lies inside the
    section), the start cap, and the end cap when it is known (any circle; any section on a mitred path).
    Used where the exact box is not known analytically or the kernel's own box is loose (curved swept
    faces): the observed box must CONTAIN this one."""
    analysis = analyse_path(points, corner_radius)
    pts: list[Vec3] = []
    if _centroid_inside(profile):
        pts.extend(centreline_points(analysis))
    if profile.kind == "circle" and profile.radius is not None:
        for centre, direction in (
            (analysis.points[0], analysis.directions[0]),
            (analysis.points[-1], analysis.directions[-1]),
        ):
            disc = f.cylinder_bbox(Axis(origin=centre, direction=direction), profile.radius, 0.0, 0.0)
            pts.extend((disc.min, disc.max))
    else:
        offsets = _section_offsets(profile, frame, analysis.points[0], analysis.directions[0])
        pts.extend(f.add(analysis.points[0], o) for o in offsets)
        if corner_radius is None:
            pts.extend(_polygon_face_points(analysis, offsets))
    return f.bbox_of_points(pts)
