# ruff: noqa: E501
"""Finding the text block TechDraw painted for one line of a note, and judging where it stands (no FreeCAD import).

A note line is its own ``DrawViewAnnotation``; the sheet measures the block the window painted and moves the line until its left
edge is where the sheet wants it (``sheet_native._fit_note``). The lookup used to take the widest text item near the line's
height: when the line's own item was not painted yet, that was ANOTHER text at that height (the dimension text of a view, 0.8 mm
wide) and the line was moved by the difference, out of the frame (the junction box sheet: two lines of the notes ran out at the
left). A line is now found by what it says; and a line that does not end where the sheet wants it is never left there.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TextBox:
    """One text item of the page scene: what it says, its left edge, width and height, and its centre (mm, page, y up)."""

    text: str
    left: float
    width: float
    height: float
    cx: float
    cy: float


def plain(text: str) -> str:
    """The words of ``text`` with the spacing collapsed (the indentation of a continuation line is not part of what it says)."""
    return " ".join(text.split())


def pick_block(
    boxes: Sequence[TextBox], x_mm: float, y_mm: float, text: str | None = None
) -> tuple[float, float, float] | None:
    """``(left, width, height)`` of the widest text item whose centre is within 1 mm of ``y_mm`` and within 8 mm of ``x_mm``
    (the estimate of where the font puts it). With ``text`` the item must SAY it (``plain``), and it is looked for 60 mm either
    side: no item that says it is painted yet means ``None``, never another text that happens to stand there."""
    wanted = plain(text) if text is not None else None
    reach = 8.0 if wanted is None else 60.0
    best: TextBox | None = None
    for box in boxes:
        if wanted is not None and plain(box.text) != wanted:
            continue
        if abs(box.cx - x_mm) <= reach and abs(box.cy - y_mm) <= 1.0 and (best is None or box.width > best.width):
            best = box
    return None if best is None else (best.left, best.width, best.height)


def misplaced(wanted_left: float, box: tuple[float, float, float] | None, tolerance: float) -> float | None:
    """How far (mm, signed) the painted block's left edge is from ``wanted_left`` when that is more than ``tolerance``; ``None``
    when it is where it should be or was not measured."""
    if box is None:
        return None
    offset = box[0] - wanted_left
    return offset if abs(offset) > tolerance else None


def painted(boxes: Sequence[TextBox], x_mm: float, y_mm: float, text: str) -> TextBox | None:
    """The text item that says ``text`` and stands nearest to ``(x_mm, y_mm)``, wherever it is on the page (``None``: none is
    painted). ``pick_block`` looks only 1 mm either side of the height the line should be at, so a line that was painted at
    another height (the lines of a note stacked on one of them: the window drew them at one place) is not found there; this is
    how the sheet learns where it stands."""
    wanted = plain(text)
    same = [b for b in boxes if plain(b.text) == wanted]
    return min(same, key=lambda b: (b.cx - x_mm) ** 2 + (b.cy - y_mm) ** 2, default=None)


def off_place(wanted_left: float, wanted_cy: float, box: TextBox | None, tol_x: float, tol_y: float) -> tuple[float, float] | None:
    """How far (dx, dy in mm) a painted line must move to stand where the sheet wants it (left edge at ``wanted_left``, middle at
    ``wanted_cy``), ``None`` when it is there within the tolerances or was not painted."""
    if box is None:
        return None
    dx, dy = wanted_left - box.left, wanted_cy - box.cy
    return (dx, dy) if abs(dx) > tol_x or abs(dy) > tol_y else None
