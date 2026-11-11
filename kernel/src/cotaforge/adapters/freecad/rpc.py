"""FreeCAD-specific half of the RPC transport: resolving the
FreeCAD root and building the worker command line, headless (``freecadcmd -c <bootstrap>``) or visible
(``freecad gui_entry.py``, the GUI binary: a real window the engineer watches).

The CAD-agnostic transport (``RpcFailure``, ``WorkerLost``, ``WorkerTimeout``, ``WorkerProcess`` and its
spawn/kill/reader-thread internals) lives in ``cotaforge.adapters.rpc`` so that other adapters (e.g. Windows
workers reached over WSL interop) can reuse it. Everything is re-exported
here so existing import paths (``from cotaforge.adapters.freecad.rpc import WorkerProcess, ...``) keep
working unchanged.

This module never imports the worker package: it only needs its directory path.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from cotaforge.adapters.rpc import (
    ENV_RPC_FD,
    HELLO_ID,
    STDOUT_PREFIX,
    Heartbeat,
    Liveness,
    NotificationListener,
    RpcFailure,
    WorkerBlocked,
    WorkerBusy,
    WorkerLost,
    WorkerProcess,
    WorkerTimeout,
)

DEFAULT_FREECAD_ROOT = Path("~/Applications/freecad-1.1.3/squashfs-root")
ENV_FREECAD_ROOT = "COTAFORGE_FREECAD_ROOT"
ENV_WORKER_LOG = "COTAFORGE_WORKER_LOG"
ENV_USER_HOME = "FREECAD_USER_HOME"
ENV_ISOLATED = "COTAFORGE_FREECAD_ISOLATED"
"""``1``: this FreeCAD gets a profile of its own (see ``isolated_home``) and no document recovery. Set by the test
fixtures and by scripts that start FreeCAD next to the engineer's real work: without it a worker shares the
harness's default private profile in the temp folder, with the recent files and the recovery data of every
session of the engineer."""
ENV_COTAFORGE_HOME = "COTAFORGE_HOME"
ENV_WORKER_ISOLATED = "COTAFORGE_WORKER_ISOLATED"
"""Set by ``worker_env`` for an isolated worker: ``gui_entry.py`` turns the recent files and the autosave (the
source of recovery data) off in its preferences."""
ENV_MOD_DIRS = "COTAFORGE_FREECAD_MOD_DIRS"
"""Optional override (``os.pathsep``-separated) of the engineer's user ``Mod`` folders searched for add-ons."""
ENV_USER_MODS = "COTAFORGE_WORKER_USER_MODS"
"""Set by ``worker_env`` in visible mode: the candidate ``Mod`` folders (``os.pathsep``-separated) whose add-ons the
worker puts on ``sys.path``, because the private ``FREECAD_USER_HOME`` hides the engineer's own."""
ENV_TEST_HOOKS = "COTAFORGE_WORKER_TEST_HOOKS"
"""Gates ``worker/main.py``'s ``TEST_HOOK_METHODS`` (``test_select_entity``/``test_edit_property``): unset
(or any value other than ``"1"``) in production, set to ``"1"`` only by callers that opt in through
``FreeCADAdapter(enable_test_hooks=True)`` (the visible integration suite) -- without it those two RPC
methods do not exist on the production worker at all (``unknown method``, same as any other typo)."""
WORKER_DIR = Path(os.path.abspath(__file__)).parent / "worker"  # not resolve(): keep a subst drive short
GUI_ENTRY = "gui_entry.py"

# FreeCAD >= 1.1's ``processFiles`` treats every ``-c``/positional
# argument as a filesystem path and probes it with ``std::filesystem`` before falling back to
# ``runString``; a run of bytes with no ``/`` longer than ``NAME_MAX`` raises ``ENAMETOOLONG`` and kills
# the process outright. The headless bootstrap must never depend on staying under that limit by luck.
_MAX_SLASHFREE_RUN = 255

__all__ = [
    "DEFAULT_FREECAD_ROOT",
    "ENV_RPC_FD",
    "ENV_MOD_DIRS",
    "ENV_TEST_HOOKS",
    "ENV_USER_HOME",
    "ENV_USER_MODS",
    "ENV_WORKER_LOG",
    "GUI_ENTRY",
    "HELLO_ID",
    "STDOUT_PREFIX",
    "WORKER_DIR",
    "Heartbeat",
    "Liveness",
    "NotificationListener",
    "RpcFailure",
    "WorkerBlocked",
    "WorkerBusy",
    "WorkerLost",
    "WorkerProcess",
    "WorkerTimeout",
    "find_freecad_root",
    "missing_root_hint",
    "user_mod_candidates",
    "worker_command",
    "worker_env",
]


def _is_windows() -> bool:
    return sys.platform == "win32"


def _binaries(root: Path) -> tuple[Path, Path]:
    """(headless, gui) executables under ``root``: the AppImage layout, or the Windows installer layout.

    Verified 2026-09-23 on a real Windows 11 machine against FreeCAD 1.1 (``C:\\Program Files\\FreeCAD 1.1``):
    the installer ships ``bin\\freecadcmd.exe`` (console subsystem, the safe headless choice) and
    ``bin\\freecad.exe`` (GUI subsystem) -- lowercase, unlike the ``FreeCADCmd.exe``/``FreeCAD.exe`` spelling
    below, but NTFS path lookups are case-insensitive so both spellings resolve the same file; kept
    capitalised here to match the installer's shortcut/Start Menu naming.
    """
    if _is_windows():
        return root / "bin" / "FreeCADCmd.exe", root / "bin" / "FreeCAD.exe"
    return root / "usr" / "bin" / "freecadcmd", root / "usr" / "bin" / "freecad"


def _has_freecadcmd(root: Path) -> bool:
    cmd = _binaries(root)[0]
    return cmd.is_file() and os.access(cmd, os.X_OK)


def _windows_candidate_roots() -> list[Path]:
    """Every Windows FreeCAD install location this harness knows to look under, newest-name-first within
    each: the installer's default (``%ProgramFiles%\\FreeCAD <ver>``, verified against a real FreeCAD 1.1
    install) takes priority over the per-user ``%LOCALAPPDATA%\\Programs\\FreeCAD*`` layout some installers
    and portable zips use instead, so a machine-wide install is preferred over a per-user one when both exist."""
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    roots = sorted((p for p in program_files.glob("FreeCAD*") if p.is_dir()), reverse=True)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots += sorted((p for p in (Path(local_app_data) / "Programs").glob("FreeCAD*") if p.is_dir()), reverse=True)
    return roots


def _candidate_roots(explicit: Path | None) -> list[Path]:
    if explicit is not None:
        return [Path(explicit).expanduser()]
    if os.environ.get(ENV_FREECAD_ROOT):
        return [Path(os.environ[ENV_FREECAD_ROOT]).expanduser()]
    if _is_windows():
        return _windows_candidate_roots()
    return [DEFAULT_FREECAD_ROOT.expanduser()]


def find_freecad_root(explicit: Path | None = None) -> Path | None:
    """Resolve the FreeCAD root: argument > ``$COTAFORGE_FREECAD_ROOT`` > the default.

    Only the first candidate that exists is considered (on Windows, without an argument or the env var,
    the newest ``%ProgramFiles%\\FreeCAD*``, falling back to the newest
    ``%LOCALAPPDATA%\\Programs\\FreeCAD*``, that has the headless executable); ``None`` when no candidate
    has ``usr/bin/freecadcmd`` (``bin/FreeCADCmd.exe`` on Windows).
    """
    for candidate in _candidate_roots(explicit):
        if _has_freecadcmd(candidate):
            return candidate
    return None


def missing_root_hint() -> str:
    """The headless executable's path relative to the FreeCAD root, phrased for the current platform
    (derived from :func:`_binaries`, the single source of truth, so this can never drift into naming the
    wrong platform's layout): used in error messages that report no FreeCAD root was found."""
    return _binaries(Path())[0].as_posix()


def _max_slashfree_run(code: str) -> int:
    """Longest byte run between ``/`` characters in ``code`` — what FreeCAD >= 1.1's path probing
    actually measures against ``NAME_MAX``."""
    return max((len(part.encode("utf-8")) for part in code.split("/")), default=0)


def _write_bootstrap_script(worker_dir: Path, bootstrap: str) -> Path:
    """Fallback for the ``ENAMETOOLONG`` limit: a temp ``.py`` file FreeCAD runs as a real script instead of a ``-c``
    argument, so no positional-argument length ever depends on the worker path's shape."""
    import tempfile  # noqa: PLC0415 - only needed on this rare path

    fd, name = tempfile.mkstemp(prefix="cotaforge-freecad-bootstrap-", suffix=".py", dir=str(worker_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(bootstrap)
    except BaseException:
        os.unlink(name)
        raise
    return Path(name)


def worker_command(freecad_root: Path, worker_dir: Path, *, visible: bool = False) -> list[str]:
    """The command line that starts the worker loop.

    Headless: ``freecadcmd -c "<bootstrap>"`` — unless the bootstrap string would hit that limit (a slash-free
    run over 255 bytes anywhere in it, e.g. an unusually deep ``worker_dir`` with no separators), in which
    case it is written to a temp script file and run positionally instead (``freecadcmd <script>``): never
    depend on the path happening to contain enough ``/`` to stay safe. Visible: ``freecad gui_entry.py`` —
    the GUI binary takes a positional script (``-c`` would put it in console mode) and runs it from inside
    its Qt event loop with the script's directory on ``sys.path``; ``gui_entry.py`` calls ``main.run()``.
    """
    headless, gui = _binaries(freecad_root)
    if visible:
        return [str(gui), str(worker_dir / GUI_ENTRY)]
    bootstrap = f"import sys; sys.path.insert(0, {str(worker_dir)!r}); import main; main.run()"
    if _max_slashfree_run(bootstrap) > _MAX_SLASHFREE_RUN:
        script = _write_bootstrap_script(worker_dir, bootstrap)
        return [str(headless), str(script)]
    return [str(headless), "-c", bootstrap]


def user_mod_candidates(
    env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> list[Path]:
    """The engineer's own FreeCAD ``Mod`` folders (where the Addon Manager installs add-ons), as candidates.

    ``$COTAFORGE_FREECAD_MOD_DIRS`` (``os.pathsep``-separated) replaces the defaults. Otherwise, per platform: FreeCAD
    >= 1.1 keeps its user data in a versioned folder (verified on 1.1.3: ``getUserAppDataDir()`` is
    ``~/.local/share/FreeCAD/v1-1/`` on Linux, and the Addon Manager installs into its ``Mod``), older versions in the
    unversioned one; the Windows and macOS locations follow the same layout and are not verified here. Candidates need
    not exist: the worker keeps the existing ones and names all of them when the add-on is not found."""
    env = os.environ if env is None else env
    explicit = env.get(ENV_MOD_DIRS)
    if explicit:
        return [Path(p).expanduser() for p in explicit.split(os.pathsep) if p.strip()]
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home
    if platform == "win32":
        appdata = env.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return [base / "FreeCAD" / "v1-1" / "Mod", base / "FreeCAD" / "Mod"]
    if platform == "darwin":
        base = home / "Library" / "Application Support" / "FreeCAD"
        return [base / "v1-1" / "Mod", base / "Mod"]
    data = env.get("XDG_DATA_HOME")
    base = (Path(data) if data else home / ".local" / "share") / "FreeCAD"
    return [base / "v1-1" / "Mod", base / "Mod", home / ".FreeCAD" / "Mod"]


def isolated_home(log_path: Path, env: Mapping[str, str] | None = None) -> Path | None:
    """The private FreeCAD profile of an isolated worker, or ``None`` when ``COTAFORGE_FREECAD_ISOLATED`` is not
    ``1``. It is ``<COTAFORGE_HOME>/freecad-home`` when the data directory is set (the profile lives and dies with
    it), else a new folder next to the log that no other run reuses. FreeCAD puts its configuration, recent files,
    temp and recovery data under ``FREECAD_USER_HOME`` (checked with ``FreeCADCmd --dump-config`` on 1.1.3)."""
    env = os.environ if env is None else env
    if env.get(ENV_ISOLATED) != "1":
        return None
    data = (env.get(ENV_COTAFORGE_HOME) or "").strip()
    if data:
        return Path(data).expanduser() / "freecad-home"
    import uuid  # noqa: PLC0415 - only the isolated start without a data directory needs it

    return log_path.parent / f"freecad-home-{uuid.uuid4().hex[:8]}"


def worker_env(log_path: Path, *, visible: bool = False, enable_test_hooks: bool = False) -> dict[str, str]:
    """Extra environment for the worker process (merged over the parent's by ``WorkerProcess``).

    ``COTAFORGE_WORKER_LOG`` tells the worker where fd 1 goes when it has no reply pipe fd and steals
    stdout for the replies (the Windows path; harmless otherwise). Visible mode also sets a private
    ``FREECAD_USER_HOME`` next to the log: FreeCAD >= 1.1 keys its
    config/data directories by version (``v1-1/``) and, when a pre-1.1 config exists without a matching
    ``v1-1/`` tree, pops a modal "migrate configuration?" dialog on first paint that a headless/offscreen
    worker can never answer, hanging until the connect timeout. ``FREECAD_USER_HOME`` puts FreeCAD in
    portable mode (``ApplicationDirectories::usingCustomDirectories()``), which short-circuits that check
    entirely, and also keeps the harness's workers off the engineer's own FreeCAD preferences. That home hides the
    engineer's own ``Mod`` folder too (add-ons from the Addon Manager, e.g. SheetMetal), so the real candidate folders
    are computed BEFORE the override and handed over in ``COTAFORGE_WORKER_USER_MODS``; the worker only reads them
    (nothing is ever written into the engineer's profile). Linux also
    points fontconfig at the system configuration exactly as the AppImage's ``AppRun`` does, which silences
    the "Cannot load default config file" error the bare ``usr/bin/freecad`` binary prints.

    ``enable_test_hooks`` sets ``COTAFORGE_WORKER_TEST_HOOKS=1`` (``ENV_TEST_HOOKS``), the only way the
    worker ever registers ``test_select_entity``/``test_edit_property`` -- opt-in, never on by default.
    """
    env = {ENV_WORKER_LOG: str(log_path)}
    if enable_test_hooks:
        env[ENV_TEST_HOOKS] = "1"
    isolated = isolated_home(log_path)
    if visible or isolated is not None:
        env[ENV_USER_MODS] = os.pathsep.join(str(p) for p in user_mod_candidates())
        home = isolated if isolated is not None else log_path.parent / "freecad-home"
        home.mkdir(parents=True, exist_ok=True)
        env[ENV_USER_HOME] = str(home)
        if isolated is not None:
            env[ENV_WORKER_ISOLATED] = "1"
    if visible:
        if sys.platform.startswith("linux"):
            env.setdefault("FONTCONFIG_FILE", os.environ.get("FONTCONFIG_FILE", "/etc/fonts/fonts.conf"))
            env.setdefault("FONTCONFIG_PATH", os.environ.get("FONTCONFIG_PATH", "/etc/fonts"))
    return env
