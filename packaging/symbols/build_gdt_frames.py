# ruff: noqa: E501
"""Writes the ISO 1101 feature control frame symbols of ``cotaforge-gdt/`` (a library of SVG files for TechDraw).

FreeCAD's own symbol library (``Mod/TechDraw/Symbols/gd-and-t``) has six frames with no text and the bare symbol of
most other characteristics. It has no position, profile, runout, symmetry or angularity frame, no frame with two or
three datum cells and no modifier. This is that gap, made as files, so the engineer inserts them the way he inserts any
symbol (TechDraw -> Insert SVG Symbol) and types the values in FreeCAD's own property editor.

A file holds one frame: the characteristic symbol cell, the tolerance cell and 0 to 3 datum cells. Every text of a cell
carries ``freecad:editable``, which FreeCAD shows as ``EditableTexts``. The width of a cell is fixed in each file (an
SVG has no text-driven width): one file per number of datum cells, and a ``-wide`` twin whose tolerance cell holds the
long texts (``Ø0,05 Ⓜ CZ``, ``0,02 / 100``).

Geometry (ISO 1101, ISO 7083): the cell is 7 mm high (twice the 3.5 mm text) and the symbol cell is 7 mm wide; the line
is 0.35 mm (the line width of the lettering of 3.5 mm text, ISO 128 / ISO 3098), and the text is 3.5 mm.

The same folder holds the datum feature symbols (ISO 5459: ``datum/datum-up.svg`` ...: the triangle, its stem and the framed
letter, with the letter editable) and the surface texture symbols (ISO 1302: ``finish/finish-required.svg`` ...: the check
mark with its bar and the roughness text editable). In those the origin of the drawing (0, 0) is the point that lands on the
feature: the middle of the base of the triangle, the vertex of the check mark, so a sheet places them by that point.

Run ``python build_gdt_frames.py`` to rewrite the files; ``python build_gdt_frames.py --check`` fails when a file on
disk differs from what this writes (the unit tests do the same).
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

CELL_H = 7.0  # ISO 7083: the height of the frame (twice the text)
SYMBOL_W = 7.0
TEXT_MM = 3.5
LINE_MM = 0.35
BASELINE_MM = 4.75  # the baseline of 3.5 mm text centred in a 7 mm cell
GLYPH_MM = 4.6  # the characteristic symbol is drawn in a square of this side, centred in the symbol cell
TOLERANCE_W = {"std": 22.0, "wide": 36.0}
DATUM_W = 10.0
FONT = "osifont, Tahoma, 'Segoe UI Symbol', sans-serif"
NAMESPACE_FREECAD = "https://www.freecad.org/wiki/index.php?title=Svg_Namespace"
DEFAULT_TOLERANCE = "0,1"
DEFAULT_DATUMS = ("A", "B", "C")

Prim = tuple  # ("poly", points, closed) | ("circle", cx, cy, r) | ("arc", cx, cy, r, start, end)


def _arrow(x0: float, y0: float, x1: float, y1: float) -> list[Prim]:
    """A runout arrow, drawn in the unit box: the shaft and an open head."""
    angle = math.atan2(y1 - y0, x1 - x0)
    head = 0.22
    left = (x1 - head * math.cos(angle - 0.45), y1 - head * math.sin(angle - 0.45))
    right = (x1 - head * math.cos(angle + 0.45), y1 - head * math.sin(angle + 0.45))
    return [("poly", [(x0, y0), (x1, y1)], False), ("poly", [left, (x1, y1), right], False)]


# The symbols, in a unit square (y up). The key is the name the characteristic has everywhere in cotaforge.
GLYPHS: dict[str, list[Prim]] = {
    "flatness": [("poly", [(0.0, 0.2), (0.7, 0.2), (1.0, 0.8), (0.3, 0.8)], True)],
    "straightness": [("poly", [(0.0, 0.5), (1.0, 0.5)], False)],
    "circularity": [("circle", 0.5, 0.5, 0.5)],
    "cylindricity": [
        ("circle", 0.5, 0.5, 0.27),
        ("poly", [(0.0, 0.0), (0.3, 1.0)], False),
        ("poly", [(0.7, 0.0), (1.0, 1.0)], False),
    ],
    "parallelism": [("poly", [(0.1, 0.0), (0.45, 1.0)], False), ("poly", [(0.55, 0.0), (0.9, 1.0)], False)],
    "perpendicularity": [("poly", [(0.0, 0.0), (1.0, 0.0)], False), ("poly", [(0.5, 0.0), (0.5, 1.0)], False)],
    "angularity": [("poly", [(1.0, 0.0), (0.0, 0.0), (0.85, 0.75)], False)],
    "position": [
        ("circle", 0.5, 0.5, 0.33),
        ("poly", [(0.5, 0.0), (0.5, 1.0)], False),
        ("poly", [(0.0, 0.5), (1.0, 0.5)], False),
    ],
    "concentricity": [("circle", 0.5, 0.5, 0.5), ("circle", 0.5, 0.5, 0.27)],
    "symmetry": [
        ("poly", [(0.1, 0.8), (0.9, 0.8)], False),
        ("poly", [(0.0, 0.5), (1.0, 0.5)], False),
        ("poly", [(0.1, 0.2), (0.9, 0.2)], False),
    ],
    "runout": _arrow(0.1, 0.1, 0.9, 0.9),
    "total_runout": [
        *_arrow(0.0, 0.1, 0.42, 0.95),
        *_arrow(0.5, 0.1, 0.92, 0.95),
        ("poly", [(0.0, 0.1), (0.5, 0.1)], False),
    ],
    "profile_line": [("arc", 0.5, 0.15, 0.5, 0.0, 180.0)],
    "profile_surface": [("arc", 0.5, 0.15, 0.5, 0.0, 180.0), ("poly", [(0.0, 0.15), (1.0, 0.15)], False)],
}

# What each characteristic accepts: the datum cells it may have (ISO 1101) and the files that carry it.
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
FOLDER = {name: name.replace("_", "-") for name in GLYPHS}
TITLE = {
    "flatness": "Flatness",
    "straightness": "Straightness",
    "circularity": "Circularity",
    "cylindricity": "Cylindricity",
    "parallelism": "Parallelism",
    "perpendicularity": "Perpendicularity",
    "angularity": "Angularity",
    "position": "Position",
    "concentricity": "Concentricity and coaxiality",
    "symmetry": "Symmetry",
    "runout": "Circular run-out",
    "total_runout": "Total run-out",
    "profile_line": "Profile of a line",
    "profile_surface": "Profile of a surface",
}


def relative_path(characteristic: str, datums: int, width: str = "std") -> str:
    """The file of a frame inside ``cotaforge-gdt``: ``position/position-2-datums.svg`` (``-wide`` for the long one)."""
    word = "datum" if datums == 1 else "datums"
    suffix = "-wide" if width == "wide" else ""
    return f"{FOLDER[characteristic]}/{FOLDER[characteristic]}-{datums}-{word}{suffix}.svg"


def _n(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _glyph(name: str) -> str:
    """The symbol as SVG elements in the symbol cell (x from 0, y down from the top of the cell)."""
    pad = (SYMBOL_W - GLYPH_MM) / 2

    def to_mm(x: float, y: float) -> tuple[float, float]:
        return pad + x * GLYPH_MM, CELL_H - (pad + y * GLYPH_MM)

    out: list[str] = []
    for prim in GLYPHS[name]:
        kind = prim[0]
        if kind == "poly":
            points = [to_mm(x, y) for x, y in prim[1]]
            d = "M " + " L ".join(f"{_n(x)} {_n(y)}" for x, y in points) + (" Z" if prim[2] else "")
            out.append(f'<path d="{d}"/>')
        elif kind == "circle":
            cx, cy = to_mm(prim[1], prim[2])
            out.append(f'<circle cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(prim[3] * GLYPH_MM)}"/>')
        else:  # arc, counter-clockwise in the unit box
            _, cx, cy, r, start, end = prim
            x0, y0 = to_mm(cx + r * math.cos(math.radians(start)), cy + r * math.sin(math.radians(start)))
            x1, y1 = to_mm(cx + r * math.cos(math.radians(end)), cy + r * math.sin(math.radians(end)))
            out.append(
                f'<path d="M {_n(x0)} {_n(y0)} A {_n(r * GLYPH_MM)} {_n(r * GLYPH_MM)} 0 0 0 {_n(x1)} {_n(y1)}"/>'
            )
    return "\n    ".join(out)


def _text(field: str, x: float, content: str) -> str:
    style = f"font-size:{_n(TEXT_MM)}px;font-family:{FONT};text-anchor:middle;fill:#000000;stroke:none"
    return (
        f'<text freecad:editable="{field}" x="{_n(x)}" y="{_n(BASELINE_MM)}" style="{style}">'
        f"<tspan>{content}</tspan></text>"
    )


def render(characteristic: str, datums: int, width: str = "std") -> str:
    """One frame as an SVG document (what TechDraw's ``DrawViewSymbol.Symbol`` holds)."""
    if characteristic not in GLYPHS:
        msg = f"unknown characteristic {characteristic!r}"
        raise ValueError(msg)
    if datums not in DATUM_RANGE[characteristic]:
        msg = f"{characteristic} has no frame with {datums} datum cells"
        raise ValueError(msg)
    cells = [SYMBOL_W, TOLERANCE_W[width], *([DATUM_W] * datums)]
    total = sum(cells)
    half = LINE_MM / 2
    left = [sum(cells[:i]) for i in range(len(cells))]
    dividers = " ".join(f"M {_n(x)} 0 V {_n(CELL_H)}" for x in left[1:])
    texts = [_text("tolerance", left[1] + cells[1] / 2, DEFAULT_TOLERANCE)]
    texts += [_text(f"datum_{i + 1}", left[2 + i] + DATUM_W / 2, DEFAULT_DATUMS[i]) for i in range(datums)]
    shown_w, shown_h = total + LINE_MM, CELL_H + LINE_MM
    title = f"{TITLE[characteristic]}: {datums} datum cell{'s' if datums != 1 else ''}" + (
        ", wide tolerance cell" if width == "wide" else ""
    )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:freecad="{NAMESPACE_FREECAD}" version="1.1" '
            f'width="{_n(shown_w)}mm" height="{_n(shown_h)}mm" viewBox="{_n(-half)} {_n(-half)} {_n(shown_w)} {_n(shown_h)}">',
            f"  <title>ISO 1101 feature control frame - {title}</title>",
            f'  <g id="frame" style="fill:#ffffff;stroke:#000000;stroke-width:{_n(LINE_MM)};stroke-linejoin:miter">',
            f'    <path d="M 0 0 H {_n(total)} V {_n(CELL_H)} H 0 Z"/>',
            f'    <path d="{dividers}" style="fill:none"/>',
            "  </g>",
            f'  <g id="symbol" style="fill:none;stroke:#000000;stroke-width:{_n(LINE_MM)};stroke-linecap:round;stroke-linejoin:round">',
            f"    {_glyph(characteristic)}",
            "  </g>",
            *[f"  {t}" for t in texts],
            "</svg>",
            "",
        ]
    )


# --- datum feature symbols (ISO 5459) and surface texture symbols (ISO 1302) ---------------------------------------------------

DATUM_BASE = 6.4  # the filled triangle, as the one of FreeCAD's own Datum_*.svg
DATUM_HEIGHT = 5.5426
DATUM_STEM = 8.9596  # from the apex of the triangle to the frame
DATUM_BOX = 7.0  # the frame of the letter is a square of the height of a feature control frame
DATUM_DIRECTIONS = {"up": (0.0, -1.0), "down": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}  # svg, y down: from the feature to the frame
MARGIN_MM = 0.4
LONG_RISE = 5.0  # the check mark: the long leg rises this much (the bar is at its top)
SHORT_RISE = 2.5
TAN60 = 1.7320508
BAR_MM = 18.0  # the bar over the roughness text: holds "Ra 12,5"
BAR_TEXT_PAD = 0.8
DEFAULT_ROUGHNESS = "Ra 3,2"
MACHINING = ("required", "forbidden", "any")


def _svg(title: str, box: tuple[float, float, float, float], body: list[str]) -> str:
    x0, y0, x1, y1 = box
    w, h = x1 - x0 + 2 * MARGIN_MM, y1 - y0 + 2 * MARGIN_MM
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:freecad="{NAMESPACE_FREECAD}" version="1.1" '
            f'width="{_n(w)}mm" height="{_n(h)}mm" viewBox="{_n(x0 - MARGIN_MM)} {_n(y0 - MARGIN_MM)} {_n(w)} {_n(h)}">',
            f"  <title>{title}</title>",
            *body,
            "</svg>",
            "",
        ]
    )


def _group(name: str, fill: str = "none") -> str:
    return (
        f'  <g id="{name}" style="fill:{fill};stroke:#000000;stroke-width:{_n(LINE_MM)};'
        'stroke-linecap:round;stroke-linejoin:round">'
    )


def _path(points: list[tuple[float, float]], close: bool = False) -> str:
    return "M " + " L ".join(f"{_n(x)} {_n(y)}" for x, y in points) + (" Z" if close else "")


def render_datum(direction: str) -> str:
    """The datum feature symbol with the frame on the ``direction`` side of the feature: the filled triangle stands on the
    feature with its base (the origin of the drawing is the middle of the base), the stem runs from its apex to the frame and
    the letter is the editable text of the frame."""
    dx, dy = DATUM_DIRECTIONS[direction]
    px, py = -dy, dx  # across the stem

    def at(along: float, across: float) -> tuple[float, float]:
        return along * dx + across * px, along * dy + across * py

    near = DATUM_HEIGHT + DATUM_STEM
    half = DATUM_BOX / 2
    corners = [at(near, -half), at(near + DATUM_BOX, -half), at(near + DATUM_BOX, half), at(near, half)]
    base = [at(0, -DATUM_BASE / 2), at(0, DATUM_BASE / 2), at(DATUM_HEIGHT, 0)]
    centre = at(near + half, 0)
    xs = [p[0] for p in (*corners, *base)]
    ys = [p[1] for p in (*corners, *base)]
    pad = LINE_MM / 2
    box = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    style = f"font-size:{_n(TEXT_MM)}px;font-family:{FONT};text-anchor:middle;fill:#000000;stroke:none"
    body = [
        _group("datum"),
        f'    <path d="{_path(base, True)}" style="fill:#000000"/>',
        f'    <path d="{_path([at(DATUM_HEIGHT, 0), at(near, 0)])}"/>',
        f'    <path d="{_path(corners, True)}" style="fill:#ffffff"/>',
        "  </g>",
        f'  <text freecad:editable="letter" x="{_n(centre[0])}" y="{_n(centre[1] + BASELINE_MM - CELL_H / 2)}" style="{style}"><tspan>A</tspan></text>',
    ]
    return _svg(f"ISO 5459 datum feature symbol - frame {direction}", box, body)


def _check_mark(machining: str) -> tuple[list[str], tuple[float, float]]:
    """The check mark with its vertex at the origin (svg: y down): the elements and the end of the long leg."""
    half = LONG_RISE / TAN60
    top = (half, -LONG_RISE)
    out: list[str] = []
    if machining == "required":
        out.append(f'    <path d="M 0 0 L {_n(-half)} {_n(-LONG_RISE)} L {_n(top[0])} {_n(top[1])} Z"/>')
    else:
        short = (-SHORT_RISE / TAN60, -SHORT_RISE)
        out.append(f'    <path d="M {_n(short[0])} {_n(short[1])} L 0 0 L {_n(top[0])} {_n(top[1])}"/>')
    if machining == "forbidden":
        out.append(f'    <circle cx="0" cy="{_n(-SHORT_RISE * 0.96)}" r="{_n(SHORT_RISE * 0.48)}"/>')
    return out, top


def render_finish(machining: str, general: bool = False, bar: bool = True) -> str:
    """The surface texture symbol with its vertex at the origin: the check mark (a closed triangle when machining is required,
    a circle in it when it is forbidden), the bar from the top of the long leg and the roughness text under the bar (editable).
    The general one adds the basic symbol in parentheses ("all the other surfaces")."""
    marks, top = _check_mark(machining)
    style = f"font-size:{_n(TEXT_MM)}px;font-family:{FONT};text-anchor:start;fill:#000000;stroke:none"
    end = top[0] + (BAR_MM if bar else 0.0)
    body = [_group("finish"), *marks]
    if bar:
        body.append(f'    <path d="M {_n(top[0])} {_n(top[1])} H {_n(end)}"/>')
    body.append("  </g>")
    if bar:
        body.append(
            f'  <text freecad:editable="roughness" x="{_n(top[0] + BAR_TEXT_PAD)}" y="{_n(top[1] + BAR_TEXT_PAD + 2.5)}" style="{style}"><tspan>{DEFAULT_ROUGHNESS}</tspan></text>'
        )
    right = end + 0.5
    if general:
        paren = f"font-size:6px;font-family:{FONT};text-anchor:start;fill:#000000;stroke:none"
        x_open = end + 1.0
        basic, basic_top = _check_mark("any")
        shift = x_open + 4.5 + (LONG_RISE / TAN60) * 0.5
        body.append(_group("basic").replace(f'<g id="basic"', f'<g id="basic" transform="translate({_n(shift)} 0)"'))
        body += basic
        body.append("  </g>")
        x_close = shift + basic_top[0] + 0.5
        body.append(f'  <text x="{_n(x_open)}" y="-0.3" style="{paren}"><tspan>(</tspan></text>')
        body.append(f'  <text x="{_n(x_close)}" y="-0.3" style="{paren}"><tspan>)</tspan></text>')
        right = x_close + 2.5
    box = (-LONG_RISE / TAN60 - LINE_MM, -LONG_RISE - 1.0, right, 0.8)
    title = (
        f"ISO 1302 surface texture - machining {machining}"
        + (", for all the other surfaces" if general else "")
        + ("" if bar else ", no value")
    )
    return _svg(title, box, body)


def datum_path(direction: str) -> str:
    return f"datum/datum-{direction}.svg"


def finish_path(machining: str, general: bool = False, bar: bool = True) -> str:
    return f"finish/finish-{'general-' if general else ''}{machining}{'' if bar else '-bare'}.svg"


def all_files() -> dict[str, str]:
    """Every file of the library: the path inside ``cotaforge-gdt`` and its text."""
    files: dict[str, str] = {}
    for name, counts in DATUM_RANGE.items():
        for datums in counts:
            for width in TOLERANCE_W:
                files[relative_path(name, datums, width)] = render(name, datums, width)
    for direction in DATUM_DIRECTIONS:
        files[datum_path(direction)] = render_datum(direction)
    for machining in MACHINING:
        files[finish_path(machining)] = render_finish(machining)
        files[finish_path(machining, general=True)] = render_finish(machining, general=True)
    files[finish_path("forbidden", bar=False)] = render_finish("forbidden", bar=False)
    return files


def library_root() -> Path:
    return Path(__file__).resolve().parent / "cotaforge-gdt"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when the files on disk differ from what is written")
    args = parser.parse_args(argv)
    root = library_root()
    wanted = all_files()
    if args.check:
        on_disk = {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*.svg")}
        if on_disk != wanted:
            diff = sorted(set(on_disk) ^ set(wanted) | {k for k in wanted if on_disk.get(k) != wanted[k]})
            sys.stderr.write(f"cotaforge-gdt is out of date ({len(diff)} files): run build_gdt_frames.py\n")
            return 1
        return 0
    for old in root.rglob("*.svg") if root.is_dir() else ():
        if old.relative_to(root).as_posix() not in wanted:
            old.unlink()
    for relative, text in wanted.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(f"{len(wanted)} files in {root}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
