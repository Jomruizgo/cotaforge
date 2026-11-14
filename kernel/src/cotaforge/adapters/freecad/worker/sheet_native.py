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
import sheet_units as su
from sheet_draw import lifted_label

PROP_ITEM = "CotaforgeItem"
PROP_SHEET = "CotaforgeSheet"
PROP_SIG = "CotaforgeSig"
PROP_VIEWKEY = "CotaforgeViewKey"  # on a view: front / top / right / isometric
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
    return None


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
    block = max(NOTE_BLOCK_MM, longest * NOTE_BLOCK_FACTOR + NOTE_BLOCK_PAD_MM)
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


def build(fc_doc: Any, page: Any, unit: su.Unit, sheet: str) -> Built:
    """Make the native objects of ``unit`` on ``page``; on any failure everything made is removed again and the reason
    is returned (the caller then draws the unit as a symbol)."""
    result = Built()
    dims = [n for n in unit.natives if isinstance(n, su.NativeDim)]
    notes = [n for n in unit.natives if isinstance(n, su.NativeNote)]
    frames: dict[str, Frame] = {}
    try:
        for dim in dims:
            if dim.view not in frames:
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
        if dims or notes:
            fc_doc.recompute()
        for note, made_lines in line_objects:
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
    """The native objects among a unit's objects (the symbol the unit may also have is not one)."""
    return [o for o in objects if str(o.TypeId) != SYMBOL]


def sync(
    fc_doc: Any, page: Any, units: list[su.Unit], sheet: str, have: dict[str, list[Any]], report: Any
) -> dict[str, list[str]]:
    """Make the native objects of ``units`` agree with what the sheet asks: a unit whose objects already carry its
    signature is left alone (adding one item touches one unit), the rest are made again. Returns, for the units whose
    objects could not be made, the reasons (those units stay symbols)."""
    failed: dict[str, list[str]] = {}
    for unit in units:
        if object_count(unit) == 0:  # only view properties (hidden lines): no objects to make
            continue
        objects = held(have.get(unit.key, []))
        wanted = signature(unit)
        if (
            objects
            and len(objects) == object_count(unit)
            and all(str(getattr(o, PROP_SIG, "")) == wanted for o in objects)
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


signature = su.signature
object_count = su.object_count
measured_problem = su.measured_problem


def remove(fc_doc: Any, objects: list[Any]) -> None:
    """Delete native objects. The cosmetic vertices they may have made stay in their views (they paint nothing): TechDraw
    numbers the vertices of a view by their order (measured in 1.1.3), so taking one out sends the later dimensions to
    other points; a sheet that is rebuilt starts clean."""
    for obj in objects:
        if fc_doc.getObject(obj.Name) is not None:
            fc_doc.removeObject(obj.Name)


# --- cosmetic edges: the lines a unit draws ON a view (centre lines and marks, thread arcs, the parting line) ------------

PROP_COSMETIC = "CotaforgeCosmetic"  # on a view: the cosmetic edges the sheet made, {unit key: [signature, [tags]]}
ISO_STYLE = {"centre": 4, "thin": 1, "parting": 8}  # ISO 128-20 line numbers (TechDraw's own list): 04 long dash dot, 01, 08
ISO_WIDTH_MM = {"centre": 0.25, "thin": 0.25, "parting": 0.5}  # the 0.5 mm group: wide 0.5, narrow 0.25 (as the PDF)
BLACK = (0.0, 0.0, 0.0, 1.0)


def _stored(view: Any) -> dict[str, list[Any]]:
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


def _tag_alive(view: Any, tag: str) -> bool:
    try:
        return view.getCosmeticEdge(tag) is not None
    except Exception:  # noqa: BLE001 - TechDraw raises when the tag is no more
        return False


def _drop(view: Any, tags: list[str]) -> None:
    for tag in tags:
        try:
            view.removeCosmeticEdge(tag)
        except Exception:  # noqa: BLE001 - the engineer deleted it already
            pass


def _make_cosmetic(frame: Frame, n: su.NativeCosmetic) -> str:
    v = frame.view
    if n.kind == "line":
        a, b = frame.local((n.values[0], n.values[1])), frame.local((n.values[2], n.values[3]))
        tag = v.makeCosmeticLine(App.Vector(a[0], a[1], 0), App.Vector(b[0], b[1], 0))
    elif n.kind == "circle":
        c = frame.local((n.values[0], n.values[1]))
        tag = v.makeCosmeticCircle(App.Vector(c[0], c[1], 0), n.values[2] / frame.scale)
    else:
        c = frame.local((n.values[0], n.values[1]))
        tag = v.makeCosmeticCircleArc(App.Vector(c[0], c[1], 0), n.values[2] / frame.scale, n.values[3], n.values[4])
    edge = v.getCosmeticEdge(tag)
    edge.Format = {
        "style": ISO_STYLE[n.style],
        "weight": ISO_WIDTH_MM[n.style],
        "color": BLACK,
        "visible": True,
    }
    return str(tag)


def sync_cosmetics(page: Any, units: list[su.Unit]) -> dict[str, list[str]]:
    """Make the cosmetic edges of the views agree with what the units ask: a unit whose edges on a view already carry the
    signature (and still exist) is left alone, the rest are made again; a unit that is gone takes its edges with it. The edges
    are the view's own: they move with it and go when it does. Returns, for the units that could not be made, the reasons
    (those units keep their symbol)."""
    problems: dict[str, list[str]] = {}
    wanted: dict[str, dict[str, list[su.NativeCosmetic]]] = {}
    for unit in units:
        for n in unit.natives:
            if isinstance(n, su.NativeCosmetic):
                wanted.setdefault(n.view, {}).setdefault(unit.key, []).append(n)
    for name in VIEW_LABELS:
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
            _drop(view, list(stored.pop(key)[1]))
            changed = True
        for key, natives in mine.items():
            signature = su.cosmetic_signature(natives)
            old = stored.get(key)
            if old is not None and old[0] == signature and all(_tag_alive(view, t) for t in old[1]):
                continue
            if old is not None:
                _drop(view, list(old[1]))
            tags: list[str] = []
            try:
                for n in natives:
                    tags.append(_make_cosmetic(frame, n))
            except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
                _drop(view, tags)
                stored.pop(key, None)
                problems.setdefault(key, []).append(
                    f"the {name} view could not take its cosmetic lines ({type(exc).__name__}: {exc})"
                )
                changed = True
                continue
            stored[key] = [signature, tags]
            changed = True
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
        elif not all(_tag_alive(view, t) for t in entry[1]):
            problems.append(f"{unit.key}: the {name} view lost a cosmetic line the sheet draws (deleted by hand)")
    return problems


def only_view_bound(unit: su.Unit) -> bool:
    """Whether everything ``unit`` is on the page lives on the views (hidden-line flags, cosmetic edges): it has no object of
    its own and, once those are made, no symbol either."""
    return bool(unit.natives) and object_count(unit) == 0 and not unit.residue


EDGE_TOL = 1e-4


def _same_edge(edge: Any, cosmetic: Any) -> bool:
    """Whether the visible edge ``edge`` is the one the cosmetic edge ``cosmetic`` of the sheet made (TechDraw hands both out
    together; the shapes it returns are y DOWN while a cosmetic edge is y UP, so a y that is mirrored matches too)."""
    curve = type(edge.Curve).__name__
    if hasattr(cosmetic, "Start") and curve == "Line":
        ends = [(float(v.Point.x), float(v.Point.y)) for v in (edge.Vertexes[0], edge.Vertexes[-1])]
        a, b = (float(cosmetic.Start.x), float(cosmetic.Start.y)), (float(cosmetic.End.x), float(cosmetic.End.y))
        for sign in (1.0, -1.0):
            want = [(a[0], sign * a[1]), (b[0], sign * b[1])]
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) < EDGE_TOL for p, q in zip(ends, want, strict=True)) or all(
                math.hypot(p[0] - q[0], p[1] - q[1]) < EDGE_TOL for p, q in zip(ends, reversed(want), strict=True)
            ):
                return True
        return False
    if hasattr(cosmetic, "Radius") and curve == "Circle":
        centre = edge.Curve.Center
        c = cosmetic.Center
        return abs(float(edge.Curve.Radius) - float(cosmetic.Radius)) < EDGE_TOL and (
            math.hypot(float(centre.x) - float(c.x), float(centre.y) - float(c.y)) < EDGE_TOL
            or math.hypot(float(centre.x) - float(c.x), float(centre.y) + float(c.y)) < EDGE_TOL
        )
    return False


def model_edges(view: Any) -> list[Any]:
    """The visible edges of ``view`` that belong to the model: TechDraw's ``getVisibleEdges`` hands out the cosmetic edges
    too, and the ones the sheet made (centre lines, thread arcs) are not the part's outline: they are left out of what the
    sheet measures and draws from the view."""
    edges = list(view.getVisibleEdges())
    for entry in _stored(view).values():
        for tag in entry[1]:
            try:
                cosmetic = view.getCosmeticEdge(tag)
            except Exception:  # noqa: BLE001 - deleted by hand
                continue
            for index in range(len(edges) - 1, -1, -1):  # cosmetic edges come after the part's own
                if _same_edge(edges[index], cosmetic):
                    del edges[index]
                    break
    return edges
