"""File-system helpers of the library: names, hashes and atomic writes."""

import hashlib
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

from cotaforge import paths

LIBRARY_DIRNAME = paths.LIBRARY
INDEX_DIRNAME = paths.INDEXES
INDEX_FILENAME = paths.LIBRARY_INDEX
ALLOWED_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md")
FICHA_SUFFIX = ".ficha.md"
NAME_PART_CHARS = (30, 60, 20)  # caps of <standard or author>, <title>, <edition> inside a file name


def ascii_slug(text: str) -> str:
    """Lowercase ascii with ``-`` between words: ``Pasadores cilíndricos`` -> ``pasadores-cilindricos``."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


def tokens(text: str) -> list[str]:
    """Lowercase ascii words and numbers of ``text``, in order: what a search matches on."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.findall(r"[a-z0-9]+", plain)


def document_name(standard: str | None, author: str | None, title: str, edition: str | None) -> str:
    """``<standard or author>_<title>_<edition>`` as slugs, ``_`` between the parts (no extension)."""
    parts = []
    for text, cap in zip((standard or author, title, edition), NAME_PART_CHARS, strict=True):
        slug = ascii_slug(text)[:cap].strip("-") if text else ""
        if slug:
            parts.append(slug)
    return "_".join(parts) or "documento"


def is_document(path: Path) -> bool:
    """A file the library indexes: an allowed type that is not a ficha, a hidden file or a temporary copy."""
    name = path.name
    return not name.startswith(".") and not name.endswith(FICHA_SUFFIX) and path.suffix.lower() in ALLOWED_EXTENSIONS


def ficha_path_of(document: Path) -> Path:
    return document.with_name(document.stem + FICHA_SUFFIX)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    """Write next to ``path`` and replace it in one step, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def copy_atomic(source: Path, target: Path) -> None:
    """Copy ``source`` (never touched) to ``target`` through a temporary file in the same folder."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp_name)
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
