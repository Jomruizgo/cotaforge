# ruff: noqa: E501, SLF001
"""What the table of requirements of a drawing reads from this sheet and this part (no FreeCAD import).

The table lives in the MCP server (``cotaforge_mcp.drawing_requirements``: a row per thing the standards ask a sheet to
carry, with what activates it, what shows it is met and the question to ask when it is not). This module is the CAD's half:
``facts`` counts, on the part the sheet is of and on the sheet itself, what those rows name, and the server evaluates the
rows. A fact is a plain integer (a count or 0/1) under a stable name; the names are the vocabulary the two sides share
(``FACT_NAMES``, checked against the table by a test). When something cannot be told (an assembly has no rounds, a part too
complex to test) its fact is 0: nothing is guessed in either direction.

The facts about the PART (threaded holes, fit holes, rounds, bosses, a turned part, sheet metal, welds, keyways) are what
the harness MEASURED, so an answer cannot dismiss the row they activate unless the engineer orders it; the facts about the
SHEET (a thread designation printed, a class on a dimension, a note found) are what shows a row is met.
"""

import re
from typing import Any

import sheet_checks
import sheet_review
from items_holes import _select as select_callout_holes
from sheet_context import ItemError

FACT_NAMES = (
    "sheet",
    "part",
    "assembly",
    "weldment",
    "holes",
    "threaded_holes",
    "fit_holes",
    "fit_holes_uncalled",
    "tolerance_classes_on_sheet",
    "thread_designation_on_sheet",
    "functional_faces",
    "surface_texture_on_sheet",
    "features",
    "rounds",
    "bosses",
    "turned",
    "boss_radii_undimensioned",
    "sheet_metal",
    "bend_note_on_sheet",
    "gdt_items",
    "datum_items",
    "weld_items",
    "weld_marked_on_sheet",
    "weld_class_on_sheet",
    "keyways",
    "keyways_measured",
    "keyway_fit_on_sheet",
    "thread_features",
    "title_material",
    "title_general_tolerance",
    "title_scale",
    "title_projection",
    "quantity_on_sheet",
    "process_on_sheet",
    "parts_lists",
    "balloons",
    "chamfers",
)

THREAD_PRINTED = re.compile(r"(?<![A-Za-z0-9])M\s?\d{1,3}(?:\s?[x×]\s?\d+(?:[.,]\d+)?)?|UN[CF]\b|\bNPT\b|\bBSP\b|\bG\s?\d/\d", re.I)
KEYWAY_FIT = re.compile(r"(?<![A-Za-z0-9])(?:P9|N9|JS9|Js9|D10)(?![A-Za-z0-9])")  # the classes of a keyway width (DIN 6885-1)
KEYWAY = re.compile(r"keyway|key\s*seat|key\s*slot|chavetero|ranura\s+de\s+chaveta|asiento\s+de\s+chaveta", re.I)
BEND = re.compile(r"doblez|doblado|pliegue|bend|fold|radio\s+interior|inside\s+radius|desarrollo|flat\s+pattern", re.I)
WELD_NOTE = re.compile(r"solda|weld", re.I)
WELD_CLASS = re.compile(r"13920", re.I)
QUANTITY = re.compile(r"cantidad|quantity|\bqty\b|\b\d+\s*(?:pzas?|pcs|uds?|unidades|piezas|off)\b|\bx\s?\d+\s*(?:pzas?|pcs|uds?)\b", re.I)
PROCESS = re.compile(
    r"proceso|process|mecaniz|machin|torne|turn(?:ed|ing)|fres|mill|corte\s+(?:l[aá]ser|por\s+agua|plasma)|laser|waterjet|plasma|"
    r"estamp|stamp|fundic|cast|forj|forg|solda|weld|doblez|sheet\s*metal|chapa|inyecci|injection|impres|print",
    re.I,
)


def _stacks(data: Any) -> list[list[Any]]:
    return sheet_checks._stack_list(data.ctx)


def _called(data: Any) -> set[int]:
    """The holes some ``hole_dim`` of the sheet calls out."""
    out: set[int] = set()
    for spec in sheet_review._of(data, "hole_dim"):
        try:
            out |= {h.index for h in select_callout_holes(data.ctx, spec)}
        except ItemError:
            continue
    return out


def _fit_stack(stack: list[Any]) -> bool:
    first = stack[0]
    return first.thread is None and (bool(first.fit) or first.hole_class == "precision")


def _toleranced_items(data: Any) -> int:
    return sum(
        1
        for kind in ("hole_dim", "dim")
        for spec in sheet_review._of(data, kind)
        if "fit" in spec or "tol" in spec
    )


def _boss_radii_undimensioned(data: Any) -> int:
    """Distinct radii of the whole convex cylinders of the part (shaft steps, pins, bosses) that no diameter or radius
    dimension, no overall diameter and no note of the sheet states."""
    ctx = data.ctx
    facts = ctx.extras.get("facts")
    if facts is None or not facts.bosses:
        return 0
    said: set[float] = set()
    for spec in sheet_review._of(data, "dim"):
        if spec.get("kind") in ("radius", "diameter"):
            try:
                radius = ctx.geometry.face(spec["face"][0]).radius
            except ItemError:
                continue
            if radius is not None:
                said.add(round(radius, 2))
    try:
        found = ctx.geometry.round_extent()
    except Exception:  # noqa: BLE001 - an overall size that cannot be told is simply not counted
        found = None
    if found is not None and sheet_review._of(data, "overall"):
        said.add(round(found[1] / 2, 2))
    for text in sheet_checks._notes(data):
        for diameter in re.findall(r"[Ø⌀∅]\s*(\d+(?:[.,]\d+)?)", text):
            said.add(round(float(diameter.replace(",", ".")) / 2, 2))
    return len({round(b.radius, 2) for b in facts.bosses} - said)


def _round_extent(data: Any) -> bool:
    ctx = data.ctx
    if ctx.extras.get("facts") is None or ctx.assembly is not None:  # an assembly is not a turned part
        return False
    try:
        return ctx.geometry.round_extent() is not None
    except Exception:  # noqa: BLE001 - a part that cannot be tested is not called turned
        return False


def facts(data: Any) -> dict[str, int]:
    """The counts of ``FACT_NAMES`` for the sheet ``data`` (a ``sheet_review.ReviewInput``)."""
    ctx = data.ctx
    part = ctx.extras.get("facts")
    texts = [*data.sheet.texts()]
    notes = sheet_checks._notes(data)
    stacks = _stacks(data)
    called = _called(data)
    fit = [s for s in stacks if _fit_stack(s)]
    toleranced = _toleranced_items(data)
    gdt = len(sheet_review._of(data, "gdt"))
    finishes = len(sheet_review._of(data, "finish"))
    roughness_note = sum(1 for n in notes if sheet_checks._ROUGHNESS.search(n))
    material = sheet_checks._material_text(data)
    fields = sheet_checks._fields(ctx)
    printed = "\n".join(texts)
    out = {
        "sheet": 1,
        "part": 0 if ctx.assembly is not None else 1,
        "assembly": 1 if ctx.assembly is not None else 0,
        "weldment": 1 if ctx.weldment is not None else 0,
        "holes": len(stacks),
        "threaded_holes": sum(1 for s in stacks if any(h.thread is not None for h in s)),
        "fit_holes": len(fit),
        "fit_holes_uncalled": sum(1 for s in fit if not {h.index for h in s} & called),
        "tolerance_classes_on_sheet": toleranced + sum(1 for s in fit if {h.index for h in s} & called),  # a callout shows the declared fit
        "thread_designation_on_sheet": 1 if THREAD_PRINTED.search(printed) else 0,
        "functional_faces": len(fit) + toleranced + gdt,
        "surface_texture_on_sheet": finishes + roughness_note,
        "features": len(data.features),
        "rounds": len(part.rounds) if part is not None else 0,
        "bosses": len(part.bosses) if part is not None else 0,
        "turned": 1 if _round_extent(data) else 0,
        "boss_radii_undimensioned": _boss_radii_undimensioned(data),
        "sheet_metal": 1 if part is not None and part.sheet_metal else 0,
        "bend_note_on_sheet": 1 if any(BEND.search(n) for n in notes) else 0,
        "gdt_items": gdt,
        "datum_items": len(sheet_review._of(data, "datum")),
        "weld_items": len(sheet_review._of(data, "weld")),
        "weld_marked_on_sheet": len(sheet_review._of(data, "weld")) + (1 if WELD_NOTE.search("\n".join(notes)) else 0),
        "weld_class_on_sheet": 1 if WELD_CLASS.search(printed) else 0,
        "keyways": (part.keyways if part is not None else 0)
        or sum(1 for f in data.features if KEYWAY.search(f"{f.id} {f.label}")),  # by geometry, else by name
        "keyways_measured": part.keyways if part is not None else 0,
        "keyway_fit_on_sheet": 1 if KEYWAY_FIT.search(printed) else 0,
        "thread_features": part.thread_features if part is not None else 0,
        "title_material": 1 if material else 0,
        "title_general_tolerance": 1 if str(fields.get("general_tolerances", "")).strip() else 0,
        "title_scale": 1 if float(ctx.extras.get("main_scale") or 0.0) > 0 else 0,
        "title_projection": 1 if ctx.extras.get("angle") in ("first", "third") else 0,
        "quantity_on_sheet": 1 if QUANTITY.search(printed) else 0,
        "process_on_sheet": 1 if PROCESS.search("\n".join(notes)) else 0,
        "parts_lists": len(sheet_review._of(data, "parts_list")),
        "balloons": len(sheet_review._of(data, "balloons")),
        "chamfers": sum(1 for f in data.features if f.kind == "chamfer"),
    }
    return out
