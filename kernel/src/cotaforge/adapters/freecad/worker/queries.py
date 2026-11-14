"""Descriptors and read-only queries: faces_of, edges_between, edges_of, vertices_of,
vertex_at, bodies_of, is_valid, body_interference, mass_properties, bounding_box, feature_tree, face_info, edge_info,
references, undo_last, export_macro, export_step, set_view, capture, style_bodies and section_plane (the last four
visible worker only).
"""

import math
import os
import sys
import traceback
from typing import Any

import appearance
import fc
import FreeCAD as App
import geometry as g
import gui
import hole_colours
import journal
import Part
import provenance as prov
import registry as reg
import sketch_defined
from protocol import WorkerError, error, invalid_reference, invalid_request, not_supported

# FreeCAD object types that count as model features when the engineer adds them by hand (live tree).
MANUAL_FEATURE_PREFIXES = ("PartDesign::", "Part::")
MANUAL_FEATURE_SKIP = frozenset({"PartDesign::Body"})  # a container, not a feature

# --- element access ----------------------------------------------------------------------------------------------


def _face_shape(doc: reg.DocumentRec, rec: reg.FaceRec, comp: reg.ComponentRec | None) -> Any:
    # by name, never ``.Faces[i]``: that wraps every face of the shape to hand back one
    if comp is not None:
        return reg.placed_component_shape(doc, comp).getElement(rec.sub_name)
    body = reg.resolve_body(doc, rec.body, None)
    return reg.world_shape(doc, body).getElement(rec.sub_name)


def _edge_shape(doc: reg.DocumentRec, rec: reg.EdgeRec, comp: reg.ComponentRec | None) -> tuple[Any, Any]:
    """(edge, solid) in world coordinates: the solid is the body that owns the edge (not the whole compound of a
    component, where another solid could answer a probe point)."""
    if comp is not None:
        edge = reg.placed_component_shape(doc, comp).getElement(rec.sub_name)
        return edge, reg.placed_component_body(doc, comp, rec.body)
    shape = reg.world_shape(doc, reg.resolve_body(doc, rec.body, None))
    return shape.getElement(rec.sub_name), shape


def _edge_faces(doc: reg.DocumentRec, rec: reg.EdgeRec, comp: reg.ComponentRec | None) -> list[Any]:
    """The faces the edge bounds, from its record (the CAD's own ``ancestorsOfType`` walks the whole shape for each
    call)."""
    owner = comp if comp is not None else reg.resolve_body(doc, rec.body, None)
    found = (owner.find("faces", face_id) for face_id in rec.faces)
    return [_face_shape(doc, face, comp) for face in found if face is not None]


def _axis_dir_world(doc: reg.DocumentRec, rec: reg.FaceRec, comp: reg.ComponentRec | None) -> g.Vec3 | None:
    if rec.axis_dir is None:
        return None
    if comp is not None:
        return g.rotate_direction(reg.component_placement(doc, comp), rec.axis_dir)
    body = reg.resolve_body(doc, rec.body, None)
    return g.rotate_direction(reg.body_placement(doc, body), rec.axis_dir)


def face_is_planar(doc: reg.DocumentRec, rec: reg.FaceRec, comp: reg.ComponentRec | None) -> bool:
    """Is the face a plane: what ``face_descriptor`` says by giving it a normal, without measuring the face (its
    centre, area, box and normal) for a caller that only needs to know which kind of surface it is."""
    return g.surface_kind(_face_shape(doc, rec, comp)) == "plane"


def face_descriptor(doc: reg.DocumentRec, rec: reg.FaceRec, comp: reg.ComponentRec | None) -> dict[str, Any]:
    face = _face_shape(doc, rec, comp)
    geo = prov.face_geometry(face, _axis_dir_world(doc, rec, comp))
    if comp is not None:
        handle = reg.prefixed(comp.id, rec.id)
        body = reg.prefixed(comp.id, rec.body)
        provenance = {"created_by": {"id": comp.created_by}, "modified_by": []}
        component: dict[str, str] | None = {"id": comp.id}
    else:
        handle = rec.id
        body = rec.body
        provenance = {"created_by": {"id": rec.created_by}, "modified_by": [{"id": m} for m in rec.modified_by]}
        component = None
    out = {"handle": {"id": handle}, "body": {"id": body}, "component": component}
    out.update(geo)
    out["provenance"] = provenance
    return out


def _edge_handle(rec: reg.EdgeRec, comp: reg.ComponentRec | None) -> str:
    return reg.prefixed(comp.id, rec.id) if comp is not None else rec.id


def _edge_geometry(doc: reg.DocumentRec, rec: reg.EdgeRec, comp: reg.ComponentRec | None) -> dict[str, Any]:
    """What the edge is (curve, ends, length, box, and the convexity when two faces bound it); any edge has it,
    whatever the faces round it."""
    edge, solid = _edge_shape(doc, rec, comp)
    return prov.edge_geometry(edge, _edge_faces(doc, rec, comp), solid)


def odd_edge_refusal(handle: str, faces: int) -> WorkerError:
    """A fillet, a chamfer, ``edges_between`` and ``edge_info`` need an edge that exactly two faces share. A foreign
    file's open shell has free edges (one face), a face glued to two solids makes a non-manifold one (three or more):
    they keep their record and their id, and are refused here, where one is asked for."""
    if faces == 0:
        what = "no face uses it"
    elif faces == 1:
        what = "only one face uses it: a free edge of an open shell or of loose faces"
    else:
        what = f"{faces} faces share it: a non-manifold edge"
    return invalid_request(
        f"edge {handle} is not shared by exactly two faces ({what}); a fillet, a chamfer or an edge between two faces "
        "needs one that is",
        None,
    )


def edge_descriptor(doc: reg.DocumentRec, rec: reg.EdgeRec, comp: reg.ComponentRec | None) -> dict[str, Any]:
    handle = _edge_handle(rec, comp)
    face_ids = [reg.prefixed(comp.id, f) if comp is not None else f for f in rec.faces]
    if len(face_ids) != 2:  # before any geometry: an odd edge costs nothing to refuse
        raise odd_edge_refusal(handle, len(face_ids))
    out = {"handle": {"id": handle}, "faces": [{"id": face_ids[0]}, {"id": face_ids[1]}]}
    out.update(_edge_geometry(doc, rec, comp))
    return out


def vertex_descriptor(doc: reg.DocumentRec, rec: reg.VertexRec, comp: reg.ComponentRec | None) -> dict[str, Any]:
    point = rec.point
    edges = list(rec.edges)
    handle = rec.id
    if comp is not None:
        point = g.apply_placement(reg.component_placement(doc, comp), point)
        edges = [reg.prefixed(comp.id, e) for e in edges]
        handle = reg.prefixed(comp.id, rec.id)
    else:
        body = reg.resolve_body(doc, rec.body, None)
        point = g.apply_placement(reg.body_placement(doc, body), point)
    return {"handle": {"id": handle}, "point": g.clean(point), "edges": [{"id": e} for e in edges]}


def catalog_line(doc: reg.DocumentRec, body: reg.BodyRec) -> str:
    """``DME 5104GL (guide pin GL, dia 0.875 in x 4.25 in)``: the catalogue properties of the body's own object."""
    obj = doc.fc_doc.getObject(body.fc_body if body.kind == "partdesign" else body.result_obj)
    if obj is None or "CotaforgeCatalogRef" not in obj.PropertiesList:
        return ""

    def prop(name: str) -> str:
        return str(getattr(obj, name, "") or "") if name in obj.PropertiesList else ""

    size = prop("CotaforgeCatalogSize")
    return f"{prop('CotaforgeCatalogFamily')} {prop('CotaforgeCatalogRef')}" + (f" ({size})" if size else "")


def body_descriptor(doc: reg.DocumentRec, body: reg.BodyRec) -> dict[str, Any]:
    shape = reg.world_shape(doc, body)
    empty = shape.isNull()
    return {
        "catalog": catalog_line(doc, body),
        "handle": {"id": body.id},
        "volume_mm3": 0.0 if empty else float(sum(reg.body_volumes(doc, body))),
        "bbox": dict(g.ZERO_BBOX) if empty else reg.body_bbox(doc, body),
        "bbox_faces_left_out": 0 if empty else reg.body_box(doc, body)[1],
        "face_count": 0 if empty else shape.countElement("Face"),
        "provenance": {"created_by": {"id": body.created_by}, "modified_by": [{"id": m} for m in body.modified_by]},
    }


# --- provenance queries -------------------------------------------------------------------------------------------


def faces_of(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    feature = doc.feature(reg.handle_id(params["feature"], op, "feature"), op)
    role = params.get("role", "any")
    if feature.kind == "component":
        if role == "modified":
            return {"faces": []}
        comp = reg.resolve_component(doc, str(feature.component), op)
        return {"faces": [face_descriptor(doc, rec, comp) for rec in comp.faces.values() if not rec.stale]}
    ids: list[str] = []
    if role in ("created", "any"):
        ids.extend(feature.created_faces)
    if role in ("modified", "any"):
        ids.extend(f for f in feature.modified_faces if f not in ids)
    out = []
    for fid in ids:
        try:
            rec, comp = reg.resolve_face(doc, fid, op)
        except Exception:  # noqa: BLE001 - stale faces are silently dropped (fake parity)
            continue
        out.append(face_descriptor(doc, rec, comp))
    return {"faces": out}


Owner = tuple[str | None, str]
"""A body of the document (``(None, body id)``) or a body of a component (``(component id, local body id)``)."""


def _owner(rec: reg.FaceRec | reg.EdgeRec, comp: reg.ComponentRec | None) -> Owner:
    return (comp.id if comp is not None else None, rec.body)


def _all_edges(
    doc: reg.DocumentRec, only: set[Owner] | None = None
) -> list[tuple[reg.EdgeRec, reg.ComponentRec | None]]:
    """The live edges of the bodies and of the components (``only``: just those of these owners; the records of a body
    nobody asked about are not made)."""
    out: list[tuple[reg.EdgeRec, reg.ComponentRec | None]] = []
    for body in doc.live_bodies():
        if only is not None and (None, body.id) not in only:
            continue
        out.extend((e, None) for e in body.edges.values() if not e.stale)
    for comp in doc.components.values():
        if only is None:
            bodies = None
        else:
            bodies = {body_id for comp_id, body_id in only if comp_id == comp.id}
            if not bodies:
                continue
        out.extend((e, comp) for e in comp.records("edges", bodies))
    return out


def _edge_face_ids(rec: reg.EdgeRec, comp: reg.ComponentRec | None) -> set[str]:
    if comp is None:
        return set(rec.faces)
    return {reg.prefixed(comp.id, f) for f in rec.faces}


def edges_between(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    a = reg.handle_id(params["a"], op, "a")
    b = reg.handle_id(params["b"], op, "b")
    owners = {_owner(*reg.resolve_face(doc, a, op)), _owner(*reg.resolve_face(doc, b, op))}
    wanted = {a, b}
    edges = [
        edge_descriptor(doc, rec, comp)
        for rec, comp in _all_edges(doc, owners)
        if _edge_face_ids(rec, comp) == wanted and len(rec.faces) == 2
    ]
    return {"edges": edges}


def edges_of(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    face_id = reg.handle_id(params["face"], op, "face")
    rec, comp = reg.resolve_face(doc, face_id, op)
    edges = [
        edge_descriptor(doc, e, c)
        for e, c in _all_edges(doc, {_owner(rec, comp)})
        if c is comp and rec.id in e.faces and len(e.faces) == 2
    ]
    return {"edges": edges}


def _vertex_records(doc: reg.DocumentRec, comp: reg.ComponentRec | None, bodies: set[str]) -> list[reg.VertexRec]:
    """The live vertex records of the given bodies of the document, or of the component."""
    if comp is not None:
        return comp.records("vertices", bodies)
    return [v for b in doc.live_bodies() if b.id in bodies for v in b.vertices.values() if not v.stale]


def vertices_of(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    edge_id = reg.handle_id(params["edge"], op, "edge")
    rec, comp = reg.resolve_edge(doc, edge_id, op)
    edge, _ = _edge_shape(doc, rec, comp)
    if g.curve_kind(edge) == "circle":
        return {"vertices": []}
    candidates = [v for v in _vertex_records(doc, comp, {rec.body}) if rec.id in v.edges]
    ordered: list[dict[str, Any]] = []
    for point in (g.vec(edge.firstVertex().Point), g.vec(edge.lastVertex().Point)):
        for v in candidates:
            desc = vertex_descriptor(doc, v, comp)
            if g.dist(tuple(desc["point"]), point) <= 1e-6 and desc not in ordered:
                ordered.append(desc)
                break
    return {"vertices": ordered}


def vertex_at(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    wanted = {reg.handle_id(f, op, "faces") for f in params["faces"]}
    comps = set()
    owners: set[Owner] = set()
    for fid in wanted:
        face, comp = reg.resolve_face(doc, fid, op)
        comps.add(comp.id if comp is not None else None)
        owners.add(_owner(face, comp))
    if len(comps) != 1:
        return {"vertices": []}
    comp_id = next(iter(comps))
    comp = doc.components[comp_id] if comp_id is not None else None
    edges = {rec.id: rec for rec, c in _all_edges(doc, owners) if (c.id if c else None) == comp_id}
    found = []
    for v in _vertex_records(doc, comp, {body_id for _, body_id in owners}):
        covered: set[str] = set()
        for eid in v.edges:
            e = edges.get(eid)
            if e is not None:
                covered.update(_edge_face_ids(e, comp))
        if wanted <= covered:
            found.append(vertex_descriptor(doc, v, comp))
    return {"vertices": found}


def _component_body_box(doc: reg.DocumentRec, comp: reg.ComponentRec, local_body: str, shape: Any) -> dict[str, Any]:
    """The exact box of a body of a component: the one measured in the part's axes, moved by the pose; the placed shape
    is measured only when the pose turns it some other way."""
    box = reg.moved_box(comp.body_local_box(local_body, True)[0], reg.component_placement(doc, comp))
    return box if box is not None else g.shape_bbox(shape)


def component_body_descriptor(doc: reg.DocumentRec, comp: reg.ComponentRec, local_body: str) -> dict[str, Any]:
    shape = reg.placed_component_body(doc, comp, local_body)
    return {
        "handle": {"id": reg.prefixed(comp.id, local_body)},
        "volume_mm3": comp.body_volume(local_body),
        "bbox": _component_body_box(doc, comp, local_body, shape),
        "face_count": comp.face_counts[local_body],
        "provenance": {"created_by": {"id": comp.created_by}, "modified_by": []},
    }


def bodies_of(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    feature = doc.feature(reg.handle_id(params["feature"], op, "feature"), op)
    role = params.get("role", "any")
    ids: list[str] = []
    if role in ("created", "any"):
        ids.extend(feature.created_bodies)
    if role in ("modified", "any"):
        ids.extend(b for b in feature.modified_bodies if b not in ids)
    out = []
    for bid in ids:
        split = reg.split_component(bid)
        if split is not None:
            comp = doc.components.get(split[0])
            if comp is not None and split[1] in comp.body_solids:
                out.append(component_body_descriptor(doc, comp, split[1]))
            continue
        body = doc.bodies.get(bid)
        if body is not None and not body.deleted:
            out.append(body_descriptor(doc, body))
    return {"bodies": out}


# --- document queries ---------------------------------------------------------------------------------------------


def _scope_items(
    doc: reg.DocumentRec, params: dict[str, Any]
) -> list[tuple[reg.BodyRec | None, reg.ComponentRec | None, Any]]:
    """``(body, component, world shape)`` covered by a ``MassRequest`` (None = whole document); ``body`` is the registry
    body the shape is the whole of and ``component`` the component it is the whole of (their measures are kept); both
    ``None`` for a part of a component."""
    op = params.get("operation_id")
    component = params.get("component")
    body = params.get("body")
    if component is not None:
        comp = reg.resolve_component(doc, reg.handle_id(component, op, "component"), op)
        return [(None, comp, reg.placed_component_shape(doc, comp))]
    if body is not None:
        body_id = reg.handle_id(body, op, "body")
        split = reg.split_component(body_id)
        if split is None:
            rec = reg.resolve_body(doc, body_id, op)
            return [(rec, None, reg.world_shape(doc, rec))]
        comp = reg.resolve_component(doc, split[0], op)
        if split[1] not in comp.body_solids:
            raise invalid_reference(body_id, op)
        return [(None, None, reg.placed_component_body(doc, comp, split[1]))]
    items: list[tuple[reg.BodyRec | None, reg.ComponentRec | None, Any]] = [
        (b, None, reg.world_shape(doc, b)) for b in doc.live_bodies()
    ]
    items.extend((None, c, reg.placed_component_shape(doc, c)) for c in doc.components.values())
    return [item for item in items if not item[2].isNull()]


def _scope_shapes(doc: reg.DocumentRec, params: dict[str, Any]) -> list[Any]:
    """World shapes covered by a ``MassRequest`` (None = whole document)."""
    return [shape for _body, _comp, shape in _scope_items(doc, params)]


def _solids(shape: Any) -> list[Any]:
    """Solids of a shape (``Part.getShape`` may wrap results in a compound without CenterOfMass). A solid is asked from
    the shape itself (``.Solids``) even when it is one: the generic wrapper of a placed component lacks what only a
    Solid object has (``MatrixOfInertia``)."""
    return list(shape.Solids)


def mass_properties(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in _scope_items(doc, params) if not item[2].isNull()]
    surface = bool(params.get("surface", True))
    want_centroid = bool(params.get("centroid", True))
    volume = 0.0
    weighted = g.ORIGIN
    area = 0.0
    solid_count = 0
    area_left_out: list[str] = []
    surface_only: list[str] = []
    for body, comp, s in items:
        name = body.id if body is not None else "component"
        if body is not None:  # measured once per shape and kept: hundreds of bodies are asked on every step
            parts = reg.body_parts(doc, body) if want_centroid else [(v, g.ORIGIN) for v in reg.body_volumes(doc, body)]
            body_area = reg.body_area(doc, body) if surface else 0.0
        elif comp is not None:  # the same for a component: the pose moves the centres, nothing else
            placement = reg.component_placement(doc, comp)
            centres = (
                [g.apply_placement(placement, c) for c in comp.centres()]
                if want_centroid
                else [g.ORIGIN] * len(comp.volumes())
            )
            parts = list(zip(comp.volumes(), centres, strict=True))
            body_area = comp.surface_area() if surface else 0.0
            name = comp.id
        else:
            parts = [(float(x.Volume), g.vec(x.CenterOfMass) if want_centroid else g.ORIGIN) for x in _solids(s)]
            body_area = float(s.Area) if surface else 0.0
            if not reg.plausible_area(body_area, s):
                body_area = math.nan
        if math.isfinite(body_area):
            area += body_area
        else:
            area_left_out.append(name)
        if not parts:
            surface_only.append(name)
        for v, centre in parts:
            volume += v
            weighted = g.add(weighted, g.scale(centre, v))
        solid_count += len(parts)
    centroid = g.scale(weighted, 1.0 / volume) if abs(volume) > 1e-12 else g.ORIGIN
    inertia = None
    if want_centroid and solid_count == 1 and volume > 1e-12:
        solids = [solid for _body, _comp, s in items for solid in _solids(s)]
        m = solids[0].MatrixOfInertia  # about the centre of mass (verified on a box)
        inertia = [[m.A11, m.A12, m.A13], [m.A21, m.A22, m.A23], [m.A31, m.A32, m.A33]]
    return {
        "volume_mm3": volume,
        "centroid": g.clean(centroid),
        "centroid_meaningful": solid_count > 0 and abs(volume) > 1e-12,
        "surface_mm2": area if surface else None,
        "area_left_out": area_left_out,
        "surface_only": surface_only,
        "inertia": inertia,
    }


def bounding_box(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    exact = bool(params.get("exact_box", True))
    box = None
    quick = False
    left_out: list[str] = []
    for body, comp, s in _scope_items(doc, params):
        if s.isNull():
            continue
        if body is not None:
            one, missing = reg.body_box(doc, body, exact)
            quick = quick or body._bbox is None
            name = body.id
        elif comp is not None:
            one, missing = reg.component_box(doc, comp, exact)
            quick = quick or (True not in comp._boxes)
            name = comp.id
        else:
            one, missing, name = g.shape_bbox(s), 0, ""
        if missing:
            left_out.append(f"{name}: {missing} face{'s' if missing != 1 else ''}")
        box = g.bbox_union(box, one)
    out = box if box is not None else dict(g.ZERO_BBOX)
    if quick:
        out = {**out, "exact": False}
    if left_out:
        out = {**out, "left_out": left_out}
    return out


OVERLAP_TOL_MM3 = 1e-6  # a common volume below this is contact, not overlap
CONTACT_TOL_MM = 1e-6


def _bop_problems(name: str, shape: Any) -> list[str]:
    try:
        shape.check(True)
    except Exception as exc:  # noqa: BLE001 - BRepCheck reports through an exception
        return [f"{name}: {exc}"]
    return []


def _shape_problems(name: str, shape: Any) -> list[str]:
    """The BOP check of one shape; the solids of a compound are checked one by one and each pair that shares volume is
    a problem."""
    solids = list(shape.Solids)
    if len(solids) < 2:
        return _bop_problems(name, shape)
    # The BOP check of a compound reports two solids that only touch (a shared face, edge or vertex) as a
    # self-intersection: check every solid on its own and call a pair a problem only when it shares volume.
    problems: list[str] = []
    for index, solid in enumerate(solids, start=1):
        problems.extend(_bop_problems(f"{name} solid {index}", solid))
    for i in range(len(solids)):
        for j in range(i + 1, len(solids)):
            if not solids[i].BoundBox.intersect(solids[j].BoundBox):
                continue
            try:
                common = float(solids[i].common(solids[j]).Volume)
            except Exception as exc:  # noqa: BLE001 - OCC refuses some pairs: say so, never assume they are apart
                problems.append(f"{name}: solids {i + 1} and {j + 1} could not be intersected ({exc})")
                continue
            if common > OVERLAP_TOL_MM3:
                problems.append(f"{name}: solids {i + 1} and {j + 1} overlap by {common:.3f} mm3")
    return problems


def _body_problems(doc: reg.DocumentRec, body: reg.BodyRec, deep: bool) -> list[str]:
    """What is wrong with one body. Both checks are kept with the body until its shape changes. A body that is still
    the shape a foreign file brought in (``BodyRec.reference``) gets the cheap ``isValid()`` only unless ``deep``: the
    BOP check of hundreds of imported solids takes minutes and the file's own geometry is not ours to repair."""
    shape = reg.world_shape(doc, body)
    if shape.isNull():
        return []
    untouched = body.reference is not None and reg.topo_shape(doc, body).isSame(body.reference)
    if body._valid is None:
        if not deep and untouched and _check_skipped(body, shape):
            return []
        body._valid = bool(shape.isValid())
    if not body._valid:
        return [f"{body.id}: shape is not valid"]
    if not deep and untouched:
        return []
    if body._deep is None:
        body._deep = _shape_problems(body.id, shape)
    return list(body._deep)


def _check_skipped(body: reg.BodyRec, shape: Any) -> bool:
    """Is this the big surface body of a file whose cheap validity check was left for later (``validity_unchecked``)."""
    return reg.validity_unchecked(body, shape.countElement("Face"), shape.countElement("Solid") > 0)


def is_valid(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    deep = bool(params.get("deep"))
    problems: list[str] = []
    unchecked: list[str] = []
    for body in doc.live_bodies():
        found = _body_problems(doc, body, deep)
        problems.extend(found)
        if not deep and not found and body._valid is None and not body.deleted:
            shape = reg.world_shape(doc, body)
            if not shape.isNull() and _check_skipped(body, shape):
                unchecked.append(body.id)
    checked_in_components = 0
    for comp in doc.components.values():
        if comp.foreign and not deep:
            found, skipped = _foreign_component_problems(comp)
            problems.extend(found)
            unchecked.extend(reg.prefixed(comp.id, body_id) for body_id in skipped)
            checked_in_components += len(comp.body_ids) - len(skipped)
            continue
        shape = reg.placed_component_shape(doc, comp)
        if shape.isNull():
            continue
        if not shape.isValid():
            problems.append(f"{comp.id}: shape is not valid")
            continue
        problems.extend(_shape_problems(comp.id, shape))
    out: dict[str, Any] = {"valid": not problems, "problems": problems}
    if unchecked:
        out["unchecked"] = unchecked
        out["checked"] = sum(1 for b in doc.live_bodies() if b.id not in unchecked) + checked_in_components
    return out


def _foreign_component_problems(comp: reg.ComponentRec) -> tuple[list[str], list[str]]:
    """``(problems, bodies left unchecked)`` of a component made of what a foreign file brought in. Like such a body
    (``_body_problems``), it gets the cheap ``isValid()`` of each solid, kept with the component: the file's own
    geometry is not ours to repair, and the BOP check and the pairwise intersection of hundreds of solids (vendor
    assemblies overlap by design) take minutes each time anything is asked."""
    if comp._valid is None:
        comp._unchecked = []
        comp._invalid = []
        for body_id, shape in comp.body_solids.items():
            if not shape.Solids and shape.countElement("Face") > reg.VALIDITY_FACE_LIMIT:
                comp._unchecked.append(body_id)  # thousands of loose faces: as long to check as to read
            elif not shape.isValid():
                comp._invalid.append(body_id)
        comp._valid = not comp._invalid
    return ([] if comp._valid else [_not_valid(comp)]), list(comp._unchecked)


NAMED_INVALID_BODIES = 5


def _not_valid(comp: reg.ComponentRec) -> str:
    """``comp:1: shape is not valid (comp:1/body:12, comp:1/body:40 (+3 more))``: which bodies, so the engineer knows
    what is broken without the deep check."""
    named = ", ".join(reg.prefixed(comp.id, b) for b in comp._invalid[:NAMED_INVALID_BODIES])
    more = len(comp._invalid) - NAMED_INVALID_BODIES
    return f"{comp.id}: shape is not valid ({named}{f' (+{more} more)' if more > 0 else ''})"


def body_interference(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Every pair of live bodies (or of ``bodies``): the volume they share (``common``) and their minimum distance
    (``distToShape``). A value OCC cannot compute stays ``None`` with a note: a pair that was not measured is never
    reported as clear."""
    op = params.get("operation_id")
    if params.get("bodies") is None:
        bodies = doc.live_bodies()
    else:
        bodies = [reg.resolve_body(doc, reg.handle_id(b, op, "bodies"), op) for b in params["bodies"]]
    shapes = [(b.id, reg.world_shape(doc, b)) for b in bodies]
    pairs = []
    for i in range(len(shapes)):
        for j in range(i + 1, len(shapes)):
            (a, sa), (b, sb) = shapes[i], shapes[j]
            common: float | None = None
            distance: float | None = None
            notes: list[str] = []
            if sa.isNull() or sb.isNull():
                notes.append("a body has no shape")
            else:
                if not sa.Solids or not sb.Solids:
                    # a surface body (loose faces) has no volume to share: only the distance means anything
                    common = 0.0
                    notes.append("a surface body has no volume: distance only")
                else:
                    try:
                        # boxes that do not meet mean no common volume (a BoundBox never under-covers its shape)
                        common = max(0.0, float(sa.common(sb).Volume)) if sa.BoundBox.intersect(sb.BoundBox) else 0.0
                        common = 0.0 if common < OVERLAP_TOL_MM3 else common
                    except Exception as exc:  # noqa: BLE001 - no number is better than a wrong one
                        notes.append(f"common volume not computed ({type(exc).__name__}: {exc})")
                try:
                    distance = float(sa.distToShape(sb)[0])
                    distance = 0.0 if distance < CONTACT_TOL_MM else distance
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"distance not computed ({type(exc).__name__}: {exc})")
            reading = {"common_mm3": common, "distance_mm": distance, "note": "; ".join(notes)}
            pairs.append({"a": {"id": a}, "b": {"id": b}, **reading})
    return {"pairs": pairs, "warnings": []}


def _registered_object_names(doc: reg.DocumentRec) -> set[str]:
    """Every FreeCAD object name the registry knows about (features, sketches, bodies, components)."""
    names: set[str] = set()
    for f in doc.features:
        names.update(f.fc_names)
        if f.fc_main:
            names.add(f.fc_main)
    names.update(s.fc_name for s in doc.sketches.values() if s.fc_name)
    for b in doc.bodies.values():
        names.update(n for n in (b.fc_body, b.result_obj) if n)
    for c in doc.components.values():
        names.update(n for n in (c.fc_part, c.fc_shape, *c.fc_links) if n)
    return names


def _manual_nodes(doc: reg.DocumentRec) -> list[dict[str, Any]]:
    """Model features present in the live document but unknown to the registry: the engineer made them by
    hand in the GUI (only possible with the visible worker). ``kind`` is FreeCAD's type name and the node is
    flagged ``manual`` in its parameters; document order."""
    known = _registered_object_names(doc)
    nodes = []
    for obj in doc.fc_doc.Objects:
        type_id = str(obj.TypeId)
        if obj.Name in known or type_id in MANUAL_FEATURE_SKIP or not type_id.startswith(MANUAL_FEATURE_PREFIXES):
            continue
        nodes.append(
            {
                "handle": {"id": f"feature:manual:{obj.Name}"},
                "kind": type_id,
                "name": str(obj.Label),
                "parameters": {"manual": True, "object": str(obj.Name)},
                "named_dimensions": {},
                "parent": None,
            }
        )
    return nodes


def feature_tree(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The document's model features: the registry's (creation order) followed by hand-made ones."""
    from ops_common import flatten_params  # noqa: PLC0415 - avoids an import cycle at module load

    nodes = []
    for f in doc.features:
        # a feature whose every object was deleted (``remove_feature``) is gone from the tree; its step stays
        if f.fc_names and not any(doc.fc_doc.getObject(n) is not None for n in f.fc_names):
            continue
        nodes.append(
            {
                "handle": {"id": f.id},
                "kind": f.kind,
                "name": f.label,
                "parameters": flatten_params(f.params),
                "named_dimensions": dict(f.named_dimensions),
                "parent": None,
            }
        )
    nodes.extend(_manual_nodes(doc))
    return {"nodes": nodes}


def face_info(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    rec, comp = reg.resolve_face(doc, reg.handle_id(params["face"], op, "face"), op)
    return face_descriptor(doc, rec, comp)


def edge_info(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    rec, comp = reg.resolve_edge(doc, reg.handle_id(params["edge"], op, "edge"), op)
    return edge_descriptor(doc, rec, comp)


# --- sketch status (optional) ----------------------------------------------------------------------------

# Sketcher::SketchObject's own diagnostic surface (stable since FreeCAD 1.0): ``DoF``, ``FullyConstrained``,
# ``GeometryCount``, ``ConstraintCount`` and four read-only lists of constraint indices --
# ``ConflictingConstraints``, ``RedundantConstraints``, ``PartiallyRedundantConstraints``,
# ``MalformedConstraints`` -- map one to one onto ``ConstraintConflict.kind``.
_CONFLICT_ATTRS: tuple[tuple[str, str], ...] = (
    ("ConflictingConstraints", "conflicting"),
    ("RedundantConstraints", "redundant"),
    ("PartiallyRedundantConstraints", "partially_redundant"),
    ("MalformedConstraints", "malformed"),
)


def sketch_status(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Degrees of freedom and constraint diagnostics of a sketch the plan created; read-only, never edits it.

    ``free_geometry`` stays empty here: its indices are those of the IR's entities (a rectangle is one entity and four
    lines of the sketch), which the sketch object does not know. FreeCAD does say which of its own geometry is still
    free (``getGeometryWithDependentParameters``): ``sketch_defined`` names it in the refusal of a feature.
    """
    op = params.get("operation_id")
    sketch_id = reg.handle_id(params["sketch"], op, "sketch")
    rec = reg.resolve_sketch(doc, sketch_id, op)
    if rec.fc_name is None:
        raise invalid_request(f"sketch {sketch_id} has not been consumed by a construction op yet", op)
    sk = doc.fc_doc.getObject(rec.fc_name)
    if sk is None:
        raise invalid_reference(sketch_id, op)
    sk.solve()
    raw_dof = int(sk.DoF)
    conflicts: list[dict[str, Any]] = []
    for attr, kind in _CONFLICT_ATTRS:
        for index in getattr(sk, attr):
            index = int(index)
            constraint = sk.Constraints[index] if 0 <= index < len(sk.Constraints) else None
            involved = []
            if constraint is not None:
                geo_ids = (constraint.First, constraint.Second, constraint.Third)
                involved = sorted({int(g) for g in geo_ids if g is not None and g >= 0})
            conflicts.append(
                {
                    "kind": kind,
                    "constraint_index": index,
                    "constraint_name": (constraint.Name if constraint is not None else "") or "",
                    "involved": involved,
                }
            )
    return {
        "sketch": {"id": sketch_id},
        "dof": max(0, raw_dof),
        "geometry_count": int(sk.GeometryCount),
        "constraint_count": int(sk.ConstraintCount),
        "fully_constrained": bool(sk.FullyConstrained),
        # any reported conflict (conflicting, redundant, partially redundant or malformed) makes the
        # sketch over-constrained, not just an outright contradiction -- see ``conflicts`` below.
        "over_constrained": raw_dof < 0 or bool(conflicts),
        "conflicts": conflicts,
        "free_geometry": [],
        "solver_message": "" if not conflicts else f"{len(conflicts)} constraint diagnostic(s) reported",
    }


def sketch_notes(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The lines ``check`` adds about the sketches behind a feature that are not fully defined (``sketch_defined``).
    Read-only: solving a sketch changes nothing the engineer sees, and the window keeps its "unsaved" flag."""
    with gui.unmodified(doc.fc_doc):
        return {"notes": sketch_defined.notes(doc.fc_doc)}


# --- references -------------------------------------------------------------------------------------------------


def signature(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    entity_id = reg.handle_id(params["entity"], op, "entity")
    return _signature_of(doc, entity_id, op)


def _signature_of(doc: reg.DocumentRec, entity_id: str, op: str | None) -> dict[str, Any]:
    kind = reg.split_component(entity_id)[1].split(":", 1)[0] if "/" in entity_id else entity_id.split(":", 1)[0]
    base: dict[str, Any] = {
        "kind": kind,
        "surface": None,
        "curve": None,
        "normal": None,
        "axis": None,
        "center": None,
        "radius": None,
        "area_mm2": None,
        "length_mm": None,
    }
    if kind == "face":
        rec, comp = reg.resolve_face(doc, entity_id, op)
        d = face_descriptor(doc, rec, comp)
        base.update(surface=d["surface"], normal=d["normal"], axis=d["axis"], center=d["center"], radius=d["radius"])
        base["area_mm2"] = d["area_mm2"]
    elif kind == "edge":
        rec, comp = reg.resolve_edge(doc, entity_id, op)
        d = _edge_geometry(doc, rec, comp)  # a free or non-manifold edge has a signature too: it can be referred to
        center = d["center"]
        if center is None and d["start"] is not None:
            center = g.clean(g.scale(g.add(tuple(d["start"]), tuple(d["end"])), 0.5))
        base.update(curve=d["curve"], axis=d["axis"], center=center, radius=d["radius"], length_mm=d["length_mm"])
    elif kind == "vertex":
        rec, comp = reg.resolve_vertex(doc, entity_id, op)
        base["center"] = vertex_descriptor(doc, rec, comp)["point"]
    elif kind == "body":
        split = reg.split_component(entity_id)
        if split is None:
            body = reg.resolve_body(doc, entity_id, op)
            box = body_descriptor(doc, body)["bbox"]
        else:
            comp = reg.resolve_component(doc, split[0], op)
            if split[1] not in comp.body_solids:
                raise invalid_reference(entity_id, op)
            box = component_body_descriptor(doc, comp, split[1])["bbox"]
        base["center"] = g.clean(g.scale(g.add(tuple(box["min"]), tuple(box["max"])), 0.5))
    elif kind == "comp":
        comp = reg.resolve_component(doc, entity_id, op)
        base["center"] = g.clean(g.vec(reg.component_placement(doc, comp).Base))
    elif kind == "feature":
        doc.feature(entity_id, op)
    else:
        raise invalid_reference(entity_id, op)
    return base


def persistent_reference(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    entity_id = reg.handle_id(params["entity"], op, "entity")
    sig = _signature_of(doc, entity_id, op)
    body = ""
    if sig["kind"] in ("face", "edge", "vertex") and "/" not in entity_id:
        resolver = {"face": reg.resolve_face, "edge": reg.resolve_edge, "vertex": reg.resolve_vertex}[sig["kind"]]
        rec, _ = resolver(doc, entity_id, op)
        body = rec.body
    return {"adapter": "freecad", "token": f"{doc.id}|{body}|{entity_id}", "signature": sig}


def resolve_reference(
    documents: dict[str, reg.DocumentRec], params: dict[str, Any], active: reg.DocumentRec | None = None
) -> dict[str, Any]:
    """A ``persistent_reference`` token (``<doc>|<body>|<entity>``) or a bare entity id of the active document (the id
    the agent is shown for a selected face or edge) back to the live entity id."""
    op = params.get("operation_id")
    ref = params["ref"]
    if ref.get("adapter") != "freecad":
        raise invalid_reference(str(ref.get("token")), op)
    parts = str(ref.get("token", "")).split("|")
    if len(parts) == 1 and active is not None:
        parts = [active.id, "", parts[0]]
    if len(parts) != 3:
        raise invalid_reference(str(ref.get("token")), op)
    doc_id, _, entity_id = parts
    doc = documents.get(doc_id)
    if doc is None:
        raise invalid_reference(str(ref.get("token")), op)
    _signature_of(doc, entity_id, op)  # raises invalid_reference when stale
    return {"entity": {"id": entity_id}}


# --- selection (optional, visible worker only; contract.SelectionProducer seam) ------------------------------------
#
# ``gui.selected_entities`` hands back FreeCAD's own (object name, object label, sub-element name) --
# raw and unstable. The mapping below turns a sub-element back into the harness's own stable id (a live
# ``FaceRec``/``EdgeRec``/``VertexRec``, matched by owning object + element index) so ``persistent_reference``
# can build the same stable ``ref`` any other tool call would; the raw label is kept only for display.

_SUB_ELEMENT_KINDS = {"Face": "face", "Edge": "edge", "Vertex": "vertex"}


def _owner_object_name(doc: reg.DocumentRec, body: reg.BodyRec) -> str | None:
    return body.fc_body if body.kind == "partdesign" else body.result_obj


def _entity_for_sub_element(doc: reg.DocumentRec, obj_name: str, sub_name: str) -> tuple[str, str] | None:
    """``(entity_id, kind)`` for a live face/edge/vertex the harness tracks, or ``None`` when
    ``sub_name`` does not parse as one of those, or ``obj_name`` is not (yet) a body the harness owns
    (e.g. a sketch, a datum, or a component the selection mapping does not resolve today)."""
    prefix = next((p for p in _SUB_ELEMENT_KINDS if sub_name.startswith(p)), None)
    if prefix is None:
        return None
    try:
        index = int(sub_name[len(prefix) :]) - 1
    except ValueError:
        return None
    kind = _SUB_ELEMENT_KINDS[prefix]
    for body in doc.live_bodies():
        if _owner_object_name(doc, body) != obj_name:
            continue
        records = {"face": body.faces, "edge": body.edges, "vertex": body.vertices}[kind]
        for rec in records.values():
            if not rec.stale and rec.index == index:
                return rec.id, kind
    return None


def feature_for_object(doc: reg.DocumentRec, obj_name: str) -> Any | None:
    """The ``FeatureRec`` whose FreeCAD object(s) include ``obj_name`` (``fc_main``/``fc_names``), or
    ``None``. Shared with the live manual-edit notice (``worker/main.py``)."""
    for feature in doc.features:
        if feature.fc_main == obj_name or obj_name in feature.fc_names:
            return feature
    return None


def _selection_entry(doc: reg.DocumentRec, entity_id: str, kind: str, obj_label: str, sub_name: str) -> dict[str, Any]:
    ref = persistent_reference(doc, {"operation_id": "selection", "entity": {"id": entity_id}})
    resolver = {"face": reg.resolve_face, "edge": reg.resolve_edge, "vertex": reg.resolve_vertex}[kind]
    rec, comp = resolver(doc, entity_id, None)
    body_handle: dict[str, str] | None = None
    feature_handle: dict[str, str] | None = None
    owner_label = ""
    if comp is None:
        body_handle = {"id": rec.body}
        # ``VertexRec`` carries neither ``created_by`` nor ``modified_by`` (registry.py): a vertex is
        # never itself the tip of a feature's provenance, only its owning face/edge is, so it is the one
        # kind here that may legitimately have no owning feature to report.
        modified_by = getattr(rec, "modified_by", None)
        feature_id = modified_by[-1] if modified_by else getattr(rec, "created_by", None)
        if feature_id is not None:
            feature_handle = {"id": feature_id}
            feature = next((f for f in doc.features if f.id == feature_id), None)
            owner_label = feature.label if feature is not None else ""
    return {
        "ref": ref,
        "kind": kind,
        "label": f"{obj_label} · {sub_name}",
        "owner_label": owner_label,
        "body": body_handle,
        "feature": feature_handle,
    }


_DATUM_TYPE_IDS = frozenset({"PartDesign::Point", "PartDesign::Line", "PartDesign::Plane"})


def _unresolved_reason(sub_name: str, type_id: str) -> str:
    """Why a selected object/sub-element cannot become a stable ``SelectionEntry`` -- display-only, never
    used to re-identify the selection later."""
    if not sub_name:
        return "whole-object selection"
    if type_id.startswith("Sketcher::"):
        return "sketch"
    if type_id in _DATUM_TYPE_IDS:
        return "datum"
    if type_id == "App::Part" or type_id.startswith("Assembly::"):
        return "assembly component"
    return "unsupported sub-element"


def get_selection(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The live CAD UI selection (``Gui.Selection``), as stable entity refs, plus (``unresolved``) every
    selected item that could not become one (a sketch, a datum, an assembly component, a whole-object
    selection, or a sub-element the harness does not track) -- reported by label + reason instead of
    silently dropped, so the agent/chip can say "1 face · 1 item not usable (sketch)". What was selected on a drawing
    sheet (an item, a view, a line or a point of a view) comes back as ``sheet`` picks (``sheet_select``), and every
    selected face or edge says which items of the sheets refer to it (``on_sheet``). ``not_supported`` headless
    (``capabilities()`` already omits it there, mirroring ``set_view``/``capture``)."""
    import sheet_select  # noqa: PLC0415 - sheet_select imports drawing, which imports this module

    op = params.get("operation_id")
    if not gui.available():
        raise not_supported("get_selection needs the visible worker (FreeCAD GUI binary); this worker is headless", op)
    entries = []
    unresolved = []
    picked: list[tuple[str, str]] = []
    selected = gui.selected_entities(doc.fc_doc.Name)
    for obj_name, obj_label, sub_name, type_id in selected:
        if sheet_select.is_sheet_row((obj_name, obj_label, sub_name, type_id)):
            continue
        found = _entity_for_sub_element(doc, obj_name, sub_name)
        if found is None:
            label = f"{obj_label} · {sub_name}" if sub_name else obj_label
            unresolved.append({"label": label, "reason": _unresolved_reason(sub_name, type_id)})
            continue
        entity_id, kind = found
        entries.append(_selection_entry(doc, entity_id, kind, obj_label, sub_name))
        picked.append((kind, entity_id))
    sheet: list[dict[str, Any]] = []
    try:
        sheet, sheet_unresolved = sheet_select.read_selection(doc, selected)
        unresolved.extend(sheet_unresolved)
        wanted = [(kind, entity_id) for kind, entity_id in picked if kind in ("face", "edge")]
        if wanted and entries:
            found_items = sheet_select.items_referring_to(doc, wanted)
            for entry, (kind, entity_id) in zip(entries, picked, strict=True):
                entry["on_sheet"] = found_items.get(entity_id, []) if kind in ("face", "edge") else []
    except Exception:  # noqa: BLE001 - what the sheets say is extra: the model selection is still right
        sys.stderr.write(traceback.format_exc())
    return {"entries": entries, "unresolved": unresolved, "sheet": sheet}


def _paint_targets(
    doc: reg.DocumentRec, ids: list[str], op: str | None
) -> tuple[list[tuple[Any, str, int]], list[str]]:
    """The ``(object, "face"|"edge", index)`` the 3D highlight paints for the face / edge ids, and the ids it skips."""
    targets: list[tuple[Any, str, int]] = []
    skipped: list[str] = []
    for entity_id in ids:
        prefix = reg.split_component(entity_id)
        local_id = prefix[1] if prefix is not None else entity_id
        kind = local_id.split(":", 1)[0]
        if prefix is not None:
            skipped.append(entity_id)  # assembly-component entity: not supported yet (see ``show_entities``)
            continue
        if kind == "face":
            rec, _comp = reg.resolve_face(doc, entity_id, op)
        elif kind == "edge":
            rec, _comp = reg.resolve_edge(doc, entity_id, op)
        else:
            raise invalid_request(f"show_entities does not support entity kind {kind!r}", op)
        body = reg.resolve_body(doc, rec.body, op)
        obj_name = _owner_object_name(doc, body)
        obj = doc.fc_doc.getObject(obj_name) if obj_name is not None else None
        if obj is None:
            skipped.append(entity_id)
            continue
        targets.append((obj, kind, rec.index))
    return targets, skipped


def show_entities(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Point the engineer at already-resolved entities (the agent-to-engineer
    counterpart of ``get_selection`` above): a TEMPORARY ``ViewObject`` colour, restored on the next call
    (``gui.highlight_entities`` always clears before applying) -- never ``Gui.Selection`` (see
    ``worker/gui.py``'s module note on why). Unlike ``get_selection``/``set_view``/``capture``, a headless
    worker is not an error here: it answers a normal ``{"shown": "unavailable", ...}`` result (the tool
    layer degrades gracefully instead of failing the call). An entity owned by an assembly component is
    not supported yet (v1 scope: plain bodies only, the same simplification ``test_select_entity`` above
    already accepts) and is skipped rather than failing the whole call.

    Besides faces and edges the request may name ``items`` of a drawing sheet (item ids, ``view:<name>``): they are
    painted on the sheet window (``sheet_select`` + ``gui.paint_sheet``, nothing of the document changes), and with
    ``where`` = ``sheet`` / ``3d`` / ``both`` each side also shows what the other side is: the items and view lines
    that refer to the faces, the faces the items refer to."""
    import sheet_select  # noqa: PLC0415 - sheet_select imports drawing, which imports this module

    op = params.get("operation_id")
    clear = bool(params.get("clear", False))
    entities = params.get("entities") or []
    items = [str(i) for i in (params.get("items") or [])]
    where = str(params.get("where") or "auto")
    if not gui.available():
        return {
            "shown": "unavailable",
            "reason": "show_entities needs the visible worker (FreeCAD GUI binary); this worker is headless",
        }
    # a highlight is remembered over the colours of the holes and put back over them: take the old one off first and
    # make the colours of the holes right again before the new one is remembered
    gui.clear_highlight(doc.fc_doc)
    hole_colours.sync(doc)
    ids = [reg.handle_id(entry, op, "entities") for entry in entities]
    faces = [i for i in ids if i.rpartition("/")[2].startswith("face:")]
    edges = [i for i in ids if i.rpartition("/")[2].startswith("edge:")]
    other = [i for i in ids if i not in faces and i not in edges]
    if other:
        raise invalid_request(f"show_entities does not support entity kind {other[0].split(':', 1)[0]!r}", op)
    plan = None
    if items or where in ("sheet", "both"):
        plan = sheet_select.plan(doc, items, faces, edges, where, params.get("sheet"))
    to_paint = ids if plan is None else [*plan.faces, *plan.edges]
    targets, skipped = _paint_targets(doc, to_paint, op)
    detail: list[str] = []
    if targets:
        gui.highlight_entities(doc.fc_doc, targets)  # clears what was shown before, on the sheets too
        detail.append("3D: " + ", ".join(dict.fromkeys(to_paint)))
    elif plan is not None or clear:
        gui.clear_highlight(doc.fc_doc)
    if plan is not None:
        detail += sheet_select.apply(doc, plan)
        detail += plan.notes
    if where == "3d" and targets:
        gui.show_model(doc.fc_doc)  # the faces are what he asked to see: bring the 3D view back in front
    painted = bool(targets) or (plan is not None and any(p.rects or p.paths or p.recolor for p in plan.paints.values()))
    if painted or clear:
        shown, reason = "highlight", None
    else:
        shown = "unavailable"
        reason = "no entity could be shown (assembly components are not supported yet)" if skipped else None
    if skipped and shown == "highlight" and not clear:
        reason = f"{len(skipped)} of {len(to_paint)} entity(ies) skipped (assembly components are not supported yet)"
    return {"shown": shown, "reason": reason, "detail": "; ".join(detail) if painted else ""}


# --- test-only RPC hooks (visible integration suite; not part of the public contract) -------------------------------
#
# ``worker/main.py``'s ``TEST_HOOK_METHODS`` excludes both from ``fc.harness_operation()`` suppression, so
# ``test_edit_property``'s recompute is classified exactly like a real hand edit would be.


def _sub_element_for_entity(doc: reg.DocumentRec, entity_id: str, op: str | None) -> tuple[str, str]:
    """Reverse of ``_entity_for_sub_element``: the ``(object_name, sub_element_name)`` FreeCAD pair for a
    live face/edge/vertex the harness tracks -- lets a test select one by its harness id instead of a
    real mouse click. Component-owned entities are not supported (the same simplification
    ``get_selection`` accepts today)."""
    for resolver in (reg.resolve_face, reg.resolve_edge, reg.resolve_vertex):
        try:
            rec, comp = resolver(doc, entity_id, op)
        except WorkerError:
            continue
        if comp is not None:
            raise not_supported("test_select_entity does not support component-owned entities", op)
        body = reg.resolve_body(doc, rec.body, op)
        obj_name = _owner_object_name(doc, body)
        if obj_name is None:
            raise invalid_reference(entity_id, op)
        return obj_name, rec.sub_name
    raise invalid_reference(entity_id, op)


def test_select_entity(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Select one face/edge/vertex programmatically, as if the engineer had clicked it in the 3D view."""
    op = params.get("operation_id")
    if not gui.available():
        raise not_supported("test_select_entity needs the visible worker", op)
    entity_id = reg.handle_id(params["entity"], op, "entity")
    obj_name, sub_name = _sub_element_for_entity(doc, entity_id, op)
    gui.select(doc.fc_doc.Name, obj_name, sub_name)
    return {}


def test_edit_property(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Set one property on a FreeCAD object by name and recompute, as if the engineer had edited it by
    hand in the property panel."""
    op = params.get("operation_id")
    obj_name = str(params.get("object", ""))
    prop = str(params.get("property", ""))
    obj = doc.fc_doc.getObject(obj_name)
    if obj is None:
        raise invalid_reference(obj_name, op)
    setattr(obj, prop, params.get("value"))
    doc.fc_doc.recompute()
    return {}


def test_hand_edit(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Test-only: one more thing the engineer does by hand in the GUI, outside ``fc.harness_operation()`` like
    ``test_edit_property``. ``what``: ``rename`` (``object`` -> label ``value``), ``add_box`` (a new ``Part::Box``
    called ``object``), ``remove`` (``object``), ``move`` (``object`` to ``value`` = [x, y, z]), ``sketch_line`` (a
    line added to the sketch ``object``), ``undo``, ``redo``, and ``camera`` (turn and zoom the 3D view: not an edit
    of the design at all)."""
    op = params.get("operation_id")
    what = str(params.get("what", ""))
    fc_doc = doc.fc_doc
    name = str(params.get("object", ""))
    obj = fc_doc.getObject(name) if name else None
    if what in ("rename", "remove", "move", "sketch_line") and obj is None:
        raise invalid_reference(name, op)
    if what == "rename":
        obj.Label = str(params.get("value"))
    elif what == "add_box":
        fc_doc.openTransaction("hand add")
        fc_doc.addObject("Part::Box", name)
        fc_doc.commitTransaction()
        fc_doc.recompute()
    elif what == "remove":
        fc_doc.openTransaction("hand remove")
        fc_doc.removeObject(name)
        fc_doc.commitTransaction()
        fc_doc.recompute()
    elif what == "move":
        x, y, z = (float(v) for v in params["value"])
        fc_doc.openTransaction("hand move")
        obj.Placement = App.Placement(App.Vector(x, y, z), App.Rotation())
        fc_doc.commitTransaction()
        fc_doc.recompute()
    elif what == "sketch_line":
        fc_doc.openTransaction("hand sketch")
        obj.addGeometry(Part.LineSegment(App.Vector(1, 1, 0), App.Vector(4, 5, 0)))
        fc_doc.commitTransaction()
        fc_doc.recompute()
    elif what == "undo":
        fc_doc.undo()
        fc_doc.recompute()
    elif what == "redo":
        fc_doc.redo()
        fc_doc.recompute()
    elif what == "camera":
        gui.turn_camera(fc_doc)
    else:
        raise invalid_request(f"test_hand_edit: unknown action {what!r}", op)
    return {}


def test_shape_signature(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Test-only: what a differential test needs to call two documents the same shape -- per live body (by body id) the
    volume, area, exact box, Placement matrix, face/edge/solid counts and the type, size and centre of every face and
    edge in element order (an in-place edit and a rebuild must agree on all of it, including the order)."""

    def rounded(value: float, digits: int) -> float:
        return round(float(value), digits) + 0.0

    bodies: dict[str, Any] = {}
    for body in doc.live_bodies():
        obj = doc.fc_doc.getObject(body.result_obj)
        shape = Part.getShape(obj)
        box = g.exact_box(shape)
        bodies[body.id] = {
            "label": str(obj.Label),
            "volume": float(shape.Volume),
            "area": float(shape.Area),
            "bbox": [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax],
            "placement": list(obj.Placement.toMatrix().A),
            "counts": [len(shape.Faces), len(shape.Edges), len(shape.Solids)],
            "faces": [
                [
                    type(f.Surface).__name__,
                    rounded(f.Area, 6),
                    *(rounded(c, 5) for c in (f.CenterOfMass.x, f.CenterOfMass.y, f.CenterOfMass.z)),
                ]
                for f in shape.Faces
            ],
            "edges": [[type(e.Curve).__name__, rounded(e.Length, 6)] for e in shape.Edges],
        }
    return {"bodies": bodies}


def test_save_document(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Test-only (``tests/integration/freecad``): save the active document to ``path``
    through the REAL ``App::Document.saveAs`` -- the exact path an engineer's Ctrl+S takes, so the visible
    integration suite can verify ``gui.start_highlight_watch``'s ``slotStartSaveDocument`` hook actually
    restores a ``show_entities`` highlight before the write, never a harness-only shortcut that skips it.
    ``plain_save`` calls ``save()`` instead (Ctrl+S once the harness pointed ``FileName`` at its own file). Runs
    outside ``fc.harness_operation`` (``main.TEST_HOOK_METHODS``), like the engineer's own save: the visible
    worker's ``slotFinishSaveDocument`` records it as the engineer's."""
    op = params.get("operation_id")
    if params.get("plain_save"):  # Ctrl+S on a document that already has a FileName: ``Std_Save`` -> ``save()``
        doc.fc_doc.save()
        return {}
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("test_save_document needs a path", op)
    doc.fc_doc.saveAs(path)
    return {}


def test_document_summary(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Test-only: ``document_summary`` of the LIVE active document for the result object of every live body (what a
    reopened file must match, ``test_inspect_saved_document``)."""
    del params
    return document_summary(doc.fc_doc, [body.result_obj for body in doc.live_bodies()])


def test_reopen_document(params: dict[str, Any]) -> dict[str, Any]:
    """Test-only: close ``name`` if it is still open, then open ``path`` fresh through ``App.openDocument``
    -- simulates the engineer closing and reopening the saved file. Deliberately bypasses the harness's own
    registration (``registry.DocumentRec``): a document loaded this way was never built by the executor, so
    it has none of the harness's face/edge handles -- ``test_view_color`` below reads it by raw FreeCAD
    object name instead, exactly what this test needs (whether the SAVED FILE carries the highlight
    colour), never what a real plan session would use."""
    op = params.get("operation_id")
    name = str(params.get("name", ""))
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("test_reopen_document needs a path", op)
    if name and App.getDocument(name) is not None:
        App.closeDocument(name)
    reopened = App.openDocument(path)
    if reopened is None:
        raise invalid_request(f"could not open {path!r}", op)
    return {"document": reopened.Name}


def object_signature(fc_doc: Any, name: str) -> dict[str, Any] | None:
    """What a save round trip must preserve of one object, read by FreeCAD object name (so it also works on a document
    opened from a file, which has no registry): volume, area, exact box, Placement, counts and the type, area and centre
    of every face in element order. ``None`` when the object is absent."""
    obj = fc_doc.getObject(name)
    if obj is None:
        return None
    shape = Part.getShape(obj)
    box = g.exact_box(shape) if not shape.isNull() else None

    def rounded(value: float, digits: int) -> float:
        return round(float(value), digits) + 0.0

    return {
        "type": str(obj.TypeId),
        "volume": rounded(shape.Volume, 6) if not shape.isNull() else 0.0,
        "area": rounded(shape.Area, 6) if not shape.isNull() else 0.0,
        "bbox": [rounded(v, 6) for v in (box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)] if box else [],
        "placement": [rounded(v, 9) for v in obj.Placement.toMatrix().A],
        "counts": [len(shape.Faces), len(shape.Edges), len(shape.Solids)],
        "faces": [
            [
                type(f.Surface).__name__,
                rounded(f.Area, 6),
                *(rounded(c, 5) for c in (f.CenterOfMass.x, f.CenterOfMass.y, f.CenterOfMass.z)),
            ]
            for f in shape.Faces
        ],
    }


def document_summary(fc_doc: Any, names: list[str]) -> dict[str, Any]:
    """Meta, Uid, every object (name, type, state) and the ``object_signature`` of ``names``: what
    ``test_inspect_saved_document`` reports, and what the check suite reads off the live document to compare."""
    return {
        "meta": {str(k): str(v) for k, v in dict(fc_doc.Meta).items()},
        "uid": str(getattr(fc_doc, "Uid", "")),
        "objects": sorted([str(o.Name), str(o.TypeId)] for o in fc_doc.Objects),
        "invalid": sorted(str(o.Name) for o in fc_doc.Objects if "Invalid" in list(o.State)),
        "signatures": {name: object_signature(fc_doc, name) for name in names},
    }


def test_inspect_saved_document(params: dict[str, Any]) -> dict[str, Any]:
    """Test-only (native save): open ``path`` with ``App.openDocument`` WITHOUT a
    recompute, report ``document_summary`` for the object names in ``objects`` and close it again. The caller makes
    sure no open document already holds that file (FreeCAD would hand back the open one instead of loading it)."""
    op = params.get("operation_id")
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("test_inspect_saved_document needs a path", op)
    opened = App.openDocument(path)
    if opened is None:
        raise invalid_request(f"could not open {path!r}", op)
    try:
        return document_summary(opened, [str(n) for n in params.get("objects") or []])
    finally:
        App.closeDocument(opened.Name)


def test_view_color(params: dict[str, Any]) -> dict[str, Any]:
    """Test-only: the raw ``ViewObject`` colour property (``DiffuseColor``/``LineColorArray``, default
    ``DiffuseColor``) of any object in any OPEN document, addressed by FreeCAD's own document/object names
    -- never a harness handle id, so it also works on a document ``test_reopen_document`` just loaded
    (which has no harness registry entries at all). Read from the object the 3D view paints (a PartDesign
    body's Tip: the body's own property is not what is on the screen), the colours in FreeCAD 1.1's
    ``(r, g, b, opacity)``."""
    op = params.get("operation_id")
    doc_name = str(params.get("document", ""))
    obj_name = str(params.get("object", ""))
    prop = str(params.get("property", "DiffuseColor"))
    fc_doc = App.getDocument(doc_name) if doc_name else App.ActiveDocument
    if fc_doc is None:
        raise invalid_reference(doc_name, op)
    obj = fc_doc.getObject(obj_name)
    if obj is None or obj.ViewObject is None:
        raise invalid_reference(obj_name, op)
    return {"value": [list(color) for color in getattr(appearance.paint_object(obj).ViewObject, prop)]}


def _plain(value: Any) -> Any:
    return [_plain(item) for item in value] if isinstance(value, (list, tuple)) else value


def test_view_state(params: dict[str, Any]) -> dict[str, Any]:
    """Test-only (visible suite): display state the view and drawing tools leave behind, by FreeCAD's own names:
    ``what`` = ``property`` (any ``ViewObject`` property of ``object``, e.g. ``Transparency``, ``ShapeColor``,
    ``Visibility``), ``modified`` (whether the window counts the document as changed), ``clip_planes`` (the
    ``SoClipPlane`` nodes of the document's 3D view), ``active_window`` (the class of the MDI window in front) or
    ``open_sheet`` (open the drawing sheet in its own window, as the engineer does with a double click, then report the
    window in front)."""
    op = params.get("operation_id")
    what = str(params.get("what", ""))
    if what == "windows":  # the document windows now open, and per FreeCAD document: whether it is loaded and shown
        gui.settle()
        return {
            "value": {
                "windows": gui.window_titles(),
                "documents": {name: gui.has_window(d) for name, d in App.listDocuments().items()},
            }
        }
    doc_name = str(params.get("document", ""))
    fc_doc = App.getDocument(doc_name) if doc_name else App.ActiveDocument
    if fc_doc is None:
        raise invalid_reference(doc_name, op)
    if what == "property":
        obj = fc_doc.getObject(str(params.get("object", "")))
        if obj is None or obj.ViewObject is None:
            raise invalid_reference(str(params.get("object", "")), op)
        return {"value": _plain(getattr(obj.ViewObject, str(params.get("property", "Visibility"))))}
    if what == "modified":  # the window's own flag: the ``*`` in the title and the "save changes?" question
        return {"value": gui.is_modified(fc_doc)}
    if what == "clip_planes":
        return {"value": gui.clip_planes(fc_doc)}
    if what == "active_window":
        return {"value": gui.active_window_class()}
    if what == "open_sheet":
        import drawing  # noqa: PLC0415 - drawing imports this module

        page = drawing.find_page(fc_doc)
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        return {"value": gui.open_sheet(page)}
    if what == "sheet_png":  # the sheet window as painted now: what the engineer sees
        import drawing  # noqa: PLC0415

        page = drawing.find_page(fc_doc, params.get("sheet"))
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        return {"value": gui.capture_sheet(page, str(params["path"]))}
    if what == "sheet_objects":  # the TechDraw objects of the page: name -> [type, label, key, X, Y]
        import drawing  # noqa: PLC0415

        page = drawing.find_page(fc_doc, params.get("sheet"))
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        return {"value": drawing.page_objects(page)}
    if what == "sheet_exact":  # the labels of the dimensions the page frames as theoretically exact
        import drawing  # noqa: PLC0415

        page = drawing.find_page(fc_doc, params.get("sheet"))
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        return {"value": [str(v.Label) for v in page.Views if getattr(v, "TheoreticalExact", False)]}
    if what == "sheet_weights":  # the ISO 128 line weights now on the page's native objects
        import drawing  # noqa: PLC0415
        import sheet_mirror  # noqa: PLC0415

        page = drawing.find_page(fc_doc, params.get("sheet"))
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        return {"value": sheet_mirror.line_weights(page)}
    if what in ("sheet_cross_check", "sheet_cross_pixels"):  # the centre marks against the projected hole centres
        import drawing  # noqa: PLC0415
        import main  # noqa: PLC0415

        assert main.WORKER.active is not None
        check = drawing.centre_mark_problems if what == "sheet_cross_check" else drawing.centre_mark_pixels
        return {"value": check(main.WORKER.active, params.get("sheet"), op)}
    if what == "sheet_select":  # select page objects / sub-elements as the engineer would: [[object, sub-element], ...]
        gui.select_many(fc_doc.Name, [(str(o), str(sub)) for o, sub in params.get("targets", [])])
        return {"value": gui.selected_entities(fc_doc.Name)}
    if (
        what == "sheet_view_names"
    ):  # the page's view objects by the name the sheet gives them: {"top": "ProjItem001", ...}
        import drawing  # noqa: PLC0415
        import sheet_select  # noqa: PLC0415

        page = drawing.find_page(fc_doc, params.get("sheet"))
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        found = {sheet_select.view_name(o): str(o.Name) for o in sheet_select.page_views(page)}
        return {"value": {k: v for k, v in found.items() if k is not None}}
    if what == "body_object":  # the FreeCAD object that shows the first body (what ``show_entities`` colours)
        import main  # noqa: PLC0415

        assert main.WORKER.active is not None
        return {"value": _owner_object_name(main.WORKER.active, main.WORKER.active.live_bodies()[0])}
    if what == "sheet_pixels":  # how many pixels of the sheet window are the highlight colour (what the engineer sees)
        import drawing  # noqa: PLC0415

        page = drawing.find_page(fc_doc, params.get("sheet"))
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        return {"value": gui.sheet_color_pixels(page)}
    if what == "sheet_dimensions":  # TechDraw's own overall dimensions of the page: [[object name, text], ...]
        import drawing  # noqa: PLC0415
        import sheet_mirror  # noqa: PLC0415

        page = drawing.find_page(fc_doc, params.get("sheet"))
        if page is None:
            raise invalid_request("the document has no drawing sheet", op)
        return {"value": [[o.Name, str(o.getText())] for o in sheet_mirror.natives(page).values()]}
    if what == "sheet_paint":  # [items painted on the sheet windows, objects recoloured]
        return {"value": gui.sheet_paint_state(fc_doc)}
    if what == "sheet_map":  # every line of a view and the model edges it draws (sheet_select.map_view)
        import main  # noqa: PLC0415
        import sheet_select  # noqa: PLC0415

        assert main.WORKER.active is not None
        return {"value": sheet_select.map_view(main.WORKER.active, params.get("sheet"), str(params.get("view", "")))}
    if (
        what == "sheet_metrics"
    ):  # what the quality floor measures on the composed sheet (leaders, positions, material...)
        import drawing  # noqa: PLC0415
        import main  # noqa: PLC0415

        assert main.WORKER.active is not None
        return {"value": drawing.quality_metrics(main.WORKER.active, params.get("sheet"), op)}
    if what == "sheet_compare":  # the window against the sheet model: the differences
        import drawing  # noqa: PLC0415
        import main  # noqa: PLC0415

        assert main.WORKER.active is not None
        return {"value": drawing.compare_window(main.WORKER.active, params.get("sheet"), op)}
    if (
        what == "script"
    ):  # a Python file run in the worker with ``doc`` (the FreeCAD document); its ``RESULT`` comes back
        scope: dict[str, Any] = {"App": App, "doc": fc_doc, "RESULT": None}
        with open(str(params["path"]), encoding="utf-8") as handle:
            exec(compile(handle.read(), str(params["path"]), "exec"), scope)  # noqa: S102 - test hook, never in production
        return {"value": scope["RESULT"]}
    raise invalid_request(f"unknown test_view_state query {what!r}", op)


def test_set_view_color(params: dict[str, Any]) -> dict[str, Any]:
    """Test-only: write a raw ``ViewObject`` colour property directly -- the write-side sibling of
    ``test_view_color`` above, for the visible integration suite to set up a scenario (never used by
    ``gui.py``/``queries.show_entities`` themselves, which always go through ``obj.ViewObject`` in-process)."""
    op = params.get("operation_id")
    doc_name = str(params.get("document", ""))
    obj_name = str(params.get("object", ""))
    prop = str(params.get("property", "DiffuseColor"))
    fc_doc = App.getDocument(doc_name) if doc_name else App.ActiveDocument
    if fc_doc is None:
        raise invalid_reference(doc_name, op)
    obj = fc_doc.getObject(obj_name)
    if obj is None or obj.ViewObject is None:
        raise invalid_reference(obj_name, op)
    value = params.get("value")
    setattr(obj.ViewObject, prop, [tuple(c) for c in value] if isinstance(value, list) else value)
    return {}


# --- undo ----------------------------------------------------------------------------------------------------------


def undo_last(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Revert the newest feature by DELETION (``journal.revert``), never through ``doc.undo()``: FreeCAD keeps at most
    20 undo transactions and ``undo()`` past that silently does nothing, which left the objects of the older ops in the
    document as ghosts while the registry forgot them. The revert is atomic: if it raises, nothing below ran, so the
    registry still describes the (rolled back) document."""
    if not doc.features:
        return {"reverted": None}
    rec = doc.features[-1]
    if rec.journal is None and getattr(rec, "reopened", False):
        # made before the document was reopened (``persist.open_document``): no journal, by design (the undo floor of
        # a resume). Nothing was touched, so this is a plain, recoverable refusal -- not a degraded session.
        raise error(
            "invalid_request",
            f"feature {rec.id} was made before the document was reopened: it has no undo journal (the undo floor of "
            "a resumed session); the document and the registry are unchanged",
            params.get("operation_id"),
            reason="undo_floor",
            feature=rec.id,
        )
    if rec.journal is None:
        raise error(
            "degraded_session",
            f"feature {rec.id} has no undo journal; the document and the registry are unchanged",
            params.get("operation_id"),
        )
    warnings = journal.revert(
        doc.fc_doc, rec.journal, rec.id, params.get("operation_id"), registered=_registered_object_names(doc)
    )
    # From here on the document IS reverted: the registry bookkeeping is done first, so whatever fails below leaves
    # document and registry describing the same (reverted) part. It is never reported as a recoverable error (a retry
    # would revert the NEXT feature): the handles may be stale, so the session degrades.
    doc.features.pop()
    doc.restore(doc.undo_stack.pop())
    try:
        for body in doc.live_bodies():
            body.invalidate()
            if body.is_lazy:  # its records were never built: nothing to re-resolve, its id ranges still hold
                continue
            shape = reg.topo_shape(doc, body)
            if shape.isNull():
                continue
            # the snapshot is older than any in-place edit made since (a plate made thicker slid its top face): a flat
            # face that no longer stands where the snapshot saw it keeps its record, as it does after the edit itself
            follow = frozenset(r.id for r in body.faces.values() if not r.stale and r.sig.surface == "plane")
            reg.refresh(doc, body, shape, rec.id, mark=False, follow=follow)
        for comp in doc.components.values():
            comp._placed = None
    except Exception as exc:  # noqa: BLE001
        raise error(
            "degraded_session",
            f"undo_last reverted feature {rec.id} in the document but refreshing the registry's handles failed "
            f"({type(exc).__name__}: {exc}); face and edge handles may be stale",
            params.get("operation_id"),
            feature=rec.id,
        ) from exc
    # ``warnings`` only when there are some: the reply of a plain undo keeps its original shape
    return {"reverted": {"id": rec.id}, "warnings": warnings} if warnings else {"reverted": {"id": rec.id}}


# --- export -----------------------------------------------------------------------------------------------------


def export_macro(params: dict[str, Any]) -> dict[str, Any]:
    """The recorded API-call script (``fc.recorder``) -- never touches the live document's ``ViewObject``
    state at all (it is text, replayed on a FRESH document later), so a ``show_entities`` highlight can
    never leak into it; no ``gui.clear_highlight`` needed here (contrast ``export_step`` below)."""
    return {"script": fc.recorder.script()}


def export_step(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = params.get("operation_id")
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("export_step needs a path", op)
    gui.clear_highlight(doc.fc_doc)  # a harness-initiated export must never carry a show_entities highlight
    objects = []
    for body in doc.live_bodies():
        obj = doc.fc_doc.getObject(body.result_obj)
        if obj is not None:
            objects.append(obj)
    if not objects and not doc.components:
        raise error("invalid_request", "nothing to export", op)
    try:
        if doc.components:
            # the components are links to other documents: export the shapes the registry measures (one compound)
            shapes = [Part.getShape(obj) for obj in objects]
            shapes += [reg.placed_component_shape(doc, comp) for comp in doc.components.values()]
            Part.makeCompound([s for s in shapes if not s.isNull()]).exportStep(path)
        else:
            Part.export(objects, path)
    except Exception as exc:  # noqa: BLE001
        raise error("internal", f"Part.export failed: {exc}", op) from exc
    return {}


def export_mesh(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The result solids of the document as ONE tessellated STL (``MeshPart`` at ``linear_deflection_mm``)."""
    import Mesh  # noqa: PLC0415 - only a mesh export needs the Mesh modules
    import MeshPart  # noqa: PLC0415

    op = params.get("operation_id")
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("export_mesh needs a path", op)
    deflection = float(params.get("linear_deflection_mm") or 0.1)
    if deflection <= 0:
        raise invalid_request("export_mesh: linear_deflection_mm must be positive", op)
    gui.clear_highlight(doc.fc_doc)
    objects = [doc.fc_doc.getObject(body.result_obj) for body in doc.live_bodies()]
    shapes = [Part.getShape(obj) for obj in objects if obj is not None]
    shapes += [reg.placed_component_shape(doc, comp) for comp in doc.components.values()]  # links: the registry's
    shapes = [shape for shape in shapes if not shape.isNull()]
    if not shapes:
        raise error("invalid_request", "nothing to export", op)
    try:
        mesh = Mesh.Mesh()
        for shape in shapes:
            mesh.addMesh(MeshPart.meshFromShape(Shape=shape, LinearDeflection=deflection, AngularDeflection=0.5))
        mesh.write(path)
    except Exception as exc:  # noqa: BLE001
        raise error("internal", f"mesh export failed: {type(exc).__name__}: {exc}", op) from exc
    return {}


def _outline_frame(face: Any) -> tuple[Any, Any, Any, Any]:
    """(origin, x, y, normal) of a planar face seen from outside: x is the world X (or Y when the face is
    perpendicular to X) projected into the plane, y = normal x x, so the frame is right-handed and nothing mirrors."""
    center = face.CenterOfMass
    u, v = face.Surface.parameter(center)
    normal = face.normalAt(u, v)
    normal.normalize()
    for axis in (App.Vector(1, 0, 0), App.Vector(0, 1, 0)):
        x = axis - normal * axis.dot(normal)
        if x.Length > 1e-6:
            x.normalize()
            return center, x, normal.cross(x), normal
    raise error("internal", "no in-plane axis found for the face")  # unreachable: a unit normal leaves X or Y


def _face_outline_2d(face: Any) -> Any:
    """Every loop of the planar ``face`` as a compound of wires lying in z=0, lower-left corner at the origin."""
    origin, x, y, normal = _outline_frame(face)
    to_world = App.Matrix(
        x.x, y.x, normal.x, origin.x, x.y, y.y, normal.y, origin.y, x.z, y.z, normal.z, origin.z, 0, 0, 0, 1
    )
    flat = face.copy()
    flat.transformShape(to_world.inverse())
    box = flat.BoundBox
    flat.translate(App.Vector(-box.XMin, -box.YMin, -box.ZMin))
    return Part.Compound(list(flat.Wires))


def _write_dxf(shape: Any, path: str, op: str | None) -> None:
    """FreeCAD's own DXF exporter (``importDXF``), run headless on a throwaway document so the live one is never
    touched. Arcs and circles come out as ARC and CIRCLE, straight runs as LINE or LWPOLYLINE, in mm."""
    previous = App.ActiveDocument
    scratch = App.newDocument("__cotaforge_dxf__")
    try:
        holder = scratch.addObject("Part::Feature", "Outline")
        holder.Shape = shape
        import importDXF  # noqa: PLC0415 - pulls Draft: only a DXF export needs it

        importDXF.export([holder], path)
    except Exception as exc:  # noqa: BLE001
        raise error("internal", f"DXF export failed: {type(exc).__name__}: {exc}", op) from exc
    finally:
        App.closeDocument(scratch.Name)
        if previous is not None:
            App.setActiveDocument(previous.Name)


def flat_pattern_face(shape: Any, op: str | None) -> Any:
    """The face that carries the outline of a flat pattern: the largest planar face of the flat solid (it and its
    twin on the other side have the area of the blank; the thickness faces are far smaller)."""
    planar = [] if shape.isNull() else [f for f in shape.Faces if isinstance(f.Surface, Part.Plane)]
    if not planar:
        raise error("internal", "the flat pattern has no planar face to take an outline from", op)
    return max(planar, key=lambda f: f.Area)


def flat_pattern_outline(shape: Any, op: str | None = None) -> Any:
    """Every loop of the flat pattern (blank and holes) as a compound of wires in z=0, lower-left corner at the
    origin: what the cutting shop gets."""
    return _face_outline_2d(flat_pattern_face(shape, op))


def export_dxf(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """DXF of the outline of a planar face (see ``_face_outline_2d``) or of the flat pattern of an ``unfold``."""
    op = params.get("operation_id")
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("export_dxf needs a path", op)
    if params.get("face") is None and params.get("flat_pattern") is None:
        raise invalid_request("export_dxf needs a face or a flat_pattern", op)
    if params.get("flat_pattern") is not None:
        feature = doc.feature(reg.handle_id(params["flat_pattern"], op, "flat_pattern"), op)
        obj = doc.fc_doc.getObject(feature.fc_main) if feature.kind == "unfold" and feature.fc_main else None
        if obj is None:
            raise invalid_request(f"export_dxf: {feature.id} is not an unfold step, it has no flat pattern", op)
        _write_dxf(flat_pattern_outline(obj.Shape, op), path, op)
    else:
        rec, comp = reg.resolve_face(doc, reg.handle_id(params["face"], op, "face"), op)
        face = _face_shape(doc, rec, comp)
        if not isinstance(face.Surface, Part.Plane):
            raise invalid_request(
                f"export_dxf needs a planar face; {rec.id} is a {type(face.Surface).__name__}: pick a flat face", op
            )
        _write_dxf(_face_outline_2d(face), path, op)
    if not os.path.isfile(path):
        raise error("internal", f"DXF export wrote nothing to {path}", op)
    return {}


# --- view (visible worker only) -----------------------------------------------------------------------------------


def _need_gui(what: str, op: str | None) -> None:
    if not gui.available():
        raise not_supported(f"{what} needs the visible worker (FreeCAD GUI binary); this worker is headless", op)


def _body_objects(doc: reg.DocumentRec, params: dict[str, Any], op: str | None) -> list[Any]:
    """The FreeCAD objects of the bodies the request names (handles ``{"id": ...}``)."""
    objects = []
    for handle in params.get("bodies") or []:
        body = reg.resolve_body(doc, reg.handle_id(handle, op, "body"), op)
        obj = doc.fc_doc.getObject(body.result_obj)
        if obj is None:
            raise invalid_reference(body.id, op)
        objects.append(obj)
    return objects


def top_level_objects(doc: reg.DocumentRec) -> list[Any]:
    """The FreeCAD object of every live body and every component: what the document shows and what a drawing draws."""
    objects = [doc.fc_doc.getObject(body.result_obj) for body in doc.live_bodies()]
    objects += [doc.fc_doc.getObject(comp.fc_part) for comp in doc.components.values()]
    return [obj for obj in objects if obj is not None]


def set_view(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Orient the active document's 3D view (a standard name) and/or fit it, all of the model or the named bodies;
    ``not_supported`` headless."""
    op = params.get("operation_id")
    _need_gui("set_view", op)
    name = params.get("name")
    if name is not None and name not in gui.VIEW_METHODS:
        raise invalid_request(f"unknown view {name!r}, expected one of {sorted(gui.VIEW_METHODS)}", op)
    keep = _body_objects(doc, params, op)
    try:
        gui.set_view(doc.fc_doc, name, keep or None, top_level_objects(doc))
    except Exception as exc:  # noqa: BLE001
        raise error("internal", f"set_view failed: {exc}", op) from exc
    return {}


def style_bodies(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Transparency / colour / visibility of bodies in the 3D view (display only); ``not_supported`` headless."""
    op = params.get("operation_id")
    _need_gui("style_bodies", op)
    objects = _body_objects(doc, params, op)
    if not objects:
        raise invalid_request("style_bodies needs at least one body", op)
    color = params.get("color")
    rgb = tuple(int(color[i : i + 2], 16) / 255.0 for i in (1, 3, 5)) if isinstance(color, str) else None
    try:
        gui.style_objects(objects, params.get("transparency"), rgb, params.get("visible"))  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        raise error("internal", f"style_bodies failed: {exc}", op) from exc
    return {}


def section_plane(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """A clipping plane in the 3D view (``axis`` + ``offset_mm``), or none; ``not_supported`` headless."""
    op = params.get("operation_id")
    _need_gui("section_plane", op)
    axis = params.get("axis")
    if axis is not None and axis not in ("x", "y", "z"):
        raise invalid_request(f"unknown axis {axis!r}, expected x, y or z", op)
    try:
        gui.set_section(doc.fc_doc, axis, float(params.get("offset_mm") or 0.0))
    except Exception as exc:  # noqa: BLE001
        raise error("internal", f"section_plane failed: {exc}", op) from exc
    return {}


def capture(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Render the active document's 3D view to ``path`` (PNG by extension); ``not_supported`` headless."""
    op = params.get("operation_id")
    if not gui.available():
        raise not_supported("capture needs the visible worker (FreeCAD GUI binary); this worker is headless", op)
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("capture needs a path", op)
    width = int(params.get("width") or gui.DEFAULT_CAPTURE_SIZE[0])
    height = int(params.get("height") or gui.DEFAULT_CAPTURE_SIZE[1])
    if width <= 0 or height <= 0:
        raise invalid_request(f"capture size must be positive, got {width}x{height}", op)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        gui.capture(doc.fc_doc, path, width, height)
    except Exception as exc:  # noqa: BLE001
        raise error("internal", f"saveImage failed: {exc}", op) from exc
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise error("internal", f"saveImage wrote no image at {path}", op)
    return {}
