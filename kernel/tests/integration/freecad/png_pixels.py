"""A PNG read back to pixels, with no imaging library: what a test counts when it asks what the 3D view really shows.

Only what Qt's ``saveImage`` writes (8 bits per channel, RGB or RGBA, not interlaced). Colours are classified by hue
(``is_orange`` ...) and not compared to one exact value, because the view shades every face with its own light."""

import struct
import zlib
from collections.abc import Callable
from pathlib import Path

Pixel = tuple[int, int, int]


def read_rgb(path: Path) -> tuple[int, int, list[bytes]]:
    """``(width, height, rows)``, each row ``width * 3`` bytes of RGB (alpha dropped)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos, idat = 8, b""
    width = height = depth = ctype = interlace = 0
    while pos < len(data):
        size, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + size]
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body[:13])
        elif kind == b"IDAT":
            idat += body
        pos += 12 + size
    assert depth == 8 and ctype in (2, 6) and interlace == 0, (depth, ctype, interlace)
    bpp = 3 if ctype == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows: list[bytes] = []
    prev = bytearray(stride)
    at = 0
    for _ in range(height):
        kind_of_filter = raw[at]
        line = bytearray(raw[at + 1 : at + 1 + stride])
        at += 1 + stride
        for x in range(stride):
            left = line[x - bpp] if x >= bpp else 0
            up = prev[x]
            corner = prev[x - bpp] if x >= bpp else 0
            if kind_of_filter == 1:
                line[x] = (line[x] + left) & 255
            elif kind_of_filter == 2:
                line[x] = (line[x] + up) & 255
            elif kind_of_filter == 3:
                line[x] = (line[x] + (left + up) // 2) & 255
            elif kind_of_filter == 4:
                guess = left + up - corner
                dl, du, dc = abs(guess - left), abs(guess - up), abs(guess - corner)
                line[x] = (line[x] + (left if dl <= du and dl <= dc else (up if du <= dc else corner))) & 255
        prev = line
        rows.append(bytes(line[i] for i in range(stride) if bpp == 3 or i % 4 != 3))
    return width, height, rows


def count(path: Path, is_colour: Callable[[int, int, int], bool]) -> int:
    """How many pixels of the picture satisfy ``is_colour(r, g, b)``."""
    _, _, rows = read_rgb(path)
    return sum(1 for row in rows for x in range(0, len(row), 3) if is_colour(row[x], row[x + 1], row[x + 2]))


def is_orange(r: int, g: int, b: int) -> bool:
    """The highlight (255, 140, 0), lit."""
    return r > 150 and b < r * 0.35 and 0.3 < g / r < 0.8


def is_grey(r: int, g: int, b: int) -> bool:
    """The part's own colour (114, 121, 128), lit."""
    return 90 < r < 150 and 90 < g < 150 and 90 < b < 160 and abs(r - b) < 40


def is_pink(r: int, g: int, b: int) -> bool:
    """A precision hole's colour (255, 128, 128), lit."""
    return r > 170 and 80 < g < 190 and 80 < b < 190 and r - g > 40 and abs(g - b) < 40
