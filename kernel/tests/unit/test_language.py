"""The two codes of the engineer's language and how to pick between two wordings."""

import pytest

from cotaforge.language import DEFAULT_LANGUAGE, LANGUAGES, normalize, resolve, say


def test_there_are_two_languages_and_spanish_is_the_default() -> None:
    assert LANGUAGES == ("es", "en") and DEFAULT_LANGUAGE == "es"


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("es", "es"),
        ("EN", "en"),
        ("en_US", "en"),
        ("es-CO", "es"),
        ("spanish", "es"),
        ("English", "en"),
        ("fr", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_understands_codes_locales_and_installer_names(value: object, code: str | None) -> None:
    assert normalize(value) == code
    assert resolve(value) == (code or DEFAULT_LANGUAGE)


def test_say_is_spanish_for_es_and_english_for_anything_else() -> None:
    assert say("es", "hola", "hello") == "hola"
    assert say("en", "hola", "hello") == "hello"
