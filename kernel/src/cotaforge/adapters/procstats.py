"""What the operating system says about a running process, with the standard library only: its CPU time and its memory.

Used to tell a CAD process that is busy (its CPU time advances) from one that is stuck, and to show the engineer that a
long read is alive. Linux reads ``/proc``; Windows asks ``kernel32``/``psapi`` through ``ctypes``. Anywhere else, and
whenever the answer cannot be had, the figures are ``None``: callers must treat "unknown" as "cannot tell", never as "no
progress".
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

__all__ = ["Vitals", "cpu_seconds_from_proc_stat", "filetime_to_seconds", "sample"]


@dataclass(frozen=True)
class Vitals:
    """CPU seconds (user + system, all threads) and resident memory in MB of one process; ``None`` when unknown."""

    cpu_s: float | None = None
    memory_mb: float | None = None


def cpu_seconds_from_proc_stat(text: str, ticks_per_second: float) -> float | None:
    """User + system time of the process described by one ``/proc/<pid>/stat`` line. The command name (field 2) sits in
    parentheses and may hold spaces and parentheses itself, so the fields are counted from the LAST ``)``."""
    close = text.rfind(")")
    if close < 0 or ticks_per_second <= 0:
        return None
    fields = text[close + 2 :].split()
    try:
        # after the name: state is fields[0] (field 3 of the file), utime and stime are fields 14 and 15
        return (int(fields[11]) + int(fields[12])) / ticks_per_second
    except (IndexError, ValueError):
        return None


def filetime_to_seconds(filetime: int) -> float:
    """Windows ``FILETIME`` durations count 100-nanosecond intervals."""
    return filetime / 1e7


def _sample_linux(pid: int) -> Vitals:
    try:
        # the Windows interop relay of a WSL server is not the CAD process: its CPU time says nothing about it
        if os.readlink(f"/proc/{pid}/exe") == "/init":
            return Vitals()
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as handle:
            cpu = cpu_seconds_from_proc_stat(handle.read(), float(os.sysconf("SC_CLK_TCK")))
        memory = None
        with open(f"/proc/{pid}/status", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    memory = int(line.split()[1]) / 1024.0  # kB
                    break
    except (OSError, ValueError):
        return Vitals()
    return Vitals(cpu, memory)


def _sample_windows(pid: int) -> Vitals:  # pragma: no cover - needs Windows
    import ctypes  # noqa: PLC0415 - only on Windows
    from ctypes import wintypes  # noqa: PLC0415

    process_query_limited_information = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return Vitals()
    try:
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),) * 4)
        cpu = None
        if kernel32.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
        ):

            def whole(value: wintypes.FILETIME) -> int:
                return (value.dwHighDateTime << 32) | value.dwLowDateTime

            cpu = filetime_to_seconds(whole(kernel) + whole(user))

        class Counters(ctypes.Structure):
            _fields_ = (
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            )

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        memory = None
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            memory = counters.WorkingSetSize / (1024.0 * 1024.0)
        return Vitals(cpu, memory)
    finally:
        kernel32.CloseHandle(handle)


def sample(pid: int | None) -> Vitals:
    """The vitals of process ``pid`` now; never raises."""
    if pid is None:
        return Vitals()
    try:
        if sys.platform.startswith("linux"):
            return _sample_linux(pid)
        if sys.platform == "win32":
            return _sample_windows(pid)
    except Exception:  # noqa: BLE001 - a monitor must never break the request it watches
        pass
    return Vitals()
