"""The Windows installer's sources (``packaging/windows``, the workflow): nothing here compiles Inno Setup or runs
Windows, it only keeps the pieces consistent with the commands the program really has."""

import re
from pathlib import Path

import yaml

from cotaforge_mcp import cli, layout

REPO = Path(__file__).resolve().parents[3]
WINDOWS = REPO / "packaging" / "windows"
ISS = WINDOWS / "cotaforge.iss"
BUILD = WINDOWS / "build.ps1"
WORKFLOW = REPO / ".github" / "workflows" / "windows-installer.yml"


def iss_text() -> str:
    return ISS.read_text(encoding="utf-8-sig")


def test_the_inno_script_is_saved_as_utf8_with_bom() -> None:
    """Inno Setup reads a script without BOM as ANSI: the Spanish texts would be garbled."""
    assert ISS.read_bytes().startswith(b"\xef\xbb\xbf")


def test_the_installer_is_per_user_fixed_folder_and_never_asks_for_admin() -> None:
    text = iss_text()
    for line in (
        "PrivilegesRequired=lowest",
        r"DefaultDirName={localappdata}\Programs\cotaforge",
        "DisableDirPage=yes",
        "OutputBaseFilename=cotaforge-{#AppVersion}-setup",
    ):
        assert line in text, line
    assert re.search(r"^AppId=\{\{[0-9A-F-]{36}\}$", text, re.MULTILINE), "a stable AppId (GUID)"


def test_only_cotaforge_processes_are_closed_and_only_after_asking() -> None:
    """A Claude session keeps a cad server running from {app}\\python (even with its window closed), and Windows will
    not replace a running file. Setup and Uninstall find those processes by their path, ask (a FreeCAD design may be
    unsaved) unless silent, and close them by PID: never the Restart Manager, never by name, never the process tree."""
    text = iss_text()
    assert "CloseApplications=no" in text.splitlines()
    assert "function PrepareToInstall(" in text and "function InitializeUninstall(" in text
    assert "FreeInstallFolder(WizardSilent()" in text and "FreeInstallFolder(UninstallSilent()" in text
    assert "'python\\'" in text, "only processes whose executable is inside {app}\\python"
    kills = [line for line in text.splitlines() if "taskkill" in line]
    assert kills and all("'/PID '" in line for line in kills)
    assert "/IM" not in text and "/T'" not in text and " /T " not in text
    assert "guárdalo antes de seguir" in text, "the question warns about unsaved FreeCAD work"


def test_no_code_line_starts_with_a_hash() -> None:
    """Inno's preprocessor reads a line starting with '#' as a directive (a `#13#10` continuation broke the build)."""
    code = iss_text().split("[Code]", 1)[1]
    assert not [line for line in code.splitlines() if line.lstrip().startswith("#")]


def test_no_pascal_comment_holds_a_brace() -> None:
    """A `{ ... }` comment ends at the first `}`: writing `{app}` inside one broke the build."""
    code = iss_text().split("[Code]", 1)[1]
    # String literals may hold braces; they never span lines in Pascal.
    code = "\n".join(re.sub(r"'[^']*'", "''", line) for line in code.splitlines())
    depth = 0
    for char in code:
        if char == "{":
            depth += 1
            assert depth == 1, "a brace inside a { } comment"
        elif char == "}":
            depth -= 1
            assert depth == 0, "an unbalanced } in [Code]"


def test_the_ci_updates_and_uninstalls_over_a_running_server() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "Update over a running server" in workflow
    assert workflow.count("time.sleep(900)") == 2, "one busy server for the update, one for the uninstall"
    assert "the update left the running server alive" in workflow
    assert "the uninstall left the running server alive" in workflow


def test_an_update_clears_the_old_python_and_plugin_first() -> None:
    text = iss_text()
    for folder in ("python", "marketplace"):
        assert f'Type: filesandordirs; Name: "{{app}}\\{folder}"' in text.split("[UninstallDelete]")[0]


def test_every_command_the_installer_runs_exists_in_the_cli() -> None:
    text = iss_text()
    commands = set(re.findall(r"cotaforge_mcp\.cli ([a-z-]+)(?: ([a-z]+))?", text))
    assert ("new", "") in commands and ("open", "") in commands
    assert ("plugin", "unregister") in commands
    assert any(name == "install-info" for name, _ in commands)
    assert any(pair == ("plugin", "register") for pair in commands)
    parser = cli.build_parser()
    for name, action in commands:
        args = [name, *([action] if action else [])]
        if name == "install-info":
            args += ["--version", "0"]
        parsed = parser.parse_args(args)
        assert parsed.command == name


def test_the_shortcuts_run_without_a_console() -> None:
    icons = [line for line in iss_text().splitlines() if line.startswith("Name: ") and "cotaforge_mcp.cli" in line]
    assert len(icons) == 2
    assert all(r"{app}\python\pythonw.exe" in line for line in icons)


def test_the_installer_detects_freecad_and_claude_and_skips_the_pages_when_silent() -> None:
    text = iss_text()
    for needle in (
        "FreeCADCmd.exe",
        "claude.exe",
        "https://www.freecad.org/downloads.php",
        "irm https://claude.ai/install.ps1 | iex",
        "SuppressibleMsgBox",
        "function ShouldSkipPage",
    ):
        assert needle in text, needle


def test_the_installed_layout_matches_what_the_program_looks_for() -> None:
    """build.ps1 stages python\\, marketplace\\ and docs\\; layout.py reads install.json next to them."""
    text = BUILD.read_text(encoding="utf-8")
    assert 'Join-Path $Stage "marketplace"' in text and "python" in text
    assert layout.INSTALL_JSON == "install.json"
    assert r"{app}\docs\{cm:ManualFile}" in iss_text()
    for manual in ("manual-usuario.md", "user-manual.md"):
        assert manual in iss_text() and manual in text and (REPO / "docs" / manual).is_file(), manual


def test_the_build_pins_python_with_a_sha256() -> None:
    text = BUILD.read_text(encoding="utf-8")
    assert re.search(r'^\$PbsSha256 = "[0-9a-f]{64}"$', text, re.MULTILINE)
    assert re.search(
        r'^\$PbsFile = "cpython-3\.12\.\d+\+\d+-x86_64-pc-windows-msvc-install_only\.tar\.gz"$', text, re.M
    )
    assert "Get-FileHash" in text
    assert text.isascii(), "Windows PowerShell 5.1 reads a script without BOM as ANSI: keep build.ps1 plain ASCII"


def test_the_workflow_runs_on_pull_requests_tags_and_by_hand_and_only_drafts_releases() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = data[True]  # YAML 1.1 reads the key `on` as True
    assert set(triggers) == {"pull_request", "push", "workflow_dispatch"}
    assert triggers["push"]["tags"] == ["v*"]
    assert set(triggers["push"]["branches"]) == {"develop", "main"}  # every merge is built and installed
    paths = set(triggers["pull_request"]["paths"])
    packed = {"plugin/**", "packaging/**", ".claude-plugin/**", "kernel/pyproject.toml", "mcp-server/pyproject.toml"}
    assert packed <= paths  # a PR that changes what the installer packs runs it before the merge
    assert {"mcp-server/src/cotaforge_mcp/cli.py", "mcp-server/src/cotaforge_mcp/launcher.py"} <= paths
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--draft" in text and "gh release create" in text
    assert "--latest" not in text and "gh release edit" not in text
    assert 'Join-Path (Split-Path $env:SETUP) "cotaforge-setup.exe"' in text and "Move-Item $env:SETUP $latest" in text, (
        "the release carries one installer, cotaforge-setup.exe, the stable name the download link of the site points at"
    )
    assert '"${{ github.ref_name }}" $latest --draft' in text, "only that one file is attached"
    assert "/VERYSILENT" in text and "/SUPPRESSMSGBOXES" in text and "/NORESTART" in text


ICONS = ("cotaforge.ico", "cotaforge-new.ico", "cotaforge-open.ico")


def test_the_installer_ships_its_icons_in_every_size_windows_uses() -> None:
    build = BUILD.read_text(encoding="utf-8")
    for name in ICONS:
        data = (WINDOWS / name).read_bytes()
        assert data[:4] == b"\x00\x00\x01\x00", f"{name} is an ICO file"
        sizes = {data[6 + 16 * i] or 256 for i in range(int.from_bytes(data[4:6], "little"))}
        assert {16, 32, 48, 256} <= sizes, name
        assert f'"{name}"' in build, f"build.ps1 stages {name}"


def test_create_and_open_have_different_names_and_icons() -> None:
    """The two launchers were easy to confuse: they start with different verbs and carry different icons."""
    text = iss_text()
    assert "SetupIconFile=cotaforge.ico" in text and r"UninstallDisplayIcon={app}\cotaforge.ico" in text
    lines = {line.split(";")[0]: line for line in text.splitlines() if line.startswith("Name: ")}
    create = lines[r'Name: "{autoprograms}\cotaforge\{cm:ShortcutNew}"']
    open_ = lines[r'Name: "{autoprograms}\cotaforge\{cm:ShortcutOpen}"']
    for language, new_name, open_name in (
        ("spanish", "Crear proyecto nuevo (CotaForge)", "Abrir proyecto existente (CotaForge)"),
        ("english", "Create new project (CotaForge)", "Open existing project (CotaForge)"),
    ):
        assert f"{language}.ShortcutNew={new_name}\n" in text and f"{language}.ShortcutOpen={open_name}\n" in text
        assert f'Type: files; Name: "{{autoprograms}}\\cotaforge\\{new_name}.lnk"' in text, (
            "the other language's shortcut goes"
        )
    assert 'Name: "english"; MessagesFile: "compiler:Default.isl"' in text and "--language ' + ActiveLanguage" in text
    assert r'IconFilename: "{app}\cotaforge-new.ico"' in create and "cli new" in create
    assert r'IconFilename: "{app}\cotaforge-open.ico"' in open_ and "cli open" in open_
    for old in ("Nuevo proyecto cotaforge.lnk", "Abrir proyecto cotaforge.lnk"):
        assert f'Type: files; Name: "{{autoprograms}}\\cotaforge\\{old}"' in text, "an update removes the 0.8.x names"


def test_the_tag_version_reaches_the_build_script_by_name() -> None:
    """Splatting an array into a script binds "-Version" positionally (v0.8.0 failed so): it must be a hashtable."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "$buildArgs = @{}" in text and "$buildArgs.Version = " in text
    assert '@("-Version"' not in text
