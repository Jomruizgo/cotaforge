# ruff: noqa: E501
"""Makes the TechDraw objects of the marks (``sheet_marks``): leader lines, weld symbols with their AWS tiles, balloons and
the spreadsheet view of a table. Every object is the engineer's to edit in FreeCAD; they hang on a view (they follow it when it
is moved) or, for a table, stand on the page.

Measured in FreeCAD 1.1.3: the position of a leader (``X``, ``Y``) is relative to its parent view and in the view's own units
(page mm divided by the view's scale); its way points are relative to that position, in page mm (not scaled), ``x`` right and
``y`` DOWN, the first one carrying the arrowhead. A balloon's
``X``, ``Y`` (the circle) and ``OriginX``, ``OriginY`` (the end of its leader) are relative to its view, in page mm, y up.
"""

import os
from typing import Any

import FreeCAD as App
import sheet_marks as sm
import sheet_symbols

PX_MM = 25.4 / 96.0  # a spreadsheet column width or row height is in pixels at 96 dpi
GROUP = "Cotaforge"
BALLOON_FONT_MM = 3.5
LEADER_TYPE = "TechDraw::DrawLeaderLine"
WELD_TYPE = "TechDraw::DrawWeldSymbol"
TILE_TYPE = "TechDraw::DrawTileWeld"
BALLOON_TYPE = "TechDraw::DrawViewBalloon"
TABLE_TYPE = "TechDraw::DrawViewSpreadsheet"
SHEET_TYPE = "Spreadsheet::Sheet"
COLUMNS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _name(page: Any, key: str, part: str) -> str:
    import re  # noqa: PLC0415

    return re.sub(r"[^A-Za-z0-9_]", "_", f"{page.Name}_{key}_{part}")


def _view_xy(frame: Any, p: sm.Point) -> tuple[float, float]:
    return p[0] - frame.x, p[1] - frame.y


def leader(fc_doc: Any, page: Any, frame: Any, points: tuple[sm.Point, ...], name: str, arrow: bool = True) -> Any:
    """A ``DrawLeaderLine`` on ``frame``'s view through the page points ``points`` (the first one is the arrow tip)."""
    obj = fc_doc.addObject(LEADER_TYPE, name)
    obj.LeaderParent = frame.view
    page.addView(obj)
    tip = points[0]
    dx, dy = _view_xy(frame, tip)
    obj.X, obj.Y = dx / frame.scale, dy / frame.scale  # the position is in the view's own units, the way points in page mm
    obj.AutoHorizontal = False  # the way points are the sheet's: the last segment is the reference line of a weld
    obj.WayPoints = [App.Vector(p[0] - tip[0], -(p[1] - tip[1]), 0.0) for p in points]
    obj.StartSymbol = "Filled arrow" if arrow else "None"
    return obj


def _tile_file(resource: str, relative: str) -> str:
    path = os.path.join(resource, "Mod", "TechDraw", "Symbols", *relative.split("/"))
    if not os.path.isfile(path):
        msg = f"FreeCAD's weld symbols have no {relative} (looked in {path})"
        raise LookupError(msg)
    return path


def weld(fc_doc: Any, page: Any, frame: Any, n: sm.NativeWeld, key: str) -> list[Any]:
    """The weld of ``n``: its leader, the weld symbol on it with one AWS tile per side asked, and a plain leader to every
    other joint the same weld is for."""
    resource = sheet_symbols.resource_dir()
    sides = ("arrow", "other") if n.sides == "both" else (n.sides,)
    files = {side: _tile_file(resource, sm.AWS_TILES[n.kind][side]) for side in sides}  # fail before anything is made
    end = (n.anchor[0] + n.sign * n.length, n.anchor[1])
    lead = leader(fc_doc, page, frame, (n.tips[0], n.anchor, end), _name(page, key, "weld_leader"))
    symbol = fc_doc.addObject(WELD_TYPE, _name(page, key, "weld"))
    page.addView(symbol)
    symbol.Leader = lead
    symbol.AllAround = n.all_around
    symbol.FieldWeld = n.field
    symbol.TailText = n.tail
    symbol.Label = n.words or symbol.Label
    vo = getattr(symbol, "ViewObject", None)
    if vo is not None:  # the 3.5 mm of ISO 3098, as every other text of the sheet (the default is 5 mm)
        for prop in ("FontSize", "TileFontSize"):
            if hasattr(vo, prop):
                setattr(vo, prop, BALLOON_FONT_MM)
    # A weld symbol makes its two tiles itself (row 0, the arrow side, and row -1, the other side) and draws only the first two
    # tiles it finds: a tile added to them is never painted (measured in 1.1.3). So the tiles are the ones it made.
    tiles = {int(t.TileRow): t for t in symbol.InList if str(t.TypeId) == TILE_TYPE}
    for side in sides:
        tile = tiles[sm.TILE_ROW[side]]
        tile.SymbolFile = files[side]
        # The tile texts (the size before the symbol, the length after it) are left empty: the tile paints them in the colour of
        # the window's theme (white in the dark one, measured), where the tail text of the symbol is always black; the engineer
        # can write them in the Weld Symbol dialog. The tail text carries the size, the length and the number of joints.
    fc_doc.recompute()
    objects = [lead, symbol]
    for number, tip in enumerate(n.tips[1:], start=2):
        objects.append(leader(fc_doc, page, frame, (tip, n.anchor), _name(page, key, f"weld_leader{number}")))
    return objects


def balloon(fc_doc: Any, page: Any, frame: Any, n: sm.NativeBalloon, key: str, number: int) -> Any:
    """A ``DrawViewBalloon`` on ``frame``'s view: ``n.text`` in a circle at ``n.centre``, its leader ending in a dot on ``n.dot``."""
    obj = fc_doc.addObject(BALLOON_TYPE, _name(page, key, f"balloon{number}"))
    page.addView(obj)
    obj.SourceView = frame.view
    obj.Text = n.text
    obj.BubbleShape = "Circular"
    obj.EndType = "Dot"
    obj.KinkLength = 0.0
    obj.X, obj.Y = _view_xy(frame, n.centre)
    obj.OriginX, obj.OriginY = _view_xy(frame, n.dot)
    vo = getattr(obj, "ViewObject", None)
    if vo is not None and hasattr(vo, "Fontsize"):
        vo.Fontsize = BALLOON_FONT_MM
    return obj


def table(fc_doc: Any, page: Any, n: sm.NativeTable, key: str) -> Any:
    """A ``DrawViewSpreadsheet`` fed by a ``Spreadsheet`` of the document: the cells are the engineer's to edit there."""
    data = fc_doc.addObject(SHEET_TYPE, _name(page, key, "cells"))
    data.Label = f"{key} {n.name}"
    for row, cells in enumerate(n.cells, start=1):
        for column, text in enumerate(cells):
            data.set(f"{COLUMNS[column]}{row}", "'" + text)  # a leading apostrophe: the text, never a formula
        data.setRowHeight(str(row), round(n.heights[row - 1] / PX_MM))
    for column, width in enumerate(n.widths):
        data.setColumnWidth(COLUMNS[column], round(width / PX_MM))
    fc_doc.recompute()
    obj = fc_doc.addObject(TABLE_TYPE, _name(page, key, "table"))
    page.addView(obj)
    obj.Source = data
    obj.CellStart = "A1"
    obj.CellEnd = f"{COLUMNS[len(n.widths) - 1]}{len(n.cells)}"
    obj.TextSize = n.text_mm / PX_MM
    obj.X = n.right - sum(n.widths) / 2
    obj.Y = n.bottom + sum(n.heights) / 2
    return obj


def make(fc_doc: Any, page: Any, frames: dict[str, Any], marks: list[Any], key: str, sheet: str, item: str) -> list[Any]:
    """Make every mark of a unit; returns the objects the page lists (each is tagged for the sheet and its item)."""
    import sheet_native  # noqa: PLC0415 - sheet_native imports this module lazily too

    objects: list[Any] = []
    balloons = 0
    before = {o.Name for o in fc_doc.Objects}
    try:
        for n in marks:
            if isinstance(n, sm.NativeWeld):
                made = weld(fc_doc, page, frames[n.view], n, key)
            elif isinstance(n, sm.NativeBalloon):
                balloons += 1
                made = [balloon(fc_doc, page, frames[n.view], n, key, balloons)]
            elif isinstance(n, sm.NativeTable):
                made = [table(fc_doc, page, n, key)]
            else:
                made = [leader(fc_doc, page, frames[n.view], n.points, _name(page, key, f"leader{len(objects) + 1}"), n.arrow)]
            objects += made
    except Exception:
        for obj in reversed(list(fc_doc.Objects)):  # nothing half made stays on the page
            if obj.Name not in before and fc_doc.getObject(obj.Name) is not None:
                fc_doc.removeObject(obj.Name)
        raise
    for obj in objects:
        sheet_native.tag(obj, key, item, sheet)
    return objects


def remove_extras(fc_doc: Any, obj: Any) -> None:
    """Delete what hangs on ``obj`` and the page does not list, before ``obj`` goes: the tiles of a weld symbol, the spreadsheet a
    table shows (when nothing else shows it)."""
    kind = str(obj.TypeId)
    if kind == WELD_TYPE:
        for dependant in list(obj.InList):
            if str(dependant.TypeId) == TILE_TYPE and fc_doc.getObject(dependant.Name) is not None:
                fc_doc.removeObject(dependant.Name)
    elif kind == TABLE_TYPE:
        source = getattr(obj, "Source", None)
        source_name = None if source is None else str(source.Name)
        fc_doc.removeObject(obj.Name)
        if source_name is not None and fc_doc.getObject(source_name) is not None and not fc_doc.getObject(source_name).InList:
            fc_doc.removeObject(source_name)
        return
    fc_doc.removeObject(obj.Name)
