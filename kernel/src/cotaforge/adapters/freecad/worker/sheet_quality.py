# ruff: noqa: E501
"""The quality floor of a drawing, as review findings (no FreeCAD import): what a shop-floor reader would send back that the
sheet's own placement should have prevented.

* leaders: two callouts whose leaders cross, a leader that goes through the part more than once, a leader that crosses a
  dimension line or an extension line (ISO 128-22: a leader crosses nothing but the outline of the part, once);
* dimensions: a dimension line or its text inside the outline of a view;
* positions: a hole located in one direction only (a lone "30"); a hole is located by a dimension, by a datum the default
  location counts from or by the part's own symmetry (a hole on a plane of symmetry needs no number there);
* the same dimension in two views (ISO 129);
* dowel holes that look like registration holes and are not marked;

``sheet_review.review`` calls ``check`` after its own checks; it only adds to the list (the findings of the other modules are
untouched).
"""

import re
from typing import Any

import hole_class
import iso286
import items_holes
import sheet_checks
import sheet_pattern
import sheet_tol
from items_common import view_segments
from items_dims import AXES
from sheet_collide import Segment, ink_of, segments_cross
from sheet_context import Context, HoleInfo, ItemError
from sheet_review import Finding, ReviewInput, _hole_of_ref, _measure, _of, ask_reference

OUTLINE_SLACK_MM = 0.5  # a dimension this close to the outline is on it, not inside it
MAX_LEADER_CUTS = 1  # a leader goes through the outline once: from the feature to the outside


def check(data: ReviewInput) -> list[Finding]:
    """Every finding of this module."""
    found: list[Finding] = []
    for part in (
        _leaders,
        _dimensions_inside,
        _partly_located,
        _patterns,
        _dowels,
        _fit_differs,
        _single_view,
        _repeated_dimensions,
    ):
        found += part(data)
    return found


# --- leaders ------------------------------------------------------------------------------------------------------------


def _parts(leader: dict[str, object]) -> list[Segment]:
    tip, elbow, end = leader["tip"], leader["elbow"], leader["end"]
    return [(tip[0], tip[1], elbow[0], elbow[1]), (elbow[0], elbow[1], end[0], end[1])]  # type: ignore[index]


def _leaders(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    leaders = ctx.extras.get("leaders", [])
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(leaders):
        for b in leaders[i + 1 :]:
            if any(segments_cross(x, y) for x in _parts(a) for y in _parts(b)):
                ids = tuple(dict.fromkeys(str(k["item"]) for k in (a, b) if k.get("item")))
                if (ids[0], ids[-1]) in seen:
                    continue
                seen.add((ids[0], ids[-1]))
                text = ctx.say(
                    f"Las guías de {' y '.join(ids)} se cruzan: mueve una (side u offset_mm) para que no se crucen.",
                    f"The leaders of {' and '.join(ids)} cross: move one (side or offset_mm) so they do not.",
                )
                out.append(Finding("leader_crossing", "warning", text, ids, key="+".join(ids)))
    out += _leaders_across_dimensions(data, leaders)
    if not ctx.views:
        return out
    outline = view_segments(ctx, next(iter(ctx.views.values())))
    for leader in leaders:
        tip, elbow = leader["tip"], leader["elbow"]
        cuts = sum(1 for s in outline if segments_cross((tip[0], tip[1], elbow[0], elbow[1]), s))  # type: ignore[index]
        if cuts > MAX_LEADER_CUTS and leader.get("item"):
            text = ctx.say(
                f"La guía de {leader['item']} atraviesa el contorno de la pieza {cuts} veces: debe cruzarlo una sola vez "
                "(muévela con side u offset_mm hacia un lado libre).",
                f"The leader of {leader['item']} goes through the outline of the part {cuts} times: it may cross it once "
                "(move it with side or offset_mm to a free side).",
            )
            out.append(Finding("leader_crosses_part", "warning", text, (str(leader["item"]),), key=str(leader["item"])))
    return out


def _leaders_across_dimensions(data: ReviewInput, leaders: list[dict[str, Any]]) -> list[Finding]:
    """A leader that goes through a dimension line or an extension line (of any dimension or datum of the sheet): the shop
    reads the callout as pointing at the dimension. One finding per callout, naming it and the items it crosses. Only on a
    sheet the global layout placed (``layout_planned``): the callouts of a sheet built item by item may cross a dimension line
    when they must, and say so in their own row."""
    ctx = data.ctx
    if not ctx.extras.get("layout_planned"):
        return []
    kinds = {str(i["id"]) for i in data.items if i.get("type") in ("datum_dims", "overall", "datum") or _linear_dim(i)}
    dimensions = [
        (unit.key, ink_of(unit.key, unit.entities).lines)
        for unit in ctx.extras.get("units", [])
        if unit.kind == "item" and unit.key in kinds and unit.entities
    ]
    out: list[Finding] = []
    seen: set[str] = set()
    for leader in leaders:
        item = str(leader.get("item") or "")
        if not item or item in seen:
            continue
        crossed = [
            key
            for key, lines in dimensions
            if key != item and any(segments_cross(part, line) for part in _parts(leader) for line in lines)
        ]
        if not crossed:
            continue
        seen.add(item)
        ids = (item, *dict.fromkeys(crossed))
        text = ctx.say(
            f"La guía de {item} cruza una línea de cota o de extensión de {', '.join(ids[1:])}: una guía no cruza cotas "
            "(ISO 128-22). Mueve la llamada (side, offset_mm o leader_mm) o la cota (offset_mm) para que quede un camino libre.",
            f"The leader of {item} crosses a dimension line or an extension line of {', '.join(ids[1:])}: a leader crosses no "
            "dimension (ISO 128-22). Move the callout (side, offset_mm or leader_mm) or the dimension (offset_mm) to leave a clear way.",
        )
        out.append(Finding("leader_crosses_dimension", "warning", text, ids, key="+".join(ids)))
    return out


# --- dimensions ---------------------------------------------------------------------------------------------------------


def _dimensions_inside(data: ReviewInput) -> list[Finding]:
    """A dimension line or the text of a linear dimension lying inside the outline of a view: dimensions go outside the part
    (extension lines come from it, leaders reach into it; the dimension itself is outside)."""
    ctx = data.ctx
    kinds = {str(i["id"]): i for i in data.items if i.get("type") in ("datum_dims", "overall") or _linear_dim(i)}
    boxes = [(name, v.box) for name, v in ctx.views.items()]
    out: list[Finding] = []
    for unit in ctx.extras.get("units", []):
        item = kinds.get(unit.key)
        if item is None or unit.kind != "item":
            continue
        ink = ink_of(unit.key, unit.entities)
        for name, box in boxes:
            inner = (
                box[0] + OUTLINE_SLACK_MM,
                box[1] + OUTLINE_SLACK_MM,
                box[2] - OUTLINE_SLACK_MM,
                box[3] - OUTLINE_SLACK_MM,
            )
            lines = [s for s in ink.dimension_lines if _inside(s, inner)]
            texts = [
                t for t, _ in ink.texts if inner[0] < t[0] and t[2] < inner[2] and inner[1] < t[1] and t[3] < inner[3]
            ]
            if lines or texts:
                text = ctx.say(
                    f"{unit.key} ({item['type']}) queda dentro del contorno de la vista {name}: las cotas van fuera de la "
                    "pieza (side u offset_mm).",
                    f"{unit.key} ({item['type']}) lies inside the outline of the {name} view: dimensions go outside the "
                    "part (side or offset_mm).",
                )
                out.append(Finding("dimension_inside_outline", "warning", text, (unit.key,), key=unit.key))
                break
    return out


def _linear_dim(item: dict[str, object]) -> bool:
    return item.get("type") == "dim" and item.get("kind") == "distance"


def _inside(s: Segment, box: tuple[float, float, float, float]) -> bool:
    return all(box[0] < x < box[2] and box[1] < y < box[3] for x, y in ((s[0], s[1]), (s[2], s[3])))


# --- positions ----------------------------------------------------------------------------------------------------------


def _partly_located(data: ReviewInput) -> list[Finding]:
    """A hole some distance dimension reaches in one direction of its plane only (the "30" with nothing across): a machinist
    cannot place it. A ``datum_dims`` gives both; the default location (``loc``) gives both to the holes nobody else locates."""
    ctx = data.ctx
    if any(i.get("type") == "datum_dims" and not i.get("auto") and _ok(data, i) for i in data.items):
        full = _datum_located(data)
    else:
        full = set()
    axes_of: dict[int, set[str]] = {}
    for m in _measure(data):
        for ends in m.holes:
            for index in ends:
                axes_of.setdefault(index, set()).add(m.axis)
    if any(i.get("type") == "datum_dims" and i.get("auto") and _ok(data, i) for i in data.items):
        # the default location: the axes it positioned each hole along (a datum edge or the plane of symmetry)
        for index, axes in ctx.extras.get("loc_axes", {}).items():
            axes_of.setdefault(index, set()).update(axes)
    _aligned_centres(data, axes_of, full)
    chained = {f.key for f in sheet_checks._hole_references(data)}  # noqa: SLF001 - hole_no_reference says it already
    out: list[Finding] = []
    for hole in ctx.holes:
        if hole.index in full or hole.index not in axes_of or sheet_checks.hole_key([hole]) in chained:
            continue
        plane = _plane_axes(hole)
        if plane is None or plane <= axes_of[hole.index]:
            continue
        ids = tuple(str(s["id"]) for s in data.items if s.get("type") == "dim" and hole.index in _holes_of(ctx, s))
        missing = ", ".join(sorted(plane - axes_of[hole.index]))
        text = ctx.say(
            f"El agujero Ø{ctx.num(hole.diameter)} solo está ubicado en una dirección: falta su posición en {missing}.",
            f"The hole Ø{ctx.num(hole.diameter)} is located in one direction only: its position along {missing} is missing.",
        )
        text += ask_reference(ctx, {hole.index})
        out.append(Finding("hole_partly_located", "warning", text, ids, key=sheet_checks.hole_key([hole])))
    return out


ALIGNED_MM = 0.1  # two hole centres this close across a dimension share that coordinate


def _aligned_centres(data: ReviewInput, axes_of: dict[int, set[str]], full: set[int]) -> None:
    """A bolt pattern is dimensioned centre to centre: a distance between two holes along x also places the second one
    along y when their centres share y (a row) and the first is located along y -- the dimension runs along the row, and
    the row's position is the first hole's. Adds those axes to ``axes_of``, until nothing more follows, so a pattern
    located from one corner is not called partly located hole by hole."""
    ctx = data.ctx
    by_index = {h.index: h for h in ctx.holes}
    plane_of = {h.index: _plane_axes(h) for h in ctx.holes}

    def known(index: int) -> set[str]:
        return set(plane_of[index] or ()) if index in full else axes_of.get(index, set())

    links = [(m.axis, a, b) for m in _measure(data) for a in m.holes[0] for b in m.holes[1] if a != b]
    changed = True
    while changed:
        changed = False
        for axis, a, b in links:
            ha, hb = by_index.get(a), by_index.get(b)
            plane = plane_of.get(a)
            if ha is None or hb is None or not plane or plane != plane_of.get(b):
                continue
            for other in plane - {axis}:
                at = "xyz".index(other)
                if abs(ha.centre[at] - hb.centre[at]) > ALIGNED_MM:
                    continue
                for source, target in ((a, b), (b, a)):
                    if other in known(source) and other not in known(target) and target not in full:
                        axes_of.setdefault(target, set()).add(other)
                        changed = True


def _ok(data: ReviewInput, spec: dict[str, object]) -> bool:
    result = data.results.get(str(spec["id"]))
    return result is not None and result.status != "error"


def _datum_located(data: ReviewInput) -> set[int]:
    ctx = data.ctx
    located: set[int] = set()
    for spec in data.items:
        if spec.get("type") != "datum_dims" or spec.get("auto") or not _ok(data, spec):
            continue  # the default location gives each hole the axes it recorded (``loc_axes``), not both
        names = (str(spec.get("view", "top")),)
        wanted = spec.get("holes", "all")
        for name in names:
            view = ctx.views.get(name)
            if view is None:
                continue
            for h in ctx.holes:
                if view.looks_along(h.direction) and (wanted == "all" or any(h.on_axis(p) for p in wanted)):
                    located.add(h.index)
    return located


def _plane_axes(hole: HoleInfo) -> set[str] | None:
    """The two model axes across a hole's axis (``None`` for a hole that is not along x, y or z)."""
    for name, vector in AXES.items():
        if abs(abs(sum(a * b for a, b in zip(hole.direction, vector, strict=True))) - 1.0) < 1e-6:
            return {n for n in AXES if n != name}
    return None


def _holes_of(ctx: Context, spec: dict[str, object]) -> set[int]:
    out: set[int] = set()
    for end in ("from", "to"):
        ref = spec.get(end)
        if isinstance(ref, dict):
            out |= {h.index for h in _hole_of_ref(ctx, ref)}
    return out


# --- the same dimension twice --------------------------------------------------------------------------------------------


def _repeated_dimensions(data: ReviewInput) -> list[Finding]:
    """A size the sheet gives in two views (ISO 129: each dimension once, in the view that shows it best): two dimensions
    that say the same thing about the part -- the same length between the same two places along an axis, the same cylinder's
    diameter -- on different views. Only a suggestion: a shop reads both."""
    ctx = data.ctx
    said: dict[tuple[Any, ...], list[tuple[str, str]]] = {}
    for item, view, key in ctx.extras.get("dim_records", []):
        said.setdefault(tuple(key), []).append((item, view))
    out: list[Finding] = []
    for key, where in said.items():
        views = list(dict.fromkeys(view for _, view in where))
        if len(views) < 2:
            continue
        ids = tuple(dict.fromkeys(item for item, _ in where if item))
        if key[0] == "len":
            what = ctx.say(
                f"La cota {ctx.num(key[3] - key[2])} a lo largo de {key[1]}",
                f"The dimension {ctx.num(key[3] - key[2])} along {key[1]}",
            )
        else:
            what = ctx.say(f"El diámetro Ø{ctx.num(key[1])}", f"The diameter Ø{ctx.num(key[1])}")
        text = ctx.say(
            f"{what} está en dos vistas ({' y '.join(views)}; {', '.join(ids)}): ISO 129 pide cada cota una sola vez, en la "
            "vista que mejor la muestra. Quita una.",
            f"{what} is on two views ({' and '.join(views)}; {', '.join(ids)}): ISO 129 asks for each dimension once, in "
            "the view that shows it best. Remove one.",
        )
        out.append(Finding("dimension_repeated", "info", text, ids, key="/".join(str(k) for k in key)))
    return out


# --- dowel holes --------------------------------------------------------------------------------------------------------


def _dowels(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    for spec in _of(data, "hole_dim"):
        if "fit" not in spec or "dowel" in spec:
            continue
        try:
            holes = items_holes._select(ctx, spec)  # noqa: SLF001
        except ItemError:
            continue
        stacks = items_holes._stacks(holes)  # noqa: SLF001
        if stacks and all(items_holes.looks_like_dowel(spec, s) for s in stacks):
            text = ctx.say(
                f"{spec['id']}: agujeros pasantes con ajuste {spec['fit']}. Si son de pasador o de registro, márcalos con "
                "dowel=true (la llamada termina en «registro» y cada agujero lleva un anillo); si no, ignóralo.",
                f"{spec['id']}: through holes with fit {spec['fit']}. If they are dowel or registration holes, mark them "
                "with dowel=true (the callout ends in 'dowel' and each hole gets a ring); if not, ignore this.",
            )
            out.append(Finding("dowel_unmarked", "info", text, (str(spec["id"]),), key=str(spec["id"])))
    return out


# --- the fit of a callout against the hole -------------------------------------------------------------------------------


def _fit_differs(data: ReviewInput) -> list[Finding]:
    """A ``hole_dim`` with its own ``fit`` (or a ``tol``, read as precision when its zone is no wider than the finest
    precision grade allows) on a hole whose ``hole`` operation declared no fit, or one of another class
    (precision / plain): the colour of the hole in the model and in the isometric view says one thing and the callout
    another. Info: the engineer decides which one is right and declares ``fit`` on the hole operation."""
    ctx = data.ctx
    out: list[Finding] = []
    for spec in _of(data, "hole_dim"):
        if "fit" not in spec and "tol" not in spec:
            continue
        try:
            holes = items_holes._select(ctx, spec)  # noqa: SLF001
        except ItemError:
            continue
        for stack in items_holes._stacks(holes):  # noqa: SLF001
            first = stack[0]
            if not first.declared or first.thread is not None:
                continue
            if "fit" in spec:
                asked = hole_class.classify(threaded=False, fit=spec["fit"], max_precision_grade=first.precision_grade)
                wants = (ctx.say("ajuste", "fit"), str(spec["fit"]))
            else:
                upper, lower = sheet_tol.v_tol(spec["tol"])
                limit = first.precision_grade or hole_class.default_max_precision_grade()
                narrow = (upper - lower) * 1000 <= iso286.tolerance_um(limit, first.diameter)
                asked = "precision" if narrow else "plain"
                wants = (ctx.say("tolerancia", "tolerance"), f"tol {spec['tol']}")
            made = first.hole_class or hole_class.classify(
                threaded=False, fit=first.fit, max_precision_grade=first.precision_grade
            )
            if first.fit is not None and made == asked:
                continue
            declared = first.fit or ctx.say("ninguno", "none")
            text = ctx.say(
                f"{spec['id']}: la llamada pide {wants[0]} {wants[1]} y la operación hole del agujero declara {declared} "
                f"(clase {made}): el color del agujero y la llamada no coinciden. Declara fit en la operación hole o "
                "corrige la llamada.",
                f"{spec['id']}: the callout asks for {wants[0]} {wants[1]} and the hole operation declares {declared} "
                f"(class {made}): the colour of the hole and the callout disagree. Declare fit on the hole operation or "
                "correct the callout.",
            )
            key = f"{spec['id']}{sheet_checks.hole_key(stack)}"
            out.append(
                Finding(
                    "hole_fit_differs",
                    "info",
                    text,
                    (str(spec["id"]),),
                    ((first.feature,) if first.feature else ()),
                    key,
                )
            )
    return out


# --- views --------------------------------------------------------------------------------------------------------------


FLAT_RATIO = (
    0.35  # a part is a plate (or a flat part) when its thickness is at most this fraction of its other two sizes
)


def _single_view(data: ReviewInput) -> list[Finding]:
    """A flat part whose holes all go through its thickness and which has no other feature: one view says everything, and
    the thickness is a note ("19 de espesor"). Suggested while the sheet draws more than one standard view."""
    ctx = data.ctx
    extent = ctx.extras.get("extent_mm")
    standard = [name for name in ctx.views if name in ("front", "top", "right")]
    if extent is None or len(standard) < 2 or not ctx.holes or ctx.assembly is not None:
        return []
    thin = min(range(3), key=lambda i: extent[i])
    others = [e for i, e in enumerate(extent) if i != thin]
    if extent[thin] > FLAT_RATIO * min(others):
        return []
    axis = tuple(1.0 if i == thin else 0.0 for i in range(3))
    if not all(abs(sum(a * b for a, b in zip(h.direction, axis, strict=True))) > 1 - 1e-6 for h in ctx.holes):
        return []
    from sheet_review import _hole_like  # noqa: PLC0415 - a helper of the checks of that module

    if any(
        (f.kind in ("chamfer", "fillet") and not f.outline) or (f.kind == "cut" and not _hole_like(ctx, f)) for f in data.features
    ):
        return []
    view = next((v for v in standard if ctx.views[v].looks_along(axis)), None)
    if view is None:
        return []
    text = ctx.say(
        f"La pieza es plana ({ctx.num(extent[thin])} mm de espesor) y todos sus agujeros pasan a lo largo del espesor: la "
        f'vista {view} lo dice todo. Dibújala en UNA vista (create views=["{view}"], o quita las demás con remove_view): '
        "el espesor va como nota y la escala sube.",
        f"The part is flat ({ctx.num(extent[thin])} mm thick) and every hole goes through the thickness: the {view} view says "
        f'everything. Draw it in ONE view (create views=["{view}"], or take the others off with remove_view): the thickness '
        "goes as a note and the scale grows.",
    )
    return [Finding("single_view_enough", "info", text, tuple(n for n in standard if n != view), key=view)]


# --- circular patterns ----------------------------------------------------------------------------------------------------


def _pitch_in_note(data: ReviewInput, pattern: sheet_pattern.Pattern) -> bool:
    """Whether a note states the pitch circle of ``pattern`` (``Ø70``, ``PCD 70``, ``círculo de 70``): its number on the sheet
    only in words, where no one can measure or check it."""
    number = re.escape(f"{round(pattern.diameter, 2):g}").replace("\\.", "[.,]")
    wanted = r"(Ø|⌀|PCD|DPC|c[ií]rculo|circle)\s*(de\s+|of\s+)?" + number + r"(?![0-9])"
    return any(re.search(wanted, text, re.I) for text in sheet_checks._notes(data))  # noqa: SLF001 - the notes of the sheet


def _by_coordinates(data: ReviewInput, pattern: sheet_pattern.Pattern) -> tuple[str, ...]:
    """The items that locate holes of ``pattern`` by distances from an edge or a datum (X / Y) instead of from its axis."""
    ctx = data.ctx
    ids: list[str] = []
    for spec in data.items:
        kind = spec.get("type")
        if kind == "dim" and spec.get("kind") == "distance" and _holes_of(ctx, spec) & pattern.indexes:
            ids.append(str(spec["id"]))
        elif kind == "datum_dims" and not spec.get("auto") and _ok(data, spec):
            view = ctx.views.get(str(spec.get("view", "top")))
            wanted = spec.get("holes", "all")
            if view is not None and any(
                h.index in pattern.indexes and view.looks_along(h.direction) and (wanted == "all" or any(h.on_axis(p) for p in wanted))
                for h in ctx.holes
            ):
                ids.append(str(spec["id"]))
        elif kind == "datum_dims" and spec.get("auto") and _ok(data, spec) and pattern.indexes & set(ctx.extras.get("loc_axes", {})):
            ids.append(str(spec["id"]))
    return tuple(ids)


def _patterns(data: ReviewInput) -> list[Finding]:
    """A circular pattern of holes is located by its pitch circle diameter (a diameter dimension on the view that shows it) and,
    when its first hole is not on a centre line, the angle of that hole (ISO 129-1): never by the X / Y of each hole, and never
    with its pitch circle only in a note. The harness draws and dimensions every pattern by itself (``pattern_dim``), so this
    says what an item of the engineer, or a pattern taken off, left wrong. A pattern whose coordinates the intent declares
    functional is answered, not changed."""
    ctx = data.ctx
    out: list[Finding] = []
    for pattern in sheet_pattern.patterns_of(ctx):
        polar = ctx.extras.get("polar", {}).get(min(pattern.reps))
        ids = _by_coordinates(data, pattern)
        problems: list[str] = []
        label = f"{pattern.count}× Ø{ctx.num(round(pattern.diameter, 2))}"
        if ids:
            problems.append(ctx.say("sus agujeros se ubican por coordenadas X / Y", "its holes are located by X / Y coordinates"))
        if polar is None or not polar["diameter"]:
            note = _pitch_in_note(data, pattern)
            problems.append(
                ctx.say(
                    "el diámetro del círculo de paso está " + ("solo en una nota" if note else "sin acotar") + " (una cota Ø sobre la vista)",
                    "the pitch circle diameter is " + ("in a note only" if note else "not dimensioned") + " (a Ø dimension on the view)",
                )
            )
        elif polar["need"] > polar["given"]:
            problems.append(
                ctx.say(
                    "falta el ángulo del primer agujero respecto de una línea de centros (pattern_dim kind=angle)",
                    "the angle of the first hole from a centre line is missing (pattern_dim kind=angle)",
                )
            )
        if not problems:
            continue
        text = ctx.say(
            f"Patrón circular {label}: " + "; ".join(problems) + ". Se ubica con el diámetro del círculo de paso y el ángulo, no con "
            "X / Y (ISO 129-1): {\"type\":\"pattern_dim\",\"at\":[x,y,z]} (un punto del eje de uno de sus agujeros). Si la intención "
            "declara esas coordenadas funcionales, responde con esa razón.",
            f"Circular pattern {label}: " + "; ".join(problems) + ". It is located by its pitch circle diameter and angle, not by "
            "X / Y (ISO 129-1): {\"type\":\"pattern_dim\",\"at\":[x,y,z]} (a point on the axis of one of its holes). If the intent "
            "declares those coordinates functional, answer with that reason.",
        )
        out.append(Finding("pattern_not_polar", "warning", text, ids, key=f"@{min(pattern.reps)}"))
    return out
