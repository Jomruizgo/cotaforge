# ruff: noqa: E501
"""What a shop reader finds wrong on the page of a sheet, read from the TechDraw objects the page holds (the review reads the
composed sheet; what the engineer edits, and what TechDraw paints and exports, is the page).

``read_page`` reads the page's own objects (duck-typed: it imports no FreeCAD) into plain rows; the checks are plain functions
of those rows and unit-tested without FreeCAD:

* ``marker_texts``: no cell or text of the sheet reads like a proof or placeholder text (a hand-edit proof left on the sheet
  that is exported);
* ``cell_overflows``: no text of a table cell is wider than its column;
* ``balloon_problems``: the positions of the parts list and the balloons match one to one, and every balloon has a leader of
  non-zero length that ends on its own part;
* ``label_problems``: the label of a view goes under its own view, never nearer to another one.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any

from sheet_model import Arc, Circle, Line, Polyline, Text, text_width

PX_MM = 25.4 / 96.0  # a spreadsheet column width is in pixels at 96 dpi
MIN_LEADER_MM = 3.0  # a leader shorter than this (past the circle) is not seen
MARKER = re.compile(
    r"\[test\]|\btest(?:ing)?\b|\blorem\b|\bipsum\b|\bfixme\b|\btodo\b|\bx{3,}\b|\basdf\b|\bplaceholder\b|\bdummy\b"
    r"|\beditad[ao]s?\b|\bedited\b",
    re.IGNORECASE,
)
Rect = tuple[float, float, float, float]


@dataclass(frozen=True)
class PageText:
    """One text the page holds: ``where`` says in which object (for the finding), ``text`` is what it says."""

    where: str
    text: str


@dataclass(frozen=True)
class PageCell:
    """A cell of a table on the page: its text, the width of its column and the text size, in mm."""

    where: str
    text: str
    width_mm: float
    size_mm: float
    column: str = ""


@dataclass(frozen=True)
class PageBalloon:
    """A balloon as TechDraw holds it: ``bubble`` and ``origin`` are in the view's own units (page mm divided by ``scale``)."""

    name: str
    text: str
    view: str
    scale: float
    bubble: tuple[float, float]
    origin: tuple[float, float]
    radius_mm: float = 3.5


@dataclass
class PageRead:
    texts: list[PageText] = field(default_factory=list)
    cells: list[PageCell] = field(default_factory=list)
    balloons: list[PageBalloon] = field(default_factory=list)
    positions: list[str] = field(default_factory=list)  # the first column of the parts or cut list: the numbers it lists
    tile_texts: list[str] = field(default_factory=list)  # the texts of the tiles of the weld symbols (the size, the length): how a symbol is found in the window


# --- reading the page ----------------------------------------------------------------------------------------------------------


def _lines(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    try:
        return [str(v) for v in value]
    except TypeError:
        return [str(value)]


def _cell_text(raw: str) -> str:
    return raw[1:] if raw.startswith("'") else raw


def read_page(page: Any, view_name: Any) -> PageRead:
    """The texts, table cells and balloons of ``page``. ``view_name(obj)`` gives the name the sheet knows a TechDraw view by
    (``front``, ``isometric``...) or ``""``."""
    out = PageRead()
    doc = page.Document
    on_page = {str(v.Name) for v in page.Views}
    for obj in doc.Objects:
        kind = str(obj.TypeId)
        label = str(obj.Label)
        if kind == "TechDraw::DrawViewAnnotation" and obj.Name in on_page:
            out.texts += [PageText(f"the note {label}", line) for line in _lines(obj.Text)]
        elif kind == "TechDraw::DrawWeldSymbol" and obj.Name in on_page:
            out.texts.append(PageText(f"the tail of {label}", str(obj.TailText)))
        elif kind == "TechDraw::DrawTileWeld":
            parent = getattr(obj, "TileParent", None)
            if parent is not None and str(parent.Name) in on_page:
                for prop in ("LeftText", "RightText", "CenterText"):
                    out.texts.append(PageText(f"{prop} of a tile of {parent.Label}", str(getattr(obj, prop, ""))))
                    if str(getattr(obj, prop, "")).strip():
                        out.tile_texts.append(str(getattr(obj, prop)).strip())
        elif kind == "TechDraw::DrawViewBalloon" and obj.Name in on_page:
            out.texts.append(PageText(f"the balloon {label}", str(obj.Text)))
            source = getattr(obj, "SourceView", None)
            if source is not None:
                out.balloons.append(
                    PageBalloon(
                        str(obj.Name),
                        str(obj.Text),
                        str(view_name(source)),
                        float(source.getScale()),
                        (float(obj.X), float(obj.Y)),
                        (float(obj.OriginX), float(obj.OriginY)),
                    )
                )
        elif kind == "TechDraw::DrawViewSpreadsheet" and obj.Name in on_page:
            _read_table(out, obj)
    return out


def _read_table(out: PageRead, view: Any) -> None:
    sheet = getattr(view, "Source", None)
    if sheet is None:
        return
    size_mm = float(view.TextSize) * PX_MM
    columns: dict[str, float] = {}
    for address in sheet.getNonEmptyCells():
        letters = "".join(c for c in str(address) if c.isalpha())
        if letters not in columns:
            columns[letters] = float(sheet.getColumnWidth(letters)) * PX_MM
        text = _cell_text(str(sheet.getContents(address)))
        out.cells.append(PageCell(f"cell {address} of {sheet.Label}", text, columns[letters], size_mm, letters))
        out.texts.append(PageText(f"cell {address} of {sheet.Label}", text))
        if letters == "A" and text.strip().isdigit():
            out.positions.append(text.strip())


# --- the checks ------------------------------------------------------------------------------------------------------------------


def marker_texts(texts: list[PageText]) -> list[PageText]:
    """The texts that read like a proof or a placeholder (``Pos. editada``, ``test``, ``TODO``...)."""
    return [t for t in texts if t.text.strip() and MARKER.search(t.text)]


def cell_overflows(cells: list[PageCell]) -> list[tuple[PageCell, float]]:
    """The cells whose text is wider than their column, with the width the text takes (mm)."""
    found = []
    for cell in cells:
        width = text_width(cell.text, cell.size_mm)
        if cell.text.strip() and width > cell.width_mm + 0.01:
            found.append((cell, round(width, 1)))
    return found


@dataclass
class BalloonProblems:
    no_leader: list[tuple[str, float]] = field(default_factory=list)  # (balloon text, visible length of its leader, mm)
    off_part: list[str] = field(default_factory=list)  # balloon texts whose leader ends off their own part
    no_balloon: list[str] = field(default_factory=list)  # listed positions that have no balloon
    no_row: list[str] = field(default_factory=list)  # balloons that name no listed position
    twice: list[str] = field(default_factory=list)  # positions with more than one balloon

    def __bool__(self) -> bool:
        return any((self.no_leader, self.off_part, self.no_balloon, self.no_row, self.twice))


def leader_mm(balloon: PageBalloon) -> float:
    """The length of the leader that shows: from the edge of the circle to the dot, in page mm."""
    dx = (balloon.bubble[0] - balloon.origin[0]) * balloon.scale
    dy = (balloon.bubble[1] - balloon.origin[1]) * balloon.scale
    return max(0.0, math.hypot(dx, dy) - balloon.radius_mm)


def _key(text: str) -> str:
    return text.strip()


def balloon_problems(
    balloons: list[PageBalloon], positions: list[str], on_part: dict[str, bool | None] | None = None
) -> BalloonProblems:
    """``positions`` of the list and the ``balloons`` of the page agree one to one; every balloon has a visible leader
    (``MIN_LEADER_MM`` past its circle) and, where the geometry can say (``on_part``: balloon name -> whether its dot lies on its own
    part, ``None`` when it cannot be told), ends on its part."""
    found = BalloonProblems()
    listed = [_key(p) for p in positions]
    texts = [_key(b.text) for b in balloons]
    found.no_balloon = sorted({p for p in listed if p not in texts}, key=_natural)
    found.no_row = sorted({t for t in texts if t not in listed}, key=_natural)
    found.twice = sorted({t for t in texts if texts.count(t) > 1}, key=_natural)
    for b in balloons:
        length = leader_mm(b)
        if length < MIN_LEADER_MM:
            found.no_leader.append((_key(b.text), round(length, 1)))
        if on_part is not None and on_part.get(b.name) is False:
            found.off_part.append(_key(b.text))
    return found


def _natural(text: str) -> tuple[int, str]:
    return (int(text), text) if text.isdigit() else (10**9, text)


# --- a mark that runs over itself -------------------------------------------------------------------------------------------------


def _text_rect(e: Text, shrink: float) -> Rect:
    width = text_width(e.text, e.height)
    x0 = e.x - (width / 2 if e.anchor == "center" else width if e.anchor == "right" else 0.0)
    return (x0 + shrink, e.y + shrink, x0 + width - shrink, e.y + e.height - shrink)


def _segments(e: Any) -> list[tuple[float, float, float, float]]:
    if isinstance(e, Line):
        return [(e.x1, e.y1, e.x2, e.y2)]
    points = list(getattr(e, "points", ()))
    if not points or isinstance(e, Text | Circle | Arc):
        return []
    closed = bool(getattr(e, "closed", True)) if isinstance(e, Polyline) else True  # a solid is a closed outline
    ring = points + ([points[0]] if closed else [])
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(ring, ring[1:], strict=False)]


def _boxes_overlap(a: Rect, b: Rect) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def self_overlaps(entities: Any, shrink: float = 0.3) -> list[str]:
    """The texts of one mark that a line of the same mark runs through, or that stand over another text of the same mark (the label
    of an arrow printed over its shaft, a draft value over the label): ink on ink, unreadable. The box of a text is taken ``shrink``
    mm smaller all round: touching is not crossing."""
    from sheet_collide import segment_hits_rect  # noqa: PLC0415

    texts = [e for e in entities if isinstance(e, Text) and e.text.strip()]
    lines = [s for e in entities for s in _segments(e)]
    found: list[str] = []
    for i, t in enumerate(texts):
        box = _text_rect(t, shrink)
        crossed = any(segment_hits_rect(s, box) for s in lines)
        over = any(_boxes_overlap(box, _text_rect(o, shrink)) for j, o in enumerate(texts) if j != i)
        if crossed or over:
            found.append(t.text)
    return found


# --- labels under their views ----------------------------------------------------------------------------------------------------

def _gap(a: Rect, b: Rect) -> float:
    dx = max(a[0] - b[2], 0.0, b[0] - a[2])
    dy = max(a[1] - b[3], 0.0, b[1] - a[3])
    return math.hypot(dx, dy)


OFFSET_MM = 0.5  # a weld symbol this far from its reference line (in the window) is off it


def weld_offsets(measured: list[tuple[str, float, float]]) -> list[tuple[str, float]]:
    """The weld symbols that stand off their reference line: ``(a tile text of the symbol, how far in mm)``."""
    return [(text, round(math.hypot(dx, dy), 1)) for text, dx, dy in measured if math.hypot(dx, dy) > OFFSET_MM]


def label_problems(labels: dict[str, Rect], views: dict[str, Rect]) -> list[tuple[str, str, float, float]]:
    """The labels that stand nearer to another view than to their own: ``(view, nearer view, gap to its own, gap to the other)``.
    ``labels`` maps a view's name to the box of its label, ``views`` every view's name to its box."""
    found = []
    for name, box in labels.items():
        if name not in views:
            continue
        own = _gap(box, views[name])
        for other, other_box in views.items():
            if other != name and _gap(box, other_box) <= own + 1e-6:
                found.append((name, other, round(own, 1), round(_gap(box, other_box), 1)))
                break
    return found
