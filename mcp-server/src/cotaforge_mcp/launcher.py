"""``cotaforge new`` and ``cotaforge open``: what the Start-menu shortcuts run.

The shortcuts call ``pythonw.exe -m cotaforge_mcp.cli <command>``, so there is no console window.

They ask for a folder, prepare the design space with the same code as ``cotaforge init`` and say exactly how to open it
in Claude Code. All the logic works against a small :class:`Ui`; the real one (:class:`TkUi`, tkinter) only draws the
dialogs, and :class:`ConsoleUi` answers without any window so the flow can be tested and scripted. Messages for the
engineer follow his language: the project's (``brief.yaml``) once the folder is known, before that the one chosen at
install.
"""

import functools
import json
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from cotaforge.language import DEFAULT_LANGUAGE, say

from cotaforge_mcp import layout, plugin_registry
from cotaforge_mcp.cli import InitError, Report, choose_language, init_space

TITLE = "CotaForge"


class Ui(Protocol):
    def pick_folder(self, title: str) -> Path | None: ...
    def info(self, text: str) -> None: ...
    def error(self, text: str) -> None: ...
    def confirm(self, text: str) -> bool:
        """A yes/no question whose "no" stops the flow."""
        ...

    def ask_terminal(self, text: str, question: str) -> bool:
        """The final message, with the question "open it also in a terminal?"."""
        ...


class TkUi:
    """The real dialogs. ``tkinter`` is imported here so that nothing else needs a display."""

    def __init__(self, title: str = TITLE, icon: str | None = None) -> None:
        """``title`` heads every dialog; ``icon`` is the file of the app folder to use instead of cotaforge's."""
        import tkinter  # noqa: PLC0415

        self._tk = tkinter
        self._title = title
        self._root = tkinter.Tk()
        self._root.withdraw()
        icon_file = layout.icon_path(icon=icon)
        if icon_file is not None:
            try:
                self._root.iconbitmap(default=str(icon_file))  # every dialog of this root, not tkinter's feather
            except tkinter.TclError:
                pass  # a missing or unreadable icon never stops the launcher
        self._root.attributes("-topmost", True)

    def pick_folder(self, title: str) -> Path | None:
        from tkinter import filedialog  # noqa: PLC0415

        chosen = filedialog.askdirectory(parent=self._root, title=title, mustexist=False)
        return Path(chosen) if chosen else None

    def info(self, text: str) -> None:
        from tkinter import messagebox  # noqa: PLC0415

        messagebox.showinfo(self._title, text, parent=self._root)

    def error(self, text: str) -> None:
        from tkinter import messagebox  # noqa: PLC0415

        messagebox.showerror(self._title, text, parent=self._root)

    def confirm(self, text: str) -> bool:
        from tkinter import messagebox  # noqa: PLC0415

        return bool(messagebox.askyesno(self._title, text, parent=self._root))

    def ask_terminal(self, text: str, question: str) -> bool:
        return self.confirm(f"{text}\n\n{question}")

    def close(self) -> None:
        self._root.destroy()


class ConsoleUi:
    """No windows: the folder comes from the command line, messages are printed, a terminal is never opened."""

    def __init__(self, folder: Path | None) -> None:
        self.folder = folder

    def pick_folder(self, title: str) -> Path | None:
        return self.folder

    def info(self, text: str) -> None:
        print(text)

    def error(self, text: str) -> None:
        print(f"error: {text}", file=sys.stderr)

    def confirm(self, text: str) -> bool:
        return True  # the folder was named on the command line: that is the confirmation

    def ask_terminal(self, text: str, question: str) -> bool:
        print(text)
        return False


# --- pieces ---------------------------------------------------------------------------------------------------------


def is_design_space(folder: Path) -> bool:
    """A folder that ``cotaforge init`` prepared: its ``.mcp.json`` declares the ``cad`` server."""
    try:
        servers = json.loads((folder / ".mcp.json").read_text(encoding="utf-8")).get("mcpServers", {})
    except (OSError, ValueError, AttributeError):
        return False
    return isinstance(servers, dict) and "cad" in servers


def installed_freecad_root(info: dict[str, object]) -> Path | None:
    """The FreeCAD folder the installer recorded, if it still has a folder."""
    recorded = info.get("freecad_root")
    if isinstance(recorded, str) and recorded and Path(recorded).is_dir():
        return Path(recorded)
    return None


def open_terminal(folder: Path, claude: Path, popen: Callable[..., object] = subprocess.Popen) -> None:
    """Start ``claude`` in ``folder`` in a console window of its own."""
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0
    popen([str(claude)], cwd=str(folder), creationflags=flags)


def instructions(
    folder: Path, report: Report, registration: list[str], claude: Path | None, product_name: str = "CotaForge"
) -> str:
    s = report.say
    lines = [s(f"El proyecto está listo en:\n{folder}", f"The project is ready in:\n{folder}")]
    extra = [*(f"{s('Aviso', 'Warning')}: {warning}" for warning in report.warnings), *registration]
    if extra:
        lines += ["", *extra]
    lines += [
        "",
        s("Para trabajar en él, en la app de escritorio de Claude:", "To work on it, in the Claude desktop app:"),
        s("  1. Abre la pestaña Code.", "  1. Open the Code tab."),
        s("  2. Elige esa carpeta como carpeta de trabajo.", "  2. Choose that folder as the working folder."),
        s(
            f"  3. Escribe / para ver los comandos de {product_name}.",
            f"  3. Type / to see the {product_name} commands.",
        ),
    ]
    if claude is None:
        lines += [
            "",
            s(
                "No encontré Claude Code en este equipo: instálalo, inicia sesión y vuelve a abrir este acceso.",
                "Claude Code was not found on this computer: install it, sign in and open this shortcut again.",
            ),
        ]
    return "\n".join(lines)


# --- the flow -------------------------------------------------------------------------------------------------------


def launch(
    mode: str,
    ui: Ui,
    *,
    runner: plugin_registry.Runner | None = None,
    popen: Callable[..., object] = subprocess.Popen,
    interpreter: str | None = None,
) -> int:
    """Run ``new`` or ``open`` (``nuevo`` / ``abrir``) against ``ui``. Returns the exit status; cancelling is not an error."""
    new = mode in ("new", "nuevo")
    s = functools.partial(say, layout.install_language() or DEFAULT_LANGUAGE)
    title = (
        s("Elige o crea la carpeta del proyecto nuevo", "Choose or create the folder of the new project")
        if new
        else s("Elige la carpeta de un proyecto de CotaForge", "Choose the folder of a CotaForge project")
    )
    folder = ui.pick_folder(title)
    if folder is None:
        return 0
    folder = Path(folder)
    s = functools.partial(say, choose_language(folder))  # from here on, the project's own language
    if not new and not is_design_space(folder):
        ui.error(
            s(
                f"Esta carpeta no es un proyecto de CotaForge:\n{folder}\n\n"
                "Elige una carpeta que se haya creado con «Crear proyecto nuevo (CotaForge)».",
                f"This folder is not a CotaForge project:\n{folder}\n\n"
                'Choose a folder created with "Create new project (CotaForge)".',
            )
        )
        return 1
    occupied = new and folder.is_dir() and any(folder.iterdir()) and not is_design_space(folder)
    if occupied and not ui.confirm(
        s(
            f"La carpeta ya tiene archivos:\n{folder}\n\n¿Preparar aquí un proyecto de CotaForge de todos modos? "
            "No se borra ni se cambia nada de lo que ya hay.",
            f"The folder already has files:\n{folder}\n\nSet up a CotaForge project here anyway? "
            "Nothing that is there is deleted or changed.",
        )
    ):
        return 0

    info = layout.read_install_info()
    try:
        report = init_space(
            folder,
            interpreter=layout.console_interpreter(sys.executable) if interpreter is None else interpreter,
            freecad_root=installed_freecad_root(info) if new else None,
            visible=True if new else None,
            language=None,
        )
    except InitError as error:
        ui.error(str(error))
        return 1

    claude = plugin_registry.find_claude()
    registration: list[str] = []
    if claude is not None and not plugin_registry.is_registered(runner, claude=claude):
        outcome = plugin_registry.register(runner, claude=claude)
        registration = [line.removeprefix("error: ") for line in outcome.lines]
        if not outcome.ok:
            registration = [
                s("No pude registrar el plugin en Claude Code:", "I could not register the plugin in Claude Code:"),
                *registration,
            ]

    text = instructions(report.folder, report, registration, claude)
    if claude is None:
        ui.info(text)
    elif ui.ask_terminal(
        text,
        s(
            "¿Quieres abrirlo ahora también en una terminal con Claude Code?",
            "Open it now in a terminal with Claude Code too?",
        ),
    ):
        open_terminal(report.folder, claude, popen)
    return 0


def run(mode: str, folder: Path | None) -> int:
    """Entry point of the shortcuts. With ``folder`` there are no windows (scripts and tests); without it, tkinter."""
    if folder is not None:
        return launch(mode, ConsoleUi(folder))
    s = functools.partial(say, layout.install_language() or DEFAULT_LANGUAGE)
    try:
        ui = TkUi()
    except Exception as error:  # noqa: BLE001 - no display or no tkinter: the only way to tell is to try
        print(
            "error: "
            + s(
                f"no se pudo abrir la ventana ({error}); usa --folder RUTA",
                f"the window could not be opened ({error}); use --folder PATH",
            ),
            file=sys.stderr,
        )
        return 1
    try:
        return launch(mode, ui)
    except Exception:  # noqa: BLE001 - without a console a crash would be silent
        ui.error(s("Algo falló:", "Something failed:") + "\n\n" + traceback.format_exc(limit=3))
        return 1
    finally:
        ui.close()
