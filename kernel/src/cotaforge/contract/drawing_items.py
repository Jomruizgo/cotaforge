"""The sheet items an agent may put on a drawing (contract/drawing_items.py): which types exist, which fields each
takes, and the checks that need no CAD. The CAD adapter checks the same items again against the part (values, ranges,
references); a test keeps this table equal to the one the FreeCAD worker has.

An item is a plain dict, ``{"type": "hole_dim", "all": true}``; the dimension VALUES are never in it, they come from
the model when the sheet is drawn.
"""

import re
from collections.abc import Sequence
from typing import Any

# built item types and the fields each takes besides ``type`` and ``id``
ITEM_FIELDS: dict[str, frozenset[str]] = {
    "hole_dim": frozenset(
        {
            "at",
            "face",
            "all",
            "centre_marks",
            "dowel",
            "view",
            "offset_mm",
            "side",
            "fit",
            "tol",
            "show",
            "text",
            "thread",
        }
    ),
    "dim": frozenset(
        {
            "kind",
            "from",
            "to",
            "along",
            "face",
            "faces",
            "view",
            "offset_mm",
            "side",
            "elbow_mm",
            "fit",
            "tol",
            "show",
            "arrows",
            "exact",
        }
    ),
    "datum_dims": frozenset(
        {"mode", "x_from", "y_from", "holes", "x_side", "y_side", "at_mm", "view", "offset_mm", "auto"}
    ),
    "note": frozenset({"text", "at_mm", "auto", "view"}),
    "keyway_dims": frozenset(),
    "chamfer_dims": frozenset(),
    "thread_dims": frozenset(),
    "revision": frozenset({"index", "description", "date", "by"}),
    "overall": frozenset({"offset_mm", "side"}),
    "hole_positions": frozenset({"auto"}),
    "view_lines": frozenset({"hidden", "centres"}),
    "line": frozenset({"style", "axis", "from", "to", "view", "extend_mm"}),
    "section": frozenset({"view", "at", "normal", "holes", "letter", "flip", "at_mm", "hatch"}),
    "detail": frozenset({"view", "at", "radius_mm", "scale", "letter", "at_mm"}),
    "aux_view": frozenset({"face", "view", "letter", "at_mm"}),
    "datum": frozenset({"letter", "face", "on", "view", "side", "offset_mm"}),
    "gdt": frozenset({"char", "value", "diameter", "modifier", "datums", "face", "on", "view", "side", "offset_mm"}),
    "finish": frozenset({"ra", "machining", "face", "general", "view", "side", "offset_mm"}),
    "parts_list": frozenset({"rows"}),
    "balloons": frozenset({"view", "side"}),
    "exploded": frozenset({"view", "mode", "axis", "factor", "base", "moves", "trails", "at_mm"}),
    "weld": frozenset(
        {
            "between",
            "kind",
            "size_mm",
            "size_kind",
            "sides",
            "all_around",
            "field",
            "length_mm",
            "pitch_mm",
            "view",
            "side",
            "offset_mm",
        }
    ),
}

# item types the sheet will have, and the phase that builds them: refused until then
PLANNED_ITEMS: dict[str, str] = {}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "dim": ("kind",),
    "note": ("text",),
    "revision": ("index", "description"),
    "detail": ("at", "radius_mm"),
    "aux_view": ("face",),
    "datum": ("letter",),
    "gdt": ("char", "value"),
}

ITEM_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")
SHEET_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,23}$")
MAX_ITEMS_PER_CALL = 50


REMARK_KINDS = ("missing", "ambiguous", "costly", "other")
REMARK_FIELDS = {"kind", "where", "text", "fix"}
MAX_REMARKS = 30
MAX_REMARK_TEXT = 400  # characters of ``text``, ``fix`` and ``where`` (a point of a report is read whole)
REPORT_AUTHORS = ("fabricator", "engineer")
AUTHOR_ALIASES = {"fabricante": "fabricator", "ingeniero": "engineer"}
"""The Spanish names a review author had before 0.10: still accepted as input and read from an old sheet."""


def canonical_author(value: Any) -> Any:
    """The English author for a Spanish one (anything else comes back as it is)."""
    return AUTHOR_ALIASES.get(value, value) if isinstance(value, str) else value


def remark_problems(remarks: Sequence[Any]) -> list[str]:
    """What is wrong with the remarks of a review report (``{"kind": "missing", "where": "...", "text": "...",
    "fix": "..."}``; only ``text`` is required), one message per problem. An empty list of remarks is a valid report."""
    found: list[str] = []
    if len(remarks) > MAX_REMARKS:
        found.append(f"at most {MAX_REMARKS} remarks per report, got {len(remarks)}: group the minor ones")
    for position, remark in enumerate(remarks[:MAX_REMARKS], start=1):
        label = f"remark {position}"
        if not isinstance(remark, dict):
            found.append(
                f'{label} is not an object like {{"kind": "missing", "where": "...", "text": "...", "fix": "..."}}'
            )
            continue
        unknown = sorted(set(remark) - REMARK_FIELDS)
        if unknown:
            found.append(f"{label}: unknown field(s) {', '.join(unknown)}; it takes {', '.join(sorted(REMARK_FIELDS))}")
        kind = remark.get("kind", "other")
        if kind not in REMARK_KINDS:
            found.append(f"{label}: kind must be one of {', '.join(REMARK_KINDS)} (got {kind!r})")
        text = remark.get("text")
        if not isinstance(text, str) or not text.strip():
            found.append(f"{label}: text is required (what is missing, ambiguous or costly)")
        for name in ("where", "text", "fix"):
            value = remark.get(name, "")
            if not isinstance(value, str):
                found.append(f"{label}: {name} must be text")
            elif len(value) > MAX_REMARK_TEXT:
                found.append(f"{label}: {name} has {len(value)} characters, at most {MAX_REMARK_TEXT}")
    return found


def problems(items: Sequence[Any]) -> list[str]:
    """What is wrong with ``items`` without asking the CAD, one message per problem (empty when they look right)."""
    found: list[str] = []
    if len(items) > MAX_ITEMS_PER_CALL:
        found.append(f"at most {MAX_ITEMS_PER_CALL} items per call, got {len(items)}")
    seen: set[str] = set()
    for position, item in enumerate(items, start=1):
        label = f"item {position}"
        if not isinstance(item, dict):
            found.append(f'{label} is not an object like {{"type": "hole_dim", "all": true}}')
            continue
        kind = item.get("type")
        if kind in PLANNED_ITEMS and kind not in ITEM_FIELDS:
            found.append(f"{label}: type {kind!r} is not available yet (planned for {PLANNED_ITEMS[kind]})")
            continue
        if kind not in ITEM_FIELDS:
            found.append(f"{label}: type must be one of {', '.join(sorted(ITEM_FIELDS))} (got {kind!r})")
            continue
        unknown = sorted(set(item) - ITEM_FIELDS[kind] - {"type", "id"})
        if unknown:
            found.append(
                f"{label} ({kind}): unknown field(s) {', '.join(unknown)}; "
                f"it takes {', '.join(sorted(ITEM_FIELDS[kind]))}"
            )
        for name in REQUIRED_FIELDS.get(kind, ()):
            if name not in item:
                found.append(f"{label} ({kind}): {name} is required")
        given = item.get("id")
        if given is not None:
            if not isinstance(given, str) or not ITEM_ID.match(given):
                found.append(
                    f"{label}: id {given!r} must be a letter then letters, digits, - or _ (24 characters at most)"
                )
            elif given in seen:
                found.append(f"{label}: id {given!r} is repeated in this call")
            else:
                seen.add(given)
    return found
