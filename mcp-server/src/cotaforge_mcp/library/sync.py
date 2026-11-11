"""Keeps the index equal to the library folder: new or changed files in, removed files out, within a time budget.

It runs at the start of every search. Fichas are cheap and always brought up to date; the full text of a PDF is
extracted in steps of a few pages, so a document that does not fit in one call is continued by the next one.
"""

import time
from dataclasses import dataclass, field

from cotaforge_mcp.library.extract import (
    IMAGE_SUFFIXES,
    TEXT_SUFFIXES,
    TextUnavailableError,
    has_words,
    open_pdf,
    page_text,
    read_text_file,
)
from cotaforge_mcp.library.ficha import FichaError
from cotaforge_mcp.library.index import PAGE_SLOT, DocRow, LibraryIndex
from cotaforge_mcp.library.store import DocFile, Library

REFRESH_BUDGET_S = 20.0
PAGES_PER_STEP = 25


@dataclass
class Refresh:
    pending: int = 0
    """Documents whose full text is not indexed yet (the next search continues)."""
    no_pypdf: int = 0
    """PDFs that only their ficha can find, because ``pypdf`` is not installed."""
    problems: list[str] = field(default_factory=list)


def _index_ficha(library: Library, index: LibraryIndex, doc: DocFile, no: int) -> None:
    problem, ficha = "", None
    try:
        ficha = library.load_ficha(doc)
    except FichaError as error:
        problem = str(error)
    words = [doc.id.replace("/", " ").replace("_", " ")]
    notes = []
    if ficha is not None:
        words += [part for part in (ficha.title, ficha.standard, ficha.edition, ficha.author) if part]
        notes = [(note.first, note.last, note.what) for note in ficha.notes]
    index.set_ficha(no, doc.ficha_stamp, ficha.text if ficha else None, problem, " ".join(words), notes)


class Deadline:
    """True once the budget is spent, but never before a first step was taken: every call makes some progress."""

    def __init__(self, budget_s: float) -> None:
        self.end = time.monotonic() + budget_s
        self.stepped = False

    def __call__(self) -> bool:
        if self.stepped and time.monotonic() >= self.end:
            return True
        self.stepped = True
        return False


def _index_text(index: LibraryIndex, row: DocRow, doc: DocFile, out_of_time: Deadline, report: Refresh) -> bool:
    """Extract the text of one document; ``False`` when the budget ran out in the middle of it."""
    suffix = doc.path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        index.set_progress(row.no, pages=1, indexed=1, text_pages=0, done=True)
        return True
    if suffix in TEXT_SUFFIXES:
        text = read_text_file(doc.path)
        index.add_pages(row.no, 1, [text])
        index.set_progress(row.no, pages=1, indexed=1, text_pages=int(has_words(text)), done=True)
        return True
    try:
        reader = open_pdf(doc.path)
    except TextUnavailableError as error:
        if error.missing_library:
            report.no_pypdf += 1
        else:
            index.set_progress(row.no, pages=None, indexed=0, text_pages=0, done=True, problem=str(error))
        return True
    total = len(reader.pages)
    limit = min(total, PAGE_SLOT - 1)
    indexed, text_pages = row.indexed, row.text_pages
    while indexed < limit:
        if out_of_time():
            return False
        chunk = [page_text(reader, i) for i in range(indexed, min(indexed + PAGES_PER_STEP, limit))]
        text_pages += sum(has_words(text) for text in chunk)
        index.add_pages(row.no, indexed + 1, chunk)
        indexed += len(chunk)
        index.set_progress(row.no, pages=total, indexed=indexed, text_pages=text_pages, done=False)
    index.set_progress(row.no, pages=total, indexed=indexed, text_pages=text_pages, done=True)
    return True


def refresh(library: Library, *, budget_s: float | None = None, first: str | None = None) -> Refresh:
    """Bring the index up to date with the folder; ``first`` is a document id to extract before the others."""
    report = Refresh()
    docs = library.scan()
    report.problems.extend(library.problems)
    if not docs and not library.index_path.exists():
        return report
    index = library.open_index()
    try:
        current = {doc.id: doc for doc in docs}
        known = index.rows()
        for gone in known.keys() - current.keys():
            index.drop(known[gone].no)
        for doc in docs:
            row = known.get(doc.id)
            if row is None:
                no = index.add_doc(doc.id, doc.rel, doc.mtime_ns, doc.size)
                _index_ficha(library, index, doc, no)
                continue
            if (row.mtime_ns, row.size, row.rel) != (doc.mtime_ns, doc.size, doc.rel):
                index.reset_text(row.no, doc.rel, doc.mtime_ns, doc.size)
            if row.ficha_stamp != doc.ficha_stamp:
                _index_ficha(library, index, doc, row.no)
        out_of_time = Deadline(REFRESH_BUDGET_S if budget_s is None else budget_s)
        todo = sorted((r for r in index.rows().values() if not r.done), key=lambda r: (r.id != first, r.rel))
        for row in todo:
            if not _index_text(index, row, current[row.id], out_of_time, report):
                break
        for row in index.rows().values():
            if not row.done:
                report.pending += 1
            report.problems.extend(f"{row.id}: {p}" for p in (row.ficha_problem, row.text_problem) if p)
        report.pending -= report.no_pypdf
    finally:
        index.close()
    return report
