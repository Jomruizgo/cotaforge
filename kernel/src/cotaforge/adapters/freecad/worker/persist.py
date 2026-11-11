"""Saving the document: ``save_document`` and the registry dump ``dump``.

The write is atomic and leaves no backup litter:

1. the highlight of ``show_entities`` is restored first (``gui.clear_highlight``; ``slotStartSaveDocument`` also fires
   for ``saveCopy``, this is belt and braces like ``queries.export_step``);
2. the stamp goes into ``Document.Meta`` (``stamp.py``);
3. ``Document.saveCopy`` writes ``.<name>.tmp.FCStd`` in the destination directory. ``saveCopy`` appends ``.FCStd`` to
   any other extension, so the temporary name must already end in it; it does not touch ``FileName`` and makes no
   ``.FCBak`` (``save``/``saveAs`` make a timestamped ``.FCBak`` from the second save on, by FreeCAD's default
   ``CreateBackupFiles`` preference, which is why they are not used);
4. ``os.replace`` onto the final path (retried on ``PermissionError``, Windows' answer when another handle has the
   destination open);
5. ``FileName`` is pointed at the final path, so the engineer's own Ctrl+S in the visible worker writes the same file
   instead of opening "Save as". A document the engineer already saved ELSEWHERE keeps its ``FileName``.

Before overwriting, the file on disk is compared with ``expected_sha256`` (what the harness wrote last): a change the
visible worker saw the engineer make with FreeCAD's own save (``gui.take_engineer_save``) is kept -- the live document
already contains it -- and anything else is a ``conflict``: never overwritten.

``dump`` is the registry without shapes (the ``adapter_state``, schema ``freecad-registry/1``): what reopening needs to
re-bind every handle id to the reopened document by element index and signature. It is never written into the
``.FCStd`` : the harness keeps it next to the file.
"""

import contextlib
import hashlib
import math
import os
import time
from collections.abc import Callable
from typing import Any

import components as comps
import edit
import geometry as g
import gui
import registry as reg
import stamp as stamp_meta
from protocol import error, invalid_request, operation_id_of

STATE_SCHEMA = "freecad-registry/3"
"""``/3`` saves a body that a foreign file brought in and nothing has touched as id ranges (``registry.Pristine``)
instead of a record per face, edge and vertex. ``/2`` (the same, every record listed) is still read. ``/1`` (an early
build, never released) dumped tuples as lists: it cannot be restored exactly, so it is refused like any unknown
schema."""
READABLE_SCHEMAS = ("freecad-registry/2", STATE_SCHEMA)
NATIVE_SUFFIX = ".FCStd"
TMP_MARK = ".tmp"
REPLACE_RETRY_INITIAL_DELAY_S = 0.01
REPLACE_RETRY_BUDGET_S = 1.5

# Test hook (``tests/freecad_worker/check_save_document.py`` only, set and reset in-process): interrupt the save after
# the temporary file is written and before ``os.replace``, to prove a failed write never replaces the previous file.
FAIL_BEFORE_REPLACE = False

_last_written: dict[str, str] = {}  # normalised final path -> sha256 of what this worker last wrote there


# --- the registry dump -----------------------------------------------------------------------------------------------


_TUPLE, _SET, _FLOAT, _VECTOR, _OPAQUE = "__tuple__", "__set__", "__float__", "__vector__", "__opaque__"


def _jsonable(value: Any) -> Any:
    """Plain JSON that ``_restored`` turns back into the SAME Python value (schema ``freecad-registry/2``): a tuple and
    a list differ in the registry (``flatten_params`` prints them differently into the feature tree, which the harness
    fingerprints), so tuples, sets, non-finite floats and FreeCAD vectors are tagged. Anything else is kept as its
    ``repr`` under ``__opaque__`` and reported by the re-bind (never silently guessed)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {_FLOAT: repr(value)}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return {_SET: sorted((_jsonable(v) for v in value), key=repr)}
    if isinstance(value, tuple):
        return {_TUPLE: [_jsonable(v) for v in value]}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if all(hasattr(value, axis) for axis in ("x", "y", "z")):  # FreeCAD Vector
        return {_VECTOR: [float(value.x), float(value.y), float(value.z)]}
    return {_OPAQUE: repr(value)}


def _restored(value: Any, opaque: list[str] | None = None) -> Any:
    """The inverse of ``_jsonable``; an ``__opaque__`` value is collected in ``opaque`` and comes back as ``None``."""
    if isinstance(value, list):
        return [_restored(v, opaque) for v in value]
    if not isinstance(value, dict):
        return value
    if len(value) == 1:
        ((key, inner),) = value.items()
        if key == _TUPLE:
            return tuple(_restored(v, opaque) for v in inner)
        if key == _SET:
            return {_restored(v, opaque) for v in inner}
        if key == _FLOAT:
            return float(inner)
        if key == _VECTOR:
            from FreeCAD import Vector  # noqa: PLC0415 - only when a vector was dumped

            return Vector(*inner)
        if key == _OPAQUE:
            if opaque is not None:
                opaque.append(str(inner))
            return None
    return {k: _restored(v, opaque) for k, v in value.items()}


def _slots(obj: Any) -> dict[str, Any]:
    return {name: _jsonable(getattr(obj, name)) for name in obj.__slots__}


def _measure(doc: reg.DocumentRec, body: reg.BodyRec) -> dict[str, Any] | None:
    """Volume, solids, faces and edges of a live body (no exact box: cheap enough for every checkpoint), what a later
    re-bind compares the reopened body with; ``None`` for a deleted body or one whose shape cannot be read."""
    if body.deleted:
        return None
    try:
        shape = reg.world_shape(doc, body)
        if shape.isNull():
            reading = edit.measure(shape, full=False)
        else:  # the volume is the one measured for the mass properties, not another pass over the shape
            parts = reg.body_volumes(doc, body)
            reading = {
                "volume_mm3": sum(parts),
                "solids": len(parts),
                "faces": shape.countElement("Face"),  # counted, not listed: a wrapper per sub-shape costs memory
                "edges": shape.countElement("Edge"),
            }
    except Exception:  # noqa: BLE001 - the dump must never fail a save over a measurement
        return None
    return {key: reading[key] for key in ("volume_mm3", "solids", "faces", "edges")}


def _cache(body: reg.BodyRec) -> dict[str, Any]:
    """What was measured of an untouched body (its area, the volume and centre of each solid, its box, whether the cheap
    validity check passed). The next session takes it back, instead of measuring thousands of bodies again, only when
    the file it opens is byte for byte the one that was saved with it (``state["file_sha256"]``)."""
    out: dict[str, Any] = {}
    if body._vols is not None:
        out["volumes"] = list(body._vols)
    if body._centres is not None:
        out["centres"] = [list(c) for c in body._centres]
    if body._area is not None:
        out["area"] = _jsonable(body._area)
    for key, box in (("bbox", body._bbox), ("fbox", body._fbox)):
        if box is not None:
            out[key] = [_jsonable(box[0]), box[1]]
    out["valid"] = body._valid
    return out


def _body(doc: reg.DocumentRec, body: reg.BodyRec) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": body.id,
        "measure": _measure(doc, body),
        "kind": body.kind,
        "fc_body": body.fc_body,
        "result_obj": body.result_obj,
        "created_by": body.created_by,
        "modified_by": list(body.modified_by),
        "deleted": body.deleted,
        "sheet_metal": body.sheet_metal,
    }
    spec = body.unchanged_since_adoption()
    if spec is not None:  # the records are made from the shape when asked, with these ids
        out["pristine"] = spec.to_json()
        out["cache"] = _cache(body)
        return out
    out["faces"] = [
        {
            "id": f.id,
            "index": f.index,
            "sig": _slots(f.sig),
            "created_by": f.created_by,
            "modified_by": list(f.modified_by),
            "stale": f.stale,
            "axis_dir": _jsonable(f.axis_dir),
        }
        for f in body.faces.values()
    ]
    out["edges"] = [
        {
            "id": e.id,
            "index": e.index,
            "sig": _slots(e.sig),
            "created_by": e.created_by,
            "faces": list(e.faces),
            "stale": e.stale,
        }
        for e in body.edges.values()
    ]
    out["vertices"] = [
        {"id": v.id, "index": v.index, "point": _jsonable(v.point), "edges": list(v.edges), "stale": v.stale}
        for v in body.vertices.values()
    ]
    return out


_FEATURE_FIELDS = (
    "id",
    "kind",
    "label",
    "params",
    "fc_names",
    "fc_main",
    "named_dimensions",
    "sketch",
    "created_faces",
    "modified_faces",
    "created_edges",
    "created_bodies",
    "modified_bodies",
    "instance_count",
    "component",
    "mate",
    "warnings",
)


def _part_file_of(doc: reg.DocumentRec, comp: reg.ComponentRec) -> str:
    """The file of the part document a component is linked to, read from the link itself (FreeCAD's own truth: it moves
    with a save-as of the part)."""
    for name in comp.fc_links:
        link = doc.fc_doc.getObject(name)
        target = getattr(link, "LinkedObject", None) if link is not None else None
        if target is not None:
            return str(target.Document.FileName or "")
    return ""


def _component(doc: reg.DocumentRec, comp: reg.ComponentRec, final: str) -> dict[str, Any]:
    return {
        "id": comp.id,
        "part_doc": comp.part_doc,
        "part_file": comps.relative_file(_part_file_of(doc, comp), os.path.dirname(final)),
        "fc_part": comp.fc_part,
        "fc_shape": comp.fc_shape,
        "fc_links": list(comp.fc_links),
        "fixed": comp.fixed,
        "created_by": comp.created_by,
        "mates": list(comp.mates),
    }


def dump(doc: reg.DocumentRec, final: str = "") -> dict[str, Any]:
    """The handle registry of ``doc`` without shapes or undo journals: JSON-safe, deterministic for one document. For an
    assembly, ``final`` is the file it is being written to: its components name their part documents relative to it."""
    state = _dump(doc)
    if doc.components:
        state["components"] = [_component(doc, c, final) for c in doc.components.values()]
    if doc.joints:
        state["joints"] = [_joint(j) for j in doc.joints.values()]
    return state


def _joint(joint: reg.JointRec) -> dict[str, Any]:
    return {
        "id": joint.id,
        "joint_type": joint.joint_type,
        "a": joint.a,
        "b": joint.b,
        "axis": None if joint.axis is None else {"origin": list(joint.axis[0]), "direction": list(joint.axis[1])},
        "base": g.transform_from_placement(joint.base),
        "low": joint.low,
        "high": joint.high,
        "value": joint.value,
        "fc_objects": list(joint.fc_objects),
        "created_by": joint.created_by,
    }


LAZY_IDS = "__bodies__"
"""A feature that adopted bodies lazily lists no face or edge ids: they are the ids of those bodies' records."""


def _feature_fields(feature: reg.FeatureRec) -> dict[str, Any]:
    fields = {}
    for name in _FEATURE_FIELDS:
        if feature.lazy_bodies and name in ("created_faces", "created_edges"):
            fields[name] = {LAZY_IDS: list(feature.lazy_bodies)}
        else:
            fields[name] = _jsonable(getattr(feature, name))
    return fields


def _dump(doc: reg.DocumentRec) -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "cad_version": g.freecad_version(),  # a reopen by another version compares the measures more loosely
        "document": {"id": doc.id, "kind": doc.kind, "name": doc.name, "fc_name": str(doc.fc_doc.Name)},
        "counters": dict(sorted(doc.counters.items())),
        "bodies": [_body(doc, b) for b in doc.bodies.values()],
        "sketches": [
            {
                "id": s.id,
                "spec": _jsonable(s.spec),
                "frame": _jsonable(s.frame),
                "profiles": _jsonable(s.profiles),
                "on_face": s.on_face,
                "body": s.body,
                "fc_name": s.fc_name,
                "named_dimensions": _jsonable(s.named_dimensions),
            }
            for s in doc.sketches.values()
        ],
        "datums": [{"id": d.id, "kind": d.kind, "pose": _jsonable(d.pose)} for d in doc.datums.values()],
        "features": [{**_feature_fields(f), "has_journal": f.journal is not None} for f in doc.features],
    }


# --- the save -------------------------------------------------------------------------------------------------


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _replace(src: str, dst: str) -> None:
    delay = REPLACE_RETRY_INITIAL_DELAY_S
    deadline = time.monotonic() + REPLACE_RETRY_BUDGET_S
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay *= 2


def temporary_path(final: str) -> str:
    """``<dir>/.<name>.tmp.FCStd``: same directory (``os.replace`` stays a rename), ends in ``.FCStd`` for
    ``saveCopy``."""
    directory, name = os.path.split(final)
    return os.path.join(directory, f".{name[: -len(NATIVE_SUFFIX)]}{TMP_MARK}{NATIVE_SUFFIX}")


def _io_failure(message: str, op: str | None, final: str, reason: str = "io") -> Exception:
    return error("internal", message, op, reason=reason, path=final)


def save_document(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    op = operation_id_of(params)
    path = str(params.get("path") or "")
    if not path or not os.path.isabs(path):
        raise invalid_request(f"save_document needs an absolute path, got {path!r}", op)
    if not path.lower().endswith(NATIVE_SUFFIX.lower()):
        raise invalid_request(f"save_document writes FreeCAD documents: the path must end in {NATIVE_SUFFIX}", op)
    stamp = params.get("stamp")
    if not isinstance(stamp, dict):
        raise invalid_request("save_document needs a stamp", op)
    if params.get("recovery_copy"):
        return _save_copy(doc, os.path.abspath(path), op, started)
    fc_doc = doc.fc_doc
    final = os.path.abspath(path)
    warnings: list[str] = []
    engineer_saved = gui.take_engineer_save(str(fc_doc.Name), final)
    if os.path.exists(final):
        if not params.get("overwrite", True):
            raise _io_failure(f"{final} already exists and overwrite is false", op, final, "conflict")
        expected = params.get("expected_sha256")
        on_disk = _sha256(final) if expected else None
        # the harness may not have heard about this worker's last write (a reply lost after the replace): what this
        # worker itself wrote there last is never "another program"
        if expected and on_disk != expected and on_disk != _last_written.get(os.path.normcase(final)):
            if not engineer_saved:
                raise _io_failure(
                    f"{final} changed on disk since the harness last saved it, and not by the engineer saving this "
                    "document in FreeCAD: left untouched",
                    op,
                    final,
                    "conflict",
                )
            warnings.append("the engineer saved this document in FreeCAD since the last checkpoint; it is included")

    for comp in doc.components.values():
        if not _part_file_of(doc, comp):
            raise error(
                "invalid_request",
                f"save_document: the part used by {comp.id} has no file: save it first (the assembly names its parts "
                "by file)",
                op,
                reason="part_unsaved",
                path=final,
            )
    gui.clear_highlight(fc_doc)  # never persist a show_entities highlight
    fc_doc.Meta = stamp_meta.merged(dict(fc_doc.Meta), stamp)
    tmp = temporary_path(final)
    state = dump(doc, final)  # before the replace: nothing that can fail runs between the replace and the reply
    try:
        os.makedirs(os.path.dirname(final), exist_ok=True)
        if os.path.exists(tmp):
            os.remove(tmp)
        fc_doc.saveCopy(tmp)
        if not os.path.isfile(tmp):
            raise OSError(f"saveCopy wrote no file at {tmp}")
        sha = _sha256(tmp)
        size = os.path.getsize(tmp)
        if FAIL_BEFORE_REPLACE:
            raise OSError("interrupted before os.replace (test hook)")
        _replace(tmp, final)
        _last_written[os.path.normcase(final)] = sha
        state["file_sha256"] = sha  # lets the next session trust the measures saved with this state
    except Exception as exc:  # noqa: BLE001 - every failure of the write is the same recoverable io failure
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.remove(tmp)
        raise _io_failure(f"save_document could not write {final}: {type(exc).__name__}: {exc}", op, final) from exc

    current = str(fc_doc.FileName or "")
    if not current or _same_path(current, final):
        if current != final:
            try:
                fc_doc.FileName = final
            except Exception as exc:  # noqa: BLE001 - the file is saved either way; Ctrl+S will just ask where
                warnings.append(f"could not point FreeCAD's own save at {final}: {type(exc).__name__}: {exc}")
    elif os.path.normcase(os.path.abspath(current)) in _last_written:
        # a "save as" of the harness itself onto another file: the document now belongs to the new file, and FreeCAD's
        # own save (Ctrl+S) must write there too; the previous file stays as it was last saved
        try:
            fc_doc.FileName = final
        except Exception as exc:  # noqa: BLE001 - the file is saved either way; Ctrl+S will keep writing the old one
            warnings.append(f"could not point FreeCAD's own save at {final}: {type(exc).__name__}: {exc}")
    else:
        warnings.append(
            f"in FreeCAD the engineer saved this document as {current}, a different file than {final}; "
            f"{final} was written as asked, but FreeCAD's own save (Ctrl+S) still writes to {current}"
        )
    gui.mark_saved(fc_doc)  # the file holds what the window shows: no "*" in its title, no "save changes?" on close
    return {
        "path": final,
        "sha256": sha,
        "size_bytes": size,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "document_uid": str(getattr(fc_doc, "Uid", "") or "") or None,
        "cad_version": g.freecad_version(),
        "adapter_state": state,
        "warnings": warnings,
    }


def _save_copy(doc: reg.DocumentRec, final: str, op: str | None, started: float) -> dict[str, Any]:
    """A recovery copy (``SaveDocumentRequest.recovery_copy``): ``saveCopy`` of the live document to ``final``,
    replacing what is there. The document keeps its own file name, its stamp and its modified flag, and no save
    bookkeeping of the harness moves (``_last_written``, the engineer's saves): the engineer's own file is never
    touched, nor is the one FreeCAD's own save (Ctrl+S) writes to."""
    if doc.components:
        raise error("invalid_request", "a recovery copy of an assembly is not made", op, reason="assembly", path=final)
    fc_doc = doc.fc_doc
    gui.clear_highlight(fc_doc)  # never persist a show_entities highlight
    tmp = temporary_path(final)
    state = dump(doc, final)
    try:
        os.makedirs(os.path.dirname(final), exist_ok=True)
        if os.path.exists(tmp):
            os.remove(tmp)
        fc_doc.saveCopy(tmp)
        if not os.path.isfile(tmp):
            raise OSError(f"saveCopy wrote no file at {tmp}")
        sha = _sha256(tmp)
        size = os.path.getsize(tmp)
        _replace(tmp, final)
        state["file_sha256"] = sha
    except Exception as exc:  # noqa: BLE001 - every failure of the write is the same recoverable io failure
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.remove(tmp)
        message = f"the recovery copy could not be written to {final}: {type(exc).__name__}: {exc}"
        raise _io_failure(message, op, final) from exc
    return {
        "path": final,
        "sha256": sha,
        "size_bytes": size,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "document_uid": str(getattr(fc_doc, "Uid", "") or "") or None,
        "cad_version": g.freecad_version(),
        "adapter_state": state,
        "warnings": [],
    }


# --- open_document: reopen a saved file and re-bind the registry ------------------------------------------

VOLUME_REL_TOL = 1e-9
"""The same file loaded by the same FreeCAD gives the same numbers; another FreeCAD version may differ in the last
digits only, which ``VOLUME_REL_TOL_OTHER_VERSION`` allows."""
VOLUME_REL_TOL_OTHER_VERSION = 1e-6
VERTEX_TOL = 1e-6
MANUAL_CREATOR = "feature:manual"


def _corrupt(message: str, op: str | None, path: str) -> Exception:
    return error("internal", message, op, reason="corrupt", path=path)


def _check_zip(path: str, op: str | None) -> None:
    """An ``.FCStd`` is a zip archive: a zero-byte, truncated or garbled file fails here, before FreeCAD is asked to
    load it (FreeCAD may load a damaged archive partially without raising)."""
    import zipfile  # noqa: PLC0415 - only when opening

    if os.path.getsize(path) == 0:
        raise _corrupt(f"{path} is empty (0 bytes)", op, path)
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, EOFError, ValueError) as exc:
        raise _corrupt(f"{path} is not a readable FreeCAD archive ({type(exc).__name__}: {exc})", op, path) from exc
    if bad is not None:
        raise _corrupt(f"{path} is damaged: the archive member {bad} fails its checksum", op, path)
    if "Document.xml" not in names:
        raise _corrupt(f"{path} holds no Document.xml: it is not a FreeCAD document", op, path)


def _face_rec(body_id: str, data: dict[str, Any]) -> reg.FaceRec:
    import provenance as prov  # noqa: PLC0415

    rec = reg.FaceRec(
        data["id"], body_id, int(data["index"]), None, prov.FaceSig(**_restored(data["sig"])), data["created_by"]
    )
    rec.modified_by = list(data["modified_by"])
    rec.stale = bool(data["stale"])
    rec.axis_dir = _restored(data["axis_dir"])
    return rec


def _edge_rec(body_id: str, data: dict[str, Any]) -> reg.EdgeRec:
    import provenance as prov  # noqa: PLC0415

    rec = reg.EdgeRec(
        data["id"], body_id, int(data["index"]), None, prov.EdgeSig(**_restored(data["sig"])), data["created_by"]
    )
    rec.faces = tuple(data["faces"])
    rec.stale = bool(data["stale"])
    return rec


def _vertex_rec(body_id: str, data: dict[str, Any]) -> reg.VertexRec:
    rec = reg.VertexRec(data["id"], body_id, int(data["index"]), _restored(data["point"]))
    rec.edges = list(data["edges"])
    rec.stale = bool(data["stale"])
    return rec


def restore(fc_doc: Any, state: dict[str, Any], doc_id: str | None = None) -> tuple[reg.DocumentRec, list[str]]:
    """The ``DocumentRec`` ``dump`` described, over the loaded ``fc_doc``, WITHOUT shapes (``rebind`` attaches them) and
    without undo journals (the reopen point is the undo floor). Returns it with the values that could not be restored
    exactly (``__opaque__`` in the dump)."""
    opaque: list[str] = []
    info = state["document"]
    doc = reg.DocumentRec(doc_id or str(info["id"]), str(info["kind"]), str(info["name"]), fc_doc)
    doc.counters = {str(k): int(v) for k, v in state["counters"].items()}
    for data in state["bodies"]:
        body = reg.BodyRec(data["id"], data["kind"], data["fc_body"], data["result_obj"], data["created_by"])
        body.modified_by = list(data["modified_by"])
        body.deleted = bool(data["deleted"])
        body.sheet_metal = bool(data.get("sheet_metal", False))
        if "pristine" in data:  # no record was saved: they are made from the shape when asked
            body.pristine = reg.Pristine.from_json(data["pristine"])
            body.doc = doc
            body._lazy = True
        else:
            body.faces = {f["id"]: _face_rec(body.id, f) for f in data["faces"]}
            body.edges = {e["id"]: _edge_rec(body.id, e) for e in data["edges"]}
            body.vertices = {v["id"]: _vertex_rec(body.id, v) for v in data["vertices"]}
        doc.bodies[body.id] = body
    for data in state["sketches"]:
        sketch = reg.SketchRec(
            data["id"],
            _restored(data["spec"], opaque),
            _restored(data["frame"], opaque),
            _restored(data["profiles"], opaque),
        )
        sketch.on_face = data["on_face"]
        sketch.body = data["body"]
        sketch.fc_name = data["fc_name"]
        sketch.named_dimensions = _restored(data["named_dimensions"], opaque)
        doc.sketches[sketch.id] = sketch
    for data in state["datums"]:
        doc.datums[data["id"]] = reg.DatumRec(data["id"], data["kind"], _restored(data["pose"], opaque))
    for data in state["features"]:
        feature = reg.FeatureRec(data["id"], data["kind"], data["label"], _restored(data["params"], opaque))
        for name in _FEATURE_FIELDS[4:]:
            value = data[name]
            if isinstance(value, dict) and LAZY_IDS in value:
                feature.lazy_bodies = [str(b) for b in value[LAZY_IDS]]
                feature.doc = doc
            else:
                setattr(feature, name, _restored(value, opaque))
        feature.journal = None
        feature.reopened = True  # type: ignore[attr-defined] - ``queries.undo_last`` refuses it as the undo floor
        doc.features.append(feature)
    return doc, opaque


def _part_documents(state: dict[str, Any], path: str, documents: dict[str, reg.DocumentRec]) -> dict[str, Any]:
    """Component id -> the open part document of each saved component (found by FILE, not by the id it had when the
    assembly was saved: ids belong to a session) and the files of the parts that are not open."""
    base = os.path.dirname(path)
    found: dict[str, reg.DocumentRec] = {}
    missing: list[str] = []
    for data in state.get("components", ()):
        target = os.path.normpath(os.path.join(base, str(data["part_file"])))
        part = next(
            (d for d in documents.values() if (f := comps.document_file(d)) and comps.same_file(f, target)),
            None,
        )
        if part is None:
            if str(data["part_file"]) not in missing:
                missing.append(str(data["part_file"]))
        else:
            found[str(data["id"])] = part
    return {"found": found, "missing": missing}


def _restore_components(doc: reg.DocumentRec, state: dict[str, Any], parts: dict[str, reg.DocumentRec]) -> None:
    """The components of a reopened assembly, filled from their part documents as they are now (links already live in
    the loaded file)."""
    for data in state.get("components", ()):
        part = parts[str(data["id"])]
        comp = reg.ComponentRec(str(data["id"]), part.id, str(data["fc_part"]), str(data["fc_shape"]), None)
        comp.fc_links = [str(n) for n in data["fc_links"]]
        comp.fixed = bool(data["fixed"])
        comp.created_by = str(data["created_by"])
        comp.mates = [str(m) for m in data["mates"]]
        bodies = comps.populate(comp, part)
        comps.sync_links(doc, comp, part, bodies)  # a body added to (or dropped from) the part since the save
        doc.components[comp.id] = comp
    for data in state.get("joints", ()):
        axis = data.get("axis")
        joint = reg.JointRec(
            str(data["id"]),
            str(data["joint_type"]),
            data["a"],
            str(data["b"]),
            None if axis is None else (g.vec(axis["origin"]), g.vec(axis["direction"])),
            g.placement_from_transform(data["base"]),
            data["low"],
            data["high"],
        )
        joint.value = float(data["value"])
        joint.fc_objects = [str(n) for n in data.get("fc_objects", ())]
        joint.created_by = str(data.get("created_by", ""))
        doc.joints[joint.id] = joint
    if doc.components:
        doc.fc_doc.recompute()


def _measured_alike(saved: dict[str, Any] | None, now: dict[str, Any] | None, rel_tol: float) -> bool:
    if saved is None or now is None:
        return saved is now
    if (saved["solids"], saved["faces"], saved["edges"]) != (now["solids"], now["faces"], now["edges"]):
        return False
    return math.isclose(saved["volume_mm3"], now["volume_mm3"], rel_tol=rel_tol, abs_tol=1e-9)


def _rebind_body(
    doc: reg.DocumentRec,
    body: reg.BodyRec,
    saved_measure: dict[str, Any] | None,
    rel_tol: float,
    cache: dict[str, Any] | None = None,
) -> bool:
    """Attach every live record of ``body`` to the reopened shape BY INDEX and check its signature (the element order
    of a loaded document is the saved one, verified on both FreeCAD lines); True when all of it matched. Otherwise the
    body is re-bound by signature only (``registry.refresh``, the in-session mechanism) and reported as drifted."""
    import provenance as prov  # noqa: PLC0415

    body.invalidate()
    shape = reg.topo_shape(doc, body)
    if shape.isNull():
        return False
    if body.is_lazy and body.pristine is not None:
        return _rebind_pristine(doc, body, shape, saved_measure, rel_tol, cache)
    faces = list(shape.Faces)
    live_faces = [r for r in body.faces.values() if not r.stale]
    live_edges = [r for r in body.edges.values() if not r.stale]
    edge_shapes = list(shape.Edges) if live_edges else []
    live_vertices = [r for r in body.vertices.values() if not r.stale]
    faces_only = not live_edges and not live_vertices  # a big surface body: only its faces have handles
    visible = set() if faces_only else {i for i, _e, _f in reg._visible_edges(shape)}
    points = [] if faces_only else [g.vec(v.Point) for v in shape.Vertexes]
    ok = len(live_faces) == len(faces) and len(live_edges) == len(visible) and len(live_vertices) == len(points)
    for rec in live_faces:
        if not ok:
            break
        if rec.index >= len(faces):
            ok = False
            break
        sig = prov.face_sig(faces[rec.index])
        if not prov.sig_match(rec.sig, sig):
            ok = False
            break
        rec.bind(shape)  # the saved signature stays: the reload may differ in float noise only
    for rec in live_edges:
        if not ok:
            break
        if rec.index not in visible:
            ok = False
            break
        sig = prov.edge_sig(edge_shapes[rec.index])
        if not prov.edge_sig_match(rec.sig, sig):
            ok = False
            break
        rec.bind(shape)
    for rec in live_vertices:
        if not ok:
            break
        if rec.index >= len(points) or g.dist(points[rec.index], rec.point) > VERTEX_TOL:
            ok = False
            break
    if ok and not _measured_alike(saved_measure, _measure(doc, body), rel_tol):
        ok = False
    if not ok:
        for rec in (*body.faces.values(), *body.edges.values()):
            rec.shape = None  # never match a shape that belongs to another index: re-bind by signature alone
        reg.refresh(doc, body, shape, MANUAL_CREATOR, mark=False)
    return ok


def _seed_measures(body: reg.BodyRec, cache: dict[str, Any]) -> None:
    if cache.get("volumes") is not None:
        body._vols = [float(v) for v in cache["volumes"]]
    if cache.get("centres") is not None:
        body._centres = [g.vec(c) for c in cache["centres"]]
    if cache.get("area") is not None:
        body._area = float(_restored(cache["area"]))
    if cache.get("bbox") is not None:
        body._bbox = (_restored(cache["bbox"][0]), int(cache["bbox"][1]))
    if cache.get("fbox") is not None:
        body._fbox = (_restored(cache["fbox"][0]), int(cache["fbox"][1]))
    body._valid = cache.get("valid")


def _rebind_pristine(
    doc: reg.DocumentRec,
    body: reg.BodyRec,
    shape: Any,
    saved_measure: dict[str, Any] | None,
    rel_tol: float,
    cache: dict[str, Any] | None,
) -> bool:
    """A body saved as id ranges (``registry.Pristine``) has no record to attach: the reopened shape has to be the saved
    one (the same numbers of faces, edges and vertices and the same volume), and then the ids are those of the ranges.
    Anything else is drift: the body gets fresh ranges, as an adopted foreign file does."""
    spec = body.pristine
    assert spec is not None
    same_counts = shape.countElement("Face") == spec.n_faces and (
        spec.faces_only
        or (shape.countElement("Edge") == spec.n_edges and shape.countElement("Vertex") == spec.n_vertices)
    )
    if same_counts and cache is not None:  # the file is the saved one: so are its numbers
        _seed_measures(body, cache)
        body.reference = shape
        return True
    if same_counts and _measured_alike(saved_measure, _measure(doc, body), rel_tol):
        body.reference = shape  # what the supplier's file made it: the cheap validity check applies again
        return True
    body._ensure()  # drift: the ranges no longer describe this shape, so it gets new ones
    return False


def _registered_names(doc: reg.DocumentRec) -> set[str]:
    import queries  # noqa: PLC0415 - queries imports this module's neighbours, not this module

    return queries._registered_object_names(doc)


def rebind(doc: reg.DocumentRec, state: dict[str, Any], *, same_version: bool, trusted: bool = False) -> dict[str, Any]:
    import queries  # noqa: PLC0415

    rel_tol = VOLUME_REL_TOL if same_version else VOLUME_REL_TOL_OTHER_VERSION
    measures = {b["id"]: b.get("measure") for b in state["bodies"]}
    caches = {b["id"]: b.get("cache") for b in state["bodies"]} if trusted else {}
    ok: list[str] = []
    drifted: list[str] = []
    unmatched: list[str] = []
    fc_doc = doc.fc_doc
    for name in sorted(_registered_names(doc)):
        if fc_doc.getObject(name) is None:
            unmatched.append(f"object:{name}")
    for body in doc.live_bodies():
        before_stale = (
            set() if body.is_lazy else {r.id for r in (*body.faces.values(), *body.edges.values()) if r.stale}
        )
        if _rebind_body(doc, body, measures.get(body.id), rel_tol, caches.get(body.id)):
            ok.append(body.id)
            continue
        drifted.append(body.id)
        unmatched.extend(
            r.id for r in (*body.faces.values(), *body.edges.values()) if r.stale and r.id not in before_stale
        )
    invalid = sorted(str(o.Name) for o in fc_doc.Objects if {"Invalid", "Error"} & set(o.State))
    foreign = [str(node["parameters"]["object"]) for node in queries._manual_nodes(doc)]
    return {
        "bodies_ok": ok,
        "bodies_drifted": drifted,
        "records_unmatched": unmatched,
        "objects_invalid": invalid,
        "foreign_objects": foreign,
        "notes": [],
    }


def adopt(
    fc_doc: Any,
    doc_id: str,
    objects: list[Any] | None = None,
    progress: Callable[[str, int, int, str], None] | None = None,
) -> tuple[reg.DocumentRec, dict[str, Any]]:
    """The registry of a document this worker knows nothing about (a file the harness never wrote, or one it wrote but
    whose recorded handles are no longer trusted): one body record per solid result of the file (``native_state.
    solid_objects``: a PartDesign body, or a Part object no other solid consumes), re-bound by signature
    (``registry.refresh``) so every face, edge and vertex gets a fresh handle, and one ``adopted`` feature record per
    body that owns them (``faces_of``, ``bodies_of`` and ``feature_tree`` answer for it). The features INSIDE a body
    stay unknown to the registry, so the feature tree lists them as hand-made. Nothing about it can be undone: the
    records carry no undo journal, like any record from before a reopen. ``objects`` names the results to adopt when
    the caller knows them (``foreign.import_geometry``: its bodies may be loose surfaces, which carry no solid).
    ``progress(phase, current, total, message)`` is told before each body."""
    import native_state  # noqa: PLC0415 - only when adopting

    doc = reg.DocumentRec(doc_id, "part", str(fc_doc.Label or fc_doc.Name), fc_doc)
    notes: list[str] = []
    to_adopt = objects if objects is not None else native_state.solid_objects(fc_doc)
    for position, obj in enumerate(to_adopt, start=1):
        if progress is not None:
            progress("adopting", position, len(to_adopt), f"adopting body {position}/{len(to_adopt)}")
        feature = reg.FeatureRec(doc.next_id("feature"), "adopted", str(obj.Label), {"object": str(obj.Name)})
        feature.fc_names = [str(obj.Name)]
        feature.fc_main = str(obj.Name)
        feature.journal = None
        feature.reopened = True  # type: ignore[attr-defined] - ``queries.undo_last`` refuses it as the undo floor
        note = reg.adopt_result(doc, obj, feature)
        if note is not None:
            notes.append(note)
        doc.features.append(feature)
    notes.insert(0, f"adopted without recorded handles: {len(doc.bodies)} solid object(s), handles minted fresh")
    report = {
        "bodies_ok": [],
        "bodies_drifted": [],
        "records_unmatched": [],
        "objects_invalid": sorted(str(o.Name) for o in fc_doc.Objects if {"Invalid", "Error"} & set(o.State)),
        "foreign_objects": [],
        "notes": notes,
    }
    return doc, report


def open_document(
    params: dict[str, Any],
    doc_id_taken: Any,
    next_doc_id: str = "doc:1",
    documents: dict[str, reg.DocumentRec] | None = None,
) -> tuple[reg.DocumentRec, dict[str, Any]]:
    """Load ``path`` (no recompute) and rebuild its registry from ``adapter_state`` -- or, when there is none, ADOPT
    the file (``adopt``) under ``next_doc_id``. ``doc_id_taken(doc_id)`` says whether the worker already holds a
    document with that id (a file saved by an earlier session may have it): this one then gets ``next_doc_id``.
    ``documents`` are the documents the worker holds: an assembly finds its parts among them by file, and the open is
    refused (``parts_missing``) while one is not there. On ANY failure the loaded document is closed again and nothing
    is registered; the file is never written."""
    import fc  # noqa: PLC0415
    import FreeCAD as App  # noqa: PLC0415
    import recorder  # noqa: PLC0415

    started = time.perf_counter()
    op = operation_id_of(params)
    path = str(params.get("path") or "")
    if not path or not os.path.isabs(path):
        raise error("invalid_request", f"open_document needs an absolute path, got {path!r}", op, reason="path")
    if not os.path.isfile(path):
        raise error("invalid_request", f"open_document: {path} does not exist", op, reason="missing", path=path)
    state = params.get("adapter_state")
    adopting = state is None
    if adopting:
        doc_id = next_doc_id
    else:
        if not isinstance(state, dict) or state.get("schema") not in READABLE_SCHEMAS:
            schema = state.get("schema") if isinstance(state, dict) else None
            raise error(
                "invalid_request",
                f"open_document: adapter_state schema {schema!r} is not {STATE_SCHEMA!r}: this worker cannot "
                "re-bind it",
                op,
                reason="adapter_state",
            )
        doc_id = str(state["document"]["id"])
        if doc_id_taken(doc_id):
            doc_id = next_doc_id
    parts: dict[str, reg.DocumentRec] = {}
    if not adopting and state.get("components"):
        needed = _part_documents(state, path, documents or {})
        if needed["missing"]:
            raise error(
                "invalid_request",
                f"open_document: {os.path.basename(path)} uses part documents that are not open: "
                + ", ".join(needed["missing"]),
                op,
                reason="parts_missing",
                path=path,
                parts="|".join(needed["missing"]),
            )
        parts = needed["found"]
    _check_zip(path, op)
    sha = _sha256(path)
    size = os.path.getsize(path)
    before = set(App.listDocuments())
    # A file the engineer already has open (by hand) is his document: FreeCAD hands back that same document, and it is
    # never closed here, not even when this open fails further on.
    borrowed = any(comps.same_file(str(d.FileName or ""), path) for d in App.listDocuments().values() if d.FileName)
    windowless = bool(params.get("hidden")) and gui.ACTIVE and not borrowed
    try:
        fc_doc = App.openDocument(path, True) if windowless else App.openDocument(path)
    except Exception as exc:  # noqa: BLE001 - whatever FreeCAD says, the file cannot be loaded as it is
        for name in set(App.listDocuments()) - before:
            with contextlib.suppress(Exception):
                App.closeDocument(name)
        raise _corrupt(f"FreeCAD cannot load {path}: {type(exc).__name__}: {exc}", op, path) from exc
    try:
        fc_doc.UndoMode = 1
        meta = {str(k): str(v) for k, v in dict(fc_doc.Meta).items() if str(k).startswith(stamp_meta.META_PREFIX)}
        if adopting:
            doc, report = adopt(fc_doc, doc_id)
        else:
            doc, opaque = restore(fc_doc, state, doc_id)
            _restore_components(doc, state, parts)
            saved_version = str(state.get("cad_version") or "")
            same_version = not saved_version or saved_version == g.freecad_version()
            trusted = same_version and bool(state.get("file_sha256")) and state.get("file_sha256") == sha
            report = rebind(doc, state, same_version=same_version, trusted=trusted)
            if opaque:
                report["notes"].append(f"{len(opaque)} recorded value(s) could not be restored exactly: {opaque[:3]}")
        recorder.register_document(fc_doc, doc.id)
        fc.recorder.record(f"docs[{doc.id!r}] = App.openDocument({path!r})")
        doc.borrowed = borrowed
        doc.windowless = windowless
        if not windowless:
            gui.on_new_document(fc_doc)
    except Exception:
        if not borrowed:
            with contextlib.suppress(Exception):
                App.closeDocument(fc_doc.Name)
        raise
    return doc, {
        "document": {"handle": {"id": doc.id}, "kind": doc.kind, "name": doc.name, "units": "mm"},
        "stamp_meta": meta,
        "sha256": sha,
        "size_bytes": size,
        "document_uid": str(getattr(fc_doc, "Uid", "") or "") or None,
        "cad_version": g.freecad_version(),
        "rebind": report,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "warnings": [],
        "borrowed": borrowed,
        "windowless": windowless,
    }
