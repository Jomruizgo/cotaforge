# ruff: noqa: E501
"""Whether a cylinder of an assembly is SEEN in a view of the sheet (no FreeCAD import).

A cylinder of one part can lie behind another part: a spacer sleeve inside a plate shows no side lines and no circle in the
views that part hides it from, so the axis line (seen from the side) or the centre mark (seen along its axis) of the sheet
only goes where the view really shows the cylinder. What the view shows is its visible outline (the contour ink TechDraw
projected): a cylinder is seen side-on when both of its silhouette lines are drawn over most of its length, and along its axis
when a circle of its radius is drawn at its centre.
"""

import math
from typing import Any

import sheet_viewrules as rules
from sheet_collide import segments_of
from sheet_model import CONTOUR, Arc, Circle

SEEN_SHARE = 0.5  # both silhouette lines are drawn over at least this share of the cylinder's length
CIRCLE_TOL_MM = 0.2


def _contour(ctx: Any) -> list[Any]:
    return [e for e in ctx.extras.get("view_ink", []) if getattr(e, "layer", None) == CONTOUR]


def side_on_seen(ctx: Any, view: Any, boss: Any) -> bool:
    """The cylinder ``boss`` (``sheet_checks.Boss``) is seen from the side in ``view``: both lines of its silhouette are drawn."""
    half = boss.length / 2
    a = view.project(tuple(p - u * half for p, u in zip(boss.axis_point, boss.axis_dir, strict=True)))
    b = view.project(tuple(p + u * half for p, u in zip(boss.axis_point, boss.axis_dir, strict=True)))
    length = math.dist(a, b)
    if length < 0.5:
        return False
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    r = boss.radius * view.scale
    outline = segments_of(_contour(ctx))
    for sign in (1.0, -1.0):
        shift = (-uy * r * sign, ux * r * sign)
        p = (a[0] + shift[0], a[1] + shift[1])
        q = (b[0] + shift[0], b[1] + shift[1])
        if rules.uncovered_run(p, q, outline) > (1.0 - SEEN_SHARE) * length:
            return False
    return True


def circle_seen(ctx: Any, view: Any, boss: Any) -> bool:
    """The cylinder ``boss`` is seen along its axis in ``view``: a circle of its radius is drawn at its centre."""
    cx, cy = view.project(boss.axis_point)
    r = boss.radius * view.scale
    return any(
        isinstance(e, Circle | Arc) and math.hypot(e.cx - cx, e.cy - cy) <= CIRCLE_TOL_MM and abs(e.r - r) <= CIRCLE_TOL_MM
        for e in _contour(ctx)
    )
