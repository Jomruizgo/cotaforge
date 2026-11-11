"""``WorkerProcess`` framing, timeouts, crash handling and spawn-failure paths — the CAD-agnostic
transport shared by every adapter. Exercised against ``echo_worker.py`` (system Python, scripted responses); no CAD
application needed. FreeCAD-specific behaviour (``find_freecad_root``, ``worker_command``, the FreeCAD-flavoured
end-to-end protocol) lives in ``tests/unit/freecad_client/test_rpc.py``.
"""

import gc
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cotaforge.adapters import rpc
from cotaforge.adapters.rpc import RpcFailure, WorkerLost, WorkerProcess, WorkerTimeout
from tests.platform_support import FD_CHANNEL_SUPPORTED, PLAIN_PYTHON, pid_running

ECHO_WORKER = Path(__file__).with_name("echo_worker.py")
WorkerFactory = Callable[..., WorkerProcess]

requires_fd_channel = pytest.mark.skipif(
    not FD_CHANNEL_SUPPORTED,
    reason="the response pipe fd is inherited through pass_fds, which is POSIX-only; on Windows the "
    "FreeCAD adapter uses the stdout channel (covered by every test here that runs without it)",
)


@pytest.fixture
def make_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[WorkerFactory]:
    started: list[WorkerProcess] = []

    def factory(mode: str = "ok", *, use_fd_channel: bool = FD_CHANNEL_SUPPORTED, **kwargs: object) -> WorkerProcess:
        monkeypatch.setenv("ECHO_WORKER_DELAY_S", "2")
        command = [PLAIN_PYTHON, str(ECHO_WORKER), mode]
        worker = WorkerProcess(command, tmp_path / "worker.log", use_fd_channel=use_fd_channel, **kwargs)  # type: ignore[arg-type]
        started.append(worker)
        return worker

    yield factory
    for worker in started:
        worker.stop(grace_s=0.5)


def test_start_returns_the_hello_result(make_worker: WorkerFactory) -> None:
    worker = make_worker()
    hello = worker.start(timeout_s=5.0)
    assert hello["hello"] == "echo-worker"
    assert hello["pid"] == worker.pid
    assert worker.alive


def test_call_returns_the_result_matched_by_id(make_worker: WorkerFactory) -> None:
    worker = make_worker()
    worker.start(timeout_s=5.0)
    result = worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert result["echo"] == {"operation_id": "base"}


def test_noise_on_stdout_and_stderr_goes_to_the_log_not_the_channel(make_worker: WorkerFactory) -> None:
    worker = make_worker("noise")
    worker.start(timeout_s=5.0)
    worker.call("canary", {"operation_id": "c"}, timeout_s=5.0)
    worker.stop(grace_s=2.0)
    log = worker.log_path.read_text(encoding="utf-8")
    assert "worker noise on stdout" in log
    assert "worker noise on stderr" in log


def test_stdout_fallback_filters_the_cadh_prefix_end_to_end(make_worker: WorkerFactory) -> None:
    """``use_fd_channel=False`` is exactly the Windows-interop transport: no pipe fd can cross the boundary, so the
    worker prints ``@cadh {json}`` on stdout and everything else is log noise."""
    worker = make_worker("stdout_fallback", use_fd_channel=False)
    hello = worker.start(timeout_s=5.0)
    assert hello["hello"] == "echo-worker"
    result = worker.call("canary", {"operation_id": "c"}, timeout_s=5.0)
    assert result["echo"] == {"operation_id": "c"}
    worker.stop(grace_s=2.0)
    log = worker.log_path.read_text(encoding="utf-8")
    assert "worker noise on stdout" in log
    assert "@cadh" not in log


def test_caller_supplied_rpc_fd_env_never_leaks_into_a_stdout_channel_child(make_worker: WorkerFactory) -> None:
    """``start()`` strips a caller-supplied ``COTAFORGE_RPC_FD`` unconditionally and only re-sets it on
    the ``use_fd_channel=True`` branch, so a stdout-channel (Windows-interop) child never sees a stale
    fd number that does not exist in its own process."""
    worker = make_worker("ok", use_fd_channel=False, env={"COTAFORGE_RPC_FD": "5"})
    hello = worker.start(timeout_s=5.0)
    assert hello["rpc_fd_env"] is None
    result = worker.call("canary", {"operation_id": "c"}, timeout_s=5.0)
    assert result["echo"] == {"operation_id": "c"}


def test_garbage_line_on_the_channel_is_session_lost(make_worker: WorkerFactory) -> None:
    worker = make_worker("garbage")
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerLost) as info:
        worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert "this is not json" in info.value.details["line"]
    assert not worker.alive


def test_delayed_reply_times_out_and_kills_the_worker(make_worker: WorkerFactory) -> None:
    worker = make_worker("delay")
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerTimeout) as info:
        worker.call("extrude", {"operation_id": "base"}, timeout_s=0.2)
    assert info.value.timeout_s == 0.2
    assert info.value.method == "extrude"
    assert not worker.alive


def test_exit_mid_call_is_worker_lost(make_worker: WorkerFactory) -> None:
    worker = make_worker("exit_mid_call")
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerLost) as info:
        worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert "exit" in str(info.value)
    assert not worker.alive


def test_call_on_a_dead_worker_is_worker_lost_without_writing(make_worker: WorkerFactory) -> None:
    worker = make_worker("exit_mid_call")
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerLost):
        worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    with pytest.raises(WorkerLost):
        worker.call("canary", {"operation_id": "c"}, timeout_s=5.0)


def test_wrong_id_is_worker_lost(make_worker: WorkerFactory) -> None:
    worker = make_worker("wrong_id")
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerLost) as info:
        worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert "id" in str(info.value)
    assert not worker.alive


def test_error_reply_raises_rpc_failure_with_the_error_dict(make_worker: WorkerFactory) -> None:
    worker = make_worker("error")
    worker.start(timeout_s=5.0)
    with pytest.raises(RpcFailure) as info:
        worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert info.value.error["code"] == "invalid_reference"
    assert info.value.error["operation_id"] == "base"
    assert worker.alive  # an application error does not lose the session


def test_missing_hello_times_out_on_start(make_worker: WorkerFactory) -> None:
    worker = make_worker("no_hello")
    with pytest.raises(WorkerTimeout):
        worker.start(timeout_s=0.2)
    assert not worker.alive


def test_a_worker_stopped_before_start_spawns_nothing(
    make_worker: WorkerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_spawn(*args: object, **kwargs: object) -> None:
        raise AssertionError("a stopped worker must not be spawned")

    worker = make_worker()
    worker.stop(grace_s=0.0)  # another thread gave up before start() reached the spawn
    monkeypatch.setattr(rpc.subprocess, "Popen", no_spawn)
    with pytest.raises(WorkerLost, match="stopped before"):
        worker.start(timeout_s=5.0)
    assert worker.pid is None and not worker.alive


def test_a_stop_that_lands_during_the_spawn_kills_the_child_it_missed(
    make_worker: WorkerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop() runs between start()'s first check and the child existing: it finds no process to stop, so start() ends
    the child itself instead of waiting for its hello."""
    worker = make_worker()
    real_popen = subprocess.Popen
    spawned: list[subprocess.Popen[bytes]] = []

    def popen_then_stop(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        worker.stop(grace_s=0.0)  # sees no process yet
        proc = real_popen(*args, **kwargs)  # type: ignore[call-overload]
        spawned.append(proc)
        return proc

    monkeypatch.setattr(rpc.subprocess, "Popen", popen_then_stop)
    started = time.monotonic()
    with pytest.raises(WorkerLost, match="stopped while"):
        worker.start(timeout_s=30.0)
    assert time.monotonic() - started < 10.0  # no wait for a hello
    assert len(spawned) == 1 and spawned[0].poll() is not None and not worker.alive


def test_a_stop_while_the_reader_thread_is_being_created_does_not_fail(
    make_worker: WorkerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop() from another thread lands after the child exists but before its reader thread has started: it must
    not try to join a thread that was never started (a Windows CI run raised "cannot join thread before it is
    started" from exactly this window)."""
    worker = make_worker()
    stopped: list[BaseException | None] = []

    class StopFirstThread(threading.Thread):
        def start(self) -> None:
            try:
                worker.stop(grace_s=0.0)  # the child exists, its reader is created but not started
                stopped.append(None)
            except BaseException as exc:  # noqa: BLE001 - recorded to assert on below
                stopped.append(exc)
            super().start()

    monkeypatch.setattr(rpc.threading, "Thread", StopFirstThread)
    with pytest.raises(WorkerLost):
        worker.start(timeout_s=30.0)
    assert stopped == [None]
    assert not worker.alive


def test_stop_ends_the_process_and_is_idempotent(make_worker: WorkerFactory) -> None:
    worker = make_worker()
    worker.start(timeout_s=5.0)
    worker.stop(grace_s=2.0)
    assert not worker.alive
    worker.stop(grace_s=0.1)
    with pytest.raises(WorkerLost):
        worker.call("canary", {"operation_id": "c"}, timeout_s=1.0)


def test_disconnect_reply_then_exit_is_clean(make_worker: WorkerFactory) -> None:
    worker = make_worker()
    worker.start(timeout_s=5.0)
    assert worker.call("disconnect", {"operation_id": "bye"}, timeout_s=5.0) == {}
    worker.stop(grace_s=2.0)
    assert worker.returncode == 0


def test_kill_hook_is_invoked_on_stop_when_the_worker_ignores_eof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``kill_hook`` is the Windows-interop extra-kill point (``taskkill.exe /PID <pid> /T /F``): it must run right
    before the default ``proc.kill()`` whenever ``stop()`` actually has to force-kill the child."""
    monkeypatch.setenv("ECHO_WORKER_DELAY_S", "5")
    hooked: list[subprocess.Popen[bytes]] = []
    worker = WorkerProcess(
        [PLAIN_PYTHON, str(ECHO_WORKER), "hang_on_stop"],
        tmp_path / "worker.log",
        use_fd_channel=FD_CHANNEL_SUPPORTED,
        kill_hook=hooked.append,
    )
    worker.start(timeout_s=5.0)
    worker.stop(grace_s=0.3)
    assert not worker.alive
    assert len(hooked) == 1


def test_kill_hook_is_invoked_at_most_once_on_a_call_timeout(make_worker: WorkerFactory) -> None:
    """The timeout path (``_wait_message`` -> ``_kill``) must invoke ``kill_hook`` exactly once."""
    hooked: list[subprocess.Popen[bytes]] = []
    worker = make_worker("delay", kill_hook=hooked.append)
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerTimeout):
        worker.call("extrude", {"operation_id": "base"}, timeout_s=0.2)
    assert not worker.alive
    assert len(hooked) == 1


def test_kill_hook_is_not_invoked_when_the_worker_already_exited(make_worker: WorkerFactory) -> None:
    """``_kill`` only invokes ``kill_hook`` for a still-alive process; a worker that already exited on
    its own (``exit_mid_call``) must never see it called."""
    hooked: list[subprocess.Popen[bytes]] = []
    worker = make_worker("exit_mid_call", kill_hook=hooked.append)
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerLost):
        worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert not worker.alive
    assert hooked == []


def test_kill_hook_is_invoked_at_most_once_by_the_finalizer_when_dropped_without_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dropping a ``WorkerProcess`` without calling ``stop()`` reaps the child via ``weakref.finalize``
    (``_kill_child``), which must also invoke ``kill_hook`` exactly once."""
    monkeypatch.setenv("ECHO_WORKER_DELAY_S", "2")
    hooked: list[subprocess.Popen[bytes]] = []
    worker = WorkerProcess(
        [PLAIN_PYTHON, str(ECHO_WORKER), "ok"],
        tmp_path / "worker.log",
        use_fd_channel=FD_CHANNEL_SUPPORTED,
        kill_hook=hooked.append,
    )
    worker.start(timeout_s=5.0)
    pid = worker.pid
    assert pid is not None
    del worker
    gc.collect()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and pid_running(pid):
        time.sleep(0.05)
    assert not pid_running(pid), "finalizer did not reap the child worker process"
    assert len(hooked) == 1


def test_kill_hook_is_invoked_at_most_once_when_abort_spawn_kills_after_a_failed_thread_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``_abort_spawn`` (reached when the reader thread cannot start) reuses ``_kill_child`` and must
    also invoke ``kill_hook`` exactly once for the spawned-but-orphaned child."""
    monkeypatch.setenv("ECHO_WORKER_DELAY_S", "2")
    hooked: list[subprocess.Popen[bytes]] = []
    spawned: list[int] = []
    real_popen = rpc.subprocess.Popen

    def popen_recording_pid(*args: object, **kwargs: object) -> object:
        proc = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(proc.pid)
        return proc

    def cannot_start(self: object) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(rpc.subprocess, "Popen", popen_recording_pid)
    monkeypatch.setattr(rpc.threading.Thread, "start", cannot_start)
    worker = WorkerProcess(
        [PLAIN_PYTHON, str(ECHO_WORKER), "ok"],
        tmp_path / "worker.log",
        use_fd_channel=FD_CHANNEL_SUPPORTED,
        kill_hook=hooked.append,
    )
    with pytest.raises(WorkerLost):
        worker.start(timeout_s=5.0)
    assert spawned and not pid_running(spawned[0]), "spawned child was left orphaned"
    assert not worker.alive
    assert len(hooked) == 1


def _open_fd_count() -> int | None:
    """Open fds of this process, or ``None`` where there is no ``/proc`` to count them (Windows, macOS)."""
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc fd accounting is Linux-only")
def test_start_raises_worker_lost_and_does_not_leak_fds_when_spawn_fails(tmp_path: Path) -> None:
    bogus = tmp_path / "not_executable"
    bogus.write_text("#!/bin/sh\necho hi\n")
    bogus.chmod(0o644)  # deliberately not executable: exec fails with PermissionError
    worker = WorkerProcess([str(bogus)], tmp_path / "worker.log")
    before = _open_fd_count()
    assert before is not None
    with pytest.raises(WorkerLost) as info:
        worker.start(timeout_s=5.0)
    assert "spawn failed" in str(info.value)
    assert not worker.alive
    after = _open_fd_count()
    assert after == before, f"leaked {after - before if after is not None else '?'} fd(s)"


def test_start_maps_a_missing_executable_to_worker_lost_on_the_platform_channel(tmp_path: Path) -> None:
    """The spawn-failure mapping on the transport the host OS really uses (fd channel on POSIX, stdout channel
    on Windows): a command that cannot be exec'd is ``WorkerLost("spawn failed...")``, never a raw ``OSError``."""
    worker = WorkerProcess(
        [str(tmp_path / "no-such-worker")], tmp_path / "worker.log", use_fd_channel=FD_CHANNEL_SUPPORTED
    )
    with pytest.raises(WorkerLost) as info:
        worker.start(timeout_s=5.0)
    assert "spawn failed" in str(info.value)
    assert not worker.alive


@requires_fd_channel
def test_start_maps_pipe_exhaustion_to_worker_lost_and_closes_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def exhausted() -> tuple[int, int]:
        raise OSError(24, "Too many open files")

    monkeypatch.setattr(rpc.os, "pipe", exhausted)
    worker = WorkerProcess([PLAIN_PYTHON, str(ECHO_WORKER), "ok"], tmp_path / "worker.log")
    before = _open_fd_count()
    with pytest.raises(WorkerLost) as info:
        worker.start(timeout_s=5.0)
    assert "Too many open files" in str(info.value)
    assert not worker.alive
    assert _open_fd_count() == before, "log file descriptor leaked"


def test_start_maps_unwritable_log_directory_to_worker_lost(tmp_path: Path) -> None:
    log_path = tmp_path / "file_not_dir" / "worker.log"
    (tmp_path / "file_not_dir").write_text("blocks mkdir")
    worker = WorkerProcess([PLAIN_PYTHON, str(ECHO_WORKER), "ok"], log_path)
    with pytest.raises(WorkerLost):
        worker.start(timeout_s=5.0)


def test_start_kills_the_spawned_child_when_the_reader_thread_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ECHO_WORKER_DELAY_S", "2")
    spawned: list[int] = []
    real_popen = rpc.subprocess.Popen

    def popen_recording_pid(*args: object, **kwargs: object) -> object:
        proc = real_popen(*args, **kwargs)  # type: ignore[arg-type]
        spawned.append(proc.pid)
        return proc

    def cannot_start(self: object) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(rpc.subprocess, "Popen", popen_recording_pid)
    monkeypatch.setattr(rpc.threading.Thread, "start", cannot_start)
    worker = WorkerProcess(
        [PLAIN_PYTHON, str(ECHO_WORKER), "ok"], tmp_path / "worker.log", use_fd_channel=FD_CHANNEL_SUPPORTED
    )
    before = _open_fd_count()
    with pytest.raises(WorkerLost) as info:
        worker.start(timeout_s=5.0)
    assert "can't start new thread" in str(info.value)
    assert spawned and not pid_running(spawned[0]), "spawned child was left orphaned"
    assert not worker.alive
    if before is not None:  # fd accounting needs /proc (Linux); the orphan check above runs everywhere
        assert _open_fd_count() == before, "fd leaked after a failed thread start"


@requires_fd_channel
def test_start_kills_the_spawned_child_when_fdopen_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_WORKER_DELAY_S", "2")

    def broken_fdopen(fd: int, *args: object, **kwargs: object) -> object:
        raise OSError(9, "Bad file descriptor")

    monkeypatch.setattr(rpc.os, "fdopen", broken_fdopen)
    worker = WorkerProcess([PLAIN_PYTHON, str(ECHO_WORKER), "ok"], tmp_path / "worker.log")
    before = _open_fd_count()
    with pytest.raises(WorkerLost):
        worker.start(timeout_s=5.0)
    assert not worker.alive
    assert _open_fd_count() == before, "fd leaked after a failed fdopen"


def test_notification_pushed_before_the_first_call_still_reaches_a_listener_registered_before_start(
    make_worker: WorkerFactory,
) -> None:
    """A notification frame (no ``id``) that lands before any ``call()`` is issued must not be treated as
    the (nonexistent) reply nothing is waiting for -- it goes straight to the listener."""
    worker = make_worker("notify")
    received: list[tuple[str, dict[str, object]]] = []
    worker.set_notification_listener(lambda method, params: received.append((method, params)))
    worker.start(timeout_s=5.0)
    _wait_until(lambda: len(received) >= 1)
    assert received == [("tick", {"n": 1})]


def test_notification_interleaved_with_a_reply_does_not_disturb_the_response_match(
    make_worker: WorkerFactory,
) -> None:
    """echo_worker's ``notify`` mode pushes a second notification between the first request and its
    reply -- ``call()`` must still return the matching result, and the notification must still surface,
    without either disturbing the other (the reader routes them to two different destinations)."""
    worker = make_worker("notify")
    received: list[tuple[str, dict[str, object]]] = []
    worker.set_notification_listener(lambda method, params: received.append((method, params)))
    worker.start(timeout_s=5.0)
    result = worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert result["echo"] == {"operation_id": "base"}
    _wait_until(lambda: len(received) >= 2)
    assert received == [("tick", {"n": 1}), ("tick", {"n": 2})]
    # a second call proves the response queue is still healthy after the interleaved notification
    second = worker.call("canary", {"operation_id": "c"}, timeout_s=5.0)
    assert second["echo"] == {"operation_id": "c"}


def test_notification_listener_can_be_registered_after_start(make_worker: WorkerFactory) -> None:
    """``set_notification_listener`` is not frozen into the reader thread's arguments at ``start()`` time
    (a mutable one-item box is): registering late still catches later notifications."""
    worker = make_worker("notify")
    worker.start(timeout_s=5.0)
    received: list[tuple[str, dict[str, object]]] = []
    worker.set_notification_listener(lambda method, params: received.append((method, params)))
    worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    _wait_until(lambda: len(received) >= 1)
    assert ("tick", {"n": 2}) in received


def test_a_broken_notification_listener_does_not_kill_the_reader_thread(make_worker: WorkerFactory) -> None:
    """The reader thread also delivers RPC replies -- an exception from a careless listener must be
    swallowed, not propagate and end the thread that ``call()`` depends on."""
    worker = make_worker("notify")

    def _boom(method: str, params: dict[str, object]) -> None:
        raise RuntimeError("listener bug")

    worker.set_notification_listener(_boom)
    worker.start(timeout_s=5.0)
    result = worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert result["echo"] == {"operation_id": "base"}
    assert worker.alive


def test_clearing_the_notification_listener_drops_further_notifications(make_worker: WorkerFactory) -> None:
    worker = make_worker("notify")
    received: list[tuple[str, dict[str, object]]] = []
    worker.set_notification_listener(lambda method, params: received.append((method, params)))
    worker.start(timeout_s=5.0)
    _wait_until(lambda: len(received) >= 1)
    worker.set_notification_listener(None)
    worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    time.sleep(0.2)  # give the (now unregistered) second notification a chance to have arrived
    assert received == [("tick", {"n": 1})]


def _wait_until(predicate: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "condition never became true"


def test_dropping_the_worker_without_stop_kills_the_child_via_finalizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ECHO_WORKER_DELAY_S", "2")
    worker = WorkerProcess(
        [PLAIN_PYTHON, str(ECHO_WORKER), "ok"], tmp_path / "worker.log", use_fd_channel=FD_CHANNEL_SUPPORTED
    )
    worker.start(timeout_s=5.0)
    pid = worker.pid
    assert pid is not None
    del worker
    gc.collect()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and pid_running(pid):
        time.sleep(0.05)
    assert not pid_running(pid), "finalizer did not reap the child worker process"


def test_windows_creationflags_are_empty_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")  # the POSIX branch, whichever OS runs the suite
    assert rpc._windows_creationflags() == {}


def test_windows_creationflags_avoid_a_console_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows-only regression: the worker this module spawns (``use_fd_channel=False``
    -- the stdout-based Windows-interop path per the module docstring) is a console-subsystem process; with
    no creation flags it pops a visible console. Exercised on POSIX CI by monkeypatching ``sys.platform``
    and injecting the two Windows-only ``subprocess`` constants, absent on a POSIX Python build."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert rpc._windows_creationflags() == {"creationflags": 0x00000200 | 0x08000000}


# --- nothing waits for a worker that is gone ---------------------------------------------------------------------


def _kill_worker(worker: WorkerProcess) -> None:
    assert worker.pid is not None
    os.kill(worker.pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def test_a_worker_that_dies_while_a_process_it_started_holds_the_pipe_ends_the_call(
    make_worker: WorkerFactory,
) -> None:
    """The pipe never reports the end while something the worker started has it open: the wait looks at the process
    itself, so the call is over within a second or two, not when the timeout or the pipe says so."""
    worker = make_worker("orphan_pipe", use_fd_channel=False)
    worker.start(timeout_s=5.0)
    threading.Timer(0.4, _kill_worker, args=(worker,)).start()
    started = time.monotonic()
    with pytest.raises(WorkerLost) as info:
        worker.call("extrude", {"operation_id": "base"}, timeout_s=30.0)
    assert time.monotonic() - started < 3.0
    assert "exited" in str(info.value)
    started = time.monotonic()
    worker.stop(grace_s=0.5)  # closing the pipe the reader is blocked on does not wait for it
    assert time.monotonic() - started < 4.0


def test_a_liveness_wait_ends_when_the_worker_is_gone_even_if_the_pipe_stays_open(make_worker: WorkerFactory) -> None:
    worker = make_worker("orphan_pipe", use_fd_channel=False)
    worker.start(timeout_s=5.0)
    threading.Timer(0.4, _kill_worker, args=(worker,)).start()
    started = time.monotonic()
    with pytest.raises(WorkerLost):
        worker.call("extrude", {}, 30.0, liveness=rpc.Liveness(stall_s=60.0, max_s=600.0, heartbeat_s=0.3))
    assert time.monotonic() - started < 3.0


def test_a_call_behind_a_running_one_gives_up_with_what_is_running(make_worker: WorkerFactory) -> None:
    worker = make_worker("delay")  # the first request takes 2 s
    worker.start(timeout_s=5.0)
    first: list[object] = []
    thread = threading.Thread(target=lambda: first.append(worker.call("slow_one", {}, timeout_s=10.0)))
    thread.start()
    _wait_until(lambda: worker._running is not None)
    started = time.monotonic()
    with pytest.raises(rpc.WorkerBusy) as info:
        worker.call("canary", {}, timeout_s=5.0, busy_wait_s=0.3)
    assert time.monotonic() - started < 1.5
    assert info.value.running == "slow_one" and "slow_one" in str(info.value)
    thread.join(timeout=10.0)
    assert first, "the running request was not disturbed"
    assert worker.alive
    assert worker.call("canary", {}, timeout_s=5.0) == {"echo": {}}  # and the next one goes through
