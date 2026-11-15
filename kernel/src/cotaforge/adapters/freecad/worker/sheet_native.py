# ruff: noqa: E501
"""The TechDraw objects an item is on the page: native dimensions (``DrawViewDimension``) and annotations
(``DrawViewAnnotation``) that FreeCAD measures, paints and exports itself, so the engineer can open the page and edit them
(no FreeCAD import except at run time, through the page's own objects).

An item says WHAT it asks of TechDraw (``sheet_units.NativeDim`` / ``NativeNote``: the view, what to measure there, where
the text goes, the value the model says it must come out as); this module makes the objects, puts them where the sheet
puts them and checks that TechDraw measures the model's value. What FreeCAD cannot do is not done here: a unit with a
native that cannot be made (an edge the view does not have, a value TechDraw measures differently) stays a symbol of our
own drawing (``sheet_mirror``), and ``review`` says so.

CONTRACT with whatever reads the page (selection in the drawing): every object made for an item carries the App string
properties ``CotaforgeItem`` (the item id; ``view:front`` ... for the views, ``threads`` / ``iso286`` for what no item
owns) and ``CotaforgeSheet`` (the sheet name), in the group ``Cotaforge``. The views of a sheet are labelled
``View_front``, ``View_top``, ``View_right`` and ``View_iso`` (``VIEW_LABELS``), which does not change while the sheet
lives; the key ``CotaforgeKey`` is the unit the object belongs to (the item id too, for objects of items).
"""

import json
import math
from dataclasses import dataclass, field
from typing import Any

import FreeCAD as App
import gui
import sheet_notefit
import sheet_symbols
import sheet_units as su
import sheet_weights as sw
from sheet_draw import lifted_label

PROP_ITEM = "CotaforgeItem"
PROP_SHEET = "CotaforgeSheet"
PROP_SIG = "CotaforgeSig"
PROP_VIEWKEY = "CotaforgeViewKey"  # on a view: front / top / right / isometric, or the name of a section, detail or aux view
PROP_DERIVED = "CotaforgeDerived"  # on a view the sheet made from an item: section | detail | aux
PROP_PARENT = "CotaforgeParent"  # on that view: the name of the view object it hangs from
PROP_SLOFF = "CotaforgeSectionLineOff"  # on a parent view: the sheet switched TechDraw's own section line off (it switches it on again)
NOTE_BLOCK_MM = 175.0  # the width of the text block of every note line (more when a line is longer)
NOTE_BLOCK_FACTOR = 1.15
NOTE_BLOCK_PAD_MM = 4.0
NOTE_FIT_PASSES = 4  # a note line is moved at most this many times to put its painted left edge on the block's
NOTE_FIT_TOL_MM = 0.5  # a line is judged misplaced from this far off its place
NOTE_MOVE_MM = 0.3  # and moved only when it is this far off: every move risks the window pulling it onto the line next to it
SYMBOL = "TechDraw::DrawViewSymbol"
GROUP = "Cotaforge"
VIEW_LABELS = {"front": "View_front", "top": "View_top", "right": "View_right", "isometric": "View_iso"}
DIM_TYPES = {"diameter": "Diameter", "radius": "Radius", "angle": "Angle"}
MATCH_TOL = 0.03  # model mm: an edge or vertex of a view is the one asked for
MOVE_TOL_MM = 0.05
MEASURED_SPEC = "%.2w"  # TechDraw's format of the measured value: two decimals at most, no trailing zeros, no unit
FONT_MM = 3.5  # ISO 3098 height of the text of a dimension on an A3 / A4 sheet
GLYPH_LIFT = 0.4  # the middle of an annotation line sits this fraction of its size above the baseline (measured)
ANNOTATION_FONT = "osifont"

DimType = str


@dataclass
class Built:
    """What making a unit's native objects gave: the objects (every one tagged), and why it failed when it did."""

    objects: list[Any] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def tag(obj: Any, key: str, item: str, sheet: str) -> None:
    """Put the contract's properties on ``obj``: the sheet, the item when there is one (``CotaforgeItem``: what the
    selection reads; the units that are no item -- threads, the ISO 286 note -- carry none) and the unit key, the one thing
    the mirror finds an object by."""
    for name, value, doc in (
        (PROP_ITEM, item, "Item of the sheet this object belongs to"),
        (PROP_SHEET, sheet, "Sheet (page) this object belongs to"),
    ):
        if not value:
            continue
        if name not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", name, GROUP, doc)
            obj.setEditorMode(name, 1)
        setattr(obj, name, value)
    if "CotaforgeKey" not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", "CotaforgeKey", GROUP, "Sheet unit this object mirrors")
        obj.setEditorMode("CotaforgeKey", 1)
    obj.CotaforgeKey = key


def tag_view(obj: Any, item: str, sheet: str) -> None:
    """``CotaforgeSheet`` on a view of the sheet (a view is no item and no unit the mirror replaces; ``item`` is only
    the name it is known by)."""
    if PROP_SHEET not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", PROP_SHEET, GROUP, "Sheet (page) this object belongs to")
        obj.setEditorMode(PROP_SHEET, 1)
    setattr(obj, PROP_SHEET, sheet)
    if item.startswith("view:"):  # which view of the sheet it is (a label is made unique per document: it cannot say)
        if PROP_VIEWKEY not in obj.PropertiesList:
            obj.addProperty("App::PropertyString", PROP_VIEWKEY, GROUP, "View of the sheet this object is")
            obj.setEditorMode(PROP_VIEWKEY, 1)
        setattr(obj, PROP_VIEWKEY, item.partition(":")[2])


def _set_sig(obj: Any, signature: str) -> None:
    if PROP_SIG not in obj.PropertiesList:
        obj.addProperty("App::PropertyString", PROP_SIG, GROUP, "What the sheet asked this object to be")
        obj.setEditorMode(PROP_SIG, 1)
    setattr(obj, PROP_SIG, signature)


# --- the views --------------------------------------------------------------------------------------------------------


def page_position(page: Any, view: Any) -> tuple[float, float]:
    """Centre of a view on the page (TechDraw coordinates, y up): a projection item sits relative to its group."""
    if str(view.TypeId) == "TechDraw::DrawProjGroupItem":
        for obj in page.Views:
            if str(obj.TypeId) == "TechDraw::DrawProjGroup" and view.Name in [item.Name for item in obj.Views]:
                return float(obj.X) + float(view.X), float(obj.Y) + float(view.Y)
    return float(view.X), float(view.Y)


def view_object(page: Any, name: str) -> Any | None:
    """The TechDraw view of the sheet called ``name`` (front, top, right, isometric), or ``None``."""
    wanted = {"front": "Front", "top": "Top", "right": "Right"}.get(name)
    for obj in page.Views:
        if wanted is not None and str(obj.TypeId) == "TechDraw::DrawProjGroup":
            for item in (
                obj.Views
            ):  # by the key the sheet tagged it with: the anchor of a sheet without a front view is another
                if str(getattr(item, PROP_VIEWKEY, "")) == name:
                    return item
            for item in obj.Views:
                if str(item.Type) == wanted and PROP_VIEWKEY not in item.PropertiesList:
                    return item
        if (
            name == "isometric"
            and str(obj.TypeId) == "TechDraw::DrawViewPart"
            and str(obj.Label) == VIEW_LABELS["isometric"]
        ):
            return obj
    for obj in derived_views(page):
        if str(getattr(obj, PROP_VIEWKEY, "")) == name:
            return obj
    return None


def derived_views(page: Any) -> list[Any]:
    """The sections, details and auxiliary views the sheet made on ``page`` (TechDraw's own view objects)."""
    return [obj for obj in page.Views if PROP_DERIVED in getattr(obj, "PropertiesList", ())]


def view_names(page: Any) -> list[str]:
    """The names of the views of ``page`` that carry cosmetic edges and dimensions: the standard ones and the derived ones."""
    return [*VIEW_LABELS, *[str(getattr(obj, PROP_VIEWKEY, "")) for obj in derived_views(page)]]


def _scan(getter: Any) -> list[Any]:
    found: list[Any] = []
    index = 0
    while index < 2000:
        try:
            found.append(getter(index))
        except Exception:  # noqa: BLE001 - TechDraw raises "Wrong ... index" past the last one
            break
        index += 1
    return found


class Frame:
    """One view as the references need it: where it is on the page, its scale and its edges and vertices (the view's own
    coordinates: model mm, y up)."""

    def __init__(self, page: Any, view: Any) -> None:
        self.view = view
        self.x, self.y = page_position(page, view)
        self.scale = float(view.getScale())
        self._edges: list[Any] | None = None
        self._vertices: list[Any] | None = None

    def local(self, p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] - self.x) / self.scale, (p[1] - self.y) / self.scale

    @property
    def edges(self) -> list[Any]:
        if self._edges is None:
            self._edges = _scan(self.view.getEdgeByIndex)
        return self._edges

    @property
    def vertices(self) -> list[Any]:
        if self._vertices is None:
            self._vertices = _scan(self.view.getVertexByIndex)
        return self._vertices

    def refresh(self) -> None:
        self._edges = self._vertices = None

    def vertex_at(self, local: tuple[float, float]) -> str | None:
        for index, v in enumerate(self.vertices):
            if math.hypot(float(v.X) - local[0], float(v.Y) - local[1]) <= MATCH_TOL:
                return f"Vertex{index}"
        return None

    def circle_at(self, local: tuple[float, float], radius: float) -> str | None:
        for index, e in enumerate(self.edges):
            curve = e.Curve
            if type(curve).__name__ != "Circle":
                continue
            c = curve.Center
            if (
                math.hypot(float(c.x) - local[0], float(c.y) - local[1]) <= MATCH_TOL
                and abs(float(curve.Radius) - radius) <= MATCH_TOL
            ):
                return f"Edge{index}"
        return None

    def line_along(self, p0: tuple[float, float], p1: tuple[float, float]) -> str | None:
        """The longest straight edge lying on the infinite line through ``p0`` and ``p1`` (local coordinates)."""
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy) or 1.0
        best: tuple[float, str] | None = None
        for index, e in enumerate(self.edges):
            if type(e.Curve).__name__ != "Line":
                continue
            a, b = e.valueAt(e.FirstParameter), e.valueAt(e.LastParameter)
            off = [abs((q.x - p0[0]) * dy - (q.y - p0[1]) * dx) / length for q in (a, b)]
            if max(off) <= MATCH_TOL and (best is None or e.Length > best[0]):
                best = (float(e.Length), f"Edge{index}")
        return None if best is None else best[1]


# --- making the objects ----------------------------------------------------------------------------------------------


def _cosmetic_points(frames: dict[str, Frame], dims: list[su.NativeDim]) -> int:
    """Make the cosmetic vertices the ``point`` references need (a vertex of the view already there -- a hole centre,
    a corner -- or one made before is used as is); how many were made."""
    made = 0
    for dim in dims:
        frame = frames[dim.view]
        for ref in dim.refs:
            if ref.kind != "point":
                continue
            local = frame.local((ref.values[0], ref.values[1]))
            if frame.vertex_at(local) is None:
                frame.view.makeCosmeticVertex(App.Vector(local[0], local[1], 0))
                made += 1
    return made


def _references(frames: dict[str, Frame], dim: su.NativeDim) -> list[tuple[Any, str]]:
    frame = frames[dim.view]
    refs: list[tuple[Any, str]] = []
    for ref in dim.refs:
        name: str | None
        if ref.kind == "point":
            name = frame.vertex_at(frame.local((ref.values[0], ref.values[1])))
        elif ref.kind == "circle":
            cx, cy = frame.local((ref.values[0], ref.values[1]))
            name = frame.circle_at((cx, cy), ref.values[2] / frame.scale)
        else:
            p0 = frame.local((ref.values[0], ref.values[1]))
            p1 = frame.local((ref.values[2], ref.values[3]))
            name = frame.line_along(p0, p1)
        if name is None:
            msg = f"the {dim.view} view has no {ref.kind} at ({ref.values[0]:.2f}, {ref.values[1]:.2f}) mm of the page for the dimension {dim.name}"
            raise LookupError(msg)
        refs.append((frame.view, name))
    return refs


def _style(obj: Any, dim: su.NativeDim) -> None:
    """The look of a dimension in the window and the PDF: the 3.5 mm ISO 3098 text; a diameter or radius with its text on a
    shelf (ISO 129-1 'referencing'). GUI only: the view providers do not exist headless."""
    vo = getattr(obj, "ViewObject", None)
    if vo is None:
        return
    if hasattr(vo, "Fontsize"):
        vo.Fontsize = FONT_MM
    if dim.leader and hasattr(vo, "StandardAndStyle"):
        vo.StandardAndStyle = "ISO Referencing"
    if dim.arrows != "auto" and hasattr(
        vo, "FlipArrowheads"
    ):  # a view-provider property of the dimension (TechDrawGui)
        vo.FlipArrowheads = dim.arrows == "outside"


def _label_of(dim: su.NativeDim) -> tuple[float, float]:
    """The label to give TechDraw's dimension object: a horizontal or vertical distance gets it moved off its line, so that the
    dimension line is where the sheet drew it (``sheet_draw.lifted_label``); the others are placed by their label as it is."""
    if dim.kind not in DIM_TYPES and dim.orient in ("h", "v"):
        return lifted_label(dim.orient, (float(dim.label[0]), float(dim.label[1])))
    return float(dim.label[0]), float(dim.label[1])


def _make_dim(
    fc_doc: Any,
    page: Any,
    frames: dict[str, Frame],
    dim: su.NativeDim,
    key: str,
    sheet: str,
    first: bool = False,
) -> Any:
    refs = _references(frames, dim)
    frame = frames[dim.view]
    obj = fc_doc.addObject("TechDraw::DrawViewDimension", _object_name(page, key, "" if first else dim.name))
    kind = DIM_TYPES.get(dim.kind) or {"h": "DistanceX", "v": "DistanceY", "a": "Distance"}[dim.orient]
    obj.Type = kind
    obj.References2D = refs
    page.addView(obj)
    obj.MeasureType = "Projected"
    obj.Label = f"{key} {dim.name}"
    if dim.text is not None:
        obj.Arbitrary = True
        obj.FormatSpec = dim.text
    if dim.tol is not None:
        obj.EqualTolerance = False
        obj.OverTolerance = float(dim.tol[0])
        obj.UnderTolerance = float(dim.tol[1])
        if dim.tol_text is not None:  # the deviations in mm as the sheet writes them (TechDraw's own would be µm)
            obj.ArbitraryTolerances = True
            obj.FormatSpecOverTolerance = dim.tol_text[0]
            obj.FormatSpecUnderTolerance = dim.tol_text[1]
        if dim.suffix:
            obj.FormatSpec = f"{MEASURED_SPEC} {dim.suffix}"
    if dim.exact:
        obj.TheoreticalExact = True
    label = _label_of(dim)
    obj.X = float(label[0]) - frame.x
    obj.Y = float(label[1]) - frame.y
    tag(obj, key, dim.item, sheet)
    return obj


def _object_name(page: Any, key: str, part: str) -> str:
    import re  # noqa: PLC0415

    return re.sub(r"[^A-Za-z0-9_]", "_", f"{page.Name}_{key}" + (f"_{part}" if part else ""))


def _text_width(text: str, size: float) -> float:
    """The width of ``text`` set in the annotation font at ``size`` mm: measured with Qt in the window, estimated headless
    (nothing is painted there)."""
    return gui.text_width_mm(text, size, ANNOTATION_FONT)


def _make_note(fc_doc: Any, page: Any, note: su.NativeNote, key: str, sheet: str) -> list[Any]:
    """One ``DrawViewAnnotation`` per line of the note: TechDraw spaces the lines of ONE annotation by 2.3 times their size
    (``LineSpace`` below 100 % cuts the glyphs) and the sheet sets its notes 5 mm apart, so each line is its own
    object, centred on the middle of its glyphs."""
    objects: list[Any] = []
    # every line of a note is a text block of the SAME width (wider than any line): TechDraw sets the text from the left of
    # the block, so the left edge of a line is where the block starts whatever the line says, and every note line has the
    # same X. The window pulls an object whose X is within a few mm of another one onto it (measured in 1.1.3), which
    # moved lines of different widths by up to 2 mm
    longest = max((_text_width(line, note.size) for line in note.lines), default=0.0)
    block = max(NOTE_BLOCK_MM, longest * NOTE_BLOCK_FACTOR + NOTE_BLOCK_PAD_MM) if note.fit else longest * 1.5 + 6.0  # a loose label: the room its words take, with a margin so TechDraw does not wrap them
    for index, line in enumerate(note.lines):
        obj = fc_doc.addObject("TechDraw::DrawViewAnnotation", _object_name(page, key, f"{note.name}{index + 1}"))
        page.addView(obj)
        obj.Text = [line]
        obj.TextSize = float(note.size)
        obj.MaxWidth = block
        obj.Label = f"{key} {note.name}{index + 1}"
        obj.X = note.at[0] + block / 2
        obj.Y = note.at[1] - note.size - index * note.step + GLYPH_LIFT * note.size
        tag(obj, key, note.item, sheet)
        objects.append(obj)
    return objects


def _line_of(obj: Any) -> str:
    """The text of the one line an annotation of a note holds."""
    text = list(obj.Text)
    return str(text[0]) if text else ""


def _fit_note(fc_doc: Any, page: Any, objects: list[Any], note: su.NativeNote) -> None:
    """Put each painted line where the sheet wants it: its left edge at ``note.at``. TechDraw centres a text on X, Y, so
    the width it was painted at (not a font estimate) moves the centre. Nothing is painted headless: the estimate stands."""
    gui.settle()  # the scene has the lines made a moment ago only after a few events: measuring before finds other texts
    for obj in objects:  # a line is found by what it SAYS: the text of a dimension at that height is not the line
        extent = gui.block_extent(page, float(obj.X), float(obj.Y), _line_of(obj))
        if extent is not None and abs(note.at[0] + extent[0] / 2 - float(obj.X)) > NOTE_MOVE_MM:
            # a line is moved only when it is off: the window pulls an object that is moved onto another one within a few mm
            # (a note line onto the next, 5 mm away: two lines on one place), so a line that is where it should be is not touched
            obj.X = note.at[0] + extent[0] / 2
    fc_doc.recompute()
    for obj in objects:  # then look where each line was PAINTED and move it the rest of the way (a few passes at most)
        box = None
        for _ in range(NOTE_FIT_PASSES):
            gui.settle()  # the scene repaints a moved item a few events later: measuring before gives the old place
            box = gui.block_box(page, float(obj.X), float(obj.Y), _line_of(obj))
            if box is None or abs(note.at[0] - box[0]) <= NOTE_MOVE_MM:
                break
            obj.X = float(obj.X) + (note.at[0] - box[0])
            fc_doc.recompute()
        off = sheet_notefit.misplaced(note.at[0], box, NOTE_FIT_TOL_MM)
        if off is not None:
            # the line did not come where the sheet wants it: never leave it where TechDraw put it (a line outside the frame
            # is a silent overflow); the unit goes back to the sheet's own text, which is inside the frame, and says why
            msg = f"the note line '{_line_of(obj).strip()[:40]}' was painted {off:+.1f} mm from where the sheet puts it"
            raise RuntimeError(msg)


LEADER_ENUMS = {"arrow": "Filled Arrow", "none": "None"}


def _leader_symbol(obj: Any, prop: str, wanted: str) -> None:
    """Set the end symbol of a leader by the name TechDraw lists for it (the list is the software's: no index is assumed)."""
    names = [str(n) for n in obj.getEnumerationsOfProperty(prop)]
    pick = next((n for n in names if n.replace(" ", "").casefold() == wanted.replace(" ", "").casefold()), None)
    if pick is not None:
        setattr(obj, prop, pick)


def _make_leader(fc_doc: Any, page: Any, frame: Frame, sym: su.NativeSymbol, key: str, sheet: str) -> Any:
    """The native ``DrawLeaderLine`` of a symbol: owned by the view (it moves with it), its first waypoint on the feature with the
    arrow, its last at the symbol. TechDraw puts the waypoints down-positive, relative to the leader's own place."""
    start = sym.leader[0]
    obj = fc_doc.addObject("TechDraw::DrawLeaderLine", _object_name(page, key, f"{sym.name}_leader"))
    page.addView(obj)
    obj.LeaderParent = frame.view
    obj.AutoHorizontal = False  # the last segment goes where the sheet put the symbol, not horizontal
    obj.Scalable = False
    obj.X = start[0] - frame.x
    obj.Y = start[1] - frame.y
    obj.WayPoints = [App.Vector(p[0] - start[0], -(p[1] - start[1]), 0) for p in sym.leader]
    _leader_symbol(obj, "StartSymbol", LEADER_ENUMS["arrow" if sym.arrow else "none"])
    _leader_symbol(obj, "EndSymbol", LEADER_ENUMS["none"])
    obj.Label = f"{key} {sym.name} leader"
    tag(obj, key, sym.item, sheet)
    return obj


def _make_symbol(fc_doc: Any, page: Any, frame: Frame | None, sym: su.NativeSymbol, key: str, sheet: str) -> list[Any]:
    obj = sheet_symbols.make_library_symbol(
        fc_doc,
        page,
        sym.file,
        list(sym.texts),
        _object_name(page, key, sym.name),
        None if frame is None else frame.view,
        sym.at,
        (0.0, 0.0) if frame is None else (frame.x, frame.y),
    )
    obj.Label = f"{key} {sym.name}"
    tag(obj, key, sym.item, sheet)
    made = [obj]
    if sym.leader and frame is not None:
        made.append(_make_leader(fc_doc, page, frame, sym, key, sheet))
    return made


def build(fc_doc: Any, page: Any, unit: su.Unit, sheet: str) -> Built:
    """Make the native objects of ``unit`` on ``page``; on any failure everything made is removed again and the reason
    is returned (the caller then draws the unit as a symbol)."""
    result = Built()
    dims = [n for n in unit.natives if isinstance(n, su.NativeDim)]
    notes = [n for n in unit.natives if isinstance(n, su.NativeNote)]
    symbols = [n for n in unit.natives if isinstance(n, su.NativeSymbol)]
    frames: dict[str, Frame] = {}
    try:
        for dim in [*dims, *symbols]:
            if dim.view and dim.view not in frames:
                view = view_object(page, dim.view)
                if view is None:
                    msg = f"the sheet has no {dim.view} view"
                    raise LookupError(msg)
                if not bool(getattr(view, "Visibility", True)):
                    # a section stands in place of this view (``in_place``): TechDraw draws no dimension on a hidden view,
                    # so the dimension is the sheet's own drawing over the section
                    msg = f"the {dim.view} view is not drawn (a section stands in its place)"
                    raise LookupError(msg)
                frames[dim.view] = Frame(page, view)
        marks = [n for n in unit.natives if isinstance(n, su.MARK_TYPES)]  # welds, balloons, tables, leaders (``sheet_native_marks``)
        for mark in marks:
            name = getattr(mark, "view", None)
            if name is not None and name not in frames:
                view = view_object(page, name)
                if view is None or not bool(getattr(view, "Visibility", True)):
                    msg = f"the sheet has no drawn {name} view for {unit.key}"
                    raise LookupError(msg)
                frames[name] = Frame(page, view)
        if _cosmetic_points(frames, dims):
            fc_doc.recompute()
            for frame in frames.values():
                frame.refresh()
        for number, dim in enumerate(dims):
            obj = _make_dim(fc_doc, page, frames, dim, unit.key, sheet, first=number == 0 and not notes)
            result.objects.append(obj)
        line_objects: list[tuple[su.NativeNote, list[Any]]] = []
        for note in notes:
            made_lines = _make_note(fc_doc, page, note, unit.key, sheet)
            line_objects.append((note, made_lines))
            result.objects.extend(made_lines)
        for sym in symbols:
            result.objects.extend(_make_symbol(fc_doc, page, frames.get(sym.view), sym, unit.key, sheet))
        if marks:
            import sheet_native_marks  # noqa: PLC0415 - it imports this module

            result.objects.extend(sheet_native_marks.make(fc_doc, page, frames, marks, unit.key, sheet, marks[0].item))
        if dims or notes or symbols or marks:
            fc_doc.recompute()
        for note, made_lines in line_objects:
            if note.fit:
                _fit_note(fc_doc, page, made_lines, note)
        for obj, dim in zip(result.objects[: len(dims)], dims, strict=True):
            _style(obj, dim)
            problem = measured_problem(obj, dim)
            if problem:
                result.problems.append(problem)
        for obj in result.objects:
            _set_sig(obj, signature(unit))
    except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
        result.problems.append(f"{type(exc).__name__}: {exc}")
    if result.problems:
        remove(fc_doc, result.objects)
        result.objects = []
    return result


def made(unit: su.Unit, objects: list[Any]) -> bool:
    """Whether TechDraw's own objects hold what ``unit`` asked for: it asked for none (only view properties) or the page
    has them. A unit that asked for objects and has none on the page was drawn as a symbol."""
    return bool(unit.natives) and (object_count(unit) == 0 or bool(held(objects)))


def held(objects: list[Any]) -> list[Any]:
    """The native objects among a unit's objects: the ones the sheet signed. A symbol of the library the sheet inserted is one; the
    symbol of the harness's own drawing a unit may also have (``sheet_mirror``) is not."""
    return [o for o in objects if str(o.TypeId) != SYMBOL or PROP_SIG in o.PropertiesList]


def sync(
    fc_doc: Any,
    page: Any,
    units: list[su.Unit],
    sheet: str,
    have: dict[str, list[Any]],
    report: Any,
    redo: set[str] | frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    """Make the native objects of ``units`` agree with what the sheet asks: a unit whose objects already carry its
    signature is left alone (adding one item touches one unit), the rest are made again. Returns, for the units whose
    objects could not be made, the reasons (those units stay symbols)."""
    failed: dict[str, list[str]] = {}
    for unit in units:
        count = object_count(unit) - (1 if su.view_of(unit) is not None else 0)  # a view is made by ``sync_views``
        if count == 0:  # only view properties (hidden lines) and cosmetic edges: no objects to make
            continue
        objects = [o for o in held(have.get(unit.key, [])) if alive(fc_doc, o) and PROP_DERIVED not in o.PropertiesList]
        wanted = signature(unit)
        dim_views = {n.view for n in unit.natives if isinstance(n, su.NativeDim)}
        if (
            objects
            and len(objects) == count
            and all(str(getattr(o, PROP_SIG, "")) == wanted for o in objects)
            and not dim_views & set(redo)  # the view it measures was made again: its references were to the old object
            and all(view_object(page, name) is not None for name in dim_views)
        ):
            report.unchanged.append(unit.key)
            continue
        remove(fc_doc, objects)
        built = build(fc_doc, page, unit, sheet)
        if built.ok:
            (report.updated if objects else report.created).append(unit.key)
        else:
            failed[unit.key] = built.problems
    return failed


HIDDEN_OWNER = "CotaforgeHidden"  # on a view: the sheet switched its hidden lines on (it switches them off again)


def sync_flags(page: Any, units: list[su.Unit]) -> None:
    """Set the properties of the views that items ask for (``NativeFlag``): the hidden lines of the views a ``view_lines``
    item shows them on are TechDraw's own, and go again when the item does. A view whose hidden lines the engineer
    switched on himself is not touched."""
    wanted = {n.view for u in units for n in u.natives if isinstance(n, su.NativeFlag) and n.prop == "HardHidden"}
    for name in ("front", "top", "right", "isometric"):
        view = view_object(page, name)
        if view is None:
            continue
        ours = HIDDEN_OWNER in view.PropertiesList
        if name in wanted and not bool(view.HardHidden):
            view.HardHidden = True
            if not ours:
                view.addProperty("App::PropertyBool", HIDDEN_OWNER, GROUP, "The sheet switched the hidden lines on")
                view.setEditorMode(HIDDEN_OWNER, 1)
                setattr(view, HIDDEN_OWNER, True)
        elif name not in wanted and ours and bool(view.HardHidden):
            view.HardHidden = False
            view.removeProperty(HIDDEN_OWNER)


def flag_problems(page: Any, unit: su.Unit) -> list[str]:
    """The view properties ``unit`` asks for that the page does not have."""
    problems: list[str] = []
    for n in unit.natives:
        if isinstance(n, su.NativeFlag) and n.prop == "HardHidden":
            view = view_object(page, n.view)
            if view is not None and not bool(view.HardHidden):
                problems.append(f"{unit.key}: the {n.view} view does not show its hidden lines (switched off by hand)")
    return problems


def compare_unit(page: Any, unit: su.Unit, objects: list[Any]) -> list[str]:
    """What differs between the native objects of ``unit`` on the page and what the sheet asks (empty: they agree): an
    object missing or moved or made for something else, and -- measured again now -- a dimension whose value is no longer
    the model's."""
    problems: list[str] = [*flag_problems(page, unit), *cosmetic_problems(page, unit)]
    mine = held(objects)
    wanted = signature(unit)
    if len(mine) != object_count(unit):
        problems.append(
            f"{unit.key}: the page has {len(mine)} native object(s) and the sheet asks for {object_count(unit)}"
        )
        return problems
    dims = [n for n in unit.natives if isinstance(n, su.NativeDim)]
    notes = [n for n in unit.natives if isinstance(n, su.NativeNote)]
    nv = su.view_of(unit)
    if nv is not None:
        problems += [p for obj in mine if PROP_DERIVED in obj.PropertiesList for p in view_problems(page, unit, nv, obj)]
    for obj in mine:
        if str(getattr(obj, PROP_SIG, "")) != wanted:
            problems.append(f"{unit.key}: {obj.Label} is not what the sheet asks for (out of date)")
    for obj, dim in zip([o for o in mine if "Dimension" in str(o.TypeId)], dims, strict=False):
        problem = measured_problem(obj, dim)
        if problem:
            problems.append(problem)
        view = view_object(page, dim.view)
        if view is not None:
            x, y = page_position(page, view)
            if dim.text is not None and str(obj.FormatSpec) != dim.text:
                problems.append(
                    f"{unit.key}: {obj.Label} has text {str(obj.FormatSpec)!r} edited by hand (the sheet says {dim.text!r})"
                )
            if dim.tol_text is not None and (
                not bool(obj.ArbitraryTolerances)
                or (str(obj.FormatSpecOverTolerance), str(obj.FormatSpecUnderTolerance)) != dim.tol_text
            ):
                problems.append(f"{unit.key}: {obj.Label} has its deviations edited by hand (the sheet says {' / '.join(dim.tol_text)})")
            if (
                abs(float(obj.X) - (_label_of(dim)[0] - x)) > MOVE_TOL_MM
                or abs(float(obj.Y) - (_label_of(dim)[1] - y)) > MOVE_TOL_MM
            ):
                problems.append(f"{unit.key}: {obj.Label} was moved by hand from where the sheet puts it")
    lines = [line for note in notes for line in note.lines]
    for obj, line in zip([o for o in mine if "Annotation" in str(o.TypeId)], lines, strict=False):
        if [str(t) for t in obj.Text] != [line]:
            problems.append(f"{unit.key}: {obj.Label} has text edited by hand (the sheet says {line!r})")
    return problems


def orphaned(unit: su.Unit, objects: list[Any]) -> list[Any]:
    """The symbols and leaders of ``unit`` whose view is gone: the engineer deleted the view they stood on, and TechDraw leaves them
    on the page with no view to follow (their place is relative to it, so they fall to the corner of the page)."""
    if not any(isinstance(n, su.NativeSymbol) and n.view for n in unit.natives):
        return []
    found: list[Any] = []
    for obj in held(objects):
        prop = "LeaderParent" if "Leader" in str(obj.TypeId) else "Owner" if str(obj.TypeId) == SYMBOL else ""
        if prop and getattr(obj, prop) is None:
            found.append(obj)
    return found


signature = su.signature
object_count = su.object_count
measured_problem = su.measured_problem


def remove(fc_doc: Any, objects: list[Any]) -> None:
    """Delete native objects. The cosmetic vertices they may have made stay in their views (they paint nothing): TechDraw
    numbers the vertices of a view by their order (measured in 1.1.3), so taking one out sends the later dimensions to
    other points; a sheet that is rebuilt starts clean."""
    import sheet_native_marks  # noqa: PLC0415 - it imports this module

    for obj in sorted(objects, key=lambda o: alive(fc_doc, o) and str(o.TypeId) == sheet_native_marks.LEADER_TYPE):  # a weld symbol goes before its leader
        if alive(fc_doc, obj):
            sheet_native_marks.remove_extras(fc_doc, obj)  # also the tiles of a weld symbol and the cells a table shows


def alive(fc_doc: Any, obj: Any) -> bool:
    """Whether ``obj`` is still in the document: removing a view takes its dimensions with it, and the name of an object that is
    gone is not a string any more."""
    name = getattr(obj, "Name", None)
    return isinstance(name, str) and fc_doc.getObject(name) is not None


# --- the views of the items: sections, details and auxiliary views ----------------------------------------------------------

DRAW_TYPES = {"section": "TechDraw::DrawViewSection", "detail": "TechDraw::DrawViewDetail", "aux": "TechDraw::DrawViewPart"}
PLACE_TOL_MM = 0.5  # how far the drawing of a view may sit from where the sheet puts it before the check says so
SIZE_TOL = 0.02  # and how much bigger or smaller (a fraction): TechDraw draws the same cut, the same edges
HATCH_FILE = ("Mod", "TechDraw", "PAT", "FCPAT.pat")  # TechDraw's own pattern library: lines, not an SVG tile (a PDF with a pattern fill is damaged)
HATCH_NAME = "Diagonal4"  # 45 degree thin lines


def _vector(values: Any) -> Any:
    return App.Vector(float(values[0]), float(values[1]), float(values[2]))


def _extent(view: Any) -> tuple[float, float, float, float] | None:
    """The box of the edges TechDraw drew in ``view``, in the view's own coordinates (scaled, y DOWN), or ``None`` when it drew none."""
    xs: list[float] = []
    ys: list[float] = []
    for edge in model_edges(view):
        box = edge.BoundBox
        xs += [box.XMin, box.XMax]
        ys += [box.YMin, box.YMax]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _page_box(page: Any, view: Any) -> tuple[float, float, float, float] | None:
    """The page rectangle (mm, y up) of what TechDraw drew in ``view``."""
    extent = _extent(view)
    if extent is None:
        return None
    x, y = page_position(page, view)
    return x + extent[0], y - extent[3], x + extent[2], y - extent[1]


def _hatch_file() -> str:
    import os  # noqa: PLC0415

    return os.path.join(str(App.getResourceDir()), *HATCH_FILE)


def _make_view(fc_doc: Any, page: Any, unit: su.Unit, nv: su.NativeView, sheet: str) -> Any:
    """The TechDraw view object ``nv`` asks for, hung from its parent view, drawn at the scale and in the frame the sheet gave
    it. Not placed yet: ``_place`` puts it where the sheet draws it, once TechDraw has computed what it shows."""
    parent = view_object(page, nv.parent)
    if parent is None:
        msg = f"the sheet has no {nv.parent} view for the {nv.kind} view {nv.name} to hang from"
        raise LookupError(msg)
    obj = fc_doc.addObject(DRAW_TYPES[nv.kind], _object_name(page, unit.key, ""))
    page.addView(obj)
    if nv.kind == "section":
        _hide_section_line(parent)
    if nv.kind != "aux":
        obj.BaseView = parent
    obj.Source = list(parent.Source)
    obj.ScaleType = "Custom"
    obj.Scale = float(nv.scale)
    if nv.kind == "section":
        assert nv.at is not None  # a section has its plane
        obj.SectionNormal = _vector(nv.direction)  # the viewer's side of the plane is the one cut away
        obj.SectionOrigin = _vector(nv.at)
        obj.XDirection = _vector(nv.xdir)
        obj.SectionSymbol = nv.letter
        obj.CutSurfaceDisplay = "PatHatch"
        obj.FileGeomPattern = _hatch_file()
        obj.NameGeomPattern = HATCH_NAME
    elif nv.kind == "detail":
        assert nv.anchor is not None  # a detail has its circle
        obj.AnchorPoint = App.Vector(float(nv.anchor[0]), float(nv.anchor[1]), 0.0)
        obj.Radius = float(nv.radius)
        obj.Reference = nv.letter
    else:
        obj.Direction = _vector(nv.direction)
        obj.XDirection = _vector(nv.xdir)
    obj.Label = nv.name
    obj.Caption = su.caption_of(nv.kind, nv.name)  # TechDraw's own text under the view (C - C: see caption_of)
    obj.X = (nv.box[0] + nv.box[2]) / 2
    obj.Y = (nv.box[1] + nv.box[3]) / 2
    tag(obj, unit.key, nv.item, sheet)
    tag_view(obj, f"view:{nv.name}", sheet)
    obj.addProperty("App::PropertyString", PROP_DERIVED, GROUP, "The kind of view the sheet made from an item")
    obj.setEditorMode(PROP_DERIVED, 1)
    setattr(obj, PROP_DERIVED, nv.kind)
    obj.addProperty("App::PropertyString", PROP_PARENT, GROUP, "The view this one hangs from")
    obj.setEditorMode(PROP_PARENT, 1)
    setattr(obj, PROP_PARENT, str(parent.Name))
    return obj


def _hide_section_line(parent: Any) -> None:
    """Switch off TechDraw's own section line on ``parent``: FreeCAD 1.1.3 exports a page with one as a damaged PDF (a stroke
    with no colour: measured on a view made by hand), so the cutting-plane line is made of cosmetic edges and annotations. It
    is a property of the view provider: there is none without a window, and the engineer who opens the file there has it on."""
    vo = getattr(parent, "ViewObject", None)
    if parent is None or vo is None or not hasattr(vo, "ShowSectionLine") or not bool(vo.ShowSectionLine):
        return
    vo.ShowSectionLine = False
    if PROP_SLOFF not in parent.PropertiesList:
        parent.addProperty("App::PropertyBool", PROP_SLOFF, GROUP, "The sheet switched TechDraw's section line off")
        parent.setEditorMode(PROP_SLOFF, 1)
    setattr(parent, PROP_SLOFF, True)


def _restore_section_lines(page: Any) -> None:
    """Switch TechDraw's section line on again on the views the sheet switched it off on and no section hangs from now."""
    hanging = {str(getattr(o, PROP_PARENT, "")) for o in derived_views(page) if str(getattr(o, PROP_DERIVED, "")) == "section"}
    for view in [*page.Views, *[i for g in page.Views if str(g.TypeId) == "TechDraw::DrawProjGroup" for i in g.Views]]:
        if PROP_SLOFF in view.PropertiesList and view.Name not in hanging:
            vo = getattr(view, "ViewObject", None)
            if vo is not None and hasattr(vo, "ShowSectionLine"):
                vo.ShowSectionLine = True
            view.removeProperty(PROP_SLOFF)


def _place(page: Any, obj: Any, nv: su.NativeView) -> None:
    """Put the drawing of ``obj`` where the sheet draws the view: TechDraw centres a view on its own bounding box (a section
    on the cut, an auxiliary view on the whole part), so the object is moved until that box is centred on the sheet's. A
    detail is centred on its circle. Then its position is an expression of its parent's (``Front.X + 120 mm``): move the
    parent and the view goes with it; to move the view by itself, clear the expression of X and Y."""
    target = ((nv.box[0] + nv.box[2]) / 2, (nv.box[1] + nv.box[3]) / 2)
    if nv.kind != "detail":
        extent = _extent(obj)
        if extent is None:
            msg = "TechDraw drew nothing in it"
            raise RuntimeError(msg)
        obj.X = target[0] - (extent[0] + extent[2]) / 2
        obj.Y = target[1] + (extent[1] + extent[3]) / 2
    parent = view_object(page, nv.parent)
    px, py = page_position(page, parent)
    group = next(
        (g for g in page.Views if str(g.TypeId) == "TechDraw::DrawProjGroup" and parent.Name in [i.Name for i in g.Views]), None
    )
    base_x = f"{parent.Name}.X" if group is None else f"{group.Name}.X + {parent.Name}.X"
    base_y = f"{parent.Name}.Y" if group is None else f"{group.Name}.Y + {parent.Name}.Y"
    obj.setExpression("X", f"{base_x} + {float(obj.X) - px:.4f} mm")
    obj.setExpression("Y", f"{base_y} + {float(obj.Y) - py:.4f} mm")


def view_problems(page: Any, unit: su.Unit, nv: su.NativeView, obj: Any) -> list[str]:
    """What differs between the view TechDraw drew and the box the sheet gave it: the drawing is not where the sheet draws
    it (moved by hand) or is not the size of the sheet's."""
    box = _page_box(page, obj)
    if box is None:
        return [f"{unit.key}: TechDraw's {nv.kind} view {nv.name} shows nothing"]
    problems: list[str] = []
    if su.tight_section_caption(nv.kind, nv.name, str(getattr(obj, "Caption", "") or "")):
        problems.append(f"{unit.key}: the caption of section {nv.name} has a bare hyphen: in osifont it is drawn over the first letter ({su.caption_of(nv.kind, nv.name)} reads right)")
    want = nv.box
    shift = ((box[0] + box[2]) / 2 - (want[0] + want[2]) / 2, (box[1] + box[3]) / 2 - (want[1] + want[3]) / 2)
    if nv.kind == "detail":
        return problems
    if abs(shift[0]) > PLACE_TOL_MM or abs(shift[1]) > PLACE_TOL_MM:
        problems.append(
            f"{unit.key}: the {nv.kind} view {nv.name} is {shift[0]:+.1f}, {shift[1]:+.1f} mm from where the sheet puts it (moved by hand)"
        )
    for have, expected, axis in ((box[2] - box[0], want[2] - want[0], "wide"), (box[3] - box[1], want[3] - want[1], "high")):
        if abs(have - expected) > max(PLACE_TOL_MM, SIZE_TOL * expected):
            problems.append(
                f"{unit.key}: TechDraw draws the {nv.kind} view {nv.name} {have:.1f} mm {axis} and the sheet {expected:.1f} mm"
            )
    return problems


def sync_views(
    fc_doc: Any, page: Any, units: list[su.Unit], sheet: str, have: dict[str, list[Any]], report: Any, wait: Any
) -> tuple[dict[str, list[str]], set[str]]:
    """Make the section, detail and auxiliary views of ``units`` agree with what the sheet asks (``NativeView``): a view that
    already carries the signature is left alone, the rest are made again. ``wait`` returns once TechDraw has computed the views
    (they are measured next). Returns the units whose view could not be made (they stay the sheet's own drawing, and say why)
    and the names of the views made new: the dimensions on them are made again, their references were to the old object."""
    failed: dict[str, list[str]] = {}
    fresh: set[str] = set()
    for obj in derived_views(page):  # the engineer took the parent away: the view that hung from it has nothing to show
        if fc_doc.getObject(str(getattr(obj, PROP_PARENT, ""))) is None and str(getattr(obj, PROP_PARENT, "")):
            remove(fc_doc, [obj])
    for unit in units:
        nv = su.view_of(unit)
        if nv is None:
            continue
        objects = [o for o in held(have.get(unit.key, [])) if alive(fc_doc, o) and PROP_DERIVED in o.PropertiesList]
        wanted = signature(unit)
        if len(objects) == 1 and str(getattr(objects[0], PROP_SIG, "")) == wanted:
            report.unchanged.append(unit.key)
            if nv.kind == "section":
                _hide_section_line(view_object(page, nv.parent))
            continue
        remove(fc_doc, objects)
        made = None
        try:
            made = _make_view(fc_doc, page, unit, nv, sheet)
            fc_doc.recompute()
            wait()
            _place(page, made, nv)
            fc_doc.recompute()
            wait()
            if _extent(made) is None:
                msg = "TechDraw drew nothing in it"
                raise RuntimeError(msg)  # noqa: TRY301
            _set_sig(made, wanted)
        except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
            if made is not None:
                remove(fc_doc, [made])
            failed[unit.key] = [f"{type(exc).__name__}: {exc}"]
            continue
        fresh.add(nv.name)
        (report.updated if objects else report.created).append(unit.key)
    _restore_section_lines(page)
    return failed, fresh


# --- cosmetic edges: the lines a unit draws ON a view (centre lines and marks, thread arcs, the parting line) ------------

PROP_COSMETIC = "CotaforgeCosmetic"  # on a view: the cosmetic edges the sheet made, {unit key: [signature, [geometry...]]}
ISO_STYLE = {"centre": 4, "thin": 1, "parting": 8, "thick": 1}  # ISO 128-20 line numbers (TechDraw's own list): 04 long dash dot, 01, 08
ISO_WIDTH_MM = {  # the table of ``sheet_weights`` (as the PDF the headless CAD writes)
    "centre": sw.weight(sw.CENTRE),
    "thin": sw.weight(sw.THREAD),
    "parting": sw.weight(sw.VISIBLE),
    "thick": sw.weight(sw.SECTION_LINE),
}
BLACK = (0.0, 0.0, 0.0, 1.0)
EDGE_TOL = 1e-4

Geometry = tuple[str, tuple[float, ...]]  # ("line", (x1, y1, x2, y2)) or ("circle", (cx, cy, r)), in the view's own units


def _stored(view: Any) -> dict[str, list[Any]]:
    """What the sheet made on ``view`` by unit key: ``[signature, [[kind, values], ...]]``. An edge is known by its geometry,
    not by its tag: TechDraw gives every cosmetic edge a new tag when a file is opened again (measured in 1.1.3)."""
    if PROP_COSMETIC not in view.PropertiesList:
        return {}
    try:
        data = json.loads(str(getattr(view, PROP_COSMETIC)) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _store(view: Any, data: dict[str, list[Any]]) -> None:
    if PROP_COSMETIC not in view.PropertiesList:
        view.addProperty("App::PropertyString", PROP_COSMETIC, GROUP, "Cosmetic edges the sheet made on this view")
        view.setEditorMode(PROP_COSMETIC, 1)
    setattr(view, PROP_COSMETIC, json.dumps(data, separators=(",", ":")))


def _cosmetic_geometry(cosmetic: Any) -> Geometry:
    """What a cosmetic edge is, as its own API gives it: ``line`` x1, y1, x2, y2 or ``circle`` (an arc too) cx, cy, r. The
    edge raises ``TypeError`` for what it is not (a line has no radius), so a failed ``Radius`` is what says it is a line."""
    try:
        radius = float(cosmetic.Radius)
    except (TypeError, AttributeError):
        a, b = cosmetic.Start, cosmetic.End
        return "line", (float(a.x), float(a.y), float(b.x), float(b.y))
    c = cosmetic.Center
    return "circle", (float(c.x), float(c.y), radius)


def _close(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return len(a) == len(b) and all(abs(x - y) < EDGE_TOL for x, y in zip(a, b, strict=True))


def _is_edge(cosmetic: Any, geometry: Geometry) -> bool:
    try:
        kind, values = _cosmetic_geometry(cosmetic)
    except Exception:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
        return False
    if kind != geometry[0]:
        return False
    if kind == "line":
        x1, y1, x2, y2 = geometry[1]
        return _close(values, (x1, y1, x2, y2)) or _close(values, (x2, y2, x1, y1))
    return _close(values, geometry[1])


def _find(view: Any, geometry: Geometry) -> Any | None:
    """The cosmetic edge of ``view`` that has ``geometry`` (``None`` when it is gone: deleted by hand)."""
    for cosmetic in list(view.CosmeticEdges):
        if _is_edge(cosmetic, geometry):
            return cosmetic
    return None


def _drop(view: Any, specs: list[Any]) -> None:
    for kind, values in specs:
        found = _find(view, (kind, tuple(values)))
        if found is not None:
            try:
                view.removeCosmeticEdge(str(found.Tag))
            except Exception:  # noqa: BLE001 - the engineer deleted it already
                pass


def _local(frame: Frame, n: su.NativeCosmetic) -> Geometry:
    """``n`` in the view's own units (a cosmetic edge is kept unscaled, y up)."""
    if n.kind == "line":
        a, b = frame.local((n.values[0], n.values[1])), frame.local((n.values[2], n.values[3]))
        return "line", (a[0], a[1], b[0], b[1])
    c = frame.local((n.values[0], n.values[1]))
    return "circle", (c[0], c[1], n.values[2] / frame.scale)


def _make_cosmetic(frame: Frame, n: su.NativeCosmetic) -> Geometry:
    v = frame.view
    geometry = _local(frame, n)
    values = geometry[1]
    if n.kind == "line":
        tag = v.makeCosmeticLine(App.Vector(values[0], values[1], 0), App.Vector(values[2], values[3], 0))
    elif n.kind == "circle":
        tag = v.makeCosmeticCircle(App.Vector(values[0], values[1], 0), values[2])
    else:
        tag = v.makeCosmeticCircleArc(App.Vector(values[0], values[1], 0), values[2], n.values[3], n.values[4])
    v.getCosmeticEdge(tag).Format = {
        "style": ISO_STYLE[n.style],
        "weight": ISO_WIDTH_MM[n.style],
        "color": BLACK,
        "visible": True,
    }
    return geometry


def _reweigh(view: Any, natives: list[su.NativeCosmetic], specs: list[Any]) -> None:
    """Put the weights of the table on the cosmetic edges the sheet made earlier (a file saved before the table changed)."""
    for n, (kind, values) in zip(natives, specs, strict=False):
        found = _find(view, (kind, tuple(values)))
        fmt = dict(found.Format) if found is not None else {}
        if fmt and abs(float(fmt.get("weight", 0.0)) - ISO_WIDTH_MM[n.style]) > 1e-6:
            found.Format = {**fmt, "weight": ISO_WIDTH_MM[n.style]}


def sync_cosmetics(page: Any, units: list[su.Unit], made: list[str] | None = None) -> dict[str, list[str]]:
    """Make the cosmetic edges of the views agree with what the units ask: a unit whose edges on a view already carry the
    signature (and still exist) is left alone, the rest are made again; a unit that is gone takes its edges with it. The edges
    are the view's own: they move with it and go when it does. Returns, for the units that could not be made, the reasons
    (those units keep their symbol); ``made`` collects the keys of the units whose edges were made or taken off."""
    problems: dict[str, list[str]] = {}
    wanted: dict[str, dict[str, list[su.NativeCosmetic]]] = {}
    for unit in units:
        for n in unit.natives:
            if isinstance(n, su.NativeCosmetic):
                wanted.setdefault(n.view, {}).setdefault(unit.key, []).append(n)

    def touched(key: str) -> None:
        if made is not None and key not in made:
            made.append(key)

    for name in view_names(page):
        view = view_object(page, name)
        if view is None:
            continue
        stored = _stored(view)
        mine = wanted.get(name, {})
        if not stored and not mine:
            continue
        frame = Frame(page, view)
        changed = False
        for key in [k for k in stored if k not in mine]:
            _drop(view, stored.pop(key)[1])
            changed = True
            touched(key)
        for key, natives in mine.items():
            signature = su.cosmetic_signature(natives)
            old = stored.get(key)
            if old is not None and old[0] == signature and all(_find(view, (k, tuple(v))) is not None for k, v in old[1]):
                _reweigh(view, natives, old[1])
                continue
            if old is not None:
                _drop(view, old[1])
            specs: list[Any] = []
            try:
                for n in natives:
                    kind, values = _make_cosmetic(frame, n)
                    specs.append([kind, list(values)])
            except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
                _drop(view, specs)
                stored.pop(key, None)
                problems.setdefault(key, []).append(
                    f"the {name} view could not take its cosmetic lines ({type(exc).__name__}: {exc})"
                )
                changed = True
                continue
            stored[key] = [signature, specs]
            changed = True
            touched(key)
        if changed:
            _store(view, stored)
    return problems


def own_cosmetic_count(view: Any) -> int:
    """How many cosmetic edges of ``view`` the sheet made (the rest are the engineer's own)."""
    return sum(len(entry[1]) for entry in _stored(view).values())


def cosmetic_problems(page: Any, unit: su.Unit) -> list[str]:
    """The cosmetic edges ``unit`` asks for that the page does not have (deleted by hand, or never made)."""
    problems: list[str] = []
    by_view: dict[str, list[su.NativeCosmetic]] = {}
    for n in unit.natives:
        if isinstance(n, su.NativeCosmetic):
            by_view.setdefault(n.view, []).append(n)
    for name, natives in by_view.items():
        view = view_object(page, name)
        entry = _stored(view).get(unit.key) if view is not None else None
        if entry is None or entry[0] != su.cosmetic_signature(natives):
            problems.append(f"{unit.key}: the {name} view does not carry the {len(natives)} cosmetic line(s) the sheet draws")
        elif any(_find(view, (k, tuple(v))) is None for k, v in entry[1]):
            problems.append(f"{unit.key}: the {name} view lost a cosmetic line the sheet draws (deleted by hand)")
    return problems


def _same_edge(edge: Any, geometry: Geometry) -> bool:
    """Whether the visible edge ``edge`` is the one a cosmetic edge with ``geometry`` (scaled to the view) made. TechDraw hands
    out the shapes y DOWN while a cosmetic edge is y UP, so a mirrored y matches too."""
    kind, values = geometry
    curve = type(edge.Curve).__name__
    if kind == "line" and curve == "Line":
        ends = [(float(v.Point.x), float(v.Point.y)) for v in (edge.Vertexes[0], edge.Vertexes[-1])]
        a, b = (values[0], values[1]), (values[2], values[3])
        for sign in (1.0, -1.0):
            want = [(a[0], sign * a[1]), (b[0], sign * b[1])]
            for order in (want, want[::-1]):
                if all(math.hypot(p[0] - q[0], p[1] - q[1]) < EDGE_TOL for p, q in zip(ends, order, strict=True)):
                    return True
        return False
    if kind == "circle" and curve == "Circle":
        centre = edge.Curve.Center
        if abs(float(edge.Curve.Radius) - values[2]) >= EDGE_TOL:
            return False
        return any(
            math.hypot(float(centre.x) - values[0], float(centre.y) - sign * values[1]) < EDGE_TOL for sign in (1.0, -1.0)
        )
    return False


def model_edges(view: Any) -> list[Any]:
    """The visible edges of ``view`` that belong to the model: TechDraw's ``getVisibleEdges`` hands out the cosmetic edges
    too, and the ones the sheet made (centre lines, thread arcs) are not the part's outline: they are left out of what the
    sheet measures and draws from the view."""
    edges = list(view.getVisibleEdges())
    scale = float(view.getScale())
    for entry in _stored(view).values():
        for kind, values in entry[1]:
            geometry = (kind, tuple(v * scale for v in values))  # a cosmetic edge is kept unscaled; the view's edges are not
            for index in range(len(edges) - 1, -1, -1):  # cosmetic edges come after the part's own
                if _same_edge(edges[index], geometry):
                    del edges[index]
                    break
    return edges


def only_view_bound(unit: su.Unit) -> bool:
    """Whether everything ``unit`` is on the page lives on the views (hidden-line flags, cosmetic edges): it has no object of
    its own and, once those are made, no symbol either."""
    return bool(unit.natives) and object_count(unit) == 0 and not unit.residue


CIRCLE_POINTS = 48


def cosmetic_paths(page: Any, key: str) -> list[list[tuple[float, float]]]:
    """The cosmetic edges the sheet made for unit ``key`` as polylines in page mm (y up), for painting them when the engineer
    is pointed at the item: a line is its two ends, a circle or an arc the full circle."""
    paths: list[list[tuple[float, float]]] = []
    for name in view_names(page):
        view = view_object(page, name)
        entry = _stored(view).get(key) if view is not None else None
        if entry is None:
            continue
        frame = Frame(page, view)
        for kind, values in entry[1]:
            if kind == "line":
                a, b = values[0:2], values[2:4]
                paths.append([(frame.x + a[0] * frame.scale, frame.y + a[1] * frame.scale), (frame.x + b[0] * frame.scale, frame.y + b[1] * frame.scale)])
            else:
                cx, cy, r = values
                paths.append(
                    [
                        (
                            frame.x + (cx + r * math.cos(2 * math.pi * i / CIRCLE_POINTS)) * frame.scale,
                            frame.y + (cy + r * math.sin(2 * math.pi * i / CIRCLE_POINTS)) * frame.scale,
                        )
                        for i in range(CIRCLE_POINTS + 1)
                    ]
                )
    return paths
