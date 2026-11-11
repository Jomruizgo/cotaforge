# ruff: noqa: E501
"""Strict check of a drawing PDF after it is written: a file that fails is an error, never a success.

Two layers, both pure Python on top of ``pypdf`` (already a dependency):

* the file itself: it opens in strict mode, has the pages and the page size the sheet asked for;
* the content stream, operator by operator: every operator has the operands the PDF reference gives it (a colour operator
  has as many components as its colour space), every operand is a finite number, a name or a string where the operator
  wants one, the graphics state is balanced. A viewer that meets ``1 0 SCN`` or ``nan 0 0 SCN`` stops drawing at that
  point (poppler reports ``Incorrect number of arguments``; the engineer's viewer showed the first view and nothing
  else), and the tools that only read the file for text never notice.

The render check ties the file to the sheet: every view the sheet says it has must have ink in the PDF over the extent of
its box, so a PDF that lost a view (or whose content stops half way) is told from one that is complete.

No rasteriser is involved: neither FreeCAD nor the installer ships one, and a new compiled dependency is not worth it for
what the operand and ink checks already tell.
"""

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PT_MM = 25.4 / 72.0
PAGE_TOL_MM = 1.5  # a PDF page is rounded to a point (0.35 mm) and Qt rounds the paper size once more
INK_COVERAGE = 0.7  # the ink of a view spans at least this much of its box, in x and in y
INK_MARGIN_MM = 2.0  # paths this far outside a view's box still count as its own (line widths, the view's label)
MAX_PROBLEMS = 12
MAX_STREAM_BYTES = 64 * 1024 * 1024

# operator -> number of operands (the colour setters with a variable count are resolved against the colour space)
FIXED: dict[bytes, int] = {
    b"b": 0, b"B": 0, b"b*": 0, b"B*": 0, b"BDC": 2, b"BI": 0, b"BMC": 1, b"BT": 0, b"BX": 0, b"c": 6, b"cm": 6,
    b"CS": 1, b"cs": 1, b"d": 2, b"d0": 2, b"d1": 6, b"Do": 1, b"DP": 2, b"EI": 0, b"EMC": 0, b"ET": 0, b"EX": 0,
    b"f": 0, b"F": 0, b"f*": 0, b"G": 1, b"g": 1, b"gs": 1, b"h": 0, b"i": 1, b"ID": 0, b"j": 1, b"J": 1, b"K": 4,
    b"k": 4, b"l": 2, b"m": 2, b"M": 1, b"MP": 1, b"n": 0, b"q": 0, b"Q": 0, b"re": 4, b"RG": 3, b"rg": 3, b"ri": 1,
    b"s": 0, b"S": 0, b"sh": 1, b"T*": 0, b"Tc": 1, b"Td": 2, b"TD": 2, b"Tf": 2, b"Tj": 1, b"TJ": 1, b"TL": 1,
    b"Tm": 6, b"Tr": 1, b"Ts": 1, b"Tw": 1, b"Tz": 1, b"v": 4, b"w": 1, b"W": 0, b"W*": 0, b"y": 4, b"'": 1, b'"': 3,
}  # fmt: skip
NAME_OPERANDS = {b"CS", b"cs", b"Do", b"gs", b"sh", b"ri", b"BMC"}
STRING_OPERANDS = {b"Tj", b"'", b'"'}
COLOUR_SETTERS = {b"SC": "stroke", b"SCN": "stroke", b"sc": "fill", b"scn": "fill"}
PAINT = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"}
CLOSE_THEN_PAINT = {b"s", b"b", b"b*"}
DEVICE = {b"DeviceGray": 1, b"DeviceRGB": 3, b"DeviceCMYK": 4, b"CalGray": 1, b"CalRGB": 3, b"Lab": 3, b"Pattern": 0}

_DELIM = rb"\x00\t\n\x0c\r ()<>\[\]{}/%"
_TOKEN = re.compile(
    rb"(?P<ws>[\x00\t\n\x0c\r ]+)"
    rb"|(?P<comment>%[^\r\n]*)"
    rb"|(?P<string>\()"
    rb"|(?P<dopen><<)|(?P<dclose>>>)"
    rb"|(?P<hex><[^<>]*>)"
    rb"|(?P<aopen>\[)|(?P<aclose>\])"
    rb"|(?P<brace>[{}])"
    rb"|(?P<name>/[^" + _DELIM + rb"]*)"
    rb"|(?P<word>[^" + _DELIM + rb"]+)"
)
_NUMBER = re.compile(rb"^[+-]?(?:\d+\.?\d*|\.\d+)$")


@dataclass
class Problem:
    """One thing wrong in a file: ``where`` says in which operator of the content stream, when it is about the stream."""

    text: str
    where: str = ""

    def __str__(self) -> str:
        return f"{self.text} ({self.where})" if self.where else self.text


@dataclass
class PdfReport:
    """What the check found: ``problems`` is empty when the file is sound."""

    pages: int = 0
    page_mm: tuple[float, float] = (0.0, 0.0)
    operators: int = 0
    problems: list[Problem] = field(default_factory=list)
    views_with_ink: list[str] = field(default_factory=list)
    views_without_ink: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        shown = "; ".join(str(p) for p in self.problems[:MAX_PROBLEMS])
        more = len(self.problems) - MAX_PROBLEMS
        return shown + (f"; (+{more} more)" if more > 0 else "")


class PdfCheckError(RuntimeError):
    """The PDF cannot be trusted: ``report`` holds every problem found."""

    def __init__(self, report: PdfReport) -> None:
        super().__init__(report.summary())
        self.report = report


@dataclass(frozen=True)
class ViewBox:
    """A view of the sheet, as the worker measured it: its name and its box on the page (mm, origin at the lower left)."""

    name: str
    rect: tuple[float, float, float, float]


# --- the content stream -------------------------------------------------------------------------------------------------


def _string_end(data: bytes, start: int) -> int:
    """Index just past the literal string that opens at ``start`` (``(``), or -1 when it never closes."""
    depth = 0
    i = start
    size = len(data)
    while i < size:
        c = data[i]
        if c == 0x5C:  # backslash: the next byte is escaped
            i += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def tokens(data: bytes) -> list[tuple[str, bytes, int]]:
    """``(kind, text, offset)`` of every token: ``num``, ``name``, ``string``, ``hex``, ``op``, ``array`` markers and
    ``bad`` (a word that is no number and no operator shape, like ``nan``)."""
    out: list[tuple[str, bytes, int]] = []
    pos = 0
    size = len(data)
    inline_image_end = -1
    while pos < size:
        m = _TOKEN.match(data, pos)
        if m is None:  # an unclosed '<' or a lone '>': not a token a content stream can have
            out.append(("bad", data[pos : pos + 1], pos))
            pos += 1
            continue
        kind = m.lastgroup or ""
        end = m.end()
        if kind in ("ws", "comment"):
            pos = end
            continue
        if kind == "string":
            close = _string_end(data, pos)
            if close < 0:
                out.append(("bad", b"(unclosed string", pos))
                break
            out.append(("string", data[pos:close], pos))
            pos = close
            continue
        text = m.group()
        if kind == "word":
            if _NUMBER.match(text):
                kind = "num"
            else:
                kind = "op"
                if text == b"ID":  # inline image data: skip to its EI
                    inline_image_end = _inline_image_end(data, end)
                    out.append(("op", text, pos))
                    pos = inline_image_end if inline_image_end > 0 else size
                    continue
        out.append((kind, text, pos))
        pos = end
    return out


def _inline_image_end(data: bytes, start: int) -> int:
    m = re.compile(rb"[\x00\t\n\x0c\r ]EI(?=[\x00\t\n\x0c\r ]|$)").search(data, start)
    return m.start() if m else -1


@dataclass
class _Ink:
    """The state the ink walk keeps: the matrix stack and the path being built."""

    ctm: tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    stack: list[tuple[float, float, float, float, float, float]] = field(default_factory=list)
    path: list[tuple[float, float]] = field(default_factory=list)
    boxes: list[tuple[float, float, float, float]] = field(default_factory=list)

    def point(self, x: float, y: float) -> tuple[float, float]:
        a, b, c, d, e, f = self.ctm
        return a * x + c * y + e, b * x + d * y + f

    def add(self, x: float, y: float) -> None:
        self.path.append(self.point(x, y))

    def paint(self) -> None:
        if self.path:
            xs = [p[0] for p in self.path]
            ys = [p[1] for p in self.path]
            self.boxes.append((min(xs), min(ys), max(xs), max(ys)))
        self.path = []

    def multiply(self, m: tuple[float, ...]) -> None:
        a, b, c, d, e, f = m
        A, B, C, D, E, F = self.ctm  # noqa: N806 - the matrix of the PDF reference
        self.ctm = (
            a * A + b * C,
            a * B + b * D,
            c * A + d * C,
            c * B + d * D,
            e * A + f * C + E,
            e * B + f * D + F,
        )


def _components(space: Any, resources: Any) -> int | None:
    """How many numbers a colour of ``space`` (a name of the page's /ColorSpace, or a device space) has; ``None`` for
    a pattern space, whose colours are a name (and the components of the underlying space when it is uncoloured)."""
    name = bytes(str(space), "latin-1").lstrip(b"/") if not isinstance(space, bytes) else space.lstrip(b"/")
    if name in DEVICE:
        return None if name == b"Pattern" else DEVICE[name]
    try:
        spaces = resources.get("/ColorSpace") if resources is not None else None
        found = spaces.get_object().get("/" + name.decode("latin-1")) if spaces is not None else None
    except Exception:  # noqa: BLE001 - a damaged resources dictionary is reported by the strict reader
        found = None
    if found is None:
        return -1  # unknown space: the operand count cannot be told
    found = found.get_object()
    if isinstance(found, str):
        return _components(found, resources)
    family = str(found[0]).lstrip("/")
    if family == "Pattern":
        return None
    if family == "ICCBased":
        return int(found[1].get_object().get("/N", 3))
    if family in ("CalRGB", "Lab"):
        return 3
    if family in ("CalGray", "Indexed", "Separation"):
        return 1
    if family == "DeviceN":
        return len(found[1])
    return _components("/" + family, resources)


def check_stream(data: bytes, resources: Any = None, ink: _Ink | None = None) -> tuple[int, list[Problem]]:
    """The operators of one content stream and every problem in them."""
    problems: list[Problem] = []
    spaces = {"stroke": b"DeviceGray", "fill": b"DeviceGray"}
    operands: list[tuple[str, bytes, int]] = []
    count = 0
    depth = 0
    text_open = False
    seen = tokens(data)
    nesting = 0
    for kind, text, offset in seen:
        if kind in ("aopen", "dopen"):
            nesting += 1
            continue
        if kind in ("aclose", "dclose"):
            nesting = max(0, nesting - 1)
            if nesting == 0:
                operands.append(("array", b"", offset))
            continue
        if nesting:
            continue
        if kind in ("num", "name", "string", "hex"):
            operands.append((kind, text, offset))
            continue
        if kind in ("bad", "brace"):
            problems.append(Problem(f"{text.decode('latin-1')!r} is not a PDF token", _at(data, offset)))
            continue
        # an operator
        count += 1
        op = text
        if op in (b"BI", b"ID", b"EI"):  # an inline image: its header is key/value pairs, not operands
            operands = []
            continue
        if op in COLOUR_SETTERS:
            _colour(op, operands, spaces[COLOUR_SETTERS[op]], resources, problems, data, offset)
        elif op in FIXED:
            _fixed(op, operands, problems, data, offset)
        elif op in (b"true", b"false", b"null"):
            continue
        else:
            problems.append(Problem(f"unknown operator or word {op.decode('latin-1')!r}", _at(data, offset)))
        _track(op, operands, spaces)
        if ink is not None:
            _ink(ink, op, operands)
        if op == b"q":
            depth += 1
        elif op == b"Q":
            depth -= 1
            if depth < 0:
                problems.append(Problem("Q without a matching q", _at(data, offset)))
                depth = 0
        elif op == b"BT":
            if text_open:
                problems.append(Problem("BT inside a text object", _at(data, offset)))
            text_open = True
        elif op == b"ET":
            if not text_open:
                problems.append(Problem("ET without BT", _at(data, offset)))
            text_open = False
        operands = []
        if len(problems) > MAX_PROBLEMS * 4:
            break
    if operands and not problems:
        problems.append(Problem("operands left at the end of the stream with no operator", _at(data, operands[0][2])))
    if depth > 0:
        problems.append(Problem(f"{depth} q operator(s) never closed with Q"))
    return count, problems


def _at(data: bytes, offset: int) -> str:
    start = max(0, offset - 48)
    around = data[start : offset + 24].decode("latin-1")
    return "near: " + " ".join(around.split())[-72:]


def _finite(text: bytes) -> bool:
    try:
        return math.isfinite(float(text))
    except ValueError:
        return False


def _fixed(
    op: bytes, operands: list[tuple[str, bytes, int]], problems: list[Problem], data: bytes, offset: int
) -> None:
    name = op.decode("latin-1")
    want = FIXED[op]
    if op == b"TJ":
        if len(operands) != 1 or operands[0][0] != "array":
            problems.append(Problem("TJ needs one array", _at(data, offset)))
        return
    if len(operands) != want:
        problems.append(
            Problem(f"{name} has {len(operands)} operand(s), the PDF reference gives it {want}", _at(data, offset))
        )
        return
    for kind, text, _ in operands:
        if op in NAME_OPERANDS:
            ok = kind == "name"
        elif op in STRING_OPERANDS and kind in ("string", "hex"):
            ok = True
        elif op in (b"d",):
            ok = kind in ("array", "num")
        elif op in (b"Tf",):
            ok = kind in ("name", "num")
        elif op in (b"BDC", b"DP"):
            ok = True
        else:
            ok = kind == "num" and _finite(text)
        if not ok:
            problems.append(Problem(f"{name} operand {text.decode('latin-1')[:24]!r} is not valid", _at(data, offset)))
            return


def _colour(
    op: bytes,
    operands: list[tuple[str, bytes, int]],
    space: bytes,
    resources: Any,
    problems: list[Problem],
    data: bytes,
    offset: int,
) -> None:
    name = op.decode("latin-1")
    numbers = [o for o in operands if o[0] == "num"]
    names = [o for o in operands if o[0] == "name"]
    others = [o for o in operands if o[0] not in ("num", "name")]
    bad = [o for o in operands if o[0] == "num" and not _finite(o[1])]
    if others or bad:
        problems.append(Problem(f"{name} has an operand that is not a finite number or a name", _at(data, offset)))
        return
    want = _components(space, resources)
    if want == -1:
        return  # a colour space the page does not define: the strict reader or the viewer will say so
    if want is None:  # a pattern space: [components] /PatternName, the name last
        if len(names) != 1 or operands[-1][0] != "name":
            problems.append(Problem(f"{name} in a pattern colour space needs a pattern name last", _at(data, offset)))
        return
    if names or len(numbers) != want:
        problems.append(
            Problem(
                f"{name} has {len(numbers)} number(s){' and a name' if names else ''}, the colour space "
                f"{space.decode('latin-1')} gives it {want}",
                _at(data, offset),
            )
        )


_SPACE_OF = {
    b"G": b"DeviceGray",
    b"g": b"DeviceGray",
    b"RG": b"DeviceRGB",
    b"rg": b"DeviceRGB",
    b"K": b"DeviceCMYK",
    b"k": b"DeviceCMYK",
}


def _track(op: bytes, operands: list[tuple[str, bytes, int]], spaces: dict[str, bytes]) -> None:
    """The colour space the next ``SCN`` / ``scn`` is read against."""
    if op in (b"CS", b"cs") and operands and operands[0][0] == "name":
        spaces["stroke" if op == b"CS" else "fill"] = operands[0][1]
    elif op in _SPACE_OF:
        spaces["stroke" if op.isupper() else "fill"] = _SPACE_OF[op]


def _ink(ink: _Ink, op: bytes, operands: list[tuple[str, bytes, int]]) -> None:
    """Follow the matrix and the paths so the boxes of what is painted are known (numbers only: a bad operand was already
    reported)."""
    try:
        v = [float(o[1]) for o in operands if o[0] == "num"]
    except ValueError:
        return
    if op == b"q":
        ink.stack.append(ink.ctm)
    elif op == b"Q":
        if ink.stack:
            ink.ctm = ink.stack.pop()
    elif op == b"cm" and len(v) == 6:
        ink.multiply((v[0], v[1], v[2], v[3], v[4], v[5]))
    elif op in (b"m", b"l") and len(v) == 2:
        ink.add(v[0], v[1])
    elif op == b"c" and len(v) == 6:
        for i in (0, 2, 4):
            ink.add(v[i], v[i + 1])
    elif op in (b"v", b"y") and len(v) == 4:
        ink.add(v[0], v[1])
        ink.add(v[2], v[3])
    elif op == b"re" and len(v) == 4:
        x, y, w, h = v
        for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
            ink.add(px, py)
    elif op in PAINT:
        ink.paint()
    elif op == b"n":
        ink.path = []


# --- the file ----------------------------------------------------------------------------------------------------------


def check_pdf(
    path: Path | str,
    *,
    pages: int = 1,
    page_mm: tuple[float, float] | None = None,
    views: list[ViewBox] | tuple[ViewBox, ...] = (),
) -> PdfReport:
    """Check the PDF at ``path`` and return what it found (``report.ok``); nothing is raised for a bad file, so a caller
    can show every problem. ``pages`` is how many pages the sheet has (always one: one drawing, one sheet), ``page_mm`` the
    paper size it asked for, ``views`` the views the sheet holds with their boxes (page mm, y up): each must have ink."""
    try:
        from pypdf import PdfReader  # noqa: PLC0415 - a dependency of the server, imported where it is used
        from pypdf.errors import PyPdfError  # noqa: PLC0415
    except ImportError:  # pragma: no cover - pypdf is a declared dependency
        report = PdfReport()
        report.problems.append(Problem("pypdf is not installed: the PDF could not be checked"))
        return report
    report = PdfReport()
    try:
        reader = PdfReader(str(path), strict=True)
        report.pages = len(reader.pages)
    except (PyPdfError, OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        report.problems.append(Problem(f"the file is not a readable PDF ({type(exc).__name__}: {exc})"))
        return report
    if report.pages != pages:
        report.problems.append(Problem(f"the PDF has {report.pages} page(s), the sheet is {pages}"))
    for number, page in enumerate(reader.pages, start=1):
        try:
            _check_page(report, page, number, page_mm, views if number == 1 else ())
        except (PyPdfError, ValueError, KeyError, TypeError, AttributeError, IndexError, RecursionError) as exc:
            report.problems.append(Problem(f"page {number} could not be read ({type(exc).__name__}: {exc})"))
    return report


def _check_page(report: PdfReport, page: Any, number: int, page_mm: tuple[float, float] | None, views: Any) -> None:
    box = page.mediabox
    width, height = float(box.width) * PT_MM, float(box.height) * PT_MM
    if number == 1:
        report.page_mm = (round(width, 1), round(height, 1))
        if page_mm is not None and (abs(width - page_mm[0]) > PAGE_TOL_MM or abs(height - page_mm[1]) > PAGE_TOL_MM):
            report.problems.append(
                Problem(f"the page is {width:.1f} x {height:.1f} mm, the sheet is {page_mm[0]:g} x {page_mm[1]:g} mm")
            )
    contents = page.get_contents()
    if contents is None:
        report.problems.append(Problem(f"page {number} has no content"))
        return
    data = contents.get_data()
    if len(data) > MAX_STREAM_BYTES:
        report.problems.append(Problem(f"page {number} has a content stream of {len(data)} bytes: too large to check"))
        return
    ink = _Ink()
    count, problems = check_stream(data, page.get("/Resources"), ink)
    report.operators += count
    for p in problems:
        report.problems.append(Problem(f"page {number}: {p.text}", p.where))
    if not count:
        report.problems.append(Problem(f"page {number} draws nothing"))
    if views and not problems:
        _check_views(report, ink.boxes, float(box.left), float(box.bottom), views)


def _check_views(
    report: PdfReport, boxes: list[tuple[float, float, float, float]], x0: float, y0: float, views: Any
) -> None:
    """Every view's box must hold ink over (most of) its extent: the paths painted inside it, taken together, span it."""
    mm = [((a - x0) * PT_MM, (b - y0) * PT_MM, (c - x0) * PT_MM, (d - y0) * PT_MM) for a, b, c, d in boxes]
    for view in views:
        vx0, vy0, vx1, vy1 = view.rect
        inside = [
            m
            for m in mm
            if m[0] >= vx0 - INK_MARGIN_MM
            and m[1] >= vy0 - INK_MARGIN_MM
            and m[2] <= vx1 + INK_MARGIN_MM
            and m[3] <= vy1 + INK_MARGIN_MM
        ]
        spans = False
        if inside:
            sx = max(m[2] for m in inside) - min(m[0] for m in inside)
            sy = max(m[3] for m in inside) - min(m[1] for m in inside)
            spans = sx >= INK_COVERAGE * (vx1 - vx0) and sy >= INK_COVERAGE * (vy1 - vy0)
        (report.views_with_ink if spans else report.views_without_ink).append(view.name)
    if report.views_without_ink:
        report.problems.append(
            Problem(
                "no ink in the PDF where the sheet has "
                + ", ".join(report.views_without_ink)
                + f" ({len(report.views_without_ink)} of {len(views)} view(s))"
            )
        )


def require_sound(path: Path | str, **kwargs: Any) -> PdfReport:
    """``check_pdf``, raising ``PdfCheckError`` when the file is not sound."""
    report = check_pdf(path, **kwargs)
    if not report.ok:
        raise PdfCheckError(report)
    return report
