"""Cross-platform helpers for tests that stand in for OS-specific facilities (FreeCAD install layout,
worker response channel, process liveness). The suite runs on Linux and on native Windows; each
helper picks the same layout/transport the product itself uses on the host OS, so a test exercises the real
code path instead of a POSIX-only one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

IS_WINDOWS = sys.platform == "win32"

PLAIN_PYTHON = getattr(sys, "_base_executable", None) or sys.executable
"""An interpreter that *is* the process it spawns. On Windows a venv's ``python.exe`` is a launcher that starts the
real interpreter as a child: ``Popen.pid`` is then the launcher's, not the worker's (a hello that reports
``os.getpid()`` would disagree with it), and killing the launcher leaves the real worker running. Fake workers
are stdlib-only scripts, so they run fine on the base interpreter."""

FD_CHANNEL_SUPPORTED = not IS_WINDOWS
"""``WorkerProcess(use_fd_channel=...)`` default for tests that build a worker directly. The response pipe fd
is inherited through ``pass_fds`` (POSIX only); ``FreeCADAdapter`` itself passes ``sys.platform != "win32"``
(``adapters/freecad/adapter.py``), so on Windows the real worker talks ``@cadh `` JSON on stdout."""

FREECAD_HEADLESS_REL = "bin/FreeCADCmd.exe" if IS_WINDOWS else "usr/bin/freecadcmd"
FREECAD_GUI_REL = "bin/FreeCAD.exe" if IS_WINDOWS else "usr/bin/freecad"
"""The two executables ``cotaforge.adapters.freecad.rpc.find_freecad_root`` expects under a FreeCAD root:
the Windows installer layout, or the Linux AppImage ``squashfs-root`` layout."""

def freecad_headless(root: Path) -> Path:
    """The headless executable the current OS's FreeCAD layout puts under ``root``."""
    return root / FREECAD_HEADLESS_REL


def freecad_gui(root: Path) -> Path:
    """The GUI executable the current OS's FreeCAD layout puts under ``root``."""
    return root / FREECAD_GUI_REL


def stub_freecad_root(root: Path, *, gui: bool = False) -> Path:
    """Create ``root`` with empty, executable stand-ins for FreeCAD's binaries (just enough for
    ``find_freecad_root``; tests that spawn a worker monkeypatch ``worker_command``) and return it.

    On POSIX ``find_freecad_root`` checks the exec mode bit, hence the ``chmod``; on Windows the bit is
    meaningless and ``os.access(X_OK)`` is true for any existing file."""
    names = [FREECAD_HEADLESS_REL, *([FREECAD_GUI_REL] if gui else [])]
    for name in names:
        binary = root / name
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.touch()
        binary.chmod(0o755)
    return root


def hide_installed_freecad(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make ``find_freecad_root()`` (no argument) find nothing, whatever is installed on this machine: no
    ``$COTAFORGE_FREECAD_ROOT``, a nonexistent Linux default and, for the Windows lookup, empty
    ``%ProgramFiles%`` / no ``%LOCALAPPDATA%`` -- a developer box with FreeCAD installed must not change the
    outcome of a "no FreeCAD" test."""
    from cotaforge.adapters.freecad import rpc  # noqa: PLC0415 - kept out of module import time

    monkeypatch.delenv("COTAFORGE_FREECAD_ROOT", raising=False)
    monkeypatch.setattr(rpc, "DEFAULT_FREECAD_ROOT", tmp_path / "absent")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "absent-program-files"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def pid_running(pid: int) -> bool:
    """Whether ``pid`` is still a live process. ``os.kill(pid, 0)`` is not a probe on Windows (it calls
    ``TerminateProcess``), so Windows goes through ``OpenProcess`` + ``WaitForSingleObject``."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        return _pid_running_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_running_windows(pid: int) -> bool:
    """``WaitForSingleObject`` on a process handle returns ``WAIT_OBJECT_0`` once the process exited. When the process
    cannot be opened, ``ERROR_INVALID_PARAMETER`` (87) means no such pid; any other error means it exists but is not
    ours to open, so it counts as alive."""
    import ctypes  # noqa: PLC0415 - Windows-only path
    from ctypes import wintypes  # noqa: PLC0415

    synchronize = 0x00100000
    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    wait_object_0 = 0
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(synchronize | process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() != error_invalid_parameter  # type: ignore[attr-defined]
    try:
        return kernel32.WaitForSingleObject(handle, 0) != wait_object_0
    finally:
        kernel32.CloseHandle(handle)
