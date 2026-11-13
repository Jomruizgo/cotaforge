# ruff: noqa: E501
"""The interface of an assembly on its sheet: the holes the parts are fastened through (no FreeCAD import).

An assembly sheet gives the overall sizes and what the mounting needs: each through hole's diameter, the centre distance
of the holes that form a row, and where the first one is, counted from an edge of the plate that carries it (or from its
plane of symmetry only when symmetry is declared -- an assembly says none, so the edge). The holes come from the model
(``Geometry.assembly_holes``); the dimensions are drawn on the view that sees them as circles, and what was drawn is
recorded in ``ctx.extras["interface"]`` so the review can tell a hole that is located by a dimension ON THE SHEET from one
that only exists in the model.
"""

import math
from dataclasses import dataclass

from items_common import outward_diagonal, place_leader
from sheet_context import AssemblyHole, Context, Point, ViewInfo, dot, parallel, sub, unit
from sheet_draw import linear_dimension
from sheet_units import NativeDim, Ref2

AXIS_TOL_MM = 0.05
SAME_MM = 0.05
MAX_ROW = 12  # a row of more holes than this is a pattern: its own dimensions, not a chain of this many


@dataclass
class HoleStack:
    """The holes of the parts that share one axis (a plate, a spacer and a plate threaded on the same bolt)."""

    holes: list[AssemblyHole]

    @property
    def centre(self) -> tuple[float, float, float]:
        return self.holes[0].centre

    @property
    def direction(self) -> tuple[float, float, float]:
        return self.holes[0].direction

    @property
    def key(self) -> str:
        return "@" + ",".join(f"{c:g}" for c in (round(v, 2) for v in self.centre))


def stacks_of(holes: list[AssemblyHole]) -> list[HoleStack]:
    """The holes grouped by axis: parallel and on one line."""
    out: list[HoleStack] = []
    for hole in holes:
        for stack in out:
            first = stack.holes[0]
            if not parallel(first.direction, hole.direction):
                continue
            axis = unit(first.direction)
            offset = sub(hole.centre, first.centre)
            along = dot(offset, axis)
            radial = math.dist(offset, tuple(a * along for a in axis))
            if radial <= AXIS_TOL_MM:
                stack.holes.append(hole)
                break
        else:
            out.append(HoleStack([hole]))
    return out


def _plate_rect(ctx: Context, view: ViewInfo, stacks: list[HoleStack]) -> tuple[float, float, float, float] | None:
    """The outline in ``view`` (page mm) of the largest part that carries a hole: the plate the positions are counted from."""
    best: tuple[float, tuple[float, float, float, float]] | None = None
    for cid in {h.comp_id for s in stacks for h in s.holes}:
        low, high = ctx.geometry.instance_box(cid)
        points = [view.project((x, y, z)) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]
        rect = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if best is None or area > best[0]:
            best = (area, rect)
    return best[1] if best else None


def _unique(values: list[float]) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > SAME_MM:
            out.append(v)
    return out


def _native(
    ctx: Context, view: ViewInfo, axis: str, a: Point, b: Point, orient: str, line: float, value: float, taken: tuple[float, float, float, float]
) -> None:
    """The dimension just drawn as a TechDraw dimension of the view (FreeCAD measures it, the sheet shows it): the window
    and the exports show native objects, not the sheet's own lines."""
    item = str(ctx.extras["current"])
    count = len(ctx.extras.get("natives", {}).get(item, [])) + 1
    label = ((a[0] + b[0]) / 2, line) if orient == "h" else (line, (a[1] + b[1]) / 2)
    ctx.native(
        NativeDim(
            item=item,
            name=f"{axis}{count}",
            kind="distance",
            view=view.name,
            refs=(Ref2("point", a), Ref2("point", b)),
            label=label,
            expected=value,
            orient=orient,
            rect=taken,
        )
    )


def callout_lines(ctx: Context, stacks: list[HoleStack]) -> list[str]:
    """``2× Ø9 (3, 2)``: each distinct diameter with how many axes have it and the positions of the parts it is in."""
    by_size: dict[float, tuple[set[str], set[int]]] = {}
    for stack in stacks:
        seen: set[float] = set()
        for h in stack.holes:
            size = round(h.diameter, 2)
            axes, positions = by_size.setdefault(size, (set(), set()))
            if size not in seen:
                axes.add(stack.key)
                seen.add(size)
            position = ctx.assembly.position_of(h.comp_id) if ctx.assembly is not None else None
            if position is not None:
                positions.add(position)
    lines = []
    for size in sorted(by_size, reverse=True):
        axes, positions = by_size[size]
        count = f"{len(axes)}× " if len(axes) > 1 else ""
        where = f" ({', '.join(str(p) for p in sorted(positions))})" if positions else ""
        lines.append(f"{count}Ø{ctx.num(size)}{where}")
    return lines


def draw(ctx: Context, view: ViewInfo) -> list[str]:
    """Draw the diameter, the centre distances and the distance from the plate's edges of the through holes that ``view``
    sees as circles. Returns what was drawn, for the item's result."""
    holes = [h for h in ctx.geometry.assembly_holes() if view.looks_along(h.direction)]
    stacks = stacks_of(holes)
    record = ctx.extras.setdefault("interface", {"stacks": {}})
    if not stacks:
        return []
    rect = _plate_rect(ctx, view, stacks)
    if rect is None:
        return []
    centres = {s.key: view.project(s.centre) for s in stacks}
    xs = _unique([p[0] for p in centres.values()])
    ys = _unique([p[1] for p in centres.values()])
    done: list[str] = []
    scale = view.scale
    placed_x: dict[float, bool] = {}
    placed_y: dict[float, bool] = {}
    if len(xs) <= MAX_ROW:
        high = max(p[1] for p in centres.values())
        dist = ctx.stack_distance(view.name, "above", None)  # the overall width goes below when its lines would cross a gap
        line = rect[3] + dist
        chain = [rect[0], *xs]
        for a, b in zip(chain, chain[1:], strict=False):
            pa = (a, rect[3] if a == rect[0] else high)
            pb = (b, high)
            text = ctx.num(abs(b - a) / scale)
            entities, taken = linear_dimension(pa, pb, "h", line, text)
            ctx.add(entities)
            ctx.reserve_dimension(entities, taken)
            ctx.record_dimension(("hole_x", round(a, 2), round(b, 2)), view.name)
            _native(ctx, view, "x", pa, pb, "h", line, abs(b - a) / scale, taken)
            placed_x[b] = True
        done.append("hole spacing and position along x: " + ", ".join(ctx.num((b - a) / scale) for a, b in zip(chain, chain[1:], strict=False)))
    if len(ys) <= MAX_ROW:
        left = min(p[0] for p in centres.values())
        dist = ctx.stack_distance(view.name, "left", None)
        line = rect[0] - dist
        chain = [rect[1], *ys]
        for a, b in zip(chain, chain[1:], strict=False):
            pa = (rect[0] if a == rect[1] else left, a)
            pb = (left, b)
            text = ctx.num(abs(b - a) / scale)
            entities, taken = linear_dimension(pa, pb, "v", line, text)
            ctx.add(entities)
            ctx.reserve_dimension(entities, taken)
            ctx.record_dimension(("hole_y", round(a, 2), round(b, 2)), view.name)
            _native(ctx, view, "y", pa, pb, "v", line, abs(b - a) / scale, taken)
            placed_y[b] = True
        done.append("hole position along y: " + ", ".join(ctx.num((b - a) / scale) for a, b in zip(chain, chain[1:], strict=False)))
    lines = callout_lines(ctx, stacks)
    first = max(stacks, key=lambda s: max(h.diameter for h in s.holes))
    cx, cy = centres[first.key]
    radius = max(h.diameter for h in first.holes) / 2 * scale
    diagonal = outward_diagonal(view, (cx, cy), None)
    tip = (cx + diagonal[0] * radius * math.sqrt(0.5), cy + diagonal[1] * radius * math.sqrt(0.5))
    ctx.add(place_leader(ctx, view, tip, diagonal, lines, None))
    anchor = ctx.extras.get("anchors", {}).get(str(ctx.extras.get("current")), [])
    if anchor:  # the callout as TechDraw's own diameter dimension of the circle, like a hole callout
        x0, y0, x1, y1 = anchor[-1]["rect"]
        ctx.native(
            NativeDim(
                item=str(ctx.extras["current"]),
                name="c1",
                kind="diameter",
                view=view.name,
                refs=(Ref2("circle", (cx, cy, radius)),),
                label=((x0 + x1) / 2, (y0 + y1) / 2),
                expected=max(h.diameter for h in first.holes),
                text="\n".join(lines),
                leader=True,
                rect=anchor[-1]["rect"],
            )
        )
    done.append("hole diameters " + "; ".join(lines))
    for stack in stacks:
        cx, cy = centres[stack.key]
        record["stacks"][stack.key] = {
            "x": any(abs(cx - x) <= SAME_MM for x in placed_x),
            "y": any(abs(cy - y) <= SAME_MM for y in placed_y),
            "diameter": True,
            "label": f"Ø{ctx.num(max(h.diameter for h in stack.holes))}",
        }
    ctx.used_view(view.name)
    return done


def unlocated(ctx: Context) -> list[tuple[str, str, str]]:
    """``(key, label, missing)`` of each through-hole axis of the assembly with no dimension on the sheet for its position
    along x or y (or at all). ``missing`` is ``x``, ``y`` or ``xy``."""
    if ctx.assembly is None:
        return []
    try:
        holes = ctx.geometry.assembly_holes()
    except Exception:  # noqa: BLE001 - a geometry that cannot list its holes has none to check
        return []
    drawn = ctx.extras.get("interface", {}).get("stacks", {})
    out = []
    for stack in stacks_of(holes):
        info = drawn.get(stack.key)
        missing = "".join(axis for axis in ("x", "y") if not (info and info[axis]))
        if missing:
            label = f"Ø{ctx.num(max(h.diameter for h in stack.holes))} {stack.key}"
            out.append((stack.key, label, missing))
    return out


__all__ = ["HoleStack", "Point", "callout_lines", "draw", "stacks_of", "unlocated"]
