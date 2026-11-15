# ruff: noqa: E501
"""The parts an assembly section does not cut (no FreeCAD import).

In a section of an assembly the solid parts are drawn whole, in their outline, as if the cutting plane passed behind them
(ISO 128-34): shafts, bolts, screws, studs, nuts, washers, pins, keys, rivets, balls and the ribs and spokes a plane would cut
lengthwise. Hatching them says they are hollow or that the plane found material that is not there. A shaft is cut only when the
section exists to show something inside it, and then with a local (broken-out) section, never with the plane of the whole one.

``solid_kind`` says what a part is from its designation and its shape; ``cut_solids`` is the review: the solid parts a section
did cut.
"""

import re
from collections.abc import Mapping, Sequence

KINDS: Mapping[str, str] = {
    "shaft": "shaft", "axle": "shaft", "spindle": "shaft", "arbor": "shaft", "eje": "shaft", "arbol": "shaft",
    "bolt": "bolt", "screw": "screw", "stud": "bolt", "tornillo": "screw", "perno": "bolt", "esparrago": "bolt",
    "nut": "nut", "tuerca": "nut",
    "washer": "washer", "arandela": "washer",
    "pin": "pin", "dowel": "pin", "pasador": "pin", "clavija": "pin",
    "key": "key", "chaveta": "key", "lengueta": "key",
    "rivet": "rivet", "remache": "rivet",
    "ball": "ball", "bola": "ball", "esfera": "ball",
    "rib": "rib", "nervio": "rib", "spoke": "spoke", "radio": "spoke",
    "rod": "rod", "varilla": "rod",
}  # fmt: skip
WORDS = re.compile(r"[a-z]+")


def _plain(text: str) -> str:
    return text.lower().translate(str.maketrans("áéíóúü", "aeiouu"))


def solid_kind(name: str, round_solid: bool = False) -> str | None:
    """The kind of solid part a designation names (``Eje de transmisión``, ``screw M8x35``, ``key``), else ``None``; a part of
    revolution with every face coaxial and nothing inside (``round_solid``) is a shaft, a pin or a ball whatever it is called."""
    for word in WORDS.findall(_plain(name)):
        base = word[:-1] if word.endswith("s") and word[:-1] in KINDS else word
        if base in KINDS:
            return KINDS[base]
    return "shaft" if round_solid else None


def cut_solids(cut: Sequence[tuple[str, str, bool]], allowed: Sequence[str] = ()) -> list[tuple[str, str]]:
    """The solid parts among ``cut`` (``(component, designation, round_solid)`` the section cut): ``(designation, kind)``.
    ``allowed`` names the parts the engineer asked to be cut (a local section that shows what is inside them)."""
    out: list[tuple[str, str]] = []
    for comp, name, round_solid in cut:
        kind = solid_kind(name, round_solid)
        if kind is not None and comp not in allowed and name not in allowed:
            out.append((name or comp, kind))
    return out
