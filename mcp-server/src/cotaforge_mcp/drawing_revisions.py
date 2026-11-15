# ruff: noqa: E501
"""The revision of a sheet that was released and then changed (ISO 7200: the revision table and the revision field).

A first issue carries no revision row. When a sheet whose PDF went out is exported again after the design changed, the sheet
records the change: a row of the title block's own revision table (the letter, what changed with the PREVIOUS value, who
asked for it, the date), the revision field of the title block bumped to the letter, and FreeCAD's own revision marker
(``misc/Revision_Triangle`` with the letter) beside each changed dimension. Earlier rows are never rewritten.

What the harness remembers is the release: at every sheet PDF export it keeps what the dimensions of the sheet stated and
the letters the revision table had, as an answer stored IN the sheet (``req.released``, inside the FreeCAD document, like every
answer of the review): no file outside the document. It is review state only: the sheet, its revision rows and its markers are
native objects that stand without it, and losing it only means the next export is read as a first issue.
"""

import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from cotaforge.contract import AdapterError, AnnotateDrawingRequest, DrawingSheetInfo
from pydantic import ValidationError

from cotaforge_mcp.session import Session

PREFIX = "rev_"
"""The id of the revision item the harness adds (``rev_A``)."""
MAX_DESCRIPTION = 80
"""What the field of the revision item holds."""
COLUMN_CHARS = {"narrow": (21, 8), "wide": (31, 9)}
"""(description, requester): how many characters the title block's revision columns hold before the text runs into the next field (the
template's own ``data-maxwidth`` over the width of a character at its size; the review flags an overflow as title_block_overflow). A4 is narrow, the
larger papers wide. The text is cut to the column (the reason is added only if it fits)."""
WIDE_TEMPLATES = ("A3", "A2", "A1", "A0")
VALUE_TOLERANCE_MM = 0.005
SYMBOL = {"diameter": "Ø", "hole": "Ø", "radius": "R"}
KIND_WORDS = {
    "es": {"hole": "agujero", "diameter": "diámetro", "radius": "radio", "distance": "cota", "overall": "total", "stage": "etapa"},
    "en": {"hole": "hole", "diameter": "diameter", "radius": "radius", "distance": "dimension", "overall": "overall", "stage": "stage"},
}
WORDS = {"es": ("a", "cota nueva", "cota eliminada", "etapa", "ingeniero", "más"), "en": ("to", "new dimension", "dimension removed", "stage", "engineer", "more")}

clock: Callable[[], float] = time.time
"""The server's clock for the date of a row (a test swaps it)."""


RELEASE_ID = "req.released"
"""The id of the answer that holds what the sheet stated when its PDF went out (stored in the sheet's own spec)."""


def released_of(info: DrawingSheetInfo) -> dict[str, Any] | None:
    """What the sheet stated at its last release (``None``: it never was released, or the record is gone)."""
    answer = info.answers.get(RELEASE_ID)
    if answer is None:
        return None
    try:
        data = json.loads(answer.text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _num(value: float, comma: bool) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") if comma else text




def snapshot(info: DrawingSheetInfo) -> dict[str, Any]:
    """What the sheet states now: the values each dimension item states (the same item may call out several holes: its
    values are kept as a list, compared as a set), the size each process-stage dimension states, and the letters of the
    revision table."""
    dims: dict[str, dict[str, Any]] = {}
    for m in info.measures:
        row = dims.setdefault(f"{m.id}|{m.kind}", {"id": m.id, "kind": m.kind, "values": []})
        row["values"] = sorted([*row["values"], m.value_mm])
    for item in info.items:
        if item.type == "dim" and item.spec.get("stage") and item.spec.get("size_mm") is not None:
            dims[f"{item.id}|stage"] = {
                "id": item.id,
                "kind": "stage",
                "values": [float(item.spec["size_mm"])],
                "stage": str(item.spec["stage"]),
            }
    letters = [str(i.spec.get("index", "")) for i in info.items if i.type == "revision"]
    return {"dims": dims, "letters": letters}


def record_release(session: Session, info: DrawingSheetInfo) -> None:
    """The sheet's PDF went out: remember what it stated and the revision letters it carried (the next export compares)."""
    session.adapter.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="drawing_release",
            action="answer",
            sheet=info.sheet,
            id=RELEASE_ID,
            verdict="applies",
            text=json.dumps(snapshot(info), separators=(",", ":"), sort_keys=True, ensure_ascii=False),
        )
    )
    session.dirty = True


def _describe(kind: str, item: str, before: float | None, after: float | None, stage: str, language: str) -> str:
    to, new, gone, _, _, _ = WORDS[language]
    comma = language == "es"
    sym = SYMBOL.get(kind, "")
    word = KIND_WORDS[language].get(kind, kind)
    where = f"{item} ({stage})" if stage else (word if kind == "hole" else f"{word} {item}")  # a hole callout is read as the hole it is
    if before is None and after is not None:
        return f"{where}: {new} {sym}{_num(after, comma)}"
    if after is None and before is not None:
        return f"{where}: {gone} ({sym}{_num(before, comma)})"
    assert before is not None
    assert after is not None
    return f"{where}: {sym}{_num(before, comma)} {to} {sym}{_num(after, comma)}"


def changes(old: dict[str, Any], new: dict[str, Any], language: str) -> tuple[list[str], list[str]]:
    """(what changed since the release, one phrase each with the PREVIOUS value; the ids of the dimension items to mark).
    The values of one item are compared as a set: the value that left is paired with the value that came in its place."""
    phrases: list[str] = []
    marks: list[str] = []
    before, after = old.get("dims", {}), new.get("dims", {})
    for key in sorted({*before, *after}):
        row = after.get(key) or before.get(key)
        left = [float(v) for v in before.get(key, {}).get("values", [])]
        came: list[float] = []
        for value in (float(v) for v in after.get(key, {}).get("values", [])):
            same = next((x for x in left if abs(x - value) <= VALUE_TOLERANCE_MM), None)
            if same is None:
                came.append(value)
            else:
                left.remove(same)
        pairs = list(zip(left, came, strict=False))
        stage = str(row.get("stage", ""))
        for was, now in pairs:
            phrases.append(_describe(row["kind"], row["id"], was, now, stage, language))
        phrases += [_describe(row["kind"], row["id"], was, None, stage, language) for was in left[len(pairs) :]]
        phrases += [_describe(row["kind"], row["id"], None, now, stage, language) for now in came[len(pairs) :]]
        if came and not any(m.split(":")[0] == row["id"] for m in marks):
            marks.append(f"{row['id']}:{came[0]:g}")  # the id and the new value: which dimension of the item is the changed one
    return phrases, marks


def description(phrases: list[str], language: str, limit: int = MAX_DESCRIPTION) -> str:
    """The text of the row (the field holds 80 characters): the changes joined, or the first one and how many more."""
    text = "; ".join(phrases)
    if len(text) <= limit:
        return text
    more = f" (+{len(phrases) - 1} {WORDS[language][5]})"
    return phrases[0][: limit - len(more)].rstrip() + more if len(phrases) > 1 else text[:limit]


def next_letter(letters: list[str]) -> str:
    """The letter after the highest single capital the table has (``A`` for the first change of a released sheet)."""
    singles = [ord(x) for x in letters if len(x) == 1 and "A" <= x <= "Z"]
    return chr(max(singles) + 1) if singles else "A"


def revise(
    session: Session,
    info: DrawingSheetInfo,
    refresh: Callable[[], DrawingSheetInfo | None],
    language: str,
    by: str | None = None,
    reason: str | None = None,
) -> tuple[DrawingSheetInfo, list[str]]:
    """Before a sheet that was released is exported again: when its dimensions no longer state what they stated at the
    release, add the revision row (and the markers) for the change. Nothing changed, a first issue, or a revision somebody
    wrote by hand since the release: nothing is added. Returns the sheet as it is after and what was done (for the reply)."""
    released = released_of(info)
    if not released or "annotate_drawing" not in session.capabilities.tools:
        return info, []
    held = {str(i.spec.get("index", "")) for i in info.items if i.type == "revision" and not i.id.startswith(PREFIX)}
    if held - set(released.get("letters", [])):
        return info, []  # a revision somebody wrote by hand since the release: it stands, the harness adds none
    phrases, marks = changes(released, snapshot(info), language)
    letters = [str(x) for x in released.get("letters", [])]
    pending = [i for i in info.items if i.id.startswith(PREFIX) and i.type == "revision" and str(i.spec.get("index", "")) not in letters]
    if not phrases:
        return info, []
    letter = next_letter(letters)
    limit, by_limit = COLUMN_CHARS["wide" if info.template in WIDE_TEMPLATES else "narrow"]
    who = " ".join((by or "").split())[:by_limit].strip() or (WORDS[language][4] if by_limit >= len(WORDS[language][4]) else "ing.")
    text = description(phrases, language, limit)
    if reason and reason.strip() and len(text) + 2 + len(reason.strip()) <= limit:
        text = f"{text}; {' '.join(reason.split())}"
    item = {
        "type": "revision",
        "id": f"{PREFIX}{letter}",
        "index": letter,
        "description": text,
        "date": datetime.fromtimestamp(clock()).strftime("%Y-%m-%d"),
        "by": who,
        **({"at": marks[:6]} if marks else {}),
    }
    same = [
        i
        for i in pending
        if all(i.spec.get(k) == item.get(k) for k in ("index", "description", "by")) and i.spec.get("at") == item.get("at")
    ]
    if same:
        return info, [f"revision {letter} is already on the sheet (not released yet): {item['description']}"]
    try:
        if pending:  # the row of an earlier try that was not released: it is rewritten, a released one never is
            session.adapter.annotate_drawing(
                AnnotateDrawingRequest(
                    operation_id="drawing_revise", action="remove", sheet=info.sheet, ids=tuple(i.id for i in pending)
                )
            )
        session.adapter.annotate_drawing(
            AnnotateDrawingRequest(operation_id="drawing_revise", action="add", sheet=info.sheet, items=(item,))
        )
    except (AdapterError, ValidationError):
        return info, []
    session.dirty = True
    fresh = refresh() or info
    return fresh, [f"revision {letter} recorded in the title block ({who}, {item['date']}): {text}"]
