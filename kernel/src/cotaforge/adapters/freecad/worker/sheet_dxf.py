"""A drawing sheet (``sheet_model.Sheet``) as a DXF R12 (AC1009) file (no FreeCAD import, so it is unit-testable).

R12 is the version nearly every CAD opens (LibreCAD, viewers, nearly every CAD software); it only has LINE, CIRCLE, ARC, TEXT,
SOLID and POLYLINE, which is all a sheet needs. What this writer does that ``TechDraw.writeDXFPage`` does not: the text
is cp1252 as the header declares (the diameter, plus-minus and degree signs are written as the R12 codes ``%%c``,
``%%p`` and ``%%d``), the title block is in it, and every layer of the sheet model is declared in the TABLES
section with its colour and line type (so a hidden or centre line is dashed in the CAD, not just on a layer named so).
"""

from sheet_model import (
    LAYERS,
    LINETYPES,
    TEXT,
    Arc,
    Circle,
    Entity,
    Glyph,
    LayerStyle,
    Line,
    Polyline,
    Sheet,
    Solid,
    Text,
)

CODEPAGE = "cp1252"
NEWLINE = "\r\n"
# characters R12 has a code for (the codes are not text: commercial CAD software turns them into the symbol)
SPECIAL = {"Ø": "%%c", "⌀": "%%c", "±": "%%p", "°": "%%d"}
# characters outside cp1252 the sheets may carry, spelled out instead of becoming "?"
FALLBACK = {"⌴": "cbore", "⌵": "csink", "↧": "depth", "→": "->"}


def encode_text(text: str) -> str:
    """``text`` as a DXF R12 string: special signs as ``%%`` codes, the rest restricted to what cp1252 can hold."""
    out = []
    for ch in text:
        if ch in SPECIAL:
            out.append(SPECIAL[ch])
        elif ch in "\r\n":
            out.append(" ")
        else:
            try:
                ch.encode(CODEPAGE)
            except UnicodeEncodeError:
                out.append(FALLBACK.get(ch, "?"))
            else:
                out.append(ch)
    return "".join(out)


def _num(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    if text in ("", "-0"):
        return "0.0"
    return text if "." in text else f"{text}.0"


class _Writer:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def put(self, code: int, value: object) -> None:
        self.lines.append(f"{code:3d}")
        self.lines.append(_num(value) if isinstance(value, float) else str(value))

    def point(self, base: int, x: float, y: float, z: float = 0.0) -> None:
        self.put(base, x)
        self.put(base + 10, y)
        self.put(base + 20, z)

    def section(self, name: str) -> None:
        self.put(0, "SECTION")
        self.put(2, name)

    def end_section(self) -> None:
        self.put(0, "ENDSEC")


def _header(w: _Writer, sheet: Sheet) -> None:
    w.section("HEADER")
    w.put(9, "$ACADVER")
    w.put(1, "AC1009")
    w.put(9, "$DWGCODEPAGE")
    w.put(3, "ANSI_1252")
    w.put(9, "$INSBASE")
    w.point(10, 0.0, 0.0)
    w.put(9, "$EXTMIN")
    w.point(10, 0.0, 0.0)
    w.put(9, "$EXTMAX")
    w.point(10, sheet.width, sheet.height)
    w.put(9, "$LIMMIN")
    w.put(10, 0.0)
    w.put(20, 0.0)
    w.put(9, "$LIMMAX")
    w.put(10, sheet.width)
    w.put(20, sheet.height)
    w.put(9, "$LTSCALE")
    w.put(40, 1.0)
    w.put(9, "$TEXTSTYLE")
    w.put(7, "STANDARD")
    w.put(9, "$CLAYER")
    w.put(8, "0")
    w.end_section()


def _ltype(w: _Writer, name: str, description: str, pattern: tuple[float, ...]) -> None:
    w.put(0, "LTYPE")
    w.put(2, name)
    w.put(70, 0)
    w.put(3, description)
    w.put(72, 65)
    w.put(73, len(pattern))
    w.put(40, float(sum(abs(p) for p in pattern)))
    for element in pattern:
        w.put(49, float(element))


def _tables(w: _Writer, layers: dict[str, LayerStyle]) -> None:
    w.section("TABLES")
    w.put(0, "TABLE")
    w.put(2, "LTYPE")
    w.put(70, len(LINETYPES))
    descriptions = {"CONTINUOUS": "Solid line", "HIDDEN": "Hidden __ __ __", "CENTER": "Center ____ _ ____ _"}
    for name, pattern in LINETYPES.items():
        _ltype(w, name, descriptions[name], pattern)
    w.put(0, "ENDTAB")
    w.put(0, "TABLE")
    w.put(2, "LAYER")
    w.put(70, len(layers) + 1)
    for name, style in [("0", LayerStyle(7, "CONTINUOUS", 0.25)), *layers.items()]:
        w.put(0, "LAYER")
        w.put(2, name)
        w.put(70, 0)
        w.put(62, style.color)
        w.put(6, style.linetype)
    w.put(0, "ENDTAB")
    w.put(0, "TABLE")
    w.put(2, "STYLE")
    w.put(70, 1)
    w.put(0, "STYLE")
    w.put(2, "STANDARD")
    w.put(70, 0)
    w.put(40, 0.0)
    w.put(41, 1.0)
    w.put(50, 0.0)
    w.put(71, 0)
    w.put(42, 3.5)
    w.put(3, "txt")
    w.put(4, "")
    w.put(0, "ENDTAB")
    w.end_section()


def _entity(w: _Writer, e: Entity) -> None:
    if isinstance(e, Line):
        w.put(0, "LINE")
        w.put(8, e.layer)
        w.point(10, e.x1, e.y1)
        w.point(11, e.x2, e.y2)
    elif isinstance(e, Polyline):
        points = list(e.points)
        if len(points) == 2 and not e.closed:
            _entity(w, Line(points[0][0], points[0][1], points[1][0], points[1][1], e.layer))
            return
        w.put(0, "POLYLINE")
        w.put(8, e.layer)
        w.put(66, 1)
        w.point(10, 0.0, 0.0)
        w.put(70, 1 if e.closed else 0)
        for x, y in points[:-1] if e.closed and points[0] == points[-1] else points:
            w.put(0, "VERTEX")
            w.put(8, e.layer)
            w.point(10, x, y)
        w.put(0, "SEQEND")
        w.put(8, e.layer)
    elif isinstance(e, Circle):
        w.put(0, "CIRCLE")
        w.put(8, e.layer)
        w.point(10, e.cx, e.cy)
        w.put(40, e.r)
    elif isinstance(e, Arc):
        w.put(0, "ARC")
        w.put(8, e.layer)
        w.point(10, e.cx, e.cy)
        w.put(40, e.r)
        w.put(50, e.start % 360.0)
        w.put(51, e.end % 360.0)
    elif isinstance(e, Text):
        _text(w, e)
    elif isinstance(e, Solid):
        pts = list(e.points)
        if len(pts) in (3, 4):
            if len(pts) == 3:
                pts.append(pts[2])
            order = [pts[0], pts[1], pts[3], pts[2]]  # SOLID corners are stored 1, 2, 4, 3
            w.put(0, "SOLID")
            w.put(8, e.layer)
            for index, (x, y) in enumerate(order):
                w.point(10 + index, x, y)
        else:
            _entity(w, Polyline(tuple(pts), e.layer, closed=True))
    elif isinstance(e, Glyph):
        for stroke in e.strokes():
            _entity(w, stroke)
    else:
        msg = f"unknown sheet entity {type(e).__name__}"
        raise TypeError(msg)


def _text(w: _Writer, e: Text) -> None:
    halign = {"left": 0, "center": 1, "right": 2}.get(e.anchor, 0)
    w.put(0, "TEXT")
    w.put(8, e.layer)
    w.point(10, e.x, e.y)
    w.put(40, e.height)
    w.put(1, encode_text(e.text))
    if e.angle:
        w.put(50, e.angle % 360.0)
    if halign:
        w.put(72, halign)
        w.point(11, e.x, e.y)


def to_dxf(sheet: Sheet) -> bytes:
    """The sheet as DXF R12 bytes (cp1252)."""
    used = {getattr(e, "layer", TEXT) for e in sheet.entities}
    for e in sheet.entities:
        if isinstance(e, Glyph):
            used.add(e.layer)
    layers = dict(LAYERS)  # all of them, used or not: the CAD lists the roles
    for extra in sorted(used - set(layers)):
        layers[extra] = LayerStyle(7, "CONTINUOUS", 0.25)
    w = _Writer()
    _header(w, sheet)
    _tables(w, layers)
    w.section("BLOCKS")
    w.end_section()
    w.section("ENTITIES")
    for entity in sheet.entities:
        _entity(w, entity)
    w.end_section()
    w.put(0, "EOF")
    text = NEWLINE.join(w.lines) + NEWLINE
    return text.encode(CODEPAGE, errors="replace")
