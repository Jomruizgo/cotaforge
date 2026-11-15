# ruff: noqa: E501
"""The parts an assembly section does not cut (no FreeCAD import).

In a section of an assembly the solid parts are drawn whole, in their outline, as if the cutting plane passed behind them
(ISO 128-34): shafts, bolts, screws, studs, nuts, washers, pins, keys, rivets, balls and the ribs and spokes a plane would cut
lengthwise. Hatching them says they are hollow or that the plane found material that is not there. A shaft is cut only when the
section exists to show something inside it, and then with a local (broken-out) section, never with the plane of the whole one.

What a part is comes from three places, in this order: its designation (the catalogue or standard part type: ``Eje``,
``screw M8x35``), the standard it names (``ISO 4762``, ``DIN 6885``) and, only when those say nothing, its shape (a long solid
of revolution with nothing inside; a small rectangular block longer than it is wide, a key). A part held whole by its shape alone
is a guess, and ``assembly_whole_guess`` asks the engineer about it.

``solid_kind`` says what a part is; ``lengthwise`` whether the plane runs along it (a bolt cut across stays cut: a circle);
``hatch_classes`` which cut bodies share a hatch (one TechDraw section each); ``cut_solids`` is the review: the solid parts a
section did cut.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

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
CUT_WORDS = frozenset(
    {
        "housing", "carcasa", "caja", "cover", "tapa", "cubierta", "hub", "cubo", "bush", "bushing", "casquillo", "buje", "bearing",
        "rodamiento", "cojinete", "flange", "brida", "support", "soporte", "plate", "placa", "pulley", "polea", "gear", "engranaje",
        "sprocket", "pinon", "body", "cuerpo", "case", "casing", "frame", "bastidor", "bracket", "base", "spacer", "separador",
        "sleeve", "manguito", "collar", "ring", "anillo", "wheel", "rueda", "disk", "disco", "coupling", "acople", "cap", "tope",
    }
)  # fmt: skip
"""Words that name a body a plane does cut: a designation like ``Carcasa del eje`` or ``shaft cover`` is one of these."""
CONNECTORS = frozenset({"de", "del", "para", "con", "of", "with", "for"})
STANDARDS: Mapping[str, str] = {
    **dict.fromkeys(("4762", "4017", "4026", "4027", "4028", "4029", "7380", "10642", "7045", "7046", "7048", "1207", "1580", "912", "933", "84", "7991", "965", "7380"), "screw"),
    **dict.fromkeys(("4014", "4016", "4018", "931", "558", "601", "603"), "bolt"),
    **dict.fromkeys(("4032", "4033", "4034", "4035", "8673", "8675", "934", "985", "982", "7040"), "nut"),
    **dict.fromkeys(("7089", "7090", "7091", "7092", "7093", "7094", "125", "433", "9021", "6340", "127", "7980", "6796"), "washer"),
    **dict.fromkeys(("2338", "2339", "8734", "8735", "8736", "8737", "8740", "8741", "8744", "8745", "8746", "8747", "8750", "8752", "1234", "6325", "7979", "94", "1481"), "pin"),
    **dict.fromkeys(("773", "6885", "6886", "6888", "2491"), "key"),
}  # fmt: skip
"""Standard numbers (ISO / DIN / EN) of the fasteners, pins and keys a plane does not cut."""
CUT = "cut"  # what the words say when the head noun names a body a plane cuts
WORDS = re.compile(r"[a-z]+")
STANDARD = re.compile(r"\b(?:iso|din|en|jis)[\s\-]*(?:en[\s\-]*)?(?:iso[\s\-]*)?(\d{1,5})\b")
LENGTHWISE_TOL = 0.3  # |axis . plane normal| up to this: the plane runs along the part (it is not cut across)
KEY_ASPECT = 1.8  # a key is at least this many times longer than it is wide
KEY_SHAPE = (0.5, 1.05)  # its height against its width (DIN 6885: 0.64 to 1)
KEY_SMALL = 0.4  # and no longer than this fraction of the assembly's size
BLOCK_FILL = 0.97  # a block fills at least this much of its own box (a plain prism, no holes)
SHAFT_SLENDER = 2.0  # a solid of revolution seen as a shaft by its shape alone is at least this many times longer than wide


def plain(text: str) -> str:
    """Lower case without accents: how names are compared."""
    return text.lower().translate(str.maketrans("áéíóúü", "aeiouu"))


_plain = plain


def _singular(word: str) -> str:
    for cut in (1, 2):  # tornillos, pasadores
        if word.endswith("s" if cut == 1 else "es") and (word[:-cut] in KINDS or word[:-cut] in CUT_WORDS):
            return word[:-cut]
    return word


def _by_words(name: str) -> str | None:
    """The kind the words of a designation name: the head noun decides when a solid word and a cut one meet (``Carcasa del eje`` is a
    housing, ``Eje de la carcasa`` a shaft; with no connector word the last one is the noun, as in English: ``drive shaft``,
    but ``shaft cover`` is a cover)."""
    words = [_singular(w) for w in WORDS.findall(_plain(name))]
    hits = [(i, w) for i, w in enumerate(words) if w in KINDS or w in CUT_WORDS]
    if not hits:
        return None
    link = next((i for i, w in enumerate(words) if w in CONNECTORS), None)
    if link is not None:
        before = [h for h in hits if h[0] < link] or hits
        word = before[0][1]
    else:
        word = hits[-1][1] if len(hits) > 1 else hits[0][1]
    return KINDS.get(word, CUT)


def _by_standard(name: str) -> str | None:
    for number in STANDARD.findall(_plain(name)):
        if number in STANDARDS:
            return STANDARDS[number]
    return None


@dataclass(frozen=True)
class SolidFact:
    """A solid part of an assembly: the component, its designation, what it is (``kind``), what says so (``basis``: name, standard
    or shape), whether it is a part of revolution with nothing inside and the direction of its long axis (``None``: not known)."""

    comp: str
    name: str
    kind: str
    basis: str
    round_solid: bool = False
    axis: tuple[float, float, float] | None = None


def named_cut(name: str | Sequence[str]) -> bool:
    """Whether the designation names a body a plane cuts (``Carcasa``, ``Tapa``, ``Cubo``, ``Rodamiento``): no shape test is needed."""
    for text in [name] if isinstance(name, str) else name:
        if (kind := _by_words(text)) is not None:
            return kind == CUT
    return False


def key_like(block: tuple[float, float, float], span: float) -> bool:
    """Whether a plain rectangular block (its three sizes) is shaped like a parallel key and small against the assembly (``span``: its
    largest size; 0 when not known)."""
    length, width, height = sorted(block, reverse=True)
    if width <= 0 or height <= 0:
        return False
    if length < KEY_ASPECT * width or not KEY_SHAPE[0] * width <= height <= KEY_SHAPE[1] * width:
        return False
    return span <= 0 or length <= KEY_SMALL * span


def classify(
    name: str | Sequence[str], round_solid: bool = False, block: tuple[float, float, float] | None = None, span: float = 0.0
) -> tuple[str, str] | None:
    """``(kind, basis)`` of a part a plane does not cut, else ``None``. ``basis`` is ``name`` (its designation says what it is),
    ``standard`` (the standard it names) or ``shape`` (nothing else says it: a guess). ``round_solid``: a part of revolution with
    every face coaxial and nothing inside; ``block``: the sizes of a plain rectangular block."""
    for text in [name] if isinstance(name, str) else name:  # the designation first, then the file name of the part
        if (kind := _by_words(text)) is not None:
            return None if kind == CUT else (kind, "name")  # a word that names a cut part ends the search
    for text in [name] if isinstance(name, str) else name:
        if (kind := _by_standard(text)) is not None:
            return kind, "standard"
    if round_solid:
        return "shaft", "shape"
    if block is not None and key_like(block, span):
        return "key", "shape"
    return None


def solid_kind(name: str | Sequence[str], round_solid: bool = False, block: tuple[float, float, float] | None = None, span: float = 0.0) -> str | None:
    """The kind of solid part a designation names (``Eje de transmisión``, ``screw M8x35``, ``key``, ``ISO 4762 M6x20``), else ``None``;
    a part of revolution with every face coaxial and nothing inside (``round_solid``) is a shaft, a pin or a ball whatever it is called."""
    found = classify(name, round_solid, block, span)
    return found[0] if found else None


def lengthwise(axis: Sequence[float] | None, normal: Sequence[float]) -> bool:
    """Whether the cutting plane (its ``normal``) runs along the part's long ``axis``: a shaft or a bolt is cut lengthwise by a plane
    through its axis and across by a plane square to it (the circle of a cross section is hatched). Not known: lengthwise."""
    if axis is None:
        return True
    a, n = math.sqrt(sum(v * v for v in axis)), math.sqrt(sum(v * v for v in normal))
    if a == 0 or n == 0:
        return True
    return abs(sum(x * y for x, y in zip(axis, normal, strict=True))) / (a * n) <= LENGTHWISE_TOL


def hatch_classes(styles: Mapping[str, tuple[float, float]]) -> list[list[str]]:
    """The cut bodies grouped by hatch ``(turn, spacing)``, in the order their first body appears: touching bodies never share a class,
    so a TechDraw section per class hatches each body at its own angle (one section has one hatch rotation)."""
    classes: dict[tuple[float, float], list[str]] = {}
    for body, style in styles.items():
        classes.setdefault((round(style[0] % 180.0, 3), round(style[1], 3)), []).append(body)
    return list(classes.values())


def cut_solids(cut: Sequence[tuple[str, str, bool]], allowed: Sequence[str] = ()) -> list[tuple[str, str]]:
    """The solid parts among ``cut`` (``(component, designation, round_solid)`` the section cut): ``(designation, kind)``.
    ``allowed`` names the parts the engineer asked to be cut (a local section that shows what is inside them)."""
    out: list[tuple[str, str]] = []
    for comp, name, round_solid in cut:
        kind = solid_kind(name, round_solid)
        if kind is not None and comp not in allowed and name not in allowed:
            out.append((name or comp, kind))
    return out
