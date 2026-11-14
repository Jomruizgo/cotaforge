# ruff: noqa: E501
"""Visible-mode hooks: everything the worker does differently when it runs inside the FreeCAD GUI binary
(``freecad gui_entry.py``, accompanied mode) instead of ``freecadcmd``.

``ACTIVE`` is ``App.GuiUp`` evaluated once at import; every function is a no-op (or ``not_supported``)
when it is False, so ``main.py`` and ``queries.py`` call these hooks unconditionally and the headless
worker behaves exactly as before. Mechanisms verified by experiment on FreeCAD 1.0.2 and 1.1.3:

- requests: a daemon thread blocks on ``stdin`` and hands each line to the Qt thread through a queued
  signal (``_Bridge``), so the event loop stays alive between requests and the engineer can orbit the
  model. No ``QSocketNotifier`` and no ``O_NONBLOCK`` on purpose: both are POSIX-only, a blocking
  ``readline`` on a pipe also works on Windows.
- painting: ``Gui.updateGui()`` after every mutating operation; the camera animation is disabled per
  3D view (``setAnimationEnabled(False)``) because FreeCAD 1.0 animates ``ViewFit`` for 250 ms.
- exit: close every document first (``App.closeDocument`` never prompts), then ``QApplication.quit()``.
  ``mainwindow.close()`` with a modified document pops the "Save changes?" dialog and the worker hangs
  until the client's grace period kills it.
- capture: ``View3DInventor.saveImage`` needs a real display (the ``offscreen`` and ``minimal`` Qt
  platforms hang or crash), which is why captures (and ``set_view``) are only offered by the visible worker.

The ``PySide`` module is FreeCAD's own shim over PySide2/PySide6, so this file does not pin a Qt major.
"""

import contextlib
import math
import os
import sys
import threading
import traceback
from collections.abc import Callable, Iterator
from typing import Any

import changes
import fc
import FreeCAD as App
import highlight
from debounce import Debouncer

ACTIVE = bool(App.GuiUp)

if ACTIVE:
    import FreeCADGui as Gui
    from PySide import QtCore, QtGui, QtWidgets

DEFAULT_CAPTURE_SIZE = (1280, 800)
CAPTURE_BACKGROUND = "White"
# Contract ``ViewRequest.name`` -> ``View3DInventor`` method that sets that standard orientation.
VIEW_METHODS = {
    "iso": "viewIsometric",
    "front": "viewFront",
    "top": "viewTop",
    "right": "viewRight",
    "rear": "viewRear",
    "left": "viewLeft",
    "bottom": "viewBottom",
    "dimetric": "viewDimetric",
    "trimetric": "viewTrimetric",
}
# Drag-selecting fires the observer once per sub-element; only the selection ``debounce_s`` after the
# last one is reported (CAD-selection seam).
SELECTION_DEBOUNCE_S = 0.15
# Opaque orange: ``queries.show_entities``' one temporary paint colour (RGBA, FreeCAD's own convention for
# ``DiffuseColor``/``LineColorArray`` entries).
HIGHLIGHT_COLOR: highlight.RGBA = (1.0, 0.55, 0.0, 0.0)

_highlight_state = highlight.HighlightState()

_keep_alive: list[Any] = []  # the bridge QObject and the reader thread must outlive ``serve()``
_keep_alive_sub: list[Any] = []  # the sheet window last looked up: its (sub-window, widget) wrappers
_shutting_down = False


def available() -> bool:
    """True inside the GUI binary: the hooks below act, ``capture`` works."""
    return ACTIVE


# --- the engineer's own saves ---------------------------------------------

_engineer_saves: set[tuple[str, str]] = set()  # (FreeCAD document name, normalised file path)


def _normalised(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def note_engineer_save(doc_name: str, path: str) -> None:
    """The engineer saved ``doc_name`` to ``path`` with FreeCAD's own save (``_HighlightWatcher``): remembered until
    the harness's next save of that document to that path consumes it (``take_engineer_save``)."""
    _engineer_saves.add((doc_name, _normalised(path)))


def take_engineer_save(doc_name: str, path: str) -> bool:
    """True (once) when the engineer saved ``doc_name`` to ``path`` since the harness's last save there."""
    key = (doc_name, _normalised(path))
    if key in _engineer_saves:
        _engineer_saves.discard(key)
        return True
    return False


def platform_name() -> str | None:
    if not ACTIVE:
        return None
    app = QtWidgets.QApplication.instance()
    return str(app.platformName()) if app is not None else None


# --- view management ------------------------------------------------------------------------------------------------


def _gui_document(fc_doc: Any) -> Any:
    return Gui.getDocument(fc_doc.Name)


def _activate(fc_doc: Any) -> Any:
    gui_doc = _gui_document(fc_doc)
    active = Gui.ActiveDocument
    if active is None or active.Document.Name != fc_doc.Name:
        Gui.ActiveDocument = gui_doc
    return gui_doc


def on_new_document(fc_doc: Any) -> None:
    """Prepare the 3D view of a document the worker just created: no camera animation, isometric."""
    if not ACTIVE:
        return
    view = _activate(fc_doc).ActiveView
    if view is None or not hasattr(view, "viewIsometric"):
        return  # a document opened with a drawing sheet: its active window is the sheet, which has no 3D orientation
    if hasattr(view, "setAnimationEnabled"):
        view.setAnimationEnabled(False)
    view.viewIsometric()  # after disabling the animation, otherwise the transition is animated
    Gui.updateGui()


# --- documents without a window --------------------------------------------------------------------------------------
#
# An ``App::Link`` to another document needs that document LOADED, not shown (measured on FreeCAD 1.1.3): a part
# opened with ``App.openDocument(path, hidden=True)`` has no window, the links to it resolve, the assembly saves a
# relative file name for it and reopens it. A window made later (``Document.createView``) or closed again (the
# sub-window) leaves the loaded document alone as long as another document links to it; closing the LAST window of a
# document nobody links to closes the document, which is why ``hide_window`` refuses that case.


def _windows(fc_doc: Any) -> list[Any]:
    """The MDI sub-windows of the document: FreeCAD titles them ``<document label> : <n>``. Nothing when another open
    document has the same label (the title would not say whose window it is)."""
    app = QtWidgets.QApplication.instance()
    area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea) if app is not None else None
    if area is None:
        return []
    label = str(fc_doc.Label)
    if any(d.Name != fc_doc.Name and str(d.Label) == label for d in App.listDocuments().values()):
        return []
    return [sub for sub in area.subWindowList() if str(sub.windowTitle()).startswith(f"{label} :")]


def has_window(fc_doc: Any) -> bool:
    """Whether the document is shown in a window of the main window. Always True headless (nothing to hide)."""
    if not ACTIVE:
        return True
    try:
        return bool(Gui.getDocument(fc_doc.Name).mdiViewsOfType("Gui::MDIView"))
    except Exception:  # noqa: BLE001 - no Gui document: no window
        return False


def window_titles() -> list[str]:
    """The titles of the document windows now open (the start page is not one): what the engineer sees."""
    if not ACTIVE:
        return []
    area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    return [str(s.windowTitle()) for s in area.subWindowList() if " : " in str(s.windowTitle())] if area else []


def is_linked_from_elsewhere(fc_doc: Any) -> bool:
    """Whether another loaded document links to this one (FreeCAD keeps a document alive for its links)."""
    return any(
        other.Name != fc_doc.Name and fc_doc.Name in {d.Name for d in other.getDependentDocuments()}
        for other in App.listDocuments().values()
    )


def show_window(fc_doc: Any) -> bool:
    """Give a document without a window its 3D window (the way ``on_new_document`` leaves a new one). True when it has a
    window afterwards. No-op headless."""
    if not ACTIVE:
        return False
    try:
        if not has_window(fc_doc):
            _gui_document(fc_doc).createView("Gui::View3DInventor")
        on_new_document(fc_doc)
        return has_window(fc_doc)
    except Exception:  # noqa: BLE001 - cosmetic: the document still answers without its window
        sys.stderr.write(traceback.format_exc())
        return False


def hide_window(fc_doc: Any) -> bool:
    """Close the window(s) of a document and keep it loaded. Only for a document that something else links to and that
    has no unsaved changes: closing the last window of any other would close the document, or ask the engineer whether
    to save it. True when the document has no window afterwards."""
    if not ACTIVE:
        return False
    try:
        if not has_window(fc_doc):
            return True
        if is_modified(fc_doc) or not is_linked_from_elsewhere(fc_doc):
            return False
        windows = _windows(fc_doc)
        if not windows:
            return False
        for sub in windows:
            sub.close()
        Gui.updateGui()
        return fc_doc.Name in App.listDocuments() and not has_window(fc_doc)
    except Exception:  # noqa: BLE001 - cosmetic
        sys.stderr.write(traceback.format_exc())
        return False


def is_modified(fc_doc: Any) -> bool:
    """Whether the window counts the document as changed (the ``*`` in its title)."""
    return bool(Gui.getDocument(fc_doc.Name).Modified)


@contextlib.contextmanager
def unmodified(fc_doc: Any) -> Iterator[None]:
    """Display changes made inside (``Visibility``, which the window counts as an edit) leave the document the way it
    was: a document that had no unsaved changes still has none, so no ``*`` in the title and no "save?" question."""
    if not ACTIVE:
        yield
        return
    try:
        gui_doc = Gui.getDocument(fc_doc.Name)
        was = bool(gui_doc.Modified)
    except Exception:  # noqa: BLE001 - no window for this document: nothing to keep
        gui_doc, was = None, True
    try:
        yield
    finally:
        if gui_doc is not None and not was:
            try:
                gui_doc.Modified = False
            except Exception:  # noqa: BLE001 - cosmetic
                sys.stderr.write(traceback.format_exc())


def mark_saved(fc_doc: Any) -> None:
    """The document was written to its file by the harness (``saveCopy`` leaves the window's flag as it was): the
    window no longer counts it as changed. Whatever is changed after this sets the flag again."""
    if not ACTIVE:
        return
    try:
        Gui.getDocument(fc_doc.Name).Modified = False
    except Exception:  # noqa: BLE001 - cosmetic
        sys.stderr.write(traceback.format_exc())


def refresh(fc_doc: Any | None) -> None:
    """Show the document's new state now: fit the model in the view and process the paint events.

    Painting is cosmetic: a failure here (the engineer closed the document's window by hand, say) is
    logged and never turned into an operation failure.
    """
    if not ACTIVE:
        return
    try:
        if fc_doc is not None:
            view = _activate(fc_doc).ActiveView
            if view is not None and hasattr(view, "fitAll"):
                view.fitAll()
        Gui.updateGui()
    except Exception:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())


def pump() -> None:
    """Let the Qt event loop run: TechDraw finishes the views it computes in background threads through queued
    signals, which only fire when events are processed. No-op headless (TechDraw computes in the recompute there)."""
    if ACTIVE:
        Gui.updateGui()


def settle(passes: int = 4, pause_s: float = 0.03) -> None:
    """Let the event loop run for a moment: an item moved on the page is repainted a few events later, so what is measured
    right after a move is still the old place. No-op headless."""
    if not ACTIVE:
        return
    import time  # noqa: PLC0415

    for _ in range(passes):
        Gui.updateGui()
        time.sleep(pause_s)


def _view3d(fc_doc: Any) -> Any:
    """The document's 3D view, even when another window (a TechDraw sheet, say) is the active one: the sheet's view
    has no ``saveImage`` / ``viewIsometric``, which is why capturing with a sheet in front used to fail."""
    gui_doc = _activate(fc_doc)
    view = gui_doc.ActiveView
    if view is not None and hasattr(view, "saveImage"):
        return view
    area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    if area is not None:
        windows = [
            sub
            for sub in area.subWindowList()
            if sub.widget() is not None and sub.widget().metaObject().className() == "Gui::View3DInventor"
        ]
        mine = [sub for sub in windows if str(sub.windowTitle()).startswith(f"{fc_doc.Name} :")] or windows
        if mine:
            area.setActiveSubWindow(mine[0])
            Gui.updateGui()
            view = gui_doc.ActiveView
            if view is not None and hasattr(view, "saveImage"):
                return view
    msg = "the document has no 3D view (close the TechDraw sheet window or open the model's 3D view)"
    raise RuntimeError(msg)


def show_model(fc_doc: Any) -> None:
    """Bring the document's 3D view in front of the sheet windows. No-op headless."""
    if not ACTIVE:
        return
    try:
        _view3d(fc_doc)
        Gui.updateGui()
    except Exception:  # noqa: BLE001 - cosmetic
        sys.stderr.write(traceback.format_exc())


def set_view(fc_doc: Any, name: str | None, keep: list[Any] | None = None, others: list[Any] | None = None) -> None:
    """Orient the document's 3D view to a standard view (``VIEW_METHODS``) when ``name`` is given, then fit the model in
    it: all of it, or only ``keep`` (the other top-level objects, ``others``, are hidden for the fit and put back:
    ``fitAll`` frames what is visible, and ``Gui.Selection`` is never touched, see the note on ``show_entities``).

    The camera animation was disabled on ``on_new_document``, so the orientation and the fit land before
    ``updateGui`` returns and a ``capture`` right after shows the requested view.
    """
    view = _view3d(fc_doc)
    if name is not None:
        getattr(view, VIEW_METHODS[name])()
    hidden: list[tuple[Any, bool]] = []
    if keep:
        for obj in others or []:
            vo = obj.ViewObject
            if vo is not None and obj not in keep:
                hidden.append((vo, bool(vo.Visibility)))
                vo.Visibility = False
    try:
        if hasattr(view, "fitAll"):
            view.fitAll()
        Gui.updateGui()
    finally:
        for vo, was in hidden:
            vo.Visibility = was
        Gui.updateGui()


def style_objects(
    objects: list[Any], transparency: int | None, color: tuple[float, float, float] | None, visible: bool | None
) -> None:
    """Display properties of body objects (``ViewObject``): never marks the document modified. The colour and
    transparency go to a body's Tip too (a PartDesign body shows its Tip's look); visibility only to the body."""
    for obj in objects:
        vo = obj.ViewObject
        if vo is None:
            continue
        targets = [vo]
        tip = getattr(obj, "Tip", None)
        if tip is not None and tip.ViewObject is not None:
            targets.append(tip.ViewObject)
        for target in targets:
            if transparency is not None and hasattr(target, "Transparency"):
                target.Transparency = int(transparency)
            if color is not None and hasattr(target, "ShapeColor"):
                target.ShapeColor = color
        if visible is not None:
            vo.Visibility = bool(visible)
    Gui.updateGui()


_clip_nodes: dict[str, Any] = {}  # FreeCAD document name -> the SoClipPlane node inserted in its scene graph
_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def set_section(fc_doc: Any, axis: str | None, offset_mm: float) -> None:
    """Install (or remove, ``axis`` None) a clipping plane in the document's 3D view. A Coin ``SoClipPlane`` keeps the
    half space its normal points to, so the normal is the NEGATED axis: what lies beyond ``offset_mm`` on the +axis side
    is cut away and the cut face looks back at the viewer. Inserted first in the scene graph, so it clips everything."""
    from pivy import coin  # noqa: PLC0415 - FreeCAD's own pivy, present in the GUI binary only

    view = _view3d(fc_doc)
    graph = view.getSceneGraph()
    old = _clip_nodes.pop(fc_doc.Name, None)
    if old is not None and graph.findChild(old) >= 0:
        graph.removeChild(old)
    if axis is not None:
        ax, ay, az = _AXES[axis]
        node = coin.SoClipPlane()
        node.plane.setValue(coin.SbPlane(coin.SbVec3f(-ax, -ay, -az), -float(offset_mm)))
        graph.insertChild(node, 0)
        _clip_nodes[fc_doc.Name] = node
    Gui.updateGui()


def clip_planes(fc_doc: Any) -> list[list[float]]:
    """Test-only: ``[nx, ny, nz, distance]`` of every ``SoClipPlane`` in the document's 3D view."""
    from pivy import coin  # noqa: PLC0415

    action = coin.SoSearchAction()
    action.setType(coin.SoClipPlane.getClassTypeId())
    action.setInterest(coin.SoSearchAction.ALL)
    action.apply(_view3d(fc_doc).getSceneGraph())
    found = []
    paths = action.getPaths()
    for index in range(paths.getLength()):
        plane = paths[index].getTail().plane.getValue()
        normal = plane.getNormal()
        found.append([float(normal[0]), float(normal[1]), float(normal[2]), float(plane.getDistanceFromOrigin())])
    return found


def active_window_class() -> str:
    """Test-only: the C++ class of the MDI window in front (``Gui::View3DInventor``, a TechDraw page, ...)."""
    area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    sub = area.activeSubWindow() if area is not None else None
    return str(sub.widget().metaObject().className()) if sub is not None and sub.widget() is not None else ""


def open_sheet(page: Any) -> str:
    """Test-only: open a drawing sheet in its own window (the engineer's double click); the window in front."""
    page.ViewObject.doubleClicked()
    Gui.updateGui()
    return active_window_class()


def _sheet_sub(page: Any) -> Any | None:
    """The MDI sub-window that shows ``page`` (its title is the page's label, ``Drawing[*]`` while modified), or ``None``
    when the sheet is not open."""
    area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    if area is None:
        return None
    label = str(page.Label)
    for sub in area.subWindowList():
        widget = sub.widget()
        if widget is None or widget.metaObject().className() != "TechDrawGui::MDIViewPage":
            continue
        if str(sub.windowTitle()).replace("[*]", "").strip() == label:
            _keep_alive_sub.clear()
            _keep_alive_sub.append((sub, widget))  # a wrapper let go of takes the widget it brought with it (PySide)
            return sub
    return None


def _sheet_window(page: Any) -> Any | None:
    """The widget that shows ``page`` (a ``TechDrawGui::MDIViewPage``), or ``None`` when it is not open."""
    sub = _sheet_sub(page)
    return sub.widget() if sub is not None else None


def _sheet_widget(page: Any) -> Any:
    """The widget that paints the sheet: its own window, else the window in front (the sheet was just shown)."""
    window = _sheet_window(page)
    if window is None:
        area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
        sub = area.activeSubWindow() if area is not None else None
        window = sub.widget() if sub is not None else None
    if window is None:
        msg = "the sheet has no window to capture"
        raise RuntimeError(msg)
    return window


def show_sheet(page: Any) -> None:
    """Show the engineer the sheet being built: open the page's window when it is not open (as a double click does) and
    let Qt paint what was just added. No-op headless. Painting is cosmetic: a failure is logged, never an error."""
    if not ACTIVE:
        return
    try:
        if page.ViewObject is not None:
            page.ViewObject.doubleClicked()  # opens the window, or brings the open one to the front
        for _ in range(3):
            Gui.updateGui()
    except Exception:  # noqa: BLE001
        sys.stderr.write(traceback.format_exc())


def text_width_mm(text: str, size_mm: float, family: str = "osifont") -> float:
    """The width ``text`` takes set in ``family`` at ``size_mm``: measured with Qt in the window (the font TechDraw
    paints annotations with), estimated headless -- nothing is painted there, so the number only places a text block."""
    if not ACTIVE:
        return len(text) * size_mm * 0.6
    font = QtGui.QFont(family)
    font.setPixelSize(100)
    return float(QtGui.QFontMetricsF(font).horizontalAdvance(text)) * size_mm / 100.0


SCENE_PER_MM = 10.0  # the TechDraw scene is in tenths of a millimetre (``Rez``), y down


def block_box(page: Any, x_mm: float, y_mm: float, text: str | None = None) -> tuple[float, float, float] | None:
    """``(left, width, height)`` in mm of the text block TechDraw painted centred near the page point ``(x_mm, y_mm)`` (y
    up) -- an annotation: its own text item sits centred on its X, Y --, or ``None`` headless or when none is there. The
    line is where its Y says (the notes are 5 mm apart); its X is only an estimate from the font metrics, off by a few mm
    for some strings, so the block is the widest text item of that line within 8 mm of the estimate. With ``text`` the item
    must SAY that text: a line whose own item is not painted yet is never taken for another text that happens to stand at
    that height (the dimension text of a view: it moved a note line out of the frame), and ``None`` is the answer."""
    if not ACTIVE:
        return None
    import TechDrawGui  # noqa: PLC0415

    import sheet_notefit  # noqa: PLC0415 - the pure choice of the item (unit-tested without a window)

    Gui.updateGui()
    boxes: list[sheet_notefit.TextBox] = []
    for item in TechDrawGui.getSceneForPage(page).items():
        if type(item).__name__ != "QGraphicsTextItem":
            continue
        rect = item.sceneBoundingRect()
        boxes.append(
            sheet_notefit.TextBox(
                str(item.toPlainText()),
                rect.x() / SCENE_PER_MM,
                rect.width() / SCENE_PER_MM,
                rect.height() / SCENE_PER_MM,
                (rect.x() + rect.width() / 2) / SCENE_PER_MM,
                -(rect.y() + rect.height() / 2) / SCENE_PER_MM,
            )
        )
    return sheet_notefit.pick_block(boxes, x_mm, y_mm, text)


def scene_texts(page: Any) -> "list[Any]":
    """Every text item of the page scene as a ``sheet_notefit.TextBox`` (empty headless)."""
    if not ACTIVE:
        return []
    import sheet_notefit  # noqa: PLC0415
    import TechDrawGui  # noqa: PLC0415

    Gui.updateGui()
    out = []
    for item in TechDrawGui.getSceneForPage(page).items():
        if type(item).__name__ != "QGraphicsTextItem":
            continue
        rect = item.sceneBoundingRect()
        out.append(
            sheet_notefit.TextBox(
                str(item.toPlainText()),
                rect.x() / SCENE_PER_MM,
                rect.width() / SCENE_PER_MM,
                rect.height() / SCENE_PER_MM,
                (rect.x() + rect.width() / 2) / SCENE_PER_MM,
                -(rect.y() + rect.height() / 2) / SCENE_PER_MM,
            )
        )
    return out


def block_extent(page: Any, x_mm: float, y_mm: float, text: str | None = None) -> tuple[float, float] | None:
    """The size (mm) of the text block ``block_box`` finds, or ``None``."""
    found = block_box(page, x_mm, y_mm, text)
    return None if found is None else (found[1], found[2])


def export_pdf(page: Any, path: str) -> str:
    """TechDraw's own PDF of the sheet, written from its window (the objects the engineer sees and edits: native
    dimensions, notes, symbols): ``TechDrawGui.exportPageAsPdf``, the exporter of the Export menu. Returns
    ``"techdraw"``. Needs the window: a sheet that was never shown exports blank, so it is shown first."""
    import TechDrawGui  # noqa: PLC0415 - only in the GUI binary

    show_sheet(page)
    for _ in range(3):
        Gui.updateGui()
    TechDrawGui.exportPageAsPdf(page, path)
    return "techdraw"


def render_png(
    page: Any,
    path: str,
    width_px: int,
    page_mm: tuple[float, float],
    region: tuple[float, float, float, float] | None = None,
) -> tuple[int, int]:
    """The sheet as a PNG, painted from the SAME scene ``export_pdf`` writes the PDF from (every object of the page, the
    ones the items made and the ones a native script added), ``width_px`` wide on white paper. ``region`` (page mm,
    ``x0, y0, x1, y1``, origin at the lower left, y up) paints only that part, ``width_px`` wide: a zoom into a dimension
    or a line type. Returns the pixels ``(width, height)``."""
    import TechDrawGui  # noqa: PLC0415 - only in the GUI binary

    show_sheet(page)
    for _ in range(3):
        Gui.updateGui()
    x0, y0, x1, y1 = region if region is not None else (0.0, 0.0, page_mm[0], page_mm[1])
    height_px = max(1, round(width_px * (y1 - y0) / (x1 - x0)))
    image = QtGui.QImage(width_px, height_px, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(image)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        scene = TechDrawGui.getSceneForPage(page)
        # the scene is in tenths of a millimetre, y DOWN: the page point (x, y) is at (10 x, -10 y)
        source = QtCore.QRectF(
            SCENE_PER_MM * x0, -SCENE_PER_MM * y1, SCENE_PER_MM * (x1 - x0), SCENE_PER_MM * (y1 - y0)
        )
        scene.render(painter, QtCore.QRectF(0, 0, width_px, height_px), source)
    finally:
        painter.end()
    if not image.save(path, "PNG"):
        msg = f"the sheet image could not be saved at {path}"
        raise RuntimeError(msg)
    return width_px, height_px


def capture_sheet(page: Any, path: str) -> list[int]:
    """Test-only: the sheet window of ``page`` as it is painted now, saved as an image (what the engineer sees); returns
    ``[width, height]`` in pixels."""
    window = None
    for _ in range(4):  # the window of a sheet that was just made or rebuilt can take a few events to be there
        show_sheet(page)
        try:
            window = _sheet_widget(page)
            break
        except RuntimeError:
            for _ in range(5):
                Gui.updateGui()
    if window is None:
        window = _sheet_widget(page)
    Gui.updateGui()
    pixmap = window.grab()
    if not pixmap.save(path):
        msg = f"the sheet window could not be saved at {path}"
        raise RuntimeError(msg)
    return [int(pixmap.width()), int(pixmap.height())]


def measure_bars(page: Any, centres: list[tuple[float, float]], page_w: float) -> list[float | None]:
    """Test-only: for each expected centre (page mm, y up) the vertical distance in mm between that centre and the
    horizontal bar of the centre mark the sheet window paints near it (the bar is the long horizontal run of dark
    pixels left of the centre, where a hole's circle has none); ``None`` when no bar is painted within 8 mm. Reads the
    window as the engineer sees it."""
    show_sheet(page)
    window = _sheet_widget(page)
    image = window.grab().toImage()
    width, height = image.width(), image.height()
    mid = height // 2

    def white(x: int, y: int) -> bool:
        c = image.pixelColor(x, y)
        return c.red() > 245 and c.green() > 245 and c.blue() > 245

    xs = [x for x in range(width) if white(x, mid)]
    x0 = min(xs)
    scale = (max(xs) - x0 + 1) / page_w
    ys = [y for y in range(height) if white(x0 + 5, y)]
    y0 = min(ys)
    page_h = (max(ys) - y0 + 1) / scale
    out: list[float | None] = []
    for cx, cy in centres:
        left, right = int(x0 + (cx - 5.5) * scale), int(x0 + (cx - 1.5) * scale)
        best: float | None = None
        for row in range(int(y0 + (page_h - cy - 8) * scale), int(y0 + (page_h - cy + 8) * scale)):
            dark = sum(1 for x in range(left, right) if image.pixelColor(x, row).red() < 215)
            if dark >= 0.5 * (right - left):
                offset = page_h - (row + 0.5 - y0) / scale - cy
                if best is None or abs(offset) < abs(best):
                    best = offset
        out.append(None if best is None else round(best, 2))
    return out


def capture(fc_doc: Any, path: str, width: int, height: int) -> None:
    """Render the document's 3D view to an image file (format from the extension, PNG expected)."""
    view = _view3d(fc_doc)
    Gui.updateGui()
    view.saveImage(path, width, height, CAPTURE_BACKGROUND)


# --- the sheet window: pointing at things of a drawing ----------------------------------------------------------------
#
# A TechDraw page paints its objects in a ``QGraphicsScene`` (one scene unit is a tenth of a millimetre, y runs down: the
# page point (x, y) of TechDraw's coordinates, mm with y up, is at scene (10 x, -10 y); verified on the window of 1.1.3,
# where a ring drawn that way sits on the circle of a hole of a view). What the agent points at is painted ON that scene
# with items of its own -- a translucent box round an item or a view, a thick line over an edge of a view -- so nothing of
# the document changes (no property, no recompute, nothing to save): the objects that have a colour of their own (the
# dimensions) are recoloured through their ``ViewObject`` and put back with the rest. A recompute of the document (any
# change to the sheet) takes the paint off: it would be in the wrong place.

SHEET_SCENE_PER_MM = 10.0
SHEET_COLOR = (255, 140, 0)
SHEET_LINE_MM = 0.7
SHEET_FILL_ALPHA = 55
SNAP_SEARCH_MM = 20.0  # how far from where an object says it is the window may have put it
_sheet_paint: dict[str, list[tuple[Any, Any]]] = {}  # document name -> (scene, item) painted there
_sheet_colours: dict[tuple[str, str], tuple[str, Any]] = {}  # (document, object) -> (attribute, original colour)
SHEET_COLOR_ATTRS = ("Color", "TextColor")  # the ViewObject property that colours a dimension, a balloon, a note


def _sheet_scene(page: Any) -> Any:
    """The scene of the window of ``page``, open and in front (opened first when it is not)."""
    show_sheet(page)
    sub = _sheet_sub(page)
    if sub is None:
        msg = "the sheet has no window to point at"
        raise RuntimeError(msg)
    area = Gui.getMainWindow().findChild(QtWidgets.QMdiArea)
    area.setActiveSubWindow(sub)
    Gui.updateGui()
    view = sub.widget().findChild(QtWidgets.QGraphicsView)
    if view is None or view.scene() is None:
        msg = "the sheet window has no scene to point at"
        raise RuntimeError(msg)
    return view.scene()


def displayed_offsets(page: Any, claimants: list[tuple[str, float, float]]) -> dict[str, tuple[float, float]]:
    """Where the window really shows the top-level objects of ``page``: for each ``(name, x, y)`` (where the object says it
    is, mm, y up) the ``(dx, dy)`` to the place of the nearest object of the scene (each scene object is claimed once,
    the closest pairs first). The window pulls a symbol that comes close to a view's origin on to it, whatever its
    ``X``/``Y`` say."""
    scene = _sheet_scene(page)
    scale = SHEET_SCENE_PER_MM
    # NEVER ``item.parentItem()`` here: PySide hands the parent back as an object it owns, and when that wrapper is let
    # go it deletes the item -- the sheet window lost everything it drew (verified in 1.1.3). ``data(0)`` marks the views.
    shown = [(it.scenePos().x() / scale, -it.scenePos().y() / scale) for it in scene.items() if it.data(0) == "QGIV"]
    pairs = sorted(
        (math.hypot(sx - x, sy - y), name, index, sx - x, sy - y)
        for name, x, y in claimants
        for index, (sx, sy) in enumerate(shown)
    )
    taken: set[int] = set()
    done: set[str] = set()
    found: dict[str, tuple[float, float]] = {}
    for distance, name, index, dx, dy in pairs:
        if distance > SNAP_SEARCH_MM:
            break
        if name in done or index in taken:
            continue
        done.add(name)
        taken.add(index)
        found[name] = (dx, dy)
    return found


def paint_sheet(
    fc_doc: Any,
    page: Any,
    rects: list[tuple[float, float, float, float]],
    paths: list[list[tuple[float, float]]],
    recolor: list[Any],
) -> None:
    """Point at things of ``page`` (in the window, opened if needed): a translucent box for each of ``rects`` (x0, y0, x1,
    y1; mm, y up), a thick line for each of ``paths`` (a list of points), and the objects of ``recolor`` in the highlight
    colour. Adds to what is painted already (``clear_highlight`` takes it all off). No-op headless."""
    if not ACTIVE:
        return
    scene = _sheet_scene(page)
    k = SHEET_SCENE_PER_MM
    colour = QtGui.QColor(*SHEET_COLOR)
    pen = QtGui.QPen(colour)
    pen.setWidthF(SHEET_LINE_MM * k)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    fill = QtGui.QColor(*SHEET_COLOR, SHEET_FILL_ALPHA)
    box_pen = QtGui.QPen(colour)
    box_pen.setWidthF(0.35 * k)
    painted = _sheet_paint.setdefault(fc_doc.Name, [])

    def keep(item: Any) -> None:
        item.setZValue(1e6)
        item.setAcceptedMouseButtons(QtCore.Qt.NoButton)  # a click goes through to what is under it
        painted.append((scene, item))

    for x0, y0, x1, y1 in rects:
        keep(scene.addRect(QtCore.QRectF(x0 * k, -y1 * k, (x1 - x0) * k, (y1 - y0) * k), box_pen, QtGui.QBrush(fill)))
    for points in paths:
        if len(points) < 2:
            continue
        path = QtGui.QPainterPath()
        path.moveTo(points[0][0] * k, -points[0][1] * k)
        for x, y in points[1:]:
            path.lineTo(x * k, -y * k)
        keep(scene.addPath(path, pen))
    for obj in recolor:
        vo = obj.ViewObject
        attr = next((a for a in SHEET_COLOR_ATTRS if vo is not None and hasattr(vo, a)), None)
        if attr is None:
            continue
        original = tuple(getattr(vo, attr))
        _sheet_colours.setdefault((fc_doc.Name, obj.Name), (attr, original))
        setattr(vo, attr, (*(c / 255.0 for c in SHEET_COLOR), original[3] if len(original) > 3 else 1.0))
    Gui.updateGui()


def can_recolor(obj: Any) -> bool:
    """Whether ``obj`` has a ViewObject colour ``paint_sheet`` can change (a dimension does; a symbol does not)."""
    vo = getattr(obj, "ViewObject", None)
    return vo is not None and any(hasattr(vo, a) for a in SHEET_COLOR_ATTRS)


def clear_sheet_paint(fc_doc: Any) -> None:
    """Take off whatever ``paint_sheet`` put on ``fc_doc``'s sheets: the items of the scenes, the colours of the objects.
    Idempotent; a window or an object that is gone is nothing left to clean."""
    if not ACTIVE:
        return
    for scene, item in _sheet_paint.pop(fc_doc.Name, []):
        try:
            scene.removeItem(item)
        except RuntimeError:  # the window was closed: its scene took the item with it
            pass
    for key in [k for k in _sheet_colours if k[0] == fc_doc.Name]:
        attr, original = _sheet_colours.pop(key)
        try:
            obj = fc_doc.getObject(key[1])
            if obj is not None and obj.ViewObject is not None:
                setattr(obj.ViewObject, attr, original)
        except Exception:  # noqa: BLE001 - one object that cannot be put back must not block the others
            sys.stderr.write(traceback.format_exc())


def sheet_paint_state(fc_doc: Any) -> list[int]:
    """Test-only: ``[items painted on the sheets, objects recoloured]`` of ``fc_doc``."""
    name = fc_doc.Name
    return [len(_sheet_paint.get(name, [])), sum(1 for key in _sheet_colours if key[0] == name)]


def sheet_color_pixels(page: Any, rgb: tuple[int, int, int] = SHEET_COLOR, tol: int = 60) -> int:
    """Test-only: how many pixels of the sheet window, as painted now, are within ``tol`` per channel of ``rgb``: what the
    engineer sees of a highlight, counted. 0 headless."""
    if not ACTIVE:
        return 0
    show_sheet(page)
    window = _sheet_widget(page)
    Gui.updateGui()
    image = window.grab().toImage()
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            if abs(c.red() - rgb[0]) <= tol and abs(c.green() - rgb[1]) <= tol and abs(c.blue() - rgb[2]) <= tol:
                count += 1
    return count


# --- show_entities (agent-to-engineer counterpart of the live selection below) -----------------------------------
#
# ``queries.show_entities`` is the ONLY caller: it resolves handle ids to real FreeCAD objects (via
# ``registry``) and hands this module ``(obj, kind, index)`` triples -- this file never resolves a handle
# itself, same separation ``queries.py`` already keeps for every other query. Deliberately NEVER touches
# ``Gui.Selection``: doing so would feed back through the ``_SelectionObserver`` below exactly as if the
# engineer had picked the entity themselves (BUG-class this feature exists to avoid), and would also
# visibly steal the engineer's own current pick. Only ``ViewObject.DiffuseColor``/``LineColorArray`` are
# ever written here, computed by the FreeCAD-free ``highlight`` module so the colour arithmetic itself is
# unit-testable without a real FreeCAD.


def clear_highlight(fc_doc: Any) -> None:
    """Restore ``fc_doc``'s colours this worker last changed via ``highlight_entities``, ONE ENTRY AT A
    TIME: an entry is forgotten (``HighlightState.discard``) only once its
    own restore actually succeeded, so a failure on one object (deleted, ``ViewObject`` gone, a property
    that no longer exists) never loses the ability to retry it later and never blocks restoring every
    OTHER object. Falls back to ``highlight.restore_value``'s safe reset when the object's current
    face/edge count no longer matches what was recorded (a plan operation, an undo, or a hand edit
    changed the topology while highlighted). Scoped to ``fc_doc`` alone: another open document's
    still-pending highlight is never touched. Called at the start of every ``highlight_entities``
    (clear-before-apply, same document only), on an explicit ``clear`` request, and before any
    harness-initiated save/export of ``fc_doc`` (``queries.export_step``) so a highlight is never written
    to disk. Idempotent (nothing remembered is a no-op). No-op headless."""
    if not ACTIVE:
        return
    doc_name = fc_doc.Name
    clear_sheet_paint(fc_doc)
    for object_name, attr, original in _highlight_state.entries_for(doc_name):
        try:
            obj = fc_doc.getObject(object_name)
            if obj is None or obj.ViewObject is None:
                _highlight_state.discard(doc_name, object_name)  # gone: nothing left to restore
                continue
            # The REAL current topology count, never the property's own live length: confirmed against
            # real FreeCAD, a stale DiffuseColor/LineColorArray is left exactly as it was across a
            # topology change (a fillet, say) instead of being resized -- reading the property back would
            # never notice the mismatch ``highlight.restore_value`` needs to detect.
            current_length = len(obj.Shape.Faces) if attr == "DiffuseColor" else len(obj.Shape.Edges)
            setattr(obj.ViewObject, attr, highlight.restore_value(original, current_length))
            _highlight_state.discard(doc_name, object_name)
        except Exception:  # noqa: BLE001 - one bad restore must never block the rest or crash the worker
            sys.stderr.write(traceback.format_exc())
    Gui.updateGui()


def drop_highlight(fc_doc_name: str) -> None:
    """Forget ``fc_doc_name``'s remembered highlight WITHOUT attempting to restore anything -- called when
    the document is about to be deleted (``_HighlightWatcher.slotDeletedDocument`` below): there is
    nothing left in FreeCAD to restore a colour on. No-op if nothing was remembered for it."""
    _highlight_state.discard_document(fc_doc_name)
    import hole_colours  # noqa: PLC0415 - hole_colours imports this module lazily too

    hole_colours.forget(fc_doc_name)
    _sheet_paint.pop(fc_doc_name, None)  # the scenes go with the document's windows
    for key in [k for k in _sheet_colours if k[0] == fc_doc_name]:
        del _sheet_colours[key]


def highlight_entities(fc_doc: Any, targets: list[tuple[Any, str, int]]) -> None:
    """Temporarily recolour ``targets`` (``(object, "face"|"edge", index)``) with ``HIGHLIGHT_COLOR``.
    Always clears any highlight left over from a previous call on THIS document first (never another open
    document's). Groups by object so an object with two highlighted faces has its ``DiffuseColor`` read
    and written once, not twice. Never marks the document modified and never calls ``Gui.Selection`` (see
    the module note above)."""
    clear_highlight(fc_doc)
    if not ACTIVE or not targets:
        return
    doc_name = fc_doc.Name
    by_object: dict[Any, list[tuple[str, int]]] = {}
    for obj, kind, index in targets:
        by_object.setdefault(obj, []).append((kind, index))
    for obj, entries in by_object.items():
        view = obj.ViewObject
        if view is None:
            continue
        face_indices = [i for k, i in entries if k == "face"]
        edge_indices = [i for k, i in entries if k == "edge"]
        if face_indices:
            current = list(view.DiffuseColor)
            _highlight_state.remember(doc_name, obj.Name, "DiffuseColor", current)
            # ``current`` is a length-1 "uniform colour" list until now on a never-recoloured shape
            # (confirmed against real FreeCAD): expand it to one entry per face FIRST, or painting index
            # 0 into that length-1 list would set the shape's ONE colour and recolour the whole object.
            expanded = highlight.expand_to_length(current, len(obj.Shape.Faces))
            view.DiffuseColor = highlight.recolor(expanded, face_indices, HIGHLIGHT_COLOR)
        if edge_indices:
            current = list(view.LineColorArray)
            _highlight_state.remember(doc_name, obj.Name, "LineColorArray", current)
            expanded = highlight.expand_to_length(current, len(obj.Shape.Edges))
            view.LineColorArray = highlight.recolor(expanded, edge_indices, HIGHLIGHT_COLOR)
    Gui.updateGui()


# --- live selection (contract.SelectionProducer seam) ----------------------------------------------------------------
#
# ``Gui.Selection.getSelectionEx`` per document; ``queries.get_selection`` turns the raw (object, sub-element)
# pairs this returns into the harness's own stable refs -- this module only ever hands back FreeCAD's own
# names, never interprets them.


def selected_entities(fc_doc_name: str) -> list[tuple[str, str, str, str]]:
    """``(object_name, object_label, sub_element_name, type_id)`` for every selected object/sub-element in
    ``fc_doc_name`` -- ``type_id`` is the raw FreeCAD ``TypeId`` (e.g. ``"Sketcher::SketchObject"``,
    ``"App::Part"``), used only so ``queries.get_selection`` can explain an unresolvable pick (sketch,
    datum, assembly component, ...), never for identity. A whole-object selection (empty
    ``SubElementNames``) is reported once with ``sub_element_name`` ``""`` -- ``get_selection`` only turns
    a face/edge/vertex sub-element into a stable ref, so this always lands in its ``unresolved`` list.
    Headless: ``[]``."""
    if not ACTIVE:
        return []
    out: list[tuple[str, str, str, str]] = []
    for sel in Gui.Selection.getSelectionEx(fc_doc_name):
        obj = sel.Object
        if obj is None:
            continue
        subs = [sub for sub in sel.SubElementNames if sub]
        type_id = str(obj.TypeId)
        if not subs:
            out.append((obj.Name, obj.Label, "", type_id))
            continue
        for sub in subs:
            out.append((obj.Name, obj.Label, sub, type_id))
    return out


def select(fc_doc_name: str, obj_name: str, sub_name: str) -> None:
    """Test-only: select one sub-element programmatically, as if the engineer had clicked it (visible
    integration suite, ``queries.test_select_entity``). No-op headless."""
    if not ACTIVE:
        return
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(fc_doc_name, obj_name, sub_name)


def select_many(fc_doc_name: str, targets: list[tuple[str, str]]) -> None:
    """Test-only: select several ``(object, sub-element)`` pairs at once, as the engineer does with Ctrl held (the
    objects of a drawing page included). No-op headless."""
    if not ACTIVE:
        return
    Gui.Selection.clearSelection()
    for obj_name, sub_name in targets:
        Gui.Selection.addSelection(fc_doc_name, obj_name, sub_name)


def turn_camera(fc_doc: Any) -> None:
    """Test-only: turn and zoom the document's 3D view, as the engineer does with the mouse (``queries.test_hand_edit``).
    No-op headless."""
    if not ACTIVE:
        return
    view = _activate(fc_doc).ActiveView
    view.viewRear()
    view.fitAll()
    view.viewIsometric()


if ACTIVE:

    class _SelectionObserver:
        """``Gui.Selection.addObserver`` target: FreeCAD calls whichever of these methods it defines by
        name (duck-typed, no base class) on every selection change. Every hook pings the same debouncer
        regardless of what changed -- the settle callback always re-reads the full live selection."""

        def __init__(self, ping: Callable[[], None]) -> None:
            self._ping = ping

        def addSelection(self, *args: Any) -> None:  # noqa: N802 - FreeCAD observer API name
            self._ping()

        def removeSelection(self, *args: Any) -> None:  # noqa: N802 - FreeCAD observer API name
            self._ping()

        def clearSelection(self, *args: Any) -> None:  # noqa: N802 - FreeCAD observer API name
            self._ping()

    class _DocumentWatcher:
        """``App.addDocumentObserver`` target for the manual-edit seam: tells ``on_event(doc_name, cause, object)``
        that something happened to a document -- and to which object, when it knows: an object created or deleted, a
        property changed (``Visibility`` apart: display only); or only to the document: a recompute, an undo, a redo.
        Harness-issued or by hand alike; ``worker/watch.py`` tells the two apart (``fc.harness_busy()``) and decides
        whether anything of the design changed. Events from other threads (a TechDraw worker, say) are dropped: the
        watch is Qt-thread only."""

        def __init__(self, on_event: Callable[[str, str, str | None], None]) -> None:
            self._on_event = on_event

        def _tell(self, doc_name: Callable[[], str], cause: str, name: str | None = None) -> None:
            if threading.current_thread() is not threading.main_thread():
                return
            try:
                self._on_event(doc_name(), cause, name)
            except Exception:  # noqa: BLE001 - an observer must never raise into FreeCAD's own signal, nor an object being destroyed
                traceback.print_exc()

        def _tell_object(self, obj: Any, cause: str) -> None:
            # a drawing sheet recomputes its views on its own, for as long as it takes: the capture leaves its
            # objects out (``changes.capture``), so their events would only wake the comparison for nothing
            if not str(getattr(obj, "TypeId", "")).startswith("TechDraw::"):
                self._tell(lambda: str(obj.Document.Name), cause, str(obj.Name))

        def slotRecomputedDocument(self, doc: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            self._tell(lambda: str(doc.Name), "recompute")

        def slotCreatedObject(self, obj: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            self._tell_object(obj, "added")

        def slotDeletedObject(self, obj: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            self._tell_object(obj, "removed")

        def slotChangedObject(self, obj: Any, prop: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            if str(prop) not in changes.DISPLAY_ONLY:
                self._tell_object(obj, "changed")

        def slotUndoDocument(self, doc: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            self._tell(lambda: str(doc.Name), "undo")

        def slotRedoDocument(self, doc: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            self._tell(lambda: str(doc.Name), "redo")

    class _HighlightWatcher:
        """``App.addDocumentObserver`` target: keeps a
        highlight from ever outliving its document or leaking into a saved file.

        Method names and signatures are FreeCAD's own ``App::DocumentObserverPython`` slots, verified
        against ``src/App/DocumentObserverPython.h`` in the FreeCAD source (not guessed): ``doc`` is the
        ``App::Document`` and the two save slots also receive the PATH of the file being written (verified
        by experiment on 1.0.2 and 1.1.3 -- an earlier version of this
        docstring called it a transaction label; it is the file path, for ``save``/``saveAs`` and for
        ``saveCopy`` alike).

        - ``slotRecomputedDocument``: whatever was pointed at on a drawing sheet is taken off (``clear_sheet_paint``).
        - ``slotDeletedDocument``: the document is about to be destroyed -- drop its highlight bookkeeping
          without attempting to restore anything (nothing is left in FreeCAD to
          restore a colour on).
        - ``slotStartSaveDocument``: fires BEFORE the document is written to disk, whether the engineer
          pressed Ctrl+S in the GUI or any other save path (the harness's own ``persist.save_document``
          included) -- restore every highlighted colour first so a highlight is never persisted into the
          ``.FCStd`` file. Harness-initiated exports (``queries.export_step``)
          additionally clear before running, since a STEP export does not go through ``App::Document.save``
          at all and this slot would not fire for it.
        - ``slotFinishSaveDocument``: a save that the harness did NOT issue (``fc.harness_busy()`` is false
          outside ``Worker.dispatch``) is the engineer's own -- recorded with ``note_engineer_save`` so the
          next harness checkpoint knows the file changed because of it, not because another program wrote it.
        """

        def slotDeletedDocument(self, doc: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            drop_highlight(doc.Name)

        def slotRecomputedDocument(self, doc: Any) -> None:  # noqa: N802 - FreeCAD's own observer method name
            # the sheet changed (or the part under it): what was pointed at on it may no longer be where it was
            if doc.Name in _sheet_paint:
                clear_sheet_paint(doc)

        def slotStartSaveDocument(self, doc: Any, path: str) -> None:  # noqa: N802 - FreeCAD's own observer method name
            del path  # the file being written; the highlight is restored whatever the destination
            clear_highlight(doc)

        def slotFinishSaveDocument(self, doc: Any, path: str) -> None:  # noqa: N802 - FreeCAD's own observer method name
            if not fc.harness_busy():
                note_engineer_save(doc.Name, path)


def start_selection_watch(on_settled: Callable[[], None], *, debounce_s: float = SELECTION_DEBOUNCE_S) -> None:
    """Arm the ``Gui.Selection`` observer; ``on_settled`` fires ``debounce_s`` after the last change, on
    the Qt thread (a restarted single-shot ``QTimer`` -- ``Debouncer`` only holds the coalescing policy,
    unit-tested on its own with a fake scheduler in ``tests/unit/freecad_client``). No-op headless."""
    if not ACTIVE:
        return
    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(on_settled)

    def _schedule(delay_s: float, callback: Callable[[], None]) -> Any:
        # ``callback`` is always ``on_settled``, already connected above; restarting a running
        # single-shot QTimer reschedules it without firing early ("restart, latest wins").
        timer.start(int(delay_s * 1000))
        return timer

    def _cancel(handle: Any) -> None:
        handle.stop()

    debouncer = Debouncer(debounce_s, on_settled, schedule=_schedule, cancel=_cancel)
    observer = _SelectionObserver(debouncer.ping)
    Gui.Selection.addObserver(observer)
    _keep_alive.extend((timer, debouncer, observer))


def start_document_watch(on_event: Callable[[str, str, str | None], None]) -> None:
    """Arm the document-change observer (``contract.ManualEditProducer`` seam). No-op headless."""
    if not ACTIVE:
        return
    watcher = _DocumentWatcher(on_event)
    App.addDocumentObserver(watcher)
    _keep_alive.append(watcher)


def timer_scheduler() -> tuple[Callable[[float, Callable[[], None]], Any], Callable[[Any], None]]:
    """``(schedule, cancel)`` for ``watch.ManualEditWatch`` (and ``Debouncer``): one single-shot ``QTimer`` per
    callback, restarted by every ``schedule`` ("latest wins") and delivering on the Qt thread. Used by the visible
    worker only."""
    timers: dict[Any, Any] = {}

    def schedule(delay_s: float, callback: Callable[[], None]) -> Any:
        timer = timers.get(callback)
        if timer is None:
            timer = QtCore.QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(callback)
            timers[callback] = timer
            _keep_alive.append(timer)
        timer.start(int(delay_s * 1000))
        return timer

    def cancel(handle: Any) -> None:
        handle.stop()

    return schedule, cancel


def start_highlight_watch() -> None:
    """Arm ``_HighlightWatcher`` (drop-on-delete, clear-before-save -- see its docstring). No-op headless:
    a headless worker never highlights anything, so there is never anything to drop or clear."""
    if not ACTIVE:
        return
    watcher = _HighlightWatcher()
    App.addDocumentObserver(watcher)
    _keep_alive.append(watcher)


# --- request loop ---------------------------------------------------------------------------------------------------

if ACTIVE:

    class _Bridge(QtCore.QObject):
        """Emitted from the reader thread, delivered in the Qt thread (AutoConnection -> queued)."""

        line_received = QtCore.Signal(str)
        eof_received = QtCore.Signal()


def serve(on_line: Callable[[str], None], on_eof: Callable[[], None]) -> None:
    """Arm the stdin reader; returns at once so FreeCAD's ``QApplication.exec()`` keeps running."""
    bridge = _Bridge()
    bridge.line_received.connect(on_line)
    bridge.eof_received.connect(on_eof)

    def reader() -> None:
        # a private, buffered handle on fd 0: FreeCAD may swap ``sys.stdin`` for its console object
        stream = os.fdopen(os.dup(0), "rb")
        while True:
            try:
                raw = stream.readline()
            except (OSError, ValueError):
                raw = b""
            if not raw:
                bridge.eof_received.emit()
                return
            bridge.line_received.emit(raw.decode("utf-8", errors="replace"))

    thread = threading.Thread(target=reader, name="cotaforge-stdin", daemon=True)
    _keep_alive.extend((bridge, thread))
    thread.start()


def shutdown(close_documents: Callable[[], None], flush: Callable[[], None]) -> None:
    """Close every document, flush the reply channel, quit the application. Idempotent."""
    global _shutting_down  # noqa: PLW0603
    if _shutting_down:
        return
    _shutting_down = True
    try:
        close_documents()
    except Exception:  # noqa: BLE001 - never let a close failure keep the process alive
        sys.stderr.write(traceback.format_exc())
    try:
        flush()
    except Exception:  # noqa: BLE001
        pass
    if ACTIVE:
        QtWidgets.QApplication.quit()
    else:
        os._exit(0)
