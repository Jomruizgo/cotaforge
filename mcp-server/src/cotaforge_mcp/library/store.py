"""The technical library on disk: one folder per topic, each document copied in with a ficha next to it.

The library is the engineer's, not the product's: the product never ships documents. Filing copies (the original is
never moved or modified), writes the ficha and, with an existing id, updates the ficha or moves the document to
another topic. Anything the engineer drops into a topic folder by hand is found by the index on the next search.
"""

import datetime as dt
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from cotaforge_mcp.library.entry import LibraryEntry
from cotaforge_mcp.library.extract import (
    IMAGE_SUFFIXES,
    TEXT_SUFFIXES,
    TextUnavailableError,
    has_words,
    open_pdf,
    read_text_file,
    sample_has_text,
)
from cotaforge_mcp.library.ficha import Ficha, FichaError, PageNote, parse_ficha, render_ficha
from cotaforge_mcp.library.files import (
    ALLOWED_EXTENSIONS,
    ascii_slug,
    copy_atomic,
    document_name,
    ficha_path_of,
    is_document,
    sha256_of,
    write_atomic,
)
from cotaforge_mcp.library.index import LibraryIndex
from cotaforge_mcp.library.topics import MAX_TOPIC_CHARS, Topics, TopicsError

UNSUPPORTED = (
    "is not a supported type: file PDF, images (.png, .jpg) or text (.txt, .md). "
    "Word, Excel and PowerPoint documents must be exported to PDF first"
)


class LibraryError(Exception):
    """A failure the agent can act on."""

    def __init__(self, message: str, *, code: str = "library_error", field_name: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.field_name = field_name


@dataclass(frozen=True)
class DocFile:
    id: str
    path: Path
    rel: str
    size: int
    mtime_ns: int
    ficha_stamp: str
    """``mtime:size`` of its ficha, empty without one: a change of it re-reads the ficha."""

    @property
    def ficha_path(self) -> Path:
        return ficha_path_of(self.path)

    @property
    def folder(self) -> str:
        """The topic: the first folder of the id (empty for a file at the library root)."""
        return self.id.split("/")[0] if "/" in self.id else ""


@dataclass
class Filed:
    kind: str
    """``filed`` | ``duplicate`` | ``updated`` | ``ficha created``"""
    id: str
    path: Path
    ficha_path: Path
    ficha: Ficha | None
    notices: list[str] = field(default_factory=list)
    new_topic: str = ""
    moved_from: str = ""
    text_available: bool = True


@dataclass
class Inspected:
    pages: int | None
    text: bool | None
    notice: str = ""
    text_available: bool = True


def inspect_document(path: Path) -> Inspected:
    """Pages and whether the document has text, without extracting all of it."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return Inspected(1, False)
    if suffix in TEXT_SUFFIXES:
        return Inspected(1, has_words(read_text_file(path)))
    try:
        reader = open_pdf(path)
    except TextUnavailableError as error:
        return Inspected(None, None, str(error), text_available=not error.missing_library)
    return Inspected(len(reader.pages), sample_has_text(reader))


class Library:
    def __init__(self, root: Path, index_path: Path) -> None:
        self.root = root
        self.index_path = index_path
        self.topics = Topics(root)
        self.problems: list[str] = []
        """Files the last scan could not use (two documents with one name)."""

    # --- what is on disk ----------------------------------------------------------------------------------------

    def scan(self) -> list[DocFile]:
        self.problems = []
        found: dict[str, DocFile] = {}
        if not self.root.is_dir():
            return []
        for folder, subfolders, names in os.walk(self.root):
            subfolders[:] = sorted(name for name in subfolders if not name.startswith("."))
            for name in sorted(names):
                path = Path(folder) / name
                if not is_document(path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                rel = path.relative_to(self.root)
                doc_id = rel.with_suffix("").as_posix()
                if doc_id in found:
                    self.problems.append(f"{rel.as_posix()}: another document has the same name; rename one of them")
                    continue
                found[doc_id] = DocFile(doc_id, path, rel.as_posix(), stat.st_size, stat.st_mtime_ns, _stamp(path))
        return list(found.values())

    def find(self, doc_id: str) -> DocFile | None:
        """The document with this id (its path without the extension, relative to the library)."""
        text = doc_id.strip().replace("\\", "/").strip("/")
        if not text or ".." in text.split("/") or ":" in text:
            return None
        candidates = {text}
        for extension in ALLOWED_EXTENSIONS:
            if text.lower().endswith(extension):
                candidates.add(text[: -len(extension)])
        for candidate in sorted(candidates, key=len, reverse=True):
            target = self.root / candidate
            if not target.parent.is_dir():
                continue
            for path in sorted(target.parent.iterdir()):
                if path.stem == target.name and path.is_file() and is_document(path):
                    stat = path.stat()
                    rel = path.relative_to(self.root)
                    return DocFile(
                        rel.with_suffix("").as_posix(),
                        path,
                        rel.as_posix(),
                        stat.st_size,
                        stat.st_mtime_ns,
                        _stamp(path),
                    )
        return None

    @staticmethod
    def load_ficha(doc: DocFile) -> Ficha | None:
        """The ficha of a document, ``None`` when it has none; ``FichaError`` when it cannot be understood."""
        try:
            source = doc.ficha_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError) as error:
            msg = f"cannot be read ({type(error).__name__})"
            raise FichaError(msg) from error
        ficha = parse_ficha(source)
        ficha.topic = doc.folder  # the folder is the truth: moving the file by hand changes its topic
        return ficha

    def open_index(self) -> LibraryIndex:
        return LibraryIndex(self.index_path, self.root)

    # --- filing -------------------------------------------------------------------------------------------------

    def file(self, entry: LibraryEntry, source: Path | None) -> Filed:
        if entry.id:
            if entry.path:
                msg = "an update (id) does not take a path: the document is already in the library"
                raise LibraryError(msg, code="invalid_entry", field_name="entry.path")
            return self._update(entry)
        if source is None:
            msg = "file needs entry.path (the document to file) or entry.id (a filed document to update)"
            raise LibraryError(msg, code="invalid_input", field_name="entry.path")
        return self._file_new(entry, source)

    def _file_new(self, entry: LibraryEntry, source: Path) -> Filed:
        extension = source.suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise LibraryError(f"{source.name!r} {UNSUPPORTED}", code="unsupported_type", field_name="entry.path")
        if not source.is_file():
            raise LibraryError(f"no file at {source}", code="no_file", field_name="entry.path")
        if self.root.resolve() in source.resolve().parents:
            msg = "that file is already inside the library: file it with its id (the path without the extension)"
            raise LibraryError(msg, code="already_in_library", field_name="entry.path")
        missing = [name for name in ("topic", "title") if not getattr(entry, name)]
        if missing:
            raise LibraryError(
                f"filing needs {' and '.join(missing)}", code="invalid_entry", field_name=f"entry.{missing[0]}"
            )
        sha = sha256_of(source)
        twin = self._with_sha(sha)
        if twin is not None:
            return Filed("duplicate", twin.id, twin.path, twin.ficha_path, self._safe_ficha(twin))
        inspected = inspect_document(source)
        notes = self._checked_notes(entry, inspected.pages)
        topic, is_new = self._topic(entry)
        title = entry.title or ""
        folder = self.root / topic
        name = _free_name(folder, document_name(entry.standard, entry.author, title, entry.edition))
        target = folder / f"{name}{extension}"
        ficha = Ficha(
            title=title,
            topic=topic,
            standard=entry.standard or None,
            edition=entry.edition or None,
            author=entry.author or None,
            pages=inspected.pages,
            text=inspected.text,
            original=source.name,
            filed=dt.date.today().isoformat(),
            sha256=sha,
        )
        ficha.merge_notes(notes)
        copy_atomic(source, target)
        write_atomic(ficha_path_of(target), render_ficha(ficha).encode("utf-8"))
        if is_new:
            self._add_topic(entry)
        notices = [inspected.notice] if inspected.notice else []
        return Filed(
            "filed",
            f"{topic}/{name}",
            target,
            ficha_path_of(target),
            ficha,
            notices,
            new_topic=topic if is_new else "",
            text_available=inspected.text_available,
        )

    def _update(self, entry: LibraryEntry) -> Filed:
        doc = self.find(entry.id or "")
        if doc is None:
            raise LibraryError(
                f"no document {entry.id!r} in the library", code="unknown_document", field_name="entry.id"
            )
        try:
            ficha = self.load_ficha(doc)
        except FichaError as error:
            msg = f"the ficha of {doc.id} {error}; fix it by hand or delete it, then file again"
            raise LibraryError(msg, code="broken_ficha", field_name="entry.id") from error
        kind = "updated"
        inspected = Inspected(None, None)
        if ficha is None:
            if not entry.title:
                msg = f"{doc.id} has no ficha yet: send title (and standard, edition, author, pages) to create it"
                raise LibraryError(msg, code="invalid_entry", field_name="entry.title")
            ficha = Ficha(
                title=entry.title, topic=doc.folder, original=doc.path.name, filed=dt.date.today().isoformat()
            )
            ficha.sha256 = sha256_of(doc.path)
            kind = "ficha created"
        if ficha.pages is None or ficha.text is None:
            inspected = inspect_document(doc.path)
            ficha.pages = ficha.pages if ficha.pages is not None else inspected.pages
            ficha.text = ficha.text if ficha.text is not None else inspected.text
        ficha.merge_notes(self._checked_notes(entry, ficha.pages))
        for name in ("title", "standard", "edition", "author"):
            value = getattr(entry, name)
            if value is not None and not (name == "title" and not value.strip()):
                setattr(ficha, name, value.strip() or None)
        topic, is_new = self._topic(entry) if entry.topic else (doc.folder, False)
        renamed = any(getattr(entry, name) is not None for name in ("title", "standard", "edition", "author"))
        stem = doc.path.stem
        if renamed:
            stem = document_name(ficha.standard, ficha.author, ficha.title, ficha.edition)
        target = self.root / topic / f"{stem}{doc.path.suffix}"
        moved_from = ""
        if target != doc.path:
            if target.exists() or ficha_path_of(target).exists():
                msg = f"{target.relative_to(self.root).as_posix()} already exists; change the title, edition or topic"
                raise LibraryError(msg, code="document_exists", field_name="entry")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(doc.path, target)
            moved_from = doc.id
        ficha.topic = topic
        write_atomic(ficha_path_of(target), render_ficha(ficha).encode("utf-8"))
        if moved_from and doc.ficha_path.exists() and doc.ficha_path != ficha_path_of(target):
            doc.ficha_path.unlink()
        if is_new:
            self._add_topic(entry)
        new_id = target.relative_to(self.root).with_suffix("").as_posix()
        notices = [inspected.notice] if inspected.notice else []
        return Filed(
            kind,
            new_id,
            target,
            ficha_path_of(target),
            ficha,
            notices,
            new_topic=topic if is_new else "",
            moved_from=moved_from,
            text_available=inspected.text_available,
        )

    # --- helpers ------------------------------------------------------------------------------------------------

    def _topic(self, entry: LibraryEntry) -> tuple[str, bool]:
        """The canonical topic for the entry and whether it is new (then it needs its synonyms)."""
        try:
            table = self.topics.table()
            known = self.topics.resolve(entry.topic or "", table)
        except TopicsError as error:
            raise LibraryError(str(error), code="topics_error", field_name="entry.topic") from error
        if known is not None:
            return known, False
        if not entry.topic_aliases:
            msg = (
                f"topic {entry.topic!r} is not in topics.yaml. Use one of: {', '.join(table)}. "
                "To create it, send topic_aliases (at least one synonym, in Spanish, with and without accents)"
            )
            raise LibraryError(msg, code="unknown_topic", field_name="entry.topic")
        slug = ascii_slug(entry.topic or "")
        if not slug:
            msg = f"{entry.topic!r} is not a topic name: use words"
            raise LibraryError(msg, code="unknown_topic", field_name="entry.topic")
        return slug[:MAX_TOPIC_CHARS].strip("-"), True

    def _add_topic(self, entry: LibraryEntry) -> None:
        try:
            self.topics.add(entry.topic or "", entry.topic_aliases or [])
        except TopicsError as error:
            raise LibraryError(str(error), code="topics_error", field_name="entry.topic_aliases") from error

    @staticmethod
    def _checked_notes(entry: LibraryEntry, pages: int | None) -> list[PageNote]:
        notes = entry.notes()
        if any(not note.what for note in notes) and not entry.id:
            msg = "every pages item needs what (the page and what it holds)"
            raise LibraryError(msg, code="invalid_entry", field_name="entry.pages")
        if pages is not None and any(note.last > pages for note in notes):
            msg = f"the page map goes beyond the document, which has {pages} page(s)"
            raise LibraryError(msg, code="invalid_entry", field_name="entry.pages")
        return notes

    def _with_sha(self, sha: str) -> DocFile | None:
        for doc in self.scan():
            ficha = self._safe_ficha(doc)
            if ficha is not None and ficha.sha256 == sha:
                return doc
        return None

    def _safe_ficha(self, doc: DocFile) -> Ficha | None:
        try:
            return self.load_ficha(doc)
        except FichaError:
            return None


def _stamp(document: Path) -> str:
    try:
        stat = ficha_path_of(document).stat()
    except OSError:
        return ""
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _free_name(folder: Path, base: str) -> str:
    """``base``, or ``base-2``, ``base-3``... when another document of the topic already has that name."""
    taken = {path.stem for path in folder.iterdir()} if folder.is_dir() else set()
    name, n = base, 2
    while name in taken or f"{name}.ficha" in taken:
        name, n = f"{base}-{n}", n + 1
    return name
