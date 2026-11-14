# ruff: noqa: E501
"""The TechDraw page as the engineer sees it while the sheet is being built: what the items put on the sheet, made as
objects of the page, item by item (the live mirror of the sheet).

In 3D every operation appears in the window as it is made and the engineer can stop it; the drawing works the same way:
each time an item is added, changed or removed (``drawing.annotate``), the objects of that item are made, replaced or
deleted on the page, and the sheet window of the visible CAD is shown. The PDF, the PNG and the DXF stay what we compose
(``sheet_model``); the mirror is made FROM that composition -- the same entities at the same places -- so the window and
the deliverable cannot differ, and ``compare`` checks that they do not.

What the page holds:

* the title block, the revision rows and the scale are TechDraw's own editable texts of the template
  (``drawing._sync_template_fields``); the overall dimensions are TechDraw dimension objects (``makeExtentDim``) and the
  views of a ``techdraw`` sheet are its own view objects: all native;
* everything an item draws -- callouts with their leader and shelf, dimension lines (we decide how they stack), GD&T
  frames, datum and finish symbols, tables, the section hatch, thread arcs -- is a ``DrawViewSymbol`` whose SVG is that
  item's own drawing (``sheet_svg.fragment``), centred where the drawing sits on the page. TechDraw has dimension and
  leader objects, but they place their own lines and text, so the window would show something other than the PDF;
* an ``hlr`` sheet has no view objects: its views are symbols of our projection too, so the window is never blank.

Each object carries the key of the unit it mirrors (``CotaforgeKey``); a unit whose object already holds exactly the SVG
and the position the sheet gives it is left alone, so adding one item to a sheet of thirty touches one object (and an
object the engineer dragged is put back). Nothing here reads the model: a symbol is a picture of what the compositor
already measured.
"""

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

import gui
import sheet_model as sm
import sheet_native
import sheet_svg
import sheet_units as su
from sheet_draw import native_label

PROP_KEY = "CotaforgeKey"
PROP_NATIVE = "CotaforgeNative"
PROP_VIEW = "CotaforgeView"
PROP_ITEM = "CotaforgeItem"  # every object an item made carries its id ...
PROP_SHEET = "CotaforgeSheet"  # ... and the name of its sheet: how a selection in the window finds the item
OVERALL_ITEM = "o1"  # the item every sheet starts with: TechDraw's own overall dimensions belong to it
SYMBOL = "TechDraw::DrawViewSymbol"
POSITION_TOL_MM = 0.01
SIZE_TOL_MM = 0.01
GUIDE_MARGIN_MM = 7.0  # the window snaps an object to a view origin within 6 mm (measured); keep clear of it by 7
MAX_SHIFT_MM = 48
# the shift is vertical only: a sideways shift painted the drawing aside by the padding (measured in 1.1.3, acceptance of the plate)
MEASURE_TOL_MM = 0.01  # the sheet prints at most two decimals; TechDraw measures the projected edges
NAME_CHARS = re.compile(r"[^A-Za-z0-9_]")
SVG_NS = "{http://www.w3.org/2000/svg}"


@dataclass
class Report:
    """What a sync did, by unit key."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    fallbacks: dict[str, list[str]] = field(default_factory=dict)  # unit key -> why its native objects were not made

    def touched(self) -> set[str]:
        return {*self.created, *self.updated, *self.removed}

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated or self.removed)

    def line(self) -> str:
        done = (("new", self.created), ("changed", self.updated), ("gone", self.removed))
        parts = [f"{len(keys)} {name}" for name, keys in done if keys]
        return ", ".join(parts) if parts else "unchanged"


def owned(page: Any) -> dict[str, list[Any]]:
    """The objects of ``page`` the mirror made, by unit key."""
    found: dict[str, list[Any]] = {}
    for obj in page.Views:
        if PROP_KEY in obj.PropertiesList:
            found.setdefault(str(getattr(obj, PROP_KEY)), []).append(obj)
    return found


def _tag(obj: Any, key: str, sheet: str = "") -> None:
    """The unit key and the sheet (``sheet_native.tag``); the item id is ``tag_item``'s, for the units that are items."""
    sheet_native.tag(obj, key, "", sheet)


def sheet_name(page: Any) -> str:
    """The name of the sheet ``page`` is (its spec's, else the page's own)."""
    try:
        spec = json.loads(str(getattr(page, "CotaforgeSpec")))  # noqa: B009 - a page of another make has no spec
        return str(spec.get("sheet") or page.Name)
    except (AttributeError, ValueError):
        return str(page.Name)


def tag_item(obj: Any, item_id: str, sheet: str) -> None:
    """Say which item of which sheet ``obj`` is part of (``CotaforgeItem`` / ``CotaforgeSheet``): what ``sheet_select``
    reads when the engineer selects the object in the window. Safe to repeat."""
    for name, text, doc in (
        (PROP_ITEM, item_id, "Item of the sheet this object belongs to"),
        (PROP_SHEET, sheet, "Sheet it is on"),
    ):
        if name not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", name, "Cotaforge", doc)
            obj.setEditorMode(name, 1)
        if str(getattr(obj, name)) != text:
            setattr(obj, name, text)


def _remove(fc_doc: Any, objects: list[Any]) -> None:
    for obj in objects:
        if fc_doc.getObject(obj.Name) is not None:
            fc_doc.removeObject(obj.Name)


Guides = tuple[list[float], list[float]]


def guides_of(page: Any, skip: set[str] = frozenset()) -> Guides:  # type: ignore[assignment]
    """The x and y of the places TechDraw's own objects sit at (the origins of the views, the symbols already made):
    the sheet window pulls a symbol whose centre comes within ``SNAP_MM`` of one of them ON to it (verified in
    FreeCAD 1.1.3: a callout drawn 5 mm off the centre of a view was moved to it), which would put a symbol's drawing
    out of place. ``skip`` are unit keys whose symbols are being replaced."""
    xs: list[float] = []
    ys: list[float] = []
    for obj in page.Views:
        type_id = str(obj.TypeId)
        if PROP_KEY in obj.PropertiesList:
            if type_id == SYMBOL and str(getattr(obj, PROP_KEY)) not in skip:
                xs.append(float(obj.X))
                ys.append(float(obj.Y))
        elif type_id == "TechDraw::DrawProjGroup":
            xs.append(float(obj.X))
            ys.append(float(obj.Y))
            for item in obj.Views:
                x, y = page_position(page, item)
                xs.append(x)
                ys.append(y)
        elif "Dim" not in type_id and hasattr(obj, "X"):
            xs.append(float(obj.X))
            ys.append(float(obj.Y))
    return xs, ys


def _clear(value: float, guides: list[float]) -> bool:
    return all(abs(value - g) > GUIDE_MARGIN_MM for g in guides)


def shift_for(unit: su.Unit, guides: Guides) -> tuple[float, float]:
    """How far to move the centre of the unit's symbol (by padding one side of its SVG with nothing) so that no guide of
    the page is within ``GUIDE_MARGIN_MM`` of it: the smallest shift, searched outward in 1 mm steps."""
    cx, cy = unit.centre
    steps = [0.0]
    for n in range(1, MAX_SHIFT_MM + 1):
        steps += [float(n), float(-n)]
    sx = 0.0  # a horizontal padding painted the drawing aside by the padding (measured in 1.1.3): only y is shifted
    sy = next((s for s in steps if _clear(cy + s, guides[1])), 0.0)
    return sx, sy


def _rect(unit: su.Unit, shift: tuple[float, float]) -> su.Rect:
    """The box of the unit's SVG: the content with its margin, and on one side per axis the padding that moves the
    centre of the box by ``shift`` (the padding is empty: nothing is drawn there)."""
    x0, y0, x1, y1 = su.padded(unit.rect)
    sx, sy = shift
    return (x0 - 2 * max(-sx, 0.0), y0 - 2 * max(-sy, 0.0), x1 + 2 * max(sx, 0.0), y1 + 2 * max(sy, 0.0))


def _svg(unit: su.Unit, page_h: float, shift: tuple[float, float] = (0.0, 0.0)) -> str:
    return sheet_svg.fragment(unit.entities, page_h, _rect(unit, shift), unit.label)


def _make_symbol(fc_doc: Any, page: Any, unit: su.Unit, page_h: float, guides: Guides, sheet: str = "") -> Any:
    shift = shift_for(unit, guides)
    obj = fc_doc.addObject(SYMBOL, NAME_CHARS.sub("_", f"{page.Name}_{unit.key}"))
    page.addView(obj)
    obj.Label = unit.label
    obj.ScaleType = "Custom"
    obj.Scale = 1.0
    obj.Symbol = _svg(unit, page_h, shift)
    obj.X, obj.Y = unit.centre[0] + shift[0], unit.centre[1] + shift[1]
    _tag(obj, unit.key, sheet)
    if unit.kind == su.KIND_ITEM:
        tag_item(obj, unit.key, sheet_name(page))
    return obj


def _current(objects: list[Any], unit: su.Unit, page_h: float, guides: Guides) -> bool:
    """Whether the unit's object already holds its drawing at its place: the SVG is the unit's (with the padding its
    position implies) and no guide of the page is close enough to pull it."""
    if len(objects) != 1 or str(objects[0].TypeId) != SYMBOL:
        return False
    obj = objects[0]
    cx, cy = unit.centre
    x, y = float(obj.X), float(obj.Y)
    return (
        str(obj.Symbol) == _svg(unit, page_h, (x - cx, y - cy))
        and abs(round(x - cx) - (x - cx)) <= POSITION_TOL_MM
        and abs(round(y - cy) - (y - cy)) <= POSITION_TOL_MM
        and _clear(y, guides[1])
        and str(obj.Label) == unit.label
    )


# --- TechDraw's own overall dimensions, put where the sheet draws them -----------------------------------------------


def tag_native(obj: Any, key: str, view_name: str, item: str = "", sheet: str = "") -> None:
    """Mark a native object the mirror places (an overall dimension) with its key and the view it measures, and with the
    contract's item and sheet."""
    obj.addProperty("App::PropertyString", PROP_NATIVE, "Cotaforge", "Native object the sheet places")
    obj.addProperty("App::PropertyString", PROP_VIEW, "Cotaforge", "View the object measures")
    for name in (PROP_NATIVE, PROP_VIEW):
        obj.setEditorMode(name, 1)
    setattr(obj, PROP_NATIVE, key)
    setattr(obj, PROP_VIEW, view_name)
    view = obj.Document.getObject(view_name)
    page = view.findParentPage() if view is not None and hasattr(view, "findParentPage") else None
    tag_item(obj, item or OVERALL_ITEM, sheet or (sheet_name(page) if page is not None else ""))


def natives(page: Any) -> dict[str, Any]:
    return {str(getattr(o, PROP_NATIVE)): o for o in page.Views if PROP_NATIVE in o.PropertiesList}


def page_position(page: Any, view: Any) -> tuple[float, float]:
    """Centre of a view on the page (TechDraw coordinates, y up): a projection item sits relative to its group."""
    if str(view.TypeId) == "TechDraw::DrawProjGroupItem":
        for obj in page.Views:
            if str(obj.TypeId) == "TechDraw::DrawProjGroup" and view.Name in [item.Name for item in obj.Views]:
                return float(obj.X) + float(view.X), float(obj.Y) + float(view.Y)
    return float(view.X), float(view.Y)


def _label_place(page: Any, obj: Any, info: dict[str, Any]) -> tuple[float, float]:
    """Where the label of the native dimension ``obj`` goes, in its view's own coordinates (y up): the dimension line is
    the one the sheet draws, the label is put so that TechDraw lands its line there (``sheet_draw.native_label``)."""
    view = obj.Document.getObject(str(getattr(obj, PROP_VIEW)))
    vx, vy = page_position(page, view)
    x, y = native_label(str(info["orient"]), float(info["mid"]), float(info["line"]))
    return x - vx, y - vy


def _split_native(page: Any, units: list[su.Unit]) -> tuple[list[su.Unit], list[su.Unit]]:
    """(the units mirrored as symbols, the units TechDraw's own objects hold): a unit is native only when the page still
    has an object for each of its dimensions (``create`` makes them; an ``overall`` added again later is a symbol)."""
    present = natives(page)
    held = [u for u in units if u.native and all(f"overall:{name}" in present for name in u.native)]
    return [u for u in units if u not in held], held


def _sync_overall(fc_doc: Any, page: Any, units: list[su.Unit], report: Report) -> None:
    """The overall dimensions of a ``techdraw`` sheet are TechDraw's own objects (``makeExtentDim``): each is put on
    the line the sheet draws it on, and one the sheet does not draw (a round part gets its diameter once) is deleted."""
    held = {name: info for u in units if u.native for name, info in u.native.items()}
    for key, obj in natives(page).items():
        tag_item(obj, OVERALL_ITEM, sheet_name(page))  # a sheet made before the tags existed gets them
        name = key.partition(":")[2]
        info = held.get(name)
        if info is None:
            fc_doc.removeObject(obj.Name)
            report.removed.append(key)
            continue
        x, y = _label_place(page, obj, info)
        if abs(float(obj.X) - x) > POSITION_TOL_MM or abs(float(obj.Y) - y) > POSITION_TOL_MM:
            obj.X, obj.Y = x, y
            report.updated.append(key)
        text = str(info.get("text", ""))
        if text.startswith("Ø") and str(obj.FormatSpec) != text:
            obj.Arbitrary = True  # the extent dimension measures a number: a diameter is written with its symbol
            obj.FormatSpec = text
            report.updated.append(key)


def _symbols(objects: list[Any]) -> list[Any]:
    return [o for o in objects if str(o.TypeId) == SYMBOL]


def symbol_unit(unit: su.Unit, native_ok: bool) -> su.Unit | None:
    """What of ``unit`` is mirrored as a symbol: all of it, or -- when TechDraw's own objects hold its dimension lines
    and texts -- only the rest (the centre marks of a hole callout); ``None`` when nothing is left."""
    if not unit.natives or not native_ok:
        return unit
    if not unit.residue:
        return None
    return replace(unit, entities=unit.residue, natives=())


def sync(fc_doc: Any, page: Any, units: list[su.Unit], page_h: float, sheet: str = "") -> Report:
    """Make the page agree with ``units``: new units are made, changed ones replaced, those that are gone deleted, the
    rest untouched. A unit whose item asked for TechDraw's own objects (native dimensions, annotations) gets them
    (``sheet_native``) and keeps a symbol only for what they do not draw; one whose natives cannot be made (a value
    TechDraw measures differently, an edge the view lacks) stays a symbol, and ``report.fallbacks`` says why. Recomputes
    once and shows the sheet window; returns what it did."""
    have = owned(page)
    report = Report()
    units = _split_native(page, units)
    _sync_overall(fc_doc, page, units[1], report)
    units = units[0]
    wanted = {u.key for u in units}
    for key in list(have):
        if key not in wanted:
            _remove(fc_doc, have.pop(key))
            report.removed.append(key)
    report.fallbacks = sheet_native.sync(fc_doc, page, [u for u in units if u.natives], sheet, have, report)
    sheet_native.sync_flags(page, [u for u in units if u.natives and u.key not in report.fallbacks])
    have = owned(page)  # the native objects were made again: what is on the page now
    for unit in units:
        shown = symbol_unit(unit, unit.key not in report.fallbacks)
        objects = _symbols(have.get(unit.key, []))
        if shown is None:
            _remove(fc_doc, objects)
            continue
        guides = guides_of(page, {unit.key})
        if _current(objects, shown, page_h, guides):
            if unit.kind == su.KIND_ITEM:  # a sheet made before the tags existed gets them
                for obj in objects:
                    tag_item(obj, unit.key, sheet_name(page))
            if unit.key not in report.touched():
                report.unchanged.append(unit.key)
            continue
        _remove(fc_doc, objects)
        _make_symbol(fc_doc, page, shown, page_h, guides, sheet)
        if unit.key not in report.touched():
            (report.updated if objects else report.created).append(unit.key)
    apply_weights(page)
    if report.changed:
        fc_doc.recompute()
    gui.show_sheet(page)
    return report


def _view_objects(page: Any) -> list[Any]:
    out: list[Any] = []
    for obj in page.Views:
        out += list(obj.Views) if str(obj.TypeId) == "TechDraw::DrawProjGroup" else [obj]
    return out


def apply_weights(page: Any) -> None:
    """ISO 128 line weights on TechDraw's own objects (they live on the view providers, so only in the GUI): the visible
    outline of a view is thick (0.5 mm); hidden, smooth and iso lines and every dimension line are thin -- the same
    hierarchy the PDF has."""
    for obj in _view_objects(page):
        vo = getattr(obj, "ViewObject", None)
        if vo is None:
            continue
        for prop, width in (
            ("LineWidth", sm.THICK_MM if "Dim" not in str(obj.TypeId) else sm.THIN_MM),
            ("HiddenWidth", sm.THIN_MM),
            ("IsoWidth", sm.HAIR_MM),
            ("ExtraWidth", sm.THIN_MM),
        ):
            if hasattr(vo, prop) and abs(float(getattr(vo, prop)) - width) > 1e-6:
                setattr(vo, prop, width)
        if (
            "Dim" in str(obj.TypeId)
            and hasattr(vo, "Fontsize")
            and abs(float(vo.Fontsize) - sheet_native.FONT_MM) > 1e-6
        ):
            vo.Fontsize = sheet_native.FONT_MM  # the 3.5 mm of ISO 3098, as every other text of the sheet


def line_weights(page: Any) -> dict[str, float]:
    """The weights now on the page's native objects (GUI only): ``outline`` of the views, ``thin`` of their hidden
    lines, ``dimension`` of the dimension objects; empty without a window."""
    found: dict[str, float] = {}
    for obj in _view_objects(page):
        vo = getattr(obj, "ViewObject", None)
        if vo is None or not hasattr(vo, "LineWidth"):
            continue
        if "Dim" in str(obj.TypeId):
            found["dimension"] = float(vo.LineWidth)
        elif hasattr(vo, "HiddenWidth"):
            found["outline"] = float(vo.LineWidth)
            found["thin"] = float(vo.HiddenWidth)
    return found


# --- the check: the window against the sheet model -----------------------------------------------------------------


def _svg_facts(svg: str) -> tuple[float, float, float, float, Counter[str]]:
    """(viewBox x, y, width, height, the texts) of a symbol's SVG."""
    root = ET.fromstring(svg.encode("utf-8"))  # noqa: S314 - our own text
    x, y, w, h = (float(v) for v in root.attrib["viewBox"].split())
    texts = Counter("".join(t.itertext()) for t in root.iter(f"{SVG_NS}text"))
    return x, y, w, h, texts


def _number(text: str) -> float | None:
    found = re.search(r"-?\d+(?:[.,]\d+)?", text)
    return float(found.group(0).replace(",", ".")) if found else None


def _compare_overall(page: Any, held: list[su.Unit]) -> list[str]:
    """TechDraw's overall dimensions against what the sheet draws: the label where the sheet puts it, and the value
    TechDraw MEASURES in its own view the one the sheet prints (an independent check of both)."""
    problems: list[str] = []
    wanted = {name: info for u in held for name, info in (u.native or {}).items()}
    present = natives(page)
    for key in sorted(present):
        if key.partition(":")[2] not in wanted:
            problems.append(f"{key}: on the page but the sheet does not draw it")
    for name, info in wanted.items():
        obj = present.get(f"overall:{name}")
        if obj is None:
            continue
        x, y = _label_place(page, obj, info)
        if abs(float(obj.X) - x) > POSITION_TOL_MM or abs(float(obj.Y) - y) > POSITION_TOL_MM:
            problems.append(
                f"overall {name}: TechDraw's dimension is at ({float(obj.X):.2f}, {float(obj.Y):.2f}) of its view, "
                f"the sheet draws it at ({x:.2f}, {y:.2f})"
            )
        measured, printed = _number(str(obj.getText())), _number(str(info["text"]))
        if measured is None or printed is None or abs(measured - printed) > MEASURE_TOL_MM:
            problems.append(
                f"overall {name}: TechDraw measures {obj.getText()!r} and the sheet prints {info['text']!r}"
            )
    return problems


CROSS_TOL_MM = 0.01
_PATH = re.compile(r'<path d="M ([-\d.]+) ([-\d.]+) L ([-\d.]+) ([-\d.]+)"')


def crosses(lines: list[tuple[float, float, float, float]]) -> list[tuple[float, float]]:
    """The centres of the crosses made of one horizontal and one vertical line that share their middle (a centre mark)."""
    horizontal = [((a + c) / 2, b) for a, b, c, d in lines if abs(b - d) < 1e-6 and abs(a - c) > 1e-6]
    vertical = [(a, (b + d) / 2) for a, b, c, d in lines if abs(a - c) < 1e-6 and abs(b - d) > 1e-6]
    return [h for h in horizontal if any(abs(h[0] - v[0]) < 1e-3 and abs(h[1] - v[1]) < 1e-3 for v in vertical)]


def page_crosses(page: Any, page_h: float) -> list[tuple[float, float]]:
    """The centre marks the TechDraw objects of ``page`` draw, in page mm where the window puts them: the lines of the
    centre layer of each symbol, moved by where the object sits against the middle of its SVG."""
    found: list[tuple[float, float]] = []
    for objects in owned(page).values():
        for obj in objects:
            if str(obj.TypeId) != SYMBOL:
                continue
            svg = str(obj.Symbol)
            x, y, w, h, _ = _svg_facts(svg)
            dx, dy = float(obj.X) - (x + w / 2), float(obj.Y) - (page_h - (y + h / 2))
            group = re.search(rf'<g id="layer-{sm.AXES}".*?</g>', svg, re.S)
            if group is None:
                continue
            lines = [
                (float(a) + dx, page_h - float(b) + dy, float(c) + dx, page_h - float(d) + dy)
                for a, b, c, d in _PATH.findall(group.group(0))
            ]
            found += crosses(lines)
    return found


def compare(page: Any, units: list[su.Unit], page_h: float) -> list[str]:
    """What differs between the objects of ``page`` and the sheet model ``units`` (empty: the window shows what the PDF
    shows): a unit with no object, an object with no unit, a symbol not centred where its entities sit, one whose size
    is not the size of its content, one whose texts are not the texts of the sheet."""
    problems: list[str] = []
    have = owned(page)
    units, held = _split_native(page, units)
    problems += _compare_overall(page, held)
    for key in sorted(set(have) - {u.key for u in units}):
        problems.append(f"{key}: on the page but not on the sheet")
    for whole in units:
        objects = have.get(whole.key)
        if not objects:
            problems.append(f"{whole.key}: on the sheet but not on the page")
            continue
        native_here = sheet_native.made(whole, objects)
        if native_here:
            problems += sheet_native.compare_unit(page, whole, objects)
        shown = symbol_unit(whole, native_here)
        if shown is None:
            continue
        unit = shown
        if not _symbols(objects):
            problems.append(f"{unit.key}: on the sheet but not on the page")
            continue
        for obj in _symbols(objects):
            rect = su.padded(unit.rect)
            x, y, w, h, texts = _svg_facts(str(obj.Symbol))
            # the drawing is where the sheet draws it when the SVG covers the unit's box and the object sits at the
            # middle of the SVG (the window puts the middle of the SVG at X, Y)
            mid = (x + w / 2, page_h - (y + h / 2))
            if abs(float(obj.X) - mid[0]) > POSITION_TOL_MM or abs(float(obj.Y) - mid[1]) > POSITION_TOL_MM:
                problems.append(
                    f"{unit.key}: the page object is at ({float(obj.X):.2f}, {float(obj.Y):.2f}) but its drawing is "
                    f"centred on ({mid[0]:.2f}, {mid[1]:.2f})"
                )
            if (
                x > rect[0] + SIZE_TOL_MM
                or x + w < rect[2] - SIZE_TOL_MM
                or page_h - y < rect[3] - SIZE_TOL_MM
                or page_h - (y + h) > rect[1] + SIZE_TOL_MM
            ):
                problems.append(f"{unit.key}: the page object does not cover the place the sheet draws it in")
            guides = guides_of(page, {unit.key})
            if not _clear(float(obj.Y), guides[1]):
                problems.append(f"{unit.key}: too close to a view origin: the window would pull it out of place")
            if texts != Counter(unit.texts):
                problems.append(
                    f"{unit.key}: its texts differ from the sheet's: {sorted(texts)} against {sorted(unit.texts)}"
                )
    return problems
