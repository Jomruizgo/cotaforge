"""The ``cotaforge`` command: ``cotaforge init <folder>`` prepares a design space for Claude Code.

Also ``cotaforge plugin register|unregister|status`` (plugin_registry.py), ``cotaforge version`` and the launchers of
the Windows installer's shortcuts, ``cotaforge new`` and ``cotaforge open`` (``nuevo`` and ``abrir`` still work) (launcher.py).

What a plugin cannot carry is written here, with the paths of this machine: the ``.mcp.json`` that starts the ``cad``
server with the interpreter that has the product installed, the permissions and the ``run_native`` safety hook in
``.claude/settings.local.json``, the conduct file (``CLAUDE.md``), an empty ``memory/`` and the language of the project
(``language`` of ``brief.yaml``).

Running it again is safe: the memory is never touched, a ``CLAUDE.md`` nobody edited is brought up to the current
template (the old one is kept beside it), one the user edited is kept (unless ``--force``) and the JSON files are
merged, so keys that are not ours survive. Messages for the engineer follow his language (``--language es|en``, else the
project's, else the one chosen at install, else Spanish); the code is in English.
"""

import argparse
import functools
import json
import os
import re
import shlex
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import cotaforge
from cotaforge import paths
from cotaforge.adapters.freecad.rpc import find_freecad_root
from cotaforge.language import DEFAULT_LANGUAGE, LANGUAGES, normalize, say
from cotaforge.native import hook as kernel_hook

import cotaforge_mcp
from cotaforge_mcp import conduct, design_memory, layout, plugin_registry, register
from cotaforge_mcp.config import ADAPTERS, ENV_ADAPTER, ENV_FREECAD_ROOT, ENV_VISIBLE, ENV_WORKSPACE

SERVER_NAME = "cad"
SERVER_MODULE = "cotaforge_mcp"
HOOK_MODULE = kernel_hook.__name__
RUN_NATIVE_MATCHER = f"mcp__{SERVER_NAME}__run_native"
HOOK_TIMEOUT_SECONDS = 10

TEMPLATE_PARTS = ("data", "design-space")
CLAUDE_MD = "CLAUDE.md"
MCP_JSON = ".mcp.json"
SETTINGS = Path(".claude") / "settings.local.json"
MEMORY_DIR = paths.MEMORY
# Memory files Claude Code loads on its own and that would reach the design space from outside it.
CLAUDE_MD_NAMES = ("CLAUDE.md", "CLAUDE.local.md", str(Path(".claude") / "CLAUDE.md"))

PLUGIN_PLACEHOLDER = "<ruta del plugin cotaforge>"


class InitError(Exception):
    """Something prevents ``init`` from writing; nothing was written when this is raised."""


STATUS_EN = {
    "sin cambios": "unchanged",
    "actualizado": "updated",
    "creado": "created",
    "conservado": "kept",
    "sobrescrito": "overwritten",
}
"""The status words of a report line are written in Spanish; this is how they read in English."""

_language = DEFAULT_LANGUAGE


def use_language(language: str | None) -> str:
    """Speak ``language`` in the error messages of this process (a command is one process); returns the one in use."""
    global _language  # noqa: PLW0603
    _language = normalize(language) or DEFAULT_LANGUAGE
    return _language


def _say(es: str, en: str) -> str:
    return say(_language, es, en)


@dataclass
class Report:
    """What ``init`` did, as lines the engineer can read, in his language."""

    folder: Path
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    language: str = DEFAULT_LANGUAGE

    def say(self, es: str, en: str) -> str:
        return say(self.language, es, en)

    def add(self, status: str, name: str, detail: str = "") -> None:
        status = status if self.language == "es" else STATUS_EN.get(status, status)
        self.lines.append(f"  {status:<12} {name}" + (f" ({detail})" if detail else ""))


# --- shell quoting --------------------------------------------------------------------------------------------------


def shell_path(path: str, *, windows: bool | None = None) -> str:
    """A path as one shell word for ``sh`` and Git Bash (where Claude Code runs hooks on Windows).

    On Windows the separators become ``/`` (accepted by Git Bash and by Windows itself), because a backslash is an
    escape character in a shell; a path with a space or other special characters is single-quoted.
    """
    if windows is None:
        windows = sys.platform == "win32"
    return shlex.quote(path.replace("\\", "/") if windows else path)


SHORT_PATH_WORD = re.compile(r"[A-Za-z0-9_@%+=:,./~-]+")


def windows_short_path(path: str) -> str | None:
    """The 8.3 short form of an existing ``path`` (``GetShortPathNameW``), or ``None`` when there is none (not Windows,
    the file does not exist, or the volume has short names disabled)."""
    if sys.platform != "win32":
        return None
    import ctypes  # noqa: PLC0415

    get_short = ctypes.windll.kernel32.GetShortPathNameW  # type: ignore[attr-defined]
    size = get_short(path, None, 0)
    if not size:
        return None
    buffer = ctypes.create_unicode_buffer(size)
    return buffer.value if get_short(path, buffer, size) else None


def hook_command(
    interpreter: str,
    *,
    windows: bool | None = None,
    short_path: Callable[[str], str | None] | None = None,
    save_methods: Sequence[str] = (),
) -> str:
    """The shell command of the ``run_native`` hook. ``save_methods`` are the calls that save a document in the CAD
    (``Adapter.save_methods``): the hook then knows them as well as FreeCAD's.

    On Windows an interpreter path with a space is replaced by its short form when that exists and has no space, so
    the command is one plain word even if Claude Code runs the hook without Git Bash; otherwise it is quoted for Git
    Bash."""
    if windows is None:
        windows = sys.platform == "win32"
    flag = f" {kernel_hook.SAVE_METHODS_FLAG}{','.join(save_methods)}" if save_methods else ""
    if windows and " " in interpreter:
        short = (short_path or windows_short_path)(interpreter)
        if short and " " not in short:
            word = short.replace("\\", "/")
            if SHORT_PATH_WORD.fullmatch(word):  # "~" is only special at the start of a word: no quotes needed
                return f"{word} -m {HOOK_MODULE}{flag}"
            interpreter = short
    return f"{shell_path(interpreter, windows=windows)} -m {HOOK_MODULE}{flag}"


# --- JSON helpers ---------------------------------------------------------------------------------------------------


def load_json_object(path: Path) -> dict[str, Any]:
    """The JSON object in ``path`` (``{}`` if absent); anything else is an error and the file is never overwritten."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        msg = _say(
            f"{path} no es un JSON válido ({error}); corrígelo o bórralo y vuelve a ejecutar",
            f"{path} is not valid JSON ({error}); fix or delete it and run this again",
        )
        raise InitError(msg) from error
    if not isinstance(data, dict):
        msg = _say(
            f"{path} no contiene un objeto JSON; corrígelo o bórralo y vuelve a ejecutar",
            f"{path} does not hold a JSON object; fix or delete it and run this again",
        )
        raise InitError(msg)
    return data


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def merge(existing: Any, template: Any) -> Any:
    """``template`` laid under ``existing``: objects merge key by key, lists are a union (existing order first) and
    anything else keeps the existing value, so what the user set is never replaced."""
    if isinstance(existing, dict) and isinstance(template, dict):
        merged = dict(existing)
        for key, value in template.items():
            merged[key] = merge(existing[key], value) if key in existing else value
        return merged
    if isinstance(existing, list) and isinstance(template, list):
        return [*existing, *(item for item in template if item not in existing)]
    return existing


def record_file(report: Report, path: Path, root: Path, data: dict[str, Any], detail: str) -> None:
    """Write ``data`` as JSON unless the file already holds exactly that."""
    name = path.relative_to(root).as_posix()
    existed = path.exists()
    if existed and load_json_object(path) == data:
        report.add("sin cambios", name)
        return
    write_text(path, dump_json(data))
    report.add("actualizado" if existed else "creado", name, detail)


# --- the template ---------------------------------------------------------------------------------------------------


def template_root() -> Traversable:
    root = resources.files(SERVER_MODULE)
    for part in TEMPLATE_PARTS:
        root = root / part
    return root


def template_files(root: Traversable, prefix: Path = Path()) -> list[tuple[Path, Traversable]]:
    found: list[tuple[Path, Traversable]] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        relative = prefix / child.name
        if child.is_dir():
            found += template_files(child, relative)
        else:
            found.append((relative, child))
    return found


def keep_conduct(folder: Path, report: Report, source: conduct.Source) -> None:
    """``CLAUDE.md`` exists and its bytes differ from the template (``--force`` not given): replace it when it is an
    old template nobody edited, else keep it and leave the current template beside it (``conduct.refresh``)."""
    outcome = conduct.refresh(folder, source)
    if outcome.state is conduct.State.REFRESHED and outcome.backup is not None:
        report.add(
            "actualizado",
            CLAUDE_MD,
            report.say(
                f"era una plantilla anterior sin editar; la copia queda en {outcome.backup.name}",
                f"it was an older template nobody edited; the copy is in {outcome.backup.name}",
            ),
        )
    elif outcome.state is conduct.State.EDITED and outcome.proposal is not None:
        where = (
            report.say(
                f"la plantilla actual está en {outcome.proposal.name} para que el agente la integre, o usa --force",
                f"the current template is in {outcome.proposal.name} for the agent to merge, or use --force",
            )
            if outcome.proposal.exists()
            else report.say(
                "ya viste la plantilla actual y la quitaste; usa --force si quieres reemplazarlo",
                "you already saw the current template and removed it; use --force if you want to replace the file",
            )
        )
        report.add("conservado", CLAUDE_MD, report.say(f"lo editaste; {where}", f"you edited it; {where}"))
    else:
        report.add("sin cambios", CLAUDE_MD)


def copy_template(folder: Path, report: Report, *, force: bool) -> None:
    """Copy the design-space template without ever replacing what is there, except ``CLAUDE.md``: under ``--force``, and
    when it is an old template nobody edited (``keep_conduct``).

    The settings are merged separately and ``memory/`` is only created when neither it nor an old ``memoria/`` exists."""
    memory_existed = (folder / MEMORY_DIR).exists() or (folder / (paths.legacy_name(MEMORY_DIR) or "")).exists()
    for relative, source in template_files(template_root()):
        target = folder / relative
        name = relative.as_posix()
        if relative == SETTINGS:
            continue
        if relative.parts[0] == MEMORY_DIR and memory_existed:
            continue
        old_name = paths.legacy_name(relative.parts[0])
        if old_name and (folder / old_name).exists() and not (folder / relative.parts[0]).exists():
            continue  # an old project keeps the Spanish name it has
        content = source.read_bytes()
        if relative == Path(paths.ASSUMPTIONS) and report.language == "es":
            content = register.SPANISH.encode("utf-8")  # the register is the engineer's text: in his language
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            report.add("creado", name)
        elif target.read_bytes() == content:
            report.add("sin cambios", name)
        elif relative.name == CLAUDE_MD and force:
            target.write_bytes(content)
            report.add("sobrescrito", name, "--force")
        elif relative == Path(CLAUDE_MD):
            keep_conduct(folder, report, conduct.product_source())
        else:
            report.add("conservado", name)
    if memory_existed:
        report.add("conservado", f"{MEMORY_DIR}/", report.say("ya existe; no se toca", "it already exists; left alone"))


# --- .mcp.json ------------------------------------------------------------------------------------------------------


def server_env(
    folder: Path, adapter: str, freecad_root: Path | None, visible: bool | None, existing: dict[str, str]
) -> dict[str, str]:
    env = dict(existing)
    env[ENV_ADAPTER] = adapter
    env[ENV_WORKSPACE] = str(folder)
    if freecad_root is not None:
        env[ENV_FREECAD_ROOT] = str(freecad_root)
    if visible is True:
        env[ENV_VISIBLE] = "1"
    elif visible is False:
        env.pop(ENV_VISIBLE, None)
    return env


def other_server_module(entry: dict[str, Any]) -> str | None:
    """The module of a cotaforge server for ANOTHER CAD that ``entry`` starts (``cotaforge_othercad``), else None."""
    args = entry.get("args")
    if not isinstance(args, list) or "-m" not in args or args.index("-m") + 1 >= len(args):
        return None
    module = str(args[args.index("-m") + 1])
    return module if module.startswith("cotaforge_") and module != SERVER_MODULE else None


def build_mcp_json(
    existing: dict[str, Any],
    folder: Path,
    interpreter: str,
    adapter: str,
    freecad_root: Path | None,
    visible: bool | None,
) -> dict[str, Any]:
    servers = existing.get("mcpServers", {})
    if not isinstance(servers, dict):
        msg = _say(
            f"{MCP_JSON}: 'mcpServers' no es un objeto; corrígelo o bórralo y vuelve a ejecutar",
            f"{MCP_JSON}: 'mcpServers' is not an object; fix or delete it and run this again",
        )
        raise InitError(msg)
    entry = servers.get(SERVER_NAME, {})
    if not isinstance(entry, dict) or not isinstance(entry.get("env", {}), dict):
        msg = _say(
            f"{MCP_JSON}: el servidor '{SERVER_NAME}' no tiene la forma esperada; corrígelo o bórralo",
            f"{MCP_JSON}: the '{SERVER_NAME}' server does not have the expected shape; fix or delete it",
        )
        raise InitError(msg)
    other = other_server_module(entry)
    if other is not None:
        msg = _say(
            f"{MCP_JSON}: el servidor '{SERVER_NAME}' de esta carpeta ya arranca {other}, de otro software de CAD; "
            "una carpeta no cambia de software de CAD: elige otra carpeta",
            f"{MCP_JSON}: the '{SERVER_NAME}' server of this folder already starts {other}, of another CAD software; "
            "a folder does not switch CAD software: choose another folder",
        )
        raise InitError(msg)
    entry = {
        **entry,
        "command": interpreter,
        "args": ["-m", SERVER_MODULE],
        "env": server_env(folder, adapter, freecad_root, visible, entry.get("env", {})),
    }
    return {**existing, "mcpServers": {**servers, SERVER_NAME: entry}}


# --- settings.local.json --------------------------------------------------------------------------------------------


def upsert_hook(settings: dict[str, Any], command: str) -> None:
    """Put the ``run_native`` hook with ``command`` in the settings, replacing a previous one of ours (another
    interpreter, an older path) and leaving every other hook alone."""
    hooks = settings.setdefault("hooks", {})
    entries = hooks.setdefault("PreToolUse", []) if isinstance(hooks, dict) else None
    if not isinstance(entries, list):
        msg = _say(
            f"{SETTINGS.as_posix()}: 'hooks.PreToolUse' no es una lista; corrígelo o bórralo",
            f"{SETTINGS.as_posix()}: 'hooks.PreToolUse' is not a list; fix or delete it",
        )
        raise InitError(msg)
    handler = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("matcher") != RUN_NATIVE_MATCHER:
            continue
        handlers = entry.get("hooks")
        if not isinstance(handlers, list):
            continue
        for index, existing in enumerate(handlers):
            if isinstance(existing, dict) and HOOK_MODULE in str(existing.get("command", "")):
                handlers[index] = {**existing, **handler}
                return
    entries.append({"matcher": RUN_NATIVE_MATCHER, "hooks": [handler]})


def inherited_claude_md(folder: Path, home: Path) -> list[str]:
    """Absolute paths of the ``CLAUDE.md`` files that exist above ``folder`` and in ``~/.claude``.

    Only existence is checked: their content is never read."""
    directories = [*folder.parents, home / ".claude"]
    found: dict[str, None] = {}
    for directory in directories:
        for name in CLAUDE_MD_NAMES:
            candidate = directory / name
            if candidate.is_file():
                # Forward slashes: the exclusion patterns are globs, where a backslash is an escape character.
                found[candidate.as_posix()] = None
    return list(found)


def build_settings(
    existing: dict[str, Any], template: dict[str, Any], excludes: list[str], command: str
) -> dict[str, Any]:
    settings = merge(existing, template)
    settings["claudeMdExcludes"] = merge(settings.get("claudeMdExcludes", []), excludes)
    upsert_hook(settings, command)
    return settings


# --- init -----------------------------------------------------------------------------------------------------------


def init_space(
    folder: Path,
    *,
    interpreter: str,
    adapter: str = "freecad",
    freecad_root: Path | None = None,
    visible: bool | None = None,
    force: bool = False,
    home: Path | None = None,
    language: str | None = None,
) -> Report:
    """Create or refresh the design space in ``folder``. Raises :class:`InitError` before writing anything when an
    existing JSON file cannot be merged.

    ``language`` (``es`` / ``en``) is the engineer's: stated, it is written to the project (``brief.yaml``) and spoken
    in the report; not stated, the project keeps the one it has, a new project takes the one chosen at install and an
    older project that never stated one stays as it was (Spanish)."""
    folder = Path(os.path.abspath(folder.expanduser()))  # not resolve(): keep a subst drive as the user typed it
    new = is_new_project(folder)
    chosen = choose_language(folder, language, new)
    use_language(chosen)
    if adapter not in ADAPTERS:
        msg = _say(
            f"adaptador desconocido {adapter!r}; usa uno de: {', '.join(ADAPTERS)}",
            f"unknown adapter {adapter!r}; use one of: {', '.join(ADAPTERS)}",
        )
        raise InitError(msg)
    if folder.exists() and not folder.is_dir():
        msg = _say(f"{folder} existe y no es una carpeta", f"{folder} exists and is not a folder")
        raise InitError(msg)
    home = Path.home() if home is None else home
    report = Report(folder, language=chosen)

    explicit = None if freecad_root is None else Path(os.path.abspath(freecad_root.expanduser()))
    discovered = find_freecad_root(explicit) if adapter == "freecad" else None
    root = explicit or discovered
    if adapter == "freecad" and discovered is None:
        report.warnings.append(
            report.say(
                "no se encontró FreeCAD (falta el ejecutable headless); indica la carpeta con --freecad-root "
                "o define COTAFORGE_FREECAD_ROOT antes de usar el servidor",
                "FreeCAD was not found (the headless executable is missing); give its folder with --freecad-root "
                "or set COTAFORGE_FREECAD_ROOT before using the server",
            )
        )

    mcp_path, settings_path = folder / MCP_JSON, folder / SETTINGS
    mcp_data = build_mcp_json(load_json_object(mcp_path), folder, interpreter, adapter, root, visible)
    template_settings = json.loads((template_root() / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    excludes = inherited_claude_md(folder, home)
    settings_data = build_settings(
        load_json_object(settings_path), template_settings, excludes, hook_command(interpreter)
    )

    folder.mkdir(parents=True, exist_ok=True)
    copy_template(folder, report, force=force)
    record_file(
        report,
        mcp_path,
        folder,
        mcp_data,
        report.say(f"servidor {SERVER_NAME}, adaptador {adapter}", f"{SERVER_NAME} server, {adapter} adapter"),
    )
    record_file(
        report,
        settings_path,
        folder,
        settings_data,
        report.say(
            f"hook de run_native, {len(excludes)} CLAUDE.md excluidos",
            f"run_native hook, {len(excludes)} CLAUDE.md excluded",
        ),
    )
    if language is not None or new:  # an older project that stated none keeps the language it always had
        state = design_memory.write_language(folder, chosen)
        if state != "unchanged":
            report.add(
                {"created": "creado", "updated": "actualizado"}[state],
                design_memory.BRIEF_FILE,
                report.say(f"idioma: {chosen}", f"language: {chosen}"),
            )
    return report


def choose_language(folder: Path, explicit: str | None = None, new: bool | None = None) -> str:
    """The language of the report and of the project: the one asked for, else the one the project states, else (a new
    project) the one chosen at install, else Spanish."""
    asked = normalize(explicit)
    if asked is not None:
        return asked
    stated = design_memory.stated_language(folder)
    if stated is not None:
        return stated
    if is_new_project(folder) if new is None else new:
        return layout.install_language() or DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE


def is_new_project(folder: Path) -> bool:
    """A folder none of whose design-space files exist yet: there is no older project to keep as it was."""
    return not any((folder / name).exists() for name in (MCP_JSON, CLAUDE_MD, design_memory.BRIEF_FILE))


def find_plugin_dir() -> Path | None:
    """The ``plugin/`` folder of the installed layout or of the source checkout this package runs from, if any (it is
    not part of the wheel)."""
    return layout.find_plugin_dir()


def quote_for_display(path: str) -> str:
    return f'"{path}"' if " " in path else path


def format_report(report: Report, plugin_dir: str, *, registered: bool = False, command: str = "cotaforge") -> str:
    s = report.say
    out = [s(f"Espacio de diseño listo en {report.folder}", f"Design space ready in {report.folder}"), *report.lines]
    out += [f"{s('aviso', 'warning')}: {warning}" for warning in report.warnings]
    if registered:
        out += [
            "",
            s(
                "Siguiente paso: el plugin ya está registrado en Claude Code. Abre esa carpeta:",
                "Next step: the plugin is already registered in Claude Code. Open that folder:",
            ),
            s(
                "  - en la app de escritorio de Claude: pestaña Code, y elige la carpeta; o",
                "  - in the Claude desktop app: the Code tab, then choose the folder; or",
            ),
            s("  - en una terminal:", "  - in a terminal:"),
            f"      cd {quote_for_display(str(report.folder))}",
            "      claude",
        ]
    else:
        out += [
            "",
            s(
                "Siguiente paso: abre Claude Code en esa carpeta con el plugin cargado:",
                "Next step: open Claude Code in that folder with the plugin loaded:",
            ),
            f"  cd {quote_for_display(str(report.folder))}",
            f"  claude --plugin-dir {quote_for_display(plugin_dir)}",
            s(
                f"Para no tener que pasar --plugin-dir, registra el plugin una vez con: {command} plugin register",
                f"To avoid passing --plugin-dir, register the plugin once with: {command} plugin register",
            ),
        ]
    return "\n".join(out)


def package_versions() -> list[str]:
    return [
        f"cotaforge-kernel {cotaforge.__version__}",
        f"cotaforge-mcp {cotaforge_mcp.__version__}",
        f"python {sys.version.split()[0]} ({sys.executable})",
    ]


def record_install(app: Path | None, version: str, freecad_root: str, claude: str, language: str = "") -> int:
    """``cotaforge install-info``: the installer records what it detected, and the language the engineer chose, next to
    the program."""
    use_language(language)
    if app is None or not app.is_dir():
        print(
            "error: "
            + _say(
                "no se encontró la carpeta de instalación de CotaForge", "the CotaForge install folder was not found"
            ),
            file=sys.stderr,
        )
        return 1
    layout.write_install_info(app, version=version, freecad_root=freecad_root, claude=claude, language=language)
    print(_say(f"CotaForge {version} instalado en {app}", f"CotaForge {version} installed in {app}"))
    return 0


def build_parser(language: str | None = None) -> argparse.ArgumentParser:
    """The command line. Its help is in ``language`` (default: the one chosen at install, else Spanish)."""
    s = functools.partial(say, normalize(language) or layout.install_language() or DEFAULT_LANGUAGE)
    parser = argparse.ArgumentParser(prog="cotaforge", description=s("Herramientas de CotaForge.", "CotaForge tools."))
    parser.add_argument(
        "--version", action="store_true", help=s("muestra las versiones instaladas", "show the installed versions")
    )
    commands = parser.add_subparsers(dest="command")
    init = commands.add_parser(
        "init",
        help=s(
            "crea o actualiza un espacio de diseño para Claude Code", "create or update a design space for Claude Code"
        ),
    )
    init.add_argument(
        "folder",
        type=Path,
        help=s("carpeta del espacio de diseño (se crea si no existe)", "design space folder (created if missing)"),
    )
    init.add_argument(
        "--adapter",
        choices=ADAPTERS,
        default="freecad",
        help=s("software de CAD (por defecto freecad)", "CAD software (default freecad)"),
    )
    init.add_argument(
        "--freecad-root",
        type=Path,
        help=s(
            "carpeta de instalación de FreeCAD (por defecto se busca sola)",
            "FreeCAD install folder (found on its own by default)",
        ),
    )
    init.add_argument(
        "--visible",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=s("abrir FreeCAD con su ventana (COTAFORGE_VISIBLE)", "open FreeCAD with its window (COTAFORGE_VISIBLE)"),
    )
    init.add_argument(
        "--force",
        action="store_true",
        help=s("reemplazar CLAUDE.md aunque lo hayas editado", "replace CLAUDE.md even if you edited it"),
    )
    init.add_argument(
        "--plugin-dir",
        type=Path,
        help=s("carpeta del plugin, solo para mostrar el siguiente paso", "plugin folder, only to show the next step"),
    )
    init.add_argument(
        "--language",
        choices=LANGUAGES,
        default=None,
        help=s(
            "idioma del ingeniero en este proyecto (se guarda en brief.yaml); por defecto el del proyecto, "
            "el elegido al instalar o es",
            "the engineer's language in this project (kept in brief.yaml); by default the project's, "
            "the one chosen at install, or es",
        ),
    )
    plugin = commands.add_parser(
        "plugin",
        help=s(
            "registra el plugin en Claude Code (terminal y app de escritorio)",
            "register the plugin in Claude Code (terminal and desktop app)",
        ),
    )
    plugin.add_argument(
        "action",
        choices=sorted(plugin_registry.COMMANDS),
        help=s(
            "register: lo instala; unregister: lo quita; status: dice si está registrado",
            "register: install it; unregister: remove it; status: say whether it is registered",
        ),
    )
    commands.add_parser("version", help=s("muestra las versiones instaladas", "show the installed versions"))
    record = commands.add_parser("install-info", help=argparse.SUPPRESS)  # run by the Windows installer
    record.add_argument("--version", dest="installed_version", required=True)
    record.add_argument("--freecad-root", default="")
    record.add_argument("--claude", default="")
    record.add_argument("--language", default="")
    record.add_argument("--app-dir", type=Path)
    for name, alias, text in (
        (
            "new",
            "nuevo",
            s(
                "pide una carpeta y crea en ella un proyecto nuevo (acceso directo del menú Inicio)",
                "ask for a folder and create a new project in it (Start menu shortcut)",
            ),
        ),
        (
            "open",
            "abrir",
            s(
                "pide la carpeta de un proyecto existente y lo refresca (acceso directo del menú Inicio)",
                "ask for the folder of an existing project and refresh it (Start menu shortcut)",
            ),
        ),
    ):
        launcher = commands.add_parser(name, aliases=[alias], help=text)
        launcher.add_argument(
            "--folder",
            "--carpeta",
            dest="folder",
            type=Path,
            help=s("usa esta carpeta y no abre ventanas", "use this folder, no windows"),
        )
    return parser


def _requested_language(argv: Sequence[str] | None) -> str | None:
    """``--language`` as typed, before the parser exists, so that the help is in it too."""
    words = list(sys.argv[1:] if argv is None else argv)
    for index, word in enumerate(words):
        if word == "--language" and index + 1 < len(words):
            return words[index + 1]
        if word.startswith("--language="):
            return word.partition("=")[2]
    return None


def main(argv: Sequence[str] | None = None) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")  # a console that cannot print an accent must not break the summary
    parser = build_parser(_requested_language(argv))
    args = parser.parse_args(argv)
    if args.version or args.command == "version":
        print("\n".join(package_versions()))
        return 0
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "plugin":
        return plugin_registry.run_command(args.action)
    if args.command == "install-info":
        return record_install(
            args.app_dir or layout.app_dir(), args.installed_version, args.freecad_root, args.claude, args.language
        )
    if args.command in ("new", "open", "nuevo", "abrir"):
        from cotaforge_mcp import launcher  # noqa: PLC0415 - it imports this module

        return launcher.run(args.command, args.folder)
    try:
        report = init_space(
            args.folder,
            interpreter=layout.console_interpreter(sys.executable),
            adapter=args.adapter,
            freecad_root=args.freecad_root,
            visible=args.visible,
            force=args.force,
            language=args.language,
        )
    except InitError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    plugin_registry.use_language(report.language)
    registered = args.plugin_dir is None and plugin_registry.is_registered()
    plugin = args.plugin_dir or find_plugin_dir()
    print(format_report(report, str(plugin) if plugin is not None else PLUGIN_PLACEHOLDER, registered=registered))
    return 0


if __name__ == "__main__":
    sys.exit(main())
