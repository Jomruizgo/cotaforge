"""Render the sheet SVG (``sheet_svg.to_svg``) to a PDF or a PNG with Qt, without any window.

FreeCAD ships PySide with ``QtSvg`` and ``QPdfWriter``; neither needs a display, only an application object (fonts
live in it). The GUI binary has one; ``freecadcmd`` has none, so a ``QGuiApplication`` on the ``offscreen`` platform
plugin is created on first use. The plugin is looked for in Qt's plugin folder BEFORE it is requested: Qt aborts the
whole process when the platform it is asked for is missing, and that would take the worker (and the document) with it.
"""

import glob
import os
import sys
from typing import Any

# Windows first tries its native plugin: it opens no window either, and unlike ``offscreen`` it sees the system fonts
# (on Windows ``offscreen`` has an empty font database and every glyph comes out as a filled box).
PLATFORMS = ("windows", "offscreen", "minimal") if sys.platform == "win32" else ("offscreen", "minimal")
PDF_DPI = 300

_app: Any = None  # keeps the application we created alive


class RenderError(RuntimeError):
    """The sheet could not be rendered (no Qt platform plugin, an SVG Qt cannot read, a file it could not write)."""


def _application() -> Any:
    global _app  # noqa: PLW0603
    from PySide import QtCore, QtGui  # noqa: PLC0415 - Qt only when a sheet is rendered

    existing = QtGui.QGuiApplication.instance()
    if existing is not None:
        return existing
    if _app is not None:
        return _app
    folders = _plugin_folders(QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.LibraryPath.PluginsPath))
    found = [(p, f) for f in folders for p in PLATFORMS if glob.glob(os.path.join(f, "platforms", f"*q{p}*"))]
    requested = os.environ.get("QT_QPA_PLATFORM")
    if requested is None:
        if not found:
            looked = ", ".join(os.path.join(f, "platforms") for f in folders)
            msg = (
                f"no Qt platform plugin to render the sheet without a window (looked for {', '.join(PLATFORMS)} in "
                f"{looked})"
            )
            raise RenderError(msg)
        platform, folder = found[0]
        os.environ["QT_QPA_PLATFORM"] = platform
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", os.path.join(folder, "platforms"))
    _app = QtGui.QGuiApplication(["cotaforge"])
    _add_techdraw_fonts(QtGui)
    return _app


def _add_techdraw_fonts(qtgui: Any) -> None:
    """Register the fonts FreeCAD ships for TechDraw (osifont, the templates' font), so the sheet text has its own
    font whatever the platform's font database holds."""
    try:
        import FreeCAD  # noqa: PLC0415

        folder = os.path.join(FreeCAD.getResourceDir(), "Mod", "TechDraw", "Resources", "fonts")
    except Exception:  # noqa: BLE001 - outside FreeCAD there are no TechDraw fonts to add
        return
    for font in sorted(glob.glob(os.path.join(folder, "*.ttf"))):
        qtgui.QFontDatabase.addApplicationFont(font)


def _plugin_folders(reported: str) -> list[str]:
    """Where Qt's plugins may be: the folder Qt reports, then the one next to FreeCAD's own ``lib``. The Windows
    FreeCAD 1.1.3 build reports ``<root>/bin/lib/qt6/plugins`` while the plugins live in ``<root>/lib/qt6/plugins``."""
    folders = [reported]
    try:
        import FreeCAD  # noqa: PLC0415

        root = FreeCAD.getHomePath()
    except Exception:  # noqa: BLE001 - outside FreeCAD (unit tests) there is no home path
        root = None
    if root:
        folders += [os.path.join(root, "lib", "qt6", "plugins"), os.path.join(root, "lib", "qt5", "plugins")]
    return list(dict.fromkeys(os.path.normpath(f) for f in folders if f))


def _renderer(svg: str) -> Any:
    from PySide import QtCore, QtSvg  # noqa: PLC0415

    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        msg = "Qt could not read the sheet SVG"
        raise RenderError(msg)
    return renderer


def render_pdf(svg: str, path: str, width_mm: float, height_mm: float, title: str) -> None:
    """The sheet as a one-page PDF of exactly ``width_mm`` x ``height_mm`` (vector: lines and text stay sharp). One
    drawing, one sheet, one file."""
    from PySide import QtCore, QtGui  # noqa: PLC0415

    _application()
    renderer = _renderer(svg)
    writer = QtGui.QPdfWriter(path)
    writer.setResolution(PDF_DPI)
    writer.setCreator("cotaforge")
    writer.setTitle(title)
    writer.setPageSize(QtGui.QPageSize(QtCore.QSizeF(width_mm, height_mm), QtGui.QPageSize.Unit.Millimeter))
    writer.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0), QtGui.QPageLayout.Unit.Millimeter)
    painter = QtGui.QPainter(writer)
    try:
        if not painter.isActive():
            msg = f"Qt could not open {path} for writing"
            raise RenderError(msg)
        renderer.render(painter, QtCore.QRectF(0, 0, writer.width(), writer.height()))
    finally:
        if painter.isActive():
            painter.end()


def render_png(
    svg: str,
    path: str,
    width_px: int,
    width_mm: float,
    height_mm: float,
    region: tuple[float, float, float, float] | None = None,
) -> tuple[int, int]:
    """The sheet as a PNG ``width_px`` wide (the height follows the sheet's proportions) on white paper; ``region``
    (page mm, ``x0, y0, x1, y1``, origin at the lower left) shows only that part, ``width_px`` wide: a zoom. Returns
    the pixels ``(width, height)``."""
    from PySide import QtCore, QtGui  # noqa: PLC0415

    _application()
    renderer = _renderer(svg)
    if region is not None:
        x0, y0, x1, y1 = region
        renderer.setViewBox(QtCore.QRectF(x0, height_mm - y1, x1 - x0, y1 - y0))  # the SVG is in page mm, y down
        width_mm, height_mm = x1 - x0, y1 - y0
    height_px = max(1, round(width_px * height_mm / width_mm))
    image = QtGui.QImage(width_px, height_px, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        renderer.render(painter, QtCore.QRectF(0, 0, width_px, height_px))
    finally:
        painter.end()
    if not image.save(path, "PNG"):
        msg = f"Qt could not write {path}"
        raise RenderError(msg)
    return width_px, height_px
