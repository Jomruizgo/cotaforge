"""``cotaforge init``: the design space it writes, the merge with what is already there, the quoting of paths with a
space and the generated ``.mcp.json`` started as a real stdio server (fake CAD). Every folder is temporary; the real
home is never read: ``home`` is always a temporary folder."""

import json
import os
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cotaforge_mcp import cli
from cotaforge_mcp.schemas import TOOLS

REPO = Path(__file__).resolve().parents[3]
SOURCE_TEMPLATE = REPO / "templates" / "design-space"
INTERPRETER = "/opt/my tools/venv/bin/python"  # a path with a space on purpose
HOOK_MODULE = "cotaforge.native.hook"


def init(folder: Path, home: Path, **kwargs: Any) -> cli.Report:
    kwargs.setdefault("interpreter", INTERPRETER)
    kwargs.setdefault("adapter", "fake")
    return cli.init_space(folder, home=home, **kwargs)


def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def tree(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


# --- the packaged template ------------------------------------------------------------------------------------------


def test_the_packaged_template_equals_templates_design_space() -> None:
    """The copy inside the package is the only one an installed ``cotaforge`` can reach: it must not drift.
    Refresh it with ``python mcp-server/scripts/sync_template.py``."""
    packaged = Path(str(cli.template_root()))
    assert packaged.is_dir()
    assert tree(packaged) == tree(SOURCE_TEMPLATE)
    assert ".claude/settings.local.json" in tree(packaged), "hidden files travel with the package"


# --- what is written ------------------------------------------------------------------------------------------------


def test_init_creates_the_design_space_in_a_new_nested_folder(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "a" / "my design"
    report = init(folder, home)
    assert report.folder == folder.resolve()
    assert (folder / "CLAUDE.md").read_bytes() == (SOURCE_TEMPLATE / "CLAUDE.md").read_bytes()
    assert (folder / "memory" / ".gitkeep").is_file()
    assert {line.split()[0] for line in report.lines} == {"creado"}


def test_mcp_json_declares_the_cad_server_with_the_absolute_interpreter(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "my design"
    init(folder, home)
    server = read_json(folder / ".mcp.json")["mcpServers"]["cad"]
    assert server["command"] == INTERPRETER
    assert server["args"] == ["-m", "cotaforge_mcp"]
    assert server["env"] == {"COTAFORGE_ADAPTER": "fake", "COTAFORGE_WORKSPACE": str(folder.resolve())}


def test_mcp_json_carries_the_freecad_root_and_the_visible_flag(tmp_path: Path, home: Path) -> None:
    root = tmp_path / "FreeCAD 1.1"
    root.mkdir()
    folder = tmp_path / "d"
    init(folder, home, adapter="freecad", freecad_root=root, visible=True)
    env = read_json(folder / ".mcp.json")["mcpServers"]["cad"]["env"]
    assert env["COTAFORGE_ADAPTER"] == "freecad"
    assert env["COTAFORGE_FREECAD_ROOT"] == str(root.resolve())
    assert env["COTAFORGE_VISIBLE"] == "1"
    init(folder, home, adapter="freecad", freecad_root=root, visible=False)
    assert "COTAFORGE_VISIBLE" not in read_json(folder / ".mcp.json")["mcpServers"]["cad"]["env"]


def test_the_freecad_root_is_discovered_like_the_kernel_does(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = tmp_path / "found"
    monkeypatch.setattr(cli, "find_freecad_root", lambda explicit=None: found if explicit is None else explicit)
    report = init(tmp_path / "d", home, adapter="freecad")
    assert read_json(tmp_path / "d" / ".mcp.json")["mcpServers"]["cad"]["env"]["COTAFORGE_FREECAD_ROOT"] == str(found)
    assert report.warnings == []


def test_a_missing_freecad_is_a_warning_not_an_error(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "find_freecad_root", lambda explicit=None: None)
    report = init(tmp_path / "d", home, adapter="freecad")
    assert len(report.warnings) == 1 and "--freecad-root" in report.warnings[0]
    assert "COTAFORGE_FREECAD_ROOT" not in read_json(tmp_path / "d" / ".mcp.json")["mcpServers"]["cad"]["env"]


def test_the_fake_adapter_needs_no_freecad(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli, "find_freecad_root", lambda explicit=None: pytest.fail("the fake adapter looks for no CAD")
    )
    assert init(tmp_path / "d", home, adapter="fake").warnings == []


def test_unknown_adapter_is_refused(tmp_path: Path, home: Path) -> None:
    with pytest.raises(cli.InitError, match="adaptador"):
        init(tmp_path / "d", home, adapter="othercad")
    assert not (tmp_path / "d").exists()


# --- settings and the hook ------------------------------------------------------------------------------------------


def hook_commands(settings: dict[str, Any]) -> list[str]:
    return [
        handler["command"]
        for entry in settings["hooks"]["PreToolUse"]
        if entry["matcher"] == "mcp__cad__run_native"
        for handler in entry["hooks"]
    ]


def test_settings_merge_the_template_with_the_absolute_hook(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "d"
    init(folder, home)
    settings = read_json(folder / ".claude" / "settings.local.json")
    template = read_json(SOURCE_TEMPLATE / ".claude" / "settings.local.json")
    assert settings["permissions"] == template["permissions"]
    assert settings["enabledMcpjsonServers"] == template["enabledMcpjsonServers"]
    assert set(template["claudeMdExcludes"]) <= set(settings["claudeMdExcludes"])
    (command,) = hook_commands(settings)
    assert command == f"'{INTERPRETER}' -m {HOOK_MODULE}"
    assert shlex.split(command) == [INTERPRETER, "-m", HOOK_MODULE]
    handler = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert (handler["type"], handler["timeout"]) == ("command", 10)


def test_hook_command_quotes_a_posix_path_with_a_space_and_a_quote() -> None:
    command = cli.hook_command("/home/o'brien/my venv/bin/python", windows=False)
    assert shlex.split(command) == ["/home/o'brien/my venv/bin/python", "-m", HOOK_MODULE]
    assert cli.hook_command("/usr/bin/python3", windows=False) == f"/usr/bin/python3 -m {HOOK_MODULE}"


def test_hook_command_uses_forward_slashes_and_quotes_on_windows() -> None:
    """Claude Code runs hooks in Git Bash on Windows: backslashes would be eaten and a space would split the path."""
    spaced = cli.hook_command(r"C:\Users\Ana Perez\venv\Scripts\python.exe", windows=True)
    assert spaced == f"'C:/Users/Ana Perez/venv/Scripts/python.exe' -m {HOOK_MODULE}"
    assert shlex.split(spaced)[0] == "C:/Users/Ana Perez/venv/Scripts/python.exe"
    plain = cli.hook_command(r"C:\tools\py\python.exe", windows=True)
    assert plain == f"C:/tools/py/python.exe -m {HOOK_MODULE}"


def test_hook_command_prefers_the_short_path_on_windows_when_the_path_has_a_space() -> None:
    long = r"C:\Users\Ana Perez\AppData\Local\Programs\cotaforge\python\python.exe"
    short = r"C:\Users\ANAPER~1\AppData\Local\Programs\cotaforge\python\python.exe"
    asked: list[str] = []

    def short_path(path: str) -> str | None:
        asked.append(path)
        return short

    command = cli.hook_command(long, windows=True, short_path=short_path)
    assert command == f"C:/Users/ANAPER~1/AppData/Local/Programs/cotaforge/python/python.exe -m {HOOK_MODULE}"
    assert shlex.split(command)[0].count(" ") == 0
    assert asked == [long]
    assert cli.hook_command(r"C:\tools\py\python.exe", windows=True, short_path=short_path).startswith("C:/tools")
    assert asked == [long], "a path without a space is never shortened"


@pytest.mark.parametrize("short", [None, "", r"C:\Users\Ana Perez\same.exe"])
def test_hook_command_falls_back_to_quoting_when_there_is_no_usable_short_path(short: str | None) -> None:
    long = r"C:\Users\Ana Perez\python.exe"
    command = cli.hook_command(long, windows=True, short_path=lambda path: short)
    assert command == f"'C:/Users/Ana Perez/python.exe' -m {HOOK_MODULE}"


def test_the_short_path_is_never_asked_off_windows() -> None:
    command = cli.hook_command("/opt/my tools/python", windows=False, short_path=lambda path: pytest.fail("asked"))
    assert shlex.split(command)[0] == "/opt/my tools/python"


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows API exists there")
def test_the_real_short_path_function_gives_none_off_windows() -> None:
    assert cli.windows_short_path("/tmp") is None


@pytest.mark.skipif(sys.platform != "win32", reason="GetShortPathNameW")
def test_the_real_short_path_function_on_windows(tmp_path: Path) -> None:
    target = tmp_path / "a folder with spaces"
    target.mkdir()
    assert cli.windows_short_path(str(target / "missing.exe")) is None
    short = cli.windows_short_path(str(target))
    assert short is not None and os.path.samefile(short, target)  # the long form itself where 8.3 names are disabled


@pytest.mark.skipif(sys.platform == "win32", reason="runs the command through sh")
def test_the_generated_hook_command_runs_through_a_shell_from_a_folder_with_a_space(tmp_path: Path, home: Path) -> None:
    """The command of a path with a space reaches the kernel hook: dangerous code asks, harmless code does not."""
    wrapper_dir = tmp_path / "my tools" / "bin"
    wrapper_dir.mkdir(parents=True)
    wrapper = wrapper_dir / "python"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    folder = tmp_path / "design space"
    init(folder, home, interpreter=str(wrapper))
    (command,) = hook_commands(read_json(folder / ".claude" / "settings.local.json"))
    assert "'" in command

    def run(code: str) -> subprocess.CompletedProcess[str]:
        payload = {"hook_event_name": "PreToolUse", "tool_name": "mcp__cad__run_native", "cwd": str(folder)}
        payload["tool_input"] = {"code": code, "intent": "test"}  # type: ignore[assignment]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
        return subprocess.run(  # noqa: S602
            command, shell=True, input=json.dumps(payload), capture_output=True, text=True, env=env, check=False
        )

    asked = run("import os; os.system('x')")
    assert asked.returncode == 0, asked.stderr
    assert json.loads(asked.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    quiet = run("import math\nx = math.pi\n")
    assert (quiet.returncode, quiet.stdout.strip()) == (0, "")


def test_claude_md_files_above_the_folder_and_in_the_home_are_excluded_by_exact_path(
    tmp_path: Path, home: Path
) -> None:
    (tmp_path / "CLAUDE.md").write_text("never read", encoding="utf-8")
    (tmp_path / "a" / ".claude").mkdir(parents=True)
    (tmp_path / "a" / ".claude" / "CLAUDE.md").write_text("never read", encoding="utf-8")
    (tmp_path / "a" / "CLAUDE.local.md").write_text("never read", encoding="utf-8")
    (home / ".claude").mkdir()
    (home / ".claude" / "CLAUDE.md").write_text("never read", encoding="utf-8")
    folder = tmp_path / "a" / "b" / "my design"
    init(folder, home)
    excludes = read_json(folder / ".claude" / "settings.local.json")["claudeMdExcludes"]
    expected = {
        (tmp_path / "CLAUDE.md").as_posix(),
        (tmp_path / "a" / ".claude" / "CLAUDE.md").as_posix(),
        (tmp_path / "a" / "CLAUDE.local.md").as_posix(),
        (home / ".claude" / "CLAUDE.md").as_posix(),
    }
    assert expected <= set(excludes)
    assert (folder / "CLAUDE.md").as_posix() not in excludes, "the design space's own CLAUDE.md stays"
    # Whatever the machine really has above the temp folder (a ``~/.claude/CLAUDE.md`` when the temp dir lives under a
    # user home) is legitimately found too: existence only, the content is never read.
    real_ancestors = {
        (directory / name).as_posix()
        for directory in tmp_path.parents
        for name in ("CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
        if (directory / name).is_file()
    }
    assert set(excludes) - expected - real_ancestors == {"**/.claude/CLAUDE.md"}
    assert real_ancestors <= set(excludes)
    assert "never read" not in (folder / ".claude" / "settings.local.json").read_text(encoding="utf-8")


# --- merge and idempotency ------------------------------------------------------------------------------------------


def test_existing_json_keys_that_are_not_ours_survive(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "d"
    (folder / ".claude").mkdir(parents=True)
    (folder / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "x"},
                    "cad": {"command": "old", "env": {"COTAFORGE_HOME": "/keep", "COTAFORGE_ADAPTER": "freecad"}},
                },
                "extra": 1,
            }
        ),
        encoding="utf-8",
    )
    other_hook = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    (folder / ".claude" / "settings.local.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls)"], "deny": ["Read(secret)"]},
                "model": "opus",
                "claudeMdExcludes": ["/mine/CLAUDE.md"],
                "hooks": {"PreToolUse": [other_hook], "Stop": [{"hooks": []}]},
            }
        ),
        encoding="utf-8",
    )
    init(folder, home)
    mcp = read_json(folder / ".mcp.json")
    assert mcp["extra"] == 1 and mcp["mcpServers"]["other"] == {"command": "x"}
    assert mcp["mcpServers"]["cad"]["command"] == INTERPRETER
    assert mcp["mcpServers"]["cad"]["env"]["COTAFORGE_HOME"] == "/keep"
    assert mcp["mcpServers"]["cad"]["env"]["COTAFORGE_ADAPTER"] == "fake"
    settings = read_json(folder / ".claude" / "settings.local.json")
    assert settings["model"] == "opus"
    template = read_json(SOURCE_TEMPLATE / ".claude" / "settings.local.json")
    assert settings["permissions"]["allow"] == ["Bash(ls)", *template["permissions"]["allow"]]
    assert settings["permissions"]["deny"] == ["Read(secret)"]
    assert settings["claudeMdExcludes"][0] == "/mine/CLAUDE.md"
    assert "**/.claude/CLAUDE.md" in settings["claudeMdExcludes"]
    assert settings["hooks"]["Stop"] == [{"hooks": []}]
    assert settings["hooks"]["PreToolUse"][0] == other_hook
    assert len(hook_commands(settings)) == 1


def test_running_init_twice_changes_nothing(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "d"
    init(folder, home)
    before = tree(folder)
    report = init(folder, home)
    assert tree(folder) == before
    assert {line.split()[0] for line in report.lines} == {"sin", "conservado"}  # "sin cambios"; memory/ is kept
    assert not list(folder.rglob("*.tmp"))


def test_a_new_interpreter_replaces_our_hook_instead_of_adding_another(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "d"
    init(folder, home)
    init(folder, home, interpreter="/other/python")
    settings = read_json(folder / ".claude" / "settings.local.json")
    assert hook_commands(settings) == [f"/other/python -m {HOOK_MODULE}"]
    assert read_json(folder / ".mcp.json")["mcpServers"]["cad"]["command"] == "/other/python"


def test_memory_and_an_edited_claude_md_are_kept_unless_forced(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "d"
    (folder / "memory").mkdir(parents=True)
    (folder / "memory" / "ajuste.md").write_text("mine", encoding="utf-8")
    (folder / "CLAUDE.md").write_text("my own conduct", encoding="utf-8")
    report = init(folder, home)
    assert (folder / "memory" / "ajuste.md").read_text(encoding="utf-8") == "mine"
    assert not (folder / "memory" / ".gitkeep").exists(), "an existing memory/ is not touched"
    assert (folder / "CLAUDE.md").read_text(encoding="utf-8") == "my own conduct"
    assert any("--force" in line and "CLAUDE.md" in line for line in report.lines)
    init(folder, home, force=True)
    assert (folder / "CLAUDE.md").read_bytes() == (SOURCE_TEMPLATE / "CLAUDE.md").read_bytes()
    assert (folder / "memory" / "ajuste.md").read_text(encoding="utf-8") == "mine"


@pytest.mark.parametrize("name", [".mcp.json", ".claude/settings.local.json"])
def test_an_invalid_json_file_is_never_overwritten_and_nothing_else_is_written(
    tmp_path: Path, home: Path, name: str
) -> None:
    folder = tmp_path / "d"
    (folder / ".claude").mkdir(parents=True)
    (folder / name).write_text("{ not json", encoding="utf-8")
    with pytest.raises(cli.InitError, match="JSON"):
        init(folder, home)
    assert (folder / name).read_text(encoding="utf-8") == "{ not json"
    assert not (folder / "CLAUDE.md").exists()


def test_a_file_where_the_folder_should_be_is_an_error(tmp_path: Path, home: Path) -> None:
    (tmp_path / "x").write_text("", encoding="utf-8")
    with pytest.raises(cli.InitError, match="no es una carpeta"):
        init(tmp_path / "x", home)


# --- the command ----------------------------------------------------------------------------------------------------


def test_main_prints_a_spanish_summary_and_the_next_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "executable", INTERPRETER)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    plugin = tmp_path / "the plugin"
    folder = tmp_path / "my design"
    assert cli.main(["init", str(folder), "--adapter", "fake", "--plugin-dir", str(plugin)]) == 0
    out = capsys.readouterr().out
    assert "Espacio de diseño listo" in out
    assert "creado" in out and ".mcp.json" in out and "settings.local.json" in out
    assert f'cd "{folder.resolve()}"' in out
    assert f'claude --plugin-dir "{plugin}"' in out
    assert "cotaforge plugin register" in out, "without a registered plugin the output says how to register it"
    assert read_json(folder / ".mcp.json")["mcpServers"]["cad"]["command"] == INTERPRETER


def test_main_without_a_registered_plugin_keeps_the_plugin_dir_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(cli.plugin_registry, "is_registered", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "find_plugin_dir", lambda: tmp_path / "the plugin")
    assert cli.main(["init", str(tmp_path / "d"), "--adapter", "fake"]) == 0
    out = capsys.readouterr().out
    assert f'claude --plugin-dir "{tmp_path / "the plugin"}"' in out


def test_main_with_a_registered_plugin_does_not_ask_for_plugin_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(cli.plugin_registry, "is_registered", lambda *args, **kwargs: True)
    folder = tmp_path / "my design"
    assert cli.main(["init", str(folder), "--adapter", "fake"]) == 0
    out = capsys.readouterr().out
    assert "--plugin-dir" not in out
    assert "pestaña Code" in out and "app de escritorio" in out
    assert f'cd "{folder.resolve()}"' in out and "      claude" in out


def test_an_explicit_plugin_dir_wins_over_a_registered_plugin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(cli.plugin_registry, "is_registered", lambda *args, **kwargs: pytest.fail("not asked"))
    assert cli.main(["init", str(tmp_path / "d"), "--adapter", "fake", "--plugin-dir", str(tmp_path)]) == 0
    assert "--plugin-dir" in capsys.readouterr().out


def test_the_interpreter_written_is_the_console_one_when_run_from_pythonw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(cli.plugin_registry, "is_registered", lambda *args, **kwargs: False)
    assert cli.main(["init", str(tmp_path / "d"), "--adapter", "fake"]) == 0
    assert read_json(tmp_path / "d" / ".mcp.json")["mcpServers"]["cad"]["command"] == str(tmp_path / "python.exe")


def test_version_prints_the_package_versions(capsys: pytest.CaptureFixture[str]) -> None:
    import cotaforge  # noqa: PLC0415

    import cotaforge_mcp  # noqa: PLC0415

    for argv in (["version"], ["--version"]):
        assert cli.main(argv) == 0
        out = capsys.readouterr().out
        assert f"cotaforge-kernel {cotaforge.__version__}" in out
        assert f"cotaforge-mcp {cotaforge_mcp.__version__}" in out


def test_install_info_records_what_the_installer_detected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = tmp_path / "Local Programs" / "cotaforge"
    app.mkdir(parents=True)
    argv = [
        "install-info",
        "--version",
        "1.2.3",
        "--freecad-root",
        r"C:\Program Files\FreeCAD 1.1",
        "--app-dir",
        str(app),
    ]
    assert cli.main(argv) == 0
    assert "1.2.3" in capsys.readouterr().out
    assert read_json(app / "install.json") == {
        "version": "1.2.3",
        "freecad_root": r"C:\Program Files\FreeCAD 1.1",
        "claude": "",
    }


def test_install_info_outside_an_installation_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.layout, "app_dir", lambda: None)
    assert cli.main(["install-info", "--version", "1.2.3"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert cli.main(["install-info", "--version", "1.2.3", "--app-dir", str(tmp_path / "missing")]) == 1


def test_no_command_prints_the_help_and_fails(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "init" in capsys.readouterr().out


def test_the_plugin_command_dispatches_to_the_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(cli.plugin_registry, "run_command", lambda name: seen.append(name) or 0)
    assert cli.main(["plugin", "status"]) == 0
    assert seen == ["status"]
    with pytest.raises(SystemExit):
        cli.main(["plugin", "nonsense"])


def test_main_reports_an_error_in_spanish_and_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".mcp.json").write_text("[]", encoding="utf-8")
    assert cli.main(["init", str(tmp_path), "--adapter", "fake"]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_the_console_script_is_declared() -> None:
    pyproject = (REPO / "mcp-server" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'cotaforge = "cotaforge_mcp.cli:main"' in pyproject


# --- the generated .mcp.json starts a real server -------------------------------------------------------------------


@asynccontextmanager
async def server_from_mcp_json(folder: Path, home: Path) -> AsyncIterator[ClientSession]:
    declared = read_json(folder / ".mcp.json")["mcpServers"]["cad"]
    env = {
        **os.environ,
        **declared["env"],
        "COTAFORGE_HOME": str(home),  # never the real ~/.cotaforge
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    params = StdioServerParameters(command=declared["command"], args=declared["args"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


async def test_the_generated_mcp_json_starts_the_server_and_lists_its_tools(tmp_path: Path, home: Path) -> None:
    folder = tmp_path / "my design"  # a space in the workspace path as well
    init(folder, home, interpreter=sys.executable)
    async with server_from_mcp_json(folder, home) as session:
        listed = {tool.name for tool in (await session.list_tools()).tools}
        assert {"op", "doc", "memory", "cad_guide"} <= listed
        assert listed <= {tool.name for tool in TOOLS}


def test_a_folder_of_another_cad_is_not_converted(tmp_path: Path) -> None:
    folder = tmp_path / "sw"
    folder.mkdir()
    other = {"mcpServers": {"cad": {"command": "py", "args": ["-m", "cotaforge_othercad"], "env": {}}}}
    (folder / ".mcp.json").write_text(json.dumps(other), encoding="utf-8")
    with pytest.raises(cli.InitError, match="de otro software de CAD"):
        cli.init_space(folder, interpreter="py", adapter="fake", home=tmp_path)
    assert json.loads((folder / ".mcp.json").read_text(encoding="utf-8")) == other
    assert not (folder / "CLAUDE.md").exists()


def test_hook_command_carries_the_save_calls_of_the_cad() -> None:
    plain = cli.hook_command("/usr/bin/python3", windows=False)
    assert "--save-methods" not in plain
    command = cli.hook_command("/usr/bin/python3", windows=False, save_methods=("SaveAs3", "SaveAs4"))
    assert shlex.split(command) == ["/usr/bin/python3", "-m", HOOK_MODULE, "--save-methods=SaveAs3,SaveAs4"]


def test_format_report_names_the_command_of_the_product() -> None:
    report = cli.Report(Path("/work/design"))
    assert "una vez con: cotaforge plugin register" in cli.format_report(report, "/plugin")
    other = cli.format_report(report, "/plugin", command="cotaforge-othercad")
    assert "una vez con: cotaforge-othercad plugin register" in other
