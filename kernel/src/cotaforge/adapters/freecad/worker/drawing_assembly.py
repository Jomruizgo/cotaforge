# ruff: noqa: E501
"""What an assembly drawing needs of the live document: the parts list data and the questions the balloons and the exploded
view ask of the components (their boxes, what a view shows of each, the whole assembly projected with some of them
displaced).

Nothing here describes a face: a component is its bodies (``comp.body_solids``, a few hundred for a vendor assembly),
and what a view shows of it is found by casting rays, each against the few bodies whose box it crosses (a section of one
body by a line), so a 900-body, 30 000-face part costs a few rays and not a pass over its faces. The displacement of an
exploded view is a ``Placement`` put on a handle to the same geometry: the document is not touched.
"""

import json
import math
import os
from typing import Any

import assembly_names
import FreeCAD as App
import geometry as g
import Part
import registry as reg
import sheet_context as sc
import sheet_project

META_MATERIAL = "cotaforge_design.material"
META_SOURCE = "cotaforge_import.source"  # written by import_geometry: the file a part came from
META_TITLE = "cotaforge_design.title"  # written by ``doc set_title``: the designation the designer gave the part
MATERIAL_PROPERTY = "CotaforgeMaterial"
SPEC_PROP = "CotaforgeSpec"
BOUGHT = {"es": "comprado", "en": "purchased"}
GRID = 7  # samples per side of a footprint, tried from its middle outwards
MAX_BODIES_SAMPLED = 16  # of a component made of many bodies, the ones nearest the viewer that rays are cast at
OUTWARD_GRID = 5  # a balloon wants its dot toward the outside of the view: every sample of a single body is tried
DEPTH_TOL_MM = 1e-3  # two bodies this close along the ray are one surface: no owner
V = App.Vector


def _part_document(doc: reg.DocumentRec, comp: reg.ComponentRec) -> Any | None:
    """The FreeCAD document of the part a component links to (through its first link), or ``None`` when it is gone."""
    for name in comp.fc_links:
        link = doc.fc_doc.getObject(name)
        target = getattr(link, "LinkedObject", None) if link is not None else None
        if target is not None:
            return target.Document
    return None


def _sheet_field(fc_part: Any, name: str) -> str:
    """A title-block field of the part's own drawing sheet (the first sheet that has it), or ``""``."""
    for obj in fc_part.Objects:
        if str(obj.TypeId) == "TechDraw::DrawPage" and SPEC_PROP in obj.PropertiesList:
            try:
                value = json.loads(str(getattr(obj, SPEC_PROP))).get("fields", {}).get(name, "")
            except ValueError:
                continue
            if value:
                return str(value)
    return ""


def _material(fc_part: Any) -> str:
    found = str(dict(fc_part.Meta).get(META_MATERIAL, "") or "").strip()
    if found:
        return found
    for obj in fc_part.Objects:
        if MATERIAL_PROPERTY in obj.PropertiesList and str(getattr(obj, MATERIAL_PROPERTY)).strip():
            return str(getattr(obj, MATERIAL_PROPERTY)).strip()
    return ""


def group_key(doc: reg.DocumentRec, comp: reg.ComponentRec) -> str:
    """What makes two components the same part: the file of the part document they link to."""
    fc_part = _part_document(doc, comp)
    path = str(getattr(fc_part, "FileName", "") or "") if fc_part is not None else ""
    return os.path.normcase(os.path.abspath(path)) if path else comp.part_doc


def assembly_info(doc: reg.DocumentRec, language: str) -> sc.Assembly | None:
    """The components of an assembly document grouped by part file (identical instances share a position, numbered in the
    order of their first instance); ``None`` for a document without components."""
    if doc.kind != "assembly" or not doc.components:
        return None
    groups: dict[str, sc.PartGroup] = {}
    for comp_id in sorted(doc.components, key=lambda c: int(c.split(":")[1]) if c.split(":")[1].isdigit() else 0):
        comp = doc.components[comp_id]
        fc_part = _part_document(doc, comp)
        key = group_key(doc, comp)
        if key in groups:
            groups[key].instances.append(comp_id)
            continue
        meta = dict(fc_part.Meta) if fc_part is not None else {}
        source = str(meta.get(META_SOURCE, "") or "")
        bought = bool(source) or bool(comp.foreign)
        number = _sheet_field(fc_part, "drawing_number") if fc_part is not None else ""
        name = assembly_names.designation(
            str(meta.get(META_TITLE, "") or ""), _sheet_field(fc_part, "title") if fc_part is not None else ""
        )
        groups[key] = sc.PartGroup(
            position=len(groups) + 1,
            key=key,
            name=name,
            reference=number or source,
            material=_material(fc_part) if fc_part is not None else "",
            remarks=BOUGHT[language] if bought else "",
            instances=[comp_id],
            bought=bought,
        )
    return sc.Assembly(list(groups.values()))


# --- the bodies of a welded part ---------------------------------------------------------------------------------------

MAX_BODIES_MEASURED = 80
ANGLE_STEP_DEG = 5


def _tight_dims(shape: Any) -> tuple[float, float, float]:
    """The sides of the smallest box around ``shape`` (longest first). The box along the axes of the document is tried
    first, then boxes turned about each principal axis of the body, so a tube lying on a slant is measured along itself."""

    def box(s: Any) -> tuple[float, float, float]:
        try:
            b = s.optimalBoundingBox(False, False)
        except Exception:  # noqa: BLE001 - an older FreeCAD has no optimal box
            b = s.BoundBox
        return b.XLength, b.YLength, b.ZLength

    best = box(shape)
    try:
        props = shape.PrincipalProperties
        axes = [props["FirstAxisOfInertia"], props["SecondAxisOfInertia"], props["ThirdAxisOfInertia"]]
    except Exception:  # noqa: BLE001 - no inertia: the axes of the document are all there is
        axes = []
    for axis in axes:
        turn = App.Rotation(axis, V(0, 0, 1))
        for angle in range(0, 90, ANGLE_STEP_DEG):
            rotation = App.Rotation(V(0, 0, 1), angle).multiply(turn)
            moved = shape.copy()
            moved.Placement = App.Placement(V(0, 0, 0), rotation).multiply(moved.Placement)
            sides = box(moved)
            if sides[0] * sides[1] * sides[2] < best[0] * best[1] * best[2] * 0.995:
                best = sides
    return tuple(sorted(best, reverse=True))  # type: ignore[return-value]


def weldment_info(doc: reg.DocumentRec, language: str) -> sc.Assembly | None:
    """The cut list of a welded part: its live bodies grouped by what they measure (see ``sheet_weldment``); ``None`` when the
    document is an assembly or has fewer than two bodies."""
    import sheet_weldment  # noqa: PLC0415

    if doc.kind == "assembly":
        return None
    material = _material(doc.fc_doc)
    facts = []
    for body in doc.live_bodies()[:MAX_BODIES_MEASURED]:
        shape = reg.world_shape(doc, body)
        if shape.isNull() or not shape.Solids:
            continue
        facts.append(
            sheet_weldment.BodyFact(
                body.id, shape.Volume, shape.Area, len(shape.Faces), _tight_dims(shape), material
            )
        )
    return sheet_weldment.group_bodies(facts, language)


# --- boxes and rays ---------------------------------------------------------------------------------------------------


class _Unit:
    """One body of one instance as the rays see it: its shape (displaced when the view is exploded) and its box."""

    def __init__(self, comp_id: str, shape: Any, low: sc.Vec3, high: sc.Vec3) -> None:
        self.comp_id = comp_id
        self.shape = shape
        self.low = low
        self.high = high


def _frame_rect(
    unit: _Unit, centre: sc.Vec3, xdir: sc.Vec3, ydir: sc.Vec3, direction: sc.Vec3
) -> tuple[float, float, float, float, float]:
    """The box of a unit in the view's coordinates: u0, v0, u1, v1 and the largest depth towards the viewer."""
    us, vs, ts = [], [], []
    for x in (unit.low[0], unit.high[0]):
        for y in (unit.low[1], unit.high[1]):
            for z in (unit.low[2], unit.high[2]):
                d = sc.sub((x, y, z), centre)
                us.append(sc.dot(d, xdir))
                vs.append(sc.dot(d, ydir))
                ts.append(sc.dot(d, direction))
    return min(us), min(vs), max(us), max(vs), max(ts)


def _grid(rect: tuple[float, ...], n: int) -> list[tuple[float, float]]:
    u0, v0, u1, v1 = rect[:4]
    return [(u0 + (u1 - u0) * (i + 0.5) / n, v0 + (v1 - v0) * (j + 0.5) / n) for i in range(n) for j in range(n)]


def _samples(rects: list[tuple[float, ...]]) -> list[tuple[float, float]]:
    """Where to cast rays at one instance, best candidates first. One body: a grid over its footprint, from its middle
    out. Many bodies (a vendor assembly): the middles of the ones nearest the viewer first, then points inside each --
    a grid over the footprint of hundreds of separate bodies would mostly hit the gaps between them."""
    if len(rects) == 1:
        u0, v0, u1, v1 = rects[0][:4]
        return sorted(_grid(rects[0], GRID), key=lambda p: math.dist(p, ((u0 + u1) / 2, (v0 + v1) / 2)))
    front = sorted(rects, key=lambda r: -r[4])[:MAX_BODIES_SAMPLED]
    centres = [((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) for r in front]
    return centres + [p for r in front for p in _grid(r, 3) if p not in centres]


class AssemblyRays:
    """The lookups of an assembly on the live document, mixed into ``LiveGeometry``."""

    doc: reg.DocumentRec
    op: str | None

    def body_boxes(self) -> list[tuple[sc.Vec3, sc.Vec3]]:
        out = []
        for body in self.doc.live_bodies():
            shape = reg.world_shape(self.doc, body)
            if not shape.isNull():
                b = shape.BoundBox
                out.append(((b.XMin, b.YMin, b.ZMin), (b.XMax, b.YMax, b.ZMax)))
        return out

    def _owner_ids(self) -> list[str]:
        """What the balloons point at: the components of an assembly, or the live bodies of a welded part."""
        if self.doc.components:
            return list(self.doc.components)
        return [b.id for b in self.doc.live_bodies()]

    def _body_units(self, comp_id: str) -> list[_Unit]:
        cache: dict[str, list[_Unit]] = self.__dict__.setdefault("_unit_cache", {})
        if comp_id not in cache:
            units = []
            if comp_id in self.doc.components:
                comp = self.doc.components[comp_id]
                shapes = [reg.placed_component_body(self.doc, comp, body_id) for body_id in comp.body_ids]
            else:  # a body of a welded part
                shapes = [reg.world_shape(self.doc, self.doc.bodies[comp_id])]
            for shape in shapes:
                box = shape.BoundBox
                units.append(_Unit(comp_id, shape, (box.XMin, box.YMin, box.ZMin), (box.XMax, box.YMax, box.ZMax)))
            cache[comp_id] = units
        return cache[comp_id]

    def _displaced(self, comp_id: str, offset: sc.Vec3 | None) -> list[_Unit]:
        units = self._body_units(comp_id)
        if offset is None:
            return units
        moved = []
        for unit in units:
            shape = Part.Shape(unit.shape)  # the same geometry under another Location: nothing is copied
            shape.Placement = App.Placement(V(*offset), App.Rotation()).multiply(shape.Placement)
            moved.append(
                _Unit(unit.comp_id, shape, sc.add(unit.low, offset), sc.add(unit.high, offset))  # type: ignore[arg-type]
            )
        return moved

    def assembly_holes(self) -> list[sc.AssemblyHole]:
        """The full round holes (concave cylinders of at least half a circle) of the bodies of every component, placed in
        the assembly: where the parts are fastened through, and so what the sheet must dimension."""
        cached = self.__dict__.get("_asm_holes")
        if cached is not None:
            return cached
        import holes as hole_reader  # noqa: PLC0415 - the hole reader of a part, used here on placed bodies

        found: list[sc.AssemblyHole] = []
        for comp_id in self.doc.components:
            for unit in self._body_units(comp_id):
                for face in unit.shape.Faces:
                    try:
                        kind = g.surface_kind(face)
                        if kind == "cylinder":
                            if not hole_reader._is_concave(face):
                                continue
                            cut = hole_reader._analytic_cut(hole_reader._FaceRef("", 0), face)
                        elif kind == "other":  # a threaded or exported hole: a cylinder written as a B-spline
                            cut = hole_reader._fitted_cut(hole_reader._FaceRef("", 0), face)
                        else:
                            continue
                    except Exception:  # noqa: BLE001 - a face that cannot be read is not a hole
                        continue
                    if cut is None or cut.arc < hole_reader.MIN_ARC:
                        continue
                    middle = (cut.t0 + cut.t1) / 2
                    centre = sc.add(cut.anchor, sc.scale(cut.direction, middle))
                    found.append(sc.AssemblyHole(comp_id, centre, cut.direction, 2 * cut.radius))
        self.__dict__["_asm_holes"] = found
        return found

    def instance_box(self, comp_id: str) -> tuple[sc.Vec3, sc.Vec3]:
        comp = self.doc.components.get(comp_id)
        if comp is None:
            msg = f"{comp_id!r} is not a component of this assembly"
            raise sc.ItemError(msg)
        box = reg.component_box(self.doc, comp, exact=False)[0]
        return tuple(box["min"]), tuple(box["max"])  # type: ignore[return-value]

    def _owner(
        self,
        units: list[_Unit],
        rects: list[tuple[float, ...]],
        frame: tuple[sc.Vec3, ...],
        u: float,
        v: float,
        reach: float,
    ) -> tuple[str, sc.Vec3] | None:
        """The instance that owns the surface the ray through ``(u, v)`` meets first, and where; ``None`` when the ray
        meets nothing or two instances meet it at the same depth."""
        centre, xdir, ydir, direction = frame
        origin = sc.add(sc.add(centre, sc.scale(xdir, u)), sc.scale(ydir, v))
        ray = Part.makeLine(
            V(*sc.add(origin, sc.scale(direction, reach))), V(*sc.sub(origin, sc.scale(direction, reach)))
        )
        crossing = sorted(
            (i for i, r in enumerate(rects) if r[0] <= u <= r[2] and r[1] <= v <= r[3]), key=lambda i: -rects[i][4]
        )
        hits: list[tuple[float, str, sc.Vec3]] = []
        for i in crossing:
            if hits and rects[i][4] < max(h[0] for h in hits) - DEPTH_TOL_MM:
                break  # this body and every later one lie wholly behind a surface already met
            try:
                points = units[i].shape.section(ray).Vertexes
            except Exception:  # noqa: BLE001 - OCC refuses some degenerate sections: that body shows nothing here
                continue
            for p in points:
                point = (p.X, p.Y, p.Z)
                hits.append((sc.dot(sc.sub(point, centre), direction), units[i].comp_id, point))
        if not hits:
            return None
        hits.sort(key=lambda h: -h[0])
        front = hits[0]
        if any(h[1] != front[1] and h[0] > front[0] - DEPTH_TOL_MM for h in hits[1:]):
            return None
        return front[1], front[2]

    def instance_anchors(
        self,
        instances: list[str],
        frame: tuple[sc.Vec3, sc.Vec3, sc.Vec3],
        offsets: dict[str, sc.Vec3],
        margin_mm: float,
        outward: bool = False,
    ) -> dict[str, list[sc.Vec3]]:
        centre, xdir, direction = frame
        ydir = sc.unit(sc.cross(direction, xdir))
        everyone = self._owner_ids()
        units: list[_Unit] = []
        for cid in everyone:
            units += self._displaced(cid, offsets.get(cid))
        rects = [_frame_rect(u, centre, xdir, ydir, direction) for u in units]
        full = (centre, xdir, ydir, direction)
        reach = 2.0 * max((math.dist(u.low, u.high) for u in units), default=1.0) + 2.0 * max(
            (math.dist(centre, u.low) for u in units), default=1.0
        )
        out: dict[str, list[sc.Vec3]] = {}
        for cid in instances:
            mine = [(u, r) for u, r in zip(units, rects, strict=True) if u.comp_id == cid]
            if not mine:
                out[cid] = []
                continue
            spread = outward and len(mine) == 1
            samples = _grid(mine[0][1], OUTWARD_GRID) if spread else _samples([r for _, r in mine])
            good: list[tuple[float, sc.Vec3]] = []
            fallback: list[sc.Vec3] = []
            for u, v in samples:
                found = self._owner(units, rects, full, u, v, reach)
                if found is None or found[0] != cid:
                    continue
                around = [(u + margin_mm, v), (u - margin_mm, v), (u, v + margin_mm), (u, v - margin_mm)]
                if all(
                    (o := self._owner(units, rects, full, a, b, reach)) is not None and o[0] == cid for a, b in around
                ):
                    good.append((math.hypot(u, v), found[1]))
                    if not spread:
                        break
                    continue
                fallback.append(found[1])
            out[cid] = [p for _, p in sorted(good, reverse=True)[:1]] if good else fallback[:1]
        return out

    def visible_share(
        self, instances: list[str], frame: tuple[sc.Vec3, sc.Vec3, sc.Vec3], offsets: dict[str, sc.Vec3]
    ) -> dict[str, float]:
        """For each instance, the share (0 to 1) of the outline it has in a view looking against ``frame`` = (centre,
        xdir, direction) that is really its own surface: rays cast on a grid over the box of each of its bodies, counted when
        the first surface they meet is the instance's. A spacer seen as a sliver between two plates has a small share."""
        centre, xdir, direction = frame
        ydir = sc.unit(sc.cross(direction, xdir))
        units: list[_Unit] = []
        for cid in self._owner_ids():
            units += self._displaced(cid, offsets.get(cid))
        rects = [_frame_rect(u, centre, xdir, ydir, direction) for u in units]
        full = (centre, xdir, ydir, direction)
        reach = 2.0 * max((math.dist(u.low, u.high) for u in units), default=1.0) + 2.0 * max(
            (math.dist(centre, u.low) for u in units), default=1.0
        )
        out: dict[str, float] = {}
        for cid in instances:
            mine = [r for u, r in zip(units, rects, strict=True) if u.comp_id == cid]
            samples = [p for r in mine[:MAX_BODIES_SAMPLED] for p in _grid(r, OUTWARD_GRID)]
            if not samples:
                out[cid] = 0.0
                continue
            own = sum(
                1 for u, v in samples if (o := self._owner(units, rects, full, u, v, reach)) is not None and o[0] == cid
            )
            out[cid] = own / len(samples)
        return out

    def assembly_projection(
        self, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3, offsets: dict[str, sc.Vec3]
    ) -> sc.Projection:
        shapes = []
        poses = []
        for cid, comp in self.doc.components.items():
            shape = reg.placed_component_shape(self.doc, comp)
            poses.append((cid, comp._placed_key, shape.hashCode()))
            if cid in offsets:
                shape = Part.Shape(shape)
                shape.Placement = App.Placement(V(*offsets[cid]), App.Rotation()).multiply(shape.Placement)
            if not shape.isNull():
                shapes.append(shape)
        if not shapes:
            raise sc.ItemError("the assembly has no geometry to draw")
        # the hidden-line projection of a big assembly takes minutes and a sheet is composed several times per request
        # (and again to capture and export): the same poses, offsets and frame give the same answer
        key = (self.doc.id, tuple(poses), centre, xdir, direction, tuple(sorted(offsets.items())))
        return sheet_project.project_view(key, shapes, centre, xdir, direction)


# --- the joints of a welded part ------------------------------------------------------------------------------------------

CONTACT_TOL_MM = 0.05  # two bodies this near touch
MAX_BODIES_JOINTED = 60
SEAM_DEFLECTION_MM = 0.2
MIN_CONTACT_MM2 = 0.01
RAY_OFFSET_MM = 0.3  # the ray that tells whether a seam is hidden starts this far in front of it


def _plane_frame(face: Any) -> tuple[sc.Vec3, sc.Vec3] | None:
    """A point and the outward unit normal of a planar face, ``None`` for any other face."""
    if g.surface_kind(face) != "plane":
        return None
    u, v = face.Surface.parameter(face.CenterOfMass)
    return g.vec(face.valueAt(u, v)), g.unit(g.vec(face.normalAt(u, v)))


def _outline(face: Any) -> list[sc.Vec3]:
    return [g.vec(p) for p in face.OuterWire.discretize(Deflection=SEAM_DEFLECTION_MM)]


def _measure_joint(
    a: str, b: str, sa: Any, sb: Any, thick: dict[str, float]
) -> Any:
    """The joint of two touching bodies: the faces of one that lie on faces of the other (the seam is the outer outline of
    what overlaps) or, when no face lies on another, the edges where the surfaces meet. ``None`` when they only touch at a
    point."""
    import sheet_joints as sj  # noqa: PLC0415

    flats_a = [(f, _plane_frame(f)) for f in sa.Faces]
    flats_b = [(f, _plane_frame(f)) for f in sb.Faces]
    big_a = max((f.Area for f, fr in flats_a if fr), default=0.0)
    big_b = max((f.Area for f, fr in flats_b if fr), default=0.0)
    overlap = 0.0
    best: tuple[float, Any, Any, Any] | None = None
    seams: list[tuple[sc.Vec3, ...]] = []
    for fa, ra in flats_a:
        if ra is None:
            continue
        for fb, rb in flats_b:
            if rb is None or sc.dot(ra[1], rb[1]) > -0.9999:
                continue
            if abs(sc.dot(sc.sub(rb[0], ra[0]), ra[1])) > CONTACT_TOL_MM:
                continue
            box = App.BoundBox(fa.BoundBox)
            box.enlarge(CONTACT_TOL_MM)
            if not box.intersect(fb.BoundBox):
                continue
            try:
                common = fa.common(fb)
            except Exception:  # noqa: BLE001 - a boolean that fails is no contact face
                continue
            for piece in common.Faces:
                if piece.Area < MIN_CONTACT_MM2:
                    continue
                overlap += piece.Area
                seams.append(tuple(_outline(piece)))
                if best is None or piece.Area > best[0]:
                    best = (piece.Area, fa, fb, piece)
    if best is not None:
        _, fa, fb, piece = best
        larger = fa if fa.Area >= fb.Area else fb
        outline = larger.OuterWire
        flush = False
        for line in seams:
            for p, q in zip(line, line[1:], strict=False):
                mid = App.Vector((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2)
                if outline.distToShape(Part.Vertex(mid))[0] < CONTACT_TOL_MM:
                    flush = True
        contact = sj.Contact(overlap, (fa.Area, fb.Area), (fa.Area <= sj.END_FACE_SHARE * big_a, fb.Area <= sj.END_FACE_SHARE * big_b), flush)
    else:
        try:
            edges = sa.section(sb).Edges
        except Exception:  # noqa: BLE001
            return None
        seams = [tuple(g.vec(p) for p in e.discretize(Deflection=SEAM_DEFLECTION_MM)) for e in edges]
        if not seams:
            return None
        contact = sj.Contact(0.0, (0.0, 0.0), (False, False))
    return sj.Joint((a, b), sj.classify(contact), tuple(seams), (thick[a], thick[b]), contact.area)


class WeldJoints:
    """The joints of a welded part on the live document, mixed into ``LiveGeometry``."""

    doc: reg.DocumentRec

    def weld_joints(self) -> list[Any]:
        """Where the bodies of the part touch (``sheet_joints.Joint``), measured, in the order of their body ids; none for
        an assembly or a part of one body."""
        cached = self.__dict__.get("_weld_joints")
        if cached is not None:
            return cached
        import sheet_joints as sj  # noqa: PLC0415

        found: list[Any] = []
        if self.doc.kind != "assembly":
            shapes = {}
            thick: dict[str, float] = {}
            for body in self.doc.live_bodies()[:MAX_BODIES_JOINTED]:
                shape = reg.world_shape(self.doc, body)
                if shape.isNull() or not shape.Solids:
                    continue
                shapes[body.id] = shape
                box = shape.BoundBox
                dims = tuple(sorted((box.XLength, box.YLength, box.ZLength), reverse=True))
                thick[body.id] = sj.wall_thickness(shape.Volume, shape.Area, dims)  # type: ignore[arg-type]
            ids = sorted(shapes, key=sj.natural)
            for i, a in enumerate(ids):
                for b in ids[i + 1 :]:
                    box = App.BoundBox(shapes[a].BoundBox)
                    box.enlarge(CONTACT_TOL_MM)
                    if not box.intersect(shapes[b].BoundBox):
                        continue
                    try:
                        if shapes[a].distToShape(shapes[b])[0] > CONTACT_TOL_MM:
                            continue
                        joint = _measure_joint(a, b, shapes[a], shapes[b], thick)
                    except Exception:  # noqa: BLE001 - a pair the kernel cannot measure has no joint listed
                        continue
                    if joint is not None:
                        found.append(joint)
        self.__dict__["_weld_joints"] = found
        return found

    def seam_hidden(self, point: sc.Vec3, towards: sc.Vec3) -> bool:
        """Whether a body of the part stands between the seam point ``point`` and a viewer far away along ``towards`` (the
        direction to the viewer): a ray from just in front of the point, tried against every live body."""
        cache: list[Any] | None = self.__dict__.get("_ray_shapes")
        if cache is None:
            cache = []
            for body in self.doc.live_bodies()[:MAX_BODIES_JOINTED]:
                shape = reg.world_shape(self.doc, body)
                if not shape.isNull() and shape.Solids:
                    cache.append(shape)
            self.__dict__["_ray_shapes"] = cache
        direction = g.unit(towards)
        far = (max(s.BoundBox.DiagonalLength for s in cache) if cache else 0.0) + 10.0
        start = sc.add(point, sc.scale(direction, RAY_OFFSET_MM))
        line = Part.makeLine(g.fcvec(start), g.fcvec(sc.add(point, sc.scale(direction, far))))
        for shape in cache:
            try:
                found = shape.section(line)
            except Exception:  # noqa: BLE001 - a body the kernel cannot cut by a line blocks nothing
                continue
            if found.Vertexes or found.Edges:
                return True
        return False
