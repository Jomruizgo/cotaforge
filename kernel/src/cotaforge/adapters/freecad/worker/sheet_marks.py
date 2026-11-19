# ruff: noqa: E501
"""The marks of a sheet that are FreeCAD's own TechDraw objects: weld symbols, balloons, tables and leaders (no FreeCAD
import, unit-testable on its own).

An item says WHAT it asks of TechDraw (these dataclasses, in page mm, y up); ``sheet_native_marks`` makes the objects. The
engineer can then edit them in FreeCAD: the weld tiles in the weld symbol dialog, the number of a balloon, the cells of the
parts list in its spreadsheet. They hang on a TechDraw view (balloons, leaders and welds) or stand alone on the page (a table),
so they follow the view when it is moved.

WELDS. FreeCAD ships the AWS weld symbol set (``Mod/TechDraw/Symbols/Welding/AWS``): the symbol of ISO 2553 maps to the AWS
tile of the same weld, the size and the length are the text before and after the tile. What the AWS form cannot carry goes to
the tail text of the weld symbol (it is painted by TechDraw): the throat ``a`` of ISO 2553 (AWS gives the leg), the seam
length and pitch, the number of identical joints, the side. ISO 2553 draws the weld of the other side under a dashed
identification line; AWS puts the arrow side below the reference line and the other side above it, with no dashed line.
"""

from dataclasses import dataclass

Point = tuple[float, float]

# ISO 2553 weld -> the AWS tile file of FreeCAD's library (relative to ``Mod/TechDraw/Symbols``). In the AWS convention the
# ``Down`` file is the weld of the arrow side (under the reference line), the ``Up`` file the weld of the other side.
AWS_TILES: dict[str, dict[str, str]] = {
    "fillet": {"arrow": "Welding/AWS/filletDown.svg", "other": "Welding/AWS/filletUp.svg"},
    "v": {"arrow": "Welding/AWS/VDown.svg", "other": "Welding/AWS/VUp.svg"},
    "square": {"arrow": "Welding/AWS/SquareDown.svg", "other": "Welding/AWS/SquareUp.svg"},
}
# TechDraw's ``TileRow`` of each side. Measured in 1.1.3 (the sheet window, in four weld symbols, left- and right-going, the arrow
# above and below the line): row 0 is painted ABOVE the reference line and row -1 BELOW it, whatever the way the arrow comes. AWS
# puts the arrow side below the line (``filletDown.svg``, whose horizontal leg is on the line) and the other side above it. NT4 took
# row 0 for the arrow side and painted every one-sided weld on the wrong side of the joint.
TILE_ROW = {"arrow": -1, "other": 0}


@dataclass(frozen=True)
class NativeWeld:
    """One weld symbol: a ``DrawLeaderLine`` along the reference line from ``anchor`` (``length`` mm towards ``sign``: +1 right, -1
    left) with a ``DrawWeldSymbol`` on it (the AWS tiles of ``kind`` for the ``sides`` asked, the flags: all round, field, and the
    tail text), and an arrow ``DrawLeaderLine`` from ``anchor`` to each of the ``tips`` (the joints the symbol is for)."""

    item: str
    view: str  # front | top | right | isometric
    tips: tuple[Point, ...]
    anchor: Point
    sign: float
    length: float
    kind: str  # fillet | v | square
    sides: str  # arrow | other | both
    left: str  # the text before the symbol: the size (a3, z4, s6)
    right: str  # the text after it: the length of a segment (and the pitch)
    tail: str  # what the AWS form does not carry and fits the tail: the length and pitch, the number of identical joints
    words: str = ""  # the whole weld in words (the label of the weld symbol)
    all_around: bool = False
    field: bool = False


@dataclass(frozen=True)
class NativeBalloon:
    """One ``DrawViewBalloon`` of ``view``: ``text`` in a circle at ``centre``, its leader ending on ``dot`` (page mm)."""

    item: str
    view: str
    text: str
    centre: Point
    dot: Point


@dataclass(frozen=True)
class NativeTable:
    """A ``DrawViewSpreadsheet`` fed by a ``Spreadsheet`` of the document: ``cells`` (row by row, the header first), the
    ``widths`` of the columns and the ``heights`` of the rows (mm), the text size, and the table's right and bottom edge on
    the page. The engineer edits the cells in the spreadsheet."""

    item: str
    name: str
    cells: tuple[tuple[str, ...], ...]
    widths: tuple[float, ...]
    heights: tuple[float, ...]
    right: float
    bottom: float
    text_mm: float = 3.0


@dataclass(frozen=True)
class NativeLeader:
    """A ``DrawLeaderLine`` of ``view`` through the page points ``points`` (the first end carries the arrowhead when ``arrow``)."""

    item: str
    view: str
    points: tuple[Point, ...]
    arrow: bool = True


@dataclass(frozen=True)
class NativeRevisionMark:
    """The revision marker next to a changed dimension: FreeCAD's own ``misc/Revision_Triangle`` symbol (a ``DrawViewSymbol``
    of the library) centred on the page point ``at``, its editable text (``ID``) the revision letter."""

    item: str
    letter: str
    at: Point
    scale: float = 0.6


MARK_TYPES = (NativeWeld, NativeBalloon, NativeTable, NativeLeader, NativeRevisionMark)
WHOLE = (NativeWeld, NativeBalloon, NativeTable, NativeRevisionMark)  # the item is nothing but these: no entity of it stays as a drawing


def count(n: object) -> int:
    """How many objects of the page ``n`` is (the objects the page lists: the leaders and the weld symbol, the balloon, the
    table's view; the tiles and the spreadsheet hang on them)."""
    if isinstance(n, NativeWeld):
        return 2 + len(n.tips)  # the reference line's leader, the weld symbol, an arrow leader per joint
    return 1


def dump(n: object) -> list[object]:
    """What ``n`` must be, as plain data (the signature of its unit)."""

    def pts(points: tuple[Point, ...]) -> list[list[float]]:
        return [[round(p[0], 2), round(p[1], 2)] for p in points]

    if isinstance(n, NativeWeld):
        return ["w", n.view, pts(n.tips), pts((n.anchor,)), n.sign, round(n.length, 2), n.kind, n.sides, n.left, n.right, n.tail, n.words, n.all_around, n.field]
    if isinstance(n, NativeBalloon):
        return ["b", n.view, n.text, pts((n.centre, n.dot))]
    if isinstance(n, NativeTable):
        return ["t", n.name, [list(r) for r in n.cells], [round(w, 2) for w in n.widths], [round(h, 2) for h in n.heights], round(n.right, 2), round(n.bottom, 2), n.text_mm]
    if isinstance(n, NativeLeader):
        return ["l", n.view, pts(n.points), n.arrow]
    if isinstance(n, NativeRevisionMark):
        return ["r", n.letter, pts((n.at,)), n.scale]
    msg = f"not a mark: {n!r}"
    raise TypeError(msg)
