"""FreeCAD-specific ``rpc`` helpers: ``find_freecad_root``, ``worker_command`` and an end-to-end check
that the FreeCAD-flavoured hello/protocol still round-trips through the shared ``WorkerProcess`` against
``fake_worker.py`` (no FreeCAD needed). Generic ``WorkerProcess`` behaviour (framing, timeouts, crash
handling, spawn-failure paths) is covered once, CAD-agnostically, in ``tests/unit/adapters/test_rpc.py``.
"""

import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import rpc
from cotaforge.adapters.freecad.rpc import WorkerLost, WorkerProcess
from tests.platform_support import (
    FD_CHANNEL_SUPPORTED,
    PLAIN_PYTHON,
    freecad_gui,
    freecad_headless,
    hide_installed_freecad,
    stub_freecad_root,
)

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")
WorkerFactory = Callable[..., WorkerProcess]


@pytest.fixture
def make_worker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[WorkerFactory]:
    started: list[WorkerProcess] = []

    def factory(mode: str = "ok", *, use_fd_channel: bool = FD_CHANNEL_SUPPORTED) -> WorkerProcess:
        monkeypatch.setenv("FAKE_WORKER_DELAY_S", "2")
        command = [PLAIN_PYTHON, str(FAKE_WORKER), mode]
        worker = WorkerProcess(command, tmp_path / "worker.log", use_fd_channel=use_fd_channel)
        started.append(worker)
        return worker

    yield factory
    for worker in started:
        worker.stop(grace_s=0.5)


def test_start_returns_the_freecad_flavoured_hello_result(make_worker: WorkerFactory) -> None:
    worker = make_worker()
    hello = worker.start(timeout_s=5.0)
    assert hello["hello"] == "cotaforge-freecad-worker"
    assert hello["pid"] == worker.pid
    assert worker.alive


def test_call_round_trips_a_freecad_shaped_result(make_worker: WorkerFactory) -> None:
    worker = make_worker()
    worker.start(timeout_s=5.0)
    assert worker.call("canary", {"operation_id": "c"}, timeout_s=5.0)["healthy"] is True
    result = worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert result["operation_id"] == "base"
    assert result["feature"] == {"id": "feature:1"}


def test_disconnect_reply_then_exit_is_clean(make_worker: WorkerFactory) -> None:
    worker = make_worker()
    worker.start(timeout_s=5.0)
    assert worker.call("disconnect", {"operation_id": "bye"}, timeout_s=5.0) == {}
    worker.stop(grace_s=2.0)
    assert worker.returncode == 0


def test_worker_command_uses_freecadcmd_with_the_bootstrap(tmp_path: Path) -> None:
    command = rpc.worker_command(tmp_path, tmp_path / "worker")
    assert command[0] == str(freecad_headless(tmp_path))
    assert command[1] == "-c"
    assert "import main; main.run()" in command[2]
    assert repr(str(tmp_path / "worker")) in command[2]  # embedded as a Python literal (backslashes doubled)


def test_max_slashfree_run_measures_the_longest_separator_free_byte_run() -> None:
    """FreeCAD >= 1.1's ``-c`` treats the whole code string as a
    path and dies with ``ENAMETOOLONG`` if any run of bytes with no ``/`` exceeds ``NAME_MAX`` (255) --
    this is exactly what ``std::filesystem`` measures per path component."""
    assert rpc._max_slashfree_run("a/bb/ccc") == 3
    assert rpc._max_slashfree_run("no/slash/free/run/here") < 256
    assert rpc._max_slashfree_run("x" * 300) == 300
    assert rpc._max_slashfree_run(f"/a/{'y' * 300}/b") == 300


def test_worker_command_falls_back_to_a_script_file_when_the_bootstrap_would_trip_rc6(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A worker directory nested deep enough, or an environment with an unusually long path component,
    must not depend on luck: ``worker_command`` measures the actual bootstrap string against the real
    ``NAME_MAX`` limit and switches to a real script file (no ``-c``, so no positional-argument length
    ever matters) instead of ever emitting a bootstrap that could hit that limit. Lowering the threshold here
    (rather than constructing an on-disk path over 255 bytes, which the filesystem itself refuses to
    create) exercises the exact same branch a pathological real path would."""
    monkeypatch.setattr(rpc, "_MAX_SLASHFREE_RUN", 5)  # any ordinary worker_dir now "trips" the limit
    worker_dir = tmp_path / "worker"
    worker_dir.mkdir()
    command = rpc.worker_command(tmp_path, worker_dir)
    assert command[0] == str(freecad_headless(tmp_path))
    assert len(command) == 2  # no "-c": a script path instead
    script = Path(command[1])
    assert script.parent == worker_dir and script.suffix == ".py"
    content = script.read_text(encoding="utf-8")
    assert "import main; main.run()" in content
    assert repr(str(worker_dir)) in content  # embedded as a Python literal (backslashes doubled)


def test_worker_command_visible_runs_the_gui_binary_with_the_entry_script(tmp_path: Path) -> None:
    command = rpc.worker_command(tmp_path, tmp_path / "worker", visible=True)
    assert command == [str(freecad_gui(tmp_path)), str(tmp_path / "worker" / "gui_entry.py")]
    assert (rpc.WORKER_DIR / rpc.GUI_ENTRY).is_file()


def test_worker_env_carries_the_log_path_and_fontconfig_only_for_visible_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = tmp_path / "w.log"
    monkeypatch.delenv("FONTCONFIG_FILE", raising=False)
    monkeypatch.setattr(rpc.sys, "platform", "linux")
    assert rpc.worker_env(log) == {"COTAFORGE_WORKER_LOG": str(log)}
    visible = rpc.worker_env(log, visible=True)
    assert visible["COTAFORGE_WORKER_LOG"] == str(log)
    assert visible["FONTCONFIG_FILE"] == "/etc/fonts/fonts.conf" and visible["FONTCONFIG_PATH"] == "/etc/fonts"
    monkeypatch.setenv("FONTCONFIG_FILE", "/custom/fonts.conf")
    assert rpc.worker_env(log, visible=True)["FONTCONFIG_FILE"] == "/custom/fonts.conf"
    monkeypatch.setattr(rpc.sys, "platform", "win32")
    win32 = rpc.worker_env(log, visible=True)
    assert win32.keys() == {"COTAFORGE_WORKER_LOG", "FREECAD_USER_HOME", "COTAFORGE_WORKER_USER_MODS"}  # no fontconfig


def test_user_mod_candidates_follow_each_platforms_freecad_layout(tmp_path: Path) -> None:
    home = tmp_path / "home"
    linux = rpc.user_mod_candidates({}, "linux", home)
    assert linux == [
        home / ".local/share/FreeCAD/v1-1/Mod",
        home / ".local/share/FreeCAD/Mod",
        home / ".FreeCAD/Mod",
    ]
    xdg = rpc.user_mod_candidates({"XDG_DATA_HOME": str(tmp_path / "xdg")}, "linux", home)
    assert xdg[:2] == [tmp_path / "xdg/FreeCAD/v1-1/Mod", tmp_path / "xdg/FreeCAD/Mod"]
    mac = rpc.user_mod_candidates({}, "darwin", home)
    assert mac == [
        home / "Library/Application Support/FreeCAD/v1-1/Mod",
        home / "Library/Application Support/FreeCAD/Mod",
    ]
    appdata = tmp_path / "Roaming"
    windows = rpc.user_mod_candidates({"APPDATA": str(appdata)}, "win32", home)
    assert windows == [appdata / "FreeCAD/v1-1/Mod", appdata / "FreeCAD/Mod"]
    assert rpc.user_mod_candidates({}, "win32", home)[0] == home / "AppData/Roaming/FreeCAD/v1-1/Mod"


def test_user_mod_candidates_honour_the_explicit_override(tmp_path: Path) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    env = {rpc.ENV_MOD_DIRS: f"{first}{rpc.os.pathsep}{rpc.os.pathsep}{second}", "APPDATA": "ignored"}
    assert rpc.user_mod_candidates(env, "win32", tmp_path) == [first, second]


def test_worker_env_visible_hands_the_real_mod_folders_over_before_the_private_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    real = tmp_path / "real-mod"
    monkeypatch.setenv(rpc.ENV_MOD_DIRS, str(real))
    monkeypatch.setattr(rpc.sys, "platform", "linux")
    log = tmp_path / "logs" / "w.log"
    log.parent.mkdir()
    assert rpc.ENV_USER_MODS not in rpc.worker_env(log)  # headless inherits the real profile
    visible = rpc.worker_env(log, visible=True)
    assert visible[rpc.ENV_USER_MODS] == str(real)
    assert not real.exists()  # only named: nothing is created in the engineer's profile


def test_worker_env_visible_sets_a_private_freecad_user_home_next_to_the_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh, harness-owned config/data directory so the
    version-migration modal never gets a chance to appear -- headless mode is unaffected and must
    not pay for a directory it will never use."""
    log = tmp_path / "logs" / "w.log"
    log.parent.mkdir(parents=True)
    monkeypatch.setattr(rpc.sys, "platform", "linux")
    assert rpc.ENV_USER_HOME not in rpc.worker_env(log)
    visible = rpc.worker_env(log, visible=True)
    home = Path(visible[rpc.ENV_USER_HOME])
    assert home == log.parent / "freecad-home"
    assert home.is_dir()


def test_windows_layout_uses_the_installer_executables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The Windows branch is unverified on a real machine; this pins the layout it assumes."""
    monkeypatch.setattr(rpc.sys, "platform", "win32")
    root = tmp_path / "FreeCAD 1.0"
    (root / "bin").mkdir(parents=True)
    assert rpc.find_freecad_root(root) is None
    exe = root / "bin" / "FreeCADCmd.exe"
    exe.write_bytes(b"MZ")
    exe.chmod(0o755)  # this test runs on POSIX, where the executable check is the mode bit
    assert rpc.find_freecad_root(root) == root
    assert rpc.worker_command(root, root / "w") == [
        str(root / "bin" / "FreeCADCmd.exe"),
        "-c",
        f"import sys; sys.path.insert(0, {str(root / 'w')!r}); import main; main.run()",
    ]
    assert rpc.worker_command(root, root / "w", visible=True) == [
        str(root / "bin" / "FreeCAD.exe"),
        str(root / "w" / "gui_entry.py"),
    ]


def test_find_freecad_root_prefers_argument_then_env_then_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    good = stub_freecad_root(tmp_path / "root")
    monkeypatch.setenv("COTAFORGE_FREECAD_ROOT", str(tmp_path / "missing"))
    assert rpc.find_freecad_root(good) == good
    assert rpc.find_freecad_root(tmp_path / "nowhere") is None
    monkeypatch.setenv("COTAFORGE_FREECAD_ROOT", str(good))
    assert rpc.find_freecad_root() == good
    hide_installed_freecad(monkeypatch, tmp_path)  # no env, no default, nothing installed on this box
    assert rpc.find_freecad_root() is None


@pytest.mark.skipif(
    sys.platform == "win32", reason="the executable check is the POSIX mode bit; Windows has no such bit"
)
def test_find_freecad_root_rejects_a_non_executable_freecadcmd(tmp_path: Path) -> None:
    root = stub_freecad_root(tmp_path / "root")
    freecad_headless(root).chmod(0o644)
    assert rpc.find_freecad_root(root) is None


def test_find_freecad_root_rejects_a_directory_where_the_executable_should_be(tmp_path: Path) -> None:
    """Platform-neutral counterpart of the mode-bit test: the headless path must be a file."""
    root = tmp_path / "root"
    freecad_headless(root).mkdir(parents=True)
    assert rpc.find_freecad_root(root) is None


def test_missing_root_hint_is_platform_correct() -> None:
    """The adapter's connect() error names the exact relative path find_freecad_root looked for, derived
    from the same ``_binaries`` table it actually checks -- never a hardcoded Linux path on Windows."""
    assert rpc.missing_root_hint() == ("bin/FreeCADCmd.exe" if sys.platform == "win32" else "usr/bin/freecadcmd")


def test_windows_candidate_roots_checks_program_files_then_localappdata_programs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(rpc.sys, "platform", "win32")
    monkeypatch.delenv("COTAFORGE_FREECAD_ROOT", raising=False)
    program_files = tmp_path / "Program Files"
    local_programs = tmp_path / "AppData" / "Local" / "Programs"
    program_files.mkdir(parents=True)
    local_programs.mkdir(parents=True)
    monkeypatch.setenv("ProgramFiles", str(program_files))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))

    local_only = local_programs / "FreeCAD 1.1"
    (local_only / "bin").mkdir(parents=True)
    exe = local_only / "bin" / "FreeCADCmd.exe"
    exe.write_bytes(b"MZ")
    exe.chmod(0o755)

    assert rpc.find_freecad_root() == local_only  # found under %LOCALAPPDATA%\Programs when Program Files has none

    program_files_root = program_files / "FreeCAD 1.2"
    (program_files_root / "bin").mkdir(parents=True)
    pf_exe = program_files_root / "bin" / "FreeCADCmd.exe"
    pf_exe.write_bytes(b"MZ")
    pf_exe.chmod(0o755)

    assert rpc.find_freecad_root() == program_files_root  # Program Files takes priority when both exist


def test_wrong_id_is_worker_lost(make_worker: WorkerFactory) -> None:
    """Regression check kept here (not just generically) because it round-trips through the FreeCAD
    hello/canned-result shape used by ``fake_worker.py`` and the real worker."""
    worker = make_worker("wrong_id")
    worker.start(timeout_s=5.0)
    with pytest.raises(WorkerLost) as info:
        worker.call("extrude", {"operation_id": "base"}, timeout_s=5.0)
    assert "id" in str(info.value)
    assert not worker.alive
