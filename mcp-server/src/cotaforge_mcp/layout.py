"""Where things are: the installed layout of the Windows installer, a source checkout, and ``install.json``.

The installer puts everything under one folder (the *app folder*)::

    <app>\\python\\            a relocatable CPython with cotaforge and its dependencies installed
    <app>\\marketplace\\       a local Claude Code marketplace: .claude-plugin\\marketplace.json and plugin\\
    <app>\\install.json        what the installer detected (FreeCAD root, claude path, version)

A source checkout has the same shape at its root (``.claude-plugin/marketplace.json`` and ``plugin/``), so both are
found through :func:`find_marketplace_dir`. Nothing here writes anything.
"""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cotaforge import language as language_codes

INSTALL_JSON = "install.json"
ICON = "cotaforge.ico"
MARKETPLACE_FILE = Path(".claude-plugin") / "marketplace.json"
PLUGIN_MANIFEST = Path("plugin") / ".claude-plugin" / "plugin.json"
PLUGIN_NAME = "cotaforge"
MARKETPLACE_NAME = "cotaforge"
PLUGIN_ID = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"


@dataclass(frozen=True)
class Plugin:
    """Which plugin and marketplace the registry commands act on, and the icon of its dialogs: a product built on this
    one (another CAD's) names its own."""

    name: str
    marketplace: str
    icon: str

    @property
    def id(self) -> str:
        return f"{self.name}@{self.marketplace}"


def default_plugin() -> Plugin:
    """The cotaforge plugin (read when asked, so that a program that rebinds the constants above is still obeyed)."""
    return Plugin(PLUGIN_NAME, MARKETPLACE_NAME, ICON)


def is_marketplace_dir(path: Path) -> bool:
    return (path / MARKETPLACE_FILE).is_file() and (path / PLUGIN_MANIFEST).is_file()


def app_dir() -> Path | None:
    """The folder of the installed layout this interpreter belongs to (``<app>``), or ``None`` outside of it.

    The interpreter lives in ``<app>\\python``; ``sys.prefix`` is that folder (``sys.executable`` is not used alone
    because a virtual environment would move it). The folder counts only if it has the installer's marker."""
    for base in (Path(sys.prefix), Path(sys.executable).parent):
        candidate = Path(os.path.abspath(base)).parent
        if (candidate / INSTALL_JSON).is_file() or is_marketplace_dir(candidate / "marketplace"):
            return candidate
    return None


def icon_path(app: Path | None = None, icon: str | None = None) -> Path | None:
    """``<app>\\cotaforge.ico`` (or the file ``icon`` names) when installed, for the launcher's windows; ``None``
    elsewhere."""
    app = app_dir() if app is None else app
    icon = icon or ICON
    if app is None or not (app / icon).is_file():
        return None
    return app / icon


def read_install_info(app: Path | None = None) -> dict[str, Any]:
    """The contents of ``install.json`` ({} if absent or unreadable: it only carries hints)."""
    app = app_dir() if app is None else app
    if app is None:
        return {}
    try:
        data = json.loads((app / INSTALL_JSON).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def install_language(info: dict[str, Any] | None = None) -> str | None:
    """The language the engineer chose at install (``es`` / ``en``), ``None`` when the installer recorded none. It only
    seeds new projects: a project keeps its own (``language`` of ``brief.yaml``)."""
    info = read_install_info() if info is None else info
    return language_codes.normalize(info.get("language"))


def write_install_info(
    app: Path, *, version: str, freecad_root: str = "", claude: str = "", language: str = ""
) -> Path:
    """Write ``<app>\\install.json`` (what the installer detected, and the language the engineer chose). Empty strings
    mean "not found"; an empty or unknown language is left out."""
    data: dict[str, str] = {"version": version, "freecad_root": freecad_root, "claude": claude}
    chosen = language_codes.normalize(language)
    if chosen is not None:
        data["language"] = chosen
    path = app / INSTALL_JSON
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def find_marketplace_dir() -> Path | None:
    """The folder that holds ``.claude-plugin/marketplace.json`` and ``plugin/``: ``<app>\\marketplace`` when
    installed, the repository root in a source checkout."""
    app = app_dir()
    if app is not None and is_marketplace_dir(app / "marketplace"):
        return app / "marketplace"
    checkout = Path(os.path.abspath(__file__)).parents[3]  # <repo>/mcp-server/src/cotaforge_mcp/layout.py
    return checkout if is_marketplace_dir(checkout) else None


def find_plugin_dir() -> Path | None:
    """The plugin folder to load with ``claude --plugin-dir`` (installed layout or source checkout)."""
    marketplace = find_marketplace_dir()
    return None if marketplace is None else marketplace / "plugin"


def console_interpreter(executable: str) -> str:
    """``python.exe`` for ``pythonw.exe``: the launcher shortcuts run without a console, but the interpreter written
    in ``.mcp.json`` and in the hook must be the console one (a stdio server needs real standard streams)."""
    path = Path(executable)
    if path.name.lower() == "pythonw.exe":
        sibling = path.with_name("python.exe")
        if sibling.is_file():
            return str(sibling)
    return executable
