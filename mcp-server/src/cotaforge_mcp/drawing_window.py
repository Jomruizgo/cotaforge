# ruff: noqa: E501
"""The window of the drawing session: opened by the design session's server, ended by it when the drawing is done.

The designer opens the drawing session (``claude --agent <plugin>:drafter``) in a terminal window of its own. To own
what it opened and nothing else, the window does not run ``claude`` directly but a small wrapper of the harness
(``python -m cotaforge_mcp.drawing_window``) that starts ``claude`` as its child and keeps a record in the design folder,
``drawings/<name>-window.json``:

* the pid of the ``claude`` it started (``claude_pid``) and of itself, a fresh ``beat`` every few seconds and the session
  id given to ``claude`` (``--session-id``), whose transcript tells whether a turn is going on;
* it ends ``claude`` when the server that opened the window (``opener_pid``) is gone, like an external adapter's guard does for
  its instance, and marks the record ``closed`` when ``claude`` ends by any means.

Nothing here ends a process by its pid, its name or its tree (the system recycles pids: a stale number can name somebody
else's process). The wrapper starts ``claude`` inside a container of its own (``cotaforge.adapters.procjob``: a Windows Job
Object that ends with its last handle, so the processes ``claude`` started go with it, even behind a ``.cmd`` shim) and
ends it by closing that container. The server that wants the window closed does not reach for any process: it marks
``closing`` in the record and the wrapper, which owns ``claude``, ends it at its next beat. Only a record whose ``beat`` is
fresh is asked (a stale one belongs to a wrapper that is gone); never a window the harness did not open. ``claude``'s MCP
server then sees the end of its input and closes the CAD it started the way it always does.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cotaforge import paths
from cotaforge.adapters import procjob

BEAT_S = 5.0
STALE_S = 25.0
"""A record whose beat is older than this belongs to a wrapper that is gone."""
STARTING_S = 90.0
"""How long a window that has not written its first beat is still taken for starting."""
IDLE_S = 30.0
"""The drawing session's transcript untouched for this long: nobody is in the middle of a turn."""
EXIT_WAIT_S = 15.0
"""How long the harness waits for the wrapper to end ``claude`` after it marked ``closing`` (the wrapper looks at its next beat)."""
FAST_FAIL_S = 20.0
"""A ``claude --resume`` that ends with an error sooner than this could not resume: the fallback takes over."""
AGENT_RE = re.compile(r"^[A-Za-z0-9][\w-]*(:[A-Za-z0-9][\w-]*)?$")


def pid_alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        import ctypes  # noqa: PLC0415 - Windows only

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return bool(kernel32.WaitForSingleObject(handle, 0) == 0x102)  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.getpgid(pid)  # only asks whether the process exists; it signals nothing
    except OSError:
        return False
    return True


def start_claude(command: list[str]) -> Any:
    """Start ``claude`` in its own container (see the module text). On POSIX it keeps the terminal it was started in (the
    harness opens no windows there), so it is a plain child that is ended through its ``Popen``."""
    return procjob.spawn(command, start_new_session=False)


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


@dataclass(frozen=True)
class Host:
    """What the window needs from the machine (all faked by the unit tests: nothing real is opened or ended by them)."""

    platform: str = field(default_factory=lambda: sys.platform)
    which: Callable[[str], str | None] = shutil.which
    popen: Callable[..., Any] = subprocess.Popen
    """Opens the window (a launcher that is not ours to end)."""
    spawn: Callable[[list[str]], Any] = start_claude
    """Starts ``claude`` inside the container the wrapper ends it through."""
    end: Callable[[Any], None] = procjob.end
    """Ends what ``spawn`` started: the container and the process, through the ``Popen`` that was returned."""
    alive: Callable[[int], bool] = pid_alive
    now: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    home: Path = field(default_factory=claude_home)
    pid: int = field(default_factory=os.getpid)
    executable: str = field(default_factory=lambda: sys.executable)


# --- the record --------------------------------------------------------------------------------------------------------


def record_path(workspace: Path, name: str) -> Path:
    return paths.resolve(paths.resolve(workspace, paths.DRAWINGS), f"{name}{paths.WINDOW_SUFFIX}")


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_record(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(temp, path)


def is_open(host: Host, record: dict[str, Any] | None) -> bool:
    """Whether the record names a window that is (or is still starting to be) there."""
    if record is None or record.get("state") == "closed":
        return False
    now = host.now()
    if record.get("state") == "starting":
        return now - float(record.get("started", 0)) < STARTING_S
    return now - float(record.get("beat", 0)) <= STALE_S


# --- opening ------------------------------------------------------------------------------------------------------------


def opening_prompt(name: str) -> str:
    """The first message of the drawing session: the part, the handoff file and the channel (no ``;`` or quotes: a
    terminal command line carries it)."""
    return (
        f"Draw the sheet of {name}. Read drawings/{name}-intent.md (planos/{name}-intencion.md in an older project) and the channel (channel list, name={name}) "
        f"and ask your questions through the channel. Write to the engineer in his own language: the one in "
        f"memory brief language."
    )


def reopening_prompt(name: str) -> str:
    """The first message of a session that picks the drawing up after its window was closed."""
    return (
        f"The window closed before you finished. Carry on with the sheet of {name}: read the channel (channel list, "
        f"name={name}) and the state of the sheet (drawing list), tell the engineer what you found, in his own "
        f"language (memory brief language), and continue."
    )


def claude_line(host: Host, agent: str, name: str, session_id: str, resume: str | None = None) -> list[str]:
    """The ``claude`` command (behind a ``.cmd`` shim it goes through ``cmd.exe``; the container takes both).
    ``resume``: the id of the conversation to continue (``--resume``) instead of a new one."""
    exe = host.which("claude") or "claude"
    if resume:
        line = [exe, "--resume", resume, "--agent", agent, reopening_prompt(name)]
    else:
        line = [exe, "--agent", agent, "--session-id", session_id, opening_prompt(name)]
    if exe.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", *line]
    return line


def typed(host: Host, folder: Path, agent: str, name: str, resume: str | None = None) -> str:
    """The command as a person types it in the design folder (the fallback; no wrapper)."""
    if resume:
        line = ["claude", "--resume", resume, "--agent", agent, reopening_prompt(name)]
    else:
        line = ["claude", "--agent", agent, opening_prompt(name)]
    if host.platform.startswith("win"):
        return f'cd /d "{folder}" && ' + subprocess.list2cmdline(line)
    import shlex  # noqa: PLC0415

    return f"cd {shlex.quote(str(folder))} && {shlex.join(line)}"


def window_line(host: Host, folder: Path, wrapper: list[str]) -> list[str]:
    """Windows Terminal when there is one, else a plain console through ``cmd /c start``; the window closes when the
    wrapper ends."""
    if host.which("wt.exe") or host.which("wt"):
        return ["wt.exe", "-d", str(folder), *wrapper]
    return ["cmd.exe", "/c", "start", "cotaforge drafter", "/D", str(folder), *wrapper]


def open_window(
    host: Host, workspace: Path, name: str, agent: str, session_id: str, resume: str | None = None
) -> list[str]:
    """Start the window (Windows only). Returns the command line that was run. Raises ``OSError`` when it cannot start;
    ``RuntimeError`` when the drawing already has a window. With ``resume`` the window continues that conversation, and if
    ``claude`` cannot resume it the wrapper starts a new session (``session_id``) that reads the channel instead."""
    path = record_path(workspace, name)
    if is_open(host, read_record(path)):
        raise RuntimeError(f"the drawing session of {name} is already open")
    claude = claude_line(host, agent, name, session_id, resume)
    record: dict[str, Any] = {
        "state": "starting",
        "started": host.now(),
        "opener_pid": host.pid,
        "session_id": resume or session_id,
    }
    if resume:
        fresh = claude_line(host, agent, name, session_id)
        record["fallback"] = {"command": [*fresh[:-1], reopening_prompt(name)], "session_id": session_id}
    wrapper = [host.executable, "-m", "cotaforge_mcp.drawing_window", "--record", str(path), "--opener", str(host.pid)]
    wrapper += ["--", *claude]
    line = window_line(host, workspace, wrapper)
    write_record(path, record)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    host.popen(
        line,
        cwd=str(workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    return line


# --- the wrapper that runs inside the window --------------------------------------------------------------------------


def _supervise(
    host: Host, record: Path, data: dict[str, Any], command: list[str], opener: int, beat_s: float
) -> tuple[int, bool, float]:
    """Run ``command`` as a child until it ends: (exit code, whether the opener went away and the child was ended for
    it, seconds it ran). The record says which pid it is and gets a beat every ``beat_s``; at each beat the child is
    ended when the harness marked ``closing`` or when the opener is gone."""
    try:
        proc = host.spawn(command)
    except OSError as exc:
        sys.stderr.write(f"cotaforge: could not start {command[0]}: {exc}\n")
        return 1, False, 0.0
    began = host.now()
    data.update(state="running", claude_pid=proc.pid, started=began, beat=began)
    write_record(record, data)
    gone = False
    code: int | None = None
    while code is None:
        try:
            code = proc.wait(timeout=beat_s)
        except subprocess.TimeoutExpired:
            if _closing(record):  # the harness asked for this window to close: only the wrapper owns claude
                data["closing"] = True
                host.end(proc)
            elif host.alive(opener):
                data["beat"] = host.now()
                data["closing"] = data.get("closing") or _closing(record)  # keep the harness's mark through our write
                write_record(record, data)
                continue
            else:
                gone = True
                host.end(proc)  # the design session is gone: so is the window it opened
            code = proc.wait(timeout=EXIT_WAIT_S)
    return code, gone, host.now() - began


def run_wrapper(record: Path, opener: int, command: list[str], host: Host | None = None, beat_s: float = BEAT_S) -> int:
    """Run ``command`` (``claude``) as a child, keep the record, and end the child if the opener goes away. A resume that
    fails at once is replaced by the new session the record names as a fallback. When ``claude`` ends without the drafter
    having said ``done`` (and not because the harness closed it or the design session ended) the channel is told, so that
    the designer hears that the window was closed early."""
    host = host or Host()
    data: dict[str, Any] = {**(read_record(record) or {}), "opener_pid": opener, "wrapper_pid": host.pid}
    data.pop("tree", None)  # a record of an older version
    fallback = data.pop("fallback", None)
    code, gone, took = _supervise(host, record, data, command, opener, beat_s)
    if code != 0 and not gone and took < FAST_FAIL_S and isinstance(fallback, dict) and not _closing(record):
        data["session_id"] = fallback["session_id"]
        code, gone, took = _supervise(host, record, data, list(fallback["command"]), opener, beat_s)
    closing = bool(data.get("closing")) or _closing(record)
    write_record(record, {**data, "state": "closed", "closed": host.now()})
    if not (gone or closing):
        _tell_the_channel(record)
    return code


def _closing(record: Path) -> bool:
    """Whether the harness is the one closing this window (``close_window`` marks the record before it ends ``claude``)."""
    return bool((read_record(record) or {}).get("closing"))


def _tell_the_channel(record: Path) -> None:
    """The window ended on its own account: unless the drawing was finished, write ``closed`` into the channel. Never
    fails (the window is ending anyway)."""
    from cotaforge_mcp import channel_tools  # noqa: PLC0415 - channel_tools imports this module

    name = record.name.removesuffix(paths.WINDOW_SUFFIX).removesuffix("-ventana.json")
    workspace = record.parent.parent
    try:
        log = channel_tools._paths(workspace, name)[0]
        if channel_tools.is_done(channel_tools.read_events(log)):
            return
        channel_tools.record_closed(workspace, name)
    except Exception as exc:  # noqa: BLE001 - a channel that cannot be written is no reason to fail the exit
        sys.stderr.write(f"cotaforge: could not tell the channel: {exc}\n")


# --- closing ------------------------------------------------------------------------------------------------------------


def transcript(host: Host, workspace: Path, session_id: str) -> Path:
    """Where Claude Code writes that session: ``<config>/projects/<folder with every non-alphanumeric as ->/<id>.jsonl``."""
    return host.home / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workspace)) / f"{session_id}.jsonl"


def idle_seconds(host: Host, workspace: Path, record: dict[str, Any]) -> float | None:
    """Seconds since the drawing session last wrote its transcript (a turn, a tool call or a message of the engineer
    does), or ``None`` when that cannot be known."""
    if not record.get("session_id"):
        return None
    try:
        return max(0.0, host.now() - transcript(host, workspace, str(record["session_id"])).stat().st_mtime)
    except OSError:
        return None


def close_window(
    host: Host, workspace: Path, name: str, *, done: bool, confirmed: bool, wait_idle_s: float = 0.0
) -> tuple[bool, str]:
    """End the drawing session's window. ``done``: the drafter said so (its sheet is saved). ``confirmed``: the engineer
    said to close, so neither the done nor the idle checks apply. Returns (closed, what to tell the engineer)."""
    path = record_path(workspace, name)
    record = read_record(path)
    if record is None or record.get("state") == "closed":
        return True, f"no drawing window is open for {name}."
    if not is_open(host, record):
        return True, f"the record of the drawing window of {name} is stale (the window is gone): nothing was ended."
    if record.get("state") == "starting" or not isinstance(record.get("claude_pid"), int):
        return False, f"the drawing window of {name} is still starting: try again in a moment."
    if not (done or confirmed):
        return False, (
            "the drafter has not said done, so its sheet may be unsaved: ask the engineer; if he says to close it, "
            "call close again with confirmed=true."
        )
    if not confirmed:
        idle = idle_seconds(host, workspace, record)
        if idle is None:
            return False, (
                "cannot tell whether the engineer is typing in that window: ask him here; if he says to close it, "
                "call close again with confirmed=true."
            )
        deadline = host.now() + wait_idle_s
        while idle < IDLE_S:
            if host.now() >= deadline:
                return False, (
                    f"not closed: the drawing session was active {idle:.0f} s ago (a turn or a message of the "
                    "engineer). Tell him and, when he says so, call close again (confirmed=true)."
                )
            host.sleep(2.0)
            idle = idle_seconds(host, workspace, record) or 0.0
    # The wrapper owns claude and its container: it sees the mark at its next beat, knows it is the harness that ends
    # claude, and ends it. Nothing is ended from here.
    write_record(path, {**record, "closing": True})
    for _ in range(int(EXIT_WAIT_S / 0.5)):
        latest = read_record(path)
        if latest is None or latest.get("state") == "closed":
            break
        host.sleep(0.5)
    else:
        return False, (
            f"the drawing window of {name} was asked to close but is still open (the wrapper answers at its next beat): "
            "try closing again in a moment."
        )
    resume = record.get("session_id")
    later = (
        f" To pick that session up later: `claude --resume {resume}` in the design folder (or open it again)."
        if resume
        else ""
    )
    return True, f"the drawing window of {name} was closed.{later}"


def main(argv: list[str]) -> int:
    """``python -m cotaforge_mcp.drawing_window --record <file> --opener <pid> -- <command...>``"""
    if "--" not in argv:
        sys.stderr.write("usage: drawing_window --record <file> --opener <pid> -- <command...>\n")
        return 2
    cut = argv.index("--")
    options, command = argv[:cut], argv[cut + 1 :]
    try:
        record = Path(options[options.index("--record") + 1])
        opener = int(options[options.index("--opener") + 1])
    except (ValueError, IndexError):
        sys.stderr.write("drawing_window needs --record and --opener\n")
        return 2
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl+C is for claude, which shares the console; the wrapper stays
    return run_wrapper(record, opener, command)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
