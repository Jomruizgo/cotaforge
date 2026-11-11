# ruff: noqa: E501
"""Colours of TechDraw's native objects that Qt's PDF writer cannot print.

TechDraw keeps the colour of a cosmetic line, a centre line or a cosmetic vertex as four floats, red / green / blue /
alpha, each meant to lie in 0..1, and takes them as they come: a ``Format`` set from a script with ``(255, 0, 0, 0)`` (the
0..255 scale most software uses) or with a negative number is stored as given. At export TechDraw hands the numbers to
``QColor.fromRgbF``, which answers with an invalid colour, and Qt's PDF engine writes an invalid colour as a colour
operator without operands: ``/CSp cs scn``. Poppler reports ``Incorrect number of arguments in 'SCN' command`` and the
viewers that stop at the first malformed operator (the engineer's did) draw the first view and nothing else. Measured on
FreeCAD 1.1.3 (Qt 6.8.3): ``-1.0`` or ``255.0`` in a component gives the bad operator, ``nan`` and ``inf`` do not.

``fix_colour`` is the pure rule (no FreeCAD import, unit-testable); ``repair`` applies it to the objects of a page's views
before the page is exported or captured, and returns what it changed so the reply can say so.
"""

import math
from typing import Any

# the property lists of a view that hold objects with a colour, and the attribute of each that holds it
COLOUR_LISTS = ("CosmeticEdges", "CenterLines", "GeomFormats", "CosmeticVertexes")


def fix_colour(color: Any) -> tuple[float, ...] | None:
    """The colour as TechDraw needs it (every component in 0..1), or ``None`` when it already is. A component above 1 with
    every component within 0..255 is the 0..255 scale and is divided by 255; any other out-of-range number is clamped and
    ``nan`` becomes 0."""
    try:
        values = [float(c) for c in color]
    except (TypeError, ValueError):
        return None
    if not values:
        return None
    sound = all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in values)
    if sound:
        return None
    colour = [v for v in values[:3] if math.isfinite(v)]
    byte_scale = bool(colour) and max(colour) > 1.0 and all(0.0 <= v <= 255.0 for v in colour)
    fixed: list[float] = []
    for i, v in enumerate(values):
        if not math.isfinite(v):
            v = 1.0 if v == math.inf else 0.0
        elif byte_scale and i < 3:
            v /= 255.0
        fixed.append(min(1.0, max(0.0, v)))
    return tuple(fixed)


def _repair_object(obj: Any) -> bool:
    """Fix the colour of one cosmetic object in place; True when it changed."""
    changed = False
    fmt = getattr(obj, "Format", None)
    if isinstance(fmt, dict) and "color" in fmt:
        fixed = fix_colour(fmt["color"])
        if fixed is not None:
            obj.Format = {**fmt, "color": fixed}
            changed = True
    colour = getattr(obj, "Color", None)
    if isinstance(colour, (tuple, list)):
        fixed = fix_colour(colour)
        if fixed is not None:
            obj.Color = fixed
            changed = True
    return changed


def repair(views: list[Any]) -> list[str]:
    """Normalise the colour of every cosmetic edge, centre line, geometry format and cosmetic vertex of ``views``; one
    line per view that had any, for the reply. A view or object this FreeCAD cannot read or write is left alone."""
    notes: list[str] = []
    for view in views:
        fixed = 0
        for attr in COLOUR_LISTS:
            try:
                objects = list(getattr(view, attr, None) or [])
            except Exception:  # noqa: BLE001 - a property this FreeCAD does not have, or one that cannot be listed
                continue
            for obj in objects:
                try:
                    fixed += _repair_object(obj)
                except Exception:  # noqa: BLE001, S112 - an object of another make: the export will say if it breaks
                    continue
        if fixed:
            notes.append(f"{view.Label}: {fixed} native object(s) had a colour outside 0..1; normalised")
    return notes
