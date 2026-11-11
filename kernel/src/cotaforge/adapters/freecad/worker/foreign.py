"""``import_geometry``: a STEP or IGES file becomes a NEW document of reference bodies.

The file is read by FreeCAD's own importer (OpenCASCADE converts the length unit to millimetres and keeps every
coordinate of the file: nothing is recentred). The importer's objects are then replaced by flat ``Part::Feature`` bodies
placed in document coordinates, one per solid -- a STEP assembly arrives as a tree of ``App::Part`` containers whose
placements are folded into each solid. Whatever is not a solid (loose faces, free shells: an IGES often arrives as
thousands of them) goes together into ONE body, a compound called ``surfaces``, so a file of surfaces is still looked at
and measured without a body per face. With ``sew`` the loose faces are first sewn (OpenCASCADE sewing) and every closed
shell that comes out becomes a solid body of its own. The bodies are adopted like the content of any foreign file
(``persist.adopt``): handles minted fresh, no history, nothing editable by an op.

The importer that keeps names, colours and the assembly structure (``Import.insert``) can return nothing, or far fewer
faces, than the plain OpenCASCADE reader (``Part.read``) finds in the same file (an IGES whose entities are only
reachable through a subfigure, blanked or dependent ones). An IGES is read by the plain reader first: when it finds no
solid there is nothing for the importer to name (loose faces all go into the one ``surfaces`` body) and the file is read
once; otherwise the importer reads it too and the plain reader's shape is taken when the importer lost faces
(``prefer_plain_reader``). The two results are never held together. A STEP falls back to the plain reader only when the
importer returns no shape at all.
"""

import contextlib
import gc
import os
import time
from collections.abc import Callable
from typing import Any

import assembly_names
import fc
import FreeCAD as App
import Part
import persist
import registry as reg
from protocol import error, operation_id_of

SUFFIX_FORMAT = {".step": "step", ".stp": "step", ".iges": "iges", ".igs": "iges"}
FORMAT_NAMES = {"step": "STEP", "iges": "IGES"}
DEFAULT_SEW_TOLERANCE_MM = 0.01  # the contract's default (``ImportGeometryRequest``); the worker imports no contract

Progress = Callable[[str, int | None, int | None, str], None]


def _no_progress(phase: str, current: int | None, total: int | None, message: str) -> None:
    """Nobody listens (the check suite, a caller without a channel)."""


PLAIN_ONLY_NOTE = (
    "read with the plain {name} reader: the file holds {faces} loose faces and shells and no solid, so there are no "
    "names, colours or assembly structure to keep"
)
PLAIN_READER_NOTE = (
    "read with the plain {name} reader (the importer that keeps names and assembly structure found {found} of the "
    "{plain} faces in the file): names, colours and assembly structure are not available"
)
LOST_FACES_RATIO = 0.9


def prefer_plain_reader(imported_faces: int, plain_faces: int) -> bool:
    """Take the plain reader's shape instead of the importer's: it finds faces and the importer lost a clear part of
    them (none, or fewer than ``LOST_FACES_RATIO`` of what the plain reader found)."""
    return plain_faces > 0 and imported_faces < LOST_FACES_RATIO * plain_faces


def _faces_of_objects(fc_doc: Any) -> int:
    return sum(o.Shape.countElement("Face") for o in fc_doc.Objects if _carries_shape(o))


def _put_plain(fc_doc: Any, plain: Any, path: str) -> None:
    obj = fc_doc.addObject("Part::Feature", "Plain")
    obj.Label = os.path.splitext(os.path.basename(path))[0]
    obj.Shape = plain


def _read(fc_doc: Any, path: str, file_format: str, op: str | None, notes: list[str]) -> None:
    """Let FreeCAD read the file into ``fc_doc`` with the importer that keeps the product structure, names and colours
    (``Import.insert``), or with the plain reader where the importer has nothing to add or lost faces (see the module
    docstring); ``notes`` says so."""
    name = FORMAT_NAMES[file_format]
    try:
        import Import  # noqa: PLC0415

        plain_faces = 0
        if file_format == "iges":
            plain = Part.read(path)
            plain_faces = plain.countElement("Face")
            if plain.countElement("Solid") == 0 and plain_faces > 0:
                _put_plain(fc_doc, plain, path)
                notes.append(PLAIN_ONLY_NOTE.format(name=name, faces=plain_faces))
                return
            del plain  # decided before the importer reads: the two results are never held together
            gc.collect()
        Import.insert(path, fc_doc.Name)
        imported = _faces_of_objects(fc_doc)
        if file_format == "step" and imported > 0:
            return
        if file_format == "iges" and not prefer_plain_reader(imported, plain_faces):
            return
        # the importer's result goes before the other is read
        _remove_all(fc_doc, [str(o.Name) for o in fc_doc.Objects])
        gc.collect()
        plain = Part.read(path)
        found = plain.countElement("Face")
        if found == 0:
            return
        _put_plain(fc_doc, plain, path)
        notes.append(PLAIN_READER_NOTE.format(name=name, found=imported, plain=found))
    except Exception as exc:  # noqa: BLE001 - whatever FreeCAD says, the file cannot be read as it is
        raise error(
            "internal",
            f"FreeCAD cannot read {os.path.basename(path)}: {type(exc).__name__}: {exc}",
            op,
            reason="unreadable",
            path=path,
        ) from exc


def _carries_shape(obj: Any) -> bool:
    if not obj.isDerivedFrom("Part::Feature"):
        return False
    shape = obj.Shape
    return shape is not None and not shape.isNull()


def _world_shape(obj: Any) -> Any:
    """The object's shape with the placements of every container above it folded in. The copy shares the geometry of the
    importer's object (``copy(False)``): that object is deleted afterwards and a deep copy of a whole assembly would
    double the memory at the peak."""
    shape = obj.Shape.copy(False)
    try:
        shape.Placement = obj.getGlobalPlacement()
    except Exception:  # noqa: BLE001 - no container: the object's own placement is the shape's
        pass
    return shape


def _pieces(shape: Any, label: str, loose: list[Any]) -> tuple[list[tuple[str, Any]], int]:
    """``(label, shape)`` of every solid under ``shape``, and how many edges, wires and vertices (geometry that is not a
    surface or a solid) were left out. Free shells and loose faces are appended to ``loose``; a compound that holds
    nothing but faces (what an IGES of tens of thousands of surfaces is) is appended whole, as it came: a wrapper per
    face and a second compound of them would cost hundreds of megabytes."""
    solids: list[Any] = []
    ignored = 0

    def walk(node: Any) -> None:
        nonlocal ignored
        kind = node.ShapeType
        if kind == "Solid":
            solids.append(node)
        elif kind in ("Shell", "Face"):
            loose.append(node)
        elif kind in ("Compound", "CompSolid"):
            children = node.childShapes()
            if kind == "Compound" and children and all(child.ShapeType == "Face" for child in children):
                loose.append(node)
                return
            for child in children:
                walk(child)
        else:
            ignored += 1

    walk(shape)
    named = [(label if len(solids) == 1 else f"{label}_{index}", solid) for index, solid in enumerate(solids, 1)]
    return named, ignored


def free_edge_count(shape: Any) -> int:
    """Edges that bound exactly one face of ``shape`` (a gap, or the rim of an open shell); a seam is not one."""
    faces = list(shape.Faces)
    edges = list(shape.Edges)
    table = reg.edge_face_table(edges, faces)
    return sum(
        1 for edge, around in zip(edges, table, strict=True) if len(around) == 1 and not edge.isSeam(faces[around[0]])
    )


def joined(parts: list[Any]) -> Any:
    """One compound of ``parts`` (shells, faces and compounds of faces); a lone compound is itself."""
    if len(parts) == 1 and parts[0].ShapeType == "Compound":
        return parts[0]
    return Part.makeCompound(parts)


def sew(loose: list[Any], tolerance_mm: float) -> tuple[list[Any], list[Any], dict[str, Any]]:
    """Sew the loose faces and shells (OpenCASCADE sewing: edges closer than ``tolerance_mm`` are joined). Returns the
    solids made of the closed shells, what is left loose (open shells and free faces) and the report."""
    started = time.perf_counter()
    compound = joined(loose)
    del loose[:]  # the caller's list must not keep the faces that are about to be replaced alive
    faces_in = compound.countElement("Face")
    compound.sewShape(tolerance_mm)
    solids: list[Any] = []
    leftover: list[Any] = []
    shells = 0
    closed = 0
    # a single sewn shell comes back as the shell itself, several as a compound of shells and free faces
    for node in compound.childShapes() if compound.ShapeType == "Compound" else [compound]:
        if node.ShapeType != "Shell":
            leftover.append(node)
            continue
        shells += 1
        if not node.isClosed():
            leftover.append(node)
            continue
        closed += 1
        solid = Part.Solid(node)
        if solid.Volume < 0:  # the shell's faces look inwards
            solid.reverse()
        if solid.Volume > 0 and solid.isValid():
            solids.append(solid)
        else:
            leftover.append(node)
    free = free_edge_count(Part.makeCompound(leftover)) if leftover else 0
    report = {
        "tolerance_mm": tolerance_mm,
        "faces": faces_in,
        "shells": shells,
        "closed_shells": closed,
        "solids": len(solids),
        "free_edges": free,
        "seconds": time.perf_counter() - started,
    }
    return solids, leftover, report


def _remove_all(fc_doc: Any, names: list[str]) -> None:
    """Delete the importer's objects, leaves before the containers that hold them."""
    remaining = list(reversed(names))
    for _ in range(len(remaining) + 1):
        progress = False
        still: list[str] = []
        for name in remaining:
            if fc_doc.getObject(name) is None:
                continue
            try:
                fc_doc.removeObject(name)
                progress = True
            except Exception:  # noqa: BLE001 - a container whose children are not gone yet: the next pass
                still.append(name)
        remaining = still
        if not remaining or not progress:
            return


def _body_facts(doc: reg.DocumentRec, body: reg.BodyRec, obj: Any) -> dict[str, Any]:
    """What the CAD measured of one imported body, each thing once. The volume is the one kept for the mass properties
    (``registry.body_mass``) and the validity the cheap ``isValid()`` (kept for ``queries.is_valid``): the deep BOP
    check of a customer's solid is not ours to run on every body of an assembly."""
    shape = reg.topo_shape(doc, body)
    parts = [] if shape.isNull() else reg.body_volumes(doc, body)
    faces = shape.countElement("Face")
    if reg.validity_unchecked(body, faces, bool(parts)):  # thousands of loose faces: the check outlasts the reading
        valid = None
    else:
        try:
            body._valid = bool(shape.isValid())
        except Exception:  # noqa: BLE001
            body._valid = False
        valid = body._valid
    closed = bool(parts) and all(shell.isClosed() for solid in shape.Solids for shell in solid.Shells)
    box, box_left_out = reg.body_box(doc, body, exact=False)  # the quick box: the exact one waits for a verifier
    exact_box = body._bbox is not None
    return {
        "body": {"id": body.id},
        "feature": {"id": body.created_by},
        "label": str(obj.Label),
        "solids": len(parts),
        "shells": shape.countElement("Shell"),
        "faces": faces,
        "volume_mm3": float(sum(parts)),
        "bbox": box,
        "bbox_exact": exact_box,
        "bbox_faces_left_out": box_left_out,
        "valid": valid,
        "closed": closed,
    }


def import_geometry(
    params: dict[str, Any], doc_id: str, progress: Progress | None = None
) -> tuple[reg.DocumentRec, dict[str, Any]]:
    """Read ``path`` into a new document called ``name`` (id ``doc_id``) and adopt its bodies. On ANY failure the new
    document is closed again and nothing is registered. ``progress(phase, current, total, message)`` hears about the
    phases of the work; each loop over the bodies is linear (the registry, the measures and the validity are done once
    per body, in this order: unpack, build, adopt, measure)."""
    report_progress: Progress = progress if progress is not None else _no_progress
    op = operation_id_of(params)
    path = str(params.get("path") or "")
    name = str(params.get("name") or "")
    if not path or not os.path.isabs(path):
        raise error("invalid_request", f"import_geometry needs an absolute path, got {path!r}", op, reason="path")
    if not os.path.isfile(path):
        raise error("invalid_request", f"import_geometry: {path} does not exist", op, reason="missing", path=path)
    suffix = os.path.splitext(path)[1].lower()
    file_format = SUFFIX_FORMAT.get(suffix)
    if file_format is None:
        raise error(
            "invalid_request",
            f"import_geometry reads {', '.join(sorted(SUFFIX_FORMAT))} files, not {suffix or 'a file without suffix'}",
            op,
            reason="format",
            path=path,
        )
    notes: list[str] = []
    fc_doc = fc.new_document(name, doc_id)
    fc.recorder.record(f"# geometry of {os.path.basename(path)} ({FORMAT_NAMES[file_format]}) read into this document")
    try:
        report_progress("reading", None, None, "reading the file (FreeCAD gives no percentage here)")
        _read(fc_doc, path, file_format, op, notes)
        objects = list(fc_doc.Objects)
        read = [str(o.Name) for o in objects]
        pieces: list[tuple[str, Any]] = []
        loose: list[Any] = []
        ignored = 0
        for position, obj in enumerate(objects, start=1):
            report_progress("preparing", position, len(objects), f"unpacking object {position}/{len(objects)}")
            if not _carries_shape(obj):
                continue
            found, skipped = _pieces(_world_shape(obj), str(obj.Label).strip() or "part", loose)
            pieces.extend(found)
            ignored += skipped
        del objects
        _remove_all(fc_doc, read)  # the importer's objects go before the memory-hungry steps: only `loose` keeps faces
        gc.collect()
        sewing: dict[str, Any] | None = None
        if loose and params.get("sew"):
            tolerance = float(params.get("sew_tolerance_mm") or DEFAULT_SEW_TOLERANCE_MM)
            report_progress(
                "sewing", None, None, f"sewing the loose faces at {tolerance:g} mm (OpenCASCADE gives no percentage)"
            )
            solids, loose, sewing = sew(loose, tolerance)
            pieces.extend((f"solid_{index}", solid) for index, solid in enumerate(solids, start=1))
        if loose:
            pieces.append(("surfaces", joined(loose)))
        del loose
        gc.collect()
        if not pieces:
            raise error(
                "invalid_request",
                f"{os.path.basename(path)} holds no solid, shell or face to import"
                + (f" (only {ignored} edges, wires or points)" if ignored else ""),
                op,
                reason="empty",
                path=path,
            )
        made = []
        for position, (label, shape) in enumerate(pieces, start=1):
            report_progress("building", position, len(pieces), f"creating body {position}/{len(pieces)}")
            obj = fc_doc.addObject("Part::Feature", "Imported")
            obj.Label = label
            obj.Shape = shape
            made.append(obj)
        del pieces
        fc_doc.recompute()
        meta = dict(fc_doc.Meta)
        meta["cotaforge_import.source"] = os.path.basename(path)  # a parts list names the file a bought part came from
        meta["cotaforge_doc.name"] = assembly_names.without_extension(name)
        fc_doc.Meta = meta
        doc, report = persist.adopt(fc_doc, doc_id, made, report_progress)
        by_object = {b.result_obj: b for b in doc.live_bodies()}
        adopted = [(by_object[str(obj.Name)], obj) for obj in made if str(obj.Name) in by_object]
        facts = []
        for position, (body, obj) in enumerate(adopted, start=1):
            report_progress("checking", position, len(adopted), f"checking body {position}/{len(adopted)}")
            body.reference = reg.topo_shape(doc, body)
            facts.append(_body_facts(doc, body, obj))
    except BaseException:
        with contextlib.suppress(Exception):
            App.closeDocument(fc_doc.Name)
        raise
    warnings = list(report["notes"][1:])  # the first note says "adopted without recorded handles": not news here
    if ignored:
        warnings.append(f"{ignored} edge, wire or point object(s) of the file were left out (no surface, no volume)")
    return doc, {
        "document": {"handle": {"id": doc.id}, "kind": doc.kind, "name": doc.name, "units": "mm"},
        "bodies": facts,
        "source_format": file_format,
        "warnings": warnings,
        "notes": notes,
        "sewing": sewing,
    }
