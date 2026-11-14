# ruff: noqa: E501
"""``hole_dim``: the callout of a hole -- diameter, depth or "through", and the thread when a ``hole`` feature declared
one -- with a leader on the view that shows the hole as a circle (no FreeCAD import).

Equal holes (same diameter, depth, through-ness and thread) share ONE callout with their count ("4x Ø8 pasante"); a
counterbore (a blind larger cylinder on the same axis as the through one) is one callout of two lines. Everything
printed comes from the hole as measured, never from the item. A hole no ``hole`` feature made (imported, or made by a
pattern) is described by its geometry only: the item says "rosca no declarada" in its result instead of guessing.
"""

import dataclasses
import math
import re
from typing import Any

import sheet_callout
import sheet_section
import sheet_thread
import sheet_tol
from items_common import asked, leader_at, mark_once, outward_diagonal, place_callout, place_leader, view_looking_along
from sheet_context import (
    COMMON_FIELDS,
    SECTION_LABEL,
    Context,
    HoleInfo,
    ItemError,
    ItemType,
    Point,
    check_fields,
    hole_stacks,
    v_bool,
    v_ids,
    v_points3,
    v_str,
    v_view_or_section,
)
from sheet_draw import centre_mark
from sheet_model import AXES, THREAD, Arc, Circle
from sheet_units import NativeDim, Ref2


def v_leader(value: Any) -> list[Any]:
    """The leader of a callout the global layout placed: its tip and its elbow, ``[tip x, tip y, elbow x, elbow y]`` (page mm),
    or a list of them when the callout has a leader for each group of holes."""
    if isinstance(value, list | tuple) and value and all(isinstance(v, list | tuple) for v in value):
        return [_leader_of(v) for v in value]
    return _leader_of(value)


def _leader_of(value: Any) -> list[float]:
    if (
        not isinstance(value, list | tuple)
        or len(value) != 4
        or any(isinstance(c, bool) or not isinstance(c, int | float) or not math.isfinite(c) for c in value)
    ):
        msg = "must be [tip x, tip y, elbow x, elbow y] in page mm"
        raise ItemError(msg)
    return [float(c) for c in value]


CHECKS = {
    "at": v_points3,
    "face": v_ids("face"),
    "all": v_bool,
    "centre_marks": v_bool,
    "dowel": v_bool,
    "text": v_str(120),
    "thread": v_str(24),
    "leader_mm": v_leader,  # where the global layout ended the leader: written by it, not asked for
    **sheet_tol.CHECKS,
    **COMMON_FIELDS,
    "view": v_view_or_section,
}
DEPTH_EPS_MM = 0.05
TIP_TOLERANCE_MM = 0.8  # a planned tip is on the circle of the hole it leaves
DEPTH = "↧"  # ISO 7200 / ASME Y14.5 depth symbol (osifont, TechDraw's own font, has it)
COUNTERBORE = "⌴"  # counterbore / spotface symbol (the countersink one is ⌵)
DOWEL_WORDS = ("registro", "dowel")  # registration (dowel) hole: ends its callout in this word (never a GD&T symbol: ISO 1101
# symbols stand only inside a feature control frame, and the concentricity one would be read as a tolerance), and a ring
# is drawn round the hole in the view
DOWEL_RING_GAP_MM = 1.6  # the ring is this far outside the hole's circle
DOWEL_MAX_DIAMETER_MM = 25.0  # a hole-basis fit on a larger hole is a seat (a bearing, a spigot), not a dowel
DOWEL_MAX_GRADE = 8


def check(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, CHECKS)
    chosen = [key for key in ("at", "face") if key in out] + (["all"] if out.get("all") else [])
    if len(chosen) != 1:
        msg = (
            "hole_dim needs exactly one of: at (a point on the axis of each hole, or a list of them), face (the "
            "face id "
            "of each hole, as inspect(action=holes) lists) or all=true"
        )
        raise ItemError(msg)
    sheet_tol.check_combination(out)
    if "text" in out and ("fit" in out or "tol" in out):
        msg = (
            "text is the whole callout as the engineer writes it: put the fit or tolerance inside it, not in fit / tol"
        )
        raise ItemError(msg)
    return out


# --- which holes -----------------------------------------------------------------------------------------------------


def _select(ctx: Context, spec: dict[str, Any]) -> list[HoleInfo]:
    if not ctx.holes:
        msg = "the part has no holes (inspect(action=holes) lists them)"
        raise ItemError(msg)
    if spec.get("all"):
        return list(ctx.holes)
    chosen: list[HoleInfo] = []
    if "at" in spec:
        for point in spec["at"]:
            hits = [h for h in ctx.holes if h.on_axis(point)]
            if not hits:
                nearest = ", ".join(
                    f"[{ctx.num(h.centre[0])}, {ctx.num(h.centre[1])}, {ctx.num(h.centre[2])}]" for h in ctx.holes[:4]
                )
                msg = f"no hole has its axis through {list(point)} (0.05 mm); holes open at {nearest}..."
                raise ItemError(msg)
            chosen += [h for h in hits if h not in chosen]
    else:
        for face_id in spec["face"]:
            hits = [h for h in ctx.holes if face_id in h.faces]
            if not hits:
                msg = f"{face_id} is not the wall of a hole (inspect(action=holes) lists the hole faces)"
                raise ItemError(msg)
            chosen += [h for h in hits if h not in chosen]
    return chosen


_stacks = hole_stacks  # the name the other items and the review import


# --- the text ---------------------------------------------------------------------------------------------------------


def describe(ctx: Context, hole: HoleInfo, diameter_text: str | None = None) -> list[str]:
    """The callout lines of one hole; ``diameter_text`` replaces ``Ø8`` (a toleranced one: ``Ø8 H7 (+0,015/0)``)."""
    depth = ctx.num(hole.depth)
    if hole.thread is not None:
        size, thread_depth = hole.thread
        if ctx.comma:
            size = size.replace(".", ",")  # M45x1,5 on a Spanish sheet
        if hole.through:
            return [f"{size} {ctx.say('pasante', 'thru')}"]
        shown = thread_depth if thread_depth is not None else hole.depth
        lines = [f"{size} {DEPTH} {ctx.num(shown)}"]
        if thread_depth is not None and thread_depth < hole.depth - DEPTH_EPS_MM:
            drill = ctx.say("taladro", "drill")
            lines.append(f"{drill} Ø{ctx.num(hole.diameter)} {DEPTH} {depth}")
        return lines
    d = diameter_text or f"Ø{ctx.num(hole.diameter)}"
    if hole.through:
        return [f"{d} {ctx.say('pasante', 'thru')}"]
    return [f"{d} {DEPTH} {depth}"]


def stack_parts(ctx: Context, stack: list[HoleInfo], diameter_text: str | None = None) -> list[list[str]]:
    """The callout lines of each hole on one axis (the larger ones, a counterbore, say so)."""
    parts: list[list[str]] = []
    previous: HoleInfo | None = None
    for hole in stack:
        part = describe(ctx, hole, diameter_text if hole is stack[0] else None)
        if previous is not None and hole.through is False and hole.diameter > previous.diameter:
            part[0] = f"{COUNTERBORE} {part[0]}"
        parts.append(part)
        previous = hole
    return parts


def stack_lines(ctx: Context, stack: list[HoleInfo], count: int, diameter_text: str | None = None) -> list[str]:
    """The callout of the holes on one axis; a tolerance (``diameter_text``) goes on the smallest, the one that is the
    hole (the larger ones are its counterbore)."""
    lines = [line for part in stack_parts(ctx, stack, diameter_text) for line in part]
    if count > 1:
        lines[0] = f"{count}× {lines[0]}"
    return lines


def _signature(stack: list[HoleInfo]) -> tuple[Any, ...]:
    return tuple((round(h.diameter, 3), round(h.depth, 2), h.through, h.thread, h.fit) for h in stack)


# --- drawing ----------------------------------------------------------------------------------------------------------


def _is_dowel(spec: dict[str, Any], stack: list[HoleInfo]) -> bool:
    """Whether the callout is of registration (dowel) holes: only when the item says so (``dowel``): the function of a hole
    is the engineer's, not something the sheet can read off a fit (a bushing seat has an H7 too)."""
    del stack
    return bool(spec.get("dowel"))


def looks_like_dowel(spec: dict[str, Any], stack: list[HoleInfo]) -> bool:
    """A plain through hole of at most 25 mm with a hole-basis fit of grade 8 or finer (H6, H7...) and no ``dowel`` choice:
    what a pin is pressed or reamed into, so the review asks whether it is one."""
    fit = spec.get("fit")
    if "dowel" in spec or fit is None or len(stack) != 1 or stack[0].thread is not None or stack[0].through is False:
        return False
    found = re.fullmatch(r"H(\d+)(?:/.*)?", str(fit))
    return bool(found) and int(found.group(1)) <= DOWEL_MAX_GRADE and stack[0].diameter <= DOWEL_MAX_DIAMETER_MM


def _native_callout(
    ctx: Context, view: str, scale: float, centre: tuple[float, float], diameter: float, lines: list[str]
) -> None:
    """The callout just placed (``place_leader`` kept where) as a TechDraw dimension of the hole's outer circle: FreeCAD
    measures it, the text is the whole callout, and it sits where the shelf text sits. It is a RADIUS dimension on purpose
    (the text is arbitrary): TechDraw draws a diameter one through the circle with an arrow on each side, and the leader
    of a callout (ISO 128-22, ISO 129-1) ends with ONE arrow on the circle, its line pointing at the centre."""
    ctx.used_view(view)
    anchor = ctx.extras["anchors"][ctx.extras["current"]][-1]
    x0, y0, x1, y1 = anchor["rect"]
    ctx.native(
        NativeDim(
            item=str(ctx.extras["current"]),
            name=f"c{len(ctx.extras.get('natives', {}).get(ctx.extras['current'], [])) + 1}",
            kind="radius",
            view=view,
            refs=(Ref2("circle", (centre[0], centre[1], diameter / 2 * scale)),),
            label=((x0 + x1) / 2, (y0 + y1) / 2),
            expected=diameter / 2,
            text="\n".join(lines),
            leader=True,
            rect=anchor["rect"],
        )
    )


def _section_target(ctx: Context, spec: dict[str, Any]) -> Any:
    """The section view the item names (``view="A-A"``), ``None`` for a standard view or none."""
    name = spec.get("view")
    return ctx.view(name) if isinstance(name, str) and SECTION_LABEL.match(name) else None


def _in_place_view(ctx: Context, hole: HoleInfo, wanted: Any) -> Any:
    """The section standing in the main view (``section in_place``) when its plane runs along the axis of ``hole``: a bore
    that no view sees as a circle is called out there, across it. ``wanted`` is the view the item named, if any."""
    for label, parent in ctx.extras.get("in_place_views", {}).items():
        if wanted is not None and wanted not in (label, parent):
            continue
        at, normal = ctx.extras["section_planes"][label]
        if any(h.index == hole.index for h in sheet_section.holes_cut(ctx, at, normal)):
            return ctx.extras["sections"][label]
    return None


def _declared_thread(ctx: Context, spec: dict[str, Any], stack: list[HoleInfo]) -> list[HoleInfo]:
    """The stack with the thread the item says the bore has (``thread="M45x1.5"``), for a bore no ``hole`` operation made
    (a revolved bore, an imported one). The geometry has the last word: the size must be in the ISO table and its tap drill
    must be the diameter the bore measures, else the item is refused and says what the bore is."""
    if "thread" not in spec:
        return stack
    wanted = sheet_thread.thread_table(spec["thread"])
    hole = stack[0]
    if wanted is None:
        msg = f"thread {spec['thread']!r} is not in the ISO 261 thread table (M45x1.5, M12, ...)"
        raise ItemError(msg)
    designation, tap_drill = wanted
    if hole.thread is not None:
        made = sheet_thread.thread_table(hole.thread[0])
        if made is not None and made[0] != designation:
            msg = f"the hole was made as {made[0]}: thread={spec['thread']!r} contradicts the model (drop thread=)"
            raise ItemError(msg)
        return stack
    if abs(hole.diameter - tap_drill) > sheet_thread.TAP_DRILL_TOL_MM:
        msg = (
            f"{designation} is drilled at Ø{ctx.num(tap_drill)} and this bore measures Ø{ctx.num(hole.diameter)}: it is "
            "not a bore for that thread (change the model, or the thread)"
        )
        raise ItemError(msg)
    return [dataclasses.replace(hole, thread=(designation, None), declared=True), *stack[1:]]


def _resolve_tolerance(spec: dict[str, Any], stack: list[HoleInfo]) -> Any:
    """The tolerance of the callout: the item's own ``fit`` / ``tol``, else the fit the ``hole`` operation declared for the
    hole (``hole(fit="H7")``) as the default. A default that the tables cannot give (a size they do not cover) is dropped,
    never an error of an item that asked for nothing."""
    if "fit" in spec or "tol" in spec:
        return sheet_tol.resolve(spec, stack[0].diameter)
    declared = stack[0].fit
    if declared is None or "text" in spec or stack[0].thread is not None:
        return None
    try:
        return sheet_tol.resolve({**spec, "fit": declared}, stack[0].diameter)
    except (ItemError, ValueError):
        return None


def _thread_arc(view: Any, hole: HoleInfo) -> list[Any]:
    """The ISO 6410 thin arc of a thread the item declared, in a view along the bore (``thread_entities`` draws it for
    the holes a ``hole`` operation made)."""
    major = sheet_thread.major_diameter(hole.thread[0]) if hole.thread else None
    if major is None or major <= hole.diameter or not view.looks_along(hole.direction):
        return []
    cx, cy = view.project(hole.centre)
    return [Arc(cx, cy, major / 2 * view.scale, sheet_thread.OPEN_FROM_DEG, sheet_thread.OPEN_TO_DEG, THREAD)]


def _planned_leader(spec: dict[str, Any], group: list[Any], radius: float) -> tuple[Point, Point, Point] | None:
    """The ``leader_mm`` of the item as (tip, elbow, centre of the hole the tip is on), or ``None`` when the item has none or
    no hole of this group has the tip on its circle (the views moved since: the leader is searched again)."""
    planned = spec.get("leader_mm")
    if planned is None:
        return None
    for vector in planned if isinstance(planned[0], list | tuple) else [planned]:
        tip, elbow = (vector[0], vector[1]), (vector[2], vector[3])
        for _stack, centre, _view in group:
            if abs(math.dist(centre, tip) - radius) <= TIP_TOLERANCE_MM:
                return tip, elbow, centre
    return None


def draw(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    stacks = [_declared_thread(ctx, spec, s) for s in _stacks(_select(ctx, spec))]
    target = _section_target(ctx, spec)
    cut: set[int] = set()
    if target is not None:
        at, normal = ctx.extras["section_planes"][spec["view"]]
        cut = {h.index for h in sheet_section.holes_cut(ctx, at, normal)}
    placed: dict[tuple[str, tuple[Any, ...]], list[tuple[list[HoleInfo], tuple[float, float], Any]]] = {}
    skipped: list[str] = []
    for stack in stacks:
        if target is not None:
            if stack[0].index not in cut:
                skipped.append(
                    f"hole {ctx.num(stack[0].diameter)} at [{', '.join(ctx.num(c) for c in stack[0].centre)}]: the plane of "
                    f"{target.name} does not run along its axis"
                )
                continue
            view = target
            key: tuple[Any, ...] = ("bore", stack[0].index)  # a bore is shown once: no shared callout
        else:
            view = view_looking_along(ctx, stack[0].direction, spec.get("view"))
            key = _signature(stack)
            if view is None:  # no view sees it as a circle: the section that stands in the main view cuts it lengthwise
                view = _in_place_view(ctx, stack[0], spec.get("view"))
                key = ("bore", stack[0].index)
        if view is None:
            where = f"the {spec['view']} view" if "view" in spec else "front, top or right"
            skipped.append(
                f"hole {ctx.num(stack[0].diameter)} at [{', '.join(ctx.num(c) for c in stack[0].centre)}]: its axis is "
                f"not along the direction of {where}"
            )
            continue
        centre = view.project(stack[0].centre)
        placed.setdefault((view.name, key), []).append((stack, centre, view))
    if not placed:
        raise ItemError("; ".join(skipped))
    if "text" in spec and len(placed) != 1:
        msg = "text is the callout of ONE hole (or one group of equal holes): choose it with at or face"
        raise ItemError(msg)
    summary: list[str] = []
    taps: dict[str, int] = {}
    general: dict[str, float] = {}
    contradictions: list[str] = []
    for (view_name, _), group in placed.items():
        view = group[0][2]
        stack, centre, _ = group[0]
        diameter_text = None
        resolved = _resolve_tolerance(spec, stack)
        if resolved is not None:
            if stack[0].thread is not None:
                msg = "a threaded hole takes no fit or tolerance on its diameter: the thread class is part of its size"
                raise ItemError(msg)
            diameter_text = sheet_tol.render(ctx, stack[0].diameter, resolved, prefix="Ø").text
        elif stack[0].thread is None and "text" not in spec:
            general.setdefault(f"Ø{ctx.num(stack[0].diameter)}", stack[0].diameter)
        lines = stack_lines(ctx, stack, len(group), diameter_text)
        if "text" in spec:  # the engineer's wording; what it states is checked against the measured hole
            lines = [part.strip() for part in spec["text"].split("\n") if part.strip()]
            contradictions += sheet_callout.problems(ctx, spec["text"], stack, len(group))
        dowel = _is_dowel(spec, stack)
        if dowel:
            lines[-1] = f"{lines[-1]}, {ctx.say(*DOWEL_WORDS)}"
        if view.derived:  # the bore cut lengthwise: the diameter dimension across it, the thread lines, nothing else
            if len(stack) > 1 and view.name in ctx.extras.get("in_place_views", {}) and "text" not in spec:
                # the section is the main view: each step of the bore has its own Ø (the counterbore says its depth)
                for hole, part in zip(stack, stack_parts(ctx, stack, diameter_text), strict=True):
                    sheet_section.dimension_bore(ctx, view, [hole], " ".join(part), spec.get("side"), spec.get("offset_mm"))
            else:
                sheet_section.dimension_bore(ctx, view, stack, " ".join(lines), spec.get("side"), spec.get("offset_mm"))
            if "thread" in spec and stack[0].thread is not None:
                ctx.add(sheet_section.thread_lines(view, stack[0], stack[0].thread[0], stack[0].thread[1]))
            summary.append(f"{view_name}: {' / '.join(lines)}")
            continue
        radius = stack[-1].diameter / 2 * view.scale
        planned = _planned_leader(spec, group, radius)
        if planned is not None:  # the global layout ended the leader here: from the hole whose circle holds its tip
            tip, elbow, centre = planned
            ctx.add(leader_at(ctx, tip, elbow, lines, inside_ok=True))
        elif asked(spec):  # the engineer says where: the leader goes exactly there, from the hole that sticks out most
            diagonal = outward_diagonal(view, group[0][1], spec.get("side"))
            _, centre, _ = max(
                ((s, c, v) for s, c, v in group), key=lambda g: diagonal[0] * g[1][0] + diagonal[1] * g[1][1]
            )
            tip = (centre[0] + diagonal[0] * radius * 0.7071, centre[1] + diagonal[1] * radius * 0.7071)
            ctx.add(place_leader(ctx, view, tip, diagonal, lines, spec.get("offset_mm"), True))
        else:  # the shortest clean leader: no crossing, one cut of the outline, its text outside the part
            entities, chosen = place_callout(
                ctx, view, [(c, member[-1].diameter / 2 * view.scale) for member, c, _ in group], lines, None
            )
            ctx.add(entities)
            centre = group[chosen][1]
        _native_callout(ctx, view.name, view.scale, centre, stack[-1].diameter, lines)
        if "thread" in spec:
            for member, _, _ in group:
                ctx.add(_thread_arc(view, member[0]))
        if spec.get("centre_marks", True):
            for member, c, _ in group:
                if mark_once(ctx, view, c):
                    ctx.add(centre_mark(c[0], c[1], member[-1].diameter / 2 * view.scale))
        if dowel:  # a ring round each registration hole: they are found at a glance
            ctx.add(
                [
                    Circle(c[0], c[1], member[-1].diameter / 2 * view.scale + DOWEL_RING_GAP_MM, AXES)
                    for member, c, _ in group
                ]
            )
        summary.append(f"{view_name}: {' / '.join(lines)}")
        for s, _, _ in group:
            for h in s[:1]:  # the larger holes of a stack are its counterbores, never a tap drill
                size = None if h.declared else sheet_thread.tap_drill_thread(h.diameter)
                if size is not None:
                    taps[size] = taps.get(size, 0) + 1
    notes = list(skipped)
    if contradictions:
        notes += [
            ctx.say("el texto contradice el modelo: ", "the text contradicts the model: ") + "; ".join(contradictions)
        ]
    text = "; ".join(summary + notes) + (sheet_tol.general_notes(ctx, "linear", general) if general else "")
    if (
        taps
    ):  # only a hole of a tap-drill diameter that no hole operation made may be a threaded one: an info, not a warning
        shown = ", ".join(
            f"{n} x Ø{ctx.num(sheet_thread.TAP_DRILLS[size])} = {size}" for size, n in sorted(taps.items())
        )
        text += ctx.say(
            f" (rosca no declarada: {shown} tiene el diámetro del taladro de rosca y no lo hizo la operación hole; si va roscado, dilo en una nota)",
            f" (thread not declared: {shown} has the tap-drill diameter and no hole operation made it; if it is threaded, say so in a note)",
        )
    return ("warning" if notes else "ok"), text


ITEMS = (ItemType("hole_dim", "h", check, draw, frozenset(CHECKS), phase=1),)
