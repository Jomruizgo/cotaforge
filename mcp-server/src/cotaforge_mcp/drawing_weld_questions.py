# ruff: noqa: E501
"""What ``drawing(action=plan)`` asks about a welded part: the designation of each body of the cut list (with the section the
harness MEASURED as the proposal), the weld of each group of identical joints (with the weld it proposes) and the welding
process with its quality level. The harness measures; the designer decides: nothing here is applied, and the answers are the
decisions ``bodies``, ``welds`` and ``welding`` of ``compose``."""

import json
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


VIEW_ALONG = {0: "right", 1: "front", 2: "top"}
"""The standard view that looks along each axis (it shows a plate whose thin side is that axis face-on)."""
AXIS_NAMES = "xyz"
FLUSH_MM = 0.5


def _merged(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for low, high in sorted(intervals):
        if out and low <= out[-1][1] + FLUSH_MM:
            out[-1] = (out[-1][0], max(out[-1][1], high))
        else:
            out.append((low, high))
    return out


Fact = tuple[str, list[bool], list[tuple[str, float]]]


def position_dims(placement: Any, views: Sequence[str]) -> tuple[list[dict[str, Any]], list[Fact]] | None:
    """The dimensions that locate the members on the plate, measured, and what is measured about them: from each edge of the
    plate to the first member (when it is not flush with it) and between members, along both axes of the plate, in the view
    that shows the plate face-on. ``None`` when that view is not on the sheet or nothing needs saying."""
    base_low, base_high = placement.base_box
    thin = min(range(3), key=lambda i: base_high[i] - base_low[i])
    view = VIEW_ALONG[thin]
    if view not in views:
        return None
    boxes = [m.box for m in placement.members]
    if all(high[thin] <= base_low[thin] + FLUSH_MM for _, high in boxes):
        plane = base_low[thin]
    else:
        plane = base_high[thin]
    axes = [i for i in range(3) if i != thin]
    dims: list[dict[str, Any]] = []
    facts: list[Fact] = []
    for a in axes:
        other = next(i for i in axes if i != a)
        runs = _merged([(low[a], high[a]) for low, high in boxes])
        pairs: list[tuple[str, float, float]] = []
        if runs[0][0] - base_low[a] > FLUSH_MM:
            pairs.append(("edge", base_low[a], runs[0][0]))
        pairs += [("gap", one[1], two[0]) for one, two in zip(runs, runs[1:], strict=False)]
        if base_high[a] - runs[-1][1] > FLUSH_MM:
            pairs.append(("edge", runs[-1][1], base_high[a]))
        flush = [runs[0][0] - base_low[a] <= FLUSH_MM, base_high[a] - runs[-1][1] <= FLUSH_MM]
        facts.append((AXIS_NAMES[a], flush, [(kind, round(high - low, 1)) for kind, low, high in pairs]))
        for _kind, low, high in pairs:
            point = [0.0, 0.0, 0.0]
            point[thin], point[other] = plane, base_low[other]
            start, end = list(point), list(point)
            start[a], end[a] = low, high
            dims.append(
                {
                    "type": "dim",
                    "kind": "distance",
                    "from": {"at": [round(v, 2) for v in start]},
                    "to": {"at": [round(v, 2) for v in end]},
                    "along": AXIS_NAMES[a],
                    "view": view,
                }
            )
    return (dims, facts) if dims else None


def _facts_text(facts: list[Fact], language: str) -> str:
    words = {
        (True, True): say(language, "a ras de los dos bordes", "flush with both edges"),
        (False, False): say(language, "retranqueados de los dos bordes", "set in from both edges"),
    }
    out = []
    for name, flush, sizes in facts:
        edges = words.get((flush[0], flush[1]), say(language, "a ras de un borde y retranqueados del otro", "flush with one edge and set in from the other"))
        said = {"edge": say(language, "al borde", "to the edge"), "gap": say(language, "entre ellos", "between them")}
        out.append(f"{name}: {edges}" + "".join(f"; {said[kind]} {_number(size, language)}" for kind, size in sizes))
    return " | ".join(out)


def _positions(weldment: Any, views: Sequence[str], language: str) -> Ask | None:
    placement = getattr(weldment, "placement", None)
    if placement is None or not placement.members:
        return None
    found = position_dims(placement, views)
    if found is None:
        return None
    dims, facts = found
    items = json.dumps(dims, ensure_ascii=False, separators=(",", ":"))
    members = ", ".join(m.id for m in placement.members)
    measured = _facts_text(facts, language)
    text = say(
        language,
        f"¿Cómo se sitúan sobre la chapa {placement.base} los cuerpos que se sueldan a ella ({members})? El soldador no los puede colocar "
        f"sin una cota que los sitúe. Medido (mm): {measured}. Propuesta: acotar la separación entre ellos y lo que falte al borde cuando no "
        f"están a ras (lo que está a ras se ve en la vista y no lleva cota). En positions: {items}. Si los fija un útil o no hace falta "
        "acotarlos: positions: []",
        f"How do the bodies welded to the plate {placement.base} ({members}) stand on it? The welder cannot place them without a "
        f"dimension that locates them. Measured (mm): {measured}. Proposal: dimension the distance between them and what is left to the "
        f"edge when they are not flush (what is flush shows in the view and takes no dimension). In positions: {items}. If a jig fixes "
        "them or no dimension is needed: positions: []",
    )
    return Ask(("positions",), text)


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


def ask(
    weldment: Any,
    language: str,
    recompose: bool = False,
    quality_on_sheet: bool = False,
    views: Sequence[str] = (),
    positioned: bool = False,
) -> list[Ask]:
    """The questions of a welded part, in the order the designer answers them: the bodies, the weld of each group of joints,
    the process. ``recompose``: the sheet carries items of an earlier compose, which a new one takes off, so everything is
    asked again. ``quality_on_sheet``: the sheet prints an ISO 5817 level already (the process is not asked again). ``positioned``:
    the sheet carries the dimensions that locate the members (they are not asked again until a new compose takes them off)."""
    if weldment is None:
        return []
    out: list[Ask] = []
    bodies = _bodies(weldment, language, recompose)
    if bodies is not None:
        out.append(bodies)
    positions = None if positioned and not recompose else _positions(weldment, views, language)
    if positions is not None:
        out.append(positions)
    out += [_joint(g, language) for g in weldment.joints if recompose or not g.welded]
    if recompose or not quality_on_sheet:
        out.append(_welding(language))
    return out
