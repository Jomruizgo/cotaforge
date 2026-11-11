"""``cotaforge nuevo`` / ``abrir``: the flow runs against a fake UI (no window ever opens), a fake ``claude`` and
temporary folders."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cotaforge_mcp import cli, launcher, layout, plugin_registry
from tests.unit.test_plugin_registry import CLAUDE, FakeClaude

INTERPRETER = "/opt/cotaforge/python/python.exe"


class FakeUi:
    def __init__(self, folder: Path | None, *, confirm: bool = True, terminal: bool = False) -> None:
        self.folder = folder
        self.answers = {"confirm": confirm, "terminal": terminal}
        self.shown: list[tuple[str, str]] = []
        self.questions: list[str] = []

    def pick_folder(self, title: str) -> Path | None:
        self.shown.append(("pick", title))
        return self.folder

    def info(self, text: str) -> None:
        self.shown.append(("info", text))

    def error(self, text: str) -> None:
        self.shown.append(("error", text))

    def confirm(self, text: str) -> bool:
        self.shown.append(("confirm", text))
        return self.answers["confirm"]

    def ask_terminal(self, text: str, question: str) -> bool:
        self.shown.append(("terminal", text))
        self.questions.append(question)
        return self.answers["terminal"]

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.shown]


class Popen:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> None:
        self.calls.append((argv, kwargs))


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No real home, no real FreeCAD search, no real ``claude``, no install.json."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "find_freecad_root", lambda explicit=None: explicit)
    monkeypatch.setattr(layout, "read_install_info", lambda app=None: {})
    monkeypatch.setattr(plugin_registry, "find_claude", lambda **kwargs: CLAUDE)


def run(mode: str, ui: FakeUi, fake: FakeClaude, popen: Popen | None = None) -> int:
    return launcher.launch(mode, ui, runner=fake, popen=popen or Popen(), interpreter=INTERPRETER)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_nuevo_creates_a_visible_design_space_registers_the_plugin_and_explains_the_next_step(tmp_path: Path) -> None:
    folder = tmp_path / "mi proyecto"
    ui, fake = FakeUi(folder), FakeClaude()
    assert run("nuevo", ui, fake) == 0
    server = read_json(folder / ".mcp.json")["mcpServers"]["cad"]
    assert server["command"] == INTERPRETER
    assert server["env"]["COTAFORGE_VISIBLE"] == "1"
    assert (folder / "CLAUDE.md").is_file() and (folder / "memory").is_dir()
    assert "cotaforge@cotaforge" in fake.plugins
    assert ui.kinds() == ["pick", "terminal"]
    message = ui.shown[-1][1]
    assert str(folder.resolve()) in message and "pestaña Code" in message and "terminal" in message


def test_nuevo_uses_the_freecad_folder_the_installer_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    freecad = tmp_path / "FreeCAD 1.1"
    freecad.mkdir()
    monkeypatch.setattr(layout, "read_install_info", lambda app=None: {"freecad_root": str(freecad)})
    folder = tmp_path / "p"
    run("nuevo", FakeUi(folder), FakeClaude())
    assert read_json(folder / ".mcp.json")["mcpServers"]["cad"]["env"]["COTAFORGE_FREECAD_ROOT"] == str(freecad)


def test_nuevo_ignores_a_recorded_freecad_folder_that_is_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout, "read_install_info", lambda app=None: {"freecad_root": str(tmp_path / "gone")})
    folder = tmp_path / "p"
    run("nuevo", FakeUi(folder), FakeClaude())
    assert "COTAFORGE_FREECAD_ROOT" not in read_json(folder / ".mcp.json")["mcpServers"]["cad"]["env"]


def test_the_terminal_button_starts_claude_in_the_folder_in_a_new_console(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    popen = Popen()
    run("nuevo", FakeUi(folder, terminal=True), FakeClaude(), popen)
    ((argv, kwargs),) = popen.calls
    assert argv == [str(CLAUDE)]
    assert kwargs["cwd"] == str(folder.resolve())
    if sys.platform == "win32":
        assert kwargs["creationflags"] == subprocess.CREATE_NEW_CONSOLE


def test_without_claude_the_message_says_to_install_it_and_no_terminal_is_offered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_registry, "find_claude", lambda **kwargs: None)
    ui, fake, popen = FakeUi(tmp_path / "p", terminal=True), FakeClaude(), Popen()
    assert run("nuevo", ui, fake, popen) == 0
    assert ui.kinds() == ["pick", "info"]
    assert "No encontré Claude Code" in ui.shown[-1][1]
    assert fake.calls == [] and popen.calls == []
    assert (tmp_path / "p" / ".mcp.json").is_file()


def test_an_already_registered_plugin_is_not_registered_again(tmp_path: Path) -> None:
    fake = FakeClaude()
    marketplace = layout.find_marketplace_dir()
    assert marketplace is not None
    plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    before = len(fake.mutations())
    run("nuevo", FakeUi(tmp_path / "p"), fake)
    assert len(fake.mutations()) == before


def test_a_failed_registration_is_said_in_the_message_but_the_project_is_still_made(tmp_path: Path) -> None:
    fake = FakeClaude()
    fake.fail = ("marketplace", "add")
    ui = FakeUi(tmp_path / "p")
    assert run("nuevo", ui, fake) == 0
    assert "No pude registrar el plugin" in ui.shown[-1][1]
    assert (tmp_path / "p" / ".mcp.json").is_file()


def test_cancelling_the_dialog_does_nothing(tmp_path: Path) -> None:
    ui, fake = FakeUi(None), FakeClaude()
    assert run("nuevo", ui, fake) == 0
    assert ui.kinds() == ["pick"] and fake.calls == []


def test_nuevo_asks_before_using_a_folder_that_already_has_files(tmp_path: Path) -> None:
    folder = tmp_path / "busy"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine", encoding="utf-8")
    declined = FakeUi(folder, confirm=False)
    assert run("nuevo", declined, FakeClaude()) == 0
    assert "confirm" in declined.kinds() and not (folder / ".mcp.json").exists()
    accepted = FakeUi(folder, confirm=True)
    run("nuevo", accepted, FakeClaude())
    assert (folder / ".mcp.json").is_file() and (folder / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_abrir_refreshes_an_existing_project_and_keeps_what_is_there(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    cli.init_space(folder, interpreter="/old/python", adapter="fake", home=tmp_path / "home")
    (folder / "CLAUDE.md").write_text("my own conduct", encoding="utf-8")
    (folder / "memory" / "nota.md").write_text("mine", encoding="utf-8")
    assert run("abrir", FakeUi(folder), FakeClaude()) == 0
    assert (folder / "CLAUDE.md").read_text(encoding="utf-8") == "my own conduct"
    assert (folder / "memory" / "nota.md").read_text(encoding="utf-8") == "mine"
    server = read_json(folder / ".mcp.json")["mcpServers"]["cad"]
    assert server["command"] == INTERPRETER


def test_abrir_refuses_a_folder_that_is_not_a_project(tmp_path: Path) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    ui, fake = FakeUi(folder), FakeClaude()
    assert run("abrir", ui, fake) == 1
    assert ui.kinds() == ["pick", "error"] and "no es un proyecto de CotaForge" in ui.shown[-1][1]
    assert list(folder.iterdir()) == [] and fake.calls == []


def test_an_init_error_is_shown_not_raised(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / ".mcp.json").write_text("[]", encoding="utf-8")
    ui = FakeUi(folder)
    assert run("nuevo", ui, FakeClaude()) == 1
    assert ui.kinds()[-1] == "error"


def test_is_design_space_needs_the_cad_server(tmp_path: Path) -> None:
    assert not launcher.is_design_space(tmp_path)
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"other": {}}}', encoding="utf-8")
    assert not launcher.is_design_space(tmp_path)
    (tmp_path / ".mcp.json").write_text('{"mcpServers": {"cad": {}}}', encoding="utf-8")
    assert launcher.is_design_space(tmp_path)
    (tmp_path / ".mcp.json").write_text("[]", encoding="utf-8")
    assert not launcher.is_design_space(tmp_path)


def test_the_command_with_a_folder_opens_no_window_and_prints_the_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeClaude()
    monkeypatch.setattr(plugin_registry, "run_claude", fake)
    folder = tmp_path / "sin ventanas"
    assert cli.main(["new", "--folder", str(folder)]) == 0
    assert (folder / ".mcp.json").is_file()
    assert "cotaforge@cotaforge" in fake.plugins
    assert "pestaña Code" in capsys.readouterr().out


def test_without_a_display_the_window_failure_is_a_message_not_a_traceback(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_display() -> None:
        raise RuntimeError("no display")

    monkeypatch.setattr(launcher, "TkUi", no_display)
    assert launcher.run("nuevo", None) == 1
    assert "--folder" in capsys.readouterr().err


def test_the_instructions_name_the_product(tmp_path: Path) -> None:
    report = cli.Report(tmp_path)
    assert "los comandos de CotaForge." in launcher.instructions(tmp_path, report, [], CLAUDE)
    other = launcher.instructions(tmp_path, report, [], CLAUDE, product_name="cotaforge para OtherCAD")
    assert "los comandos de cotaforge para OtherCAD." in other
