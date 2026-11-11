"""``cotaforge plugin register | unregister | status``: put the cotaforge plugin in Claude Code's user configuration.

It drives the ``claude`` command (``claude plugin marketplace add`` + ``claude plugin install``), so the plugin shows
up in the terminal and in the Code tab of the desktop app. The marketplace is a local folder that Claude Code loads in
place: replacing its files (an update) is picked up at the next session. The subprocess runner is injectable, so the
tests use a fake ``claude``. Messages for the user are in Spanish; nothing here raises to the caller.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cotaforge.language import DEFAULT_LANGUAGE, normalize, say

from cotaforge_mcp import layout

CLAUDE_TIMEOUT_SECONDS = 180

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]

_language: str | None = None


def use_language(language: str | None) -> None:
    """Speak ``language`` (``es`` / ``en``) from now on in this process; ``None``: the one chosen at install. A command
    is one process, so a setting of the process is enough."""
    global _language  # noqa: PLW0603
    _language = normalize(language)


def _say(es: str, en: str) -> str:
    return say(_language or layout.install_language() or DEFAULT_LANGUAGE, es, en)


def claude_missing() -> str:
    return _say(
        "no se encontró Claude Code (el comando 'claude'): instálalo desde https://claude.com/product/claude-code, "
        "inicia sesión y vuelve a ejecutar este comando",
        "Claude Code was not found (the 'claude' command): install it from https://claude.com/product/claude-code, "
        "sign in and run this command again",
    )


class PluginError(Exception):
    """``claude`` could not be run or answered something unexpected; the message is for the user."""


@dataclass
class Outcome:
    """What a plugin command did: ``ok`` is the exit status, ``lines`` are for the user."""

    ok: bool
    lines: list[str] = field(default_factory=list)
    changed: bool = False


@dataclass(frozen=True)
class PluginState:
    marketplace_path: str | None  # where the marketplace named cotaforge points; None if it is not configured
    plugin_installed: bool

    @property
    def marketplace_configured(self) -> bool:
        return self.marketplace_path is not None


# --- finding and running claude -------------------------------------------------------------------------------------


def find_claude(
    *,
    which: Callable[[str], str | None] = shutil.which,
    home: Path | None = None,
    info: Mapping[str, Any] | None = None,
) -> Path | None:
    """``claude`` on the PATH, then the native installer's folder (``~/.local/bin``), then the path the installer
    recorded in ``install.json``."""
    found = which("claude")
    if found:
        return Path(found)
    home = Path.home() if home is None else home
    info = layout.read_install_info() if info is None else info
    candidates = [home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude"]
    recorded = info.get("claude")
    if isinstance(recorded, str) and recorded:
        candidates.append(Path(recorded))
    return next((path for path in candidates if path.is_file()), None)


def run_claude(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """Run ``argv`` (the first item is the ``claude`` executable) without a console window and without stdin."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        return subprocess.run(  # noqa: S603 - argv is built here, never from user text
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        msg = f"no se pudo ejecutar {argv[0]}: {error}"
        raise PluginError(msg) from error


def last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def claude_json(runner: Runner | None, claude: Path, *args: str) -> list[dict[str, Any]]:
    result = (runner or run_claude)([str(claude), "plugin", *args, "--json"])
    if result.returncode != 0:
        msg = _say(
            f"'claude plugin {' '.join(args)}' falló: {last_line(result.stderr or result.stdout)}",
            f"'claude plugin {' '.join(args)}' failed: {last_line(result.stderr or result.stdout)}",
        )
        raise PluginError(msg)
    try:
        data = json.loads(result.stdout)
    except ValueError as error:
        msg = _say(
            f"'claude plugin {' '.join(args)}' no devolvió JSON; ¿una versión de Claude Code demasiado antigua?",
            f"'claude plugin {' '.join(args)}' did not return JSON; is your Claude Code too old?",
        )
        raise PluginError(msg) from error
    if not isinstance(data, list):
        msg = _say(
            f"'claude plugin {' '.join(args)}' devolvió algo inesperado",
            f"'claude plugin {' '.join(args)}' returned something unexpected",
        )
        raise PluginError(msg)
    return [item for item in data if isinstance(item, dict)]


def same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def read_state(runner: Runner | None, claude: Path, plugin: layout.Plugin | None = None) -> PluginState:
    plugin = plugin or layout.default_plugin()
    marketplaces = claude_json(runner, claude, "marketplace", "list")
    plugins = claude_json(runner, claude, "list")
    path: str | None = None
    for item in marketplaces:
        if item.get("name") == plugin.marketplace:
            location = item.get("path") or item.get("installLocation") or item.get("repo") or item.get("url") or ""
            path = str(location)
    installed = any(item.get("id") == plugin.id for item in plugins)
    return PluginState(path, installed)


def registered_here(state: PluginState, marketplace: Path | None) -> bool:
    """The plugin is installed and its marketplace is the folder of this install."""
    return (
        state.plugin_installed
        and marketplace is not None
        and state.marketplace_path is not None
        and same_path(state.marketplace_path, str(marketplace))
    )


def is_registered(
    runner: Runner | None = None,
    *,
    claude: Path | None = None,
    marketplace: Path | None = None,
    plugin: layout.Plugin | None = None,
) -> bool:
    """True when the plugin is installed from the local marketplace of this install. Never raises: any problem
    (``claude`` missing or failing) means "not registered"."""
    claude = find_claude() if claude is None else claude
    marketplace = layout.find_marketplace_dir() if marketplace is None else marketplace
    if claude is None or marketplace is None:
        return False
    try:
        state = read_state(runner, claude, plugin)
    except PluginError:
        return False
    return registered_here(state, marketplace)


# --- the commands ---------------------------------------------------------------------------------------------------


def step(runner: Runner | None, claude: Path, *args: str) -> None:
    result = (runner or run_claude)([str(claude), "plugin", *args])
    if result.returncode != 0:
        msg = _say(
            f"'claude plugin {' '.join(args)}' falló: {last_line(result.stderr or result.stdout)}",
            f"'claude plugin {' '.join(args)}' failed: {last_line(result.stderr or result.stdout)}",
        )
        raise PluginError(msg)


def register(
    runner: Runner | None = None,
    *,
    claude: Path | None = None,
    marketplace: Path | None = None,
    plugin: layout.Plugin | None = None,
) -> Outcome:
    plugin = plugin or layout.default_plugin()
    claude = find_claude() if claude is None else claude
    if claude is None:
        return Outcome(False, [f"error: {claude_missing()}"])
    marketplace = layout.find_marketplace_dir() if marketplace is None else marketplace
    if marketplace is None:
        return Outcome(
            False,
            [
                "error: "
                + _say(
                    "no se encontró la carpeta del plugin de CotaForge junto a esta instalación",
                    "the CotaForge plugin folder was not found next to this installation",
                )
            ],
        )
    try:
        state = read_state(runner, claude, plugin)
        if registered_here(state, marketplace):
            return Outcome(
                True,
                [
                    _say(
                        f"El plugin {plugin.id} ya estaba registrado desde {marketplace}.",
                        f"The plugin {plugin.id} was already registered from {marketplace}.",
                    )
                ],
            )
        lines = []
        if state.marketplace_path and not same_path(state.marketplace_path, str(marketplace)):
            lines.append(
                _say(
                    f"El marketplace {plugin.marketplace} apuntaba a {state.marketplace_path}; ahora apunta aquí.",
                    f"The marketplace {plugin.marketplace} pointed to {state.marketplace_path}; it now points here.",
                )
            )
        step(runner, claude, "marketplace", "add", str(marketplace))
        step(runner, claude, "install", plugin.id, "--scope", "user")
    except PluginError as error:
        return Outcome(False, [f"error: {error}"])
    lines.append(
        _say(
            f"Plugin {plugin.id} registrado desde {marketplace}. Está en la terminal y en la pestaña Code.",
            f"Plugin {plugin.id} registered from {marketplace}. It is in the terminal and in the Code tab.",
        )
    )
    return Outcome(True, lines, changed=True)


def unregister(
    runner: Runner | None = None,
    *,
    claude: Path | None = None,
    marketplace: Path | None = None,
    plugin: layout.Plugin | None = None,
) -> Outcome:
    plugin = plugin or layout.default_plugin()
    claude = find_claude() if claude is None else claude
    if claude is None:
        return Outcome(False, [f"error: {claude_missing()}"])
    marketplace = layout.find_marketplace_dir() if marketplace is None else marketplace
    try:
        state = read_state(runner, claude, plugin)
        elsewhere = (
            state.marketplace_path is not None
            and marketplace is not None
            and not same_path(state.marketplace_path, str(marketplace))
            and Path(state.marketplace_path).exists()
        )
        if elsewhere:
            return Outcome(
                True,
                [
                    _say(
                        f"El marketplace {plugin.marketplace} apunta a {state.marketplace_path}, no a esta "
                        "instalación: no se toca.",
                        f"The marketplace {plugin.marketplace} points to {state.marketplace_path}, not to this "
                        "installation: left alone.",
                    )
                ],
            )
        if not state.plugin_installed and not state.marketplace_configured:
            return Outcome(
                True,
                [_say(f"El plugin {plugin.id} no estaba registrado.", f"The plugin {plugin.id} was not registered.")],
            )
        if state.plugin_installed:
            step(runner, claude, "uninstall", plugin.id, "--scope", "user")
        if state.marketplace_configured:
            step(runner, claude, "marketplace", "remove", plugin.marketplace)
    except PluginError as error:
        return Outcome(False, [f"error: {error}"])
    removed = _say(
        f"Plugin {plugin.id} y su marketplace quitados de Claude Code.",
        f"Plugin {plugin.id} and its marketplace removed from Claude Code.",
    )
    return Outcome(True, [removed], changed=True)


def status(
    runner: Runner | None = None,
    *,
    claude: Path | None = None,
    marketplace: Path | None = None,
    plugin: layout.Plugin | None = None,
    command: str = "cotaforge",
) -> Outcome:
    plugin = plugin or layout.default_plugin()
    claude = find_claude() if claude is None else claude
    marketplace = layout.find_marketplace_dir() if marketplace is None else marketplace
    lines = [
        f"claude:       {claude if claude is not None else _say('no encontrado', 'not found')}",
        f"marketplace:  {marketplace if marketplace is not None else _say('no encontrado', 'not found')}",
    ]
    if claude is None:
        return Outcome(False, [*lines, f"error: {claude_missing()}"])
    try:
        state = read_state(runner, claude, plugin)
    except PluginError as error:
        return Outcome(False, [*lines, f"error: {error}"])
    registered = registered_here(state, marketplace)
    lines += [
        f"{_say('registrado en', 'registered at')}: {state.marketplace_path or 'no'}",
        f"plugin {plugin.id}: "
        + (_say("instalado", "installed") if state.plugin_installed else _say("no instalado", "not installed")),
        _say("estado: ", "state: ")
        + (
            _say("registrado", "registered")
            if registered
            else _say(
                f"NO registrado (usa: {command} plugin register)", f"NOT registered (use: {command} plugin register)"
            )
        ),
    ]
    return Outcome(registered, lines)


COMMANDS: dict[str, Callable[..., Outcome]] = {"register": register, "unregister": unregister, "status": status}


def run_command(name: str, runner: Runner | None = None) -> int:
    """Run one plugin command and print its lines (errors to stderr). Returns the exit status."""
    outcome = COMMANDS[name](runner)
    for line in outcome.lines:
        print(line, file=sys.stderr if line.startswith("error:") else sys.stdout)
    return 0 if outcome.ok else 1
