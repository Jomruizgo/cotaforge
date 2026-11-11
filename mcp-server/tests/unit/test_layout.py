"""The installed layout of the Windows installer (``<app>\\python``, ``<app>\\marketplace``, ``<app>\\install.json``)
and the source checkout are both found; nothing outside a temporary folder is read."""

import json
import sys
from pathlib import Path

import pytest

from cotaforge_mcp import layout

REPO = Path(__file__).resolve().parents[3]


def make_app(root: Path) -> Path:
    app = root / "Local Programs" / "cotaforge"  # a space on purpose
    (app / "python").mkdir(parents=True)
    (app / "python" / "python.exe").write_bytes(b"")
    plugin = app / "marketplace" / "plugin" / ".claude-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text("{}", encoding="utf-8")
    (app / "marketplace" / ".claude-plugin").mkdir()
    (app / "marketplace" / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
    (app / "install.json").write_text(
        json.dumps({"version": "0.7.0", "freecad_root": "C:/FreeCAD", "claude": "C:/c/claude.exe"}), encoding="utf-8"
    )
    return app


@pytest.fixture
def installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    app = make_app(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(app / "python"))
    monkeypatch.setattr(sys, "executable", str(app / "python" / "python.exe"))
    return app


def test_the_app_folder_is_found_from_the_interpreter(installed: Path) -> None:
    assert layout.app_dir() == installed


def test_the_installed_marketplace_and_plugin_are_found(installed: Path) -> None:
    assert layout.find_marketplace_dir() == installed / "marketplace"
    assert layout.find_plugin_dir() == installed / "marketplace" / "plugin"


def test_install_json_hints_are_read(installed: Path) -> None:
    info = layout.read_install_info()
    assert info["freecad_root"] == "C:/FreeCAD" and info["claude"] == "C:/c/claude.exe"


def test_a_broken_install_json_gives_no_hints(installed: Path) -> None:
    (installed / "install.json").write_text("{ nope", encoding="utf-8")
    assert layout.read_install_info() == {}
    (installed / "install.json").write_text("[1]", encoding="utf-8")
    assert layout.read_install_info() == {}


def test_install_info_is_written_as_utf8_and_read_back(installed: Path) -> None:
    layout.write_install_info(
        installed, version="9.9.9", freecad_root="C:\\Archivos\\FreeCAD 1.1", claude="C:\\José\\c.exe"
    )
    assert layout.read_install_info() == {
        "version": "9.9.9",
        "freecad_root": "C:\\Archivos\\FreeCAD 1.1",
        "claude": "C:\\José\\c.exe",
    }
    assert "José" in (installed / "install.json").read_text(encoding="utf-8")


def test_a_source_checkout_is_its_own_marketplace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))  # not the installed layout
    monkeypatch.setattr(sys, "executable", str(tmp_path / "venv" / "python"))
    assert layout.app_dir() is None
    assert layout.read_install_info() == {}
    # the checkout may be reached through a substituted drive (subst): same folder, other spelling
    marketplace, plugin = layout.find_marketplace_dir(), layout.find_plugin_dir()
    assert marketplace is not None and marketplace.resolve() == REPO
    assert plugin is not None and plugin.resolve() == REPO / "plugin"


def test_pythonw_is_replaced_by_the_console_interpreter(tmp_path: Path) -> None:
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    assert layout.console_interpreter(str(tmp_path / "pythonw.exe")) == str(tmp_path / "python.exe")
    assert layout.console_interpreter(str(tmp_path / "python.exe")) == str(tmp_path / "python.exe")


def test_pythonw_without_a_sibling_is_left_alone(tmp_path: Path) -> None:
    assert layout.console_interpreter(str(tmp_path / "pythonw.exe")) == str(tmp_path / "pythonw.exe")
