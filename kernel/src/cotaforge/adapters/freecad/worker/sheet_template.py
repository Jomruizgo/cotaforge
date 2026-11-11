# ruff: noqa: E501
"""A drawing template (the SVG TechDraw uses: frame, title block, editable fields) as sheet entities, so the title block
goes into the DXF as well as into the PDF (no FreeCAD import, so it is unit-testable on its own).

Only what the templates use is understood: ``rect``, ``circle``, ``path`` made of straight segments (``M L H V Z``,
absolute or relative) and ``text``, with the style inherited from the groups. A template element this reader does not
know is an error, not something silently left out of the title block. Text marked ``freecad:editable="name"`` takes the
value of ``fields[name]``; one with ``data-maxwidth`` is made smaller until the value fits that many millimetres.
"""

import re
import xml.etree.ElementTree as ET

import sheet_units as su
from sheet_model import AXES, TITLE_BLOCK, Circle, Entity, Line, Polyline, Solid, Text, text_width

SVG_NS = "{http://www.w3.org/2000/svg}"
FREECAD_NS = "{https://www.freecad.org/wiki/index.php?title=Svg_Namespace}"
SKIPPED = {"title", "metadata", "defs"}
MIN_TEXT_MM = 2.0
_TOKEN = re.compile(r"([MmLlHhVvZz])|(-?(?:\d+\.?\d*|\.\d+)(?:[eE]-?\d+)?)")


def _style(element: ET.Element, inherited: dict[str, str]) -> dict[str, str]:
    style = dict(inherited)
    for key in ("fill", "stroke", "stroke-width", "stroke-dasharray", "font-size", "text-anchor"):
        if element.get(key) is not None:
            style[key] = str(element.get(key))
    for part in (element.get("style") or "").split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            style[key.strip()] = value.strip()
    return style


def _px(value: str | None, default: float) -> float:
    if not value:
        return default
    match = re.match(r"\s*(-?[\d.]+)", value)
    return float(match.group(1)) if match else default


def _subpaths(d: str) -> list[tuple[list[tuple[float, float]], bool]]:
    """The straight subpaths of an SVG path: (points, closed), in SVG coordinates."""
    tokens = [(m.group(1), m.group(2)) for m in _TOKEN.finditer(d)]
    paths: list[tuple[list[tuple[float, float]], bool]] = []
    points: list[tuple[float, float]] = []
    x = y = start_x = start_y = 0.0
    command = ""
    index = 0

    def number() -> float:
        nonlocal index
        word, num = tokens[index]
        if num is None:
            msg = f"path {d!r}: a number was expected"
            raise ValueError(msg)
        index += 1
        return float(num)

    while index < len(tokens):
        word, _ = tokens[index]
        if word is not None:
            command = word
            index += 1
            if command in "Zz":
                if points:
                    paths.append((points, True))
                    points = []
                x, y = start_x, start_y
                continue
        elif not command or command in "Zz":
            msg = f"path {d!r}: numbers without a command"
            raise ValueError(msg)
        relative = command.islower()
        kind = command.upper()
        if kind == "M":
            nx, ny = number(), number()
            x, y = (x + nx, y + ny) if relative else (nx, ny)
            if points:
                paths.append((points, False))
            points = [(x, y)]
            start_x, start_y = x, y
            command = "l" if relative else "L"  # further pairs after a moveto are line-tos
        elif kind == "L":
            nx, ny = number(), number()
            x, y = (x + nx, y + ny) if relative else (nx, ny)
            points.append((x, y))
        elif kind == "H":
            nx = number()
            x = x + nx if relative else nx
            points.append((x, y))
        elif kind == "V":
            ny = number()
            y = y + ny if relative else ny
            points.append((x, y))
    if points:
        paths.append((points, False))
    return paths


def _shape(points: list[tuple[float, float]], closed: bool, style: dict[str, str], page_h: float) -> list[Entity]:
    flipped = tuple((px, page_h - py) for px, py in points)
    fill = style.get("fill", "none")
    stroked = style.get("stroke", "none") != "none"
    layer = AXES if style.get("stroke-dasharray", "none") != "none" else TITLE_BLOCK
    width = _px(style.get("stroke-width"), 0.35) if stroked else None
    if closed and fill != "none" and not stroked:
        return [Solid(flipped, TITLE_BLOCK)]
    if len(flipped) == 2 and not closed:
        return [Line(flipped[0][0], flipped[0][1], flipped[1][0], flipped[1][1], layer, width)]
    return [Polyline(flipped, layer, closed, width)]


def _text(element: ET.Element, style: dict[str, str], fields: dict[str, str], page_h: float) -> list[Entity]:
    name = element.get(f"{FREECAD_NS}editable")
    if name is not None:
        content = fields.get(name, "")
    else:
        content = "".join(element.itertext())
    content = " ".join(content.split())
    if not content:
        return []
    size = _px(style.get("font-size"), 3.5)
    limit = element.get("data-maxwidth")
    if limit and text_width(content, size) > float(limit):
        size = max(MIN_TEXT_MM, float(limit) / (len(content) * text_width("x", 1.0)))
    anchor = {"start": "left", "middle": "center", "end": "right"}.get(style.get("text-anchor", "start"), "left")
    return [Text(float(element.get("x", 0)), page_h - float(element.get("y", 0)), content, size, TITLE_BLOCK, anchor)]


def _walk(element: ET.Element, inherited: dict[str, str], fields: dict[str, str], page_h: float) -> list[Entity]:
    out: list[Entity] = []
    for child in element:
        tag = child.tag.removeprefix(SVG_NS)
        if tag in SKIPPED:
            continue
        style = _style(child, inherited)
        if tag == "g":
            out.extend(_walk(child, style, fields, page_h))
        elif tag == "rect":
            x, y = float(child.get("x", 0)), float(child.get("y", 0))
            w, h = float(child.get("width", 0)), float(child.get("height", 0))
            out.extend(_shape([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, style, page_h))
        elif tag == "circle":
            layer = AXES if style.get("stroke-dasharray", "none") != "none" else TITLE_BLOCK
            cx, cy, r = float(child.get("cx", 0)), float(child.get("cy", 0)), float(child.get("r", 0))
            out.append(Circle(cx, page_h - cy, r, layer, _px(style.get("stroke-width"), 0.35)))
        elif tag == "path":
            for points, closed in _subpaths(child.get("d", "")):
                out.extend(_shape(points, closed, style, page_h))
        elif tag == "text":
            out.extend(_text(child, style, fields, page_h))
        else:
            msg = f"the template has an element this reader does not know: <{tag}>"
            raise ValueError(msg)
    return out


def parse(template_svg: str, fields: dict[str, str]) -> tuple[float, float, list[Entity]]:
    """(page width mm, page height mm, entities) of the template with its editable ``fields`` filled in."""
    root = ET.fromstring(template_svg)
    box = (root.get("viewBox") or "").split()
    if len(box) != 4:
        msg = "the template has no viewBox"
        raise ValueError(msg)
    width, height = float(box[2]), float(box[3])
    return width, height, _walk(root, {}, fields, height)


TITLE_BLOCK_TOP_MAX_MM = 70.0  # the title block (and the revision table beside it) lies in the bottom 70 mm of the page
TITLE_BLOCK_FLOOR_MM = 9.5  # ... above the frame's bottom edge (10 mm); the grid labels below it are not part of it


def title_block_rect(entities: list[Entity], page_w: float) -> tuple[float, float, float, float] | None:
    """The rectangle the title block takes on the page (x0, y0, x1, y1; y up): the box of the template's entities that
    sit in the bottom-right corner above the frame's lower edge -- the rule is the shape of the corner, not a size, so it
    holds for the A4 and the A3 template alike -- or ``None`` when the template has none."""
    boxes = []
    for e in entities:
        if getattr(e, "layer", "") != TITLE_BLOCK:
            continue
        box = su.bounds([e])
        if box is None or box[1] < TITLE_BLOCK_FLOOR_MM or box[3] > TITLE_BLOCK_TOP_MAX_MM:
            continue
        if box[2] - box[0] > page_w * 0.8 or box[0] < page_w * 0.2:  # the frame, the grid ticks
            continue
        boxes.append(box)
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def editable_fields(template_svg: str) -> list[str]:
    """The names of the template's editable text fields, in document order."""
    root = ET.fromstring(template_svg)
    return [el.get(f"{FREECAD_NS}editable") for el in root.iter(f"{SVG_NS}text") if el.get(f"{FREECAD_NS}editable")]
