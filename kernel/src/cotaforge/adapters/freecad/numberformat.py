"""Why FreeCAD with a window cannot start when Windows uses one character as decimal and thousands separator.

FreeCAD's GUI stops at a modal titled "Invalid system settings" and never reaches the worker script; the headless
binary is not affected. Two read-only checks (the harness never changes a Windows setting):

* ``separator_conflict`` -- before the window is started, from ``sDecimal``/``sThousand`` of
  ``HKCU\\Control Panel\\International``;
* ``blocked_by_dialog`` -- if FreeCAD reached the dialog anyway, by the title of a top-level window of the process the
  harness started. Nothing is pressed on it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

DIALOG_TITLE = "invalid system settings"
"""Lower-cased title of the FreeCAD modal (matched as a substring, case-insensitive)."""

_REG_KEY = r"Control Panel\International"

HOW_TO_FIX = (
    "FreeCAD with a window does not start when Windows uses the same character as decimal and thousands separator. "
    "The engineer has to change it in Windows: Configuración de Windows, Hora e idioma, "
    "Idioma y región, Formato regional (Cambiar formatos / Formatos adicionales, make decimal and thousands "
    "separators different). The harness never changes a Windows setting. "
    "Without a window FreeCAD is not affected: the agent can go on headless (start the server without "
    "COTAFORGE_VISIBLE; no view or selection tools) if the engineer agrees."
)

Reader = Callable[[str], str | None]


def read_registry_value(name: str) -> str | None:
    """A string value of ``HKCU\\Control Panel\\International``; ``None`` when absent or not on Windows. Read only."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    return value if isinstance(value, str) else None


def separator_conflict(reader: Reader | None = None) -> str | None:
    """The explanation when decimal and thousands separators are the same character, else ``None``."""
    read = reader or read_registry_value
    decimal, thousand = read("sDecimal"), read("sThousand")
    if not decimal or not thousand or decimal != thousand:
        return None
    return f"Windows number format: decimal and thousands separator are both {decimal!r}. " + HOW_TO_FIX


def _windows_titles(pid: int) -> list[str]:
    """Titles of the visible top-level windows owned by ``pid`` (Windows only)."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles: list[str] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def collect(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                titles.append(buffer.value)
        return True

    user32.EnumWindows(callback_type(collect), 0)
    return titles


def blocked_by_dialog(pid: int | None, titles: Callable[[int], list[str]] | None = None) -> str | None:
    """The explanation when a window of ``pid`` is FreeCAD's "Invalid system settings" modal, else ``None``."""
    if pid is None:
        return None
    if titles is None:
        if sys.platform != "win32":
            return None
        titles = _windows_titles
    try:
        found = titles(pid)
    except Exception:  # noqa: BLE001 - a failed look must never decide the fate of the start
        return None
    if not any(DIALOG_TITLE in title.lower() for title in found):
        return None
    return "FreeCAD stopped at its 'Invalid system settings' dialog. " + HOW_TO_FIX
