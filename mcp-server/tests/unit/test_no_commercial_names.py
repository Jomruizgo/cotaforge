"""The repository names no commercial CAD software: it is open source and neutral.

Write "commercial CAD software" (or an invented name such as ``ExternalCAD`` in a fixture) instead. Parasolid (``x_t`` /
``x_b``) is a file format, not a product, and may stay. The only file allowed to hold the names is this one, which lists
them.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ALLOWED = {Path(__file__).resolve().relative_to(ROOT).as_posix()}
BINARY = {".ico", ".png", ".jpg", ".jpeg", ".gif", ".exe", ".zip", ".pdf", ".fcstd", ".whl", ".pyc", ".svg"}

NAMES = (
    r"solid\s?works",
    r"solid\s?edge",
    r"catia",
    r"\bcreo\b",
    r"fusion\s?360",
    r"onshape",
    r"mastercam",
    r"autocad",
    r"draftsight",
    r"autodesk",
    r"dassault",
    r"siemens",
    r"\binventor\b",  # not ``View3DInventor``, the FreeCAD class
    r"\.sld(?:prt|asm|drw)\b",
    r"\bsld(?:prt|asm|drw)\b",
)
PATTERN = re.compile("|".join(NAMES), re.IGNORECASE)
# ``NX`` is also a constant of the tests (the -X axis): it counts only next to another name, in prose.
NX_IN_PROSE = re.compile(r"(?:\b(?:or|and)|Siemens) NX\b|\bNX (?:or|and)\b")


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("not a git checkout")
    return [name for name in out.decode("utf-8").split("\0") if name]


def test_no_commercial_cad_name_in_the_repository() -> None:
    found: list[str] = []
    for name in _tracked_files():
        if name in ALLOWED:
            continue
        if PATTERN.search(name):
            found.append(f"{name}: in the file name")
        path = ROOT / name
        if path.suffix.lower() in BINARY or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if PATTERN.search(line) or NX_IN_PROSE.search(line):
                found.append(f"{name}:{number}: {line.strip()[:100]}")
    assert not found, "commercial CAD names (write 'commercial CAD software' instead):\n" + "\n".join(found[:40])
