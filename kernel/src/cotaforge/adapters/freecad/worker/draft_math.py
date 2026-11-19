"""The maths of ``draft_analysis`` without FreeCAD (unit-testable with plain pytest).

Every ray of the shadow test runs along the pull direction (one sense or the other), so the model is projected along it:
a triangle becomes a flat triangle in the plane across the pull, with a depth along the pull at each corner. A ray
from a point is blocked when the point's projection lies inside a triangle whose depth there is beyond the point,
in the sense the ray goes. Triangles are indexed on a grid of the projection, so a point looks at the few near it.
"""

import math
from collections import defaultdict

Vec = tuple[float, float, float]

DRAFT_ZERO_DEG = 0.01
"""Mirror of ``cotaforge.contract.mould.DRAFT_ZERO_DEG`` (the worker imports nothing from the package; a test keeps
the two equal)."""
DRAFT_MIN_TOL_DEG = 0.001  # mirror of the contract's: a face drafted to exactly the minimum measures a hair under it
HIT_EPS = 1e-6  # mm: a surface this close to the start of a ray, along it, is the start's own
INSIDE_EPS = 1e-9  # barycentric slack: a ray through a shared edge hits the triangles on both sides
MAX_CELLS_PER_TRIANGLE = 400  # a triangle covering more cells than this is tested by every ray instead


def dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def unit(v: Vec) -> Vec:
    n = math.sqrt(dot(v, v))
    return (v[0] / n, v[1] / n, v[2] / n)


def lean_deg(normal: Vec, pull: Vec) -> float:
    """The angle between a face with this unit ``normal`` and the pull direction: 0 for a wall parallel to the pull, 90
    for a face looking straight along it."""
    return math.degrees(math.asin(min(1.0, abs(dot(normal, pull)))))


def classify(draft_deg: float, shadowed: bool, min_draft_deg: float) -> str:
    """Mirror of ``cotaforge.contract.mould.classify_draft``."""
    if shadowed:
        return "undercut"
    if draft_deg <= DRAFT_ZERO_DEG:
        return "zero"
    return "low" if draft_deg < min_draft_deg - DRAFT_MIN_TOL_DEG else "ok"


def release_sense(normal: Vec, pull: Vec) -> int:
    """The sense along the pull in which the mould half that forms a face with this outward normal moves away from it: a
    face looking along the pull is formed by the half on that side, one looking against it by the other half. A wall
    (no lean) counts for the first."""
    return 1 if dot(normal, pull) >= 0.0 else -1


def basis_across(pull: Vec) -> tuple[Vec, Vec]:
    """Two unit vectors that, with ``pull`` (unit), make a right-handed frame."""
    helper: Vec = (1.0, 0.0, 0.0) if abs(pull[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = unit(
        (
            pull[1] * helper[2] - pull[2] * helper[1],
            pull[2] * helper[0] - pull[0] * helper[2],
            pull[0] * helper[1] - pull[1] * helper[0],
        )
    )
    e2 = (
        pull[1] * e1[2] - pull[2] * e1[1],
        pull[2] * e1[0] - pull[0] * e1[2],
        pull[0] * e1[1] - pull[1] * e1[0],
    )
    return e1, e2


class ShadowCaster:
    """The triangles of a model, indexed for rays along ``pull``."""

    def __init__(self, vertices: list[Vec], triangles: list[tuple[int, int, int]], pull: Vec) -> None:
        self.pull = unit(pull)
        self.e1, self.e2 = basis_across(self.pull)
        projected = [(dot(v, self.e1), dot(v, self.e2), dot(v, self.pull)) for v in vertices]
        self._tris: list[tuple[float, float, float, float, float, float, float, float, float]] = []
        for i, j, k in triangles:
            (a0, b0, h0), (a1, b1, h1), (a2, b2, h2) = projected[i], projected[j], projected[k]
            if abs((a1 - a0) * (b2 - b0) - (a2 - a0) * (b1 - b0)) < 1e-12:
                continue  # parallel to the pull: a ray along it cannot cross it
            self._tris.append((a0, b0, h0, a1, b1, h1, a2, b2, h2))
        sizes = sorted(
            max(max(t[0], t[3], t[6]) - min(t[0], t[3], t[6]), max(t[1], t[4], t[7]) - min(t[1], t[4], t[7]))
            for t in self._tris
        )
        self._cell = max(sizes[len(sizes) // 2] * 2.0, 1e-3) if sizes else 1.0
        self._grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._big: list[int] = []
        for index, t in enumerate(self._tris):
            i0, i1 = (math.floor(min(t[0], t[3], t[6]) / self._cell), math.floor(max(t[0], t[3], t[6]) / self._cell))
            j0, j1 = (math.floor(min(t[1], t[4], t[7]) / self._cell), math.floor(max(t[1], t[4], t[7]) / self._cell))
            if (i1 - i0 + 1) * (j1 - j0 + 1) > MAX_CELLS_PER_TRIANGLE:
                self._big.append(index)
                continue
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    self._grid[(i, j)].append(index)

    def blocked(self, point: Vec, sense: int) -> bool:
        """Whether a ray from ``point`` along ``sense`` * pull meets a triangle of the model."""
        a, b, h = dot(point, self.e1), dot(point, self.e2), dot(point, self.pull)
        cell = (math.floor(a / self._cell), math.floor(b / self._cell))
        for index in [*self._grid.get(cell, ()), *self._big]:
            a0, b0, h0, a1, b1, h1, a2, b2, h2 = self._tris[index]
            det = (a1 - a0) * (b2 - b0) - (a2 - a0) * (b1 - b0)
            w1 = ((a - a0) * (b2 - b0) - (a2 - a0) * (b - b0)) / det
            w2 = ((a1 - a0) * (b - b0) - (a - a0) * (b1 - b0)) / det
            w0 = 1.0 - w1 - w2
            if w0 < -INSIDE_EPS or w1 < -INSIDE_EPS or w2 < -INSIDE_EPS:
                continue
            depth = w0 * h0 + w1 * h1 + w2 * h2
            if sense * (depth - h) > HIT_EPS:
                return True
        return False


def shadowed_share(caster: ShadowCaster, samples: list[tuple[Vec, Vec]], offset: float) -> float:
    """The share of ``(point, outward normal)`` samples that cannot be reached from the pull direction: each ray starts
    ``offset`` mm off the face along its normal and goes the way the face's own mould half releases it."""
    if not samples:
        return 0.0
    blocked = 0
    for point, normal in samples:
        start = (point[0] + normal[0] * offset, point[1] + normal[1] * offset, point[2] + normal[2] * offset)
        if caster.blocked(start, release_sense(normal, caster.pull)):
            blocked += 1
    return blocked / len(samples)
