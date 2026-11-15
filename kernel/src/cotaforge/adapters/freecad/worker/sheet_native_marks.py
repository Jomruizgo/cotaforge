# ruff: noqa: E501
"""Makes the TechDraw objects of the marks (``sheet_marks``): leader lines, weld symbols with their AWS tiles, balloons and
the spreadsheet view of a table. Every object is the engineer's to edit in FreeCAD; they hang on a view (they follow it when it
is moved) or, for a table, stand on the page.

Measured in FreeCAD 1.1.3: the position of a leader (``X``, ``Y``) is relative to its parent view and in the view's own units
(page mm divided by the view's scale); its way points are relative to that position, in page mm (not scaled), ``x`` right and
``y`` DOWN, the first one carrying the arrowhead. A balloon's
``X``, ``Y`` (the circle) and ``OriginX``, ``OriginY`` (the end of its leader) are relative to its view, in the view's own units
(page mm divided by the view's scale), y up.
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


GUIDE_MARGIN_MM = 7.0  # the window pulls an object whose origin is within 6 mm of a view's origin on to it (measured; keep clear by 7)
VIEW_KINDS = ("TechDraw::DrawViewPart", "TechDraw::DrawProjGroup", "TechDraw::DrawProjGroupItem")


def guides(page: Any) -> tuple[list[float], list[float]]:
    """The x and y (page mm) of the origins of the page's views: where the window pulls an object that comes near them."""
    import sheet_native  # noqa: PLC0415 - sheet_native imports this module lazily too

    xs: list[float] = []
    ys: list[float] = []
    for obj in page.Views:
        found = [obj, *(obj.Views if str(obj.TypeId) == "TechDraw::DrawProjGroup" else [])]
        for view in found:
            if str(view.TypeId) in VIEW_KINDS:
                x, y = sheet_native.page_position(page, view)
                xs.append(x)
                ys.append(y)
    return xs, ys


def free_origin(page: Any, points: tuple[sm.Point, ...]) -> sm.Point:
    """The point of ``points`` a leader is positioned at: the first (the arrow tip) unless a view's origin is within the pull of the
    window of it. The weld symbol hangs on its leader's origin and the window pulls it on to such a guide (measured in 1.1.3: the
    symbol of the frame's weld sat 3.8 mm off its reference line, on the y of the isometric view): the first point clear of every
    guide, else the first."""
    xs, ys = guides(page)
    for p in points:
        if all(abs(p[0] - x) > GUIDE_MARGIN_MM for x in xs) and all(abs(p[1] - y) > GUIDE_MARGIN_MM for y in ys):
            return p
    return points[0]


def leader(
    fc_doc: Any, page: Any, frame: Any, points: tuple[sm.Point, ...], name: str, arrow: bool = True, origin: sm.Point | None = None
) -> Any:
    """A ``DrawLeaderLine`` on ``frame``'s view through the page points ``points`` (the first one is the arrow tip), positioned at
    ``origin`` (the tip unless asked: the way points are relative to it)."""
    obj = fc_doc.addObject(LEADER_TYPE, name)
    obj.LeaderParent = frame.view
    page.addView(obj)
    tip = origin or points[0]
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
    """The weld of ``n``: the leader of its reference line with the weld symbol on it (one AWS tile per side asked) and an arrow
    leader from the reference line to each joint the weld is for. The reference line is its own leader so that it can be put at a
    point clear of the views' origins: the window pulls the weld symbol on to a view's origin that comes near the origin of its
    leader (measured in 1.1.3: 3.8 mm off its reference line, on the y of the isometric view), and a leader draws its arrowhead at
    its origin, so the tip cannot be moved off it."""
    resource = sheet_symbols.resource_dir()
    sides = ("arrow", "other") if n.sides == "both" else (n.sides,)
    files = {side: _tile_file(resource, sm.AWS_TILES[n.kind][side]) for side in sides}  # fail before anything is made
    end = (n.anchor[0] + n.sign * n.length, n.anchor[1])
    line = (n.anchor, end)
    lead = leader(fc_doc, page, frame, line, _name(page, key, "weld_leader"), arrow=False, origin=free_origin(page, line))
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
        # AWS puts the size left of the symbol and the length right of it, on the reference line: those are the tile's own texts
        # and the tail keeps the process and notes only. A tile paints its texts in the colour of the window's theme (white in the
        # dark one, measured); ``gui.ink_black`` makes them black before the window is shown and before every export, because the
        # sheet the welder reads is the exported one, not the window.
        tile.LeftText = n.left
        tile.RightText = n.right
    fc_doc.recompute()
    objects = [lead, symbol]
    for number, tip in enumerate(n.tips, start=1):
        objects.append(leader(fc_doc, page, frame, (tip, n.anchor), _name(page, key, f"weld_arrow{number}")))
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
    # The bubble and the origin of its leader are in the view's own units (page mm divided by the view's scale), as a leader's
    # position is: measured in 1.1.3 with a view at 1:5, a balloon given in page mm sat 5 times too near the view's origin, on
    # the part, with a leader of no length.
    dx, dy = _view_xy(frame, n.centre)
    ox, oy = _view_xy(frame, n.dot)
    obj.X, obj.Y = dx / frame.scale, dy / frame.scale
    obj.OriginX, obj.OriginY = ox / frame.scale, oy / frame.scale
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
