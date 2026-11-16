# ruff: noqa: E501
"""The stroke widths of a PDF, read from its content operators (``w``, ``cm``, ``d``, the stroke colour and the paint
operators): width in mm on the paper, dashed or not, colour, and how many mm of line were drawn with it. Test support, no
FreeCAD and no rasteriser: the check of a sheet's line weights is on what the file really holds."""

import math
import re
import zlib
from collections import Counter
from pathlib import Path

PT_MM = 25.4 / 72.0
_TOKEN = re.compile(rb"\[[^\]]*\]|/[^\s/\[\]()<>]+|\((?:\\.|[^\\)])*\)|<[^>]*>|-?\d*\.\d+|-?\d+\.?|[A-Za-z'\"*]+")
STROKE = {b"S", b"s", b"B", b"b", b"B*", b"b*"}
FILL_AND_STROKE = {b"B", b"b", b"B*", b"b*"}
NO_PAINT = {b"n", b"f", b"F", b"f*"}


def _streams(data: bytes) -> list[bytes]:
    out = []
    for found in re.finditer(rb"stream\r?\n", data):
        end = data.find(b"endstream", found.end())
        raw = data[found.end() : end]
        for load in (zlib.decompress, lambda b: zlib.decompressobj().decompress(b)):
            try:
                out.append(load(raw))
                break
            except zlib.error:
                continue
    return out


def _mul(a: list[float], b: list[float]) -> list[float]:
    return [
        a[0] * b[0] + a[1] * b[2],
        a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2],
        a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4],
        a[4] * b[1] + a[5] * b[3] + b[5],
    ]


def strokes(path: Path) -> Counter[tuple[float, bool, tuple[float, ...]]]:
    """``(width_mm, dashed, colour)`` -> mm of stroked line, over every content stream of the file."""
    found: Counter[tuple[float, bool, tuple[float, ...]]] = Counter()
    for stream in _streams(path.read_bytes()):
        ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        stack: list[tuple[list[float], float, bool, tuple[float, ...], tuple[float, ...]]] = []
        width, dashed, colour, fill = 1.0, False, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        nums: list[float] = []
        length, here, closed, last_fill = 0.0, None, False, None
        for token in _TOKEN.findall(stream):
            if token.startswith(b"["):
                dashed = bool(re.match(rb"\[\s*[\d.]", token))  # the dash array; the phase that follows is a number
                continue
            try:
                nums.append(float(token))
                continue
            except ValueError:
                pass
            if token == b"q":
                stack.append((ctm[:], width, dashed, colour, fill))
            elif token == b"Q" and stack:
                ctm, width, dashed, colour, fill = stack.pop()
            elif token == b"cm" and len(nums) >= 6:
                ctm = _mul(nums[-6:], ctm)
            elif token == b"w" and nums:
                width = nums[-1]
            elif token in (b"RG", b"SC", b"SCN") and nums:
                colour = tuple(round(x, 1) for x in nums[-3:])
            elif token in (b"rg", b"sc", b"scn") and nums:
                fill = last_fill = tuple(round(x, 1) for x in nums[-3:])
            elif token == b"G" and nums:
                colour = (round(nums[-1], 1),) * 3
            elif token == b"g" and nums:
                fill = last_fill = (round(nums[-1], 1),) * 3
            elif token == b"h":
                closed = True
            elif token == b"m" and len(nums) >= 2:
                here = (nums[-2], nums[-1])
            elif token in (b"l", b"c", b"v", b"y") and len(nums) >= 2 and here is not None:
                length += math.hypot(nums[-2] - here[0], nums[-1] - here[1])
                here = (nums[-2], nums[-1])
            elif token == b"re" and len(nums) >= 4:
                length += 2 * (abs(nums[-2]) + abs(nums[-1]))
            elif (token in FILL_AND_STROKE and colour == fill) or (
                token in STROKE and closed and colour == last_fill != (0.0, 0.0, 0.0)
            ):
                length, closed = (
                    0.0,
                    False,
                )  # a shape whose outline has the fill's colour (the shaded picture of the isometric view): no line
            elif token in STROKE:
                closed = False
                scale = math.sqrt(abs(ctm[0] * ctm[3] - ctm[1] * ctm[2])) or 1.0
                found[(round(width * scale * PT_MM, 2), dashed, colour)] += length * scale * PT_MM
                length = 0.0
            elif token in NO_PAINT:
                length, closed = 0.0, False
            nums = []
    return found


def by_width(path: Path) -> dict[float, float]:
    """Width (mm) -> mm of line, dashed and continuous together."""
    out: dict[float, float] = {}
    for (width, _dashed, _colour), mm in strokes(path).items():
        out[width] = out.get(width, 0.0) + mm
    return dict(sorted(out.items()))


def table(path: Path) -> str:
    """The widths of a PDF as text, one line per (width, dash): the share of the drawn line each one has."""
    found = strokes(path)
    total = sum(found.values()) or 1.0
    merged: dict[tuple[float, bool], float] = {}
    for (width, dashed, _colour), mm in found.items():
        merged[(width, dashed)] = merged.get((width, dashed), 0.0) + mm
    rows = [
        f"  {width:5.2f} mm {'dashed    ' if dashed else 'continuous'} {mm:8.0f} mm of line ({100 * mm / total:4.1f} %)"
        for (width, dashed), mm in sorted(merged.items())
    ]
    return f"{path.name}\n" + "\n".join(rows)


def dashed_strokes(path: Path) -> list[tuple[float, tuple[float, ...], tuple[float, float, float, float]]]:
    """Every stroke of the file drawn with a dash pattern: ``(width in mm, the pattern in mm on the paper, the box of the
    stroke in mm from the lower left corner of the page)``. The pattern is what says which line type a line is: a dashed
    line has two lengths (dash, gap), the long-dash dotted one of ISO 128 four (long dash, gap, dot, gap)."""
    found: list[tuple[float, tuple[float, ...], tuple[float, float, float, float]]] = []
    for stream in _streams(path.read_bytes()):
        ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        stack: list[tuple[list[float], float, tuple[float, ...]]] = []
        width, pattern = 1.0, ()
        nums: list[float] = []
        points: list[tuple[float, float]] = []
        for token in _TOKEN.findall(stream):
            if token.startswith(b"["):
                try:
                    pattern = tuple(float(x) for x in token[1:-1].split())
                except ValueError:
                    pattern = ()
                continue
            try:
                nums.append(float(token))
                continue
            except ValueError:
                pass
            if token == b"q":
                stack.append((ctm[:], width, pattern))
            elif token == b"Q" and stack:
                ctm, width, pattern = stack.pop()
            elif token == b"cm" and len(nums) >= 6:
                ctm = _mul(nums[-6:], ctm)
            elif token == b"w" and nums:
                width = nums[-1]
            elif token in (b"m", b"l", b"c", b"v", b"y") and len(nums) >= 2:
                x, y = nums[-2], nums[-1]
                points.append(((x * ctm[0] + y * ctm[2] + ctm[4]) * PT_MM, (x * ctm[1] + y * ctm[3] + ctm[5]) * PT_MM))
            elif token in STROKE:
                scale = math.sqrt(abs(ctm[0] * ctm[3] - ctm[1] * ctm[2])) or 1.0
                if pattern and points:
                    xs, ys = [p[0] for p in points], [p[1] for p in points]
                    box = (min(xs), min(ys), max(xs), max(ys))
                    found.append((round(width * scale * PT_MM, 2), tuple(round(v * scale * PT_MM, 2) for v in pattern), box))
                points = []
            elif token in NO_PAINT:
                points = []
            nums = []
    return found


def is_chain(pattern: tuple[float, ...]) -> bool:
    """Whether a dash pattern is a long-dash dotted line: a long dash, a gap, a dot (much shorter than the dash), a gap."""
    return len(pattern) == 4 and pattern[0] >= 6 * pattern[2] and pattern[0] > pattern[1]  # noqa: PLR2004
