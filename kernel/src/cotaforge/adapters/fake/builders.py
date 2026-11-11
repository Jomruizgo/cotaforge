"""Topology builders of the fake adapter.

Each builder mutates a ``FakeDocument`` through a ``Build`` context that records
everything the feature creates or modifies, so that ``undo_last`` (7.7) can revert it.
All geometry is analytic and exact for the supported cases; unsupported geometry raises
``AdapterError(code=not_supported)``.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cotaforge.adapters.fake.booleans import as_box, box_primitive, intersection_primitive, principal_axis
from cotaforge.adapters.fake.model import (
    TOLERANCE,
    FakeDocument,
    FakeEdge,
    FakeFace,
    FakeFeature,
    FakeVertex,
    Primitive,
    PrimitiveKind,
    bbox_corners,
    bbox_of_points,
    bbox_union,
    cylinder_bbox,
    disc_bbox,
    polygon_area_and_centroid,
    revolution_bbox,
    vadd,
    vclose,
    vcross,
    vdot,
    vlength,
    vscale,
    vsub,
    vunit,
)
from cotaforge.contract import (
    AdapterError,
    Axis,
    BoundingBox,
    CurveType,
    ErrorCode,
    ErrorInfo,
    Profile,
    SketchFrame,
    SurfaceType,
    Transform,
    Vec3,
)

Point2 = tuple[float, float]


def not_supported(message: str, operation_id: str | None = None) -> AdapterError:
    return AdapterError(
        ErrorInfo(code=ErrorCode.not_supported, message=message, operation_id=operation_id, recoverable=False)
    )


def invalid_request(message: str, operation_id: str | None = None) -> AdapterError:
    return AdapterError(
        ErrorInfo(code=ErrorCode.invalid_request, message=message, operation_id=operation_id, recoverable=False)
    )


def geometry_failed(message: str, operation_id: str | None = None) -> AdapterError:
    return AdapterError(
        ErrorInfo(code=ErrorCode.geometry_failed, message=message, operation_id=operation_id, recoverable=True)
    )


def rotation_about(axis: Axis, angle_deg: float) -> Transform:
    """Rigid rotation about an axis through ``axis.origin``.

    ``Transform.from_axis_angle`` rotates about the world origin and uses ``origin`` as a translation.
    """
    rotation = Transform.from_axis_angle(axis.direction, angle_deg)
    pivot = axis.origin
    return Transform(origin=vsub(pivot, rotation.apply_direction(pivot)), rotation=rotation.rotation)


def reflection_transform(plane: SketchFrame, operation_id: str | None = None) -> Transform:
    """The reflection across ``plane`` (its origin and normal) as an improper ``Transform``: ``p - 2 n (n . (p - o))``.
    Exact across an axis-aligned plane, where the rotation is a +/-1 diagonal and every primitive's box stays exact;
    across any other plane the primitives' axis-aligned boxes could not follow, so the fake says so."""
    normal = vunit(plane.n)
    if sum(1 for c in normal if abs(c) > 1e-9) != 1:
        msg = (
            "the fake adapter reflects only across an axis-aligned plane (its normal along X, Y or Z): an oblique "
            "reflection would need non-axis-aligned boxes; run it on FreeCAD"
        )
        raise not_supported(msg, operation_id)
    rotation = tuple(tuple((1.0 if i == j else 0.0) - 2.0 * normal[i] * normal[j] for j in range(3)) for i in range(3))
    shift = 2.0 * vdot(normal, plane.origin)
    return Transform(origin=vscale(normal, shift), rotation=rotation)  # type: ignore[arg-type]


def dihedral_degrees(normal_a: Vec3, normal_b: Vec3) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, vdot(normal_a, normal_b)))))


def orient_ccw(points: list[Point2]) -> list[Point2]:
    """Return the polygon with counter-clockwise winding in the (u, v) plane."""
    signed = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        signed += x0 * y1 - x1 * y0
    return list(points) if signed > 0 else list(reversed(points))


def rect_points(center: Point2, width: float, height: float) -> list[Point2]:
    cx, cy = center
    hw, hh = width / 2.0, height / 2.0
    return [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)]


# --- Build context -------------------------------------------------------------------------


@dataclass
class Build:
    """Records what a feature creates or modifies in a document."""

    doc: FakeDocument
    feature: FakeFeature
    body: str

    def _lookup(self, entity_id: str) -> Any:
        kind = entity_id.split(":", 1)[0]
        stores: dict[str, dict[str, Any]] = {
            "face": self.doc.faces,
            "edge": self.doc.edges,
            "vertex": self.doc.vertices,
            "prim": self.doc.primitives,
            "body": self.doc.bodies,
        }
        return stores[kind].get(entity_id)

    def is_created(self, entity_id: str) -> bool:
        f = self.feature
        return (
            entity_id in f.created_faces
            or entity_id in f.created_edges
            or entity_id in f.created_vertices
            or entity_id in f.primitives
            or entity_id in f.created_bodies
        )

    def touch(self, entity_id: str) -> None:
        """Snapshot a pre-existing entity before the feature mutates it, so undo can restore it."""
        if entity_id in self.feature.replaced or self.is_created(entity_id):
            return
        obj = self._lookup(entity_id)
        if obj is not None:
            self.feature.replaced[entity_id] = copy.deepcopy(obj)

    def add_primitive(self, prim: Primitive) -> Primitive:
        if self.body in self.doc.bodies:
            self.touch(self.body)
        self.doc.primitives[prim.id] = prim
        self.doc.bodies.setdefault(self.body, []).append(prim.id)
        self.feature.primitives.append(prim.id)
        if self.body not in self.feature.created_bodies and self.body not in self.feature.modified_bodies:
            self.feature.modified_bodies.append(self.body)
        return prim

    def new_primitive_id(self) -> str:
        return self.doc.next_id("prim")

    def add_face(
        self,
        surface: SurfaceType,
        center: Vec3,
        area_mm2: float,
        bbox: BoundingBox,
        *,
        normal: Vec3 | None = None,
        axis: Axis | None = None,
        radius: float | None = None,
        polygon: list[Vec3] | None = None,
        length: float | None = None,
        outer_radius: float | None = None,
    ) -> FakeFace:
        face = FakeFace(
            id=self.doc.next_id("face"),
            surface=surface,
            normal=normal,
            axis=axis,
            center=center,
            radius=radius,
            area_mm2=area_mm2,
            bbox=bbox,
            body=self.body,
            created_by=self.feature.id,
            polygon=polygon,
            length=length,
            outer_radius=outer_radius,
        )
        self.doc.faces[face.id] = face
        self.feature.created_faces.append(face.id)
        return face

    def add_line_edge(
        self, face_a: str, face_b: str, start: Vec3, end: Vec3, *, convex: bool | None, dihedral_deg: float | None
    ) -> FakeEdge:
        edge = FakeEdge(
            id=self.doc.next_id("edge"),
            curve=CurveType.line,
            faces=(face_a, face_b),
            start=start,
            end=end,
            center=None,
            axis=None,
            radius=None,
            length_mm=vlength(vsub(end, start)),
            bbox=bbox_of_points((start, end)),
            convex=convex,
            dihedral_deg=dihedral_deg,
        )
        self._register_edge(edge)
        return edge

    def add_circle_edge(
        self,
        face_a: str,
        face_b: str,
        center: Vec3,
        direction: Vec3,
        radius: float,
        *,
        convex: bool | None,
        dihedral_deg: float | None,
        fraction: float = 1.0,
    ) -> FakeEdge:
        edge = FakeEdge(
            id=self.doc.next_id("edge"),
            curve=CurveType.circle if fraction >= 1.0 else CurveType.arc,
            faces=(face_a, face_b),
            start=None,
            end=None,
            center=center,
            axis=Axis(origin=center, direction=direction),
            radius=radius,
            length_mm=2.0 * math.pi * radius * fraction,
            bbox=disc_bbox(center, direction, radius),
            convex=convex,
            dihedral_deg=dihedral_deg,
        )
        self._register_edge(edge)
        return edge

    def _register_edge(self, edge: FakeEdge) -> None:
        self.doc.edges[edge.id] = edge
        self.feature.created_edges.append(edge.id)
        for face_id in edge.faces:
            self.touch(face_id)
            face_edges = self.doc.faces[face_id].edges
            if edge.id not in face_edges:
                face_edges.append(edge.id)

    def add_vertex(self, point: Vec3, edges: list[str]) -> FakeVertex:
        vertex = FakeVertex(id=self.doc.next_id("vertex"), point=point, edges=list(edges))
        self.doc.vertices[vertex.id] = vertex
        self.feature.created_vertices.append(vertex.id)
        return vertex

    def modify_face_area(self, face_id: str, delta: float) -> None:
        self.touch(face_id)
        face = self.doc.faces[face_id]
        new_area = face.area_mm2 + delta
        if new_area < 0.0:
            raise geometry_failed(f"face {face_id} area would go negative ({new_area:.6g} mm2)")
        face.area_mm2 = new_area
        self.feature.face_area_deltas[face_id] = self.feature.face_area_deltas.get(face_id, 0.0) + delta
        self.mark_modified(face_id)

    def mark_modified(self, face_id: str) -> None:
        self.touch(face_id)
        face = self.doc.faces[face_id]
        if self.feature.id not in face.modified_by:
            face.modified_by.append(self.feature.id)
        if face_id not in self.feature.modified_faces:
            self.feature.modified_faces.append(face_id)

    def remove_edge(self, edge_id: str) -> FakeEdge:
        self.touch(edge_id)
        edge = self.doc.edges.pop(edge_id)
        self.feature.removed_edges.append(edge_id)
        self.feature.removed_edge_objects[edge_id] = edge
        for face_id in edge.faces:
            face = self.doc.faces.get(face_id)
            if face is not None and edge_id in face.edges:
                self.touch(face_id)
                face.edges.remove(edge_id)
        for vertex in list(self.doc.vertices.values()):
            if edge_id in vertex.edges:
                self.touch(vertex.id)
                vertex.edges.remove(edge_id)
        return edge


# --- Queries used by builders ---------------------------------------------------------------


def positive_bbox(doc: FakeDocument, body: str | None = None) -> BoundingBox | None:
    prims = doc.primitives.values() if body is None else doc.body_primitives(body)
    box: BoundingBox | None = None
    for prim in prims:
        if prim.sign > 0:
            box = bbox_union(box, prim.bbox)
    return box


def axial_range_from(doc: FakeDocument, body: str, point: Vec3, direction: Vec3) -> tuple[float, float]:
    """``(near, far)`` distances from ``point`` along ``direction`` to the body's positive material (bbox level).

    ``near`` is clamped to zero so a plane inside the body starts the range at the plane itself.
    """
    box = positive_bbox(doc, body)
    if box is None:
        return (0.0, 0.0)
    corners = [
        (x, y, z) for x in (box.min[0], box.max[0]) for y in (box.min[1], box.max[1]) for z in (box.min[2], box.max[2])
    ]
    distances = [vdot(vsub(c, point), direction) for c in corners]
    near = max(0.0, min(distances))
    far = max(0.0, max(distances))
    return (near, far)


def find_planar_face(
    doc: FakeDocument, body: str, point: Vec3, normal: Vec3, *, exclude: set[str] | None = None
) -> FakeFace | None:
    """A planar face of ``body`` whose outward normal is ``normal`` and whose plane contains ``point``."""
    for face in doc.faces.values():
        if face.body != body or face.surface is not SurfaceType.plane or face.normal is None:
            continue
        if exclude and face.id in exclude:
            continue
        if vdot(face.normal, normal) < 1.0 - 1e-6:
            continue
        if abs(vdot(vsub(face.center, point), normal)) > 1e-6:
            continue
        inside = all(face.bbox.min[i] - 1e-6 <= point[i] <= face.bbox.max[i] + 1e-6 for i in range(3))
        if inside:
            return face
    return None


# --- Prism ----------------------------------------------------------------------------------


def build_prism(
    b: Build,
    base: list[Vec3],
    direction: Vec3,
    depth: float,
    sign: int,
    *,
    blind: bool = True,
    entry_face: str | None = None,
    exit_face: str | None = None,
    frame: SketchFrame | None = None,
) -> Primitive:
    """Prism from a planar base loop (counter-clockwise about ``direction``) swept by ``depth``.

    ``sign=+1`` builds a boss (k + 2 faces, 3k edges) when ``entry_face`` is ``None`` — a free-standing
    solid, or one that will not be merged with anything at its base. When ``entry_face`` is given (a boss
    whose sketch lies on that existing face and merges into its body), the base cap
    does not become a new face: it merges into ``entry_face``, so only k + 1 faces are created (side walls
    plus the far cap) — this matches FreeCAD, where the host face is *modified*, not duplicated.

    ``sign=-1`` builds a pocket whose side faces point inward, with a bottom face when ``blind``.
    """
    k = len(base)
    top = [vadd(p, vscale(direction, depth)) for p in base]
    area, base_centroid = polygon_area_and_centroid(tuple(base), direction)
    prim = Primitive(
        id=b.new_primitive_id(),
        kind=PrimitiveKind.prism,
        sign=sign,
        volume_mm3=area * depth,
        centroid=vadd(base_centroid, vscale(direction, depth / 2.0)),
        bbox=bbox_of_points(tuple(base) + tuple(top)),
        feature=b.feature.id,
        polygon=tuple(base),
        direction=direction,
        depth=depth,
        frame=frame,
    )
    b.add_primitive(prim)

    outward: list[Vec3] = []
    for i in range(k):
        edge_dir = vunit(vsub(base[(i + 1) % k], base[i]))
        outward.append(vunit(vcross(edge_dir, direction)))
    side_sign = 1.0 if sign > 0 else -1.0

    sides: list[FakeFace] = []
    for i in range(k):
        j = (i + 1) % k
        quad = [base[i], base[j], top[j], top[i]]
        side_area = vlength(vsub(base[j], base[i])) * depth
        center = vscale(vadd(vadd(base[i], base[j]), vadd(top[i], top[j])), 0.25)
        sides.append(
            b.add_face(
                SurfaceType.plane,
                center,
                side_area,
                bbox_of_points(tuple(quad)),
                normal=vscale(outward[i], side_sign),
                polygon=quad,
            )
        )

    if sign > 0:
        near_id: str | None
        if entry_face is not None:
            near_id = entry_face
        else:
            bottom = b.add_face(
                SurfaceType.plane,
                base_centroid,
                area,
                bbox_of_points(tuple(base)),
                normal=vscale(direction, -1.0),
                polygon=list(reversed(base)),
            )
            near_id = bottom.id
        far: FakeFace | None = b.add_face(
            SurfaceType.plane,
            vadd(base_centroid, vscale(direction, depth)),
            area,
            bbox_of_points(tuple(top)),
            normal=direction,
            polygon=top,
        )
        far_id: str | None = far.id if far else None
    else:
        near_id = entry_face
        if blind:
            far_face = b.add_face(
                SurfaceType.plane,
                vadd(base_centroid, vscale(direction, depth)),
                area,
                bbox_of_points(tuple(top)),
                normal=vscale(direction, -1.0),
                polygon=list(reversed(top)),
            )
            far_id = far_face.id
        else:
            far_id = exit_face
    convex_ring = sign > 0

    near_edges: list[FakeEdge | None] = []
    far_edges: list[FakeEdge | None] = []
    vertical: list[FakeEdge] = []
    for i in range(k):
        j = (i + 1) % k
        near_edges.append(
            b.add_line_edge(near_id, sides[i].id, base[i], base[j], convex=convex_ring, dihedral_deg=90.0)
            if near_id is not None
            else None
        )
        far_edges.append(
            b.add_line_edge(sides[i].id, far_id, top[i], top[j], convex=convex_ring, dihedral_deg=90.0)
            if far_id is not None
            else None
        )
    for i in range(k):
        h = (i - 1) % k
        turn = vdot(vcross(vsub(base[i], base[h]), vsub(base[(i + 1) % k], base[i])), direction)
        convex = (turn > 0) if sign > 0 else False
        vertical.append(
            b.add_line_edge(
                sides[h].id,
                sides[i].id,
                base[i],
                top[i],
                convex=convex,
                dihedral_deg=dihedral_degrees(outward[h], outward[i]),
            )
        )
    for i in range(k):
        h = (i - 1) % k
        base_edges = [e.id for e in (near_edges[h], near_edges[i]) if e is not None] + [vertical[i].id]
        top_edges = [e.id for e in (far_edges[h], far_edges[i]) if e is not None] + [vertical[i].id]
        b.add_vertex(base[i], base_edges)
        b.add_vertex(top[i], top_edges)
    return prim


# --- Shell (inward) -----------------------------------------------------------------------------


def _wall_does_not_fit(message: str, operation_id: str | None) -> AdapterError:
    """A wall the geometry cannot hold: asking again with the same thickness fails the same way."""
    return AdapterError(
        ErrorInfo(code=ErrorCode.geometry_failed, message=message, operation_id=operation_id, recoverable=False)
    )


def build_shell(b: Build, faces: list[FakeFace], thickness: float, *, operation_id: str | None = None) -> None:
    """Hollow an UNTOUCHED axis-aligned box or right cylinder inward through ``faces`` (the removed faces).

    The cavity is a negative primitive (a pocket): one removed face is a blind pocket (inner walls and a floor), two
    opposite ones a through cavity (inner walls only), exactly the face counts FreeCAD's ``Thickness`` gives. The
    removed faces keep only their rim. Anything the analytic bookkeeping cannot model exactly (an edited body, a
    removed side wall, adjacent removed faces) is ``not_supported`` rather than approximated."""
    doc = b.doc
    prims = doc.body_primitives(b.body)
    body_faces = [f for f in doc.faces.values() if f.body == b.body]
    if len(prims) != 1 or prims[0].sign < 0 or any(f.created_by != prims[0].feature for f in body_faces):
        raise not_supported(
            "shell: the fake adapter only hollows an untouched box or cylinder (no hole, boss, fillet or earlier "
            "shell on the body); run it on FreeCAD",
            operation_id,
        )
    if any(f.normal is None or f.surface is not SurfaceType.plane for f in faces):
        raise not_supported(
            "shell: the fake adapter only removes planar faces (the end faces of a box or cylinder)", operation_id
        )
    prim = prims[0]
    if prim.kind is PrimitiveKind.prism:
        _shell_box(b, prim, faces, thickness, operation_id)
    elif prim.kind is PrimitiveKind.cylinder:
        _shell_cylinder(b, prim, faces, thickness, operation_id)
    else:
        raise not_supported("shell: the fake adapter only hollows a box or a cylinder", operation_id)


def _shell_box(b: Build, prim: Primitive, faces: list[FakeFace], thickness: float, operation_id: str | None) -> None:
    box = as_box(prim)
    if box is None:
        raise not_supported("shell: the fake adapter only hollows an axis-aligned box", operation_id)
    sides: dict[int, dict[bool, FakeFace]] = {}
    for face in faces:
        assert face.normal is not None
        axis = max(range(3), key=lambda i: abs(face.normal[i]))  # type: ignore[index]
        if abs(abs(face.normal[axis]) - 1.0) > 1e-6:  # type: ignore[index]
            raise not_supported("shell: the fake adapter only removes the faces of an axis-aligned box", operation_id)
        sides.setdefault(axis, {})[face.normal[axis] > 0] = face  # type: ignore[index]
    if len(sides) != 1:
        raise not_supported(
            "shell: the fake adapter removes one face or two OPPOSITE faces; adjacent removed faces need FreeCAD",
            operation_id,
        )
    ((axis, removed),) = sides.items()
    through = len(removed) == 2
    extents = [box.max[i] - box.min[i] for i in range(3)]
    lateral = [i for i in range(3) if i != axis]
    for i in lateral:
        if extents[i] - 2.0 * thickness <= TOLERANCE:
            raise _wall_does_not_fit(
                f"shell wall {thickness:g} mm leaves no cavity along {'XYZ'[i]}: the body is {extents[i]:g} mm "
                "there with two walls; use a thinner wall",
                operation_id,
            )
    depth = extents[axis] - (0.0 if through else thickness)
    if depth <= TOLERANCE:
        raise _wall_does_not_fit(
            f"shell wall {thickness:g} mm leaves no cavity along {'XYZ'[axis]}: the body is {extents[axis]:g} mm "
            "there with one wall; use a thinner wall",
            operation_id,
        )
    entry = removed.get(True) or removed[False]  # the high face when both are removed
    exit_face = removed[False] if through else None
    assert entry.normal is not None
    direction = vscale(entry.normal, -1.0)
    plane = box.max[axis] if entry.normal[axis] > 0 else box.min[axis]
    first, second = lateral
    unit = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    if vdot(vcross(unit[first], unit[second]), direction) < 0:
        first, second = second, first  # counter-clockwise about the pocket direction

    def corner(u: float, v: float) -> Vec3:
        point = [0.0, 0.0, 0.0]
        point[axis], point[first], point[second] = plane, u, v
        return (point[0], point[1], point[2])

    lo1, hi1 = box.min[first] + thickness, box.max[first] - thickness
    lo2, hi2 = box.min[second] + thickness, box.max[second] - thickness
    loop = [corner(lo1, lo2), corner(hi1, lo2), corner(hi1, hi2), corner(lo1, hi2)]
    build_prism(
        b,
        loop,
        direction,
        depth,
        -1,
        blind=not through,
        entry_face=entry.id,
        exit_face=exit_face.id if exit_face else None,
    )
    ring = (hi1 - lo1) * (hi2 - lo2)
    b.modify_face_area(entry.id, -ring)
    if exit_face is not None:
        b.modify_face_area(exit_face.id, -ring)


def _shell_cylinder(
    b: Build, prim: Primitive, faces: list[FakeFace], thickness: float, operation_id: str | None
) -> None:
    assert prim.axis is not None and prim.radius is not None and prim.height is not None
    axis = prim.axis
    caps: dict[str, FakeFace] = {}
    for face in faces:
        assert face.normal is not None
        sense = vdot(face.normal, axis.direction)
        along = vdot(vsub(face.center, axis.origin), axis.direction)
        if abs(abs(sense) - 1.0) > 1e-6:
            raise not_supported("shell: the fake adapter only removes the end caps of a cylinder", operation_id)
        if abs(along) <= TOLERANCE and sense < 0:
            caps["base"] = face
        elif abs(along - prim.height) <= TOLERANCE and sense > 0:
            caps["top"] = face
        else:
            raise not_supported("shell: the fake adapter only removes the end caps of a cylinder", operation_id)
    through = len(caps) == 2
    radius = prim.radius - thickness
    if radius <= TOLERANCE:
        raise _wall_does_not_fit(
            f"shell wall {thickness:g} mm leaves no cavity: the cylinder radius is {prim.radius:g} mm; "
            "use a thinner wall",
            operation_id,
        )
    height = prim.height - (0.0 if through else thickness)
    if height <= TOLERANCE:
        raise _wall_does_not_fit(
            f"shell wall {thickness:g} mm leaves no cavity along the axis: the cylinder is {prim.height:g} mm long "
            "with one end wall; use a thinner wall",
            operation_id,
        )
    if "top" in caps and not through:
        origin, direction = vadd(axis.origin, vscale(axis.direction, prim.height)), vscale(axis.direction, -1.0)
        entry = caps["top"]
    else:
        origin, direction = axis.origin, axis.direction
        entry = caps["base"]
    exit_face = caps["top"] if through else None
    build_cylinder(
        b,
        origin,
        direction,
        radius,
        height,
        -1,
        blind=not through,
        entry_face=entry.id,
        exit_face=exit_face.id if exit_face else None,
    )
    ring = math.pi * radius * radius
    b.modify_face_area(entry.id, -ring)
    if exit_face is not None:
        b.modify_face_area(exit_face.id, -ring)


# --- Footprints on a planar face (holes and cuts) --------------------------------------------


def _plane_basis(normal: Vec3) -> tuple[Vec3, Vec3]:
    """Any orthonormal (u, v) spanning the plane of ``normal`` (the face's own sketch frame is not needed
    for a containment test; only the projection matters)."""
    n = vunit(normal)
    seed: Vec3 = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = vunit(vsub(seed, vscale(n, vdot(seed, n))))
    return u, vcross(n, u)


def _project(point: Vec3, origin: Vec3, u: Vec3, v: Vec3) -> Point2:
    rel = vsub(point, origin)
    return (vdot(rel, u), vdot(rel, v))


def _point_in_polygon(point: Point2, polygon: list[Point2]) -> bool:
    """Even-odd ray casting; a point on the boundary counts as inside (callers apply the radius margin)."""
    x, y = point
    inside = False
    count = len(polygon)
    for i in range(count):
        (x0, y0), (x1, y1) = polygon[i], polygon[(i + 1) % count]
        if (y0 > y) != (y1 > y):
            x_cross = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < x_cross:
                inside = not inside
    return inside


def _distance_to_segment(point: Point2, a: Point2, b: Point2) -> float:
    ax, ay = a
    bx, by = b
    px, py = point
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    t = 0.0 if length_sq <= 1e-18 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def circle_within_face(face: FakeFace, center: Vec3, radius: float, tol: float = TOLERANCE) -> bool:
    """Whether a circle of ``radius`` centred at ``center`` (a point on the face's plane) lies entirely inside
    the planar ``face``: exact for prism faces (``polygon``: the centre is inside the outer loop and no edge
    is closer than the radius) and for discs/annuli (``outer_radius``: the outer rim only — an inner bore is
    not recorded, so a hole overlapping it is not caught); a bounding-box test for any other planar face."""
    if face.normal is None:
        return True
    if face.polygon:
        u, v = _plane_basis(face.normal)
        loop = [_project(p, face.center, u, v) for p in face.polygon]
        c = _project(center, face.center, u, v)
        if not _point_in_polygon(c, loop):
            return False
        edges = ((loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop)))
        return all(_distance_to_segment(c, a, b) >= radius - tol for a, b in edges)
    if face.outer_radius is not None:
        return vlength(vsub(center, face.center)) + radius <= face.outer_radius + tol
    disc = disc_bbox(center, face.normal, radius)
    return all(face.bbox.min[i] - tol <= disc.min[i] and disc.max[i] <= face.bbox.max[i] + tol for i in range(3))


def profile_touches_face(face: FakeFace, frame: SketchFrame, profile: Profile, tol: float = TOLERANCE) -> bool:
    """Whether a sketch profile drawn on ``face`` (``frame`` is the sketch's frame on it) has at least part of
    its footprint on the face — a cut whose profile lies entirely outside its host face removes nothing
    from it. A prism face is tested against its outer loop: a circle profile with an exact disc-polygon
    overlap (the centre inside the loop, or the disc within ``radius`` of one of the loop's edges — this
    catches a thin diagonal sliver that four cardinal sample points would miss), other profile kinds
    with the profile's centroid and corners; other planar faces at bounding-box level."""
    if face.normal is not None and face.polygon:
        u, v = _plane_basis(face.normal)
        loop = [_project(p, face.center, u, v) for p in face.polygon]
        if profile.kind == "circle" and profile.center is not None and profile.radius is not None:
            c = _project(frame.to_world(*profile.center), face.center, u, v)
            if _point_in_polygon(c, loop):
                return True
            edges = ((loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop)))
            return any(_distance_to_segment(c, a, b) <= profile.radius + tol for a, b in edges)
        samples: list[Vec3] = [frame.to_world(*profile.centroid)]
        if profile.kind == "polygon" and profile.points:
            samples += [frame.to_world(pu, pv) for pu, pv in profile.points]
        else:
            (u0, v0), (u1, v1) = profile.bbox_uv
            samples += [frame.to_world(pu, pv) for pu in (u0, u1) for pv in (v0, v1)]
        return any(_point_in_polygon(_project(p, face.center, u, v), loop) for p in samples)
    (p_u0, p_v0), (p_u1, p_v1) = profile.bbox_uv
    (h_u0, h_v0), (h_u1, h_v1) = face_footprint_uv(face, frame)
    return p_u0 <= h_u1 + tol and p_u1 >= h_u0 - tol and p_v0 <= h_v1 + tol and p_v1 >= h_v0 - tol


# --- On-face extrude coverage --------------------------------------


def face_footprint_uv(face: FakeFace, frame: SketchFrame) -> tuple[Point2, Point2]:
    """The face's own world bbox, projected onto the sketch frame's (``u``, ``v``) axes relative to
    ``frame.origin`` (the face centre). Mirrors (duplicated — the dependency rule forbids
    ``adapters.fake`` from importing ``cotaforge.verification``) ``verification/expected.py``'s
    ``_face_footprint_uv``, which every fake/FreeCAD behaviour here must stay in parity with."""
    us: list[float] = []
    vs: list[float] = []
    for corner in bbox_corners(face.bbox):
        rel = vsub(corner, frame.origin)
        us.append(vdot(rel, frame.u))
        vs.append(vdot(rel, frame.v))
    return (min(us), min(vs)), (max(us), max(vs))


def classify_face_coverage(profile: Profile, host_bbox_uv: tuple[Point2, Point2], tol: float) -> str:
    """``inside`` / ``covers`` / ``flush`` / ``overhangs`` — duplicated from (and must stay in parity with)
    ``verification/expected.py::_classify_coverage``; see that function's docstring for the rule."""
    (p_u0, p_v0), (p_u1, p_v1) = profile.bbox_uv
    (h_u0, h_v0), (h_u1, h_v1) = host_bbox_uv
    if p_u0 > h_u0 + tol and p_v0 > h_v0 + tol and p_u1 < h_u1 - tol and p_v1 < h_v1 - tol:
        return "inside"
    if profile.kind == "circle" and profile.center is not None and profile.radius is not None:
        cu, cv = profile.center
        radius = profile.radius
        corners = ((h_u0, h_v0), (h_u0, h_v1), (h_u1, h_v0), (h_u1, h_v1))
        covers = all(math.hypot(cu - hu, cv - hv) <= radius + tol for hu, hv in corners)
    else:
        covers = p_u0 <= h_u0 + tol and p_v0 <= h_v0 + tol and p_u1 >= h_u1 - tol and p_v1 >= h_v1 - tol
    if covers:
        return "covers"
    within = p_u0 >= h_u0 - tol and p_v0 >= h_v0 - tol and p_u1 <= h_u1 + tol and p_v1 <= h_v1 + tol
    return "flush" if within else "overhangs"


def _find_shared_line_edge(
    doc: FakeDocument, face_a: str, face_b: str, operation_id: str | None = None
) -> FakeEdge | None:
    """The line edge whose two adjacent faces are exactly ``{face_a, face_b}`` -- the vertical edge shared
    by two side faces at a box corner. Raises ``geometry_failed`` if more than one such edge exists, since
    silently returning the first match would pick an arbitrary edge to extend/remove instead of surfacing
    the inconsistent document state."""
    wanted = {face_a, face_b}
    matches = [edge for edge in doc.edges.values() if edge.curve is CurveType.line and set(edge.faces) == wanted]
    if len(matches) > 1:
        raise geometry_failed(f"ambiguous shared edge between faces {face_a} and {face_b}", operation_id)
    return matches[0] if matches else None


def _extend_vertical_edge(b: Build, edge: FakeEdge, old_point: Vec3, new_point: Vec3) -> None:
    """Move the endpoint of ``edge`` sitting at ``old_point`` to ``new_point``, snapshotting first so undo
    restores the pre-extension edge: a covered host's side faces are *extended*, not
    rebuilt, so their vertical edges must grow along with them instead of dangling at the old height.
    """
    b.touch(edge.id)
    live = b.doc.edges[edge.id]
    if live.start is not None and vclose(live.start, old_point, TOLERANCE):
        live.start = new_point
    elif live.end is not None and vclose(live.end, old_point, TOLERANCE):
        live.end = new_point
    else:
        raise geometry_failed(f"edge {edge.id} does not end at the expected corner while extending it")
    assert live.start is not None and live.end is not None
    live.length_mm = vlength(vsub(live.end, live.start))
    live.bbox = bbox_of_points((live.start, live.end))


def merge_extrude_covering_host_face(
    b: Build,
    host_face: FakeFace,
    base: list[Vec3],
    direction: Vec3,
    depth: float,
    frame: SketchFrame | None = None,
    operation_id: str | None = None,
) -> FakeFace:
    """The ``covers`` case of an on-face merge extrude: the profile's footprint equals or contains the
    host face's, so — matching real FreeCAD (see ``tests/integration/freecad/test_boss_on_face.py``) — the host face is
    entirely consumed rather than merely shrunk. ``host_face`` is removed outright (any later reference to its handle
    becomes a stale reference, like FreeCAD's), each side face adjacent to it is *modified* (extended by ``depth``
    along ``direction``) instead of a new side wall being created, and only the far cap is new.

    Each host-face edge is paired with the base loop's edge whose endpoints coincide with it (within
    ``TOLERANCE``), regardless of index or winding — pairing by index alone is wrong on any host face
    whose own edge order does not happen to match the new base loop's order (e.g. a non-Z side face),
    since it silently swaps which neighbouring face receives which area delta. Every host edge must
    match exactly one base edge; a host edge with no match (or a document with a face/edge in an
    inconsistent state) raises ``geometry_failed`` rather than corrupting a sibling face's area.

    A new boundary edge is created between the cap and each extended side face, at the far end (the
    matched base edge translated by ``depth`` along ``direction``), so the cap and its neighbours have
    real topology (``edges_of``/``edges_between``) instead of an empty edge list.

    Each of the ``k`` old host-face corners is also fixed up to match FreeCAD: the vertical edge shared by
    the two side faces meeting at that corner is extended from the old corner to the new cap corner (its
    far endpoint moves by ``depth`` along ``direction``; ``_extend_vertical_edge``), the stale corner
    vertex is removed, and a new vertex is registered at the cap corner referencing the extended vertical
    edge plus the two new cap-boundary edges -- so ``vertex_at``/``vertices_of`` resolve on the cap the
    same way they do in FreeCAD, instead of returning nothing.
    """
    k = len(base)
    top = [vadd(p, vscale(direction, depth)) for p in base]
    area, base_centroid = polygon_area_and_centroid(tuple(base), direction)

    # Register the boss's own solid volume (build_prism does this too -- omitted here would silently
    # under-report volume_mm3 since this function otherwise only touches faces/edges, not primitives).
    prim = Primitive(
        id=b.new_primitive_id(),
        kind=PrimitiveKind.prism,
        sign=1,
        volume_mm3=area * depth,
        centroid=vadd(base_centroid, vscale(direction, depth / 2.0)),
        bbox=bbox_of_points(tuple(base) + tuple(top)),
        feature=b.feature.id,
        polygon=tuple(base),
        direction=direction,
        depth=depth,
        frame=frame,
    )
    b.add_primitive(prim)

    base_edges = [(base[i], base[(i + 1) % k]) for i in range(k)]
    host_edge_ids = list(host_face.edges)
    matched: set[int] = set()
    sibling_of_index: dict[int, str] = {}

    for edge_id in host_edge_ids:
        edge = b.doc.edges.get(edge_id)
        match_index: int | None = None
        if edge is not None and edge.start is not None and edge.end is not None:
            for idx, (p0, p1) in enumerate(base_edges):
                if idx in matched:
                    continue
                if (vclose(edge.start, p0, TOLERANCE) and vclose(edge.end, p1, TOLERANCE)) or (
                    vclose(edge.start, p1, TOLERANCE) and vclose(edge.end, p0, TOLERANCE)
                ):
                    match_index = idx
                    break
        if match_index is None:
            raise geometry_failed(
                f"host edge {edge_id} has no matching base edge while merging a covering extrude", operation_id
            )
        matched.add(match_index)
        if edge is not None:
            sibling_id = edge.faces[0] if edge.faces[1] == host_face.id else edge.faces[1]
            sibling = b.doc.faces.get(sibling_id) if sibling_id != host_face.id else None
            if sibling is not None:
                p0, p1 = base_edges[match_index]
                edge_length = vlength(vsub(p1, p0))
                b.modify_face_area(sibling_id, edge_length * depth)
                sibling = b.doc.faces[sibling_id]
                j = (match_index + 1) % k
                sibling.bbox = bbox_union(sibling.bbox, bbox_of_points((top[match_index], top[j])))
                sibling_of_index[match_index] = sibling_id
        b.remove_edge(edge_id)

    b.touch(host_face.id)
    del b.doc.faces[host_face.id]

    cap = b.add_face(
        SurfaceType.plane,
        vadd(base_centroid, vscale(direction, depth)),
        area,
        bbox_of_points(tuple(top)),
        normal=direction,
        polygon=top,
    )

    cap_edges: dict[int, str] = {}
    for idx, sibling_id in sibling_of_index.items():
        j = (idx + 1) % k
        cap_edge = b.add_line_edge(sibling_id, cap.id, top[idx], top[j], convex=True, dihedral_deg=90.0)
        cap_edges[idx] = cap_edge.id

    for i in range(k):
        h = (i - 1) % k
        sib_h = sibling_of_index.get(h)
        sib_i = sibling_of_index.get(i)
        vertex_edges = [cap_edges[e] for e in (h, i) if e in cap_edges]
        if sib_h is not None and sib_i is not None:
            vertical_edge = _find_shared_line_edge(b.doc, sib_h, sib_i, operation_id)
            if vertical_edge is None:
                raise geometry_failed(
                    f"no vertical edge between faces {sib_h} and {sib_i} while extending corner {i} of a "
                    "covering merge extrude",
                    operation_id,
                )
            old_vertex = next(
                (
                    v
                    for v in b.doc.vertices.values()
                    if vertical_edge.id in v.edges and vclose(v.point, base[i], TOLERANCE)
                ),
                None,
            )
            _extend_vertical_edge(b, vertical_edge, base[i], top[i])
            vertex_edges.append(vertical_edge.id)
            if old_vertex is not None:
                b.touch(old_vertex.id)
                del b.doc.vertices[old_vertex.id]
        b.add_vertex(top[i], vertex_edges)

    return cap


# --- Cylinder ---------------------------------------------------------------------------------


def build_cylinder(
    b: Build,
    origin: Vec3,
    direction: Vec3,
    radius: float,
    height: float,
    sign: int,
    *,
    blind: bool = True,
    entry_face: str | None = None,
    exit_face: str | None = None,
    frame: SketchFrame | None = None,
) -> Primitive:
    """Cylinder with its base disc centred at ``origin`` growing along ``direction``.

    Mirrors ``build_prism``'s ``entry_face`` handling: when ``sign > 0`` and ``entry_face`` is given (a
    cylindrical boss whose sketch lies on that existing face and merges into its body), the near disc
    merges into ``entry_face`` instead of becoming a new face.
    """
    axis = Axis(origin=origin, direction=direction)
    prim = Primitive(
        id=b.new_primitive_id(),
        kind=PrimitiveKind.cylinder,
        sign=sign,
        volume_mm3=math.pi * radius * radius * height,
        centroid=vadd(origin, vscale(direction, height / 2.0)),
        bbox=cylinder_bbox(origin, direction, radius, height),
        feature=b.feature.id,
        axis=axis,
        radius=radius,
        height=height,
        frame=frame,
    )
    b.add_primitive(prim)
    far_point = vadd(origin, vscale(direction, height))
    disc_area = math.pi * radius * radius
    lateral = b.add_face(
        SurfaceType.cylinder,
        vadd(origin, vscale(direction, height / 2.0)),
        2.0 * math.pi * radius * height,
        prim.bbox,
        axis=axis,
        radius=radius,
        length=height,
    )
    if sign > 0:
        near_id: str | None
        if entry_face is not None:
            near_id = entry_face
        else:
            near = b.add_face(
                SurfaceType.plane,
                origin,
                disc_area,
                disc_bbox(origin, direction, radius),
                normal=vscale(direction, -1.0),
                outer_radius=radius,
            )
            near_id = near.id
        far = b.add_face(
            SurfaceType.plane,
            far_point,
            disc_area,
            disc_bbox(far_point, direction, radius),
            normal=direction,
            outer_radius=radius,
        )
        far_id: str | None = far.id
        convex = True
    else:
        near_id = entry_face
        if blind:
            bottom = b.add_face(
                SurfaceType.plane,
                far_point,
                disc_area,
                disc_bbox(far_point, direction, radius),
                normal=vscale(direction, -1.0),
                outer_radius=radius,
            )
            far_id = bottom.id
        else:
            far_id = exit_face
        convex = False
    if near_id is not None:
        b.add_circle_edge(lateral.id, near_id, origin, direction, radius, convex=convex, dihedral_deg=90.0)
    if far_id is not None:
        b.add_circle_edge(lateral.id, far_id, far_point, direction, radius, convex=convex, dihedral_deg=90.0)
    return prim


# --- Revolution ---------------------------------------------------------------------------------


def build_revolution(
    b: Build,
    frame: SketchFrame,
    points_uv: list[Point2],
    axis: Axis,
    angle_deg: float,
    sign: int = 1,
    *,
    operation_id: str | None = None,
) -> Primitive:
    """Revolve a polygon with segments parallel/perpendicular to an axis lying in the sketch plane."""
    d = axis.direction
    n = frame.n
    if abs(vdot(d, n)) > 1e-9 or abs(vdot(vsub(axis.origin, frame.origin), n)) > 1e-6:
        raise not_supported("revolve axis must lie in the sketch plane", operation_id)
    if not 0.0 < angle_deg <= 360.0:
        raise invalid_request("revolve angle must be in (0, 360]", operation_id)
    e = vunit(vcross(n, d))
    world = [frame.to_world(u, v) for u, v in points_uv]
    ar = [(vdot(vsub(p, axis.origin), d), vdot(vsub(p, axis.origin), e)) for p in world]
    if all(r <= TOLERANCE for _, r in ar):
        e = vscale(e, -1.0)
        ar = [(a, -r) for a, r in ar]
    if any(r < -TOLERANCE for _, r in ar):
        raise not_supported("revolve profile must not cross the axis", operation_id)
    ar = [(a, max(r, 0.0)) for a, r in ar]
    ar = orient_ccw(ar)
    k = len(ar)

    fraction = angle_deg / 360.0
    # Polygon integrals in (a, r): area, int r dA, int a r dA, int r^2 dA.
    area = 0.0
    int_r = 0.0
    int_ar = 0.0
    int_r2 = 0.0
    for i in range(k):
        x0, y0 = ar[i]
        x1, y1 = ar[(i + 1) % k]
        cross = x0 * y1 - x1 * y0
        area += cross / 2.0
        int_r += cross * (y0 + y1) / 6.0
        int_ar += cross * (2 * x0 * y0 + x0 * y1 + x1 * y0 + 2 * x1 * y1) / 24.0
        int_r2 += cross * (y0 * y0 + y0 * y1 + y1 * y1) / 12.0
    if int_r <= 1e-12:
        raise invalid_request("degenerate revolve profile", operation_id)
    volume = 2.0 * math.pi * int_r * fraction
    a_bar = int_ar / int_r
    theta = math.radians(angle_deg)
    centroid = vadd(axis.origin, vscale(d, a_bar))
    if angle_deg < 360.0:
        rho = (int_r2 / int_r) * (math.sin(theta / 2.0) / (theta / 2.0))
        bisector = vadd(vscale(e, math.cos(theta / 2.0)), vscale(vcross(d, e), math.sin(theta / 2.0)))
        centroid = vadd(centroid, vscale(bisector, rho))

    prim = Primitive(
        id=b.new_primitive_id(),
        kind=PrimitiveKind.revolution,
        sign=sign,
        volume_mm3=volume,
        centroid=centroid,
        bbox=revolution_bbox(axis, tuple(ar)),
        feature=b.feature.id,
        axis=axis,
        profile_ar=tuple(ar),
        angle_deg=angle_deg,
        frame=frame,
    )
    b.add_primitive(prim)

    faces: list[FakeFace | None] = []
    for i in range(k):
        a0, r0 = ar[i]
        a1, r1 = ar[(i + 1) % k]
        da, dr = a1 - a0, r1 - r0
        if abs(dr) <= TOLERANCE and abs(da) > TOLERANCE:
            if r0 <= TOLERANCE:
                faces.append(None)  # segment on the axis: no surface
                continue
            a_lo, a_hi = min(a0, a1), max(a0, a1)
            origin = vadd(axis.origin, vscale(d, a_lo))
            faces.append(
                b.add_face(
                    SurfaceType.cylinder,
                    vadd(axis.origin, vscale(d, (a0 + a1) / 2.0)),
                    2.0 * math.pi * r0 * (a_hi - a_lo) * fraction,
                    cylinder_bbox(origin, d, r0, a_hi - a_lo),
                    axis=Axis(origin=origin, direction=d),
                    radius=r0,
                    length=a_hi - a_lo,
                )
            )
        elif abs(da) <= TOLERANCE and abs(dr) > TOLERANCE:
            normal = vscale(d, 1.0 if dr > 0 else -1.0)
            center = vadd(axis.origin, vscale(d, a0))
            r_out = max(r0, r1)
            faces.append(
                b.add_face(
                    SurfaceType.plane,
                    center,
                    math.pi * abs(r1 * r1 - r0 * r0) * fraction,
                    disc_bbox(center, d, r_out),
                    normal=normal,
                    outer_radius=r_out,
                )
            )
        elif abs(da) <= TOLERANCE and abs(dr) <= TOLERANCE:
            raise invalid_request("degenerate revolve segment", operation_id)
        else:
            raise not_supported("slanted revolve segments (cones) are not supported", operation_id)

    for i in range(k):
        h = (i - 1) % k
        a_i, r_i = ar[i]
        if r_i <= TOLERANCE or faces[h] is None or faces[i] is None:
            continue
        prev_seg = (ar[i][0] - ar[h][0], ar[i][1] - ar[h][1])
        next_seg = (ar[(i + 1) % k][0] - a_i, ar[(i + 1) % k][1] - r_i)
        turn = prev_seg[0] * next_seg[1] - prev_seg[1] * next_seg[0]
        b.add_circle_edge(
            faces[h].id,
            faces[i].id,
            vadd(axis.origin, vscale(d, a_i)),
            d,
            r_i,
            convex=turn > 0,
            dihedral_deg=90.0,
            fraction=fraction,
        )

    if angle_deg < 360.0:
        start_normal = vscale(vcross(d, e), -1.0)
        end_rotation = rotation_about(axis, angle_deg)
        for normal, transform in ((start_normal, Transform.identity()), (vscale(start_normal, -1.0), end_rotation)):
            polygon = [transform.apply(p) for p in world]
            _, cap_centroid = polygon_area_and_centroid(tuple(polygon), n)
            b.add_face(
                SurfaceType.plane,
                cap_centroid,
                area,
                bbox_of_points(tuple(polygon)),
                normal=transform.apply_direction(normal),
                polygon=polygon,
            )
    return prim


# --- Chamfer and fillet --------------------------------------------------------------------------


def _edge_frame(
    b: Build, edge: FakeEdge, operation_id: str | None
) -> tuple[FakeFace, FakeFace, Vec3, Vec3, Vec3, Vec3]:
    """Faces a, b and the in-face directions pointing away from the edge (into the material for convex edges)."""
    if edge.curve is not CurveType.line or edge.start is None or edge.end is None:
        raise not_supported(f"edge {edge.id} is not a line", operation_id)
    face_a = b.doc.faces[edge.faces[0]]
    face_b = b.doc.faces[edge.faces[1]]
    if face_a.normal is None or face_b.normal is None:
        raise not_supported(f"edge {edge.id} is not between two planar faces", operation_id)
    if edge.dihedral_deg is None or abs(edge.dihedral_deg - 90.0) > 1e-6:
        raise not_supported(f"edge {edge.id} is not a 90 degree edge", operation_id)
    conv = 1.0 if edge.convex else -1.0
    t_a = vscale(face_b.normal, -conv)
    t_b = vscale(face_a.normal, -conv)
    return face_a, face_b, t_a, t_b, face_a.normal, face_b.normal


def build_chamfer(
    b: Build, edge: FakeEdge, d1: float, d2: float, distance1_face: str, *, operation_id: str | None = None
) -> Primitive:
    face_a, face_b, t_a, t_b, n_a, n_b = _edge_frame(b, edge, operation_id)
    if distance1_face == face_b.id:
        face_a, face_b, t_a, t_b, n_a, n_b = face_b, face_a, t_b, t_a, n_b, n_a
    elif distance1_face != face_a.id:
        raise invalid_request(f"distance1_face {distance1_face} is not adjacent to edge {edge.id}", operation_id)
    start, end = edge.start, edge.end
    assert start is not None and end is not None
    length = edge.length_mm
    mid = vscale(vadd(start, end), 0.5)
    qa0, qa1 = vadd(start, vscale(t_a, d1)), vadd(end, vscale(t_a, d1))
    qb0, qb1 = vadd(start, vscale(t_b, d2)), vadd(end, vscale(t_b, d2))
    sign = -1 if edge.convex else 1
    prim = Primitive(
        id=b.new_primitive_id(),
        kind=PrimitiveKind.chamfer_wedge,
        sign=sign,
        volume_mm3=d1 * d2 * length / 2.0,
        centroid=vadd(mid, vadd(vscale(t_a, d1 / 3.0), vscale(t_b, d2 / 3.0))),
        bbox=bbox_of_points((start, end, qa0, qa1, qb0, qb1)),
        feature=b.feature.id,
        points=(start, end, qa0, qa1, qb0, qb1),
    )
    b.add_primitive(prim)
    normal = vunit(vadd(vscale(n_a, d1), vscale(n_b, d2)))
    polygon = [qa0, qa1, qb1, qb0]
    face = b.add_face(
        SurfaceType.plane,
        vadd(mid, vadd(vscale(t_a, d1 / 2.0), vscale(t_b, d2 / 2.0))),
        length * math.hypot(d1, d2),
        bbox_of_points(tuple(polygon)),
        normal=normal,
        polygon=polygon,
    )
    b.remove_edge(edge.id)
    ea = b.add_line_edge(face_a.id, face.id, qa0, qa1, convex=edge.convex, dihedral_deg=dihedral_degrees(n_a, normal))
    eb = b.add_line_edge(face_b.id, face.id, qb0, qb1, convex=edge.convex, dihedral_deg=dihedral_degrees(n_b, normal))
    for point, e in ((qa0, ea), (qa1, ea), (qb0, eb), (qb1, eb)):
        b.add_vertex(point, [e.id])
    b.modify_face_area(face_a.id, -d1 * length)
    b.modify_face_area(face_b.id, -d2 * length)
    return prim


FILLET_CENTROID_FACTOR = 1.0 / (6.0 * (1.0 - math.pi / 4.0))


def build_fillet(b: Build, edge: FakeEdge, radius: float, *, operation_id: str | None = None) -> Primitive:
    face_a, face_b, t_a, t_b, n_a, n_b = _edge_frame(b, edge, operation_id)
    start, end = edge.start, edge.end
    assert start is not None and end is not None
    length = edge.length_mm
    direction = vunit(vsub(end, start))
    conv = 1.0 if edge.convex else -1.0
    c0 = vadd(start, vadd(vscale(t_a, radius), vscale(t_b, radius)))
    c1 = vadd(end, vadd(vscale(t_a, radius), vscale(t_b, radius)))
    c_mid = vscale(vadd(c0, c1), 0.5)
    bisector = vadd(n_a, n_b)  # from the axis toward the original edge, times conv
    sign = -1 if edge.convex else 1
    support = (c0, c1, vadd(c0, vscale(t_a, -radius)), vadd(c1, vscale(t_a, -radius)))
    support += (vadd(c0, vscale(t_b, -radius)), vadd(c1, vscale(t_b, -radius)), start, end)
    prim = Primitive(
        id=b.new_primitive_id(),
        kind=PrimitiveKind.fillet_cut,
        sign=sign,
        volume_mm3=(1.0 - math.pi / 4.0) * radius * radius * length,
        centroid=vadd(c_mid, vscale(bisector, conv * FILLET_CENTROID_FACTOR * radius)),
        bbox=bbox_of_points(support),
        feature=b.feature.id,
        points=support,
    )
    b.add_primitive(prim)
    axis = Axis(origin=c0, direction=direction)
    face = b.add_face(
        SurfaceType.cylinder,
        vadd(c_mid, vscale(bisector, conv * 2.0 * radius / math.pi)),
        (math.pi / 2.0) * radius * length,
        bbox_of_points(support),
        axis=axis,
        radius=radius,
        length=length,
    )
    b.remove_edge(edge.id)
    pa0, pa1 = vadd(start, vscale(t_a, radius)), vadd(end, vscale(t_a, radius))
    pb0, pb1 = vadd(start, vscale(t_b, radius)), vadd(end, vscale(t_b, radius))
    ea = b.add_line_edge(face_a.id, face.id, pa0, pa1, convex=False, dihedral_deg=0.0)
    eb = b.add_line_edge(face_b.id, face.id, pb0, pb1, convex=False, dihedral_deg=0.0)
    for point, e in ((pa0, ea), (pa1, ea), (pb0, eb), (pb1, eb)):
        b.add_vertex(point, [e.id])
    b.modify_face_area(face_a.id, -radius * length)
    b.modify_face_area(face_b.id, -radius * length)
    return prim


# --- Patterns ---------------------------------------------------------------------------------------


def clone_feature(b: Build, seed: FakeFeature, transform: Transform) -> None:
    """Clone the seed feature's primitives, faces, edges and vertices moved by ``transform``."""
    doc = b.doc
    mapping: dict[str, str] = {}
    for prim_id in seed.primitives:
        prim = doc.primitives[prim_id]
        new_id = b.new_primitive_id()
        mapping[prim_id] = new_id
        b.add_primitive(prim.transformed(transform, new_id=new_id, feature=b.feature.id))
    for face_id in seed.created_faces:
        face = doc.faces[face_id]
        new_face = face.transformed(transform, new_id=doc.next_id("face"))
        new_face.created_by = b.feature.id
        new_face.modified_by = []
        new_face.edges = []
        mapping[face_id] = new_face.id
        doc.faces[new_face.id] = new_face
        b.feature.created_faces.append(new_face.id)
    for edge_id in seed.created_edges:
        edge = doc.edges.get(edge_id)
        if edge is None:
            continue
        new_edge = edge.transformed(transform, new_id=doc.next_id("edge"))
        new_edge.faces = (mapping.get(edge.faces[0], edge.faces[0]), mapping.get(edge.faces[1], edge.faces[1]))
        mapping[edge_id] = new_edge.id
        b._register_edge(new_edge)
    for vertex_id in seed.created_vertices:
        vertex = doc.vertices.get(vertex_id)
        if vertex is None:
            continue
        new_vertex = vertex.transformed(transform, new_id=doc.next_id("vertex"))
        new_vertex.edges = [mapping[e] for e in vertex.edges if e in mapping]
        doc.vertices[new_vertex.id] = new_vertex
        b.feature.created_vertices.append(new_vertex.id)
    for face_id, delta in seed.face_area_deltas.items():
        if face_id in doc.faces:
            b.modify_face_area(face_id, delta)
    for face_id in seed.modified_faces:
        if face_id in doc.faces:
            b.mark_modified(face_id)


def clone_body(
    b: Build,
    source_body: str,
    transform: Transform,
    *,
    scale: float = 1.0,
    scale_about: Vec3 = (0.0, 0.0, 0.0),
) -> None:
    """Duplicate a whole body into ``b.body`` scaled uniformly by ``scale`` about ``scale_about`` and then moved by
    ``transform``: every primitive (negative ones too), face, edge and vertex of ``source_body`` gets a fresh id, the
    faces are attributed to the current feature and the edges point at the cloned faces. The source is not touched."""
    doc = b.doc
    scaled = scale != 1.0

    def grow(entity: Any) -> Any:
        return entity.scaled(scale, scale_about) if scaled else entity

    for prim_id in list(doc.bodies[source_body]):
        b.add_primitive(
            grow(doc.primitives[prim_id]).transformed(transform, new_id=b.new_primitive_id(), feature=b.feature.id)
        )
    mapping: dict[str, str] = {}
    for face in [f for f in doc.faces.values() if f.body == source_body]:
        clone = grow(face).transformed(transform, new_id=doc.next_id("face"))
        clone.body = b.body
        clone.created_by = b.feature.id
        clone.modified_by = []
        clone.edges = []
        mapping[face.id] = clone.id
        doc.faces[clone.id] = clone
        b.feature.created_faces.append(clone.id)
    for edge in [e for e in doc.edges.values() if e.faces[0] in mapping]:
        clone_edge = grow(edge).transformed(transform, new_id=doc.next_id("edge"))
        clone_edge.faces = (mapping.get(edge.faces[0], edge.faces[0]), mapping.get(edge.faces[1], edge.faces[1]))
        mapping[edge.id] = clone_edge.id
        b._register_edge(clone_edge)  # noqa: SLF001 - same module family as clone_feature
    for vertex in [v for v in doc.vertices.values() if any(e in mapping for e in v.edges)]:
        clone_vertex = grow(vertex).transformed(transform, new_id=doc.next_id("vertex"))
        clone_vertex.edges = [mapping[e] for e in vertex.edges if e in mapping]
        doc.vertices[clone_vertex.id] = clone_vertex
        b.feature.created_vertices.append(clone_vertex.id)


# --- Split ------------------------------------------------------------------------------------------------------

_SPLIT_MARGIN_MM = 1.0


def build_split(
    doc: FakeDocument,
    feature: FakeFeature,
    source: str,
    plane: SketchFrame,
    keep: str,
    new_body: Callable[[], str],
    operation_id: str | None = None,
) -> None:
    """Cut ``source`` along ``plane`` into a positive piece (the side the normal points to) and a negative one, create
    the kept ones (positive first) as new bodies and consume the source.

    Exact only where the clipped primitives are: a body that is ONE positive axis-aligned box, or ONE cylinder along a
    world axis, cut by a plane whose normal is a world axis (a cylinder only across its axis). Everything else is a
    ``not_supported`` that says so; a plane that misses the body is a geometry error."""
    prims = doc.body_primitives(source)
    if (
        len(prims) != 1
        or prims[0].sign < 0
        or (as_box(prims[0]) is None and prims[0].kind is not PrimitiveKind.cylinder)
    ):
        msg = (
            "the fake adapter splits only a body that is one axis-aligned box or one cylinder (no features, holes or "
            "earlier cuts): run it on FreeCAD"
        )
        raise not_supported(msg, operation_id)
    prim = prims[0]
    normal = vunit(plane.n)
    axis = principal_axis(normal)
    if axis is None:
        msg = (
            "the fake adapter splits only along an axis-aligned plane (its normal along X, Y or Z): an oblique cut "
            "would need non-axis-aligned boxes; run it on FreeCAD"
        )
        raise not_supported(msg, operation_id)
    towards = 1.0 if normal[axis] > 0 else -1.0
    low, high = prim.bbox.min[axis], prim.bbox.max[axis]
    cut = plane.origin[axis]
    if cut <= low + TOLERANCE or cut >= high - TOLERANCE:
        info = ErrorInfo(
            code=ErrorCode.geometry_failed,
            message=f"plane does not cut the body: it sits at {cut:g} mm and the body spans {low:g} to {high:g} mm",
            operation_id=operation_id,
            recoverable=False,
        )
        raise AdapterError(info)
    wanted = ("positive", "negative") if keep == "both" else (keep,)
    pieces: list[Primitive] = []
    for side in wanted:
        above = (side == "positive") == (towards > 0)  # the piece lies at larger coordinates than the cut
        span = (cut, high + _SPLIT_MARGIN_MM) if above else (low - _SPLIT_MARGIN_MM, cut)
        box_min = [prim.bbox.min[i] - _SPLIT_MARGIN_MM for i in range(3)]
        box_max = [prim.bbox.max[i] + _SPLIT_MARGIN_MM for i in range(3)]
        box_min[axis], box_max[axis] = span
        half_space = box_primitive(BoundingBox(min=tuple(box_min), max=tuple(box_max)), "")  # type: ignore[arg-type]
        piece = intersection_primitive(prim, half_space)
        if piece is None:
            msg = "the fake adapter cannot cut this cylinder along its length; run it on FreeCAD"
            raise not_supported(msg, operation_id)
        pieces.append(piece)

    # consume the source: everything it owns is snapshotted for undo, then removed
    b = Build(doc, feature, source)
    b.touch(source)
    face_ids = {f.id for f in doc.faces.values() if f.body == source}
    edges = [e for e in doc.edges.values() if e.faces[0] in face_ids or e.faces[1] in face_ids]
    edge_ids = {e.id for e in edges}
    vertices = [v for v in doc.vertices.values() if any(e in edge_ids for e in v.edges)]
    for prim_id in doc.bodies[source]:
        b.touch(prim_id)
    for entity_id in [*(v.id for v in vertices), *edge_ids, *face_ids]:
        b.touch(entity_id)
    for vertex in vertices:
        doc.vertices.pop(vertex.id)
    for edge_id in edge_ids:
        doc.edges.pop(edge_id)
    for face_id in face_ids:
        doc.faces.pop(face_id)
    for prim_id in doc.bodies.pop(source):
        doc.primitives.pop(prim_id)
    feature.modified_bodies.append(source)

    for piece in pieces:
        body = new_body()
        made = Build(doc, feature, body)
        if piece.polygon is not None:
            assert piece.direction is not None and piece.depth is not None
            build_prism(made, list(piece.polygon), piece.direction, piece.depth, 1)
        else:
            assert piece.axis is not None and piece.radius is not None and piece.height is not None
            build_cylinder(made, piece.axis.origin, piece.axis.direction, piece.radius, piece.height, 1)


# --- Sweep ----------------------------------------------------------------------------------------------------

_SWEEP_TOLERANCE = 1e-6
_SWEEP_PLACEMENT_TOLERANCE = 1e-2
_ARC_STEP_DEG = 5.0


def _perpendicular_pair(direction: Vec3) -> tuple[Vec3, Vec3]:
    seed: Vec3 = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)
    e1 = vunit(vsub(seed, vscale(direction, vdot(seed, direction))))
    return e1, vcross(direction, e1)


def _ellipse_points(centre: Vec3, a: Vec3, b: Vec3, samples: int = 16) -> list[Vec3]:
    """Boundary points of ``centre + cos(t) a + sin(t) b``: the two extremes along each world axis (so the box of the
    points is the exact box of the ellipse) plus uniform samples (so a later rotation stays close)."""
    points: list[Vec3] = []
    for k in range(3):
        t = math.atan2(b[k], a[k])
        offset = vadd(vscale(a, math.cos(t)), vscale(b, math.sin(t)))
        points.extend((vadd(centre, offset), vsub(centre, offset)))
    for i in range(samples):
        t = 2.0 * math.pi * i / samples
        points.append(vadd(centre, vadd(vscale(a, math.cos(t)), vscale(b, math.sin(t)))))
    return points


@dataclass
class _SweepPath:
    points: list[Vec3]
    dirs: list[Vec3]
    lens: list[float]
    phis: list[float]
    tangents: list[float]
    radius: float | None

    @property
    def length(self) -> float:
        total = sum(self.lens)
        if self.radius is not None:
            total += sum(self.radius * phi - 2.0 * t for phi, t in zip(self.phis, self.tangents, strict=True))
        return total

    def straight(self, i: int) -> tuple[Vec3, Vec3, float]:
        """Start, end and length of the straight part of segment ``i`` (fillets cut the tangent lengths off)."""
        t_in = self.tangents[i - 1] if i > 0 else 0.0
        t_out = self.tangents[i] if i < len(self.tangents) else 0.0
        start = vadd(self.points[i], vscale(self.dirs[i], t_in))
        end = vsub(self.points[i + 1], vscale(self.dirs[i], t_out))
        return start, end, self.lens[i] - t_in - t_out

    def arc(self, corner: int) -> tuple[Vec3, Vec3, Vec3, float]:
        """Centre, first radius vector, second radius vector and bend-plane normal of the fillet at a corner."""
        assert self.radius is not None
        d_in, d_out = self.dirs[corner], self.dirs[corner + 1]
        vertex = self.points[corner + 1]
        phi = self.phis[corner]
        t = self.tangents[corner]
        towards_centre = vunit(vsub(d_out, d_in))
        centre = vadd(vertex, vscale(towards_centre, self.radius / math.cos(phi / 2.0)))
        first = vsub(vsub(vertex, vscale(d_in, t)), centre)
        second = vsub(vadd(vertex, vscale(d_out, t)), centre)
        return centre, first, second, vunit(vcross(d_in, d_out))


def _sweep_path(points: list[Vec3], radius: float | None, operation_id: str | None) -> _SweepPath:
    if len(points) < 2:
        raise invalid_request("a sweep path needs at least 2 points", operation_id)
    dirs: list[Vec3] = []
    lens: list[float] = []
    for i in range(len(points) - 1):
        delta = vsub(points[i + 1], points[i])
        length = vlength(delta)
        if length <= _SWEEP_TOLERANCE:
            raise invalid_request(f"sweep path points {i} and {i + 1} coincide", operation_id)
        dirs.append(vscale(delta, 1.0 / length))
        lens.append(length)
    phis: list[float] = []
    for i in range(1, len(points) - 1):
        phi = math.atan2(vlength(vcross(dirs[i - 1], dirs[i])), vdot(dirs[i - 1], dirs[i]))
        if phi <= _SWEEP_TOLERANCE:
            raise invalid_request(f"sweep path point {i} is collinear with its neighbours: drop it", operation_id)
        if math.pi - phi <= _SWEEP_TOLERANCE:
            raise invalid_request(f"sweep path reverses on itself at point {i}", operation_id)
        phis.append(phi)
    tangents = [0.0] * len(phis) if radius is None else [radius * math.tan(phi / 2.0) for phi in phis]
    path = _SweepPath(points, dirs, lens, phis, tangents, radius)
    if radius is not None:
        for i in range(len(lens)):
            if path.straight(i)[2] <= _SWEEP_TOLERANCE:
                raise geometry_failed(
                    f"sweep corner_radius_mm {radius:g} does not fit path segment {i} ({lens[i]:g} mm long)",
                    operation_id,
                )
    return path


def _sweep_centroid(path: _SweepPath) -> Vec3:
    """Length-weighted centre of the swept centre line. Exact for a straight sweep; an approximation for a bent
    one (the true centroid of the solid shifts slightly towards the inside of each bend)."""
    pieces: list[tuple[float, Vec3]] = []
    for i in range(len(path.lens)):
        start, end, length = path.straight(i)
        pieces.append((length, vscale(vadd(start, end), 0.5)))
    if path.radius is not None:
        for corner, phi in enumerate(path.phis):
            centre, first, second, _ = path.arc(corner)
            middle = vunit(vadd(first, second))
            reach = path.radius * math.sin(phi / 2.0) / (phi / 2.0)
            pieces.append((path.radius * phi, vadd(centre, vscale(middle, reach))))
    total = sum(length for length, _ in pieces)
    weighted: Vec3 = (0.0, 0.0, 0.0)
    for length, point in pieces:
        weighted = vadd(weighted, vscale(point, length))
    return vscale(weighted, 1.0 / total)


def _mitre_joint_offsets(path: _SweepPath, offsets: list[Vec3]) -> list[list[Vec3]]:
    """World vertices of every end face of a mitred prismatic sweep (start cap, one face per corner, end cap).

    The section vertices ``o`` (perpendicular to the incoming direction) land on the bisector plane at
    ``P + o - (o.m / d.m) d_in``; the next segment continues from their mirror image across that plane."""
    faces = [[vadd(path.points[0], o) for o in offsets]]
    current = offsets
    for j in range(1, len(path.points) - 1):
        d_in = path.dirs[j - 1]
        m = vunit(vadd(d_in, path.dirs[j]))
        dm = vdot(d_in, m)
        faces.append([vadd(path.points[j], vsub(o, vscale(d_in, vdot(o, m) / dm))) for o in current])
        current = [vsub(o, vscale(m, 2.0 * vdot(o, m))) for o in current]
    faces.append([vadd(path.points[-1], o) for o in current])
    return faces


def _rotate_about(vector: Vec3, axis: Vec3, angle: float) -> Vec3:
    """Rodrigues rotation of ``vector`` about the unit ``axis``."""
    c, s = math.cos(angle), math.sin(angle)
    return vadd(vadd(vscale(vector, c), vscale(vcross(axis, vector), s)), vscale(axis, vdot(axis, vector) * (1.0 - c)))


def build_sweep(
    b: Build,
    frame: SketchFrame,
    profile: Profile,
    points: Any,
    corner_radius: float | None,
    *,
    operation_id: str | None = None,
) -> Primitive:
    """Sweep ONE closed profile along a polyline (mitred corners, or circular fillets for a circular section).

    Volume is exactly ``area x path length``. Faces: two caps, a cylinder (circle) or one planar quad per profile
    side (polygon) per segment, and a torus per filleted corner. Edges: only the two cap boundaries. The bounding
    box is exact for mitred paths and for straight and filleted circles (sampled discs along the arcs)."""
    path = _sweep_path([(float(p[0]), float(p[1]), float(p[2])) for p in points], corner_radius, operation_id)
    d0 = path.dirs[0]
    if abs(vdot(frame.n, d0)) < math.cos(math.radians(0.5)):
        raise invalid_request("the sweep profile plane must be normal to the first path segment", operation_id)
    centroid_world = frame.to_world(*profile.centroid)
    if vlength(vsub(centroid_world, path.points[0])) > _SWEEP_PLACEMENT_TOLERANCE:
        raise invalid_request(
            f"the sweep profile centroid {centroid_world} is not at the path start {path.points[0]}", operation_id
        )
    circle = profile.kind == "circle" and profile.radius is not None
    if corner_radius is not None and not circle:
        raise not_supported("the fake models filleted sweeps of circular sections only", operation_id)

    if circle:
        assert profile.radius is not None
        prim_points = _build_circle_sweep_geometry(b, path, profile.radius)
    else:
        prim_points = _build_polygon_sweep_geometry(b, path, frame, profile, centroid_world)
    return _add_sweep_primitive(b, frame, path, profile.area_mm2, prim_points)


def _add_sweep_primitive(
    b: Build, frame: SketchFrame, path: _SweepPath, area: float, prim_points: list[Vec3]
) -> Primitive:
    prim = Primitive(
        id=b.new_primitive_id(),
        kind=PrimitiveKind.sweep,
        sign=1,
        volume_mm3=area * path.length,
        centroid=_sweep_centroid(path),
        bbox=bbox_of_points(prim_points),
        feature=b.feature.id,
        points=tuple(prim_points),
        frame=frame,
    )
    b.add_primitive(prim)
    return prim


Station = tuple[Vec3, Vec3, Vec3]


def _circle_stations(path: _SweepPath, radius: float) -> list[tuple[str, int, list[Station]]]:
    """The ellipses (centre, a, b) that bound every body piece of a circular sweep, in path order, as
    ``(kind, index, stations)``: a ``straight`` piece per segment (its two end ellipses) and, for a filleted
    path, an ``arc`` piece per corner (discs along the arc)."""
    pieces: list[tuple[str, int, list[Station]]] = []
    n = len(path.points)
    if path.radius is None:
        stations: list[Station] = []
        e1, e2 = _perpendicular_pair(path.dirs[0])
        stations.append((path.points[0], vscale(e1, radius), vscale(e2, radius)))
        for j in range(1, n - 1):
            d_in = path.dirs[j - 1]
            m = vunit(vadd(d_in, path.dirs[j]))
            e1 = vunit(vsub(m, vscale(d_in, vdot(m, d_in))))
            tan_half = math.tan(path.phis[j - 1] / 2.0)
            a = vscale(vsub(e1, vscale(d_in, tan_half)), radius)
            stations.append((path.points[j], a, vscale(vcross(d_in, e1), radius)))
        e1, e2 = _perpendicular_pair(path.dirs[-1])
        stations.append((path.points[-1], vscale(e1, radius), vscale(e2, radius)))
        return [("straight", i, [stations[i], stations[i + 1]]) for i in range(len(path.lens))]
    for i in range(len(path.lens)):
        start, end, _ = path.straight(i)
        e1, e2 = _perpendicular_pair(path.dirs[i])
        disc = (vscale(e1, radius), vscale(e2, radius))
        pieces.append(("straight", i, [(start, *disc), (end, *disc)]))
        if i < len(path.phis):
            pieces.append(("arc", i, _arc_stations(path, i, radius)))
    return pieces


def _arc_stations(path: _SweepPath, corner: int, radius: float) -> list[Station]:
    centre, first, second, normal = path.arc(corner)
    phi = path.phis[corner]
    steps = max(2, math.ceil(math.degrees(phi) / _ARC_STEP_DEG))
    d_in = path.dirs[corner]
    out: list[Station] = []
    for k in range(steps + 1):
        s = k / steps
        w1 = math.sin((1.0 - s) * phi) / math.sin(phi)
        w2 = math.sin(s * phi) / math.sin(phi)
        point = vadd(centre, vadd(vscale(first, w1), vscale(second, w2)))
        tangent = _rotate_about(d_in, normal, s * phi)
        e1, e2 = _perpendicular_pair(tangent)
        out.append((point, vscale(e1, radius), vscale(e2, radius)))
    return out


def _station_points(stations: list[Station]) -> list[Vec3]:
    return [p for centre, a, b in stations for p in _ellipse_points(centre, a, b)]


def _build_circle_sweep_geometry(b: Build, path: _SweepPath, radius: float) -> list[Vec3]:
    all_points: list[Vec3] = []
    disc = math.pi * radius * radius
    d_first, d_last = path.dirs[0], path.dirs[-1]
    start_cap = b.add_face(
        SurfaceType.plane,
        path.points[0],
        disc,
        disc_bbox(path.points[0], d_first, radius),
        normal=vscale(d_first, -1.0),
        outer_radius=radius,
    )
    laterals: list[FakeFace] = []
    for kind, index, stations in _circle_stations(path, radius):
        points = _station_points(stations)
        all_points.extend(points)
        box = bbox_of_points(points)
        if kind == "arc":
            assert path.radius is not None
            centre, _, _, normal = path.arc(index)
            laterals.append(
                b.add_face(
                    SurfaceType.torus,
                    stations[len(stations) // 2][0],
                    2.0 * math.pi * radius * path.radius * path.phis[index],
                    box,
                    axis=Axis(origin=centre, direction=normal),
                    radius=path.radius,  # a torus reports its major (bend) radius, like the FreeCAD worker
                )
            )
            continue
        start, end, length = path.straight(index)
        laterals.append(
            b.add_face(
                SurfaceType.cylinder,
                vscale(vadd(start, end), 0.5),
                2.0 * math.pi * radius * length,
                box,
                axis=Axis(origin=start, direction=path.dirs[index]),
                radius=radius,
                length=length,
            )
        )
    end_cap = b.add_face(
        SurfaceType.plane,
        path.points[-1],
        disc,
        disc_bbox(path.points[-1], d_last, radius),
        normal=d_last,
        outer_radius=radius,
    )
    b.add_circle_edge(laterals[0].id, start_cap.id, path.points[0], d_first, radius, convex=True, dihedral_deg=90.0)
    b.add_circle_edge(laterals[-1].id, end_cap.id, path.points[-1], d_last, radius, convex=True, dihedral_deg=90.0)
    return all_points


def _build_polygon_sweep_geometry(
    b: Build, path: _SweepPath, frame: SketchFrame, profile: Profile, centroid_world: Vec3
) -> list[Vec3]:
    if profile.kind == "polygon" and profile.points:
        corners = list(profile.points)
    else:
        (u0, v0), (u1, v1) = profile.bbox_uv
        corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    d0 = path.dirs[0]
    offsets: list[Vec3] = []
    for u, v in corners:
        rel = vsub(frame.to_world(u, v), centroid_world)
        offsets.append(vsub(rel, vscale(d0, vdot(rel, d0))))
    faces = _mitre_joint_offsets(path, offsets)
    sides = len(offsets)
    start_cap = b.add_face(
        SurfaceType.plane,
        centroid_world,
        profile.area_mm2,
        bbox_of_points(faces[0]),
        normal=vscale(d0, -1.0),
        polygon=list(faces[0]),
    )
    laterals: list[list[FakeFace]] = []
    for i in range(len(path.lens)):
        row: list[FakeFace] = []
        for k in range(sides):
            quad = [faces[i][k], faces[i][(k + 1) % sides], faces[i + 1][(k + 1) % sides], faces[i + 1][k]]
            normal = vunit(vcross(vsub(quad[1], quad[0]), path.dirs[i]))
            middle = vscale(vadd(quad[0], quad[1]), 0.5)
            if vdot(normal, vsub(middle, path.points[i])) < 0.0:
                normal = vscale(normal, -1.0)
            area, centre = polygon_area_and_centroid(tuple(quad), normal)
            row.append(b.add_face(SurfaceType.plane, centre, area, bbox_of_points(quad), normal=normal, polygon=quad))
        laterals.append(row)
    end_cap = b.add_face(
        SurfaceType.plane,
        path.points[-1],
        profile.area_mm2,
        bbox_of_points(faces[-1]),
        normal=path.dirs[-1],
        polygon=list(faces[-1]),
    )
    for k in range(sides):
        b.add_line_edge(
            start_cap.id, laterals[0][k].id, faces[0][k], faces[0][(k + 1) % sides], convex=True, dihedral_deg=90.0
        )
        b.add_line_edge(
            end_cap.id, laterals[-1][k].id, faces[-1][k], faces[-1][(k + 1) % sides], convex=True, dihedral_deg=90.0
        )
    return [p for face in faces for p in face]


def linear_instances(
    direction: Vec3, spacing: float, count: int, direction2: Vec3 | None, spacing2: float | None, count2: int | None
) -> list[Transform]:
    d1 = vunit(direction)
    d2 = vunit(direction2) if direction2 is not None else None
    transforms: list[Transform] = []
    for j in range(count2 or 1):
        for i in range(count):
            if i == 0 and j == 0:
                continue
            offset = vscale(d1, i * spacing)
            if d2 is not None and spacing2 is not None:
                offset = vadd(offset, vscale(d2, j * spacing2))
            transforms.append(Transform(origin=offset))
    return transforms


def circular_instances(axis: Axis, count: int, angle_deg: float, equal_spacing: bool) -> list[Transform]:
    if equal_spacing:
        step = angle_deg / count if abs(angle_deg - 360.0) < 1e-9 else angle_deg / (count - 1)
    else:
        step = angle_deg
    return [rotation_about(axis, k * step) for k in range(1, count)]
