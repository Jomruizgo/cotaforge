# ruff: noqa: E501
"""The ISO 1101 feature control frames FreeCAD's own symbol library lacks, as files (no FreeCAD import).

The library ``cotaforge-gdt`` (``packaging/symbols/cotaforge-gdt``, made by ``build_gdt_frames.py``) holds SVG symbols: a
cell with the characteristic symbol, the tolerance cell and 0 to 3 datum cells. Every text of a cell carries
``freecad:editable``, so FreeCAD shows it as ``EditableTexts`` of the ``DrawViewSymbol`` and the engineer edits it in
FreeCAD's property editor, with no help from the harness. A human inserts the file with TechDraw's *Insert SVG Symbol*;
the harness makes the same object with the same file. This module only READS those files: it never writes an SVG.

``gdt_frame_symbol`` is the one door: it picks the file and works out the texts. The caller (the placement of a GD&T item
on a sheet) makes the native ``TechDraw::DrawViewSymbol`` attached to its view, sets ``Symbol`` to the returned text, then
``EditableTexts`` to the returned values, and hangs it with a native ``DrawLeaderLine``::

    svg, texts = gdt_frame_symbol("position", 0.1, ("diameter", "M"), ("A", "B", "C"))
    symbol.Symbol = svg          # the library file as it is
    symbol.EditableTexts = list(texts)   # ["Ø0,1 Ⓜ", "A", "B", "C"]

FreeCAD rewrites ``Symbol`` (same drawing, other attribute order and an ``xml:space``) when ``EditableTexts`` is set, so a
guard that asks "is this symbol one of the library files" must not compare text: ``is_frame_symbol`` compares the drawing
with its editable texts left out, which is what stays the same however the engineer edits the values.

Cell widths: an SVG has no text-driven width, so each file has fixed cells: the tolerance cell holds about ``Ø0,125 Ⓜ``
(``std``) and a ``wide`` twin holds the long ones (``Ø0,05 Ⓜ CZ``, ``0,02 / 100``); a datum cell holds ``A``, ``A-B`` or
``A Ⓜ``. There is one file per number of datum cells. ``gdt_frame_symbol`` picks ``wide`` by itself when the text needs
it and refuses what fits in neither.
"""

import os
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from functools import lru_cache
from pathlib import Path

LIBRARY_NAME = "cotaforge-gdt"
ENV_DIR = "COTAFORGE_GDT_DIR"  # the library folder itself, when it is not where the install puts it
FREECAD_NS = "https://www.freecad.org/wiki/index.php?title=Svg_Namespace"
SVG_NS = "http://www.w3.org/2000/svg"
EDITABLE = f"{{{FREECAD_NS}}}editable"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

ALIASES = {"coaxiality": "concentricity", "circular_runout": "runout", "roundness": "circularity"}
# the datum cells each characteristic has a file for (ISO 1101: a form tolerance has none, an orientation or run-out
# tolerance needs one; a position or profile tolerance may have none)
DATUM_RANGE: dict[str, range] = {
    "flatness": range(0, 1),
    "straightness": range(0, 1),
    "circularity": range(0, 1),
    "cylindricity": range(0, 1),
    "profile_line": range(0, 4),
    "profile_surface": range(0, 4),
    "position": range(0, 4),
    "parallelism": range(1, 4),
    "perpendicularity": range(1, 4),
    "angularity": range(1, 4),
    "concentricity": range(1, 4),
    "symmetry": range(1, 4),
    "runout": range(1, 4),
    "total_runout": range(1, 4),
}
CHARACTERISTICS = tuple(DATUM_RANGE)

# what goes in the tolerance cell
DIAMETER_OK = frozenset({"straightness", "parallelism", "perpendicularity", "angularity", "position", "concentricity"})
MATERIAL_OK = DIAMETER_OK | {"symmetry"}  # Ⓜ and Ⓛ: the toleranced feature is an axis or a median plane
PROJECTED_OK = frozenset({"parallelism", "perpendicularity", "angularity", "position"})  # Ⓟ
ZONE_OK = frozenset(
    {
        "profile_line",
        "profile_surface",
        "position",
        "parallelism",
        "perpendicularity",
        "angularity",
        "symmetry",
        "concentricity",
    }
)  # CZ, SZ, UF
CIRCLED = {"M": "Ⓜ", "L": "Ⓛ", "P": "Ⓟ"}
ZONE = ("CZ", "SZ", "UF")
PREFIX = {"diameter": "Ø", "Ø": "Ø", "spherical_diameter": "SØ", "SØ": "SØ"}
BAD_LETTERS = "IOQ"  # ISO 5459

# an estimate of the width of a text at 3.5 mm, to choose between the files (the real font decides at render time)
STD_FITS_MM = 19.0
WIDE_FITS_MM = 33.0
DATUM_FITS_MM = 8.4


class GdtFrameError(ValueError):
    """A frame the library cannot make; the message says what to change."""


def library_dir() -> Path:
    """The folder of the library: ``COTAFORGE_GDT_DIR``, else the first ``symbols/cotaforge-gdt`` or
    ``packaging/symbols/cotaforge-gdt`` above this file (the install keeps it next to ``python\\``; a checkout has it under
    ``packaging``). ``GdtFrameError`` when there is none."""
    given = os.environ.get(ENV_DIR)
    if given:
        folder = Path(given)
        if folder.is_dir():
            return folder
        msg = f"{ENV_DIR} points to {given}, which is not a folder"
        raise GdtFrameError(msg)
    for parent in Path(__file__).resolve().parents:
        for sub in ("symbols", "packaging/symbols"):
            folder = parent / sub / LIBRARY_NAME
            if folder.is_dir():
                return folder
    msg = f"the {LIBRARY_NAME} symbol library was not found (set {ENV_DIR} to its folder)"
    raise GdtFrameError(msg)


def _relative(characteristic: str, datums: int, wide: bool) -> str:
    folder = characteristic.replace("_", "-")
    word = "datum" if datums == 1 else "datums"
    return f"{folder}/{folder}-{datums}-{word}{'-wide' if wide else ''}.svg"


@lru_cache(maxsize=8)
def _library(folder: str) -> dict[str, str]:
    root = Path(folder)
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.svg"))}


def library_files() -> dict[str, str]:
    """Every file of the library by its path inside the folder (``/`` separators), with its text."""
    return _library(str(library_dir()))


# --- the texts ---------------------------------------------------------------------------------------------------------


def _text_mm(text: str) -> float:
    """About how wide ``text`` is at 3.5 mm."""
    total = 0.0
    for char in text:
        if char in "ⓂⓁⓅ" or char == "Ø":
            total += 3.6 if char != "Ø" else 2.6
        elif char == "-":
            total += 1.4
        elif char in " ,.":
            total += 1.2
        elif char.isdigit():
            total += 2.2
        else:
            total += 2.6
    return total


def _decimal(value: float, decimal: str) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text.replace(".", decimal)


def _tolerance_text(characteristic: str, tolerance: float | str, modifiers: Iterable[str], decimal: str) -> str:
    if isinstance(tolerance, bool) or not isinstance(tolerance, int | float | str):
        msg = "tolerance must be a number (mm) or the text of the cell"
        raise GdtFrameError(msg)
    if isinstance(tolerance, str):
        value = tolerance.strip()
        if not value:
            msg = "tolerance is empty"
            raise GdtFrameError(msg)
    else:
        if not 0 < tolerance <= 50:
            msg = f"tolerance {tolerance} mm is out of range (0 to 50)"
            raise GdtFrameError(msg)
        value = _decimal(float(tolerance), decimal)
    prefix = ""
    circled: list[str] = []
    zone = ""
    for raw in modifiers:
        token = str(raw).strip()
        key = token.upper() if token.upper() in (*CIRCLED, *ZONE) else token
        if key in PREFIX:
            if characteristic not in DIAMETER_OK:
                msg = f"{characteristic} has no diameter zone (Ø)"
                raise GdtFrameError(msg)
            prefix = PREFIX[key]
        elif key in CIRCLED:
            allowed = PROJECTED_OK if key == "P" else MATERIAL_OK
            if characteristic not in allowed:
                msg = f"{characteristic} takes no {key} modifier"
                raise GdtFrameError(msg)
            circled.append(CIRCLED[key])
        elif key in ZONE:
            if characteristic not in ZONE_OK:
                msg = f"{characteristic} takes no {key} modifier"
                raise GdtFrameError(msg)
            zone = key
        else:
            msg = f"unknown modifier {token!r}: use diameter, spherical_diameter, M, L, P, CZ, SZ or UF"
            raise GdtFrameError(msg)
    if len(circled) > 1:
        msg = "a tolerance takes one of M, L and P"
        raise GdtFrameError(msg)
    return " ".join(part for part in (prefix + value, *circled, zone) if part)


def _letter(text: str) -> str:
    if len(text) != 1 or not "A" <= text <= "Z" or text in BAD_LETTERS:
        msg = f"datum {text!r} must be one capital letter A-Z other than I, O, Q (ISO 5459)"
        raise GdtFrameError(msg)
    return text


def _datum_text(entry: str | Sequence[str]) -> tuple[str, tuple[str, ...]]:
    """The text of a datum cell and the letters it cites. An entry is ``"A"``, ``"A-B"`` (a common datum) or
    ``("A", "M")`` (a letter with a material condition)."""
    if isinstance(entry, str):
        letters = tuple(_letter(part) for part in entry.split("-"))
        if len(letters) > 2:
            msg = f"datum {entry!r}: a common datum has two letters (A-B)"
            raise GdtFrameError(msg)
        return "-".join(letters), letters
    if len(entry) != 2 or not isinstance(entry[0], str) or str(entry[1]).upper() not in ("M", "L"):
        msg = f"datum {entry!r} must be a letter, 'A-B', or (letter, 'M' or 'L')"
        raise GdtFrameError(msg)
    letter = _letter(entry[0])
    return f"{letter} {CIRCLED[str(entry[1]).upper()]}", (letter,)


def gdt_frame_symbol(
    characteristic: str,
    tolerance: float | str,
    modifiers: Iterable[str] = (),
    datums: Sequence[str | Sequence[str]] = (),
    *,
    decimal: str = ",",
    width: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    """The library file for a feature control frame and the values of its editable texts: ``(svg_text, editable_texts)``.

    ``characteristic``: flatness, straightness, circularity, cylindricity, parallelism, perpendicularity, angularity,
    position, concentricity (or coaxiality), symmetry, runout, total_runout, profile_line, profile_surface.
    ``tolerance``: the value in mm (formatted with ``decimal``) or the text of the cell (``"0,02 / 100"``).
    ``modifiers``: ``diameter`` (Ø), ``spherical_diameter`` (SØ), one of ``M`` ``L`` ``P`` (circled), one of ``CZ`` ``SZ``
    ``UF``. ``datums``: up to three cells, each ``"A"``, ``"A-B"`` or ``("A", "M")``. ``width``: ``"std"`` or ``"wide"`` to
    force the tolerance cell (by default the narrowest that holds the text).

    ``svg_text`` is the file as it is; ``editable_texts`` is in the order of the file: the tolerance, then the datums.
    Raises ``GdtFrameError`` for a frame ISO 1101 has no such cell for.
    """
    name = ALIASES.get(characteristic, characteristic)
    if name not in DATUM_RANGE:
        msg = f"unknown characteristic {characteristic!r}: expected one of {', '.join(CHARACTERISTICS)}"
        raise GdtFrameError(msg)
    cells = [_datum_text(entry) for entry in datums]
    count = len(cells)
    if count not in DATUM_RANGE[name]:
        msg = (
            f"{characteristic} is a form tolerance: it takes no datums"
            if DATUM_RANGE[name].stop == 1
            else f"{characteristic} takes {DATUM_RANGE[name].start} to {DATUM_RANGE[name].stop - 1} datum cells, not {count}"
        )
        raise GdtFrameError(msg)
    cited = [letter for _, letters in cells for letter in letters]
    if len(set(cited)) != len(cited):
        msg = f"a datum letter is repeated: {', '.join(cited)}"
        raise GdtFrameError(msg)
    for text, _ in cells:
        if _text_mm(text) > DATUM_FITS_MM:
            msg = f"datum cell {text!r} does not fit in the cell of the library"
            raise GdtFrameError(msg)
    tolerance_text = _tolerance_text(name, tolerance, modifiers, decimal)
    fit = _text_mm(tolerance_text)
    if width not in (None, "std", "wide"):
        msg = "width must be 'std', 'wide' or None"
        raise GdtFrameError(msg)
    wide = width == "wide" or (width is None and fit > STD_FITS_MM)
    if fit > (WIDE_FITS_MM if wide else STD_FITS_MM):
        msg = f"the tolerance text {tolerance_text!r} is too long for the {'wide' if wide else 'narrow'} cell of the library"
        raise GdtFrameError(msg)
    relative = _relative(name, count, wide)
    files = library_files()
    if relative not in files:
        msg = f"the library {library_dir()} has no {relative}"
        raise GdtFrameError(msg)
    return files[relative], (tolerance_text, *(text for text, _ in cells))


def gdt_frame_file(characteristic: str, datum_cells: int, *, wide: bool = False) -> str:
    """The path inside the library of the frame with that many datum cells (what a ``DrawViewSymbol`` made from it records)."""
    name = ALIASES.get(characteristic, characteristic)
    if name not in DATUM_RANGE or datum_cells not in DATUM_RANGE[name]:
        msg = f"the library has no {characteristic} frame with {datum_cells} datum cells"
        raise GdtFrameError(msg)
    return _relative(name, datum_cells, wide)


# --- telling a library symbol from another --------------------------------------------------------------------------------


def _tree(element: ET.Element, blank: bool) -> tuple:
    """The drawing of an element without its editable texts (``blank``: everything under an editable text), without the
    whitespace between elements and without the ``xml:space`` FreeCAD adds when it rewrites the symbol."""
    blank = blank or EDITABLE in element.attrib
    attrs = tuple(sorted((k, v) for k, v in element.attrib.items() if k != XML_SPACE))
    text = "" if blank else (element.text or "").strip()
    return (element.tag, attrs, text, tuple(_tree(child, blank) for child in element))


@lru_cache(maxsize=8)
def _signatures(folder: str) -> frozenset[str]:
    return frozenset(repr(_tree(ET.fromstring(text), False)) for text in _library(folder).values())


def is_frame_symbol(svg_text: str) -> bool:
    """Whether ``svg_text`` is a file of the library, whatever values its editable texts hold (and however FreeCAD has
    rewritten it): the drawing is the same. A symbol whose geometry or other texts differ is not."""
    try:
        root = ET.fromstring(svg_text.strip())
    except ET.ParseError:
        return False
    return repr(_tree(root, False)) in _signatures(str(library_dir()))


def fill_editable_texts(svg_text: str, texts: Sequence[str]) -> str:
    """``svg_text`` with its editable texts set to ``texts``, in order: what FreeCAD paints when ``EditableTexts`` holds them
    (for a preview and for the tests; the sheet never uses it: FreeCAD does it)."""
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("freecad", FREECAD_NS)
    root = ET.fromstring(svg_text.strip())
    fields = [e for e in root.iter() if EDITABLE in e.attrib]
    if len(fields) != len(texts):
        msg = f"the symbol has {len(fields)} editable texts, {len(texts)} given"
        raise GdtFrameError(msg)
    for field, text in zip(fields, texts, strict=True):
        spans = [child for child in field.iter() if child is not field and child.tag.endswith("tspan")]
        target = spans[0] if spans else field
        target.text = text
    return ET.tostring(root, encoding="unicode")


def editable_fields(svg_text: str) -> list[tuple[str, str]]:
    """The editable texts of a symbol in the order FreeCAD lists them: ``(name, value)``."""
    root = ET.fromstring(svg_text.strip())
    out: list[tuple[str, str]] = []
    for element in root.iter():
        if EDITABLE in element.attrib:
            out.append((element.attrib[EDITABLE], re.sub(r"\s+", " ", "".join(element.itertext())).strip()))
    return out
