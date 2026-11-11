"""Components of an assembly: instances of a part DOCUMENT, linked to it, not copied from it.

A component is an ``App::Part`` (its ``Placement`` is the pose of the instance) holding one ``App::Link`` per solid
body of the part document (``LinkTransform`` on, so the pose of the body inside the part is kept and the component
pose goes on top of it). The links are cross-document: FreeCAD stores the part's file next to the link, relative to the
assembly's file, and loads the part with the assembly. A part saved again therefore shows in the assembly on the next
recompute or reopen.

The registry keeps, for each component, the part's bodies as a compound in the part's own axes (``base_shape``) with
the face / edge / vertex records cloned from the part's: that is what mates, interference and mass properties measure.
``populate`` fills it; ``refresh`` fills it again from the part documents as they are now.
"""

import math
import os
from typing import Any

import fc
import geometry as g
import Part
import registry as reg
from protocol import invalid_request


def part_bodies(part: reg.DocumentRec) -> list[reg.BodyRec]:
    """The live bodies of a part that have a shape: what an instance of the part is made of."""
    return [b for b in part.live_bodies() if not reg.world_shape(part, b).isNull()]


def link_target(part: reg.DocumentRec, body: reg.BodyRec) -> Any:
    """The FreeCAD object of ``part`` a link must point at to show ``body``."""
    name = body.fc_body if body.kind == "partdesign" else body.result_obj
    return part.fc_doc.getObject(name) if name else None


def document_file(doc: reg.DocumentRec) -> str:
    return str(doc.fc_doc.FileName or "")


def same_file(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def relative_file(target: str, start_dir: str) -> str:
    """``target`` relative to ``start_dir`` with forward slashes (what the record keeps; independent of the OS)."""
    try:
        return os.path.relpath(target, start_dir).replace("\\", "/")
    except ValueError:  # another drive on Windows: there is no relative form
        return target.replace("\\", "/")


def require_files(doc: reg.DocumentRec, part: reg.DocumentRec, operation_id: str | None) -> None:
    """A cross-document link needs both files: FreeCAD stores the part by its file, and the owner must be saved to
    resolve it. The caller (the session) saves the assembly itself; the part is the engineer's to save."""
    if not document_file(doc):
        raise invalid_request(
            f"the assembly {doc.name!r} has no file yet: save it before inserting a part (components are linked to "
            "the part's file, relative to the assembly's)",
            operation_id,
        )
    if not document_file(part):
        raise invalid_request(
            f"the part {part.name!r} has no file: save it before inserting it into an assembly "
            "(the assembly links to the part's file, so the part stays editable on its own)",
            operation_id,
        )


def _lazy_spec(body: reg.BodyRec, counts: tuple[int, int, int]) -> reg.Pristine | None:
    """The id ranges of a body that is still what a foreign file brought in, when its records need not be copied: the
    component makes them from the shape, if and when something asks. ``None`` for any other body (modelled here, or
    changed since). ``counts``: the faces, edges and vertices of its shape."""
    if body.kind != "part" or body.reference is None:
        return None
    spec = body.unchanged_since_adoption()
    if spec is None or counts[0] != spec.n_faces:
        return None
    if not spec.faces_only and (counts[1] != spec.n_edges or counts[2] != spec.n_vertices):
        return None
    return spec


def _untouched(part: reg.DocumentRec, body: reg.BodyRec) -> bool:
    """Is the body the very shape a foreign file brought in."""
    return body.reference is not None and reg.topo_shape(part, body).isSame(body.reference)


def _fill(comp: reg.ComponentRec, part: reg.DocumentRec, bodies: list[reg.BodyRec]) -> None:
    """Bodies, base shape and face / edge / vertex records of ``comp`` from ``bodies`` of ``part`` (part axes). The
    shapes are the part's own geometry under their own Location: nothing is copied, and no face is described here."""
    shapes = [Part.Shape(reg.world_shape(part, b)) for b in bodies]
    comp.base_shape = g.make_compound(shapes)
    comp.reset_records()
    comp.body_ids, comp.body_solids, comp.face_counts = [], {}, {}
    comp.foreign = bool(bodies) and all(_untouched(part, b) for b in bodies)
    f_offset = e_offset = v_offset = 0
    for b, shape in zip(bodies, shapes, strict=True):
        comp.body_ids.append(b.id)
        comp.body_solids[b.id] = shape
        counts = (shape.countElement("Face"), shape.countElement("Edge"), shape.countElement("Vertex"))
        spec = _lazy_spec(b, counts)
        if spec is not None:
            comp.defer(reg.Slice(b.id, spec, b.created_by, f_offset, e_offset, v_offset))
        else:
            _copy_records(comp, part, b, f_offset, e_offset, v_offset)
        comp.face_counts[b.id] = counts[0]
        f_offset += counts[0]
        e_offset += counts[1]
        v_offset += counts[2]
    comp.face_total, comp.edge_total = f_offset, e_offset
    comp._placed = None
    comp._placed_key = None
    if comp.foreign:
        _inherit_measures(comp, bodies)


def _inherit_measures(comp: reg.ComponentRec, bodies: list[reg.BodyRec]) -> None:
    """The component is the very shape of the part's bodies: what they measured (volumes, centres of mass, area, the
    validity check) is what the component measures, so a measure of hundreds of solids is not taken a second time.
    Whatever a body did not measure yet is left for the component to measure when asked."""
    if all(b._vols is not None for b in bodies):
        comp._volumes = [v for b in bodies for v in b._vols or ()]
    if all(b._centres is not None for b in bodies):
        comp._centres = [c for b in bodies for c in b._centres or ()]
    areas = [b._area for b in bodies]
    if all(a is not None and math.isfinite(a) for a in areas):
        comp._area = sum(a for a in areas if a is not None)
    for b in bodies:  # the boxes they measured, in the part's axes (the pose of the component moves them)
        if b._bbox is not None:
            comp._body_boxes[True][b.id] = b._bbox
        if b._fbox is not None:
            comp._body_boxes[False][b.id] = b._fbox
    # a body nobody checked is left unchecked only when it is the big surface body whose check is left for later
    skipped = [
        b.id
        for b in bodies
        if b._valid is None
        and reg.validity_unchecked(b, comp.face_counts[b.id], comp.body_solids[b.id].countElement("Solid") > 0)
    ]
    if all(b._valid is not None or b.id in skipped for b in bodies):
        comp._unchecked = skipped
        comp._invalid = [b.id for b in bodies if b._valid is False]
        comp._valid = not comp._invalid


def _copy_records(
    comp: reg.ComponentRec, part: reg.DocumentRec, b: reg.BodyRec, f_offset: int, e_offset: int, v_offset: int
) -> None:
    """The records of a body modelled here: copies of the part's, shifted to the body's place in the compound."""
    bp = reg.body_placement(part, b)
    for rec in b.faces.values():
        if rec.stale:
            continue
        c = rec.clone()
        c.index = f_offset + rec.index
        c.bind(comp.base_shape)
        if rec.axis_dir is not None:
            c.axis_dir = g.rotate_direction(bp, rec.axis_dir)
        comp._faces[c.id] = c
    for rec in b.edges.values():
        if rec.stale:
            continue
        c = rec.clone()
        c.index = e_offset + rec.index
        c.bind(comp.base_shape)
        comp._edges[c.id] = c
    for rec in b.vertices.values():
        if rec.stale:
            continue
        c = rec.clone()
        c.index = v_offset + rec.index
        c.point = g.apply_placement(bp, rec.point)
        comp._vertices[c.id] = c


def populate(comp: reg.ComponentRec, part: reg.DocumentRec) -> list[reg.BodyRec]:
    bodies = part_bodies(part)
    _fill(comp, part, bodies)
    return bodies


def add_links(
    doc: reg.DocumentRec, comp_part: Any, part: reg.DocumentRec, bodies: list[reg.BodyRec], label: str
) -> list[str]:
    """One ``App::Link`` per body inside the component's ``App::Part``; returns their object names."""
    names: list[str] = []
    for body in bodies:
        target = link_target(part, body)
        if target is None:
            raise invalid_request(f"part {part.name!r}: the object of body {body.id} is gone", None)
        link = fc.add_object(
            doc.fc_doc, "App::Link", comp_part.Name + "_link", label=f"{label}_{body.id.replace(':', '')}"
        )
        fc.call(link, "setLink", target)
        fc.set_prop(link, "LinkTransform", True)
        fc.call(comp_part, "addObject", link)
        names.append(str(link.Name))
    return names


def sync_links(doc: reg.DocumentRec, comp: reg.ComponentRec, part: reg.DocumentRec, bodies: list[reg.BodyRec]) -> None:
    """After the part changed: a link for every body of the part now and none for a body that is gone (a part
    document that grew or lost a body). Not an op: no undo unit, the registry is the one that says what is there."""
    grp = doc.fc_doc.getObject(comp.fc_part)
    if grp is None:
        return
    wanted = {link_target(part, b).Name: b for b in bodies if link_target(part, b) is not None}
    have: dict[str, str] = {}
    for name in comp.fc_links:
        link = doc.fc_doc.getObject(name)
        target = getattr(link, "LinkedObject", None) if link is not None else None
        if link is None or target is None or target.Name not in wanted:
            if link is not None:
                fc.remove_object(doc.fc_doc, name)
            continue
        have[target.Name] = name
    kept = [n for n in comp.fc_links if doc.fc_doc.getObject(n) is not None]
    for target_name, body in wanted.items():
        if target_name in have:
            continue
        kept.extend(add_links(doc, grp, part, [body], comp.id.replace(":", "")))
    comp.fc_links = kept
    if kept:
        comp.fc_shape = kept[0]


def _extent(comp: reg.ComponentRec) -> tuple[float, int]:
    """``(volume, faces)`` of the component's shape: what tells that a part changed (the volumes are the ones the
    component already measured)."""
    return sum(comp.volumes()), comp.face_total


def refresh(doc: reg.DocumentRec, documents: dict[str, reg.DocumentRec]) -> list[str]:
    """Bring the components of an assembly up to date with their part documents (a part saved again, a body added to
    it): recompute the links, then take the base shape and records from the parts again. The pose, the mates and the
    fixed flag stay. Returns the ids of the components whose geometry changed."""
    changed: list[str] = []
    for comp in doc.components.values():
        part = documents.get(comp.part_doc)
        if part is None:
            continue
        bodies = part_bodies(part)
        before = _extent(comp) if comp.base_shape is not None and not comp.base_shape.isNull() else None
        sync_links(doc, comp, part, bodies)
        _fill(comp, part, bodies)
        after = None if comp.base_shape.isNull() else _extent(comp)
        if before is None or after is None or before[1] != after[1] or abs(before[0] - after[0]) > 1e-6:
            changed.append(comp.id)
    doc.fc_doc.recompute()
    return changed
