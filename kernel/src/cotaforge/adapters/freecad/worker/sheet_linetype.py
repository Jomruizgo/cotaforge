# ruff: noqa: E501
"""The line type of a centre line or a cosmetic edge of a TechDraw view, and the two edges a centre line hangs from (no
FreeCAD import).

What TechDraw 1.1.3 does, measured in the window and in the dash pattern of the exported PDF:

* A ``CenterLine`` and a ``CosmeticEdge`` carry TWO style fields. ``Format["style"]`` is a Qt pen style and is ignored
  whenever the other one, the LINE NUMBER, is valid. The line number is what the window paints and the PDF exports, the
  Python ``Format`` cannot set it, and a new object gets it from the preference ``Decorations/CenterLineStyle`` (unset: 2,
  the dashed line). That is why every line the sheet made was a dashed one, whatever ``style`` it asked for.
* The number indexes the line table of the standard in ``Standards/LineStandard``: 0 ANSI, 1 ASME (TechDraw's choice when
  the preference is unset), 2 ISO 128-20. With ISO, number 4 is the long-dash dotted line (dash 24, gap 3, dot, gap 3
  pen widths: 4.32 0.54 0.18 0.54 mm at 0.18); with ASME number 4 is its centre line, a long and a short dash.
* The number is saved in the content of the properties ``CenterLines`` and ``CosmeticEdges``, and
  ``dumpPropertyContent`` / ``restorePropertyContent`` read and write that content: the object that comes back is the one
  the Centerline dialog makes when the engineer picks the line style by hand. The objects keep their order; their tags
  change (as they do when a file is opened).
* A centre line between two edges (``makeCenterLine([edge, edge], mode)``) is redrawn from its two edges every time the
  view is: it follows the model and the view. ``Extension`` is in page mm; ``HorizShift`` / ``VertShift`` are in the
  view's own units and move the line along x / y.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any

ANSI, ASME, ISO = 0, 1, 2
UNSET_STANDARD = ASME  # what TechDraw paints with when ``Standards/LineStandard`` was never set
CONTINUOUS = 1
LINE_NUMBERS: dict[int, dict[str, int]] = {  # the line number of a kind of line in the table of each standard
    ANSI: {"centre": 3, "parting": 4},  # long dash, dash; long dash, two dashes
    ASME: {"centre": 4, "parting": 14},  # Center; Phantom
    ISO: {"centre": 4, "parting": 8},  # 04 long-dash dotted; 08 long-dash short-dash
}
CHAIN_NUMBERS: dict[int, tuple[int, ...]] = {  # every number of the table of a standard that paints a centre line (a chain)
    ANSI: (3,),  # long dash, dash
    ASME: (4, 5, 17),  # Center, Symmetry, Chain: the same long dash and dash
    ISO: (4, 5),  # 04 long-dash dotted, 05 long-dash double-dotted
}
CHAIN_MM = (24.0, 3.0, 1.0, 3.0)  # ISO 128-20 line 04 in pen widths: long dash, gap, dot, gap
ITEMS = {"CenterLines": "CenterLine", "CosmeticEdges": "CosmeticEdge"}
MODE_VERTICAL, MODE_HORIZONTAL = 0, 1
_NUMBER = re.compile(r'<LineNumber value="(\d+)"')


def line_number(style: str, standard: int) -> int:
    """TechDraw's line number of ``style`` (centre, parting; anything else is the continuous line) with ``standard``."""
    return LINE_NUMBERS.get(standard, LINE_NUMBERS[ISO]).get(style, CONTINUOUS)


UNSET_INT = -99  # what ``GetInt`` answers for a preference that was never saved (no real index is that)


def set_default_int(params: Any, key: str, value: int, is_list: bool = False) -> bool:
    """Set the integer preference ``key`` of the parameter group ``params`` to ``value`` only when the engineer has none of his
    own: never changes a value that is there. A list preference (the line standard) saved with "no item" holds a negative
    index; that is no choice of his either, TechDraw falls back to its own default for it. Returns whether it was written."""
    stored = int(params.GetInt(key, UNSET_INT))
    if stored != UNSET_INT and not (is_list and stored < 0):
        return False
    params.SetInt(key, value)
    return True


def is_chain(number: int, standard: int) -> bool:
    """Whether the line number ``number`` of the table of ``standard`` paints a centre line (any chain line it has)."""
    return number in CHAIN_NUMBERS.get(standard, CHAIN_NUMBERS[ISO])


def _blocks(xml: str, prop: str) -> list[re.Match[str]]:
    item = ITEMS[prop]
    return list(re.finditer(rf"<{item}\s.*?</{item}>", xml, flags=re.S))


def numbers(xml: str, prop: str) -> list[int]:
    """The line number of every object of the property content ``xml``, in the order of the list (0: none saved)."""
    out = []
    for block in _blocks(xml, prop):
        found = _NUMBER.search(block.group(0))
        out.append(int(found.group(1)) if found else 0)
    return out


def renumber(xml: str, prop: str, wanted: dict[int, int]) -> str:
    """``xml`` with the line number of the objects at the positions of ``wanted`` set; the rest is left as it is."""
    out: list[str] = []
    last = 0
    for index, block in enumerate(_blocks(xml, prop)):
        text = block.group(0)
        if index in wanted:
            if _NUMBER.search(text) is None:
                msg = f"object {index} of {prop} saves no line number"
                raise ValueError(msg)
            text = _NUMBER.sub(f'<LineNumber value="{wanted[index]}"', text, count=1)
        out += [xml[last : block.start()], text]
        last = block.end()
    return "".join([*out, xml[last:]])


def read(view: Any, prop: str) -> tuple[str, str]:
    """The saved content of the property ``prop`` of ``view``: the name of its file in the archive and its text."""
    archive = zipfile.ZipFile(io.BytesIO(bytes(view.dumpPropertyContent(prop))))
    name = archive.namelist()[0]
    return name, archive.read(name).decode("utf-8")


def numbers_of(view: Any, prop: str) -> list[int]:
    return numbers(read(view, prop)[1], prop)


def apply(view: Any, prop: str, wanted: dict[int, int]) -> bool:
    """Give the objects of ``prop`` at the positions of ``wanted`` their line number. The property is written only when a
    number differs; returns whether it was."""
    name, xml = read(view, prop)
    have = numbers(xml, prop)
    if all(index < len(have) and have[index] == number for index, number in wanted.items()):
        return False
    if any(index >= len(have) for index in wanted):
        msg = f"{prop} has {len(have)} object(s), fewer than asked for"
        raise ValueError(msg)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, renumber(xml, prop, wanted))
    view.restorePropertyContent(prop, bytearray(buffer.getvalue()))
    return True


@dataclass(frozen=True)
class Pair:
    """The two edges a centre line hangs from: their indexes in the view, TechDraw's mode (vertical / horizontal), how far
    the line goes past both ends of them (view units) and how far it is moved along itself."""

    a: int
    b: int
    mode: int
    extension: float
    shift: float


def pair_for(lines: list[tuple[int, float, float, float, float]], wanted: tuple[float, ...], tol: float) -> Pair | None:
    """The pair of straight edges of the view (``lines``: index, x1, y1, x2, y2) the centre line ``wanted`` (x1, y1, x2,
    y2) lies midway between: both parallel to it, at the same distance on either side, over the same stretch, inside the
    length of the line (the two sides of a cylinder or of a bore). ``None`` when the view has no such pair, or the line is
    neither horizontal nor vertical there. The longest pair is taken; then the nearest one."""
    x1, y1, x2, y2 = wanted[:4]
    if abs(y1 - y2) <= tol < abs(x1 - x2):
        mode, along = MODE_HORIZONTAL, 0
    elif abs(x1 - x2) <= tol < abs(y1 - y2):
        mode, along = MODE_VERTICAL, 1
    else:
        return None
    across = 1 - along
    level = (wanted[across] + wanted[across + 2]) / 2
    low, high = sorted((wanted[along], wanted[along + 2]))
    sides: list[tuple[int, float, float, float]] = []
    for index, *p in lines:
        if abs(p[across] - p[across + 2]) > tol:
            continue
        offset = (p[across] + p[across + 2]) / 2 - level
        start, end = sorted((p[along], p[along + 2]))
        if abs(offset) <= tol or end - start <= tol or start < low - tol or end > high + tol:
            continue
        sides.append((index, offset, start, end))
    best: tuple[tuple[float, float, float], Pair] | None = None
    for a, offset_a, start_a, end_a in sides:
        if offset_a < 0:
            continue
        for b, offset_b, start_b, end_b in sides:
            if offset_b > 0 or abs(offset_a + offset_b) > tol or abs(start_a - start_b) > tol or abs(end_a - end_b) > tol:
                continue
            extension = max(((high - low) - (end_a - start_a)) / 2, 0.0)
            shift = (low + high) / 2 - (start_a + end_a) / 2
            rank = (-(end_a - start_a), offset_a, abs(shift))
            if best is None or rank < best[0]:
                best = (rank, Pair(a, b, mode, extension, shift))
    return None if best is None else best[1]
