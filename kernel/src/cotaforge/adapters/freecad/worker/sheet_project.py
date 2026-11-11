# ruff: noqa: E501
"""Projections of the part for the derived views of a sheet (section, detail, auxiliary, hidden lines): hidden-line
removal with ``TechDraw.projectEx`` and the cut face of a section, both returned as ``sheet_model`` entities in the
view's own coordinates (scale 1, origin at the model point ``centre``, y up).

Why not TechDraw view objects: a section / detail view object computes in the background in the GUI and hatches only in
its scene, which a headless export does not have; every step taken on a half-computed view is a crash risk. Here the
projection is a function call that returns when it is done, in the headless and the visible worker alike, and the
sheet draws the cut face itself.

The frame is the one TechDraw uses for a view with ``Direction`` d (towards the viewer) and ``XDirection`` x: a model
point p lands at ``x_local = (p - C) . x`` and ``y_local = (p - C) . (d x x)``. ``projectEx`` picks its own x axis, so
the shape is rotated into the frame first and projected looking down +z.
"""

import math
from typing import Any

import FreeCAD as App
import geometry as g
import Part
import sheet_context as sc
import sheet_model as model
from protocol import not_supported

CHORD_DEVIATION_MM = 0.02  # a curve discretised for fitting or for a polyline
FIT_TOL_MM = 0.005  # how far a discretised edge may stray from the line / circle it is classified as
VISIBLE_SETS = (0, 3)  # projectEx: V (sharp visible) and VO (visible outlines)
HIDDEN_SETS = (5, 8)  # H (sharp hidden) and HO (hidden outlines)
ON_PLANE_MM = 1e-4
SAME_POINT_MM = 1e-6


def _techdraw() -> Any:
    try:
        import TechDraw  # noqa: PLC0415 - not every FreeCAD build has the module
    except ImportError as exc:
        raise not_supported(f"this FreeCAD has no TechDraw module ({exc})", None) from exc
    return TechDraw


def _frame_matrix(centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> Any:
    """The matrix taking a model point into the view's frame (x, y = d x x, z = d), the point ``centre`` at the origin."""
    ydir = sc.cross(direction, xdir)
    rotate = App.Matrix(
        xdir[0], xdir[1], xdir[2], 0.0,
        ydir[0], ydir[1], ydir[2], 0.0,
        direction[0], direction[1], direction[2], 0.0,
        0.0, 0.0, 0.0, 1.0,
    )  # fmt: skip
    move = App.Matrix()
    move.move(App.Vector(-centre[0], -centre[1], -centre[2]))
    return rotate.multiply(move)


# --- edges -> entities --------------------------------------------------------------------------------------------------


def _circle_through(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> tuple[float, float, float] | None:
    """Centre and radius of the circle through three points, or ``None`` when they are (nearly) in line."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)


def edge_entities(edge: Any, layer: str) -> list[model.Entity]:
    """One projected edge as a line, a circle, an arc or a polyline: HLR hands everything back as B-splines, so the
    shape is recognised from its points (a hole must stay a circle in the DXF)."""
    points = [(float(p.x), float(p.y)) for p in edge.discretize(Deflection=CHORD_DEVIATION_MM)]
    if len(points) < 2:
        return []
    first, last = points[0], points[-1]
    closed = math.dist(first, last) < SAME_POINT_MM
    if not closed:
        chord = math.dist(first, last)
        if chord > SAME_POINT_MM:
            nx, ny = (last[1] - first[1]) / chord, -(last[0] - first[0]) / chord
            if all(abs((p[0] - first[0]) * nx + (p[1] - first[1]) * ny) <= FIT_TOL_MM for p in points):
                return [model.Line(first[0], first[1], last[0], last[1], layer)]
    count = len(points)
    probe = (points[count // 3], points[2 * count // 3]) if closed else (points[count // 2], points[count // 2])
    circle = _circle_through(first, probe[0], probe[1]) if closed else _circle_through(first, probe[0], last)
    if circle is not None and 0.05 < circle[2] < 1e5:
        cx, cy, r = circle
        if all(abs(math.hypot(p[0] - cx, p[1] - cy) - r) <= FIT_TOL_MM + r * 1e-4 for p in points):
            if closed:
                return [model.Circle(cx, cy, r, layer)]
            angle = [math.degrees(math.atan2(p[1] - cy, p[0] - cx)) % 360.0 for p in (first, probe[0], last)]
            if (angle[1] - angle[0]) % 360.0 > (angle[2] - angle[0]) % 360.0:  # the middle is clockwise from the start
                angle[0], angle[2] = angle[2], angle[0]
            return [model.Arc(cx, cy, r, angle[0], angle[2], layer)]
    return [model.Polyline(tuple(points), layer, closed=closed)]


def _entities(compounds: list[Any], layer: str) -> list[model.Entity]:
    out: list[model.Entity] = []
    for compound in compounds:
        for edge in compound.Edges:
            out += edge_entities(edge, layer)
    return out


# --- the projection ------------------------------------------------------------------------------------------------------


def _removal_box(point: sc.Vec3, normal: sc.Vec3, size: float) -> Any:
    """A box whose face lies on the cutting plane and that fills the half-space on the viewer's side (``-normal``)."""
    box = Part.makeBox(size, size, size, App.Vector(-size / 2, -size / 2, -size))
    rotation = App.Rotation(App.Vector(0, 0, 1), App.Vector(*normal))
    box.Placement = App.Placement(App.Vector(*point), rotation)
    return box


def cut_shapes_indexed(shapes: list[Any], point: sc.Vec3, normal: sc.Vec3) -> tuple[list[int], list[Any]]:
    """The parts of ``shapes`` on the far side of the plane through ``point`` with the given ``normal`` (the viewer looks
    along ``normal``, so what lies on the side he looks from is removed), with the index of the shape each came from."""
    extent = max(s.BoundBox.DiagonalLength for s in shapes)
    reach = 4.0 * extent + 100.0
    box = _removal_box(point, normal, reach)
    owners: list[int] = []
    cut = []
    for index, shape in enumerate(shapes):
        part = shape.cut(box)
        if not part.isNull() and part.Edges:
            owners.append(index)
            cut.append(part)
    return owners, cut


def cut_shapes(shapes: list[Any], point: sc.Vec3, normal: sc.Vec3) -> list[Any]:
    """The parts of ``shapes`` left by the cut (see ``cut_shapes_indexed``)."""
    return cut_shapes_indexed(shapes, point, normal)[1]


def cut_loops(
    shapes: list[Any],
    point: sc.Vec3,
    normal: sc.Vec3,
    centre: sc.Vec3,
    xdir: sc.Vec3,
    direction: sc.Vec3,
    owners: list[int] | None = None,
    found: list[int] | None = None,
) -> list[list[sc.Point]]:
    """The outlines (outer loops and holes) of the faces the plane leaves on ``shapes`` (already cut), in the view's
    coordinates. ``owners`` says which body each of ``shapes`` is (an index); the body of every loop is appended to
    ``found``."""
    ydir = sc.cross(direction, xdir)
    loops: list[list[sc.Point]] = []
    n = App.Vector(*normal)
    origin = App.Vector(*point)
    for position, shape in enumerate(shapes):
        for face in shape.Faces:
            if g.surface_kind(face) != "plane":
                continue
            if abs(abs(face.normalAt(0, 0).dot(n)) - 1.0) > 1e-6:
                continue
            if abs((face.CenterOfMass - origin).dot(n)) > ON_PLANE_MM:
                continue
            for wire in face.Wires:
                pts = wire.discretize(Deflection=CHORD_DEVIATION_MM)
                loop = [
                    (sc.dot(sc.sub((p.x, p.y, p.z), centre), xdir), sc.dot(sc.sub((p.x, p.y, p.z), centre), ydir))
                    for p in pts
                ]
                if len(loop) > 2 and math.dist(loop[0], loop[-1]) < SAME_POINT_MM:
                    loop.pop()
                if len(loop) > 2:
                    loops.append(loop)
                    if found is not None:
                        found.append(owners[position] if owners is not None else position)
    return loops


def project(
    shapes: list[Any],
    centre: sc.Vec3,
    xdir: sc.Vec3,
    direction: sc.Vec3,
    cut: tuple[sc.Vec3, sc.Vec3] | None = None,
) -> sc.Projection:
    """Look at ``shapes`` from ``direction`` (towards the viewer), x axis ``xdir``, origin at ``centre``. With ``cut``
    ``(point, normal)`` the part is first cut by that plane (the viewer's side removed): the result then has the loops of
    the cut faces and no hidden lines."""
    solids = shapes
    loops: list[list[sc.Point]] = []
    owner: list[int] = []
    if cut is not None:
        owners, solids = cut_shapes_indexed(shapes, cut[0], cut[1])
        if not solids:
            raise sc.ItemError("the cutting plane leaves nothing of the part")
        loops = cut_loops(solids, cut[0], cut[1], centre, xdir, direction, owners, owner)
        if not loops:
            raise sc.ItemError("the cutting plane does not cut the part (it misses it or only touches a face)")
    visible, hidden = _hlr(solids, centre, xdir, direction, hidden=cut is None)
    return sc.Projection(visible, hidden, loops, owner)


def _framed(shapes: list[Any], centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> Any:
    """``shapes`` in the frame of the view without copying them: the frame goes on the compound as a ``Placement`` (the
    geometry is shared, so a vendor assembly of tens of thousands of faces costs the projection and nothing else)."""
    framed = Part.Shape(Part.Compound(shapes))
    framed.Placement = App.Placement(_frame_matrix(centre, xdir, direction)).multiply(framed.Placement)
    return framed


def project_placed(shapes: list[Any], centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> sc.Projection:
    """The visible edges of ``shapes`` seen like ``project`` does, without hidden lines (an assembly's)."""
    visible, _ = _hlr(shapes, centre, xdir, direction, hidden=False)
    return sc.Projection(visible, [], [])


CLUSTER_MIN_SOLIDS = 8  # fewer solids than this: one projection of everything, as it always was
CLUSTER_MARGIN_MM = 0.01


def _view_boxes(solids: list[Any], centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[tuple[float, ...]]:
    """The box of each solid on the view plane (u0, v0, u1, v1), from the corners of its (conservative) 3D box."""
    ydir = sc.cross(direction, xdir)
    boxes = []
    for solid in solids:
        b = solid.BoundBox
        us, vs = [], []
        for x in (b.XMin, b.XMax):
            for y in (b.YMin, b.YMax):
                for z in (b.ZMin, b.ZMax):
                    d = sc.sub((x, y, z), centre)
                    us.append(sc.dot(d, xdir))
                    vs.append(sc.dot(d, ydir))
        boxes.append(
            (
                min(us) - CLUSTER_MARGIN_MM,
                min(vs) - CLUSTER_MARGIN_MM,
                max(us) + CLUSTER_MARGIN_MM,
                max(vs) + CLUSTER_MARGIN_MM,
            )
        )
    return boxes


def clusters(boxes: list[tuple[float, ...]]) -> list[list[int]]:
    """Group the boxes into the connected sets whose rectangles overlap (sweep over u): a body can only hide, or be hidden
    by, a body whose box overlaps its own on the view plane, so each set can be projected on its own."""
    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    active: list[int] = []
    for i in sorted(range(len(boxes)), key=lambda k: boxes[k][0]):
        active = [j for j in active if boxes[j][2] >= boxes[i][0]]
        for j in active:
            if boxes[j][1] <= boxes[i][3] and boxes[i][1] <= boxes[j][3]:
                parent[find(i)] = find(j)
        active.append(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: g[0])


def _solids_of(shapes: list[Any]) -> list[Any] | None:
    """Every solid of ``shapes``, or ``None`` when something else (loose faces, shells) would be left out."""
    solids: list[Any] = []
    for shape in shapes:
        found = shape.Solids
        if not found or len(shape.Faces) != sum(len(s.Faces) for s in found):
            return None
        solids += found
    return solids


def _hlr(
    shapes: list[Any], centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3, hidden: bool
) -> tuple[list[model.Entity], list[model.Entity]]:
    """Hidden-line removal of ``shapes`` in the frame of the view: ``(visible, hidden)`` entities. A part of many
    bodies is projected cluster by cluster (the bodies that overlap on the view plane together): HLR time and memory grow
    much faster than the number of bodies, and bodies that never overlap on the plane cannot hide one another."""
    td = _techdraw()
    sets = VISIBLE_SETS + (HIDDEN_SETS if hidden else ())
    solids = _solids_of(shapes) if sum(len(s.Solids) for s in shapes) >= CLUSTER_MIN_SOLIDS else None
    groups = (
        [solids]
        if solids is None
        else [[solids[i] for i in g] for g in clusters(_view_boxes(solids, centre, xdir, direction))]
    )
    visible: list[model.Entity] = []
    hid: list[model.Entity] = []
    for group in groups:
        result = td.projectEx(
            _framed(shapes if solids is None else group, centre, xdir, direction), App.Vector(0, 0, 1)
        )
        visible += _entities([result[i] for i in VISIBLE_SETS], model.CONTOUR)
        if hidden:
            hid += _entities([result[i] for i in HIDDEN_SETS if i in sets], model.HIDDEN)
        del result  # the HLR shapes of one cluster are freed before the next is made
    return visible, hid


VIEW_CACHE_MAX = 8  # the four standard views of a sheet, the exploded one and a little room
_VIEW_CACHE: dict[Any, sc.Projection] = {}


def project_view(key: Any, shapes: list[Any], centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> sc.Projection:
    """``project_placed`` kept under ``key`` (the poses of the shapes and the frame): a sheet is composed again on every
    item call, capture and export, and the projection of a big assembly takes minutes -- the same shapes seen the same
    way give the same answer. Only the entities are kept (never the shapes); the oldest entry goes first."""
    if key not in _VIEW_CACHE:
        projection = project_placed(shapes, centre, xdir, direction)
        while len(_VIEW_CACHE) >= VIEW_CACHE_MAX:
            _VIEW_CACHE.pop(next(iter(_VIEW_CACHE)))
        _VIEW_CACHE[key] = projection
    return _VIEW_CACHE[key]


def face_loops(face: Any, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[list[sc.Point]]:
    """The outline of one face in a view's coordinates."""
    ydir = sc.cross(direction, xdir)
    out: list[list[sc.Point]] = []
    for wire in face.Wires:
        pts = wire.discretize(Deflection=CHORD_DEVIATION_MM)
        out.append(
            [
                (sc.dot(sc.sub((p.x, p.y, p.z), centre), xdir), sc.dot(sc.sub((p.x, p.y, p.z), centre), ydir))
                for p in pts
            ]
        )
    return out
