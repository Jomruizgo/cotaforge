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
    side, the nearest one to ``x_mm`` of those: no item that says it is painted yet means ``None``, never another text that
    happens to stand there."""
    wanted = plain(text) if text is not None else None
    reach = 8.0 if wanted is None else 60.0
    best: TextBox | None = None
    for box in boxes:
        if wanted is not None and plain(box.text) != wanted:
            continue
        if abs(box.cx - x_mm) > reach or abs(box.cy - y_mm) > 1.0:
            continue
        # the widest one near the estimate; when the line is known by what it says, the NEAREST one (two labels with the same
        # words at one height -- the parting line is labelled in each view -- must not be taken for each other)
        closer = best is not None and wanted is not None and abs(box.cx - x_mm) < abs(best.cx - x_mm)
        if best is None or closer or (wanted is None and box.width > best.width):
            best = box
    return None if best is None else (best.left, best.width, best.height)


def misplaced(wanted_left: float, box: tuple[float, float, float] | None, tolerance: float) -> float | None:
    """How far (mm, signed) the painted block's left edge is from ``wanted_left`` when that is more than ``tolerance``; ``None``
    when it is where it should be or was not measured."""
    if box is None:
        return None
    offset = box[0] - wanted_left
    return offset if abs(offset) > tolerance else None


NOTE_SNAP_MM = 5.0  # a note's X keeps this far from the X of any other object of the page
NOTE_SNAP_STEP_MM = 4.0  # ... by widening its text block this much at a time
NOTE_SNAP_TRIES = 8


def block_clear_of_others(others: list[float], left: float, block: float) -> float:
    """The width of a note's text block (its centre, X, is ``left`` + half of it) widened until that X is at least
    ``NOTE_SNAP_MM`` from the X of every other object of the page (``others``). The window pulls an object whose X is within a
    few mm of another one onto it (measured in 1.1.3): a dimension centred 3 mm from the notes' X moved every line of them
    3.3 mm and the fit could not put them back. An object on the very same X is no risk (the lines of the notes share one)."""
    for _ in range(NOTE_SNAP_TRIES):
        centre = left + block / 2
        if all(abs(centre - o) < 0.05 or abs(centre - o) >= NOTE_SNAP_MM for o in others):
            break
        block += NOTE_SNAP_STEP_MM
    return block
