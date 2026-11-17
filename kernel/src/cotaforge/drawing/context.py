# ruff: noqa: E501
"""The sheet context: what this drawing is of, joined in one place from what the harness already measures and reads.

``build`` takes the facts the CAD counted on the part and the sheet (the worker's ``sheet_reqfacts``) and the sections
of the design's intent file, and says which catalog sheet type, processes and features this drawing has, plus the
material and the quantity. What neither the model nor the intent file decides is an open question, never a guess.
The plan (``plan.py``) crosses this context with the rule catalog. Nothing is stored: it is built from the page and the
intent file every time.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

PROCESS_WORDS: dict[str, str] = {
    "turning": r"tornea|\bturn(?:ed|ing)?\b|\blathe\b|\btorno\b",
    "milling": r"fresa|\bmill(?:ed|ing)?\b",
    "grinding": r"rectific|\bgrind|\bground\b",
    "laser_plasma_water": r"laser|plasma|chorro de agua|water\s?jet|oxicorte",
    "wire_edm": r"\bhilo\b|wire[\s-]*(?:cut|edm)|\bwedm\b",
    "sinker_edm": r"penetracion|sinker|die[\s-]*sink",
    "casting": r"\bfundi|\bcast(?:ing)?\b",
    "hand_tools": r"(?<!soldadura )(?<!welding )\bmanual(?!\s+(?:welding|weld|soldadura))|a mano\b|\blima(?:do)?\b|by hand|hand tools?",
    "sheet_metal_bending": r"dobl|plega|\bbend|\bbent\b|\bfold",
    "welding": r"solda|\bweld",
    "injection_moulding": r"inyec|injection|mo(?:u)?ld(?:ed|ing)",
    "machining": r"mecaniza|machin|taladr|\bdrill|escaria|\bream|rosca|\btap(?:ped|ping)?\b",
}
"""The words of the process section that name each catalog process (matched on unaccented lower case)."""
QUESTION_PREFIX = "plan."
"""The id of an open question of the context starts with it (``plan.process``): the sheet keeps its answer."""
QUESTION_KEYS = ("type", "process", "material", "quantity", "datums", "relations", "fits")
DESIGN_KEYS = ("process", "relations", "fits")
"""The questions whose answer is a design fact (the route the part takes, what the function depends on, what seats on
what): the intent file states them, or the engineer orders them (``by``). The drafter's own word closes none of them."""
MACHINING = frozenset({"turning", "milling", "grinding"})

_FIT_CLASS = re.compile(r"(?<![A-Za-z0-9])(?:[A-HJ-LNP-Z]{1,2}|[a-hj-np-z]{1,2})\d{1,2}(?![A-Za-z0-9])")
_THREAD = re.compile(r"(?<![A-Za-z0-9])M\d{1,3}(?:\s?[x×]\s?\d+(?:[.,]\d+)?)?\b|\brosca\b|\bthread", re.I)
_BEARING = re.compile(r"rodamiento|cojinete|\bbearing", re.I)
_EDGE_FORM = re.compile(r"garganta|salida de rosca|ranura de salida|undercut|relief groove|din\s?509|din\s?76", re.I)
_GEOMETRIC = re.compile(
    r"coaxial|concentr|perpendicular|paralel|parallel|planitud|flatness|circularidad|roundness|cilindricidad|"
    r"cylindricity|salto|runout|run-out|posicion de|true position",
    re.I,
)
_NOTHING = re.compile(r"^\W*(?:no aplica|n/?a|ningun[oa]?|none|not applicable)\b", re.I)


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


@dataclass(frozen=True)
class Question:
    """Something the sheet depends on that nobody declared: ``step`` is where the work stops for it. ``key`` is the
    section of the intent file that would say it; the answer to the question is recorded under ``id``."""

    step: str
    about: str
    text: str
    key: str = ""

    @property
    def id(self) -> str:
        return f"{QUESTION_PREFIX}{self.key}"


@dataclass(frozen=True)
class SheetContext:
    sheet_type: str
    processes: tuple[str, ...]
    features: tuple[str, ...]
    material: str
    quantity: str
    questions: tuple[Question, ...]

    def open_at(self, step: str) -> tuple[Question, ...]:
        return tuple(q for q in self.questions if q.step == step)


def processes_in(text: str) -> tuple[str, ...]:
    """The catalog processes a process section names, in catalog order; a specific machining process implies machining."""
    plain = _plain(text)
    found = {name for name, words in PROCESS_WORDS.items() if re.search(words, plain)}
    if found & MACHINING:
        found.add("machining")
    return tuple(name for name in PROCESS_WORDS if name in found)


def _said(sections: Mapping[str, str], key: str) -> str:
    text = " ".join((sections.get(key) or "").split()).lstrip("-*• ").strip()
    return "" if _NOTHING.match(_plain(text)) else text


def build(facts: Mapping[str, int], sections: Mapping[str, str], title_material: str = "") -> SheetContext:
    """The context of one sheet. ``facts``: the counts of the CAD's review (empty: this CAD reports none); ``sections``:
    the intent file by section key (function, mating, fits, datums, process, material, relations, quantity...);
    ``title_material``: the material the title block already prints."""

    def has(name: str) -> bool:
        return facts.get(name, 0) > 0

    everything = "\n".join(sections.values())
    questions: list[Question] = []

    if has("weldment"):
        sheet_type = "welded"
    elif has("assembly"):
        sheet_type = "assembly"
    elif has("part"):
        sheet_type = "part"
    else:
        sheet_type = ""
        questions.append(
            Question("purpose", "sheet type", "the CAD does not say whether this is a part, a welded part or an assembly", "type")
        )

    process_text = _said(sections, "process")
    found = set(processes_in(process_text))
    if has("sheet_metal"):
        found.add("sheet_metal_bending")
    if has("weldment"):
        found.add("welding")
    if has("mould_items"):
        found.add("injection_moulding")
    if not process_text and sheet_type != "assembly":
        questions.append(
            Question(
                "purpose",
                "process",
                "the intent file declares no process or route: it sets who reads the sheet, what is delivered and "
                "which rules apply",
                "process",
            )
        )
    elif process_text and not found:
        questions.append(
            Question("purpose", "process", f"the process given names none the harness knows: {process_text!r}", "process")
        )

    material = _said(sections, "material") or title_material.strip()
    if not material and sheet_type != "assembly":
        questions.append(Question("purpose", "material", "neither the intent file nor the title block gives the material", "material"))
    quantity = _said(sections, "quantity")
    if not quantity and not has("quantity_on_sheet"):
        questions.append(Question("purpose", "quantity", "neither the intent file nor the sheet gives the quantity", "quantity"))

    turned = has("turned")
    holes = has("holes")
    fits_text = _said(sections, "fits")
    relations = _said(sections, "relations")
    datums = _said(sections, "datums")
    features = {
        "hole": holes,
        "hole_group": has("hole_groups"),
        "circular_pattern": has("circular_patterns"),
        "thread": has("threaded_holes") or has("thread_features") or bool(_THREAD.search(fits_text)),
        "fit": has("fit_holes") or bool(_FIT_CLASS.search(fits_text)),
        "revolution_body": turned,
        "flat_beside_axis": has("flats"),
        "keyway": has("keyways"),
        "chamfer": has("chamfers"),
        "round": has("rounds"),
        "edge_form": bool(_EDGE_FORM.search(_plain(everything))),
        "bearing_seat": bool(_BEARING.search(everything)) and (turned or holes),
        "datum": has("datum_items") or bool(datums),
        "geometric_tolerance": has("gdt_items") or bool(_GEOMETRIC.search(_plain(relations))),
        "functional_relation": bool(relations),
        "weld_joint": has("weldment") or has("weld_items"),
        "solid_part_in_section": sheet_type in ("assembly", "welded") and has("sections"),
        "parting_line": has("mould_items"),
        "bend": has("sheet_metal"),
        "section": has("sections"),
        "part_in_assembly": sheet_type == "assembly",
    }
    if not datums and (holes or turned or sheet_type != "part"):
        questions.append(
            Question("references", "datums", "the intent file declares no datums: where the positions are counted from", "datums")
        )
    if not relations:
        questions.append(
            Question(
                "dimensions",
                "functional relations",
                "the intent file declares no functional relation: which measures the function depends on, and from what",
                "relations",
            )
        )
    if not fits_text and (turned or has("fit_holes")):
        questions.append(
            Question("tolerances", "fits", "the intent file declares no fits: which diameters seat or locate another part", "fits")
        )
    return SheetContext(
        sheet_type=sheet_type,
        processes=tuple(name for name in PROCESS_WORDS if name in found),
        features=tuple(name for name, present in features.items() if present),
        material=material,
        quantity=quantity,
        questions=tuple(questions),
    )
