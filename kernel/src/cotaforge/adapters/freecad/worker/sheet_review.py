# ruff: noqa: E501
"""``drawing(action=review)``: what a checker would find wrong with a sheet, deterministically and without an LLM (no
FreeCAD import).

The review reads the sheet the way it was just drawn: the stored items (with what each did), the holes and features of
the part, and the page rectangles the views took. Each finding has a ``code`` (stable, for tests and for the agent), a
severity (``warning`` is a likely drawing error, ``info`` something to confirm), a message in the sheet's language and the
ids of the items and features involved.

Findings: holes and cut features nobody dimensions or calls out, features whose position is not given, a distance given
twice (an ordinate set plus a chain between the same holes, a chain that closes on the overall size, the same pair twice),
threads (a threaded hole without its callout, holes whose thread is unknown), datums a GD&T frame cites and no datum item
defines, a geometric tolerance written as the text of a note instead of a frame, items that cannot be drawn (stale ids), and layout (views overlapping each other or the title block, text outside
the frame).
"""

import re
from dataclasses import dataclass, field
from typing import Any

import sheet_callout
import sheet_checks
import sheet_interface
import sheet_isoview
import sheet_thread
import sheet_weldrules
from items_assembly import rows_of
from items_dims import AXES, _along, coordinate, resolve_ref
from items_holes import _declared_thread as items_holes_declared
from items_holes import _select as select_callout_holes
from items_holes import _stacks
from sheet_context import Context, HoleInfo, ItemError, ItemResult, Rect, dot
from sheet_context import unit as unit_vector
from sheet_model import TITLE_BLOCK, Sheet, Text, text_width

SAME_MM = 0.1  # two positions are the same
OVERLAP_MM = 1.0  # views may touch; they may not intrude this far into each other
FRAME_MM = (20.0, 10.0, 10.0, 10.0)  # ISO 5457: left (binding margin), top, right, bottom
MAX_TEXT_FINDINGS = 3


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str  # warning | info
    message: str
    items: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    key: str = ""  # what tells two findings of one code apart (it is part of the id an answer names)


@dataclass
class FeatureInfo:
    """A feature of the part as the review needs it: ``kind`` (cut, chamfer, fillet...), the face ids it created."""

    id: str
    kind: str
    label: str
    faces: list[str]
    edges: list[str] = field(default_factory=list)


@dataclass
class ReviewInput:
    items: list[dict[str, Any]]
    results: dict[str, ItemResult]
    ctx: Context
    sheet: Sheet
    features: list[FeatureInfo] = field(default_factory=list)


def review(data: ReviewInput) -> list[Finding]:
    """Every finding, warnings first."""
    found: list[Finding] = []
    for check in (
        _item_errors,
        _hole_callouts,
        _feature_dimensions,
        _over_dimensioning,
        _datums,
        _gdt_as_note,
        _gdt_location_exact,
        _gdt_axis_oriented,
        _gdt_located,
        _view_lines,
        _unused_views,
        _collisions,
        _layout,
        _assembly_lists,
        _weldment_lists,
        _assembly_holes,
        _assembly_joints,
        _hidden_instances,
        sheet_checks.checks,  # the finished-drawing list (F6)
    ):
        found += check(data)
    import sheet_quality  # noqa: PLC0415 - it reads the types of this module (the quality floor of the sheet)

    found += sheet_quality.check(data)
    import sheet_viewchecks  # noqa: PLC0415 - the three drawing rules of the views and their dimensions

    found += sheet_viewchecks.check(data)
    return sorted(found, key=lambda f: 0 if f.severity == "warning" else 1)


# --- helpers ------------------------------------------------------------------------------------------------------------


def _collisions(data: ReviewInput) -> list[Finding]:
    """Every place the ink of one item lies on another's (a dimension under a tolerance frame, a note over a dimension
    line) or an item is off the drawing area: the placement pass moves what it can, so what is left here is a sheet a shop
    would send back. One finding per pair, naming the items."""
    ctx = data.ctx
    ids = {str(s["id"]) for s in data.items}
    types = {str(s["id"]): str(s["type"]) for s in data.items}
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for c in ctx.extras.get("collisions", []):
        if (c.a, c.b) in seen:
            continue
        seen.add((c.a, c.b))
        named = tuple(k for k in (c.a, c.b) if k in ids)
        label = lambda k: f"{k} ({types[k]})" if k in types else k  # noqa: E731
        if c.kind == "outside_frame":
            text = ctx.say(
                f"{label(c.a)} queda fuera de la zona de dibujo: muévelo (side, offset_mm o at_mm).",
                f"{label(c.a)} is outside the drawing area: move it (side, offset_mm or at_mm).",
            )
        else:
            room = _free_room(ctx, c.a)
            text = ctx.say(
                f"{label(c.a)} se solapa con {label(c.b)} ({c.what}): sepáralos (side, offset_mm o at_mm)."
                + (
                    f" Hay sitio libre del lado {_SIDE_ES[room[1]]} de la vista {room[0]} (side={room[1]})."
                    if room
                    else ""
                )
                + " Solo se acepta el solape si no hay sitio libre, y entonces se responde como decisión (no aplica: <por qué>).",
                f"{label(c.a)} collides with {label(c.b)} ({c.what}): separate them (side, offset_mm or at_mm)."
                + (f" There is free room on the {room[1]} of the {room[0]} view (side={room[1]})." if room else "")
                + " A collision is accepted only when no free place exists, and then it is answered as a deliberate choice (not_applies: <why>).",
            )
        out.append(Finding("collision", "warning", text, named))
    return out


_SIDE_ES = {"above": "de arriba", "below": "de abajo", "left": "izquierdo", "right": "derecho"}


def _free_room(ctx: Context, item: str) -> tuple[str, str] | None:
    """(view, side) where the dimension ``item`` could stand with nothing in the way, when the sheet shows such a place."""
    import sheet_viewrules  # noqa: PLC0415 - the rules of the views and their dimensions

    views = {**ctx.views, **ctx.extras.get("sections", {})}
    own = [d for d in ctx.extras.get("dim_shapes", []) if d.owner == item]
    for dim in own:
        view = views.get(dim.view)
        side = sheet_viewrules.free_side(dim, view.box, ctx.occupied, ctx.area) if view is not None else None
        if side is not None:
            return dim.view, side
    return None


# item types that give a view a job: they measure or mark something in it (a centre line, a note or a hidden-line pass
# do not), or they hang off it as the parent of a section / detail / auxiliary view (cutting line, detail circle)
CARRYING_TYPES = frozenset(
    {"dim", "overall", "hole_dim", "datum_dims", "balloons", "gdt", "datum", "finish", "section", "detail", "aux_view"}
)


def _unused_views(data: ReviewInput) -> list[Finding]:
    """Any view but the isometric that carries no dimension, hole callout or balloon (and is not the parent of a section
    or detail): a view with nothing to read in it is either missing its dimensions or not needed. An alert, not a
    refusal: the agent dimensions the view, removes it, or answers why it stays."""
    ctx = data.ctx
    types = {str(s["id"]): str(s.get("type")) for s in data.items}
    use = ctx.extras.get("view_use", {})
    sections = ctx.extras.get("sections", {})
    extra = [n for n in ctx.extras.get("views_extra", {}) if n != "isometric" and n not in ctx.views]
    if len(ctx.views) + len(sections) <= 1:  # a sheet of one view: it is the drawing
        return []
    out: list[Finding] = []
    by_hand = ctx.extras.get("hand_marks", {})  # {view: count} of dimensions / balloons the engineer put on the page
    for name in [*ctx.views, *sections, *extra]:
        if by_hand.get(name) or any(types.get(i) in CARRYING_TYPES for i in use.get(name, ())):
            continue
        owner = ctx.extras.get("section_items", {}).get(name, "")
        out.append(_view_without_job(ctx, name, name in sections, owner))
    return out


def _view_without_job(ctx: Context, name: str, is_section: bool, owner: str) -> Finding:
    """The finding for a view that carries nothing (``view_adds_nothing``), worded for the case: the end view of a turned part
    that only repeats circles the diameters already give, the section of an assembly (which may stay without a dimension only
    if it shows what the other views do not: how the parts fit, the fits), any other view."""
    turned = ctx.geometry.round_extent() if ctx.assembly is None and ctx.geometry is not None else None
    view = ctx.views.get(name)
    if is_section and owner:
        how = ctx.say(
            f"quita su elemento {owner} (drawing action=remove, ids=[{owner}])",
            f"remove its item {owner} (drawing action=remove, ids=[{owner}])",
        )
    else:
        how = ctx.say(
            f"quítala (drawing action=remove_view, view={name}) y las demás se reparten el espacio",
            f"drop it (drawing action=remove_view, view={name}) and the others share the room",
        )
    if turned is not None and view is not None and view.looks_along(turned[0]):
        text = ctx.say(
            f"La vista {name} es la vista de extremo de una pieza torneada y no lleva ninguna cota: solo repite círculos que "
            f"ya dan los Ø y la rosca de la vista principal, que lo lleva todo. {how[0].upper() + how[1:]}. Solo se queda si "
            "enseña lo que la principal no enseña (un chavetero visto de frente, caras planas, un patrón de agujeros en la "
            "cara): acótalo entonces; o el ingeniero lo ordena con su razón.",
            f"The {name} view is the end view of a turned part and carries no dimension: it only repeats circles that the "
            f"diameters and the thread of the main view already give, and the main view carries everything. {how[0].upper() + how[1:]}. "
            "It stays only if it shows what the main view does not (a keyway seen end-on, flats, a hole pattern on the face): "
            "dimension it then; or the engineer orders it with his reason.",
        )
    elif is_section and ctx.assembly is not None:
        text = ctx.say(
            f"El corte {name} no lleva ninguna cota. En un plano de conjunto un corte solo se queda sin cotas si enseña lo "
            "que las otras vistas no enseñan: el montaje (cómo encajan las piezas entre sí) o los ajustes. Acota el ajuste o "
            f"el espesor que muestra, o {how}; si enseña el montaje o los ajustes, el ingeniero lo ordena diciendo qué "
            "enseña (by=, reason=).",
            f"Section {name} carries no dimension. In an assembly sheet a section may stay without one only if it shows what "
            "the other views do not: the assembly (how the parts fit together) or the fits. Dimension the fit or the "
            f"thickness it shows, or {how}; if it shows the assembly or the fits, the engineer orders it saying what it "
            "shows (by=, reason=).",
        )
    else:
        text = ctx.say(
            f"La vista {name} no lleva ninguna cota, llamada de agujero ni globo: acótala (dim, overall, hole_dim) o {how}. "
            "Si debe quedarse así, responde con la razón (no aplica: <por qué>).",
            f"The {name} view carries no dimension, hole callout or balloon: dimension it (dim, overall, hole_dim) or {how}. "
            "If it must stay as it is, answer with the reason (not_applies: <why>).",
        )
    return Finding("view_adds_nothing", "warning", text, (name,))


def _ok(data: ReviewInput, spec: dict[str, Any]) -> bool:
    result = data.results.get(spec["id"])
    return result is not None and result.status != "error"


def _of(data: ReviewInput, kind: str) -> list[dict[str, Any]]:
    return [i for i in data.items if i.get("type") == kind and _ok(data, i)]


def _label(hole: HoleInfo, ctx: Context) -> str:
    c = ", ".join(ctx.num(v) for v in hole.centre)
    return f"Ø{ctx.num(hole.diameter)} [{c}]"


def _hole_of_ref(ctx: Context, ref: dict[str, Any]) -> list[HoleInfo]:
    """The holes a dimension reference stands for (the axis through ``at``, a hole wall face, a circular edge)."""
    if "at" in ref:
        return [h for h in ctx.holes if h.on_axis(tuple(ref["at"]))]  # type: ignore[arg-type]
    if "face" in ref:
        return [h for h in ctx.holes if ref["face"] in h.faces]
    try:
        edge = ctx.geometry.edge(ref["edge"])
    except ItemError:
        return []
    if edge.kind == "circle" and edge.centre is not None:
        return [h for h in ctx.holes if h.on_axis(edge.centre)]
    return []


def _distance_dims(data: ReviewInput) -> list[dict[str, Any]]:
    return [i for i in _of(data, "dim") if i.get("kind") == "distance"]


def _located_holes(data: ReviewInput) -> set[int]:
    """The holes some ``datum_dims`` or distance dimension positions."""
    ctx = data.ctx
    located: set[int] = set()
    for spec in _of(data, "datum_dims"):
        if spec.get("auto"):
            # the default location positions a hole along the axes it recorded (a datum edge or a plane of symmetry);
            # one it could not reference in either direction is not located, one it did in a single direction is
            # partly located (``hole_partly_located`` says which direction is missing)
            located |= set(ctx.extras.get("loc_axes", {}))
            continue
        for name in (spec.get("view", "top"),):
            view = ctx.views.get(name)
            if view is None:
                continue
            wanted = spec.get("holes", "all")
            for h in ctx.holes:
                if view.looks_along(h.direction) and (wanted == "all" or any(h.on_axis(p) for p in wanted)):
                    located.add(h.index)
    for spec in _distance_dims(data):
        for end in ("from", "to"):
            for h in _hole_of_ref(ctx, spec[end]):
                located.add(h.index)
    return located


def ask_reference(ctx: Context, indexes: set[int]) -> str:
    """What to add to a finding about holes whose position the default location could not reference: the axes with no
    reference (the part is not symmetric across them and no datum item faces across them) and who decides."""
    missing = ctx.extras.get("loc_missing", {})
    axes = sorted({a for i in indexes for a in missing.get(i, ())})
    if not axes:
        return ""
    where = ", ".join(axes)
    return ctx.say(
        f" Falta una referencia en {where}: la pieza no es simétrica en ese sentido y ningún datum la cubre. Pregunta al "
        "ingeniero desde qué cara o arista se cuentan esas posiciones, declárala con un ítem datum (face) y la ubicación "
        "por defecto la usa.",
        f" No reference along {where}: the part is not symmetric across it and no datum covers it. Ask the engineer which "
        "face or edge those positions are counted from, declare it with a datum item (face) and the default location "
        "uses it.",
    )


def _located_faces(data: ReviewInput) -> set[str]:
    """The faces some distance dimension reaches."""
    return {spec[end]["face"] for spec in _distance_dims(data) for end in ("from", "to") if "face" in spec[end]}


# --- items that cannot be drawn --------------------------------------------------------------------------------------------


def _item_errors(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    for spec in data.items:
        result = data.results.get(spec["id"])
        if result is None or result.status != "error":
            continue
        why = result.detail
        if spec["type"] == "gdt" and "is not on this sheet (datums drawn" in why:
            continue  # reported as datum_undefined
        if re.search(r"is not a (face|edge) of the part|not the wall of a hole", why):
            code = "stale_reference"
            text = ctx.say(
                f"{spec['id']} ({spec['type']}) cita una cara o arista que la pieza ya no tiene: {why}. Vuelve a "
                "añadirlo con el id actual.",
                f"{spec['id']} ({spec['type']}) cites a face or edge the part no longer has: {why}. Add it again with "
                "the current id.",
            )
        elif spec["type"] in ("dim", "hole_dim", "datum_dims") and re.search(
            r"not on the part|no hole has its axis", why
        ):
            code = "dim_no_reference"
            text = ctx.say(
                f"{spec['id']} ({spec['type']}) no apunta a geometría de la pieza: {why}",
                f"{spec['id']} ({spec['type']}) points at no geometry of the part: {why}",
            )
        else:
            code = "item_error"
            text = ctx.say(
                f"{spec['id']} ({spec['type']}) no se puede dibujar: {why}",
                f"{spec['id']} ({spec['type']}) cannot be drawn: {why}",
            )
        out.append(Finding(code, "warning", text, (spec["id"],)))
    return out


# --- holes ----------------------------------------------------------------------------------------------------------------


def _callout_text_findings(ctx: Context, spec: dict[str, Any], holes: list[HoleInfo]) -> list[Finding]:
    stacks = _stacks(holes)
    try:
        stack = items_holes_declared(ctx, spec, stacks[0])
    except ItemError:
        return []  # the item itself said why it cannot be drawn
    said = sheet_callout.problems(ctx, str(spec["text"]), stack, len(stacks))
    if not said:
        return []
    text = ctx.say(
        f"{spec['id']} (hole_dim): el texto de la llamada no concuerda con el agujero medido: {'; '.join(said)}.",
        f"{spec['id']} (hole_dim): the callout text does not agree with the measured hole: {'; '.join(said)}.",
    )
    return [Finding("callout_contradicts", "warning", text, (spec["id"],), (), spec["id"])]


def _hole_callouts(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    called: dict[int, list[str]] = {}
    for spec in _of(data, "hole_dim"):
        try:
            holes = select_callout_holes(ctx, spec)
        except ItemError:
            continue
        for h in holes:
            called.setdefault(h.index, []).append(spec["id"])
        if "text" in spec:  # the engineer's own callout: what it states must agree with the measured hole
            out += _callout_text_findings(ctx, spec, holes)
    located = _located_holes(data)
    for stack in _stacks(list(ctx.holes)):
        indexes = {h.index for h in stack}
        features = tuple(dict.fromkeys(h.feature for h in stack if h.feature))
        name = " + ".join(_label(h, ctx) for h in stack)
        threaded = any(h.thread for h in stack)
        if not indexes & called.keys():
            if threaded:
                text = ctx.say(
                    f"Agujero roscado {name} sin llamada: falta la rosca (hole_dim lo cubre).",
                    f"Threaded hole {name} has no callout: the thread is missing (hole_dim covers it).",
                )
                out.append(Finding("thread_no_callout", "warning", text, (), features, sheet_checks.hole_key(stack)))
            else:
                text = ctx.say(
                    f"Agujero {name} sin llamada de diámetro y profundidad (hole_dim).",
                    f"Hole {name} has no callout (hole_dim).",
                )
                out.append(Finding("hole_no_callout", "warning", text, (), features, sheet_checks.hole_key(stack)))
        else:
            ids = tuple(dict.fromkeys(i for n in indexes for i in called.get(n, [])))
            taps = [h for h in stack[:1] if not h.declared and sheet_thread.tap_drill_thread(h.diameter)]
            if (
                taps
            ):  # only a hole of a tap-drill diameter no hole operation made may be threaded: never a clearance hole
                size = sheet_thread.tap_drill_thread(taps[0].diameter)
                text = ctx.say(
                    f"Agujero {name}: su diámetro es el del taladro de {size} y ninguna operación hole lo hizo (rosca no "
                    "declarada); si lleva rosca, añade una nota.",
                    f"Hole {name}: its diameter is the tap drill of {size} and no hole operation made it (thread not "
                    "declared); if it is threaded, add a note.",
                )
                out.append(Finding("thread_undeclared", "info", text, ids, features, sheet_checks.hole_key(stack)))
        if not indexes & located:
            text = ctx.say(
                f"Agujero {name} sin posición: ninguna cota de datum_dims ni de distancia lo ubica, ni la simetría de la pieza.",
                f"Hole {name} is not located: no datum_dims or distance dimension positions it, nor the symmetry of the part.",
            )
            text += ask_reference(ctx, indexes)
            out.append(
                Finding(
                    "hole_not_located",
                    "warning",
                    text,
                    tuple(dict.fromkeys(i for n in indexes for i in called.get(n, []))),
                    features,
                    sheet_checks.hole_key(stack),
                )
            )
    return out


# --- features -------------------------------------------------------------------------------------------------------------


def _hole_like(ctx: Context, feature: FeatureInfo) -> bool:
    """A cut that only made a hole (its cylinder walls are hole walls): the hole checks cover it."""
    wall_ids = {f for h in ctx.holes for f in h.faces}
    kinds = []
    for face_id in feature.faces:
        try:
            kinds.append((face_id, ctx.geometry.face(face_id).kind))
        except ItemError:
            continue
    cylinders = [f for f, k in kinds if k == "cylinder"]
    others = [f for f, k in kinds if k != "cylinder"]
    return bool(cylinders) and all(f in wall_ids for f in cylinders) and len(others) <= 1


def _touching(ctx: Context, feature: FeatureInfo, ref: dict[str, Any]) -> bool:
    if "face" in ref:
        return ref["face"] in feature.faces
    if "edge" in ref:
        return ref["edge"] in feature.edges
    return bool(ctx.geometry.faces_at(tuple(ref["at"]), feature.faces))  # type: ignore[arg-type]


def _feature_dimensions(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    dims = _of(data, "dim")
    for feature in data.features:
        if feature.kind not in ("cut", "chamfer", "fillet") or not feature.faces:
            continue
        if feature.kind == "cut" and _hole_like(ctx, feature):
            continue
        size: list[str] = []
        place: list[str] = []
        for spec in dims:
            kind = spec.get("kind")
            if kind in ("radius", "chamfer", "diameter"):
                if spec["face"][0] in feature.faces:
                    size.append(spec["id"])
            elif kind == "angle":
                if any(f in feature.faces for f in spec["faces"]):
                    size.append(spec["id"])
            elif kind == "distance":
                ends = [_touching(ctx, feature, spec[e]) for e in ("from", "to")]
                if all(ends):
                    size.append(spec["id"])
                elif any(ends):
                    place.append(spec["id"])
        if feature.kind in ("chamfer", "fillet"):
            noted = any(
                re.search(r"\bR\s*\d|chafl|chamfer|radio|fillet", str(n.get("text", "")), re.I)
                for n in _of(data, "note")
            )
            if not size and not noted:
                text = ctx.say(
                    f"{feature.kind} {feature.id} sin cota ni nota (radio o chaflán): acótalo o cubre con una nota "
                    f"general.",
                    f"{feature.kind} {feature.id} has no dimension or note: dimension it or cover it with a general "
                    f"note.",
                )
                if feature.kind == "chamfer":  # the harness measures a chamfer and dimensions it itself (``chamfer_dims``)
                    out.append(Finding("chamfer_no_dimension", "info", text, (), (feature.id,)))
                else:
                    out.append(Finding("feature_no_dimension", "info", text, (), (feature.id,)))
            continue
        if not size and not place:
            text = ctx.say(
                f"Rasgo {feature.id} ({feature.label}) sin cota: ni tamaño ni posición.",
                f"Feature {feature.id} ({feature.label}) has no dimension: neither size nor position.",
            )
            out.append(Finding("feature_no_dimension", "warning", text, (), (feature.id,)))
        elif not place:
            text = ctx.say(
                f"Rasgo {feature.id} ({feature.label}): tiene tamaño ({', '.join(size)}) pero ninguna cota lo ubica.",
                f"Feature {feature.id} ({feature.label}) has a size ({', '.join(size)}) but no dimension positions it.",
            )
            out.append(Finding("feature_not_located", "warning", text, tuple(size), (feature.id,)))
    return out


# --- over-dimensioning ---------------------------------------------------------------------------------------------------


@dataclass
class _Measured:
    spec: dict[str, Any]
    axis: str
    lo: float
    hi: float
    holes: tuple[set[int], set[int]]


def _measure(data: ReviewInput) -> list[_Measured]:
    ctx = data.ctx
    out: list[_Measured] = []
    for spec in _distance_dims(data):
        try:
            a, b = resolve_ref(ctx, spec["from"]), resolve_ref(ctx, spec["to"])
            name = _along(ctx, a, b, spec.get("along"))
            ca, cb = coordinate(a, AXES[name], name), coordinate(b, AXES[name], name)
        except ItemError:
            continue
        ends = (
            {h.index for h in _hole_of_ref(ctx, spec["from"])},
            {h.index for h in _hole_of_ref(ctx, spec["to"])},
        )
        out.append(_Measured(spec, name, min(ca, cb), max(ca, cb), ends))
    return out


def _extent(ctx: Context, axis: str) -> float | None:
    """The overall size of the part along a model axis, from the views' boxes."""
    vector = AXES[axis]
    for view in ctx.views.values():
        ax, ay = view.axis(vector)
        width, height = (view.box[2] - view.box[0]) / view.scale, (view.box[3] - view.box[1]) / view.scale
        if abs(ax) > 0.99:
            return width
        if abs(ay) > 0.99:
            return height
    return None


def _over_dimensioning(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    measured = _measure(data)
    # 1. the same distance twice
    for i, m in enumerate(measured):
        for n in measured[i + 1 :]:
            if m.axis == n.axis and abs(m.lo - n.lo) < SAME_MM and abs(m.hi - n.hi) < SAME_MM:
                text = ctx.say(
                    f"La misma distancia ({ctx.num(m.hi - m.lo)} mm a lo largo de {m.axis}) está acotada dos veces: "
                    f"{m.spec['id']} y {n.spec['id']}.",
                    f"The same distance ({ctx.num(m.hi - m.lo)} mm along {m.axis}) is dimensioned twice: "
                    f"{m.spec['id']} and {n.spec['id']}.",
                )
                out.append(Finding("duplicate_dimension", "warning", text, (m.spec["id"], n.spec["id"])))
    # 1b. a dimension that repeats an overall extent
    for overall in _of(data, "overall"):
        for m in measured:
            extent = _extent(ctx, m.axis)
            if extent is not None and abs((m.hi - m.lo) - extent) < SAME_MM * 2:
                text = ctx.say(
                    f"{m.spec['id']} repite la cota total en {m.axis} ({ctx.num(extent)} mm) que ya da {overall['id']}.",
                    f"{m.spec['id']} repeats the overall size along {m.axis} ({ctx.num(extent)} mm) that "
                    f"{overall['id']} already gives.",
                )
                out.append(Finding("duplicate_dimension", "warning", text, (overall["id"], m.spec["id"])))
    # 2. an ordinate set / table plus a dimension between two of its holes
    for table in _of(data, "datum_dims"):
        view = ctx.views.get(table.get("view", "top"))
        if view is None:
            continue
        wanted = table.get("holes", "all")
        covered = {
            h.index
            for h in ctx.holes
            if view.looks_along(h.direction) and (wanted == "all" or any(h.on_axis(p) for p in wanted))
        }
        if table.get("auto"):  # the default location only located the holes no other item does
            covered &= set(ctx.extras.get("auto_covered", ()))
        for m in measured:
            if (
                m.holes[0]
                and m.holes[1]
                and m.holes[0] <= covered
                and m.holes[1] <= covered
                and m.holes[0] != m.holes[1]
            ):
                text = ctx.say(
                    f"{m.spec['id']} acota la distancia entre dos agujeros que {table['id']} ya ubica desde el datum: "
                    "sobreacotado (cadena + ordenadas).",
                    f"{m.spec['id']} dimensions the distance between two holes that {table['id']} already locates from "
                    "the datum: over-dimensioned (chain + ordinates).",
                )
                out.append(Finding("over_dimensioned", "warning", text, (table["id"], m.spec["id"])))
    # 3. a chain that closes on the overall size
    if _of(data, "overall"):
        for axis in AXES:
            overall = _extent(ctx, axis)
            same = sorted((m for m in measured if m.axis == axis), key=lambda m: m.lo)
            if overall is None or len(same) < 2:
                continue
            chain = [same[0]]
            for m in same[1:]:
                if abs(m.lo - chain[-1].hi) < SAME_MM:
                    chain.append(m)
            if len(chain) >= 2 and abs(sum(m.hi - m.lo for m in chain) - overall) < SAME_MM * 2:
                ids = tuple(m.spec["id"] for m in chain)
                text = ctx.say(
                    f"Las cotas {', '.join(ids)} forman una cadena que suma la cota total en {axis} "
                    f"({ctx.num(overall)} mm): sobreacotado; quita una.",
                    f"Dimensions {', '.join(ids)} form a chain that adds up to the overall size along {axis} "
                    f"({ctx.num(overall)} mm): over-dimensioned; remove one.",
                )
                out.append(Finding("over_dimensioned", "warning", text, ids))
    return out


# --- datums ---------------------------------------------------------------------------------------------------------------


def _letters(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(re.findall(r"[A-Z]", value.upper())) if len(value) <= 12 else set()
    if isinstance(value, list | tuple):
        return {c for v in value for c in _letters(v)}
    return set()


LOCATED_CHARS = ("position", "profile_line", "profile_surface")


def _gdt_features(data: ReviewInput, frame: dict[str, Any]) -> tuple[str, bool]:
    """(what the frame tolerates, whether a dimension locates it): the holes of the callout it hangs from, or the face
    it points at."""
    ctx = data.ctx
    if "on" in frame:
        target = next((i for i in data.items if i["id"] == frame["on"]), None)
        if target is None:
            return frame["on"], True  # the frame itself is an error then
        if target.get("type") == "hole_dim":
            try:
                holes = select_callout_holes(ctx, target)
            except ItemError:
                return frame["on"], True
            located = _located_holes(data)
            name = " + ".join(_label(h, ctx) for h in holes)
            return f"{name} ({frame['on']})", all(h.index in located for h in holes)
        face = (target.get("face") or [""])[0]
        return f"{face} ({frame['on']})", face in _located_faces(data)
    face = frame["face"][0]
    return face, face in _located_faces(data)


def _gdt_located(data: ReviewInput) -> list[Finding]:
    """A position or profile tolerance is measured from the datums: the feature must be located from them by a
    dimension (a ``datum_dims`` ordinate or a ``dim`` distance). Theoretically exact (boxed) dimensions are not an item
    yet, so the located dimension here is the plain one."""
    out: list[Finding] = []
    for frame in (i for i in _of(data, "gdt") if i.get("char") in LOCATED_CHARS):
        feature, located = _gdt_features(data, frame)
        if not located:
            text = data.ctx.say(
                f"{frame['id']} ({frame['char']}) sobre {feature}: ninguna cota (datum_dims o distancia) ubica el rasgo "
                "desde los datums; la tolerancia queda incompleta.",
                f"{frame['id']} ({frame['char']}) on {feature}: no dimension (datum_dims or distance) locates the "
                "feature from the datums; the tolerance is incomplete.",
            )
            out.append(Finding("gdt_not_located", "warning", text, (frame["id"],)))
    return out


def _datums(data: ReviewInput) -> list[Finding]:
    """A ``gdt`` frame cites datum letters (``datums``) and a ``datum`` item (``letter``) defines one. A frame whose own
    drawing failed for citing an undefined datum is still looked at here (it is the finding, not a generic item error)."""
    ctx = data.ctx
    defined = {d["letter"] for d in _of(data, "datum") if "letter" in d}
    out: list[Finding] = []
    for frame in (i for i in data.items if i.get("type") == "gdt"):
        missing = sorted(set(_letters(frame.get("datums"))) - defined)
        if missing:
            text = ctx.say(
                f"{frame['id']} cita el datum {', '.join(missing)} y ningún ítem datum lo define.",
                f"{frame['id']} cites datum {', '.join(missing)} and no datum item defines it.",
            )
            out.append(Finding("datum_undefined", "warning", text, (frame["id"],)))
    return out


def _position_frames(
    data: ReviewInput,
) -> list[tuple[dict[str, Any], list[HoleInfo], tuple[float, float, float] | None]]:
    """Every position frame hung from a callout, with the holes it tolerates and the direction of their axis (``None`` for
    the axis of a cylinder that is no hole: the direction of its face)."""
    ctx = data.ctx
    out: list[tuple[dict[str, Any], list[HoleInfo], tuple[float, float, float] | None]] = []
    for frame in (i for i in _of(data, "gdt") if i.get("char") == "position" and "on" in i):
        target = next((i for i in data.items if i["id"] == frame["on"]), None)
        if target is None:
            continue
        if target.get("type") == "hole_dim":
            try:
                holes = select_callout_holes(ctx, target)
            except ItemError:
                continue
            out.append((frame, holes, tuple(unit_vector(holes[0].direction)) if holes else None))  # type: ignore[arg-type]
        elif target.get("face"):
            try:
                axis = ctx.geometry.face(target["face"][0]).axis_dir
            except ItemError:
                continue
            out.append((frame, [], tuple(unit_vector(axis)) if axis is not None else None))  # type: ignore[arg-type]
    return out


def _gdt_location_exact(data: ReviewInput) -> list[Finding]:
    """The dimensions that locate a feature with a position tolerance are theoretically exact (ISO 1101, ISO 5458): boxed.
    The ones the sheet writes itself (``loc``, ``datum_dims``) are boxed by the sheet; a ``dim`` the agent added is not
    unless it says ``exact``."""
    ctx = data.ctx
    out: list[Finding] = []
    for frame, holes, _ in _position_frames(data):
        wanted = {h.index for h in holes}
        plain = [
            d["id"]
            for d in _distance_dims(data)
            if not d.get("exact") and wanted & {h.index for end in ("from", "to") for h in _hole_of_ref(ctx, d[end])}
        ]
        if plain:
            text = ctx.say(
                f"{frame['id']} (posición) tolera un agujero que localizan las cotas {', '.join(plain)} sin recuadro: con una "
                "tolerancia de posición la cota de localización es teóricamente exacta (ISO 1101); añade exact=true a esas cotas.",
                f"{frame['id']} (position) tolerates a hole located by the dimensions {', '.join(plain)} with no box: with a "
                "position tolerance the locating dimension is theoretically exact (ISO 1101); add exact=true to those dimensions.",
            )
            out.append(Finding("gdt_location_not_exact", "warning", text, (frame["id"], *plain), key=frame["id"]))
    return out


def _parallel(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
    return abs(dot(unit_vector(a), unit_vector(b))) > 0.999


def _fixes_direction(data: ReviewInput, datum: dict[str, Any], axis: tuple[float, float, float]) -> bool:
    """Whether a datum item fixes the direction ``axis`` has: a plane the axis is perpendicular to, or an axis parallel to it."""
    ctx = data.ctx
    try:
        if datum.get("face"):
            face = ctx.geometry.face(datum["face"][0])
            if face.kind == "plane" and face.normal is not None:
                return _parallel(face.normal, axis)
            return face.kind == "cylinder" and face.axis_dir is not None and _parallel(face.axis_dir, axis)
    except ItemError:
        return False
    target = next((i for i in data.items if i["id"] == datum.get("on")), None)
    if target is None:
        return False
    if target.get("type") == "hole_dim":
        try:
            return any(_parallel(h.direction, axis) for h in select_callout_holes(ctx, target))
        except ItemError:
            return False
    try:
        direction = ctx.geometry.face(target["face"][0]).axis_dir if target.get("face") else None
    except ItemError:
        return False
    return direction is not None and _parallel(direction, axis)


def _gdt_axis_oriented(data: ReviewInput) -> list[Finding]:
    """A position tolerance of a hole needs a datum that fixes the direction of its axis: the face the hole is perpendicular
    to (the primary datum of a through hole), then the edges that locate it. Datums that only fix the location leave the
    axis free to tilt."""
    ctx = data.ctx
    datums = {d["letter"]: d for d in _of(data, "datum") if "letter" in d}
    out: list[Finding] = []
    for frame, _, axis in _position_frames(data):
        if axis is None:
            continue
        cited = [datums[c] for c in _letters(frame.get("datums")) if c in datums]
        if any(_fixes_direction(data, d, axis) for d in cited):
            continue
        text = ctx.say(
            f"{frame['id']} (posición) no cita ningún datum que fije la dirección del eje: el datum primario de un agujero es "
            "la cara a la que es perpendicular (declárala con un ítem datum y cítala la primera: ⌖ Ø0,05 | C | A | B).",
            f"{frame['id']} (position) cites no datum that fixes the direction of the axis: the primary datum of a hole is "
            "the face it is perpendicular to (declare it with a datum item and cite it first: ⌖ Ø0,05 | C | A | B).",
        )
        out.append(Finding("gdt_axis_not_oriented", "warning", text, (frame["id"],), key=frame["id"]))
    return out


_NUMBER = r"(?:Ø|⌀)?\s?\d+[.,]\d+"
_GDT_STRONG = (
    r"planitud|rectitud|circularidad|redondez|cilindricidad|paralelismo|perpendicularidad|angularidad|concentricidad|"
    r"coaxialidad|salto (?:radial|axial|total|circular)|flatness|straightness|roundness|circularity|cylindricity|"
    r"parallelism|perpendicularity|angularity|concentricity|coaxiality|(?:total |circular )?runout"
)
_GDT_WEAK = r"posici.n|simetr.a|perfil de (?:l.nea|superficie)|position|symmetry|profile of (?:a )?(?:line|surface)"
_GDT_SYMBOLS = "⊥∥⌖◎⌭⌒⌓↗⌯⏥"
GDT_IN_TEXT = re.compile(
    rf"(?:{_GDT_STRONG})\b[^.;\n]{{0,40}}?{_NUMBER}"
    rf"|(?:toleranc\w+|tolerance)\s+(?:de |of )?(?:{_GDT_WEAK})\b[^.;\n]{{0,40}}?{_NUMBER}"
    rf"|(?:{_GDT_WEAK})\b[^.;\n]{{0,25}}?(?:toleranc\w+|tolerance)[^.;\n]{{0,25}}?{_NUMBER}"
    rf"|[{_GDT_SYMBOLS}]\s?{_NUMBER}",
    re.IGNORECASE,
)


def _gdt_as_note(data: ReviewInput) -> list[Finding]:
    """A geometric tolerance written as the text of a note (``perpendicularidad 0,02 A``, ``⊥ Ø0,1 A``): a shop reads a
    feature control frame (ISO 1101) on the feature, not a sentence in the notes block. The note is flagged and the
    answer is a ``gdt`` item (and its ``datum`` items); a note that only names a characteristic without a value is quiet."""
    ctx = data.ctx
    out: list[Finding] = []
    for note in _of(data, "note"):
        text = str(note.get("text") or "")
        if not GDT_IN_TEXT.search(text):
            continue
        shown = text if len(text) <= 60 else text[:57] + "..."
        message = ctx.say(
            f'{note["id"]} escribe una tolerancia geométrica como texto ("{shown}"): va en un marco ISO 1101 sobre el rasgo '
            "o colgado de su cota, con sus datums definidos y usados. Usa un ítem gdt (char, value, datums, face u on) y "
            "borra la nota; si no es una tolerancia geométrica, respóndelo como decisión (no aplica: <por qué>).",
            f'{note["id"]} writes a geometric tolerance as text ("{shown}"): it belongs in an ISO 1101 frame on the feature '
            "or hung from its dimension, with its datums defined and used. Use a gdt item (char, value, datums, face or on) "
            "and remove the note; if it is not a geometric tolerance, answer it as a decision (not applicable: <why>).",
        )
        out.append(Finding("gdt_as_note", "warning", message, (str(note["id"]),), key=str(note["id"])))
    return out


# --- views ---------------------------------------------------------------------------------------------------------------


def _view_lines(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    if _of(data, "view_lines") or not ctx.holes:
        return []
    text = ctx.say(
        'El plano tiene agujeros y no lleva líneas ocultas ni ejes: añade {"type":"view_lines"}.',
        'The sheet has holes and no hidden lines or centre lines: add {"type":"view_lines"}.',
    )
    return [Finding("no_view_lines", "info", text)]


HIDDEN_CHECK_LIMIT = 80  # instances looked at for "hidden in every view": the first of each part, then the rest
REVIEW_MARGIN_MM = 2.5


JOINT_WORDS = re.compile(
    r"tornill|perno|tuerca|arandela|remach|pasador|chaveta|espárrag|esparrag|bulón|bulon|soldad|solda|adhesiv|pegad|pegament|"
    r"ajuste|interferencia|prensad|encaj|unión|union|screw|bolt|nut\b|washer|rivet|dowel|pin\b|weld|adhesive|glue|bond|"
    r"press[\s-]*fit|interference|clamp|abraz|fasten|sujec|fij",
    re.I,
)  # a fastener, a weld, a fit, or a note that says how the parts are held


def _assembly_joints(data: ReviewInput) -> list[Finding]:
    """An assembly of two or more parts that nothing joins: no fastener among the parts, no weld, no fit on the sheet and
    no note of the engineer saying how they are held. A question to the designer: the harness adds nothing."""
    ctx = data.ctx
    if ctx.assembly is None or len(ctx.assembly.groups) < 2:
        return []
    for group in ctx.assembly.groups:
        if group.bought or JOINT_WORDS.search(f"{group.name} {group.remarks}"):
            return []
    if any(i.get("type") == "weld" for i in data.items):
        return []
    if any("fit" in i for i in data.items if i.get("type") in ("dim", "hole_dim")):
        return []
    if any(JOINT_WORDS.search(n) for n in sheet_checks._notes(data)):
        return []
    text = ctx.say(
        "Las piezas del conjunto no están unidas: ninguna es un elemento de fijación (tornillo, perno, pasador, remache), ni hay "
        "soldadura, ni un ajuste, ni una nota del ingeniero que diga cómo se sujetan. Pregunta al diseñador cómo se unen "
        "(¿tornillos que se añaden al conjunto, soldadura, ajuste a presión?) y refléjalo: inserta la pieza de fijación o escribe "
        "una nota. El arnés no añade nada por su cuenta.",
        "The parts of the assembly are not joined: none is a fastener (screw, bolt, pin, rivet), and there is no weld, no fit "
        "and no note of the engineer saying how they are held. Ask the designer how they are joined (screws to add to the "
        "assembly, a weld, a press fit?) and show it: insert the fastener or write a note. The harness adds nothing by itself.",
    )
    return [Finding("assembly_not_joined", "warning", text)]


def _assembly_holes(data: ReviewInput) -> list[Finding]:
    """A through hole of the assembly is located only by a dimension that is ON THE SHEET (its centre distance and its
    distance from the edge of the plate): a hole that the model has and the sheet does not position is a finding."""
    ctx = data.ctx
    out: list[Finding] = []
    for key, label, missing in sheet_interface.unlocated(ctx):
        along = {"x": "en x", "y": "en y", "xy": "en x e y"}[missing]
        along_en = {"x": "along x", "y": "along y", "xy": "along x and y"}[missing]
        text = ctx.say(
            f"Agujero pasante {label} del conjunto sin posición {along} en la hoja: ninguna cota dibujada da su distancia entre "
            "centros ni su distancia a un borde de la placa. Una vista que lo vea como círculo (superior) y el item overall "
            "las dibujan; o acota con dim distance desde una arista de la placa.",
            f"Through hole {label} of the assembly has no position {along_en} on the sheet: no dimension drawn gives its centre "
            "distance or its distance from an edge of the plate. A view that sees it as a circle (the top one) and the overall "
            "item draw them; or dimension it with dim distance from an edge of the plate.",
        )
        out.append(Finding("hole_not_located", "warning", text, (), (), key))
    return out


def _weldment_lists(data: ReviewInput) -> list[Finding]:
    """The sheet of a welded part carries its cut list and a balloon per body, numbered as the list (``sheet_weldrules``), and
    every position has the designation (profile and size) the designer gave: the harness never guesses it."""
    ctx = data.ctx
    if ctx.weldment is None:
        return []
    lists = _of(data, "parts_list")
    balloons = _of(data, "balloons")
    list_ids = tuple(i["id"] for i in lists)
    balloon_ids = tuple(i["id"] for i in balloons)
    listed = frozenset(str(n) for n in ctx.extras.get("parts_list", {}).get("positions", []))
    drawn = frozenset(str(n) for n in ctx.extras.get("balloons", {}).get("drawn", set()))
    sheet = sheet_weldrules.WeldSheet(True, listed, drawn if balloons else frozenset())
    names = {str(g.position): g for g in ctx.weldment.groups}
    out: list[Finding] = []
    for found in sheet_weldrules.weld_documentation(sheet):
        shown = ", ".join(f"{n} ({names[n].instances[0]})" if n in names else n for n in found.numbers)
        if found.code == "weld_list_missing":
            text = ctx.say(
                'El plano de la pieza soldada no lleva lista de corte: añade {"type":"parts_list"} (una fila por pieza '
                'distinta, con su cantidad, designación y longitud) y {"type":"balloons"}.',
                'The sheet of the welded part has no cut list: add {"type":"parts_list"} (one row per distinct piece, with '
                'quantity, designation and length) and {"type":"balloons"}.',
            )
            out.append(Finding("weld_list_missing", "warning", text, balloon_ids))
        elif found.code == "item_without_balloon":
            text = ctx.say(
                f"Cuerpos de la lista de corte sin globo en el plano: posiciones {shown}. Cada cuerpo lleva su globo "
                "(balloons, en la vista donde se ve).",
                f"Bodies of the cut list with no balloon on the sheet: positions {shown}. Every body has its balloon "
                "(balloons, on the view that shows it).",
            )
            out.append(Finding("item_without_balloon", "warning", text, (*list_ids, *balloon_ids)))
        else:
            text = ctx.say(
                f"Globos sin fila en la lista de corte: {', '.join(found.numbers)}.",
                f"Balloons with no row in the cut list: {', '.join(found.numbers)}.",
            )
            out.append(Finding("balloon_not_in_list", "warning", text, (*list_ids, *balloon_ids)))
    unnamed = [names[str(n)] for n in ctx.extras.get("parts_list", {}).get("unnamed", []) if str(n) in names]
    if unnamed:
        shown = ", ".join(f"{g.position} ({g.instances[0]}: {g.reference})" for g in unnamed)
        first = unnamed[0]
        text = ctx.say(
            f"Cuerpos sin designación en la lista de corte: {shown}. Pregunta al diseñador qué es cada uno (perfil y medida, "
            "p. ej. tubo 40x40x3, pletina 50x6, o chapa de espesor y material) y escríbelo; nunca el nombre del objeto. "
            f'En la lista: rows=[{{"component": "{first.instances[0]}", "name": "<designación>"}}].',
            f"Bodies with no designation in the cut list: {shown}. Ask the designer what each one is (profile and size, e.g. "
            "tube 40x40x3, flat bar 50x6, or plate thickness and material) and write it; never the object name. "
            f'In the list: rows=[{{"component": "{first.instances[0]}", "name": "<designation>"}}].',
        )
        out.append(Finding("weld_body_unnamed", "warning", text, list_ids))
    out += _weldment_designations(data, names, list_ids)
    out += _weldment_views(data)
    out += _weldment_welds(data)
    return out


def _weldment_designations(data: ReviewInput, names: dict[str, Any], list_ids: tuple[str, ...]) -> list[Finding]:
    """The designation the designer wrote agrees with the body (tubes, flat bars and plates; the rest is said, not checked)."""
    import sheet_weldment  # noqa: PLC0415

    ctx = data.ctx
    written = ctx.extras.get("parts_list", {}).get("names", {})
    wrong: list[str] = []
    skipped: list[str] = []
    for position, name in written.items():
        group = names.get(str(position))
        if group is None or group.measured is None:
            continue
        verdict = sheet_weldment.check_designation(str(name), group.measured)
        label = f"{position} ({group.instances[0]}, {name!r})"
        if verdict.status == "mismatch":
            wrong.append(f"{label}: {verdict.why}")
        elif verdict.status == "skipped":
            skipped.append(f"{label}: {verdict.why}")
    out: list[Finding] = []
    if wrong:
        out.append(
            Finding(
                "weld_designation_mismatch",
                "warning",
                ctx.say(
                    "La designación de la lista de corte no coincide con el cuerpo medido: "
                    + "; ".join(wrong)
                    + ". Corrige el cuerpo del modelo (¿hueco o macizo? ¿qué sección?) o la designación, y pregunta al diseñador cuál es la buena.",
                    "The designation in the cut list does not agree with the measured body: "
                    + "; ".join(wrong)
                    + ". Fix the model body (hollow or solid? which section?) or the designation, and ask the designer which is right.",
                ),
                list_ids,
            )
        )
    if skipped:
        out.append(
            Finding(
                "weld_designation_unchecked",
                "info",
                ctx.say(
                    "No se comprobó la designación contra el cuerpo (solo se leen tubos, pletinas y chapas): "
                    + "; ".join(skipped)
                    + ".",
                    "The designation was not checked against the body (only tubes, flat bars and plates are read): "
                    + "; ".join(skipped)
                    + ".",
                ),
                list_ids,
            )
        )
    return out


def _weldment_views(data: ReviewInput) -> list[Finding]:
    """Two standard views that draw the same rectangles for every body: one of them adds nothing."""
    import sheet_weldment  # noqa: PLC0415

    ctx = data.ctx
    boxes = ctx.geometry.body_boxes() if ctx.geometry is not None else []
    out: list[Finding] = []
    for kept, repeat in sheet_weldment.congruent_views(boxes):
        if kept not in ctx.views or repeat not in ctx.views:
            continue
        text = ctx.say(
            f"La vista {repeat} dibuja lo mismo que la vista {kept} (cada cuerpo ocupa el mismo rectángulo): no añade nada. "
            f"Quítala (drawing action=remove_view, view={repeat}) y las demás se reparten el espacio.",
            f"The {repeat} view draws the same as the {kept} view (every body fills the same rectangle): it adds nothing. "
            f"Drop it (drawing action=remove_view, view={repeat}) and the others share the room.",
        )
        out.append(Finding("view_redundant", "warning", text, (repeat,), key=repeat))
    return out


WELD_TEXT = re.compile(r"solda|weld|cord[oó]n|bead|2553", re.I)


def _weldment_welds(data: ReviewInput) -> list[Finding]:
    """The welds of a welded part (``sheet_weldreview``): a symbol on every measured joint, one per joint, no ISO 13920 as a
    quality class."""
    import sheet_weldreview  # noqa: PLC0415

    return sheet_weldreview.findings(data)


def _assembly_lists(data: ReviewInput) -> list[Finding]:
    """The parts list and the balloons of an assembly sheet agree: a position without a balloon, balloons without a list."""
    ctx = data.ctx
    if ctx.assembly is None:
        return []
    lists = _of(data, "parts_list")
    balloons = _of(data, "balloons")
    list_ids = tuple(i["id"] for i in lists)
    balloon_ids = tuple(i["id"] for i in balloons)
    out: list[Finding] = []
    if not lists:
        text = ctx.say(
            'El plano de conjunto no lleva lista de piezas: añade {"type":"parts_list"}.',
            'The assembly sheet has no parts list: add {"type":"parts_list"}.',
        )
        out.append(Finding("no_parts_list", "info", text, balloon_ids))
    if lists and not balloons:
        text = ctx.say(
            'La lista de piezas no tiene globos: añade {"type":"balloons"}.',
            'The parts list has no balloons: add {"type":"balloons"}.',
        )
        out.append(Finding("parts_without_balloons", "warning", text, list_ids))
    if balloons and not lists:
        text = ctx.say(
            "Hay globos y ninguna lista de piezas que los explique (globos sin fila).",
            "There are balloons and no parts list to explain them (balloons without a row).",
        )
        out.append(Finding("balloons_without_row", "warning", text, balloon_ids))
    if lists:
        try:
            rows = rows_of(ctx, lists[0])
        except ItemError:
            rows = []
        unnamed = [g for g, cells in rows if not cells[2].strip()]
        if unnamed:
            shown = ", ".join(f"{g.position} ({g.instances[0]})" for g in unnamed)
            first = unnamed[0]
            text = ctx.say(
                f"Piezas sin designación en la lista de piezas: {shown}. Pregunta al diseñador cómo se llama cada una "
                "(en español, no el nombre del archivo) y escríbela: doc(action=set_title, name=...) en el documento de la "
                f'pieza, o rows=[{{"component": "{first.instances[0]}", "name": "<designación>"}}] en la lista.',
                f"Parts with no designation in the parts list: {shown}. Ask the designer what each is called (in the "
                "project language, not the file name) and write it: doc(action=set_title, name=...) in the part's "
                f'document, or rows=[{{"component": "{first.instances[0]}", "name": "<designation>"}}] in the list.',
            )
            out.append(Finding("part_unnamed", "warning", text, list_ids))
        bare = [g for g, cells in rows if not cells[3].strip() and not g.bought]
        if bare:
            shown = ", ".join(f"{g.position} ({g.label})" for g in bare)
            text = ctx.say(
                f"Piezas propias sin Nº de plano / Referencia en la lista: {shown}. Dales número de plano (campo "
                "drawing_number de su hoja) o escríbelo con rows.",
                f"Own parts with no drawing number / reference in the parts list: {shown}. Give them a drawing number "
                "(drawing_number on their sheet) or write it with rows.",
            )
            out.append(Finding("part_without_reference", "info", text, list_ids))
    book = ctx.extras.get("balloons", {})
    listed = set(ctx.extras.get("parts_list", {}).get("positions", []))
    drawn = set(book.get("drawn", set()))
    if lists and balloons:
        names = {g.position: g.label for g in ctx.assembly.groups}
        missing = sorted(listed - drawn)
        if missing:
            shown = ", ".join(f"{n} ({names.get(n, '?')})" for n in missing)
            text = ctx.say(
                f"Posiciones de la lista sin globo: {shown}.",
                f"Positions of the parts list without a balloon: {shown}.",
            )
            out.append(Finding("position_without_balloon", "warning", text, (*list_ids, *balloon_ids)))
        extra = sorted(drawn - listed)
        if extra:
            shown = ", ".join(str(n) for n in extra)
            text = ctx.say(
                f"Globos sin fila en la lista de piezas: {shown}.",
                f"Balloons without a row in the parts list: {shown}.",
            )
            out.append(Finding("balloon_without_row", "warning", text, (*list_ids, *balloon_ids)))
    shown_in = {str(v) for v in book.get("view", {}).values()}
    pictorial = {n for n in ctx.extras.get("views_extra", {}) if n in ("isometric", "exploded")}
    if balloons and pictorial and shown_in and not shown_in & pictorial:
        text = ctx.say(
            f"Los globos están en la vista {', '.join(sorted(shown_in))} y la hoja tiene {' y '.join(sorted(pictorial))}: los "
            f"globos van en la vista isométrica, donde se ve cada pieza (balloons con view={sorted(pictorial)[0]}).",
            f"The balloons are on the {', '.join(sorted(shown_in))} view and the sheet has a {' and '.join(sorted(pictorial))} "
            f"view: balloons go on the pictorial view, where each part is seen (balloons with view={sorted(pictorial)[0]}).",
        )
        out.append(Finding("balloons_off_isometric", "warning", text, balloon_ids))
    thin = {n: s for n, s in sorted(book.get("share", {}).items()) if n in drawn and s < sheet_isoview.VISIBLE_MIN}
    if thin:
        labels = {g.position: g.label for g in ctx.assembly.groups}
        shown = ", ".join(f"{n} ({labels.get(n, '?')}, {round(100 * s)} %)" for n, s in thin.items())
        percent = round(100 * sheet_isoview.VISIBLE_MIN)
        text = ctx.say(
            f"El globo de la(s) posición(es) {shown} señala una pieza de la que la vista casi no enseña nada (menos del "
            f"{percent} % de su contorno es superficie suya a la vista): no se ve lo que el globo nombra. Pon el globo de esa "
            "pieza en una vista donde se vea (una vista explosionada: exploded, y balloons sobre ella), o cambia la dirección de la vista.",
            f"The balloon of position(s) {shown} points at a part that the view barely shows (less than {percent} % of its "
            "outline is its own surface in view): what the balloon names cannot be seen. Put that part's balloon on a view where "
            "it shows (an exploded view, with the balloons on it), or change the view direction.",
        )
        out.append(Finding("balloon_part_hidden", "warning", text, balloon_ids))
    rect = ctx.extras.get("parts_list", {}).get("rect")
    if rect is not None:
        for name, box in _rects(data).items():
            if _overlap(rect, box):
                text = ctx.say(
                    f"La lista de piezas se solapa con la vista {name}.", f"The parts list overlaps view {name}."
                )
                out.append(Finding("parts_list_over_view", "warning", text, list_ids))
    return out


def sub_(a: tuple[float, ...], b: tuple[float, ...]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _corners(low: tuple[float, ...], high: tuple[float, ...]) -> list[tuple[float, float, float]]:
    return [(x, y, z) for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])]


def _hidden_instances(data: ReviewInput) -> list[Finding]:
    """Component instances that no view of the sheet shows (a bolt buried in a housing, a part behind another in every
    direction): the drawing cannot tell the shop it exists."""
    ctx = data.ctx
    if ctx.assembly is None:
        return []
    views = [*ctx.views.values(), *ctx.extras.get("views_extra", {}).values()]
    if not views:
        return []
    explosion = ctx.extras.get("explosion", {})
    ordered = [g.instances[0] for g in ctx.assembly.groups]
    ordered += [i for g in ctx.assembly.groups for i in g.instances[1:]]
    ordered = ordered[:HIDDEN_CHECK_LIMIT]
    hidden = set(ordered)
    for at, normal in ctx.extras.get("section_planes", {}).values():  # a part the plane of a section cuts is seen in the cut
        for cid in list(hidden):
            try:
                low, high = ctx.geometry.instance_box(cid)
            except ItemError:
                continue
            sides = [dot(sub_(corner, at), normal) for corner in _corners(low, high)]
            if min(sides) <= 1e-6 <= max(sides):
                hidden.discard(cid)
    if not hidden:
        return []
    for view in views:
        offsets = explosion.get("offsets", {}) if view.name == "exploded" else {}
        try:
            found = ctx.geometry.instance_anchors(
                sorted(hidden), (view.centre, view.xdir, view.direction), offsets, REVIEW_MARGIN_MM / view.scale
            )
        except ItemError:
            return []
        hidden -= {cid for cid, points in found.items() if points}
        if not hidden:
            return []
    out = []
    for cid in sorted(hidden):
        position = ctx.assembly.position_of(cid)
        group = next(g for g in ctx.assembly.groups if g.position == position)
        text = ctx.say(
            f"El componente {cid} (posición {position}, {group.label}) no se ve en ninguna vista del plano.",
            f"Component {cid} (position {position}, {group.label}) is not visible in any view of the sheet.",
        )
        out.append(Finding("instance_hidden", "warning", text))
    if len(ctx.assembly.instances()) > HIDDEN_CHECK_LIMIT:
        text = ctx.say(
            f"Solo se comprobaron {HIDDEN_CHECK_LIMIT} componentes de {len(ctx.assembly.instances())}.",
            f"Only {HIDDEN_CHECK_LIMIT} of {len(ctx.assembly.instances())} components were checked.",
        )
        out.append(Finding("hidden_check_partial", "info", text))
    return out


def _rects(data: ReviewInput) -> dict[str, Rect]:
    ctx = data.ctx
    rects: dict[str, Rect] = {}
    named = {tuple(round(c, 3) for c in v.box): name for name, v in ctx.views.items()}
    for number, box in enumerate(ctx.extras.get("view_boxes", []), start=1):
        rects[named.get(tuple(round(c, 3) for c in box), f"view {number}")] = box
    rects.update(ctx.extras.get("view_rects", {}))
    return rects


def _overlap(a: Rect, b: Rect) -> bool:
    return (
        a[0] + OVERLAP_MM < b[2] and b[0] + OVERLAP_MM < a[2] and a[1] + OVERLAP_MM < b[3] and b[1] + OVERLAP_MM < a[3]
    )


def _layout(data: ReviewInput) -> list[Finding]:
    ctx = data.ctx
    out: list[Finding] = []
    rects = _rects(data)
    names = list(rects)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if _overlap(rects[a], rects[b]):
                text = ctx.say(f"Las vistas {a} y {b} se solapan.", f"Views {a} and {b} overlap.")
                ids = tuple(n for n in (a, b) if n in {s["id"] for s in data.items})
                out.append(Finding("views_overlap", "warning", text, ids))
    page_w, page_h = data.sheet.width, data.sheet.height
    left, top, right, bottom = FRAME_MM
    frame = (left, bottom, page_w - right, page_h - top)
    area_bottom = ctx.area[1]
    for name, rect in rects.items():
        ids = (name,) if name in {s["id"] for s in data.items} else ()
        if rect[1] < area_bottom - 0.5:
            text = ctx.say(f"La vista {name} invade el rótulo.", f"View {name} runs into the title block.")
            out.append(Finding("view_over_title_block", "warning", text, ids))
        if rect[0] < frame[0] or rect[1] < frame[1] or rect[2] > frame[2] or rect[3] > frame[3]:
            text = ctx.say(f"La vista {name} se sale del marco.", f"View {name} goes outside the frame.")
            out.append(Finding("view_outside_frame", "warning", text, ids))
    reported = 0
    owner = {id(e): u.key for u in ctx.extras.get("units", []) for e in u.entities}
    item_ids = {s["id"] for s in data.items}
    flagged: set[str] = set()
    for e in data.sheet.entities:
        if not isinstance(e, Text) or e.layer == TITLE_BLOCK:
            continue
        w = text_width(e.text, e.height)
        x0 = e.x - (w / 2 if e.anchor == "center" else w if e.anchor == "right" else 0.0)
        box = (x0, e.y, x0 + w, e.y + e.height)
        if e.angle:
            box = (e.x - e.height, e.y, e.x, e.y + w)
        if box[0] < frame[0] or box[1] < frame[1] or box[2] > frame[2] or box[3] > frame[3]:
            reported += 1
            key = owner.get(id(e))
            if key is not None:
                flagged.add(key)
            if reported <= MAX_TEXT_FINDINGS:
                text = ctx.say(
                    f"El texto «{e.text}» queda fuera del marco.", f"The text '{e.text}' is outside the frame."
                )
                out.append(Finding("text_outside_frame", "warning", text, (key,) if key in item_ids else ()))
    out += _item_places(data, frame, flagged)
    return out


def _box(x0: float, y0: float, x1: float, y1: float) -> str:
    return f"[{x0:.2f}, {y0:.2f}, {x1:.2f}, {y1:.2f}]"


def _item_places(data: ReviewInput, frame: Rect, flagged: set[str]) -> list[Finding]:
    """An item whose drawing (lines, frames, text) runs outside the frame or over the title block: placed by the
    placer, or asked for with ``at_mm`` -- either way the sheet would be printed wrong, and the finding names the item."""
    ctx = data.ctx
    block = ctx.extras.get("title_block")
    out: list[Finding] = []
    for unit in ctx.extras.get("units", []):
        if unit.kind != "item":
            continue
        x0, y0, x1, y1 = unit.rect
        if unit.key not in flagged and (x0 < frame[0] or y0 < frame[1] or x1 > frame[2] or y1 > frame[3]):
            text = ctx.say(
                f"{unit.key} ({unit.item_type}) se sale del marco del plano ({_box(x0, y0, x1, y1)} frente a {_box(*frame)}): muévelo (offset_mm, side o at_mm).",
                f"{unit.key} ({unit.item_type}) runs outside the frame of the sheet ({_box(x0, y0, x1, y1)} against {_box(*frame)}): move it (offset_mm, side or at_mm).",
            )
            out.append(Finding("item_outside_frame", "warning", text, (unit.key,)))
        if block is not None and unit.item_type != "parts_list" and _overlap((x0, y0, x1, y1), block):
            text = ctx.say(
                f"{unit.key} ({unit.item_type}) invade el rótulo: muévelo (offset_mm, side o at_mm).",
                f"{unit.key} ({unit.item_type}) runs over the title block: move it (offset_mm, side or at_mm).",
            )
            out.append(Finding("item_over_title_block", "warning", text, (unit.key,)))
    return out
