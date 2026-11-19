"""Start a child process so that ending it can only ever reach what the harness started.

Ending processes by pid, by name or by walking a parent/child tree is unsafe: the system recycles pids, so a stale
number can name somebody else's process. Here that is made impossible instead of forbidden. A child is started inside
a container the system itself tracks, and it is ended by closing the container:

* Windows: a Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. The process is created suspended, put in the job
  and only then resumed, so nothing it starts can be outside the job. Closing the job handle (:func:`end`, the handle
  being garbage collected, or this process dying for any reason) makes Windows end exactly the job's processes.
* POSIX: a session of its own (``start_new_session=True``), so the child leads a process group that :func:`end`
  signals as a whole. This path is for Linux/macOS only. It cannot promise to end the group when this process dies (only
  Windows can), and it signals the group only while the child has not been reaped yet, because a reaped leader's id
  could belong to something else by then.

``spawn`` returns the ordinary ``subprocess.Popen``; ``end`` is what replaces ``proc.kill()`` everywhere a worker is
ended. Nothing here takes a pid from outside: every handle comes from the ``Popen`` that was just created.
"""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import weakref
from ctypes import wintypes
from typing import Any

__all__ = ["JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE", "contained", "end", "spawn"]

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
CREATE_SUSPENDED = 0x00000004
END_WAIT_S = 5.0
"""How long :func:`end` waits for a process whose job it closed to be gone, before ending the process itself."""
_ATTR = "_cotaforge_container"
"""The attribute of the ``Popen`` that holds its container (a :class:`Job` on Windows, ``True`` for a POSIX group)."""


# --- Windows: the Job Object --------------------------------------------------------------------------------------


class _BasicLimits(ctypes.Structure):
    _fields_ = (
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    )


class _IoCounters(ctypes.Structure):
    _fields_ = tuple(
        (name, ctypes.c_uint64) for name in ("Read", "Write", "Other", "ReadBytes", "WriteBytes", "OtherBytes")
    )


class _ExtendedLimits(ctypes.Structure):
    _fields_ = (
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    )


class _WinApi:
    """The few Windows calls the job needs, with exact argument types (handles are 64-bit on a 64-bit Python). Kept
    behind one object so that the unit tests can stand a fake in for it on any platform."""

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        self._k = kernel32
        self._nt = ntdll

    def create_kill_on_close_job(self) -> int:
        handle = self._k.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = self._k.SetInformationJobObject(
            handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        if not ok:
            error = ctypes.get_last_error()  # type: ignore[attr-defined]
            self._k.CloseHandle(handle)
            raise ctypes.WinError(error)  # type: ignore[attr-defined]
        return int(handle)

    def assign(self, job: int, process: int) -> None:
        if not self._k.AssignProcessToJobObject(job, process):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]

    def resume(self, process: int) -> None:
        status = self._nt.NtResumeProcess(process)
        if status < 0:
            raise OSError(f"NtResumeProcess failed with status {status & 0xFFFFFFFF:#x}")

    def terminate(self, job: int) -> None:
        self._k.TerminateJobObject(job, 1)

    def close(self, handle: int) -> None:
        self._k.CloseHandle(handle)


_api: _WinApi | None = None


def _win_api() -> _WinApi:
    global _api  # noqa: PLW0603 - one lazily built binding to the system DLLs
    if _api is None:
        _api = _WinApi()
    return _api


class Job:
    """A Job Object whose handle is closed (which ends every process in it) by :meth:`close`, when this object is
    garbage collected or when the interpreter exits; if this process dies the system closes the handle itself."""

    def __init__(self, api: Any, handle: int) -> None:
        self._api = api
        self._handle: int | None = handle
        self._finalizer = weakref.finalize(self, api.close, handle)

    @property
    def closed(self) -> bool:
        return self._handle is None

    def end(self) -> None:
        """End every process of the job, then close the handle. Idempotent."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            self._api.terminate(handle)
        finally:
            self._finalizer.detach()
            self._api.close(handle)


def _spawn_in_job(command: list[str], api: Any, popen_kwargs: dict[str, Any]) -> subprocess.Popen[bytes]:
    """Create the process suspended, put it in a new job, resume it. Whatever goes wrong after the process exists ends
    it (through its own ``Popen``) before the error is raised: no child is ever left running outside the job."""
    popen_kwargs.pop("start_new_session", None)  # a POSIX notion; the job is the container here
    flags = int(popen_kwargs.pop("creationflags", 0)) | CREATE_SUSPENDED
    handle = api.create_kill_on_close_job()
    job = Job(api, handle)
    try:
        proc = subprocess.Popen(command, creationflags=flags, **popen_kwargs)  # noqa: S603 - argv built by the caller
    except BaseException:
        job.end()
        raise
    try:
        api.assign(handle, int(proc._handle))  # type: ignore[attr-defined]  # noqa: SLF001 - Popen's own process handle
        setattr(proc, _ATTR, job)
        api.resume(int(proc._handle))  # type: ignore[attr-defined]  # noqa: SLF001
    except BaseException:
        job.end()  # the process is in the job (assign worked) or alone and suspended: end() or the next line ends it
        _kill_handle(proc)
        raise
    return proc


def _kill_handle(proc: subprocess.Popen[bytes]) -> None:
    """End the child through its own ``Popen`` (a handle, not a pid), and wait for it."""
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=10.0)
    except subprocess.TimeoutExpired:
        pass


# --- the public calls ---------------------------------------------------------------------------------------------


def spawn(command: list[str], **popen_kwargs: Any) -> subprocess.Popen[bytes]:
    """``subprocess.Popen(command, **popen_kwargs)`` with the child inside its own container (see the module text).
    ``creationflags`` given by the caller are kept; ``start_new_session`` is set on POSIX unless the caller says
    ``False`` (the child then keeps its terminal and :func:`end` only ends the child itself). Raises ``OSError`` as
    ``Popen`` does, and also when the child could not be put in its container (it is ended first)."""
    if sys.platform == "win32":
        return _spawn_in_job(command, _win_api(), dict(popen_kwargs))
    popen_kwargs.setdefault("start_new_session", True)
    proc = subprocess.Popen(command, **popen_kwargs)  # noqa: S603 - argv built by the caller
    if popen_kwargs["start_new_session"]:  # False (a child that must keep its terminal): no group of its own to end
        setattr(proc, _ATTR, True)
    return proc


def contained(proc: subprocess.Popen[bytes]) -> bool:
    """Whether ``proc`` was started by :func:`spawn` and its container is still there to end."""
    container = getattr(proc, _ATTR, None)
    if isinstance(container, Job):
        return not container.closed
    return container is True


def end(proc: subprocess.Popen[bytes]) -> None:
    """End ``proc`` and everything that was started inside its container, then let go of the container. Idempotent, and
    never raises for a process that is already gone. The child itself is always ended through its own ``Popen`` too, so
    a ``Popen`` that did not come from :func:`spawn` is simply killed."""
    container = getattr(proc, _ATTR, None)
    if isinstance(container, Job):
        try:
            container.end()
        except OSError:
            pass
        # Ending a job is not instant. Wait for the process through its own handle before anything else touches it:
        # ``Popen.kill`` on a process that is already being terminated takes the exit code it can already read for
        # the end (``returncode`` is set) while the process is still there for a moment.
        try:
            proc.wait(timeout=END_WAIT_S)
        except subprocess.TimeoutExpired:
            pass
    elif container is True and proc.returncode is None and hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the group this launcher created, while its leader is not yet reaped
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
