# ruff: noqa: E501
"""The line weights of a sheet, in ONE table (no FreeCAD import, unit-testable).

Source: the owner's reference drawings (stroke widths read from the PDF operators of his shop drawings) and ISO 128-20
line group 0.35 (thick 0.35 mm, thin 0.18 mm), with the hidden lines drawn thick as his drawings do. The pattern on all of
them: 0.35 continuous for the visible outline, 0.35 dashed for the hidden one, 0.18 for the bulk of the thin lines and a
finer pen for the lines that must stay behind the rest.

0.07 mm is used only for the hatching and the tangent edges: lines that are many and close together or that only
accompany the outline, which on paper (a 600 dpi printer puts a 0.07 mm line on two dots) and in a PDF viewer read as the
quiet lines they are. The centre lines, the dimension lines and everything the shop reads stay at 0.18, the thinnest weight
of the ISO series that a plotter or a photocopy of the sheet still reproduces; the owner's finest lines are probably centre
lines too, but that is a guess, and a centre line lost in a copy costs more than one drawn a little heavy.

What TechDraw does not let us set (measured in 1.1.3): the hatch of a section view is drawn at 0.10 mm whatever
``WeightPattern`` says (0.07, 0.2, 0.3 and 0.5 gave the same PDF), the cell borders of a native spreadsheet table (the parts
list) at 0.09 mm, and the all-around circle of a weld symbol at 0.24 mm. The headless CAD writes the hatching at 0.07.

Where a weight lives. A view's widths (``LineWidth``, ``HiddenWidth``, ``IsoWidth``, ``ExtraWidth``), the section's hatch
(``WeightPattern``) and the width of a dimension, balloon or leader are properties of the TechDraw VIEW PROVIDERS: they exist
only in a FreeCAD with a window and are saved in the GUI part of the file, so a document written by the headless CAD
carries none of them (measured in 1.1.3: no App-level width on a view, a dimension, a balloon or a leader). What persists
in every CAD is the ``Format`` of a cosmetic edge and of a centre line (``weight``). The visible CAD therefore sets the rest
when it makes or composes the sheet and when it opens a file whose page it has not set yet (``PROP_MARK``).
"""

from collections.abc import Iterable

SOURCE = "owner's reference drawings, ISO 128-20 group 0.35"
THICK = 0.35
THIN = 0.18
FINE = 0.07

VISIBLE = "visible"
HIDDEN = "hidden"
CENTRE = "centre"
DIMENSION = "dimension"
HATCH = "hatch"
SECTION_LINE = "section_line"
THREAD = "thread"
TANGENT = "tangent"
TITLE_BLOCK = "title_block"
TABLE_GRID = "table_grid"
FRAME = "frame"

WEIGHTS: dict[str, float] = {
    VISIBLE: THICK,  # visible outlines
    HIDDEN: THICK,  # hidden outlines (dashed), thick like the visible ones
    CENTRE: THIN,  # centre lines and axes (chain thin)
    DIMENSION: THIN,  # dimension and extension lines, leaders, balloons, weld leaders
    HATCH: FINE,  # the hatching of a cut
    SECTION_LINE: THICK,  # the ends of a cutting-plane line; its body is a thin chain (``CENTRE``)
    THREAD: THIN,  # the thin arc of an external thread
    TANGENT: FINE,  # tangent edges and iso lines
    TITLE_BLOCK: THICK,  # the grid and the cells of the title block (the frame and the outer line are ``FRAME``)
    TABLE_GRID: THIN,  # the inner lines of a table (the parts list our own writer draws); its top line is ``TITLE_BLOCK``
    FRAME: 2 * THICK,  # the sheet frame and the outer line of the title block (ISO 5457: twice the thick line)
}

# TechDraw's own list of line groups (``Mod/TechDraw/LineGroup/LineGroup.csv``, in file order); ``Decorations/LineGroup`` is the
# index into it. "FC 0.35mm" is thin 0.18, graphic 0.25, thick 0.35, extra 0.70.
LINE_GROUP_NAME = "FC 0.35mm"
LINE_GROUP_INDEX = 1

# the view-provider width properties of a view and the kind each one draws
VIEW_PROPS: dict[str, str] = {
    "LineWidth": VISIBLE,
    "HiddenWidth": HIDDEN,
    "IsoWidth": TANGENT,
    "ExtraWidth": VISIBLE,  # TechDraw does not use it; as thick as the outline so no default heavier than the outline stays in the file
}
SECTION_PROP = "WeightPattern"  # the hatch of a section view (a view-provider property of ``DrawViewSection``)
MARK_VALUE = f"{SOURCE}; thick {THICK}, thin {THIN}, fine {FINE}"
PROP_MARK = "CotaforgeLineWeights"  # on the page: the table the view providers were set to (absent: not set yet)

TOLERANCE = 0.006  # mm: a width the PDF or the property writes rounded


def weight(kind: str) -> float:
    """The width (mm) of a kind of line."""
    return WEIGHTS[kind]


def allowed_widths() -> list[float]:
    """Every width the table uses, ascending."""
    return sorted(set(WEIGHTS.values()))


def in_table(width: float, kind: str | None = None) -> bool:
    """Whether ``width`` is the table's for ``kind`` (any kind of the table when none is given)."""
    wanted = [WEIGHTS[kind]] if kind is not None else allowed_widths()
    return any(abs(width - w) <= TOLERANCE for w in wanted)


def off_table(readings: Iterable[tuple[str, str | None, float]]) -> list[str]:
    """The readings ``(object, kind, width)`` whose width is not the table's for their kind (any width of the table when the
    kind is ``None``: a cosmetic line of the engineer's), as ``object: 0.50 mm (0.35 mm expected)`` lines, merged per object."""
    seen: dict[str, list[str]] = {}
    for name, kind, width in readings:
        if in_table(width, kind):
            continue
        expected = (
            f"{WEIGHTS[kind]:g} mm expected"
            if kind is not None
            else "one of " + ", ".join(f"{w:g}" for w in allowed_widths()) + " mm expected"
        )
        text = f"{width:g} mm ({expected})"
        seen.setdefault(name, [])
        if text not in seen[name]:
            seen[name].append(text)
    return [f"{name}: {'; '.join(texts)}" for name, texts in seen.items()]
