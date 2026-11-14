"""Vocabulary bridge: commercial CAD terms (Spanish and English) -> the op / tool / parameter that does it here.

Every operation of the IR has at least one term here (a test fails otherwise): the table is how an engineer who thinks
in commercial CAD words finds the operation, and ``term_hits`` feeds the "did you mean" of an unknown operation name.
"""

from dataclasses import dataclass

from cotaforge_mcp import catalog


@dataclass(frozen=True)
class Term:
    keys: tuple[str, ...]
    target: str
    """What to call, e.g. ``op extrude`` or ``inspect properties``."""
    detail: str = ""
    op: str | None = None
    """Operation name, when the target is an op: a term whose op is not in this build is shown as unavailable."""


TERMS: tuple[Term, ...] = (
    Term(
        ("croquis", "sketch", "boceto"),
        "op sketch",
        "entities rect (chamfer_mm: corners)/square/circle/slot/line/arc; on a plane or a face",
        "sketch",
    ),
    Term(
        ("rectángulo", "rectángulo por 2 puntos", "rectangle", "corner rectangle"),
        "op sketch",
        "entity kind=rect (center,width,height)",
        None,
    ),
    Term(("rectángulo desde centro", "center rectangle"), "op sketch", "entity kind=rect: center is the centre", None),
    Term(("círculo", "circle"), "op sketch", "entity kind=circle (center,diameter)", None),
    Term(("línea", "line"), "op sketch", "entity kind=line chained end to end into a closed loop", None),
    Term(
        ("arco por 3 puntos", "3 point arc", "arco con centro", "centerpoint arc", "arco", "arc"),
        "op sketch",
        "entity kind=arc3 (start,through,end) | arc_ends (center,start,end,ccw) | "
        "arc (center,radius,start_deg,end_deg); chain with lines",
        None,
    ),
    Term(
        ("ranura", "ranura recta", "slot", "straight slot"),
        "op sketch",
        "entity kind=slot (start,end = centres of the round ends, width); cut it for a slot in a part",
        None,
    ),
    Term(
        ("cota inteligente", "smart dimension", "dimension"),
        "op sketch",
        "named_dimensions: {name, entity, attribute}",
        None,
    ),
    Term(
        ("relaciones geometricas", "geometric relations", "sketch relations"),
        "op sketch",
        "constraints: {kind: horizontal|vertical|coincident|concentric|equal|tangent|symmetric, entities}",
        None,
    ),
    Term(
        ("plano de referencia", "reference plane", "plano"),
        "op ref_plane",
        "define: offset | angle | three_points | normal_to | midplane; then sketch on it (plane from_op)",
        "ref_plane",
    ),
    Term(
        ("eje de referencia", "reference axis", "eje"),
        "op ref_axis",
        "define: two_points | point_direction | entity | plane_intersection",
        "ref_axis",
    ),
    Term(
        ("extruir saliente", "extruir base", "saliente extruido", "boss extrude", "extruded boss", "extrude boss/base"),
        "op extrude",
        "sketch, depth_mm, direction, merge",
        "extrude",
    ),
    Term(
        ("extruir corte", "corte extruido", "extruded cut", "cut-extrude"),
        "op cut",
        "sketch, end_condition blind|through_all, depth_mm",
        "cut",
    ),
    Term(
        ("saliente por revolución", "revolución saliente", "revolved boss", "revolve boss/base"),
        "op revolve",
        "sketch, axis u|v|<line name>, angle_deg",
        "revolve",
    ),
    Term(
        ("corte por revolución", "revolved cut"),
        "-",
        "no dedicated op: revolve a cutter body and boolean_subtract it",
        None,
    ),
    Term(
        ("barrido saliente", "barrido", "sweep", "swept boss"),
        "op sweep",
        "sketch (profile), path {points}, merge",
        "sweep",
    ),
    Term(("recubrir", "loft", "recubrimiento saliente"), "-", "no loft op in this build", None),
    Term(
        ("asistente para taladro", "taladro", "hole wizard", "hole"),
        "op hole",
        "face, position (u,v), standard, diameter_mm, end_condition",
        "hole",
    ),
    Term(("redondeo", "empalme", "fillet"), "op fillet", "edges (refs or an edge set), radius_mm", "fillet"),
    Term(
        ("chaflán", "chamfer"),
        "op chamfer",
        "edges (refs or an edge set), distance1_mm (distance2_mm + distance1_on)",
        "chamfer",
    ),
    Term(
        ("matriz lineal", "linear pattern"),
        "op linear_pattern",
        "features, direction, spacing_mm, count (+ direction2/spacing2_mm/count2)",
        "linear_pattern",
    ),
    Term(
        ("matriz circular", "circular pattern"),
        "op circular_pattern",
        "features, axis, count, angle_deg",
        "circular_pattern",
    ),
    Term(("vaciado", "shell"), "op shell", "faces to remove, thickness_mm, direction inward|outward", "shell"),
    Term(
        ("extracción", "ángulo de salida", "draft"),
        "op draft",
        "faces, neutral plane, pull direction, angle_deg",
        "draft",
    ),
    Term(("simetría", "mirror"), "op mirror", "features (seeds in one body), plane", "mirror"),
    Term(
        ("simetría de cuerpo", "mirror bodies", "espejo de cuerpo"),
        "op mirror_body",
        "source body, plane: a new mirrored body",
        "mirror_body",
    ),
    Term(
        ("contracción", "merma", "shrinkage", "cavidad", "cavity"),
        "op shrink_body",
        "source body, shrinkage_percent (a number or [x, y, z]), factor_source (datasheet, moulder, engineer)",
        "shrink_body",
    ),
    Term(
        ("análisis de desmoldeo", "contrasalida", "draft analysis", "undercut"),
        "inspect draft",
        "pull [x, y, z], min_draft_deg, paint: the draft of every face and the faces that cannot be released",
    ),
    Term(
        ("superficie de partición", "postizo", "macho y cavidad", "core and cavity"),
        "op mould_split",
        "source (shrunk part), pull, block_size_mm or margin_mm, zero_draft_to, slides; see inspect parting",
        "mould_split",
    ),
    Term(
        ("línea de partición", "parting line", "parting analysis"),
        "inspect parting",
        "pull [x, y, z], id (core|cavity for walls with no draft): the parting loops, the walls to decide, the slides",
    ),
    Term(("dividir", "split", "partir"), "op split", "source body, plane, keep both|positive|negative", "split"),
    Term(
        ("copiar cuerpo", "move/copy bodies", "mover/copiar cuerpos"),
        "op copy_body",
        "source, offset, rotate, scale",
        "copy_body",
    ),
    Term(
        ("brida base", "brida base/pestaña", "base flange", "base-flange", "base-flange/tab"),
        "op base_flange",
        "sketch (outline; holes as closed profiles inside), thickness_mm, direction",
        "base_flange",
    ),
    Term(
        ("brida de arista", "brida de borde", "labio", "pestaña de arista", "edge flange", "edge-flange"),
        "op edge_flange",
        "edge, length_mm, angle_deg, bend_radius_mm, position, length_from",
        "edge_flange",
    ),
    Term(
        ("desplegar", "desdoblar", "aplanar", "patrón plano", "desarrollo plano", "flat pattern", "unfold", "flatten"),
        "op unfold",
        "face (stays fixed), k_factor (required); then export dxf flat=<step id>",
        "unfold",
    ),
    Term(
        ("factor k", "k-factor", "k factor"),
        "-",
        "no default: op unfold takes k_factor (ANSI, 0..1, usually 0.3-0.5); ask the engineer or the shop",
        None,
    ),
    Term(("caja", "box"), "op box", "size [x,y,z], placement", "box"),
    Term(("cilindro", "cylinder"), "op cylinder", "diameter_mm, height_mm, axis, origin", "cylinder"),
    Term(("combinar", "union", "suma"), "op boolean_union", "target, tools (bodies)", "boolean_union"),
    Term(("sustraer", "resta", "subtract"), "op boolean_subtract", "target, tools, keep_tools", "boolean_subtract"),
    Term(
        ("intersección", "intersect", "común", "common"), "op boolean_intersect", "target, tools", "boolean_intersect"
    ),
    Term(("mover cuerpo", "move body", "translate"), "op translate", "target, offset", "translate"),
    Term(("girar cuerpo", "rotate body", "rotate"), "op rotate", "target, axis, angle_deg, about", "rotate"),
    Term(
        ("insertar componentes", "insert components", "insertar componente"),
        "op insert_component",
        "part (document), placement",
        "insert_component",
    ),
    Term(("fijo", "fijar componente", "fix component"), "op fix_component", "component", "fix_component"),
    Term(
        ("mover componente", "move component", "colocar componente"),
        "op place_component",
        "component (insert step), placement: absolute pose",
        "place_component",
    ),
    Term(
        ("junta", "articulación", "bisagra", "joint", "hinge", "revolute", "slider", "guía"),
        "op add_joint",
        "type fixed|revolute|slider, b (moves), a (omit = ground), axis, min, max",
        "add_joint",
    ),
    Term(
        ("mover junta", "abrir junta", "set joint", "joint value", "ángulo de la junta"),
        "op set_joint",
        "joint (add_joint step), value (deg or mm)",
        "set_joint",
    ),
    Term(
        ("relación concéntrica", "concentric mate", "concéntrica"),
        "op mate_concentric",
        "a, b: two cylindrical faces",
        "mate_concentric",
    ),
    Term(
        ("relación coincidente", "coincident mate", "coincidente"),
        "op mate_coincident",
        "a, b: two planar faces",
        "mate_coincident",
    ),
    Term(
        ("relación de distancia", "distance mate", "distancia"),
        "op mate_distance",
        "a, b: two planar faces, distance_mm",
        "mate_distance",
    ),
    Term(
        ("detección de interferencia", "interference detection", "interferencia"),
        "op check_interference",
        "components (default all); inspect interference: components, or the bodies of a part",
        "check_interference",
    ),
    Term(("medir", "measure"), "inspect measure", "bounding box and per-body volume", None),
    Term(
        ("propiedades físicas", "mass properties", "propiedades de masa"),
        "inspect properties",
        "volume, centroid, surface, inertia",
        None,
    ),
    Term(
        ("árbol de operaciones", "featuremanager", "feature tree"), "inspect tree", "features with their step ids", None
    ),
    Term(("deshacer", "undo"), "undo", "undo() last step, undo(to=<id>) back to a step", None),
    Term(("guardar", "save"), "doc save", "path (default: the open file or a new one in the workspace)", None),
    Term(("abrir", "open"), "doc open", "any native file of the CAD, with or without the harness stamp", None),
    Term(
        ("senalar", "mostrar", "seleccionar", "show", "select"),
        "view show / view selection",
        "show faces/edges/sheet items to the engineer; read his selection (3D or sheet)",
        None,
    ),
    Term(
        ("capturar", "captura de pantalla", "screenshot", "capture"),
        "view capture",
        "needs the visible CAD (COTAFORGE_VISIBLE=1)",
        None,
    ),
    Term(("exportar step", "guardar como step", "export step"), "export step", "path", None),
    Term(("exportar stl", "malla", "export stl", "mesh"), "export mesh", "path, deflection_mm", None),
    Term(
        ("exportar dxf", "dxf", "export dxf", "corte láser", "laser cut"),
        "export dxf",
        "face=<id or ref> (a planar face) or flat=<unfold step id>; overwrite",
        None,
    ),
)


def _norm(text: str) -> str:
    table = str.maketrans("áéíóúüñÁÉÍÓÚ", "aeiouunAEIOU")
    return text.translate(table).strip().lower()


def term_hits(query: str) -> list[str]:
    """Op names suggested by a free-text query (for unknown-op errors)."""
    needle = _norm(query)
    hits: list[str] = []
    for term in TERMS:
        if term.op and any(needle == _norm(k) or needle in _norm(k) for k in term.keys):
            hits.append(term.op)
    return list(dict.fromkeys(hits))


def terms_table(available: list[str], reasons: dict[str, str] | None = None) -> str:
    """``reasons`` maps an operation the CAD could offer but cannot right now to why and how to get it."""
    reasons = reasons or {}
    lines = ["term -> call (parameters)"]
    for term in TERMS:
        names = " / ".join(term.keys[:4])
        if term.op is not None and term.op not in available:
            lines.append(
                f"{names} -> not available with this CAD" + (f": {reasons[term.op]}" if term.op in reasons else "")
            )
        elif term.target == "-":
            lines.append(f"{names} -> {term.detail}")
        else:
            lines.append(f"{names} -> {term.target} ({term.detail})")
    return "\n".join(lines)


def terms_of(op: str) -> list[Term]:
    """Every term that maps to the operation ``op``."""
    return [t for t in TERMS if t.op == op]


def known_op_names() -> set[str]:
    return set(catalog.op_names())
