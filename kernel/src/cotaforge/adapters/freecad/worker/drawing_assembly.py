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

    def _body_units(self, comp_id: str) -> list[_Unit]:
        cache: dict[str, list[_Unit]] = self.__dict__.setdefault("_unit_cache", {})
        if comp_id not in cache:
            comp = self.doc.components[comp_id]
            units = []
            for body_id in comp.body_ids:
                shape = reg.placed_component_body(self.doc, comp, body_id)
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
        everyone = list(self.doc.components)
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
