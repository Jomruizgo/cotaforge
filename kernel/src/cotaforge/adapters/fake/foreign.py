"""``FakeAdapter.import_geometry`` / ``list_holes``: stand-ins for reading a customer's file and for reading its holes.

The "foreign file" of the fake is JSON whatever its suffix (a test writes it): ``{"units": "inch"?, "solids": [{"box":
[sx, sy, sz], "at": [x, y, z]}, ...]}``. Each solid becomes a box at its own coordinates in a NEW part document (the
file's coordinates are kept exactly); the bodies are measured like any other. Reading the holes looks at the cylindrical
faces whose circle edges are concave (a drilled cylinder) and at what closes their ends (a disc as wide as the hole is
a flat bottom).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cotaforge.adapters.fake.model import FakeDocument
from cotaforge.contract import (
    AdapterError,
    Axis,
    BodyHandle,
    BoxRequest,
    DocumentKind,
    ErrorCode,
    ErrorInfo,
    FaceHandle,
    FeatureRequest,
    HoleInfo,
    ImportedBody,
    ImportGeometryRequest,
    ImportGeometryResult,
    ListHolesRequest,
    ListHolesResult,
    NewDocumentRequest,
    ProgressNotice,
    SurfaceType,
    Transform,
)

if TYPE_CHECKING:
    from cotaforge.adapters.fake.adapter import FakeAdapter

SUFFIXES = {".step": "step", ".stp": "step", ".iges": "iges", ".igs": "iges"}


def _failure(message: str, request: ImportGeometryRequest, reason: str, code: ErrorCode) -> AdapterError:
    return AdapterError(
        ErrorInfo(
            code=code,
            message=message,
            operation_id=request.operation_id,
            details={"reason": reason, "path": request.path},
            recoverable=False,
        )
    )


def import_geometry(adapter: FakeAdapter, request: ImportGeometryRequest) -> ImportGeometryResult:
    path = Path(request.path)
    file_format = SUFFIXES.get(path.suffix.lower())
    if file_format is None:
        raise _failure(
            f"import_geometry reads STEP and IGES files, not {path.suffix!r}",
            request,
            "format",
            ErrorCode.invalid_request,
        )
    if not path.is_file():
        raise _failure(f"import_geometry: {path} does not exist", request, "missing", ErrorCode.invalid_request)
    try:
        spec: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        solids = list(spec["solids"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _failure(f"the fake cannot read {path.name}: {exc}", request, "unreadable", ErrorCode.internal) from exc
    if not solids:
        raise _failure(
            f"{path.name} holds no solid, shell or face to import", request, "empty", ErrorCode.invalid_request
        )
    info = adapter.new_document(
        NewDocumentRequest(operation_id=request.operation_id, name=request.name, kind=DocumentKind.part)
    )
    bodies: list[ImportedBody] = []
    adapter.tell_progress(ProgressNotice(phase="reading", message="reading the file"))
    for index, solid in enumerate(solids, start=1):
        adapter.tell_progress(
            ProgressNotice(
                phase="adopting", current=index, total=len(solids), message=f"adopting body {index}/{len(solids)}"
            )
        )
        made = adapter.box(
            BoxRequest(
                operation_id=f"{request.operation_id}-{index}",
                size=tuple(solid["box"]),
                transform=Transform(origin=tuple(solid.get("at", (0.0, 0.0, 0.0)))),
            )
        )
        for body in adapter.bodies_of(FeatureRequest(operation_id=request.operation_id, feature=made.feature)).bodies:
            bodies.append(
                ImportedBody(
                    body=body.handle,
                    feature=made.feature,
                    label=f"solid_{index}",
                    solids=1,
                    shells=1,
                    faces=body.face_count,
                    volume_mm3=body.volume_mm3 or 0.0,  # the fake only makes solids
                    bbox=body.bbox,
                    valid=True,
                    closed=True,
                )
            )
    return ImportGeometryResult(
        document=info, bodies=tuple(bodies), source_format=file_format, source_units=spec.get("units")
    )


def _close(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(a - b) <= tol


def _rim_closed(doc: FakeDocument, face_id: str, edge_id: str) -> bool:
    edge = doc.edges[edge_id]
    other_id = next((f for f in edge.faces if f != face_id), None)
    other = doc.faces.get(other_id) if other_id is not None else None
    face = doc.faces[face_id]
    return (
        other is not None
        and other.surface is SurfaceType.plane
        and other.outer_radius is not None
        and face.radius is not None
        and _close(other.outer_radius, face.radius)
    )


def list_holes(adapter: FakeAdapter, request: ListHolesRequest) -> ListHolesResult:
    doc = adapter._doc(request)
    wanted = request.body.id if request.body is not None else None
    holes: list[HoleInfo] = []
    for face in doc.faces.values():
        if face.surface is not SurfaceType.cylinder or face.axis is None or face.radius is None or face.length is None:
            continue
        if face.body not in doc.bodies or (wanted is not None and face.body != wanted):
            continue
        rims = [doc.edges[e] for e in face.edges if e in doc.edges and doc.edges[e].center is not None]
        if not rims or any(e.convex is not False for e in rims):
            continue  # a convex cylinder (pin, boss), or one the fake knows nothing about
        origin, direction = face.axis.origin, face.axis.direction
        closed = {
            "base": any(
                _rim_closed(doc, face.id, e.id)
                for e in rims
                if math.dist(e.center, origin) < 1e-6  # type: ignore[arg-type]
            ),
            "far": any(
                _rim_closed(doc, face.id, e.id)
                for e in rims
                if math.dist(e.center, origin) >= 1e-6  # type: ignore[arg-type]
            ),
        }
        through: bool | None = None
        axis = Axis(origin=origin, direction=direction)
        if closed["base"] != closed["far"]:
            through = False
            if closed["base"]:  # the open end is the far one: the axis runs from there back into the material
                far = tuple(o + d * face.length for o, d in zip(origin, direction, strict=True))
                axis = Axis(origin=far, direction=tuple(-d for d in direction))
        elif not closed["base"]:
            through = True
        holes.append(
            HoleInfo(
                body=BodyHandle(id=face.body),
                diameter_mm=2.0 * face.radius,
                axis=axis,
                depth_mm=face.length,
                through=through,
                faces=(FaceHandle(id=face.id),),
            )
        )
    return ListHolesResult(holes=tuple(holes))
