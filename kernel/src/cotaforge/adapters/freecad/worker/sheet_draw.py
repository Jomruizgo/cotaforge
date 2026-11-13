# ruff: noqa: E501
"""Annotation geometry as sheet entities (no FreeCAD import): dimension lines with extension lines and arrowheads,
leaders with their text, centre marks, angle dimensions and tables. Everything is in page millimetres, y up.

These are the drawing blocks the items (``items_*.py``) compose; they know nothing about the part.
"""

import math

from sheet_model import (
    AXES,
    DIMENSIONS,
    TEXT,
    TITLE_BLOCK,
    Arc,
    Entity,
    Line,
    Polyline,
    Solid,
    Text,
    text_width,
)

ARROW_LENGTH_MM = 3.0
ARROW_HALF_WIDTH_MM = 0.9
EXTENSION_GAP_MM = 1.0  # an extension line starts this far from the feature
EXTENSION_OVERSHOOT_MM = 2.0  # and goes this far beyond the dimension line
TEXT_GAP_MM = 1.2  # between a dimension line and its text
LINE_STEP_MM = (
    7.9  # between the lines of a multi-line callout: TechDraw spaces a dimension's lines 2.25 text heights (measured)
)
SHELF_MM = 4.0
Point = tuple[float, float]
Rect = tuple[float, float, float, float]


def arrow(tip: Point, direction: Point) -> Solid:
    """A filled arrowhead with its tip at ``tip`` pointing along the unit vector ``direction``."""
    dx, dy = direction
    bx, by = tip[0] - dx * ARROW_LENGTH_MM, tip[1] - dy * ARROW_LENGTH_MM
    px, py = -dy * ARROW_HALF_WIDTH_MM, dx * ARROW_HALF_WIDTH_MM
    return Solid(((tip[0], tip[1]), (bx + px, by + py), (bx - px, by - py)), DIMENSIONS)


def _sign(value: float) -> float:
    return -1.0 if value < 0 else 1.0


def arrows_inside(span: float, width: float, arrows: str = "auto") -> bool:
    """Whether the arrowheads of a dimension of ``span`` mm with a text ``width`` mm wide stand between its extension lines:
    ``auto`` puts them there only when the text and both arrows fit (otherwise they go outside, the way a draughtsman flips
    them); ``inside`` / ``outside`` are the engineer's choice."""
    if arrows == "auto":
        return span >= 2 * ARROW_LENGTH_MM + width
    return arrows == "inside"


def linear_dimension(
    a: Point, b: Point, orient: str, line: float, text: str, overflow: str = "hi", arrows: str = "auto"
) -> tuple[list[Entity], Rect]:
    """A dimension between the page points ``a`` and ``b``. ``orient`` ``"h"``: the dimension line is horizontal at
    ``y = line`` and measures along x; ``"v"``: vertical at ``x = line``, measuring along y, the text rotated to read
    from the right. ``arrows``: ``auto`` (outside when text and arrows do not fit between the extension lines), ``inside``
    or ``outside``. A text too wide for the span goes beside the arrow of the ``overflow`` end (``"hi"``: the right / upper
    one, ``"lo"``: the left / lower one: the end of a baseline dimension that sits on its datum, where nothing else is).
    Returns the entities and the rectangle they take (for stacking and collisions)."""
    out: list[Entity] = []
    height = 3.5
    width = text_width(text, height)
    if orient == "h":
        x1, x2 = a[0], b[0]
        for px, py in (a, b):
            direction = _sign(line - py)
            if abs(line - py) > EXTENSION_GAP_MM:
                out.append(
                    Line(
                        px, py + direction * EXTENSION_GAP_MM, px, line + direction * EXTENSION_OVERSHOOT_MM, DIMENSIONS
                    )
                )
        lo, hi = min(x1, x2), max(x1, x2)
        span = hi - lo
        inside = arrows_inside(span, width, arrows)
        if inside:
            out.append(Line(lo, line, hi, line, DIMENSIONS))
            out += [arrow((lo, line), (-1.0, 0.0)), arrow((hi, line), (1.0, 0.0))]
        else:
            out.append(Line(lo - 2 * ARROW_LENGTH_MM, line, hi + 2 * ARROW_LENGTH_MM, line, DIMENSIONS))
            out += [arrow((lo, line), (1.0, 0.0)), arrow((hi, line), (-1.0, 0.0))]
        if width <= span - 1 or not inside:
            mid = (lo + hi) / 2
            if width > span - 1 and overflow == "lo":  # text does not fit: beside the left arrow
                out.append(Text(lo - 2 * ARROW_LENGTH_MM - 1, line + TEXT_GAP_MM, text, height, DIMENSIONS, "right"))
                box = (
                    lo - 2 * ARROW_LENGTH_MM - 1 - width,
                    line - 1,
                    hi + 2 * ARROW_LENGTH_MM,
                    line + TEXT_GAP_MM + height,
                )
            elif width > span - 1:  # text does not fit between the extension lines: beside the right arrow
                out.append(Text(hi + 2 * ARROW_LENGTH_MM + 1, line + TEXT_GAP_MM, text, height, DIMENSIONS, "left"))
                box = (
                    lo - 2 * ARROW_LENGTH_MM,
                    line - 1,
                    hi + 2 * ARROW_LENGTH_MM + 1 + width,
                    line + TEXT_GAP_MM + height,
                )
            else:
                out.append(Text(mid, line + TEXT_GAP_MM, text, height, DIMENSIONS, "center"))
                box = (lo, line - 1, hi, line + TEXT_GAP_MM + height)
        elif overflow == "lo":  # the arrows are inside but the text does not fit: beside the left one
            out.append(Text(lo - ARROW_LENGTH_MM - 1, line + TEXT_GAP_MM, text, height, DIMENSIONS, "right"))
            box = (lo - ARROW_LENGTH_MM - 1 - width, line - 1, hi, line + TEXT_GAP_MM + height)
        else:
            out.append(Text(hi + ARROW_LENGTH_MM + 1, line + TEXT_GAP_MM, text, height, DIMENSIONS, "left"))
            box = (lo, line - 1, hi + ARROW_LENGTH_MM + 1 + width, line + TEXT_GAP_MM + height)
        return out, box
    y1, y2 = a[1], b[1]
    for px, py in (a, b):
        direction = _sign(line - px)
        if abs(line - px) > EXTENSION_GAP_MM:
            out.append(
                Line(px + direction * EXTENSION_GAP_MM, py, line + direction * EXTENSION_OVERSHOOT_MM, py, DIMENSIONS)
            )
    lo, hi = min(y1, y2), max(y1, y2)
    span = hi - lo
    inside = arrows_inside(span, width, arrows)
    if inside:
        out.append(Line(line, lo, line, hi, DIMENSIONS))
        out += [arrow((line, lo), (0.0, -1.0)), arrow((line, hi), (0.0, 1.0))]
    else:
        out.append(Line(line, lo - 2 * ARROW_LENGTH_MM, line, hi + 2 * ARROW_LENGTH_MM, DIMENSIONS))
        out += [arrow((line, lo), (0.0, 1.0)), arrow((line, hi), (0.0, -1.0))]
    mid = (lo + hi) / 2
    if width > span - 1 and overflow == "lo":
        out.append(Text(line - TEXT_GAP_MM, lo - 2 * ARROW_LENGTH_MM - 1, text, height, DIMENSIONS, "right", 90.0))
        box = (line - TEXT_GAP_MM - height, lo - 2 * ARROW_LENGTH_MM - 1 - width, line + 1, hi + 2 * ARROW_LENGTH_MM)
    elif width > span - 1:
        out.append(Text(line - TEXT_GAP_MM, hi + 2 * ARROW_LENGTH_MM + 1, text, height, DIMENSIONS, "left", 90.0))
        box = (line - TEXT_GAP_MM - height, lo, line + 1, hi + 2 * ARROW_LENGTH_MM + 1 + width)
    else:
        out.append(Text(line - TEXT_GAP_MM, mid, text, height, DIMENSIONS, "center", 90.0))
        box = (line - TEXT_GAP_MM - height, lo, line + 1, hi)
    return out, box


def leader(tip: Point, elbow: Point, lines: list[str], side: str, height: float = 3.5) -> tuple[list[Entity], Rect]:
    """A leader: an arrow at ``tip``, a line to ``elbow`` and a shelf toward ``side`` (``right`` / ``left``) with the
    text lines above it (the first line on top). Returns the entities and the rectangle the text takes."""
    out: list[Entity] = [Line(tip[0], tip[1], elbow[0], elbow[1], DIMENSIONS)]
    dx, dy = elbow[0] - tip[0], elbow[1] - tip[1]
    length = math.hypot(dx, dy)
    if length > ARROW_LENGTH_MM:
        out.append(arrow(tip, (-dx / length, -dy / length)))
    direction = 1.0 if side == "right" else -1.0
    widest = max((text_width(t, height) for t in lines), default=0.0)
    end = elbow[0] + direction * max(SHELF_MM, widest + 1.0)
    out.append(Line(elbow[0], elbow[1], end, elbow[1], DIMENSIONS))
    anchor = "left" if direction > 0 else "right"
    count = len(lines)
    for index, text in enumerate(lines):
        baseline = elbow[1] + 0.8 + (count - 1 - index) * LINE_STEP_MM
        out.append(Text(elbow[0] + direction * 0.5, baseline, text, height, TEXT, anchor))
    x0, x1 = sorted((elbow[0], end))
    box = (x0, elbow[1], x1, elbow[1] + 0.8 + count * LINE_STEP_MM)
    return out, box


def centre_mark(cx: float, cy: float, r: float) -> list[Entity]:
    """The cross of a circle's centre lines, reaching 2 mm beyond the circle."""
    reach = r + 2.0
    return [Line(cx - reach, cy, cx + reach, cy, AXES), Line(cx, cy - reach, cx, cy + reach, AXES)]


def angle_dimension(apex: Point, a0: float, a1: float, radius: float, text: str) -> tuple[list[Entity], Rect]:
    """An angle dimension: an arc of ``radius`` about ``apex`` from ray angle ``a0`` to ``a1`` (degrees, the arc runs
    counter-clockwise from the first to the second, so the caller orders them), arrowheads on both ends, the two rays
    extended to the arc and the text outside it."""
    out: list[Entity] = [Arc(apex[0], apex[1], radius, a0, a1, DIMENSIONS)]
    for angle in (a0, a1):
        rad = math.radians(angle)
        out.append(
            Line(
                apex[0],
                apex[1],
                apex[0] + (radius + EXTENSION_OVERSHOOT_MM) * math.cos(rad),
                apex[1] + (radius + EXTENSION_OVERSHOOT_MM) * math.sin(rad),
                DIMENSIONS,
            )
        )
    for angle, turn in ((a0, 1.0), (a1, -1.0)):
        rad = math.radians(angle)
        tip = (apex[0] + radius * math.cos(rad), apex[1] + radius * math.sin(rad))
        tangent = (-math.sin(rad) * turn, math.cos(rad) * turn)  # pointing back along the arc, away from the tip
        out.append(arrow(tip, (-tangent[0], -tangent[1])))
    mid = math.radians((a0 + ((a1 - a0) % 360.0) / 2) % 360.0)
    tx, ty = apex[0] + (radius + 4.0) * math.cos(mid), apex[1] + (radius + 4.0) * math.sin(mid)
    anchor = "left" if math.cos(mid) >= 0 else "right"
    out.append(Text(tx, ty - 1.2, text, 3.5, DIMENSIONS, anchor))
    width = text_width(text)
    box = (
        min(tx, tx + (width if anchor == "left" else -width)),
        ty - 1.2,
        max(tx, tx + (width if anchor == "left" else -width)),
        ty + 2.5,
    )
    return out, box


def table(
    x: float,
    y_top: float,
    widths: list[float],
    header: list[str],
    rows: list[list[str]],
    title: str | None = None,
    row_height: float = 6.0,
    text_height: float = 3.5,
) -> tuple[list[Entity], Rect]:
    """A ruled table with its top-left corner at ``(x, y_top)`` (rows go down); numbers are right-aligned by the
    caller's strings, text is left-aligned in its cell. Returns the entities and the rectangle it takes."""
    out: list[Entity] = []
    total = sum(widths)
    count = len(rows) + 1
    top = y_top - (row_height if title else 0.0)
    if title:
        out.append(Text(x + 1.0, y_top - row_height + 1.8, title, text_height + 0.5, TITLE_BLOCK, "left"))
    bottom = top - count * row_height
    out.append(Line(x, top, x + total, top, TITLE_BLOCK, 0.35))
    for index in range(count + 1):
        out.append(Line(x, top - index * row_height, x + total, top - index * row_height, TITLE_BLOCK, 0.25))
    edge = x
    for width in [0.0, *widths]:
        edge += width
        out.append(Line(edge, top, edge, bottom, TITLE_BLOCK, 0.25))
    for r, cells in enumerate([header, *rows]):
        left = x
        for width, cell in zip(widths, cells, strict=False):
            out.append(Text(left + 1.0, top - (r + 1) * row_height + 1.8, cell, text_height, TITLE_BLOCK, "left"))
            left += width
    box = (x, bottom, x + total, y_top)
    return out, box


EXACT_PAD_MM = 1.0


def exact_box(entities: list[Entity]) -> None:
    """Draw the frame ISO 1101 / 5458 puts round a theoretically exact dimension (a basic size, the location of a feature
    with a position tolerance): a closed box round the value text of ``entities`` (a dimension's), turned with the text."""
    index = max((i for i, e in enumerate(entities) if isinstance(e, Text)), default=None)
    if index is None:
        return
    text: Text = entities[index]
    width = text_width(text.text, text.height)
    left = {"left": 0.0, "center": -width / 2, "right": -width}.get(text.anchor, 0.0)
    low, high = -EXACT_PAD_MM, text.height + 0.2
    angle = math.radians(text.angle)
    ux, uy = math.cos(angle), math.sin(angle)
    corners = []
    for u, v in (
        (left - EXACT_PAD_MM, low),
        (left + width + EXACT_PAD_MM, low),
        (left + width + EXACT_PAD_MM, high),
        (left - EXACT_PAD_MM, high),
    ):
        corners.append((text.x + u * ux - v * uy, text.y + u * uy + v * ux))
    entities.append(Polyline(tuple(corners), text.layer, closed=True))
