# ruff: noqa: E501
"""The three drawing rules of ``sheet_viewrules`` as review findings of the FreeCAD sheet (no FreeCAD import):

* ``dimension_side``: a dimension whose extension lines cross the part while the other side is free;
* ``view_redundant``: a view that only carries a cutting line, or whose dimensions another view repeats;
* ``arrows_inside_short``: a short dimension with its arrows inside.

They are style: listed and answerable, they never hold an export (``sheet_checks.NON_BLOCKING``).
``sheet_review.review`` calls ``check`` after its own checks.
"""

from typing import Any

import sheet_viewrules as rules
from sheet_collide import segments_of
from sheet_context import Context, ViewInfo
from sheet_model import CONTOUR
from sheet_review import Finding, ReviewInput

DIMENSION_TYPES = frozenset({"dim", "overall", "datum_dims"})
CARRIES_OTHER = frozenset({"hole_dim", "balloons", "gdt", "datum", "finish", "detail", "aux_view", "weld"})
SAME_PLANE = 1e-3


def check(data: ReviewInput) -> list[Finding]:
    return [*_sides(data), *_short(data), *_redundant(data), *_hatches(data)]


# --- the hatch of the bodies of a section ----------------------------------------------------------------------------------------


def _hatches(data: ReviewInput) -> list[Finding]:
    """Two adjacent bodies of a section hatched alike (same pattern, angle and spacing), or two bodies of one material with
    different patterns: the bodies are told apart by angle or spacing, never by the pattern (the material's)."""
    import sheet_hatch  # noqa: PLC0415 - the hatching of a section with several bodies

    ctx = data.ctx
    out: list[Finding] = []
    for label, (facts, touching) in ctx.extras.get("hatches", {}).items():
        for f in sheet_hatch.hatch_findings(facts, touching):
            a, b = f.bodies
            if f.code == "hatch_same_as_neighbour":
                text = ctx.say(
                    f"En la sección {label}, {a} y {b} se tocan y llevan el mismo rayado: distíngalos por el ángulo "
                    f"(gira {b} {ctx.num(f.propose_turn or 90.0)}°: 45° frente a 135°) o por el espaciado "
                    f'(section hatch={{"{b}": {{"turn": {ctx.num(f.propose_turn or 90.0)}}}}}), nunca por el patrón.',
                    f"In section {label}, {a} and {b} touch and are hatched alike: tell them apart by angle (turn {b} "
                    f"{ctx.num(f.propose_turn or 90.0)} degrees: 45 against 135) or by spacing "
                    f'(section hatch={{"{b}": {{"turn": {ctx.num(f.propose_turn or 90.0)}}}}}), never by the pattern.',
                )
            else:
                text = ctx.say(
                    f"En la sección {label}, {a} y {b} son del mismo material y tienen patrones distintos: el patrón es "
                    "el del material, igual en todos los cuerpos; distíngalos por el ángulo o el espaciado.",
                    f"In section {label}, {a} and {b} are of the same material and have different patterns: the pattern "
                    "is the material's, the same on every body; tell them apart by angle or spacing.",
                )
            out.append(Finding(f.code, "warning", text, key=f"{label}:{a}+{b}"))
    return out


# --- the side of a dimension ----------------------------------------------------------------------------------------------


def _views(ctx: Context) -> dict[str, ViewInfo]:
    return {**ctx.views, **ctx.extras.get("sections", {})}


def _outlines(ctx: Context, views: dict[str, ViewInfo]) -> dict[str, list[rules.Segment]]:
    ink = [e for e in ctx.extras.get("view_ink", []) if getattr(e, "layer", None) == CONTOUR]
    pieces = segments_of(ink)
    out: dict[str, list[rules.Segment]] = {}
    for name, view in views.items():
        x0, y0, x1, y1 = view.box
        out[name] = [
            s for s in pieces if x0 - 1 <= (s[0] + s[2]) / 2 <= x1 + 1 and y0 - 1 <= (s[1] + s[3]) / 2 <= y1 + 1
        ]
    return out


def _es(side: str) -> str:
    return {"above": "arriba", "below": "abajo", "left": "a la izquierda", "right": "a la derecha"}[side]


def _sides(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    shapes: list[rules.DimShape] = ctx.extras.get("dim_shapes", [])
    if not shapes:
        return []
    views = _views(ctx)
    found = rules.dimension_side(shapes, _outlines(ctx, views), {n: v.box for n, v in views.items()})
    types = {str(i["id"]): str(i.get("type")) for i in data.items}
    out: list[Finding] = []
    for f in found:
        name = f"{f.owner} ({types[f.owner]})" if f.owner in types else f.owner
        text = ctx.say(
            f"La cota {name} de la vista {f.view} está {_es(f.now)} y sus líneas de extensión cruzan la pieza {f.crossings_now} "
            f"veces; del lado {_es(f.better)} cruzan {f.crossings_better}. Una cota va del lado del rasgo que mide: muévela "
            f"(side={f.better}).",
            f"Dimension {name} in the {f.view} view stands {f.now} and its extension lines cross the part {f.crossings_now} "
            f"times; on the {f.better} side they cross {f.crossings_better}. A dimension goes on the side of the feature it "
            f"measures: move it (side={f.better}).",
        )
        out.append(Finding("dimension_side", "warning", text, (f.owner,) if f.owner in types else (), key=f.owner))
    return out


# --- arrows -----------------------------------------------------------------------------------------------------------------


def _short(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    for d in rules.short_dimensions(ctx.extras.get("dim_shapes", [])):
        text = ctx.say(
            f"La cota {d.owner} de la vista {d.view} mide {ctx.num(d.span or 0.0)} mm entre sus líneas de extensión: no caben "
            "el texto y las dos flechas. Pon las flechas por fuera (arrows=outside).",
            f"Dimension {d.owner} in the {d.view} view spans {ctx.num(d.span or 0.0)} mm between its extension lines: the text "
            "and the two arrows do not fit. Put the arrows outside (arrows=outside).",
        )
        out.append(Finding("arrows_inside_short", "warning", text, (d.owner,), key=d.owner))
    return out


# --- a view that only carries a cutting line ------------------------------------------------------------------------------


def _unit(v: Any) -> tuple[float, float, float]:
    length = sum(c * c for c in v) ** 0.5 or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def _alternatives(ctx: Context, parent: str, normal: Any) -> tuple[str, ...]:
    """The standard views, besides ``parent``, in which a plane with this normal is seen edge-on (its cutting line is a line
    there): the views whose direction lies in the plane."""
    n = _unit(normal)
    return tuple(
        name
        for name, view in ctx.views.items()
        if name != parent and abs(sum(a * b for a, b in zip(_unit(view.direction), n, strict=True))) <= SAME_PLANE
    )


def _uses(data: ReviewInput) -> list[rules.ViewUse]:
    ctx = data.ctx
    types = {str(i["id"]): str(i.get("type")) for i in data.items}
    specs = {str(i["id"]): i for i in data.items}
    use = ctx.extras.get("view_use", {})
    sections: dict[str, ViewInfo] = ctx.extras.get("sections", {})
    owner_of = {item: label for label, item in ctx.extras.get("section_items", {}).items()}
    planes = ctx.extras.get("section_planes", {})
    dims: dict[str, set[Any]] = {}
    recorded: set[tuple[str, str]] = set()
    for item, view, key in ctx.extras.get("dim_records", []):
        dims.setdefault(view, set()).add(tuple(key))
        recorded.add((item, view))
    by_hand = ctx.extras.get("hand_marks", {})
    cuts: dict[str, list[rules.Cut]] = {}
    for item, label in owner_of.items():
        parent = str(
            specs.get(item, {}).get("view")
            or next((v for v, users in use.items() if item in users and v in ctx.views), "")
        )
        if parent and label in planes:
            cuts.setdefault(parent, []).append(rules.Cut(label, _alternatives(ctx, parent, planes[label][1])))
    out: list[rules.ViewUse] = []
    for name in [*ctx.views, *sections]:
        users = [i for i in use.get(name, ()) if i not in owner_of]  # the section item only holds the cutting line
        other = bool(by_hand.get(name)) or any(
            types.get(i) in CARRIES_OTHER or (types.get(i) in DIMENSION_TYPES and (i, name) not in recorded)
            for i in users
        )
        out.append(
            rules.ViewUse(name, frozenset(dims.get(name, ())), other, tuple(cuts.get(name, ())), keep=name in sections)
        )
    out.append(rules.ViewUse("isometric", pictorial=True))
    return out


def _redundant(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    if len(ctx.views) + len(ctx.extras.get("sections", {})) <= 1:
        return []
    out: list[Finding] = []
    for r in rules.redundant_views(_uses(data)):
        labels = ", ".join(c.label for c in r.cuts)
        carriers = sorted({a for c in r.cuts for a in c.alternatives})
        move = (
            ctx.say(
                f"; la línea de corte {labels} pasa a la vista {' o '.join(carriers)} (section con view=)",
                f"; the cutting line {labels} moves to the {' or '.join(carriers)} view (section with view=)",
            )
            if r.cuts
            else ""
        )
        if r.reason == "cut_only":
            text = ctx.say(
                f"La vista {r.name} solo lleva la línea de corte {labels}: otra vista que se queda puede llevarla y el corte "
                f"lleva las cotas. Quítala (drawing action=remove_view, view={r.name}){move}.",
                f"The {r.name} view only carries the cutting line {labels}: another view that stays can carry it and the "
                f"section carries the dimensions. Drop it (drawing action=remove_view, view={r.name}){move}.",
            )
        else:
            text = ctx.say(
                f"Las cotas de la vista {r.name} las repite {', '.join(r.repeated_in)} y no lleva nada propio: es redundante. "
                f"Quítala (drawing action=remove_view, view={r.name}){move}.",
                f"The dimensions of the {r.name} view are repeated in {', '.join(r.repeated_in)} and it carries nothing of its "
                f"own: it is redundant. Drop it (drawing action=remove_view, view={r.name}){move}.",
            )
        out.append(Finding("view_redundant", "warning", text, (r.name,), key=r.name))
    return out
