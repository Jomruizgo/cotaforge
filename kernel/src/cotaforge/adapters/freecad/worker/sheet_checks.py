# ruff: noqa: E501, SLF001
"""What a drawing must answer before it is a finished drawing (no FreeCAD import, unit-testable on its own).

Three things live here, all read by ``sheet_review``/``drawing``:

* ``checks``: the automatic findings of the "plano terminado" list -- hole positions measured from a reference, fit
  holes with their tolerance, forms (radii) with a dimension, centre marks and axes, texts lying on the part, the
  general notes (material, edges, heat treatment, coating, roughness, marking), the title-block material against the
  notes that quote it, hidden-line density, depth features that only show hidden, threads with no representation,
  tolerances finer than IT6. Each is a SUGGESTION TO THINK ABOUT: it is fixed (and gone on the next review) or answered
  ("no aplica: <why this part does not need it>"); nothing here is an error to eliminate.
* ``judgement``: the questions no program can answer (enough views, sections, measurable dimensions...).
* ``assign_ids`` / ``NON_BLOCKING``: the stable id every finding carries so an answer can name it, and the findings
  that are notices about the harness (not about the drawing) and so never hold an export.

Everything comes from the model's real geometry (the holes, the faces the worker listed in ``PartFacts``) and from what
the sheet really draws; when a fact is not known, the check is skipped, never guessed.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any

import iso286
import sheet_collide
import sheet_review  # circular on purpose: both import each other as modules and use each other only when called
import sheet_section
import sheet_seen
from items_holes import _select as select_callout_holes
from items_holes import _stacks
from sheet_context import Context, HoleInfo, ItemError
from sheet_model import AXES, CONTOUR, HIDDEN, THREAD, Arc, Line, Text, fmt

# --- facts the worker reads from the live part (the checks never import FreeCAD) --------------------------------------


@dataclass(frozen=True)
class RoundFace:
    """A cylindrical face that is part of a form, not a hole and not a whole pin: a fillet, a rounded corner, the end of
    a slot. ``concave`` is true when the material lies outside."""

    face_id: str
    radius: float
    concave: bool


@dataclass(frozen=True)
class Boss:
    """A convex cylinder that goes all the way round (a pin, a shaft, a boss)."""

    face_id: str
    radius: float
    axis_point: tuple[float, float, float]  # on the axis, halfway along the cylinder
    axis_dir: tuple[float, float, float]
    length: float = 0.0  # along the axis (0: not known)
    bore: bool = False  # the wall of a hole all the way round (only read in an assembly, whose holes nobody lists)


@dataclass
class PartFacts:
    rounds: list[RoundFace] = field(default_factory=list)
    bosses: list[Boss] = field(default_factory=list)
    assembly: bool = False  # the cylinders of the components of an assembly: some are hidden behind other parts
    sheet_metal: bool = False
    keyways: int = 0  # slots with parallel walls along the axis of a cylindrical face (``sheet_keyway``)
    keyway_slots: list[Any] = field(default_factory=list)  # the same slots with their faces (``sheet_keyway.Slot``)
    thread_features: int = 0  # features named as a thread that no ``hole`` operation made (an external thread)


# --- ids ------------------------------------------------------------------------------------------------------------------

NON_BLOCKING = frozenset(
    {
        "window_differs",
        "native_fallback",
        "native_unread",
        "hidden_check_partial",
        "single_view_enough",
        "dowel_unmarked",
        "dimension_repeated",
        "dimension_side",
        "dimension_crossing",
        "datum_on_line",
        "datum_unused",
        "leader_not_on_chamfer",
        "view_redundant",
        "arrows_inside_short",
        "sheet_unbalanced",
        "hatch_same_as_neighbour",
        "hatch_pattern_differs",
        "weld_designation_unchecked",
    }
)
"""Notices that never hold an export: about the harness (the FreeCAD window, TechDraw's own objects) or suggestions whose
question is asked anyway (``single_view_enough`` is the plate case of the memory principle ``drawing-sufficient-views``,
``dowel_unmarked`` of ``q.callouts``) or that is a matter of style (``dimension_repeated``: the same size in two views,
ISO 129, which a shop reads without trouble): they are listed and can be answered, but the question is what the export
waits for."""
MAX_ID_KEY = 60


def finding_id(code: str, key: str) -> str:
    return f"{code}:{key[:MAX_ID_KEY]}" if key else code


def assign_ids(rows: list[dict[str, Any]]) -> None:
    """Give each finding row an ``id`` (``code`` or ``code:key``), stable between reviews: the ``key`` the check gave
    it, else the ids of the items and features it names. Two with the same id get ``#2``, ``#3``..."""
    seen: dict[str, int] = {}
    for row in rows:
        key = str(row.get("key") or ",".join([*row.get("items", ()), *row.get("features", ())]))
        base = finding_id(str(row["code"]), key)
        seen[base] = seen.get(base, 0) + 1
        row["id"] = base if seen[base] == 1 else f"{base}#{seen[base]}"
        row["blocking"] = row["code"] not in NON_BLOCKING


# --- helpers --------------------------------------------------------------------------------------------------------------


def hole_key(stack: list[HoleInfo]) -> str:
    """Where a hole is, as the id of its findings: ``@x,y,z`` of the opening of the hole itself (the smallest cylinder
    of a counterbore stack)."""
    return "@" + ",".join(fmt(c) for c in stack[0].centre)


def _finding(
    code: str, severity: str, message: str, key: str = "", items: tuple[str, ...] = (), features: tuple[str, ...] = ()
) -> Any:
    return sheet_review.Finding(code, severity, message, items, features, key)


def _notes(data: Any) -> list[str]:
    return [str(n.get("text", "")) for n in sheet_review._of(data, "note")]


def _fields(ctx: Context) -> dict[str, str]:
    return {str(k): str(v) for k, v in ctx.extras.get("fields", {}).items()}


def _stack_list(ctx: Context) -> list[list[HoleInfo]]:
    return _stacks(list(ctx.holes))


def _has(texts: list[str], pattern: re.Pattern[str]) -> bool:
    return any(pattern.search(t) for t in texts)


def _dist_to_segment(px: float, py: float, seg: sheet_collide.Segment) -> float:
    x1, y1, x2, y2 = seg
    dx, dy = x2 - x1, y2 - y1
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


# --- holes: reference of the position, fits -------------------------------------------------------------------------------

DOWEL_SIZES_MM = (3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 16.0, 20.0)
"""Nominal sizes of dowel pins and reamers (ISO 2338 / 8734). A plain hole of exactly one of them is a candidate fit hole;
Ø5 is left out because it is also the tap drill of M6 (``thread_undeclared`` asks about it)."""
DOWEL_TOL_MM = 0.005
ANCHOR_TOL = 0.05


def _hole_references(data: Any) -> list[Any]:
    """A hole located only from other holes (a chain with no end on a face, an edge or a point of the part, and no datum
    ordinates) is not measured from any declared reference."""
    ctx = data.ctx
    out: list[Any] = []
    anchored: set[int] = set()
    links: list[tuple[set[int], set[int]]] = []
    for spec in sheet_review._distance_dims(data):
        ends = [{h.index for h in sheet_review._hole_of_ref(ctx, spec[e])} for e in ("from", "to")]
        for end, other in ((0, 1), (1, 0)):
            if ends[end] and not ends[other]:
                anchored |= ends[end]
        if ends[0] and ends[1]:
            links.append((ends[0], ends[1]))
    for spec in sheet_review._of(data, "datum_dims"):
        if spec.get("auto"):  # the default location: the holes it located, from a declared datum or a plane of symmetry
            anchored |= set(ctx.extras.get("auto_covered", ()))
            continue
        view = ctx.views.get(spec.get("view", "top"))
        if view is None:
            continue
        wanted = spec.get("holes", "all")
        anchored |= {
            h.index
            for h in ctx.holes
            if view.looks_along(h.direction) and (wanted == "all" or any(h.on_axis(p) for p in wanted))
        }
    changed = True
    while changed:  # a hole measured from an anchored one is anchored only through a chain: say it is a chain
        changed = False
        for a, b in links:
            if (a & anchored) and not (b <= anchored):
                anchored |= b
                changed = True
            if (b & anchored) and not (a <= anchored):
                anchored |= a
                changed = True
    located = sheet_review._located_holes(data)
    for stack in _stack_list(ctx):
        indexes = {h.index for h in stack}
        if indexes & located and not indexes & anchored:
            name = " + ".join(sheet_review._label(h, ctx) for h in stack)
            text = ctx.say(
                f"Agujero {name}: se ubica solo respecto a otros agujeros; ninguna cota parte de una cara, una arista o un "
                "dato de la pieza (datum_dims o dim distance a una cara).",
                f"Hole {name} is located only from other holes: no dimension starts at a face, an edge or a datum of the "
                "part (datum_dims, or dim distance to a face).",
            )
            out.append(_finding("hole_no_reference", "warning", text, hole_key(stack)))
    return out


def _toleranced_holes(data: Any) -> set[int]:
    done: set[int] = set()
    for spec in sheet_review._of(data, "hole_dim"):
        if "fit" not in spec and "tol" not in spec:
            continue
        try:
            done |= {h.index for h in select_callout_holes(data.ctx, spec)}
        except ItemError:
            continue
    return done


def _called_holes(data: Any) -> set[int]:
    """The holes some ``hole_dim`` calls out (a callout without a fit of its own prints the one the hole declared)."""
    done: set[int] = set()
    for spec in sheet_review._of(data, "hole_dim"):
        try:
            done |= {h.index for h in select_callout_holes(data.ctx, spec)}
        except ItemError:
            continue
    return done


def _fit_holes(data: Any) -> list[Any]:
    """A plain hole of a dowel / reamer size with no tolerance on its callout: a fit hole needs its class (H7) and, with
    ``hole_not_located``, its position. A hole that is not one of those sizes is never flagged here: the memory principle ``drawing-general-tolerance-declared``
    asks about bearing seats and the like."""
    ctx = data.ctx
    toleranced = _toleranced_holes(data)
    called = _called_holes(data)
    out: list[Any] = []
    for stack in _stack_list(ctx):
        hole = stack[0]
        if hole.thread is not None or hole.index in toleranced:
            continue
        if hole.fit and hole.index in called:
            continue  # the hole operation declared its fit and a callout prints it: the class is on the sheet
        if not any(abs(hole.diameter - size) <= DOWEL_TOL_MM for size in DOWEL_SIZES_MM):
            continue
        name = " + ".join(sheet_review._label(h, ctx) for h in stack)
        text = ctx.say(
            f"Agujero {name}: tiene el diámetro de un pasador o escariador. Si es un agujero de ajuste, lleva su clase "
            '(hole_dim con fit="H7") y su posición acotada; si es de paso, responde con el motivo.',
            f"Hole {name} has the diameter of a dowel or reamer. If it is a fit hole it needs its class (hole_dim with "
            'fit="H7") and a dimensioned position; if it is a clearance hole, answer with the reason.',
        )
        out.append(_finding("fit_hole_no_tolerance", "warning", text, hole_key(stack)))
    return out


def _fine_tolerances(data: Any) -> list[Any]:
    """A fit finer than IT6, or a tolerance band narrower than IT6 of its size: a shop needs a reason to grind it."""
    ctx = data.ctx
    out: list[Any] = []
    measured = {m.spec["id"]: m.hi - m.lo for m in sheet_review._measure(data)}
    for spec in [*sheet_review._of(data, "hole_dim"), *sheet_review._of(data, "dim")]:
        grade, band = None, None
        if "fit" in spec:
            try:
                grades = [iso286.parse(p)[1] for p in spec["fit"].split("/")]
            except iso286.FitError:
                continue
            if min(grades) < 6:
                grade = min(grades)
        elif "tol" in spec:
            band = (spec["tol"][0] - spec["tol"][1]) * 1000.0
        else:
            continue
        nominal = _nominal_of(data, spec, measured)
        if grade is None and (band is None or nominal is None):
            continue
        if grade is None:
            try:
                finer = band < iso286.tolerance_um(6, nominal) - 1e-6  # type: ignore[arg-type]
            except iso286.FitError:
                continue
            if not finer:
                continue
            what = ctx.say(f"una banda de {band:g} µm (más fina que IT6)", f"a band of {band:g} µm (finer than IT6)")
        else:
            what = f"IT{grade}"
        text = ctx.say(
            f"{spec['id']} pide {what}, más fino que IT6: hace falta una razón (rectificado, ajuste de rodamiento...); "
            "respóndela o afloja la tolerancia.",
            f"{spec['id']} asks for {what}, finer than IT6: it needs a reason (ground, bearing fit...); answer it or "
            "loosen the tolerance.",
        )
        out.append(_finding("tolerance_finer_than_it6", "info", text, spec["id"], (spec["id"],)))
    return out


def _nominal_of(data: Any, spec: dict[str, Any], measured: dict[str, float]) -> float | None:
    ctx = data.ctx
    if spec["type"] == "hole_dim":
        try:
            return select_callout_holes(ctx, spec)[0].diameter
        except (ItemError, IndexError):
            return None
    kind = spec.get("kind")
    if kind == "distance":
        return measured.get(spec["id"])
    if kind in ("radius", "diameter"):
        try:
            radius = ctx.geometry.face(spec["face"][0]).radius
        except ItemError:
            return None
        return None if radius is None else (radius if kind == "radius" else 2 * radius)
    return None


# --- forms: radii ---------------------------------------------------------------------------------------------------------

_RADIUS_IN_NOTE = re.compile(r"\bR\s*(\d+(?:[.,]\d+)?)", re.I)
_ALL_RADII = re.compile(
    r"radios?\s+(no\s+indicad|general|salvo|restantes|en\s+esquinas)|todos\s+los\s+(radios|redondeos)|"
    r"(unspecified|all|other)\s+(radii|fillets)|fillets?\s+(unless|except)",
    re.I,
)


def _rounds(data: Any) -> list[Any]:
    """Every radius of the part with no dimension and no note: a fillet, a rounded corner, the end of a slot. Fillets the
    ``fillet`` feature made are asked about by ``feature_no_dimension`` already."""
    ctx = data.ctx
    facts: PartFacts | None = ctx.extras.get("facts")
    if facts is None or not facts.rounds:
        return []
    owned = {f for feat in data.features if feat.kind in ("fillet", "chamfer") for f in feat.faces}
    texts = _notes(data)
    if _has(texts, _ALL_RADII):
        return []
    said = {round(float(m.replace(",", ".")), 2) for t in texts for m in _RADIUS_IN_NOTE.findall(t)}
    dimensioned: set[float] = set()
    for spec in sheet_review._of(data, "dim"):
        if spec.get("kind") in ("radius", "diameter"):
            try:
                radius = ctx.geometry.face(spec["face"][0]).radius
            except ItemError:
                continue
            if radius is not None:
                dimensioned.add(round(radius, 2))
    by_radius: dict[float, list[RoundFace]] = {}
    for r in facts.rounds:
        if r.face_id not in owned:
            by_radius.setdefault(round(r.radius, 2), []).append(r)
    out: list[Any] = []
    for radius, faces in sorted(by_radius.items()):
        if radius in said or radius in dimensioned:
            continue
        shown = ", ".join(f.face_id for f in faces[:4]) + (f" (+{len(faces) - 4})" if len(faces) > 4 else "")
        text = ctx.say(
            f"Radio R{ctx.num(radius)} ({len(faces)} cara(s): {shown}) sin cota ni nota: acótalo (dim kind=radius) o "
            f"cúbrelo con una nota («Radios R{ctx.num(radius)} salvo indicación»).",
            f"Radius R{ctx.num(radius)} ({len(faces)} face(s): {shown}) has no dimension or note: dimension it (dim "
            f'kind=radius) or cover it with a note ("Radii R{ctx.num(radius)} unless stated").',
        )
        out.append(_finding("round_no_dimension", "info", text, f"R{fmt(radius)}"))
    return out


# --- centre marks and axes ------------------------------------------------------------------------------------------------

MARK_TOL_MM = 0.3


def _axis_segments(data: Any) -> list[sheet_collide.Segment]:
    return [(e.x1, e.y1, e.x2, e.y2) for e in data.sheet.entities if isinstance(e, Line) and e.layer == AXES]


def _marked(segments: list[sheet_collide.Segment], point: tuple[float, float]) -> bool:
    return any(_dist_to_segment(point[0], point[1], s) <= MARK_TOL_MM for s in segments)


def _marks(data: Any) -> list[Any]:
    """Every hole seen as a circle carries a centre mark, and every round boss a mark (seen along its axis) or an axis
    line (seen from the side)."""
    ctx = data.ctx
    segments = _axis_segments(data)
    out: list[Any] = []
    for stack in _stack_list(ctx):
        missing = [
            v.name
            for v in ctx.views.values()
            if v.looks_along(stack[0].direction) and not _marked(segments, v.project(stack[0].centre))
        ]
        if missing:
            name = " + ".join(sheet_review._label(h, ctx) for h in stack)
            text = ctx.say(
                f'Agujero {name} sin marca de centro en la vista {", ".join(missing)}: añade {{"type":"view_lines"}} '
                "(ejes y marcas de todos los agujeros).",
                f'Hole {name} has no centre mark in the {", ".join(missing)} view: add {{"type":"view_lines"}} (axes '
                "and marks on every hole).",
            )
            out.append(_finding("centre_mark_missing", "info", text, hole_key(stack)))
    facts: PartFacts | None = ctx.extras.get("facts")
    for boss in facts.bosses if facts is not None else []:
        missing = []
        for v in ctx.views.values():
            if v.looks_along(boss.axis_dir):
                point = v.project(boss.axis_point)
                if not _marked(segments, point) and not (facts.assembly and not sheet_seen.circle_seen(ctx, v, boss)):
                    missing.append(v.name)
            elif v.shows_axis_along(boss.axis_dir) and not _marked(segments, v.project(boss.axis_point)):
                if boss.bore or (facts.assembly and not sheet_seen.side_on_seen(ctx, v, boss)):
                    continue  # a bore is inside the material here, and a cylinder behind another part shows no sides
                missing.append(v.name)
        for label, view in ctx.extras.get("sections", {}).items():  # a section cuts the cylinders whose axis lies in its plane
            plane = ctx.extras.get("section_planes", {}).get(label)
            if (
                plane is not None
                and boss.length > 0
                and sheet_section.in_plane(plane[0], plane[1], boss.axis_point, boss.axis_dir)
                and not _marked(segments, view.project(boss.axis_point))
            ):
                missing.append(label)
        if missing:
            text = ctx.say(
                f"Cilindro Ø{ctx.num(2 * boss.radius)} ({boss.face_id}) sin eje o marca de centro en la vista "
                f"{', '.join(missing)}: dibuja su eje.",
                f"Cylinder Ø{ctx.num(2 * boss.radius)} ({boss.face_id}) has no axis or centre mark in the "
                f"{', '.join(missing)} view: draw its axis.",
            )
            out.append(_finding("cylinder_no_axis", "info", text, boss.face_id))
    return out


# --- texts on the part ----------------------------------------------------------------------------------------------------

TEXT_ITEMS = frozenset({"dim", "hole_dim", "datum_dims", "overall", "gdt", "finish", "note"})


def _inside(point: tuple[float, float], segments: list[sheet_collide.Segment]) -> bool:
    """Even-odd test of a point against the visible outline of a view (a ray towards +x)."""
    px, py = point
    crossings = 0
    for x1, y1, x2, y2 in segments:
        if (y1 > py) != (y2 > py):
            x = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if x > px:
                crossings += 1
    return crossings % 2 == 1


def _text_on_part(data: Any) -> list[Any]:
    """Dimension values and notes whose centre lies inside the outline of a view: they are read on the part, over its
    lines. (Axes and the diameters of large parts are the justified exception: answer it.)"""
    ctx = data.ctx
    ink = ctx.extras.get("view_ink", [])
    if not ink or not ctx.views:
        return []
    per_view: dict[str, list[sheet_collide.Segment]] = {}
    for name, view in ctx.views.items():
        x0, y0, x1, y1 = view.box
        pad = 1.0
        segs: list[sheet_collide.Segment] = []
        for e in ink:
            if getattr(e, "layer", None) != CONTOUR or isinstance(e, Text):
                continue
            for s in sheet_collide._segments(e):
                if x0 - pad <= (s[0] + s[2]) / 2 <= x1 + pad and y0 - pad <= (s[1] + s[3]) / 2 <= y1 + pad:
                    segs.append(s)
        per_view[name] = segs
    out: list[Any] = []
    flagged: set[str] = set()
    for unit in ctx.extras.get("units", []):
        if unit.kind != "item" or unit.item_type not in TEXT_ITEMS or unit.key in flagged:
            continue
        for e in unit.entities:
            if not isinstance(e, Text) or not e.text.strip():
                continue
            x0, y0, x1, y1 = sheet_collide.text_box(e)
            centre = ((x0 + x1) / 2, (y0 + y1) / 2)
            hit = next(
                (
                    n
                    for n, v in ctx.views.items()
                    if v.box[0] < centre[0] < v.box[2]
                    and v.box[1] < centre[1] < v.box[3]
                    and _inside(centre, per_view[n])
                ),
                None,
            )
            if hit is None:
                continue
            flagged.add(unit.key)
            text = ctx.say(
                f"{unit.key} ({unit.item_type}): el texto «{e.text}» cae dentro del contorno de la vista {hit}; las cotas "
                "van fuera de la pieza (offset_mm, side).",
                f"{unit.key} ({unit.item_type}): the text '{e.text}' lies inside the outline of the {hit} view; dimensions "
                "go outside the part (offset_mm, side).",
            )
            out.append(_finding("text_over_part", "info", text, unit.key, (unit.key,)))
            break
    return out


# --- the general notes ----------------------------------------------------------------------------------------------------

_MATERIAL = re.compile(
    r"\bmaterial\b|acero|alumin|steel|inox|lat[oó]n|brass|bronce|bronze|\baisi\b|\bastm\b|\bsae\s*\d|\bs\d{3}\b|pl[aá]stico|"
    r"nylon|polietileno|cobre|copper",
    re.I,
)
_EDGES = re.compile(r"rebab|desbarb|aristas?|bordes?|deburr|break\s+(sharp\s+)?edges?|sharp\s+edges?|burrs?", re.I)
_HEAT = re.compile(
    r"tratamiento\s+t[eé]rmico|temple|revenid|normaliz|recocid|cementad|nitrur|dureza|\bHRC\b|\bHV\b|heat[\s-]*treat|harden|"
    r"anneal|temper|stress[\s-]*reliev|alivio\s+de\s+tensiones",
    re.I,
)
_COATING = re.compile(
    r"recubrimiento|pintura|pintad|anodiz|galvaniz|zincad|cromad|n[ií]quel|pavonad|fosfat|oxid|coating|paint|plating|anodi[sz]|"
    r"galvani[sz]|powder|polvo|sin\s+recubrimiento|bare",
    re.I,
)
_ROUGHNESS = re.compile(r"rugosidad|\bRa\s*\d|roughness|acabado\s+superficial|surface\s+finish", re.I)
_MARKING = re.compile(
    r"marcad|grabad|marking|etch|engrav|identific|n[uú]mero\s+de\s+(parte|pieza|plano)|part\s+number|stamp", re.I
)
_SEE_TITLE = re.compile(
    r"seg[uú]n\s+r[oó]tulo|ver\s+r[oó]tulo|see\s+title\s*block|per\s+title\s*block|as\s+(in|per)\s+title", re.I
)

NOTE_RULES: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    (
        "edges",
        _EDGES,
        "aristas y rebabas (p. ej. «Romper aristas vivas 0,3 a 0,5 mm; sin rebabas»)",
        "edges and burrs (e.g. 'Break sharp edges 0.3 to 0.5 mm; no burrs')",
    ),
    (
        "heat_treatment",
        _HEAT,
        "tratamiento térmico (con dureza) o «sin tratamiento térmico»",
        "heat treatment (with hardness) or 'no heat treatment'",
    ),
    (
        "coating",
        _COATING,
        "recubrimiento o pintura («Anodizado negro», «Sin recubrimiento»)",
        "coating or paint ('Black anodised', 'No coating')",
    ),
    (
        "roughness",
        _ROUGHNESS,
        "rugosidad general (finish general=true, o una nota «Ra 3,2 salvo indicación»)",
        "general roughness (finish general=true, or a note 'Ra 3.2 unless stated')",
    ),
    (
        "marking",
        _MARKING,
        "marcado o identificación de la pieza (grabado, etiqueta, número de plano)",
        "marking or identification of the part (engraving, label, drawing number)",
    ),
)


def _material_text(data: Any) -> str:
    return _fields(data.ctx).get("part_material", "").strip()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _notes_missing(data: Any) -> list[Any]:
    """Material and stock, edges, heat treatment, coating, general roughness, marking: each is on the sheet (a note, the
    title block, a general finish symbol) or answered."""
    ctx = data.ctx
    texts = _notes(data)
    out: list[Any] = []
    if not _material_text(data) and not _has(texts, _MATERIAL):
        text = ctx.say(
            "Falta el material y el formato de partida: ni el rótulo ni ninguna nota lo dicen (p. ej. «Material: AISI 1045, "
            "lámina de 19 mm rectificada»).",
            "The material and stock form are missing: neither the title block nor any note says them (e.g. 'Material: AISI "
            "1045, ground plate 19 mm').",
        )
        out.append(_finding("notes_material", "warning", text, "material"))
    for code, pattern, es, en in NOTE_RULES:
        present = _has(texts, pattern)
        if code == "roughness":
            present = present or any(i.get("general") for i in sheet_review._of(data, "finish"))
        if present:
            continue
        text = ctx.say(
            f"Falta la nota de {es}. Añade un note, o responde «no aplica» con el motivo.",
            f"The note on {en} is missing. Add a note, or answer 'does not apply' with the reason.",
        )
        out.append(_finding(f"notes_{code}", "info", text, code))
    return out


def _material_consistency(data: Any) -> list[Any]:
    """A note that says "material according to the title block" needs a material in the title block; a note that names a
    material must not name another one than the title block does."""
    ctx = data.ctx
    field_material = _material_text(data)
    out: list[Any] = []
    for note in sheet_review._of(data, "note"):
        text = str(note.get("text", ""))
        found = re.search(r"material\s*[:=]?\s*([^.;\n]+)", text, re.I)
        if not found:
            continue
        said = found.group(1).strip()
        if _SEE_TITLE.search(text):
            if not field_material:
                msg = ctx.say(
                    f"{note['id']} remite el material al rótulo y el campo de material del rótulo está vacío.",
                    f"{note['id']} sends the material to the title block and the title block's material field is empty.",
                )
                out.append(_finding("material_title_block_empty", "warning", msg, note["id"], (note["id"],)))
            continue
        a, b = _norm(said), _norm(field_material)
        if field_material and a and b and a not in b and b not in a:
            msg = ctx.say(
                f"{note['id']} dice «{said}» y el rótulo dice «{field_material}»: el material debe ser uno solo.",
                f"{note['id']} says '{said}' and the title block says '{field_material}': the material must be one.",
            )
            out.append(_finding("material_inconsistent", "warning", msg, note["id"], (note["id"],)))
    return out


# --- hidden lines, sections, threads --------------------------------------------------------------------------------------

HIDDEN_RATIO = 0.6  # hidden length against visible length in one view
HIDDEN_MIN_COUNT = 12


def _length(e: Any) -> float:
    return sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in sheet_collide._segments(e))


def _middle_in(e: Any, box: tuple[float, float, float, float], pad: float = 2.0) -> bool:
    """Whether the middle of an entity lies in ``box`` (a view's rectangle, a little widened)."""
    segs = sheet_collide._segments(e)
    if not segs:
        return False
    mx = sum(s[0] + s[2] for s in segs) / (2 * len(segs))
    my = sum(s[1] + s[3] for s in segs) / (2 * len(segs))
    return box[0] - pad <= mx <= box[2] + pad and box[1] - pad <= my <= box[3] + pad


def _hidden_density(data: Any) -> list[Any]:
    ctx = data.ctx
    hidden = [e for e in data.sheet.entities if getattr(e, "layer", None) == HIDDEN and not isinstance(e, Text)]
    ink = [
        e for e in ctx.extras.get("view_ink", []) if getattr(e, "layer", None) == CONTOUR and not isinstance(e, Text)
    ]
    if not hidden or not ink:
        return []
    out: list[Any] = []
    for name, view in ctx.views.items():
        mine = [e for e in hidden if _middle_in(e, view.box)]
        seen = sum(_length(e) for e in ink if _middle_in(e, view.box))
        if len(mine) >= HIDDEN_MIN_COUNT and seen > 0 and sum(_length(e) for e in mine) >= HIDDEN_RATIO * seen:
            text = ctx.say(
                f"La vista {name} tiene {len(mine)} líneas ocultas, que suman más de la mitad de la longitud de sus líneas "
                "visibles: no se leen. Usa un corte o un corte parcial (section) y deja las ocultas solo donde aclaran "
                "(view_lines hidden=).",
                f"The {name} view has {len(mine)} hidden lines, adding up to more than half the length of its visible "
                "ones: they cannot be read. Use a section or a partial section and keep hidden lines only where they "
                "clarify (view_lines hidden=).",
            )
            out.append(_finding("hidden_dense", "info", text, name))
    return out


def _cut_through(spec: dict[str, Any], hole: HoleInfo, planes: dict[str, tuple[Any, Any]] | None = None) -> bool:
    """Whether the section ``spec`` runs along ``hole``'s axis. ``planes`` are the cutting planes the sheet drew, by item id:
    the one a section through the part's own axis (``holes="axis"``) stores nothing else of."""
    at, normal = spec.get("at"), spec.get("normal")
    if spec.get("holes") == "axis":
        at, normal = (planes or {}).get(str(spec.get("id")), (None, None))
    elif "holes" in spec:
        wanted = spec["holes"]
        return wanted == "all" or any(hole.on_axis(tuple(p)) for p in wanted)
    if at is None or normal is None:
        return False
    n = math.sqrt(sum(c * c for c in normal)) or 1.0
    d = math.sqrt(sum(c * c for c in hole.direction)) or 1.0
    offset = sum((hole.centre[i] - at[i]) * normal[i] / n for i in range(3))
    return abs(offset) <= 0.05 and abs(sum(hole.direction[i] * normal[i] for i in range(3))) / (n * d) <= 1e-3


def _hidden_features(data: Any) -> list[Any]:
    """Blind holes and counterbores show their depth only as hidden lines: a section through them says it in full."""
    ctx = data.ctx
    sections = sheet_review._of(data, "section")
    planes = {
        str(ctx.extras.get("section_items", {}).get(label)): plane
        for label, plane in ctx.extras.get("section_planes", {}).items()
    }
    names: list[str] = []
    for stack in _stack_list(ctx):
        deep = stack[0].through is False or len(stack) > 1
        if deep and not any(_cut_through(s, h, planes) for s in sections for h in stack):
            names.append(" + ".join(sheet_review._label(h, ctx) for h in stack))
    if not names:
        return []
    shown = "; ".join(names[:4]) + (f" (+{len(names) - 4})" if len(names) > 4 else "")
    text = ctx.say(
        f"Agujeros ciegos o abocardados cuya profundidad solo se ve en líneas ocultas: {shown}. Propón un corte "
        "(section holes=...) que los atraviese, o un corte parcial.",
        f"Blind or counterbored holes whose depth shows only in hidden lines: {shown}. Propose a section "
        "(section holes=...) through them, or a partial section.",
    )
    return [_finding("hidden_feature_no_section", "info", text, "holes")]


def _threads(data: Any) -> list[Any]:
    """A threaded hole is drawn with its thread arc in the view that sees it as a circle: no view looks along it, no
    representation."""
    ctx = data.ctx
    out: list[Any] = []
    arcs = [e for e in data.sheet.entities if isinstance(e, Arc) and e.layer == THREAD]
    for stack in _stack_list(ctx):
        threaded = next((h for h in stack if h.thread is not None), None)
        if threaded is None:
            continue
        shown = [
            v
            for v in ctx.views.values()
            if v.looks_along(threaded.direction)
            and any(math.dist((a.cx, a.cy), v.project(threaded.centre)) < MARK_TOL_MM for a in arcs)
        ]
        if not shown:
            name = " + ".join(sheet_review._label(h, ctx) for h in stack)
            text = ctx.say(
                f"Agujero roscado {name} sin representación de rosca: ninguna vista lo ve como círculo. Añade una vista "
                "que lo muestre de frente (aux_view, section) o una nota con la rosca.",
                f"Threaded hole {name} has no thread representation: no view sees it as a circle. Add a view square-on "
                "to it (aux_view, section) or a note with the thread.",
            )
            out.append(_finding("thread_no_representation", "warning", text, hole_key(stack)))
    return out


def checks(data: Any) -> list[Any]:
    """Every automatic finding of the finished-drawing list."""
    found: list[Any] = []
    for check in (
        _hole_references,
        _fit_holes,
        _fine_tolerances,
        _rounds,
        _marks,
        _text_on_part,
        _notes_missing,
        _material_consistency,
        _hidden_density,
        _hidden_features,
        _threads,
    ):
        found += check(data)
    return found


# --- the questions of judgement -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    id: str
    text: str


def judgement(data: Any) -> list[Question]:
    """The questions no program answers AND no drawing principle of the memory already asks, in the sheet's language. Views,
    sections, dimensions, tolerances and fabrication notes are the base set's ``dibujo-*`` principles (one question
    each is one too many: answering the same thing twice makes the answers rote). What is left has no counterpart:
    callouts, line weights and the assembly. Each is answered with ``applies: <how, here>`` or ``no aplica: <why>``.
    What a sheet must CARRY (the sheet metal's bends included) is the table of requirements of the server, not a question."""
    ctx = data.ctx
    say = ctx.say
    out = [
        Question(
            "q.callouts",
            say(
                "¿Las llamadas dicen cantidad, Ø, profundidad (↧), caja (⌴) o avellanado (⌵) y rosca, y los agujeros de "
                "función distinta (registro, pasador con su ajuste) se distinguen a simple vista?",
                "Do the callouts give quantity, Ø, depth (↧), counterbore (⌴) or countersink (⌵) and thread, and are holes of "
                "a different function (locating, dowel with its fit) told apart at a glance?",
            ),
        ),
        Question(
            "q.line_weights",
            say(
                "¿Los grosores de línea siguen ISO 128 (contorno grueso; cotas, ejes, ocultas y rayado finos) y el texto "
                "tiene altura uniforme? Míralo con drawing(action=capture).",
                "Do the line weights follow ISO 128 (thick outline; thin dimensions, axes, hidden lines and hatching) and is "
                "the text one height? Look at it with drawing(action=capture).",
            ),
        ),
    ]
    if ctx.assembly is not None:
        out.append(
            Question(
                "q.assembly",
                say(
                    "¿El ensamble tiene posiciones con número y guía junto a la tabla, comerciales descritos por norma, "
                    "ajustes de montaje acotados y notas de montaje o calibración?",
                    "Does the assembly have numbered positions with leaders next to the table, commercial parts described "
                    "by standard, assembly fits dimensioned and assembly or calibration notes?",
                ),
            )
        )
    return out  # the sheet metal is a row of the table of requirements (``req.bent_sheet``), not a question here


__all__ = [
    "NON_BLOCKING",
    "Boss",
    "PartFacts",
    "Question",
    "RoundFace",
    "assign_ids",
    "checks",
    "finding_id",
    "hole_key",
    "judgement",
]
