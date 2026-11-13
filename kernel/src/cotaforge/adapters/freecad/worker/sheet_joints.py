# ruff: noqa: E501
"""The joints of a welded part, on plain facts (no FreeCAD import, adapter-neutral: an adapter measures the contacts between
the bodies and builds the ``Joint`` records, the rules below read them).

A joint is where two bodies of the part touch: the harness MEASURES it (a face of one lies on a face of the other, or only
their edges meet) and never guesses it from names. What it does NOT know is the weld: which kind, the throat or the leg, one
side or both, all round or intermittent are the designer's decision. The harness only proposes the usual one (a fillet whose
throat is about 0.7 of the thinner wall, rounded to a standard throat) so the designer can accept it with one word.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

Vec3 = tuple[float, float, float]

KINDS = ("tee", "corner", "butt", "lap", "edge")
THROATS = (2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0)  # standard throats a (mm)
THROAT_FACTOR = 0.7  # a = 0.7 x the thinner wall (the usual rule for a fillet weld loaded evenly)
BUTT_SQUARE_MAX_MM = 3.0  # a butt joint up to this thickness is welded square (I) from one side; thicker, a V groove
AREA_SAME = 0.9  # the contact is "the whole face" of a body when it covers this share of it
END_FACE_SHARE = 0.3  # a face smaller than this share of the biggest flat face of its body is an end (a tube end, a plate edge)
LARGER_FACE = 1.2  # a face this many times bigger than the contact is a surface the other body stands on
THICKNESS_STEP_MM = 0.5


def natural(body_id: str) -> tuple[int, str]:
    """Sort key of a body id: ``body:2`` before ``body:10``."""
    found = re.search(r"(\d+)$", body_id)
    return (int(found.group(1)) if found else 0, body_id)


def joint_key(a: str, b: str) -> str:
    """The same two bodies give the same key, whatever the order."""
    first, second = sorted((a, b), key=natural)
    return f"{first}+{second}"


def wall_thickness(volume: float, area: float, dims: tuple[float, float, float]) -> float:
    """The wall of a body: twice its volume over its surface (exact for a plate or a tube wall, more than the wall for a
    solid bar), never more than its smallest side; in half millimetres. What the harness measures, not a designation."""
    smallest = min(dims)
    estimate = 2.0 * volume / area if area > 0 else smallest
    return round(min(estimate, smallest) / THICKNESS_STEP_MM) * THICKNESS_STEP_MM


@dataclass(frozen=True)
class Contact:
    """What was measured where two bodies touch: ``area`` of the overlap of two faces (0: only edges meet), the areas of the
    two whole faces, whether each face is an end of its body, whether the seam runs along an outer edge of the bigger face
    (``flush``)."""

    area: float
    faces: tuple[float, float]
    ends: tuple[bool, bool]
    flush: bool = False


def classify(contact: Contact) -> str:
    """``butt`` (two ends meet over the whole face), ``tee`` (an end stands on the surface of the other), ``corner`` (the end
    stands on the other and is flush with its edge, or two ends meet partly), ``lap`` (two surfaces overlap), ``edge`` (only
    edges touch)."""
    if contact.area <= 0:
        return "edge"
    small, large = sorted(contact.faces)
    if small > 0 and contact.area >= AREA_SAME * large:
        return "butt"
    standing = contact.area >= AREA_SAME * small and large >= LARGER_FACE * contact.area
    if contact.ends[0] and contact.ends[1]:
        return "corner"
    if standing and (contact.ends[0] or contact.ends[1]):
        return "corner" if contact.flush else "tee"
    if not contact.ends[0] and not contact.ends[1]:
        return "lap"
    return "corner" if contact.flush else "tee"


@dataclass(frozen=True)
class Joint:
    """Two bodies that touch. ``seams`` are the lines the weld follows (3D polylines, mm): the outer outline of the contact,
    or the intersection edges when only edges meet. ``thickness`` is the wall measured of each body."""

    bodies: tuple[str, str]
    kind: str
    seams: tuple[tuple[Vec3, ...], ...]
    thickness: tuple[float, float]
    area: float = 0.0

    @property
    def key(self) -> str:
        return joint_key(*self.bodies)

    @property
    def length(self) -> float:
        return sum(math.dist(p, q) for line in self.seams for p, q in zip(line, line[1:], strict=False))

    @property
    def thin(self) -> float:
        return min(self.thickness)

    @property
    def signature(self) -> tuple[str, float, int]:
        """What makes two joints the same weld job: the kind, the thinner wall and the length of the seam."""
        return (self.kind, round(self.thin, 1), round(self.length / 2.0))


def groups(joints: Sequence[Joint]) -> list[list[Joint]]:
    """The joints that are the same job (same kind, wall and seam length) together, in the order of the first of each. The
    designer welds a group once: one symbol, ``n×``."""
    out: dict[tuple[str, float, int], list[Joint]] = {}
    for joint in sorted(joints, key=lambda j: [natural(b) for b in j.bodies]):
        out.setdefault(joint.signature, []).append(joint)
    return list(out.values())


@dataclass(frozen=True)
class Weld:
    """What a weld symbol says: ``kind`` fillet | v | square, the ``size_mm`` and what it is (``throat`` a, ``leg`` z, for a
    butt weld the penetration s)."""

    kind: str
    size_mm: float
    size_kind: str


def standard_throat(wanted: float) -> float:
    """The standard throat nearest to ``wanted`` (the larger one when it is half way)."""
    best = min(THROATS, key=lambda t: (abs(t - wanted), -t))
    return best


def proposal(joint: Joint) -> Weld:
    """The usual weld of a joint, offered to the designer, never applied: a fillet of throat ~0.7 x the thinner wall for a
    tee, corner, lap or edge; for a butt joint a square (up to 3 mm) or V butt weld of the wall's thickness."""
    if joint.kind == "butt":
        return Weld("square" if joint.thin <= BUTT_SQUARE_MAX_MM else "v", joint.thin, "throat")
    return Weld("fillet", standard_throat(THROAT_FACTOR * joint.thin), "throat")


def covers(pairs: Sequence[Sequence[str]]) -> set[str]:
    """The joint keys a list of body pairs names."""
    return {joint_key(p[0], p[1]) for p in pairs if len(p) == 2}  # noqa: PLR2004


def same_class_note(text: str) -> bool:
    """Whether a note gives ISO 13920 as the quality level of a weld. ISO 13920 is the general tolerances for welded
    constructions (lengths, angles, straightness); the quality levels of a weld (B, C, D) are ISO 5817. A note that says
    ISO 13920 next to a class, in a weld note, mixes them up; a note that says it is the general tolerance is right."""
    if not re.search(r"13920", text):
        return False
    if re.search(r"5817", text) and not re.search(r"clase\s+iso\s*13920|class\s+iso\s*13920|13920\s*[-–]\s*[bcd]\b", text, re.I):
        return False
    if re.search(r"toleranc|tolerance", text, re.I):
        return False
    return bool(re.search(r"solda|weld|cord[oó]n|bead|clase|class|nivel|level|calidad|quality", text, re.I))
