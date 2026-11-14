"""The drawing sheet as plain data: what a sheet shows, independent of how it is written (no FreeCAD import, so it is
unit-testable on its own and runs on the worker's Python 3.11).

A ``Sheet`` is a page size and a list of entities (line, polyline, arc, circle, text, solid, glyph), each on a LAYER
named by its role (``CONTORNO``, ``OCULTAS``, ``COTAS``, ``TEXTO``, ``ROTULO``, ``EJES``, ``SOMBREADO``). Two writers
turn it into files: ``sheet_svg`` (the PDF and the PNG, through Qt) and ``sheet_dxf`` (an own DXF R12 writer), so both
show the same content.

Coordinates are page millimetres with the origin at the BOTTOM LEFT and y UP (the DXF and TechDraw convention); the SVG
writer flips y. Angles are degrees, counter-clockwise. Text is real Unicode (``Ø``, ``±``, ``°``); the DXF writer maps
it to ``%%c``, ``%%p`` and ``%%d``.
"""

import math
from dataclasses import dataclass, field

CONTOUR = "CONTORNO"
HIDDEN = "OCULTAS"
DIMENSIONS = "COTAS"
TEXT = "TEXTO"
TITLE_BLOCK = "ROTULO"
AXES = "EJES"
HATCH = "SOMBREADO"
THREAD = "ROSCAS"
FLAT_FACES = "CARAS_PLANAS"  # the diagonals of a flat face of a turned part: a layer no native object draws
ISO_SHADE = "ISO_COLOR"  # the shaded picture under the lines of the isometric view (``Sheet.underlay``), never DXF

CENTRE_OVERSHOOT_MM = 2.5  # a centre line goes this far past the feature it marks, at EVERY end (ISO 128-24)
CENTRE_CLEAR_MM = 1.0  # ... and stops this far short of a dimension line or a dimension text (it never reaches one)


@dataclass(frozen=True)
class LayerStyle:
    """How a layer is drawn: ``color`` is the ACI colour index (DXF), ``linetype`` one of ``CONTINUOUS`` /
    ``HIDDEN`` / ``CENTER``, ``width_mm`` the SVG stroke width and ``dash`` its dash pattern (mm)."""

    color: int
    linetype: str
    width_mm: float
    dash: tuple[float, ...] = ()


# ISO 128-20 line weights (mm): the visible outline is THICK, everything else THIN (about half), so a sheet reads by
# its hierarchy. The DXF (R12) has no lineweight: each layer gets the ACI colour that a standard pen table maps to
# that weight -- colour 7 (white/black) = 0.50 mm, colour 3 (green) = 0.25 mm for dimensions, text and leaders,
# colour 1 (red) = 0.25 mm for centre lines, colours 8 / 9 (greys) = 0.25 / 0.18 mm for hidden lines and hatching.
THICK_MM = 0.5
THIN_MM = 0.25
HAIR_MM = 0.18
TEXT_MM = 3.5  # every dimension and note is written at this height; only titles are larger

LAYERS: dict[str, LayerStyle] = {
    CONTOUR: LayerStyle(7, "CONTINUOUS", THICK_MM),
    HIDDEN: LayerStyle(8, "HIDDEN", THIN_MM, (3.0, 1.5)),
    DIMENSIONS: LayerStyle(3, "CONTINUOUS", THIN_MM),
    TEXT: LayerStyle(3, "CONTINUOUS", THIN_MM),
    TITLE_BLOCK: LayerStyle(7, "CONTINUOUS", 0.35),
    AXES: LayerStyle(1, "CENTER", THIN_MM, (6.0, 0.75, 0.125, 0.75)),
    HATCH: LayerStyle(9, "CONTINUOUS", HAIR_MM),
    THREAD: LayerStyle(3, "CONTINUOUS", THIN_MM),  # ISO 6410: the thin arc at the major diameter of a thread
    FLAT_FACES: LayerStyle(3, "CONTINUOUS", THIN_MM),  # the diagonals of a flat face seen face-on
}

LINETYPES: dict[str, tuple[float, ...]] = {
    "CONTINUOUS": (),
    "HIDDEN": (3.0, -1.5),  # DXF pattern: positive = dash, negative = gap
    "CENTER": (6.0, -0.75, 0.125, -0.75),
}

TEXT_HEIGHT_MM = 3.5
TEXT_WIDTH_FACTOR = 0.6  # average advance of a character as a fraction of its height (osifont is narrow)

Point = tuple[float, float]


def text_width(text: str, height: float = TEXT_HEIGHT_MM) -> float:
    """An estimate of the width of ``text``: the real font decides at render time, this is for layout only."""
    return len(text) * height * TEXT_WIDTH_FACTOR


@dataclass(frozen=True)
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str = CONTOUR
    width: float | None = None  # SVG stroke width override (mm); the DXF takes it from the layer


@dataclass(frozen=True)
class Polyline:
    points: tuple[Point, ...]
    layer: str = CONTOUR
    closed: bool = False
    width: float | None = None


@dataclass(frozen=True)
class Arc:
    """Counter-clockwise from ``start`` to ``end`` degrees."""

    cx: float
    cy: float
    r: float
    start: float
    end: float
    layer: str = CONTOUR
    width: float | None = None


@dataclass(frozen=True)
class Circle:
    cx: float
    cy: float
    r: float
    layer: str = CONTOUR
    width: float | None = None


@dataclass(frozen=True)
class Text:
    """``x, y`` is the baseline point of the text, at its left, centre or right (``anchor``); ``angle`` rotates it
    counter-clockwise about that point."""

    x: float
    y: float
    text: str
    height: float = TEXT_HEIGHT_MM
    layer: str = TEXT
    anchor: str = "left"
    angle: float = 0.0


@dataclass(frozen=True)
class Solid:
    """A filled polygon: an arrowhead (3 points) or a filled mark. In DXF 3 or 4 points are a ``SOLID``; more are
    written as a closed outline."""

    points: tuple[Point, ...]
    layer: str = DIMENSIONS
    fill: tuple[int, int, int] | None = None
    """An RGB (0..255) to fill with instead of the ink: the shaded isometric picture (``Sheet.underlay``)."""


# Stroke glyphs: symbols the fonts do not all have, drawn as polylines in a unit box (height 1, baseline at 0, about
# 0.7 wide). New phases add theirs here (GD&T characters, surface-finish marks).
def _ngon(cx: float, cy: float, r: float, n: int = 16) -> tuple[Point, ...]:
    return tuple((cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n + 1))


def _arc_points(cx: float, cy: float, r: float, start: float, end: float, n: int = 12) -> tuple[Point, ...]:
    return tuple(
        (
            cx + r * math.cos(math.radians(start + (end - start) * i / n)),
            cy + r * math.sin(math.radians(start + (end - start) * i / n)),
        )
        for i in range(n + 1)
    )


# ISO 1101 geometrical characteristics, the material-condition modifiers and the like, in a unit SQUARE (the frames of
# ``items_gdt`` draw them about 4.5 mm high in a 7 mm cell)
GLYPHS: dict[str, tuple[tuple[Point, ...], ...]] = {
    "diameter": (_ngon(0.35, 0.5, 0.32), ((0.05, 0.1), (0.65, 0.9))),
    "flatness": (((0.0, 0.2), (0.7, 0.2), (1.0, 0.8), (0.3, 0.8), (0.0, 0.2)),),
    "straightness": (((0.0, 0.5), (1.0, 0.5)),),
    "circularity": (_ngon(0.5, 0.5, 0.5, 20),),
    "cylindricity": (_ngon(0.5, 0.5, 0.27, 16), ((0.0, 0.0), (0.3, 1.0)), ((0.7, 0.0), (1.0, 1.0))),
    "parallelism": (((0.1, 0.0), (0.45, 1.0)), ((0.55, 0.0), (0.9, 1.0))),
    "perpendicularity": (((0.0, 0.0), (1.0, 0.0)), ((0.5, 0.0), (0.5, 1.0))),
    "angularity": (((1.0, 0.0), (0.0, 0.0), (0.85, 0.75)),),
    "position": (_ngon(0.5, 0.5, 0.33, 16), ((0.5, 0.0), (0.5, 1.0)), ((0.0, 0.5), (1.0, 0.5))),
    "concentricity": (_ngon(0.5, 0.5, 0.5, 20), _ngon(0.5, 0.5, 0.27, 16)),
    "symmetry": (((0.1, 0.8), (0.9, 0.8)), ((0.0, 0.5), (1.0, 0.5)), ((0.1, 0.2), (0.9, 0.2))),
    "runout": (((0.1, 0.1), (0.9, 0.9)), ((0.4, 0.9), (0.9, 0.9), (0.9, 0.4))),
    "total_runout": (
        ((0.05, 0.1), (0.5, 0.9)),
        ((0.3, 0.9), (0.5, 0.9), (0.5, 0.55)),
        ((0.45, 0.1), (0.9, 0.9)),
        ((0.7, 0.9), (0.9, 0.9), (0.9, 0.55)),
        ((0.05, 0.1), (0.45, 0.1)),
    ),
    "profile_line": (_arc_points(0.5, 0.15, 0.5, 0.0, 180.0),),
    "profile_surface": (_arc_points(0.5, 0.15, 0.5, 0.0, 180.0), ((0.0, 0.15), (1.0, 0.15))),
    "mmc": (_ngon(0.5, 0.5, 0.5, 20), ((0.2, 0.25), (0.2, 0.75), (0.5, 0.4), (0.8, 0.75), (0.8, 0.25))),
    "lmc": (_ngon(0.5, 0.5, 0.5, 20), ((0.35, 0.78), (0.35, 0.25), (0.72, 0.25))),
}


@dataclass(frozen=True)
class Glyph:
    name: str
    x: float
    y: float
    height: float = TEXT_HEIGHT_MM
    layer: str = DIMENSIONS
    angle: float = 0.0

    def strokes(self) -> list[Polyline]:
        if self.name not in GLYPHS:
            msg = f"unknown glyph {self.name!r}, expected one of {sorted(GLYPHS)}"
            raise ValueError(msg)
        rad = math.radians(self.angle)
        cos, sin = math.cos(rad), math.sin(rad)
        out = []
        for stroke in GLYPHS[self.name]:
            points = tuple(
                (self.x + (px * cos - py * sin) * self.height, self.y + (px * sin + py * cos) * self.height)
                for px, py in stroke
            )
            out.append(Polyline(points, self.layer))
        return out


Entity = Line | Polyline | Arc | Circle | Text | Solid | Glyph


@dataclass
class Sheet:
    """One page: its size in mm, a title (the PDF's) and its entities."""

    width: float
    height: float
    title: str = ""
    entities: list[Entity] = field(default_factory=list)
    underlay: list[Entity] = field(default_factory=list)
    """Filled polygons painted UNDER every entity, in the PDF / PNG / SVG only: no item sees them (no collisions, no
    review, no DXF). The colour of the isometric view (``sheet_iso``)."""

    def add(self, *items: Entity) -> None:
        self.entities.extend(items)

    def extend(self, items: "list[Entity] | tuple[Entity, ...]") -> None:
        self.entities.extend(items)

    def of_layer(self, layer: str) -> list[Entity]:
        return [e for e in self.entities if getattr(e, "layer", None) == layer]

    def texts(self) -> list[str]:
        return [e.text for e in self.entities if isinstance(e, Text)]


# --- numbers -------------------------------------------------------------------------------------------------------


def fmt(value: float, comma: bool = False) -> str:
    """A measured value as a drawing writes it: at most two decimals, no trailing zeros (``60``, ``12.5``); with
    ``comma`` the decimal sign is a comma, as Spanish drawings write it (``12,5``)."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    text = "0" if text in ("", "-0") else text
    return text.replace(".", ",") if comma else text


def fmt_scale(scale: float) -> str:
    """The title-block scale: ``1 : 2`` for 0.5, ``2 : 1`` for 2, ``1 : 1``."""
    if scale >= 1:
        return f"{fmt(scale)} : 1"
    return f"1 : {fmt(1 / scale)}"
