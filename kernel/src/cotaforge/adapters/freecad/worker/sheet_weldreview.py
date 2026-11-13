# ruff: noqa: E501
"""The review of the welds of a welded part (no FreeCAD import): every joint the harness measured has its weld symbol, or the
engineer's order that it has none; a joint that has neither is a blocking finding that asks the designer ONCE (offering the
usual weld, never applying it). Also: one symbol per joint, and a weld note that names ISO 13920 as a quality class."""

import re
from typing import Any

import sheet_joints
import sheet_weldrules
from sheet_context import Context

WELD_TEXT = re.compile(r"solda|weld|cord[oó]n|bead|2553", re.I)
KIND_NAMES = {
    "tee": ("en T", "T joint"),
    "corner": ("en esquina", "corner joint"),
    "butt": ("a tope", "butt joint"),
    "lap": ("a solape", "lap joint"),
    "edge": ("de arista", "edge joint"),
}


def joints_of(ctx: Context) -> list[Any]:
    try:
        return list(ctx.geometry.weld_joints())
    except Exception:  # noqa: BLE001 - a geometry that cannot measure its joints has none to ask about
        return []


def _joint_name(ctx: Context, joint: Any) -> str:
    def pos(body: str) -> str:
        found = ctx.weldment.position_of(body) if ctx.weldment is not None else None
        return f"{found}({body})" if found is not None else body

    return f"{pos(joint.bodies[0])}+{pos(joint.bodies[1])}"


def _proposal_text(ctx: Context, joint: Any) -> str:
    weld = sheet_joints.proposal(joint)
    closed = any(len(line) > 2 and line[0] == line[-1] for line in joint.seams)  # noqa: PLR2004
    size = ctx.num(weld.size_mm)
    thin = ctx.num(joint.thin)
    if weld.kind == "fillet":
        return ctx.say(
            f"soldadura de ángulo continua, garganta a={size} mm (0,7 × los {thin} mm de la pared más delgada, con mínimo de 3 mm para soldaduras resistentes según EN 1993-1-8 §4.5.2, a la serie "
            "normal), por el lado de la flecha" + (", todo alrededor" if closed else ""),
            f"continuous fillet weld, throat a={size} mm (0.7 x the {thin} mm of the thinner wall, at least 3 mm for load-bearing welds per EN 1993-1-8 4.5.2, rounded to the standard "
            "series), on the arrow side" + (", all round" if closed else ""),
        )
    es = "I (cuadrada)" if weld.kind == "square" else "V"
    en = "square (I)" if weld.kind == "square" else "V"
    return ctx.say(
        f"soldadura a tope en {es}, penetración s={size} mm (el espesor)",
        f"{en} butt weld, penetration s={size} mm (the thickness)",
    )


def weld_pairs(spec: dict[str, Any]) -> list[list[str]]:
    return [spec.get("between") or [], *(spec.get("also") or [])]


def _example(first: Any, others: list[Any], weld: sheet_joints.Weld, closed: bool) -> str:
    pair = ", ".join(f'"{b}"' for b in first.bodies)
    also = ", ".join(f'["{j.bodies[0]}", "{j.bodies[1]}"]' for j in others)
    parts = [f'"between":[{pair}]'] + ([f'"also":[{also}]'] if also else [])
    parts += [f'"kind":"{weld.kind}"', '"size_mm":<size>', '"sides":"arrow"'] + (['"all_around":true'] if closed else [])
    return '{"type":"weld",' + ",".join(parts) + "}"


def joint_findings(data: Any, joints: list[Any]) -> list[Any]:
    """One finding per group of identical joints that no weld item covers."""
    from items_weld import seen_in  # noqa: PLC0415
    from sheet_review import Finding  # noqa: PLC0415

    ctx = data.ctx
    covered = sheet_joints.covers([p for spec in data_of(data, "weld") for p in weld_pairs(spec)])
    out: list[Any] = []
    for group in sheet_joints.groups(joints):
        open_ = [j for j in group if j.key not in covered]
        if not open_:
            continue
        first = open_[0]
        names = ", ".join(_joint_name(ctx, j) for j in open_)
        kind = KIND_NAMES[first.kind][0 if ctx.language == "es" else 1]
        view = seen_in(ctx, first)
        where = (
            ctx.say(f"se ve en la vista {view}", f"seen in the {view} view")
            if view
            else ctx.say("no se ve en ninguna vista estándar", "seen in no standard view")
        )
        weld = sheet_joints.proposal(first)
        closed = any(len(line) > 2 and line[0] == line[-1] for line in first.seams)  # noqa: PLR2004
        item = _example(first, open_[1:], weld, closed)
        many = len(open_) > 1
        text = ctx.say(
            f"Unión {kind} sin soldadura en el plano: {names} ({len(open_)} {'iguales' if many else 'igual'}; cordón de "
            f"{ctx.num(first.length)} mm, pared más delgada {ctx.num(first.thin)} mm; {where}). El plano no dice cómo se suelda: "
            f"pregunta UNA vez al diseñador qué soldadura lleva, proponiendo {_proposal_text(ctx, first)}; con su respuesta añade "
            f"un weld por grupo (símbolo ISO 2553 con la flecha en el cordón): {item}. Una nota general «a=4 salvo indicación» "
            "solo complementa los símbolos, no los sustituye.",
            f"{kind.capitalize()} with no weld on the sheet: {names} ({len(open_)} {'identical' if many else 'joint'}; seam "
            f"{ctx.num(first.length)} mm, thinner wall {ctx.num(first.thin)} mm; {where}). The sheet does not say how it is "
            f"welded: ask the designer ONCE which weld it carries, proposing {_proposal_text(ctx, first)}; with the answer add "
            f"one weld per group (an ISO 2553 symbol with the arrow on the seam): {item}. A general note 'a=4 unless shown' "
            "only adds to the symbols, it does not replace them.",
        )
        out.append(Finding("weld_joint_unspecified", "warning", text, (), (), first.key))
    return out


def data_of(data: Any, kind: str) -> list[dict[str, Any]]:
    import sheet_review  # noqa: PLC0415

    return sheet_review._of(data, kind)


def duplicate_findings(data: Any) -> list[Any]:
    facts = [
        sheet_weldrules.WeldFact(str(spec["id"]), sheet_joints.joint_key(pair[0], pair[1]), str(spec.get("sides", "arrow")))
        for spec in data_of(data, "weld")
        for pair in weld_pairs(spec)
        if len(pair) == 2  # noqa: PLR2004
    ]
    from sheet_review import Finding  # noqa: PLC0415

    ctx = data.ctx
    return [
        Finding(
            "weld_duplicate",
            "warning",
            ctx.say(
                f"La unión {dup.joint} lleva más de un símbolo ({', '.join(dup.items)}) y uno ya dice ambos lados: un símbolo por "
                "unión; quita el repetido.",
                f"The joint {dup.joint} carries more than one symbol ({', '.join(dup.items)}) and one already says both sides: "
                "one symbol per joint; remove the repeated one.",
            ),
            dup.items,
            (),
            dup.joint,
        )
        for dup in sheet_weldrules.duplicate_welds(facts)
    ]


def class_findings(data: Any) -> list[Any]:
    """A weld note that gives ISO 13920 as the quality class: ISO 13920 is the general tolerances of welded constructions;
    the quality levels of a weld (B, C, D) are ISO 5817."""
    from sheet_review import Finding  # noqa: PLC0415

    ctx = data.ctx
    bad = [n for n in data_of(data, "note") if sheet_joints.same_class_note(str(n.get("text", "")))]
    if not bad:
        return []
    text = ctx.say(
        "Una nota da ISO 13920 como clase de calidad de la soldadura. ISO 13920 son las tolerancias generales de las "
        "construcciones soldadas (longitudes, ángulos, rectitud); los niveles de calidad de una soldadura (B, C, D) son de "
        "ISO 5817. Corrige la nota: nivel ISO 5817 para la soldadura, ISO 13920 solo como tolerancia general en el rótulo.",
        "A note gives ISO 13920 as the quality class of the weld. ISO 13920 is the general tolerances of welded constructions "
        "(lengths, angles, straightness); the quality levels of a weld (B, C, D) are ISO 5817. Fix the note: an ISO 5817 level "
        "for the weld, ISO 13920 only as the general tolerance in the title block.",
    )
    return [Finding("weld_class_13920", "warning", text, tuple(str(n["id"]) for n in bad))]


def pictorial_findings(data: Any) -> list[Any]:
    """A weld symbol whose arrow touches a pictorial view (ISO 2553 symbols stand on orthographic views or sections: in a picture
    the seam is distorted and the arrow side is ambiguous)."""
    from items_weld import PICTORIAL  # noqa: PLC0415
    from sheet_review import Finding  # noqa: PLC0415

    ctx = data.ctx
    seen = ctx.extras.get("weld_views", {})
    out = []
    for spec in data_of(data, "weld"):
        bad = sorted({v for v in seen.get(str(spec["id"]), []) if v in PICTORIAL or v.startswith("iso")})
        if bad:
            text = ctx.say(
                f"El símbolo de soldadura {spec['id']} apunta a una vista pictórica ({', '.join(bad)}): en una isométrica el cordón se "
                "deforma y el lado de la flecha (ISO 2553) es ambiguo. Ponlo en una vista ortogonal o en un corte que muestre la unión; "
                "si ninguna la muestra, añade esa vista o corte.",
                f"The weld symbol {spec['id']} points into a pictorial view ({', '.join(bad)}): in an isometric view the seam is "
                "distorted and the arrow side (ISO 2553) is ambiguous. Put it on an orthographic view or a section that shows the "
                "joint; if none does, add that view or section.",
            )
            out.append(Finding("weld_on_pictorial", "warning", text, (str(spec["id"]),)))
    return out


def findings(data: Any) -> list[Any]:
    """The welds of a welded part: every measured joint has its symbol; with no joint measured, a part that says no weld
    anywhere is asked which ones."""
    from sheet_review import Finding  # noqa: PLC0415

    ctx = data.ctx
    out = class_findings(data) + duplicate_findings(data) + pictorial_findings(data)
    joints = joints_of(ctx)
    if joints:
        return out + joint_findings(data, joints)
    if data_of(data, "weld"):
        return out
    if any(WELD_TEXT.search(str(n.get("text", ""))) for n in data_of(data, "note")):
        return out
    text = ctx.say(
        "La pieza soldada no dice cómo se sueldan las uniones. Pregunta al diseñador qué uniones se sueldan y con qué soldadura "
        "(tipo, garganta o cateto, todo el contorno o intermitente, proceso, nivel de calidad ISO 5817) y ponlo con items weld "
        "(símbolo ISO 2553) entre los cuerpos que se tocan; no se midió ninguna unión (ningún cuerpo toca a otro).",
        "The welded part does not say how its joints are welded. Ask the designer which joints are welded and with which weld "
        "(kind, throat or leg, all around or intermittent, process, ISO 5817 quality level) and add it as weld items (an ISO "
        "2553 symbol) between the bodies that touch; no joint was measured (no body touches another).",
    )
    return [*out, Finding("weld_not_specified", "warning", text, ())]
