"""FreeCADAdapter: the client half of the FreeCAD adapter.

Every contract method is exactly one RPC to the worker running inside ``freecadcmd``:
``params = request.model_dump(mode="json")`` and ``Model.model_validate(result)`` on the way back.
Worker errors are rebuilt into ``AdapterError(ErrorInfo)``; transport failures map to ``timeout``
(recoverable) and ``session_lost`` (not recoverable). Health methods never raise on a lost worker.

``visible=True`` starts the worker inside the FreeCAD GUI binary instead of ``freecadcmd``: a real window
shows the document evolve operation by operation (accompanied mode), ``set_view`` orients it and ``capture``
renders it to PNG.
Same protocol, same worker package; only the spawn command and the connect timeout differ.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel, ValidationError

import cotaforge.adapters.freecad.rpc as freecad_rpc
from cotaforge.adapters.freecad import external_thread, numberformat
from cotaforge.adapters.freecad.fileunits import declared_length_unit
from cotaforge.adapters.freecad.pathlimit import path_length_problem
from cotaforge.adapters.freecad.rpc import (
    WORKER_DIR,
    Heartbeat,
    Liveness,
    RpcFailure,
    WorkerBlocked,
    WorkerBusy,
    WorkerLost,
    WorkerProcess,
    WorkerTimeout,
    find_freecad_root,
)
from cotaforge.contract import (
    Ack,
    Adapter,
    AdapterError,
    AddJointRequest,
    AnnotateDrawingRequest,
    ApiValidation,
    BaseFlangeRequest,
    BodiesResult,
    BodyInterferenceRequest,
    BodyInterferenceResult,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CanaryResult,
    Capabilities,
    CaptureDrawingRequest,
    CaptureDrawingResult,
    CaptureRequest,
    ChamferRequest,
    CircularPatternRequest,
    ComponentRequest,
    ConnectRequest,
    CopyBodyRequest,
    CutRequest,
    CylinderRequest,
    DatumAxisRequest,
    DatumAxisResult,
    DatumPlaneRequest,
    DatumPlaneResult,
    DescribeDrawingRequest,
    DescribeDrawingResult,
    DesignAttributes,
    DocumentInfo,
    DocumentStamp,
    DraftAnalysisRequest,
    DraftAnalysisResult,
    DraftRequest,
    DrawingRequest,
    DrawingResult,
    EdgeFlangeRequest,
    EdgeInfo,
    EdgeRequest,
    EdgesBetweenRequest,
    EdgesResult,
    EditFeatureRequest,
    EditResult,
    Empty,
    EntityRequest,
    EntityResult,
    ErrorCode,
    ErrorInfo,
    ExportDrawingRequest,
    ExportDrawingResult,
    ExportDxfRequest,
    ExportMeshRequest,
    ExportRequest,
    ExtensionRequest,
    ExtrudeRequest,
    FaceInfo,
    FaceRequest,
    FacesResult,
    FeatureList,
    FeatureRequest,
    FeatureTree,
    FilletRequest,
    FixComponentRequest,
    FocusRequest,
    FocusResult,
    GeometricSignature,
    HoleRequest,
    ImportGeometryRequest,
    ImportGeometryResult,
    InsertComponentRequest,
    InterferenceRequest,
    InterferenceResult,
    JointInfo,
    JointRequest,
    JointSweepResult,
    LinearPatternRequest,
    ListFeaturesRequest,
    ListHolesRequest,
    ListHolesResult,
    ManualEditListener,
    ManualEditNotice,
    MassProperties,
    MassRequest,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    MeasureBodiesResult,
    MirrorBodyRequest,
    MirrorRequest,
    MoveBodyRequest,
    NewDocumentRequest,
    OpenDocumentRequest,
    OpenDocumentResult,
    OperationResult,
    OperationSpec,
    PersistentRef,
    PlaceComponentRequest,
    ProgressListener,
    ProgressNotice,
    ReferenceVisibilityRequest,
    ReferenceVisibilityResult,
    RemoveFeatureRequest,
    Request,
    ResolveRequest,
    RevolveRequest,
    RunNativeRequest,
    RunNativeResult,
    SaveDocumentRequest,
    SaveDocumentResult,
    SectionRequest,
    SelectionListener,
    SelectionResult,
    SessionInfo,
    SetActiveDocumentRequest,
    SetDesignAttributesRequest,
    SetFeaturePropertiesRequest,
    SetJointRequest,
    SetTimeoutRequest,
    ShellRequest,
    ShowEntitiesRequest,
    ShowEntitiesResult,
    ShrinkBodyRequest,
    SketchRequest,
    SketchResult,
    SketchStatusRequest,
    SketchStatusResult,
    SplitRequest,
    StyleRequest,
    SweepJointRequest,
    SweepRequest,
    ToolOverlapRequest,
    ToolOverlapResult,
    Transform,
    UndoResult,
    UnfoldRequest,
    ValidityRequest,
    ValidityResult,
    VertexAtRequest,
    VerticesResult,
    ViewRequest,
)
from cotaforge.conventions.requests import (
    colour_edits,
    colour_hole,
    hole_convention,
    iso_colour_drawing,
)

M = TypeVar("M", bound=BaseModel)

__all__ = ["FreeCADAdapter"]

# The GUI binary needs about 2.2 s to show its window before the worker script runs; a
# caller's short connect timeout must not turn that into a false ``timeout``.
VISIBLE_MIN_CONNECT_TIMEOUT_S = 15.0
# A save writes the whole document (and a slow disk, an antivirus or a synced folder can hold it): a timeout kills the
# worker and the live document with it, so the save gets far more room than an operation.
SAVE_TIMEOUT_S = 300.0
SWEEP_TIMEOUT_S = 600.0
# A large assembly takes minutes to read and a huge one far longer, so an import is not given a fixed time:
# it is waited for while the CAD process is alive and its CPU time advances (``rpc.Liveness``), and given up on only
# when it makes no CPU progress for IMPORT_STALL_S or has run for IMPORT_MAX_S in all.
IMPORT_STALL_S = 120.0
IMPORT_MAX_S = 3600.0
HEARTBEAT_S = 5.0  # how often a long request is looked at and the engineer told it is alive
# What is asked of the components of a large assembly (hundreds of bodies, tens of thousands of curved faces) takes as
# long as the assembly is big, so it is waited for like an import (a fixed 60 s would kill the CAD and the document
# with it on a slow machine) and is not given a fixed time either.
COMPONENT_METHODS = frozenset(
    {
        "insert_component",
        "faces_of",
        "bodies_of",
        "mass_properties",
        "bounding_box",
        "component_bounding_box",
        "is_valid",
        "check_interference",
        "body_interference",
    }
)
ENV_IMPORT_STALL_S = "COTAFORGE_IMPORT_STALL_S"  # read by the server (``cotaforge_mcp.config``), which passes it on
ENV_IMPORT_MAX_S = "COTAFORGE_IMPORT_MAX_S"
# Extra seconds the client waits for a ``run_native`` reply beyond the request's own ``timeout_s``: the worker
# interrupts a runaway fragment at ``timeout_s`` and still needs time to roll back and measure.
NATIVE_RPC_GRACE_S = 30.0
# The views of a sheet are computed by the CAD and take as long as the model is big: a part is a few seconds, an
# assembly of hundreds of bodies minutes. The worker waits up to its own ``DRAWING_WAIT_S`` (worker/drawing.py; far
# longer for an assembly) and answers with a clear error after it, and the client gives no fixed time either: it waits
# like for an import (the CAD alive and its CPU time advancing), because a client timeout would kill the worker and
# the document with it.
DRAWING_METHODS = frozenset(
    {"create_drawing", "annotate_drawing", "describe_drawing", "capture_drawing", "export_drawing"}
)


def _default_log_dir() -> Path:
    return Path(tempfile.gettempdir()) / "cotaforge"


class FreeCADAdapter(Adapter):
    name = "freecad"
    cad_key = "freecad"
    display_name = "FreeCAD"
    document_suffix = ".FCStd"

    def __init__(
        self,
        freecad_root: Path | None = None,
        *,
        timeout_s: float = 60.0,
        keep_script: bool = False,
        log_dir: Path | None = None,
        visible: bool = False,
        enable_test_hooks: bool = False,
        import_stall_s: float = IMPORT_STALL_S,
        import_max_s: float = IMPORT_MAX_S,
        heartbeat_s: float = HEARTBEAT_S,
    ) -> None:
        self._import_stall_s = import_stall_s
        self._import_max_s = import_max_s
        self._heartbeat_s = heartbeat_s
        self._progress_listener: ProgressListener | None = None
        self._last_progress: ProgressNotice | None = None
        self._freecad_root = Path(freecad_root).expanduser() if freecad_root is not None else None
        self._timeout_s = timeout_s
        self._keep_script = keep_script
        self._visible = visible
        self._enable_test_hooks = enable_test_hooks
        self._log_dir = Path(log_dir) if log_dir is not None else _default_log_dir()
        self._rpc: WorkerProcess | None = None
        self._session: SessionInfo | None = None
        self._last_script_path: Path | None = None
        self._selection_listener: SelectionListener | None = None
        self._manual_edit_listener: ManualEditListener | None = None
        self._sheet_metal: dict[str, Any] | None = None
        """What the worker's hello said about the SheetMetal add-on (``available``, ``version``, ``reason``,
        ``install``); ``None`` until a worker has connected."""
        self._starting: WorkerProcess | None = None
        """The worker a ``connect`` in another thread is starting (what ``cancel_connect`` stops)."""
        self._connect_cancelled = threading.Event()

    # --- properties -------------------------------------------------------------------------------

    @property
    def worker_pid(self) -> int | None:
        return self._rpc.pid if self._rpc is not None and self._rpc.alive else None

    def crash_worker(self) -> None:
        """End the FreeCAD process alone and at once, as a crash would (see ``WorkerProcess.crash``). For tests of
        recovery: the next call finds the worker gone."""
        if self._rpc is not None:
            self._rpc.crash()

    @property
    def last_script_path(self) -> Path | None:
        return self._last_script_path

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def freecad_root(self) -> Path | None:
        return self._freecad_root

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def sheet_metal(self) -> dict[str, Any] | None:
        """The SheetMetal add-on as the connected worker found it (``None`` before a worker connected)."""
        return None if self._sheet_metal is None else dict(self._sheet_metal)

    def __enter__(self) -> FreeCADAdapter:
        self.connect(ConnectRequest(operation_id="connect", timeout_s=self._timeout_s))
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.disconnect(Empty(operation_id="disconnect"))

    # --- RPC plumbing -----------------------------------------------------------------------------

    @staticmethod
    def _timeout_message(exc: WorkerTimeout) -> str:
        """The text of a timeout; a liveness rule names itself and the setting that changes it."""
        if exc.rule == "stall":
            return f"{exc} (the CAD process is alive but stopped using CPU; {ENV_IMPORT_STALL_S} sets how long to wait)"
        if exc.rule == "cap":
            return f"{exc} ({ENV_IMPORT_MAX_S} sets the limit)"
        return str(exc)

    def _raw_call(
        self,
        method: str,
        request: Request,
        timeout_s: float | None = None,
        liveness: Liveness | None = None,
    ) -> dict[str, Any]:
        """One RPC; transport and worker failures become AdapterError (the caller validates the result).
        ``timeout_s`` overrides the wait for this call only; ``liveness`` replaces it with the rule of
        ``rpc.Liveness`` (the CAD process alive and using CPU)."""
        rpc = self._rpc
        if rpc is None:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.session_lost,
                    message="not connected",
                    operation_id=request.operation_id,
                    recoverable=False,
                )
            )
        if liveness is None and timeout_s is None and (method in COMPONENT_METHODS or method in DRAWING_METHODS):
            liveness = Liveness(
                stall_s=self._import_stall_s,
                max_s=self._import_max_s,
                heartbeat_s=self._heartbeat_s,
                on_heartbeat=lambda beat: self._on_heartbeat(beat, f"{method} (FreeCAD gives no percentage here)"),
            )
        try:
            long_ = method in ("save_document", "open_document")  # a large document takes long to write or load
            timeout = max(self._timeout_s, SAVE_TIMEOUT_S) if long_ else self._timeout_s
            if timeout_s is not None:
                timeout = timeout_s
            return rpc.call(method, request.model_dump(mode="json"), timeout, liveness=liveness)
        except RpcFailure as exc:
            raise AdapterError(self._error_info(exc.error, request)) from exc
        except WorkerTimeout as exc:
            timeout_details: dict[str, str | float | int | bool | None] = {
                "timeout_s": exc.timeout_s,
                "method": method,
            }
            if exc.rule is not None:  # a long request given up on by the liveness rule: say which one
                timeout_details.update(rule=exc.rule, limit_s=exc.limit_s, elapsed_s=exc.elapsed_s)
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.timeout,
                    message=self._timeout_message(exc),
                    operation_id=request.operation_id,
                    details=timeout_details,
                    recoverable=True,
                )
            ) from exc
        except WorkerBusy as exc:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.timeout,
                    message=str(exc),
                    operation_id=request.operation_id,
                    details={"method": method, "reason": "busy", "running": exc.running, "running_s": exc.running_s},
                    recoverable=True,
                )
            ) from exc
        except WorkerLost as exc:
            details: dict[str, str | float | int | bool | None] = {"method": method, **exc.details}
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.session_lost,
                    message=str(exc),
                    operation_id=request.operation_id,
                    details=details,
                    recoverable=False,
                )
            ) from exc

    def _call(
        self,
        method: str,
        request: Request,
        model: type[M],
        timeout_s: float | None = None,
        liveness: Liveness | None = None,
    ) -> M:
        result = self._raw_call(method, request, timeout_s, liveness)
        try:
            return model.model_validate(result)
        except ValidationError as exc:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.internal,
                    message=f"malformed {model.__name__} result for {method}: {exc.error_count()} validation error(s)",
                    operation_id=request.operation_id,
                    details={"method": method, "errors": str(exc)[:2000]},
                    recoverable=True,
                )
            ) from exc

    @staticmethod
    def _error_info(error: dict[str, Any], request: Request) -> ErrorInfo:
        try:
            return ErrorInfo.model_validate(error)
        except ValidationError as exc:
            return ErrorInfo(
                code=ErrorCode.internal,
                message=f"malformed error from worker: {error.get('message', error)}",
                operation_id=request.operation_id,
                details={"errors": str(exc)[:2000]},
                recoverable=True,
            )

    @staticmethod
    def _check_path(path: str | Path, what: str, request: Request, code: ErrorCode = ErrorCode.invalid_request) -> None:
        """Refuse (Windows only) a path so long that FreeCAD's temporary files would overrun MAX_PATH."""
        problem = path_length_problem(path, what)
        if problem is not None:
            raise AdapterError(
                ErrorInfo(
                    code=code,
                    message=problem,
                    operation_id=request.operation_id,
                    details={"reason": "path_too_long", "path": str(path), "length": len(str(path))},
                    recoverable=False,
                )
            )

    def _stop_worker(self, grace_s: float = 5.0) -> None:
        rpc = self._rpc
        self._rpc = None
        self._session = None
        self._sheet_metal = None
        if rpc is not None:
            rpc.stop(grace_s=grace_s)

    # --- Session ------------------------------------------------------------------------------------

    def connect(self, request: ConnectRequest) -> SessionInfo:
        if self._rpc is not None and self._rpc.alive and self._session is not None:
            return self._session
        try:
            return self._connect(request)
        finally:
            self._starting = None

    def cancel_connect(self) -> None:
        """Called from another thread while ``connect`` runs (the client went away before FreeCAD was up): the worker
        being started is stopped by its own process handle, so no FreeCAD is left behind, and ``connect`` raises. A
        ``connect`` that has not reached the spawn yet raises before it. Final: this adapter does not connect again."""
        self._connect_cancelled.set()
        starting = self._starting
        if starting is not None:
            starting.stop(grace_s=0.0)

    def _check_cancelled(self, request: ConnectRequest, rpc: WorkerProcess) -> None:
        if not self._connect_cancelled.is_set():
            return
        rpc.stop(grace_s=0.5)
        self._stop_worker(grace_s=0.5)
        raise AdapterError(
            ErrorInfo(
                code=ErrorCode.session_lost,
                message="connect cancelled: the server is shutting down",
                operation_id=request.operation_id,
                recoverable=False,
            )
        )

    def _connect(self, request: ConnectRequest) -> SessionInfo:
        self._stop_worker(grace_s=0.5)
        root = find_freecad_root(self._freecad_root)
        if root is None:
            hint = freecad_rpc.missing_root_hint()
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.not_supported,
                    message=f"FreeCAD root not found (no {hint}); set COTAFORGE_FREECAD_ROOT",
                    operation_id=request.operation_id,
                    recoverable=False,
                )
            )
        if self._visible and (conflict := numberformat.separator_conflict()) is not None:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.not_supported,
                    message=conflict,
                    operation_id=request.operation_id,
                    recoverable=True,
                )
            )
        log_path = self._log_dir / f"freecad-worker-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{id(self):x}.log"
        for what, path in (
            ("the FreeCAD folder", root / "bin" / "FreeCADCmd.exe"),
            ("the worker script", WORKER_DIR / "gui_entry.py"),
            ("the worker log", log_path),
        ):
            self._check_path(path, what, request, ErrorCode.not_supported)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        rpc = WorkerProcess(
            freecad_rpc.worker_command(root, WORKER_DIR, visible=self._visible),
            log_path,
            # a pipe fd cannot be inherited on Windows: the worker steals stdout for its replies there.
            # Verified 2026-09-23 on a real Windows 11 machine (FreeCAD 1.1, tests/integration/freecad/
            # test_visible.py, 12/12 passed): the GUI subsystem binary (freecad.exe, visible=True) keeps
            # and answers on the same stdout-fallback channel exactly like the console binary
            # (freecadcmd.exe, headless) does -- no separate transport was needed for the visible worker.
            use_fd_channel=sys.platform != "win32",
            env=freecad_rpc.worker_env(log_path, visible=self._visible, enable_test_hooks=self._enable_test_hooks),
            cwd=WORKER_DIR,
        )
        hello_timeout_s = max(request.timeout_s, VISIBLE_MIN_CONNECT_TIMEOUT_S) if self._visible else request.timeout_s
        self._starting = rpc
        self._check_cancelled(request, rpc)
        try:
            hello = rpc.start(hello_timeout_s, probe=numberformat.blocked_by_dialog if self._visible else None)
        except WorkerBlocked as exc:
            rpc.stop(grace_s=0.5)
            self._check_cancelled(request, rpc)
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.not_supported,
                    message=exc.reason,
                    operation_id=request.operation_id,
                    details={"log": str(log_path)},
                    recoverable=True,
                )
            ) from exc
        except WorkerTimeout as exc:
            rpc.stop(grace_s=0.5)
            self._check_cancelled(request, rpc)
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.timeout,
                    message=f"worker did not send its hello within {hello_timeout_s} s (log: {log_path})",
                    operation_id=request.operation_id,
                    details={"timeout_s": hello_timeout_s, "log": str(log_path)},
                    recoverable=True,
                )
            ) from exc
        except WorkerLost as exc:
            rpc.stop(grace_s=0.5)
            self._check_cancelled(request, rpc)
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.session_lost,
                    message=f"worker failed to start: {exc} (log: {log_path})",
                    operation_id=request.operation_id,
                    details={"log": str(log_path), **exc.details},
                    recoverable=False,
                )
            ) from exc
        self._check_cancelled(request, rpc)
        self._rpc = rpc
        rpc.set_notification_listener(self._on_notification)
        try:
            validation = self._call("validate_api", Empty(operation_id=request.operation_id), ApiValidation)
            if not validation.ok or validation.problems:
                problems = "; ".join(validation.problems) or "validate_api reported ok=False"
                raise AdapterError(
                    ErrorInfo(
                        code=ErrorCode.not_supported,
                        message=f"FreeCAD API validation failed: {problems}",
                        operation_id=request.operation_id,
                        details={"problems": len(validation.problems)},
                        recoverable=False,
                    )
                )
            canary = self._call("canary", Empty(operation_id=request.operation_id), CanaryResult)
            if not canary.healthy:
                failed = sorted(name for name, ok in canary.checks.items() if not ok)
                raise AdapterError(
                    ErrorInfo(
                        code=ErrorCode.degraded_session,
                        message=f"canary unhealthy after connect: failed checks {failed}",
                        operation_id=request.operation_id,
                        details=dict(canary.checks),
                        recoverable=False,
                    )
                )
        except AdapterError:
            self._stop_worker(grace_s=1.0)
            raise
        self._check_cancelled(request, rpc)
        sheet_metal = hello.get("sheet_metal")
        self._sheet_metal = dict(sheet_metal) if isinstance(sheet_metal, dict) else None
        self._session = SessionInfo(adapter=self.name, version=str(hello.get("freecad", "unknown")), units="mm")
        return self._session

    def disconnect(self, request: Empty) -> Ack:
        rpc = self._rpc
        if rpc is None:
            return Ack()
        if rpc.alive:
            if self._keep_script:
                self._write_session_script(request)
            try:
                # a request still running (one the client gave up on) is not waited for: the worker is stopped below
                rpc.call("disconnect", request.model_dump(mode="json"), min(self._timeout_s, 10.0), busy_wait_s=2.0)
            except (RpcFailure, WorkerLost, WorkerTimeout, WorkerBusy):
                pass
        self._stop_worker(grace_s=5.0)
        return Ack()

    def _write_session_script(self, request: Empty) -> None:
        rpc = self._rpc
        if rpc is None:
            return
        try:
            result = rpc.call(
                "export_macro",
                ExportRequest(operation_id=request.operation_id, path="").model_dump(mode="json"),
                self._timeout_s,
                busy_wait_s=2.0,
            )
        except (RpcFailure, WorkerLost, WorkerTimeout, WorkerBusy):
            return
        script = result.get("script")
        if not isinstance(script, str):
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_dir / f"freecad-session-{rpc.pid}.py"
        path.write_text(script, encoding="utf-8")
        self._last_script_path = path

    def new_document(self, request: NewDocumentRequest) -> DocumentInfo:
        return self._call("new_document", request, DocumentInfo)

    def set_active_document(self, request: SetActiveDocumentRequest) -> DocumentInfo:
        return self._call("set_active_document", request, DocumentInfo)

    def active_document(self, request: Empty) -> DocumentInfo:
        return self._call("active_document", request, DocumentInfo)

    def set_timeout(self, request: SetTimeoutRequest) -> Ack:
        self._timeout_s = request.timeout_s
        return Ack()

    def suppress_interaction(self, request: Empty) -> Ack:
        return self._call("suppress_interaction", request, Ack)

    def restore_interaction(self, request: Empty) -> Ack:
        return self._call("restore_interaction", request, Ack)

    # --- Health ---------------------------------------------------------------------------------------

    def session_alive(self) -> bool:
        """No RPC: whether the worker this adapter launched is still running. False after a crash, after the kill that
        follows a timeout or a broken channel, and before ``connect``; ``connect`` then launches a new worker in the
        same mode (the dead one is only reaped, never killed by name)."""
        return self._rpc is not None and self._rpc.alive and self._session is not None

    def canary(self, request: Empty) -> CanaryResult:
        started = time.perf_counter()
        if self._rpc is None or not self._rpc.alive:
            return CanaryResult(
                healthy=False, checks={"process": False}, duration_ms=(time.perf_counter() - started) * 1000.0
            )
        try:
            return self._call("canary", request, CanaryResult)
        except AdapterError as exc:
            if exc.info.code in (ErrorCode.session_lost, ErrorCode.timeout):
                return CanaryResult(
                    healthy=False, checks={"process": False}, duration_ms=(time.perf_counter() - started) * 1000.0
                )
            raise

    def validate_api(self, request: Empty) -> ApiValidation:
        if self._rpc is None or not self._rpc.alive:
            return ApiValidation(ok=False, problems=("worker process is not running",))
        try:
            return self._call("validate_api", request, ApiValidation)
        except AdapterError as exc:
            if exc.info.code in (ErrorCode.session_lost, ErrorCode.timeout):
                return ApiValidation(ok=False, problems=(f"{exc.info.code.value}: {exc.info.message}",))
            raise

    # --- Construction ---------------------------------------------------------------------------------

    def sketch(self, request: SketchRequest) -> SketchResult:
        return self._call("sketch", request, SketchResult)

    def datum_plane(self, request: DatumPlaneRequest) -> DatumPlaneResult:
        return self._call("datum_plane", request, DatumPlaneResult)

    def datum_axis(self, request: DatumAxisRequest) -> DatumAxisResult:
        return self._call("datum_axis", request, DatumAxisResult)

    def extrude(self, request: ExtrudeRequest) -> OperationResult:
        return self._call("extrude", request, OperationResult)

    def cut(self, request: CutRequest) -> OperationResult:
        return self._call("cut", request, OperationResult)

    def revolve(self, request: RevolveRequest) -> OperationResult:
        return self._call("revolve", request, OperationResult)

    def sweep(self, request: SweepRequest) -> OperationResult:
        return self._call("sweep", request, OperationResult)

    def hole(self, request: HoleRequest) -> OperationResult:
        # the colour of the faces of the hole is the convention in force at the moment it is made
        return self._call("hole", colour_hole(request, hole_convention(self.conventions)), OperationResult)

    def linear_pattern(self, request: LinearPatternRequest) -> OperationResult:
        return self._call("linear_pattern", request, OperationResult)

    def circular_pattern(self, request: CircularPatternRequest) -> OperationResult:
        return self._call("circular_pattern", request, OperationResult)

    def chamfer(self, request: ChamferRequest) -> OperationResult:
        return self._call("chamfer", request, OperationResult)

    def fillet(self, request: FilletRequest) -> OperationResult:
        return self._call("fillet", request, OperationResult)

    def shell(self, request: ShellRequest) -> OperationResult:
        return self._call("shell", request, OperationResult)

    def draft(self, request: DraftRequest) -> OperationResult:
        return self._call("draft", request, OperationResult)

    def mirror(self, request: MirrorRequest) -> OperationResult:
        return self._call("mirror", request, OperationResult)

    def mirror_body(self, request: MirrorBodyRequest) -> OperationResult:
        return self._call("mirror_body", request, OperationResult)

    def shrink_body(self, request: ShrinkBodyRequest) -> OperationResult:
        return self._call("shrink_body", request, OperationResult)

    def split(self, request: SplitRequest) -> OperationResult:
        return self._call("split", request, OperationResult)

    def base_flange(self, request: BaseFlangeRequest) -> OperationResult:
        """A flat sheet from a closed sketch through the SheetMetal add-on (``worker/ops_sheetmetal.py``)."""
        return self._call("base_flange", request, OperationResult)

    def edge_flange(self, request: EdgeFlangeRequest) -> OperationResult:
        return self._call("edge_flange", request, OperationResult)

    def unfold(self, request: UnfoldRequest) -> OperationResult:
        return self._call("unfold", request, OperationResult)

    def edit_feature(self, request: EditFeatureRequest) -> EditResult:
        """In-place edit of box, cylinder and move_body features on Part chains (``worker/edit.py``): one RPC, one
        transaction, one recompute for the whole batch; a failed check is rolled back by the worker. A hole edited
        takes the colour of its class as the convention has it now."""
        request = colour_edits(request, hole_convention(self.conventions))
        return self._call("edit_feature", request, EditResult)

    def list_features(self, request: ListFeaturesRequest) -> FeatureList:
        """Every feature the FreeCAD document holds, body by body (``worker/features.py``), whoever made it."""
        return self._call("list_features", request, FeatureList)

    def remove_feature(self, request: RemoveFeatureRequest) -> RunNativeResult:
        """Delete a feature the way FreeCAD does (``Body.removeObject``) as one ``run_native``-style step."""
        return self._call("remove_feature", request, RunNativeResult)

    def set_feature_properties(self, request: SetFeaturePropertiesRequest) -> RunNativeResult:
        """Set properties of a feature, validated against FreeCAD's own property, as one ``run_native``-style step."""
        return self._call("set_feature_properties", request, RunNativeResult)

    def focus_model(self, request: FocusRequest) -> FocusResult:
        """Show each body's Tip and hide sketches and intermediate features (``Visibility``; works headless too)."""
        return self._call("focus_model", request, FocusResult)

    def set_reference_visibility(self, request: ReferenceVisibilityRequest) -> ReferenceVisibilityResult:
        """Show or hide sketches, datum planes, lines and points by name (``Visibility``; works headless too)."""
        return self._call("set_reference_visibility", request, ReferenceVisibilityResult)

    def measure_bodies(self, request: Empty) -> MeasureBodiesResult:
        """READ-ONLY reading of every live body, every subtraction and every overlap (``worker/edit.py``)."""
        return self._call("measure_bodies", request, MeasureBodiesResult)

    def body_interference(self, request: BodyInterferenceRequest) -> BodyInterferenceResult:
        """READ-ONLY common volume and minimum distance of every pair of live bodies (``worker/queries.py``)."""
        return self._call("body_interference", request, BodyInterferenceResult)

    def save_document(self, request: SaveDocumentRequest) -> SaveDocumentResult:
        """Atomic ``saveCopy`` + ``os.replace`` of the active part with the stamp in ``Document.Meta`` and the registry
        dump as ``adapter_state`` (``worker/persist.py``). The worker runs on this same machine, so the path is
        passed as is."""
        self._check_path(request.path, "the document path", request)
        return self._call("save_document", request, SaveDocumentResult)

    def open_document(self, request: OpenDocumentRequest) -> OpenDocumentResult:
        """``App.openDocument`` (no recompute) of a saved part, then the registry rebuilt from ``adapter_state`` and
        re-bound by element index and signature (``worker/persist.py``). The worker hands back the raw ``cotaforge.*``
        metadata; the stamp is parsed here, with the contract's own rules (a newer stamp schema is refused)."""
        self._check_path(request.path, "the document path", request)
        raw = self._raw_call("open_document", request)
        meta = raw.pop("stamp_meta", {}) or {}
        try:
            stamp = DocumentStamp.from_meta({str(k): str(v) for k, v in meta.items()})
        except (ValueError, TypeError) as exc:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.invalid_request,
                    message=f"the document's harness stamp cannot be read: {exc}",
                    operation_id=request.operation_id,
                    details={"reason": "stamp", "path": request.path},
                    recoverable=False,
                )
            ) from exc
        try:
            return OpenDocumentResult.model_validate({**raw, "stamp": stamp})
        except ValidationError as exc:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.internal,
                    message=f"malformed OpenDocumentResult: {exc.error_count()} validation error(s)",
                    operation_id=request.operation_id,
                    details={"method": "open_document", "errors": str(exc)[:2000]},
                    recoverable=True,
                )
            ) from exc

    def import_geometry(self, request: ImportGeometryRequest) -> ImportGeometryResult:
        """A STEP or IGES file read by FreeCAD into a new document of adopted bodies (``worker/foreign.py``); the unit
        the file declares is read from the file here (``fileunits.py``)."""
        self._check_path(request.path, "the file to import", request)
        self._last_progress = None
        liveness = Liveness(
            stall_s=self._import_stall_s,
            max_s=self._import_max_s,
            heartbeat_s=self._heartbeat_s,
            on_heartbeat=self._on_heartbeat,
        )
        result = self._call("import_geometry", request, ImportGeometryResult, liveness=liveness)
        return result.model_copy(update={"source_units": declared_length_unit(request.path)})

    def draft_analysis(self, request: DraftAnalysisRequest) -> DraftAnalysisResult:
        """READ-ONLY draft of every face against a pull direction, with the shadow test (``worker/mould.py``)."""
        return self._call("draft_analysis", request, DraftAnalysisResult)

    def list_holes(self, request: ListHolesRequest) -> ListHolesResult:
        """READ-ONLY concave cylindrical faces grouped into holes (``worker/holes.py``)."""
        return self._call("list_holes", request, ListHolesResult)

    def tool_overlap(self, request: ToolOverlapRequest) -> ToolOverlapResult:
        """READ-ONLY one boolean of a tool solid with the body it would act on (``worker/overlap.py``)."""
        return self._call("tool_overlap", request, ToolOverlapResult)

    def close_document(self, request: Empty) -> Ack:
        """``App.closeDocument`` of the active part (``worker/main.py``): unsaved changes are discarded, the registry of
        that document is dropped and there is no active document afterwards."""
        return self._call("close_document", request, Ack)

    def design_attributes(self, request: Empty) -> DesignAttributes:
        """Material and process route stored in the document (``worker/design_attrs.py``)."""
        return self._call("design_attributes", request, DesignAttributes)

    def set_design_attributes(self, request: SetDesignAttributesRequest) -> DesignAttributes:
        """Store them: a ``CotaforgeMaterial`` property on every live body and keys in ``Document.Meta``."""
        return self._call("set_design_attributes", request, DesignAttributes)

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        """What FreeCAD offers beyond the common vocabulary: the cosmetic thread of a shaft end."""
        return (external_thread.spec(),)

    def external_thread(self, request: ExtensionRequest) -> OperationResult:
        """The numbers of the size (major, pitch, basic minor) travel with it: the worker has no thread table of its own
        for this, and the sheet reads them back from the record."""
        numbers = external_thread.record(str(request.params["size"]))
        return self._call(
            "external_thread", request.model_copy(update={"params": {**request.params, **numbers}}), OperationResult
        )

    def box(self, request: BoxRequest) -> OperationResult:
        return self._call("box", request, OperationResult)

    def cylinder(self, request: CylinderRequest) -> OperationResult:
        return self._call("cylinder", request, OperationResult)

    def boolean(self, request: BooleanRequest) -> OperationResult:
        return self._call("boolean", request, OperationResult)

    def move_body(self, request: MoveBodyRequest) -> OperationResult:
        return self._call("move_body", request, OperationResult)

    def copy_body(self, request: CopyBodyRequest) -> OperationResult:
        return self._call("copy_body", request, OperationResult)

    # --- Assemblies -----------------------------------------------------------------------------------

    def insert_component(self, request: InsertComponentRequest) -> OperationResult:
        return self._call("insert_component", request, OperationResult)

    def fix_component(self, request: FixComponentRequest) -> OperationResult:
        return self._call("fix_component", request, OperationResult)

    def place_component(self, request: PlaceComponentRequest) -> OperationResult:
        return self._call("place_component", request, OperationResult)

    def add_joint(self, request: AddJointRequest) -> OperationResult:
        return self._call("add_joint", request, OperationResult)

    def set_joint(self, request: SetJointRequest) -> OperationResult:
        return self._call("set_joint", request, OperationResult)

    def joint_info(self, request: JointRequest) -> JointInfo:
        return self._call("joint_info", request, JointInfo)

    def sweep_joint(self, request: SweepJointRequest) -> JointSweepResult:
        # up to MAX_SWEEP_POSITIONS exact interference readings of every pair: longer than an ordinary call
        return self._call("sweep_joint", request, JointSweepResult, timeout_s=max(self._timeout_s, SWEEP_TIMEOUT_S))

    def mate_concentric(self, request: MateConcentricRequest) -> OperationResult:
        return self._call("mate_concentric", request, OperationResult)

    def mate_coincident(self, request: MateCoincidentRequest) -> OperationResult:
        return self._call("mate_coincident", request, OperationResult)

    def mate_distance(self, request: MateDistanceRequest) -> OperationResult:
        return self._call("mate_distance", request, OperationResult)

    def check_interference(self, request: InterferenceRequest) -> InterferenceResult:
        return self._call("check_interference", request, InterferenceResult)

    def component_bounding_box(self, request: ComponentRequest) -> BoundingBox:
        return self._call("component_bounding_box", request, BoundingBox)

    def component_transform(self, request: ComponentRequest) -> Transform:
        return self._call("component_transform", request, Transform)

    # --- Provenance -----------------------------------------------------------------------------------

    def faces_of(self, request: FeatureRequest) -> FacesResult:
        return self._call("faces_of", request, FacesResult)

    def edges_between(self, request: EdgesBetweenRequest) -> EdgesResult:
        return self._call("edges_between", request, EdgesResult)

    def edges_of(self, request: FaceRequest) -> EdgesResult:
        return self._call("edges_of", request, EdgesResult)

    def vertices_of(self, request: EdgeRequest) -> VerticesResult:
        return self._call("vertices_of", request, VerticesResult)

    def vertex_at(self, request: VertexAtRequest) -> VerticesResult:
        return self._call("vertex_at", request, VerticesResult)

    def bodies_of(self, request: FeatureRequest) -> BodiesResult:
        return self._call("bodies_of", request, BodiesResult)

    # --- Query ------------------------------------------------------------------------------------------

    def is_valid(self, request: ValidityRequest) -> ValidityResult:
        return self._call("is_valid", request, ValidityResult)

    def mass_properties(self, request: MassRequest) -> MassProperties:
        return self._call("mass_properties", request, MassProperties)

    def bounding_box(self, request: MassRequest) -> BoundingBox:
        return self._call("bounding_box", request, BoundingBox)

    def feature_tree(self, request: Empty) -> FeatureTree:
        return self._call("feature_tree", request, FeatureTree)

    def face_info(self, request: FaceRequest) -> FaceInfo:
        return self._call("face_info", request, FaceInfo)

    def edge_info(self, request: EdgeRequest) -> EdgeInfo:
        return self._call("edge_info", request, EdgeInfo)

    # --- Query (optional) ---------------------------------------------------------------------------------

    def sketch_status(self, request: SketchStatusRequest) -> SketchStatusResult:
        return self._call("sketch_status", request, SketchStatusResult)

    def check_notes(self) -> tuple[str, ...]:
        """The sketches behind a feature that are not fully defined by dimensions (an opened file keeps them). Never
        raises: ``check`` goes on without the lines when the worker cannot answer."""
        try:
            result = self._raw_call("sketch_notes", Empty(operation_id="check_notes"))
        except AdapterError:
            return ()
        return tuple(str(line) for line in result.get("notes", ()))

    # --- References -------------------------------------------------------------------------------------

    def persistent_reference(self, request: EntityRequest) -> PersistentRef:
        return self._call("persistent_reference", request, PersistentRef)

    def resolve_reference(self, request: ResolveRequest) -> EntityResult:
        return self._call("resolve_reference", request, EntityResult)

    def signature(self, request: EntityRequest) -> GeometricSignature:
        return self._call("signature", request, GeometricSignature)

    # --- Undo --------------------------------------------------------------------------------------------

    def undo_last(self, request: Empty) -> UndoResult:
        return self._call("undo_last", request, UndoResult)

    # --- Selection (optional group, visible worker only; also a SelectionProducer/ManualEditProducer) ------

    def get_selection(self, request: Empty) -> SelectionResult:
        """Current CAD UI selection (``Gui.Selection``), stable refs only.

        Only the visible worker has a UI selection to read; a headless worker answers ``not_supported``
        and ``capabilities()`` omits it unless ``visible=True`` (mirrors ``set_view``/``capture``).
        """
        return self._call("get_selection", request, SelectionResult)

    def show_entities(self, request: ShowEntitiesRequest) -> ShowEntitiesResult:
        """Point the engineer at already-resolved entities -- the agent-to-engineer counterpart of
        ``get_selection`` above. Unlike ``get_selection``/``set_view``/``capture``, this stays in
        ``capabilities()`` even on a headless worker: the worker itself degrades (``shown="unavailable"``
        with a reason) instead of the tool becoming entirely unusable, so it is never excluded here the
        way ``VIEW_TOOLS``/``SELECTION_TOOLS`` are below."""
        return self._call("show_entities", request, ShowEntitiesResult)

    def set_selection_listener(self, listener: SelectionListener) -> None:
        """``contract.SelectionProducer``: register the callback the worker's ``selection_changed``
        notification (``Gui.Selection`` observer, debounced) is translated into and forwarded to."""
        self._selection_listener = listener

    def set_manual_edit_listener(self, listener: ManualEditListener) -> None:
        """``contract.ManualEditProducer``: register the callback the worker's ``manual_edit``
        notification (document-change observer, suppressed during harness-issued operations) is
        translated into and forwarded to."""
        self._manual_edit_listener = listener

    def set_progress_listener(self, listener: ProgressListener | None) -> None:
        """``contract.ProgressProducer``: where the progress of a long request goes (the worker's ``progress``
        notifications and this adapter's own heartbeats while it waits). ``None`` stops it."""
        self._progress_listener = listener

    def _tell_progress(self, notice: ProgressNotice) -> None:
        listener = self._progress_listener
        if listener is None:
            return
        try:
            listener(notice)
        except Exception:  # noqa: BLE001 - a broken listener must not fail the request it watches
            pass

    def _on_heartbeat(self, beat: Heartbeat, doing: str | None = None) -> None:
        """Every ``heartbeat_s`` of a long request: the phase the worker last reported (``reading`` until it reports
        one: FreeCAD gives no percentage while it reads the file) with what the operating system says about the worker
        process."""
        last = self._last_progress if doing is None else None  # ``doing``: not an import, no phase to repeat
        phase = last.phase if last is not None else "reading"
        text = last.message if last is not None else (doing or "reading the file (FreeCAD gives no percentage here)")
        stats = [f"{beat.elapsed_s:.0f} s elapsed"]
        if beat.vitals.cpu_s is not None:
            stats.append(f"CAD process CPU {beat.vitals.cpu_s:.0f} s")
        if beat.vitals.memory_mb is not None:
            stats.append(f"memory {beat.vitals.memory_mb:.0f} MB")
        self._tell_progress(
            ProgressNotice(
                phase=phase,
                message=f"{text}: {', '.join(stats)}",
                current=last.current if last is not None else None,
                total=last.total if last is not None else None,
                elapsed_s=beat.elapsed_s,
                cpu_s=beat.vitals.cpu_s,
                memory_mb=beat.vitals.memory_mb,
                heartbeat=True,
            )
        )

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        """``WorkerProcess``'s notification callback (``adapters/rpc.py``): runs on the worker's reader
        thread, never the tool-call thread, so it only ever touches the two listener callables set above
        -- a listener must never assume the tool-call thread. A malformed or unrecognised payload is dropped rather than
        raised: a broken notification must never take down the reader thread (which also delivers RPC replies)."""
        try:
            if method == "selection_changed":
                if self._selection_listener is not None:
                    self._selection_listener(SelectionResult.model_validate(params))
            elif method == "manual_edit":
                if self._manual_edit_listener is not None:
                    self._manual_edit_listener(ManualEditNotice.model_validate(params))
            elif method == "progress":
                notice = ProgressNotice.model_validate(
                    {k: v for k, v in params.items() if k in ProgressNotice.model_fields}
                )
                self._last_progress = notice
                self._tell_progress(notice)
        except ValidationError:
            pass

    # --- View (optional group, visible worker only) ------------------------------------------------------

    VIEW_TOOLS = frozenset({"set_view", "capture", "style_bodies", "section_plane"})
    SELECTION_TOOLS = frozenset({"get_selection"})

    def set_view(self, request: ViewRequest) -> Ack:
        """Orient the active document's 3D view (a standard ``name``) and/or fit the model, or just ``bodies``, in it.

        Only the visible worker has a 3D view; a headless worker answers ``not_supported`` and
        ``capabilities()`` omits ``set_view`` unless ``visible=True``.
        """
        return self._call("set_view", request, Ack)

    def style_bodies(self, request: StyleRequest) -> Ack:
        """Transparency, colour and visibility of bodies in the 3D view (display only). Visible worker only, like
        ``set_view``."""
        return self._call("style_bodies", request, Ack)

    def section_plane(self, request: SectionRequest) -> Ack:
        """A clipping plane in the 3D view (display only). Visible worker only, like ``set_view``."""
        return self._call("section_plane", request, Ack)

    # --- Drawings (optional group; the sheet is built, rendered and exported without a window) ----------------

    def create_drawing(self, request: DrawingRequest) -> DrawingResult:
        """Build the drawing sheet (``worker/drawing.py``); returns once every view has finished computing."""
        request = iso_colour_drawing(request, hole_convention(self.conventions))
        return self._call("create_drawing", request, DrawingResult)

    def annotate_drawing(self, request: AnnotateDrawingRequest) -> DrawingResult:
        """Add or remove sheet items, or lay the sheet out again; every item is resolved against the live part."""
        request = iso_colour_drawing(request, hole_convention(self.conventions))
        return self._call("annotate_drawing", request, DrawingResult)

    def describe_drawing(self, request: DescribeDrawingRequest) -> DescribeDrawingResult:
        """The sheets of the document, their fields and items, and what each item does now."""
        return self._call("describe_drawing", request, DescribeDrawingResult)

    def capture_drawing(self, request: CaptureDrawingRequest) -> CaptureDrawingResult:
        """The sheet as a PNG, from the same rendering as the PDF (the window scene in the visible CAD, the composed
        sheet headless); ``region`` zooms. The result lists the native objects it shows or could not draw."""
        self._check_path(request.path, "the capture path", request)
        return self._call("capture_drawing", request, CaptureDrawingResult)

    def export_drawing(self, request: ExportDrawingRequest) -> ExportDrawingResult:
        """The sheets as PDF or DXF; neither needs a sheet to be open in a window."""
        self._check_path(request.path, "the export path", request)
        return self._call("export_drawing", request, ExportDrawingResult)

    def capture(self, request: CaptureRequest) -> Ack:
        """Render the active document's 3D view to ``request.path`` (PNG by extension, ``width`` x ``height``).

        Only the visible worker can render (FreeCAD's ``saveImage`` needs a real display); a headless worker
        answers ``not_supported`` and ``capabilities()`` omits ``capture`` unless ``visible=True``.
        """
        return self._call("capture", request, Ack)

    def profile_hints(self) -> tuple[str, ...]:
        return (
            "detail: features (sketch + extrude/cut, hole, fillet; editable), not box/cylinder + boolean. "
            "sketch (concept model, doc set_purpose): box, cylinder and booleans are fine.",
            "prefer: closed profiles (rect/square/circle/slot, or line and arc chains), fully dimensioned.",
            "positions: hole takes a world point (at) and sketch on a face takes frame=world; "
            "change a value later with op(edit=<id>, params=...).",
        )

    def known_traps(self) -> tuple[str, ...]:
        return (
            "PartDesign keeps ONE solid per body: a boss whose profile does not touch the body fails with "
            "'feature added no material'.",
            "A pocket (cut) whose profile lies outside the material removes nothing and fails.",
            "Positions on a face are (u, v) offsets from the face CENTRE in the face's local frame, not world "
            "coordinates (hole: at=[x, y, z] takes a world point; sketch: frame=world takes world points); on the "
            "base planes XY: u=X v=Y; YZ: u=Y v=Z; XZ: u=X v=-Z (normal +Y).",
            "A face selector must match exactly ONE face; a rejected reference lists the candidates.",
            "cut.scope must name the body the sketch belongs to (PartDesign cuts exactly one body).",
            "A sketch exists as a FreeCAD object only after an extrude/cut/revolve consumed it; sketch_status "
            "needs a consumed sketch.",
            "Features that were in a file when it was opened are not undoable; only what this session adds is.",
            "Patterns need the seed feature to belong to exactly one PartDesign body.",
            "TechDraw (drawings by hand, via run_native; drawing(action=create) builds a sheet and waits for you): a "
            "view is computed in the background after it is added; wait for that recompute to finish before the next "
            "step (dimensions, annotations, reading the view), or the step works on an empty or stale view and can "
            "crash FreeCAD.",
            "TechDraw: a sheet's PDF made by TechDraw's own export comes out blank unless the sheet is open in a "
            "window; export(action=pdf) does not have that problem.",
            "TechDraw projected geometry is not in part coordinates: the vertices of a view come scaled (by the view's "
            "Scale) and with Y inverted; undo both (divide by Scale, negate Y) before comparing them with the part.",
            "TechDraw cosmetic lines (view.makeCosmeticLine(p1, p2), then getCosmeticEdge(tag).Format = {'style', "
            "'weight', 'color', 'visible'}), measured in 1.1.3 in the sheet window and in its PDF: every style number "
            "(1 to 15; 0, -1 and 99 are accepted unchecked) is painted as the same ISO dashed line (dash 12 x weight, "
            "gap 3 x weight), so none gives a continuous or a chain (centre) line; weight and colour do take "
            "effect (a red and a blue line on a native section showed), but only after view.touch() and a "
            "recompute. The point is in the view's frame (mm from its centre, y up, measured at 1:1). For centre "
            "lines, axes, dashed and thin continuous lines use "
            "drawing(action=add, items=[{'type': 'line', 'axis': 'face:N'}]) (style chain | dashed | "
            "continuous): it draws the ISO 128 line types, and they reach the PDF.",
            "TechDraw cosmetic lines: a colour component outside 0..1 (255, -1) is printed by Qt's PDF writer as a "
            "colour operator with no operands and the viewer stops after the first view; use 0..1. A line that "
            "does not show may be outside the page: its point is in the view's frame, not the page's.",
        )

    GUIDE_NOTES: ClassVar[dict[str, str]] = {
        "note:joint": (
            " In FreeCAD the joint is a real Assembly joint when a is an instance (the engineer sees it in the model "
            "tree); a joint to the ground exists in the harness only."
        ),
        "note:add_joint": " In FreeCAD the joint is a real Assembly joint when a is a component.",
        "note:hole.up_to_face": "; FreeCAD's Hole has no such end: drilled blind to that distance",
    }
    """The sentences of the guide that are true of FreeCAD only; the server places them inside its own texts."""

    def guide_topic(self, topic: str) -> str | None:
        return self.GUIDE_NOTES.get(topic)

    def native_environment(self) -> tuple[str, ...]:
        if self._visible:  # the worker runs inside the FreeCAD GUI binary: the GUI module is there to import
            gui = (
                "  Everything else (os, sys, FreeCADGui, ...) must be imported by the fragment; the FreeCAD GUI is up "
                "(ViewObject exists), but display changes are not part of the measured step."
            )
        else:
            gui = (
                "  Everything else (os, sys, ...) must be imported by the fragment; there is no GUI (no FreeCADGui, "
                "no ViewObject)."
            )
        return (
            "  Preloaded: doc (the active document), App and FreeCAD, Part, Base, Vector, Placement, Rotation, "
            "Sketcher, PartDesign, math.",
            "  Helpers: faces_where(obj, pred) -> [('Face3', face)], edges_where(obj, pred) -> [('Edge5', edge)], "
            "solids() -> the objects whose solids are the document's results.",
            gui,
            "  The fragment runs inside ONE transaction and the document is recomputed afterwards: do not open, "
            "commit or abort transactions, undo/redo, or open/close documents yourself.",
            "  Face, edge and vertex indexes change after every recompute: look them up again instead of reusing them.",
            "  FreeCAD 1.1 names: AttachmentSupport (not Support); SideType='Two sides' with Type='Length' and "
            "Type2='Length' (not 'TwoLengths'); assign a whole Placement, not Placement.Base.x.",
            "  print() output comes back in the reply (a long one is shortened there and kept whole in a file the "
            "reply names); input() raises EOFError.",
        )

    SHEET_METAL_TOOLS = frozenset({"base_flange", "edge_flange", "unfold"})

    def _has_sheet_metal(self) -> bool:
        if self._session is None:
            return True  # not connected: whether the add-on is there is only known to a worker (see ``capabilities``)
        return bool(self._sheet_metal and self._sheet_metal.get("available"))

    def capabilities(self) -> Capabilities:
        """What this adapter offers. Without a worker the answer is static (``visible``, and the sheet-metal ops
        assumed present, so a server can publish its tools and instructions without starting FreeCAD); a connected
        worker narrows it: the add-on is not bundled, its ops exist only when the worker found it."""
        tools = super().capabilities().tools
        if not self._has_sheet_metal():
            tools = tools - self.SHEET_METAL_TOOLS
        return Capabilities(tools=tools if self._visible else tools - self.VIEW_TOOLS - self.SELECTION_TOOLS)

    def assumed_tools(self) -> frozenset[str]:
        """The sheet-metal ops until a worker has said whether the add-on is there."""
        return self.SHEET_METAL_TOOLS if self._session is None else frozenset()

    def unavailable_tools(self) -> dict[str, str]:
        """The sheet-metal ops, with why they are missing and how to install the add-on, once a worker has said the
        SheetMetal workbench is not there (before any worker connected nothing is known, so nothing is claimed)."""
        if self._sheet_metal is None or self._has_sheet_metal():
            return {}
        reason = str(self._sheet_metal.get("reason") or "the SheetMetal add-on is not available")
        why = f"{reason}. {self._sheet_metal.get('install', '')}".strip()
        return dict.fromkeys(sorted(self.SHEET_METAL_TOOLS), why)

    # --- Export (optional group, implemented) -----------------------------------------------------------

    def export_macro(self, request: ExportRequest) -> Ack:
        """The worker returns ``{"script": str}``; the client writes ``request.path``."""
        result = self._raw_call("export_macro", request)
        script = result.get("script")
        if not isinstance(script, str):
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.internal,
                    message="malformed export_macro result: missing 'script'",
                    operation_id=request.operation_id,
                    recoverable=True,
                )
            )
        path = Path(request.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(script, encoding="utf-8")
        return Ack()

    def export_step(self, request: ExportRequest) -> Ack:
        self._check_path(request.path, "the export path", request)
        return self._call("export_step", request, Ack)

    def export_mesh(self, request: ExportMeshRequest) -> Ack:
        """STL of the result solids, tessellated at ``linear_deflection_mm`` (``MeshPart``)."""
        self._check_path(request.path, "the export path", request)
        return self._call("export_mesh", request, Ack)

    def export_dxf(self, request: ExportDxfRequest) -> Ack:
        """DXF of a planar face's outline or of an unfold's flat pattern, written headless by FreeCAD's own
        ``importDXF`` exporter."""
        return self._call("export_dxf", request, Ack)

    # --- Native (optional group, implemented) --------------------------------------------------------------

    def run_native(self, request: RunNativeRequest) -> RunNativeResult:
        """Run a fragment of FreeCAD Python as one undoable step (``worker/ops_native.py``).

        The worker enforces ``request.timeout_s`` itself (a trace hook interrupts Python code between lines and
        rolls the step back); the RPC wait is ``timeout_s + NATIVE_RPC_GRACE_S`` so that clean path always wins.
        Only a fragment stuck inside a single long native call (OCC) outlives it: the worker is then killed
        like for any other timeout and the session is lost (``timeout`` here, ``session_lost`` afterwards).
        """
        return self._call("run_native", request, RunNativeResult, timeout_s=request.timeout_s + NATIVE_RPC_GRACE_S)
