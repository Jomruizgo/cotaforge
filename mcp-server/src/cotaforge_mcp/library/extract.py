"""Text of the documents, page by page. ``pypdf`` is imported on first use: without it the fichas still work."""

from pathlib import Path
from types import ModuleType
from typing import Any

MIN_PAGE_CHARS = 25
"""A page with fewer non-blank characters is taken as having no text (a scan or a drawing)."""
MAX_PAGE_CHARS = 20_000
MAX_TEXT_FILE_BYTES = 2_000_000
SAMPLE_PAGES = 12

TEXT_SUFFIXES = (".txt", ".md")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


class TextUnavailableError(Exception):
    """The full text of a PDF cannot be read (no ``pypdf``, or the file does not open)."""

    def __init__(self, message: str, *, missing_library: bool = False) -> None:
        super().__init__(message)
        self.missing_library = missing_library


def load_pypdf() -> ModuleType | None:
    try:
        import pypdf  # noqa: PLC0415
    except ImportError:
        return None
    return pypdf


def open_pdf(path: Path) -> Any:  # noqa: ANN401 - a pypdf reader, whose module is optional
    pypdf = load_pypdf()
    if pypdf is None:
        msg = "pypdf is not installed: the full text of PDFs is not available"
        raise TextUnavailableError(msg, missing_library=True)
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            reader.decrypt("")
        _ = len(reader.pages)  # parses the page tree now, so a broken file fails here
    except Exception as error:  # pypdf raises many unrelated types on damaged or locked files
        msg = f"the PDF cannot be read ({type(error).__name__}: {str(error)[:80]})"
        raise TextUnavailableError(msg) from error
    return reader


def page_text(reader: Any, index: int) -> str:  # noqa: ANN401
    try:
        return (reader.pages[index].extract_text() or "")[:MAX_PAGE_CHARS]
    except Exception:  # one bad page must not stop the rest of the document
        return ""


def has_words(text: str) -> bool:
    return len("".join(text.split())) >= MIN_PAGE_CHARS


def sample_has_text(reader: Any) -> bool:  # noqa: ANN401
    """Whether at least half of an even sample of the pages has text (a scanned document has none)."""
    total = len(reader.pages)
    if total == 0:
        return False
    step = max(1, total // SAMPLE_PAGES)
    sample = range(0, total, step)
    return sum(has_words(page_text(reader, i)) for i in sample) * 2 >= len(sample)


def read_text_file(path: Path) -> str:
    with path.open("rb") as handle:
        return handle.read(MAX_TEXT_FILE_BYTES).decode("utf-8", errors="replace")
