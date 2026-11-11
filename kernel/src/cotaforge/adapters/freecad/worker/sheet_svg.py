# ruff: noqa: E501
"""A drawing sheet (``sheet_model.Sheet``) as ONE SVG document (no FreeCAD import, so it is unit-testable on its own).

TechDraw can only render a sheet through its GUI scene (``TechDrawGui.exportPageAsPdf`` paints a scene that exists only
while the sheet is open in a window, which is why a PDF made without it came out blank), so the sheet is composed from
data and ``sheet_render`` turns this SVG into a PDF page or a PNG with Qt. The SVG is in page millimetres
(``viewBox`` = the page), one ``<g id="layer-NAME">`` per layer, y flipped from the model's y-up.
"""

import math
from collections.abc import Sequence
from xml.sax.saxutils import escape

from sheet_model import (
    LAYERS,
    TEXT,
    Arc,
    Circle,
    Entity,
    Glyph,
    Line,
    Polyline,
    Sheet,
    Solid,
    Text,
)

FONT_FAMILY = "osifont, Segoe UI, DejaVu Sans, sans-serif"
INK = "#000000"


def _n(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _stroke(layer: str, width: float | None) -> str:
    style = LAYERS.get(layer, LAYERS[TEXT])
    parts = [f'stroke-width="{_n(width if width is not None else style.width_mm)}"']
    if style.dash:
        parts.append(f'stroke-dasharray="{" ".join(_n(d) for d in style.dash)}"')
    return " ".join(parts)


def _line(e: Line, h: float) -> str:
    return f'<path d="M {_n(e.x1)} {_n(h - e.y1)} L {_n(e.x2)} {_n(h - e.y2)}" {_stroke(e.layer, e.width)}/>'


def _polyline(e: Polyline, h: float) -> str:
    coords = " L ".join(f"{_n(x)} {_n(h - y)}" for x, y in e.points)
    return f'<path d="M {coords}{" Z" if e.closed else ""}" {_stroke(e.layer, e.width)}/>'


def _arc(e: Arc, h: float) -> str:
    sweep_deg = (e.end - e.start) % 360.0
    a0, a1 = math.radians(e.start), math.radians(e.end)
    x0, y0 = e.cx + e.r * math.cos(a0), e.cy + e.r * math.sin(a0)
    x1, y1 = e.cx + e.r * math.cos(a1), e.cy + e.r * math.sin(a1)
    large = 1 if sweep_deg > 180.0 else 0
    # counter-clockwise on the page (y up) is sweep-flag 0 once y is flipped
    return (
        f'<path d="M {_n(x0)} {_n(h - y0)} A {_n(e.r)} {_n(e.r)} 0 {large} 0 {_n(x1)} {_n(h - y1)}" '
        f"{_stroke(e.layer, e.width)}/>"
    )


def _circle(e: Circle, h: float) -> str:
    return f'<circle cx="{_n(e.cx)}" cy="{_n(h - e.cy)}" r="{_n(e.r)}" {_stroke(e.layer, e.width)}/>'


def _text(e: Text, h: float) -> str:
    anchor = {"left": "start", "center": "middle", "right": "end"}.get(e.anchor, "start")
    x, y = _n(e.x), _n(h - e.y)
    rotate = f' transform="rotate({_n(-e.angle)} {x} {y})"' if e.angle else ""
    return (
        f'<text x="{x}" y="{y}" font-size="{_n(e.height)}" text-anchor="{anchor}" font-family="{FONT_FAMILY}" '
        f'fill="{INK}" stroke="none"{rotate}>{escape(e.text)}</text>'
    )


def _solid(e: Solid, h: float) -> str:
    coords = " L ".join(f"{_n(x)} {_n(h - y)}" for x, y in e.points)
    if e.fill is not None:  # a shaded polygon: its own stroke closes the hairline gaps between neighbours
        colour = "#{:02x}{:02x}{:02x}".format(*e.fill)
        return f'<path d="M {coords} Z" fill="{colour}" stroke="{colour}" stroke-width="0.12" stroke-linejoin="round"/>'
    return f'<path d="M {coords} Z" fill="{INK}" stroke="none"/>'


def _entity(e: Entity, h: float) -> list[str]:
    if isinstance(e, Line):
        return [_line(e, h)]
    if isinstance(e, Polyline):
        return [_polyline(e, h)]
    if isinstance(e, Arc):
        return [_arc(e, h)]
    if isinstance(e, Circle):
        return [_circle(e, h)]
    if isinstance(e, Text):
        return [_text(e, h)]
    if isinstance(e, Solid):
        return [_solid(e, h)]
    if isinstance(e, Glyph):
        return [_polyline(s, h) for s in e.strokes()]
    msg = f"unknown sheet entity {type(e).__name__}"
    raise TypeError(msg)


def fragment(
    entities: "Sequence[Entity]", page_h: float, rect: tuple[float, float, float, float], title: str = ""
) -> str:
    """A part of a sheet as its own SVG document: the ``entities`` (page coordinates, y up) seen through ``rect`` (x0, y0,
    x1, y1 on the page, y up), one millimetre per unit, so a TechDraw symbol made from it has the size of ``rect`` and,
    centred on the middle of ``rect``, every entity lands where the PDF puts it. Same layers and strokes as ``to_svg``."""
    x0, y0, x1, y1 = rect
    width, height = x1 - x0, y1 - y0
    by_layer: dict[str, list[Entity]] = {}
    for entity in entities:
        by_layer.setdefault(getattr(entity, "layer", TEXT), []).append(entity)
    order = [name for name in LAYERS if name in by_layer] + sorted(n for n in by_layer if n not in LAYERS)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(width)}mm" height="{_n(height)}mm" '
        f'viewBox="{_n(x0)} {_n(page_h - y1)} {_n(width)} {_n(height)}" version="1.1">',
        f"<title>{escape(title)}</title>",
    ]
    for name in order:
        out.append(f'<g id="layer-{name}" fill="none" stroke="{INK}" stroke-linecap="round" stroke-linejoin="round">')
        for entity in by_layer[name]:
            out.extend(_entity(entity, page_h))
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


def to_svg(sheet: Sheet) -> str:
    """The sheet as an SVG document; layers appear in the order of ``LAYERS``, then any other layer by name."""
    h = sheet.height
    by_layer: dict[str, list[Entity]] = {}
    for entity in sheet.entities:
        by_layer.setdefault(getattr(entity, "layer", TEXT), []).append(entity)
    order = [name for name in LAYERS if name in by_layer] + sorted(n for n in by_layer if n not in LAYERS)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(sheet.width)}mm" height="{_n(h)}mm" '
        f'viewBox="0 0 {_n(sheet.width)} {_n(h)}" version="1.1">',
        f"<title>{escape(sheet.title)}</title>",
    ]
    if sheet.underlay:  # first: everything else is painted over it
        out.append('<g id="layer-underlay" stroke="none">')
        for entity in sheet.underlay:
            out.extend(_entity(entity, h))
        out.append("</g>")
    for name in order:
        out.append(f'<g id="layer-{name}" fill="none" stroke="{INK}" stroke-linecap="round" stroke-linejoin="round">')
        for entity in by_layer[name]:
            out.extend(_entity(entity, h))
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)
