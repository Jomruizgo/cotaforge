"""ISO metric thread sizes (contract/threads.py): the one table the IR, the executor and the verification share for hole
diameters. Coarse threads (``M12``) from M2 to M68 and fine threads (``M45x1.5``) from the ISO 261 series of pitches.

Pure data, standard library only (the worker of the drawing sheet loads this file by path). The tap drill is NOT typed:
it is the major diameter minus the pitch (the tapping drill rule of ISO 2306 / DIN 336, the size a tap goes into and the
one the CAD kernel drills for a cosmetic tapped hole), computed by ``tap_drill_by_rule``. The only exceptions are the
two coarse sizes whose rule value is not a standard drill: M8 (6.75, drilled 6.8) and M12 (10.25, drilled 10.2).
Clearance diameters are the medium series of ISO 273 and are known up to M36 only: a larger or a fine size has none and
``iso_clearance`` asks for ``diameter_mm``.

PROVENANCE: the sizes are the ISO 261 / ISO 262 series (coarse pitches and the fine pitches by nominal diameter) as the
handbooks reproduce them and as the ISO metric tables of the CAD list them; the text of the standard was not at hand. A
test on the real CAD checks every size of this table against the CAD's own tables, with the drill it computes for each:
no size is invented and no drill differs. ``TRANSCRIPTION_NOTE`` travels with the reports that quote these tables until
the owner has checked them against the standard.
"""

import re
from dataclasses import dataclass

__all__ = [
    "EDITION",
    "FINE_THREADS",
    "THREADS",
    "TRANSCRIPTION_NOTE",
    "ThreadSpec",
    "clearance_diameter_mm",
    "fine_thread_sizes",
    "tap_drill_by_rule",
    "tap_drill_mm",
    "thread",
    "thread_sizes",
]

EDITION = "ISO 261 / ISO 262 / ISO 2306 / ISO 273"
TRANSCRIPTION_NOTE = {
    "es": "tabla de roscas ISO 261 transcrita; verificar contra la norma",
    "en": "ISO 261 thread table transcribed; verify against the standard",
}

_DESIGNATION = re.compile(r"^M(\d+(?:[.,]\d+)?)(?:x(\d+(?:[.,]\d+)?))?$")


@dataclass(frozen=True)
class ThreadSpec:
    """One row of the table. A plain dataclass on purpose: the FreeCAD worker (no pydantic in its Python) loads
    this file by path for the drawing sheet, so the module imports nothing but the standard library."""

    designation: str
    """``M12`` for a coarse thread, ``M45x1.5`` for a fine one (the way a drawing writes them, with a dot)."""
    major_mm: float
    pitch_mm: float
    tap_drill_mm: float
    """Major diameter minus pitch (ISO 2306 rule); M8 and M12 coarse take the standard drill next to it."""
    clearance_medium_mm: float | None = None
    """ISO 273 clearance hole, medium series; ``None`` where the table does not carry it."""
    fine: bool = False


def tap_drill_by_rule(major_mm: float, pitch_mm: float) -> float:
    """The tapping drill of a metric thread by the rule of ISO 2306: major diameter minus pitch."""
    return round(major_mm - pitch_mm, 2)


def _number(value: float) -> str:
    return f"{value:g}"


def _coarse(major: float, pitch: float, clearance: float | None = None, drill: float | None = None) -> ThreadSpec:
    return ThreadSpec(
        designation=f"M{_number(major)}",
        major_mm=major,
        pitch_mm=pitch,
        tap_drill_mm=tap_drill_by_rule(major, pitch) if drill is None else drill,
        clearance_medium_mm=clearance,
    )


THREADS: dict[str, ThreadSpec] = {
    spec.designation: spec
    for spec in (
        _coarse(2, 0.4, 2.4),
        _coarse(2.5, 0.45, 2.9),
        _coarse(3, 0.5, 3.4),
        _coarse(3.5, 0.6, 3.9),
        _coarse(4, 0.7, 4.5),
        _coarse(5, 0.8, 5.5),
        _coarse(6, 1.0, 6.6),
        _coarse(8, 1.25, 9.0, drill=6.8),
        _coarse(10, 1.5, 11.0),
        _coarse(12, 1.75, 13.5, drill=10.2),
        _coarse(14, 2.0, 15.5),
        _coarse(16, 2.0, 17.5),
        _coarse(18, 2.5, 20.0),
        _coarse(20, 2.5, 22.0),
        _coarse(22, 2.5, 24.0),
        _coarse(24, 3.0, 26.0),
        _coarse(27, 3.0, 30.0),
        _coarse(30, 3.5, 33.0),
        _coarse(33, 3.5, 36.0),
        _coarse(36, 4.0, 39.0),
        _coarse(39, 4.0),
        _coarse(42, 4.5),
        _coarse(45, 4.5),
        _coarse(48, 5.0),
        _coarse(52, 5.0),
        _coarse(56, 5.5),
        _coarse(60, 5.5),
        _coarse(64, 6.0),
        _coarse(68, 6.0),
    )
}

# ISO 261 fine pitches (mm) by nominal diameter (mm)
_FINE_PITCHES: dict[float, tuple[float, ...]] = {
    1: (0.2,),
    1.1: (0.2,),
    1.2: (0.2,),
    1.4: (0.2,),
    1.6: (0.2,),
    1.8: (0.2,),
    2: (0.25,),
    2.2: (0.25,),
    2.5: (0.35,),
    3: (0.35,),
    3.5: (0.35,),
    4: (0.5,),
    4.5: (0.5,),
    5: (0.5,),
    5.5: (0.5,),
    6: (0.75,),
    7: (0.75,),
    8: (0.75, 1.0),
    9: (0.75, 1.0),
    10: (0.75, 1.0, 1.25),
    11: (0.75, 1.0),
    12: (1.0, 1.25, 1.5),
    14: (1.0, 1.25, 1.5),
    15: (1.0, 1.5),
    16: (1.0, 1.5),
    17: (1.0, 1.5),
    18: (1.0, 1.5, 2.0),
    20: (1.0, 1.5, 2.0),
    22: (1.0, 1.5, 2.0),
    24: (1.0, 1.5, 2.0),
    25: (1.0, 1.5, 2.0),
    27: (1.0, 1.5, 2.0),
    28: (1.0, 1.5, 2.0),
    30: (1.0, 1.5, 2.0, 3.0),
    32: (1.5, 2.0),
    33: (1.5, 2.0, 3.0),
    35: (1.5, 2.0),
    36: (1.5, 2.0, 3.0),
    39: (1.5, 2.0, 3.0),
    40: (1.5, 2.0, 3.0),
    42: (1.5, 2.0, 3.0, 4.0),
    45: (1.5, 2.0, 3.0, 4.0),
    48: (1.5, 2.0, 3.0, 4.0),
    50: (1.5, 2.0, 3.0),
    52: (1.5, 2.0, 3.0, 4.0),
    55: (1.5, 2.0, 3.0, 4.0),
    56: (1.5, 2.0, 3.0, 4.0),
    58: (1.5, 2.0, 3.0, 4.0),
    60: (1.5, 2.0, 3.0, 4.0),
    62: (1.5, 2.0, 3.0, 4.0),
    64: (1.5, 2.0, 3.0, 4.0),
    65: (1.5, 2.0, 3.0, 4.0),
    68: (1.5, 2.0, 3.0, 4.0),
    70: (1.5, 2.0, 3.0, 4.0, 6.0),
    72: (1.5, 2.0, 3.0, 4.0, 6.0),
    75: (1.5, 2.0, 3.0, 4.0, 6.0),
    76: (1.5, 2.0, 3.0, 4.0, 6.0),
    80: (1.5, 2.0, 3.0, 4.0, 6.0),
    85: (2.0, 3.0, 4.0, 6.0),
    90: (2.0, 3.0, 4.0, 6.0),
    95: (2.0, 3.0, 4.0, 6.0),
    100: (2.0, 3.0, 4.0, 6.0),
}


def _fine(major: float, pitch: float) -> ThreadSpec:
    return ThreadSpec(
        designation=f"M{_number(major)}x{_number(pitch)}",
        major_mm=major,
        pitch_mm=pitch,
        tap_drill_mm=tap_drill_by_rule(major, pitch),
        fine=True,
    )


FINE_THREADS: dict[str, ThreadSpec] = {
    spec.designation: spec
    for spec in (_fine(major, pitch) for major, pitches in _FINE_PITCHES.items() for pitch in pitches)
}


def thread_sizes() -> tuple[str, ...]:
    """Every coarse designation of the table, by ascending major diameter."""
    return tuple(THREADS)


def fine_thread_sizes() -> tuple[str, ...]:
    """Every fine designation of the table (``M45x1.5``), by ascending major diameter then pitch."""
    return tuple(FINE_THREADS)


def thread(size: str) -> ThreadSpec | None:
    """The table row of ``size``: ``M6`` (coarse), ``M45x1.5`` (fine; a comma or an ``×`` is read as the dot and the
    ``x``) or the coarse thread spelled with its pitch (``M12x1.75``). ``None`` when the table does not have it."""
    found = _DESIGNATION.match(str(size).strip().replace("×", "x").replace("X", "x"))
    if found is None:
        return None
    major = float(found.group(1).replace(",", "."))
    if found.group(2) is None:
        return THREADS.get(f"M{_number(major)}")
    pitch = float(found.group(2).replace(",", "."))
    fine = FINE_THREADS.get(f"M{_number(major)}x{_number(pitch)}")
    if fine is not None:
        return fine
    coarse = THREADS.get(f"M{_number(major)}")
    return coarse if coarse is not None and abs(coarse.pitch_mm - pitch) < 1e-9 else None


def tap_drill_mm(size: str) -> float | None:
    spec = thread(size)
    return None if spec is None else spec.tap_drill_mm


def clearance_diameter_mm(size: str) -> float | None:
    spec = thread(size)
    return None if spec is None else spec.clearance_medium_mm
