# ruff: noqa: E501
"""The isometric view of a drawing in colour: the part shaded, every hole in the colour of how it is made.

Why this route. The deliverable (PDF, PNG) is not TechDraw's export: it is the sheet composed from data and rendered with Qt
(``sheet_svg`` -> ``sheet_render``), because TechDraw can only print through a GUI scene. TechDraw's ``DrawViewPart`` is a line
drawing and cannot be filled, so the colour is made here, as filled polygons of the composed sheet that lie UNDER the lines
of the views (``Sheet.underlay``): vector, so they print sharp; recomputed from the model every time the sheet is composed, so
they are never stale; and their inputs (the colours of the holes and the ``iso_colour`` flag) are in the file -- the registry
and the sheet spec -- so it works on a headless worker and after a reopen. The view's place, scale and the items that use it
are untouched: the polygons are placed with the very ``ViewInfo.project`` the balloons use.

The picture is a painter's-algorithm render of the tessellated faces: polygons projected on the page, drawn far to near (BSP order),
each filled flat with the colour of its face scaled by a light that is fixed in the view (so every sheet is lit the same way).
A face that is not a hole is neutral grey; a face a coloured hole made takes the colour of its class
(``hole_colours.face_colours``). It is an illustration, not a hidden-line drawing: the lines of the view stay the ink.

This module imports no FreeCAD at module level; ``shade`` reaches the shapes through ``registry``, the pure part is
``shade_meshes``.
"""

import math
import sys
import time
import traceback
from collections.abc import Sequence
from typing import Any

import hole_colours
from sheet_model import ISO_SHADE, Solid

Vec3 = tuple[float, float, float]
Mesh = tuple["tuple[int, int, int] | None", "Sequence[tuple[Vec3, Vec3, Vec3]]"]

NEUTRAL = (214, 214, 214)  # the part where no hole made the face
LIGHT_IN_VIEW = (-0.4, 0.6, 0.7)  # towards the light, in view axes (x right, y up, z towards the viewer)
AMBIENT = 0.55  # the share of the colour a face turned away from the light still shows
MAX_TRIANGLES = 8000  # a heavier part keeps the line drawing (the picture would be slow and huge)
MIN_AREA_MM2 = 1e-4  # a triangle seen edge-on has no area on the page
BUILD_BUDGET_S = 3.0  # the exact order may take this long; past it the polygons are sorted by depth instead
BALANCE_MIN = 0.125  # a polygon plane that sets this share of the group aside (coplanar or alone on a side) at least...
SAMPLE = 64  # polygons a candidate plane is judged on
BALANCE_FROM = 24  # ...in a node of more polygons than this, or a plane across the group is used (``_median_plane``)
Notes = list[tuple[str, str]]  # what the reply must say, (es, en)


def _unit(v: Vec3) -> Vec3:
    n = math.sqrt(sum(c * c for c in v)) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def light_of(view: Any) -> Vec3:
    """The direction to the light in model axes: fixed in the view's own axes, so the same on every sheet."""
    lx, ly, lz = LIGHT_IN_VIEW
    return _unit(
        tuple(lx * view.xdir[i] + ly * view.ydir[i] + lz * view.direction[i] for i in range(3))  # type: ignore[arg-type]
    )


def lit(colour: tuple[int, int, int], normal: Vec3, light: Vec3) -> tuple[int, int, int]:
    """``colour`` as a face with this ``normal`` shows it (two-sided: the winding of a tessellation says nothing sure)."""
    factor = AMBIENT + (1.0 - AMBIENT) * abs(_dot(_unit(normal), light))
    return (round(colour[0] * factor), round(colour[1] * factor), round(colour[2] * factor))


Polygon = tuple[tuple[Vec3, ...], tuple[int, int, int]]
SPLIT_EPS = 1e-6  # mm: a vertex this close to a plane lies on it
SPLITTER_CANDIDATES = 4  # polygons tried as the splitting plane of a node: the one that cuts fewest wins


class _Node:
    __slots__ = ("back", "coplanar", "d", "front", "n")

    def __init__(self, n: Vec3, d: float, coplanar: list[Polygon]) -> None:
        self.n, self.d, self.coplanar = n, d, coplanar
        self.front: _Node | list[Polygon] | None = None
        self.back: _Node | list[Polygon] | None = None


def _plane_of(points: Sequence[Vec3]) -> tuple[Vec3, float] | None:
    normal = _cross(_sub(points[1], points[0]), _sub(points[2], points[0]))
    if _dot(normal, normal) < 1e-18:
        return None
    n = _unit(normal)
    return n, _dot(n, points[0])


def _side(poly: Polygon, n: Vec3, d: float) -> tuple[list[float], int, int]:
    nx, ny, nz = n
    dists = [nx * p[0] + ny * p[1] + nz * p[2] - d for p in poly[0]]
    ahead = behind = 0
    for x in dists:
        if x > SPLIT_EPS:
            ahead += 1
        elif x < -SPLIT_EPS:
            behind += 1
    return dists, ahead, behind


def _cut(poly: Polygon, dists: list[float]) -> tuple[Polygon, Polygon]:
    """The two parts of ``poly`` on either side of a plane it crosses (a point on the plane belongs to both)."""
    points = poly[0]
    front: list[Vec3] = []
    back: list[Vec3] = []
    for i, a in enumerate(points):
        b = points[(i + 1) % len(points)]
        da, db = dists[i], dists[(i + 1) % len(points)]
        if da >= -SPLIT_EPS:
            front.append(a)
        if da <= SPLIT_EPS:
            back.append(a)
        if (da > SPLIT_EPS and db < -SPLIT_EPS) or (da < -SPLIT_EPS and db > SPLIT_EPS):
            t = da / (da - db)
            cross = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), a[2] + t * (b[2] - a[2]))
            front.append(cross)
            back.append(cross)
    return (tuple(front), poly[1]), (tuple(back), poly[1])


class _TooHeavyError(ValueError):
    def __init__(self, total: int) -> None:
        super().__init__(f"{total} triangles is more than the isometric picture takes ({MAX_TRIANGLES})")
        self.total = total


class _OutOfTimeError(Exception):
    """The exact order took longer than the budget."""


def _median_plane(polys: list[Polygon]) -> tuple[Vec3, float]:
    """A plane across the middle of the group, perpendicular to its longest axis. On a curved face every triangle has the
    whole rest on one side, so splitting on triangle planes peels one triangle per level (quadratic); a plane that is not
    one of the polygons halves the group, and cuts only the few polygons that cross it."""
    centres = [tuple(sum(p[i] for p in q[0]) / len(q[0]) for i in range(3)) for q in polys]
    spans = [max(c[i] for c in centres) - min(c[i] for c in centres) for i in range(3)]
    axis = spans.index(max(spans))
    values = sorted(c[axis] for c in centres)
    n: Vec3 = (1.0 if axis == 0 else 0.0, 1.0 if axis == 1 else 0.0, 1.0 if axis == 2 else 0.0)
    return n, (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2


def _split(polys: list[Polygon], n: Vec3, d: float) -> tuple[_Node, list[Polygon], list[Polygon]]:
    """A node on the plane (n, d) and the polygons in front of it and behind it (the ones it crosses, cut in two)."""
    node = _Node(n, d, [])
    front: list[Polygon] = []
    back: list[Polygon] = []
    for q in polys:
        dists, ahead, behind = _side(q, n, d)
        if ahead and behind:
            f, b = _cut(q, dists)
            front.append(f)
            back.append(b)
        elif ahead:
            front.append(q)
        elif behind:
            back.append(q)
        else:
            node.coplanar.append(q)
    return node, front, back


def _build(polygons: list[Polygon], deadline: float | None = None) -> _Node | list[Polygon] | None:
    """The BSP tree of the polygons (iterative: a curved face gives a plane per triangle, a deep tree). Raises
    ``_OutOfTimeError`` past ``deadline`` (a ``time.thread_time`` value: neither a busy machine nor the window of the CAD may change the sheet)."""
    root: list[Any] = [None]
    stack: list[tuple[list[Polygon], Any, str]] = [(polygons, root, "0")]

    def put(holder: Any, slot: str, value: Any) -> None:
        if isinstance(holder, list):
            holder[0] = value
        else:
            setattr(holder, slot, value)

    while stack:
        polys, holder, slot = stack.pop()
        if not polys:
            continue
        if deadline is not None and time.thread_time() > deadline:
            raise _OutOfTimeError
        step = max(1, len(polys) // SPLITTER_CANDIDATES)
        sample = polys[:: max(1, len(polys) // SAMPLE)]  # the planes are judged on this many polygons, not on all
        best: tuple[int, int, tuple[Vec3, float]] | None = None
        bigger = 0  # polygons the best plane leaves on its busier side (the ones it crosses count on both)
        for index in range(0, len(polys), step)[:SPLITTER_CANDIDATES]:
            plane = _plane_of(polys[index][0])
            if plane is None:
                continue
            sides = [_side(q, *plane)[1:] for q in sample]
            cuts = sum(1 for ahead, behind in sides if ahead and behind)
            if best is None or cuts < best[0]:
                best = (cuts, index, plane)
                bigger = max(sum(1 for a, _ in sides if a), sum(1 for _, b in sides if b))
        if best is None:  # nothing with an area is left
            continue
        n, d = best[2]
        if len(polys) > BALANCE_FROM and bigger > (1 - BALANCE_MIN) * len(sample):
            across = _median_plane(polys)
            node, front, back = _split(polys, *across)
            if not front or not back:  # that plane split nothing: the polygon's own plane then
                node, front, back = _split(polys, n, d)
        else:
            node, front, back = _split(polys, n, d)
        put(holder, slot, node)
        stack.append((front, node, "front"))
        stack.append((back, node, "back"))
    return root[0]


def _back_to_front(tree: _Node | list[Polygon] | None, toward: Vec3) -> list[Polygon]:
    """The polygons from the farthest to the nearest along ``toward`` (the direction to the viewer)."""
    out: list[Polygon] = []
    stack: list[Any] = [tree]
    while stack:
        item = stack.pop()
        if item is None:
            continue
        if isinstance(item, list):
            out.extend(item)
            continue
        if _dot(item.n, toward) > 0:  # the viewer is in front of the plane: what is behind it is farther
            stack.extend((item.front, item.coplanar, item.back))
        else:
            stack.extend((item.back, item.coplanar, item.front))
    return out


def shade_meshes(
    meshes: Sequence[Mesh], view: Any, notes: Notes | None = None, budget: float = BUILD_BUDGET_S
) -> list[Solid]:
    """Filled polygons (page mm, y up) for the triangles of ``meshes`` as ``view`` sees them, far to near.

    ``view`` is a ``sheet_context.ViewInfo`` (``project``, ``direction`` towards the viewer, ``xdir``, ``ydir``). The order
    is exact, not a sort by depth of the centre (a big triangle of a face with a hole sorts wrong against the small triangles
    of the wall of the hole, and the wall shows over the face): the polygons go in a BSP tree and are read from the far side
    of every plane to the near one, cutting the ones a plane crosses. Raises ``ValueError`` when the part is too heavy
    (``MAX_TRIANGLES``). When the exact order takes longer than ``budget`` seconds the polygons are sorted by the depth of
    their centre instead and ``notes`` (when given) says so: a wall may then show over a face."""
    total = sum(len(triangles) for _, triangles in meshes)
    if total > MAX_TRIANGLES:
        raise _TooHeavyError(total)
    light = light_of(view)
    polygons: list[Polygon] = []
    for colour, triangles in meshes:
        base = colour if colour is not None else NEUTRAL
        for a, b, c in triangles:
            normal = _cross(_sub(b, a), _sub(c, a))
            pa, pb, pc = view.project(a), view.project(b), view.project(c)
            area2 = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
            if abs(area2) < 2 * MIN_AREA_MM2:
                continue  # seen edge-on: no area on the page, and nothing can hide behind it
            polygons.append(((a, b, c), lit(base, normal, light)))
    toward = _unit(view.direction)
    try:
        ordered = _back_to_front(_build(polygons, time.thread_time() + budget), toward)
    except _OutOfTimeError:
        ordered = sorted(polygons, key=lambda q: sum(_dot(p, toward) for p in q[0]))
        if notes is not None:
            notes.append(
                (
                    "el isométrico en color usa un orden de caras aproximado (la pieza es muy compleja): una pared de un "
                    "agujero puede verse sobre la cara de delante; las líneas del plano son las correctas",
                    "the colour of the isometric view uses an approximate face order (the part is very complex): a hole wall "
                    "may show over the face in front of it; the lines of the drawing are right",
                )
            )
    solids: list[Solid] = []
    for points, fill in ordered:
        if len(points) >= 3:
            solids.append(Solid(tuple(view.project(p) for p in points), ISO_SHADE, fill))
    return solids


def shade(doc: Any, bodies: Sequence[Any], view: Any, op: str | None = None) -> tuple[list[Solid], Notes]:
    """The coloured picture of ``bodies`` seen in ``view`` and what the reply must say about it. ``[]`` with the reason
    when it cannot be made (a line on stderr too) -- the sheet then keeps its line drawing, never a half-made picture."""
    import registry as reg  # noqa: PLC0415 - FreeCAD is only reached through the registry

    notes: Notes = []
    try:
        meshes: list[Mesh] = []
        for body in bodies:
            shape = reg.world_shape(doc, body)
            if shape.isNull():
                continue
            box = shape.BoundBox
            tolerance = min(0.25, max(0.02, box.DiagonalLength * 0.0008))
            colours = hole_colours.face_colours(doc, body)
            for index, face in enumerate(shape.Faces):
                vertices, triangles = face.tessellate(tolerance)
                points = [(float(v.x), float(v.y), float(v.z)) for v in vertices]
                meshes.append((colours.get(index), [(points[i], points[j], points[k]) for i, j, k in triangles]))
        return shade_meshes(meshes, view, notes), notes
    except _TooHeavyError as exc:
        sys.stderr.write(f"isometric colour skipped ({op}): {exc}\n")
        return [], [
            (
                f"el isométrico se queda sin color: la pieza tiene {exc.total} triángulos y el color admite {MAX_TRIANGLES}; "
                "el plano sigue siendo el dibujo de líneas",
                f"the isometric view has no colour: the part has {exc.total} triangles and the colour takes {MAX_TRIANGLES}; "
                "the drawing is the line drawing",
            )
        ]
    except Exception:  # noqa: BLE001 - an illustration: its failure never fails the sheet
        sys.stderr.write(f"isometric colour skipped ({op}):\n{traceback.format_exc()}")
        return [], [
            (
                "el isométrico se queda sin color: no se pudo calcular (el dibujo de líneas no cambia)",
                "the isometric view has no colour: it could not be computed (the line drawing is unchanged)",
            )
        ]
