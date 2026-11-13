"""FakeAdapter: the in-memory analytic adapter.

It implements every required method of ``cotaforge.contract.Adapter`` against the
dataclasses of ``model.py`` using the builders of ``builders.py``. It imports only
``cotaforge.contract`` (and the package version).
"""

from __future__ import annotations

import copy
import functools
import math
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from cotaforge import __version__
from cotaforge.adapters.fake import assembly as asm
from cotaforge.adapters.fake import editing, foreign, saving, sheetmetal
from cotaforge.adapters.fake.booleans import as_box, intersection_primitive
from cotaforge.adapters.fake.builders import (
    Build,
    axial_range_from,
    build_chamfer,
    build_cylinder,
    build_fillet,
    build_prism,
    build_revolution,
    build_shell,
    build_split,
    build_sweep,
    circle_within_face,
    circular_instances,
    classify_face_coverage,
    clone_body,
    clone_feature,
    face_footprint_uv,
    find_planar_face,
    invalid_request,
    linear_instances,
    merge_extrude_covering_host_face,
    not_supported,
    orient_ccw,
    positive_bbox,
    profile_touches_face,
    rect_points,
    reflection_transform,
)
from cotaforge.adapters.fake.faults import Fault, FaultEngine, FaultKind, FaultPlan
from cotaforge.adapters.fake.model import (
    TOLERANCE,
    FakeComponent,
    FakeDatumAxis,
    FakeDatumPlane,
    FakeDocument,
    FakeEdge,
    FakeFace,
    FakeFeature,
    FakeJoint,
    FakeSketch,
    FakeVertex,
    HistoryEntry,
    Primitive,
    PrimitiveKind,
    bbox_union,
    sketch_dof_report,
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
    MAX_SWEEP_POSITIONS,
    Ack,
    Adapter,
    AdapterError,
    AddJointRequest,
    AnyHandle,
    ApiValidation,
    Arc3Entity,
    ArcEndsEntity,
    ArcEntity,
    Axis,
    BaseFlangeRequest,
    BodiesResult,
    BodyHandle,
    BodyInfo,
    BodyInterferenceRequest,
    BodyInterferenceResult,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CanaryResult,
    Capabilities,
    ChamferRequest,
    CircleEntity,
    CircularPatternRequest,
    ComponentHandle,
    ComponentRequest,
    ConnectRequest,
    CopyBodyRequest,
    CurveType,
    CutRequest,
    CylinderRequest,
    DatumAxisHandle,
    DatumAxisRequest,
    DatumAxisResult,
    DatumPlaneHandle,
    DatumPlaneRequest,
    DatumPlaneResult,
    DatumPlaneTarget,
    DesignAttributes,
    Direction,
    DocumentHandle,
    DocumentInfo,
    DocumentKind,
    EdgeFlangeRequest,
    EdgeHandle,
    EdgeInfo,
    EdgeRequest,
    EdgesBetweenRequest,
    EdgesResult,
    EditFeatureRequest,
    EditResult,
    Empty,
    EndCondition,
    EntityRequest,
    EntityResult,
    ErrorCode,
    ErrorInfo,
    ExtrudeRequest,
    FaceHandle,
    FaceInfo,
    FaceRequest,
    FacesResult,
    FeatureHandle,
    FeatureNode,
    FeatureRequest,
    FeatureTree,
    FilletRequest,
    FixComponentRequest,
    GeometricSignature,
    HoleRequest,
    ImportGeometryRequest,
    ImportGeometryResult,
    InsertComponentRequest,
    InterferencePair,
    InterferenceRequest,
    InterferenceResult,
    JointHandle,
    JointInfo,
    JointRequest,
    JointSweepClash,
    JointSweepPair,
    JointSweepResult,
    JointType,
    LinearPatternRequest,
    LineEntity,
    ListHolesRequest,
    ListHolesResult,
    ManualEditListener,
    ManualEditNotice,
    MassProperties,
    MassRequest,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    MateHandle,
    MeasureBodiesResult,
    MirrorBodyRequest,
    MirrorRequest,
    MoveBodyRequest,
    NewDocumentRequest,
    OpenDocumentRequest,
    OpenDocumentResult,
    OperationResult,
    PersistentRef,
    PlaceComponentRequest,
    PlaneName,
    Profile,
    ProgressListener,
    ProgressNotice,
    Provenance,
    ProvenanceRole,
    RectEntity,
    Request,
    ResolveRequest,
    RevolveRequest,
    SaveDocumentRequest,
    SaveDocumentResult,
    SelectionEntry,
    SelectionListener,
    SelectionResult,
    SessionInfo,
    SetActiveDocumentRequest,
    SetDesignAttributesRequest,
    SetJointRequest,
    SetTimeoutRequest,
    SheetPick,
    SheetRef,
    ShellRequest,
    ShowEntitiesRequest,
    ShowEntitiesResult,
    Side,
    SketchFrame,
    SketchHandle,
    SketchRequest,
    SketchResult,
    SketchStatusRequest,
    SketchStatusResult,
    SlotEntity,
    SplitRequest,
    SquareEntity,
    SurfaceType,
    SweepJointRequest,
    SweepRequest,
    Transform,
    UndoResult,
    UnfoldRequest,
    UnresolvedSelection,
    ValidityRequest,
    ValidityResult,
    Vec3,
    VertexAtRequest,
    VertexHandle,
    VertexInfo,
    VerticesResult,
    arc_geometry,
    frame_for_face,
    frame_for_plane,
    has_nested,
    sketch_profiles,
    sweep_values,
)


def _recorded[F: Callable[..., Any]](method: F) -> F:
    """Record a mutating call in ``FakeDocument.history`` once it succeeds (``edit_feature`` replays the history). Only
    the outermost call is recorded, with the id counters as they were before it; nothing is recorded while a replay runs
    or when the call raises."""

    @functools.wraps(method)
    def wrapper(self: FakeAdapter, request: Request) -> Any:
        doc = self._active
        counters = dict(doc.counters) if doc is not None else {}
        self._depth += 1
        try:
            result = method(self, request)
        finally:
            self._depth -= 1
        if doc is not None and self._depth == 0 and not self._replaying:
            feature = getattr(result, "feature", None)
            doc.history.append(
                HistoryEntry(method.__name__, request, counters, feature.id if feature is not None else None)
            )
        return result

    return wrapper  # type: ignore[return-value]


_ZERO_BBOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(0.0, 0.0, 0.0))
_ORIGIN: Vec3 = (0.0, 0.0, 0.0)
_HANDLE_CLASSES: dict[str, type[AnyHandle]] = {
    "face": FaceHandle,
    "edge": EdgeHandle,
    "vertex": VertexHandle,
    "body": BodyHandle,
    "feature": FeatureHandle,
    "comp": ComponentHandle,
}


class FakeAdapter(Adapter):
    name = "fake"
    document_suffix = ".fakecad.json"

    def __init__(
        self, faults: FaultPlan | None = None, *, deterministic_ids: bool = True, sheet_metal: bool = True
    ) -> None:
        self._sheet_metal = sheet_metal
        """Whether this fake has its sheet-metal capability (a test switches it off to play a CAD whose add-on is
        not installed)."""
        self._faults = FaultEngine(faults)
        self._documents: dict[str, FakeDocument] = {}
        self._doc_paths: dict[str, str] = {}  # document id -> the file it was last saved to or opened from
        self._active: FakeDocument | None = None
        self._deterministic_ids = deterministic_ids
        self._doc_counter = 0
        self._timeout_s = 60.0
        self._connected = False
        self._last_fired: tuple[Fault, ...] = ()
        self._selection: tuple[AnyHandle, ...] = ()
        self._selection_labels: dict[str, str] = {}
        self._selection_unresolved: tuple[UnresolvedSelection, ...] = ()
        self._selection_listener: SelectionListener | None = None
        self._manual_edit_listener: ManualEditListener | None = None
        self._progress_listener: ProgressListener | None = None
        self._depth = 0  # nesting of the recorded mutating calls (only the outermost is recorded)
        self._replaying = False  # True while ``edit_feature`` replays a document: nothing is recorded
        # ``show_entities`` test double state: ``visible`` stands in for a real
        # adapter's visible-GUI-vs-headless distinction (FreeCAD: ``visible=True`` at construction; here
        # a plain public flag a test flips directly). ``highlighted`` is the id set the most recent call
        # actually painted -- always replaced, never merged, mirroring "cleared on the next call".
        self.visible: bool = True
        self.highlighted: dict[str, AnyHandle] = {}
        self.show_entities_calls: list[ShowEntitiesRequest] = []
        self.sheet_selection: tuple[SheetPick, ...] = ()  # test double: what the engineer selected on a drawing sheet
        self.on_sheet: dict[str, tuple[SheetRef, ...]] = {}  # test double: the sheet items that refer to an entity id
        self.highlighted_items: tuple[str, ...] = ()  # test double: the sheet items the last show_entities pointed at
        # ``save_document`` test hooks (``adapters.fake.saving``): every request seen, a scripted failure, an
        # interruption between the temporary file and the replace, and the paths the "engineer" saved by hand.
        self.save_calls: list[SaveDocumentRequest] = []
        self.copy_calls: list[SaveDocumentRequest] = []  # the recovery copies (``SaveDocumentRequest.recovery_copy``)
        self.save_failure: ErrorInfo | None = None
        self.fail_before_replace = False
        self._engineer_saves: set[str] = set()
        self._last_written: dict[str, str] = {}  # path -> sha256 of what this adapter wrote there last
        # ``open_document``: every request seen, and the version this "CAD" reports (a test changes it to
        # stand in for an upgraded CAD reopening an older file).
        self.open_calls: list[OpenDocumentRequest] = []
        self.cad_version = "fake"
        # Windows: the files the "engineer" already has open (opening one reports ``borrowed``), the activations asked
        # for and the documents closed (so a test sees what the session closed and what it left alone).
        self.borrowed_paths: set[str] = set()
        self.activate_calls: list[SetActiveDocumentRequest] = []
        self.closed_ids: list[str] = []

    def profile_hints(self) -> tuple[str, ...]:
        return (
            "detail: sketch + extrude/cut, hole; sketch (concept model): box, cylinder and booleans are fine; the fake "
            "is an analytic stand-in with exact volumes.",
            "avoid: revolving profiles that contain arcs or circles (unsupported).",
        )

    def known_traps(self) -> tuple[str, ...]:
        return (
            "The fake has no real kernel: booleans only cover simple containment cases.",
            "Positions on a face are (u, v) offsets from the face centre in its local frame (hole: at=[x, y, z] takes "
            "a world point; sketch: frame=world takes world points).",
        )

    def fired_faults(self) -> tuple[tuple[str, int, FaultKind], ...]:
        return self._faults.fired()

    # --- Session -------------------------------------------------------------------------

    def connect(self, request: ConnectRequest) -> SessionInfo:
        self._connected = True
        self._timeout_s = request.timeout_s
        return SessionInfo(adapter="fake", version=__version__, units="mm")

    def disconnect(self, request: Empty) -> Ack:
        self._connected = False
        return Ack()

    def new_document(self, request: NewDocumentRequest) -> DocumentInfo:
        self._doc_counter += 1
        doc = FakeDocument(
            id=f"doc:{self._doc_counter}",
            kind=request.kind,
            name=request.name,
            deterministic_ids=self._deterministic_ids,
        )
        self._documents[doc.id] = doc
        self._active = doc
        return self._document_info(doc)

    def set_active_document(self, request: SetActiveDocumentRequest) -> DocumentInfo:
        doc = self._documents.get(request.document.id)
        if doc is None:
            raise self._error(ErrorCode.invalid_reference, f"unknown document {request.document.id}", request)
        self._active = doc
        self.activate_calls.append(request)
        if request.refresh and doc.kind is DocumentKind.assembly:
            self._refresh_components(doc)
        return self._document_info(doc)

    def _refresh_components(self, doc: FakeDocument) -> None:
        """The components take the part documents as they are now (a part changed after it was inserted)."""
        for component in doc.components.values():
            part = self._documents.get(component.part_id)
            if part is not None:
                component.part = copy.deepcopy(part)

    def active_document(self, request: Empty) -> DocumentInfo:
        return self._document_info(self._doc(request))

    def set_timeout(self, request: SetTimeoutRequest) -> Ack:
        self._timeout_s = request.timeout_s
        return Ack()

    def suppress_interaction(self, request: Empty) -> Ack:
        return Ack()

    def restore_interaction(self, request: Empty) -> Ack:
        return Ack()

    # --- Health -----------------------------------------------------------------------------

    def canary(self, request: Empty) -> CanaryResult:
        started = time.perf_counter()
        healthy = self._faults.canary_healthy
        checks = {"plane": True, "render": healthy, "tree": True}
        return CanaryResult(healthy=healthy, checks=checks, duration_ms=(time.perf_counter() - started) * 1000.0)

    def validate_api(self, request: Empty) -> ApiValidation:
        return ApiValidation(ok=True, problems=())

    # --- Construction ---------------------------------------------------------------------------

    @_recorded
    def datum_plane(self, request: DatumPlaneRequest) -> DatumPlaneResult:
        """Echo the requested pose and assign ``datum_plane:N``. A datum is not a feature: it never touches mass,
        bounding box, the feature tree or the undo stack (a sketch is the same)."""
        doc = self._doc(request)
        fired = self._begin(request)
        frame = request.frame
        if any(f.kind is FaultKind.wrong_datum_pose for f in fired):
            frame = self._displaced_frame(frame)
        datum_id = doc.next_id("datum_plane")
        doc.datum_planes[datum_id] = FakeDatumPlane(id=datum_id, frame=frame)
        return DatumPlaneResult(
            datum=DatumPlaneHandle(id=datum_id),
            frame=frame,
            rebuild_ok=not any(f.kind is FaultKind.rebuild_failed for f in fired),
        )

    @_recorded
    def datum_axis(self, request: DatumAxisRequest) -> DatumAxisResult:
        doc = self._doc(request)
        fired = self._begin(request)
        axis = request.axis
        if any(f.kind is FaultKind.wrong_datum_pose for f in fired):
            axis = self._displaced_axis(axis)
        datum_id = doc.next_id("datum_axis")
        doc.datum_axes[datum_id] = FakeDatumAxis(id=datum_id, axis=axis)
        return DatumAxisResult(
            datum=DatumAxisHandle(id=datum_id),
            axis=axis,
            rebuild_ok=not any(f.kind is FaultKind.rebuild_failed for f in fired),
        )

    @staticmethod
    def _displaced_axis(axis: Axis) -> Axis:
        """The axis moved 1 mm sideways (a shift along it would describe the same line)."""
        side = vcross(axis.direction, (1.0, 0.0, 0.0))
        if vlength(side) < 1e-6:
            side = vcross(axis.direction, (0.0, 1.0, 0.0))
        return Axis(origin=vadd(axis.origin, vunit(side)), direction=axis.direction)

    @staticmethod
    def _displaced_frame(frame: SketchFrame) -> SketchFrame:
        tilt = Transform.from_axis_angle(frame.u, 5.0, origin=(0.0, 0.0, 0.0))
        return SketchFrame(
            origin=vadd(frame.origin, frame.n),
            u=frame.u,
            v=tilt.apply_direction(frame.v),
            n=tilt.apply_direction(frame.n),
        )

    @_recorded
    def sketch(self, request: SketchRequest) -> SketchResult:
        doc = self._doc(request)
        fired = self._begin(request)
        on_face: str | None = None
        if isinstance(request.on, PlaneName):
            frame = frame_for_plane(request.on)
        elif isinstance(request.on, DatumPlaneTarget):
            datum = doc.datum_planes.get(request.on.handle.id)
            if datum is None:
                raise self._error(
                    ErrorCode.invalid_reference,
                    f"datum plane {request.on.handle.id} does not exist in this document",
                    request,
                )
            if not (
                vclose(datum.frame.origin, request.on.frame.origin, 1e-6)
                and vclose(datum.frame.n, request.on.frame.n, 1e-6)
                and vclose(datum.frame.u, request.on.frame.u, 1e-6)
            ):
                raise invalid_request(
                    f"sketch frame for datum plane {datum.id} differs from the pose the datum was created with",
                    request.operation_id,
                )
            frame = request.on.frame
        else:
            face, component = self._locate_face(doc, request.on.id, request)
            if face.normal is None:
                raise invalid_request(f"face {face.id} is not planar", request.operation_id)
            frame = frame_for_face(self._face_info(doc, face, component), request.origin)
            on_face = face.id
        try:
            profiles = sketch_profiles(request.entities)
        except ValueError as exc:
            raise invalid_request(str(exc), request.operation_id) from exc
        sketch = FakeSketch(
            id=doc.next_id("sketch"),
            frame=frame,
            entities=request.entities,
            profiles=profiles,
            on_face=on_face,
            constraints=request.constraints,
            named_dimensions=request.named_dimensions,
        )
        if any(f.kind is FaultKind.underdefined_sketch for f in fired):
            sketch.free_dof = max(1, sketch_dof_report(sketch).dof)  # the model's own count, at least one
        doc.sketches[sketch.id] = sketch
        return SketchResult(
            sketch=SketchHandle(id=sketch.id),
            frame=frame,
            profile_areas_mm2=tuple(p.area_mm2 for p in profiles),
            rebuild_ok=not any(f.kind is FaultKind.underdefined_sketch for f in fired),
            warnings=tuple(
                f"rect {e.name}: four corners chamfered {e.chamfer_mm:g} mm at 45 degrees"
                for e in request.entities
                if isinstance(e, RectEntity) and e.chamfer_mm is not None
            ),
        )

    @_recorded
    def extrude(self, request: ExtrudeRequest) -> OperationResult:
        doc = self._doc(request)
        sketch = self._defined_sketch(doc, request.sketch.id, request)
        if sketch.on_face is not None and asm.split_prefixed(sketch.on_face) is not None:
            raise not_supported("extrude on a component face is not supported", request.operation_id)
        if has_nested(sketch.profiles):
            raise not_supported(
                "a loop inside a loop (a ring, an island) is not modelled by the fake adapter: its solids are one "
                "prism per loop; run it on FreeCAD",
                request.operation_id,
            )

        def build(feature: FakeFeature) -> None:
            body = self._boss_body(doc, sketch, request.merge, feature)
            b = Build(doc, feature, body)
            n = sketch.frame.n
            direction = n if request.direction is Direction.positive else vscale(n, -1.0)
            back = 0.0
            if request.both_directions:
                back = request.depth2_mm if request.depth2_mm is not None else request.depth_mm
            depth = request.depth_mm + back
            total_area = 0.0
            # A boss whose sketch lies on an existing face and merges into that face's body does not get a
            # new base cap: the base merges into the host face, which is only modified (see the
            # `_op_extrude` docstring in verification/expected.py). ``entry_face`` tells the builders to reuse
            # the host face as the near cap instead of creating a new one -- unless the profile's footprint
            # *covers* the host face outright (equals or contains it), in which case the host face is
            # consumed entirely (removed, stale handle) instead of reused; see ``covers_host_face`` below and
            # ``builders.merge_extrude_covering_host_face``/``classify_face_coverage``.
            joins_face = request.merge and sketch.on_face is not None
            entry_face = sketch.on_face if joins_face else None
            covers_host_face = False
            host_face_obj: FakeFace | None = None
            if (
                joins_face
                and sketch.on_face is not None
                and request.direction is Direction.positive
                and not request.both_directions
                and len(sketch.profiles) == 1
            ):
                host_face_obj = doc.faces.get(sketch.on_face)
                if host_face_obj is not None:
                    host_bbox_uv = face_footprint_uv(host_face_obj, sketch.frame)
                    coverage = classify_face_coverage(sketch.profiles[0], host_bbox_uv, TOLERANCE)
                    if coverage == "covers" and sketch.profiles[0].kind != "rect":
                        # Unreachable through the executor: `ExpectedComputer._op_extrude` rejects a
                        # non-rect profile covering a rectangular host face as a `plan_error` before the
                        # adapter is ever called. Defensive only, for direct adapter callers.
                        raise invalid_request(
                            "only a rectangular profile may cover a rectangular host face",
                            request.operation_id,
                        )
                    covers_host_face = coverage == "covers"
            for profile in sketch.profiles:
                total_area += profile.area_mm2
                if covers_host_face and host_face_obj is not None:
                    base = self._base_loop(sketch, profile, direction, back)
                    merge_extrude_covering_host_face(
                        b, host_face_obj, base, direction, depth, frame=sketch.frame, operation_id=request.operation_id
                    )
                elif profile.kind == "circle":
                    assert profile.center is not None and profile.radius is not None
                    origin = vsub(sketch.frame.to_world(*profile.center), vscale(direction, back))
                    build_cylinder(
                        b, origin, direction, profile.radius, depth, 1, entry_face=entry_face, frame=sketch.frame
                    )
                else:
                    base = self._base_loop(sketch, profile, direction, back)
                    build_prism(b, base, direction, depth, 1, entry_face=entry_face, frame=sketch.frame)
            if joins_face and not covers_host_face:
                assert sketch.on_face is not None
                b.modify_face_area(sketch.on_face, -total_area)

        return self._construct(doc, "extrude", request, build, sketch=sketch)

    @_recorded
    def cut(self, request: CutRequest) -> OperationResult:
        doc = self._doc(request)
        sketch = self._defined_sketch(doc, request.sketch.id, request)
        if request.end_condition is EndCondition.blind and request.depth_mm is None:
            raise invalid_request("blind cut requires depth_mm", request.operation_id)
        if sketch.on_face is not None and asm.split_prefixed(sketch.on_face) is not None:
            raise not_supported("cut on a component face is not supported", request.operation_id)
        if has_nested(sketch.profiles):
            raise not_supported(
                "a loop inside a loop (an island) is not modelled by the fake adapter: its solids are one prism per "
                "loop; run it on FreeCAD",
                request.operation_id,
            )
        if request.end_condition is EndCondition.up_to_face:
            raise invalid_request("a cut ends blind or through_all; up_to_face is for holes", request.operation_id)
        host = doc.faces.get(sketch.on_face) if sketch.on_face is not None else None
        if host is not None:
            for profile in sketch.profiles:
                if not profile_touches_face(host, sketch.frame, profile):
                    raise self._error(
                        ErrorCode.geometry_failed,
                        f"cut profile (centroid {_fmt_vec(sketch.frame.to_world(*profile.centroid))}) lies entirely "
                        f"outside its host face {host.id} (centre {_fmt_vec(host.center)}, bbox "
                        f"{_fmt_vec(host.bbox.min)}-{_fmt_vec(host.bbox.max)}); sketch coordinates are (u, v) from "
                        "the face centre in its local frame",
                        request,
                    )
        if request.scope is not None:
            resolved_body = self._cut_body(doc, sketch, request)
            scope_ids = [b.id for b in request.scope]
            if scope_ids != [resolved_body]:
                raise invalid_request(
                    f"cut.scope {scope_ids!r} does not match {resolved_body!r}, the body its sketch belongs "
                    "to: the fake always confines a cut to that one body, like FreeCAD's PartDesign -- "
                    "scope cannot select a different one; omit it or name that body",
                    request.operation_id,
                )

        def build(feature: FakeFeature) -> None:
            body = self._cut_body(doc, sketch, request)
            b = Build(doc, feature, body)
            n = sketch.frame.n
            direction = n if request.direction is Direction.positive else vscale(n, -1.0)
            through = request.end_condition is EndCondition.through_all
            for profile in sketch.profiles:
                centroid = sketch.frame.to_world(*profile.centroid)
                if through:
                    near, far = axial_range_from(doc, body, centroid, direction)
                    back = 0.0
                    if request.both_directions:
                        _, back = axial_range_from(doc, body, centroid, vscale(direction, -1.0))
                    start, length = near, far - near + back
                    if length <= 1e-9:
                        raise self._error(
                            ErrorCode.geometry_failed, "through-all cut finds no material", request, recoverable=True
                        )
                    start -= back
                else:
                    assert request.depth_mm is not None
                    back = 0.0
                    if request.both_directions:
                        back = request.depth2_mm if request.depth2_mm is not None else request.depth_mm
                    start, length = -back, request.depth_mm + back
                    if length <= 0:
                        raise invalid_request("blind cut depth_mm must be positive", request.operation_id)
                entry_point = vadd(centroid, vscale(direction, start))
                exit_point = vadd(entry_point, vscale(direction, length))
                entry = self._entry_face(doc, body, sketch, entry_point, vscale(direction, -1.0))
                exit_face = find_planar_face(doc, body, exit_point, direction) if through else None
                if profile.kind == "circle":
                    assert profile.center is not None and profile.radius is not None
                    origin = vadd(sketch.frame.to_world(*profile.center), vscale(direction, start))
                    build_cylinder(
                        b,
                        origin,
                        direction,
                        profile.radius,
                        length,
                        -1,
                        blind=not through,
                        entry_face=entry,
                        exit_face=exit_face.id if exit_face else None,
                        frame=sketch.frame,
                    )
                else:
                    base = self._base_loop(sketch, profile, direction, -start)
                    build_prism(
                        b,
                        base,
                        direction,
                        length,
                        -1,
                        blind=not through,
                        entry_face=entry,
                        exit_face=exit_face.id if exit_face else None,
                        frame=sketch.frame,
                    )
                if entry is not None:
                    b.modify_face_area(entry, -profile.area_mm2)
                if exit_face is not None:
                    b.modify_face_area(exit_face.id, -profile.area_mm2)

        return self._construct(doc, "cut", request, build, sketch=sketch)

    @_recorded
    def revolve(self, request: RevolveRequest) -> OperationResult:
        doc = self._doc(request)
        sketch = self._defined_sketch(doc, request.sketch.id, request)

        def build(feature: FakeFeature) -> None:
            body = self._boss_body(doc, sketch, True, feature)
            b = Build(doc, feature, body)
            for profile in sketch.profiles:
                if profile.kind == "circle":
                    raise not_supported("revolved circles (tori) are not supported", request.operation_id)
                points = self._profile_points(profile)
                build_revolution(
                    b, sketch.frame, points, request.axis, request.angle_deg, 1, operation_id=request.operation_id
                )

        return self._construct(doc, "revolve", request, build, sketch=sketch)

    @_recorded
    def sweep(self, request: SweepRequest) -> OperationResult:
        doc = self._doc(request)
        sketch = self._defined_sketch(doc, request.sketch.id, request)
        if len(sketch.profiles) != 1:
            raise invalid_request(
                f"sweep needs exactly one closed profile, sketch {sketch.id} has {len(sketch.profiles)}",
                request.operation_id,
            )

        def build(feature: FakeFeature) -> None:
            body = self._boss_body(doc, sketch, request.merge, feature)
            b = Build(doc, feature, body)
            build_sweep(
                b,
                sketch.frame,
                sketch.profiles[0],
                request.path,
                request.corner_radius_mm,
                operation_id=request.operation_id,
            )

        return self._construct(doc, "sweep", request, build, sketch=sketch)

    @_recorded
    def base_flange(self, request: BaseFlangeRequest) -> OperationResult:
        """A flat sheet from a hole-free rectangular outline (see ``adapters.fake.sheetmetal``)."""
        return sheetmetal.base_flange(self, request)

    @_recorded
    def edge_flange(self, request: EdgeFlangeRequest) -> OperationResult:
        """A straight wall with the volume of the bent flange, on a whole side of the sheet."""
        return sheetmetal.edge_flange(self, request)

    @_recorded
    def unfold(self, request: UnfoldRequest) -> OperationResult:
        """The flat pattern's measured facts from the closed form; the solid does not change."""
        return sheetmetal.unfold(self, request)

    def capabilities(self) -> Capabilities:
        tools = super().capabilities().tools
        return Capabilities(tools=tools if self._sheet_metal else tools - sheetmetal.SHEET_METAL_TOOLS)

    def unavailable_tools(self) -> dict[str, str]:
        if self._sheet_metal:
            return {}
        why = (
            "this fake has its sheet-metal capability switched off (FakeAdapter(sheet_metal=False)); a real CAD would "
            "say which add-on to install"
        )
        return dict.fromkeys(sorted(sheetmetal.SHEET_METAL_TOOLS), why)

    @_recorded
    def shell(self, request: ShellRequest) -> OperationResult:
        doc = self._doc(request)
        located = [self._locate_face(doc, handle.id, request) for handle in request.faces]
        if any(component is not None for _, component in located):
            raise not_supported("shell on a component face is not supported", request.operation_id)
        faces = [face for face, _ in located]
        if len({face.id for face in faces}) != len(faces):
            raise invalid_request("shell lists the same face twice", request.operation_id)
        if len({face.body for face in faces}) != 1:
            raise invalid_request("shell faces belong to different bodies", request.operation_id)
        if request.outward:
            raise not_supported(
                "an outward shell (the walls grow around the old body, which becomes the cavity) is not modelled by "
                "the fake adapter; run it on FreeCAD",
                request.operation_id,
            )
        if request.join != "intersection":
            raise not_supported(
                "a shell with an arc join has no exact analytic form in the fake adapter; run it on FreeCAD",
                request.operation_id,
            )

        def build(feature: FakeFeature) -> None:
            build_shell(
                Build(doc, feature, faces[0].body), faces, request.thickness_mm, operation_id=request.operation_id
            )

        return self._construct(doc, "shell", request, build)

    @_recorded
    def hole(self, request: HoleRequest) -> OperationResult:
        doc = self._doc(request)
        face, component = self._locate_face(doc, request.face.id, request)
        if component is not None:
            raise not_supported("hole on a component face is not supported", request.operation_id)
        if face.normal is None:
            if face.surface is SurfaceType.cylinder:
                raise not_supported(
                    "a hole on a cylindrical face (across a tube or shaft wall) is not supported by the fake adapter: "
                    "its analytic bookkeeping cannot model the curved-wall exit; run it on FreeCAD",
                    request.operation_id,
                )
            raise invalid_request(f"face {face.id} is not planar", request.operation_id)
        if request.end_condition is EndCondition.blind and request.depth_mm is None:
            raise invalid_request("blind hole requires depth_mm", request.operation_id)
        if (
            request.counterbore is not None
            or request.countersink is not None
            or request.end_condition is EndCondition.up_to_face
        ):
            raise not_supported(
                "a counterbore, a countersink and up_to_face are not modelled by the fake "
                "adapter (its analytic bookkeeping is a plain cylinder; an angled point is kept flat); "
                "run them on FreeCAD",
                request.operation_id,
            )
        frame = frame_for_face(self._face_info(doc, face, None), request.origin)
        points = [frame.to_world(*p) for p in (request.positions or (request.position,))]
        direction = vscale(face.normal, -1.0)
        radius = request.diameter_mm / 2.0
        through = request.end_condition is EndCondition.through_all
        for origin in points:
            if circle_within_face(face, origin, radius):
                continue
            # The analytic bookkeeping would subtract a full cylinder wherever the hole lands; a footprint
            # that leaves the face is a wrong position (frame or selector), not material to drill.
            raise self._error(
                ErrorCode.geometry_failed,
                f"hole footprint (centre {_fmt_vec(origin)}, radius {radius:g} mm) is not fully inside face "
                f"{face.id} (centre {_fmt_vec(face.center)}, bbox {_fmt_vec(face.bbox.min)}-"
                f"{_fmt_vec(face.bbox.max)}); position is (u, v) from the centre of the face's bounding box",
                request,
            )

        def build(feature: FakeFeature) -> None:
            b = Build(doc, feature, face.body)
            for origin in points:
                if through:
                    _, length = axial_range_from(doc, face.body, origin, direction)
                else:
                    assert request.depth_mm is not None
                    length = request.depth_mm
                if length <= 1e-9:
                    raise self._error(ErrorCode.geometry_failed, "hole finds no material", request, recoverable=True)
                exit_face = (
                    find_planar_face(
                        doc, face.body, vadd(origin, vscale(direction, length)), direction, exclude={face.id}
                    )
                    if through
                    else None
                )
                build_cylinder(
                    b,
                    origin,
                    direction,
                    radius,
                    length,
                    -1,
                    blind=not through,
                    entry_face=face.id,
                    exit_face=exit_face.id if exit_face else None,
                )
                b.modify_face_area(face.id, -math.pi * radius * radius)
                if exit_face is not None:
                    b.modify_face_area(exit_face.id, -math.pi * radius * radius)

        return self._construct(doc, "hole", request, build).model_copy(update={"place": frame})

    @_recorded
    def linear_pattern(self, request: LinearPatternRequest) -> OperationResult:
        doc = self._doc(request)
        seeds = [self._feature(doc, f.id, request) for f in request.features]
        transforms = linear_instances(
            request.direction,
            request.spacing_mm,
            request.count,
            request.direction2,
            request.spacing2_mm,
            request.count2,
        )
        instances = request.count * (request.count2 or 1)
        return self._pattern(doc, "linear_pattern", request, seeds, transforms, instances)

    @_recorded
    def circular_pattern(self, request: CircularPatternRequest) -> OperationResult:
        doc = self._doc(request)
        seeds = [self._feature(doc, f.id, request) for f in request.features]
        transforms = circular_instances(request.axis, request.count, request.angle_deg, request.equal_spacing)
        return self._pattern(doc, "circular_pattern", request, seeds, transforms, request.count)

    @_recorded
    def mirror(self, request: MirrorRequest) -> OperationResult:
        doc = self._doc(request)
        seeds = [self._feature(doc, f.id, request) for f in request.features]
        reflection = reflection_transform(request.plane, request.operation_id)
        return self._pattern(doc, "mirror", request, seeds, [reflection], 2)

    @_recorded
    def chamfer(self, request: ChamferRequest) -> OperationResult:
        doc = self._doc(request)
        if request.distance1_face is None and request.distance2_mm is not None:
            raise invalid_request("distance1_face is required when distance2_mm is set", request.operation_id)
        edges = [self._edge(doc, e.id, request) for e in request.edges]
        if not edges:
            raise invalid_request("chamfer requires at least one edge", request.operation_id)
        d2 = request.distance2_mm if request.distance2_mm is not None else request.distance1_mm

        def build(feature: FakeFeature) -> None:
            for edge in edges:
                face1 = request.distance1_face.id if request.distance1_face is not None else edge.faces[0]
                b = Build(doc, feature, doc.faces[edge.faces[0]].body)
                build_chamfer(b, edge, request.distance1_mm, d2, face1, operation_id=request.operation_id)

        return self._construct(doc, "chamfer", request, build)

    @_recorded
    def fillet(self, request: FilletRequest) -> OperationResult:
        doc = self._doc(request)
        edges = [self._edge(doc, e.id, request) for e in request.edges]
        if not edges:
            raise invalid_request("fillet requires at least one edge", request.operation_id)

        def build(feature: FakeFeature) -> None:
            for edge in edges:
                b = Build(doc, feature, doc.faces[edge.faces[0]].body)
                build_fillet(b, edge, request.radius_mm, operation_id=request.operation_id)

        return self._construct(doc, "fillet", request, build)

    @_recorded
    def box(self, request: BoxRequest) -> OperationResult:
        doc = self._doc(request)
        sx, sy, sz = request.size
        if min(sx, sy, sz) <= 0:
            raise invalid_request("box size must be positive", request.operation_id)

        def build(feature: FakeFeature) -> None:
            body = self._new_body(doc, feature)
            t = request.transform
            base = [t.apply(p) for p in ((0.0, 0.0, 0.0), (sx, 0.0, 0.0), (sx, sy, 0.0), (0.0, sy, 0.0))]
            build_prism(Build(doc, feature, body), base, t.apply_direction((0.0, 0.0, 1.0)), sz, 1)

        return self._construct(doc, "box", request, build)

    @_recorded
    def cylinder(self, request: CylinderRequest) -> OperationResult:
        doc = self._doc(request)
        if request.diameter_mm <= 0 or request.height_mm <= 0:
            raise invalid_request("cylinder dimensions must be positive", request.operation_id)

        def build(feature: FakeFeature) -> None:
            body = self._new_body(doc, feature)
            build_cylinder(
                Build(doc, feature, body),
                request.axis.origin,
                request.axis.direction,
                request.diameter_mm / 2.0,
                request.height_mm,
                1,
            )

        return self._construct(doc, "cylinder", request, build)

    @_recorded
    def boolean(self, request: BooleanRequest) -> OperationResult:
        doc = self._doc(request)
        target = request.target.id
        tools = [t.id for t in request.tools]
        for body in [target, *tools]:
            if body not in doc.bodies:
                raise self._error(ErrorCode.invalid_reference, f"unknown body {body}", request)
        if target in tools or len(set(tools)) != len(tools):
            raise invalid_request("boolean bodies must be distinct", request.operation_id)
        if not tools:
            raise invalid_request("boolean requires at least one tool body", request.operation_id)
        if request.keep_tools and request.kind is not BooleanKind.subtract:
            raise invalid_request("keep_tools is only supported by boolean_subtract", request.operation_id)

        def build(feature: FakeFeature) -> None:
            self._boolean(doc, feature, request.kind, target, tools, request, keep_tools=request.keep_tools)

        return self._construct(doc, f"boolean_{request.kind.value}", request, build)

    @_recorded
    def move_body(self, request: MoveBodyRequest) -> OperationResult:
        doc = self._doc(request)
        body = request.body.id
        if body not in doc.bodies:
            raise self._error(ErrorCode.invalid_reference, f"unknown body {body}", request)

        def build(feature: FakeFeature) -> None:
            b = Build(doc, feature, body)
            t = request.transform
            for prim_id in doc.bodies[body]:
                b.touch(prim_id)
                doc.primitives[prim_id] = doc.primitives[prim_id].transformed(t)
            face_ids = [f.id for f in doc.faces.values() if f.body == body]
            edge_ids = [e.id for e in doc.edges.values() if e.faces[0] in face_ids]
            vertex_ids = [v.id for v in doc.vertices.values() if any(e in edge_ids for e in v.edges)]
            for face_id in face_ids:
                b.touch(face_id)
                doc.faces[face_id] = doc.faces[face_id].transformed(t)
                b.mark_modified(face_id)
            for edge_id in edge_ids:
                b.touch(edge_id)
                doc.edges[edge_id] = doc.edges[edge_id].transformed(t)
            for vertex_id in vertex_ids:
                b.touch(vertex_id)
                doc.vertices[vertex_id] = doc.vertices[vertex_id].transformed(t)
            feature.modified_bodies.append(body)

        return self._construct(doc, "move_body", request, build)

    @_recorded
    def mirror_body(self, request: MirrorBodyRequest) -> OperationResult:
        doc = self._doc(request)
        source = request.source.id
        if source not in doc.bodies:
            raise self._error(ErrorCode.invalid_reference, f"unknown body {source}", request)
        reflection = reflection_transform(request.plane, request.operation_id)

        def build(feature: FakeFeature) -> None:
            body = self._new_body(doc, feature)
            clone_body(Build(doc, feature, body), source, reflection)

        return self._construct(doc, "mirror_body", request, build)

    @_recorded
    def copy_body(self, request: CopyBodyRequest) -> OperationResult:
        doc = self._doc(request)
        source = request.source.id
        if source not in doc.bodies:
            raise self._error(ErrorCode.invalid_reference, f"unknown body {source}", request)

        def build(feature: FakeFeature) -> None:
            body = self._new_body(doc, feature)
            clone_body(
                Build(doc, feature, body),
                source,
                request.transform,
                scale=request.scale,
                scale_about=request.scale_about,
            )

        return self._construct(doc, "copy_body", request, build)

    @_recorded
    def split(self, request: SplitRequest) -> OperationResult:
        doc = self._doc(request)
        source = request.source.id
        if source not in doc.bodies:
            raise self._error(ErrorCode.invalid_reference, f"unknown body {source}", request)

        def build(feature: FakeFeature) -> None:
            build_split(
                doc,
                feature,
                source,
                request.plane,
                request.keep,
                lambda: self._new_body(doc, feature),
                request.operation_id,
            )

        return self._construct(doc, "split", request, build)

    # --- Assemblies -------------------------------------------------------------------------------

    @_recorded
    def insert_component(self, request: InsertComponentRequest) -> OperationResult:
        doc = self._doc(request)
        part = self._documents.get(request.part.id)
        if part is None:
            raise self._error(ErrorCode.invalid_reference, f"unknown document {request.part.id}", request)

        def build(feature: FakeFeature) -> None:
            component = asm.insert_component(doc, part, request.transform)
            feature.component = component.id
            # counted, not listed, as the CAD adapter does: ``faces_of`` of the step lists them
            feature.face_count = len(component.part.faces)
            feature.edge_count = len(component.part.edges)
            feature.created_bodies.extend(asm.prefixed(component.id, b) for b in component.part.bodies)

        return self._construct(doc, "component", request, build)

    @_recorded
    def fix_component(self, request: FixComponentRequest) -> OperationResult:
        doc = self._doc(request)
        component = self._component(doc, request.component.id, request)

        def build(feature: FakeFeature) -> None:
            feature.component = component.id
            feature.previous_fixed = component.fixed
            component.fixed = True

        return self._construct(doc, "fix_component", request, build)

    @_recorded
    def place_component(self, request: PlaceComponentRequest) -> OperationResult:
        doc = self._doc(request)
        component = self._component(doc, request.component.id, request)
        self._refuse_if_jointed(doc, component, request)

        def build(feature: FakeFeature) -> None:
            feature.component = component.id
            feature.previous_transform = component.transform
            component.transform = request.transform

        return self._construct(doc, "place_component", request, build)

    def _refuse_if_jointed(self, doc: FakeDocument, component: FakeComponent, request: Request) -> None:
        """A component a joint carries (or that a joint hangs from) is placed by the joint, not by hand."""
        joint = next((j for j in doc.joints.values() if component.id in (j.b, j.a)), None)
        if joint is not None:
            role = "joined by" if joint.b == component.id else "the reference of"
            raise self._error(
                ErrorCode.invalid_request,
                f"component {component.id} is {role} joint {joint.id}: it cannot be moved by hand. Use set_joint to "
                "move the joint, or undo the joint first",
                request,
                recoverable=True,
            )

    @_recorded
    def add_joint(self, request: AddJointRequest) -> OperationResult:
        doc = self._doc(request)
        b = self._component(doc, request.b.id, request)
        a = None if request.a is None else self._component(doc, request.a.id, request)
        if a is b:
            raise self._error(ErrorCode.invalid_request, "a joint joins two different components", request)
        if request.joint_type is JointType.fixed:
            if request.axis is not None:
                raise self._error(ErrorCode.invalid_request, "a fixed joint has no axis", request)
        elif request.axis is None:
            raise self._error(ErrorCode.invalid_request, f"a {request.joint_type.value} joint needs an axis", request)
        low, high = request.min_value, request.max_value
        if (low is not None and low > 0) or (high is not None and high < 0):
            raise self._error(
                ErrorCode.invalid_request,
                "the limits must include 0: the pose the joint is made at is its value 0",
                request,
            )
        carried = next((j for j in doc.joints.values() if j.b == b.id), None)
        if carried is not None:
            raise self._error(ErrorCode.invalid_request, f"component {b.id} is already joined by {carried.id}", request)

        def build(feature: FakeFeature) -> None:
            joint = FakeJoint(
                id=doc.next_id("joint"),
                joint_type=request.joint_type,
                a=a.id if a is not None else None,
                b=b.id,
                axis=request.axis,
                base=b.transform,
                low=low,
                high=high,
            )
            doc.joints[joint.id] = joint
            feature.component = b.id
            feature.joint = joint.id

        return self._construct(doc, "add_joint", request, build)

    @_recorded
    def set_joint(self, request: SetJointRequest) -> OperationResult:
        doc = self._doc(request)
        joint = self._joint(doc, request.joint.id, request)
        if joint.joint_type is JointType.fixed:
            raise self._error(ErrorCode.invalid_request, f"joint {joint.id} is fixed: it has no value to set", request)
        self._require_within(joint, request.value, request)
        component = self._component(doc, joint.b, request)

        def build(feature: FakeFeature) -> None:
            feature.component = component.id
            feature.joint = joint.id
            feature.previous_transform = component.transform
            feature.previous_value = joint.value
            component.transform = asm.joint_pose(joint, request.value)
            joint.value = request.value

        return self._construct(doc, "set_joint", request, build)

    def joint_info(self, request: JointRequest) -> JointInfo:
        doc = self._doc(request)
        return self._joint_info(self._joint(doc, request.joint.id, request))

    def sweep_joint(self, request: SweepJointRequest) -> JointSweepResult:
        doc = self._doc(request)
        self._begin(request)
        joint = self._joint(doc, request.joint.id, request)
        if joint.joint_type is JointType.fixed:
            raise self._error(
                ErrorCode.invalid_request, f"joint {joint.id} is fixed: it has no value to sweep", request
            )
        values = sweep_values(request.start, request.end, request.step)
        if len(values) > MAX_SWEEP_POSITIONS:
            raise self._error(
                ErrorCode.invalid_request,
                f"the sweep has {len(values)} positions, the most is {MAX_SWEEP_POSITIONS}: raise the step or "
                "shorten the range",
                request,
            )
        for end in (values[0], values[-1]):
            self._require_within(joint, end, request, sweep=True)
        moving = self._component(doc, joint.b, request)
        others = [c for c in doc.components.values() if c.id != moving.id]
        saved = moving.transform
        least: dict[str, dict[str, Any]] = {c.id: {} for c in others}
        first: tuple[float, str, float] | None = None
        notes: list[str] = []
        try:
            for value in values:
                moving.transform = asm.joint_pose(joint, value)
                clash: tuple[str, float] | None = None
                for other in others:
                    volume, distance, note = asm.measure_components(moving, other)
                    row = least[other.id]
                    if volume is None or distance is None:
                        row["unmeasured"] = row.get("unmeasured", 0) + 1
                        if note and note not in notes:
                            notes.append(note)
                    elif (
                        volume <= 0.0
                        and "first_overlap" not in row
                        and ("min" not in row or distance < row["min"] - 1e-9)
                    ):
                        row["min"], row["at"] = distance, value  # the clearance up to the first overlap
                    if volume is not None and volume > 0.0:
                        row.setdefault("first_overlap", value)
                        if clash is None or volume > clash[1]:
                            clash = (other.id, volume)
                if clash is not None and first is None:
                    first = (value, clash[0], clash[1])
        finally:
            moving.transform = saved
        return JointSweepResult(
            joint=self._joint_info(joint),
            start=request.start,
            end=request.end,
            step=request.step,
            positions=len(values),
            first_clash=None
            if first is None
            else JointSweepClash(
                value=first[0], a=ComponentHandle(id=moving.id), b=ComponentHandle(id=first[1]), volume_mm3=first[2]
            ),
            pairs=tuple(
                JointSweepPair(
                    a=ComponentHandle(id=moving.id),
                    b=ComponentHandle(id=other.id),
                    min_distance_mm=least[other.id].get("min"),
                    at_value=least[other.id].get("at"),
                    first_overlap_value=least[other.id].get("first_overlap"),
                    unmeasured=least[other.id].get("unmeasured", 0),
                )
                for other in others
            ),
            notes=tuple(notes),
        )

    def _joint(self, doc: FakeDocument, joint_id: str, request: Request) -> FakeJoint:
        joint = doc.joints.get(joint_id)
        if joint is None:
            raise self._error(ErrorCode.invalid_reference, f"unknown joint {joint_id}", request)
        return joint

    def _require_within(self, joint: FakeJoint, value: float, request: Request, *, sweep: bool = False) -> None:
        low, high = joint.low, joint.high
        if (low is None or value >= low - 1e-9) and (high is None or value <= high + 1e-9):
            return
        unit = "deg" if joint.joint_type is JointType.revolute else "mm"
        shown = f"[{'-inf' if low is None else f'{low:g}'}, {'inf' if high is None else f'{high:g}'}] {unit}"
        message = (
            f"the sweep goes outside the limits of joint {joint.id}: {shown}; use start and end inside them"
            if sweep
            else f"value {value:g} {unit} is outside the limits of joint {joint.id}: {shown}"
        )
        raise self._error(ErrorCode.invalid_request, message, request, recoverable=True)

    @staticmethod
    def _joint_info(joint: FakeJoint) -> JointInfo:
        return JointInfo(
            handle=JointHandle(id=joint.id),
            joint_type=joint.joint_type,
            a=ComponentHandle(id=joint.a) if joint.a else None,
            b=ComponentHandle(id=joint.b),
            axis=joint.axis,
            value=joint.value,
            min_value=joint.low,
            max_value=joint.high,
            unit={"revolute": "deg", "slider": "mm"}.get(joint.joint_type.value, ""),
        )

    @_recorded
    def mate_concentric(self, request: MateConcentricRequest) -> OperationResult:
        doc = self._doc(request)
        a, b, component = self._mate_faces(doc, request.a.id, request.b.id, request)
        delta = asm.concentric_alignment(a, b, request.alignment, request.operation_id)
        return self._mate(doc, "mate_concentric", request, component, delta)

    @_recorded
    def mate_coincident(self, request: MateCoincidentRequest) -> OperationResult:
        doc = self._doc(request)
        a, b, component = self._mate_faces(doc, request.a.id, request.b.id, request)
        offset = asm.planar_translation(a, b, request.alignment, 0.0, request.operation_id)
        wrong_side = request.flip

        def other_side(fired: tuple[Fault, ...]) -> Vec3:
            assert a.normal is not None
            if wrong_side != any(f.kind is FaultKind.wrong_side for f in fired):
                return vadd(offset, asm.mirrored_translation(component, offset, a.center, a.normal))
            return offset

        return self._mate(doc, "mate_coincident", request, component, offset, resolve=other_side)

    @_recorded
    def mate_distance(self, request: MateDistanceRequest) -> OperationResult:
        doc = self._doc(request)
        a, b, component = self._mate_faces(doc, request.a.id, request.b.id, request)
        side = 1.0 if request.side is Side.positive else -1.0
        if request.flip:
            side = -side

        def resolve(fired: tuple[Fault, ...]) -> Vec3:
            effective = -side if any(f.kind is FaultKind.wrong_side for f in fired) else side
            return asm.planar_translation(
                a, b, request.alignment, effective * request.distance_mm, request.operation_id
            )

        return self._mate(doc, "mate_distance", request, component, None, resolve=resolve)

    def check_interference(self, request: InterferenceRequest) -> InterferenceResult:
        doc = self._doc(request)
        self._begin(request)
        if request.components is None:
            ids = list(doc.components)
        else:
            ids = [self._component(doc, c.id, request).id for c in request.components]
        pairs = asm.check_interference(doc, ids, request.operation_id)
        readings = []
        for a, b, volume in pairs:
            note = ""
            distance: float | None = 0.0
            if volume <= 1e-6:
                distance = asm.component_distance(
                    asm.component_primitives(doc.components[a]), asm.component_primitives(doc.components[b])
                )
                if distance is None:
                    note = "the fake measures distances between axis-aligned boxes only"
            readings.append(
                InterferencePair(
                    a=ComponentHandle(id=a), b=ComponentHandle(id=b), volume_mm3=volume, distance_mm=distance, note=note
                )
            )
        return InterferenceResult(pairs=tuple(readings), total_volume_mm3=sum(v for _, _, v in pairs))

    def component_bounding_box(self, request: ComponentRequest) -> BoundingBox:
        doc = self._doc(request)
        component = self._component(doc, request.component.id, request)
        box: BoundingBox | None = None
        for prim in asm.component_primitives(component):
            if prim.sign > 0:
                box = bbox_union(box, prim.bbox)
        return box or _ZERO_BBOX

    def component_transform(self, request: ComponentRequest) -> Transform:
        doc = self._doc(request)
        return self._component(doc, request.component.id, request).transform

    # --- Provenance --------------------------------------------------------------------------------

    def faces_of(self, request: FeatureRequest) -> FacesResult:
        doc = self._doc(request)
        feature = self._feature(doc, request.feature.id, request)
        if feature.kind == "component":
            if request.role is ProvenanceRole.modified:
                return FacesResult(faces=())
            assert feature.component is not None
            component = doc.components[feature.component]
            faces = [asm.component_face(component, local) for local in component.part.faces]
            return FacesResult(faces=tuple(self._face_info(doc, f, component.id) for f in faces))
        ids: list[str] = []
        if request.role in (ProvenanceRole.created, ProvenanceRole.any):
            ids.extend(feature.created_faces)
        if request.role in (ProvenanceRole.modified, ProvenanceRole.any):
            ids.extend(f for f in feature.modified_faces if f not in ids)
        return FacesResult(
            faces=tuple(
                self._face_info(doc, *self._locate_face(doc, i, request)) for i in ids if self._exists_face(doc, i)
            )
        )

    def edges_between(self, request: EdgesBetweenRequest) -> EdgesResult:
        doc = self._doc(request)
        wanted = {request.a.id, request.b.id}
        self._locate_face(doc, request.a.id, request)
        self._locate_face(doc, request.b.id, request)
        edges = [e for e in self._all_edges(doc) if set(e.faces) == wanted]
        return EdgesResult(edges=tuple(self._edge_info(e) for e in edges))

    def edges_of(self, request: FaceRequest) -> EdgesResult:
        doc = self._doc(request)
        face, component = self._locate_face(doc, request.face.id, request)
        prefix = f"{component}{asm.PREFIX_SEPARATOR}" if component else ""
        edges = [self._edge(doc, prefix + e, request) for e in face.edges]
        return EdgesResult(edges=tuple(self._edge_info(e) for e in edges))

    def vertices_of(self, request: EdgeRequest) -> VerticesResult:
        doc = self._doc(request)
        edge = self._edge(doc, request.edge.id, request)
        if edge.curve is not CurveType.line or edge.start is None or edge.end is None:
            return VerticesResult(vertices=())
        vertices = [v for v in self._all_vertices(doc) if edge.id in v.edges]
        ordered: list[FakeVertex] = []
        for point in (edge.start, edge.end):
            for vertex in vertices:
                if vclose(vertex.point, point) and vertex not in ordered:
                    ordered.append(vertex)
                    break
        return VerticesResult(vertices=tuple(self._vertex_info(v) for v in ordered))

    def vertex_at(self, request: VertexAtRequest) -> VerticesResult:
        doc = self._doc(request)
        wanted = {f.id for f in request.faces}
        for face_id in wanted:
            self._locate_face(doc, face_id, request)
        edges = {e.id: e for e in self._all_edges(doc)}
        found = []
        for vertex in self._all_vertices(doc):
            covered: set[str] = set()
            for edge_id in vertex.edges:
                if edge_id in edges:
                    covered.update(edges[edge_id].faces)
            if wanted <= covered:
                found.append(vertex)
        return VerticesResult(vertices=tuple(self._vertex_info(v) for v in found))

    def bodies_of(self, request: FeatureRequest) -> BodiesResult:
        doc = self._doc(request)
        feature = self._feature(doc, request.feature.id, request)
        ids: list[str] = []
        if request.role in (ProvenanceRole.created, ProvenanceRole.any):
            ids.extend(feature.created_bodies)
        if request.role in (ProvenanceRole.modified, ProvenanceRole.any):
            ids.extend(b for b in feature.modified_bodies if b not in ids)
        return BodiesResult(bodies=tuple(self._body_info(doc, b, request) for b in ids if self._body_exists(doc, b)))

    # --- Query ---------------------------------------------------------------------------------------

    def is_valid(self, request: ValidityRequest) -> ValidityResult:
        doc = self._doc(request)
        problems = []
        for body, prim_ids in doc.bodies.items():
            volume = sum(doc.primitives[p].sign * doc.primitives[p].volume_mm3 for p in prim_ids)
            if volume < -1e-9:
                problems.append(f"body {body} has negative volume {volume:.3f}")
        return ValidityResult(valid=not problems, problems=tuple(problems))

    def mass_properties(self, request: MassRequest) -> MassProperties:
        doc = self._doc(request)
        prims, faces = self._scope(doc, request)
        volume = 0.0
        weighted: Vec3 = _ORIGIN
        for prim in prims:
            signed = prim.sign * prim.volume_mm3
            volume += signed
            weighted = vadd(weighted, vscale(prim.centroid, signed))
        centroid = vscale(weighted, 1.0 / volume) if abs(volume) > 1e-12 else _ORIGIN
        return MassProperties(
            volume_mm3=volume, centroid=centroid, surface_mm2=sum(f.area_mm2 for f in faces), inertia=None
        )

    def bounding_box(self, request: MassRequest) -> BoundingBox:
        doc = self._doc(request)
        prims, _ = self._scope(doc, request)
        box: BoundingBox | None = None
        for prim in prims:
            if prim.sign > 0:
                box = bbox_union(box, prim.bbox)
        return box or _ZERO_BBOX

    def feature_tree(self, request: Empty) -> FeatureTree:
        doc = self._doc(request)
        nodes = tuple(
            FeatureNode(
                handle=FeatureHandle(id=f.id),
                kind=f.kind,
                name=f.name,
                parameters=dict(f.parameters),
                named_dimensions=dict(f.named_dimensions),
                parent=None,
            )
            for f in doc.features
        )
        return FeatureTree(nodes=nodes)

    def face_info(self, request: FaceRequest) -> FaceInfo:
        doc = self._doc(request)
        return self._face_info(doc, *self._locate_face(doc, request.face.id, request))

    def edge_info(self, request: EdgeRequest) -> EdgeInfo:
        doc = self._doc(request)
        return self._edge_info(self._edge(doc, request.edge.id, request))

    # --- Query (optional) ----------------------------------------------------------------------------------

    def sketch_status(self, request: SketchStatusRequest) -> SketchStatusResult:
        doc = self._doc(request)
        sketch = self._sketch(doc, request.sketch.id, request)
        report = sketch_dof_report(sketch)
        return SketchStatusResult(
            sketch=request.sketch,
            dof=report.dof,
            geometry_count=report.geometry_count,
            constraint_count=report.constraint_count,
            fully_constrained=report.fully_constrained,
            over_constrained=report.over_constrained,
            conflicts=report.conflicts,
            free_geometry=report.free_geometry,
            solver_message=report.solver_message,
        )

    # --- References ---------------------------------------------------------------------------------------

    def persistent_reference(self, request: EntityRequest) -> PersistentRef:
        return PersistentRef(adapter="fake", token=request.entity.id, signature=self.signature(request))

    def resolve_reference(self, request: ResolveRequest) -> EntityResult:
        doc = self._doc(request)
        kind = request.ref.signature.kind
        handle_class = _HANDLE_CLASSES.get(kind)
        if request.ref.adapter != "fake" or handle_class is None:
            raise self._error(ErrorCode.invalid_reference, f"cannot resolve reference {request.ref.token}", request)
        token = request.ref.token
        exists = {
            "face": lambda: self._exists_face(doc, token),
            "edge": lambda: self._exists_edge(doc, token),
            "vertex": lambda: token in doc.vertices,
            "body": lambda: self._body_exists(doc, token),
            "feature": lambda: any(f.id == token for f in doc.features),
            "comp": lambda: token in doc.components,
        }[kind]
        if not exists():
            raise self._error(ErrorCode.invalid_reference, f"stale reference {token}", request)
        return EntityResult(entity=handle_class(id=token))

    def signature(self, request: EntityRequest) -> GeometricSignature:
        doc = self._doc(request)
        entity = request.entity
        if isinstance(entity, FaceHandle):
            face, _ = self._locate_face(doc, entity.id, request)
            return GeometricSignature(
                kind="face",
                surface=face.surface,
                normal=face.normal,
                axis=face.axis,
                center=face.center,
                radius=face.radius,
                area_mm2=face.area_mm2,
            )
        if isinstance(entity, EdgeHandle):
            edge = self._edge(doc, entity.id, request)
            center = edge.center if edge.center is not None else _midpoint(edge)
            return GeometricSignature(
                kind="edge",
                curve=edge.curve,
                axis=edge.axis,
                center=center,
                radius=edge.radius,
                length_mm=edge.length_mm,
            )
        if isinstance(entity, VertexHandle):
            vertex = self._vertex(doc, entity.id, request)
            return GeometricSignature(kind="vertex", center=vertex.point)
        if isinstance(entity, BodyHandle):
            info = self._body_info(doc, entity.id, request)
            return GeometricSignature(kind="body", center=info.bbox.center())
        if isinstance(entity, ComponentHandle):
            component = self._component(doc, entity.id, request)
            return GeometricSignature(kind="comp", center=component.transform.origin)
        self._feature(doc, entity.id, request)
        return GeometricSignature(kind="feature")

    # --- Undo ---------------------------------------------------------------------------------------------

    def undo_last(self, request: Empty) -> UndoResult:
        doc = self._doc(request)
        if not doc.features:
            return UndoResult(reverted=None)
        feature = doc.features.pop()
        self._revert(doc, feature)
        for index, entry in enumerate(doc.history):  # the replay of ``edit_feature`` forgets it too, and what followed
            if entry.feature_id == feature.id:
                del doc.history[index:]
                break
        return UndoResult(reverted=FeatureHandle(id=feature.id))

    # --- In-place editing (optional) -------------------------------------------------------------------------

    def edit_feature(self, request: EditFeatureRequest) -> EditResult:
        """In-place editing by deterministic replay with stable ids; see ``adapters.fake.editing`` for what it edits,
        what it refuses (the same scope as the FreeCAD worker's) and how a failed edit leaves the document untouched."""
        return editing.edit_feature(self, request)

    def measure_bodies(self, request: Empty) -> MeasureBodiesResult:
        """Read-only reading of the whole part (see ``adapters.fake.editing.measure_bodies``)."""
        return editing.measure_bodies(self, request)

    def body_interference(self, request: BodyInterferenceRequest) -> BodyInterferenceResult:
        """Read-only pairwise reading of the part's bodies (see ``adapters.fake.editing.body_interference``)."""
        return editing.body_interference(self, request)

    # --- Saving the document (optional) ----------------------------------------------------------------------

    def save_document(self, request: SaveDocumentRequest) -> SaveDocumentResult:
        """Deterministic, atomic stand-in for a native save (see ``adapters.fake.saving``)."""
        return saving.save_document(self, request)

    def open_document(self, request: OpenDocumentRequest) -> OpenDocumentResult:
        """Reopen a file ``save_document`` wrote, same ids, and say how it re-binds (``adapters.fake.saving``)."""
        return saving.open_document(self, request)

    def import_geometry(self, request: ImportGeometryRequest) -> ImportGeometryResult:
        """A JSON stand-in for a STEP/IGES file read into a new document (``adapters.fake.foreign``)."""
        return foreign.import_geometry(self, request)

    def list_holes(self, request: ListHolesRequest) -> ListHolesResult:
        return foreign.list_holes(self, request)

    def design_attributes(self, request: Empty) -> DesignAttributes:
        doc = self._doc(request)
        return DesignAttributes(material=doc.material, route=doc.route, title=doc.title, roles=dict(doc.roles))

    def set_design_attributes(self, request: SetDesignAttributesRequest) -> DesignAttributes:
        doc = self._doc(request)
        if request.material is not None:
            doc.material = request.material.strip() or None
        if request.route is not None:
            doc.route = tuple(request.route)
        if request.title is not None:
            doc.title = request.title.strip() or None
        if request.roles is not None:
            doc.roles = dict(request.roles)
        return DesignAttributes(material=doc.material, route=doc.route, title=doc.title, roles=dict(doc.roles))

    def close_document(self, request: Empty) -> Ack:
        """Forget the active document (the fake keeps nothing on disk besides what ``save_document`` wrote)."""
        doc = self._doc(request)
        self.closed_ids.append(doc.id)
        self._documents.pop(doc.id, None)
        self._doc_paths.pop(doc.id, None)
        self._active = None
        return Ack()

    def simulate_engineer_save(self, path: Path) -> None:
        """Test-only: the engineer saved the active document to ``path`` with the CAD's own save (Ctrl+S)."""
        saving.simulate_engineer_save(self, path)

    # --- Selection (optional) -------------------------------------------------------------------------------

    def get_selection(self, request: Empty) -> SelectionResult:
        doc = self._doc(request)
        return SelectionResult(
            entries=tuple(self._selection_entry(doc, handle, request) for handle in self._selection),
            unresolved=self._selection_unresolved,
            sheet=self.sheet_selection,
        )

    def show_entities(self, request: ShowEntitiesRequest) -> ShowEntitiesResult:
        """Test double for the agent-to-engineer direction: ``visible=True`` (default) reports
        ``highlight`` and records the entities actually painted (``self.highlighted``, replaced every
        call -- the previous highlight is always cleared first, whether or not new entities follow);
        ``visible=False`` stands in for a headless CAD and reports ``unavailable`` with a reason,
        never raises. Never touches ``self._selection`` (the engineer's own UI selection)."""
        self.show_entities_calls.append(request)
        self.highlighted = {}
        self.highlighted_items = ()
        if not self.visible:
            return ShowEntitiesResult(shown="unavailable", reason="fake adapter is headless (visible=False)")
        if request.entities:
            self.highlighted = {handle.id: handle for handle in request.entities}
        if request.items:
            self.highlighted_items = request.items
        detail = f"sheet: {', '.join(request.items)}" if request.items else ""
        return ShowEntitiesResult(shown="highlight", detail=detail)

    def _selection_entry(self, doc: FakeDocument, handle: AnyHandle, request: Request) -> SelectionEntry:
        ref = self.persistent_reference(EntityRequest(operation_id=f"{request.operation_id}.ref", entity=handle))
        kind = ref.signature.kind
        if kind not in ("face", "edge", "vertex"):
            raise invalid_request(
                f"selection entry {handle.id} is a {kind}, not a face/edge/vertex", request.operation_id
            )
        body, feature = self._selection_owner(doc, kind, handle.id)
        owner_label = next((f.name for f in doc.features if feature is not None and f.id == feature.id), "")
        label = self._selection_labels.get(handle.id, f"{kind} {handle.id}")
        return SelectionEntry(
            ref=ref,
            kind=kind,  # type: ignore[arg-type]
            label=label,
            owner_label=owner_label,
            body=body,
            feature=feature,
            on_sheet=self.on_sheet.get(handle.id, ()),
        )

    def _selection_owner(
        self, doc: FakeDocument, kind: str, entity_id: str
    ) -> tuple[BodyHandle | None, FeatureHandle | None]:
        """The body/feature that owns a selected face/edge/vertex, read off the fake's own topology --
        used only for the selection's display context (``SelectionEntry.owner_label``/``body``), never
        for identity. An edge/vertex borrows its first adjacent face's owner."""
        face_id: str | None = None
        if kind == "face":
            face_id = entity_id
        elif kind == "edge":
            edge = doc.edges.get(entity_id)
            face_id = edge.faces[0] if edge is not None else None
        elif kind == "vertex":
            vertex = doc.vertices.get(entity_id)
            first_edge = doc.edges.get(vertex.edges[0]) if vertex is not None and vertex.edges else None
            face_id = first_edge.faces[0] if first_edge is not None else None
        face = doc.faces.get(face_id) if face_id is not None else None
        if face is None:
            return None, None
        return BodyHandle(id=face.body), FeatureHandle(id=face.created_by)

    # --- Test-only: manual edits of the document ------------------
    #
    # ``feature_tree`` returns the DOCUMENT's features, hand-made ones included. The fake has no UI, so
    # these three methods stand in for the engineer editing the tree by hand; they are not contract
    # tools and exist only so ``diff_tree`` obsolescence can be exercised on Linux.

    def inject_manual_feature(
        self, kind: str, name: str, parameters: dict[str, float | str | bool] | None = None
    ) -> FeatureHandle:
        """Append a geometry-less feature to the active document as if the engineer had created it by hand."""
        doc = self._doc(Empty(operation_id="manual"))
        doc.hand_edited = True
        feature = FakeFeature(id=doc.next_id("feature"), kind=kind, name=name, parameters=dict(parameters or {}))
        doc.features.append(feature)
        return FeatureHandle(id=feature.id)

    def hand_edit_feature(
        self,
        feature: FeatureHandle,
        *,
        name: str | None = None,
        kind: str | None = None,
        parameters: dict[str, float | str | bool] | None = None,
    ) -> None:
        """Rename / retype / re-parameterise a feature in place (tree metadata only, geometry untouched): the
        engineer editing the tree by hand. (The contract's ``edit_feature`` is the harness editing in place.)"""
        doc = self._doc(Empty(operation_id="manual"))
        doc.hand_edited = True
        target = self._feature(doc, feature.id, Empty(operation_id="manual"))
        if name is not None:
            target.name = name
        if kind is not None:
            target.kind = kind
        if parameters is not None:
            target.parameters = dict(parameters)

    def delete_feature(self, feature: FeatureHandle) -> None:
        """Remove a feature from the document by hand (its geometry is reverted like ``undo_last`` would).

        Test-only path: like ``undo_last`` it also forgets every datum made after that feature started, so
        deleting a NON-last feature wipes the datums of the features after it too."""
        doc = self._doc(Empty(operation_id="manual"))
        target = self._feature(doc, feature.id, Empty(operation_id="manual"))
        doc.hand_edited = True
        doc.features.remove(target)
        self._revert(doc, target)

    # --- Test-only: CAD UI selection (contract.SelectionProducer seam) ------------------------------------------
    #
    # The fake has no UI, so ``set_selection`` stands in for the engineer clicking faces/edges/vertices in
    # the 3D view: it records the selection and, when a listener is registered (``set_selection_listener``,
    # ``contract.adapter.SelectionProducer``), notifies it immediately -- the same push a FreeCAD worker's
    # ``Gui.Selection`` observer must perform once wired on that side (out of scope here).

    def set_selection(
        self,
        entities: Sequence[AnyHandle],
        *,
        labels: Mapping[str, str] | None = None,
        unresolved: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Set the current UI selection (handles already resolvable in the active document) and push it
        to the registered listener, if any. ``labels`` overrides the default ``"<kind> <id>"`` display
        label per handle id. ``unresolved`` stands in for the FreeCAD worker's own unresolvable picks (a
        sketch, a datum, ...): ``(label, reason)`` pairs reported verbatim on ``get_selection().unresolved``
        -- the fake has no such objects of its own to select, so this is the only way to exercise that
        reporting path against the fake adapter. Replaces, never merges, both lists -- like the resolved
        ``entities`` themselves."""
        self._selection = tuple(entities)
        if labels:
            self._selection_labels.update(labels)
        self._selection_unresolved = tuple(
            UnresolvedSelection(label=label, reason=reason) for label, reason in unresolved
        )
        self._notify_selection()

    def set_selection_listener(self, listener: SelectionListener) -> None:
        """``contract.adapter.SelectionProducer``: register the callback ``set_selection`` pushes to."""
        self._selection_listener = listener

    def _notify_selection(self) -> None:
        if self._selection_listener is None or self._active is None:
            return
        self._selection_listener(self.get_selection(Empty(operation_id="selection.notify")))

    # --- Test-only: live manual-edit notice (contract.ManualEditProducer seam) ---------------------------------
    #
    # Unlike ``inject_manual_feature``/``edit_feature``/``delete_feature`` above (which only become
    # visible through a pull-based tree diff, on the next tool call), ``notify_manual_edit``
    # stands in for a FreeCAD document-change observer: it pushes the notice to the registered listener
    # immediately, so the chat can render "Detected hand edit: ..." without any tool call in between.

    def notify_manual_edit(
        self,
        *,
        artefact_kind: str,
        ref: str,
        by: str,
        reason: str,
        changes: Sequence[str],
        document: str = "",
        name: str = "",
        action: str = "edited",
    ) -> None:
        """Push a manual-edit notice to the registered listener, if any (test-only; a no-op otherwise)."""
        if self._manual_edit_listener is None:
            return
        self._manual_edit_listener(
            ManualEditNotice(
                artefact_kind=artefact_kind,
                ref=ref,
                by=by,
                reason=reason,
                changes=tuple(changes),
                document=document,
                name=name,
                action=action,
            )
        )

    def set_progress_listener(self, listener: ProgressListener | None) -> None:
        """``contract.adapter.ProgressProducer``: where ``import_geometry`` reports its phases."""
        self._progress_listener = listener

    def tell_progress(self, notice: ProgressNotice) -> None:
        if self._progress_listener is not None:
            self._progress_listener(notice)

    def set_manual_edit_listener(self, listener: ManualEditListener) -> None:
        """``contract.adapter.ManualEditProducer``: register the callback ``notify_manual_edit`` pushes to."""
        self._manual_edit_listener = listener

    # --- Internals: construction pipeline ------------------------------------------------------------------------

    def _construct(
        self,
        doc: FakeDocument,
        kind: str,
        request: Request,
        build: Callable[[FakeFeature], None],
        *,
        sketch: FakeSketch | None = None,
    ) -> OperationResult:
        started = time.perf_counter()
        fired = self._begin(request)
        feature = self._new_feature(doc, kind, request, sketch)
        doc.features.append(feature)
        try:
            build(feature)
            self._apply_post_faults(doc, feature, fired)
        except AdapterError:
            doc.features.pop()
            self._revert(doc, feature)
            raise
        return self._result(feature, request, fired, started)

    def _mate(
        self,
        doc: FakeDocument,
        kind: str,
        request: Request,
        component: FakeComponent,
        offset: Vec3 | Transform | None,
        *,
        resolve: Callable[[tuple[Fault, ...]], Vec3] | None = None,
    ) -> OperationResult:
        def build(feature: FakeFeature) -> None:
            feature.component = component.id
            feature.previous_transform = component.transform
            feature.mate = doc.next_id("mate")
            # ``resolve`` (coincident/distance, fault-aware) always yields a pure translation; ``offset`` may
            # already be a full rotate + translate delta (mate_concentric, general case, 5.6).
            result = resolve(self._last_fired) if resolve is not None else offset
            assert result is not None
            delta = result if isinstance(result, Transform) else Transform(origin=result)
            asm.transform_component(component, delta)
            component.mates.append(feature.mate)

        return self._construct(doc, kind, request, build)

    def _pattern(
        self,
        doc: FakeDocument,
        kind: str,
        request: Request,
        seeds: list[FakeFeature],
        transforms: list[Transform],
        instances: int,
    ) -> OperationResult:
        for seed in seeds:
            if not seed.primitives:
                raise invalid_request(f"feature {seed.id} has no geometry to pattern", request.operation_id)

        def build(feature: FakeFeature) -> None:
            feature.instance_count = instances
            for seed in seeds:
                body = self._body_of_primitive(doc, seed.primitives[0])
                b = Build(doc, feature, body)
                for t in transforms:
                    clone_feature(b, seed, t)

        return self._construct(doc, kind, request, build)

    def _begin(self, request: Request) -> tuple[Fault, ...]:
        fired = self._faults.begin(request.operation_id)
        self._last_fired = fired
        for fault in fired:
            if fault.kind is FaultKind.dangling_reference:
                self._invalidate_referenced_face(request)
                raise AdapterError(
                    ErrorInfo(
                        code=ErrorCode.invalid_reference,
                        message=f"injected fault on {request.operation_id}: referenced face edited by hand",
                        operation_id=request.operation_id,
                        details={"fault": fault.kind.value, "attempt": self._faults.attempts(request.operation_id)},
                        recoverable=fault.recoverable,
                    )
                )
            if fault.kind is FaultKind.raise_error:
                raise AdapterError(
                    ErrorInfo(
                        code=fault.error_code,
                        message=f"injected fault on {request.operation_id}",
                        operation_id=request.operation_id,
                        details={"fault": fault.kind.value, "attempt": self._faults.attempts(request.operation_id)},
                        recoverable=fault.recoverable,
                    )
                )
        return fired

    def _invalidate_referenced_face(self, request: Request) -> None:
        """``FaultKind.dangling_reference``: renames
        the request's own referenced face to a fresh id in the current document, as if the engineer had
        edited it by hand between the original resolution and the repair's re-resolution -- the geometry
        (normal, centre, area) is untouched, only the handle id changes, so a later live re-probe
        (``Executor.probe_references``) finds a different, still-valid handle instead of the stale one."""
        face_ref = getattr(request, "face", None)
        if not isinstance(face_ref, FaceHandle):
            return
        doc = self._active
        if doc is None:
            return
        old_id = face_ref.id
        face = doc.faces.pop(old_id, None)
        if face is None:
            return
        doc.hand_edited = True  # the injected rename is not in the recorded history: a replay would not reproduce it
        new_id = doc.next_id("face")
        face.id = new_id
        doc.faces[new_id] = face
        for feature in doc.features:
            feature.created_faces = [new_id if f == old_id else f for f in feature.created_faces]
            feature.modified_faces = [new_id if f == old_id else f for f in feature.modified_faces]
        for edge in doc.edges.values():
            edge.faces = tuple(new_id if f == old_id else f for f in edge.faces)  # type: ignore[assignment]

    def _apply_post_faults(self, doc: FakeDocument, feature: FakeFeature, fired: tuple[Fault, ...]) -> None:
        for fault in fired:
            if fault.kind is FaultKind.wrong_volume:
                for prim_id in feature.primitives:
                    doc.primitives[prim_id].volume_mm3 *= fault.volume_factor
            elif fault.kind is FaultKind.wrong_face_count:
                body = next(iter(feature.created_bodies + feature.modified_bodies), "body:0")
                face = FakeFace(
                    id=doc.next_id("face"),
                    surface=SurfaceType.plane,
                    normal=(0.0, 0.0, 1.0),
                    axis=None,
                    center=_ORIGIN,
                    radius=None,
                    area_mm2=0.0,
                    bbox=_ZERO_BBOX,
                    body=body,
                    created_by=feature.id,
                    polygon=[],
                )
                doc.faces[face.id] = face
                feature.created_faces.append(face.id)

    def _result(
        self, feature: FakeFeature, request: Request, fired: tuple[Fault, ...], started: float
    ) -> OperationResult:
        rebuild_ok = not any(f.kind is FaultKind.rebuild_failed for f in fired)
        return OperationResult(
            operation_id=request.operation_id,
            feature=FeatureHandle(id=feature.id),
            rebuild_ok=rebuild_ok,
            created_faces=tuple(FaceHandle(id=f) for f in feature.created_faces),
            modified_faces=tuple(FaceHandle(id=f) for f in feature.modified_faces),
            created_edges=tuple(EdgeHandle(id=e) for e in feature.created_edges),
            created_bodies=tuple(BodyHandle(id=b) for b in feature.created_bodies),
            modified_bodies=tuple(BodyHandle(id=b) for b in feature.modified_bodies),
            sketch=SketchHandle(id=feature.sketch) if feature.sketch else None,
            component=ComponentHandle(id=feature.component) if feature.component else None,
            mate=MateHandle(id=feature.mate) if feature.mate else None,
            joint=self._joint_info(self._active.joints[feature.joint])
            if feature.joint and self._active is not None and feature.joint in self._active.joints
            else None,
            instance_count=feature.instance_count,
            face_count=feature.face_count,
            edge_count=feature.edge_count,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    def _new_feature(self, doc: FakeDocument, kind: str, request: Request, sketch: FakeSketch | None) -> FakeFeature:
        feature_id = doc.next_id("feature")
        doc.datum_marks[feature_id] = (frozenset(doc.datum_planes), frozenset(doc.datum_axes))
        number = feature_id.split(":", 1)[1]
        parameters: dict[str, float | str | bool] = {}
        for key, value in request.model_dump().items():
            if key == "operation_id" or value is None:
                continue
            if key == "thread" and isinstance(value, dict):
                # a tapped hole's cosmetic thread: metadata only, flattened so the feature tree reads it
                parameters["thread_size"] = str(value["size"])
                if value.get("depth_mm") is not None:
                    parameters["thread_depth_mm"] = float(value["depth_mm"])
                continue
            if isinstance(value, bool | int | float | str):
                parameters[key] = value
            elif isinstance(value, dict) and "id" in value:
                parameters[key] = str(value["id"])
            else:
                parameters[key] = str(value)
        named: dict[str, float] = {}
        if sketch is not None:
            named = _named_dimension_values(sketch)
        return FakeFeature(
            id=feature_id,
            kind=kind,
            name=f"{kind}_{number}",
            parameters=parameters,
            sketch=sketch.id if sketch else None,
            named_dimensions=named,
        )

    def _revert(self, doc: FakeDocument, feature: FakeFeature) -> None:
        """Undo of 7.7: remove what the feature created and restore what it modified."""
        marks = doc.datum_marks.pop(feature.id, None)
        if marks is not None:
            planes_before, axes_before = marks
            for datum_id in [d for d in doc.datum_planes if d not in planes_before]:
                del doc.datum_planes[datum_id]
            for datum_id in [d for d in doc.datum_axes if d not in axes_before]:
                del doc.datum_axes[datum_id]
        if feature.kind == "component" and feature.component is not None:
            doc.components.pop(feature.component, None)
            return
        if feature.kind == "fix_component" and feature.component is not None:
            doc.components[feature.component].fixed = bool(feature.previous_fixed)
            return
        if feature.kind == "place_component" and feature.component is not None:
            if feature.previous_transform is not None and feature.component in doc.components:
                doc.components[feature.component].transform = feature.previous_transform
            return
        if feature.kind == "add_joint" and feature.joint is not None:
            doc.joints.pop(feature.joint, None)
            return
        if feature.kind == "set_joint" and feature.joint in doc.joints and feature.component in doc.components:
            doc.components[feature.component].transform = feature.previous_transform or Transform.identity()
            doc.joints[feature.joint].value = feature.previous_value or 0.0
            return
        if feature.kind.startswith("mate_") and feature.component is not None:
            component = doc.components[feature.component]
            if feature.previous_transform is not None:
                component.transform = feature.previous_transform
            if feature.mate in component.mates:
                component.mates.remove(feature.mate)
            return
        for edge_id in feature.created_edges:
            edge = doc.edges.pop(edge_id, None)
            if edge is None:
                continue
            for face_id in edge.faces:
                face = doc.faces.get(face_id)
                if face is not None and edge_id in face.edges:
                    face.edges.remove(edge_id)
            for vertex in doc.vertices.values():
                if edge_id in vertex.edges:
                    vertex.edges.remove(edge_id)
        for vertex_id in feature.created_vertices:
            doc.vertices.pop(vertex_id, None)
        for face_id in feature.created_faces:
            doc.faces.pop(face_id, None)
        for prim_id in feature.primitives:
            doc.primitives.pop(prim_id, None)
            for prim_ids in doc.bodies.values():
                if prim_id in prim_ids:
                    prim_ids.remove(prim_id)
        for body_id in feature.created_bodies:
            doc.bodies.pop(body_id, None)
        for face_id in feature.modified_faces:
            face = doc.faces.get(face_id)
            if face is not None and feature.id in face.modified_by:
                face.modified_by.remove(feature.id)
        for face_id, delta in feature.face_area_deltas.items():
            if face_id in doc.faces and face_id not in feature.replaced:
                doc.faces[face_id].area_mm2 -= delta
        stores: dict[str, dict[str, Any]] = {
            "face": doc.faces,
            "edge": doc.edges,
            "vertex": doc.vertices,
            "prim": doc.primitives,
            "body": doc.bodies,
        }
        for entity_id, snapshot in feature.replaced.items():
            stores[entity_id.split(":", 1)[0]][entity_id] = copy.deepcopy(snapshot)

    # --- Internals: booleans ------------------------------------------------------------------------------------

    def _boolean(
        self,
        doc: FakeDocument,
        feature: FakeFeature,
        kind: BooleanKind,
        target: str,
        tools: list[str],
        request: Request,
        *,
        keep_tools: bool = False,
    ) -> None:
        target_prims = doc.body_primitives(target)
        tool_prims = [p for tool in tools for p in doc.body_primitives(tool)]
        if any(p.kind is PrimitiveKind.sweep for p in (*target_prims, *tool_prims)):
            raise not_supported(
                f"boolean {kind.value} with a swept body is not supported: sweep into the body you want to combine "
                "(merge) or build the shape from extrude/revolve",
                request.operation_id,
            )
        corrections: list[tuple[Primitive, int]] = []
        for p in target_prims:
            for q in tool_prims:
                inter = intersection_primitive(p, q)
                if inter is None:
                    raise not_supported(
                        f"boolean {kind.value} between {p.kind.value} {p.id} and {q.kind.value} {q.id} "
                        "is outside the containment rules of the fake",
                        request.operation_id,
                    )
                if inter.volume_mm3 > 1e-9:
                    corrections.append((inter, p.sign * q.sign))
        if kind is BooleanKind.intersect:
            for prim in [*target_prims, *tool_prims]:
                if prim.sign > 0 and as_box(prim) is None:
                    raise not_supported("boolean intersect supports only axis-aligned prisms", request.operation_id)

        b = Build(doc, feature, target)
        b.touch(target)
        for prim in target_prims:
            b.touch(prim.id)
        feature.modified_bodies.append(target)
        tool_faces = [f for f in doc.faces.values() if f.body in tools]
        for tool in tools:
            b.touch(tool)
            for prim_id in doc.bodies[tool]:
                b.touch(prim_id)
        if kind is BooleanKind.union:
            for tool in tools:
                doc.bodies[target].extend(doc.bodies.pop(tool))
            for face in tool_faces:
                b.touch(face.id)
                face.body = target
                b.mark_modified(face.id)
            for inter, sign in corrections:
                self._add_correction(b, inter, -sign)
        elif kind is BooleanKind.subtract:
            if keep_tools:
                # Fish-mouth coping: every tool body survives untouched (own faces/edges, own primitives,
                # still a live entry in doc.bodies) -- target gets its own COPY of each tool face (new id,
                # normal flipped) as the new cavity wall, so a later faces_of(target-op) sees the coped
                # notch without disturbing the kept tool's own topology. The copies also get their own
                # edges and vertices (new ids, remapped onto the copied faces) so edges_of/edges_between/
                # vertices_of -- and a fillet on the coped face -- work there exactly as on any other face
                # (a cavity face with edges=[] would otherwise silently report none of it).
                face_id_map: dict[str, str] = {}
                for face in tool_faces:
                    cavity = face.transformed(Transform.identity(), new_id=doc.next_id("face"))
                    cavity.body = target
                    cavity.created_by = feature.id
                    cavity.modified_by = []
                    cavity.edges = []
                    if cavity.normal is not None:
                        cavity.normal = vscale(cavity.normal, -1.0)
                    doc.faces[cavity.id] = cavity
                    feature.created_faces.append(cavity.id)
                    face_id_map[face.id] = cavity.id
                edge_id_map: dict[str, str] = {}
                for edge in list(doc.edges.values()):
                    if edge.faces[0] not in face_id_map or edge.faces[1] not in face_id_map:
                        continue
                    cavity_edge = edge.transformed(Transform.identity(), new_id=doc.next_id("edge"))
                    cavity_edge.faces = (face_id_map[edge.faces[0]], face_id_map[edge.faces[1]])
                    doc.edges[cavity_edge.id] = cavity_edge
                    feature.created_edges.append(cavity_edge.id)
                    edge_id_map[edge.id] = cavity_edge.id
                    doc.faces[cavity_edge.faces[0]].edges.append(cavity_edge.id)
                    doc.faces[cavity_edge.faces[1]].edges.append(cavity_edge.id)
                for vertex in list(doc.vertices.values()):
                    mapped_edges = [edge_id_map[e] for e in vertex.edges if e in edge_id_map]
                    if not mapped_edges:
                        continue
                    cavity_vertex = vertex.transformed(Transform.identity(), new_id=doc.next_id("vertex"))
                    cavity_vertex.edges = mapped_edges
                    doc.vertices[cavity_vertex.id] = cavity_vertex
                    feature.created_vertices.append(cavity_vertex.id)
            else:
                for tool in tools:
                    for prim_id in doc.bodies.pop(tool):
                        doc.primitives.pop(prim_id)
                for face in tool_faces:
                    b.touch(face.id)
                    face.body = target
                    if face.normal is not None:
                        face.normal = vscale(face.normal, -1.0)
                    b.mark_modified(face.id)
            for inter, sign in corrections:
                self._add_correction(b, inter, -sign)
        else:
            removed_faces = [f for f in doc.faces.values() if f.body == target] + tool_faces
            removed_ids = {f.id for f in removed_faces}
            removed_edges = [e for e in doc.edges.values() if e.faces[0] in removed_ids or e.faces[1] in removed_ids]
            removed_edge_ids = {e.id for e in removed_edges}
            removed_vertices = [v for v in doc.vertices.values() if any(e in removed_edge_ids for e in v.edges)]
            for entity in [*removed_vertices, *removed_edges, *removed_faces]:
                b.touch(entity.id)
            for vertex in removed_vertices:
                doc.vertices.pop(vertex.id)
            for edge in removed_edges:
                doc.edges.pop(edge.id)
            for face in removed_faces:
                doc.faces.pop(face.id)
            for body in [target, *tools]:
                for prim_id in doc.bodies.pop(body):
                    doc.primitives.pop(prim_id)
            doc.bodies[target] = []
            for inter, sign in corrections:
                if as_box(inter) is not None:
                    assert inter.polygon is not None and inter.direction is not None and inter.depth is not None
                    build_prism(b, list(inter.polygon), inter.direction, inter.depth, sign)
                else:
                    self._add_correction(b, inter, sign)

    @staticmethod
    def _add_correction(b: Build, inter: Primitive, sign: int) -> None:
        inter.id = b.new_primitive_id()
        inter.sign = sign
        inter.feature = b.feature.id
        b.add_primitive(inter)

    # --- Internals: lookups ------------------------------------------------------------------------------------

    def _doc(self, request: Request) -> FakeDocument:
        if self._active is None:
            raise self._error(ErrorCode.no_active_document, "no active document", request)
        return self._active

    def _document_info(self, doc: FakeDocument) -> DocumentInfo:
        return DocumentInfo(handle=DocumentHandle(id=doc.id), kind=doc.kind, name=doc.name, units="mm")

    @staticmethod
    def _error(code: ErrorCode, message: str, request: Request, *, recoverable: bool = False) -> AdapterError:
        return AdapterError(
            ErrorInfo(code=code, message=message, operation_id=request.operation_id, recoverable=recoverable)
        )

    def _sketch(self, doc: FakeDocument, sketch_id: str, request: Request) -> FakeSketch:
        sketch = doc.sketches.get(sketch_id)
        if sketch is None:
            raise self._error(ErrorCode.invalid_reference, f"unknown sketch {sketch_id}", request)
        return sketch

    def _defined_sketch(self, doc: FakeDocument, sketch_id: str, request: Request) -> FakeSketch:
        """The sketch a feature is built on: refused unless fully defined, as the real adapters refuse it (same reason
        code as an external adapter's ``sketch_not_fully_defined``)."""
        sketch = self._sketch(doc, sketch_id, request)
        if sketch.free_dof > 0:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.invalid_request,
                    message=(
                        f"the sketch {sketch.id} still has {sketch.free_dof} degree(s) of freedom: every sketch "
                        "behind a feature ends fully defined by dimensions"
                    ),
                    operation_id=request.operation_id,
                    details={"sketch": sketch.id, "reason": "sketch_not_fully_defined", "dof": sketch.free_dof},
                    recoverable=False,
                )
            )
        return sketch

    def _feature(self, doc: FakeDocument, feature_id: str, request: Request) -> FakeFeature:
        for feature in doc.features:
            if feature.id == feature_id:
                return feature
        raise self._error(ErrorCode.invalid_reference, f"unknown feature {feature_id}", request)

    def _component(self, doc: FakeDocument, component_id: str, request: Request) -> FakeComponent:
        component = doc.components.get(component_id)
        if component is None:
            raise self._error(ErrorCode.invalid_reference, f"unknown component {component_id}", request)
        return component

    def _locate_face(self, doc: FakeDocument, face_id: str, request: Request) -> tuple[FakeFace, str | None]:
        split = asm.split_prefixed(face_id)
        if split is None:
            face = doc.faces.get(face_id)
            if face is None:
                raise self._error(ErrorCode.invalid_reference, f"unknown face {face_id}", request)
            return face, None
        component_id, local_id = split
        component = self._component(doc, component_id, request)
        if local_id not in component.part.faces:
            raise self._error(ErrorCode.invalid_reference, f"unknown face {face_id}", request)
        return asm.component_face(component, local_id), component_id

    def _exists_face(self, doc: FakeDocument, face_id: str) -> bool:
        split = asm.split_prefixed(face_id)
        if split is None:
            return face_id in doc.faces
        component = doc.components.get(split[0])
        return component is not None and split[1] in component.part.faces

    def _exists_edge(self, doc: FakeDocument, edge_id: str) -> bool:
        split = asm.split_prefixed(edge_id)
        if split is None:
            return edge_id in doc.edges
        component = doc.components.get(split[0])
        return component is not None and split[1] in component.part.edges

    def _edge(self, doc: FakeDocument, edge_id: str, request: Request) -> FakeEdge:
        split = asm.split_prefixed(edge_id)
        if split is None:
            edge = doc.edges.get(edge_id)
            if edge is None:
                raise self._error(ErrorCode.invalid_reference, f"unknown edge {edge_id}", request)
            return edge
        component = self._component(doc, split[0], request)
        local = component.part.edges.get(split[1])
        if local is None:
            raise self._error(ErrorCode.invalid_reference, f"unknown edge {edge_id}", request)
        edge = local.transformed(component.transform, new_id=edge_id)
        edge.faces = (asm.prefixed(component.id, local.faces[0]), asm.prefixed(component.id, local.faces[1]))
        return edge

    def _vertex(self, doc: FakeDocument, vertex_id: str, request: Request) -> FakeVertex:
        split = asm.split_prefixed(vertex_id)
        if split is None:
            vertex = doc.vertices.get(vertex_id)
            if vertex is None:
                raise self._error(ErrorCode.invalid_reference, f"unknown vertex {vertex_id}", request)
            return vertex
        component = self._component(doc, split[0], request)
        local = component.part.vertices.get(split[1])
        if local is None:
            raise self._error(ErrorCode.invalid_reference, f"unknown vertex {vertex_id}", request)
        vertex = local.transformed(component.transform, new_id=vertex_id)
        vertex.edges = [asm.prefixed(component.id, e) for e in local.edges]
        return vertex

    def _all_edges(self, doc: FakeDocument) -> list[FakeEdge]:
        edges = list(doc.edges.values())
        for component in doc.components.values():
            for local in component.part.edges.values():
                edge = local.transformed(component.transform, new_id=asm.prefixed(component.id, local.id))
                edge.faces = (asm.prefixed(component.id, local.faces[0]), asm.prefixed(component.id, local.faces[1]))
                edges.append(edge)
        return edges

    def _all_vertices(self, doc: FakeDocument) -> list[FakeVertex]:
        vertices = list(doc.vertices.values())
        for component in doc.components.values():
            for local in component.part.vertices.values():
                vertex = local.transformed(component.transform, new_id=asm.prefixed(component.id, local.id))
                vertex.edges = [asm.prefixed(component.id, e) for e in local.edges]
                vertices.append(vertex)
        return vertices

    def _body_exists(self, doc: FakeDocument, body_id: str) -> bool:
        split = asm.split_prefixed(body_id)
        if split is None:
            return body_id in doc.bodies
        component = doc.components.get(split[0])
        return component is not None and split[1] in component.part.bodies

    def _body_of_primitive(self, doc: FakeDocument, prim_id: str) -> str:
        for body, prim_ids in doc.bodies.items():
            if prim_id in prim_ids:
                return body
        msg = f"primitive {prim_id} belongs to no body"
        raise KeyError(msg)

    def _scope(self, doc: FakeDocument, request: MassRequest) -> tuple[list[Primitive], list[FakeFace]]:
        """Primitives and faces (in document space) covered by a MassRequest."""
        if request.component is not None:
            component = self._component(doc, request.component.id, request)
            faces = [asm.component_face(component, f) for f in component.part.faces]
            return asm.component_primitives(component), faces
        if request.body is not None:
            body_id = request.body.id
            split = asm.split_prefixed(body_id)
            if split is None:
                if body_id not in doc.bodies:
                    raise self._error(ErrorCode.invalid_reference, f"unknown body {body_id}", request)
                return doc.body_primitives(body_id), [f for f in doc.faces.values() if f.body == body_id]
            component = self._component(doc, split[0], request)
            if split[1] not in component.part.bodies:
                raise self._error(ErrorCode.invalid_reference, f"unknown body {body_id}", request)
            prims = [
                component.part.primitives[p].transformed(component.transform) for p in component.part.bodies[split[1]]
            ]
            faces = [asm.component_face(component, f.id) for f in component.part.faces.values() if f.body == split[1]]
            return prims, faces
        prims = list(doc.primitives.values())
        faces = list(doc.faces.values())
        for component in doc.components.values():
            prims.extend(asm.component_primitives(component))
            faces.extend(asm.component_face(component, f) for f in component.part.faces)
        return prims, faces

    def _mate_faces(
        self, doc: FakeDocument, a_id: str, b_id: str, request: Request
    ) -> tuple[FakeFace, FakeFace, FakeComponent]:
        a, comp_a = self._locate_face(doc, a_id, request)
        b, comp_b = self._locate_face(doc, b_id, request)
        if comp_a is None or comp_b is None:
            raise asm.mate_failed("mates require faces of two components", request.operation_id)
        if comp_a == comp_b:
            raise asm.mate_failed("mates require faces of two different components", request.operation_id)
        component = doc.components[comp_b]
        if component.fixed:
            raise asm.mate_failed(f"component {comp_b} owning face b is fixed", request.operation_id)
        self._refuse_if_jointed(doc, component, request)
        return a, b, component

    # --- Internals: geometry helpers ---------------------------------------------------------------------------------

    @staticmethod
    def _profile_points(profile: Profile) -> list[tuple[float, float]]:
        if profile.kind == "rect":
            assert profile.center is not None
            width = profile.bbox_uv[1][0] - profile.bbox_uv[0][0]
            height = profile.bbox_uv[1][1] - profile.bbox_uv[0][1]
            return rect_points(profile.center, width, height)
        assert profile.points is not None
        return orient_ccw(list(profile.points))

    def _base_loop(self, sketch: FakeSketch, profile: Profile, direction: Vec3, back: float) -> list[Vec3]:
        """World base loop, counter-clockwise about ``direction``, shifted by ``-back`` along it."""
        points = self._profile_points(profile)
        world = [sketch.frame.to_world(u, v) for u, v in points]
        if vdot(direction, sketch.frame.n) < 0:
            world.reverse()
        return [vsub(p, vscale(direction, back)) for p in world]

    def _new_body(self, doc: FakeDocument, feature: FakeFeature) -> str:
        body = doc.next_id("body")
        doc.bodies[body] = []
        feature.created_bodies.append(body)
        return body

    def _boss_body(self, doc: FakeDocument, sketch: FakeSketch, merge: bool, feature: FakeFeature) -> str:
        if merge and sketch.on_face is not None:
            return doc.faces[sketch.on_face].body
        if merge and len(doc.bodies) == 1:
            return next(iter(doc.bodies))
        return self._new_body(doc, feature)

    def _cut_body(self, doc: FakeDocument, sketch: FakeSketch, request: Request) -> str:
        if sketch.on_face is not None:
            return doc.faces[sketch.on_face].body
        if len(doc.bodies) == 1:
            return next(iter(doc.bodies))
        for profile in sketch.profiles:
            point = sketch.frame.to_world(*profile.centroid)
            for body in doc.bodies:
                box = positive_bbox(doc, body)
                if box is not None and all(box.min[i] - 1e-6 <= point[i] <= box.max[i] + 1e-6 for i in range(3)):
                    return body
        raise invalid_request("cut on a plane needs exactly one body or a profile inside a body", request.operation_id)

    @staticmethod
    def _entry_face(doc: FakeDocument, body: str, sketch: FakeSketch, point: Vec3, normal: Vec3) -> str | None:
        if sketch.on_face is not None:
            face = doc.faces[sketch.on_face]
            if face.normal is not None and abs(vdot(vsub(face.center, point), normal)) <= 1e-6:
                return face.id
        found = find_planar_face(doc, body, point, normal)
        return found.id if found else None

    # --- Internals: descriptors ---------------------------------------------------------------------------------------

    def _face_info(self, doc: FakeDocument, face: FakeFace, component: str | None) -> FaceInfo:
        if component is not None:
            created_by = next((f.id for f in doc.features if f.kind == "component" and f.component == component), "")
            modified: tuple[FeatureHandle, ...] = ()
        else:
            created_by = face.created_by
            modified = tuple(FeatureHandle(id=m) for m in face.modified_by)
        return FaceInfo(
            handle=FaceHandle(id=face.id),
            body=BodyHandle(id=face.body),
            component=ComponentHandle(id=component) if component else None,
            surface=face.surface,
            normal=face.normal,
            axis=face.axis,
            center=face.center,
            radius=face.radius,
            area_mm2=face.area_mm2,
            bbox=face.bbox,
            provenance=Provenance(created_by=FeatureHandle(id=created_by), modified_by=modified),
        )

    @staticmethod
    def _edge_info(edge: FakeEdge) -> EdgeInfo:
        return EdgeInfo(
            handle=EdgeHandle(id=edge.id),
            curve=edge.curve,
            faces=(FaceHandle(id=edge.faces[0]), FaceHandle(id=edge.faces[1])),
            start=edge.start,
            end=edge.end,
            center=edge.center,
            axis=edge.axis,
            radius=edge.radius,
            length_mm=edge.length_mm,
            bbox=edge.bbox,
            convex=edge.convex,
            dihedral_deg=edge.dihedral_deg,
        )

    @staticmethod
    def _vertex_info(vertex: FakeVertex) -> VertexInfo:
        return VertexInfo(
            handle=VertexHandle(id=vertex.id), point=vertex.point, edges=tuple(EdgeHandle(id=e) for e in vertex.edges)
        )

    def _body_info(self, doc: FakeDocument, body_id: str, request: Request) -> BodyInfo:
        prims, faces = self._scope(doc, MassRequest(operation_id=request.operation_id, body=BodyHandle(id=body_id)))
        volume = sum(p.sign * p.volume_mm3 for p in prims)
        box: BoundingBox | None = None
        for prim in prims:
            if prim.sign > 0:
                box = bbox_union(box, prim.bbox)
        created_by = next((f.id for f in doc.features if body_id in f.created_bodies), "")
        modified = tuple(FeatureHandle(id=f.id) for f in doc.features if body_id in f.modified_bodies)
        return BodyInfo(
            handle=BodyHandle(id=body_id),
            volume_mm3=volume,
            bbox=box or _ZERO_BBOX,
            face_count=len(faces),
            provenance=Provenance(created_by=FeatureHandle(id=created_by), modified_by=modified),
        )


def _fmt_vec(value: Vec3) -> str:
    return "(" + ", ".join(f"{v + 0.0:.6g}" for v in value) + ")"


def _midpoint(edge: FakeEdge) -> Vec3 | None:
    if edge.start is None or edge.end is None:
        return None
    return vscale(vadd(edge.start, edge.end), 0.5)


def _named_dimension_values(sketch: FakeSketch) -> dict[str, float]:
    by_name = {e.name: e for e in sketch.entities}
    values: dict[str, float] = {}
    for nd in sketch.named_dimensions:
        entity = by_name.get(nd.entity)
        if entity is None:
            continue
        value: float | None = None
        if isinstance(entity, RectEntity):
            value = {
                "width": entity.width,
                "height": entity.height,
                "chamfer": entity.chamfer_mm,
                "u": entity.center[0],
                "v": entity.center[1],
            }.get(nd.attribute)
        elif isinstance(entity, SquareEntity):
            value = {"side": entity.side, "u": entity.center[0], "v": entity.center[1]}.get(nd.attribute)
        elif isinstance(entity, CircleEntity):
            value = {
                "diameter": entity.diameter,
                "radius": entity.diameter / 2.0,
                "u": entity.center[0],
                "v": entity.center[1],
            }.get(nd.attribute)
        elif isinstance(entity, LineEntity):
            length = math.dist(entity.start, entity.end)
            value = {"length": length, "u": entity.start[0], "v": entity.start[1]}.get(nd.attribute)
        elif isinstance(entity, ArcEntity | Arc3Entity | ArcEndsEntity):
            geometry = arc_geometry(entity)
            value = {"radius": geometry.radius, "u": geometry.center[0], "v": geometry.center[1]}.get(nd.attribute)
        elif isinstance(entity, SlotEntity):
            value = {
                "width": entity.width,
                "length": math.dist(entity.start, entity.end),
                "u": entity.start[0],
                "v": entity.start[1],
            }.get(nd.attribute)
        if value is not None:
            values[nd.name] = float(value)
    return values


__all__ = ["FakeAdapter"]
