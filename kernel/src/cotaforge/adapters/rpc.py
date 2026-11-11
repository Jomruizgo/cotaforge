"""Generic worker-process RPC transport, CAD-agnostic (lifted out of ``adapters/freecad/rpc.py`` so
other adapters can reuse it).

Requests go to the child process' stdin. Responses come back on a dedicated pipe fd inherited by the
child (``COTAFORGE_RPC_FD``) when ``use_fd_channel=True``, so that anything the child prints to stdout
never corrupts the channel; stdout and stderr are then redirected to a log file. When the fd channel is
not used (``use_fd_channel=False`` — the Windows-interop case, where a pipe fd cannot be inherited across
the WSL boundary) the child writes ``@cadh `` + JSON to stdout and the parent filters on that prefix;
that path never touches ``os.pipe``/``pass_fds``.

This module knows nothing about any particular CAD application: the command line, working directory and
extra environment are supplied by the caller. See ``adapters/freecad/rpc.py`` for the FreeCAD-specific
spawn command and re-exports that keep existing import paths working.

Unsolicited notifications (the CAD-selection/manual-edit seam): a worker may push a frame
with no ``id`` at any time, not just in reply to a request -- e.g. the FreeCAD worker's ``Gui.Selection``
observer or document-change observer. ``_reader_loop`` tells such a frame apart from a response (has an
int ``id``) and from garbage (parses as JSON but is neither) and routes it straight to the registered
:data:`NotificationListener` instead of ``events`` -- it never touches the response queue ``call()``/
``_wait_message`` consume, so a notification arriving between a request and its reply never disturbs that
match.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from cotaforge.adapters.procstats import Vitals, sample

ENV_RPC_FD = "COTAFORGE_RPC_FD"


def _windows_creationflags() -> dict[str, object]:
    """Windows-only ``subprocess.Popen`` kwargs to spawn the worker without a visible console window; an
    empty dict on POSIX. The worker this module spawns is a console-subsystem process
    (``freecadcmd``/``python.exe``); with no creation flags at all it either attaches to this process'
    console or, more commonly for the desktop build (no console anywhere in its tree), pops a brand-new
    visible one. ``CREATE_NO_WINDOW`` suppresses that; ``CREATE_NEW_PROCESS_GROUP`` is layered in too
    (harmless together, and it gives a group a later hard-kill can target as a whole).

    ``getattr`` guards both Windows-only constants, neither of which exists on a POSIX Python build --
    exercised on POSIX CI by monkeypatching ``sys.platform`` and injecting them
    (``tests/unit/adapters/test_rpc.py``)."""
    if sys.platform != "win32":
        return {}
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flags} if flags else {}


STDOUT_PREFIX = "@cadh "
HELLO_ID = 0

NotificationListener = Callable[[str, dict[str, Any]], None]

__all__ = [
    "ENV_RPC_FD",
    "HELLO_ID",
    "MIN_CPU_ADVANCE_S",
    "STDOUT_PREFIX",
    "Heartbeat",
    "Liveness",
    "LivenessWatch",
    "NotificationListener",
    "RpcFailure",
    "WorkerBusy",
    "WorkerLost",
    "WorkerProcess",
    "WorkerTimeout",
]


class RpcFailure(Exception):  # noqa: N818 - name fixed by the wire protocol
    """The worker answered ``ok: false``; ``error`` is the ErrorInfo-shaped dict it sent."""

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(str(error.get("message", error)))
        self.error = error


class WorkerLost(Exception):  # noqa: N818 - name fixed by the wire protocol
    """The worker process died, the channel closed, or the framing was broken."""

    def __init__(self, reason: str, **details: str | int | float | bool | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


class WorkerBlocked(WorkerLost):
    """The start probe found the worker stuck on something that will not clear by waiting (``reason`` says what); the
    worker was ended by its PID."""


class WorkerBusy(Exception):  # noqa: N818 - name fixed by the wire protocol
    """The worker is still answering an earlier request and this one waited ``waited_s`` for it in vain: nothing was
    sent and the worker is untouched. ``running`` names the earlier request and ``running_s`` how long it has been
    going."""

    def __init__(self, method: str, waited_s: float, running: str | None, running_s: float | None) -> None:
        what = "an earlier request"
        if running is not None and running_s is not None:
            what = f"{running} (running for {running_s:.0f} s)"
        super().__init__(f"{method}: the CAD is still busy with {what}; waited {waited_s:.0f} s")
        self.method = method
        self.waited_s = waited_s
        self.running = running
        self.running_s = running_s


class WorkerTimeout(Exception):  # noqa: N818 - name fixed by the wire protocol
    """No reply within ``timeout_s``; the worker was killed. A request waited for under a ``Liveness`` rule says which
    rule fired in ``rule`` (``stall``: the process stopped using CPU; ``cap``: it ran past the overall limit) and
    carries that rule's ``limit_s`` and the seconds the request had run (``elapsed_s``)."""

    def __init__(
        self,
        method: str,
        timeout_s: float,
        *,
        rule: str | None = None,
        limit_s: float | None = None,
        elapsed_s: float | None = None,
    ) -> None:
        if rule == "stall":
            text = f"{method}: the CAD process used no CPU for {limit_s:g} s (stall rule) after {elapsed_s:.0f} s"
        elif rule == "cap":
            text = f"{method}: still running after {elapsed_s:.0f} s, past the limit of {limit_s:g} s (cap rule)"
        else:
            text = f"{method}: no reply within {timeout_s} s"
        super().__init__(text)
        self.method = method
        self.timeout_s = timeout_s
        self.rule = rule
        self.limit_s = limit_s
        self.elapsed_s = elapsed_s


MIN_CPU_ADVANCE_S = 0.05
"""CPU seconds between two samples that count as the process making progress."""

PROBE_SLICE_S = 1.0
"""How often a start probe looks at the worker."""

WAIT_SLICE_S = 0.5
"""A wait for the worker looks at the process this often: a worker that is gone ends the wait within about a second,
whether or not its pipe ever reports the end (a process the worker started can hold the pipe open after it dies)."""
EXIT_GRACE_S = 0.5
"""How long after the process was seen gone a reply, or the end of the channel, still on its way is waited for."""
BUSY_WAIT_S = 30.0
"""How long a request waits for the worker to finish an earlier one before it gives up with ``WorkerBusy``."""
CLOSE_WAIT_S = 0.5
"""How long closing the pipe to a worker is waited for (``_close_channel``)."""


@dataclass(frozen=True)
class Heartbeat:
    """One periodic look at a request that is being waited for under a ``Liveness`` rule."""

    elapsed_s: float
    vitals: Vitals


@dataclass(frozen=True)
class Liveness:
    """How to wait for a request that may legitimately take very long: keep waiting while the process is alive and its
    CPU time keeps advancing; give up when it makes no CPU progress for ``stall_s`` (``stall`` rule) or when the request
    has run for ``max_s`` in all (``cap`` rule). Every ``heartbeat_s`` the process is looked at and ``on_heartbeat``
    told. Where the CPU time cannot be read the ``stall`` rule cannot be applied and only the cap protects."""

    stall_s: float
    max_s: float
    heartbeat_s: float = 5.0
    on_heartbeat: Callable[[Heartbeat], None] | None = None


class LivenessWatch:
    """The ``Liveness`` rule as a pure function of time and CPU samples (no clock, no process: testable). ``check`` is
    called with each sample; it returns ``None`` while the request may go on, else ``(rule, limit_s)``."""

    def __init__(self, liveness: Liveness, now: float) -> None:
        self._liveness = liveness
        self._started = now
        self._last_cpu: float | None = None
        self._last_progress = now

    def check(self, now: float, cpu_s: float | None) -> tuple[str, float] | None:
        if now - self._started >= self._liveness.max_s:
            return "cap", self._liveness.max_s
        if cpu_s is None:
            return None
        if self._last_cpu is None or cpu_s >= self._last_cpu + MIN_CPU_ADVANCE_S:
            self._last_cpu = cpu_s
            self._last_progress = now
            return None
        if now - self._last_progress >= self._liveness.stall_s:
            return "stall", self._liveness.stall_s
        return None


_Event = tuple[str, Any]  # ("message", dict) | ("garbage", str) | ("eof", None)


def _reap(proc: subprocess.Popen[bytes], timeout_s: float = 10.0) -> None:
    """Wait for a killed process to be gone, but not for ever (a process the system cannot end yet is left to it)."""
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        pass


def _close_channel(channel: IO[bytes], reader: threading.Thread | None) -> None:
    """Close the reply channel unless the reader thread is still blocked reading it. ``close()`` takes the buffer's
    lock, which a reader blocked in a read holds: when the worker is dead but a process it started still holds the pipe
    open, that read never ends and a plain ``close()`` would never return. The reader closes the channel itself when
    its read ends (``_reader_loop``)."""
    if channel.closed:
        return
    if reader is not None and reader is not threading.current_thread() and reader.is_alive():
        reader.join(timeout=CLOSE_WAIT_S)
        if reader.is_alive():
            return
    try:
        channel.close()
    except (OSError, ValueError):
        pass


def _kill_child(
    proc: subprocess.Popen[bytes],
    log: IO[bytes] | None,
    channel: IO[bytes] | None,
    kill_hook: Callable[[subprocess.Popen[bytes]], None] | None = None,
    reader: threading.Thread | None = None,
) -> None:
    """``weakref.finalize`` callback: reap the child and close its handles if ``stop()`` was never
    called. Deliberately a plain function (not a bound method) so it holds no reference back to the
    ``WorkerProcess`` and does not keep it alive."""
    if proc.poll() is None:
        if kill_hook is not None:
            try:
                kill_hook(proc)
            except Exception:  # noqa: BLE001 - a broken hook must not block the default kill
                pass
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass
    if channel is not None:
        _close_channel(channel, reader)
    if log is not None and not log.closed:
        try:
            log.close()
        except OSError:
            pass
    if proc.stdin is not None and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except OSError:
            pass


def _reader_loop(
    channel: IO[bytes],
    use_fd_channel: bool,
    events: queue.Queue[_Event],
    log: IO[bytes] | None,
    notify_box: list[NotificationListener | None],
) -> None:
    """Reader thread body. Deliberately a plain function bound to explicit arguments (not a
    ``WorkerProcess`` method): a bound method as the thread target would keep the ``WorkerProcess``
    alive via the running thread for as long as the channel stays open, defeating ``weakref.finalize``
    (and any GC-based cleanup) while the worker is connected.

    ``notify_box`` is a one-element list, not the listener itself: ``WorkerProcess.set_notification_listener``
    may be called after ``start()`` (the FreeCAD adapter wires it once the worker is up), and this thread
    was already handed its arguments by then -- a plain callable argument would freeze at whatever was set
    (or unset) at spawn time, while a mutable box read fresh on every line picks up a listener registered
    later, or cleared, without restarting the thread.
    """
    try:
        for raw in channel:
            text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not use_fd_channel:
                if not text.startswith(STDOUT_PREFIX):
                    _write_log(log, text)
                    continue
                text = text[len(STDOUT_PREFIX) :]
            if not text.strip():
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                events.put(("garbage", text))
                continue
            if isinstance(message, dict) and isinstance(message.get("id"), int):
                events.put(("message", message))
                continue
            if isinstance(message, dict) and "id" not in message and isinstance(message.get("method"), str):
                notify = notify_box[0]
                if notify is not None:
                    try:
                        notify(message["method"], dict(message.get("params") or {}))
                    except Exception:  # noqa: BLE001 - a broken listener must not kill the reader thread
                        pass
                continue
            events.put(("garbage", text))
    except (OSError, ValueError):
        pass
    finally:
        events.put(("eof", None))
        try:
            channel.close()  # from here, never from a thread that would wait for this one's read to end
        except (OSError, ValueError):
            pass


def _write_log(log: IO[bytes] | None, text: str) -> None:
    if log is not None and not log.closed:
        try:
            log.write((text + "\n").encode("utf-8"))
            log.flush()
        except (OSError, ValueError):
            pass


class WorkerProcess:
    """Spawn a child process and talk newline-delimited JSON to it (one request per line on stdin, one
    reply per line on the response channel). CAD-agnostic: ``command`` is the full argv to spawn.

    ``kill_hook``, when given, is called with the live ``subprocess.Popen`` right before the default
    ``proc.kill()`` whenever the worker is killed (timeout, lost session, or the drop-without-stop
    finalizer) — the hook point for a platform-specific extra kill such as Windows'
    ``taskkill.exe /PID <pid> /T /F`` for a worker reached over WSL interop. A hook that raises is
    swallowed; the default kill still runs.
    """

    def __init__(
        self,
        command: list[str],
        log_path: Path,
        *,
        use_fd_channel: bool = True,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        kill_hook: Callable[[subprocess.Popen[bytes]], None] | None = None,
        vitals: Callable[[int | None], Vitals] = sample,
        busy_wait_s: float = BUSY_WAIT_S,
    ) -> None:
        self._command = list(command)
        self._busy_wait_s = busy_wait_s
        self._gone_since: float | None = None
        """When the wait for the current request first saw the process gone (see ``_next_event``)."""
        self._running: tuple[str, float] | None = None
        """The request the worker is answering now (method, monotonic start), for a request that has to wait for it."""
        self._vitals = vitals
        self._log_path = Path(log_path)
        self._use_fd_channel = use_fd_channel
        self._env = dict(env) if env is not None else None
        self._cwd = Path(cwd) if cwd is not None else None
        self._kill_hook = kill_hook
        self._proc: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None
        self._channel: IO[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._events: queue.Queue[_Event] = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = HELLO_ID + 1
        self._lost: str | None = None
        self._stopped = False
        self._finalizer: weakref.finalize | None = None
        self._notify_box: list[NotificationListener | None] = [None]

    # --- properties -------------------------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    @property
    def log_path(self) -> Path:
        return self._log_path

    # --- notifications ------------------------------------------------------------------------------

    def set_notification_listener(self, listener: NotificationListener | None) -> None:
        """Register (or clear, with ``None``) the callback the reader thread calls for every unsolicited
        notification frame -- a dict with a ``method`` and no ``id`` (e.g. FreeCAD's ``Gui.Selection``/
        document-change observers, once the FreeCAD worker pushes them). Runs on the reader thread: the
        listener must not block, and an exception it raises is swallowed there (never kills the reader).
        Safe to call before or after ``start()``."""
        self._notify_box[0] = listener

    # --- lifecycle --------------------------------------------------------------------------------

    def start(self, timeout_s: float, *, probe: Callable[[int | None], str | None] | None = None) -> dict[str, Any]:
        """Spawn the worker and wait for its unsolicited hello (``id`` 0); returns the hello result. ``probe``, when
        given, is called with the worker PID every ``PROBE_SLICE_S`` while waiting; a returned text means the worker is
        blocked for good: it is ended and ``WorkerBlocked`` raised with that text instead of waiting out the timeout."""
        if self._proc is not None:
            msg = "worker already started"
            raise RuntimeError(msg)
        if self._stopped:  # stop() came first (another thread gave up on this start): nothing is spawned
            raise WorkerLost("worker stopped before it was started")
        command = self._command
        env = dict(os.environ)
        if self._env is not None:
            env.update(self._env)
        env.pop(ENV_RPC_FD, None)  # never let a caller-supplied value leak into the child
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = open(self._log_path, "ab")  # closed in stop()
        except OSError as exc:
            self._close_files()
            raise WorkerLost(f"spawn failed: {exc}") from exc
        cwd = self._cwd if self._cwd is not None and self._cwd.is_dir() else None
        if self._use_fd_channel:
            try:
                read_fd, write_fd = os.pipe()
            except OSError as exc:  # fd exhaustion is the realistic failure when many workers run
                self._close_files()
                raise WorkerLost(f"spawn failed: {exc}") from exc
            env[ENV_RPC_FD] = str(write_fd)
            try:
                self._proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=self._log,
                    stderr=subprocess.STDOUT,
                    pass_fds=(write_fd,),
                    env=env,
                    cwd=cwd,
                    **_windows_creationflags(),
                )
            except OSError as exc:
                for fd in (read_fd, write_fd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                self._close_files()
                raise WorkerLost(f"spawn failed: {exc}") from exc
            try:
                os.close(write_fd)
                self._channel = os.fdopen(read_fd, "rb")
            except OSError as exc:
                self._abort_spawn(read_fd, write_fd)  # closing an already-closed fd is tolerated
                raise WorkerLost(f"spawn failed: {exc}") from exc
        else:
            try:
                self._proc = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=self._log,
                    env=env,
                    cwd=cwd,
                    **_windows_creationflags(),
                )
            except OSError as exc:
                self._close_files()
                raise WorkerLost(f"spawn failed: {exc}") from exc
            if self._proc.stdout is None:  # defensive: Popen was given stdout=PIPE
                self._abort_spawn()
                raise WorkerLost("spawn failed: worker stdout pipe unavailable")
            self._channel = self._proc.stdout
        try:
            reader = threading.Thread(
                target=_reader_loop,
                args=(self._channel, self._use_fd_channel, self._events, self._log, self._notify_box),
                name=f"rpc-worker-{self._proc.pid}",
                daemon=True,
            )
            reader.start()
            # Published only once started: a stop() from another thread joins it, and a thread that was created but
            # not started yet cannot be joined.
            self._reader = reader
        except (OSError, RuntimeError) as exc:  # e.g. "can't start new thread" under resource exhaustion
            self._reader = None
            self._abort_spawn()
            raise WorkerLost(f"spawn failed: {exc}") from exc
        self._finalizer = weakref.finalize(
            self, _kill_child, self._proc, self._log, self._channel, self._kill_hook, self._reader
        )
        if self._stopped:
            # stop() ran while the child was being spawned and found no process to stop: this thread owns it, so it
            # ends it here (``_proc`` is set before this check and ``_stopped`` before stop() reads ``_proc``, so one
            # of the two always sees the other)
            self._kill()
            raise WorkerLost("worker stopped while it was being started")
        try:
            if probe is None:
                message = self._wait_message("hello", timeout_s)
            else:
                message = self._wait_hello_probed(timeout_s, probe)
        except (WorkerLost, WorkerTimeout):
            self._kill()
            raise
        if message.get("id") != HELLO_ID or message.get("ok") is not True or "result" not in message:
            self._lose("unexpected first message from the worker")
            raise WorkerLost("unexpected first message from the worker", line=json.dumps(message))
        return dict(message["result"])

    def call(
        self,
        method: str,
        params: dict[str, Any],
        timeout_s: float,
        *,
        liveness: Liveness | None = None,
        busy_wait_s: float | None = None,
    ) -> dict[str, Any]:
        """Send one request and wait for its reply; raises RpcFailure | WorkerLost | WorkerTimeout | WorkerBusy. With
        ``liveness`` the wait follows that rule instead of the fixed ``timeout_s`` (which is then ignored). One request
        is on the wire at a time: a second waits ``busy_wait_s`` (default ``BUSY_WAIT_S``) for the first to be answered
        and then gives up with ``WorkerBusy`` instead of waiting behind it for as long as it takes."""
        waited = self._busy_wait_s if busy_wait_s is None else busy_wait_s
        if not self._lock.acquire(timeout=waited):
            running = self._running
            elapsed = time.monotonic() - running[1] if running is not None else None
            raise WorkerBusy(method, waited, running[0] if running is not None else None, elapsed)
        try:
            self._running = (method, time.monotonic())
            return self._call_locked(method, params, timeout_s, liveness)
        finally:
            self._running = None
            self._lock.release()

    def _call_locked(
        self, method: str, params: dict[str, Any], timeout_s: float, liveness: Liveness | None
    ) -> dict[str, Any]:
        self._require_alive()
        self._gone_since = None
        request_id = self._next_id
        self._next_id += 1
        line = json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        try:
            proc.stdin.write(line.encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            reason = f"worker stdin closed while sending {method}"
            self._lose(reason)
            raise WorkerLost(reason, method=method, exit_code=proc.poll()) from exc
        message = self._wait_message(method, timeout_s, liveness)
        if message.get("id") != request_id:
            reason = f"reply id {message.get('id')!r} does not match request id {request_id}"
            self._lose(reason)
            raise WorkerLost(reason, method=method)
        if message.get("ok") is True:
            return dict(message.get("result") or {})
        error = message.get("error")
        if not isinstance(error, dict):
            reason = f"reply to {method} carries neither result nor error"
            self._lose(reason)
            raise WorkerLost(reason, method=method, line=json.dumps(message))
        raise RpcFailure(error)

    def stop(self, grace_s: float = 5.0) -> None:
        """Close stdin (the worker exits on EOF), wait ``grace_s``, kill if still alive; idempotent. Final: a worker
        stopped before (or while) it starts is never spawned (or is killed by ``start`` itself)."""
        self._stopped = True
        proc = self._proc
        if proc is None:  # not spawned (yet): start() owns its open files and ends any child it spawns
            return
        if proc.poll() is None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            try:
                proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                if self._kill_hook is not None:
                    try:
                        self._kill_hook(proc)
                    except Exception:  # noqa: BLE001 - a broken hook must not block the default kill
                        pass
                proc.kill()
                _reap(proc)
        if self._lost is None and proc is not None:
            self._lost = "stopped"
        self._close_files()

    # --- internals --------------------------------------------------------------------------------

    def _require_alive(self) -> None:
        if self._proc is None:
            raise WorkerLost("worker not started")
        if self._lost is not None:
            raise WorkerLost(f"worker lost: {self._lost}")
        code = self._proc.poll()
        if code is not None:
            self._lost = f"worker exited with code {code}"
            raise WorkerLost(self._lost, exit_code=code)

    def _wait_message(self, method: str, timeout_s: float, liveness: Liveness | None = None) -> dict[str, Any]:
        if liveness is None:
            event = self._next_event(timeout_s)
            if event is None:
                self._kill()
                self._lost = f"timeout waiting for {method}"
                raise WorkerTimeout(method, timeout_s)
            kind, payload = event
        else:
            kind, payload = self._wait_alive(method, liveness)
        return self._message_of(kind, payload, method)

    def _wait_hello_probed(self, timeout_s: float, probe: Callable[[int | None], str | None]) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        for _ in range(int(timeout_s / PROBE_SLICE_S) + 3):  # bounded: the deadline decides, the counter only guards
            remaining = deadline - time.monotonic()
            event = self._next_event(max(0.0, min(PROBE_SLICE_S, remaining)))
            if event is not None:
                return self._message_of(event[0], event[1], "hello")
            try:
                reason = probe(self.pid)
            except Exception:  # noqa: BLE001 - a broken probe must not decide the fate of the start
                reason = None
            if reason:
                self._kill()
                self._lost = reason
                raise WorkerBlocked(reason)
            if remaining <= 0:
                break
        self._kill()
        self._lost = "timeout waiting for hello"
        raise WorkerTimeout("hello", timeout_s)

    def _next_event(self, timeout_s: float) -> _Event | None:
        """The next reply, line or end of the channel; ``None`` when ``timeout_s`` ran out. The process is looked at
        every ``WAIT_SLICE_S``: when it is gone and nothing more arrives within ``EXIT_GRACE_S`` the channel is reported
        ended (``eof``) without waiting for the pipe to say so, which it does not when something the worker started
        still holds it open."""
        deadline = time.monotonic() + timeout_s
        while True:
            now = time.monotonic()
            try:
                return self._events.get(timeout=max(0.0, min(WAIT_SLICE_S, deadline - now)))
            except queue.Empty:
                pass
            now = time.monotonic()
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                if self._gone_since is None:
                    self._gone_since = now
                elif now - self._gone_since >= EXIT_GRACE_S:
                    return ("eof", None)
            if now >= deadline:
                return None

    def _wait_alive(self, method: str, liveness: Liveness) -> _Event:
        """Wait for the reply under a ``Liveness`` rule: look at the process every ``heartbeat_s``."""
        started = time.monotonic()
        watch = LivenessWatch(liveness, started)
        while True:
            event = self._next_event(liveness.heartbeat_s)
            if event is not None:
                return event
            now = time.monotonic()
            vitals = self._vitals(self.pid)
            verdict = watch.check(now, vitals.cpu_s)
            if liveness.on_heartbeat is not None:
                try:
                    liveness.on_heartbeat(Heartbeat(now - started, vitals))
                except Exception:  # noqa: BLE001 - a broken listener must not decide the fate of the request
                    pass
            if verdict is not None:
                rule, limit = verdict
                self._kill()
                self._lost = f"{rule} rule fired while waiting for {method}"
                raise WorkerTimeout(method, limit, rule=rule, limit_s=limit, elapsed_s=now - started)

    def _message_of(self, kind: str, payload: Any, method: str) -> dict[str, Any]:
        if kind == "message":
            return dict(payload)
        if kind == "garbage":
            reason = f"non-JSON line on the response channel while waiting for {method}"
            self._lose(reason)
            raise WorkerLost(reason, method=method, line=payload)
        code = self._proc.poll() if self._proc is not None else None
        if code is None and self._proc is not None:
            try:
                code = self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                code = None
        reason = f"worker exited (code {code}) while waiting for {method}"
        self._lose(reason)
        raise WorkerLost(reason, method=method, exit_code=code)

    def _lose(self, reason: str) -> None:
        """Mark the session as lost and kill the worker: the channel cannot be resynchronised."""
        self._lost = reason
        self._kill()

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            if self._kill_hook is not None:
                try:
                    self._kill_hook(proc)
                except Exception:  # noqa: BLE001 - a broken hook must not block the default kill
                    pass
            proc.kill()
            _reap(proc)
        self._close_files()

    def _abort_spawn(self, *raw_fds: int) -> None:
        """Release a partially started worker: kill the child if spawned, close raw fds and files."""
        if self._proc is not None:
            _kill_child(self._proc, None, None, self._kill_hook)
            self._proc = None
        for fd in raw_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._close_files()

    def _close_files(self) -> None:
        if self._finalizer is not None:
            self._finalizer.detach()
            self._finalizer = None
        proc = self._proc
        if proc is not None and proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        reader = self._reader
        if self._channel is not None:
            _close_channel(self._channel, reader)
        if self._log is not None and not self._log.closed:
            self._log.close()
