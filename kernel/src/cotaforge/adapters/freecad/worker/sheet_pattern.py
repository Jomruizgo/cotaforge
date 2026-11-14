# ruff: noqa: E501
"""Circular hole patterns of a part (no FreeCAD import): holes of one size whose centres lie on one circle about an axis.

A bolt circle is located by its pitch circle diameter and, if it is not on a centre line, the angle of its first hole
(ISO 129-1 polar dimensioning); never by the X / Y of every hole. This module only FINDS the patterns and measures them in a
view; ``items_pattern`` draws and dimensions them, ``items_common.hole_mark`` turns the centre marks along the radius.

What counts as a pattern (a rule that must not mistake the four holes of a rectangle for a bolt circle):

* at least three stacks of holes (a counterbore is two holes on one axis: one stack) with the same size, depth, thread and
  direction, whose centres are on one circle to 0.05 mm;
* equally spaced: three or four need an axis of the part (a bore, a boss, the part's own axis) at the centre of the circle,
  five or more need none; unequally spaced: an axis at the centre and not the four corners of a rectangle (two pairs of
  holes opposite each other on the circle).
"""

import math
from dataclasses import dataclass
from typing import Any

from sheet_context import (
    Context,
    HoleInfo,
    ItemError,
    Vec3,
    ViewInfo,
    add,
    cross,
    dot,
    hole_stacks,
    norm,
    parallel,
    scale,
    sub,
    unit,
)

TOL_MM = 0.05
EVEN_TOL_DEG = 0.5
CENTRE_LINE_TOL_DEG = 0.05
MAX_STACKS = 40  # a group larger than this is not searched (the search is cubic)
MIN_RADIUS_MM = 1.0

Axis = tuple[Vec3, Vec3]  # a point on the axis and its direction


@dataclass(frozen=True)
class Pattern:
    """The holes of one circular pattern. ``centre`` is the centre of the pitch circle (on its axis, level with the first
    hole), ``members`` the indexes of the holes of each stack (one tuple per place of the pattern), ``reps`` the hole that
    stands for each stack (the smallest: a counterbore is its larger one)."""

    centre: Vec3
    direction: Vec3
    radius: float
    members: tuple[tuple[int, ...], ...]
    reps: tuple[int, ...]
    even: bool
    lone: bool = False

    @property
    def count(self) -> int:
        return len(self.reps)

    @property
    def indexes(self) -> frozenset[int]:
        return frozenset(i for stack in self.members for i in stack)

    @property
    def diameter(self) -> float:
        return 2 * self.radius


# --- finding ------------------------------------------------------------------------------------------------------------


def _signature(stack: list[HoleInfo]) -> tuple[Any, ...]:
    return tuple((round(h.diameter, 3), round(h.depth, 2), h.through, h.thread, h.fit) for h in stack)


def _canonical(d: Vec3) -> tuple[float, float, float]:
    u = unit(d)
    for c in u:
        if abs(c) > 1e-9:
            if c < 0:
                u = (-u[0], -u[1], -u[2])
            break
    return (round(u[0], 5) + 0.0, round(u[1], 5) + 0.0, round(u[2], 5) + 0.0)


def _basis(d: Vec3) -> tuple[Vec3, Vec3]:
    d = unit(d)
    helper = (1.0, 0.0, 0.0) if abs(d[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = unit(cross(d, helper))
    return u, cross(d, u)


def _circle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> tuple[float, float, float] | None:
    """The circle through three points (centre x, y and radius), ``None`` when they are on a line."""
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-9:
        return None
    sa, sb, sc = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    ux = (sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d
    uy = (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d
    return ux, uy, math.hypot(a[0] - ux, a[1] - uy)


def _best_circle(points: list[tuple[float, float]]) -> tuple[tuple[float, float, float], list[int]] | None:
    """The circle most of ``points`` lie on (at least three), with the indexes of the points on it."""
    best: tuple[tuple[float, float, float], list[int]] | None = None
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                found = _circle(points[i], points[j], points[k])
                if found is None or found[2] < MIN_RADIUS_MM:
                    continue
                on = [m for m, p in enumerate(points) if abs(math.hypot(p[0] - found[0], p[1] - found[1]) - found[2]) <= TOL_MM]
                if len(on) >= 3 and (best is None or len(on) > len(best[1])):
                    best = (found, on)
    return best


def on_axis(point: Vec3, direction: Vec3, axes: list[Axis]) -> bool:
    """Whether ``point`` is on one of ``axes`` that runs along ``direction``."""
    for origin, d in axes:
        if not parallel(d, direction):
            continue
        off = sub(point, origin)
        along = scale(unit(d), dot(off, unit(d)))
        if norm(sub(off, along)) <= TOL_MM:
            return True
    return False


def _gaps(angles: list[float]) -> list[float]:
    ordered = sorted(angles)
    return [(ordered[(i + 1) % len(ordered)] - ordered[i]) % 360.0 or 360.0 for i in range(len(ordered))]


def _rectangle(angles: list[float]) -> bool:
    """Four holes in two pairs opposite each other: the corners of a rectangle, whatever circle they happen to lie on."""
    if len(angles) != 4:
        return False
    return all(any(abs(((a - b) % 360.0) - 180.0) <= EVEN_TOL_DEG for b in angles) for a in angles)


def find(holes: list[HoleInfo], axes: list[Axis] | None = None) -> list[Pattern]:
    """The circular patterns of ``holes``; ``axes`` are the axes of the features of the part (bores, bosses)."""
    axes = list(axes or [])
    groups: dict[tuple[Any, ...], list[list[HoleInfo]]] = {}
    for stack in hole_stacks(holes):
        groups.setdefault((_canonical(stack[0].direction), _signature(stack)), []).append(stack)
    found: list[Pattern] = []
    for (direction, _), stacks in groups.items():
        remaining = list(stacks)
        u, v = _basis(direction)
        while 3 <= len(remaining) <= MAX_STACKS:
            points = [(dot(s[0].centre, u), dot(s[0].centre, v)) for s in remaining]
            best = _best_circle(points)
            if best is None:
                break
            (cu, cv, radius), on = best
            members = [remaining[i] for i in on]
            first = members[0][0]
            centre = add(first.centre, add(scale(u, cu - dot(first.centre, u)), scale(v, cv - dot(first.centre, v))))
            angles = [math.degrees(math.atan2(dot(s[0].centre, v) - cv, dot(s[0].centre, u) - cu)) % 360.0 for s in members]
            count = len(members)
            even = all(abs(g - 360.0 / count) <= EVEN_TOL_DEG for g in _gaps(angles))
            centred = on_axis(centre, direction, axes)
            if (even and (count >= 5 or centred)) or (not even and centred and not _rectangle(angles)):
                found.append(
                    Pattern(
                        centre,
                        unit(direction),
                        radius,
                        tuple(tuple(h.index for h in s) for s in members),
                        tuple(s[0].index for s in members),
                        even,
                    )
                )
            remaining = [s for i, s in enumerate(remaining) if i not in set(on)]
    return sorted(found, key=lambda p: min(p.reps))


def axes_of_holes(holes: list[HoleInfo]) -> list[Axis]:
    return [(h.centre, h.direction) for h in holes]


def feature_axes(ctx: Context) -> list[Axis]:
    """The axes of the part's features: its holes and its whole cylinders (bosses, shafts, the bores of a turned part)."""
    axes = axes_of_holes(ctx.holes)
    facts = ctx.extras.get("facts")
    for boss in facts.bosses if facts is not None else ():
        axes.append((boss.axis_point, boss.axis_dir))
    return axes


def patterns_of(ctx: Context) -> list[Pattern]:
    """The patterns of the part, found once per sheet drawing (an assembly has none: its holes are the components')."""
    cached = ctx.extras.get("patterns")
    if cached is None:
        cached = [] if ctx.assembly is not None else find(ctx.holes, feature_axes(ctx))
        ctx.extras["patterns"] = cached
    return cached


def lone_hole(ctx: Context, hole: HoleInfo, about: Vec3 | None = None) -> Pattern:
    """A single hole located by polar coordinates: a pattern of one, about ``about`` (a point of an axis) or, without it, about
    the axis of the pattern of the part, else the one axis of a feature along the same direction (a bore, a boss)."""
    direction = unit(hole.direction)
    if about is None:
        origins = [p.centre for p in patterns_of(ctx) if parallel(p.direction, direction)]
        if not origins:
            origins = [o for o, d in feature_axes(ctx) if parallel(d, direction) and not on_axis(hole.centre, direction, [(o, d)])]
        feet = {tuple(round(c, 1) for c in sub(o, scale(direction, dot(o, direction)))): o for o in origins}
        if len(feet) != 1:
            msg = (
                "a lone hole is dimensioned by polar coordinates about an axis, and the part has "
                + ("none" if not feet else "several")
                + " to count from: say about=[x, y, z] (a point on that axis)"
            )
            raise ItemError(msg)
        about = next(iter(feet.values()))
    off = sub(hole.centre, about)
    radial = sub(off, scale(direction, dot(off, direction)))
    radius = norm(radial)
    if radius < MIN_RADIUS_MM:
        msg = "the hole is on the axis it would be counted from: it has no radius"
        raise ItemError(msg)
    centre = sub(hole.centre, radial)
    stack = next(s for s in hole_stacks(ctx.holes) if any(h.index == hole.index for h in s))  # a counterbore is two holes on one axis
    return Pattern(centre, direction, radius, (tuple(h.index for h in stack),), (stack[0].index,), True, lone=True)


# --- in a view ---------------------------------------------------------------------------------------------------------


def view_angle(view: ViewInfo, centre: Vec3, point: Vec3) -> float:
    """The angle (degrees, 0 to 360, counter-clockwise from the page's x axis) of ``point`` about ``centre`` as ``view`` draws them."""
    c, p = view.project(centre), view.project(point)
    angle = math.degrees(math.atan2(p[1] - c[1], p[0] - c[0])) % 360.0
    return 0.0 if angle > 360.0 - CENTRE_LINE_TOL_DEG else angle


def offset_from_centre_line(angle: float) -> float:
    """The signed angle (-45 to 45 degrees) from the nearest centre line of the view (horizontal or vertical) to ``angle``."""
    return ((angle + 45.0) % 90.0) - 45.0


def on_centre_line(angle: float) -> bool:
    return abs(offset_from_centre_line(angle)) <= CENTRE_LINE_TOL_DEG


def first_hole(view: ViewInfo, pattern: Pattern, holes: dict[int, HoleInfo]) -> tuple[int, float]:
    """The hole whose angle from a centre line of the view is the smallest (the lowest page angle when several tie), and that
    angle: the one an angular dimension of the pattern is given for."""
    best: tuple[float, float, int] | None = None
    for index in pattern.reps:
        angle = view_angle(view, pattern.centre, holes[index].centre)
        key = (round(abs(offset_from_centre_line(angle)), 3), round(angle, 3), index)
        if best is None or key < best:
            best = key
    assert best is not None
    return best[2], best[1]


# --- the items that name a pattern ---------------------------------------------------------------------------------------


def _items(ctx: Context) -> list[dict[str, Any]]:
    return list(ctx.extras.get("items") or ctx.extras.get("sheet_items") or [])


def _hole_at(ctx: Context, at: Any) -> HoleInfo | None:
    return next((h for h in ctx.holes if h.on_axis(tuple(at))), None)  # type: ignore[arg-type]


def lone_patterns(ctx: Context) -> list[Pattern]:
    """The lone holes a ``pattern_dim`` item names that no circular pattern holds: each a pattern of one (the polar
    coordinates of a pin or a screw on a radius). A hole that cannot be counted from an axis is left to its item to say."""
    cached = ctx.extras.get("lone_patterns")
    if cached is None:
        cached = []
        taken = {i for p in patterns_of(ctx) for i in p.indexes}
        for spec in _items(ctx):
            if spec.get("type") != "pattern_dim" or not spec.get("at"):
                continue
            hole = _hole_at(ctx, spec["at"])
            if hole is None or hole.index in taken or any(hole.index in p.indexes for p in cached):
                continue
            try:
                cached.append(lone_hole(ctx, hole, tuple(spec["about"]) if spec.get("about") else None))  # type: ignore[arg-type]
            except ItemError:
                continue
        ctx.extras["lone_patterns"] = cached
    return cached


def pattern_of(ctx: Context, hole: HoleInfo) -> Pattern | None:
    for pattern in [*patterns_of(ctx), *lone_patterns(ctx)]:
        if hole.index in pattern.indexes:
            return pattern
    return None


def pattern_for_item(ctx: Context, spec: dict[str, Any]) -> Pattern:
    """The pattern a ``pattern_dim`` item stands for: the one that holds the hole whose axis passes through ``at``."""
    if not spec.get("at"):
        msg = "pattern_dim needs at: a point on the axis of one hole of the pattern"
        raise ItemError(msg)
    hole = _hole_at(ctx, spec["at"])
    if hole is None:
        msg = f"no hole has its axis through {list(spec['at'])} (inspect(action=holes) lists them)"
        raise ItemError(msg)
    found = pattern_of(ctx, hole)
    if found is None:
        lone_hole(ctx, hole, tuple(spec["about"]) if spec.get("about") else None)  # raises the reason
        msg = "the hole is on no circular pattern"
        raise ItemError(msg)
    return found


def holes_of_item(ctx: Context, spec: dict[str, Any]) -> frozenset[int]:
    """The holes a ``pattern_dim`` item locates (none when it names no hole of the part)."""
    if not spec.get("at"):
        return frozenset()
    hole = _hole_at(ctx, spec["at"])
    found = pattern_of(ctx, hole) if hole is not None else None
    return found.indexes if found is not None else frozenset()
