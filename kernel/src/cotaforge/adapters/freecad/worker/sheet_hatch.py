# ruff: noqa: E501
"""The hatching of a section with several bodies (no FreeCAD import, adapter-neutral).

In a section each body is told from its neighbours at a glance by the ANGLE of its hatch and/or its SPACING, never by its
PATTERN: the pattern identifies the material (the same material has the same pattern on every body). This module holds
what both adapters share: the styles, the plan that gives touching bodies different ones, which bodies touch in a cut, and
the review of the result.

A style is ``(turn, spacing)``: the turn in degrees from the standard hatch (0: as it is, 90: perpendicular, so 45 degrees
becomes 135) and the spacing as a factor of the adapter's usual spacing (1: as it is).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

Style = tuple[float, float]
Point = tuple[float, float]

STYLES: tuple[Style, ...] = (
    (0.0, 1.0),
    (90.0, 1.0),
    (0.0, 2.0),
    (90.0, 2.0),
)  # ISO 128-50: direction first, then spacing
TOUCH_MM = 0.3  # two cut areas whose outlines are this near are neighbours


def plan(
    bodies: Sequence[str], touching: Sequence[tuple[str, str]], asked: Mapping[str, Style] | None = None
) -> dict[str, Style]:
    """The style of each body: the one that was asked for, else the first of ``STYLES`` that no body touching it already
    has (two bodies that meet in the cut never share angle and spacing, so the line between them shows)."""
    chosen: dict[str, Style] = {name: style for name, style in (asked or {}).items() if name in bodies}
    for name in bodies:
        if name in chosen:
            continue
        taken = {chosen[o] for a, b in touching for o in (a, b) if name in (a, b) and o != name and o in chosen}
        chosen[name] = next((s for s in STYLES if s not in taken), STYLES[0])
    return chosen


def _near(p: Point, loop: Sequence[Point]) -> bool:
    for i, a in enumerate(loop):
        b = loop[(i + 1) % len(loop)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
        if math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy)) <= TOUCH_MM:
            return True
    return False


def touching_bodies(groups: Mapping[str, Sequence[Sequence[Point]]]) -> list[tuple[str, str]]:
    """The pairs of bodies whose cut areas meet: a vertex of the outline of one lies on the outline of the other."""
    names = list(groups)
    pairs = []
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            if any(_near(p, other) for loop in groups[first] for p in loop for other in groups[second]) or any(
                _near(p, other) for loop in groups[second] for p in loop for other in groups[first]
            ):
                pairs.append((first, second))
    return pairs


@dataclass(frozen=True)
class HatchFact:
    """The hatch one body has in a section: ``material`` (empty: not known), ``pattern`` (the pattern's name), ``turn`` and
    ``spacing`` as in ``Style``."""

    body: str
    material: str = ""
    pattern: str = ""
    turn: float = 0.0
    spacing: float = 1.0


@dataclass(frozen=True)
class HatchFinding:
    code: str  # hatch_same_as_neighbour | hatch_pattern_differs
    bodies: tuple[str, str]
    propose_turn: float | None = None  # the other angle for the second body


def _same_turn(a: float, b: float) -> bool:
    return abs((a - b + 90.0) % 180.0 - 90.0) < 1.0  # a hatch turned 180 degrees is the same hatch


def hatch_findings(facts: Sequence[HatchFact], touching: Sequence[tuple[str, str]]) -> list[HatchFinding]:
    """Two adjacent cut bodies with the same pattern, angle and spacing; two bodies of the same material with different
    patterns."""
    by_body = {f.body: f for f in facts}
    out: list[HatchFinding] = []
    for a, b in touching:
        fa, fb = by_body.get(a), by_body.get(b)
        if fa is None or fb is None:
            continue
        if fa.pattern == fb.pattern and _same_turn(fa.turn, fb.turn) and abs(fa.spacing - fb.spacing) < 0.05:
            out.append(HatchFinding("hatch_same_as_neighbour", (a, b), (fb.turn + 90.0) % 180.0))
    names = list(by_body)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            fa, fb = by_body[a], by_body[b]
            if fa.material and fa.material == fb.material and fa.pattern != fb.pattern:
                out.append(HatchFinding("hatch_pattern_differs", (a, b)))
    return out
