"""The ficha: a small markdown file next to each document with what the agent decided about it.

YAML frontmatter (title, topic, standard, edition, author, pages, text, original, filed, sha256) and a page map, one
``- p. 2-3: what`` line per range. It is meant to be read and edited by hand, so parsing is tolerant: unknown keys and
free lines are kept, and only a ficha that cannot be understood at all raises ``FichaError``.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

FRONTMATTER = re.compile(r"\A﻿?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)(.*)", re.DOTALL)
NOTE_LINE = re.compile(r"^\s*[-*]\s+(?:p|pp|pág|pag)\.?\s*(\d+)(?:\s*[-–—]\s*(\d+))?\s*:\s*(.+?)\s*$", re.IGNORECASE)
FIELDS = ("title", "topic", "standard", "edition", "author", "pages", "text", "original", "filed", "sha256")


class FichaError(ValueError):
    """The ficha cannot be understood (no frontmatter, bad YAML, no title)."""


@dataclass
class PageNote:
    first: int
    last: int
    what: str

    @property
    def label(self) -> str:
        return f"p. {self.first}" if self.first == self.last else f"p. {self.first}-{self.last}"

    def covers(self, page: int) -> bool:
        return self.first <= page <= self.last


@dataclass
class Ficha:
    title: str
    topic: str = ""
    standard: str | None = None
    edition: str | None = None
    author: str | None = None
    pages: int | None = None
    text: bool | None = None
    """``False`` = scanned or an image: the full text does not cover it. ``None`` = not known."""
    original: str | None = None
    filed: str | None = None
    sha256: str | None = None
    notes: list[PageNote] = field(default_factory=list)
    extra: list[str] = field(default_factory=list)
    """Free lines of the body that are not page notes: kept when the ficha is written again."""

    def reference(self) -> str:
        """What a citation names: the standard (or the title) and the edition."""
        name = self.standard or self.title
        return f"{name}, {self.edition}" if self.edition else name

    def cite(self, page: str = "N") -> str:
        return f"«{self.reference()}, p. {page}»"

    def merge_notes(self, new: list[PageNote]) -> None:
        """Add ranges; the same range replaces its text and an empty text removes it."""
        by_range = {(n.first, n.last): n for n in self.notes}
        for note in new:
            if note.what:
                by_range[(note.first, note.last)] = note
            else:
                by_range.pop((note.first, note.last), None)
        self.notes = sorted(by_range.values(), key=lambda n: (n.first, n.last))


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def parse_ficha(source: str) -> Ficha:
    match = FRONTMATTER.match(source)
    if match is None:
        msg = "no frontmatter (the file must start with a --- block holding title and topic)"
        raise FichaError(msg)
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        msg = f"the frontmatter is not valid YAML ({str(error).splitlines()[0][:80]})"
        raise FichaError(msg) from error
    if not isinstance(data, dict):
        msg = "the frontmatter is not a list of fields"
        raise FichaError(msg)
    title = _text(data.get("title"))
    if title is None:
        msg = "the frontmatter has no title"
        raise FichaError(msg)
    flag = data.get("text")
    ficha = Ficha(
        title=title,
        topic=_text(data.get("topic")) or "",
        standard=_text(data.get("standard")),
        edition=_text(data.get("edition")),
        author=_text(data.get("author")),
        pages=_int(data.get("pages")),
        text=flag if isinstance(flag, bool) else None,
        original=_text(data.get("original")),
        filed=_text(data.get("filed")),
        sha256=_text(data.get("sha256")),
    )
    for line in match.group(2).splitlines():
        note = NOTE_LINE.match(line)
        if note is None:
            if line.strip():
                ficha.extra.append(line.rstrip())
            continue
        first, last = int(note.group(1)), int(note.group(2) or note.group(1))
        if first > last:
            first, last = last, first
        ficha.notes.append(PageNote(first, last, note.group(3)))
    ficha.notes.sort(key=lambda n: (n.first, n.last))
    return ficha


def render_ficha(ficha: Ficha) -> str:
    values = {name: getattr(ficha, name) for name in FIELDS}
    front = {name: value for name, value in values.items() if value not in (None, "")}
    head = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, width=10_000)
    body = [f"- {note.label}: {note.what}" for note in ficha.notes] + ficha.extra
    return f"---\n{head}---\n" + "\n".join(body) + ("\n" if body else "")
