# ruff: noqa: E501
"""What ``drawing(action=plan)`` asks about a welded part: the designation of each body of the cut list (with the section the
harness MEASURED as the proposal), the weld of each group of identical joints (with the weld it proposes) and the welding
process with its quality level. The harness measures; the designer decides: nothing here is applied, and the answers are the
decisions ``bodies``, ``welds`` and ``welding`` of ``compose``."""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from cotaforge.language import say

JOINT_NAMES = {
    "tee": ("en T", "T joint"),
    "corner": ("en esquina", "corner joint"),
    "butt": ("a tope", "butt joint"),
    "lap": ("a solape", "lap joint"),
    "edge": ("de arista", "edge joint"),
}
PROFILE_NAMES = {"tube": ("Tubo", "Tube"), "bar": ("Pletina", "Flat bar"), "plate": ("Chapa", "Plate")}
WELD_NAMES = {
    "fillet": ("soldadura de ángulo continua", "continuous fillet weld"),
    "square": ("soldadura a tope en I", "square (I) butt weld"),
    "v": ("soldadura a tope en V", "V butt weld"),
}
DEFAULT_WELDING = '{"process":"MAG","quality":"C","tolerance":"B"}'


@dataclass(frozen=True)
class Ask:
    """One question: ``keys`` the decisions that answer it, ``joints`` the joints it is about (empty: not about joints)."""

    keys: tuple[str, ...]
    text: str
    joints: tuple[str, ...] = ()


def _number(value: float, language: str) -> str:
    text = f"{value:g}"
    return text.replace(".", ",") if language == "es" else text


def joint_key(a: str, b: str) -> str:
    """The same two bodies give the same key, whatever the order (``body:2`` before ``body:10``)."""

    def natural(body: str) -> tuple[int, str]:
        found = re.search(r"(\d+)$", body)
        return (int(found.group(1)) if found else 0, body)

    first, second = sorted((a, b), key=natural)
    return f"{first}+{second}"


def weld_keys(items: Iterable[dict[str, Any]]) -> set[str]:
    """The joints the ``weld`` items of the decisions name (``between`` and ``also``)."""
    out: set[str] = set()
    for item in items:
        if item.get("type") != "weld":
            continue
        for pair in [item.get("between") or [], *(item.get("also") or [])]:
            if isinstance(pair, Sequence) and len(pair) == 2 and all(isinstance(p, str) for p in pair):  # noqa: PLR2004
                out.add(joint_key(pair[0], pair[1]))
    return out


def designation(body: Any, language: str) -> str:
    """The designation the measured section suggests (``Tubo 40×40×3``): a proposal for the designer, never applied."""
    name = PROFILE_NAMES[body.kind][0 if language == "es" else 1]
    sizes = [*body.section_mm, *([body.wall_mm] if body.kind == "tube" and body.wall_mm else [])]
    return f"{name} {'×'.join(_number(s, language) for s in sizes)}"


def _bodies(weldment: Any, language: str, recompose: bool) -> Ask | None:
    todo = [b for b in weldment.bodies if recompose or not b.named]
    if not todo:
        return None
    shown = "; ".join(
        f"{b.position} ({b.instances[0]}{f', x{len(b.instances)}' if len(b.instances) > 1 else ''}"
        f"{f', {b.reference}' if b.reference else ''}): {designation(b, language)}"
        for b in todo
    )
    rows = ", ".join(f'{{"component":"{b.instances[0]}","name":"{designation(b, language)}"}}' for b in todo)
    text = say(
        language,
        f"¿Qué es cada cuerpo de la lista de corte? Designación (perfil y medida) de cada posición: la sección se midió, el perfil y el "
        f"material los decide el diseñador, nunca el nombre del objeto. Propuesta por lo medido: {shown}. En bodies: [{rows}]",
        f"What is each body of the cut list? The designation (profile and size) of each position: the section was measured, the profile "
        f"and the material are the designer's decision, never the object name. Proposal from what was measured: {shown}. In bodies: [{rows}]",
    )
    return Ask(("bodies",), text)


def _joint(group: Any, language: str) -> Ask:
    kind = JOINT_NAMES.get(group.kind, (group.kind, group.kind))[0 if language == "es" else 1]
    names = ", ".join(f"{a}+{b}" for a, b in group.pairs)
    many = len(group.pairs) > 1
    weld = WELD_NAMES[group.weld_kind][0 if language == "es" else 1]
    size = _number(group.size_mm, language)
    sizing = f"a={size} mm" if group.weld_kind == "fillet" else f"s={size} mm"
    around = say(language, ", todo alrededor", ", all round") if group.all_around else ""
    first, *others = group.pairs
    parts = [f'"between":["{first[0]}","{first[1]}"]']
    if others:
        parts.append('"also":[' + ",".join(f'["{a}","{b}"]' for a, b in others) + "]")
    parts += [f'"kind":"{group.weld_kind}"', f'"size_mm":{group.size_mm:g}', '"size_kind":"throat"', '"sides":"arrow"']
    if group.all_around:
        parts.append('"all_around":true')
    item = '{"type":"weld",' + ",".join(parts) + "}"
    basis = (
        say(language, f"0,7 × la pared más delgada ({_number(group.thin_mm, language)} mm), con mínimo de 3 mm", f"0.7 x the thinner wall ({_number(group.thin_mm, language)} mm), at least 3 mm")
        if group.weld_kind == "fillet"
        else say(language, "el espesor", "the thickness")
    )
    text = say(
        language,
        f"¿Qué soldadura lleva la unión {kind} {names} ({len(group.pairs)} {'iguales' if many else 'igual'}; cordón de "
        f"{_number(group.length_mm, language)} mm)? Propuesta medida: {weld}, {sizing} ({basis}), por el lado de la flecha{around}. "
        f"Con su respuesta, un weld por grupo: {item}",
        f"Which weld does the {kind} {names} ({len(group.pairs)} {'identical' if many else 'joint'}; seam "
        f"{_number(group.length_mm, language)} mm) carry? Measured proposal: {weld}, {sizing} ({basis}), on the arrow side{around}. "
        f"With the answer, one weld per group: {item}",
    )
    return Ask(("welds",), text, tuple(joint_key(a, b) for a, b in group.pairs))


def _welding(language: str) -> Ask:
    text = say(
        language,
        "¿Con qué proceso se suelda y qué niveles lleva? Proceso (MAG, TIG, electrodo...), nivel de calidad de la soldadura ISO 5817 "
        "(B, C o D) y clase de las tolerancias generales de la construcción soldada ISO 13920 (A, B, C o D). Propuesta: MAG, nivel C, "
        f"tolerancias B. La nota y el rótulo salen de esta decisión, sin contradecir los símbolos. En welding: {DEFAULT_WELDING}",
        "Which welding process and which levels? The process (MAG, TIG, stick...), the weld quality level ISO 5817 (B, C or D) and the "
        "class of the general tolerances of the welded construction ISO 13920 (A, B, C or D). Proposal: MAG, level C, tolerances B. The "
        f"note and the title block come from this decision and never contradict the symbols. In welding: {DEFAULT_WELDING}",
    )
    return Ask(("welding",), text)


def ask(weldment: Any, language: str, recompose: bool = False, quality_on_sheet: bool = False) -> list[Ask]:
    """The questions of a welded part, in the order the designer answers them: the bodies, the weld of each group of joints,
    the process. ``recompose``: the sheet carries items of an earlier compose, which a new one takes off, so everything is
    asked again. ``quality_on_sheet``: the sheet prints an ISO 5817 level already (the process is not asked again)."""
    if weldment is None:
        return []
    out: list[Ask] = []
    bodies = _bodies(weldment, language, recompose)
    if bodies is not None:
        out.append(bodies)
    out += [_joint(g, language) for g in weldment.joints if recompose or not g.welded]
    if recompose or not quality_on_sheet:
        out.append(_welding(language))
    return out
