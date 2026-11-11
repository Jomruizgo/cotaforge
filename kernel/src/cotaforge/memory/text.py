"""Text normalisation shared by the memory search and by the declared-set matching."""

import unicodedata


def normalize_text(text: str) -> str:
    """Case-folded text without diacritics ("Acero Inoxidable" and "acéro inoxidable" compare equal)."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.casefold()


def normalize_key(text: str) -> str:
    """``normalize_text`` with all whitespace removed ("Acero 1045" and "acero1045" compare equal)."""
    return "".join(normalize_text(text).split())
