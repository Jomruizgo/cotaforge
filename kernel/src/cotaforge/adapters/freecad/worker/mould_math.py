"""The maths of the parting line, the parting surface and the slides, without FreeCAD (unit-testable with plain pytest).

Convention (the same as ``draft_analysis``): ``pull`` is the direction the CORE (the moving half) opens along. A face of
the part whose outward normal looks along ``+pull`` is formed by the core; one that looks against it is formed by the
CAVITY (the fixed half, on the ``-pull`` side). A wall with no lean at all (``zero``) belongs to neither by itself: its
side comes from its neighbours (a wall between two core faces is the core's) or from the engineer's decision.
"""

import math
from collections import defaultdict

Vec = tuple[float, float, float]
Vec2 = tuple[float, float]

CORE = "core"
CAVITY = "cavity"
ZERO = "zero"
STRADDLE = "straddle"
SLIDE = "slide"

ZERO_LEAN = math.sin(math.radians(0.01))
"""The lean (``normal . pull``) under which a face has no draft: the sine of ``DRAFT_ZERO_DEG`` (0.01 degrees)."""

PLANAR_TOL = 1e-6
"""Relative to the size of the loop: a loop whose points are all this close to one plane lies in a plane."""


def side_of_leans(lowest: float, highest: float, zero_lean: float = ZERO_LEAN) -> str:
    """The side a face belongs to from the least and the greatest ``normal . pull`` over it: ``core`` (looks along the
    pull), ``cavity`` (looks against it), ``zero`` (no lean anywhere) or ``straddle`` (looks both ways: the silhouette
    runs across the face, not along an edge)."""
    if lowest >= -zero_lean and highest <= zero_lean:
        return ZERO
    if lowest >= -zero_lean:
        return CORE
    if highest <= zero_lean:
        return CAVITY
    return STRADDLE


def components(nodes: set[str], neighbours: dict[str, set[str]]) -> list[list[str]]:
    """The connected groups of ``nodes`` (adjacent through ``neighbours``), each sorted, the groups sorted by their
    first member: deterministic."""
    seen: set[str] = set()
    groups: list[list[str]] = []
    for start in sorted(nodes):
        if start in seen:
            continue
        group: list[str] = []
        stack = [start]
        seen.add(start)
        while stack:
            node = stack.pop()
            group.append(node)
            for other in sorted(neighbours.get(node, ())):
                if other in nodes and other not in seen:
                    seen.add(other)
                    stack.append(other)
        groups.append(sorted(group))
    return groups


def resolve_sides(
    raw: dict[str, str], neighbours: dict[str, set[str]], decision: str | None
) -> tuple[dict[str, str], list[dict[str, object]]]:
    """The final side of every face and the walls that need the engineer.

    ``raw`` is each face's ``side_of_leans`` (or ``slide`` for a face a slide forms). A group of connected ``zero``
    faces whose neighbours (the leaning faces next to the group) are all on ONE side takes that side: a wall between
    two core faces is the core's, nothing to decide. A group with neighbours on both sides (the outer wall of a shell:
    the rim on one end, the bottom on the other) has no side of its own: it takes ``decision`` (``core`` or ``cavity``)
    when one is given, else it stays ``zero`` and is reported as ambiguous. Returns ``(sides, ambiguous)``,
    ``ambiguous``
    being ``{"faces", "touches"}`` rows (``touches``: the sides of its neighbours)."""
    sides = dict(raw)
    ambiguous: list[dict[str, object]] = []
    for group in components({f for f, s in raw.items() if s == ZERO}, neighbours):
        members = set(group)
        touching = {
            raw[n] for f in group for n in neighbours.get(f, ()) if n not in members and raw.get(n) in (CORE, CAVITY)
        }
        if len(touching) == 1:
            chosen = next(iter(touching))
        elif decision in (CORE, CAVITY):
            chosen = decision
        else:
            ambiguous.append({"faces": group, "touches": sorted(touching)})
            continue
        for face in group:
            sides[face] = chosen
    return sides, ambiguous


def parting_edges(edge_faces: dict[str, tuple[str, str]], sides: dict[str, str]) -> list[str]:
    """The edges between a core face and a cavity face: the parting line. ``edge_faces`` maps an edge to the two faces
    that share it."""
    out = []
    for edge, (a, b) in sorted(edge_faces.items()):
        if {sides.get(a), sides.get(b)} == {CORE, CAVITY}:
            out.append(edge)
    return out


def chain_loops(ends: dict[str, tuple[str, str]]) -> list[dict[str, object]]:
    """Chain edges, given as ``edge -> (vertex a, vertex b)``, into loops. Each row is ``{"edges": [(edge, forward)],
    "closed": bool}``: ``forward`` is whether the edge is walked from its first vertex to its second. A vertex with
    more than two edges (two loops touching) is walked through arbitrarily; the row then says ``branched``."""
    at: dict[str, list[str]] = defaultdict(list)
    for edge, (a, b) in sorted(ends.items()):
        at[a].append(edge)
        if b != a:
            at[b].append(edge)
    branched_vertices = {v for v, edges in at.items() if len(edges) > 2}
    used: set[str] = set()
    loops: list[dict[str, object]] = []
    for first in sorted(ends):
        if first in used:
            continue
        used.add(first)
        walked = [(first, True)]
        start, vertex = ends[first]
        closed = False
        while True:
            if vertex == start:
                closed = True
                break
            following = [e for e in at[vertex] if e not in used]
            if not following:
                break
            edge = following[0]
            used.add(edge)
            a, b = ends[edge]
            forward = a == vertex
            walked.append((edge, forward))
            vertex = b if forward else a
        loops.append(
            {
                "edges": walked,
                "closed": closed,
                "branched": any(ends[e][0] in branched_vertices or ends[e][1] in branched_vertices for e, _ in walked),
            }
        )
    return loops


def signed_area(points: list[Vec2]) -> float:
    """The signed area of a polygon (positive when it runs counter-clockwise)."""
    total = 0.0
    for i, (x0, y0) in enumerate(points):
        x1, y1 = points[(i + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return total / 2.0


def outward_normal(direction: Vec2, counter_clockwise: bool) -> Vec2:
    """The unit normal, in the plane across the pull, that points out of a polygon at an edge running along
    ``direction`` (any length): to the right of a counter-clockwise polygon's edge, to the left of a clockwise one."""
    length = math.hypot(direction[0], direction[1])
    if length < 1e-12:
        msg = "an edge with no extent across the pull has no outward normal"
        raise ValueError(msg)
    dx, dy = direction[0] / length, direction[1] / length
    return (dy, -dx) if counter_clockwise else (-dy, dx)


def miter_offset(n1: Vec2, n2: Vec2, distance: float) -> Vec2:
    """Where the corner of two edges with outward unit normals ``n1`` and ``n2`` moves when both edges are pushed out
    by ``distance``: the intersection of the two pushed lines. Raises ``ValueError`` for an edge that folds back on
    itself (the normals nearly opposite), where the corner runs away."""
    denominator = 1.0 + n1[0] * n2[0] + n1[1] * n2[1]
    if denominator < 1e-3:
        msg = "two edges fold back on each other: their corner has no finite offset"
        raise ValueError(msg)
    scale = distance / denominator
    return ((n1[0] + n2[0]) * scale, (n1[1] + n2[1]) * scale)


def volume_balance(block: float, parts: list[float]) -> float:
    """The relative gap between the block and the sum of the pieces it was cut into (0 = they add up exactly)."""
    return abs(block - sum(parts)) / block if block > 0.0 else float("inf")


def proposed_margin(extent_mm: float) -> float:
    """The margin proposed on every side of the part when the engineer gives no block: a fixed 25 mm, or a fifth of the
    part's largest side if that is more. A first-pass rule of thumb for a steel insert (enough steel round the cavity
    for the cooling and the screws), NOT a standard: the mould base and the press size it for real."""
    return max(25.0, 0.2 * extent_mm)
