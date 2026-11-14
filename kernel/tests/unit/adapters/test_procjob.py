"""``procjob``: a child is started inside a container the system tracks (a Windows Job Object that ends with its last
handle, a process group on POSIX) and ended by closing it, so that ending can only reach what was started.

Three layers: the order of the Windows calls against a fake binding (runs everywhere); the real behaviour on the host
(a grandchild ends with the child, a sibling started outside survives); and, on Windows, that killing the parent
Python ends the child by itself.
"""

import gc
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters import procjob
from tests.platform_support import IS_WINDOWS, PLAIN_PYTHON, pid_running

SRC = Path(procjob.__file__).resolve().parents[2]
"""The ``src`` folder of the package under test, for a child Python that must import it."""

PARENT_OF_A_GRANDCHILD = textwrap.dedent(
    """
    import subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print(grandchild.pid, flush=True)
    time.sleep(120)
    """
)
SLEEP = "import time; time.sleep(120)"


def _wait_gone(pid: int, seconds: float = 20.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_running(pid):
            return True
        time.sleep(0.1)
    return False


# --- the order of the Windows calls, against a fake binding --------------------------------------------------------


class FakeApi:
    """Records the calls the job makes, in order. ``fail`` names the call that raises."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail = fail

    def _do(self, name: str, *args: Any) -> None:
        self.calls.append((name, *args))
        if self.fail == name:
            raise OSError(f"{name} failed")

    def create_kill_on_close_job(self) -> int:
        self._do("create")
        return 100

    def assign(self, job: int, process: int) -> None:
        self._do("assign", job, process)

    def resume(self, process: int) -> None:
        self._do("resume", process)

    def terminate(self, job: int) -> None:
        self._do("terminate", job)

    def close(self, handle: int) -> None:
        self._do("close", handle)


class FakeProc:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._handle = 555
        self.pid = 4242
        self.returncode: int | None = None
        self.calls = calls

    def kill(self) -> None:
        self.calls.append(("proc.kill",))
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeApi, list[dict[str, Any]]]:
    """``spawn`` on a pretend Windows: a fake binding and a fake ``Popen`` that records how it was called."""
    api = FakeApi()
    popens: list[dict[str, Any]] = []

    def popen(command: list[str], **kwargs: Any) -> FakeProc:
        api.calls.append(("popen", kwargs.get("creationflags")))
        popens.append({"command": command, **kwargs})
        return FakeProc(api.calls)

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(procjob, "_win_api", lambda: api)
    monkeypatch.setattr(procjob.subprocess, "Popen", popen)
    return api, popens


def test_windows_the_process_is_created_suspended_put_in_the_job_and_only_then_resumed(
    windows: tuple[FakeApi, list[dict[str, Any]]],
) -> None:
    api, popens = windows
    proc = procjob.spawn(["worker.exe", "arg"], creationflags=0x08000000, env={"A": "1"})
    assert [call[0] for call in api.calls] == ["create", "popen", "assign", "resume"]
    assert api.calls[1][1] == 0x08000000 | procjob.CREATE_SUSPENDED, "the caller's flags are kept"
    assert api.calls[2] == ("assign", 100, 555) and api.calls[3] == ("resume", 555)
    assert popens[0]["command"] == ["worker.exe", "arg"] and popens[0]["env"] == {"A": "1"}
    assert procjob.contained(proc)


def test_windows_ending_closes_the_job_and_the_process_through_its_own_handle(
    windows: tuple[FakeApi, list[dict[str, Any]]],
) -> None:
    api, _ = windows
    proc = procjob.spawn(["worker.exe"])
    api.calls.clear()
    procjob.end(proc)
    assert api.calls == [("terminate", 100), ("close", 100), ("proc.kill",)]
    assert not procjob.contained(proc)
    api.calls.clear()
    procjob.end(proc)  # idempotent: the job is not touched twice
    assert api.calls == [("proc.kill",)]


def test_windows_a_child_that_cannot_be_put_in_the_job_is_ended_and_the_error_raised(
    windows: tuple[FakeApi, list[dict[str, Any]]],
) -> None:
    api, _ = windows
    api.fail = "assign"
    with pytest.raises(OSError, match="assign failed"):
        procjob.spawn(["worker.exe"])
    names = [call[0] for call in api.calls]
    assert "resume" not in names, "a child that is not in the job never runs"
    assert "proc.kill" in names and ("terminate", 100) in api.calls and ("close", 100) in api.calls


def test_windows_a_child_that_cannot_be_resumed_is_ended_and_the_error_raised(
    windows: tuple[FakeApi, list[dict[str, Any]]],
) -> None:
    api, _ = windows
    api.fail = "resume"
    with pytest.raises(OSError, match="resume failed"):
        procjob.spawn(["worker.exe"])
    assert "proc.kill" in [call[0] for call in api.calls] and ("terminate", 100) in api.calls


def test_windows_a_failing_popen_leaves_no_job_behind(
    windows: tuple[FakeApi, list[dict[str, Any]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    api, _ = windows

    def broken(command: list[str], **kwargs: Any) -> None:
        raise FileNotFoundError("no such program")

    monkeypatch.setattr(procjob.subprocess, "Popen", broken)
    with pytest.raises(FileNotFoundError):
        procjob.spawn(["missing.exe"])
    assert api.calls == [("create",), ("terminate", 100), ("close", 100)]


def test_windows_a_job_that_is_dropped_is_closed() -> None:
    api = FakeApi()
    job = procjob.Job(api, 7)
    del job
    gc.collect()
    assert api.calls == [("close", 7)], "closing the handle is what makes Windows end the job's processes"


# --- real behaviour on this machine ---------------------------------------------------------------------------------


def _start_family() -> tuple[subprocess.Popen[bytes], int]:
    """A contained child that has started a grandchild; returns the child and the grandchild's pid."""
    proc = procjob.spawn([PLAIN_PYTHON, "-c", PARENT_OF_A_GRANDCHILD], stdout=subprocess.PIPE)
    assert proc.stdout is not None
    return proc, int(proc.stdout.readline().decode().strip())


def test_ending_the_container_ends_the_child_and_what_it_started_and_nothing_else() -> None:
    sibling = subprocess.Popen([PLAIN_PYTHON, "-c", SLEEP])  # noqa: S603 - started outside any container
    proc, grandchild = _start_family()
    try:
        assert pid_running(grandchild) and proc.poll() is None
        procjob.end(proc)
        proc.wait(timeout=20)
        assert _wait_gone(grandchild), "what the child started was left running"
        assert sibling.poll() is None and pid_running(sibling.pid), "a process outside the container was ended"
    finally:
        sibling.kill()
        sibling.wait(timeout=20)
        procjob.end(proc)
        if proc.stdout:
            proc.stdout.close()


def test_a_process_that_did_not_come_from_spawn_is_simply_ended_through_its_own_handle() -> None:
    proc = subprocess.Popen([PLAIN_PYTHON, "-c", SLEEP])  # noqa: S603
    assert not procjob.contained(proc)
    procjob.end(proc)
    assert proc.wait(timeout=20) != 0
    procjob.end(proc)  # already gone: nothing raises


def test_a_child_that_already_exited_is_ended_without_error() -> None:
    proc = procjob.spawn([PLAIN_PYTHON, "-c", "pass"])
    assert proc.wait(timeout=30) == 0
    procjob.end(proc)
    procjob.end(proc)


@pytest.mark.skipif(not IS_WINDOWS, reason="only a Windows Job Object outlives nothing: it ends with this process")
def test_windows_the_child_and_its_children_end_when_the_parent_python_is_killed(tmp_path: Path) -> None:
    """The handle to the job dies with the parent, whatever killed it: Windows then ends what the job holds."""
    parent_code = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(SRC)!r})
        import subprocess
        from cotaforge.adapters import procjob
        child = procjob.spawn([{PLAIN_PYTHON!r}, "-c", {PARENT_OF_A_GRANDCHILD!r}], stdout=subprocess.PIPE)
        grandchild = child.stdout.readline().decode().strip()
        print(child.pid, grandchild, flush=True)
        time.sleep(120)
        """
    )
    sibling = subprocess.Popen([PLAIN_PYTHON, "-c", SLEEP])  # noqa: S603
    parent = subprocess.Popen([PLAIN_PYTHON, "-c", parent_code], stdout=subprocess.PIPE)  # noqa: S603
    try:
        assert parent.stdout is not None
        child_pid, grandchild_pid = (int(x) for x in parent.stdout.readline().decode().split())
        assert pid_running(child_pid) and pid_running(grandchild_pid)
        parent.kill()  # through its own handle
        parent.wait(timeout=20)
        assert _wait_gone(child_pid), "the child outlived its parent"
        assert _wait_gone(grandchild_pid), "what the child started outlived its parent"
        assert sibling.poll() is None and pid_running(sibling.pid), "a process outside the job was ended"
    finally:
        sibling.kill()
        sibling.wait(timeout=20)
        parent.kill()
        parent.wait(timeout=20)
        if parent.stdout:
            parent.stdout.close()


@pytest.mark.skipif(not IS_WINDOWS, reason="the Windows structure layout")
def test_windows_the_limit_structure_has_the_size_the_system_expects() -> None:
    import ctypes  # noqa: PLC0415

    expected = 144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112
    assert ctypes.sizeof(procjob._ExtendedLimits) == expected  # noqa: SLF001
