# ruff: noqa: E501
"""``note`` and ``revision`` (no FreeCAD import).

Notes are numbered lines in a block at the bottom left of the drawing area (``NOTAS:`` above them), or free text at a
page position (``at_mm``). A revision is a row of the revision table in the title block (the template has five rows;
the last five revisions show) and sets the ``revision_index`` field to the latest one.
"""

import re
import textwrap
from typing import Any

from sheet_context import (
    SECTION_LABEL,
    Context,
    ItemError,
    ItemType,
    check_fields,
    v_enum,
    v_point2,
    v_str,
)
from sheet_model import TEXT, Text, text_width
from sheet_units import NativeNote

NOTE_CHARS = 70  # a note longer than this wraps
VIEW_NOTE_CHARS = 40  # a note that belongs to a view is a short block under it
LINE_STEP_MM = 5.0
NOTE_HEIGHT = 3.5
BLOCK_MARGIN_MM = 2.0

AUTO_NOTES = ("thickness",)  # the notes the sheet writes by itself (``drawing`` adds and takes them off)


def _v_section(value: Any) -> str:
    if isinstance(value, str) and SECTION_LABEL.match(value):
        return value
    msg = "must be the label of a section view (A-A): the note is written under it"
    raise ItemError(msg)


NOTE_CHECKS = {"text": v_str(240), "at_mm": v_point2, "auto": v_enum(*AUTO_NOTES), "view": _v_section}
REVISION_CHECKS = {"index": v_str(4), "description": v_str(80), "date": v_str(12), "by": v_str(12)}


def check_note(spec: dict[str, Any]) -> dict[str, Any]:
    out = check_fields(spec, NOTE_CHECKS)
    if "auto" in out and ("text" in out or "at_mm" in out or "view" in out):
        msg = "auto is a note the sheet writes itself (thickness): it takes no text, no at_mm and no view"
        raise ItemError(msg)
    if "view" in out and "at_mm" in out:
        msg = "a note on a view goes under it: give view or at_mm, not both"
        raise ItemError(msg)
    if "auto" not in out and "text" not in out:
        msg = "text is required"
        raise ItemError(msg)
    return out


def note_text(ctx: Context, spec: dict[str, Any]) -> str:
    """What a note says: its text, or -- for the notes the sheet writes itself -- the thickness of a part drawn in ONE view
    (measured on the model, along the way that view looks)."""
    kind = spec.get("auto")
    if kind is None:
        return str(spec["text"])
    extent = ctx.extras.get("extent_mm")
    if extent is None or not ctx.views:
        msg = "the thickness is measured on the part: the sheet has no part geometry here"
        raise ItemError(msg)
    view = next(iter(ctx.views.values()))
    thickness = sum(abs(a) * e for a, e in zip(view.direction, extent, strict=True))
    return ctx.say(f"Espesor {ctx.num(round(thickness, 2))}", f"Thickness {ctx.num(round(thickness, 2))}")


THICKNESS_NOTE = re.compile(r"\b(?:espesor|thickness)\b\s*[:=]?\s*\d", re.IGNORECASE)


def states_thickness(text: str) -> bool:
    """Whether a note says the thickness itself (``Espesor 19 mm``): the sheet's own thickness note is then left out, never
    repeated."""
    return THICKNESS_NOTE.search(text) is not None


def _rank(spec: dict[str, Any]) -> int:
    """The order of the numbered notes: the part's thickness first, then the engineer's."""
    return {"thickness": 0}.get(str(spec.get("auto")), 1)


def check_revision(spec: dict[str, Any]) -> dict[str, Any]:
    return check_fields(spec, REVISION_CHECKS, required=("index", "description"))


def _wrap(number: int, text: str) -> list[str]:
    lines = textwrap.wrap(f"{number}. {text}", NOTE_CHARS, subsequent_indent="    ")
    return lines or [f"{number}. "]


def prepare_notes(ctx: Context, specs: list[dict[str, Any]]) -> None:
    """How many lines the numbered block takes, so it can grow upward from the bottom with the header on top."""
    plan: dict[str, tuple[int, int]] = {}
    total = 0
    widest = 0.0
    specs = sorted(
        (s for s in specs if "view" not in s), key=_rank
    )  # a stable sort: the engineer's notes keep the order they were added in; a note on a view is not numbered
    for number, spec in enumerate(specs, start=1):
        try:
            wrapped = _wrap(number, note_text(ctx, spec))
        except ItemError:  # a note that cannot be written is reported when it is drawn
            wrapped = [f"{number}. "]
        count = len(wrapped)
        if "at_mm" not in spec:
            plan[spec["id"]] = (total, count)
            total += count
            widest = max([widest, *(text_width(line, NOTE_HEIGHT) for line in wrapped)])
    ctx.extras["note_width"] = widest
    ctx.extras["note_plan"] = plan
    ctx.extras["note_lines"] = total
    ctx.extras["note_numbers"] = {spec["id"]: n for n, spec in enumerate(specs, start=1)}


BLOCK_STEP_MM = 20.0


def _block_x(ctx: Context, left: float, low: float, total: int) -> float:
    """Where the numbered block stands: at the left of the drawing area, or -- when dimensions already took that room --
    the first place to the right that is free (the block moves as a whole)."""
    width = ctx.extras["note_width"]
    x = left + BLOCK_MARGIN_MM
    while x + width < ctx.area[2]:
        if ctx.free((x, low, x + width, low + BLOCK_MARGIN_MM + (total + 1) * LINE_STEP_MM)):
            return x
        x += BLOCK_STEP_MM
    return left + BLOCK_MARGIN_MM


def _draw_view_note(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """A note that belongs to a section view: a short block of lines under the view's label, left-aligned with the view
    (else to its right) where nothing else is; it is not numbered."""
    view = ctx.view(spec["view"])
    rect = ctx.extras["view_rects"][ctx.extras["section_items"][spec["view"]]]
    lines = textwrap.wrap(note_text(ctx, spec), VIEW_NOTE_CHARS) or [""]
    height = len(lines) * LINE_STEP_MM + BLOCK_MARGIN_MM
    width = max(text_width(line, NOTE_HEIGHT) for line in lines)
    below = (view.box[0], rect[1] - height, view.box[0] + width, rect[1])
    beside = (rect[2] + 1.0, view.box[3] - height, rect[2] + 1.0 + width, view.box[3])
    for place in (below, beside):
        if ctx.inside(place) and ctx.free(place):
            break
    else:
        msg = "there is no free place under or beside the view for this note: give it at_mm=[x, y] instead of view"
        raise ItemError(msg)
    x, top = place[0], place[3] - BLOCK_MARGIN_MM - NOTE_HEIGHT
    ctx.add([Text(x, top - n * LINE_STEP_MM, line, NOTE_HEIGHT, TEXT, "left") for n, line in enumerate(lines)])
    ctx.reserve(place)
    ctx.used_view(view.name)
    ctx.native(NativeNote(str(spec["id"]), "n", tuple(lines), (x, top + NOTE_HEIGHT), NOTE_HEIGHT, LINE_STEP_MM))
    return "ok", f"note under {spec['view']}"


def draw_note(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    if "view" in spec:
        return _draw_view_note(ctx, spec)
    number = ctx.extras.get("note_numbers", {}).get(spec["id"], ctx.notes + 1)
    ctx.notes += 1
    lines = _wrap(number, note_text(ctx, spec))
    left, low, _, _ = ctx.area
    entities: list[Text] = []
    if "at_mm" in spec:
        x, y = spec["at_mm"]
        for index, line in enumerate(lines):
            entities.append(Text(x, y - index * LINE_STEP_MM, line, NOTE_HEIGHT, TEXT, "left"))
        ctx.add(entities)
        ctx.native(NativeNote(str(spec["id"]), "n", tuple(lines), (x, y + NOTE_HEIGHT), NOTE_HEIGHT, LINE_STEP_MM))
        return "ok", f"note {number} at page position [{ctx.num(x)}, {ctx.num(y)}]"
    plan = ctx.extras.get("note_plan", {})
    if spec["id"] not in plan:
        msg = "internal: the notes were not prepared"
        raise ItemError(msg)
    offset, _ = plan[spec["id"]]
    total = ctx.extras["note_lines"]
    x = ctx.extras.get(
        "note_x"
    )  # the block stands where the first note drawn found room (the notes follow in any order)
    if x is None:
        x = _block_x(ctx, left, low, total)
    ctx.extras["note_x"] = x
    top_line = low + BLOCK_MARGIN_MM + (total - offset - 1) * LINE_STEP_MM
    for index, line in enumerate(lines):
        entities.append(Text(x, top_line - index * LINE_STEP_MM, line, NOTE_HEIGHT, TEXT, "left"))
    shown = list(lines)
    first_baseline = top_line
    if offset == 0:  # the first note carries the header above the whole block
        header = ctx.say("NOTAS:", "NOTES:")
        entities.append(Text(x, low + BLOCK_MARGIN_MM + total * LINE_STEP_MM, header, NOTE_HEIGHT, TEXT, "left"))
        ctx.reserve((x, low, x + ctx.extras["note_width"], low + BLOCK_MARGIN_MM + (total + 1) * LINE_STEP_MM))
        shown = [header, *lines]
        first_baseline = low + BLOCK_MARGIN_MM + total * LINE_STEP_MM
    ctx.add(entities)
    ctx.native(
        NativeNote(str(spec["id"]), "n", tuple(shown), (x, first_baseline + NOTE_HEIGHT), NOTE_HEIGHT, LINE_STEP_MM)
    )
    return "ok", f"note {number} in the notes block"


def prepare_revisions(ctx: Context, specs: list[dict[str, Any]]) -> None:
    ctx.revisions = [
        {
            "index": spec["index"],
            "description": spec["description"],
            "date": spec.get("date", ""),
            "by": spec.get("by", ""),
        }
        for spec in specs
    ]


def draw_revision(ctx: Context, spec: dict[str, Any]) -> tuple[str, str]:
    """The row is part of the title block, which is composed from ``ctx.revisions``; nothing is drawn here."""
    shown = ctx.revisions[-5:]
    if not any(r["index"] == spec["index"] for r in shown):
        return "warning", f"revision {spec['index']} is not shown: the title block has room for the last 5 revisions"
    return "ok", f"revision {spec['index']} in the revision table"


ITEMS = (
    ItemType("note", "n", check_note, draw_note, frozenset(NOTE_CHECKS), prepare_notes, phase=2),
    ItemType("revision", "r", check_revision, draw_revision, frozenset(REVISION_CHECKS), prepare_revisions, phase=2),
)
