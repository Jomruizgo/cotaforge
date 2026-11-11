"""The engineer's language: ``es`` or ``en``.

Everything the agent reads is English. What the engineer reads or edits (the init report, the dialogs, the questions the
harness puts to him, the texts on a drawing sheet) follows his language. It is chosen at install, stored in the project
(``language`` of ``brief.yaml``) and can be changed per project; a project that never stated one is Spanish, as every
project was before the setting existed.

This module only knows the two codes and how to pick between two wordings; where the setting is stored is the business
of the code that has a project (``cotaforge_mcp.design_memory``) or an installation (``cotaforge_mcp.layout``).
"""

from typing import Any

LANGUAGES = ("es", "en")
DEFAULT_LANGUAGE = "es"
"""What a project that never stated a language is (the harness spoke Spanish before the setting existed)."""

_NAMES = {
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "espanol": "es",
    "español": "es",
    "castellano": "es",
    "en": "en",
    "eng": "en",
    "english": "en",
    "ingles": "en",
    "inglés": "en",
}


def normalize(value: Any) -> str | None:
    """``es`` / ``en`` for what names a supported language (``"ES"``, ``"en_US"``, ``"es-CO"``, ``"spanish"``,
    ``"Español"``); ``None`` for anything else, so a missing or unknown value never picks a language by accident."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("_", "-")
    if not text:
        return None
    return _NAMES.get(text) or _NAMES.get(text.split("-", 1)[0])


def resolve(value: Any, default: str = DEFAULT_LANGUAGE) -> str:
    """The language ``value`` names, else ``default``."""
    return normalize(value) or default


def say(language: str, es: str, en: str) -> str:
    """The Spanish wording for ``es``, the English one for anything else."""
    return es if language == "es" else en
