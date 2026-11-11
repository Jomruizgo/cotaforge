"""SQLite full-text index of the library, kept outside it and safe to delete (it is rebuilt on the next search).

``docs`` has one row per document with what was indexed so far; ``pages_fts`` holds the text of each page and
``notes_fts`` the ficha (one header row per document plus one row per page-map line). FTS rows are addressed by rowid
(``doc number * slot + page``) so a document's rows are removed with a range delete, never a table scan.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SCHEMA_VERSION = "1"
PAGE_SLOT = 100_000
NOTE_SLOT = 1_000
TOKENIZER = "unicode61 remove_diacritics 2"

SCHEMA = f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE docs (
    no INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    rel TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    ficha_stamp TEXT NOT NULL DEFAULT '',
    ficha_text INTEGER,
    ficha_problem TEXT NOT NULL DEFAULT '',
    pages INTEGER,
    indexed INTEGER NOT NULL DEFAULT 0,
    text_pages INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    text_problem TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE pages_fts USING fts5(body, tokenize='{TOKENIZER}');
CREATE VIRTUAL TABLE notes_fts USING fts5(head, body, first UNINDEXED, last UNINDEXED, tokenize='{TOKENIZER}');
"""


@dataclass
class DocRow:
    no: int
    id: str
    rel: str
    mtime_ns: int
    size: int
    ficha_stamp: str
    ficha_text: bool | None
    ficha_problem: str
    pages: int | None
    indexed: int
    text_pages: int
    done: bool
    text_problem: str

    @property
    def has_text(self) -> bool | None:
        """The ficha's word when it has one, else what the extraction found; ``None`` while not known."""
        if self.ficha_text is not None:
            return self.ficha_text
        if self.done and self.pages is not None:
            return self.pages > 0 and self.text_pages * 2 >= self.pages
        return None


@dataclass
class NoteHit:
    no: int
    first: int
    last: int
    head: str
    body: str


@dataclass
class PageHit:
    no: int
    page: int
    snippet: str


def fts_query(words: list[str]) -> str:
    """Every word must appear (a prefix is enough): ``"din"* AND "7979"*``. Words are letters and digits only."""
    return " AND ".join(f'"{word}"*' for word in words)


def _connect(path: Path, root: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        has_meta = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone()
        stored = dict(db.execute("SELECT key, value FROM meta").fetchall()) if has_meta else {}
        if stored.get("version") != SCHEMA_VERSION or stored.get("root") != str(root):
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
                if not name.startswith(("sqlite_", "pages_fts_", "notes_fts_")):
                    db.execute(f'DROP TABLE IF EXISTS "{name}"')
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO meta VALUES (?, ?)", [("version", SCHEMA_VERSION), ("root", str(root))])
            db.commit()
    except BaseException:
        db.close()
        raise
    return db


class LibraryIndex:
    def __init__(self, path: Path, root: Path) -> None:
        try:
            self.db = _connect(path, root)
        except sqlite3.DatabaseError:  # not a database, or damaged: it holds nothing that cannot be rebuilt
            path.unlink(missing_ok=True)
            self.db = _connect(path, root)

    def close(self) -> None:
        self.db.close()

    def rows(self) -> dict[str, DocRow]:
        found = self.db.execute(
            "SELECT no, id, rel, mtime_ns, size, ficha_stamp, ficha_text, ficha_problem, pages, indexed, "
            "text_pages, done, text_problem FROM docs"
        ).fetchall()
        rows = [
            DocRow(
                r[0], r[1], r[2], r[3], r[4], r[5], None if r[6] is None else bool(r[6]), *r[7:11], bool(r[11]), r[12]
            )
            for r in found
        ]
        return {row.id: row for row in rows}

    def add_doc(self, doc_id: str, rel: str, mtime_ns: int, size: int) -> int:
        cursor = self.db.execute(
            "INSERT INTO docs (id, rel, mtime_ns, size) VALUES (?, ?, ?, ?)", (doc_id, rel, mtime_ns, size)
        )
        self.db.commit()
        return int(cursor.lastrowid or 0)

    def _clear_pages(self, no: int) -> None:
        self.db.execute(
            "DELETE FROM pages_fts WHERE rowid BETWEEN ? AND ?", (no * PAGE_SLOT, no * PAGE_SLOT + PAGE_SLOT - 1)
        )

    def _clear_notes(self, no: int) -> None:
        self.db.execute(
            "DELETE FROM notes_fts WHERE rowid BETWEEN ? AND ?", (no * NOTE_SLOT, no * NOTE_SLOT + NOTE_SLOT - 1)
        )

    def drop(self, no: int) -> None:
        self._clear_pages(no)
        self._clear_notes(no)
        self.db.execute("DELETE FROM docs WHERE no = ?", (no,))
        self.db.commit()

    def reset_text(self, no: int, rel: str, mtime_ns: int, size: int) -> None:
        """The document changed on disk: forget its text, to be extracted again."""
        self._clear_pages(no)
        self.db.execute(
            "UPDATE docs SET rel = ?, mtime_ns = ?, size = ?, pages = NULL, indexed = 0, text_pages = 0, done = 0, "
            "text_problem = '' WHERE no = ?",
            (rel, mtime_ns, size, no),
        )
        self.db.commit()

    def set_ficha(
        self,
        no: int,
        stamp: str,
        text: bool | None,
        problem: str,
        head: str,
        notes: Iterable[tuple[int, int, str]],
    ) -> None:
        self._clear_notes(no)
        base = no * NOTE_SLOT
        self.db.execute("INSERT INTO notes_fts (rowid, head, body, first, last) VALUES (?, ?, '', 0, 0)", (base, head))
        for seq, (first, last, what) in enumerate(list(notes)[: NOTE_SLOT - 1], start=1):
            self.db.execute(
                "INSERT INTO notes_fts (rowid, head, body, first, last) VALUES (?, ?, ?, ?, ?)",
                (base + seq, head, what, first, last),
            )
        self.db.execute(
            "UPDATE docs SET ficha_stamp = ?, ficha_text = ?, ficha_problem = ? WHERE no = ?",
            (stamp, None if text is None else int(text), problem, no),
        )
        self.db.commit()

    def add_pages(self, no: int, first_page: int, texts: list[str]) -> int:
        """Index the text of consecutive pages from ``first_page`` (1-based); returns how many had text."""
        with_text = 0
        for offset, text in enumerate(texts):
            page = first_page + offset
            if text.strip() and page < PAGE_SLOT:
                self.db.execute("INSERT INTO pages_fts (rowid, body) VALUES (?, ?)", (no * PAGE_SLOT + page, text))
                with_text += 1
        return with_text

    def set_progress(
        self, no: int, *, pages: int | None, indexed: int, text_pages: int, done: bool, problem: str = ""
    ) -> None:
        self.db.execute(
            "UPDATE docs SET pages = ?, indexed = ?, text_pages = ?, done = ?, text_problem = ? WHERE no = ?",
            (pages, indexed, text_pages, int(done), problem, no),
        )
        self.db.commit()

    def search_notes(self, words: list[str], limit: int) -> list[NoteHit]:
        found = self.db.execute(
            "SELECT rowid, head, body, first, last FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
            (fts_query(words), limit),
        ).fetchall()
        return [NoteHit(r[0] // NOTE_SLOT, int(r[3]), int(r[4]), r[1], r[2]) for r in found]

    def search_pages(self, words: list[str], limit: int) -> list[PageHit]:
        found = self.db.execute(
            "SELECT rowid, snippet(pages_fts, 0, '[', ']', '...', 14) FROM pages_fts WHERE pages_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (fts_query(words), limit),
        ).fetchall()
        return [PageHit(r[0] // PAGE_SLOT, r[0] % PAGE_SLOT, r[1]) for r in found]
