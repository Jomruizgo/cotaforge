"""All shipped parts in one place, looked up by id or found by words."""

import re
import unicodedata
from functools import lru_cache

from cotaforge_mcp.parts import data_angles, data_fasteners, data_sheet, data_tubes
from cotaforge_mcp.parts.model import Part

FAMILIES: tuple[tuple[str, str, str], ...] = (
    ("tube_square", "square structural tube", "tubo cuadrado 40x40"),
    ("tube_rect", "rectangular structural tube", "tubo rectangular 100x50"),
    ("tube_round", "round structural tube", "tubo redondo 48.3"),
    ("angle", "equal-leg angle", "angulo 50x50x5"),
    ("sheet_gauge", "sheet steel gauge", "calibre 16"),
    ("bolt", "hex bolt/screw ISO 4014/4017", "tornillo m8"),
    ("nut", "hex nut ISO 4032", "tuerca m10"),
    ("washer", "plain washer ISO 7089", "arandela m12"),
)


def normalize(text: str) -> str:
    """Lower case, no accents, ``x`` for the multiplication signs, ``2.0`` written ``2``."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    plain = plain.replace("×", "x").replace("*", "x").replace(",", ".")
    plain = re.sub(r"(?<=\d)\.0+(?!\d)", "", plain)
    plain = re.sub(r"(?<=[\d\"/])\s*x\s*(?=\d)", "x", plain)  # "3 x 3 x 3/16" -> "3x3x3/16"
    return re.sub(r"\s+", " ", plain).strip()


def matches_all(tokens: list[str], haystack: str) -> bool:
    """Every token occurs in ``haystack`` as a whole size or word: ``50x100x3`` does not match ``50x100x3.5`` and
    ``40x2`` does not match the tail of ``40x40x2`` (a token is not preceded by a digit, a dot or an ``x`` and not
    followed by a digit), while ``40x40`` does match ``40x40x2`` and ``tub`` matches ``tubo``."""
    return all(re.search(rf"(?<![\d.x]){re.escape(t)}(?!\d|\.\d)", haystack) for t in tokens)


@lru_cache(maxsize=1)
def all_parts() -> tuple[Part, ...]:
    parts = [*data_tubes.parts(), *data_angles.parts(), *data_sheet.parts(), *data_fasteners.parts()]
    ids = [p.id for p in parts]
    if len(set(ids)) != len(ids):  # a data error, caught by the tests; failing loudly beats a silently shadowed row
        duplicated = sorted({i for i in ids if ids.count(i) > 1})
        msg = f"duplicate catalog ids: {duplicated}"
        raise RuntimeError(msg)
    return tuple(parts)


@lru_cache(maxsize=1)
def _haystacks() -> tuple[tuple[Part, str], ...]:
    def words(p: Part) -> str:
        flag = "" if p.verified else "sin verificar"
        return normalize(f"{p.id} {p.designation} {p.kind} {p.keywords} {p.source.label} {flag}")

    return tuple((p, words(p)) for p in all_parts())


def get_part(part_id: str) -> Part | None:
    wanted = part_id.strip().lower()
    return next((p for p in all_parts() if p.id == wanted), None)


def search_parts(text: str) -> list[Part]:
    """Parts whose id, name, kind or keywords contain EVERY word of ``text``; verified rows first, table order kept."""
    tokens = normalize(text).split()
    if not tokens:
        return []
    hits = [p for p, hay in _haystacks() if matches_all(tokens, hay)]
    return sorted(hits, key=lambda p: not p.verified)  # sorted is stable


def family_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in all_parts():
        counts[p.kind] = counts.get(p.kind, 0) + 1
    return counts
