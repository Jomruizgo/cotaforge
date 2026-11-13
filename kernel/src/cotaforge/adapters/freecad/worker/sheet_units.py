"""The pieces of a composed sheet the CAD window mirrors, as plain data (no FreeCAD import, unit-testable on its own).

The compositor (``drawing.compose``) draws a whole sheet into ``sheet_model`` entities; the PDF, the PNG and the DXF are
written from them. The engineer watches the TechDraw sheet window while the agent builds the sheet, so every piece that
one item (or the thread arcs, the ISO 286 note, an ``hlr`` view) put on the sheet is also made as an object of the
TechDraw page (``sheet_mirror``): a ``Unit`` is that piece -- a key, a label and the entities it drew, at their place on
the page. The key is the item id (``h1``), ``view:front`` / ``view:top`` / ... for the views of an ``hlr`` sheet,
``threads`` and ``iso286`` for what no item owns.

"""

import json
import math
from dataclasses import dataclass, field
from typing import Any

from sheet_clip import arc_points
from sheet_model import DIMENSIONS, HIDDEN, TEXT, Arc, Circle, Entity, Glyph, Line, Polyline, Solid, Text, text_width

Rect = tuple[float, float, float, float]
Point = tuple[float, float]

KIND_ITEM = "item"
KIND_VIEW = "view"
KIND_EXTRA = "extra"
TEXT_SLACK = 1.12  # the real font may be wider than ``text_width`` estimates: the SVG fragment is cut at its box
MARGIN_MM = 1.5
KEY_THREADS = "threads"
NATIVE_LAYERS = frozenset({DIMENSIONS, TEXT})  # what TechDraw's own dimension / annotation objects draw


@dataclass(frozen=True)
class Ref2:
    """A place of a TechDraw view a native dimension measures from, in PAGE mm (y up): a ``point`` (x, y) -- a vertex of
    the view when one is there, else a cosmetic vertex made for it --, a ``circle`` (cx, cy, r: a circle or an arc, the
    edge of the view with that centre and radius) or a ``line`` (x0, y0, x1, y1: the straight edge along that line)."""

    kind: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class NativeDim:
    """One TechDraw dimension object an item asks for (``DrawViewDimension``): FreeCAD MEASURES ``refs`` in ``view``;
    ``expected`` is the model's value (mm, degrees for an angle) it must come out as, and the check that it does is the
    truth of a native dimension. ``text`` is the whole text when the item's callout is not the measured number
    (``Ø9 pasante`` and a counterbore, a fit) -- TechDraw's Arbitrary text --; ``None`` shows what TechDraw measures.
    ``label`` is where the text sits (page mm, y up) and so where the dimension line goes; ``tol`` is the (upper,
    lower) deviation TechDraw prints itself with the measured value; ``leader`` puts the text on a shelf (ISO 129-1
    referencing, for the diameters and radii whose text is long)."""

    item: str
    name: str
    kind: str  # distance | diameter | radius | angle
    view: str  # front | top | right | isometric
    refs: tuple[Ref2, ...]
    label: Point
    expected: float | None = None
    text: str | None = None
    orient: str = ""  # distance: h (measures along the page x) | v
    tol: tuple[float, float] | None = None
    leader: bool = False
    rect: Rect | None = None  # the room the label and its line take (page mm), for the collision check
    arrows: str = "auto"  # inside | outside: TechDraw flips the arrowheads to match; auto leaves them to TechDraw


@dataclass(frozen=True)
class NativeNote:
    """One ``DrawViewAnnotation``: ``lines`` of text whose top-left corner is at ``at`` (page mm, y up)."""

    item: str
    name: str
    lines: tuple[str, ...]
    at: Point
    size: float = 3.5
    step: float = 5.0  # between the baselines of two lines


@dataclass(frozen=True)
class NativeFlag:
    """A property TechDraw's own view has that does what the item draws: ``HardHidden`` shows the hidden edges of the
    view (the dashed lines of ``view_lines``) with TechDraw's own hidden-line style and width. No object is made: the
    view is the object, and the sheet switches the property on and off as the item comes and goes."""

    item: str
    view: str
    prop: str = "HardHidden"


Native = NativeDim | NativeNote | NativeFlag


@dataclass(frozen=True)
class Unit:
    """What one item (or one view, or the thread arcs) put on the sheet."""

    key: str
    label: str
    kind: str
    entities: tuple[Entity, ...]
    item_type: str = ""
    native: dict[str, Any] | None = field(default=None, compare=False)  # TechDraw's own objects hold this unit
    natives: tuple[Native, ...] = field(default=(), compare=False)  # TechDraw objects that replace its dimension lines

    @property
    def rect(self) -> Rect:
        found = bounds(self.entities)
        if found is None:
            msg = f"unit {self.key} has nothing to show"
            raise ValueError(msg)
        return found

    @property
    def centre(self) -> tuple[float, float]:
        x0, y0, x1, y1 = padded(self.rect)
        return (x0 + x1) / 2, (y0 + y1) / 2

    @property
    def texts(self) -> list[str]:
        return [e.text for e in self.entities if isinstance(e, Text)]

    @property
    def residue(self) -> tuple[Entity, ...]:
        """What the natives do not draw: with natives, the entities that are not dimension lines and texts (the centre
        marks of a hole callout); without, everything."""
        if not self.natives:
            return self.entities
        gone = NATIVE_LAYERS | ({HIDDEN} if any(isinstance(n, NativeFlag) for n in self.natives) else set())
        return tuple(e for e in self.entities if getattr(e, "layer", None) not in gone)


def text_rect(e: Text) -> Rect:
    """The box of a text as the SVG fragment must hold it (a little wide: the font decides at render time)."""
    width = text_width(e.text, e.height) * TEXT_SLACK
    x0 = e.x - (width / 2 if e.anchor == "center" else width if e.anchor == "right" else 0.0)
    descent = e.height * 0.3
    corners = [(x0, e.y - descent), (x0 + width, e.y - descent), (x0 + width, e.y + e.height), (x0, e.y + e.height)]
    if e.angle:
        rad = math.radians(e.angle)
        cos, sin = math.cos(rad), math.sin(rad)
        corners = [
            (e.x + (px - e.x) * cos - (py - e.y) * sin, e.y + (px - e.x) * sin + (py - e.y) * cos) for px, py in corners
        ]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _points(e: Entity) -> list[tuple[float, float]]:
    if isinstance(e, Line):
        return [(e.x1, e.y1), (e.x2, e.y2)]
    if isinstance(e, Polyline):
        return list(e.points)
    if isinstance(e, Arc):
        return arc_points(e.cx, e.cy, e.r, e.start, e.end)
    if isinstance(e, Circle):
        return [(e.cx - e.r, e.cy - e.r), (e.cx + e.r, e.cy + e.r)]
    if isinstance(e, Solid):
        return list(e.points)
    if isinstance(e, Glyph):
        return [p for stroke in e.strokes() for p in stroke.points]
    return []


def bounds(entities: Any) -> Rect | None:
    """The box of the entities on the page (texts by their estimated width), or ``None`` when there are none."""
    xs: list[float] = []
    ys: list[float] = []
    for e in entities:
        if isinstance(e, Text):
            x0, y0, x1, y1 = text_rect(e)
            xs += [x0, x1]
            ys += [y0, y1]
            continue
        for x, y in _points(e):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def padded(rect: Rect) -> Rect:
    """``rect`` with the margin an SVG fragment keeps around its content (strokes have width)."""
    return rect[0] - MARGIN_MM, rect[1] - MARGIN_MM, rect[2] + MARGIN_MM, rect[3] + MARGIN_MM


def collect(
    entities: list[Entity],
    items: list[dict[str, Any]],
    slices: dict[str, tuple[int, int]],
    statuses: dict[str, str],
    extras: dict[str, tuple[int, int]],
    views: dict[str, list[Entity]] | None,
    native: dict[str, dict[str, Any]] | None = None,
    natives: dict[str, list[Native]] | None = None,
) -> list[Unit]:
    """The units of a composed sheet, in the order they should stack on the page.

    ``slices`` maps an item id to the range of ``entities`` it drew; an item that failed (status ``error``) or drew
    nothing makes no unit. ``extras`` is the same for the pieces no item owns (``threads``). ``views`` are
    the views of an ``hlr`` sheet (they are not TechDraw objects there, so the window needs them). ``native`` maps the
    id of an item that TechDraw's own objects hold (the overall dimensions of a ``techdraw`` sheet) to what the
    mirror needs to put them where the sheet draws them. ``natives`` maps an item id to the TechDraw dimension and
    annotation objects it asks for (``NativeDim`` / ``NativeNote``)."""
    units: list[Unit] = []
    for name, group in (views or {}).items():
        if group:
            units.append(Unit(f"view:{name}", f"view {name}", KIND_VIEW, tuple(group)))
    if "threads" in extras:
        a, b = extras["threads"]
        if b > a:
            units.append(Unit(KEY_THREADS, "thread arcs", KIND_EXTRA, tuple(entities[a:b])))
    for item in items:
        item_id = str(item["id"])
        if statuses.get(item_id) == "error" or item_id not in slices:
            continue
        a, b = slices[item_id]
        if b <= a:
            continue
        held = (native or {}).get(item_id)
        units.append(
            Unit(
                item_id,
                f"{item_id} {item['type']}",
                KIND_ITEM,
                tuple(entities[a:b]),
                str(item["type"]),
                held,
                tuple((natives or {}).get(item_id, ())),
            )
        )
    return units


# --- what a unit's native objects must be (pure: the objects themselves are made in ``sheet_native``) -----------------

VALUE_TOL_MM = 0.011  # TechDraw measures the model's value to the digit the sheet prints
ANGLE_TOL_DEG = 0.06


def signature(unit: Unit) -> str:
    """The text that says what the natives of ``unit`` must be: when the objects of the unit already carry it, they are
    left alone (adding one item touches one unit)."""

    def dump(n: Native) -> list[Any]:
        if isinstance(n, NativeDim):
            return [
                "d",
                n.name,
                n.kind,
                n.view,
                [[r.kind, [round(v, 2) for v in r.values]] for r in n.refs],
                [round(v, 2) for v in n.label],
                None if n.expected is None else round(n.expected, 3),
                n.text,
                n.orient,
                n.tol,
                n.leader,
            ]
        if isinstance(n, NativeFlag):
            return ["f", n.view, n.prop]
        return ["n", n.name, list(n.lines), [round(v, 2) for v in n.at], n.size, n.step]

    return json.dumps([dump(n) for n in unit.natives], separators=(",", ":"), default=str)


def object_count(unit: Unit) -> int:
    """How many native objects ``unit`` is: a dimension each, a note one annotation per line."""
    return sum(1 if isinstance(n, NativeDim) else len(n.lines) for n in unit.natives if not isinstance(n, NativeFlag))


def measured_problem(obj: Any, dim: NativeDim) -> str | None:
    """Why TechDraw's measurement of ``obj`` (its ``getRawValue()``) is not the model's value (``None`` when it is): the
    truth check of a native dimension."""
    if dim.expected is None:
        return None
    try:
        got = float(obj.getRawValue())
    except Exception as exc:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
        return f"{dim.item} {dim.name}: TechDraw could not measure it ({exc})"
    tol = ANGLE_TOL_DEG if dim.kind == "angle" else VALUE_TOL_MM
    if abs(abs(got) - dim.expected) > tol:
        return f"{dim.item} {dim.name}: TechDraw measures {got:.3f} and the model says {dim.expected:.3f}"
    return None
