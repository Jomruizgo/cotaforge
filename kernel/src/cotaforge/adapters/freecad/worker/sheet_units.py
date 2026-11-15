# ruff: noqa: E501
"""The pieces of a composed sheet the CAD window mirrors, as plain data (no FreeCAD import, unit-testable on its own).

The compositor (``drawing.compose``) draws a whole sheet into ``sheet_model`` entities; the PDF, the PNG and the DXF are
written from them. The engineer watches the TechDraw sheet window while the agent builds the sheet, so every piece that
one item (or the thread arcs, the ISO 286 note) put on the sheet is also made as an object of the
TechDraw page (``sheet_mirror``): a ``Unit`` is that piece -- a key, a label and the entities it drew, at their place on
the page. The key is the item id (``h1``); ``threads`` and ``iso286`` are for what no item owns.

"""

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any

from sheet_clip import arc_points
from sheet_marks import MARK_TYPES, WHOLE, NativeBalloon, NativeLeader, NativeTable, NativeWeld
from sheet_marks import count as marks_count
from sheet_marks import dump as marks_dump
from sheet_model import (
    AXES,
    DIMENSIONS,
    FLAT_FACES,
    HIDDEN,
    PARTING,
    TEXT,
    THREAD,
    ZONE,
    Arc,
    Circle,
    Entity,
    Glyph,
    Line,
    Polyline,
    Solid,
    Text,
    text_width,
)

Rect = tuple[float, float, float, float]
Point = tuple[float, float]

KIND_ITEM = "item"
KIND_EXTRA = "extra"
TEXT_SLACK = 1.12  # the real font may be wider than ``text_width`` estimates: the SVG fragment is cut at its box
MARGIN_MM = 1.5
KEY_THREADS = "threads"
KEY_ISO_SCALE = "iso_scale"  # the scale written under the isometric view when it is not the sheet's
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
    lower) deviation TechDraw prints with the measured value, as ``tol_text`` (mm: left to TechDraw's own unit schema a
    deviation of 12 µm prints as ``-12``); ``suffix`` follows the measured value (a fit symbol); ``leader`` puts the text on a shelf (ISO 129-1
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
    exact: bool = False  # a theoretically exact dimension: TechDraw frames its text (``TheoreticalExact``)
    tol_text: tuple[str, str] | None = None  # the upper and lower deviation as the sheet writes them (mm, its decimal sign)
    suffix: str = ""  # written after the measured value (the fit symbol of a toleranced size: ``P9``)


@dataclass(frozen=True)
class NativeNote:
    """One ``DrawViewAnnotation``: ``lines`` of text whose top-left corner is at ``at`` (page mm, y up)."""

    item: str
    name: str
    lines: tuple[str, ...]
    at: Point
    size: float = 3.5
    step: float = 5.0  # between the baselines of two lines
    fit: bool = True  # the window is asked where the line was painted and it is moved to the left edge asked (a short label is not)


@dataclass(frozen=True)
class NativeFlag:
    """A property TechDraw's own view has that does what the item draws: ``HardHidden`` shows the hidden edges of the
    view (the dashed lines of ``view_lines``) with TechDraw's own hidden-line style and width. No object is made: the
    view is the object, and the sheet switches the property on and off as the item comes and goes."""

    item: str
    view: str
    prop: str = "HardHidden"


@dataclass(frozen=True)
class NativeCosmetic:
    """A cosmetic edge of a TechDraw view (``makeCosmeticLine`` / ``makeCosmeticCircle`` / ``makeCosmeticCircleArc``): the
    centre lines and marks, the thin arc of a thread, the parting line. It belongs to the view's own cosmetic list, so it
    moves with the view, is edited there line by line and goes when the view does. ``values`` are in page mm (y up): a
    ``line`` is x1, y1, x2, y2; a ``circle`` cx, cy, r; an ``arc`` cx, cy, r, start, end (degrees, counter-clockwise).
    ``style`` is the line kind (``COSMETIC_STYLES``)."""

    item: str
    view: str  # front | top | right | isometric
    kind: str  # line | circle | arc
    values: tuple[float, ...]
    style: str


COSMETIC_STYLES = {  # the layer the compositor draws on -> the kind of line TechDraw is asked for
    AXES: "centre",
    THREAD: "thin",
    FLAT_FACES: "thin",
    PARTING: "parting",
    ZONE: "thin",
}


@dataclass(frozen=True)
class NativeView:
    """A view TechDraw makes of the part: a ``DrawViewSection`` (the cut, its hatch, the cutting line and letter in the parent
    view), a ``DrawViewDetail`` (a circle of the parent enlarged) or, for an auxiliary view, a ``DrawViewPart`` looked at square
    to a face. ``box`` is the page rectangle (mm, y up) of what the sheet draws in it: the object is put where that box sits,
    at ``scale``. ``direction`` is the model vector towards the viewer and ``xdir`` the model vector along the page's x; a section
    cuts at the plane through ``at`` and a detail is the circle of ``radius`` model mm at ``anchor`` (the parent's own
    coordinates: model mm from the parent's origin, y up). ``parent`` is the view it hangs from; ``name`` is what the other
    items call it (``A-A``). Every dimension of the view is a ``NativeDim`` whose ``view`` is that name."""

    item: str
    name: str
    kind: str  # section | detail | aux
    parent: str  # front | top | right | isometric
    box: Rect
    scale: float
    direction: tuple[float, float, float]
    xdir: tuple[float, float, float]
    letter: str
    at: tuple[float, float, float] | None = None
    anchor: Point | None = None
    radius: float = 0.0


Native = NativeDim | NativeNote | NativeFlag | NativeCosmetic | NativeWeld | NativeBalloon | NativeTable | NativeLeader | NativeView


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
    covered: frozenset[int] = field(default=frozenset(), compare=False)  # ids of the entities a NativeCosmetic draws

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
        if any(isinstance(n, (*WHOLE, NativeView)) for n in self.natives):
            return ()  # welds, balloons, tables and the views TechDraw makes: the objects are the whole of the item
        objects = any(isinstance(n, NativeDim | NativeNote) for n in self.natives)
        gone = (NATIVE_LAYERS if objects else set()) | ({HIDDEN} if any(isinstance(n, NativeFlag) for n in self.natives) else set())
        return tuple(e for e in self.entities if getattr(e, "layer", None) not in gone and id(e) not in self.covered)


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
    native: dict[str, dict[str, Any]] | None = None,
    natives: dict[str, list[Native]] | None = None,
) -> list[Unit]:
    """The units of a composed sheet, in the order they should stack on the page.

    ``slices`` maps an item id to the range of ``entities`` it drew; an item that failed (status ``error``) or drew
    nothing makes no unit. ``extras`` is the same for the pieces no item owns (``threads``). ``views`` are
    ``native`` maps the
    id of an item that TechDraw's own objects hold (the overall dimensions of a ``techdraw`` sheet) to what the
    mirror needs to put them where the sheet draws them. ``natives`` maps an item id to the TechDraw dimension and
    annotation objects it asks for (``NativeDim`` / ``NativeNote``)."""
    units: list[Unit] = []
    for key, what in ((KEY_THREADS, "thread arcs"), (KEY_ISO_SCALE, "isometric view scale")):
        if key in extras:
            a, b = extras[key]
            if b > a:
                units.append(Unit(key, what, KIND_EXTRA, tuple(entities[a:b])))
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
                *(["exact"] if n.exact else []),  # only when set: a sheet made before keeps its objects
                *([["tol_text", list(n.tol_text), n.suffix]] if n.tol_text else []),
            ]
        if isinstance(n, NativeFlag):
            return ["f", n.view, n.prop]
        if isinstance(n, MARK_TYPES):
            return marks_dump(n)
        if isinstance(n, NativeView):
            return [
                "v",
                n.kind,
                n.name,
                n.parent,
                [round(v, 2) for v in n.box],
                round(n.scale, 4),
                [round(v, 4) for v in n.direction],
                [round(v, 4) for v in n.xdir],
                n.letter,
                None if n.at is None else [round(v, 3) for v in n.at],
                None if n.anchor is None else [round(v, 3) for v in n.anchor],
                round(n.radius, 3),
            ]
        return ["n", n.name, list(n.lines), [round(v, 2) for v in n.at], n.size, n.step, *([] if n.fit else ["loose"])]

    return json.dumps(
        [dump(n) for n in unit.natives if not isinstance(n, NativeCosmetic)], separators=(",", ":"), default=str
    )


def cosmetic_signature(natives: Any) -> str:
    """What the cosmetic edges of one view for one unit must be (a view that already carries it is left alone)."""
    return json.dumps(
        [[n.kind, [round(v, 2) for v in n.values], n.style] for n in natives], separators=(",", ":")
    )


def object_count(unit: Unit) -> int:
    """How many native objects ``unit`` is: a dimension or a view each, a note one annotation per line."""
    return sum(
        1 if isinstance(n, NativeDim | NativeView) else marks_count(n) if isinstance(n, MARK_TYPES) else len(n.lines)
        for n in unit.natives
        if not isinstance(n, NativeFlag | NativeCosmetic)
    )


def view_of(unit: Unit) -> NativeView | None:
    """The view ``unit`` is, when it is one (a section, detail or auxiliary view item)."""
    return next((n for n in unit.natives if isinstance(n, NativeView)), None)


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


# --- the cosmetic edges of a unit (pure) --------------------------------------------------------------------------------

COSMETIC_MARGIN_MM = 30.0  # a line this near the box of a TechDraw view (a centre line past the part) is the view's


def _segments(e: Entity) -> list[tuple[str, tuple[float, ...]]]:
    """What a cosmetic edge can be made of ``e``: ``(kind, values)``, none when it has no cosmetic form."""
    if isinstance(e, Line):
        return [("line", (e.x1, e.y1, e.x2, e.y2))]
    if isinstance(e, Polyline):
        points = list(e.points) + ([e.points[0]] if e.closed and e.points else [])
        return [("line", (a[0], a[1], b[0], b[1])) for a, b in zip(points, points[1:], strict=False)]
    if isinstance(e, Arc):
        return [("arc", (e.cx, e.cy, e.r, e.start, e.end))]
    if isinstance(e, Circle):
        return [("circle", (e.cx, e.cy, e.r))]
    return []


def _anchor(kind: str, values: tuple[float, ...]) -> Point:
    if kind == "line":
        return (values[0] + values[2]) / 2, (values[1] + values[3]) / 2
    return values[0], values[1]


def _inside(p: Point, rect: Rect, margin: float = 0.0) -> bool:
    return rect[0] - margin <= p[0] <= rect[2] + margin and rect[1] - margin <= p[1] <= rect[3] + margin


def with_cosmetics(units: list[Unit], views: dict[str, Rect], derived: list[Rect]) -> list[Unit]:
    """``units`` with the lines of the layers TechDraw has a cosmetic edge for (``COSMETIC_STYLES``) asked of the view they lie
    on (``views``: TechDraw's own views by name, with the page box of what they draw; ``derived``: boxes of views the sheet drew
    itself, whose lines are not any TechDraw view's). A line that lies on no view stays what it was: part of the unit's drawing.
    The unit of a view (a section, detail or auxiliary view) is all TechDraw's: its centre and thread lines inside the view are
    cosmetic edges of it, and the rest of what it drew (the cut, the hatch, the cutting line, the letters) is the view's own."""
    out: list[Unit] = []
    for unit in units:
        made: list[NativeCosmetic] = []
        covered: set[int] = set()
        seen: set[tuple[str, str, tuple[float, ...], str]] = set()  # the same line drawn twice is one edge
        own = view_of(unit)
        for e in unit.entities:
            style = COSMETIC_STYLES.get(getattr(e, "layer", ""))
            if own is not None:
                covered.add(id(e))
                if style is not None:
                    for kind, values in _segments(e):
                        if _inside(_anchor(kind, values), own.box):
                            n = NativeCosmetic(unit.key, own.name, kind, values, style)
                            key = (n.view, n.kind, tuple(round(v, 2) for v in n.values), n.style)
                            if key not in seen:
                                seen.add(key)
                                made.append(n)
                continue
            if style is None:
                continue
            parts = _segments(e)
            if not parts:
                continue
            placed: list[NativeCosmetic] = []
            for kind, values in parts:
                point = _anchor(kind, values)
                if any(_inside(point, box) for box in derived):
                    placed = []
                    break
                near = [
                    (((box[0] + box[2]) / 2 - point[0]) ** 2 + ((box[1] + box[3]) / 2 - point[1]) ** 2, name)
                    for name, box in views.items()
                    if _inside(point, box)
                ] or [
                    (((box[0] + box[2]) / 2 - point[0]) ** 2 + ((box[1] + box[3]) / 2 - point[1]) ** 2, name)
                    for name, box in views.items()
                    if _inside(point, box, COSMETIC_MARGIN_MM)
                ]
                if not near:
                    placed = []
                    break
                placed.append(NativeCosmetic(unit.key, min(near)[1], kind, values, style))
            if placed:
                for n in placed:
                    key = (n.view, n.kind, tuple(round(v, 2) for v in n.values), n.style)
                    if key not in seen:
                        seen.add(key)
                        made.append(n)
                covered.add(id(e))
        marks, marked = mould_marks(unit, views, derived, covered)
        made = [*made, *marks]
        covered |= marked
        out.append(replace(unit, natives=(*unit.natives, *made), covered=frozenset(covered)) if made else unit)
    return out


def caption_of(kind: str, name: str) -> str:
    """The text TechDraw writes under a view. A section's name has its hyphen spaced (``C - C``): in osifont, the font of the
    TechDraw templates, the bare hyphen of ``C-C`` is drawn over the right side of the first C and the label reads ``E-C``
    (the letters A, B, D... leave room for it, the C and the G do not)."""
    return name.replace("-", " - ") if kind == "section" else name


def tight_section_caption(kind: str, name: str, caption: str) -> bool:
    """Whether a section carries its name as it is (``C-C``) where ``caption_of`` spaces it: the label would read ``E-C``."""
    return kind == "section" and "-" in name and caption.strip() == name.strip()


# --- the marks of a moulded part (ISO 10135): leaders, notes and cosmetic lines (pure) ----------------------------------------

MOULD_ITEMS = frozenset({"parting_line", "pull_direction", "mould_zone", "no_draft"})
SHAFT_TOL_MM = 0.05  # the shaft of an arrow ends on the tip of its head


def _nearest_view(points: list[Point], views: dict[str, Rect]) -> str | None:
    """The TechDraw view whose box the ``points`` are nearest to (the one a mark stands beside), ``None`` when there is none."""
    best: tuple[float, str] | None = None
    for name, box in views.items():
        for p in points:
            dx = max(box[0] - p[0], 0.0, p[0] - box[2])
            dy = max(box[1] - p[1], 0.0, p[1] - box[3])
            if best is None or dx * dx + dy * dy < best[0]:
                best = (dx * dx + dy * dy, name)
    return None if best is None else best[1]


def mould_marks(
    unit: Unit, views: dict[str, Rect], derived: list[Rect], taken: set[int]
) -> tuple[list[Native], set[int]]:
    """What of a moulded-part item is made of TechDraw's own objects: an arrow with its shaft is a leader line with an
    arrowhead (the opening direction, a draft exception), every other line, arc and circle (the bar at the tail of the
    arrow, the draft wedge, the hatch of a zone) a cosmetic edge of the view it stands beside, every text an annotation. The
    entities handled are returned by id so the sheet draws none of them again. ``taken`` are the ones already asked for."""
    if unit.item_type not in MOULD_ITEMS or not views:
        return [], set()
    made: list[Native] = []
    covered: set[int] = set()
    shafts: dict[int, Line] = {}
    for solid in unit.entities:
        if not isinstance(solid, Solid) or len(solid.points) != 3 or getattr(solid, "layer", None) != DIMENSIONS:  # noqa: PLR2004
            continue
        tip = solid.points[0]
        for e in unit.entities:
            if not isinstance(e, Line) or id(e) in shafts or getattr(e, "layer", None) != DIMENSIONS:
                continue
            for end, tail in (((e.x1, e.y1), (e.x2, e.y2)), ((e.x2, e.y2), (e.x1, e.y1))):
                if math.dist(end, tip) <= SHAFT_TOL_MM:
                    view = _nearest_view([tip, tail], views)
                    if view is not None and not any(_inside(tip, box) for box in derived):
                        made.append(NativeLeader(unit.key, view, (tip, tail)))
                        covered |= {id(solid), id(e)}
                        shafts[id(e)] = e
                    break
            if id(solid) in covered:
                break
    for e in unit.entities:
        if id(e) in covered or id(e) in taken or isinstance(e, Text | Solid):
            continue
        parts = _segments(e)
        view = _nearest_view([_anchor(k, v) for k, v in parts], views) if parts else None
        if view is None or any(_inside(_anchor(k, v), box) for k, v in parts for box in derived):
            continue
        made += [NativeCosmetic(unit.key, view, k, v, "thin") for k, v in parts]
        covered.add(id(e))
    texts = [e for e in unit.entities if isinstance(e, Text)]
    for number, e in enumerate(texts, start=1):
        width = text_width(e.text, e.height)
        left = e.x - (width / 2 if e.anchor == "center" else width if e.anchor == "right" else 0.0)
        made.append(NativeNote(unit.key, f"m{number}", (e.text,), (left, e.y + e.height), e.height, e.height * 1.4, False))
        covered.add(id(e))
    return made, covered
