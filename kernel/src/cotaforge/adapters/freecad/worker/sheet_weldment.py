"""The cut list of a weldment (no FreeCAD import, adapter-neutral): one part made of several welded bodies.

A weldment is a part document with two or more live bodies. What the harness KNOWS of each body is what it measures:
its volume, its surface, its faces and the sides of the smallest box around it. It does NOT know that a body is "Tubo
40x40x3" or "Pletina 50x6": nothing in the model says so, and a name is never made up. The designation is the designer's:
it is written in the rows of the parts list (``rows=[{"component": "body:2", "name": "Tubo 40x40x3"}]``) and an empty one
is a question to him (``weld_body_unnamed``).

Bodies that are the same piece (same volume, same box, same faces and surface: so the same profile, length and end cuts)
share one row with their quantity.
"""

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
        )
    return Assembly(list(groups.values()))
