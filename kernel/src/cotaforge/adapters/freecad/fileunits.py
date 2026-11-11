"""The length unit a STEP or IGES file DECLARES, read from the file itself.

FreeCAD (OpenCASCADE) converts every imported file to millimetres, so the geometry is right whatever the file says; this
only lets the harness tell the engineer what the supplier's file used (a part delivered in inches is a common surprise).
Nothing here is guessed: when the file does not say, the answer is ``None``.
"""

import re
from pathlib import Path

_SI_PREFIX = {"MILLI": "mm", "CENTI": "cm", "MICRO": "um", "KILO": "km", "": "m"}
_CONVERSION_NAMES = {
    "INCH": "inch",
    "IN": "inch",
    "FOOT": "foot",
    "FT": "foot",
    "MILE": "mile",
    "MILLIMETRE": "mm",
    "MILLIMETER": "mm",
    "METRE": "m",
    "METER": "m",
}
_IGES_FLAGS = {
    1: "inch",
    2: "mm",
    4: "foot",
    5: "mile",
    6: "m",
    7: "km",
    8: "mil",
    9: "um",
    10: "cm",
    11: "microinch",
}
_IGES_NAMES = {"IN": "inch", "INCH": "inch", "MM": "mm", "FT": "foot", "MI": "mile", "M": "m", "KM": "km"}
_IGES_NAMES.update({"MIL": "mil", "UM": "um", "CM": "cm", "UIN": "microinch"})
_QUOTED = re.compile(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']*)'", re.IGNORECASE)
_SI = re.compile(r"SI_UNIT\s*\(\s*(?:\.(\w+)\.|\$)\s*,\s*\.METRE\.", re.IGNORECASE)
_HOLLERITH = re.compile(r"\s*(\d+)H")
_MAX_STEP_STATEMENT = 4000


def _step_unit(statement: str) -> str | None:
    named = _QUOTED.search(statement)
    if named is not None:
        name = named.group(1).strip().upper()
        return _CONVERSION_NAMES.get(name, name.lower())
    si = _SI.search(statement)
    if si is not None:
        return _SI_PREFIX.get((si.group(1) or "").upper())
    return None


def _step_length_unit(path: Path) -> str | None:
    """The first ``LENGTH_UNIT`` entity of the file (a statement may span several lines)."""
    buffer: list[str] = []
    with path.open("r", encoding="latin-1", errors="replace") as handle:
        for line in handle:
            if buffer or "LENGTH_UNIT" in line:
                buffer.append(line.strip())
                text = "".join(buffer)
                if ";" in line or len(text) > _MAX_STEP_STATEMENT:
                    if "LENGTH_UNIT" in text:
                        return _step_unit(text)
                    buffer = []
    return None


def _iges_tokens(text: str) -> list[str]:
    """The parameters of the IGES global section (Hollerith strings read by their length)."""
    delimiter, record = ",", ";"
    tokens: list[str] = []
    position = 0
    if text.startswith("1H") and len(text) > 2:
        delimiter = text[2]
        tokens.append(delimiter)
        position = 3
        if text[position : position + 1] == delimiter:
            position += 1
    while position <= len(text):
        match = _HOLLERITH.match(text, position)
        if match is not None:
            start = match.end()
            tokens.append(text[start : start + int(match.group(1))])
            position = start + int(match.group(1))
        else:
            end = position
            while end < len(text) and text[end] not in (delimiter, record):
                end += 1
            tokens.append(text[position:end].strip())
            position = end
        if position >= len(text) or text[position] == record:
            break
        position += 1  # the delimiter
        if len(tokens) == 2 and tokens[1] and tokens[1] != record and len(tokens[1]) == 1:
            record = tokens[1]
    return tokens


def _iges_length_unit(path: Path) -> str | None:
    lines: list[str] = []
    with path.open("r", encoding="latin-1", errors="replace") as handle:
        for line in handle:
            if len(line) >= 73 and line[72] == "G":
                lines.append(line[:72].rstrip("\r\n"))
            elif lines:
                break  # the global section is one block
    tokens = _iges_tokens("".join(lines))  # the 72 columns are significant: a string may continue on the next line
    if len(tokens) < 14 or not tokens[13].strip().isdigit():
        return None
    flag = int(tokens[13].strip())
    if flag == 3:
        return _IGES_NAMES.get(tokens[14].strip().upper()) if len(tokens) > 14 else None
    return _IGES_FLAGS.get(flag)


def declared_length_unit(path: str | Path) -> str | None:
    """``mm``, ``cm``, ``m``, ``inch``... as the STEP (``.step``/``.stp``) or IGES (``.iges``/``.igs``) file declares,
    ``None`` when it does not say or cannot be read."""
    target = Path(path)
    suffix = target.suffix.lower()
    try:
        if suffix in (".step", ".stp"):
            return _step_length_unit(target)
        if suffix in (".iges", ".igs"):
            return _iges_length_unit(target)
    except OSError:
        return None
    return None
