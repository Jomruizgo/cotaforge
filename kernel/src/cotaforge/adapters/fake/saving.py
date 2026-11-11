"""``FakeAdapter.save_document`` / ``open_document``: a deterministic stand-in for a native file.

The "native file" of the fake is canonical JSON: the stamp as flat ``cotaforge.*`` metadata (the same keys the FreeCAD
worker writes into ``Document.Meta``), the document's fingerprint (sorted ids, rounded geometry), its recorded
construction history (``FakeDocument.history``: method, request, id counters) and the tree metadata of every feature.
Same document and same stamp, same bytes. The write mirrors the FreeCAD worker's: a temporary file in the same
directory, then ``os.replace``; a failure removes the temporary file and leaves the previous file (and the live
document) untouched.

Reopening (``open_document``) replays the recorded history into a fresh document with the same ids (the
replay ``edit_feature`` already uses), then applies the tree metadata of the file: a renamed or re-parameterised
feature, or a geometry-less feature added by hand, comes back as the file says. Nothing is written. The re-bind report
compares the reopened document with the ``adapter_state`` returned by the save that wrote the file: a body whose faces
or measured volume differ is ``drifted``, a recorded feature that is gone is ``records_unmatched`` and a feature the
state does not know is a ``foreign_object``. A test edits the JSON to stand in for the engineer editing the part.

Test hooks (attributes of ``FakeAdapter``, never part of the contract): ``save_failure`` raises the given error before
anything is written, ``fail_before_replace`` interrupts the write after the temporary file exists,
``simulate_engineer_save(path)`` stands in for the engineer pressing Ctrl+S on the same document (the next save that
finds the file changed attributes it to the engineer instead of reporting a conflict) and ``cad_version`` is the
version this "CAD" reports (a test changes it to stand in for an upgraded CAD).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cotaforge.adapters.fake.model import FakeDocument, FakeFeature, HistoryEntry
from cotaforge.contract import (
    TOOL_REGISTRY,
    AdapterError,
    DocumentKind,
    DocumentStamp,
    Empty,
    ErrorCode,
    ErrorInfo,
    OpenDocumentRequest,
    OpenDocumentResult,
    RebindReport,
    Request,
    SaveDocumentRequest,
    SaveDocumentResult,
)

if TYPE_CHECKING:
    from cotaforge.adapters.fake.adapter import FakeAdapter

FORMAT = "cotaforge-fake-document/2"
STATE_SCHEMA = "fake-document/2"
_VOLUME_TOL = 1e-6


def _failure(
    message: str,
    request: Request,
    reason: str,
    path: str,
    *,
    code: ErrorCode = ErrorCode.internal,
    extra: dict[str, Any] | None = None,
) -> AdapterError:
    return AdapterError(
        ErrorInfo(
            code=code,
            message=message,
            operation_id=request.operation_id,
            details={"reason": reason, "path": path, **(extra or {})},
            recoverable=code is ErrorCode.internal,
        )
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(doc: FakeDocument) -> list[dict[str, Any]]:
    return [{"id": f.id, "kind": f.kind, "name": f.name, "parameters": dict(f.parameters)} for f in doc.features]


def _payload(doc: FakeDocument, meta: dict[str, str], saved_by: str, parts: dict[str, str] | None = None) -> bytes:
    data = {
        "format": FORMAT,
        "saved_by": saved_by,
        "meta": meta,
        "document": {"id": doc.id, "kind": doc.kind.value, "name": doc.name, "fingerprint": doc.fingerprint()},
        "counters": dict(sorted(doc.counters.items())),
        "history": [
            {
                "method": entry.method,
                "request": entry.request.model_dump(mode="json"),
                "counters": dict(sorted(entry.counters.items())),
                "feature_id": entry.feature_id,
            }
            for entry in doc.history
        ],
        "tree": _tree(doc),
        "attributes": {"material": doc.material, "route": list(doc.route), "roles": dict(doc.roles)},
        # an assembly names the part documents it uses by their file, relative to its own (never by absolute path)
        "parts": dict(sorted((parts or {}).items())),
    }
    return (json.dumps(data, sort_keys=True, indent=1) + "\n").encode("utf-8")


def _state(adapter: FakeAdapter, doc: FakeDocument) -> dict[str, Any]:
    """What a later ``open_document`` compares the reopened document with: per body its faces and measured volume, and
    the feature ids."""
    from cotaforge.adapters.fake.editing import _measure  # noqa: PLC0415 - editing imports this module's neighbours

    measures = _measure(adapter, doc)
    return {
        "schema": STATE_SCHEMA,
        "document": doc.id,
        "counters": dict(sorted(doc.counters.items())),
        "bodies": {
            body_id: {
                "faces": sorted(f for f, face in doc.faces.items() if face.body == body_id),
                "volume_mm3": round(measure.volume_mm3, 6),
            }
            for body_id, measure in sorted(measures.items())
        },
        "features": [f.id for f in doc.features],
    }


def _write_atomically(path: Path, data: bytes, *, interrupt: bool) -> None:
    tmp = path.parent / f".{path.name}.tmp"
    try:
        tmp.write_bytes(data)
        if interrupt:
            msg = "interrupted before the replace (test hook)"
            raise OSError(msg)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def save_document(adapter: FakeAdapter, request: SaveDocumentRequest) -> SaveDocumentResult:
    started = time.perf_counter()
    if request.recovery_copy:
        return _save_copy(adapter, request, started)
    adapter.save_calls.append(request)
    if adapter.save_failure is not None:
        raise AdapterError(adapter.save_failure)
    doc = adapter._doc(request)
    path = Path(request.path)
    if not path.is_absolute():
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.invalid_request,
                message=f"save_document needs an absolute path, got {request.path!r}",
                operation_id=request.operation_id,
                recoverable=False,
            )
        )
    warnings: list[str] = []
    if path.exists():
        if not request.overwrite:
            raise _failure(f"{path} already exists and overwrite is false", request, "conflict", request.path)
        on_disk = _sha256(path)
        mine = adapter._last_written.get(str(path))  # a write whose reply the caller may have lost is not foreign
        if request.expected_sha256 is not None and on_disk not in (request.expected_sha256, mine):
            if str(path) not in adapter._engineer_saves:
                raise _failure(
                    f"{path} changed on disk since the last save and not by the engineer saving this document: "
                    "left untouched",
                    request,
                    "conflict",
                    request.path,
                )
            warnings.append("the engineer saved this document by hand since the last checkpoint; that save is kept")
    adapter._engineer_saves.discard(str(path))
    parts = _part_files(adapter, doc, path, request)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(
            path, _payload(doc, request.stamp.to_meta(), "harness", parts), interrupt=adapter.fail_before_replace
        )
        sha = _sha256(path)
        size = path.stat().st_size
        adapter._last_written[str(path)] = sha
        adapter._doc_paths[doc.id] = str(path)
    except OSError as exc:
        raise _failure(f"save_document could not write {path}: {exc}", request, "io", request.path) from exc
    return SaveDocumentResult(
        path=str(path),
        sha256=sha,
        size_bytes=size,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        document_uid=f"fake:{doc.id}:{doc.name}",
        cad_version=adapter.cad_version,
        adapter_state=_state(adapter, doc),
        warnings=tuple(warnings),
    )


def _save_copy(adapter: FakeAdapter, request: SaveDocumentRequest, started: float) -> SaveDocumentResult:
    """A recovery copy: the file is written, the document keeps its own file and the bookkeeping is untouched."""
    doc = adapter._doc(request)
    path = Path(request.path)
    if not path.is_absolute():
        raise _failure(
            f"save_document needs an absolute path, got {request.path!r}",
            request,
            "path",
            request.path,
            code=ErrorCode.invalid_request,
        )
    if doc.components:
        raise _failure("a recovery copy of an assembly is not made", request, "assembly", request.path)
    adapter.copy_calls.append(request)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(path, _payload(doc, request.stamp.to_meta(), "harness", {}), interrupt=False)
        sha = _sha256(path)
        size = path.stat().st_size
    except OSError as exc:
        raise _failure(f"save_document could not write {path}: {exc}", request, "io", request.path) from exc
    return SaveDocumentResult(
        path=str(path),
        sha256=sha,
        size_bytes=size,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        document_uid=f"fake:{doc.id}:{doc.name}",
        cad_version=adapter.cad_version,
        adapter_state={**_state(adapter, doc), "file_sha256": sha},
    )


def _relative(target: Path, start: Path) -> str:
    return Path(os.path.relpath(target, start)).as_posix()


def _part_files(adapter: FakeAdapter, doc: FakeDocument, path: Path, request: SaveDocumentRequest) -> dict[str, str]:
    """Part document id -> its file relative to ``path``'s folder, for every part an assembly's components use."""
    out: dict[str, str] = {}
    for component in doc.components.values():
        part_path = adapter._doc_paths.get(component.part_id)
        if part_path is None:
            name = component.part.name
            raise _failure(
                f"save_document: the part {name!r} used by {component.id} has no file: save it first",
                request,
                "part_unsaved",
                request.path,
                code=ErrorCode.invalid_request,
                extra={"part": name},
            )
        out[component.part_id] = _relative(Path(part_path), path.parent)
    return out


def simulate_engineer_save(adapter: FakeAdapter, path: Path) -> None:
    """Test hook: the engineer saved the active document to ``path`` by hand (the CAD's own save, not the harness's).
    The file keeps the stamp of the last harness save (Ctrl+S writes the document's own metadata), as FreeCAD does."""
    doc = adapter._doc(Empty(operation_id="engineer_save"))
    meta: dict[str, str] = {}
    with contextlib.suppress(OSError, ValueError, KeyError):
        meta = dict(json.loads(path.read_text(encoding="utf-8"))["meta"])
    path.write_bytes(_payload(doc, meta, "engineer"))
    adapter._engineer_saves.add(str(path))


# --- open_document ------------------------------------------------------------------------------------


def _request_model(method: str) -> type[Request]:
    spec = TOOL_REGISTRY.get(method)
    if spec is None:
        msg = f"unknown recorded method {method!r}"
        raise ValueError(msg)
    return spec.request_model  # type: ignore[return-value]


def _load(path: Path, request: OpenDocumentRequest) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise _failure(
            f"open_document: {path} does not exist", request, "missing", request.path, code=ErrorCode.invalid_request
        ) from exc
    except OSError as exc:
        raise _failure(f"open_document could not read {path}: {exc}", request, "io", request.path) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            msg = f"not a {FORMAT} file"
            raise ValueError(msg)
        for key in ("meta", "document", "history", "tree", "counters"):
            if key not in data:
                msg = f"the {key!r} section is missing"
                raise ValueError(msg)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _failure(
            f"open_document: {path} cannot be loaded (corrupt or partial file: {exc})", request, "corrupt", request.path
        ) from exc
    return data, raw


def _open_parts(
    adapter: FakeAdapter, data: dict[str, Any], path: Path, request: OpenDocumentRequest
) -> dict[str, str]:
    """Old part document id -> the id of the open document that is that file now. A part that is not open is an error
    that names the files, so the caller can open them and try again."""
    mapping: dict[str, str] = {}
    missing: list[str] = []
    open_by_path = {
        os.path.normcase(os.path.abspath(file)): doc_id
        for doc_id, file in adapter._doc_paths.items()
        if doc_id in adapter._documents
    }
    for old_id, relative in (data.get("parts") or {}).items():
        found = open_by_path.get(os.path.normcase(os.path.abspath(path.parent / relative)))
        if found is None:
            missing.append(str(relative))
        else:
            mapping[str(old_id)] = found
    if missing:
        raise _failure(
            f"open_document: {path.name} uses part documents that are not open: {', '.join(missing)}",
            request,
            "parts_missing",
            request.path,
            code=ErrorCode.invalid_request,
            extra={"parts": "|".join(missing)},  # details are scalars; "|" cannot be part of a Windows file name
        )
    return mapping


def _rebuild(
    adapter: FakeAdapter, data: dict[str, Any], request: OpenDocumentRequest, part_ids: dict[str, str]
) -> FakeDocument:
    from cotaforge.adapters.fake.editing import _replay  # noqa: PLC0415 - editing imports the adapter module

    info = data["document"]
    doc_id = str(info["id"])
    if doc_id in adapter._documents:  # a file saved by an earlier session had this id: this one gets a new one
        doc_id = f"doc:{adapter._doc_counter + 1}"
    doc = FakeDocument(id=doc_id, kind=DocumentKind(info["kind"]), name=str(info["name"]))
    try:
        history = [
            HistoryEntry(
                str(entry["method"]),
                _use_parts(_request_model(str(entry["method"])).model_validate(entry["request"]), part_ids),
                {str(k): int(v) for k, v in entry["counters"].items()},
                entry.get("feature_id"),
            )
            for entry in data["history"]
        ]
        doc.counters = {str(k): int(v) for k, v in data["counters"].items()}
        fresh = _replay(adapter, doc, history)
    except AdapterError as exc:
        raise _failure(
            f"open_document: the document in {request.path} does not load ({exc.info.code.value}: {exc.info.message})",
            request,
            "corrupt",
            request.path,
        ) from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise _failure(
            f"open_document: {request.path} cannot be loaded (corrupt or partial file: {exc})",
            request,
            "corrupt",
            request.path,
        ) from exc
    attributes = data.get("attributes") or {}
    fresh.material = attributes.get("material") or None
    fresh.route = tuple(attributes.get("route") or ())
    fresh.roles = dict(attributes.get("roles") or {})
    by_id = {f.id: f for f in fresh.features}
    for node in data["tree"]:
        feature = by_id.get(str(node["id"]))
        if feature is None:  # made by hand: no geometry the replay could have produced
            fresh.features.append(
                FakeFeature(
                    id=str(node["id"]),
                    kind=str(node["kind"]),
                    name=str(node["name"]),
                    parameters=dict(node["parameters"]),
                )
            )
            fresh.hand_edited = True
            continue
        if (feature.kind, feature.name, feature.parameters) != (node["kind"], node["name"], node["parameters"]):
            feature.kind, feature.name, feature.parameters = (
                str(node["kind"]),
                str(node["name"]),
                dict(node["parameters"]),
            )
            fresh.hand_edited = True
    return fresh


def _use_parts(recorded: Request, part_ids: dict[str, str]) -> Request:
    """An ``insert_component`` request names its part by the id it had when it was saved: the part open now."""
    part = getattr(recorded, "part", None)
    if part is not None and part.id in part_ids:
        return recorded.model_copy(update={"part": part.model_copy(update={"id": part_ids[part.id]})})
    return recorded


def _rebind(adapter: FakeAdapter, doc: FakeDocument, recorded: dict[str, Any]) -> RebindReport:
    now = _state(adapter, doc)
    ok: list[str] = []
    drifted: list[str] = []
    unmatched: list[str] = []
    for body_id, was in sorted(recorded.get("bodies", {}).items()):
        current = now["bodies"].get(body_id)
        if current is None:
            unmatched.append(body_id)
            continue
        missing = sorted(set(was["faces"]) - set(current["faces"]))
        unmatched.extend(missing)
        if current["faces"] != was["faces"] or abs(current["volume_mm3"] - was["volume_mm3"]) > _VOLUME_TOL:
            drifted.append(body_id)
        else:
            ok.append(body_id)
    known = set(recorded.get("features", []))
    unmatched.extend(f for f in recorded.get("features", []) if f not in {g.id for g in doc.features})
    foreign = tuple(f.name for f in doc.features if f.id not in known)
    drifted.extend(b for b in now["bodies"] if b not in recorded.get("bodies", {}))
    return RebindReport(
        bodies_ok=tuple(ok),
        bodies_drifted=tuple(drifted),
        records_unmatched=tuple(unmatched),
        foreign_objects=foreign,
    )


def open_document(adapter: FakeAdapter, request: OpenDocumentRequest) -> OpenDocumentResult:
    started = time.perf_counter()
    adapter.open_calls.append(request)
    path = Path(request.path)
    if not path.is_absolute():
        raise _failure(
            f"open_document needs an absolute path, got {request.path!r}",
            request,
            "path",
            request.path,
            code=ErrorCode.invalid_request,
        )
    if request.adapter_state is not None and request.adapter_state.get("schema") != STATE_SCHEMA:
        raise _failure(
            f"open_document: adapter_state schema {request.adapter_state.get('schema')!r} is not {STATE_SCHEMA!r}: "
            "this adapter cannot re-bind it",
            request,
            "adapter_state",
            request.path,
            code=ErrorCode.invalid_request,
        )
    data, raw = _load(path, request)
    try:
        stamp = DocumentStamp.from_meta({str(k): str(v) for k, v in data["meta"].items()})
    except (ValueError, TypeError) as exc:
        raise _failure(
            f"open_document: the stamp in {path} is unreadable: {exc}", request, "corrupt", request.path
        ) from exc
    part_ids = _open_parts(adapter, data, path, request)
    doc = _rebuild(adapter, data, request, part_ids)
    if request.adapter_state is None:  # adopted: nothing was recorded, so there is nothing to compare with
        report = RebindReport(notes=("adopted without recorded handles",))
    else:
        report = _rebind(adapter, doc, request.adapter_state)
    adapter._documents[doc.id] = doc
    adapter._doc_paths[doc.id] = str(path)
    adapter._active = doc
    borrowed = str(path) in adapter.borrowed_paths
    number = int(doc.id.rsplit(":", 1)[1]) if doc.id.rsplit(":", 1)[-1].isdigit() else 0
    adapter._doc_counter = max(adapter._doc_counter, number)
    return OpenDocumentResult(
        document=adapter._document_info(doc),
        stamp=stamp,
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        document_uid=f"fake:{doc.id}:{doc.name}",
        cad_version=adapter.cad_version,
        rebind=report,
        duration_ms=(time.perf_counter() - started) * 1000.0,
        borrowed=borrowed,
        windowless=request.hidden and not borrowed,
    )
