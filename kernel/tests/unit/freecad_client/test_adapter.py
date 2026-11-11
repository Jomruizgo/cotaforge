"""FreeCADAdapter against ``fake_worker.py``: model validation, error mapping, connect handshake,
capabilities, export_macro and the session context manager (no FreeCAD needed)."""

import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cotaforge.adapters import rpc as shared_rpc
from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession, numberformat, rpc
from cotaforge.adapters.freecad.adapter import VISIBLE_MIN_CONNECT_TIMEOUT_S
from cotaforge.contract import (
    Ack,
    AdapterError,
    CanaryResult,
    CaptureRequest,
    ConnectRequest,
    DocumentInfo,
    DocumentKind,
    Empty,
    ErrorCode,
    ExportRequest,
    ExtrudeRequest,
    FaceHandle,
    FeatureHandle,
    MassProperties,
    MassRequest,
    NewDocumentRequest,
    OperationResult,
    SessionInfo,
    SetTimeoutRequest,
    ShowEntitiesRequest,
    ShowEntitiesResult,
    SketchHandle,
    SketchStatusRequest,
    SketchStatusResult,
    UndoResult,
    ViewRequest,
)
from tests.platform_support import PLAIN_PYTHON, hide_installed_freecad, stub_freecad_root


@pytest.fixture(autouse=True)
def plain_number_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests start a stub worker: what the Windows of the machine running them says must not refuse it."""
    monkeypatch.setattr(numberformat, "read_registry_value", lambda name: None)


FAKE_WORKER = Path(__file__).with_name("fake_worker.py")
AdapterFactory = Callable[..., FreeCADAdapter]


def _connect(timeout_s: float = 5.0) -> ConnectRequest:
    return ConnectRequest(operation_id="connect", timeout_s=timeout_s)


def _extrude(operation_id: str = "base") -> ExtrudeRequest:
    return ExtrudeRequest(operation_id=operation_id, sketch=SketchHandle(id="sketch:1"), depth_mm=8.0)


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """A directory that passes ``find_freecad_root`` (the command itself is monkeypatched)."""
    return stub_freecad_root(tmp_path / "root")


@pytest.fixture
def make_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path) -> Iterator[AdapterFactory]:
    created: list[FreeCADAdapter] = []

    def factory(mode: str = "ok", **kwargs: object) -> FreeCADAdapter:
        command = [PLAIN_PYTHON, str(FAKE_WORKER), mode]
        monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: command)
        monkeypatch.setenv("FAKE_WORKER_DELAY_S", "2")
        kwargs.setdefault("log_dir", tmp_path / "logs")
        adapter = FreeCADAdapter(fake_root, **kwargs)  # type: ignore[arg-type]
        created.append(adapter)
        return adapter

    yield factory
    for adapter in created:
        adapter.disconnect(Empty(operation_id="cleanup"))


@pytest.fixture
def connected(make_adapter: AdapterFactory) -> FreeCADAdapter:
    adapter = make_adapter()
    adapter.connect(_connect())
    return adapter


# --- connect handshake ----------------------------------------------------------------------------


def test_connect_runs_hello_validate_api_and_canary_and_returns_session_info(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter()
    info = adapter.connect(_connect())
    assert info == SessionInfo(adapter="freecad", version="0.0-fake", units="mm")
    assert adapter.worker_pid is not None
    assert adapter.name == "freecad"


def test_connect_is_idempotent_while_the_worker_is_alive(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter()
    first = adapter.connect(_connect())
    pid = adapter.worker_pid
    assert adapter.connect(_connect()) == first
    assert adapter.worker_pid == pid


def test_connect_reports_validate_api_problems_as_not_supported(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("problems")
    with pytest.raises(AdapterError) as info:
        adapter.connect(_connect())
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert "untested FreeCAD version 0.21" in info.value.info.message
    assert adapter.worker_pid is None


def test_connect_reports_an_unhealthy_canary_as_degraded_session(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("unhealthy")
    with pytest.raises(AdapterError) as info:
        adapter.connect(_connect())
    assert info.value.info.code is ErrorCode.degraded_session
    assert info.value.info.recoverable is False
    assert info.value.info.details["box"] is False
    assert adapter.worker_pid is None


def test_connect_without_hello_is_a_timeout(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("no_hello")
    with pytest.raises(AdapterError) as info:
        adapter.connect(_connect(timeout_s=0.2))
    assert info.value.info.code is ErrorCode.timeout
    assert info.value.info.recoverable is True
    assert adapter.worker_pid is None


def test_cancel_connect_stops_the_worker_being_started(make_adapter: AdapterFactory) -> None:
    """The client left while FreeCAD was still starting: connect (in another thread) ends at once and the worker it
    had spawned is gone, by its own process handle."""
    adapter = make_adapter("no_hello")
    outcome: list[BaseException] = []

    def connect() -> None:
        try:
            adapter.connect(_connect(timeout_s=30.0))
        except AdapterError as exc:
            outcome.append(exc)

    thread = threading.Thread(target=connect)
    thread.start()
    deadline = time.monotonic() + 10.0
    while (adapter._starting is None or adapter._starting.pid is None) and time.monotonic() < deadline:
        time.sleep(0.02)
    starting = adapter._starting
    assert starting is not None and starting.alive
    adapter.cancel_connect()
    thread.join(timeout=10.0)
    assert not thread.is_alive() and not starting.alive
    assert len(outcome) == 1 and isinstance(outcome[0], AdapterError)
    assert adapter.worker_pid is None


def test_a_cancelled_adapter_does_not_spawn_a_worker(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("ok")
    adapter.cancel_connect()
    with pytest.raises(AdapterError, match="cancelled"):
        adapter.connect(_connect())
    assert adapter.worker_pid is None


def test_connect_without_a_freecad_root_is_not_supported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    hide_installed_freecad(monkeypatch, tmp_path)
    adapter = FreeCADAdapter()
    with pytest.raises(AdapterError) as info:
        adapter.connect(_connect())
    assert info.value.info.code is ErrorCode.not_supported
    assert rpc.missing_root_hint() in info.value.info.message  # platform-correct hint, never a hardcoded posix path


def test_connect_maps_a_worker_spawn_failure_to_session_lost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path
) -> None:
    """A ``freecadcmd`` that exists but fails to spawn (EMFILE, exec format error, ...) must not leak a
    bare OSError/PermissionError out of ``connect()``: it maps to ``AdapterError(session_lost)``."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise PermissionError("[Errno 13] Permission denied")

    monkeypatch.setattr(shared_rpc.subprocess, "Popen", _boom)
    adapter = FreeCADAdapter(fake_root, log_dir=tmp_path / "logs")
    with pytest.raises(AdapterError) as info:
        adapter.connect(_connect())
    assert info.value.info.code is ErrorCode.session_lost
    assert info.value.info.recoverable is False
    assert "spawn failed" in info.value.info.message
    assert adapter.worker_pid is None


def test_calls_before_connect_are_session_lost(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter()
    with pytest.raises(AdapterError) as info:
        adapter.mass_properties(MassRequest(operation_id="m"))
    assert info.value.info.code is ErrorCode.session_lost
    assert info.value.info.operation_id == "m"


# --- one RPC per contract method ---------------------------------------------------------------------


def test_results_are_validated_into_contract_models(connected: FreeCADAdapter) -> None:
    doc = connected.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="plate"))
    assert isinstance(doc, DocumentInfo)
    assert doc.handle.id == "doc:1"
    result = connected.extrude(_extrude())
    assert isinstance(result, OperationResult)
    assert result.operation_id == "base"
    assert len(result.created_faces) == 6
    mass = connected.mass_properties(MassRequest(operation_id="m"))
    assert isinstance(mass, MassProperties)
    assert mass.volume_mm3 == 48000.0
    undo = connected.undo_last(Empty(operation_id="u"))
    assert isinstance(undo, UndoResult)
    assert undo.reverted is not None and undo.reverted.id == "feature:1"
    assert undo.warnings == ()  # a plain undo carries none


def test_undo_warnings_of_the_worker_reach_the_contract_result(connected: FreeCADAdapter) -> None:
    undo = connected.undo_last(Empty(operation_id="warn"))
    assert undo.warnings == ("hand edit of Box.Placement discarded",)


def test_sketch_status_sends_the_method_name_and_deserialises_the_result(connected: FreeCADAdapter) -> None:
    status = connected.sketch_status(SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id="sketch:1")))
    assert isinstance(status, SketchStatusResult)
    assert status.sketch == FeatureHandle(id="sketch:1")
    assert status.dof == 0
    assert status.fully_constrained is True
    assert status.conflicts == ()


def test_edit_feature_sends_the_typed_requests_of_every_edit_and_deserialises_the_result(
    connected: FreeCADAdapter,
) -> None:
    from cotaforge.contract import BoxRequest, CylinderRequest, EditFeatureRequest, EditResult, FeatureEdit

    edits = (
        FeatureEdit(
            feature=FeatureHandle(id="feature:1"), kind="box", request=BoxRequest(operation_id="b", size=(1, 2, 3))
        ),
        FeatureEdit(
            feature=FeatureHandle(id="feature:2"),
            kind="cylinder",
            request=CylinderRequest(
                operation_id="c",
                diameter_mm=4.0,
                height_mm=5.0,
                axis={"origin": (0, 0, 0), "direction": (0, 0, 1)},  # type: ignore[arg-type]
            ),
        ),
    )

    result = connected.edit_feature(EditFeatureRequest(operation_id="edit", edits=edits))

    assert isinstance(result, EditResult) and result.applied and result.rebuild_ok and not result.dry_run
    assert result.recomputed == (FeatureHandle(id="feature:1"), FeatureHandle(id="feature:2"))
    # the worker saw the SUBCLASS fields of each request, not just the base ``operation_id``
    assert result.warnings == ("box:operation_id,size,transform", "cylinder:axis,diameter_mm,height_mm,operation_id")
    dry = connected.edit_feature(EditFeatureRequest(operation_id="edit", edits=edits[:1], dry_run=True))
    assert dry.dry_run and not dry.applied


def test_edit_feature_is_a_capability_of_the_freecad_adapter(make_adapter: AdapterFactory) -> None:
    assert "edit_feature" in make_adapter().capabilities().tools


def test_measure_bodies_reads_the_part_through_the_worker(connected: FreeCADAdapter) -> None:
    from cotaforge.contract import Empty, MeasureBodiesResult

    assert "measure_bodies" in connected.capabilities().tools
    result = connected.measure_bodies(Empty(operation_id="m"))
    assert isinstance(result, MeasureBodiesResult)
    assert result.bodies[0].measure.solids == 2 and result.cuts[0].removed_mm3 == 0.0


def test_save_document_sends_the_stamp_and_deserialises_the_result(connected: FreeCADAdapter) -> None:
    from cotaforge.contract import DocumentStamp, SaveDocumentRequest, SaveDocumentResult

    assert "save_document" in connected.capabilities().tools
    assert FreeCADAdapter.document_suffix == ".FCStd"
    stamp = DocumentStamp(
        design_id="d",
        plan_id="p",
        plan_revision=2,
        save_seq=3,
        last_step_index=4,
        harness_version="0.0.0",
        plan_sha256="a" * 64,
    )
    result = connected.save_document(SaveDocumentRequest(operation_id="s", path="/w/models/p.FCStd", stamp=stamp))
    assert isinstance(result, SaveDocumentResult) and result.path == "/w/models/p.FCStd"
    assert result.adapter_state == {"schema": "freecad-registry/1"}
    assert (
        "plan_id=p" in result.warnings and "save_seq=3" in result.warnings and "sidecar_sha256=None" in result.warnings
    )


def test_open_document_builds_the_stamp_from_the_worker_metadata(connected: FreeCADAdapter) -> None:
    from cotaforge.contract import OpenDocumentRequest, OpenDocumentResult

    assert "open_document" in connected.capabilities().tools
    state = {"schema": "freecad-registry/2", "bodies": []}
    request = OpenDocumentRequest(operation_id="o", path="/w/models/p.FCStd", adapter_state=state)
    result = connected.open_document(request)
    assert isinstance(result, OpenDocumentResult) and result.rebind.clean
    assert result.rebind.notes == ("freecad-registry/2",)  # the adapter_state crossed the wire
    assert result.stamp is not None and result.stamp.save_seq == 3 and result.stamp.sidecar_sha256 is None
    plain = connected.open_document(OpenDocumentRequest(operation_id="o", path="/w/plain.FCStd", adapter_state=state))
    assert plain.stamp is None
    with pytest.raises(AdapterError) as newer:
        connected.open_document(OpenDocumentRequest(operation_id="o", path="/w/future.FCStd", adapter_state=state))
    assert newer.value.info.code is ErrorCode.invalid_request and newer.value.info.details["reason"] == "stamp"


def test_error_replies_become_adapter_errors_with_the_worker_error_info(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("error")
    adapter.connect(_connect())
    with pytest.raises(AdapterError) as info:
        adapter.extrude(_extrude())
    assert info.value.info.code is ErrorCode.invalid_reference
    assert info.value.info.operation_id == "base"
    assert info.value.info.details == {"handle": "face:99"}
    assert info.value.info.recoverable is False
    assert adapter.canary(Empty(operation_id="c")).healthy  # the session survives an application error


def test_malformed_error_replies_become_internal_errors(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("bad_error")
    adapter.connect(_connect())
    with pytest.raises(AdapterError) as info:
        adapter.extrude(_extrude())
    assert info.value.info.code is ErrorCode.internal
    assert info.value.info.operation_id == "base"


def test_malformed_results_become_internal_errors(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("malformed_result")
    adapter.connect(_connect())
    with pytest.raises(AdapterError) as info:
        adapter.extrude(_extrude())
    assert info.value.info.code is ErrorCode.internal
    assert "extrude" in info.value.info.message


def test_timeout_is_recoverable_and_the_worker_is_gone(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("delay")
    adapter.connect(_connect())
    adapter.set_timeout(SetTimeoutRequest(operation_id="t", timeout_s=0.2))
    with pytest.raises(AdapterError) as info:
        adapter.extrude(_extrude())
    assert info.value.info.code is ErrorCode.timeout
    assert info.value.info.recoverable is True
    assert info.value.info.details["timeout_s"] == 0.2
    assert info.value.info.operation_id == "base"
    canary = adapter.canary(Empty(operation_id="c"))
    assert canary == CanaryResult(healthy=False, checks={"process": False}, duration_ms=canary.duration_ms)


def test_worker_exit_mid_call_is_session_lost_and_health_calls_never_raise(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("exit_mid_call")
    adapter.connect(_connect())
    with pytest.raises(AdapterError) as info:
        adapter.extrude(_extrude())
    assert info.value.info.code is ErrorCode.session_lost
    assert info.value.info.recoverable is False
    assert adapter.canary(Empty(operation_id="c")).healthy is False
    validation = adapter.validate_api(Empty(operation_id="v"))
    assert validation.ok is False
    assert validation.problems
    with pytest.raises(AdapterError) as again:
        adapter.mass_properties(MassRequest(operation_id="m"))
    assert again.value.info.code is ErrorCode.session_lost


def test_a_dead_worker_is_seen_without_a_call_and_connect_relaunches_it_in_the_same_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path
) -> None:
    """A crash (here: the test kills the worker it spawned, by that PID) must not leave the adapter unusable:
    ``session_alive`` says so without an RPC and ``connect`` starts a new worker, visible again when it was visible."""
    modes: list[object] = []

    def command(root: Path, worker_dir: Path, **kw: object) -> list[str]:
        modes.append(kw.get("visible"))
        return [PLAIN_PYTHON, str(FAKE_WORKER), "ok"]

    monkeypatch.setattr(rpc, "worker_command", command)
    adapter = FreeCADAdapter(fake_root, log_dir=tmp_path / "logs", visible=True, timeout_s=5.0)
    assert adapter.session_alive() is False  # not connected yet
    with adapter:
        pid = adapter.worker_pid
        assert pid is not None and adapter.session_alive() is True
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        deadline = time.monotonic() + 10
        while adapter.session_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert adapter.session_alive() is False
        adapter.connect(_connect())
        assert adapter.session_alive() is True
        assert adapter.worker_pid not in (None, pid)
        assert modes == [True, True]


def test_set_timeout_is_client_side_only(connected: FreeCADAdapter) -> None:
    ack = connected.set_timeout(SetTimeoutRequest(operation_id="t", timeout_s=12.5))
    assert ack is not None
    assert connected.timeout_s == 12.5


# --- capabilities, export, disconnect ------------------------------------------------------------------


def test_capabilities_exclude_capture_set_view_document_events_and_tree_diff(make_adapter: AdapterFactory) -> None:
    tools = make_adapter().capabilities().tools
    assert {"export_macro", "export_step", "extrude", "canary"} <= tools
    assert not {"capture", "set_view", "document_events", "tree_diff"} & tools


def test_show_entities_is_one_rpc_and_stays_capable_even_headless(connected: FreeCADAdapter) -> None:
    """Unlike ``capture``/``set_view``/``get_selection``, ``show_entities`` is never excluded from
    ``capabilities()`` for a headless worker: it degrades on the worker side
    (``shown="unavailable"``) instead of the tool becoming entirely unusable."""
    assert "show_entities" in connected.capabilities().tools
    reply = connected.show_entities(
        ShowEntitiesRequest(operation_id="show", entities=(FaceHandle(id="face:1"),), note="the hole face")
    )
    assert reply == ShowEntitiesResult(shown="highlight")


def test_visible_adapter_offers_set_view_and_capture_and_spawns_the_gui_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path
) -> None:
    seen: dict[str, object] = {}

    def command(root: Path, worker_dir: Path, **kw: object) -> list[str]:
        seen.update(kw, root=root)
        return [PLAIN_PYTHON, str(FAKE_WORKER), "ok"]

    monkeypatch.setattr(rpc, "worker_command", command)
    adapter = FreeCADAdapter(fake_root, log_dir=tmp_path / "logs", visible=True, timeout_s=5.0)
    assert adapter.visible is True
    assert {"set_view", "capture"} <= adapter.capabilities().tools
    with adapter:
        assert seen == {"visible": True, "root": fake_root}
        assert adapter.set_view(ViewRequest(operation_id="view", name="front")) == Ack()
        target = tmp_path / "shots" / "plate.png"
        ack = adapter.capture(CaptureRequest(operation_id="cap", path=str(target), width=640, height=480))
        assert ack == Ack()


def test_the_native_environment_only_says_there_is_no_gui_when_the_worker_is_headless(fake_root: Path) -> None:
    headless = "\n".join(FreeCADAdapter(fake_root, visible=False).native_environment())
    visible = "\n".join(FreeCADAdapter(fake_root, visible=True).native_environment())
    assert "there is no GUI" in headless
    assert "there is no GUI" not in visible and "FreeCADGui" in visible and "GUI is up" in visible


def test_visible_connect_never_waits_less_than_the_gui_startup_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path
) -> None:
    monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: [PLAIN_PYTHON, str(FAKE_WORKER), "ok"])
    timeouts: list[float] = []
    original = rpc.WorkerProcess.start

    def start(self: rpc.WorkerProcess, timeout_s: float, **_kw: object) -> dict[str, object]:
        timeouts.append(timeout_s)
        return original(self, timeout_s)

    monkeypatch.setattr(rpc.WorkerProcess, "start", start)
    with FreeCADAdapter(fake_root, log_dir=tmp_path / "logs", visible=True, timeout_s=1.0):
        pass
    with FreeCADAdapter(fake_root, log_dir=tmp_path / "logs", visible=False, timeout_s=1.0):
        pass
    assert timeouts == [VISIBLE_MIN_CONNECT_TIMEOUT_S, 1.0]


def test_export_macro_writes_the_script_returned_by_the_worker(connected: FreeCADAdapter, tmp_path: Path) -> None:
    target = tmp_path / "out" / "session.py"
    connected.export_macro(ExportRequest(operation_id="x", path=str(target)))
    assert target.read_text(encoding="utf-8").startswith("import FreeCAD as App, Part, Sketcher")


def test_disconnect_stops_the_worker_and_is_idempotent(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter()
    adapter.connect(_connect())
    adapter.disconnect(Empty(operation_id="bye"))
    assert adapter.worker_pid is None
    adapter.disconnect(Empty(operation_id="bye"))
    with pytest.raises(AdapterError) as info:
        adapter.mass_properties(MassRequest(operation_id="m"))
    assert info.value.info.code is ErrorCode.session_lost


def test_keep_script_writes_the_recorded_script_on_disconnect(make_adapter: AdapterFactory, tmp_path: Path) -> None:
    adapter = make_adapter(keep_script=True, log_dir=tmp_path / "logs")
    adapter.connect(_connect())
    pid = adapter.worker_pid
    assert adapter.last_script_path is None
    adapter.disconnect(Empty(operation_id="bye"))
    assert adapter.last_script_path == tmp_path / "logs" / f"freecad-session-{pid}.py"
    assert "App.newDocument" in adapter.last_script_path.read_text(encoding="utf-8")


def test_context_manager_connects_and_disconnects(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter()
    with adapter as inside:
        assert inside is adapter
        assert adapter.worker_pid is not None
    assert adapter.worker_pid is None


def test_session_yields_a_connected_adapter_and_closes_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path
) -> None:
    monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: [PLAIN_PYTHON, str(FAKE_WORKER), "ok"])
    with FreeCADSession(freecad_root=fake_root, log_dir=tmp_path / "logs") as adapter:
        assert isinstance(adapter, FreeCADAdapter)
        assert adapter.worker_pid is not None
        assert adapter.canary(Empty(operation_id="c")).healthy
    assert adapter.worker_pid is None


def test_session_kills_the_worker_when_the_body_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path
) -> None:
    monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: [PLAIN_PYTHON, str(FAKE_WORKER), "ok"])
    session = FreeCADSession(freecad_root=fake_root, log_dir=tmp_path / "logs")
    with pytest.raises(RuntimeError), session as adapter:
        raise RuntimeError("boom")
    assert adapter.worker_pid is None


def test_worker_log_lives_under_log_dir(make_adapter: AdapterFactory, tmp_path: Path) -> None:
    adapter = make_adapter("noise", log_dir=tmp_path / "logs")
    adapter.connect(_connect())
    adapter.disconnect(Empty(operation_id="bye"))
    logs = list((tmp_path / "logs").glob("freecad-worker-*.log"))
    assert len(logs) == 1
    assert "Recompute" in logs[0].read_text(encoding="utf-8")
