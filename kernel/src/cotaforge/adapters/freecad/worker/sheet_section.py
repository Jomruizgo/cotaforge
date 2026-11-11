# ruff: noqa: E501
"""What a section view shows of the features the cutting plane runs along (no FreeCAD import): the centre line of every
bore and round boss it goes through, the ISO 6410 thread lines of a threaded bore, and the dimension across a bore.

The section view itself is ``items_views.draw_section``; it registers the view as a ``ViewInfo`` (``derived``) so the other
items can name it (``view="A-A"``) like a standard one. The geometry here is read from the model -- the holes' axes, depths
and diameters, the cylinders of the part -- and placed with ``ViewInfo.project``; nothing is typed.
"""

import math

from sheet_context import Context, HoleInfo, ItemError, Point, Vec3, ViewInfo, dot, hole_stacks, sub, unit
from sheet_draw import linear_dimension
from sheet_model import AXES, THREAD, Entity, Line
from sheet_thread import major_diameter

PLANE_TOL_MM = 0.05  # an axis lies in the cutting plane within this
AXIS_REACH_MM = 2.0  # a centre line goes this far beyond the ends of a bore or a boss (clear of the view label)
PARALLEL_TOL = 1e-3
AXIS_ON_PAGE = 0.99  # |component| of the axis on a page axis to call it horizontal or vertical


def in_plane(at: Vec3, normal: Vec3, point: Vec3, direction: Vec3) -> bool:
    """Whether the axis through ``point`` along ``direction`` lies in the plane through ``at`` with ``normal``."""
    return (
        abs(dot(unit(direction), unit(normal))) < PARALLEL_TOL
        and abs(dot(sub(point, at), unit(normal))) <= PLANE_TOL_MM
    )


def holes_cut(ctx: Context, at: Vec3, normal: Vec3) -> list[HoleInfo]:
    """The holes whose axis the plane contains: the section shows them lengthwise."""
    return [h for h in ctx.holes if in_plane(at, normal, h.centre, h.direction)]


def _page_axis(view: ViewInfo, start: Vec3, direction: Vec3, length: float) -> tuple[Point, Point, Point] | None:
    """``(a, b, u)``: the page ends of the segment from ``start`` along ``direction`` for ``length`` mm and the page unit
    vector from ``a`` to ``b``; ``None`` when it is a point on the page."""
    a = view.project(start)
    b = view.project(tuple(s + d * length for s, d in zip(start, unit(direction), strict=True)))  # type: ignore[arg-type]
    size = math.dist(a, b)
    if size < 0.5:
        return None
    return a, b, ((b[0] - a[0]) / size, (b[1] - a[1]) / size)


def _stack_reach(stack: list[HoleInfo]) -> tuple[Vec3, Vec3, float]:
    """``(start, direction, length)`` of the bore a stack of holes on one axis makes: from where it opens to its far end."""
    first = stack[0]
    d = unit(first.direction)
    ends = [
        0.0,
        *(dot(sub(h.centre, first.centre), d) for h in stack),
        *(dot(sub(h.centre, first.centre), d) + h.depth for h in stack),
    ]
    low, high = min(ends), max(ends)
    start = tuple(c + u * low for c, u in zip(first.centre, d, strict=True))
    return start, d, high - low  # type: ignore[return-value]


def centre_lines(ctx: Context, view: ViewInfo, at: Vec3, normal: Vec3) -> tuple[list[Entity], int]:
    """The centre line (a chain line, the ``EJES`` layer) of every bore the plane runs along and of every round boss
    (pin, shaft, hub) of the part whose axis lies in it."""
    out: list[Entity] = []
    count = 0
    for stack in hole_stacks(holes_cut(ctx, at, normal)):
        start, d, length = _stack_reach(stack)
        got = _page_axis(view, start, d, length)
        if got is not None:
            a, b, u = got
            out.append(
                Line(
                    a[0] - u[0] * AXIS_REACH_MM,
                    a[1] - u[1] * AXIS_REACH_MM,
                    b[0] + u[0] * AXIS_REACH_MM,
                    b[1] + u[1] * AXIS_REACH_MM,
                    AXES,
                )
            )
            count += 1
    facts = ctx.extras.get("facts")
    for boss in facts.bosses if facts is not None else ():
        if boss.length <= 0 or not in_plane(at, normal, boss.axis_point, boss.axis_dir):
            continue
        d = unit(boss.axis_dir)
        start = tuple(p - u * boss.length / 2 for p, u in zip(boss.axis_point, d, strict=True))
        got = _page_axis(view, start, d, boss.length)  # type: ignore[arg-type]
        if got is None:
            continue
        a, b, u = got
        line = Line(
            a[0] - u[0] * AXIS_REACH_MM,
            a[1] - u[1] * AXIS_REACH_MM,
            b[0] + u[0] * AXIS_REACH_MM,
            b[1] + u[1] * AXIS_REACH_MM,
            AXES,
        )
        if not any(
            _same_line(line, other) for other in out if isinstance(other, Line)
        ):  # a bore and a boss may share an axis
            out.append(line)
            count += 1
    return out, count


def _same_line(a: Line, b: Line) -> bool:
    """Whether two lines lie on one straight line on the page (the later one is not drawn twice)."""
    ux, uy = a.x2 - a.x1, a.y2 - a.y1
    size = math.hypot(ux, uy)
    if size < 1e-9:
        return False
    off = abs((b.x1 - a.x1) * uy - (b.y1 - a.y1) * ux) / size
    off2 = abs((b.x2 - a.x1) * uy - (b.y2 - a.y1) * ux) / size
    return off < 0.1 and off2 < 0.1


def thread_lines(view: ViewInfo, hole: HoleInfo, size: str, depth: float | None) -> list[Entity]:
    """The ISO 6410 representation of a threaded bore cut lengthwise: the thin continuous line at the major diameter on
    each side of the axis, as long as the thread (``depth``; the whole bore when ``None``). The hatching stops at the
    bore (the minor diameter, the thick line), so these lines run inside the hatched wall."""
    major = major_diameter(size)
    if major is None or major <= hole.diameter:
        return []
    length = hole.depth if depth is None else min(depth, hole.depth)
    got = _page_axis(view, hole.centre, hole.direction, length)
    if got is None:
        return []
    a, b, u = got
    n = (-u[1], u[0])
    r = major / 2 * view.scale
    return [
        Line(a[0] + n[0] * s * r, a[1] + n[1] * s * r, b[0] + n[0] * s * r, b[1] + n[1] * s * r, THREAD)
        for s in (1.0, -1.0)
    ]


def threads_cut(ctx: Context, view: ViewInfo, at: Vec3, normal: Vec3) -> tuple[list[Entity], int]:
    """The thread lines of every declared threaded hole the plane runs along."""
    out: list[Entity] = []
    count = 0
    for hole in holes_cut(ctx, at, normal):
        if hole.thread is None:
            continue
        got = thread_lines(view, hole, hole.thread[0], hole.thread[1])
        if got:
            out += got
            count += 1
    return out, count


def dimension_bore(
    ctx: Context, view: ViewInfo, stack: list[HoleInfo], text: str, side: str | None, offset_mm: float | None
) -> str:
    """The dimension of a bore the section shows lengthwise: a diameter dimension across it, its extension lines from the
    two walls at the end that opens on the side asked for (else the end the bore opens at), ``text`` on its line. The
    distance between the walls is the measured diameter; ``text`` is what is written there."""
    hole = stack[0]
    ax, ay = view.axis(unit(hole.direction))
    if abs(ay) >= AXIS_ON_PAGE:
        orient = "h"
    elif abs(ax) >= AXIS_ON_PAGE:
        orient = "v"
    else:
        msg = f"the bore is oblique in the {view.name} view: a diameter dimension across it needs it horizontal or vertical there"
        raise ItemError(msg)
    k = view.scale
    major = major_diameter(hole.thread[0]) if hole.thread is not None else None
    # a thread is dimensioned at its major diameter (the thin lines), a plain bore at its wall
    r = (major if major is not None and major > hole.diameter else hole.diameter) / 2 * k
    opening = view.project(hole.centre)
    far = view.project(tuple(c + u * hole.depth for c, u in zip(hole.centre, unit(hole.direction), strict=True)))  # type: ignore[arg-type]
    box = view.box
    allowed = ("above", "below") if orient == "h" else ("left", "right")
    if side is not None and side not in allowed:
        msg = f"this bore is {'vertical' if orient == 'h' else 'horizontal'} in the {view.name} view: side must be {' or '.join(allowed)}"
        raise ItemError(msg)
    if side is None:
        if hole.through:  # open at both ends: the side the view's label is not on (it hangs under the view)
            side = "above" if orient == "h" else "right"
        else:  # outside the part, where the bore opens: opposite to the way it goes into the material
            side = ("above" if -ay > 0 else "below") if orient == "h" else ("right" if -ax > 0 else "left")
    toward = {"above": (0.0, 1.0), "below": (0.0, -1.0), "right": (1.0, 0.0), "left": (-1.0, 0.0)}[side]
    end = max((opening, far), key=lambda p: p[0] * toward[0] + p[1] * toward[1])
    dist = ctx.stack_distance(view.name, side, offset_mm)
    if orient == "h":
        line = box[3] + dist if side == "above" else box[1] - dist
        entities, taken = linear_dimension((end[0] - r, end[1]), (end[0] + r, end[1]), "h", line, text)
    else:
        line = box[2] + dist if side == "right" else box[0] - dist
        entities, taken = linear_dimension((end[0], end[1] - r), (end[0], end[1] + r), "v", line, text)
    ctx.add(entities)
    ctx.reserve_dimension(entities, taken)
    ctx.used_view(view.name)
    return f"Ø{ctx.num(2 * r / k)} across the bore in the {view.name} view, {side}"
