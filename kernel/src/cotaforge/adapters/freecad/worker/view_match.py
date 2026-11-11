# ruff: noqa: E501
"""Which model elements a line of a TechDraw view is the drawing of (no FreeCAD import, unit-testable on its own).

TechDraw does not hand over where an edge of a view came from (its geometry keeps an index into its own source shape, and
the source of a projection group is a compound), so the link is made by geometry, the way the sheet's items already
place their dimensions on the view (``sheet_context.ViewInfo``): a model point ``p`` lands in a view with direction ``d``
and x axis ``x`` (``y = d x x``), centre ``C`` (the model point at the view's origin) at

    (x_local, y_local) = ((p - C) . x, (p - C) . y)        and its depth towards the viewer is  p . d

in the view's own units, scale 1, y UP -- which is exactly how ``DrawViewPart.getEdgeByIndex(i)`` /
``getVertexByIndex(i)`` (the objects behind the sub-element names ``Edge<i>`` / ``Vertex<i>`` of a selection) give their
geometry (verified in FreeCAD 1.1.3: a hole at model (15, 12) of a plate centred on (50, 30) is the circle centred on
(-35, -18) of the top view).

A line of the view is the drawing of a model edge when every sample of the line lies on the projection of that edge
(a visible stretch of a longer edge is still that edge). Several edges can project onto the same line (the front and the
back edge of a plate, the two rims of a through hole): the one nearest the viewer is the one the view draws, the one
farthest from it the one a hidden line shows; the others are kept as ``behind``. A line that no model edge explains may be
the silhouette of a cylinder (the generatrix at the side of a shaft, tilted in an isometric view): then it is the face.
"""

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Pt = tuple[float, float]

TOL_MM = 0.05  # a sample lies on a projected edge within this (model mm; the view is drawn at scale 1 here)
DEPTH_TIE_MM = 1e-4  # two candidates at this depth are the same line seen twice (a seam, a split edge)
AXIS_ALONG = 1e-6  # |axis x direction| below this: the view looks along the axis of the cylinder


@dataclass(frozen=True)
class Frame:
    """A view's projection: ``direction`` points to the viewer, ``xdir`` is the view's x axis, ``centre`` the model point
    at its origin."""

    centre: Vec3
    xdir: Vec3
    direction: Vec3

    @property
    def ydir(self) -> Vec3:
        d, x = self.direction, self.xdir
        return (d[1] * x[2] - d[2] * x[1], d[2] * x[0] - d[0] * x[2], d[0] * x[1] - d[1] * x[0])

    def project(self, p: Vec3) -> tuple[float, float, float]:
        """``(x, y, depth)`` of a model point: view coordinates (scale 1, y up) and distance towards the viewer."""
        c, x, y, d = self.centre, self.xdir, self.ydir, self.direction
        v = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
        return (
            v[0] * x[0] + v[1] * x[1] + v[2] * x[2],
            v[0] * y[0] + v[1] * y[1] + v[2] * y[2],
            p[0] * d[0] + p[1] * d[1] + p[2] * d[2],
        )

    def flat(self, p: Vec3) -> Pt:
        x, y, _ = self.project(p)
        return x, y


@dataclass(frozen=True)
class EdgeCand:
    """A model edge as a polyline of 3D samples (fine enough that the chord is within the tolerance of the curve)."""

    id: str
    points: tuple[Vec3, ...]


@dataclass(frozen=True)
class VertexCand:
    id: str
    point: Vec3


@dataclass(frozen=True)
class CylinderCand:
    """A cylindrical face by its axis (a point on it, the unit direction), radius and the extent along the axis
    (``t0`` to ``t1`` from the point): what a view seen across the axis draws as two lines."""

    face: str
    point: Vec3
    axis: Vec3
    radius: float
    t0: float
    t1: float
    convex: bool = True  # a boss or a shaft; a bore draws its silhouette only as a hidden line


@dataclass
class Match:
    """What a line (or vertex) of a view is: the model ``edges`` / ``faces`` drawn on it (nearest the viewer first
    when ``hidden`` is false; farthest first for a hidden line), and the ``behind`` ones that project onto the same
    place but are not what the view shows."""

    edges: list[str] = field(default_factory=list)
    faces: list[str] = field(default_factory=list)
    behind: list[str] = field(default_factory=list)


# --- geometry --------------------------------------------------------------------------------------------------------------


def _seg_closest(p: Pt, a: Pt, b: Pt) -> tuple[float, float]:
    """``(distance, t)`` from ``p`` to the segment ``a``-``b`` (``t`` in 0..1 along it)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-18 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy)), t


def _on_polyline(p: Pt, flat: list[Pt], depths: list[float]) -> tuple[float, float]:
    """``(distance, depth at the nearest point)`` of ``p`` to a projected polyline."""
    best, depth = math.inf, 0.0
    for i in range(len(flat) - 1):
        d, t = _seg_closest(p, flat[i], flat[i + 1])
        if d < best:
            best, depth = d, depths[i] + t * (depths[i + 1] - depths[i])
    return best, depth


def _box(points: list[Pt]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _order(found: list[tuple[str, float]], hidden: bool) -> Match:
    """Split candidates ``(id, depth)`` into the ones the view draws and the ones behind (or in front of, for a hidden
    line) them."""
    if not found:
        return Match()
    best = min(d for _, d in found) if hidden else max(d for _, d in found)
    shown = [i for i, d in found if abs(d - best) <= DEPTH_TIE_MM]
    rest = [i for i, d in found if abs(d - best) > DEPTH_TIE_MM]
    return Match(edges=shown, behind=rest)


# --- matching --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Projected:
    """A model edge seen in a view: the polyline in view coordinates, the depth of each of its points, its box."""

    id: str
    flat: tuple[Pt, ...]
    depths: tuple[float, ...]
    box: tuple[float, float, float, float]


def project_edges(frame: Frame, candidates: list[EdgeCand]) -> list[Projected]:
    """The candidates seen in ``frame`` (done once per view, then every line of the view is matched against them)."""
    out = []
    for cand in candidates:
        points = [frame.project(p) for p in cand.points]
        flat = tuple((x, y) for x, y, _ in points)
        out.append(Projected(cand.id, flat, tuple(z for _, _, z in points), _box(list(flat))))
    return out


def match_edge(
    frame: Frame,
    samples: list[Pt],
    candidates: list[Projected],
    cylinders: list[CylinderCand] | None = None,
    hidden: bool = False,
    tol: float = TOL_MM,
) -> Match:
    """The model edges (and cylinder faces, for a silhouette line) that the line of the view sampled by ``samples`` (view
    coordinates, scale 1, y up: at least two points, ends included) is the drawing of."""
    if len(samples) < 2:
        return Match()
    sample_box = _box(samples)
    middle = samples[len(samples) // 2]
    found: list[tuple[str, float]] = []
    for cand in candidates:
        box = cand.box
        if not (
            box[0] - tol <= sample_box[0]
            and sample_box[2] <= box[2] + tol
            and box[1] - tol <= sample_box[1]
            and sample_box[3] <= box[3] + tol
        ):
            continue
        flat, depths = list(cand.flat), list(cand.depths)
        if all(_on_polyline(s, flat, depths)[0] <= tol for s in samples):
            found.append((cand.id, _on_polyline(middle, flat, depths)[1]))
    match = _order(found, hidden)
    for cyl in cylinders or []:
        if not cyl.convex and not hidden:
            continue
        if _on_silhouette(frame, cyl, samples, tol):
            match.faces.append(cyl.face)
    return match


def _on_silhouette(frame: Frame, cyl: CylinderCand, samples: list[Pt], tol: float) -> bool:
    """Whether the samples lie on one of the two silhouette lines of ``cyl``: where the surface turns away from the viewer,
    the generatrices ``radius`` to either side of the axis along ``axis x direction`` (across the axis of a shaft in a
    front view, tilted in an isometric one). A view along the axis has none: the rims are the circle."""
    d, axis, point = frame.direction, cyl.axis, cyl.point
    side = (
        axis[1] * d[2] - axis[2] * d[1],
        axis[2] * d[0] - axis[0] * d[2],
        axis[0] * d[1] - axis[1] * d[0],
    )
    norm = math.sqrt(side[0] ** 2 + side[1] ** 2 + side[2] ** 2)
    if norm < AXIS_ALONG:
        return False
    side = (side[0] / norm, side[1] / norm, side[2] / norm)
    for sign in (1.0, -1.0):
        ends = [
            frame.flat(tuple(point[i] + axis[i] * t + sign * cyl.radius * side[i] for i in range(3)))  # type: ignore[arg-type]
            for t in (cyl.t0, cyl.t1)
        ]
        if all(_seg_closest(s, ends[0], ends[1])[0] <= tol for s in samples):
            return True
    return False


def match_vertex(
    frame: Frame, point: Pt, candidates: list[VertexCand], hidden: bool = False, tol: float = TOL_MM
) -> Match:
    """The model vertices a vertex of the view (view coordinates, scale 1, y up) is the drawing of."""
    found = []
    for cand in candidates:
        x, y, depth = frame.project(cand.point)
        if math.hypot(x - point[0], y - point[1]) <= tol:
            found.append((cand.id, depth))
    return _order(found, hidden)
