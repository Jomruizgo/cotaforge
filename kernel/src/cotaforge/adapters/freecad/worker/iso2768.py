"""ISO 2768 general tolerances as pure data (no FreeCAD import): part 1 (linear sizes, external radii and chamfer
heights, angles; classes f fine, m medium, c coarse, v very coarse) and part 2 (geometrical tolerances; classes H, K,
L).

PROVENANCE: transcribed by hand from ISO 2768-1:1989 (tables 1 to 3) and ISO 2768-2:1989 (tables 1 to 4) as they are
reproduced in handbooks; the text of the standard was not at hand. ``TRANSCRIPTION_NOTE`` travels with every report that
quotes these tables until the owner has checked them against the standard.
"""

import math
import re

EDITION = "ISO 2768-1:1989 / ISO 2768-2:1989"
TRANSCRIPTION_NOTE = {
    "es": "tablas ISO 2768 transcritas; verificar contra la norma",
    "en": "ISO 2768 tables transcribed; verify against the standard",
}

LINEAR_CLASSES = ("f", "m", "c", "v")
GEOMETRIC_CLASSES = ("H", "K", "L")
INF = math.inf

# table 1: permissible deviations (+-, mm) for linear dimensions; the size ranges are "over 0.5 up to" the bound
LINEAR_BOUNDS = (3.0, 6.0, 30.0, 120.0, 400.0, 1000.0, 2000.0, 4000.0)
LINEAR: dict[str, tuple[float | None, ...]] = {
    "f": (0.05, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, None),
    "m": (0.1, 0.1, 0.2, 0.3, 0.5, 0.8, 1.2, 2.0),
    "c": (0.2, 0.3, 0.5, 0.8, 1.2, 2.0, 3.0, 4.0),
    "v": (None, 0.5, 1.0, 1.5, 2.5, 4.0, 6.0, 8.0),
}

# table 2: external radii and chamfer heights (+-, mm)
RADIUS_BOUNDS = (3.0, 6.0, INF)
RADIUS: dict[str, tuple[float, ...]] = {
    "f": (0.2, 0.5, 1.0),
    "m": (0.2, 0.5, 1.0),
    "c": (0.4, 1.0, 2.0),
    "v": (0.4, 1.0, 2.0),
}

# table 3: angular dimensions, by the length of the shorter leg (mm); the deviation is +-minutes of arc
ANGLE_BOUNDS = (10.0, 50.0, 120.0, 400.0, INF)
ANGLE_MINUTES: dict[str, tuple[int, ...]] = {
    "f": (60, 30, 20, 10, 5),
    "m": (60, 30, 20, 10, 5),
    "c": (90, 60, 30, 15, 10),
    "v": (180, 120, 60, 30, 20),
}

# ISO 2768-2: straightness and flatness by the length of the feature (mm), mm
FLATNESS_BOUNDS = (10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0)
FLATNESS: dict[str, tuple[float, ...]] = {
    "H": (0.02, 0.05, 0.1, 0.2, 0.3, 0.4),
    "K": (0.05, 0.1, 0.2, 0.4, 0.6, 0.8),
    "L": (0.1, 0.2, 0.4, 0.8, 1.2, 1.6),
}
# perpendicularity (by the shorter side) and symmetry, mm
SQUARE_BOUNDS = (100.0, 300.0, 1000.0, 3000.0)
PERPENDICULARITY: dict[str, tuple[float, ...]] = {
    "H": (0.2, 0.3, 0.4, 0.5),
    "K": (0.4, 0.6, 0.8, 1.0),
    "L": (0.6, 1.0, 1.5, 2.0),
}
SYMMETRY: dict[str, tuple[float, ...]] = {
    "H": (0.5, 0.5, 0.5, 0.5),
    "K": (0.6, 0.6, 0.8, 1.0),
    "L": (0.6, 1.0, 1.5, 2.0),
}
RUNOUT: dict[str, float] = {"H": 0.1, "K": 0.2, "L": 0.5}

_DESIGNATION = re.compile(r"2768\s*-?\s*([fmcv])([HKL])?", re.IGNORECASE)


def parse(text: str) -> tuple[str, str | None] | None:
    """``"ISO 2768-mK"`` -> ``("m", "K")``; ``None`` when the text does not name the standard (an own note)."""
    match = _DESIGNATION.search(text or "")
    if match is None:
        return None
    return match.group(1).lower(), (match.group(2).upper() if match.group(2) else None)


def _pick(bounds: tuple[float, ...], values: tuple, size: float) -> object:
    for bound, value in zip(bounds, values, strict=True):
        if size <= bound:
            return value
    return None


def linear(cls: str, size_mm: float) -> float | None:
    """Permissible deviation (+-, mm) of a linear dimension, or ``None`` below 0.5 mm, above 4000 mm or where the class
    has no value (f above 2000 mm, v up to 3 mm)."""
    if size_mm < 0.5:
        return None
    return _pick(LINEAR_BOUNDS, LINEAR[cls], size_mm)  # type: ignore[return-value]


def radius(cls: str, size_mm: float) -> float | None:
    """Permissible deviation (+-, mm) of an external radius or a chamfer height."""
    if size_mm < 0.5:
        return None
    return _pick(RADIUS_BOUNDS, RADIUS[cls], size_mm)  # type: ignore[return-value]


def angle_minutes(cls: str, shorter_leg_mm: float) -> int:
    """Permissible deviation (+-, minutes of arc) of an angle by the length of its shorter leg."""
    return int(_pick(ANGLE_BOUNDS, ANGLE_MINUTES[cls], shorter_leg_mm))  # type: ignore[arg-type]


def angle_text(minutes: int) -> str:
    """``60`` -> ``±1°``, ``30`` -> ``±0°30'``, ``5`` -> ``±0°5'``."""
    degrees, rest = divmod(minutes, 60)
    return f"±{degrees}°" + (f"{rest}'" if rest else "")


def flatness(cls: str, length_mm: float) -> float | None:
    """Straightness and flatness (mm) by the length of the feature."""
    return _pick(FLATNESS_BOUNDS, FLATNESS[cls], length_mm)  # type: ignore[return-value]


def perpendicularity(cls: str, shorter_side_mm: float) -> float | None:
    return _pick(SQUARE_BOUNDS, PERPENDICULARITY[cls], shorter_side_mm)  # type: ignore[return-value]


def symmetry(cls: str, length_mm: float) -> float | None:
    return _pick(SQUARE_BOUNDS, SYMMETRY[cls], length_mm)  # type: ignore[return-value]


def runout(cls: str) -> float:
    return RUNOUT[cls]
