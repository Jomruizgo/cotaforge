# ruff: noqa: E501
"""The symbols of FreeCAD's own TechDraw library, the only ``DrawViewSymbol`` a sheet may hold.

The harness makes everything on a sheet out of the software's own objects, so the engineer can go on with FreeCAD alone
(``docs`` of the design: only native objects). A symbol the harness drew itself, as an SVG it generated, is not one: it
does not follow its view, it cannot be edited line by line and it stays behind when its view goes. The symbols of the
library that ships with FreeCAD (``Mod/TechDraw/Symbols``: GD&T, datum, surface roughness, welding, ...) are the software's
own: the engineer inserts the same files from the TechDraw menu. This module is the one place that makes a
``DrawViewSymbol``, from one of those files, and the one place that tells whether a symbol on a page is one of them (the
review reports the others: ``foreign_symbols``).

The guard in code: ``tests/unit/freecad_client/test_native_only.py`` fails when any other module of the worker makes a
``DrawViewSymbol`` or sets the ``Symbol`` of one.
"""

import os
from pathlib import Path
from typing import Any

SYMBOL_TYPE = "TechDraw::DrawViewSymbol"
LIBRARY_DIR = ("Mod", "TechDraw", "Symbols")
PROP_LIBRARY = "CotaforgeLibrary"  # on a symbol: the library file it was made from (relative to the library folder)

# What FreeCAD's own library has for each GD&T characteristic, datum and finish symbol the sheet draws (paths in the library
# folder). ``frame`` is the file with the feature control frame (symbol and empty cells), ``symbol`` the bare glyph. The
# library files carry no editable text, so a tolerance value or a datum letter is a native annotation over the cell it goes in;
# ``None`` is a gap: the library has no file for it and the symbol must be composed from native objects, never generated SVG.
GDT_LIBRARY: dict[str, dict[str, str | None]] = {
    "flatness": {"frame": "gd-and-t/FlatnessFrame.svg", "symbol": "gd-and-t/flatness.svg"},
    "circularity": {"frame": "gd-and-t/RoundnessFrame.svg", "symbol": "gd-and-t/roundness.svg"},
    "cylindricity": {"frame": "gd-and-t/CylindricityFrame.svg", "symbol": "gd-and-t/cylindricity.svg"},
    "parallelism": {"frame": "gd-and-t/ParallelismFrame.svg", "symbol": "gd-and-t/parallelism.svg"},
    "perpendicularity": {"frame": "gd-and-t/SquarenessFrame.svg", "symbol": "gd-and-t/perpendicularity.svg"},
    "concentricity": {"frame": "gd-and-t/ConcentricityFrame.svg", "symbol": "gd-and-t/concentricity.svg"},
    "position": {"frame": None, "symbol": "gd-and-t/position.svg"},
    "symmetry": {"frame": None, "symbol": "gd-and-t/symmetry.svg"},
    "total_runout": {"frame": None, "symbol": "gd-and-t/totalrunout.svg"},
    "angularity": {"frame": None, "symbol": "gd-and-t/angularity-left.svg"},
    "profile_line": {"frame": None, "symbol": "gd-and-t/profile-tolerance-of-line.svg"},
    "profile_surface": {"frame": None, "symbol": "gd-and-t/profile-tolerance-of-plane.svg"},
    "straightness": {"frame": None, "symbol": None},
    "runout": {"frame": None, "symbol": None},
    "datum": {"frame": "misc/Datum_Up.svg", "symbol": "gd-and-t/ReferenceSurface.svg"},
    "finish": {"frame": "surface-roughness-symbols/Roughness.svg", "symbol": "surface-roughness-symbols/RoughnessGeneral.svg"},
}

_cache: dict[str, dict[str, str]] = {}


def library_root(resource_dir: str) -> Path:
    return Path(resource_dir, *LIBRARY_DIR)


EXTRA_ROOTS: list[Path] = []
"""Folders of symbol files besides FreeCAD's own that the sheet may insert (the ISO 1101 frame set the product ships for what
FreeCAD's library lacks: files, never SVG made at run time). Empty until that set is registered here."""


def library(resource_dir: str) -> dict[str, str]:
    """Every SVG of FreeCAD's symbol library (and of ``EXTRA_ROOTS``) by its path relative to its folder (``/`` separators),
    with its text."""
    found = _cache.get(resource_dir)
    if found is None:
        found = {}
        for root in (library_root(resource_dir), *EXTRA_ROOTS):
            if root.is_dir():
                for path in sorted(root.rglob("*.svg")):
                    found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
        _cache[resource_dir] = found
    return found


def library_file(resource_dir: str, relative: str) -> str:
    """The text of one library symbol; ``KeyError`` naming the folder when FreeCAD does not have it."""
    files = library(resource_dir)
    if relative not in files:
        msg = f"FreeCAD's symbol library ({library_root(resource_dir)}) has no {relative}"
        raise KeyError(msg)
    return files[relative]


def is_library_text(resource_dir: str, text: str) -> bool:
    """Whether ``text`` is a library file as it is (a symbol whose text the harness drew, or changed, is not)."""
    wanted = text.strip()
    return any(wanted == own.strip() for own in library(resource_dir).values())


def make(
    fc_doc: Any, page: Any, resource_dir: str, relative: str, name: str, x: float, y: float, scale: float = 1.0
) -> Any:
    """Insert the library symbol ``relative`` on ``page`` at (x, y) page mm, as the TechDraw menu does (``Insert SVG symbol``):
    a ``DrawViewSymbol`` whose text is that file."""
    obj = fc_doc.addObject(SYMBOL_TYPE, name)
    page.addView(obj)
    obj.Symbol = library_file(resource_dir, relative)
    obj.Scale = scale
    obj.X, obj.Y = x, y
    obj.addProperty(
        "App::PropertyString", PROP_LIBRARY, "Cotaforge", "FreeCAD library symbol this object was made from"
    )
    setattr(obj, PROP_LIBRARY, relative)
    return obj


def foreign_symbols(page: Any, resource_dir: str) -> list[Any]:
    """The symbols of ``page`` that are not a file of FreeCAD's library: what the harness drew itself, or the engineer's own."""
    found: list[Any] = []
    for obj in page.Views:
        if str(obj.TypeId) == SYMBOL_TYPE and not is_library_text(resource_dir, str(obj.Symbol)):
            found.append(obj)
    return found


def resource_dir() -> str:
    """FreeCAD's resource folder (the one ``Mod/`` is in)."""
    import FreeCAD as App  # noqa: PLC0415 - not importable outside FreeCAD

    return os.path.normpath(App.getResourceDir())
