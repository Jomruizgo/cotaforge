# ruff: noqa: E501
"""Drawing sheets (TechDraw): build them, annotate them, render and export them. Works in the headless worker and in the
visible one; none of it needs a sheet window.

* A SHEET is a TechDraw page (an ISO 5457 template, a first-angle front / top / right group, an isometric view and the
  overall dimensions) plus a SPEC: a JSON text on the page (``CotaforgeSpec``, so it survives save and reopen) with the
  sheet name, the template KEY (``A3_es``: the template path is looked up again on every use, the one TechDraw stores is
  absolute), the title-block fields and the list of ITEMS (``sheet_items``: hole callouts, dimensions, datum
  dimensions, notes, revisions...). ``create`` makes a sheet (a name makes another one), ``annotate`` adds / removes
  items or rebuilds the layout, ``describe`` lists, ``capture`` / ``export`` write.
* Items are resolved against the CURRENT geometry every time the sheet is composed: change the part and the
  dimensions follow. What is annotated lives in the spec and is composed by us, so the PDF, the PNG and the DXF show the
  same thing; the TechDraw page mirrors it live (``sheet_mirror``): each item is made on the page when it is added, a
  symbol of its own drawing at its own place, next to TechDraw's native views, overall dimensions and title block.
* WAITING: TechDraw computes the projection of a view in the background (in the GUI; the headless worker computes inside
  ``recompute``) and a step taken on a view that is still being computed is what crashed FreeCAD in real use. So every
  entry point here returns only once every view has geometry and nothing is left touched: ``wait_views`` recomputes,
  lets Qt deliver the "finished" signals (``gui.pump``) and polls, up to ``DRAWING_WAIT_S``, then fails naming the
  views.
* PDF / PNG -- ONE source for what the agent captures, what a reviewer sees and what the engineer opens. In the visible CAD
  the sheet window's scene is that source: the PDF is TechDraw's own export of it (``TechDrawGui.exportPageAsPdf``, which
  needs the window open: a PDF made without it came out blank) and the PNG is the same scene painted to an image (a
  region of it, to zoom), so every object of the page appears, those the items made and those a native script added. The
  headless CAD has no scene: the sheet is composed from data (``sheet_model``: template + each view's edges + the items),
  written as SVG (``sheet_svg``) and rendered with Qt (``sheet_render``) for both the PDF and the PNG, and the native
  objects it cannot draw (``sheet_output``) are listed in the reply, never left out silently. One sheet is one PDF page.
  DXF -- our own R12 writer (``sheet_dxf``), one file per sheet.
"""

import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import design_attrs
import drawing_assembly
import FreeCAD as App
import geometry as g
import gui
import hole_colours
import holes
import items_notes
import Part
import provenance as prov
import queries
import registry as reg
import sheet_checks
import sheet_collide
import sheet_colours
import sheet_context as sc
import sheet_dxf
import sheet_facts
import sheet_hlr
import sheet_interface
import sheet_iso
import sheet_isoview
import sheet_items
import sheet_keyway
import sheet_layout
import sheet_mirror
import sheet_model as model
import sheet_native
import sheet_output
import sheet_pattern
import sheet_plan_bridge
import sheet_project
import sheet_render
import sheet_reqfacts
import sheet_review
import sheet_svg
import sheet_symbols
import sheet_template
import sheet_template_gen
import sheet_thread
import sheet_turned
import sheet_units
import thread_records
from protocol import WorkerError, error, invalid_request, not_supported

SHEET = "Drawing"  # the name of the first sheet
SPEC_PROP = "CotaforgeSpec"
SPEC_VERSION = 1
PROJECTIONS = ("techdraw", "hlr")  # how the standard views are made; "techdraw" is FreeCAD's own default
SHEET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,23}$")
DRAWING_WAIT_S = 90.0
ASSEMBLY_WAIT_S = 3600.0  # an assembly's views take as long as it is big: the client's liveness rule gives up first
POLL_S = 0.05
SETTLE_S = 0.3  # extra event-loop time after the views report ready (GUI): the last signals of a recompute
GENERAL_TOLERANCES = "ISO 2768-mK"
SHIPPED_TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
# TechDraw's own English templates (key ``A4`` / ``A3``); ours are ``A4_es`` / ``A3_es`` in ``templates/``
TECHDRAW_TEMPLATES = {"A4": "ISO/A4_Landscape_ISO5457_minimal.svg", "A3": "ISO/A3_Landscape_ISO5457_minimal.svg"}
SIZES = tuple(sheet_layout.PAGES_MM)
DOCUMENT_TYPE = {"es": "Plano de fabricación", "en": "Component drawing"}
ASSEMBLY_TYPE = {"es": "Plano de conjunto", "en": "Assembly drawing"}
VIEW_KEYS = {"Front": "front", "Top": "top", "Right": "right"}
DISCRETIZE_MM = 0.02  # chord deviation of the curves written as polylines
LEGACY_ORDER = 0  # a sheet made before specs existed sorts first
FULL_CIRCLE_DEG = 359.0  # a hole wall covers the whole circle (``list_holes`` reports the arc it covers)


TECHDRAW_PREFS = "User parameter:BaseApp/Preferences/Mod/TechDraw/"
DEFAULT_PREFS: tuple[tuple[str, str, float], ...] = (
    ("Decorations", "LineGroup", 2),  # TechDraw's own list: "FC 0.50mm" (thick 0.50, thin 0.25), ISO 128-20's group for A4 to A2
    ("Dimensions", "FontSize", sheet_native.FONT_MM),  # the 3.5 mm of ISO 3098; TechDraw's own default is 5 mm
)
"""What a new TechDraw object reads when the engineer adds one by hand to the sheet: the line group and the text size of the
sheet. Measured in 1.1.3: with the preferences unset a dimension added by hand came out 0.35 mm thick and 5 mm high, next to
the sheet's 0.25 mm and 3.5 mm."""


def apply_techdraw_defaults() -> None:
    """Set the TechDraw preferences of ``DEFAULT_PREFS`` the engineer has not set himself (never changes a value that is there):
    what he adds to the sheet by hand then has the line weights and text size of the sheet's own objects."""
    for group, key, value in DEFAULT_PREFS:
        params = App.ParamGet(TECHDRAW_PREFS + group)
        if isinstance(value, int):
            if params.GetInt(key, -99) == -99:
                params.SetInt(key, value)
        elif params.GetFloat(key, -99.0) == -99.0:
            params.SetFloat(key, float(value))


def _techdraw(op: str | None) -> Any:
    try:
        import TechDraw  # noqa: PLC0415 - not every FreeCAD build has the module
    except ImportError as exc:
        raise not_supported(f"this FreeCAD has no TechDraw module ({exc})", op) from exc
    return TechDraw


def activate(fc_doc: Any) -> None:
    """Make ``fc_doc`` FreeCAD's active document. Parts of TechDraw (``makeExtentDim`` for one) create their objects in
    ``App.ActiveDocument``, which can be ``None`` in the worker after saves, undos or closed documents even though the
    session's document is open; every entry point therefore pins it first, as ``native_env`` does."""
    active = App.ActiveDocument
    if active is None or active.Name != fc_doc.Name:
        App.setActiveDocument(fc_doc.Name)


@contextmanager
def _stage(what: str, op: str | None) -> Iterator[None]:
    """Name the step a non-harness exception came from, so an ``internal`` error says what failed."""
    try:
        yield
    except (WorkerError, sc.ItemError):
        raise
    except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
        raise error("internal", f"drawing: {what} failed ({type(exc).__name__}: {exc})", op) from exc


# --- templates --------------------------------------------------------------------------------------------------------


def template_key(size: str, language: str, angle: str = "first") -> str:
    """``A3`` + ``es`` -> ``A3_es`` (ours); English is TechDraw's own template, ``A3``; third angle adds ``_third`` (always
    ours: ``sheet_template_gen`` makes the paper sizes and the mirrored projection symbol TechDraw's own templates lack)."""
    base = f"{size}_es" if language == "es" else size
    return base if angle == "first" else f"{base}_third"


def split_key(key: str) -> tuple[str, str]:
    """``(paper size, language)`` of a template key."""
    size, *rest = key.split("_")
    return size, ("es" if "es" in rest else "en")


def key_angle(key: str) -> str:
    """The projection angle a template key is for: ``first`` or ``third``."""
    return "third" if "third" in key.split("_")[1:] else "first"


def template_path(key: str, op: str | None) -> str:
    """The template file for ``key``, looked up now (never taken from the path stored in the document)."""
    size, language = split_key(key)
    if size not in SIZES:
        raise invalid_request(f"unknown template {key!r}, expected one of {', '.join(SIZES)}", op)
    angle = key_angle(key)
    shipped = f"{size}_es.svg"
    if language == "es" and angle == "first" and os.path.isfile(os.path.join(SHIPPED_TEMPLATES, shipped)):
        return os.path.join(SHIPPED_TEMPLATES, shipped)
    if angle == "third" or language == "es" or size not in TECHDRAW_TEMPLATES:  # ours: made for this paper and angle
        return _generated_template(key, size, language, angle, op)
    relative = TECHDRAW_TEMPLATES[size]
    roots = [App.getResourceDir(), App.getHomePath(), os.path.join(App.getHomePath(), "share")]
    for root in roots:
        path = os.path.join(root, "Mod", "TechDraw", "Templates", *relative.split("/"))
        if os.path.isfile(path):
            return path
    raise not_supported(f"the TechDraw template {relative} was not found under {roots[0]}", op)


def _generated_template(key: str, size: str, language: str, angle: str, op: str | None) -> str:
    """The file of a template ``sheet_template_gen`` makes (a paper size or an angle the shipped ones do not cover): written
    once to the temp folder, rewritten when its text changed. TechDraw copies the file INTO the document when the page
    takes it (``PropertyFileIncluded``), so the sheet owns its template from then on."""
    folder = os.path.join(tempfile.gettempdir(), "cotaforge-templates")
    path = os.path.join(folder, f"{key}.svg")
    text = sheet_template_gen.build(size, language, angle)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, encoding="utf-8") as handle:
            same = handle.read() == text
    except OSError:
        same = False
    if not same:
        try:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
        except OSError as exc:
            raise error("internal", f"the template {key}.svg could not be written to {folder}: {exc}", op) from exc
    return path


def template_text(key: str, op: str | None) -> str:
    with open(template_path(key, op), encoding="utf-8") as handle:
        return handle.read()


# --- the spec on the page ---------------------------------------------------------------------------------------------


def read_spec(page: Any) -> dict[str, Any] | None:
    """The sheet spec stored on ``page``, or ``None`` for a sheet cotaforge did not make (or whose text is damaged)."""
    if SPEC_PROP not in page.PropertiesList:
        return None
    try:
        spec = json.loads(str(getattr(page, SPEC_PROP)))
    except ValueError:
        return None
    return spec if isinstance(spec, dict) and spec.get("v") == SPEC_VERSION else None


def write_spec(page: Any, spec: dict[str, Any]) -> None:
    if SPEC_PROP not in page.PropertiesList:
        page.addProperty(
            "App::PropertyString", SPEC_PROP, "Cotaforge", "Sheet specification (JSON), written by cotaforge"
        )
        page.setEditorMode(SPEC_PROP, 1)
    setattr(page, SPEC_PROP, json.dumps(spec, separators=(",", ":")))


def spec_of(page: Any) -> dict[str, Any]:
    """The spec of ``page``; a sheet made by an older cotaforge (or by hand) gets one worked out from the page: English,
    TechDraw's template, the overall dimensions only."""
    spec = read_spec(page)
    if spec is not None:
        return _with_english_reviewers(spec)
    template = page.Template
    path = os.path.basename(str(getattr(template, "Template", "")))
    size = "A3" if "A3" in path else "A4"
    texts = {str(k): str(v) for k, v in dict(getattr(template, "EditableTexts", {})).items()}
    return {
        "v": SPEC_VERSION,
        "sheet": str(page.Name),
        "n": LEGACY_ORDER,
        "template": size,
        "language": "en",
        "scale_requested": None,
        "body": None,
        "fields": {k: v for k, v in texts.items() if k not in ("scale", "sheet_number")},
        "items": [{"id": "o1", "type": "overall"}],
        "next": 2,
        "legacy": True,
    }


def _with_english_reviewers(spec: dict[str, Any]) -> dict[str, Any]:
    """A sheet saved before 0.10 keeps its reports and the reviewer's choice under the Spanish names."""
    reports = spec.get("reports")
    if isinstance(reports, dict) and any(who in OLD_REVIEWERS for who in reports):
        spec["reports"] = {OLD_REVIEWERS.get(who, who): report for who, report in reports.items()}
    choice = spec.get("review_choice")
    if isinstance(choice, dict) and choice.get("who") in OLD_REVIEWERS:
        spec["review_choice"] = {**choice, "who": OLD_REVIEWERS[choice["who"]]}
    return spec


def pages(fc_doc: Any) -> list[Any]:
    """Every sheet of the document in the order they were made."""
    found = [o for o in fc_doc.Objects if str(o.TypeId) == "TechDraw::DrawPage"]
    return sorted(found, key=lambda p: (read_spec(p) or {}).get("n", LEGACY_ORDER))


def find_page(fc_doc: Any, sheet: str | None = None) -> Any | None:
    """The sheet called ``sheet``, or the first one when no name is given."""
    found = pages(fc_doc)
    if sheet is None:
        return found[0] if found else None
    for page in found:
        if sheet in (spec_of(page)["sheet"], str(page.Name), str(page.Label)):
            return page
    return None


def _need_page(fc_doc: Any, sheet: str | None, op: str | None) -> Any:
    page = find_page(fc_doc, sheet)
    if page is not None:
        return page
    found = pages(fc_doc)
    if not found:
        raise invalid_request("the document has no drawing sheet: create it first with drawing(action=create)", op)
    names = ", ".join(spec_of(p)["sheet"] for p in found)
    raise invalid_request(f"the document has no drawing sheet called {sheet!r}; its sheets are: {names}", op)


def _is_dimension(obj: Any) -> bool:
    type_id = str(obj.TypeId)
    return "DrawViewDimension" in type_id or "DimExtent" in type_id or type_id.endswith("DrawViewDimExtent")


def part_views(page: Any) -> list[Any]:
    """Every view of the page that has projected geometry (the group's items included, dimensions left out)."""
    views: list[Any] = []
    for obj in page.Views:
        if str(obj.TypeId) == "TechDraw::DrawProjGroup":
            views.extend(obj.Views)
        elif hasattr(obj, "getVisibleEdges"):
            views.append(obj)
    return views


def remove_page(fc_doc: Any, page: Any) -> None:
    """Delete one sheet with everything it owns, in the one order FreeCAD survives: removing a page or a projection
    group before its items crashed the process (segfault inside TechDraw), so the dimensions and loose views go first,
    then the items through the group, then group, page and template."""
    activate(fc_doc)
    views = list(page.Views)
    template = page.Template
    dims = [v.Name for v in views if _is_dimension(v)]
    groups = [v.Name for v in views if str(v.TypeId) == "TechDraw::DrawProjGroup"]
    loose = [v.Name for v in views if v.Name not in dims and v.Name not in groups]
    for name in dims + loose:
        if fc_doc.getObject(name) is not None:
            fc_doc.removeObject(name)
    for name in groups:
        group = fc_doc.getObject(name)
        if group is not None:
            for item in list(group.Views):
                group.removeProjection(item.Type)
    names = groups + [page.Name] + ([template.Name] if template is not None else [])
    for name in names:
        if fc_doc.getObject(name) is not None:
            fc_doc.removeObject(name)


def remove_sheet(fc_doc: Any) -> None:
    """Delete every sheet (and any other TechDraw object) of the document."""
    activate(fc_doc)
    for page in pages(fc_doc):
        remove_page(fc_doc, page)
    rows = [(o.Name, str(o.TypeId)) for o in fc_doc.Objects if str(o.TypeId).startswith("TechDraw::")]
    dims = [n for n, t in rows if "Dimension" in t or "DimExtent" in t]
    groups = [n for n, t in rows if t == "TechDraw::DrawProjGroup"]
    items = [n for n, t in rows if t == "TechDraw::DrawProjGroupItem"]
    templates = [n for n, t in rows if "Template" in t]
    taken = set(dims + groups + items + templates)
    loose = [n for n, _ in rows if n not in taken]
    for name in dims + loose:
        if fc_doc.getObject(name) is not None:
            fc_doc.removeObject(name)
    for name in groups:
        group = fc_doc.getObject(name)
        if group is not None:
            for item in list(group.Views):
                group.removeProjection(item.Type)
    for name in groups + templates:
        if fc_doc.getObject(name) is not None:
            fc_doc.removeObject(name)


# --- waiting for the views ----------------------------------------------------------------------------------------


def _state(obj: Any) -> list[str]:
    return [str(s) for s in obj.State]


def _has_geometry(view: Any) -> bool:
    try:
        return len(view.getVisibleEdges()) > 0
    except Exception:  # noqa: BLE001 - a view still being computed has no geometry object to read yet
        return False


def wait_views(fc_doc: Any, page: Any, op: str | None, timeout_s: float = DRAWING_WAIT_S) -> float:
    """Recompute and wait until every view of ``page`` is computed (geometry present, nothing touched, none invalid).

    Returns the seconds it took. A view in error fails at once; views that are not done after ``timeout_s`` fail with
    their names. The Qt event loop is run between polls (``gui.pump``): in the GUI the background computation reports
    back through queued signals that only fire when events are processed."""
    start = time.monotonic()
    kinds = ("Assembly::AssemblyObject", "App::Part")
    if timeout_s == DRAWING_WAIT_S and any(str(o.TypeId) in kinds for o in fc_doc.Objects):
        timeout_s = ASSEMBLY_WAIT_S
    fc_doc.recompute()
    while True:
        gui.pump()
        views = part_views(page)
        invalid = [v.Label for v in views if "Invalid" in _state(v)]
        if invalid:
            raise error(
                "geometry_failed",
                f"TechDraw could not compute the view(s) {', '.join(invalid)} (the part may be empty or invalid); "
                "check it with inspect(action=properties)",
                op,
            )
        if any("Touched" in _state(v) for v in views):
            fc_doc.recompute()
        pending = [v.Label for v in views if "Touched" in _state(v) or not _has_geometry(v)]
        if not pending:
            break
        if time.monotonic() - start > timeout_s:
            raise error(
                "timeout",
                f"the CAD did not finish computing the view(s) {', '.join(pending)} within {timeout_s:.0f} s: nothing "
                "was done on them (a step on a half-computed view can crash it); try again, or a larger scale",
                op,
            )
        time.sleep(POLL_S)
    if gui.available():  # the signals of the last recompute
        settle_until = time.monotonic() + SETTLE_S
        while time.monotonic() < settle_until:
            gui.pump()
            time.sleep(POLL_S)
    return time.monotonic() - start


# --- the part the sheet draws -----------------------------------------------------------------------------------------


def _bodies(doc: reg.DocumentRec, body: str | None, op: str | None) -> list[reg.BodyRec]:
    if body is None:
        return doc.live_bodies()
    return [reg.resolve_body(doc, reg.handle_id(body, op, "body"), op)]


def _sources(doc: reg.DocumentRec, body: str | None, op: str | None) -> list[Any]:
    if body is None:
        return queries.top_level_objects(doc)
    objects = [doc.fc_doc.getObject(b.result_obj) for b in _bodies(doc, body, op)]
    return [o for o in objects if o is not None]


def _bounds(doc: reg.DocumentRec, body: str | None = None, op: str | None = None) -> dict[str, Any] | None:
    box = None
    for rec in _bodies(doc, body, op):
        shape = reg.world_shape(doc, rec)
        if not shape.isNull():
            box = g.bbox_union(box, g.shape_bbox(shape))
    if body is None:
        for comp in doc.components.values():
            shape = reg.placed_component_shape(doc, comp)
            if not shape.isNull():
                box = g.bbox_union(box, g.shape_bbox(shape))
    return box


def _extents(doc: reg.DocumentRec, body: str | None = None, op: str | None = None) -> tuple[float, float, float]:
    box = _bounds(doc, body, op)
    if box is None:
        return 0.0, 0.0, 0.0
    return tuple(float(hi) - float(lo) for lo, hi in zip(box["min"], box["max"], strict=True))  # type: ignore[return-value]


def _shapes(doc: reg.DocumentRec, body: str | None, op: str | None) -> list[Any]:
    shapes = [reg.world_shape(doc, rec) for rec in _bodies(doc, body, op)]
    if body is None:
        shapes += [reg.placed_component_shape(doc, comp) for comp in doc.components.values()]
    return [s for s in shapes if not s.isNull()]


# --- title block fields -----------------------------------------------------------------------------------------------


def _initial_fields(
    params: dict[str, Any], language: str, fallback_title: str, assembly: bool = False
) -> dict[str, str]:
    """The title-block values a sheet starts with, by the field names TechDraw's templates use. The material is only the one
    given: while it is empty the title block shows the design's (``title_fields``), so it follows ``set_material``."""
    return {
        "title": str(params.get("title") or fallback_title),
        "creator": str(params.get("author") or ""),
        "date_of_issue": str(params.get("date") or date.today().isoformat()),
        "part_material": str(params.get("material") or ""),
        "drawing_number": str(params.get("drawing_number") or ""),
        "revision_index": str(params.get("revision") or ""),
        "general_tolerances": str(params.get("general_tolerances") or GENERAL_TOLERANCES),
        "legal_owner_1": str(params.get("company") or ""),
        "document_type": (ASSEMBLY_TYPE if assembly else DOCUMENT_TYPE)[language],
        "language_code": language.upper(),
    }


def material_of(fc_doc: Any) -> str:
    """The material the design says it is made of (``CotaforgeMaterial`` on its bodies, the document's metadata), empty
    when none is declared: what the ``Material`` field of the title block shows while the sheet has no text of its own."""
    meta = dict(fc_doc.Meta)
    values = [str(meta.get(design_attrs.META_MATERIAL, "") or "")]
    for obj in fc_doc.Objects:
        if design_attrs.MATERIAL_PROPERTY in obj.PropertiesList:
            values.append(str(getattr(obj, design_attrs.MATERIAL_PROPERTY) or ""))
    found: list[str] = []
    for value in (v.strip() for v in values):
        if value and value not in found:
            found.append(value)
    return design_attrs.SEPARATOR.join(found)


def title_fields(
    spec: dict[str, Any],
    names: list[str],
    scale: float,
    index: int,
    count: int,
    revisions: list[dict[str, str]],
    material: str = "",
) -> dict[str, str]:
    """Every editable field of the template with its value: the stored ones, the scale and the sheet number as the sheet
    is now, and the revision table rows (the last five revisions; the latest sets the revision field). ``material`` fills
    the material field while the sheet's own is empty (the design's material follows ``doc(action=set_material)``)."""
    values = {name: "" for name in names}
    values.update({k: str(v) for k, v in spec.get("fields", {}).items() if k in values})
    if "part_material" in values and not values["part_material"].strip() and not spec.get("assembly"):
        values["part_material"] = material
    if "scale" in values:
        values["scale"] = model.fmt_scale(scale)
    if "sheet_number" in values:
        values["sheet_number"] = f"{index} / {count}"
    rows = revisions[-5:]
    if rows and "revision_index" in values:
        values["revision_index"] = rows[-1]["index"]
    for number, row in enumerate(rows, start=1):
        for key in ("index", "description", "date", "by"):
            if f"rev_{number}_{key}" in values:
                values[f"rev_{number}_{key}"] = row.get(key, "")
    return values


def _revisions(spec: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {k: str(item.get(k, "")) for k in ("index", "description", "date", "by")}
        for item in spec.get("items", [])
        if item.get("type") == "revision"
    ]


def _sync_template_fields(fc_doc: Any, op: str | None) -> None:
    """Put the stored fields (sheet numbers, revisions) into the template objects, so the sheet window of FreeCAD shows
    the same title block."""
    found = pages(fc_doc)
    for index, page in enumerate(found, start=1):
        spec = spec_of(page)
        try:
            names = sheet_template.editable_fields(template_text(spec["template"], op))
        except WorkerError:
            continue
        scale = _scale_of(page) or 1.0
        page.Template.EditableTexts = title_fields(
            spec, names, scale, index, len(found), _revisions(spec), material_of(fc_doc)
        )


VIEW_CHOICES = ("front", "top", "right", "iso")  # the views of a sheet, in the order they are laid out
VIEW_ALIASES = {"isometric": "iso"}


ANCHOR_DIRECTIONS = {  # (direction towards the viewer, x direction) of the one standard view of a sheet without a front view
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "right": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}
ROW_FIRST_MM = 15.0  # the first baseline row: 10 mm from the view, and its text above the line
ROW_STEP_MM = 7.0  # each further row
CALLOUT_SPACING_MM = 34.0  # between the front view and the top / right ones when the part has holes (a callout fits)
CALLOUT_ROOM_MM = (
    24.0  # beside the views of a part with holes: where the leaders of their callouts end and their text goes
)
IN_PLACE_ROOM_MM = 34.0  # beside the section that stands in the main view: four diameter lines stack there, before the isometric view


def anchor_of(shown: tuple[str, ...] | list[str]) -> str:
    """The standard view the projection group hangs from: the front view, or -- on a sheet that has none -- its one top or
    right view."""
    if "front" in shown:
        return "front"
    return next((v for v in ("top", "right") if v in shown), "front")


def _distinct(values: list[float]) -> int:
    return len({round(v, 2) for v in values})


def turned_of(doc: reg.DocumentRec, spec: dict[str, Any], op: str | None) -> dict[str, Any] | None:
    """How the front view of a TURNED part is oriented (``sheet_turned``): ``None`` for any other sheet (an assembly, an
    ``hlr`` sheet, a sheet with no front view, a part that is not a body of revolution about x, y or z). Otherwise the axis,
    whether the view is flipped so the thread end is on the right, and the part's length and diameter."""
    body = spec.get("body")
    if projection_of(spec) == "hlr" or _is_assembly(doc, body) or anchor_of(views_of(spec)) != "front":
        return None
    found = LiveGeometry(doc, _shapes(doc, body, op), op).round_extent()
    box = _bounds(doc, body, op)
    if found is None or not box:
        return None
    axis, diameter = found
    index = sheet_turned.axis_index(axis)
    low, high = float(box["min"][index]), float(box["max"][index])
    middles = []
    for record in thread_records.read_threads(doc.fc_doc):
        start, direction, length = record["start"], record["direction"], float(record["length_mm"])
        middles.append(float(start[index]) + float(direction[index]) * length / 2)
    flip = sheet_turned.thread_end_flip(axis, (low, high), middles)
    return {"axis": [float(c) for c in axis], "flip": flip, "length": high - low, "diameter": float(diameter)}


SECTION_ROOM_MM = 42.0  # the label (9), the clear space round the view (8), the hole dimensions and the gaps


def _section_room(
    doc: reg.DocumentRec, spec: dict[str, Any], shown: tuple[str, ...], third: bool, op: str | None
) -> dict[str, float]:
    """An assembly drawn as one view and its section (no front view): the section goes where first-angle projection puts the
    view looked at from the front -- ABOVE the plan (third angle: below it) -- so the layout keeps that room, the height of
    the section and its label, beside the baseline of the hole dimensions."""
    if "front" in shown or "right" in shown or not any(i.get("type") == "section" for i in spec.get("items", [])):
        return {}
    section = next(i for i in spec["items"] if i.get("type") == "section")
    box = _bounds(doc, spec.get("body"), op)
    index = {"top": 2, "front": 1, "right": 0}.get(str(section.get("view")))
    if box is None or index is None:
        return {}
    room = float(box["max"][index]) - float(box["min"][index]) + SECTION_ROOM_MM
    return {"below_mm" if third else "above_mm": room}


def _dimension_room(
    doc: reg.DocumentRec, spec: dict[str, Any], shown: tuple[str, ...], third: bool, op: str | None
) -> dict[str, float]:
    """The room (mm of paper) the baseline dimensions of the holes need beyond the views, so the layout reserves it before
    it picks the scale: ``below_mm`` / ``above_mm`` for the rows of x values, ``left_mm`` for the rows of y values. One row
    per distinct coordinate (holes that share one share its dimension), ``ROW_STEP_MM`` apart. An assembly keeps the room
    of its section."""
    if _is_assembly(doc, spec.get("body")):
        return _section_room(doc, spec, shown, third, op)
    if "front" in sheet_turned.hidden_views(spec.get("items", [])):  # the diameters of the section stack beside it
        return {"right_mm": IN_PLACE_ROOM_MM}
    if AUTO_LOCATE_ID in spec.get("declined", ()) or spec.get("turned"):
        return {}  # a turned part's holes are called out, not stacked from a baseline: its frame is not the standard one
    axes = {"top": ((0, 1), (0.0, 0.0, 1.0)), "front": ((0, 2), (0.0, 1.0, 0.0)), "right": ((1, 2), (1.0, 0.0, 0.0))}
    rows_x = {"top": 0, "front": 0, "right": 0}
    rows_y = 0
    found = hole_infos(doc, spec.get("body"), op)
    for name, ((ix, iy), direction) in axes.items():
        if name not in shown:
            continue
        mine = [h for h in found if sc.parallel(h.direction, direction)]
        if mine:
            rows_x[name] = _distinct([h.centre[ix] for h in mine])
            rows_y = max(rows_y, _distinct([h.centre[iy] for h in mine]))

    def room(rows: int, extra: int = 0) -> float:
        return ROW_FIRST_MM + ROW_STEP_MM * (rows - 1 + extra) if rows else 0.0

    out: dict[str, float] = {
        "left_mm": room(rows_y),
        "right_mm": CALLOUT_ROOM_MM if found else 0.0,
        "spacing_mm": CALLOUT_SPACING_MM if found else 0.0,  # between the views: callouts of the top view go there too
    }
    if third:  # the top view hangs above the front one: its rows go above it
        out["above_mm"] = room(rows_x["top"])
        out["below_mm"] = room(rows_x["front"], 1)
    else:  # the top view hangs below the front one: its rows go below it; the front one's go above (next to the overall)
        out["below_mm"] = room(rows_x["top"])
        out["above_mm"] = room(rows_x["front"], 1)
    return {k: v for k, v in out.items() if v}


def views_of(spec: dict[str, Any]) -> tuple[str, ...]:
    """The views the sheet has, in layout order: ``spec["views"]`` when it says, else all four (the isometric one unless
    the sheet was made without it)."""
    given = spec.get("views")
    if given is None:
        return tuple(v for v in VIEW_CHOICES if v != "iso" or spec.get("isometric", True))
    return tuple(v for v in VIEW_CHOICES if v in given)


def normalise_views(wanted: Any, op: str | None) -> list[str]:
    """``views=["front", "top", "iso"]`` as the spec keeps it: known names only, ``isometric`` taken for ``iso``. The
    projection group hangs from ONE standard view and the overall dimensions are measured on it: the front view, or, on a
    sheet of a single standard view (a plate seen from above: ``["top"]``), that one."""
    names = [VIEW_ALIASES.get(str(v), str(v)) for v in wanted]
    unknown = [n for n in names if n not in VIEW_CHOICES]
    if unknown:
        raise invalid_request(f"unknown view(s) {', '.join(unknown)}: the views are {', '.join(VIEW_CHOICES)}", op)
    standard = [n for n in names if n != "iso"]
    if "front" not in names and len(set(standard)) != 1:
        raise invalid_request(
            "a sheet has its front view, or exactly ONE other standard view (top or right: a plate seen from above) "
            "that the isometric one may join",
            op,
        )
    return [v for v in VIEW_CHOICES if v in names]


def projection_of(spec: dict[str, Any]) -> str:
    """How the sheet's standard views are made: ``techdraw`` (FreeCAD's own default, view objects on the page) or
    ``hlr`` (projected by us, no view objects)."""
    return "hlr" if spec.get("projection") == "hlr" else "techdraw"


def _scale_of(page: Any) -> float | None:
    spec = read_spec(page)
    if spec is not None and projection_of(spec) == "hlr":
        return float(spec["hlr"]["scale"])
    views = part_views(page)
    return float(views[0].getScale()) if views else None


# --- creating ---------------------------------------------------------------------------------------------------------


def _iso_quarter_turned(spec: dict[str, Any]) -> bool:
    """Whether the isometric view of the sheet is a quarter turn from the usual one (what it shows across swaps)."""
    frame = spec.get("iso_frame")
    return bool(frame) and sheet_isoview.quarter_turned(tuple(frame[0]))  # type: ignore[arg-type]


def _build(
    doc: reg.DocumentRec, spec: dict[str, Any], op: str | None
) -> tuple[Any, sheet_layout.Layout, float, list[str]]:
    """Make the TechDraw objects of the sheet ``spec`` describes (replacing the sheet of that name, if any), wait for
    them and store the spec on the page. Returns (page, layout, seconds waited, warnings)."""
    td = _techdraw(op)
    fc_doc = doc.fc_doc
    name = spec["sheet"]
    size, _ = split_key(spec["template"])
    sources = _sources(doc, spec.get("body"), op)
    if not sources:
        raise invalid_request("the document has no body to draw", op)
    box = _bounds(doc, spec.get("body"), op)
    width, depth, height = (
        tuple(float(hi) - float(lo) for lo, hi in zip(box["min"], box["max"], strict=True)) if box else (0.0, 0.0, 0.0)
    )
    if min(width, depth, height) <= 0:
        raise invalid_request("the part has no volume to draw (check it with inspect(action=properties))", op)
    page_w, page_h = sheet_layout.PAGES_MM[size]
    requested = spec.get("scale_requested")
    reserve = None
    if _is_assembly(doc, spec.get("body")):  # room for the parts list: one row per distinct part, and its header
        parts = len({_group_key(doc, c) for c in doc.components.values()})
        reserve = (sheet_layout.TITLE_BLOCK_WIDTH_MM, sheet_layout.PARTS_ROW_MM * (parts + 1) + 4.0)
    shown = views_of(spec)
    with_iso = "iso" in shown
    anchor = anchor_of(shown)
    third = key_angle(spec["template"]) == "third"
    turned = turned_of(doc, spec, op)
    if turned is None:
        spec.pop("turned", None)
    else:
        spec["turned"] = turned  # the view is turned, so the layout arranges length x diameter, not the part's own box
    room = _dimension_room(doc, spec, shown, third, op)
    across, up = {"front": (width, height), "top": (width, depth), "right": (depth, height)}[anchor]
    lay_w, lay_d, lay_h = (
        sheet_turned.layout_sizes(turned["length"], turned["diameter"]) if turned else (width, depth, height)
    )
    layout = sheet_layout.plan(
        across if anchor != "front" else lay_w,
        lay_d if anchor == "front" else 0.0,
        up if anchor != "front" else lay_h,
        page_w,
        page_h,
        float(requested) if requested else None,
        reserve,
        with_iso,
        "top" in shown and anchor == "front",
        "right" in shown and anchor == "front",
        third=third,
        iso_dims=(lay_d, lay_w, lay_h) if _iso_quarter_turned(spec) else (lay_w, lay_d, lay_h),
        **room,
    )
    warnings: list[str] = []
    if not layout.standard:
        warnings.append(f"no standard scale fits the sheet: drawn at {model.fmt_scale(layout.scale)}; try A3")
    if not layout.fits:
        warnings.append(f"at {model.fmt_scale(layout.scale)} the views do not fit the sheet: use a smaller scale or A3")
    if layout.iso_scale:
        warnings.append(
            f"the isometric view is drawn at {model.fmt_scale(layout.iso_scale)} (written under it) so the main views can "
            f"take {model.fmt_scale(layout.scale)}: a pictorial view may have its own scale"
        )

    activate(fc_doc)
    names = sheet_template.editable_fields(template_text(spec["template"], op))
    hlr = projection_of(spec) == "hlr"
    if hlr:  # the layout is kept in the spec: there are no view objects to read it from
        spec["hlr"] = _hlr_record(spec, layout, (width, depth, height), box, page_h)
    with _stage("creating the sheet and its template", op):
        old = find_page(fc_doc, name)
        if old is not None:
            remove_page(fc_doc, old)
        page = fc_doc.addObject("TechDraw::DrawPage", name)
        base = str(page.Name)
        template = fc_doc.addObject("TechDraw::DrawSVGTemplate", f"{base}Template")
        template.Template = template_path(spec["template"], op)
        page.Template = template
        write_spec(page, spec)
        index = pages(fc_doc).index(page) + 1
        template.EditableTexts = title_fields(
            spec, names, layout.scale, index, len(pages(fc_doc)), _revisions(spec), material_of(fc_doc)
        )

    if hlr:
        try:
            waited = _project_views(doc, spec, op)  # made now (and kept): the sheet is ready on return
        except Exception:
            remove_page(fc_doc, page)  # a sheet that cannot be drawn is not left behind
            raise
        _sync_template_fields(fc_doc, op)
        waited += wait_views(fc_doc, page, op)  # no views to wait for: leaves nothing touched
        return page, layout, waited, warnings

    def centre_y(y_down: float) -> float:  # SVG page y (down) -> TechDraw y (up)
        return page_h - y_down

    with _stage("creating the front / top / right views", op):
        group = fc_doc.addObject("TechDraw::DrawProjGroup", f"{base}Views")
        page.addView(group)
        group.Source = sources
        group.ProjectionType = "Third angle" if third else "First angle"
        group.ScaleType = "Custom"
        group.Scale = layout.scale
        group.spacingX = group.spacingY = layout.spacing
        group.X, group.Y = layout.front[0], centre_y(layout.front[1])
        fc_doc.recompute()
        for view_name in ("Front", "Top", "Right"):
            if view_name == "Front" or (anchor == "front" and view_name.lower() in shown):
                added = group.addProjection(view_name)
                if (
                    view_name == "Front" and anchor != "front"
                ):  # the one view of the sheet is the anchor, looked at from its side
                    added.Direction = App.Vector(*ANCHOR_DIRECTIONS[anchor][0])
                    added.XDirection = App.Vector(*ANCHOR_DIRECTIONS[anchor][1])
                elif view_name == "Front" and turned:  # a turned part: its axis along the page, as it is machined
                    direction, xdirection = sheet_turned.frame(tuple(turned["axis"]), bool(turned["flip"]))  # type: ignore[arg-type]
                    added.Direction = App.Vector(*direction)
                    added.XDirection = App.Vector(*xdirection)
        for item in group.Views:  # the names the page keeps for its views (contract of ``sheet_native``)
            key = anchor if str(item.Type) == "Front" else VIEW_KEYS[str(item.Type)]
            item.Label = sheet_native.VIEW_LABELS[key]
            sheet_native.tag_view(item, f"view:{key}", name)

    if with_iso:
        with _stage("creating the isometric view", op):
            iso = fc_doc.addObject("TechDraw::DrawViewPart", f"{base}Isometric")
            page.addView(iso)
            iso.Label = sheet_native.VIEW_LABELS["isometric"]
            sheet_native.tag_view(iso, "view:isometric", name)
            iso.Source = sources
            iso_direction, iso_xdirection = (
                sheet_turned.iso_frame(tuple(turned["axis"]), bool(turned["flip"]))  # type: ignore[arg-type]
                if turned
                else (
                    tuple(spec["iso_frame"][0]), tuple(spec["iso_frame"][1])
                ) if spec.get("iso_frame") else (sheet_layout.ISO_DIRECTION, sheet_layout.ISO_XDIRECTION)
            )
            iso.Direction = App.Vector(*iso_direction)
            iso.XDirection = App.Vector(*iso_xdirection)
            iso.ScaleType = "Custom"
            iso.Scale = layout.iso_scale or layout.scale  # its own scale, when it has one
            iso.X, iso.Y = layout.iso[0], centre_y(layout.iso[1])

    waited = wait_views(fc_doc, page, op)  # the dimensions refer to finished views

    with _stage("adding the overall dimensions", op):
        overall_id = next((str(i["id"]) for i in spec.get("items", []) if i.get("type") == "overall"), "o1")
        members = {str(item.Type): item for item in group.Views}
        depth_view, depth_direction = (members["Top"], 1) if "Top" in members else (members.get("Right"), 0)
        wanted = [("width", members["Front"], 0), ("height", members["Front"], 1)]
        if "front" in sheet_turned.hidden_views(spec.get("items", [])):
            wanted = []  # a section stands in the main view: TechDraw draws no dimension on a view that is hidden
        if (
            depth_view is not None
        ):  # the depth is seen on the top view, else along the right view (a sheet of one standard
            wanted.append(("depth", depth_view, depth_direction))  # view has neither: its thickness is a note)
        for label, view, direction in wanted:
            dim = td.makeExtentDim(view, [], direction)
            dim.Label = f"Dim {label}"
            sheet_mirror.tag_native(dim, f"overall:{label}", str(view.Name), overall_id, name)
    _sync_template_fields(fc_doc, op)
    waited += wait_views(
        fc_doc, page, op
    )  # also leaves nothing touched: a later in-place edit refuses a touched object
    return page, layout, waited, warnings


def _hlr_record(
    spec: dict[str, Any],
    layout: sheet_layout.Layout,
    size: tuple[float, float, float],
    box: dict[str, Any] | None,
    page_h: float,
) -> dict[str, object]:
    """The layout of an ``hlr`` sheet as the spec keeps it (page y UP): the scale, the place of the front and isometric
    views, the size of the part and the model point every group view has at its origin (the centre of the part's box,
    like TechDraw's projection group)."""
    assert box is not None  # a part with a volume has a box
    centre = tuple((float(lo) + float(hi)) / 2 for lo, hi in zip(box["min"], box["max"], strict=True))
    iso = (layout.iso[0], page_h - layout.iso[1]) if spec.get("isometric", True) else None
    return sheet_hlr.record(
        layout.scale,
        layout.spacing,
        (layout.front[0], page_h - layout.front[1]),
        iso,
        size,
        centre,  # type: ignore[arg-type]
        layout.iso_scale,
    )


def _project_views(doc: reg.DocumentRec, spec: dict[str, Any], op: str | None) -> float:
    """Project the standard views of an ``hlr`` sheet (they are kept for the next composition); seconds it took."""
    start = time.monotonic()
    geometry = LiveGeometry(doc, _shapes(doc, spec.get("body"), op), op)
    try:
        sheet_hlr.compose_views(geometry, spec["hlr"])
    except sc.ItemError as exc:
        raise error("geometry_failed", f"the part could not be projected: {exc}", op) from exc
    return time.monotonic() - start


def _is_assembly(doc: reg.DocumentRec, body: Any) -> bool:
    """Whether the sheet draws an assembly: an assembly document with components, all of it (no ``body``)."""
    return doc.kind == "assembly" and bool(doc.components) and body is None


def _group_key(doc: reg.DocumentRec, comp: reg.ComponentRec) -> str:
    return drawing_assembly.group_key(doc, comp)


def _name_for(fc_doc: Any, wanted: str | None, op: str | None) -> str:
    if wanted is None:
        first = pages(fc_doc)
        return spec_of(first[0])["sheet"] if first else SHEET
    if not SHEET_NAME.match(wanted):
        raise invalid_request(
            f"sheet name {wanted!r} must be a short name: a letter, then letters, digits or _ (24 characters at most)",
            op,
        )
    return wanted


CARRIED_KEYS = (
    "declined", "answers", "review_choice", "review_request", "reports", "report_n", "symmetry", "iso_frame", "composed",
)  # fmt: skip
# what a sheet remembers beyond its items, and a rebuild keeps: the defaults the engineer took off, the answers to the
# review, who he chose to review it, the reports of the reviewers
FLAT_RATIO = 0.35  # a round part is a disc (its length is a thickness) when it is at most this fraction of its diameter


def _stated(params: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
    """``params`` with what the caller did not state (``unset``) taken from the sheet that is being rebuilt: a rebuild to
    take a view off must not also change the paper, the language or the scale."""
    out = dict(params)
    size, language = split_key(old["template"])
    values = {
        "template": size,
        "language": language,
        "angle": key_angle(old["template"]),
        "scale": old.get("scale_requested"),
        "projection": projection_of(old),
        "body": old.get("body"),
    }
    for name in params.get("unset") or ():
        if name in values:
            out[name] = values[name]
    return out


def _rebuilt_fields(
    old: dict[str, str], new: dict[str, str], language: str, defaults: dict[str, str]
) -> dict[str, str]:
    """The title block of a rebuilt sheet: what the engineer or the agent set stays, unless the caller states another value
    (a value equal to what a sheet starts with is not a statement). The approval is cleared by the rebuild itself."""
    out = dict(old)
    changed = language.upper() != old.get("language_code", language.upper())  # another language: its own wording too
    for key, value in new.items():
        if key not in old or value != defaults.get(key) or (changed and key in ("document_type", "language_code")):
            out[key] = value
    out[APPROVER_FIELD] = ""
    return out


def _views_for_part(doc: reg.DocumentRec, body: Any, op: str | None) -> tuple[list[str], bool | str, str] | None:
    """The views a part is drawn in when the caller does not say: ``(views, section through the axis, why)`` for a body of
    revolution, ``None`` for any other part (all the standard views, as always). ``section`` is ``False`` (none), ``True`` (a
    section A-A beside the end view) or ``"in_place"`` (the section stands in the main view and there is no end view: a part
    of revolution whose every feature is coaxial and hollow, ``sheet_turned.section_only``). A revolved part with a bore has everything
    in the view that looks along its axis (what is cut, where) and the section through the axis (the profile, the bore, the
    threads); a solid one, the one view that shows its profile."""
    if body is None and doc.kind != "assembly" and len(doc.live_bodies()) > 1:  # a weldment: no view that repeats another
        import sheet_weldment  # noqa: PLC0415

        boxes = LiveGeometry(doc, [], op).body_boxes()
        kept = sheet_weldment.views_without_repeats(boxes) if boxes else None
        if kept is not None and len(kept) < len(sheet_weldment.VIEW_AXES):
            gone = ", ".join(v for v in sheet_weldment.VIEW_AXES if v not in kept)
            return kept, False, f"the {gone} view would draw the same as another one: left out"
        return None
    geometry = LiveGeometry(doc, _shapes(doc, body, op), op)
    found = geometry.round_extent()
    if found is None:
        return None
    axis = found[0]
    index = max(range(3), key=lambda i: abs(axis[i]))
    name = "xyz"[index]
    features = geometry.axial_features()
    if features is not None and sheet_turned.section_only(*features):
        return (
            ["front"],
            "in_place",
            f"revolved about {name}, every feature coaxial and hollow: ONE axial section in the main view (axis horizontal, "
            "as it is machined) carries every diameter and height, an end view would only repeat circles; plus the isometric view",
        )
    bored = any(sc.parallel(h.direction, axis) for h in hole_infos(doc, body, op))
    if bored:
        along = ("right", "front", "top")[index]
        return [along], True, f"revolved about {name} with a bore: the {along} view and a section A-A through the axis"
    wants_end = bool(sheet_facts.collect(doc, _bodies(doc, body, op)).keyways)
    views = sheet_turned.views_for(wants_end)
    why = (
        f"revolved about {name}: the main view with its axis horizontal (as it is machined) and the isometric view; the "
        "keyway is shown in a removed section across it (keyway_dims), not in an end view"
        if wants_end
        else f"revolved about {name}: ONE main view with its axis horizontal (as it is machined); it carries the "
        "diameters, the lengths, the thread and the chamfers, an end view would repeat circles; plus the isometric view"
    )
    return views, False, why


def _views_for_assembly(
    doc: reg.DocumentRec, body: Any, op: str | None
) -> tuple[list[str], dict[str, Any], str] | None:
    """The views an assembly is drawn in when the caller does not say: ``(views, section item, why)``, or ``None`` (all the
    standard views, as always). Parts fastened through holes that all run one way and lie on one line have what an assembly
    sheet shows in the section through their axes: the view that looks along the holes plus that section, in projection with
    it, instead of a full outside view that the section would only repeat."""
    stacks = sheet_interface.stacks_of(LiveGeometry(doc, _shapes(doc, body, op), op).assembly_holes())
    if not stacks:
        return None
    axes = {tuple(round(c, 6) for c in s.direction) for s in stacks}
    if len(axes) != 1:
        return None
    axis = next(iter(axes))
    index = max(range(3), key=lambda i: abs(axis[i]))
    if abs(axis[index]) < 0.999:
        return None
    centres = [s.centre for s in stacks]
    for j in (i for i in range(3) if i != index):
        if max(c[j] for c in centres) - min(c[j] for c in centres) <= sheet_interface.AXIS_TOL_MM:
            normal = [0.0, 0.0, 0.0]
            normal[j] = 1.0
            view = ("right", "front", "top")[index]
            section = {"type": "section", "at": list(centres[0]), "normal": normal, "view": view, "letter": "A"}
            return [view], section, f"the holes run along {'xyz'[index]} on one line: the {view} view and section A-A through their axes"
    return None


def _best_isometric(doc: reg.DocumentRec, body: Any, op: str | None) -> tuple[sc.Vec3, sc.Vec3] | None:
    """The isometric direction (of the four from above) that shows the most of each part of the assembly, or ``None`` when
    the assembly is too big to try (it keeps the usual one)."""
    ids = list(doc.components)
    if not ids or len(ids) > sheet_isoview.MAX_PARTS_TRIED:
        return None
    geometry = LiveGeometry(doc, _shapes(doc, body, op), op)
    try:
        boxes = [geometry.instance_box(c) for c in ids]
        centre = tuple((min(b[0][i] for b in boxes) + max(b[1][i] for b in boxes)) / 2 for i in range(3))
        shares = [
            geometry.visible_share(ids, (centre, sc.unit(x), sc.unit(d)), {}) for d, x in sheet_isoview.CANDIDATES
        ]
    except (sc.ItemError, WorkerError):
        return None
    return sheet_isoview.CANDIDATES[sheet_isoview.choose(shares)]


def create(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Build the sheet (a new one when ``sheet`` names none that exists). A sheet of that name that exists is REBUILT, and
    what is still valid in it stays: its items (placed again on the new views), the answers to its review, who reviews it,
    the title block; the reply lists what did not resolve. ``fresh`` starts it from scratch and says what it threw away."""
    op = params.get("operation_id")
    fc_doc = doc.fc_doc
    name = _name_for(fc_doc, params.get("sheet"), op)
    old = find_page(fc_doc, name)
    old_spec = read_spec(old) if old is not None else None
    fresh = bool(params.get("fresh"))
    carried = old_spec if old_spec is not None and not fresh else None
    if carried is not None:
        params = _stated(params, carried)
    key = str(params.get("template") or "A4")
    paper_wanted = carried is None and "template" in (params.get("unset") or ())  # nobody chose a paper: the part's own
    if key not in SIZES:
        raise invalid_request(f"unknown template {key!r}, expected one of {sorted(SIZES)}", op)
    language = str(params.get("language") or "es")
    if language not in ("es", "en"):
        raise invalid_request(f"unknown language {language!r}, expected es or en", op)
    body = params.get("body")
    body = body["id"] if isinstance(body, dict) else body
    others = [read_spec(p) for p in pages(fc_doc) if p is not old]
    order = (
        old_spec["n"]
        if old_spec
        else (LEGACY_ORDER if old is not None else 1 + max([s["n"] for s in others if s] + [0]))
    )
    angle = str(params.get("angle") or "first")
    if angle not in ANGLES:
        raise invalid_request(f"unknown angle {angle!r}, expected first or third", op)
    fallback = str(doc.name or fc_doc.Label or fc_doc.Name)
    assembly = _is_assembly(doc, body)
    fields = _initial_fields(params, language, fallback, assembly)
    if carried is not None:
        fields = _rebuilt_fields(
            dict(carried.get("fields", {})), fields, language, _initial_fields({}, language, fallback, assembly)
        )
    spec = {
        "v": SPEC_VERSION,
        "sheet": name,
        "n": order,
        "template": template_key(key, language, angle),
        "language": language,
        "scale_requested": params.get("scale"),
        "body": body,
        "fields": fields,
        "items": [dict(i) for i in carried["items"]] if carried is not None else [{"id": "o1", "type": "overall"}],
        "next": int(carried.get("next", 2)) if carried is not None else 2,
    }
    if carried is not None:
        # the places the global layout gave were decided for the views of the old sheet (another paper, another scale): they
        # are not the engineer's and a rebuild takes them off, so a layout of the new sheet plans them again
        sheet_plan_bridge.forget_planned(spec["items"], carried.get("layout"))
        for kept_key in CARRIED_KEYS:
            if kept_key in carried:
                spec[kept_key] = json.loads(json.dumps(carried[kept_key]))
        spec["edits"] = int(
            carried.get("edits", 0)
        )  # the rebuild below counts as one more change (``_clear_approval``)
    if assembly:
        spec["assembly"] = True
    _declare_symmetry(spec, params.get("symmetry"))
    if params.get("iso_colour"):  # the convention of the harness; a sheet without the key stays a line drawing
        spec["iso_colour"] = True
    projection = str(params.get("projection") or "techdraw")
    if projection not in PROJECTIONS:
        raise invalid_request(f"unknown projection {projection!r}, expected one of {', '.join(PROJECTIONS)}", op)
    if projection == "hlr":  # the default (FreeCAD's own) leaves the spec exactly as it always was
        spec["projection"] = "hlr"
        if angle != "first":
            raise invalid_request("angle=third needs projection=techdraw: an hlr sheet is made in first angle", op)
    apply_techdraw_defaults()
    warnings: list[str] = []
    section_wanted: bool | str = False
    assembly_section: dict[str, Any] | None = None
    if params.get("views") is not None:
        chosen = normalise_views(params["views"], op)
        if projection == "hlr" and set(chosen) != set(views_of(spec)):
            raise invalid_request("views choose the views of a techdraw sheet: an hlr sheet always has all of them", op)
        spec["views"] = chosen
        if params.get("isometric") is False:  # a part sheet keeps the isometric view and its spec as it was
            spec["isometric"] = False
    elif carried is not None:  # a rebuild that names no views keeps the ones the sheet has
        if "views" in carried:
            spec["views"] = list(carried["views"])
        if carried.get("isometric") is False or params.get("isometric") is False:
            spec["isometric"] = False
    else:
        if params.get("isometric") is False:
            spec["isometric"] = False
        picked = _views_for_part(doc, body, op) if projection == "techdraw" and not assembly else None  # noqa: E501
        if picked is not None:
            spec["views"], section_wanted, why = picked
            spec["views"] = [*spec["views"], "iso"] if params.get("isometric") is not False else spec["views"]
            if params.get("isometric") is False:
                spec["isometric"] = False
            warnings.append(f"views chosen from the part ({why}); views=[...] states others")
        elif assembly and projection == "techdraw":
            found = _views_for_assembly(doc, body, op)
            if found is not None:
                spec["views"], assembly_section, why = found
                if params.get("isometric") is not False:
                    spec["views"] = [*spec["views"], "iso"]
                else:
                    spec["isometric"] = False
                warnings.append(f"views chosen from the assembly ({why}); views=[...] states others")
    if assembly and paper_wanted:  # parts list, balloons and the views of an assembly: A3, not the A4 of a part
        key = "A3"
        spec["template"] = template_key(key, language, angle)
    if assembly and projection == "techdraw" and "iso" in views_of(spec) and "iso_frame" not in spec:
        frame = _best_isometric(doc, body, op)
        if frame is not None:
            spec["iso_frame"] = [list(frame[0]), list(frame[1])]
            if frame != sheet_isoview.DEFAULT:
                warnings.append(
                    f"isometric view looks from {tuple(int(c) for c in frame[0])}: the direction that shows the most of each part"
                )
    if paper_wanted and section_wanted is not True and not assembly:  # the views known: the paper is chosen for the sheet that will be drawn
        # (a part with a section view keeps the default paper: the layout reserves no room for that view yet)
        chosen = _auto_paper(doc, spec, params, op)
        if chosen is not None:
            key = chosen[0]
            spec["template"] = template_key(key, language, angle)
            warnings.append(
                f"paper chosen from the part: {key} at {model.fmt_scale(chosen[1])} (A4 or A3 at full size or larger, "
                "else the smallest paper where it is legible: not below 1:5 and its largest side drawn at least 80 mm); template=... states another"
            )
    apply_title_block(spec, params.get("title_block"), op)
    _sync_auto_items(doc, spec, op)
    if section_wanted:
        taken = {str(i["id"]) for i in spec["items"]}
        axial: dict[str, Any] = {"type": "section", "holes": "axis"}
        if section_wanted == "in_place":
            axial["in_place"] = True
        spec["items"].append(sheet_items.normalise(axial, taken, spec["next"]))
        spec["next"] += 1
    if assembly_section is not None:
        taken = {str(i["id"]) for i in spec["items"]}
        spec["items"].append(sheet_items.normalise(assembly_section, taken, spec["next"]))
        spec["next"] += 1
    if carried is not None:
        _clear_approval(spec)
    page, layout, waited, built = _build(doc, spec, op)
    result = _result(doc, page, layout.scale, waited, [*warnings, *built], op)
    result.update(_rebuild_report(old_spec, carried, spec, result["items"], fresh))
    return result


def _rebuild_report(
    old: dict[str, Any] | None,
    carried: dict[str, Any] | None,
    spec: dict[str, Any],
    rows: list[dict[str, str]],
    fresh: bool,
) -> dict[str, list[str]]:
    """What a rebuild kept, what no longer resolves, and -- when ``fresh`` -- what it threw away, as lines for the reply."""

    def own(items: list[dict[str, Any]]) -> list[str]:
        return [str(i["id"]) for i in items if not (i.get("auto") and is_auto_id(str(i["id"])))]

    def memory(source: dict[str, Any]) -> list[str]:
        lines = []
        if source.get("answers"):
            lines.append(f"{len(source['answers'])} answer(s) to the review")
        if (source.get("review_choice") or {}).get("who"):
            lines.append(f"the chosen reviewer ({source['review_choice']['who']})")
        if source.get("reports"):
            lines.append(f"{len(source['reports'])} reviewer report(s)")
        if source.get("declined"):
            lines.append(f"the default items taken off ({', '.join(source['declined'])})")
        return lines

    out: dict[str, list[str]] = {"kept": [], "unresolved": [], "discarded": []}
    if carried is not None:
        ids = own(carried["items"])
        if ids:
            out["kept"].append(f"items {', '.join(ids)} (placed again on the new views)")
        out["kept"] += memory(carried)
        out["unresolved"] = [
            f"{r['id']} ({r['type']}): {r['detail']}" for r in rows if r["status"] == "error" and r["id"] in ids
        ]
    elif fresh and old is not None:
        ids = own(old["items"])
        if ids:
            out["discarded"].append(f"items {', '.join(ids)}")
        out["discarded"] += memory(old)
    return out


COMPUTED_FIELDS = ("scale", "sheet_number", "revision_index", "approval_person")
# the sheet writes these itself (and the revision rows); ``Aprobó`` is the engineer's, only ``approve`` fills it


def apply_title_block(spec: dict[str, Any], given: Any, op: str | None) -> None:
    """Set title-block fields of ``spec`` by the name the template gives them (an empty text clears one). A name the
    template does not have, or one the sheet writes itself, is refused with the names that can be set."""
    if not given:
        return
    names = sheet_template.editable_fields(template_text(spec["template"], op))
    settable = [n for n in names if n not in COMPUTED_FIELDS and not n.startswith("rev_")]
    unknown = sorted(k for k in given if k not in settable)
    if unknown:
        raise invalid_request(
            f"the title block has no settable field {', '.join(unknown)}; it has: {', '.join(settable)}", op
        )
    spec.setdefault("fields", {}).update({str(k): str(v) for k, v in given.items()})


def _route_title_block_notes(
    spec: dict[str, Any], requested: list[Any], op: str | None, material: str = ""
) -> tuple[list[Any], list[str]]:
    """Take out of ``requested`` the notes that state a datum the title block has a field for and put the datum in that field
    of the sheet (``items_notes.route_title_block_notes``). Returns the items left and what was moved (said to the agent)."""
    names = set(sheet_template.editable_fields(template_text(spec["template"], op)))
    current = {k: str(v) for k, v in spec.get("fields", {}).items()}
    if not current.get("part_material", "").strip() and not spec.get("assembly"):
        current["part_material"] = material  # while the sheet has none of its own the title block shows the design's
    return items_notes.route_title_block_notes(requested, names, spec.setdefault("fields", {}), bool(spec.get("assembly")), current)


def _auto_paper(
    doc: reg.DocumentRec, spec: dict[str, Any], params: dict[str, Any], op: str | None
) -> tuple[str, float] | None:
    """The paper and scale ``sheet_layout.auto_paper`` gives the part of a new sheet whose paper nobody chose, for the views
    the sheet will have (``None``: the part has no volume to measure, or it is an assembly, which keeps its own room for
    the parts list)."""
    body = spec.get("body")
    if _is_assembly(doc, body):
        return None
    box = _bounds(doc, body, op)
    if not box:
        return None
    width, depth, height = (float(hi) - float(lo) for lo, hi in zip(box["min"], box["max"], strict=True))
    if min(width, depth, height) <= 0:
        return None
    shown = views_of(spec)
    anchor = anchor_of(shown)
    across, up = {"front": (width, height), "top": (width, depth), "right": (depth, height)}[anchor]
    turned = turned_of(doc, spec, op)
    lay_w, lay_d, lay_h = (
        sheet_turned.layout_sizes(turned["length"], turned["diameter"]) if turned else (width, depth, height)
    )
    return sheet_layout.auto_paper(  # the arguments ``_build`` gives the layout of the same sheet
        lay_w if anchor == "front" else across,
        lay_d if anchor == "front" else 0.0,
        lay_h if anchor == "front" else up,
        "iso" in shown,
        "top" in shown and anchor == "front",
        "right" in shown and anchor == "front",
        third=str(params.get("angle") or "first") == "third",
        iso_dims=(lay_w, lay_d, lay_h),
    )


def _expand_chamfer_dims(
    doc: reg.DocumentRec, stored: list[dict[str, Any]], requested: list[Any], op: str | None
) -> list[Any]:
    """``chamfer_dims`` is not an item of its own: it is a callout (``1 × 45°``) for every chamfer of the part that no
    dimension of the sheet gives yet, made now from the faces the chamfer features created as ``dim`` items of kind
    ``chamfer`` (``<id>``, ``<id>2``...), so the sheet keeps plain dimensions."""
    if not any(isinstance(i, dict) and i.get("type") == "chamfer_dims" for i in requested):
        return requested
    dimensioned = {
        str(f)
        for i in stored
        if i.get("type") == "dim" and i.get("kind") == "chamfer"
        for f in ([i["face"]] if isinstance(i.get("face"), str) else list(i.get("face") or ()))
    }
    faces = [
        feature.faces[0]
        for feature in feature_infos(doc)
        if feature.kind == "chamfer" and feature.faces and not dimensioned & set(feature.faces)
    ]
    out: list[Any] = []
    for item in requested:
        if not (isinstance(item, dict) and item.get("type") == "chamfer_dims"):
            out.append(item)
            continue
        prefix = str(item.get("id") or "chamfer")
        if not faces:
            raise invalid_request("every chamfer of the part has a dimension already (chamfer_dims)", op)
        out.extend(
            {"type": "dim", "id": prefix if number == 1 else f"{prefix}{number}", "kind": "chamfer", "face": face}
            for number, face in enumerate(faces, start=1)
        )
    return out


def _expand_thread_dims(
    doc: reg.DocumentRec, stored: list[dict[str, Any]], requested: list[Any], op: str | None
) -> list[Any]:
    """``thread_dims`` is not an item of its own: it is the callout (``M12 long. útil 20``) of every external thread of the
    part (an ``external_thread`` feature) that no dimension of the sheet gives yet, made now from the cylinders the threads
    were cut on as ``dim`` items of kind ``diameter`` (``<id>``, ``<id>2``...)."""
    if not any(isinstance(i, dict) and i.get("type") == "thread_dims" for i in requested):
        return requested
    dimensioned = {
        str(f)
        for i in [*stored, *(r for r in requested if isinstance(r, dict))]
        if i.get("type") == "dim" and i.get("kind") == "diameter"
        for f in ([i["face"]] if isinstance(i.get("face"), str) else list(i.get("face") or ()))
    }
    faces = list(
        dict.fromkeys(
            str(r["cylinder"])
            for r in thread_records.read_threads(doc.fc_doc)
            if r.get("cylinder") and str(r["cylinder"]) not in dimensioned
        )
    )
    out: list[Any] = []
    for item in requested:
        if not (isinstance(item, dict) and item.get("type") == "thread_dims"):
            out.append(item)
            continue
        prefix = str(item.get("id") or "thread")
        if not faces:
            continue  # the sheet (or the others of this call) dimension the threads already: nothing to add
        out.extend(
            {"type": "dim", "id": prefix if number == 1 else f"{prefix}{number}", "kind": "diameter", "face": face}
            for number, face in enumerate(faces, start=1)
        )
    return out


_KEYWAY_FALLBACK: dict[str, list[dict[str, Any]]] = {}
"""The width and the depth of a keyway as plain dimensions, by the id of the removed section that was to carry them: a section that
finds no room on the sheet is refused and its dimensions go where the sheet has a place for them (``add`` puts them back)."""


def _expand_keyway_dims(
    doc: reg.DocumentRec, spec: dict[str, Any], requested: list[Any], op: str | None
) -> list[Any]:
    """``keyway_dims`` is not an item of its own: it is the dimensions of the keyways of the part (width, depth as the
    standards give it, length, position), made now from the faces of the model as ``dim`` items with their own ids
    (``<id>_w``, ``_t``, ``_l``, ``_p``), so the sheet keeps plain dimensions that follow the normal rules."""
    if not any(isinstance(i, dict) and i.get("type") == "keyway_dims" for i in requested):
        return requested
    import items_views  # noqa: PLC0415 - the letters of the views

    _KEYWAY_FALLBACK.clear()
    along_side = "above" if turned_of(doc, spec, op) is not None else None  # a turned part's lengths stack below: the keyway's stand above
    slots =sheet_facts.collect(doc, _bodies(doc, spec.get("body"), op)).keyway_slots
    shown = tuple(views_of(spec))
    views_made = [i for i in [*spec.get("items", ()), *requested] if isinstance(i, dict) and i.get("type") in ("section", "detail", "aux_view")]
    taken = {str(i["letter"]) for i in views_made if i.get("letter")}
    auto = sum(1 for i in views_made if not i.get("letter"))  # views that take the next free letters when they are drawn
    out: list[Any] = []
    for item in requested:
        if not (isinstance(item, dict) and item.get("type") == "keyway_dims"):
            out.append(item)
            continue
        prefix = str(item.get("id") or "keyway")
        try:
            depth_tol = tuple(float(v) for v in item["depth_tol"]) if item.get("depth_tol") else None
        except (TypeError, ValueError) as exc:
            raise invalid_request("keyway_dims depth_tol is [upper, lower] in mm, like [0.2, 0]", op) from exc
        if depth_tol is not None and len(depth_tol) != 2:
            raise invalid_request("keyway_dims depth_tol is [upper, lower] in mm, like [0.2, 0]", op)
        if item.get("fit") is not None and not isinstance(item["fit"], str):
            raise invalid_request('keyway_dims fit is the ISO 286 class of the width, like "P9" or "N9"', op)
        made: list[Any] = []
        for number, slot in enumerate(slots, start=1):
            name = prefix if len(slots) == 1 else f"{prefix}{number}"
            section = None
            end_view = sheet_keyway.PROFILE_VIEW.get(sheet_keyway.axis_name(slot.cylinder.axis) or "")
            if item.get("section", True) and end_view is not None and end_view not in shown:  # a sheet with the end view keeps its dimensions there
                # the keyway is shown in a REMOVED section across it, on the extension of its cutting line: it carries the width
                # (with its class) and the depth, and the end view of the part is not needed for them
                free = [letter for letter in items_views.LETTERS if letter not in taken]
                letter = str(item.get("letter") or free[min(auto, len(free) - 1)])
                section = f"{letter}-{letter}"
            dims = sheet_keyway.dimension_specs(slot, name, shown, section, item.get("fit"), depth_tol, along_side)
            if section and any(d.get("view") == section for d in dims):  # no width or depth to carry: no section
                taken.add(section[0])
                _KEYWAY_FALLBACK[f"{name}_sec"] = [
                    d for d in sheet_keyway.dimension_specs(slot, name, shown, None, item.get("fit"), depth_tol, along_side) if str(d["id"]).endswith(("_w", "_t"))
                ]
                made.append(
                    {"type": "section", "id": f"{name}_sec", "at": [round(v, 4) for v in sheet_keyway.middle_of(slot)],
                     "normal": list(slot.cylinder.axis), "letter": section[0], "removed": True}
                )  # fmt: skip
            elif section:
                dims = sheet_keyway.dimension_specs(slot, name, shown, None, item.get("fit"), depth_tol, along_side)
            made += dims
        if not made:
            raise invalid_request("the part has no keyway whose walls can be dimensioned (keyway_dims)", op)
        out.extend(made)
    return out


def _expand_baseline_dims(
    doc: reg.DocumentRec, spec: dict[str, Any], stored: list[dict[str, Any]], requested: list[Any], op: str | None
) -> list[Any]:
    """``baseline_dims`` is not an item of its own: it is the length of every step of a turned part counted from ONE datum
    face (``from``, a shoulder or an end the engineer or the intent file named: it is never chosen here), made now as ``dim``
    items ``<id>1``, ``<id>2``... from that face (parallel dimensions, ISO 129-1). A length the sheet already gives from that
    face is not made again."""
    if not any(isinstance(i, dict) and i.get("type") == "baseline_dims" for i in requested):
        return requested
    facts = sheet_facts.collect(doc, _bodies(doc, spec.get("body"), op))
    skip = {f.face_id for slot in facts.keyway_slots for f in slot.ends if f.face_id}
    spans = [(c.axis, c.lo, c.hi) for c in facts.cylinders if not c.concave]
    given = {
        (str((i.get("from") or {}).get("face")), str((i.get("to") or {}).get("face")))
        for i in stored
        if i.get("type") == "dim" and i.get("kind") == "distance" and isinstance(i.get("from"), dict) and isinstance(i.get("to"), dict)
    }
    out: list[Any] = []
    for item in requested:
        if not (isinstance(item, dict) and item.get("type") == "baseline_dims"):
            out.append(item)
            continue
        datum = item.get("from")
        if isinstance(datum, dict):
            datum = datum.get("face")
        if not isinstance(datum, str) or not datum.startswith("face:"):
            raise invalid_request(
                'baseline_dims needs from="face:N": the shoulder or the end the lengths are counted from (the face the intent file '
                "names as the axial reference, or the one the engineer picks: it is never chosen for him)",
                op,
            )
        try:
            made = sheet_turned.baseline_dims(
                datum, facts.planes, spans, skip, str(item.get("id") or "bl"), item.get("view"), item.get("side")
            )
        except sheet_turned.BaselineError as exc:
            raise invalid_request(str(exc), op) from exc
        fresh = [m for m in made if (datum, m["to"]["face"]) not in given and (m["to"]["face"], datum) not in given]
        if not fresh:
            raise invalid_request(f"every length from {datum} is on the sheet already (baseline_dims)", op)
        out.extend(fresh)
    return out


def _declare_symmetry(spec: dict[str, Any], declared: Any) -> None:
    """The design intent says the part is symmetric (``True``) or not (``False``); ``None`` leaves the sheet as it was. It
    is never taken from the shape: a plate can be symmetric by chance, and a plane of symmetry that is not the designer's
    would become a reference the holes are counted from and a centre line on the sheet."""
    if declared is None:
        return
    if declared:
        spec["symmetry"] = True
    else:
        spec.pop("symmetry", None)


def update(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the sheet after the part changed: the views are laid out again for the new size (the scale asked for at
    creation is kept, or the largest standard one that fits again) and every item is kept. ``title_block`` sets fields of
    the title block."""
    op = params.get("operation_id")
    page = _need_page(doc.fc_doc, params.get("sheet"), op)
    spec = spec_of(page)
    spec.pop("legacy", None)
    if params.get("iso_colour") is not None:  # the convention in force now (colour or line drawing)
        if params["iso_colour"]:
            spec["iso_colour"] = True
        else:
            spec.pop("iso_colour", None)
    apply_title_block(spec, params.get("title_block"), op)
    _declare_symmetry(spec, params.get("symmetry"))
    _sync_auto_items(doc, spec, op)
    _clear_approval(spec)
    page, layout, waited, warnings = _build(doc, spec, op)
    return _result(doc, page, layout.scale, waited, warnings, op)


AUTO_LOCATE_ID = "loc"  # the default location of the holes (a datum_dims with auto)
AUTO_THICKNESS_ID = "tn"  # the thickness note of a sheet of one view
LEGACY_ISO286_ID = "n286"  # the "tables transcribed" note older sheets carry: the sheet no longer writes it
AUTO_HOLES_ID = "hp"  # the diameter, centre distance and edge distance of the through holes of an assembly
AUTO_IDS = (AUTO_LOCATE_ID, AUTO_THICKNESS_ID, AUTO_HOLES_ID)
AUTO_PATTERN_ID = re.compile(r"pd[0-9]+(a[0-9]*)?\Z")  # the polar dimensions of a circular pattern: pd1 (its diameter), pd1a (its angle)
ANGLES = ("first", "third")


def is_auto_id(item_id: str) -> bool:
    """Whether ``item_id`` is the id of an item every sheet carries by itself (the default location, the thickness note, the
    polar dimensions of a circular pattern)."""
    return item_id in AUTO_IDS or bool(AUTO_PATTERN_ID.match(item_id))


def _has_thickness(
    doc: reg.DocumentRec, body: str | None, view: str, op: str | None, turned: bool = False
) -> bool:
    """Whether the length of the part along the line of sight of its one view is a thickness a note can state: any part but
    a round one, which has it only when the view looks along its axis and it is a disc (a shaft seen from the side, a
    sleeve seen end-on: its length is no thickness)."""
    found = LiveGeometry(doc, _shapes(doc, body, op), op).round_extent()
    if found is None:
        return True
    if turned and view == "front":  # the turned front view looks across the axis, whatever the axis is
        return False
    axis, diameter = found
    direction = {"front": (0.0, 1.0, 0.0), "top": (0.0, 0.0, 1.0), "right": (1.0, 0.0, 0.0)}[view]
    if not sc.parallel(direction, axis):
        return False
    length = sum(abs(a) * e for a, e in zip(axis, _extents(doc, body, op), strict=True))
    return length <= FLAT_RATIO * diameter


VIEW_DIRECTIONS = sheet_pattern.VIEW_DIRECTIONS  # what each standard view looks along


def _pattern_items(found: list[sc.HoleInfo], standard: list[str], round_axis: tuple[sc.Vec3, sc.Vec3] | None = None) -> list[dict[str, Any]]:
    """The polar dimensions the sheet carries by itself for the holes (``sheet_pattern.auto_items``)."""
    return sheet_pattern.auto_items(found, standard, round_axis)


def _round_axis(doc: reg.DocumentRec, body: str | None, op: str | None) -> tuple[sc.Vec3, sc.Vec3] | None:
    """The axis of a part that is a body of revolution (a disc, a flange): its direction and a point of it (the middle of the
    box across it), else ``None``."""
    shapes = _shapes(doc, body, op)
    found = LiveGeometry(doc, shapes, op).round_extent()
    if found is None or not shapes:
        return None
    boxes = [_exact_box(shape) for shape in shapes]
    return found[0], (
        (min(b.XMin for b in boxes) + max(b.XMax for b in boxes)) / 2,
        (min(b.YMin for b in boxes) + max(b.YMax for b in boxes)) / 2,
        (min(b.ZMin for b in boxes) + max(b.ZMax for b in boxes)) / 2,
    )


def _sync_auto_items(doc: reg.DocumentRec, spec: dict[str, Any], op: str | None) -> None:
    """Keep the items every sheet carries by itself in step with the part and the views: the baseline location of the holes
    (``loc``) when the part has holes, the thickness note (``tn``) when the sheet has ONE standard view. The engineer may
    take one off with ``remove`` (it is then ``declined`` and never comes back); the rest come and go with their reason."""
    items = spec["items"]
    declined = set(spec.get("declined", ()))
    standard = [v for v in views_of(spec) if v != "iso"]
    assembly = _is_assembly(doc, spec.get("body"))
    wanted: dict[str, dict[str, Any]] = {}
    if assembly and LiveGeometry(doc, _shapes(doc, spec.get("body"), op), op).assembly_holes():
        wanted[AUTO_HOLES_ID] = {"id": AUTO_HOLES_ID, "type": "hole_positions", "auto": True}
    found_holes = [] if assembly else hole_infos(doc, spec.get("body"), op)
    if found_holes:
        wanted[AUTO_LOCATE_ID] = {"id": AUTO_LOCATE_ID, "type": "datum_dims", "auto": True}
        wanted.update({i["id"]: i for i in _pattern_items(found_holes, standard, _round_axis(doc, spec.get("body"), op))})
    if not assembly and len(standard) == 1 and _has_thickness(
        doc, spec.get("body"), standard[0], op, turned_of(doc, spec, op) is not None
    ):
        wanted[AUTO_THICKNESS_ID] = {"id": AUTO_THICKNESS_ID, "type": "note", "auto": "thickness"}
    if AUTO_THICKNESS_ID in wanted and any(
        i.get("type") == "note" and not i.get("auto") and items_notes.states_thickness(str(i.get("text") or "")) for i in items
    ):
        del wanted[AUTO_THICKNESS_ID]  # a note of the engineer states the thickness: the sheet does not say it twice
    items = [i for i in items if i.get("auto") != "iso286"]  # a note of older sheets that no sheet writes any more
    have = {str(i["id"]) for i in items}
    for key, item in wanted.items():
        if key not in declined and key not in have:
            items.append(item)
    for position, i in enumerate(items):  # a pattern that moved: its items follow it (what the layout placed goes with the old one)
        want = wanted.get(str(i["id"]))
        if i.get("auto") and i.get("type") == "pattern_dim" and want is not None and any(i.get(k) != want.get(k) for k in ("at", "kind")):
            items[position] = want
    spec["items"] = [
        i for i in items if not (i.get("auto") and is_auto_id(str(i["id"])) and str(i["id"]) not in wanted)
    ]


# --- the finished drawing: answers and the engineer's approval -----------------------------------------------------------

APPROVER_FIELD = "approval_person"  # "Aprobó" of the title block: written by ``approve`` and by nothing else
PENDING_SHOWN = 12


def _clear_approval(spec: dict[str, Any]) -> None:
    """The sheet changed: whoever approved it approved another drawing. ``Aprobó`` goes empty again. ``edits`` counts
    the changes, so a report can say at which one it was made; a change does NOT make a report old."""
    spec.pop("approval", None)
    spec["edits"] = int(spec.get("edits", 0)) + 1  # shown next to a report's ``edit``; it gates nothing
    if spec.get("fields", {}).get(APPROVER_FIELD):
        spec["fields"][APPROVER_FIELD] = ""


REVIEWERS = ("fabricator", "engineer")
REPORT_PREFIX = {"fabricator": "fab", "engineer": "eng"}
OLD_REVIEWERS = {"fabricante": "fabricator", "ingeniero": "engineer"}
"""The Spanish names of the reviewers in a sheet saved before 0.10 and in a call that still uses them."""


def _report_rows(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The last report of each reviewer, with ``edit``: the sheet's change counter when it was recorded."""
    return {
        who: {
            "n": int(report["n"]),
            "by": who,
            "at": float(report.get("at", 0.0)),
            "remarks": list(report.get("remarks", [])),
            "edit": int(report.get("edits", 0)),
        }
        for who, report in spec.get("reports", {}).items()
        if who in REVIEWERS
    }


def _request_row(spec: dict[str, Any]) -> dict[str, Any] | None:
    request = spec.get("review_request")
    if not request:
        return None
    return {"at": float(request["at"])}


def review_report_action(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Store a reviewer's report in the sheet spec (the last one of each reviewer is kept). Its remarks get the ids
    ``fab<n>.<i>`` / ``eng<n>.<i>``, which ``answer`` accepts. Does not change the sheet."""
    op = params.get("operation_id")
    who = str(params.get("by") or "")
    who = OLD_REVIEWERS.get(who, who)
    if who not in REVIEWERS:
        raise invalid_request(f"review_report needs by: {' | '.join(REVIEWERS)}", op)
    page = _need_page(doc.fc_doc, params.get("sheet"), op)
    spec = spec_of(page)
    spec.pop("legacy", None)
    number = int(spec.get("report_n", 0)) + 1
    prefix = f"{REPORT_PREFIX[who]}{number}"
    remarks = []
    for position, raw in enumerate(params.get("remarks") or (), start=1):
        remark = {k: " ".join(str(raw.get(k, "")).split()) for k in ("kind", "where", "text", "fix")}
        remark["kind"] = remark["kind"] or "other"
        remarks.append({"id": f"{prefix}.{position}", **remark})
    spec["report_n"] = number
    spec.setdefault("reports", {})[who] = {
        "n": number,
        "at": float(params.get("at") or 0.0),
        "edits": int(spec.get("edits", 0)),
        "remarks": remarks,
    }
    write_spec(page, spec)
    return {"sheet": spec["sheet"], "changed": [r["id"] for r in remarks]}


def request_review_action(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Record when the engineer was asked who reviews this sheet (it stays through the changes of the sheet)."""
    op = params.get("operation_id")
    if params.get("at") is None:
        raise invalid_request("request_review needs at", op)
    page = _need_page(doc.fc_doc, params.get("sheet"), op)
    spec = spec_of(page)
    spec.pop("legacy", None)
    spec["review_request"] = {"at": float(params["at"])}
    write_spec(page, spec)
    return {"sheet": spec["sheet"], "changed": []}


def review_choice_action(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Record who the engineer chose to review this sheet (``by``: engineer | fabricator); it stays through changes."""
    op = params.get("operation_id")
    who = str(params.get("by") or "")
    who = OLD_REVIEWERS.get(who, who)
    if who not in REVIEWERS:
        raise invalid_request(f"review_choice needs by: {' | '.join(REVIEWERS)}", op)
    page = _need_page(doc.fc_doc, params.get("sheet"), op)
    spec = spec_of(page)
    spec.pop("legacy", None)
    spec["review_choice"] = {"who": who}
    write_spec(page, spec)
    return {"sheet": spec["sheet"], "changed": []}


def _choice_row(spec: dict[str, Any]) -> str:
    choice = spec.get("review_choice")
    if choice and choice.get("who") in REVIEWERS:
        return str(choice["who"])
    return ""


def _remark_ids(spec: dict[str, Any]) -> list[str]:
    return [str(r["id"]) for report in spec.get("reports", {}).values() for r in report.get("remarks", [])]


REQUIREMENT_PREFIX = "req."  # the ids of the rows of the table of requirements: the caller (the server) evaluated them


def answer(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Store the answer to one finding, question or review remark in the sheet spec (so it survives save and reopen). The
    id must be one the review lists now, unless ``memory`` says the caller (who read the memory entry) named an entry or
    it is a row of the table of requirements (``req.``), which the caller evaluated against the facts of this review."""
    op = params.get("operation_id")
    item_id = str(params.get("id") or "").strip()
    verdict = params.get("verdict")
    text = " ".join(str(params.get("text") or "").split())
    if not item_id or verdict not in ("applies", "not_applies") or not text:
        raise invalid_request("answer needs id, verdict (applies | not_applies) and text", op)
    ((page, spec, number, total),) = _all_sheets(doc, params.get("sheet") or spec_name_of_first(doc, op), op)
    if not params.get("memory") and not item_id.startswith(REQUIREMENT_PREFIX):
        findings, questions = review_report(doc, page, spec, number, total, op)
        known = [r["id"] for r in findings] + [q["id"] for q in questions] + _remark_ids(spec)
        if item_id not in known:
            pending = [r["id"] for r in findings if not r["answer"]] + [q["id"] for q in questions if not q["answer"]]
            shown = ", ".join(pending[:PENDING_SHOWN]) + (
                f" (+{len(pending) - PENDING_SHOWN})" if len(pending) > PENDING_SHOWN else ""
            )
            raise invalid_request(
                f"sheet {spec['sheet']} has no finding or question {item_id!r} now (a finding that was fixed is gone); "
                f"pending: {shown or 'none'}",
                op,
            )
    spec.pop("legacy", None)
    stored = {"verdict": str(verdict), "text": text}
    ordered_by = " ".join(str(params.get("by") or "").split())
    if ordered_by:  # the engineer who ordered an answer the harness would not take on the agent's word
        stored["ordered_by"] = ordered_by
    spec.setdefault("answers", {})[item_id] = stored
    write_spec(page, spec)
    return {"sheet": spec["sheet"], "changed": [item_id]}


def approve(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Put the engineer's name in the title block (``Aprobó``) with the words he approved it with. The one place that
    field is written; any later change of the sheet empties it again."""
    op = params.get("operation_id")
    by = " ".join(str(params.get("by") or "").split())
    reason = " ".join(str(params.get("text") or "").split())
    if not by or not reason:
        raise invalid_request("approve needs by (the engineer) and text (his words)", op)
    page = _need_page(doc.fc_doc, params.get("sheet"), op)
    activate(doc.fc_doc)
    spec = spec_of(page)
    spec.pop("legacy", None)
    spec.setdefault("fields", {})[APPROVER_FIELD] = by
    spec["approval"] = {"by": by, "reason": reason, "date": date.today().isoformat()}
    write_spec(page, spec)
    _sync_template_fields(doc.fc_doc, op)
    wait_views(doc.fc_doc, page, op)
    return {"sheet": spec["sheet"], "changed": []}


# --- items ------------------------------------------------------------------------------------------------------------


def record_action(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Store what the order that composed the sheet decided (``composed``): the signature of the decisions that affect the
    whole sheet. The items pinned by hand (``composed["pinned"]``) stay. It changes nothing on the sheet."""
    op = params.get("operation_id")
    page = _need_page(doc.fc_doc, params.get("sheet"), op)
    spec = spec_of(page)
    spec.pop("legacy", None)
    pinned = list((spec.get("composed") or {}).get("pinned") or ())
    spec["composed"] = {**dict(params.get("composed") or {}), **({"pinned": pinned} if pinned else {})}
    write_spec(page, spec)
    return {"sheet": spec["sheet"], "changed": []}


def _drawn_by_item(ctx: sc.Context) -> dict[str, tuple[Any, ...]]:
    """What every item of the sheet drew, by item id (the views are not items)."""
    return {u.key: tuple(u.entities) for u in ctx.extras.get("units", []) if u.kind != sheet_units.KIND_VIEW}


def _edited(item: dict[str, Any], patch: dict[str, Any], op: str | None) -> dict[str, Any]:
    try:
        return sheet_items.edited(item, patch)
    except sc.ItemError as exc:
        raise invalid_request(f"{item['id']} ({item['type']}): {exc}; nothing was changed", op) from exc


def edit_action(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """``annotate(action="edit")``: change items IN PLACE and nothing else. The sheet is drawn as it stands and with the edit;
    the edit is refused (nothing is stored) when an edited item cannot be drawn, lies on another object where it did not
    before, or makes any other item draw differently (it would be moved silently). An edited item is pinned: the layout and a
    later compose leave it where it is."""
    op = params.get("operation_id")
    fc_doc = doc.fc_doc
    page = _need_page(fc_doc, params.get("sheet"), op)
    activate(fc_doc)
    spec = spec_of(page)
    spec.pop("legacy", None)
    items = [dict(i) for i in spec.get("items", [])]
    position = {str(i["id"]): n for n, i in enumerate(items)}
    patches = [dict(p) for p in params.get("items") or ()]
    missing = [str(p["id"]) for p in patches if str(p["id"]) not in position]
    if missing:
        raise invalid_request(f"no item {', '.join(missing)} on sheet {spec['sheet']}; it has: {', '.join(sorted(position))}", op)
    edited = {str(p["id"]): _edited(items[position[str(p["id"])]], p, op) for p in patches}
    changing = {k: v for k, v in edited.items() if v != items[position[k]]}
    if not changing:
        raise invalid_request(f"{', '.join(edited)}: the item already is as asked; nothing to change", op)
    trial_items = [changing.get(str(i["id"]), i) for i in items]
    composed = dict(spec.get("composed") or {})
    composed["pinned"] = sorted({*(composed.get("pinned") or ()), *changing})
    layout = dict(spec.get("layout") or {})
    placed = {k: v for k, v in (layout.get("placed") or {}).items() if k not in changing}  # his place now, not the planner's
    final = {**spec, "items": trial_items, "composed": composed, **({"layout": {**layout, "placed": placed}} if layout else {})}
    wait_views(fc_doc, page, op)
    _, _, before = compose(doc, page, spec, 1, 1, op)
    tried = compose(doc, page, final, 1, 1, op)
    refused = {r.id: r.detail for r in tried[1] if r.status == "error" and r.id in changing}
    if refused:
        raise invalid_request(
            "; ".join(f"{k}: {why}" for k, why in refused.items()) + "; nothing was changed", op
        )
    known = {(c.a, c.b, c.kind) for c in before.extras.get("collisions", [])}
    hits = [c for c in tried[2].extras.get("collisions", []) if (c.a, c.b, c.kind) not in known]
    if hits:
        raise invalid_request(
            "the edit would put "
            + "; ".join(f"{c.a} on {c.b} ({c.what or c.kind})" for c in hits[:3])
            + "; nothing was changed (another side, another offset_mm, or the other item is the one to move)",
            op,
        )
    was, now = _drawn_by_item(before), _drawn_by_item(tried[2])
    shifted = sorted(k for k in now if k not in changing and k in was and was[k] != now[k])
    if shifted:
        raise invalid_request(
            f"the edit would make {', '.join(shifted[:5])} draw differently (it makes them give way); nothing was changed "
            "(a place that does not crowd them, or edit them too)",
            op,
        )
    spec["items"] = trial_items
    spec["composed"] = composed
    if layout:
        spec["layout"] = {**layout, "placed": placed}
    _clear_approval(spec)
    write_spec(page, spec)
    _sync_template_fields(fc_doc, op)
    return _result(
        doc, page, _scale_of(page) or 1.0, wait_views(fc_doc, page, op), [], op, changed=tuple(sorted(changing)), tried=(final, tried)
    )


def annotate(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """``add`` / ``remove`` items of a sheet, or ``update`` its layout. An item that cannot be drawn against the part is
    reported in the result and NOT stored (the others are)."""
    op = params.get("operation_id")
    action = str(params.get("action", ""))
    fc_doc = doc.fc_doc
    if action == "update":
        return update(doc, params)
    if action == "answer":
        return answer(doc, params)
    if action == "layout":
        return layout_action(doc, params)
    if action == "edit":
        return edit_action(doc, params)
    if action == "record":
        return record_action(doc, params)
    if action == "approve":
        return approve(doc, params)
    if action == "review_report":
        return review_report_action(doc, params)
    if action == "request_review":
        return request_review_action(doc, params)
    if action == "review_choice":
        return review_choice_action(doc, params)
    if action in ("add_view", "remove_view"):
        return _change_view(doc, params, action, op)
    if action not in ("add", "remove", "remove_sheet"):
        raise invalid_request(
            f"unknown drawing action {action!r}, expected add, remove, remove_sheet, update, add_view, remove_view, "
            "answer, approve, request_review, review_choice, review_report, layout, edit or record",
            op,
        )
    page = _need_page(fc_doc, params.get("sheet"), op)
    activate(fc_doc)
    spec = spec_of(page)
    spec.pop("legacy", None)
    items = [dict(i) for i in spec.get("items", [])]
    if action == "remove_sheet":
        remove_page(fc_doc, page)
        _sync_template_fields(fc_doc, op)
        fc_doc.recompute()
        remaining = pages(fc_doc)
        return {"sheet": spec["sheet"], "removed": True, "sheets": [spec_of(p)["sheet"] for p in remaining]}
    if action == "remove":
        wanted = [str(i) for i in (params.get("ids") or ())]
        if not wanted:
            raise invalid_request(
                "remove needs the ids of the items to take off (remove_sheet deletes the whole sheet)", op
            )
        ids = {i["id"] for i in items}
        missing = [r for r in wanted if r not in ids]
        if missing:
            raise invalid_request(
                f"no item {', '.join(missing)} on sheet {spec['sheet']}; it has: {', '.join(sorted(ids))}", op
            )
        items = [i for i in items if i["id"] not in set(wanted)]
        spec["items"] = items
        taken_off = [r for r in wanted if is_auto_id(r)]
        if taken_off:  # the engineer does not want it: it does not come back
            spec["declined"] = sorted({*spec.get("declined", ()), *taken_off})
        _sync_auto_items(doc, spec, op)
        _clear_approval(spec)
        write_spec(page, spec)
        _sync_template_fields(fc_doc, op)
        return _result(doc, page, _scale_of(page) or 1.0, wait_views(fc_doc, page, op), [], op, changed=tuple(wanted))
    requested = _expand_keyway_dims(doc, spec, list(params.get("items") or ()), op)
    requested = _expand_baseline_dims(doc, spec, items, requested, op)
    requested = _expand_chamfer_dims(doc, items, requested, op)
    asked_threads = any(isinstance(i, dict) and i.get("type") == "thread_dims" for i in requested)
    requested = _expand_thread_dims(doc, items, requested, op)
    requested, routed = _route_title_block_notes(spec, requested, op, material_of(fc_doc))
    if not requested and routed:
        write_spec(page, spec)
        _sync_template_fields(fc_doc, op)
        return _result(doc, page, _scale_of(page) or 1.0, wait_views(fc_doc, page, op), routed, op)
    if not requested and asked_threads:  # every thread has its callout: the sheet stays as it is
        return _result(doc, page, _scale_of(page) or 1.0, wait_views(fc_doc, page, op), [], op)
    if not requested:
        raise invalid_request('add needs items: a list like [{"type": "hole_dim", "all": true}]', op)
    if len(items) + len(requested) > sheet_items.MAX_ITEMS:
        raise invalid_request(f"a sheet takes at most {sheet_items.MAX_ITEMS} items", op)
    taken = {i["id"] for i in items}
    added: list[dict[str, Any]] = []
    for position, raw in enumerate(requested, start=1):
        try:
            done = sheet_items.normalise(raw, taken, spec["next"])
            sheet_items.refuse_repeated_note(done, [*items, *added])
        except sc.ItemError as exc:
            raise invalid_request(f"item {position} of {len(requested)}: {exc}", op) from exc
        spec["next"] += 1
        taken.add(done["id"])
        added.append(done)
    waited = wait_views(fc_doc, page, op)
    refused, tried = _refused(doc, page, spec, items, added, op)
    lost = [sid for sid in refused if sid in _KEYWAY_FALLBACK]
    if lost:  # a removed section of a keyway found no room: its width and depth stand on the sheet as plain dimensions
        survivors = [a for a in added if a["id"] not in refused]
        names = {a["id"] for a in survivors} | {i["id"] for i in items}
        again: list[dict[str, Any]] = []
        for sid in lost:
            for raw in _KEYWAY_FALLBACK[sid]:
                try:
                    done = sheet_items.normalise(raw, names, spec["next"])
                except sc.ItemError:
                    continue
                names.add(done["id"])
                again.append(done)
        added = survivors + again
        refused, tried = _refused(doc, page, spec, items, added, op)
    keep = [a for a in added if a["id"] not in refused]
    spec["items"] = items + keep
    _sync_auto_items(doc, spec, op)
    if keep:
        _clear_approval(spec)
    write_spec(page, spec)
    _sync_template_fields(fc_doc, op)
    waited += wait_views(fc_doc, page, op)
    return _result(
        doc, page, _scale_of(page) or 1.0, waited, routed, op, refused=refused, changed=tuple(a["id"] for a in keep), tried=tried
    )


def _plan_boxes(page: Any, ctx: sc.Context) -> dict[str, tuple[float, float, float, float]]:
    """The rectangle of every view of the sheet (page mm, y up) by name: the standard ones, and the others (the isometric
    one, a view an item made) as ``iso``, ``iso2``..."""
    boxes = {name: v.box for name, v in ctx.views.items()}
    extra = [
        r
        for r in ctx.extras.get("view_boxes", [])
        if not any(max(abs(a - b) for a, b in zip(r, k, strict=True)) < 0.5 for k in boxes.values())
    ]
    if not extra and ctx.extras.get("iso_box") is not None:
        extra = [ctx.extras["iso_box"]]
    for number, rect in enumerate(extra, start=1):
        boxes["iso" if number == 1 else f"iso{number}"] = tuple(rect)  # type: ignore[assignment]
    return boxes


LIFT_STEP_MM = sc.STACK_STEP_MM
"""How far a dimension line moves outward to open room for a callout (one step of the dimension stack)."""
MAX_LIFTS = 2


def _plan_sheet(
    doc: reg.DocumentRec, page: Any, spec: dict[str, Any], items: list[dict[str, Any]], op: str | None
) -> tuple[model.Sheet, sc.Context, sheet_plan_bridge.Prepared, sheet_plan_bridge.Outcome]:
    """Compose the sheet with ``items`` as they are, read what every item drew and plan the layout of it."""
    sheet, _results, ctx = compose(doc, page, {**spec, "items": items}, 1, 1, op)
    units = list(ctx.extras.get("units", []))
    lists = [u.rect for u in units if u.item_type == "parts_list" and u.entities]
    prepared = sheet_plan_bridge.prepare(
        (sheet.width, sheet.height),
        ctx.area,
        ctx.extras.get("title_block"),
        _plan_boxes(page, ctx),
        [u for u in units if u.item_type != "parts_list"],
        items,
        lists[0] if lists else None,
        sheet_plan_bridge.view_outline(ctx.extras.get("view_ink", [])),
    )
    return sheet, ctx, prepared, sheet_plan_bridge.run(prepared)


def _lifted(items: list[dict[str, Any]], step: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """The items with the overall dimensions ``step`` steps farther out than the first slot of the stack (they stand
    outermost, so a callout whose way out is closed by them gets the room between them and the part), and what was written."""
    written: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for item in items:
        if item.get("type") == "overall" and item.get("offset_mm") is None and item.get("side") is None:
            written[str(item["id"])] = {"offset_mm": sc.DEFAULT_OFFSET_MM + LIFT_STEP_MM * step}
            item = {**item, **written[str(item["id"])]}
        out.append(item)
    return out, written


def global_layout(doc: reg.DocumentRec, page: Any, spec: dict[str, Any], op: str | None) -> dict[str, Any]:
    """Place the linear dimensions of the whole sheet at once (``sheet_plan_bridge``): the sheet is drawn as it stands, the
    planner reads what every item drew, and each dimension or leader that asked for no place is given the ``side`` and ``offset_mm`` (or the ``elbow_mm``) it
    stands at. What the planner placed before is planned again (it is not the engineer's). A callout that has no way out that
    crosses no dimension line, no other leader and no text gets the overall dimensions moved outward one step (up to
    ``MAX_LIFTS``) when that places it and the sheet still fits. When the planned sheet collides
    more than the one it replaces, nothing is kept. Returns what the call reports (``layout_*``)."""
    items = [dict(i) for i in spec.get("items", [])]
    sheet_plan_bridge.forget_planned(items, spec.get("layout"))  # a place the planner gave is not a place the engineer asked for
    spec["items"] = items
    _sheet, ctx, _prepared, outcome = _plan_sheet(doc, page, spec, items, op)
    original = items
    lifted: dict[str, dict[str, Any]] = {}
    for step in range(1, MAX_LIFTS + 1):  # one step may not be enough room: the next one is tried from the sheet as it was
        if not outcome.blocked:
            break
        trial, written = _lifted(original, step)
        if not written:
            break
        _t_sheet, t_ctx, _t_prepared, t_outcome = _plan_sheet(doc, page, spec, trial, op)
        collisions = len(t_ctx.extras.get("collisions", [])) <= len(ctx.extras.get("collisions", []))
        if len(t_outcome.blocked) < len(outcome.blocked) and t_outcome.fits and collisions:
            items, ctx, outcome, lifted = trial, t_ctx, t_outcome, written
    unplaced = [f"{key}: {why}" for key, why in outcome.unplaced]
    kept: dict[str, dict[str, Any]] = {}
    worse = False
    if outcome.placements:
        wanted = dict(outcome.placements)
        for attempt in range(4):  # a place that makes a new collision is dropped, the others stay
            planned = [{**i, **wanted[str(i["id"])]} if str(i["id"]) in wanted else i for i in items]
            _, answers, after = compose(doc, page, {**spec, "items": planned}, 1, 1, op)
            known = {(c.a, c.b, c.kind) for c in ctx.extras.get("collisions", [])}
            blamed = {n for c in after.extras.get("collisions", []) if (c.a, c.b, c.kind) not in known for n in (c.a, c.b)}
            dropped = [k for k in wanted if k in blamed]
            if not dropped or len(wanted) == len(dropped) or attempt == 3:
                break
            for key in dropped:
                del wanted[key]
        broke = {r.id for r in answers if r.status == "error"}
        worse = len(after.extras.get("collisions", [])) > len(ctx.extras.get("collisions", []))
        if worse:
            unplaced += [
                f"{key}: the planned place collided more than the one it has now: kept where it was"
                for key in outcome.placements
            ]
        else:
            kept = {k: v for k, v in wanted.items() if k not in broke}
            unplaced += [
                f"{key}: the planned place made a collision: kept where it was" for key in outcome.placements if key not in wanted
            ]
            unplaced += [f"{key}: the CAD refused the planned place: kept where it was" for key in wanted if key in broke]
    if worse:  # nothing planned is kept: the dimensions stay where they were too
        items, lifted = original, {}
    kept = {**lifted, **kept}
    spec["items"] = [{**i, **kept[str(i["id"])]} if str(i["id"]) in kept else i for i in items]
    spec["layout"] = {"placed": kept, "unplaced": unplaced}
    return {
        "layout_placed": sorted(kept),
        "layout_unplaced": unplaced,
        "layout_fits": outcome.fits,
        "layout_needed": [round(outcome.needed[0], 1), round(outcome.needed[1], 1)],
    }


def layout_action(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """``annotate(action="layout")``: ``global_layout`` on the sheet, in one call."""
    op = params.get("operation_id")
    fc_doc = doc.fc_doc
    page = _need_page(fc_doc, params.get("sheet"), op)
    activate(fc_doc)
    spec = spec_of(page)
    spec.pop("legacy", None)
    report = global_layout(doc, page, spec, op)
    _clear_approval(spec)
    write_spec(page, spec)
    _sync_template_fields(fc_doc, op)
    waited = wait_views(fc_doc, page, op)
    return {
        **_result(doc, page, _scale_of(page) or 1.0, waited, [], op, changed=tuple(report["layout_placed"])),
        **report,
    }


def _change_view(doc: reg.DocumentRec, params: dict[str, Any], action: str, op: str | None) -> dict[str, Any]:
    """``add_view`` / ``remove_view``: the sheet is laid out again with the views it has now, over all the room, at the
    largest standard scale that fits (a scale asked for at creation is given up: the point of taking a view off is the room
    it frees). The items are kept; one that cannot be drawn without the view says so in its row, and the result carries
    the scale before and after."""
    page = _need_page(doc.fc_doc, params.get("sheet"), op)
    spec = spec_of(page)
    spec.pop("legacy", None)
    if projection_of(spec) == "hlr":
        raise invalid_request(
            "add_view / remove_view choose the views of a techdraw sheet: an hlr sheet has all of them", op
        )
    view = VIEW_ALIASES.get(str(params.get("view")), str(params.get("view")))
    if view not in VIEW_CHOICES:
        raise invalid_request(f"unknown view {view!r}: the views are {', '.join(VIEW_CHOICES)}", op)
    have = list(views_of(spec))
    if action == "remove_view":
        if view not in have:
            raise invalid_request(f"the sheet has no {view} view (it has {', '.join(have)})", op)
        have.remove(view)
        if view == "front" and len([v for v in have if v != "iso"]) != 1:
            raise invalid_request(
                "the front view can only go when exactly ONE other standard view stays (a plate seen from above: take "
                "the others off first): the views are laid out from it",
                op,
            )
    else:
        if view in have:
            raise invalid_request(f"the sheet already has the {view} view", op)
        have.append(view)
    before = _scale_of(page) or 1.0
    clean = _collision_count(doc, page, spec, op)
    spec["views"] = normalise_views(have, op)
    spec["scale_requested"] = None
    spec["isometric"] = "iso" in spec["views"]
    _sync_auto_items(doc, spec, op)
    _clear_approval(spec)
    page, layout, waited, warnings = _build(doc, spec, op)
    # the largest standard scale at which what the sheet carries still fits: a scale that puts a dimension off the sheet or
    # on another object is not the one that fits, so the next smaller one is tried, never below the scale it had
    smaller = [s for s in sheet_layout.STANDARD_SCALES if before <= s < layout.scale - 1e-9]
    while _collision_count(doc, page, spec, op) > clean and smaller:
        spec["scale_requested"] = smaller.pop(0)
        page, layout, extra, warnings = _build(doc, spec, op)
        waited += extra
    spec["isometric"] = "iso" in spec["views"]
    note = (
        f"{'taken off' if action == 'remove_view' else 'added'}: {view}; "
        f"scale {model.fmt_scale(before)} -> {model.fmt_scale(layout.scale)}"
    )
    result = _result(doc, page, layout.scale, waited, [note, *warnings], op)
    failing = [f"{r['id']} ({r['type']}): {r['detail']}" for r in result["items"] if r["status"] == "error"]
    if failing:
        result["warnings"] = [*result["warnings"], "items that cannot be drawn on these views: " + "; ".join(failing)]
    return result


def _collision_count(doc: reg.DocumentRec, page: Any, spec: dict[str, Any], op: str | None) -> int:
    """How many collisions (and objects off the drawing area) the sheet has as it is composed now."""
    found = pages(doc.fc_doc)
    _, _, ctx = compose(doc, page, spec_of(page), found.index(page) + 1, len(found), op)
    return len(ctx.extras.get("collisions", []))


def _refused(
    doc: reg.DocumentRec,
    page: Any,
    spec: dict[str, Any],
    existing: list[dict[str, Any]],
    added: list[dict[str, Any]],
    op: str | None,
) -> tuple[dict[str, tuple[str, str]], tuple[dict[str, Any], tuple[model.Sheet, list[sc.ItemResult], sc.Context]]]:
    """Draw the sheet with the new items and drop (by id, with its type and the reason) the ones that fail; the rest are
    drawn again without them, since a refused item must not leave its dimension slot taken. Also the spec of the last
    drawing and what it drew: when the sheet ends up with exactly that spec, the result does not draw it a third time."""
    refused: dict[str, tuple[str, str]] = {}
    trial = added
    trial_spec = dict(spec, items=existing + trial)
    drawn = compose(doc, page, trial_spec, 1, 1, op)
    for _ in range(len(added)):
        failed = {r.id: (r.type, r.detail) for r in drawn[1] if r.status == "error" and r.id in {a["id"] for a in trial}}
        if not failed:
            break
        refused.update(failed)
        trial = [a for a in trial if a["id"] not in failed]
        trial_spec = dict(spec, items=existing + trial)
        drawn = compose(doc, page, trial_spec, 1, 1, op)
    return refused, (trial_spec, drawn)


# --- composing --------------------------------------------------------------------------------------------------------


def _page_position(page: Any, view: Any) -> tuple[float, float]:
    """Centre of a view on the page (TechDraw coordinates, y up): a projection item sits relative to its group."""
    if str(view.TypeId) == "TechDraw::DrawProjGroupItem":
        for obj in page.Views:
            if str(obj.TypeId) == "TechDraw::DrawProjGroup" and view.Name in [item.Name for item in obj.Views]:
                return float(obj.X) + float(view.X), float(obj.Y) + float(view.Y)
    return float(view.X), float(view.Y)


def _view_box(view: Any) -> Any:
    """The box of the view's visible edges in the view's own coordinates (TechDraw keeps them y DOWN, like Qt)."""
    return Part.Compound(sheet_native.model_edges(view)).optimalBoundingBox(False, False)


def view_key(view: Any) -> str | None:
    """The name (``front`` / ``top`` / ``right``) a view of the projection group goes by: the key the sheet tagged it with
    (the anchor of a sheet without a front view is the top or the right one; a label cannot say, FreeCAD makes it unique in
    the document), else the group's own name for it."""
    tagged = str(getattr(view, sheet_native.PROP_VIEWKEY, ""))
    if tagged in ("front", "top", "right"):
        return tagged
    return VIEW_KEYS.get(str(getattr(view, "Type", "")))


def view_infos(page: Any) -> dict[str, sc.ViewInfo]:
    """The front / top / right views as the items see them (the isometric one carries no dimensions)."""
    infos: dict[str, sc.ViewInfo] = {}
    for view in part_views(page):
        if str(view.TypeId) != "TechDraw::DrawProjGroupItem":
            continue
        key = view_key(view)
        if key is None:
            continue
        x, y = _page_position(page, view)
        box = _view_box(view)
        scale = float(view.getScale())
        infos[key] = sc.ViewInfo(
            key,
            x,
            y,
            scale,
            g.vec(view.Direction),
            g.vec(view.XDirection),
            g.vec(view.getGeometricCenter()),
            (x + box.XMin, y - box.YMax, x + box.XMax, y - box.YMin),
            vertices=tuple(
                (x + float(v.X) * scale, y + float(v.Y) * scale)
                for v in sheet_native._scan(view.getVertexByIndex)  # noqa: SLF001
            ),
        )
    return infos


def hand_marks(page: Any) -> dict[str, int]:
    """How many dimensions and balloons of the TechDraw page that the mirror did not make (the engineer's own) sit on
    each view, by the name the review uses (front / top / right / isometric): plain data for ``sheet_review``. A
    dimension belongs to the view its first 2D reference points at, a balloon to its source view."""
    names: dict[str, str] = {}
    for view in part_views(page):
        key = view_key(view) if str(view.TypeId) == "TechDraw::DrawProjGroupItem" else None
        if key is None and str(view.Label) == sheet_native.VIEW_LABELS["isometric"]:
            key = "isometric"
        if key is not None:
            names[str(view.Name)] = key
    found: dict[str, int] = {}
    for obj in page.Views:
        kind = str(obj.TypeId)
        if sheet_mirror.PROP_KEY in obj.PropertiesList or not kind.endswith(("DrawViewDimension", "DrawViewBalloon")):
            continue
        try:
            if kind.endswith("DrawViewBalloon"):
                on = obj.SourceView
            else:
                first = obj.References2D[0]
                on = first[0] if isinstance(first, (list, tuple)) else first
        except (IndexError, TypeError, AttributeError):
            continue
        key = names.get(str(getattr(on, "Name", "")))
        if key is not None:
            found[key] = found.get(key, 0) + 1
    return found


def iso_info(page: Any) -> sc.ViewInfo | None:
    """The isometric view of the sheet as an item sees it (balloons can be placed on it); ``None`` when it is gone."""
    for view in part_views(page):
        if str(view.TypeId) == "TechDraw::DrawViewPart" and str(view.Label) == sheet_native.VIEW_LABELS["isometric"]:
            x, y = _page_position(page, view)
            box = _view_box(view)
            return sc.ViewInfo(
                "isometric",
                x,
                y,
                float(view.getScale()),
                g.vec(view.Direction),
                g.vec(view.XDirection),
                g.vec(view.getGeometricCenter()),
                (x + box.XMin, y - box.YMax, x + box.XMax, y - box.YMin),
            )
    return None


def view_rects(page: Any) -> list[sc.Rect]:
    """The page rectangle of the geometry of EVERY view of the sheet (isometric included), y up: where a derived view
    must not land."""
    rects: list[sc.Rect] = []
    for view in part_views(page):
        x, y = _page_position(page, view)
        box = _view_box(view)
        rects.append((x + box.XMin, y - box.YMax, x + box.XMax, y - box.YMin))
    return rects


def _edge_entities(edge: Any, x0: float, y0: float, layer: str) -> list[model.Entity]:
    """One edge of a view as sheet entities at the view's place ``(x0, y0)`` on the page (y up). TechDraw keeps the
    edges of a view in Qt's orientation, y DOWN, so every y is negated here (verified on an asymmetric part)."""
    kind = type(edge.Curve).__name__
    if kind == "Line":
        a, b = edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)
        return [model.Line(x0 + a.x, y0 - a.y, x0 + b.x, y0 - b.y, layer)]
    if kind == "Circle":
        centre = edge.Curve.Center
        cx, cy = x0 + centre.x, y0 - centre.y
        if edge.isClosed():
            return [model.Circle(cx, cy, float(edge.Curve.Radius), layer)]
        first, last = edge.FirstParameter, edge.LastParameter
        a, m, b = edge.valueAt(first), edge.valueAt((first + last) / 2), edge.valueAt(last)

        def angle(p: Any) -> float:
            return math.degrees(math.atan2(-(p.y - centre.y), p.x - centre.x)) % 360.0

        start, mid, end = angle(a), angle(m), angle(b)
        if (mid - start) % 360.0 > (end - start) % 360.0:  # the middle is not on the counter-clockwise way: swap ends
            start, end = end, start
        return [model.Arc(cx, cy, float(edge.Curve.Radius), start, end, layer)]
    points = edge.discretize(Deflection=DISCRETIZE_MM)
    return [model.Polyline(tuple((x0 + p.x, y0 - p.y) for p in points), layer, closed=bool(edge.isClosed()))]


def view_entities(
    page: Any, hidden: set[str] | frozenset[str] = frozenset(), only: set[str] | None = None
) -> list[model.Entity]:
    """The visible edges of every view of the page, at their place (the contour layer), but the views of ``hidden`` (a
    section stands in their place: ``sheet_turned.hidden_views``); with ``only``, just the standard views it names."""
    out: list[model.Entity] = []
    for view in part_views(page):
        standard = str(view.TypeId) == "TechDraw::DrawProjGroupItem"
        if hidden and standard and view_key(view) in hidden:
            continue
        if only is not None and not (standard and view_key(view) in only):
            continue
        x, y = _page_position(page, view)
        for edge in sheet_native.model_edges(view):
            out += _edge_entities(edge, x, y, model.CONTOUR)
    return out


SYMMETRY_TOL = 1e-5  # the mirror image shares this fraction of the volume with the part: it is the same part
SYMMETRY_MAX_FACES = 400  # a part with more faces is not tested (a boolean on it is slow): it says no symmetry
SYMMETRY_CACHE = 16
SPAN_SAMPLES = 80  # places along a cylinder at which its surface is looked for
SPAN_TOL_MM = 0.02
_SYMMETRY: dict[tuple[Any, ...], dict[str, float]] = {}


def _symmetry_of(shape: Any) -> dict[str, float]:
    """The model axes whose middle plane of the part's box is a plane of symmetry of the shape: the shape mirrored in it
    shares its whole volume with the shape. Measured, never assumed; empty when the part is too complex to test or the
    boolean fails."""
    volume = float(shape.Volume)
    if volume <= 0 or len(shape.Faces) > SYMMETRY_MAX_FACES:
        return {}
    box = shape.BoundBox
    found: dict[str, float] = {}
    for name, low, high, normal in (
        ("x", box.XMin, box.XMax, App.Vector(1, 0, 0)),
        ("y", box.YMin, box.YMax, App.Vector(0, 1, 0)),
        ("z", box.ZMin, box.ZMax, App.Vector(0, 0, 1)),
    ):
        plane = (low + high) / 2
        base = App.Vector(plane * normal.x, plane * normal.y, plane * normal.z)
        try:
            common = shape.common(shape.mirror(base, normal))
            same = abs(float(common.Volume) - volume) <= SYMMETRY_TOL * volume
        except Exception:  # noqa: BLE001 - OCC raises plain exceptions of every kind
            continue
        if same:
            found[name] = plane
    return found


def _exact_box(shape: Any) -> Any:
    """The bounding box of ``shape`` from its exact geometry (not from a triangulation, which cuts the curves short)."""
    try:
        return shape.optimalBoundingBox(False, False)
    except Exception:  # noqa: BLE001 - an older FreeCAD, or a shape it cannot box: the plain box is what there is
        return shape.BoundBox


MOULD_CACHE = 400
_MOULD: dict[tuple[Any, ...], Any] = {}  # what was measured for a moulded part (its box, its sections), by document and shapes


class LiveGeometry(drawing_assembly.AssemblyRays, drawing_assembly.WeldJoints, sc.Geometry):
    """The lookups items make into the model, on the live document."""

    def __init__(self, doc: reg.DocumentRec, shapes: list[Any], op: str | None) -> None:
        self.doc = doc
        self.op = op
        self._shapes = shapes
        self._boundary: Any = None
        self._projections: dict[Any, sc.Projection] = {}

    # faces and edges by id
    def _face(self, face_id: str) -> tuple[Any, Any]:
        try:
            rec, comp = reg.resolve_face(self.doc, face_id, self.op)
        except WorkerError as exc:
            raise sc.ItemError(f"{face_id} is not a face of the part (inspect(action=faces) lists them)") from exc
        if comp is not None:
            return reg.placed_component_shape(self.doc, comp).Faces[rec.index], reg.placed_component_shape(
                self.doc, comp
            )
        solid = reg.world_shape(self.doc, reg.resolve_body(self.doc, rec.body, self.op))
        return solid.Faces[rec.index], solid

    def face(self, face_id: str) -> sc.FaceInfo:
        face, _ = self._face(face_id)
        kind = g.surface_kind(face)
        point = g.vec(face.CenterOfMass)
        if kind == "plane":
            return sc.FaceInfo(face_id, kind, point, normal=prov.outward_normal(face))
        if kind == "cylinder":
            surf = face.Surface
            u, v = prov.mid_params(face)
            return sc.FaceInfo(
                face_id,
                kind,
                point,
                radius=float(surf.Radius),
                axis_point=g.vec(surf.Center),
                axis_dir=g.unit(g.vec(surf.Axis)),
                surface_point=g.vec(face.valueAt(u, v)),
            )
        return sc.FaceInfo(face_id, kind, point)

    def edge(self, edge_id: str) -> sc.EdgeInfo:
        try:
            rec, comp = reg.resolve_edge(self.doc, edge_id, self.op)
        except WorkerError as exc:
            raise sc.ItemError(f"{edge_id} is not an edge of the part (inspect(action=edges) lists them)") from exc
        solid = (
            reg.placed_component_shape(self.doc, comp)
            if comp is not None
            else reg.world_shape(self.doc, reg.resolve_body(self.doc, rec.body, self.op))
        )
        edge = solid.Edges[rec.index]
        kind = g.curve_kind(edge)
        if kind == "line":
            a, b = prov.edge_ends(edge)
            return sc.EdgeInfo(edge_id, "line", start=a, end=b)
        if kind == "circle":
            curve = edge.Curve
            return sc.EdgeInfo(
                edge_id,
                "circle",
                centre=g.vec(curve.Center),
                radius=float(curve.Radius),
                axis_dir=g.unit(g.vec(curve.Axis)),
            )
        return sc.EdgeInfo(edge_id, kind)

    def on_part(self, p: sc.Vec3) -> sc.Vec3 | None:
        if self._boundary is None:
            self._boundary = Part.Compound([f for shape in self._shapes for f in shape.Faces])
        if self._boundary.isNull():
            return None
        distance, solutions, _ = self._boundary.distToShape(Part.Vertex(g.fcvec(p)))
        if distance > sc.POSITION_TOL_MM:
            return None
        return g.vec(solutions[0][0])

    # --- a moulded part (ISO 10135): the pull direction and the parting plane are proposed from these ---
    def _key(self, *parts: Any) -> tuple[Any, ...]:
        """The key of something measured on THESE shapes of this document: the sheet is drawn again for every item added (and
        for each nudge of a collision), and a section of a part with hundreds of faces takes seconds."""
        return (self.doc.id, tuple(shape.hashCode() for shape in self._shapes), *parts)

    def mould_box(self) -> tuple[sc.Vec3, sc.Vec3, sc.Vec3] | None:
        key = self._key("mould_box")
        if key not in _MOULD:
            _MOULD[key] = self._mould_box()
            if len(_MOULD) > MOULD_CACHE:
                _MOULD.pop(next(iter(_MOULD)))
        return _MOULD[key]  # type: ignore[no-any-return]

    def _mould_box(self) -> tuple[sc.Vec3, sc.Vec3, sc.Vec3] | None:
        boxes = [_exact_box(shape) for shape in self._shapes]
        if not boxes:
            return None
        low = (min(b.XMin for b in boxes), min(b.YMin for b in boxes), min(b.ZMin for b in boxes))
        high = (max(b.XMax for b in boxes), max(b.YMax for b in boxes), max(b.ZMax for b in boxes))
        solids = [solid for shape in self._shapes for solid in (shape.Solids or [shape])]  # a compound has no centre of mass of its own
        volume = sum(float(solid.Volume) for solid in solids)
        if volume <= 0:
            return None
        centre = tuple(sum(float(solid.Volume) * g.vec(solid.CenterOfMass)[i] for solid in solids) / volume for i in range(3))
        return low, high, centre  # type: ignore[return-value]

    def _section(self, axis: sc.Vec3, distance: float) -> tuple[float, Any] | None:
        """``(area, wire)`` of the largest closed wire the plane square to ``axis`` at ``distance`` cuts from the part."""
        key = self._key("section", axis, round(distance, 3))
        if key in _MOULD:
            return _MOULD[key]  # type: ignore[no-any-return]
        found = self._slice(axis, distance)
        _MOULD[key] = found
        if len(_MOULD) > MOULD_CACHE:
            _MOULD.pop(next(iter(_MOULD)))
        return found

    def _slice(self, axis: sc.Vec3, distance: float) -> tuple[float, Any] | None:
        best: tuple[float, Any] | None = None
        direction = App.Vector(*axis)
        for shape in self._shapes:
            try:
                wires = shape.slice(direction, distance)
            except Exception:  # noqa: BLE001 - a plane that grazes a face: that section is not there
                continue
            for wire in wires:
                try:
                    area = float(Part.Face(wire).Area) if wire.isClosed() else 0.0
                except Exception:  # noqa: BLE001, S112
                    continue
                if best is None or area > best[0]:
                    best = (area, wire)
        return best

    def outline_areas(self, axis: sc.Vec3, around: tuple[float, float] | None = None) -> list[tuple[float, float]]:
        key = self._key("areas", tuple(axis), around)
        if key not in _MOULD:
            _MOULD[key] = self._outline_areas(axis, around)
        return list(_MOULD[key])

    def _outline_areas(self, axis: sc.Vec3, around: tuple[float, float] | None = None) -> list[tuple[float, float]]:
        box = self.mould_box()
        if box is None:
            return []
        unit = tuple(abs(c) for c in axis)
        index = max(range(3), key=lambda i: unit[i])
        lo, hi = around if around is not None else (box[0][index], box[1][index])
        count = 32 if around is None else 24
        step = (hi - lo) / count
        out: list[tuple[float, float]] = []
        with _stage("sampling the sections of the part for the parting plane", self.op):
            for i in range(count):
                d = lo + (i + 0.5) * step
                found = self._section(unit, d)  # type: ignore[arg-type]
                if found is not None:
                    out.append((d, found[0]))
            if around is None and out:  # then finer round the widest one, where a shoulder lies between two samples
                best = max(out, key=lambda s: s[1])[0]
                out += self.outline_areas(axis, (best - step, best + step))
        return sorted(out)

    def axial_planes(self, axis: sc.Vec3) -> list[float]:
        unit = tuple(abs(c) for c in axis)
        index = max(range(3), key=lambda i: unit[i])
        found: set[float] = set()
        for shape in self._shapes:
            for face in shape.Faces:
                if g.surface_kind(face) != "plane" or abs(abs(face.normalAt(0, 0).dot(App.Vector(*unit))) - 1.0) > 1e-6:
                    continue
                found.add(round(float(g.vec(face.CenterOfMass)[index]), 2))
        return sorted(found)

    def section_outline(self, axis: sc.Vec3, level: float, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[sc.Point]:
        unit = tuple(abs(c) for c in axis)
        best: tuple[float, Any] | None = None
        for nudge in (-0.02, 0.02):  # a plane on a face of the part has no section: a hair either side of it has
            found = self._section(unit, level + nudge)  # type: ignore[arg-type]
            if found is not None and (best is None or found[0] > best[0]):
                best = found
        if best is None:
            return []
        ydir = sc.cross(direction, xdir)
        points = best[1].discretize(Deflection=0.05)
        loop = [(sc.dot(sc.sub((p.x, p.y, p.z), centre), xdir), sc.dot(sc.sub((p.x, p.y, p.z), centre), ydir)) for p in points]
        if len(loop) > 2 and math.dist(loop[0], loop[-1]) < 1e-6:
            loop.pop()
        return loop

    def circle_span(self, a: sc.Vec3, b: sc.Vec3) -> float | None:
        chord = g.length(g.sub(b, a))
        if chord < 1e-6:
            return None
        along = g.unit(g.sub(b, a))
        middle = tuple((x + y) / 2 for x, y in zip(a, b, strict=True))
        tol = sc.POSITION_TOL_MM

        def on_axis(origin: sc.Vec3, axis: sc.Vec3) -> bool:
            if abs(g.dot(along, axis)) > 1e-3:  # the chord is across the axis
                return False
            off = g.sub(middle, origin)
            return g.length(g.sub(off, g.scale(axis, g.dot(off, axis)))) <= tol  # ... and its middle is on it

        for shape in self._shapes:
            walls = []  # a bore, a boss or a cone with draft: the axis crosses the chord at its middle (a wall comes in halves)
            for face in shape.Faces:
                kind = g.surface_kind(face)
                if kind not in ("cylinder", "cone"):
                    continue
                surf = face.Surface
                origin = g.vec(surf.Center if kind == "cylinder" else surf.Apex)
                if on_axis(origin, g.unit(g.vec(surf.Axis))):
                    walls.append(face)
            if all(any(w.distToShape(Part.Vertex(g.fcvec(p)))[0] <= tol for w in walls) for p in (a, b)):
                return chord
            for edge in shape.Edges:  # a circular edge (the mouth of a bore): both points on it
                if g.curve_kind(edge) != "circle":
                    continue
                curve = edge.Curve
                if not on_axis(g.vec(curve.Center), g.unit(g.vec(curve.Axis))):
                    continue
                if all(edge.distToShape(Part.Vertex(g.fcvec(p)))[0] <= tol for p in (a, b)):
                    return chord
        return None

    def projection(
        self, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3, cut: tuple[sc.Vec3, sc.Vec3] | None = None
    ) -> sc.Projection:
        key = (centre, xdir, direction, cut)
        if key not in self._projections:
            with _stage("projecting the part for a derived view", self.op):
                self._projections[key] = sheet_project.project(self._shapes, centre, xdir, direction, cut)
        return self._projections[key]

    def view_projection(self, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> sc.Projection:
        poses = tuple(shape.hashCode() for shape in self._shapes)  # the same shapes seen the same way: one projection
        key = ("view", self.doc.id, poses, centre, xdir, direction)
        with _stage("projecting the part for a standard view", self.op):
            return sheet_project.project_view(key, self._shapes, centre, xdir, direction)

    def face_outline(self, face_id: str, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[list[sc.Point]]:
        face, _ = self._face(face_id)
        return sheet_project.face_loops(face, centre, xdir, direction)

    def faces_at(self, p: sc.Vec3, face_ids: list[str]) -> list[str]:
        found = []
        for face_id in face_ids:
            try:
                face, _ = self._face(face_id)
            except sc.ItemError:
                continue
            if face.distToShape(Part.Vertex(g.fcvec(p)))[0] <= sc.POSITION_TOL_MM:
                found.append(face_id)
        return found

    def round_extent(self) -> tuple[sc.Vec3, float] | None:
        # ``BoundBox`` of a curved solid is the box of its triangulation when one exists: a Ø12 shaft measures 11.97 x 11.99
        # and would never match its own diameter, so the exact (analytic) box is asked for
        boxes = [_exact_box(shape) for shape in self._shapes]
        if not boxes:
            return None
        low = (min(b.XMin for b in boxes), min(b.YMin for b in boxes), min(b.ZMin for b in boxes))
        high = (max(b.XMax for b in boxes), max(b.YMax for b in boxes), max(b.ZMax for b in boxes))
        size = tuple(h - lo for h, lo in zip(high, low, strict=True))
        for shape in self._shapes:
            for face in shape.Faces:
                if g.surface_kind(face) != "cylinder":
                    continue
                surf = face.Surface
                axis = g.unit(g.vec(surf.Axis))
                index = next((i for i in range(3) if abs(abs(axis[i]) - 1.0) < 1e-6), None)
                if index is None:
                    continue
                diameter = 2.0 * float(surf.Radius)
                across = [size[i] for i in range(3) if i != index]
                if any(abs(extent - diameter) > 0.01 for extent in across):
                    continue
                u, v = prov.mid_params(face)
                radial = g.sub(g.vec(face.valueAt(u, v)), g.vec(surf.Center))
                if g.dot(g.vec(face.normalAt(u, v)), radial) > 0:  # an outer cylinder, not a bore of a square block
                    return axis, diameter
        return None

    def axial_features(self) -> tuple[bool, bool] | None:
        found = self.round_extent()
        if found is None or len(self._shapes) != 1:
            return None
        axis = found[0]
        box = _exact_box(self._shapes[0])
        through = ((box.XMin + box.XMax) / 2, (box.YMin + box.YMax) / 2, (box.ZMin + box.ZMax) / 2)
        facts: list[sheet_turned.FaceFact] = []
        for face in self._shapes[0].Faces:
            kind = g.surface_kind(face)
            surf = face.Surface
            if kind == "plane":
                facts.append(sheet_turned.FaceFact("plane", normal=g.unit(g.vec(surf.Axis))))
                continue
            if kind not in ("cylinder", "cone", "torus", "sphere"):
                facts.append(sheet_turned.FaceFact("other"))
                continue
            point = g.vec(surf.Apex if kind == "cone" else surf.Center)
            u, v = prov.mid_params(face)
            radial = g.sub(g.vec(face.valueAt(u, v)), through)
            radial = g.sub(radial, g.scale(axis, g.dot(radial, axis)))  # from the axis line out to the surface
            concave = g.dot(g.vec(face.normalAt(u, v)), radial) < -1e-9
            facts.append(
                sheet_turned.FaceFact(
                    kind,
                    axis=None if kind == "sphere" else g.unit(g.vec(surf.Axis)),
                    point=point,
                    concave=concave,
                )
            )
        return sheet_turned.coaxial_facts(facts, axis, through)

    def symmetry(self) -> dict[str, float]:
        if len(self._shapes) != 1:
            return {}
        shape = self._shapes[0]
        key = (self.doc.id, shape.hashCode())
        if key not in _SYMMETRY:
            if len(_SYMMETRY) >= SYMMETRY_CACHE:
                _SYMMETRY.clear()
            with _stage("testing the part for planes of symmetry", self.op):
                _SYMMETRY[key] = _symmetry_of(shape)
        return dict(_SYMMETRY[key])

    def cylinder_span(self, face_id: str, across: sc.Vec3) -> tuple[float, float] | None:
        face, _ = self._face(face_id)
        if g.surface_kind(face) != "cylinder":
            return None
        surf = face.Surface
        axis, origin, radius = g.unit(g.vec(surf.Axis)), g.vec(surf.Center), float(surf.Radius)
        side = g.sub(across, g.scale(axis, g.dot(across, axis)))
        if g.length(side) < 1e-6:
            return None
        side = g.unit(side)
        box = face.BoundBox
        reach = [
            g.dot(g.sub((x, y, z), origin), axis)
            for x in (box.XMin, box.XMax)
            for y in (box.YMin, box.YMax)
            for z in (box.ZMin, box.ZMax)
        ]
        low, high = min(reach), max(reach)
        step = (high - low) / SPAN_SAMPLES
        runs: list[tuple[float, float]] = []
        start: float | None = None
        for n in range(SPAN_SAMPLES + 1):
            s = low + n * step
            point = tuple(o + a * s + d * radius for o, a, d in zip(origin, axis, side, strict=True))
            here = face.distToShape(Part.Vertex(g.fcvec(point)))[0] <= SPAN_TOL_MM
            if here and start is None:
                start = s
            if not here and start is not None:
                runs.append((start, s - step))
                start = None
        if start is not None:
            runs.append((start, high))
        runs = [r for r in runs if r[1] > r[0]]
        return max(runs, key=lambda r: r[1] - r[0]) if runs else None

    def cone_chamfer(self, face_id: str) -> sc.ConeChamfer | None:
        face, _ = self._face(face_id)
        if g.surface_kind(face) != "cone":
            return None
        circles = [e for e in face.Edges if g.curve_kind(e) == "circle"]
        if len(circles) != 2:
            return None
        axis = g.unit(g.vec(face.Surface.Axis))
        ends = sorted(((g.vec(e.Curve.Center), float(e.Curve.Radius)) for e in circles), key=lambda c: c[1])
        (c_small, r_small), (c_big, r_big) = ends
        axial = abs(g.dot(g.sub(c_big, c_small), axis))
        radial = r_big - r_small
        leg_a, leg_b = max(axial, radial), min(axial, radial)
        if leg_a < 1e-6:
            return None
        return sc.ConeChamfer(
            face_id, leg_a, leg_b, math.degrees(math.atan2(leg_b, leg_a)), axis, (c_small, r_small), (c_big, r_big)
        )

    def chamfer(self, face_id: str) -> sc.ChamferInfo:
        face, solid = self._face(face_id)
        if g.surface_kind(face) != "plane":
            raise sc.ItemError(f"{face_id} is not flat: only a flat chamfer can be dimensioned (use a note otherwise)")
        straight = []
        for edge in face.Edges:
            if g.curve_kind(edge) != "line":
                continue
            a, b = prov.edge_ends(edge)
            neighbours = [f for f in solid.ancestorsOfType(edge, Part.Face) if not f.isSame(face)]
            if len(neighbours) == 1 and g.surface_kind(neighbours[0]) == "plane":
                straight.append((edge, g.unit(g.sub(b, a)), neighbours[0]))
        if not straight:
            raise sc.ItemError(f"{face_id} has no straight edge shared with a flat face: it is not a flat chamfer")
        longest = max(straight, key=lambda s: s[0].Length)
        pair = [s for s in straight if sc.parallel(s[1], longest[1])]
        if len(pair) != 2:
            raise sc.ItemError(
                f"{face_id} is not a chamfer between two flat faces (it has {len(pair)} edges along its length)"
            )
        (edge_a, direction, face_a), (edge_b, _, face_b) = pair
        n_a, n_b = prov.outward_normal(face_a), prov.outward_normal(face_b)
        d_a = g.dot(n_a, g.vec(face_a.CenterOfMass))
        d_b = g.dot(n_b, g.vec(face_b.CenterOfMass))
        u = g.cross(n_a, n_b)
        uu = g.dot(u, u)
        if uu < 1e-9:
            raise sc.ItemError(f"the faces next to {face_id} are parallel: it is not a chamfer")
        corner = g.scale(g.add(g.scale(g.cross(n_b, u), d_a), g.scale(g.cross(u, n_a), d_b)), 1.0 / uu)
        legs = []
        for edge in (edge_a, edge_b):
            a, b = prov.edge_ends(edge)
            mid = g.scale(g.add(a, b), 0.5)
            legs.append(g.length(g.cross(g.sub(mid, corner), g.unit(u))))
        leg_a, leg_b = legs
        gamma = math.pi - math.acos(max(-1.0, min(1.0, g.dot(n_a, n_b))))
        if leg_b > leg_a:  # the longer leg is the one the angle is measured from
            leg_a, leg_b = leg_b, leg_a
        angle = math.degrees(math.atan2(leg_b * math.sin(gamma), leg_a - leg_b * math.cos(gamma)))
        return sc.ChamferInfo(
            face_id, leg_a, leg_b, angle, direction, g.vec(face.CenterOfMass), prov.outward_normal(face)
        )


def hole_infos(doc: reg.DocumentRec, body: str | None, op: str | None) -> list[sc.HoleInfo]:
    """The holes of the part with what a callout needs; the thread comes from the ``hole`` feature that made the hole
    (found through the provenance of its faces), a hole made any other way is geometry only (``declared`` False)."""
    params: dict[str, Any] = {"operation_id": op or "drawing"}
    if body is not None:
        params["body"] = {"id": body}
    # a partial cylinder (the round end of a slot, a half hole) is not a hole to call out: only full circles are
    found = [h for h in holes.list_holes(doc, params)["holes"] if float(h["arc_deg"]) >= FULL_CIRCLE_DEG]
    features = {f.id: f for f in doc.features}
    out: list[sc.HoleInfo] = []
    for index, hole in enumerate(found):
        face_ids = tuple(str(f["id"]) for f in hole["faces"])
        declared, thread, made_by, fit, made_class, grade = False, None, None, None, None, None
        for face_id in face_ids:
            try:
                rec, comp = reg.resolve_face(doc, face_id, op)
            except WorkerError:
                continue
            feature = features.get(rec.created_by) if comp is None else None
            if feature is not None and made_by is None:
                made_by = feature.id
            given = (
                hole_colours.declarations(doc, feature) if feature is not None else None
            )  # a pattern copy: its seed's
            if given is not None:
                declared = True
                fit, made_class, grade, spec = given
                if isinstance(spec, dict) and spec.get("size"):
                    depth = spec.get("depth_mm")
                    thread = (str(spec["size"]), float(depth) if depth is not None else None)
                break
        axis = hole["axis"]
        out.append(
            sc.HoleInfo(
                index,
                tuple(float(c) for c in axis["origin"]),  # type: ignore[arg-type]
                tuple(float(c) for c in axis["direction"]),  # type: ignore[arg-type]
                float(hole["diameter_mm"]),
                float(hole["depth_mm"]),
                hole["through"],
                face_ids,
                thread,
                declared,
                made_by,
                fit,
                made_class,
                grade,
                float(hole.get("tip_mm") or 0.0),
            )
        )
    return out


def compose_sheet(
    doc: reg.DocumentRec, page: Any, spec: dict[str, Any], index: int, count: int, op: str | None
) -> tuple[model.Sheet, list[sc.ItemResult]]:
    """The sheet as it is now (title block, views, items) as a ``sheet_model.Sheet``, and what each item did."""
    sheet, results, _ = compose(doc, page, spec, index, count, op)
    return sheet, results


MAX_PLACEMENT_PASSES = 6  # the sheet is composed again at most this many times to clear its collisions
MAX_NUDGES = 5  # and an item is moved away from a collision at most this many times
PATIENCE_PASSES = 3  # passes in a row that clear no collision before the best one is kept


def compose(
    doc: reg.DocumentRec, page: Any, spec: dict[str, Any], index: int, count: int, op: str | None
) -> tuple[model.Sheet, list[sc.ItemResult], sc.Context]:
    """``compose_sheet`` and the context the items were drawn in (the review reads what they left in it).

    The placement pass: the sheet is composed, every object's ink is checked against every other's
    (``sheet_collide``), and each item that lies on an earlier one is moved farther away (``Context.nudge``) before the
    sheet is composed again, until nothing collides or an item cannot be moved more. What still collides is in
    ``ctx.extras["collisions"]`` (the review names the items) and in the result of the item."""
    cache: dict[str, Any] = {}
    nudges: dict[str, int] = {}
    best: tuple[int, tuple[model.Sheet, list[sc.ItemResult], sc.Context], dict[str, int]] | None = None
    stale = 0
    for _ in range(MAX_PLACEMENT_PASSES):
        drawn = _compose_pass(doc, page, spec, index, count, op, dict(nudges), cache)
        found = drawn[2].extras.get("collisions", [])
        score = len(found) + sum(1 for c in found if c.kind == "outside_frame")  # off the sheet counts twice
        if best is None or score < best[0]:
            best, stale = (score, drawn, dict(nudges)), 0
        else:
            stale += 1
        movers = _movers(drawn[2], nudges)
        if score == 0 or not movers or stale >= PATIENCE_PASSES:
            break
        for item in movers:
            nudges[item] = nudges.get(item, 0) + 1
    assert best is not None
    sheet, results, ctx = best[1]
    ctx.extras["nudges"] = best[2]
    _report_collisions(ctx, results)
    _sync_hidden_views(doc, page, spec, set(ctx.extras.get("in_place_views", {}).values()))
    return sheet, results, ctx


def _sync_hidden_views(doc: reg.DocumentRec, page: Any, spec: dict[str, Any], standing: set[str]) -> None:
    """The page window shows the views the sheet draws: the view a section stands in place of (``in_place``) is hidden in it,
    and shown again when the section goes or could not be drawn (``standing`` are the views a drawn section stands for)."""
    hidden = sheet_turned.hidden_views(spec.get("items", [])) & standing if projection_of(spec) == "techdraw" else set()
    with gui.unmodified(doc.fc_doc):
        for view in part_views(page):
            if str(view.TypeId) != "TechDraw::DrawProjGroupItem":
                continue
            shown = view_key(view) not in hidden
            if bool(view.Visibility) != shown:
                view.Visibility = shown


def _item_ranks(ctx: sc.Context) -> dict[str, tuple[int, int]]:
    """(phase, position) of every item of the sheet: the later one of two that collide is the one that gives way."""
    return {
        str(i["id"]): (sheet_items.REGISTRY[str(i["type"])].phase if str(i["type"]) in sheet_items.REGISTRY else 0, n)
        for n, i in enumerate(ctx.extras.get("items", []))
    }


def _movers(ctx: sc.Context, nudges: dict[str, int]) -> set[str]:
    """The items to move away: of each pair that collides, the later-drawn one, unless it has been moved enough."""
    ranks = _item_ranks(ctx)
    movers: set[str] = set()
    for c in ctx.extras.get("collisions", []):
        able = [k for k in (c.a, c.b) if k in ranks and nudges.get(k, 0) < MAX_NUDGES]
        if able:
            movers.add(max(able, key=lambda k: ranks[k]))
    return movers


def _report_collisions(ctx: sc.Context, results: list[sc.ItemResult]) -> None:
    """Say, in the result of the items that still collide, what they lie on."""
    said: dict[str, list[str]] = {}
    for c in ctx.extras.get("collisions", []):
        said.setdefault(c.a, []).append(f"{c.what} ({c.b})")
        said.setdefault(c.b, []).append(f"{c.what} ({c.a})")
    for position, r in enumerate(results):
        if r.id in said and r.status != "error":
            more = ctx.say("se solapa con", "overlaps") + " " + "; ".join(said[r.id][:2])
            results[position] = sc.ItemResult(r.id, r.type, "warning", f"{r.detail}; {more}" if r.detail else more)


def _compose_pass(
    doc: reg.DocumentRec,
    page: Any,
    spec: dict[str, Any],
    index: int,
    count: int,
    op: str | None,
    nudges: dict[str, int],
    cache: dict[str, Any],
) -> tuple[model.Sheet, list[sc.ItemResult], sc.Context]:
    """One drawing of the whole sheet with the items nudged as ``nudges`` says (what does not change between passes -- the
    part, its holes, the views -- is kept in ``cache``)."""
    key = spec["template"]
    size, language = split_key(key)
    text = template_text(key, op)
    names = sheet_template.editable_fields(text)
    page_w, page_h = sheet_layout.PAGES_MM[size]
    scale = _scale_of(page) or 1.0
    left, top, right, bottom = sheet_layout.drawing_area(page_w, page_h)
    fields = spec.get("fields", {})
    sheet = model.Sheet(page_w, page_h, str(fields.get("title") or page.Label))
    hlr = projection_of(spec) == "hlr"
    if "geometry" not in cache:
        cache["geometry"] = LiveGeometry(doc, _shapes(doc, spec.get("body"), op), op)
        cache["holes"] = hole_infos(doc, spec.get("body"), op)
        cache["threads"] = (
            []
            if _is_assembly(doc, spec.get("body"))
            else sheet_thread.external_threads(thread_records.read_threads(doc.fc_doc))
        )
        cache["facts"] = (
            sheet_facts.collect_assembly(doc)  # the whole cylinders of the components: spacers, pins, the bores
            if _is_assembly(doc, spec.get("body"))
            else sheet_facts.collect(doc, _bodies(doc, spec.get("body"), op))
        )
        cache["weldment"] = (
            drawing_assembly.weldment_info(doc, language) if spec.get("body") is None and not _is_assembly(doc, None) else None
        )
        cache["views"] = {} if hlr else view_infos(page)
        cache["view_entities"] = [] if hlr else view_entities(page)
        cache["hidden_views"] = set() if hlr else sheet_turned.hidden_views(spec.get("items", []))
        # the sheet does not draw the view a section stands in place of; the items still see its outline until the section is drawn
        cache["view_entities_shown"] = (
            view_entities(page, cache["hidden_views"]) if cache["hidden_views"] else cache["view_entities"]
        )
    geometry = cache["geometry"]
    ctx = sc.Context(
        sheet=sheet,
        views=cache["views"],
        holes=cache["holes"],
        geometry=geometry,
        area=(left, page_h - bottom, right, page_h - top),
        language=language,
        threads=cache["threads"],
    )
    ctx.extras["nudges"] = nudges
    ctx.extras["facts"] = cache["facts"]  # the rounds and whole cylinders of the part (None: an assembly)
    hlr_views = None
    if hlr:
        try:
            hlr_views = sheet_hlr.compose_views(geometry, spec["hlr"])
        except sc.ItemError as exc:
            raise error("geometry_failed", f"the part could not be projected: {exc}", op) from exc
        sheet_hlr.apply(ctx, hlr_views, _is_assembly(doc, spec.get("body")) or cache["weldment"] is not None)
    else:
        ctx.extras["view_boxes"] = view_rects(page)
    ctx.extras["main_scale"] = scale
    if spec.get("turned"):  # the layout arranged length x diameter: the alternatives the review offers do the same
        ctx.extras["layout_mm"] = (spec["turned"]["length"], spec["turned"]["diameter"], spec["turned"]["diameter"])
    ctx.extras["sheet_views"] = list(views_of(spec))  # what the sheet was made with: the review asks for the isometric
    ctx.extras["angle"] = key_angle(key)
    if "extent" not in cache:
        cache["extent"] = _extents(doc, spec.get("body"), op)
        box = _bounds(doc, spec.get("body"), op)
        cache["model_box"] = (tuple(box["min"]), tuple(box["max"])) if box else None
    ctx.extras["extent_mm"] = cache[
        "extent"
    ]  # width (x), depth (y), height (z) of the part: the thickness note needs it
    declared = bool(spec.get("symmetry"))  # the intent says so; the shape alone never makes a plane a reference
    if declared and "symmetry" not in cache:
        wanted = bool(cache["holes"]) and not _is_assembly(doc, spec.get("body"))
        cache["symmetry"] = geometry.symmetry() if wanted else {}
    ctx.extras["symmetry"] = cache["symmetry"] if declared else {}  # the planes the design says it is symmetric across
    if cache["model_box"] is not None and not _is_assembly(doc, spec.get("body")):
        ctx.extras["model_box"] = cache["model_box"]  # the silhouette a position is counted from (see items_datum)
    ctx.weldment = cache["weldment"]
    if _is_assembly(doc, spec.get("body")) or ctx.weldment is not None:
        ctx.assembly = drawing_assembly.assembly_info(doc, language) if ctx.weldment is None else None
        iso = None if hlr else iso_info(page)
        if iso is not None:
            ctx.extras["views_extra"] = {"isometric": iso}
            ctx.extras["iso_box"] = iso.box
    ctx.extras["general_tolerances"] = str(fields.get("general_tolerances") or "")
    ctx.extras["fields"] = dict(fields)
    items: list[dict[str, Any]] = []
    results: list[sc.ItemResult] = []
    taken: set[str] = set()
    for position, raw in enumerate(spec.get("items", []), start=1):
        try:
            done = sheet_items.normalise(raw, taken, position)
        except sc.ItemError as exc:
            results.append(
                sc.ItemResult(
                    str(raw.get("id", "?")) if isinstance(raw, dict) else "?",
                    str(raw.get("type", "?")) if isinstance(raw, dict) else "?",
                    "error",
                    str(exc),
                )
            )
            continue
        taken.add(done["id"])
        items.append(done)
    ctx.extras["items"] = items
    sheet_items.prepare_all(ctx, items)
    shown_fields = title_fields(spec, names, scale, index, count, ctx.revisions, material_of(doc.fc_doc))
    ctx.extras["fields"]["part_material"] = shown_fields.get(
        "part_material", ""
    )  # the design's, while the sheet has none
    _, _, template_entities = sheet_template.parse(text, shown_fields)
    sheet.extend(template_entities)
    ctx.extras["title_block"] = sheet_template.title_block_rect(template_entities, page_w)
    if spec.get("iso_colour") and "iso" in views_of(spec) and not _is_assembly(doc, spec.get("body")):
        if "iso_shade" not in cache:  # the picture under the lines of the isometric view (see ``sheet_iso``)
            iso_view = ctx.extras.get("views_extra", {}).get("isometric") or (None if hlr else iso_info(page))
            cache["iso_shade"], cache["iso_notes"] = (
                sheet_iso.shade(doc, _bodies(doc, spec.get("body"), op), iso_view, op)
                if iso_view is not None
                else ([], [])
            )
        sheet.underlay = list(cache["iso_shade"])
        ctx.extras["iso_notes"] = [ctx.say(es, en) for es, en in cache["iso_notes"]]  # for the reply
    views_start = len(sheet.entities)
    sheet.extend(hlr_views.entities if hlr_views is not None else cache["view_entities_shown"])
    ctx.extras["view_ink"] = sheet.entities[views_start:]
    if cache.get("hidden_views"):
        ctx.extras["view_ink"] = list(cache["view_entities"])  # the outline of the hidden view, until its section is drawn
        ctx.extras["view_ink_shown"] = list(cache["view_entities_shown"])
    start = len(sheet.entities)
    sheet.extend(sheet_thread.thread_entities(ctx))
    ranges = {"threads": (start, len(sheet.entities))}
    iso_view = hlr_views.iso if hlr_views is not None else (iso_info(page) if "iso" in views_of(spec) else None)
    if iso_view is not None and abs(iso_view.scale - scale) > 1e-9:  # its own scale goes under it (ISO 5455)
        label = ctx.say(f"Isométrica {model.fmt_scale(iso_view.scale)}", f"Isometric {model.fmt_scale(iso_view.scale)}")
        start = len(sheet.entities)
        sheet.add(model.Text((iso_view.box[0] + iso_view.box[2]) / 2, iso_view.box[1] - 6.0, label, anchor="center"))
        ranges[sheet_units.KEY_ISO_SCALE] = (start, len(sheet.entities))
    results += sheet_items.draw_items(ctx, items)
    if cache.get("hidden_views"):
        gone = cache["hidden_views"] - set(ctx.extras.get("in_place_views", {}).values())
        if gone:  # the section could not be drawn (its item says why): the view it should have replaced stays on the sheet
            sheet.extend(view_entities(page, only=gone))
    native = {
        i["id"]: dict(ctx.extras.get("overall_dims", {})) for i in items if i["type"] == "overall" and not hlr
    }  # TechDraw's own dimension objects, put on the lines the sheet draws
    ctx.extras["units"] = sheet_units.collect(
        sheet.entities,
        items,
        ctx.extras.get("item_slices", {}),
        {r.id: r.status for r in results},
        ranges,
        hlr_views.groups if hlr_views is not None else None,
        native,
        None if hlr else ctx.extras.get("natives"),  # an hlr sheet has no TechDraw views to measure in
    )
    if not hlr:  # the lines TechDraw has a cosmetic edge for are asked of the view they lie on
        shown = {n: v.box for n, v in ctx.views.items() if not v.derived and n in sheet_native.VIEW_LABELS}
        if iso_view is not None:
            shown["isometric"] = iso_view.box
        ctx.extras["units"] = sheet_units.with_cosmetics(
            ctx.extras["units"], shown, [v.box for v in ctx.views.values() if v.derived]
        )
    ctx.extras["collisions"] = _collisions(ctx)
    return sheet, results, ctx


def _collisions(ctx: sc.Context) -> list[sheet_collide.Collision]:
    """Where the ink of one item lies on the ink of another (items in the order of their drawing, the later one is ``a``)
    or a text lies on the outline of a view."""
    order = {str(i["id"]): n for n, i in enumerate(ctx.extras.get("items", []))}
    units = sorted(
        (u for u in ctx.extras["units"] if u.kind != sheet_units.KIND_VIEW),
        key=lambda u: order.get(u.key, -1),
    )
    inks = [sheet_collide.ink_of(u.key, u.entities) for u in units]
    views = [sheet_collide.ink_of("views", ctx.extras.get("view_ink", []))]
    found = sheet_collide.collisions(inks, views)
    left, low, right, top = ctx.area
    frame = (left - sc.AREA_SLACK_MM, low, right + sc.AREA_SLACK_MM, top + sc.AREA_SLACK_MM)
    for u, ink in zip(units, inks, strict=True):  # an item off the drawing area (past the frame, over the title block)
        if u.item_type not in ("parts_list",) and u.key in order and (ink.texts or ink.lines):
            x0, y0, x1, y1 = sheet_collide.extent(ink)  # an item of centre lines only has no ink to be off the sheet
            if x0 < frame[0] or y0 < frame[1] or x1 > frame[2] or y1 > frame[3]:
                found.append(
                    sheet_collide.Collision(
                        u.key, "sheet", "outside_frame", ((x0 + x1) / 2, (y0 + y1) / 2), "outside the drawing area"
                    )
                )
    return found


def _all_sheets(doc: reg.DocumentRec, wanted: str | None, op: str | None) -> list[tuple[Any, dict[str, Any], int, int]]:
    """The sheets in scope as (page, spec, number, total); every one is waited for first (the model may have changed
    since the sheet was built)."""
    activate(doc.fc_doc)
    found = pages(doc.fc_doc)
    if not found:
        raise invalid_request("the document has no drawing sheet: create it first with drawing(action=create)", op)
    chosen = [_need_page(doc.fc_doc, wanted, op)] if wanted else found
    for page in chosen:
        wait_views(doc.fc_doc, page, op)
    return [(page, spec_of(page), found.index(page) + 1, len(found)) for page in chosen]


# --- results ----------------------------------------------------------------------------------------------------------


def _same_spec(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two specs draw the same sheet (a tuple and a list that hold the same numbers are the same; the count of
    changes and the approval, which the sheet does not draw, are not compared)."""
    mute = {"edits", "approval"}
    left = {k: v for k, v in a.items() if k not in mute}
    right = {k: v for k, v in b.items() if k not in mute}
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(right, sort_keys=True, default=str)


def _item_rows(results: list[sc.ItemResult], refused: dict[str, tuple[str, str]] | None = None) -> list[dict[str, str]]:
    rows = [{"id": r.id, "type": r.type, "status": r.status, "detail": r.detail} for r in results]
    for item_id, (kind, why) in (refused or {}).items():
        rows.append({"id": item_id, "type": kind, "status": "refused", "detail": f"not stored: {why}"})
    return rows


def _result(
    doc: reg.DocumentRec,
    page: Any,
    scale: float,
    waited: float,
    warnings: list[str],
    op: str | None,
    refused: dict[str, tuple[str, str]] | None = None,
    changed: tuple[str, ...] = (),
    tried: tuple[dict[str, Any], tuple[model.Sheet, list[sc.ItemResult], sc.Context]] | None = None,
) -> dict[str, Any]:
    spec = spec_of(page)
    size, language = split_key(spec["template"])
    found = pages(doc.fc_doc)
    if tried is not None and len(found) == 1 and _same_spec(tried[0], spec):
        sheet, results, ctx = tried[1]  # drawn a moment ago from this very spec (one page: the same numbering, the views untouched)
    else:
        sheet, results, ctx = compose(doc, page, spec, found.index(page) + 1, len(found), op)
    window, failed = mirror(doc, page, ctx, sheet)
    warnings = [*warnings, *ctx.extras.get("iso_notes", []), *failed]
    width, depth, height = _extents(doc, spec.get("body"), op)
    rows = _item_rows(results, refused)
    return {
        "changed": [*changed, *(item_id for item_id in (refused or {}) if item_id not in changed)],
        "window": window,
        "sheet": spec["sheet"],
        "template": size,
        "language": language,
        "page_mm": list(sheet_layout.PAGES_MM[size]),
        "scale": scale,
        "views": ["isometric" if v == "iso" else v for v in views_of(spec)],
        "dimensions": [
            f"width {model.fmt(width)} mm",
            f"height {model.fmt(height)} mm",
            f"depth {model.fmt(depth)} mm",
        ],
        "sheets": [spec_of(p)["sheet"] for p in found],
        "items": rows,
        "waited_s": round(waited, 3),
        "warnings": warnings,
        "projection": projection_of(spec),
        "angle": key_angle(spec["template"]),
    }


def mirror(doc: reg.DocumentRec, page: Any, ctx: sc.Context, sheet: model.Sheet) -> tuple[str, list[str]]:
    """Make the page show what was just composed (``sheet_mirror``); (what it did, warnings). The PDF and the DXF do not
    depend on it, so a failure here is a warning, never an error."""
    units = ctx.extras["units"]
    try:
        report = sheet_mirror.sync(doc.fc_doc, page, units, sheet.height, spec_of(page)["sheet"])
        wait_views(doc.fc_doc, page, None)  # the symbols are computed, nothing is left touched
    except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
        return "", [
            f"the sheet window could not be updated ({type(exc).__name__}: {exc}); the PDF and the DXF are right"
        ]
    notes = [
        f"{key}: TechDraw's own objects could not be made ({'; '.join(why)}): it is drawn as a symbol of our drawing"
        for key, why in report.fallbacks.items()
    ]
    return report.line(), notes


def page_objects(page: Any) -> list[list[Any]]:
    """The mirror objects of ``page`` as [name, type, label, unit key, x, y] rows (a test reads what the window holds)."""
    return [
        [o.Name, str(o.TypeId).split("::")[-1], str(o.Label), key, round(float(o.X), 3), round(float(o.Y), 3)]
        for key, objects in sheet_mirror.owned(page).items()
        for o in objects
    ]


def centre_mark_problems(doc: reg.DocumentRec, sheet: str | None, op: str | None) -> list[str]:
    """Every centre-mark cross of the sheet against the projected centre of its hole, in the sheet model AND in the
    TechDraw objects (where the window puts them), to 0.01 mm: a cross with no hole centre under it, a hole seen as a
    circle whose cross the page lacks while the sheet has it, are all reported. Empty: the marks are at the centres."""
    page = _need_page(doc.fc_doc, sheet, op)
    found = pages(doc.fc_doc)
    composed, _, ctx = compose(doc, page, spec_of(page), found.index(page) + 1, len(found), op)
    tol = sheet_mirror.CROSS_TOL_MM
    in_model = sheet_mirror.crosses(
        [(e.x1, e.y1, e.x2, e.y2) for e in composed.entities if isinstance(e, model.Line) and e.layer == model.AXES]
    )
    on_page = sheet_mirror.page_crosses(page, composed.height)
    expected = [
        (view.name, hole, view.project(hole.centre))
        for hole in ctx.holes
        for view in ctx.views.values()
        if view.looks_along(hole.direction)
    ]
    problems: list[str] = []

    def near(point: tuple[float, float], points: list[tuple[float, float]]) -> bool:
        return any(abs(point[0] - p[0]) <= tol and abs(point[1] - p[1]) <= tol for p in points)

    for name, hole, centre in expected:
        sheet_has, page_has = near(centre, in_model), near(centre, on_page)
        if sheet_has and not page_has:
            problems.append(
                f"hole D{hole.diameter:g} in the {name} view: the sheet has its centre mark, the page does not"
            )
    centres = [c for _, _, c in expected]
    for label, points in (("sheet", in_model), ("page", on_page)):
        for point in points:
            if not near(point, centres):
                problems.append(
                    f"a centre mark of the {label} at ({point[0]:.3f}, {point[1]:.3f}) is not at the centre of any hole"
                )
    return problems


def centre_mark_pixels(doc: reg.DocumentRec, sheet: str | None, op: str | None) -> list[list[Any]]:
    """Test-only (visible worker): for every hole seen as a circle, [view, diameter, offset in mm of the centre mark the
    window paints from the projected centre]."""
    page = _need_page(doc.fc_doc, sheet, op)
    found = pages(doc.fc_doc)
    composed, _, ctx = compose(doc, page, spec_of(page), found.index(page) + 1, len(found), op)
    expected = [
        (view.name, hole.diameter, view.project(hole.centre))
        for hole in ctx.holes
        for view in ctx.views.values()
        if view.looks_along(hole.direction)
    ]
    offsets = gui.measure_bars(page, [e[2] for e in expected], composed.width)
    return [[name, diameter, offset] for (name, diameter, _), offset in zip(expected, offsets, strict=True)]


def quality_metrics(doc: reg.DocumentRec, sheet: str | None, op: str | None) -> dict[str, Any]:
    """Test-only: what the quality floor measures on the sheet as composed now -- per hole whether the sheet gives its x and
    its y (and the values), the pairs of leaders that cross, the leaders that cut the outline more than once, the dimensions
    inside an outline, the material field of the title block and the notes block."""
    import sheet_quality  # noqa: PLC0415

    page = _need_page(doc.fc_doc, sheet, op)
    found = pages(doc.fc_doc)
    composed, results, ctx = compose(doc, page, spec_of(page), found.index(page) + 1, len(found), op)
    spec = spec_of(page)
    items = [sheet_items.normalise(i, set(), 1) for i in spec.get("items", [])]
    ctx.extras["layout_planned"] = spec.get("layout") is not None
    data = sheet_review.ReviewInput(items, {r.id: r for r in results}, ctx, composed, feature_infos(doc))
    quality = sheet_quality.check(data)
    positions = ctx.extras.get("positions", {})
    return {
        "holes": {
            str(h.index): {"diameter": h.diameter, **{k: v for k, v in positions.get(h.index, {}).items()}}
            for h in ctx.holes
        },
        "findings": [[f.code, list(f.items), f.severity] for f in quality],
        "leaders": len(ctx.extras.get("leaders", [])),
        "material": str(ctx.extras.get("fields", {}).get("part_material", "")),
        "texts": composed.texts(),
        "scale": _scale_of(page),
        "views": sorted(ctx.views),
    }


def compare_window(doc: reg.DocumentRec, sheet: str | None, op: str | None) -> list[str]:
    """What differs between the objects of the sheet's page and the sheet as composed (empty: window and PDF agree)."""
    page = _need_page(doc.fc_doc, sheet, op)
    found = pages(doc.fc_doc)
    composed, _, ctx = compose(doc, page, spec_of(page), found.index(page) + 1, len(found), op)
    return sheet_mirror.compare(page, ctx.extras["units"], composed.height)


CORNER_CHAMFER_MAX = 0.25  # a flat chamfer of the outline is at most this fraction of the second largest side of the part
CORNER_AXIS_TOL = 0.02


def corner_chamfers(doc: reg.DocumentRec) -> list[sheet_review.FeatureInfo]:
    """The corners an outline sketch cut (a rectangle with ``chamfer_mm``): flat faces that no chamfer feature made, running
    parallel to a world axis and slanted across the other two, small beside the part, between two flat faces. One entry per
    size (``chamfer``, the faces of equal corners together) so that the review and ``chamfer_dims`` ask for ONE callout per
    size. A part of several bodies (a frame, a weldment) is not read: its slanted faces are mitres."""
    bodies = doc.live_bodies()
    if doc.components or len(bodies) != 1:
        return []
    sides = sorted(_extents(doc, None, None), reverse=True)
    limit = CORNER_CHAMFER_MAX * sides[1] if len(sides) > 1 else 0.0
    if limit <= 0.0:
        return []
    geometry = LiveGeometry(doc, [], None)
    groups: dict[tuple[float, float, float], list[str]] = {}
    for rec in bodies[0].faces.values():
        if rec.stale:
            continue
        try:
            face = geometry.face(rec.id)
        except sc.ItemError:
            continue
        if face.kind != "plane" or face.normal is None:
            continue
        parts = [abs(c) for c in face.normal]
        if max(parts) > 1 - CORNER_AXIS_TOL or min(parts) > CORNER_AXIS_TOL:
            continue  # facing an axis, or slanted across all three: not the cut corner of an outline
        try:
            info = geometry.chamfer(rec.id)
        except sc.ItemError:
            continue
        if info.leg_a > limit:
            continue
        groups.setdefault((round(info.leg_a, 2), round(info.leg_b, 2), round(info.angle_deg, 1)), []).append(rec.id)
    return [
        sheet_review.FeatureInfo(f"corner{number}", "chamfer", "corner chamfer", faces, outline=True)
        for number, faces in enumerate(groups.values(), start=1)
    ]


def feature_infos(doc: reg.DocumentRec) -> list[sheet_review.FeatureInfo]:
    """The features of the part the review looks at (cuts, chamfers, fillets) with the faces and edges they made."""
    out = []
    for feature in doc.features:
        if feature.kind in ("cut", "chamfer", "fillet"):
            label = str(feature.label or feature.kind)
            out.append(
                sheet_review.FeatureInfo(
                    feature.id, feature.kind, label, list(feature.created_faces), list(feature.created_edges)
                )
            )
    out += [c for c in corner_chamfers(doc) if not any(set(c.faces) & set(f.faces) for f in out)]
    return out


def review_report(
    doc: reg.DocumentRec, page: Any, spec: dict[str, Any], number: int, total: int, op: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The review of one sheet: (findings, questions of judgement), each with the id an answer names and the answer the
    sheet holds for it (``None``: pending)."""
    findings, questions, _, _ = review_bundle(doc, page, spec, number, total, op, with_facts=False)
    return findings, questions


def review_bundle(
    doc: reg.DocumentRec,
    page: Any,
    spec: dict[str, Any],
    number: int,
    total: int,
    op: str | None,
    with_facts: bool = True,
    drawn: tuple[model.Sheet, list[sc.ItemResult], sc.Context] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """``review_report`` and, with ``with_facts``, the facts the table of requirements of a drawing reads
    (``sheet_reqfacts``), counted on the same composed sheet (``drawn``: the one the caller has just composed from this
    very ``spec``, so that it is not composed a second time)."""
    findings, ctx = review_findings(doc, page, spec, number, total, op, drawn)
    answers = spec.get("answers", {})
    for row in findings:
        row["answer"] = answers.get(row["id"])
    questions = [
        {"id": q.id, "text": q.text, "answer": answers.get(q.id)} for q in sheet_checks.judgement(_last_input(ctx))
    ]
    facts = sheet_reqfacts.facts(_last_input(ctx)) if with_facts else {}
    given = sheet_reqfacts.measures(_last_input(ctx)) if with_facts else {}
    return findings, questions, facts, given


def _last_input(ctx: sc.Context) -> sheet_review.ReviewInput:
    """The review input the last ``review_findings`` built (the questions read the same context)."""
    return ctx.extras["review_input"]


def review_findings(
    doc: reg.DocumentRec,
    page: Any,
    spec: dict[str, Any],
    number: int,
    total: int,
    op: str | None,
    drawn: tuple[model.Sheet, list[sc.ItemResult], sc.Context] | None = None,
) -> tuple[list[dict[str, Any]], sc.Context]:
    """The findings of one sheet as rows (``sheet_review``), with their ids, and the context they were found in."""
    sheet, results, ctx = drawn if drawn is not None else compose(doc, page, spec, number, total, op)
    items: list[dict[str, Any]] = []
    taken: set[str] = set()
    for position, raw in enumerate(spec.get("items", []), start=1):
        try:
            done = sheet_items.normalise(raw, taken, position)
        except sc.ItemError:
            continue
        taken.add(done["id"])
        items.append(done)
    ctx.extras["layout_planned"] = spec.get("layout") is not None  # the sheet was placed by the global layout (the sheet flow)
    data = sheet_review.ReviewInput(items, {r.id: r for r in results}, ctx, sheet, feature_infos(doc))
    ctx.extras["review_input"] = data
    ctx.extras["hand_marks"] = hand_marks(page)  # what the engineer put on the TechDraw page by hand
    findings = [
        {
            "code": f.code,
            "severity": f.severity,
            "message": f.message,
            "items": [*f.items],
            "features": [*f.features],
            "key": f.key,
        }
        for f in sheet_review.review(data)
    ]
    for line in (spec.get("layout") or {}).get("unplaced", []):  # what the global layout could not place: never dropped silently
        key, _, why = str(line).partition(": ")
        findings.append(
            {
                "code": "layout_unplaced",
                "severity": "warning",
                "message": ctx.say(
                    f"El planificador de la hoja no pudo colocar {key}: {why}. Colócala con side u offset_mm o cambia la hoja.",
                    f"The sheet layout could not place {key}: {why}. Place it with side or offset_mm, or change the sheet.",
                ),
                "items": [key] if key in {str(i.get("id")) for i in spec.get("items", [])} else [],
                "features": [],
                "key": key,
            }
        )
    held = sheet_mirror.owned(page)
    for unit in ctx.extras["units"]:  # an item that asked for TechDraw's own objects and is drawn as a symbol instead
        if unit.natives and held.get(unit.key) and not sheet_native.made(unit, held[unit.key]):
            findings.append(
                {
                    "code": "native_fallback",
                    "severity": "info",
                    "message": ctx.say(
                        f"{unit.key} no tiene objetos nativos de TechDraw (los valores no coincidían o falta una arista): "
                        "se dibuja como símbolo y el ingeniero no lo puede editar en FreeCAD.",
                        f"{unit.key} has no native TechDraw objects (the values did not match or an edge is missing): it is "
                        "drawn as a symbol and the engineer cannot edit it in FreeCAD.",
                    ),
                    "items": [unit.key],
                    "features": [],
                }
            )
    foreign = sheet_symbols.foreign_symbols(page, sheet_symbols.resource_dir())
    if foreign:  # a drawing of ours that is not a FreeCAD object: it does not follow its view and the engineer cannot edit it
        keys = sorted({str(getattr(o, sheet_mirror.PROP_KEY, "") or o.Label) for o in foreign})
        findings.append(
            {
                "code": "foreign_symbol",
                "severity": "info",  # the items still drawn this way are listed in tests/unit/freecad_client/test_native_only.py
                "message": ctx.say(
                    f"La hoja tiene {len(foreign)} símbolo(s) que no son de la biblioteca de FreeCAD ({', '.join(keys)[:120]}): "
                    "son un dibujo suelto que no sigue a su vista ni se edita línea a línea, y se queda si se borra la vista. "
                    "Debe ser un objeto nativo de TechDraw.",
                    f"The sheet holds {len(foreign)} symbol(s) that are not from FreeCAD's library ({', '.join(keys)[:120]}): "
                    "a loose drawing that does not follow its view, cannot be edited line by line and stays when the view is "
                    "deleted. It must be a native TechDraw object.",
                ),
                "items": [k for k in keys if k in {str(i.get("id")) for i in spec.get("items", [])}],
                "features": [],
                "key": "",
            }
        )
    unread = sheet_output.native_rows(page)
    if unread:  # on the page, in the PDF and in the capture, but not in the composed sheet the review reads
        shown = "; ".join(sheet_output.listed(unread)[:5]) + (f" (+{len(unread) - 5})" if len(unread) > 5 else "")
        findings.append(
            {
                "code": "native_unread",
                "severity": "warning",
                "message": ctx.say(
                    f"La hoja tiene {len(unread)} objeto(s) nativos de TechDraw que esta revisión no puede leer (sus cotas, "
                    f"rayados y tipos de línea no se comprueban): {shown}. Se ven en la captura de la hoja y en el PDF; "
                    "revísalos mirando la captura (con región para acercar); no los des por comprobados.",
                    f"The sheet holds {len(unread)} native TechDraw object(s) this review cannot read (their dimensions, "
                    f"hatching and line types are not checked): {shown}. They show in the sheet capture and in the PDF; "
                    "look at them in the capture (with a region to zoom), do not take them as checked.",
                ),
                "items": [],
                "features": [],
                "key": "",
            }
        )
    differs = sheet_mirror.compare(page, ctx.extras["units"], sheet.height)
    if differs:  # the window must show what the PDF shows: a difference is an error of ours, never of the drawing
        shown = "; ".join(differs[:3]) + (f" (+{len(differs) - 3})" if len(differs) > 3 else "")
        text = ctx.say(
            f"La ventana de FreeCAD no muestra lo mismo que el PDF: {shown}. Exportar o añadir un ítem la redibuja.",
            f"The FreeCAD window does not show what the PDF shows: {shown}. Exporting or adding an item redraws it.",
        )
        findings.append({"code": "window_differs", "severity": "warning", "message": text, "items": [], "features": []})
    sheet_checks.assign_ids(findings)
    return findings, ctx


MAX_PRINTED = 160


def _printed(sheet: model.Sheet) -> list[str]:
    """The texts the sheet prints, in the order it draws them and without repeats: dimension values, callouts, notes,
    title block. What a reader who only has the drawing can read, as text."""
    seen: dict[str, None] = {}
    for text in sheet.texts():
        clean = " ".join(str(text).split())
        if clean:
            seen.setdefault(clean, None)
    return list(seen)[:MAX_PRINTED]


def describe(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Every sheet (or the one asked for) with its title-block fields and its items as stored, each with what it does
    now against the part; with ``review`` also the findings of the review."""
    op = params.get("operation_id")
    sheets = []
    for page, spec, number, total in _all_sheets(doc, params.get("sheet"), op):
        drawn = compose(doc, page, spec, number, total, op)
        composed, results, _ = drawn
        by_id = {r.id: r for r in results}
        size, language = split_key(spec["template"])
        row: dict[str, Any] = {
            "sheet": spec["sheet"],
            "template": size,
            "language": language,
            "page_mm": list(sheet_layout.PAGES_MM[size]),
            "scale": _scale_of(page) or 1.0,
            "body": spec.get("body"),
            "projection": projection_of(spec),
            "angle": key_angle(spec["template"]),
            "views": ["isometric" if v == "iso" else v for v in views_of(spec)],
            "fields": {k: v for k, v in spec.get("fields", {}).items() if v},
            "items": [
                {
                    "id": item["id"],
                    "type": item["type"],
                    "status": by_id[item["id"]].status if item["id"] in by_id else "error",
                    "detail": by_id[item["id"]].detail if item["id"] in by_id else "not drawn",
                    "spec": item,
                }
                for item in spec.get("items", [])
            ],
        }
        row["answers"] = {str(k): dict(v) for k, v in spec.get("answers", {}).items()}
        row["approved_by"] = str(spec.get("fields", {}).get("approval_person", ""))
        row["edits"] = int(spec.get("edits", 0))
        row["reports"] = _report_rows(spec)
        row["review_request"] = _request_row(spec)
        row["review_choice"] = _choice_row(spec)
        row["composed"] = dict(spec.get("composed") or {})
        row["printed"] = _printed(composed)
        row["native"] = sheet_output.listed(sheet_output.native_rows(page))
        if params.get("review"):
            row["findings"], row["questions"], row["facts"], given = review_bundle(
                doc, page, spec, number, total, op, drawn=drawn
            )
            row.update(given)
        sheets.append(row)
    return {"sheets": sheets}


# --- rendering and exporting ------------------------------------------------------------------------------------------


def _render_failure(exc: Exception, op: str | None, what: str) -> WorkerError:
    if isinstance(exc, WorkerError):
        return exc
    return error("internal", f"{what}: {type(exc).__name__}: {exc}", op)


MIN_REGION_MM = 2.0


def _region(value: Any, sheet: model.Sheet, op: str | None) -> tuple[float, float, float, float] | None:
    """The zoom a capture asks for (``x0, y0, x1, y1`` in page mm, origin at the lower left), inside the sheet."""
    if value is None:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise invalid_request("region needs four numbers: x0, y0, x1, y1 (mm on the sheet)", op) from exc
    if not (0.0 <= x0 <= x1 <= sheet.width and 0.0 <= y0 <= y1 <= sheet.height):
        raise invalid_request(
            f"region {x0:g},{y0:g},{x1:g},{y1:g} is not inside the sheet: it is {sheet.width:g} x {sheet.height:g} mm, "
            "x to the right and y up from the lower left corner",
            op,
        )
    if x1 - x0 < MIN_REGION_MM or y1 - y0 < MIN_REGION_MM:
        raise invalid_request(f"region must be at least {MIN_REGION_MM:g} mm on each side", op)
    return x0, y0, x1, y1


def _bounds_of_ink(entities: Any) -> tuple[float, float, float, float] | None:
    """The box of what a unit draws with lines (its texts are left out: the PDF check looks for strokes)."""
    return sheet_units.bounds([e for e in entities if not isinstance(e, model.Text)])


def sheet_views(page: Any, ctx: sc.Context, spec: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """The views the sheet holds, each with its box on the page (mm, y up) -- the standard ones, the native ones a script
    added and the derived ones the items made (section, detail, auxiliary) -- and the notes where the page differs from
    what the items say: a view the items count that the page lacks (the engineer may have deleted it in the window), or a
    view the items do not know of."""
    found: list[dict[str, Any]] = []
    notes: list[str] = []
    standard: set[str] = set()
    if projection_of(spec) == "hlr":
        rects = ctx.extras.get("view_boxes", [])
        names = [*sheet_hlr.GROUP, *(["isometric"] if len(rects) > len(sheet_hlr.GROUP) else [])]
        for name, rect in zip(names, rects, strict=False):
            found.append({"name": name, "rect": list(rect), "kind": "standard"})
            standard.add(name)
    else:
        for view in part_views(page):
            try:
                x, y = _page_position(page, view)
                box = _view_box(view)
            except Exception as exc:  # noqa: BLE001 - a view with no geometry has no box to look for ink in
                notes.append(f"view {view.Label} has no geometry to measure ({type(exc).__name__}: {exc})")
                continue
            rect = [x + box.XMin, y - box.YMax, x + box.XMax, y - box.YMin]
            if str(view.TypeId) == "TechDraw::DrawProjGroupItem":
                name = view_key(view) or str(view.Label)
            elif str(view.Label) == sheet_native.VIEW_LABELS["isometric"]:
                name = "isometric"
            else:
                found.append({"name": str(view.Label), "rect": rect, "kind": "native"})
                continue
            found.append({"name": name, "rect": rect, "kind": "standard"})
            standard.add(name)
    for unit in ctx.extras.get("units", []):
        if unit.item_type in ("section", "detail", "aux_view"):
            rect = _bounds_of_ink(unit.entities)
            if rect is not None:
                found.append({"name": f"{unit.item_type} {unit.key}", "rect": list(rect), "kind": "derived"})
    wanted = {"isometric" if v == "iso" else v for v in views_of(spec)}
    if wanted - standard:
        notes.append(
            f"the sheet's views are {', '.join(sorted(wanted))} and the page has no {', '.join(sorted(wanted - standard))}"
        )
    return found, notes


def _prepare(
    doc: reg.DocumentRec, page: Any, spec: dict[str, Any], number: int, total: int, op: str | None
) -> tuple[model.Sheet, sc.Context, list[str]]:
    """Everything ``capture`` and ``export`` share, so both write the same thing: the colours TechDraw's PDF writer cannot
    print are normalised (``sheet_colours``), the sheet is composed, and the page is made to show it (``mirror``). Returns
    the composed sheet, its context and the notes for the reply."""
    notes = sheet_colours.repair(part_views(page))
    if notes:
        for view in part_views(page):
            view.touch()
        doc.fc_doc.recompute()
        gui.pump()
    sheet, _, ctx = compose(doc, page, spec, number, total, op)
    _, warnings = mirror(doc, page, ctx, sheet)  # the window shows what is about to be written
    return sheet, ctx, [*notes, *ctx.extras.get("iso_notes", []), *warnings]


def _outcome(
    page: Any,
    ctx: sc.Context,
    spec: dict[str, Any],
    sheet: model.Sheet,
    renderer: str,
    warnings: list[str],
) -> dict[str, Any]:
    """The part of the reply that says what the file holds: the views with their boxes, the native objects on the page and,
    when the renderer is not the scene itself, which of them it did not draw."""
    rows = sheet_output.native_rows(page)
    views, notes = sheet_views(page, ctx, spec)
    return {
        "renderer": renderer,
        "page_mm": [sheet.width, sheet.height],
        "views": views,
        "native": sheet_output.listed(rows),
        "unrendered": [] if renderer == "techdraw" else sheet_output.not_drawn(rows),
        "warnings": [*warnings, *notes],
    }


def capture(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The sheet as a PNG, from the same source as the PDF (``gui.render_png`` paints the scene the PDF is written from;
    the headless CAD renders the composed sheet): every object of the page, native ones included, or the reply lists
    the ones it could not draw. ``region`` zooms into part of the sheet."""
    op = params.get("operation_id")
    path = str(params.get("path", ""))
    if not path:
        raise invalid_request("capture_drawing needs a path", op)
    ((page, spec, number, total),) = _all_sheets(doc, params.get("sheet") or spec_name_of_first(doc, op), op)
    width = int(params.get("width") or 1400)
    renderer = "compositor"
    try:
        sheet, ctx, warnings = _prepare(doc, page, spec, number, total, op)
        region = _region(params.get("region"), sheet, op)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        size: tuple[int, int] = (0, 0)
        if gui.available():
            try:
                size = gui.render_png(page, path, width, (sheet.width, sheet.height), region)
                renderer = "techdraw"
            except Exception as exc:  # noqa: BLE001 - the image is still the same sheet, less what only the scene draws
                warnings.append(
                    f"the sheet window could not be painted ({type(exc).__name__}: {exc}); the image is CotaForge's own "
                    "rendering of the sheet"
                )
        if renderer != "techdraw":
            size = sheet_render.render_png(sheet_svg.to_svg(sheet), path, width, sheet.width, sheet.height, region)
    except Exception as exc:  # noqa: BLE001
        raise _render_failure(exc, op, "rendering the sheet failed") from exc
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise error("internal", f"the sheet image was not written at {path}", op)
    out = _outcome(page, ctx, spec, sheet, renderer, warnings)
    out["size_px"] = list(size)
    out["region"] = list(region) if region is not None else None
    return out


def spec_name_of_first(doc: reg.DocumentRec, op: str | None) -> str:
    return spec_of(_need_page(doc.fc_doc, None, op))["sheet"]


def export(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """ONE sheet as a PDF or a DXF at ``path``: one drawing, one sheet, one file. ``sheet`` names it; without it the
    document must have exactly one (with several the error lists them). Returns the file written."""
    op = params.get("operation_id")
    path = str(params.get("path", ""))
    fmt = str(params.get("format", ""))
    if not path:
        raise invalid_request("export_drawing needs a path", op)
    if fmt not in ("pdf", "dxf"):
        raise invalid_request(f"unknown drawing format {fmt!r}, expected pdf or dxf", op)
    wanted = params.get("sheet")
    if not wanted:
        names = [spec_of(p)["sheet"] for p in pages(doc.fc_doc)]
        if len(names) > 1:
            raise invalid_request(
                f"the document has {len(names)} sheets ({', '.join(names)}): an export writes ONE sheet to ONE file; "
                "name it with sheet=",
                op,
            )
    ((page, spec, number, total),) = _all_sheets(doc, wanted, op)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    renderer = "compositor"
    try:
        sheet, ctx, warnings = _prepare(doc, page, spec, number, total, op)
        if fmt == "pdf":
            if gui.available():  # the PDF is TechDraw's own: the page the engineer sees and edits
                try:
                    renderer = gui.export_pdf(page, path)
                except Exception as exc:  # noqa: BLE001 - never lose the export: ours is the same sheet
                    warnings.append(
                        f"TechDraw's own PDF export failed ({type(exc).__name__}: {exc}); the PDF is written by CotaForge"
                    )
            if renderer != "techdraw":
                sheet_render.render_pdf(sheet_svg.to_svg(sheet), path, sheet.width, sheet.height, sheet.title)
        else:
            with open(path, "wb") as handle:
                handle.write(sheet_dxf.to_dxf(sheet))
    except Exception as exc:  # noqa: BLE001
        raise _render_failure(exc, op, f"writing the {fmt} failed") from exc
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise error("internal", f"the {fmt} was not written at {path}", op)
    out = _outcome(page, ctx, spec, sheet, renderer if fmt == "pdf" else "compositor", warnings)
    return {"paths": [path], **out}
