"""The functional dimensions the design intent declares, and whether the sheet gives each one directly (ISO 129-1).

Which dimensions a sheet must carry depends on what matters for the function: if the gap between two plates matters, the
length of the spacer is on the sheet; if a fit matters, the diameters of both parts are. A generic checklist buries the
important dimension or misses it, so this table does not know any part type: it reads what the intent file DECLARES (a
line of its function, mating faces, fits, dimensions or functional relations sections that says it is critical / functional,
or names a gap or a clearance), with the value and the tolerance the line states, and asks the sheet for a dimension that
gives that value BY ITSELF. A value that only comes out of adding or subtracting other dimensions (a chain) is not given.

The CAD reports what each dimension item of the sheet states (``DrawingSheetInfo.measures``) and the positions of the parts
list of an assembly (``components``); a functional dimension may live on the detail sheet of a part of an assembly, and then the
parts-list row of that part must reference that sheet (its drawing number). This module only reads those two lists.
"""

import itertools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

SECTIONS = ("function", "mating", "fits", "dimensions", "relations")
"""The intent sections whose lines can declare a functional dimension."""
MARKER = (
    r"cr[ií]tic[ao]|critical|funcional|functional|holgura|(?<![a-z])gap(?![a-z])|clearance|juego\b|separaci[oó]n entre|distancia entre|"
    r"distance between|(?<![a-z])spacing(?![a-z])"
)
"""What makes a line a functional dimension: it says so (critical, functional) or names the gap or clearance that matters.
Words of the function, never of a part type."""
TOLERANCE_MM = 0.05
"""Two values are the same dimension when they differ by less than this."""
MAX_MEASURES = 40

_VALUE_MM = re.compile(r"(?:[Ø⌀∅]\s*)?(\d+(?:[.,]\d+)?)\s*mm\b|[Ø⌀∅]\s*(\d+(?:[.,]\d+)?)", re.I)
_PLUS_MINUS = re.compile(r"±\s*\d+(?:[.,]\d+)?|\+\s*\d+(?:[.,]\d+)?\s*/\s*[-−]\s*\d+(?:[.,]\d+)?")
_CLASS = re.compile(r"(?<![A-Za-z0-9])(?:JS|Js|js|[A-HJ-LN-Z]|[a-hj-z])\d{1,2}(?![A-Za-z0-9])")
_MARKER = re.compile(MARKER, re.I)


@dataclass(frozen=True)
class Declared:
    """One functional dimension of the intent file: the line, the value it states (``None``: it gives none) and the
    tolerance or fit class it states (empty: none)."""

    text: str
    value_mm: float | None
    tolerance: str


def _number(raw: str) -> float:
    return float(raw.replace(",", "."))


def parse_line(line: str) -> Declared | None:
    """The functional dimension a line declares, or ``None`` when it is not one."""
    text = " ".join(line.split()).lstrip("-*• ").strip()
    if not text or not _MARKER.search(text):
        return None
    found = _VALUE_MM.search(_PLUS_MINUS.sub(" ", text))
    value = _number(found.group(1) or found.group(2)) if found else None
    tolerance = ""
    pm = _PLUS_MINUS.search(text)
    if pm:
        tolerance = pm.group(0)
    else:
        fit = _CLASS.search(text)
        if fit:
            tolerance = fit.group(0)
    return Declared(text, value, tolerance)


def declared(text: str) -> list[Declared]:
    """The functional dimensions declared by the lines of ``text`` (the joined sections of the intent file)."""
    out = [d for line in text.splitlines() if (d := parse_line(line)) is not None]
    return list(dict.fromkeys(out))


def _same(a: float, b: float) -> bool:
    return abs(a - b) < TOLERANCE_MM


def chain(value: float, measures: Sequence[Any]) -> str:
    """How ``value`` comes out of the dimensions of the sheet by adding or subtracting two or three of them
    (``"36 - 10 - 10"``), or ``""`` when it does not."""
    given = [(m.value_mm, m.id) for m in measures][:MAX_MEASURES]
    for (a, _), (b, _) in itertools.permutations(given, 2):
        if _same(a - b, value):
            return f"{a:g} - {b:g}"
        if _same(a + b, value) and a <= b:
            return f"{a:g} + {b:g}"
    for (a, _), (b, _), (c, _) in itertools.product(given, repeat=3):
        if _same(a - b - c, value):
            return f"{a:g} - {b:g} - {c:g}"
        if _same(a + b + c, value) and a <= b <= c:
            return f"{a:g} + {b:g} + {c:g}"
    return ""


def _hint(item: Declared) -> str:
    value = "" if item.value_mm is None else f" (it measures {item.value_mm:g} mm)"
    return (
        'drawing(action="add", items=[{"type":"dim","kind":"distance","from":<one feature>,"to":<the other>,"id":"<id>"}]) '
        'between the two features it separates, or {"type":"dim","kind":"diameter","face":["<face>"]} for a diameter'
        f"{value}; in an assembly it may live on the detail sheet of the part that carries it, and then the parts-list row of that part must print its drawing number"
    )


def problems(items: Sequence[Declared], measures: Sequence[Any], components: Sequence[Any]) -> list[str]:
    """What each declared functional dimension still lacks on the sheet (English, for the agent); empty: all are given."""
    out: list[str] = []
    for item in items:
        quoted = f"functional dimension «{item.text}»"
        if item.value_mm is None:
            out.append(
                f"{quoted}: the intent file names it but gives no value; say which dimension carries it and its value "
                "(write it in the intent file) and put it on the sheet"
            )
            continue
        direct = [m for m in measures if _same(m.value_mm, item.value_mm)]
        if direct:
            if item.tolerance and not any(m.toleranced for m in direct):
                out.append(
                    f"{quoted}: item {direct[0].id} gives {item.value_mm:g} mm but not its tolerance «{item.tolerance}»: "
                    "add tol or fit to that dimension"
                )
            continue
        lowered = item.text.lower()
        named = [c for c in components if len(c.name.strip()) >= 3 and c.name.strip().lower() in lowered]
        if named and all(c.reference.strip() for c in named):
            continue  # it is on the detail sheet of the part (the parts-list row cites it); that sheet is reviewed on its own
        how = chain(item.value_mm, measures)
        if how:
            out.append(
                f"{quoted}: {item.value_mm:g} mm only comes out as a chain ({how}); a functional dimension is given directly "
                f"(ISO 129-1), never as the result of other dimensions. Add it: {_hint(item)}"
            )
        elif named:
            names = ", ".join(c.name for c in named if not c.reference.strip())
            out.append(
                f"{quoted}: not on this sheet and the parts-list row of {names} prints no drawing number, so no detail sheet "
                f"carries it. Add it: {_hint(item)}"
            )
        else:
            out.append(f"{quoted}: no dimension of the sheet gives {item.value_mm:g} mm. Add it: {_hint(item)}")
    return out
