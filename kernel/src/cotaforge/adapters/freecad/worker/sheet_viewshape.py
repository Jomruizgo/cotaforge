# ruff: noqa: E501
"""A dimension of the sheet as the facts ``sheet_viewrules`` judges (no FreeCAD import): its extension lines, where its
dimension line stands, the span between the arrows and whether the arrowheads are between the extension lines."""

import math
from collections.abc import Sequence

from sheet_model import DIMENSIONS, Entity, Line, Solid, Text, text_width
from sheet_viewrules import ARROW_MM, AXIS_TOL, DimShape


def dimension_shape(owner: str, view: str, entities: Sequence[Entity], arrows: str = "auto") -> DimShape | None:
    """The shape of a linear dimension drawn as ``entities`` (its lines, its two arrowheads and its text), or ``None`` for
    anything else (a leader, a callout: those have no extension lines to move)."""
    tips = [e for e in entities if isinstance(e, Solid) and e.layer == DIMENSIONS and len(e.points) == 3]
    texts = [e for e in entities if isinstance(e, Text)]
    if len(tips) != 2 or not texts:
        return None
    heads = [t.points for t in tips]
    a, b = heads[0][0], heads[1][0]
    span = math.dist(a, b)
    lines = [e for e in entities if isinstance(e, Line) and e.layer == DIMENSIONS]
    main = [e for e in lines if _on(e, a) and _on(e, b)]  # the dimension line runs through both arrow tips
    if not main:
        return None
    first = main[0]
    length = math.hypot(first.x2 - first.x1, first.y2 - first.y1)
    if length < 1e-6:
        return None
    horizontal = abs(first.y2 - first.y1) <= AXIS_TOL * length
    if not horizontal and abs(first.x2 - first.x1) > AXIS_TOL * length:
        return None
    line_at = first.y1 if horizontal else first.x1
    bases = [((h[1][0] + h[2][0]) / 2, (h[1][1] + h[2][1]) / 2) for h in heads]
    inside = math.dist(bases[0], b) < span and math.dist(bases[1], a) < span  # the bodies lie between the two tips
    extension = tuple((e.x1, e.y1, e.x2, e.y2) for e in lines if e not in main)
    width = max(text_width(t.text, t.height) for t in texts)
    return DimShape(
        owner, view, extension, line_at, span, width, inside, pinned=arrows != "auto", arrow_length=ARROW_MM
    )


def _on(line: Line, p: tuple[float, float]) -> bool:
    """Whether the point ``p`` lies on the infinite line of ``line`` (within a twentieth of a millimetre)."""
    dx, dy = line.x2 - line.x1, line.y2 - line.y1
    length = math.hypot(dx, dy)
    return length > 1e-9 and abs((p[0] - line.x1) * dy - (p[1] - line.y1) * dx) / length < 0.05
