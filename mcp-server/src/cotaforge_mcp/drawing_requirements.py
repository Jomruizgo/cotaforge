# ruff: noqa: E501
"""What the standards ask a shop sheet to carry, activated by what the part has.

The agent that draws knows the standards; what it does is OMIT what applies. So this is not prose to read: it is a table
(``ROWS``, data) and a mechanism, the same one the memory has for its entries (an entry is activated by what is recognised
in the model and must be answered before the milestone). Each row says

* what ACTIVATES it (a fact the CAD measured on the part, or one the design's intent file declares);
* what the sheet must then CARRY, and the standard it comes from (the number: ``clause`` stays empty until somebody has the
  text of the standard open and checks it -- a clause is never written from memory);
* what shows it is MET (facts counted on the sheet, or review findings that stand for "missing");
* the QUESTION to ask when it is not, with a proposed answer.

``evaluate`` lists the rows that apply to THIS part with their state: ``met`` (the sheet carries it), ``missing`` (it is
absent) or ``needs_answer`` (nothing the harness can read says, somebody must). The export waits until every active row is
met or answered. An answer is ``met: <where on the sheet>`` or ``does not apply: <why>``; a ``does not apply`` on a row the
harness MEASURED as applying (a thread exists, a fit is declared, the intent says so) is not accepted unless the engineer
ordered it, with his words quoted (``drawing(action=answer, by=, reason=)``). The same hardening holds for the review
findings that are measurements (``MEASURED_FINDINGS``).

The facts come from the CAD's review (``DrawingSheetInfo.facts``; the FreeCAD worker's ``sheet_reqfacts`` fills them) plus
what the intent file of the part declares (``intent.<row id>``, read here). A CAD that reports no facts gets no rows.
"""

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cotaforge.language import say

from cotaforge_mcp import functional_dimensions as functional
from cotaforge_mcp.measured_alerts import MEASURED_FACT

PREFIX = "req."
"""Every row id starts with it: ``drawing(action=answer, id="req.thread", ...)``."""
MEASURED_ROW = PREFIX + "measured_alerts"
"""The row that holds the export while a measured design alert is open (``measured_alerts``)."""

MET, MISSING, NEEDS_ANSWER = "met", "missing", "needs_answer"


@dataclass(frozen=True)
class Need:
    """A fact of the sheet and what it says in words (Spanish, English)."""

    fact: str
    es: str
    en: str


@dataclass(frozen=True)
class Row:
    id: str
    title: tuple[str, str]
    must: tuple[str, str]
    """What the sheet must carry (Spanish, English)."""
    standard: str
    question: tuple[str, str]
    proposal: tuple[str, str]
    triggers: tuple[str, ...] = ()
    """Facts that activate the row (any of them above 0); none: it applies to every sheet that passes ``only``."""
    only: str = ""
    """``part`` or ``assembly``: the row applies only to that kind of sheet."""
    needs: tuple[Need, ...] = ()
    """Facts of the sheet that must be above 0 for the row to be met."""
    lacks: tuple[Need, ...] = ()
    """Facts that count what is wrong and must be 0 (a fit hole with no callout)."""
    codes: tuple[str, ...] = ()
    """Review findings that stand for this row being missing: the row takes them in (they are shown under it and one answer
    closes them)."""
    measured: tuple[str, ...] = ()
    """Facts that, when above 0, make the activation a MEASUREMENT: the row cannot be dismissed without the engineer's order."""
    always_measured: bool = False
    model_only: bool = False
    """No answer on the sheet closes the row: it is gone when its facts stop activating it (the model was fixed, or the
    engineer's order was recorded in memory, which is where the order of a measured alert lives)."""
    intent: tuple[str, ...] = ()
    """Sections of the intent file that can declare this row."""
    intent_pattern: str = ""
    """What in those sections declares it; a hit also makes the row measured (the designer said so)."""
    functional: bool = False
    """The row is the functional dimensions of the intent file (``functional_dimensions``): it is active on every sheet, a
    decision when the intent declares none, and met when the sheet gives each declared one directly."""
    clause: str = ""
    """The clause or table of ``standard`` this comes from, ONLY when it was checked against the text of the standard."""
    auto: str = ""
    """How the harness meets the row ITSELF when everything it needs is already known (a DETERMINED row; empty: the row needs
    a DECISION, nobody knows the value, so it is a question): ``callouts`` (a callout of every hole: the thread and the fit
    the hole operations declared print by themselves), ``view_lines`` (the centre lines), ``intent_note`` (a note with the
    line of the intent file that declares it, in the designer's own words) or ``title`` (the title block ``create`` fills:
    material, general tolerance, scale, projection) or ``keyway`` (the dimensions of a keyway the walls of the part show:
    width, depth, length, position; its fit class stays a question) or ``parts_list`` (the parts list and a balloon per position on the
    isometric view of an assembly: both are read from its components) or ``chamfers`` (a callout of every chamfer no dimension gives)
    or ``mould`` (the parting line of a moulded part, measured on the model: where the outline of the section is widest, along the
    direction the part is shallowest; the draft, the gate and the ejector zones are decisions, so they stay questions)."""


def _n(fact: str, es: str, en: str) -> Need:
    return Need(fact, es, en)


# The standards are named by number only: no clause is written here until it is verified against the standard's text.
ROWS: tuple[Row, ...] = (
    Row(
        id="req.thread",
        auto="callouts",
        title=("Designación de rosca", "Thread designation"),
        must=(
            "cada rosca lleva su designación (tamaño, paso y la clase de tolerancia si importa) y se dibuja como la norma indica",
            "every thread carries its designation (size, pitch, tolerance class where it matters) and is drawn as the standard shows",
        ),
        standard="ISO 6410",
        question=(
            "¿Qué rosca lleva (designación, paso, clase de tolerancia) y qué longitud útil tiene?",
            "Which thread is it (designation, pitch, tolerance class) and what usable length does it have?",
        ),
        proposal=(
            "M<tamaño> de paso grueso, 6H en el agujero / 6g en el eje, longitud útil igual a la profundidad del taladro",
            "M<size> coarse pitch, 6H in a hole / 6g on a shaft, usable length equal to the drilled depth",
        ),
        triggers=("threaded_holes", "thread_features", "intent.req.thread"),
        needs=(_n("thread_designation_on_sheet", "una designación de rosca impresa (M6, M8x1,25...)", "a printed thread designation (M6, M8x1.25...)"),),
        codes=("thread_no_callout", "thread_no_representation"),
        measured=("threaded_holes",),
        intent=("fits", "mating", "process", "function"),
        intent_pattern=r"rosca|thread|(?<![A-Za-z0-9])M\d{1,3}(?:[x×,.]\d+)?(?![A-Za-z0-9])",
    ),
    Row(
        id="req.fit_class",
        auto="callouts",
        title=("Clase de tolerancia en los ajustes", "Tolerance class on fits"),
        must=(
            "todo agujero o eje con ajuste, y toda cara que acople con una pieza comercial, lleva su clase de tolerancia (H7, g6...) en la cota",
            "every hole or shaft with a fit, and every face that mates with a commercial part, carries its tolerance class (H7, g6...) on the dimension",
        ),
        standard="ISO 286-1, ISO 286-2",
        question=(
            "¿Qué ajuste lleva cada agujero o eje de unión (clase y con qué pieza acopla)?",
            "Which fit does each coupling hole or shaft carry (class, and which part it mates with)?",
        ),
        proposal=(
            "H7 en el agujero y g6 / h6 en el eje, o la clase que pida el catálogo del componente comercial",
            "H7 on the hole and g6 / h6 on the shaft, or the class the catalogue of the commercial component asks for",
        ),
        triggers=("fit_holes", "intent.req.fit_class"),
        needs=(_n("tolerance_classes_on_sheet", "una cota con su clase de tolerancia", "a dimension with its tolerance class"),),
        lacks=(_n("fit_holes_uncalled", "agujeros con ajuste sin llamada", "fit holes with no callout"),),
        codes=("fit_hole_no_tolerance",),
        measured=("fit_holes",),
        intent=("fits", "mating"),
        intent_pattern=r"(?<![A-Za-z0-9])[A-Za-z]{1,2}\d{1,2}(?![A-Za-z0-9])|ajuste|(?<![a-z])fit(?![a-z])|rodamiento|bearing|comercial|commercial|pasador|dowel|retén|seal",
    ),
    Row(
        id="req.general_tolerance",
        auto="title",
        title=("Tolerancia general", "General tolerance"),
        must=(
            "la tolerancia general (ISO 2768 o ISO 22081; ISO 13920 en una construcción soldada) en el rótulo",
            "the general tolerance (ISO 2768 or ISO 22081; ISO 13920 for a welded construction) in the title block",
        ),
        standard="ISO 2768-1, ISO 22081 (ISO 2768-2 is withdrawn), ISO 13920",
        question=(
            "¿Qué tolerancia general lleva la hoja (ISO 2768-mK por defecto; clase ISO 13920 si es soldada)?",
            "Which general tolerance does the sheet carry (ISO 2768-mK by default; an ISO 13920 class if it is welded)?",
        ),
        proposal=("ISO 2768-mK (soldada: ISO 13920 clase B)", "ISO 2768-mK (welded: ISO 13920 class B)"),
        needs=(_n("title_general_tolerance", "la tolerancia general en el rótulo", "the general tolerance in the title block"),),
        always_measured=True,
    ),
    Row(
        id="req.title_block",
        auto="title",
        title=("Rótulo: material, escala y proyección", "Title block: material, scale and projection"),
        must=(
            "el rótulo lleva el material (y su forma de partida), la escala y el símbolo del método de proyección",
            "the title block carries the material (and its starting form), the scale and the projection symbol",
        ),
        standard="ISO 7200, ISO 5455, ISO 128",
        question=(
            "¿De qué material y forma de partida se fabrica (barra, lámina, tocho)?",
            "Which material and starting form is it made from (bar, plate, billet)?",
        ),
        proposal=(
            "el material del documento y la forma de partida del archivo de intención",
            "the material of the document and the starting form from the intent file",
        ),
        needs=(
            _n("title_material", "el material en el rótulo", "the material in the title block"),
            _n("title_scale", "la escala", "the scale"),
            _n("title_projection", "el símbolo de proyección", "the projection symbol"),
        ),
        codes=("notes_material", "material_title_block_empty", "material_inconsistent", "material_only_in_note"),
        always_measured=True,
    ),
    Row(
        id="req.surface_texture",
        auto="intent_note",
        title=("Textura superficial", "Surface texture"),
        must=(
            "la textura superficial (Ra) de las caras funcionales y la rugosidad general de la hoja",
            "the surface texture (Ra) of the functional faces and the general roughness of the sheet",
        ),
        standard="ISO 21920-1 / ISO 1302",
        question=(
            "¿Qué caras mecanizadas son funcionales y qué Ra lleva cada una (y la rugosidad general)?",
            "Which machined faces are functional and which Ra does each carry (and the general roughness)?",
        ),
        proposal=(
            "Ra 1,6 en asientos y caras de ajuste, Ra 3,2 general",
            "Ra 1.6 on seats and fit faces, Ra 3.2 general",
        ),
        only="part",
        needs=(_n("surface_texture_on_sheet", "un símbolo de acabado o una nota de rugosidad", "a finish symbol or a roughness note"),),
        codes=("notes_roughness",),
        measured=("functional_faces",),
        intent=("treatments", "fits", "mating"),
        intent_pattern=r"\bRa\b|rugosidad|roughness|acabado superficial|surface (?:finish|texture)",
    ),
    Row(
        id="req.edge_state",
        auto="intent_note",
        title=("Estado de aristas", "Edge state"),
        must=(
            "el estado de las aristas (romper aristas vivas, sin rebabas, o la cota del chaflán)",
            "the state of the edges (break sharp edges, no burrs, or the size of the chamfer)",
        ),
        standard="ISO 13715",
        question=(
            "¿Qué estado de aristas lleva la pieza (romper 0,3 a 0,5 mm, aristas vivas, chaflán acotado)?",
            "Which edge state does the part carry (break 0.3 to 0.5 mm, sharp edges, a dimensioned chamfer)?",
        ),
        proposal=(
            "«Romper aristas vivas 0,3 a 0,5 mm; sin rebabas» en una placa cortada; en una pieza torneada o fresada se pregunta",
            "'Break sharp edges 0.3 to 0.5 mm; no burrs' on a cut plate; on a turned or milled part it is asked",
        ),
        only="part",
        codes=("notes_edges",),
        intent=("treatments", "process", "function"),
        intent_pattern=r"rebab|desbarb|arista|borde|deburr|edge|chafl|chamfer|burr",
    ),
    Row(
        id="req.heat_treatment",
        auto="intent_note",
        title=("Tratamiento térmico", "Heat treatment"),
        must=(
            "el tratamiento térmico con su dureza, o «sin tratamiento térmico»",
            "the heat treatment with its hardness, or 'no heat treatment'",
        ),
        standard="",
        question=(
            "¿Lleva tratamiento térmico (cuál, con qué dureza y en qué zonas) o ninguno?",
            "Does it carry a heat treatment (which, with what hardness and where) or none?",
        ),
        proposal=("«Sin tratamiento térmico» si el archivo de intención no dice otra cosa", "'No heat treatment' unless the intent file says otherwise"),
        only="part",
        codes=("notes_heat_treatment",),
        intent=("treatments", "process", "material"),
        intent_pattern=r"tratamiento\s+t[eé]rmico|temple|revenid|normaliz|recocid|cementad|nitrur|dureza|\bHRC\b|\bHV\b|heat[\s-]*treat|harden|anneal|temper|stress[\s-]*reliev",
    ),
    Row(
        id="req.coating",
        auto="intent_note",
        title=("Recubrimiento", "Coating"),
        must=("el recubrimiento o la pintura, o «sin recubrimiento»", "the coating or paint, or 'no coating'"),
        standard="",
        question=(
            "¿Lleva recubrimiento o pintura (cuál, espesor, qué zonas se enmascaran) o ninguno?",
            "Does it carry a coating or paint (which, thickness, which areas are masked) or none?",
        ),
        proposal=("«Sin recubrimiento» si el archivo de intención no dice otra cosa", "'No coating' unless the intent file says otherwise"),
        only="part",
        codes=("notes_coating",),
        intent=("treatments", "process", "material"),
        intent_pattern=r"recubrimiento|pintura|pintad|anodiz|galvaniz|zincad|cromad|n[ií]quel|pavonad|fosfat|coating|paint|plating|anodi[sz]|galvani[sz]|powder",
    ),
    Row(
        id="req.marking",
        auto="intent_note",
        title=("Marcado", "Marking"),
        must=(
            "el marcado o la identificación de la pieza (grabado, etiqueta, número de plano)",
            "the marking or identification of the part (engraving, label, drawing number)",
        ),
        standard="",
        question=(
            "¿Cómo se marca o identifica la pieza (grabado, etiqueta, número de plano) o no se marca?",
            "How is the part marked or identified (engraving, label, drawing number) or is it not marked?",
        ),
        proposal=("el número de plano grabado o en etiqueta", "the drawing number engraved or on a label"),
        only="part",
        codes=("notes_marking",),
    ),
    Row(
        id="req.dimensions",
        auto="callouts",
        title=("Tamaño y posición de cada rasgo", "Size and location of every feature"),
        must=(
            "cada agujero, rasgo y radio lleva su tamaño y su posición desde una referencia de la hoja, una vez",
            "every hole, feature and radius carries its size and its location from a reference the sheet has, once",
        ),
        standard="ISO 129-1",
        question=(
            "¿Desde qué cara, arista o eje se cuentan las posiciones y cuál es la cota crítica de cada relación?",
            "Which face, edge or axis are the positions counted from and which dimension is critical in each relation?",
        ),
        proposal=(
            "desde las caras de referencia del archivo de intención; sin referencia se pregunta, nunca desde la esquina de la caja",
            "from the reference faces of the intent file; with no reference it is asked, never from the corner of the box",
        ),
        only="part",
        triggers=("holes", "features", "rounds"),
        codes=(
            "hole_not_located",
            "hole_partly_located",
            "hole_no_reference",
            "hole_no_callout",
            "feature_no_dimension",
            "feature_not_located",
            "round_no_dimension",
        ),
        measured=("holes", "features", "rounds"),
        intent=("datums", "relations", "dimensions"),
    ),
    Row(
        id="req.chamfers",
        auto="chamfers",
        title=("Chaflanes acotados", "Chamfers dimensioned"),
        must=(
            "cada chaflán lleva su tamaño y su ángulo (1 × 45°) en la vista donde se ve de perfil",
            "every chamfer carries its size and angle (1 x 45 deg) on the view where it is seen in profile",
        ),
        standard="ISO 129-1, ISO 13715",
        question=(
            "¿Qué tamaño y ángulo lleva cada chaflán, o se cubre con una nota general de aristas?",
            "Which size and angle does each chamfer carry, or is it covered by a general edge note?",
        ),
        proposal=("el tamaño y el ángulo que mide el modelo", "the size and angle the model measures"),
        only="part",
        triggers=("chamfers",),
        codes=("chamfer_no_dimension",),
        measured=("chamfers",),
    ),
    Row(
        id="req.centre_lines",
        auto="view_lines",
        title=("Ejes y marcas de centro", "Centre lines and centre marks"),
        must=(
            "cada agujero y cada rasgo redondo lleva su marca de centro o su eje",
            "every hole and round feature carries its centre mark or centre line",
        ),
        standard="ISO 128",
        question=(
            "¿Algún agujero o cilindro no lleva eje (view_lines los dibuja todos)?",
            "Does any hole or cylinder lack its axis (view_lines draws them all)?",
        ),
        proposal=("añadir view_lines", "add view_lines"),
        triggers=("holes", "bosses"),  # an assembly too: the sleeves, pins and bores of its components
        codes=("centre_mark_missing", "cylinder_no_axis", "flat_face_unmarked"),  # the last: the diagonals of a flat of a turned part
        measured=("holes", "bosses"),
    ),
    Row(
        id="req.turned_part",
        title=("Pieza torneada: diámetros", "Turned part: diameters"),
        must=(
            "una pieza torneada lleva sus diámetros en el corte o en la vista lateral, y su eje",
            "a turned part carries its diameters on the section or the side view, and its centre line",
        ),
        standard="ISO 129-1",
        question=(
            "¿Qué diámetros del eje son funcionales y cuáles son libres?",
            "Which diameters of the shaft are functional and which are free?",
        ),
        proposal=(
            "todos los escalones con su diámetro en la vista lateral o en el corte, los funcionales con su ajuste",
            "every step with its diameter on the side view or the section, the functional ones with their fit",
        ),
        only="part",
        triggers=("turned",),
        lacks=(_n("boss_radii_undimensioned", "escalones sin cota de diámetro", "steps with no diameter dimension"),),
        measured=("turned",),
    ),
    Row(
        id="req.geometric_tolerance",
        title=("Tolerancias geométricas y datos", "Geometric tolerances and datums"),
        must=(
            "toda tolerancia geométrica cita datos definidos en la hoja y el rasgo está ubicado desde ellos",
            "every geometric tolerance cites datums defined on the sheet and the feature is located from them",
        ),
        standard="ISO 1101, ISO 5459",
        question=(
            "¿Qué caras o ejes son los datos A, B, C y en qué orden se citan?",
            "Which faces or axes are the datums A, B, C and in which order are they cited?",
        ),
        proposal=("los datos del archivo de intención, en el orden de montaje", "the datums of the intent file, in the order of assembly"),
        triggers=("gdt_items",),
        needs=(_n("datum_items", "un dato definido en la hoja", "a datum defined on the sheet"),),
        codes=("datum_undefined", "gdt_not_located", "gdt_as_note", "gdt_location_not_exact", "gdt_axis_not_oriented"),
        measured=("gdt_items",),
    ),
    Row(
        id="req.keyway",
        auto="keyway",
        title=("Chavetero", "Keyway"),
        must=(
            "el chavetero lleva su ancho con la clase de tolerancia, su profundidad (d - t1 en el eje, d + t2 en el cubo), su longitud y su posición",
            "a keyway carries its width with the tolerance class, its depth (d - t1 on a shaft, d + t2 in a hub), its length and its position",
        ),
        standard="DIN 6885-1, ISO 773",
        question=(
            "¿Qué ajuste lleva el ancho del chavetero (el tipo de ajuste de la chaveta: normal, firme o deslizante)? El ancho, la profundidad, la longitud y la posición ya están acotados.",
            "Which fit does the keyway width carry (the fit type of the key: normal, tight or sliding)? Its width, depth, length and position are already dimensioned.",
        ),
        proposal=(
            "ajuste normal: N9 en el eje y JS9 en el cubo; firme: P9 en ambos; deslizante: H9 en el eje y D10 en el cubo (DIN 6885-1, según catálogos; la norma no se ha abierto)",
            "normal fit: N9 on the shaft and JS9 in the hub; tight: P9 on both; sliding: H9 on the shaft and D10 in the hub (DIN 6885-1 as the catalogues list it; the standard was not opened)",
        ),
        triggers=("keyways", "intent.req.keyway"),
        needs=(_n("keyway_fit_on_sheet", "the fit class of the width (N9, P9, JS9, D10...)", "the fit class of the width (N9, P9, JS9, D10...)"),),
        measured=("keyways_measured",),
        intent=("mating", "function", "process", "fits"),
        intent_pattern=r"chavetero|keyway|key\s*seat|key\s*slot|chaveta|(?<![a-z])key(?![a-z])",
    ),
    Row(
        id="req.bent_sheet",
        title=("Chapa doblada", "Bent sheet"),
        must=(
            "radio interior, sentido y ángulo de cada doblez, el desarrollo con sus cotas y el espesor o calibre en una nota",
            "inside radius, direction and angle of each bend, the flat pattern with its dimensions and the thickness or gauge in a note",
        ),
        standard="",
        question=(
            "¿Qué radio interior, sentido y ángulo lleva cada doblez y qué espesor o calibre tiene la chapa?",
            "Which inside radius, direction and angle does each bend carry, and which thickness or gauge is the sheet?",
        ),
        proposal=("radio interior igual al espesor, «hacia abajo 90°» por doblez", "inside radius equal to the thickness, 'down 90 deg' per bend"),
        only="part",
        triggers=("sheet_metal",),
        needs=(_n("bend_note_on_sheet", "una nota o cota de doblez (radio, sentido, desarrollo)", "a bend note or dimension (radius, direction, flat pattern)"),),
        measured=("sheet_metal",),
    ),
    Row(
        id="req.welds",
        auto="intent_note",
        title=("Soldaduras", "Welds"),
        must=(
            "un símbolo de soldadura por unión o grupo de uniones iguales (tamaño, lados, todo alrededor o intermitente) y el nivel de calidad de la soldadura",
            "one weld symbol per joint or group of identical joints (size, sides, all round or intermittent) and the quality level of the weld",
        ),
        standard="ISO 2553, ISO 5817",
        question=(
            "¿Qué soldadura lleva cada unión (tipo, garganta o cateto, lados, todo alrededor o intermitente) y qué nivel de calidad ISO 5817 (B, C o D)?",
            "Which weld does each joint carry (kind, throat or leg, sides, all round or intermittent) and which ISO 5817 quality level (B, C or D)?",
        ),
        proposal=(
            "soldadura de ángulo continua, garganta a = 0,7 × la pared más delgada a la serie normal, nivel ISO 5817 C",
            "continuous fillet weld, throat a = 0.7 x the thinner wall rounded to the standard series, ISO 5817 level C",
        ),
        triggers=("weld_items", "intent.req.welds"),
        needs=(
            _n("weld_marked_on_sheet", "un símbolo de soldadura (ISO 2553)", "a weld symbol (ISO 2553)"),
            _n("weld_class_on_sheet", "el nivel de calidad ISO 5817", "the ISO 5817 quality level"),
        ),
        codes=("weld_joint_unspecified", "weld_class_13920", "weld_duplicate"),
        measured=("weld_items",),
        intent=("welds", "process"),
        intent_pattern=r"solda|weld",
    ),
    Row(
        id="req.moulded_part",
        auto="mould",
        title=("Pieza moldeada por inyección", "Moulded part"),
        must=(
            "la línea de partición en su propio tipo de línea, la dirección de apertura con el desmoldeo general (valor y símbolo), la zona permitida de inyección y las zonas permitidas de marcas de expulsor, cada una con su símbolo, y las excepciones sin desmoldeo o de arista viva donde hagan falta (no solo notas de texto)",
            "the parting line in its own line type, the opening direction with the general draft (value and symbol), the permitted gate zone and the permitted ejector-mark zones, each with its symbol, and the no-draft and sharp-edge exceptions where they are needed (not only notes of text)",
        ),
        standard="ISO 10135",
        question=(
            "¿Qué desmoldeo general lleva la pieza, en qué zona puede entrar el material (inyección) y en cuáles se admiten marcas de expulsor? ¿Qué caras no llevan desmoldeo o llevan arista viva?",
            "Which general draft does the part take, in which zone may the melt enter (gate) and in which are ejector marks allowed? Which faces take no draft or a sharp edge?",
        ),
        proposal=(
            "la dirección de apertura y la línea de partición como las mide el modelo (la línea de partición la añade el arnés), desmoldeo general de 1°, la inyección en la cara que lo permite el archivo de intención y los expulsores en las cabezas de torreta y el suelo",
            "the opening direction and the parting line as the model measures them (the harness adds the parting line), a general draft of 1 deg, the gate on the face the intent file allows and the ejectors on the heads of the bosses and the floor",
        ),
        triggers=("mould_items", "intent.req.moulded_part"),
        needs=(
            _n("mould_parting_on_sheet", "una línea de partición (parting_line, ISO 10135)", "a parting line (parting_line, ISO 10135)"),
            _n("mould_pull_on_sheet", "la dirección de apertura (pull_direction)", "the opening direction (pull_direction)"),
            _n("mould_draft_on_sheet", "el desmoldeo general con su valor (pull_direction con draft_deg)", "the general draft with its value (pull_direction with draft_deg)"),
            _n("mould_gate_on_sheet", "la zona permitida de inyección (mould_zone kind=gate)", "the permitted gate zone (mould_zone kind=gate)"),
            _n("mould_ejector_on_sheet", "las zonas permitidas de expulsor (mould_zone kind=ejector)", "the permitted ejector zones (mould_zone kind=ejector)"),
        ),
        measured=("mould_items",),
        intent=("process", "function"),
        intent_pattern=r"inyecci[oó]n|injection|moldeo|mou?lded|mou?lding",
    ),
    Row(
        id="req.parts_list",
        auto="parts_list",
        title=("Lista de piezas y globos", "Parts list and balloons"),
        must=(
            "una lista de piezas y un globo por posición, con los números de la lista",
            "a parts list and a balloon per position, numbered as the list",
        ),
        standard="ISO 7573, ISO 6433",
        question=(
            "¿Qué piezas forman el conjunto, con qué número de plano o referencia y cantidad?",
            "Which parts make up the assembly, with which drawing number or reference and quantity?",
        ),
        proposal=("una fila por pieza distinta, con su cantidad", "one row per distinct part, with its quantity"),
        triggers=("assembly", "weld_items", "weldment"),
        needs=(
            _n("parts_lists", "una lista de piezas", "a parts list"),
            _n("balloons", "globos", "balloons"),
        ),
        codes=(
            "no_parts_list",
            "parts_without_balloons",
            "balloons_without_row",
            "position_without_balloon",
            "balloon_without_row",
            "parts_list_over_view",
            "instance_hidden",
            "balloons_off_isometric",
            "balloon_part_hidden",
            "weld_list_missing",
            "item_without_balloon",
            "balloon_not_in_list",
            "weld_body_unnamed",
            "weld_designation_mismatch",
            "weld_not_specified",
        ),
        measured=("assembly", "weld_items", "weldment"),
    ),
    Row(
        id="req.quantity_process",
        auto="intent_note",
        title=("Cantidad y proceso", "Quantity and process"),
        must=(
            "la cantidad y el proceso de fabricación en el rótulo o en una nota",
            "the quantity and the manufacturing process in the title block or a note",
        ),
        standard="",
        question=(
            "¿Cuántas piezas se fabrican y con qué proceso (corte láser, fresado, torneado...)?",
            "How many parts are made and by which process (laser cutting, milling, turning...)?",
        ),
        proposal=("la cantidad del pedido y el proceso del archivo de intención", "the quantity of the order and the process of the intent file"),
        only="part",
        needs=(
            _n("quantity_on_sheet", "la cantidad", "the quantity"),
            _n("process_on_sheet", "el proceso", "the process"),
        ),
        intent=("process",),
        intent_pattern=r"cantidad|quantity|\bqty\b|\d+\s*(?:pzas?|pcs|uds?|unidades|piezas)",
    ),
    Row(
        id="req.functional_dimensions",
        functional=True,
        title=("Cotas funcionales", "Functional dimensions"),
        must=(
            "cada medida de la que depende la función (la holgura entre dos placas, la longitud del separador, los diámetros de un ajuste) va en la hoja directamente, entre los dos rasgos que la definen y con su tolerancia o ajuste, nunca como resultado de una cadena; en un conjunto puede estar en el plano de detalle de la pieza si su fila de la lista de piezas cita ese plano",
            "every measure the function depends on (the gap between two plates, the spacer length, the diameters of a fit) is on the sheet directly, between the two features that define it and with its tolerance or fit, never as the result of a chain; in an assembly it may be on the detail sheet of the part if that part's row of the parts list cites that sheet",
        ),
        standard="ISO 129-1",
        question=(
            "¿De qué medidas depende la función (holguras, distancias entre caras, diámetros de ajuste) y con qué valor y tolerancia? Se anotan en la sección de relaciones funcionales del archivo de intención.",
            "Which measures does the function depend on (gaps, distances between faces, fit diameters) and with which value and tolerance? They are written in the functional relations section of the intent file.",
        ),
        proposal=(
            "las medidas que fijan cómo acopla o se separa cada pieza, cada una con su valor; si ninguna es funcional, «no aplica: todas libres» con las palabras del ingeniero",
            "the measures that fix how each part couples or stands apart, each with its value; if none is functional, 'does not apply: all free' with the engineer's words",
        ),
        intent=functional.SECTIONS,
        intent_pattern=functional.MARKER,
    ),
    Row(
        id=MEASURED_ROW,
        title=("Alertas de diseño medidas, resueltas", "Measured design alerts resolved"),
        must=(
            "ninguna alerta de diseño que el arnés midió en el modelo sigue abierta: o se corrigió el modelo, o el ingeniero ordenó mantenerla, con sus palabras citadas",
            "no design alert the harness measured on the model is still open: the model was fixed, or the engineer ordered it kept, his words quoted",
        ),
        standard="",
        question=(
            "¿Corriges el modelo (el arnés vuelve a medir) o el ingeniero ordena mantener lo medido? En ese caso, ¿con qué palabras?",
            "Do you fix the model (the harness measures again) or does the engineer order what was measured kept? In that case, in which words?",
        ),
        proposal=(
            "corregir el modelo; el plano se exporta cuando la alerta ya no se dispara",
            "fix the model; the drawing is exported once the alert no longer fires",
        ),
        triggers=(MEASURED_FACT,),
        lacks=(
            _n(
                MEASURED_FACT,
                "medidas de diseño aún abiertas en el modelo",
                "measured design alert(s) still firing on the model (memory(action=review) lists them)",
            ),
        ),
        measured=(MEASURED_FACT,),
        model_only=True,
    ),
)

ROW_IDS = tuple(r.id for r in ROWS)
SERVER_FACTS = frozenset({MEASURED_FACT})
"""The facts the SERVER measures on the model (the CAD's sheet review does not report them)."""
FACTS_USED = (
    frozenset(
        {t for r in ROWS for t in r.triggers if not t.startswith("intent.")}
        | {r.only for r in ROWS if r.only}
        | {n.fact for r in ROWS for n in (*r.needs, *r.lacks)}
        | {m for r in ROWS for m in r.measured}
    )
    - SERVER_FACTS
)
"""The names of the CAD's facts the table reads (the worker's ``sheet_reqfacts.FACT_NAMES`` must provide every one)."""

MEASURED_FINDINGS = frozenset(
    {
        "hole_not_located",
        "hole_partly_located",
        "hole_no_reference",
        "hole_no_callout",
        "thread_no_callout",
        "thread_no_representation",
        "feature_no_dimension",
        "feature_not_located",
        "gdt_not_located",
        "datum_undefined",
        "stale_reference",
        "dim_no_reference",
        "item_error",
        "callout_contradicts",
        "duplicate_dimension",
        "over_dimensioned",
        "views_overlap",
        "view_over_title_block",
        "view_outside_frame",
        "text_outside_frame",
        "item_outside_frame",
        "item_over_title_block",
        "leader_crossing",
        "leader_crosses_part",
        "leader_crosses_dimension",
        "dimension_inside_outline",
        "material_title_block_empty",
        "material_inconsistent",
        "material_only_in_note",
        "parts_without_balloons",
        "balloons_without_row",
        "position_without_balloon",
        "balloon_without_row",
        "parts_list_over_view",
        "instance_hidden",
        "balloon_part_hidden",
        "weld_list_missing",
        "item_without_balloon",
        "balloon_not_in_list",
        "weld_body_unnamed",
        "weld_designation_mismatch",
        "weld_not_specified",
        "weld_joint_unspecified",
        "weld_class_13920",
        "weld_duplicate",
        "view_adds_nothing",
        "sheet_underused",
        "paper_too_small",
        "part_unnamed",
        "no_isometric",
        "cylinder_no_axis",
        "flat_face_unmarked",
        "chamfer_no_dimension",
    }
)
"""Review findings that state a fact the harness measured on the part or the sheet (a hole with no position, a thread with
no callout, an item that cannot be drawn, two views on top of each other): an answer ``does not apply`` cannot dismiss
them, only the engineer's order can. The ones that are a judgement stay answerable: ``collision`` (accepted when no free
place exists), ``fit_hole_no_tolerance`` (a diameter that LOOKS like a dowel), ``text_over_part``, the ``notes_*`` (a part may have no heat treatment), the style ones."""

NOT_APPLIES = "not_applies"
APPLIES = "applies"


# --- the intent file ---------------------------------------------------------------------------------------------------------

_SECTIONS = {
    "function": ("function", "funcion"),
    "mating": ("mating faces", "caras de union"),
    "fits": ("fits", "ajustes"),
    "datums": ("datums", "datos"),
    "process": ("process", "proceso"),
    "material": ("material",),
    "treatments": ("treatments", "tratamientos"),
    "welds": ("welds", "soldaduras"),
    "dimensions": ("dimensions", "cotas"),
    "relations": ("functional relations", "relaciones funcionales"),
    "sheet": ("sheet", "hoja"),
}
_UNDEFINED = re.compile(r"^(?:undefined|indefinid[oa]|sin definir|por definir|tbd|pendiente|\?+|-+|—+)$", re.I)
_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s+|\*\*)\s*(.+?)\s*\**\s*:?\s*$")


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return re.sub(r"\(.*?\)", "", "".join(c for c in decomposed if not unicodedata.combining(c))).strip().lower()


def _section_of(heading: str) -> str:
    plain = _plain(heading)
    for key, names in _SECTIONS.items():
        if any(plain == n or plain.startswith(n + " ") for n in names):
            return key
    return ""


@dataclass(frozen=True)
class Intent:
    """What the design's intent file says, by section (``sections``); ``path`` is the file it was read from."""

    path: str = ""
    sections: dict[str, str] = field(default_factory=dict)

    def text(self, keys: tuple[str, ...]) -> str:
        return "\n".join(self.sections.get(k, "") for k in keys if self.sections.get(k))

    def declares(self, row: Row) -> str:
        """The line of the intent file that declares ``row`` (``""``: it does not)."""
        if not row.intent_pattern:
            return ""
        pattern = re.compile(row.intent_pattern, re.I)
        for line in self.text(row.intent).splitlines():
            if pattern.search(line):
                return " ".join(line.split()).lstrip("-*• ").strip()
        return ""

    def silent_on(self, row: Row) -> bool:
        """The sections that would carry ``row`` are missing, empty or undefined in the file."""
        return bool(row.intent) and not self.text(row.intent)


def parse_intent(text: str, path: str = "") -> Intent:
    """The sections of an intent file: the headings the drafter reads (Function, Mating faces, Fits, Datums, Process,
    Material, Treatments and finish, Welds, Dimensions, Functional relations, Sheet; the Spanish ones too). A section with
    nothing under it, or ``undefined``, is empty."""
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    sections: dict[str, list[str]] = {}
    current = ""
    for line in body.splitlines():
        found = _HEADING.match(line)
        if found and (key := _section_of(found.group(1))):
            current = key
            sections.setdefault(current, [])
            continue
        if found and line.lstrip().startswith("#"):
            current = ""  # another heading: not one of ours
            continue
        if current and line.strip():
            sections[current].append(line.strip())
    out: dict[str, str] = {}
    for key, lines in sections.items():
        joined = "\n".join(lines).strip()
        if joined and not all(_UNDEFINED.match(ln.lstrip("-*• ").strip()) for ln in lines):
            out[key] = joined
    return Intent(path, out)


def find_intent(workspace: Path, doc_name: str) -> Intent:
    """The intent file of the open part in the design folder: ``drawings/<part>-intent.md`` (older projects:
    ``planos/<part>-intencion.md``). The one that names the open document; with none named, the only one there is."""
    found: list[tuple[Path, str]] = []
    for folder, suffix in (("drawings", "-intent.md"), ("planos", "-intencion.md")):
        base = workspace / folder
        try:
            found += [(p, p.name[: -len(suffix)]) for p in sorted(base.glob(f"*{suffix}")) if p.is_file()]
        except OSError:
            continue
    name = doc_name.lower().removesuffix(".fcstd")
    chosen = next((p for p, part in found if name and (part.lower() == name or name.startswith(part.lower()))), None)
    if chosen is None and len(found) == 1:
        chosen = found[0][0]
    if chosen is None:
        return Intent()
    try:
        return parse_intent(chosen.read_text(encoding="utf-8"), str(chosen))
    except (OSError, UnicodeDecodeError):
        return Intent()


# --- the evaluation -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Requirement:
    """One row that applies to this sheet: its ``state``, why it is active, what is lacking, whether the harness MEASURED the
    activation (``measured``: it cannot be dismissed without the engineer's order), the findings it took in, the answer the
    sheet holds and whether the row is ``closed`` (met, or answered in a way that counts)."""

    row: Row
    state: str
    why: str
    lacking: tuple[str, ...]
    measured: bool
    codes: tuple[str, ...]
    intent_line: str
    answer: Any
    closed: bool
    refused_answer: bool
    auto_items: tuple[str, ...] = ()
    """The items of the sheet the harness added for this row (``rq_...``): they are on the sheet now."""
    model_measured: bool = False
    """The activation is a measurement of the MODEL (a thread, a declared fit...), not only the intent file's words."""
    detail: tuple[str, ...] = ()
    """What the server measured for this row, line by line (the open measured design alerts)."""

    @property
    def id(self) -> str:
        return self.row.id

    @property
    def added(self) -> bool:
        """Met by what the harness itself put on the sheet (an item it added, or the title block ``create`` filled)."""
        return self.state == MET and (bool(self.auto_items) or self.row.auto == "title")


def answer_counts(answer: Any, measured: bool) -> bool:
    """Whether ``answer`` closes something: ``applies`` always; ``not_applies`` unless the harness measured it and the
    engineer did not order it."""
    if answer is None:
        return False
    if answer.verdict == APPLIES:
        return True
    return not measured or bool(getattr(answer, "ordered_by", ""))


def finding_closed(finding: Any) -> bool:
    """A review finding is closed by an answer that counts (``MEASURED_FINDINGS`` cannot be dismissed on the agent's word)."""
    return answer_counts(finding.answer, finding.code in MEASURED_FINDINGS)


def _activated(row: Row, facts: dict[str, int]) -> list[str]:
    return [t for t in row.triggers if facts.get(t, 0) > 0]


def evaluate(
    facts: dict[str, int],
    findings: tuple[Any, ...] | list[Any],
    answers: dict[str, Any],
    intent: Intent,
    item_ids: Collection[str] = (),
    details: dict[str, tuple[str, ...]] | None = None,
    measures: Sequence[Any] = (),
    components: Sequence[Any] = (),
) -> list[Requirement]:
    """The rows that apply to this sheet, in table order, each with its state. ``facts`` empty (the CAD reports none): no
    rows. ``findings`` are the review's, ``answers`` the sheet's by id; ``details`` what the server measured, by row id.
    What it says is for the agent: English."""
    if not facts:
        return []
    merged = dict(facts)
    lines: dict[str, str] = {}
    for row in ROWS:
        hit = intent.declares(row)
        if hit:
            merged[f"intent.{row.id}"] = 1
            lines[row.id] = hit
    out: list[Requirement] = []
    for row in ROWS:
        if row.only and merged.get(row.only, 0) <= 0:
            continue
        active = _activated(row, merged)
        if row.triggers and not active:
            continue
        measured = row.always_measured or any(merged.get(m, 0) > 0 for m in row.measured) or row.id in lines
        if row.always_measured or not row.triggers:
            why = f"every {row.only} sheet" if row.only else "every sheet"
        else:
            why = ", ".join(
                "the intent file declares it" if t.startswith("intent.") else f"{merged[t]} {t.replace('_', ' ')}"
                for t in active
            )
        open_codes = tuple(
            dict.fromkeys(f.code for f in findings if f.code in row.codes and f.blocking and not finding_closed(f))
        )
        lacking = [n.en for n in row.needs if merged.get(n.fact, 0) <= 0]
        lacking += [f"{merged[n.fact]} {n.en}" for n in row.lacks if merged.get(n.fact, 0) > 0]
        state = MISSING if open_codes or lacking else MET if (row.needs or row.lacks or row.codes) else NEEDS_ANSWER
        if row.functional:
            items = functional.declared(intent.text(row.intent))
            lacking = functional.problems(items, measures, components)
            state = MISSING if lacking else MET if items else NEEDS_ANSWER
            measured = bool(items)
            if items:
                why = "the intent file declares it"
        answer = answers.get(row.id)
        counts = answer_counts(answer, measured)
        if row.model_only:  # no answer closes it, not even "met" or an order: the model is fixed, or the order is in memory
            counts = False
        out.append(
            Requirement(
                row=row,
                state=state,
                why=why,
                lacking=tuple(lacking),
                measured=measured,
                codes=open_codes,
                intent_line=lines.get(row.id, ""),
                answer=answer,
                closed=state == MET or counts,
                refused_answer=answer is not None and not counts,
                auto_items=tuple(sorted(i for i in item_ids if owns(row, i))),
                model_measured=row.always_measured or any(merged.get(m, 0) > 0 for m in row.measured),
                detail=(details or {}).get(row.id, ()),
            )
        )
    return out


AUTO_PREFIX = "rq_"
"""The id of every item the harness adds for a row starts with it."""
CALLOUTS_ID = AUTO_PREFIX + "callouts"
VIEW_LINES_ID = AUTO_PREFIX + "view_lines"
KEYWAY_ID = AUTO_PREFIX + "keyway"
CHAMFERS_ID = AUTO_PREFIX + "chamfers"
THREADS_ID = AUTO_PREFIX + "threads"
THREAD_ROW = PREFIX + "thread"
PARTS_LIST_ID = AUTO_PREFIX + "parts_list"
BALLOONS_ID = AUTO_PREFIX + "balloons"
PARTING_ID = AUTO_PREFIX + "parting"
CALLOUT_CODES = frozenset({"thread_no_callout", "hole_no_callout", "fit_hole_no_tolerance"})


def auto_item_id(row: Row) -> str:
    """The id of the item the harness adds to meet ``row`` (empty: it adds none for this row)."""
    if row.auto == "callouts":
        return CALLOUTS_ID
    if row.auto == "view_lines":
        return VIEW_LINES_ID
    if row.auto == "keyway":
        return KEYWAY_ID
    if row.auto == "parts_list":
        return PARTS_LIST_ID
    if row.auto == "chamfers":
        return CHAMFERS_ID
    if row.auto == "mould":
        return PARTING_ID
    if row.auto == "intent_note":
        return AUTO_PREFIX + row.id.removeprefix(PREFIX)
    return ""


def owns(row: Row, item_id: str) -> bool:
    """Whether ``item_id`` is an item the harness added for ``row`` (the keyway's dimensions: ``rq_keyway_w``, ``_t``...)."""
    mine = auto_item_id(row)
    return bool(mine) and (
        item_id == mine
        or (row.auto in ("keyway", "chamfers") and item_id.startswith(mine))
        or (row.auto == "parts_list" and item_id == BALLOONS_ID)
        or (row.id == THREAD_ROW and item_id.startswith(THREADS_ID))
    )


def rows_of_item(item_id: str) -> list[Row]:
    """The rows an item the harness added stands for (the callouts stand for several)."""
    return [r for r in ROWS if item_id and owns(r, item_id)]


@dataclass(frozen=True)
class Addition:
    """An item the harness adds to the sheet by itself because the model or the intent file already says everything it
    needs: ``rows`` are the rows it meets, ``item`` the item as ``drawing(action=add)`` takes it, ``what`` how it is said."""

    rows: tuple[str, ...]
    item: dict[str, Any]
    what: str


def plan_additions(
    rows: list[Requirement],
    items: list[Any],
    assembly: bool = False,
    views: tuple[str, ...] = (),
    external_threads: bool = False,
    weldment: bool = False,
) -> list[Addition]:
    """What the harness adds to the sheet for the DETERMINED rows that are not met: callouts of every hole when the part has
    threaded or fitted holes nobody called out (the thread and the fit come from the hole operations), the centre lines (of an
    assembly without hidden lines), the parts list and a balloon per position on the isometric view of an assembly (both read
    the components of the model), and a note carrying the line of the intent file that declares what the row asks for. A row
    that was answered (the item was removed with a reason) is never added again, and the agent's own items come first:
    callouts are not added while the sheet has a ``hole_dim`` of its own, nor a list while it has a ``parts_list``. With
    ``external_threads`` (the part holds an ``external_thread`` feature) the callout of each threaded cylinder is added too
    (``thread_dims``: a diameter dimension that prints the thread, measured on the model, nothing to decide). A ``weldment``
    (a welded part) gets the balloons only: its cut list carries the designer's designations, so the list is his."""
    have = {i.id for i in items}
    kinds = {i.type for i in items}
    dimensioned = any(i.id.startswith(KEYWAY_ID) for i in items)
    out: dict[str, Addition] = {}
    for r in rows:
        row = r.row
        if external_threads and row.id == THREAD_ROW and r.answer is None and not any(
            i.id.startswith(THREADS_ID) for i in items
        ):
            out[THREADS_ID] = Addition(
                (row.id,),
                {"type": "thread_dims", "id": THREADS_ID},
                "a callout (size, pitch, usable length) of every external thread, measured on the model",
            )
        if row.auto == "parts_list":
            if not (assembly or weldment) or r.state == MET or r.answer is not None or not (r.codes or r.lacking):
                continue
            if assembly and "parts_list" not in kinds and PARTS_LIST_ID not in have:
                out[PARTS_LIST_ID] = Addition(
                    (row.id,),
                    {"type": "parts_list", "id": PARTS_LIST_ID},
                    "the parts list: one row per distinct part with its quantity, read from the components of the model",
                )
            if "balloons" not in kinds and BALLOONS_ID not in have:
                view = "isometric" if "isometric" in views or not views else views[0]
                out[BALLOONS_ID] = Addition(
                    (row.id,),
                    {"type": "balloons", "id": BALLOONS_ID, "view": view},
                    f"a balloon per position on the {view} view, numbered as the list",
                )
            continue
        item_id = auto_item_id(row)
        if not item_id or r.state == MET or r.answer is not None or item_id in have:
            continue
        if row.auto == "mould":
            if "parting_line" not in kinds:  # the engineer's own parting line comes first
                out[PARTING_ID] = Addition(
                    (row.id,),
                    {"type": "parting_line", "id": PARTING_ID},
                    "the parting line in its own line type: the opening direction and the level of the parting plane are measured on "
                    "the model (the part is shallowest along it; the outline of the section is widest there)",
                )
            continue
        if row.auto == "keyway" and (dimensioned or not r.model_measured):
            continue  # only a keyway the walls of the part show (a measurement) is dimensioned by the harness
        wants_callouts = bool(CALLOUT_CODES & set(r.codes)) or any("no callout" in text for text in r.lacking)
        if row.auto == "callouts" and (not wants_callouts or "hole_dim" in kinds):
            continue
        if row.auto == "view_lines" and (not r.codes or "view_lines" in kinds):
            continue
        if row.auto == "chamfers" and "chamfer_no_dimension" not in r.codes:
            continue
        if row.auto == "intent_note" and not r.intent_line:
            continue
        if item_id in out:
            out[item_id] = Addition((*out[item_id].rows, row.id), out[item_id].item, out[item_id].what)
            continue
        if row.auto == "callouts":
            item: dict[str, Any] = {"type": "hole_dim", "id": item_id, "all": True}
            what = "a callout of every hole: the thread and the fit the hole operations declared print with it"
        elif row.auto == "view_lines":
            item, what = {"type": "view_lines", "id": item_id}, "the centre lines and marks of every hole and cylinder"
            if assembly:
                item["hidden"] = []  # the hidden edges of a whole assembly are not what the shop reads: only the axes
        elif row.auto == "chamfers":
            item = {"type": "chamfer_dims", "id": item_id}
            what = "a callout (size x angle) of every chamfer, measured on the model, on the view that shows it in profile"
        elif row.auto == "keyway":
            item = {"type": "keyway_dims", "id": item_id}
            what = "the width, the depth (d - t1 / d + t2), the length and the position of the keyway, measured on the model"
        else:
            item = {"type": "note", "id": item_id, "text": r.intent_line}
            what = f"a note with the intent file's own words: «{r.intent_line}»"
        out[item_id] = Addition((row.id,), item, what)
    return list(out.values())


def claimed_codes(rows: list[Requirement]) -> frozenset[str]:
    """The finding codes the active rows took in: shown under their row, closed with it."""
    return frozenset(c for r in rows for c in r.row.codes)


# --- what is said -------------------------------------------------------------------------------------------------------------


def question_of(req: Requirement, language: str) -> str:
    """The ONE question for the person who knows (the designer, through the channel, or the engineer), in the engineer's
    language, with the proposed answer."""
    row = req.row
    proposal = say(language, row.proposal[0], row.proposal[1])
    if req.intent_line:
        known = say(
            language,
            f"el archivo de intención dice «{req.intent_line}»: confírmalo y ponlo en la hoja",
            f"the intent file says '{req.intent_line}': confirm it and put it on the sheet",
        )
        return known
    return say(language, row.question[0], row.question[1]) + " " + say(language, f"Propuesta: {proposal}", f"Proposal: {proposal}")


def line(req: Requirement, language: str = "es") -> list[str]:
    """The lines that list one row (English for the agent; the question is in the engineer's language). A row that is met is
    one line: the listing is read whole, and what is open is what the agent needs."""
    row = req.row
    tag = (
        "added automatically"
        if req.added
        else "met"
        if req.state == MET
        else "answered"
        if req.closed
        else {MISSING: "MISSING", NEEDS_ANSWER: "NEEDS AN ANSWER"}[req.state]
    )
    standard = f" ({row.standard}{', ' + row.clause if row.clause else ''})" if row.standard else ""
    head = f"  [{tag}] {row.id} — {row.title[1]}{standard}; active: {req.why}" + (" [measured]" if req.measured else "")
    if req.added:
        how = (
            f"item {', '.join(req.auto_items)} (the harness added it from what the model or the intent file says; move it "
            "if it is in the way, remove it only with a reason)"
            if req.auto_items
            else "in the title block (create fills it)"
        )
        return [head, f"      added by the harness: {how}"]
    if req.state == MET:
        return [head]
    out = [head]
    if req.closed and req.answer is not None:
        out.append(f"      answered: {answer_text(req.answer)}")
        return out
    if req.auto_items:
        out.append(f"      added by the harness: {', '.join(req.auto_items)} (what the model already says; the rest is below)")
    out.append(f"      must carry: {row.must[1]}")
    if req.lacking:
        out.append(f"      lacking: {'; '.join(req.lacking)}")
    if req.codes:
        out.append(f"      takes in the findings: {', '.join(req.codes)}")
    out.extend(f"      measured now: {text}" for text in req.detail)
    if row.model_only:
        out.append(
            "      no answer on the sheet closes this row: fix the model (the harness measures again), or, if the ENGINEER "
            "orders it kept, memory(action=answer, id=<the entry above>, by=<his name>, text='does not apply: <his words>')"
        )
    elif req.answer is not None and req.refused_answer:
        out.append(
            "      the answer 'does not apply' is NOT accepted: the harness measured it (or the intent file declares it); "
            "only the engineer's order closes it (by=<him>, reason=<his words>)"
        )
    out.append(f"      ask, one at a time: «{question_of(req, language)}»")
    return out


def answer_text(answer: Any) -> str:
    verdict = "MET" if answer.verdict == APPLIES else "DOES NOT APPLY"
    text = answer.text
    for prefix in ("met:", "cumple:", "applies:", "aplica:", "corregido:", "fixed:", "does not apply:", "no aplica:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    by = f" (ordered by {answer.ordered_by})" if getattr(answer, "ordered_by", "") else ""
    return f"{verdict}: {text}{by}"
