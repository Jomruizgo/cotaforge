"""The cut list of a weldment (no FreeCAD import, adapter-neutral): one part made of several welded bodies.

A weldment is a part document with two or more live bodies. What the harness KNOWS of each body is what it measures:
its volume, its surface, its faces and the sides of the smallest box around it. It does NOT know that a body is "Tubo
40x40x3" or "Pletina 50x6": nothing in the model says so, and a name is never made up. The designation is the designer's:
it is written in the rows of the parts list (``rows=[{"component": "body:2", "name": "Tubo 40x40x3"}]``) and an empty one
is a question to him (``weld_body_unnamed``).

Bodies that are the same piece (same volume, same box, same faces and surface: so the same profile, length and end cuts)
share one row with their quantity.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from sheet_context import Assembly, PartGroup
from sheet_model import fmt

PROFILE_RATIO = 4.0  # longest side over the next one: from here the body is bar stock, listed by its length
ROUND_MM = 0.1


@dataclass(frozen=True)
class BodyFact:
    """What was measured of one body: ``dims`` are the sides of its smallest box, longest first (mm)."""

    id: str
    volume: float  # mm3
    area: float  # mm2
    faces: int
    dims: tuple[float, float, float]
    material: str = ""


def signature(fact: BodyFact) -> str:
    """What makes two bodies the same piece: the sides of the box, the volume, the surface and the number of faces."""
    sides = ",".join(f"{round(d / ROUND_MM) * ROUND_MM:.1f}" for d in fact.dims)
    return f"{sides}|{round(fact.volume / 10.0)}|{round(fact.area / 10.0)}|{fact.faces}"


def measure(dims: tuple[float, float, float], comma: bool, language: str) -> str:
    """The length of a bar (``L 600``) or the blank of a plate (``200 x 150 x 6``): the longest side against the next one
    tells which."""
    long, mid, thin = dims
    if mid <= 0 or long / mid >= PROFILE_RATIO:
        return f"L {fmt(long, comma)}"
    return " × ".join(fmt(d, comma) for d in (long, mid, thin))


def group_bodies(facts: Sequence[BodyFact], language: str) -> Assembly | None:
    """The positions of the cut list, numbered in the order of the first body of each; ``None`` when there are fewer than two
    bodies (a single body is a part, not a weldment)."""
    if len(facts) < 2:  # noqa: PLR2004
        return None
    comma = language == "es"
    groups: dict[str, PartGroup] = {}
    for fact in facts:
        key = signature(fact)
        if key in groups:
            groups[key].instances.append(fact.id)
            continue
        groups[key] = PartGroup(
            position=len(groups) + 1,
            key=key,
            name="",  # the designer's: never the object name
            reference=measure(fact.dims, comma, language),
            material=fact.material,
            remarks="",
            instances=[fact.id],
            measured=fact,
        )
    return Assembly(list(groups.values()))


# --- views that draw the same ---------------------------------------------------------------------------------------------

VIEW_AXES = {"front": (0, 2, False), "top": (0, 1, True), "right": (1, 2, False)}  # page u, page v (flipped: below the front)
SAME_MM = 0.5
Box = tuple[tuple[float, float, float], tuple[float, float, float]]


def _outline(boxes: Sequence[Box], view: str) -> frozenset[tuple[float, float, float, float]]:
    """The rectangles the bodies make in a view, from the corner of the whole (so two views can be compared)."""
    u, v, flip = VIEW_AXES[view]
    rects = [(lo[u], lo[v], hi[u], hi[v]) for lo, hi in boxes]
    if flip:
        rects = [(a, -d, c, -b) for a, b, c, d in rects]
    left = min(r[0] for r in rects)
    low = min(r[1] for r in rects)
    step = SAME_MM
    return frozenset(
        tuple(round((value - base) / step) for value, base in zip(r, (left, low, left, low), strict=True))  # type: ignore[misc]
        for r in rects
    )


def congruent_views(boxes: Sequence[Box]) -> list[tuple[str, str]]:
    """The pairs of standard views (``kept``, ``repeat``) whose bodies make the same rectangles: the second draws what the
    first draws already (a square frame seen from the front and from above)."""
    if len(boxes) < 2:  # noqa: PLR2004
        return []
    seen: list[str] = []
    found: list[tuple[str, str]] = []
    for view in VIEW_AXES:
        mine = _outline(boxes, view)
        twin = next((s for s in seen if _outline(boxes, s) == mine), None)
        if twin is not None:
            found.append((twin, view))
        else:
            seen.append(view)
    return found


def views_without_repeats(boxes: Sequence[Box]) -> list[str]:
    """The standard views of a weldment with the ones that repeat another left out (the front one always stays)."""
    gone = {repeat for _, repeat in congruent_views(boxes)}
    return [v for v in VIEW_AXES if v not in gone]


# --- the designation against the body -------------------------------------------------------------------------------------

ROUND_WORDS = re.compile(r"redond|round|chs|[øØ⌀]|diam|\bd\s*\d", re.I)
TUBE = re.compile(r"\b(tubo|tube|rhs|shs|ptr|hss)\b", re.I)
FLAT = re.compile(r"\b(pletina|platina|llanta|flat\s*bar|flat)\b", re.I)
PLATE = re.compile(r"\b(chapa|plate|placa|l[aá]mina|sheet)\b", re.I)
TUBE_SIZE_TOL = 0.12  # share of the box volume that a tube's walls may differ by (corner radii, mitres)
SOLID_MIN_FILL = 0.6  # a flat bar or plate fills at least this much of its box (chamfers, mitres, cut-outs)


@dataclass(frozen=True)
class DesignationCheck:
    """``status``: ok | mismatch | skipped (not a tube, flat bar or plate designation: nothing was checked); ``why`` says it."""

    status: str
    why: str = ""


def _numbers(text: str) -> list[float]:
    return [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text)]


def check_designation(name: str, fact: BodyFact) -> DesignationCheck:
    """Whether the designer's designation agrees with what was measured of the body. Tubes (RHS/SHS), flat bars and plates are
    read; any other designation is skipped (said, not guessed). The section of a bar is its two smaller sides."""
    text = name.strip()
    length, side, thin = fact.dims
    box = length * side * thin or 1.0
    fill = fact.volume / box
    numbers = _numbers(text)
    if ROUND_WORDS.search(text) and not PLATE.search(text):
        return DesignationCheck("skipped", "round sections are not checked")
    if TUBE.search(text):
        if len(numbers) != 3:  # noqa: PLR2004
            return DesignationCheck("skipped", "a tube is read as width x height x wall")
        a, b, t = numbers
        big, small = max(a, b), min(a, b)
        if t <= 0 or 2 * t >= small:
            return DesignationCheck("skipped", "the wall does not fit the section")
        if abs(side - big) > SAME_MM * 2 or abs(thin - small) > SAME_MM * 2:
            return DesignationCheck(
                "mismatch", f"the body measures a section of {fmt(side)} x {fmt(thin)} mm, the designation says {fmt(big)} x {fmt(small)}"
            )
        expected = (big * small - (big - 2 * t) * (small - 2 * t)) / (big * small)
        if fill > expected + TUBE_SIZE_TOL:
            return DesignationCheck(
                "mismatch",
                f"the designation is a hollow tube (it fills {round(100 * expected)} % of its box) and the body is solid "
                f"(it fills {round(100 * min(fill, 1.0))} %)",
            )
        if fill < expected - TUBE_SIZE_TOL:
            return DesignationCheck(
                "mismatch", f"the body fills {round(100 * fill)} % of its box and a {fmt(t)} mm wall fills {round(100 * expected)} %"
            )
        return DesignationCheck("ok")
    if FLAT.search(text):
        if len(numbers) != 2:  # noqa: PLR2004
            return DesignationCheck("skipped", "a flat bar is read as width x thickness")
        big, small = max(numbers), min(numbers)
        if abs(side - big) > SAME_MM * 2 or abs(thin - small) > SAME_MM * 2:
            return DesignationCheck(
                "mismatch", f"the body measures a section of {fmt(side)} x {fmt(thin)} mm, the designation says {fmt(big)} x {fmt(small)}"
            )
        if fill < SOLID_MIN_FILL:
            return DesignationCheck("mismatch", f"the designation is solid bar and the body is hollow (it fills {round(100 * fill)} % of its box)")
        return DesignationCheck("ok")
    if PLATE.search(text):
        if len(numbers) == 1:
            if abs(thin - numbers[0]) > SAME_MM * 2:
                return DesignationCheck("mismatch", f"the body is {fmt(thin)} mm thick, the designation says {fmt(numbers[0])}")
        elif len(numbers) == 3:  # noqa: PLR2004
            wanted = sorted(numbers, reverse=True)
            if any(abs(m - n) > SAME_MM * 2 for m, n in zip(fact.dims, wanted, strict=True)):
                return DesignationCheck(
                    "mismatch", f"the body measures {' x '.join(fmt(d) for d in fact.dims)} mm, the designation says {' x '.join(fmt(n) for n in wanted)}"
                )
        else:
            return DesignationCheck("skipped", "a plate is read as its thickness or its three sizes")
        if fill < SOLID_MIN_FILL:
            return DesignationCheck("mismatch", f"the designation is a solid plate and the body fills {round(100 * fill)} % of its box")
        return DesignationCheck("ok")
    return DesignationCheck("skipped", "only tubes, flat bars and plates are checked")
