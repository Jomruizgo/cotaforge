# ruff: noqa: E501
"""The drawing rules of ``sheet_viewrules`` as review findings of the FreeCAD sheet (no FreeCAD import):

* ``dimension_side``: a dimension whose extension lines cross the part while the other side is free or clearly nearer;
* ``dimension_crossing``: an extension line across the dimension line of another dimension (ISO 129);
* ``datum_on_line``: the triangle, box or stem of a datum on a line of another item;
* ``datum_unused``: a datum item whose letter no tolerance frame (``gdt``) cites;
* ``leader_not_on_chamfer``: the leader of a chamfer callout not on the chamfer's line, not from outside or not perpendicular;
* ``view_redundant``: a view that only carries a cutting line, or whose dimensions another view repeats;
* ``arrows_inside_short``: a short dimension with its arrows inside.

They are style: listed and answerable, they never hold an export (``sheet_checks.NON_BLOCKING``).
``sheet_review.review`` calls ``check`` after its own checks.
"""

import math
from typing import Any

import sheet_viewrules as rules
from sheet_collide import ink_of, segments_of
from sheet_context import Context, ViewInfo
from sheet_model import CONTOUR, Line, Polyline, Solid
from sheet_review import Finding, ReviewInput
from sheet_units import KIND_ITEM

DIMENSION_TYPES = frozenset({"dim", "overall", "datum_dims"})
CARRIES_OTHER = frozenset({"hole_dim", "balloons", "gdt", "datum", "finish", "detail", "aux_view", "weld"})
SAME_PLANE = 1e-3
rules_point = tuple[float, float]


def check(data: ReviewInput) -> list[Finding]:
    return [
        *_sides(data),
        *_crossings(data),
        *_datums(data),
        *_unused_datums(data),
        *_chamfers(data),
        *_short(data),
        *_redundant(data),
        *_hatches(data),
        *_underused(data),
        *_no_isometric(data),
        *_crowded(data),
    ]


# --- the paper a sheet leaves blank ---------------------------------------------------------------------------------------------


def _footprint(ctx: Context) -> list[rules.Rect]:
    """The rectangles of everything the sheet draws in the free area for its views: every view (isometric and section
    views included) and the strip of each dimension line that lies in the area (clipped to it)."""
    boxes: list[rules.Rect] = list(ctx.extras.get("view_boxes", [])) or [v.box for v in ctx.views.values()]
    boxes += [v.box for v in ctx.extras.get("sections", {}).values()]
    boxes += [v.box for v in ctx.extras.get("views_extra", {}).values()]
    left, low, right, top = ctx.area
    out = [tuple(b) for b in boxes]
    for s in ctx.extras.get("strips", []):
        if s[0] < right and left < s[2] and s[1] < top and low < s[3]:
            out.append((max(s[0], left), max(s[1], low), min(s[2], right), min(s[3], top)))
    return out  # type: ignore[return-value]


def _view_boxes(ctx: Context) -> list[rules.Rect]:
    """The rectangles of the views alone (no dimension strips): what the part itself takes of the sheet."""
    boxes: list[rules.Rect] = list(ctx.extras.get("view_boxes", [])) or [v.box for v in ctx.views.values()]
    boxes += [v.box for v in ctx.extras.get("sections", {}).values()]
    boxes += [v.box for v in ctx.extras.get("views_extra", {}).values()]
    return [tuple(b) for b in boxes]  # type: ignore[misc]


def _fewer_views(ctx: Context) -> list[tuple[str, float]]:
    """``(views=[...], scale)`` for each set of FEWER standard views the part could be drawn in on this paper, with the
    largest standard scale the layout arithmetic gives it (fewest views dropped first): what to take off to make the views
    larger. Empty for an assembly or when the size of the part is not known."""
    import itertools  # noqa: PLC0415

    import sheet_layout  # noqa: PLC0415 - the layout arithmetic that chose the scale in the first place

    extent = ctx.extras.get("layout_mm") or ctx.extras.get("extent_mm")
    names = [n for n in ctx.views if n in ("front", "top", "right")]
    if extent is None or ctx.assembly is not None or "front" not in names:
        return []
    iso = "isometric" in ctx.extras.get("views_extra", {}) or len(ctx.extras.get("view_boxes", [])) > len(names)
    now = {"top": "top" in names, "right": "right" in names}  # the isometric stays: every sheet carries it
    w, d, h = (float(v) for v in extent)
    third = ctx.extras.get("angle") == "third"
    found: list[tuple[int, str, float]] = []
    held = [k for k, v in now.items() if v]
    for size in range(len(held) - 1, -1, -1):
        for keep in itertools.combinations(held, size):
            chosen = ["front", *[k for k in ("top", "right") if k in keep], *(["iso"] if iso else [])]
            layout = sheet_layout.plan(
                w, d, h, ctx.sheet.width, ctx.sheet.height, None, None, iso, "top" in keep, "right" in keep,
                third=third, iso_dims=(w, d, h), own_iso_scale=False,
            )  # fmt: skip
            found.append((len(chosen), "views=[" + ", ".join(chosen) + "]", layout.scale))
    found.sort(key=lambda row: -row[0])
    return [(what, scale) for _, what, scale in found]


def _iso_own(ctx: Context, scale: float) -> tuple[float, float] | None:
    """``(main scale, isometric scale)`` when the isometric view drawn at a scale of its own (ISO 5455: a pictorial view may
    have one, written under it) lets the main views take a larger standard scale on this paper: the automatic layout's
    arithmetic. ``None`` for an assembly, a sheet without an isometric view or when nothing is gained."""
    import sheet_layout  # noqa: PLC0415 - the layout arithmetic that chose the scale in the first place

    extent = ctx.extras.get("layout_mm") or ctx.extras.get("extent_mm")
    names = [n for n in ctx.views if n in ("front", "top", "right")]
    iso = "isometric" in ctx.extras.get("views_extra", {}) or len(ctx.extras.get("view_boxes", [])) > len(names)
    if extent is None or ctx.assembly is not None or "front" not in names or not iso:
        return None
    w, d, h = (float(v) for v in extent)
    layout = sheet_layout.plan(
        w, d, h, ctx.sheet.width, ctx.sheet.height, None, None, True, "top" in names, "right" in names,
        third=ctx.extras.get("angle") == "third", iso_dims=(w, d, h),
    )  # fmt: skip
    if layout.iso_scale > 0 and layout.scale > scale * (1 + 1e-9):
        return layout.scale, layout.iso_scale
    return None


CROWD_TIP_MM = 6.0  # two callouts whose leaders touch the part this close are crowded at one spot
CROWD_ROOM_FACTOR = 4.0  # ... and there is room nearby when the view is this many times longer than that distance


def _crowded(data: ReviewInput) -> list[Finding]:
    """Two callouts of different items whose leaders end within ``CROWD_TIP_MM`` of each other on a view that is much longer than
    that (the thread callout and the chamfer callout both jammed at the end of a shaft while its length is free): move one
    along the feature (``offset_mm`` / ``side`` of the item), where there is room."""
    ctx = data.ctx
    leaders = [lead for lead in ctx.extras.get("leaders", []) if lead.get("item")]
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(leaders):
        for b in leaders[i + 1 :]:
            if a["item"] == b["item"] or tuple(sorted((a["item"], b["item"]))) in seen:
                continue
            gap = math.dist(a["tip"], b["tip"])
            if gap >= CROWD_TIP_MM:
                continue
            roomy = any(
                v.box[0] - 0.5 <= a["tip"][0] <= v.box[2] + 0.5
                and v.box[1] - 0.5 <= a["tip"][1] <= v.box[3] + 0.5
                and max(v.box[2] - v.box[0], v.box[3] - v.box[1]) >= CROWD_ROOM_FACTOR * CROWD_TIP_MM
                for v in ctx.views.values()
            )
            if not roomy:
                continue
            seen.add(tuple(sorted((a["item"], b["item"]))))
            text = ctx.say(
                f"Las llamadas {a['item']} y {b['item']} se amontonan en el mismo sitio ({ctx.num(round(gap, 1))} mm entre "
                "sus puntas) y la vista tiene sitio libre al lado: separa una a lo largo de la pieza (offset_mm o side).",
                f"Callouts {a['item']} and {b['item']} are crowded at the same spot ({ctx.num(round(gap, 1))} mm between "
                "their tips) and the view has free room beside it: move one along the part (offset_mm or side).",
            )
            out.append(Finding("callouts_crowded", "warning", text, (str(a["item"]), str(b["item"])), key=f"{a['item']},{b['item']}"))
    return out


def _no_isometric(data: ReviewInput) -> list[Finding]:
    """A manufacturing sheet carries the isometric view (in colour by the harness's convention): it is how the shop sees the
    part at a glance. A sheet made without it (``views=[...]`` with no ``iso``, or ``isometric=false``) is flagged; the engineer
    may order it so."""
    ctx = data.ctx
    shown = ctx.extras.get("sheet_views")
    if shown is None or "iso" in shown:
        return []
    text = ctx.say(
        "La hoja no lleva vista isométrica: todo plano de fabricación la lleva (en color, por la convención del arnés). "
        'Añádela: drawing(action=create, views=[..., "iso"]) o sin views para que el arnés elija; solo falta si el ingeniero '
        "lo ordena (by=, reason=).",
        "The sheet has no isometric view: every manufacturing sheet carries it (in colour, by the harness's convention). "
        'Add it: drawing(action=create, views=[..., "iso"]) or leave views out so the harness chooses; it is missing only '
        "if the engineer orders it (by=, reason=).",
    )
    return [Finding("no_isometric", "warning", text, (), key="iso")]


def _underused(data: ReviewInput) -> list[Finding]:
    """The views and their dimensions cover too little of the free drawing area (a larger standard scale or a smaller paper
    holds them), or sit in a corner with most of the room on one side. ``sheet_underused`` is a measurement WITH a remedy (a
    larger standard scale, a smaller paper, fewer views) and holds the export until it is applied or the engineer orders
    otherwise; the same gap with no remedy (the automatic layout, nothing larger fits) is ``sheet_unbalanced``, a notice
    that can be answered."""
    import sheet_layout  # noqa: PLC0415 - the paper sizes and the standard scales
    from sheet_model import fmt_scale  # noqa: PLC0415

    ctx = data.ctx
    scale = float(ctx.extras.get("main_scale") or 0.0)
    rects = _footprint(ctx)
    if not rects or scale <= 0:
        return []
    area = (ctx.area[2] - ctx.area[0], ctx.area[3] - ctx.area[1])
    papers = {
        name: (r - left, bottom - top)
        for name, (pw, ph) in sheet_layout.PAGES_MM.items()
        if name != "Letter"
        for left, top, r, bottom in [sheet_layout.drawing_area(pw, ph)]
    }
    found = rules.sheet_underused(
        rects,
        ctx.area,
        scale,
        area,
        corner=ctx.assembly is None,
        scales=sheet_layout.STANDARD_SCALES,
        papers=papers,
        views=_view_boxes(ctx),
        fewer=_fewer_views(ctx),
        iso_own=_iso_own(ctx, scale),
    )
    if found is None:
        return []
    fix_es: list[str] = []
    fix_en: list[str] = []
    if found.scale is not None:
        fix_es.append(f"cabe una escala mayor (scale={ctx.num(found.scale)}, {fmt_scale(found.scale)})")
        fix_en.append(f"a larger scale fits (scale={ctx.num(found.scale)}, {fmt_scale(found.scale)})")
    if found.paper is not None:
        fix_es.append(f"cabe en una hoja menor (template={found.paper})")
        fix_en.append(f"it fits a smaller sheet (template={found.paper})")
    if found.iso_main is not None and found.iso_scale is not None:
        own_es, own_en = (
            f"con la isométrica a su propia escala ({fmt_scale(found.iso_scale)}, escrita bajo la vista) las vistas "
            f"principales caben a scale={ctx.num(found.iso_main)} ({fmt_scale(found.iso_main)}): pide esa escala y la "
            "isométrica se ajusta sola",
            f"with the isometric view at its own scale ({fmt_scale(found.iso_scale)}, written under the view) the main "
            f"views fit at scale={ctx.num(found.iso_main)} ({fmt_scale(found.iso_main)}): ask for that scale and the "
            "isometric view adjusts itself",
        )
        fix_es.append(own_es)
        fix_en.append(own_en)
    if found.fewer and found.fewer_scale is not None:
        fix_es.append(f"con menos vistas ({found.fewer}) cabe la escala {fmt_scale(found.fewer_scale)}")
        fix_en.append(f"with fewer views ({found.fewer}) the scale {fmt_scale(found.fewer_scale)} fits")
    if fix_es:
        advice = ctx.say("; ".join(fix_es), "; ".join(fix_en))
    else:
        advice = ctx.say(
            "la disposición es la automática y no cabe una escala estándar mayor ni una hoja menor: si el hueco es "
            "intencionado, responde con la razón (no aplica: <por qué>)",
            "the layout is the automatic one and no larger standard scale or smaller sheet fits: if the gap is "
            "deliberate, answer with the reason (not_applies: <why>)",
        )
    if found.reason == "small" and found.ink < rules.LOW_INK <= found.coverage:
        text = ctx.say(
            f"El bloque de vistas y cotas abarca la hoja ({ctx.num(found.block[0])} x {ctx.num(found.block[1])} mm de "
            f"{ctx.num(found.area[0])} x {ctx.num(found.area[1])} mm), pero las vistas en sí ocupan solo el "
            f"{ctx.num(round(found.ink * 100))} % del área de dibujo: la hoja desperdicia papel; {advice}.",
            f"The block of views and dimensions spans the sheet ({ctx.num(found.block[0])} x {ctx.num(found.block[1])} mm of "
            f"{ctx.num(found.area[0])} x {ctx.num(found.area[1])} mm), but the views themselves take only "
            f"{ctx.num(round(found.ink * 100))}% of the drawing area: the sheet wastes paper; {advice}.",
        )
    elif found.reason == "small":
        text = ctx.say(
            f"Las vistas y sus cotas ocupan solo el {ctx.num(round(found.coverage * 100))} % del área de dibujo "
            f"({ctx.num(found.block[0])} x {ctx.num(found.block[1])} mm de {ctx.num(found.area[0])} x {ctx.num(found.area[1])} mm): "
            f"la hoja desperdicia papel; {advice}.",
            f"The views and their dimensions take only {ctx.num(round(found.coverage * 100))}% of the drawing area "
            f"({ctx.num(found.block[0])} x {ctx.num(found.block[1])} mm of {ctx.num(found.area[0])} x {ctx.num(found.area[1])} mm): "
            f"the sheet wastes paper; {advice}.",
        )
    else:
        side_es = {"left": "a la izquierda", "right": "a la derecha", "above": "arriba", "below": "abajo"}[found.side]
        text = ctx.say(
            f"El bloque de vistas queda en una esquina: sobran {ctx.num(found.free_mm)} mm {side_es} y solo "
            f"{ctx.num(found.opposite_mm)} mm en el lado opuesto; {advice}.",
            f"The block of views sits in a corner: {ctx.num(found.free_mm)} mm are free on the {found.side} and only "
            f"{ctx.num(found.opposite_mm)} mm on the opposite side; {advice}.",
        )
    remedy = (
        found.scale is not None
        or found.paper is not None
        or bool(found.fewer and found.fewer_scale is not None)
        or found.iso_main is not None
    )
    return [Finding("sheet_underused" if remedy else "sheet_unbalanced", "warning", text, (), key=found.reason)]


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
    extents = ctx.extras.get("extent_dims", [])
    if not shapes and not extents:
        return []
    views = _views(ctx)
    outlines = _outlines(ctx, views)
    found = rules.dimension_side(shapes, outlines, {n: v.box for n, v in views.items()})
    types = {str(i["id"]): str(i.get("type")) for i in data.items}
    out: list[Finding] = []
    for g in rules.extent_gaps(extents, outlines):  # TechDraw's own overall width: its extension lines are not the item's
        text = ctx.say(
            f"La cota {g.owner} (overall) de la vista {g.view} está {_es(g.now)} y sus líneas de extensión suben desde las "
            f"esquinas inferiores de la vista y cuelgan {ctx.num(g.gap_mm)} mm sobre ninguna línea de la pieza (cruzan un hueco): "
            "parecen una arista que no existe. Una cota va del lado donde sus líneas no cruzan la vista: muévela "
            "(overall con side=below).",
            f"Dimension {g.owner} (overall) in the {g.view} view stands {g.now} and its extension lines rise from the lower "
            f"corners of the view and hang {ctx.num(g.gap_mm)} mm over no line of the part (they cross a gap): they look like "
            "an edge that does not exist. A dimension goes on the side where its lines do not cross the view: move it "
            "(overall with side=below).",
        )
        out.append(Finding("dimension_side", "warning", text, (g.owner,), key=g.owner))
    for f in found:
        name = f"{f.owner} ({types[f.owner]})" if f.owner in types else f.owner
        if f.reason == "length":
            text = ctx.say(
                f"La cota {name} de la vista {f.view} está {_es(f.now)} y sus líneas de extensión recorren {ctx.num(f.length_now)} mm "
                f"sobre la pieza; del lado {_es(f.better)} recorrerían {ctx.num(f.length_better)} mm (el rasgo está más cerca de ese "
                f"borde). Una cota va del lado más cercano al rasgo que mide: muévela (side={f.better}).",
                f"Dimension {name} in the {f.view} view stands {f.now} and its extension lines run {ctx.num(f.length_now)} mm over "
                f"the part; on the {f.better} side they would run {ctx.num(f.length_better)} mm (the feature is nearer that edge). "
                f"A dimension goes on the side nearest the feature it measures: move it (side={f.better}).",
            )
        else:
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


# --- an extension line across the dimension line of another dimension ------------------------------------------------------


def _side_of(dim: rules.DimShape, box: rules.Rect) -> str | None:
    """The side of its view a dimension stands on (``None``: inside the view or not axis-aligned)."""
    axis = rules._axis(dim)  # noqa: SLF001 - the same reading of a dimension as the rules
    if axis is None or dim.line_at is None:
        return None
    lo, hi = (box[1], box[3]) if axis == "h" else (box[0], box[2])
    if dim.line_at >= hi - rules.EDGE_MM:
        return "above" if axis == "h" else "right"
    if dim.line_at <= lo + rules.EDGE_MM:
        return "below" if axis == "h" else "left"
    return None


def _crossings(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    shapes: list[rules.DimShape] = ctx.extras.get("dim_shapes", [])
    if len(shapes) < 2:
        return []
    views = _views(ctx)
    types = {str(i["id"]): str(i.get("type")) for i in data.items}
    out: list[Finding] = []
    for f in rules.dimension_crossings(shapes):
        label = lambda k: f"{k} ({types[k]})" if k in types else k  # noqa: E731
        mine = next((d for d in shapes if d.owner == f.owner and d.view == f.view), None)
        view = views.get(f.view)
        free = (
            rules.free_side(mine, view.box, ctx.occupied, ctx.area) if mine is not None and view is not None else None
        )
        where = ctx.say(f"en [{ctx.num(f.at[0])}, {ctx.num(f.at[1])}]", f"at [{ctx.num(f.at[0])}, {ctx.num(f.at[1])}]")
        move = (
            ctx.say(
                f"Hay sitio libre del lado {_es(free)} (side={free}).",
                f"There is free room on the {free} side (side={free}).",
            )
            if free
            else ctx.say(
                "Pásala al otro lado de la vista (side=) si hay sitio, o ponla más cerca de la pieza que la cruzada (offset_mm=).",
                "Move it to the other side of the view (side=) if there is room, or put it nearer the part than the crossed one (offset_mm=).",
            )
        )
        if f.owner == f.other:
            who = ctx.say(
                f"una cota de {label(f.owner)} cruza otra suya",
                f"a dimension of {label(f.owner)} crosses another of its own",
            )
        else:
            who = ctx.say(
                f"las líneas de extensión de {label(f.owner)} cruzan la línea de cota de {label(f.other)}",
                f"the extension lines of {label(f.owner)} cross the dimension line of {label(f.other)}",
            )
        text = ctx.say(
            f"En la vista {f.view}, {who} {where}: una línea de extensión no cruza la línea de cota de otra cota (ISO 129). {move}",
            f"In the {f.view} view, {who} {where}: an extension line does not cross the dimension line of another dimension (ISO 129). {move}",
        )
        named = tuple(dict.fromkeys(k for k in (f.owner, f.other) if k in types))
        out.append(Finding("dimension_crossing", "warning", text, named, key=f"{f.owner}x{f.other}"))
    return out


# --- the datum symbol on a line of another item -----------------------------------------------------------------------------


def _datum_ink(owner: str, entities: Any) -> rules.DatumInk:
    triangle: tuple[tuple[float, float], ...] = ()
    frames: list[rules.Rect] = []
    lines: list[rules.Segment] = []
    for e in entities:
        if isinstance(e, Solid) and len(e.points) == 3:
            triangle = tuple(e.points)
        elif isinstance(e, Polyline) and e.closed and len(e.points) == 4:
            xs, ys = [p[0] for p in e.points], [p[1] for p in e.points]
            frames.append((min(xs), min(ys), max(xs), max(ys)))
        elif isinstance(e, Line):
            lines.append((e.x1, e.y1, e.x2, e.y2))
    return rules.DatumInk(owner, triangle, tuple(frames), tuple(lines))


def _datums(data: ReviewInput) -> list[Finding]:
    """A datum symbol of a face whose triangle, letter box or stem lies on an extension line, a dimension line or a leader of
    another item (the outline of the part it rests on is not one). Review only: the placement pass does not use it."""
    ctx = data.ctx
    specs = {str(i["id"]): i for i in data.items}
    items = [u for u in ctx.extras.get("units", []) if u.kind == KIND_ITEM]
    datums = [
        _datum_ink(u.key, u.entities)
        for u in items
        if u.item_type == "datum"
        and "on" not in specs.get(u.key, {})  # a datum on a callout stands on its shelf by design
    ]
    if not datums:
        return []
    others = {u.key: list(ink_of(u.key, u.entities).lines) for u in items if u.item_type != "datum"}
    types = {k: str(i.get("type")) for k, i in specs.items()}
    out: list[Finding] = []
    for f in rules.datum_on_line(datums, others):
        part = {
            "triangle": ctx.say("el triángulo", "the triangle"),
            "frame": ctx.say("el recuadro de la letra", "the lettered box"),
            "stem": ctx.say("el trazo que une triángulo y recuadro", "the stem joining triangle and box"),
        }[f.part]
        over = f"{f.over} ({types[f.over]})" if f.over in types else f.over
        text = ctx.say(
            f"En el datum {f.owner}, {part} cae sobre una línea de {over} (cerca de [{ctx.num(f.at[0])}, {ctx.num(f.at[1])}]): "
            "el símbolo no se apoya en líneas de cota ni de extensión de otro elemento, solo en el contorno de la pieza. "
            "Muévelo (offset_mm, o side= al lado donde no pasan líneas).",
            f"In datum {f.owner}, {part} lies on a line of {over} (near [{ctx.num(f.at[0])}, {ctx.num(f.at[1])}]): the symbol "
            "does not stand on the dimension or extension lines of another item, only on the outline of the part. "
            "Move it (offset_mm, or side= to a side where no lines run).",
        )
        out.append(
            Finding(
                "datum_on_line",
                "warning",
                text,
                (f.owner, f.over) if f.over in types else (f.owner,),
                key=f"{f.owner}x{f.over}",
            )
        )
    return out


def _unused_datums(data: ReviewInput) -> list[Finding]:
    """A ``datum`` item whose letter no ``gdt`` item cites in its ``datums``: a datum symbol means nothing on its own."""
    ctx = data.ctx
    declared = [(str(i["id"]), str(i["letter"])) for i in data.items if i.get("type") == "datum" and i.get("letter")]
    if not declared:
        return []
    cited = [list(i.get("datums") or ()) for i in data.items if i.get("type") == "gdt"]
    out: list[Finding] = []
    for f in rules.datum_unused(declared, cited):
        text = ctx.say(
            f"El datum {f.letter} ({f.owner}) no lo usa ningún marco de tolerancia: quítalo (drawing action=remove) o úsalo "
            "en un ítem gdt (datums=[...]). Un símbolo de datum no significa nada por sí solo.",
            f"Datum {f.letter} ({f.owner}) is not used by any tolerance frame: remove it (drawing action=remove) or use it in "
            "a gdt item (datums=[...]). A datum symbol means nothing on its own.",
        )
        out.append(Finding("datum_unused", "warning", text, (f.owner,), key=f.owner))
    return out


# --- the leader of a chamfer callout ---------------------------------------------------------------------------------------


def _chamfers(data: ReviewInput) -> list[Finding]:
    """The callout of a chamfer (``dim kind=chamfer``): its leader ends on the chamfer's own line, comes from outside the part
    and is close to perpendicular to that line. What the review has: the page point and the outward normal of the chamfer in its
    view that the item recorded (``ctx.extras["chamfer_marks"]``), the leader it placed (``ctx.extras["anchors"]``) and the
    outline of the view. A chamfer drawn by a geometry that gave no normal is not judged."""
    ctx = data.ctx
    marks: dict[str, tuple[str, rules_point, rules_point]] = ctx.extras.get("chamfer_marks", {})
    if not marks:
        return []
    views = _views(ctx)
    outlines = _outlines(ctx, views)
    out: list[Finding] = []
    for item, (view, point, normal) in marks.items():
        placed = ctx.extras.get("anchors", {}).get(item)
        if not placed:
            continue
        tip, elbow = placed[-1]["tip"], placed[-1]["elbow"]
        problems = rules.chamfer_leader_problems(tip, elbow, point, normal, outlines.get(view, []))
        if problems:
            why = "; ".join(
                {
                    "off_line": ctx.say(
                        "su extremo no cae en la línea del chaflán sino en otra arista (la cara lateral vecina)",
                        "its end is not on the chamfer's line but on another edge (the neighbouring side face)",
                    ),
                    "from_inside": ctx.say(
                        "llega desde el lado de la pieza, no desde fuera",
                        "it comes in from the part's side, not from outside",
                    ),
                    "oblique": ctx.say(
                        f"se desvía más de {ctx.num(rules.CHAMFER_ANGLE_DEG)}° de la normal del chaflán (debe ser casi perpendicular a su línea)",
                        f"it deviates more than {ctx.num(rules.CHAMFER_ANGLE_DEG)} degrees from the chamfer's normal (it must be close to perpendicular to its line)",
                    ),
                }[p]
                for p in problems
            )
            text = ctx.say(
                f"La llamada del chaflán {item} de la vista {view}: {why}. El extremo del directriz va al punto medio de la línea "
                "del chaflán, llegando desde fuera de la pieza, casi perpendicular a ella: quita side= para la posición "
                "por defecto o ajusta offset_mm.",
                f"The callout of chamfer {item} in the {view} view: {why}. The leader ends at the middle of the chamfer's line, "
                "coming from outside the part, close to perpendicular to it: drop side= for the default place or adjust offset_mm.",
            )
            out.append(Finding("leader_not_on_chamfer", "warning", text, (item,), key=item))
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
