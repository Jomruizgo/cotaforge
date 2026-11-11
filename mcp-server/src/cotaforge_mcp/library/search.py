"""Search of the library: the fichas first (they carry the agent's judgement), then the full text of the pages."""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from cotaforge_mcp.library.ficha import Ficha, FichaError
from cotaforge_mcp.library.files import tokens
from cotaforge_mcp.library.index import DocRow
from cotaforge_mcp.library.store import DocFile, Library
from cotaforge_mcp.library.sync import Refresh, refresh

MAX_WORDS = 8
HIT_POOL = 60
MAX_NOTE_DOCS = 5
MAX_NOTES_PER_DOC = 3
MAX_TEXT_DOCS = 5
MAX_TEXT_PAGES = 4


@dataclass
class DocRef:
    id: str
    path: Path
    title: str
    reference: str
    """What a citation names: the standard (or the title) and the edition."""
    pages: int | None
    has_text: bool | None


@dataclass
class NoteLine:
    doc: DocRef
    label: str
    """``p. 2-3``; empty when only the title, standard or topic of the document matched."""
    what: str


@dataclass
class TextLine:
    doc: DocRef
    pages: list[int]
    snippet: str


@dataclass
class Hits:
    notes: list[NoteLine] = field(default_factory=list)
    texts: list[TextLine] = field(default_factory=list)
    refresh: Refresh = field(default_factory=Refresh)
    documents: int = 0


@dataclass
class Overview:
    documents: int = 0
    topics: dict[str, int] = field(default_factory=dict)
    without_text: int = 0
    refresh: Refresh = field(default_factory=Refresh)


def _file_of(library: Library, row: DocRow) -> DocFile:
    return DocFile(row.id, library.root / row.rel, row.rel, row.size, row.mtime_ns, row.ficha_stamp)


def _ficha_of(library: Library, row: DocRow) -> Ficha | None:
    try:
        return library.load_ficha(_file_of(library, row))
    except FichaError:
        return None


def doc_ref(library: Library, row: DocRow, ficha: Ficha | None) -> DocRef:
    title = ficha.title if ficha else row.id.rsplit("/", 1)[-1]
    return DocRef(row.id, library.root / row.rel, title, ficha.reference() if ficha else title, row.pages, row.has_text)


def _score(words: list[str], text: str) -> int:
    """How many of the query words the text has (as a word or the start of one)."""
    found = tokens(text)
    return sum(any(token.startswith(word) for token in found) for word in words)


def search(library: Library, text: str) -> Hits:
    words = tokens(text)[:MAX_WORDS]
    hits = Hits(refresh=refresh(library))
    if not words or not library.index_path.exists():
        return hits
    index = library.open_index()
    try:
        rows = {row.no: row for row in index.rows().values()}
        hits.documents = len(rows)
        fichas: dict[int, Ficha | None] = {}

        def ficha_of(no: int) -> Ficha | None:
            if no not in fichas:
                fichas[no] = _ficha_of(library, rows[no])
            return fichas[no]

        by_doc: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
        for hit in index.search_notes(words, HIT_POOL):
            if hit.no in rows:
                by_doc[hit.no].append((hit.first, hit.last, hit.body))
        for no, found in list(by_doc.items())[:MAX_NOTE_DOCS]:
            ref = doc_ref(library, rows[no], ficha_of(no))
            ranked = sorted(
                ((_score(words, body), first, last, body) for first, last, body in found if first), reverse=True
            )
            shown = [(first, last, body) for score, first, last, body in ranked if score][:MAX_NOTES_PER_DOC]
            if not shown:
                hits.notes.append(NoteLine(ref, "", ""))
            for first, last, body in shown:
                label = f"p. {first}" if first == last else f"p. {first}-{last}"
                hits.notes.append(NoteLine(ref, label, body))
        pages: dict[int, list[int]] = defaultdict(list)
        snippets: dict[int, str] = {}
        for hit in index.search_pages(words, HIT_POOL):
            if hit.no not in rows:
                continue
            ficha = ficha_of(hit.no)
            mapped = next((n for n in ficha.notes if n.covers(hit.page)), None) if ficha else None
            if mapped is not None:
                # a page the ficha already maps: its own words say what it holds
                ref = doc_ref(library, rows[hit.no], ficha)
                line = NoteLine(ref, f"p. {hit.page}", f"{mapped.what} (the text matches here)")
                if all((n.doc.id, n.label) != (ref.id, line.label) for n in hits.notes):
                    hits.notes.append(line)
                continue
            pages[hit.no].append(hit.page)
            snippets.setdefault(hit.no, hit.snippet)
        for no, found_pages in list(pages.items())[:MAX_TEXT_DOCS]:
            ref = doc_ref(library, rows[no], ficha_of(no))
            hits.texts.append(TextLine(ref, sorted(set(found_pages))[:MAX_TEXT_PAGES], " ".join(snippets[no].split())))
    finally:
        index.close()
    return hits


def overview(library: Library) -> Overview:
    result = Overview(refresh=refresh(library))
    if not library.index_path.exists():
        return result
    index = library.open_index()
    try:
        rows = index.rows().values()
    finally:
        index.close()
    for row in rows:
        result.documents += 1
        topic = row.id.split("/")[0] if "/" in row.id else "(sin tema)"
        result.topics[topic] = result.topics.get(topic, 0) + 1
        result.without_text += row.has_text is False
    return result


def find_row(library: Library, doc_id: str) -> tuple[DocFile, Ficha | None, str] | None:
    """A document by id for ``get``: its file, its ficha, and what is wrong with the ficha (empty when nothing)."""
    doc = library.find(doc_id)
    if doc is None:
        return None
    try:
        return doc, library.load_ficha(doc), ""
    except FichaError as error:
        return doc, None, str(error)
