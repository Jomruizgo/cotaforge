# ruff: noqa: E501
"""The linear dimensions of the TechDraw page as the sheet rules judge them (no FreeCAD import).

The review composes the sheet from its items, and a dimension that an item draws as a TechDraw object stands on the page
where TechDraw puts its lines, not where the composed entities have them (a diameter drawn as a leader in the composed
sheet is an aligned dimension with two extension lines on the page). The rules that measure the lines of the dimensions
(``dimension_crossing``, the stacks) read what the page holds: ``dimension_shapes`` turns each linear TechDraw dimension
into the ``DimShape`` of ``sheet_viewrules`` from the two measured points and the two arrow tips FreeCAD reports
(``getLinearPoints`` in the page millimetres of the view, ``getArrowPositions`` in the view's own unscaled millimetres).

A dimension this cannot read (an oblique one, one TechDraw has not computed the lines of, a dimension of a view it cannot place,
an object that fails to answer) is returned apart as an ``(owner, why)`` row: it is never skipped. When the sheet's own drawing of
the same item has its shape (a headless FreeCAD computes no lines for the page), that shape stands in; the rest are listed by the
review as ``native_unread``, which holds the export.
"""

import math
from collections.abc import Callable
from typing import Any

from sheet_model import text_width
from sheet_viewrules import ARROW_MM, DimShape

AXIS_TOL_MM = 0.05  # the dimension line is vertical / horizontal when its arrow tips differ by less than this across it
TEXT_MM = 3.5
LINEAR = ("Distance", "DistanceX", "DistanceY", "Extent")  # the dimension types that have extension lines


def _point(value: Any) -> tuple[float, float]:
    return float(value.x), float(value.y)


def _first_view(obj: Any) -> Any | None:
    """The view a dimension measures: the object its first 2D reference points at (an extent dimension: its source)."""
    for name in ("References2D", "Source"):
        try:
            first = getattr(obj, name, None)
            first = first[0] if isinstance(first, (list, tuple)) and first else first
            first = first[0] if isinstance(first, (list, tuple)) else first
        except (IndexError, TypeError, AttributeError):
            continue
        if first is not None:
            return first
    return None


def shape_of(
    owner: str,
    view: str,
    points: tuple[tuple[float, float], tuple[float, float]],
    arrows: tuple[tuple[float, float], tuple[float, float]],
    origin: tuple[float, float],
    scale: float,
    text: str = "",
) -> DimShape | None:
    """The ``DimShape`` of a linear dimension from its measured ``points`` (the view's page mm, from the view's origin), its arrow
    tips ``arrows`` (the view's unscaled mm) and the view's ``origin`` (page mm) and ``scale``. ``None`` when the dimension line is
    neither vertical nor horizontal or its two ends are the same."""
    ox, oy = origin
    measured = [(ox + p[0], oy + p[1]) for p in points]
    tips = [(ox + a[0] * scale, oy + a[1] * scale) for a in arrows]
    if math.dist(tips[0], tips[1]) < 1e-6:
        return None
    vertical = abs(tips[0][0] - tips[1][0]) <= AXIS_TOL_MM  # a vertical dimension line: its extension lines are horizontal
    horizontal = abs(tips[0][1] - tips[1][1]) <= AXIS_TOL_MM
    if vertical == horizontal:  # both: a point; neither: oblique
        return None
    if vertical:
        line_at = (tips[0][0] + tips[1][0]) / 2
        extension = tuple((p[0], p[1], line_at, p[1]) for p in measured)
        span = abs(measured[0][1] - measured[1][1])
    else:
        line_at = (tips[0][1] + tips[1][1]) / 2
        extension = tuple((p[0], p[1], p[0], line_at) for p in measured)
        span = abs(measured[0][0] - measured[1][0])
    width = text_width(text, TEXT_MM) if text else 0.0
    return DimShape(owner, view, extension, line_at, span, width, None, False, ARROW_MM)


def dimension_shapes(
    page: Any,
    names: dict[str, str],
    position: Callable[[Any, Any], tuple[float, float]],
    item_prop: str,
) -> tuple[list[DimShape], list[tuple[str, str]]]:
    """The linear dimensions of ``page`` as shapes, and the ones that could not be read as ``(owner, "label: why")`` rows.

    ``names``: the name of a view object -> the name the review uses for it (front, top...); ``position(page, view)``: the
    page position of a view's origin; ``item_prop``: the property that names the item an object belongs to (a dimension
    nobody tagged is its own owner: its label)."""
    shapes: list[DimShape] = []
    unread: list[tuple[str, str]] = []
    for obj in page.Views:
        if not hasattr(obj, "getArrowPositions") or str(getattr(obj, "Type", "")) not in LINEAR:
            continue
        label = str(getattr(obj, "Label", "") or getattr(obj, "Name", ""))
        owner = str(getattr(obj, item_prop, "") or label)
        view = _first_view(obj)
        try:
            if view is None:
                unread.append((owner, f"{label}: it measures no view of the page"))
                continue
            arrows, points = list(obj.getArrowPositions()), list(obj.getLinearPoints())
            if len(arrows) < 2 or len(points) < 2 or math.dist(_point(arrows[0]), _point(arrows[1])) < 1e-6:  # noqa: PLR2004 - a linear dimension has two of each
                unread.append((owner, f"{label}: TechDraw has not computed its lines"))
                continue
            shape = shape_of(
                owner,
                names.get(str(view.Name), str(getattr(view, "Label", view.Name))),
                (_point(points[0]), _point(points[1])),
                (_point(arrows[0]), _point(arrows[1])),
                position(page, view),
                float(view.getScale()),
                str(obj.getText()),
            )
        except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind: the dimension is listed, never skipped
            unread.append((owner, f"{label}: {type(exc).__name__}: {exc}"))
            continue
        if shape is None:
            unread.append((owner, f"{label}: its dimension line is oblique, its position is not read"))
        else:
            shapes.append(shape)
    return shapes, unread


def merged(model: list[DimShape], page: list[DimShape]) -> list[DimShape]:
    """The shapes the rules judge: the page's for every item that has TechDraw dimensions, the composed sheet's for the rest (an
    item drawn as a symbol, a sheet read without its page)."""
    held = {s.owner for s in page}
    return [*(s for s in model if s.owner not in held), *page]
