"""ISO 286 limits and fits as pure data (no FreeCAD import): the standard tolerance grades IT4..IT11 and the fundamental
deviations of the shaft letters e f g h js k m n p and the hole letters E F G H J JS K M N P, for nominal sizes up to
500 mm. ``deviations("H7", 8)`` is ``(15.0, 0.0)`` -- upper and lower limit deviation in micrometres.

PROVENANCE -- READ THIS BEFORE TRUSTING THE NUMBERS. The tables below were transcribed by hand from the values of
ISO 286-1:2010 (tolerance grades, clause 4 table) and ISO 286-2:2010 (fundamental deviations of shafts and holes) as
they are reproduced in the usual engineering handbooks. The text of the standard was NOT at hand when they were typed:
they have been checked for internal consistency (the hole deviations are derived from the shaft ones by the standard's
general and special rules, and the ``DELTA`` column must equal the difference of two neighbouring grades) and against
the ~30 canonical fits the unit tests list, but not against the printed standard. The sheet prints the class and the
limits these tables give for it (``Ø20 H7 (+0,021/0)``) and never a note that tells the shop to verify them: a drawing
is a statement, not a request to check it.

Size ranges are written "over a up to and including b": a nominal size of exactly 10 mm is in the 6..10 range.
"""

import bisect
import re

EDITION = "ISO 286-1:2010 / ISO 286-2:2010"

MAX_SIZE_MM = 500.0
GRADES = (5, 6, 7, 8, 9, 10, 11)  # the grades a fit may use here

# upper limit of each size range, mm (the first range starts above 0)
RANGES = (3.0, 6.0, 10.0, 18.0, 30.0, 50.0, 80.0, 120.0, 180.0, 250.0, 315.0, 400.0, 500.0)

# ISO 286-1 table 1: standard tolerances IT, micrometres, one value per size range. IT4 is here only to check DELTA.
IT: dict[int, tuple[float, ...]] = {
    4: (3, 4, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 20),
    5: (4, 5, 6, 8, 9, 11, 13, 15, 18, 20, 23, 25, 27),
    6: (6, 8, 9, 11, 13, 16, 19, 22, 25, 29, 32, 36, 40),
    7: (10, 12, 15, 18, 21, 25, 30, 35, 40, 46, 52, 57, 63),
    8: (14, 18, 22, 27, 33, 39, 46, 54, 63, 72, 81, 89, 97),
    9: (25, 30, 36, 43, 52, 62, 74, 87, 100, 115, 130, 140, 155),
    10: (40, 48, 58, 70, 84, 100, 120, 140, 160, 185, 210, 230, 250),
    11: (60, 75, 90, 110, 130, 160, 190, 220, 250, 290, 320, 360, 400),
}

# ISO 286-2 shafts: upper deviation es of e, f, g (negative); lower deviation ei of k (grades 4..7, else 0), m, n, p
SHAFT_ES = {
    "e": (-14, -20, -25, -32, -40, -50, -60, -72, -85, -100, -110, -125, -135),
    "f": (-6, -10, -13, -16, -20, -25, -30, -36, -43, -50, -56, -62, -68),
    "g": (-2, -4, -5, -6, -7, -9, -10, -12, -14, -15, -17, -18, -20),
}
SHAFT_EI = {
    "k": (0, 1, 1, 1, 2, 2, 2, 3, 3, 4, 4, 4, 5),
    "m": (2, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23),
    "n": (4, 8, 10, 12, 15, 17, 20, 23, 27, 31, 34, 37, 40),
    "p": (6, 12, 15, 18, 22, 26, 32, 37, 43, 50, 56, 62, 68),
}

# ISO 286-2 holes: J has its own table (grades 6, 7, 8 only), upper deviation ES
HOLE_J_ES = {
    6: (2, 5, 5, 6, 8, 10, 13, 16, 18, 22, 25, 29, 33),
    7: (4, 6, 8, 10, 12, 14, 18, 22, 26, 30, 36, 39, 43),
    8: (6, 10, 12, 15, 20, 24, 28, 34, 41, 47, 55, 60, 66),
}

# the special rule of K, M, N (up to IT8) and P (up to IT7): ES = -ei + DELTA, DELTA per grade IT5..IT8
DELTA: dict[int, tuple[float, ...]] = {
    5: (0, 1, 2, 3, 3, 4, 5, 5, 6, 6, 7, 7, 7),
    6: (0, 3, 3, 3, 4, 5, 6, 7, 7, 9, 9, 11, 13),
    7: (0, 4, 6, 7, 8, 9, 11, 13, 15, 17, 20, 21, 23),
    8: (0, 6, 7, 9, 12, 14, 16, 19, 23, 26, 29, 32, 34),
}

SHAFT_LETTERS = ("e", "f", "g", "h", "js", "k", "m", "n", "p")
HOLE_LETTERS = ("E", "F", "G", "H", "J", "JS", "K", "M", "N", "P")
_DESIGNATION = re.compile(r"^([A-Za-z]{1,2})(\d{1,2})$")


class FitError(ValueError):
    """A designation or a size the tables here do not cover; the message says what to change."""


def size_index(size_mm: float) -> int:
    """The size range a nominal size falls in (0 is "up to 3 mm")."""
    if not 0.0 < size_mm <= MAX_SIZE_MM:
        msg = f"a fit needs a nominal size above 0 and up to {MAX_SIZE_MM:g} mm (this one is {size_mm:g})"
        raise FitError(msg)
    return bisect.bisect_left(RANGES, size_mm)


def parse(designation: str) -> tuple[str, int, bool]:
    """``"H7"`` -> ``("H", 7, True)``: the letter(s), the grade and whether it is a hole (capital letters)."""
    match = _DESIGNATION.match(designation.strip())
    if match is None:
        msg = f"{designation!r} is not a tolerance class like H7, g6 or JS9"
        raise FitError(msg)
    letters, grade = match.group(1), int(match.group(2))
    hole = letters.isupper()
    if not (letters.isupper() or letters.islower()):
        msg = f"{designation!r}: capital letters are holes (H7), small letters shafts (g6); do not mix them"
        raise FitError(msg)
    allowed = HOLE_LETTERS if hole else SHAFT_LETTERS
    if letters not in allowed:
        msg = f"{designation!r}: the letter {letters} is not in the tables here; available: {', '.join(allowed)}"
        raise FitError(msg)
    if grade not in GRADES:
        msg = f"{designation!r}: grade IT{grade} is not in the tables here; available: IT5 to IT11"
        raise FitError(msg)
    return letters, grade, hole


def split_fit(text: str) -> list[str]:
    """``"H7/g6"`` -> ``["H7", "g6"]``; ``"H7"`` -> ``["H7"]``. Every part is checked."""
    parts = [p.strip() for p in text.split("/")]
    if len(parts) > 2 or not all(parts):
        msg = f"{text!r} is not a class (H7) or a fit (H7/g6)"
        raise FitError(msg)
    for part in parts:
        parse(part)
    return parts


def tolerance_um(grade: int, size_mm: float) -> float:
    return float(IT[grade][size_index(size_mm)])


def _shaft(letters: str, grade: int, i: int) -> tuple[float, float]:
    it = IT[grade][i]
    if letters == "h":
        return 0.0, -float(it)
    if letters == "js":
        return it / 2, -it / 2
    if letters in SHAFT_ES:
        es = float(SHAFT_ES[letters][i])
        return es, es - it
    ei = float(SHAFT_EI[letters][i]) if letters != "k" or grade <= 7 else 0.0
    return ei + it, ei


def _hole(letters: str, grade: int, i: int, size_mm: float) -> tuple[float, float]:
    it = IT[grade][i]
    if letters == "H":
        return float(it), 0.0
    if letters == "JS":
        return it / 2, -it / 2
    if letters == "J":
        if grade not in HOLE_J_ES:
            msg = f"J{grade}: the J letter is defined for grades IT6 to IT8 only; use JS{grade} for a symmetric zone"
            raise FitError(msg)
        es = float(HOLE_J_ES[grade][i])
        return es, es - it
    if letters in ("E", "F", "G"):
        ei = -float(SHAFT_ES[letters.lower()][i])
        return ei + it, ei
    delta = DELTA[grade][i] if grade in DELTA else 0.0
    if letters == "K":
        es = -float(SHAFT_EI["k"][i]) + delta if grade <= 8 else 0.0
    elif letters == "M":
        es = -float(SHAFT_EI["m"][i]) + (delta if grade <= 8 else 0.0)
    elif letters == "N":
        if grade <= 8:
            es = -float(SHAFT_EI["n"][i]) + delta
        else:
            es = -float(SHAFT_EI["n"][i]) if size_mm <= RANGES[0] else 0.0
    else:  # P
        es = -float(SHAFT_EI["p"][i]) + (delta if grade <= 7 else 0.0)
    return es, es - it


def deviations(designation: str, size_mm: float) -> tuple[float, float]:
    """``(upper, lower)`` limit deviations of the class ``designation`` for the nominal size, in micrometres."""
    letters, grade, hole = parse(designation)
    i = size_index(size_mm)
    return _hole(letters, grade, i, size_mm) if hole else _shaft(letters, grade, i)
