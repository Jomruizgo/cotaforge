"""A long request (``import_geometry``) is waited for by liveness, not by a fixed time, and says how far it is.

The rule itself (``LivenessWatch``) is tested as a pure function of time and CPU samples; the plumbing -- heartbeats,
the worker's ``progress`` notifications, the kill and the error that names the rule that fired -- through
``FreeCADAdapter`` against ``fake_worker.py``'s ``import_*`` modes: a worker that uses CPU for a while and then answers,
one that sleeps, and one that never stops computing. No FreeCAD needed."""

import contextlib
import importlib.util
import os
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cotaforge.adapters import procstats
from cotaforge.adapters.freecad import FreeCADAdapter, rpc
from cotaforge.adapters.freecad.rpc import Liveness, WorkerProcess, WorkerTimeout
from cotaforge.adapters.procstats import Vitals
from cotaforge.adapters.rpc import MIN_CPU_ADVANCE_S, LivenessWatch, WorkerLost
from cotaforge.contract import (
    AdapterError,
    BoxRequest,
    ConnectRequest,
    DocumentHandle,
    Empty,
    ErrorCode,
    ImportGeometryRequest,
    InsertComponentRequest,
    MassRequest,
    ProgressNotice,
    ProgressProducer,
    Transform,
    ValidityRequest,
)
from tests.platform_support import PLAIN_PYTHON, stub_freecad_root

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")
WORKER_DIR = Path(rpc.__file__).parent / "worker"
AdapterFactory = Callable[..., FreeCADAdapter]


# --- the rule, as a function of time and CPU samples -----------------------------------------------------------------


def watch(stall_s: float = 10.0, max_s: float = 100.0) -> LivenessWatch:
    return LivenessWatch(Liveness(stall_s=stall_s, max_s=max_s), now=1000.0)


def test_a_process_whose_cpu_keeps_advancing_is_never_given_up_on_by_the_stall_rule() -> None:
    rule = watch(stall_s=10.0)
    cpu = 0.0
    for second in range(1, 90):  # 89 s, far past the stall time, always using CPU
        cpu += 1.0
        assert rule.check(1000.0 + second, cpu) is None


def test_a_process_that_stops_using_cpu_is_given_up_on_after_the_stall_time() -> None:
    rule = watch(stall_s=10.0)
    assert rule.check(1005.0, 4.0) is None  # baseline sample
    assert rule.check(1012.0, 4.0) is None  # 7 s without progress: not yet
    assert rule.check(1015.0, 4.0) == ("stall", 10.0)  # 10 s since the last progress (at 1005)


def test_cpu_progress_restarts_the_stall_clock() -> None:
    rule = watch(stall_s=10.0)
    assert rule.check(1005.0, 4.0) is None
    assert rule.check(1013.0, 4.0) is None
    assert rule.check(1014.0, 5.0) is None  # it used a second: the clock starts again here
    assert rule.check(1023.0, 5.0) is None
    assert rule.check(1024.0, 5.0) == ("stall", 10.0)


def test_a_sliver_of_cpu_is_not_progress() -> None:
    rule = watch(stall_s=10.0)
    assert rule.check(1001.0, 4.0) is None
    assert rule.check(1008.0, 4.0 + MIN_CPU_ADVANCE_S / 2) is None
    assert rule.check(1011.0, 4.0 + MIN_CPU_ADVANCE_S / 2) == ("stall", 10.0)


def test_a_process_that_never_used_cpu_stalls_from_its_first_sample() -> None:
    rule = watch(stall_s=10.0)
    assert rule.check(1005.0, 0.0) is None  # the first sample is the baseline
    assert rule.check(1014.0, 0.0) is None
    assert rule.check(1015.0, 0.0) == ("stall", 10.0)


def test_the_overall_cap_fires_even_while_cpu_advances() -> None:
    rule = watch(stall_s=10.0, max_s=60.0)
    cpu = 0.0
    for second in range(1, 60):
        cpu += 1.0
        assert rule.check(1000.0 + second, cpu) is None
    assert rule.check(1060.0, cpu + 1.0) == ("cap", 60.0)


def test_without_a_cpu_reading_only_the_cap_protects() -> None:
    rule = watch(stall_s=10.0, max_s=60.0)
    assert rule.check(1050.0, None) is None  # cannot tell: never a stall
    assert rule.check(1060.0, None) == ("cap", 60.0)


# --- what the operating system says about a process ------------------------------------------------------------------


def test_the_cpu_time_is_read_from_a_proc_stat_line_whose_command_name_has_spaces_and_parentheses() -> None:
    line = "1234 (Free (CAD) cmd) S 1 1234 1234 0 -1 4194560 100 0 0 0 250 50 0 0 20 0 8 0 1000 2000 300 1844"
    assert procstats.cpu_seconds_from_proc_stat(line, 100.0) == pytest.approx(3.0)  # (250 + 50) ticks of 1/100 s
    assert procstats.cpu_seconds_from_proc_stat("garbage", 100.0) is None
    assert procstats.cpu_seconds_from_proc_stat(line, 0.0) is None


def test_windows_filetime_counts_hundred_nanosecond_ticks() -> None:
    assert procstats.filetime_to_seconds(25_000_000) == pytest.approx(2.5)


@pytest.mark.skipif(not sys.platform.startswith(("linux", "win32")), reason="no process figures on this platform")
def test_the_vitals_of_this_very_process_are_known() -> None:
    sum(range(3_000_000))  # some CPU of its own
    vitals = procstats.sample(os.getpid())
    assert vitals.cpu_s is not None and vitals.cpu_s > 0.0
    assert vitals.memory_mb is not None and vitals.memory_mb > 1.0


def test_the_vitals_of_a_process_that_does_not_exist_are_unknown() -> None:
    assert procstats.sample(None) == Vitals()
    assert procstats.sample(2**22 + 12345) == Vitals()


# --- the worker's own side: throttled progress notifications ---------------------------------------------------------


def load_worker_module(name: str) -> object:
    spec = importlib.util.spec_from_file_location(f"worker_{name}", WORKER_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_worker_reporter_sends_the_first_and_last_of_a_phase_and_a_few_in_between() -> None:
    reporting = load_worker_module("reporting")
    now = [0.0]
    sent: list[dict] = []
    report = reporting.Reporter(sent.append, "op1", min_interval_s=1.0, clock=lambda: now[0])  # type: ignore[attr-defined]

    report("reading", None, None, "reading the file")
    for index in range(1, 101):  # 100 bodies, 0.05 s apart: 5 s in all
        now[0] += 0.05
        report("adopting", index, 100, f"adopting body {index}/100")
    report("checking", 1, 2, "checking body 1/2")
    report("checking", 2, 2, "checking body 2/2")

    phases = [(m["phase"], m["current"]) for m in sent]
    assert phases[0] == ("reading", None)
    assert phases[1] == ("adopting", 1)  # the first of a new phase always goes
    assert ("adopting", 100) in phases  # and the last
    adopting = [m for m in sent if m["phase"] == "adopting"]
    assert 4 <= len(adopting) <= 9  # about one a second, not a hundred
    assert [m["current"] for m in adopting] == sorted(m["current"] for m in adopting)
    assert phases[-2:] == [("checking", 1), ("checking", 2)]
    assert all(m["operation_id"] == "op1" for m in sent)


def test_the_worker_reporter_without_a_channel_does_nothing() -> None:
    reporting = load_worker_module("reporting")
    reporting.Reporter(None)("adopting", 1, 2, "x")  # type: ignore[attr-defined]


# --- the plumbing: heartbeats, notifications, kills ------------------------------------------------------------------


@pytest.fixture
def make_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[AdapterFactory]:
    created: list[FreeCADAdapter] = []
    root = stub_freecad_root(tmp_path / "root")

    def factory(mode: str, **kwargs: object) -> FreeCADAdapter:
        command = [PLAIN_PYTHON, str(FAKE_WORKER), mode]
        monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: command)
        kwargs.setdefault("log_dir", tmp_path / "logs")
        adapter = FreeCADAdapter(root, **kwargs)  # type: ignore[arg-type]
        adapter.connect(ConnectRequest(operation_id="connect", timeout_s=10.0))
        created.append(adapter)
        return adapter

    yield factory
    for adapter in created:
        adapter.disconnect(Empty(operation_id="cleanup"))


@pytest.fixture
def step_file(tmp_path: Path) -> Path:
    path = tmp_path / "customer.step"
    path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
    return path


def import_request(path: Path) -> ImportGeometryRequest:
    return ImportGeometryRequest(operation_id="import.1", path=str(path), name="customer")


def test_the_freecad_adapter_can_say_how_far_a_long_request_is() -> None:
    assert isinstance(FreeCADAdapter(), ProgressProducer)


def test_a_busy_worker_is_waited_for_past_the_stall_time_and_reports_while_it_works(
    make_adapter: AdapterFactory, step_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_WORKER_BUSY_S", "1.6")
    adapter = make_adapter("import_busy", import_stall_s=0.6, import_max_s=60.0, heartbeat_s=0.15)
    heard: list[ProgressNotice] = []
    adapter.set_progress_listener(heard.append)
    pid = adapter.worker_pid

    started = time.monotonic()
    result = adapter.import_geometry(import_request(step_file))

    assert time.monotonic() - started > 1.5  # longer than the stall time: the CPU kept it alive
    assert result.document.name == "customer" and adapter.worker_pid == pid  # same process, never killed
    beats = [n for n in heard if n.heartbeat]
    assert len(beats) >= 4
    assert all(b.phase == "reading" and "FreeCAD gives no percentage" in b.message for b in beats[:3])
    assert beats[1].elapsed_s is not None and beats[1].elapsed_s > beats[0].elapsed_s  # type: ignore[operator]
    if procstats.sample(pid).cpu_s is not None:
        assert beats[-1].cpu_s is not None and beats[-1].cpu_s > beats[0].cpu_s  # type: ignore[operator]
        assert beats[-1].memory_mb is not None and "CAD process CPU" in beats[-1].message
    worker_side = [n for n in heard if not n.heartbeat]
    assert [(n.phase, n.current, n.total) for n in worker_side] == [("adopting", i, 3) for i in (1, 2, 3)]
    assert worker_side[0].message == "adopting body 1/3"


def test_a_heartbeat_after_the_worker_reported_a_phase_repeats_that_phase(
    make_adapter: AdapterFactory, step_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_WORKER_BUSY_S", "0.1")
    adapter = make_adapter("import_busy", import_stall_s=5.0, import_max_s=60.0, heartbeat_s=0.05)
    heard: list[ProgressNotice] = []
    adapter.set_progress_listener(heard.append)
    adapter.import_geometry(import_request(step_file))
    adapter.set_progress_listener(None)  # nothing is heard any more, and nothing breaks
    adapter.import_geometry(import_request(step_file))
    assert {n.phase for n in heard if not n.heartbeat} == {"adopting"}
    after = [n for n in heard if n.heartbeat and n.current is not None]
    assert after
    assert all(n.phase == "adopting" and n.total == 3 for n in after)


def test_a_worker_that_stops_using_cpu_is_killed_and_the_error_names_the_stall_rule(
    make_adapter: AdapterFactory, step_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not sys.platform.startswith(("linux", "win32")):
        pytest.skip("no CPU time on this platform")
    monkeypatch.setenv("FAKE_WORKER_DELAY_S", "30")
    adapter = make_adapter("import_idle", import_stall_s=0.6, import_max_s=60.0, heartbeat_s=0.15)

    started = time.monotonic()
    with pytest.raises(AdapterError) as caught:
        adapter.import_geometry(import_request(step_file))

    info = caught.value.info
    assert time.monotonic() - started < 10.0
    assert info.code is ErrorCode.timeout and info.recoverable
    assert info.details["rule"] == "stall" and info.details["limit_s"] == 0.6
    assert "stall rule" in info.message and "COTAFORGE_IMPORT_STALL_S" in info.message
    assert adapter.worker_pid is None  # the CAD process is gone


def test_a_worker_that_computes_for_ever_is_killed_by_the_overall_cap(
    make_adapter: AdapterFactory, step_file: Path
) -> None:
    adapter = make_adapter("import_busy_forever", import_stall_s=60.0, import_max_s=1.2, heartbeat_s=0.15)

    with pytest.raises(AdapterError) as caught:
        adapter.import_geometry(import_request(step_file))

    info = caught.value.info
    assert info.code is ErrorCode.timeout
    assert info.details["rule"] == "cap" and info.details["limit_s"] == 1.2
    assert "cap rule" in info.message and "COTAFORGE_IMPORT_MAX_S" in info.message
    assert adapter.worker_pid is None


def test_where_the_cpu_time_cannot_be_read_a_quiet_worker_is_not_called_stalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_WORKER_DELAY_S", "1.5")
    process = WorkerProcess(
        [PLAIN_PYTHON, str(FAKE_WORKER), "import_idle"],
        tmp_path / "worker.log",
        use_fd_channel=sys.platform != "win32",
        vitals=lambda pid: Vitals(),
    )
    process.start(10.0)
    try:
        beats = []
        liveness = Liveness(stall_s=0.3, max_s=30.0, heartbeat_s=0.1, on_heartbeat=beats.append)
        reply = process.call("import_geometry", {"operation_id": "x", "name": "n", "path": "p"}, 1.0, liveness=liveness)
        assert reply["source_format"] == "step"
        assert beats and all(b.vitals == Vitals() for b in beats)
    finally:
        process.stop()


def test_a_worker_that_dies_while_waited_for_is_reported_as_lost_not_as_a_timeout(
    tmp_path: Path,
) -> None:
    process = WorkerProcess(
        [PLAIN_PYTHON, str(FAKE_WORKER), "exit_mid_call"],
        tmp_path / "worker.log",
        use_fd_channel=sys.platform != "win32",
    )
    process.start(10.0)
    try:
        with pytest.raises(WorkerLost):
            process.call(
                "import_geometry",
                {"operation_id": "x", "name": "n", "path": "p"},
                1.0,
                liveness=Liveness(stall_s=30.0, max_s=60.0, heartbeat_s=0.1),
            )
    finally:
        process.stop()


def test_the_timeout_without_a_rule_keeps_its_old_wording() -> None:
    assert str(WorkerTimeout("save_document", 300.0)) == "save_document: no reply within 300.0 s"


def test_what_is_asked_of_the_components_of_a_large_assembly_is_waited_for_by_liveness_too(
    make_adapter: AdapterFactory,
) -> None:
    """An insert, a measure of hundreds of bodies: a fixed 60 s would kill the CAD (and the document with it) on a
    slow machine, so these are waited for like an import; the quick calls keep the fixed time."""
    adapter = make_adapter("import_busy", import_stall_s=7.0, import_max_s=70.0, heartbeat_s=0.5)
    seen: dict[str, Liveness | None] = {}
    real = adapter._rpc.call  # type: ignore[union-attr]

    def spy(method: str, params: dict[str, object], timeout_s: float, **kwargs: object) -> dict:
        seen[method] = kwargs.get("liveness")  # type: ignore[assignment]
        return real(method, params, timeout_s, **kwargs)

    adapter._rpc.call = spy  # type: ignore[union-attr,method-assign]
    for call in (
        lambda: adapter.insert_component(
            InsertComponentRequest(operation_id="i", part=DocumentHandle(id="doc:1"), transform=Transform.identity())
        ),
        lambda: adapter.mass_properties(MassRequest(operation_id="m")),
        lambda: adapter.bounding_box(MassRequest(operation_id="b")),
        lambda: adapter.is_valid(ValidityRequest(operation_id="v")),
        lambda: adapter.box(BoxRequest(operation_id="x", size=(1.0, 1.0, 1.0))),
    ):
        with contextlib.suppress(
            AdapterError
        ):  # the stub worker may refuse them: only how they were waited for matters
            call()
    for method in ("insert_component", "mass_properties", "bounding_box", "is_valid"):
        waiting = seen[method]
        assert waiting is not None and (waiting.stall_s, waiting.max_s) == (7.0, 70.0), method
    assert seen["box"] is None


def test_the_drawing_calls_are_waited_for_by_liveness_too() -> None:
    """The views of an assembly sheet take minutes: no fixed time, the same rule as an import."""
    from cotaforge.adapters.freecad.adapter import COMPONENT_METHODS, DRAWING_METHODS

    assert DRAWING_METHODS == {
        "create_drawing",
        "annotate_drawing",
        "describe_drawing",
        "capture_drawing",
        "export_drawing",
    }
    assert not DRAWING_METHODS & COMPONENT_METHODS
