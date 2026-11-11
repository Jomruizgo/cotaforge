"""The Windows number format check and the "Invalid system settings" dialog probe (fake registry values and window
titles: nothing here reads the registry or looks at a window)."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from cotaforge.adapters import rpc as shared_rpc
from cotaforge.adapters.freecad import FreeCADAdapter, numberformat, rpc
from cotaforge.adapters.rpc import WorkerBlocked, WorkerProcess, WorkerTimeout
from cotaforge.contract import AdapterError, ConnectRequest, Empty, ErrorCode
from tests.platform_support import PLAIN_PYTHON, pid_running, stub_freecad_root

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")


def _registry(**values: str) -> Callable[[str], str | None]:
    return lambda name: values.get(name)


# --- separator_conflict ---------------------------------------------------------------------------


def test_equal_decimal_and_thousand_separators_are_reported_with_where_to_change_them() -> None:
    text = numberformat.separator_conflict(_registry(sDecimal=",", sThousand=","))
    assert text is not None
    assert "','" in text
    assert "Formato regional" in text
    assert "Idioma y región" in text
    assert "never changes a Windows setting" in text
    assert "headless" in text


@pytest.mark.parametrize(
    "values",
    [{"sDecimal": ",", "sThousand": "."}, {"sDecimal": "."}, {"sThousand": "."}, {}, {"sDecimal": "", "sThousand": ""}],
)
def test_distinct_or_unreadable_separators_are_not_a_conflict(values: dict[str, str]) -> None:
    assert numberformat.separator_conflict(_registry(**values)) is None


def test_the_registry_is_not_read_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(numberformat, "sys", SimpleNamespace(platform="linux"))
    assert numberformat.read_registry_value("sDecimal") is None


# --- blocked_by_dialog ----------------------------------------------------------------------------


def test_a_window_of_the_process_titled_invalid_system_settings_is_the_dialog() -> None:
    text = numberformat.blocked_by_dialog(42, lambda pid: ["FreeCAD", "Invalid system settings"] if pid == 42 else [])
    assert text is not None
    assert "Invalid system settings" in text
    assert "Formato regional" in text


def test_other_windows_or_no_pid_are_not_the_dialog() -> None:
    assert numberformat.blocked_by_dialog(42, lambda pid: ["FreeCAD", "Start Center"]) is None
    assert numberformat.blocked_by_dialog(42, lambda pid: []) is None
    assert numberformat.blocked_by_dialog(None, lambda pid: ["Invalid system settings"]) is None


def test_a_failed_window_look_is_not_a_verdict() -> None:
    def broken(pid: int) -> list[str]:
        raise OSError("no desktop")

    assert numberformat.blocked_by_dialog(42, broken) is None


# --- WorkerProcess.start probe --------------------------------------------------------------------


@pytest.fixture
def silent_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[WorkerProcess]:
    """A worker that never sends its hello (it sleeps)."""
    monkeypatch.setenv("FAKE_WORKER_DELAY_S", "30")
    worker = WorkerProcess([PLAIN_PYTHON, str(FAKE_WORKER), "no_hello"], tmp_path / "w.log", use_fd_channel=False)
    yield worker
    worker.stop(grace_s=0.5)


def test_a_probe_verdict_ends_the_worker_by_pid_and_stops_the_wait(silent_worker: WorkerProcess) -> None:
    seen: list[int | None] = []

    def probe(pid: int | None) -> str | None:
        seen.append(pid)
        return "stuck on a dialog"

    started = time.monotonic()
    with pytest.raises(WorkerBlocked) as info:
        silent_worker.start(30.0, probe=probe)
    assert time.monotonic() - started < 10.0
    assert info.value.reason == "stuck on a dialog"
    assert seen and seen[0] == silent_worker.pid
    assert silent_worker.pid is not None
    assert not pid_running(silent_worker.pid)


def test_a_probe_without_a_verdict_still_times_out(silent_worker: WorkerProcess) -> None:
    with pytest.raises(WorkerTimeout):
        silent_worker.start(1.5, probe=lambda pid: None)
    assert not silent_worker.alive


def test_a_broken_probe_does_not_decide_the_start(silent_worker: WorkerProcess) -> None:
    def broken(pid: int | None) -> str | None:
        raise RuntimeError("boom")

    with pytest.raises(WorkerTimeout):
        silent_worker.start(1.5, probe=broken)


def test_the_probe_is_part_of_the_shared_transport() -> None:
    assert issubclass(WorkerBlocked, shared_rpc.WorkerLost)
    assert rpc.WorkerBlocked is WorkerBlocked


# --- the adapter ----------------------------------------------------------------------------------


@pytest.fixture
def make_visible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Callable[..., FreeCADAdapter]]:
    created: list[FreeCADAdapter] = []

    def factory(mode: str = "ok", *, visible: bool = True) -> FreeCADAdapter:
        command = [PLAIN_PYTHON, str(FAKE_WORKER), mode]
        monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: command)
        monkeypatch.setenv("FAKE_WORKER_DELAY_S", "30")
        adapter = FreeCADAdapter(stub_freecad_root(tmp_path / "root"), log_dir=tmp_path / "logs", visible=visible)
        created.append(adapter)
        return adapter

    yield factory
    for adapter in created:
        adapter.disconnect(Empty(operation_id="cleanup"))


def _connect(timeout_s: float = 5.0) -> ConnectRequest:
    return ConnectRequest(operation_id="connect", timeout_s=timeout_s)


def test_visible_connect_with_equal_separators_says_why_and_starts_nothing(
    make_visible: Callable[..., FreeCADAdapter], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(numberformat, "read_registry_value", _registry(sDecimal=",", sThousand=","))
    adapter = make_visible()
    with pytest.raises(AdapterError) as info:
        adapter.connect(_connect())
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is True
    assert "Formato regional" in info.value.info.message
    assert adapter.worker_pid is None


def test_headless_connect_ignores_the_number_format(
    make_visible: Callable[..., FreeCADAdapter], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(numberformat, "read_registry_value", _registry(sDecimal=",", sThousand=","))
    adapter = make_visible("ok", visible=False)
    adapter.connect(_connect())
    assert adapter.worker_pid is not None


def test_a_dialog_found_while_waiting_for_the_window_ends_the_start_at_once(
    make_visible: Callable[..., FreeCADAdapter], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(numberformat, "read_registry_value", _registry(sDecimal=",", sThousand="."))
    monkeypatch.setattr(numberformat, "_windows_titles", lambda pid: ["Invalid system settings"])
    monkeypatch.setattr(numberformat, "sys", SimpleNamespace(platform="win32"))
    adapter = make_visible("no_hello")
    started = time.monotonic()
    with pytest.raises(AdapterError) as info:
        adapter.connect(_connect())
    assert time.monotonic() - started < 10.0  # the visible start would otherwise wait 15 s at least
    assert info.value.info.code is ErrorCode.not_supported
    assert "Invalid system settings" in info.value.info.message
    assert "Formato regional" in info.value.info.message
    assert adapter.worker_pid is None
