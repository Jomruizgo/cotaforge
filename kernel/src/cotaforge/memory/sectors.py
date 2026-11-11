"""Canonical sectors: a table of ids with names and aliases that normalises what an engineer writes.

The brief's sector and the ``sector`` of every example are compared by canonical id, not by literal text, so
"dispositivos" and "dispositivos de manufactura" are the same sector. Resolution, in order:

1. the whole text is an id, a name or an alias (compared without case, accents or punctuation);
2. the whole text is an ambiguous word (``matrices``): nothing is assigned, the candidates are reported;
3. otherwise the aliases found inside the text, longest first, give a list of ids ("chapa y soldadura" is two);
4. nothing found: the sector stays literal and behaves as "not covered".

The base table ships in ``base/sectors.yaml``; the engineer extends it with a ``sectors.yaml`` in the memory folder of
the user or of the project (new sectors, or more aliases for an existing id).
"""

import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from cotaforge.memory.model import ID_PATTERN
from cotaforge.memory.text import normalize_text

SECTORS_FILENAME = "sectors.yaml"
GENERAL_ID = "general"
LITERAL_PREFIX = "~"  # a sector that resolves to no id is compared by its normalised text, under this prefix

_SHIPPED_BASE = Path(__file__).resolve().parent / "base"
_ID = re.compile(ID_PATTERN)
_Tokens = tuple[str, ...]


class SectorTableError(ValueError):
    """The base table is malformed (a programming error of the product, not of the engineer)."""


def _tokens(text: str) -> _Tokens:
    return tuple(re.findall(r"\w+", normalize_text(text)))


@dataclass(frozen=True)
class Sector:
    id: str
    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectorResolution:
    """What a text resolved to: ids (possibly several), or ambiguous words with their candidates, or nothing."""

    text: str
    ids: tuple[str, ...] = ()
    ambiguous: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @property
    def resolved(self) -> bool:
        return bool(self.ids)

    @property
    def declared(self) -> bool:
        """True when the text names a real sector (an unresolved text or ``general`` is 'no sector')."""
        return bool(self.ids) and self.ids != (GENERAL_ID,)

    def keys(self) -> frozenset[str]:
        """What two sectors are compared by: the ids, or the normalised literal when nothing resolved."""
        if self.ids:
            return frozenset(self.ids)
        literal = " ".join(_tokens(self.text))
        return frozenset({LITERAL_PREFIX + literal}) if literal else frozenset()


class SectorTable:
    def __init__(self, sectors: Iterable[Sector], ambiguous: dict[_Tokens, tuple[str, ...]] | None = None) -> None:
        self._sectors: dict[str, Sector] = {}
        self._exact: dict[_Tokens, str] = {}
        self._ambiguous: dict[_Tokens, tuple[str, ...]] = dict(ambiguous or {})
        for sector in sectors:
            self._add(sector)

    def _add(self, sector: Sector) -> None:
        known = self._sectors.get(sector.id)
        aliases = tuple(dict.fromkeys([*(known.aliases if known else ()), *sector.aliases]))
        merged = Sector(sector.id, sector.name or (known.name if known else sector.id), aliases)
        self._sectors[sector.id] = merged
        for key in (sector.id, merged.name, *sector.aliases):
            tokens = _tokens(key)
            if tokens:
                self._exact[tokens] = sector.id
                self._ambiguous.pop(tokens, None)  # an alias the engineer assigns is no longer ambiguous

    # ---- reading ----------------------------------------------------------------------------------

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._sectors)

    def sector(self, sector_id: str) -> Sector | None:
        return self._sectors.get(sector_id)

    def is_canonical(self, text: str) -> bool:
        """True when ``text`` is exactly a sector id (what the base set must use)."""
        return text in self._sectors

    def name(self, sector_id: str) -> str:
        sector = self._sectors.get(sector_id)
        return sector.name if sector else sector_id

    def sector_words(self) -> list[str]:
        """Words that name a sector or a process in a principle: ids, names and aliases (not ``general``)."""
        words: list[str] = []
        for sector in self._sectors.values():
            if sector.id == GENERAL_ID:
                continue
            words.append(sector.id.replace("-", " "))
            words.extend(sector.aliases)
        return words

    def resolve(self, text: str | None) -> SectorResolution:
        if not text or not text.strip():
            return SectorResolution(text or "")
        tokens = _tokens(text)
        if tokens in self._exact:
            return SectorResolution(text, (self._exact[tokens],))
        if tokens in self._ambiguous:
            return SectorResolution(text, (), ((text.strip(), self._ambiguous[tokens]),))
        return self._inclusion(text, tokens)

    def _inclusion(self, text: str, tokens: _Tokens) -> SectorResolution:
        keys = sorted(
            (key for key, sector_id in self._exact.items() if sector_id != GENERAL_ID),
            key=lambda key: -len(key),
        )
        longest = max((len(key) for key in [*keys, *self._ambiguous]), default=1)
        ids: list[str] = []
        ambiguous: list[tuple[str, tuple[str, ...]]] = []
        index = 0
        while index < len(tokens):
            for size in range(min(longest, len(tokens) - index), 0, -1):
                window = tokens[index : index + size]
                if window in self._exact and self._exact[window] != GENERAL_ID:
                    if self._exact[window] not in ids:
                        ids.append(self._exact[window])
                    index += size
                    break
                if window in self._ambiguous:
                    ambiguous.append((" ".join(window), self._ambiguous[window]))
                    index += size
                    break
            else:
                index += 1
        return SectorResolution(text, tuple(ids), tuple(ambiguous))

    # ---- extending --------------------------------------------------------------------------------

    def extended(self, data: Any) -> tuple["SectorTable", list[str]]:
        """A copy of this table with the sectors / aliases / ambiguous words of ``data`` (the parsed YAML of an
        engineer's ``sectors.yaml``) added, plus the problems found (the valid parts are applied anyway)."""
        table = SectorTable(self._sectors.values(), self._ambiguous)
        problems: list[str] = []
        if data is None:
            return table, problems
        if not isinstance(data, dict):
            return table, ["the file must be a mapping with `sectors:` (and optionally `ambiguous:`)"]
        for row in data.get("sectors") or ():
            problem = table._apply_row(row)  # noqa: SLF001
            if problem:
                problems.append(problem)
        raw = data.get("ambiguous")
        for word, candidates in (raw.items() if isinstance(raw, dict) else ()):
            options = candidates if isinstance(candidates, list) else ()
            wanted = tuple(c for c in options if c in table._sectors)  # noqa: SLF001
            if len(wanted) >= 2 and _tokens(str(word)):
                table._ambiguous[_tokens(str(word))] = wanted  # noqa: SLF001
            else:
                problems.append(f"ambiguous '{word}': needs a list of at least two existing sector ids")
        return table, problems

    def _apply_row(self, row: Any) -> str | None:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            return "each sector needs an `id`"
        sector_id = row["id"].strip()
        if not _ID.match(sector_id):
            return f"sector id '{sector_id}' is not a valid id (lowercase letters, digits and '-')"
        aliases = row.get("aliases") or ()
        if not isinstance(aliases, list | tuple) or not all(isinstance(a, str) for a in aliases):
            return f"sector '{sector_id}': `aliases` must be a list of texts"
        name = str(row.get("name") or "").strip()
        if sector_id not in self._sectors and not name:
            return f"sector '{sector_id}' is new: it needs a `name`"
        self._add(Sector(sector_id, name, tuple(a.strip() for a in aliases if a.strip())))
        return None


def _parse_base(path: Path) -> SectorTable:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SectorTableError(f"cannot read the base sector table {path}: {error}") from error
    table, problems = SectorTable([]).extended(data)
    if problems:
        raise SectorTableError(f"the base sector table {path} is malformed: {'; '.join(problems)}")
    return table


@functools.lru_cache(maxsize=4)
def _base_table(path: Path) -> SectorTable:
    return _parse_base(path)


def base_sector_table(base_dir: Path | None = None) -> SectorTable:
    """The table the product ships (``base/sectors.yaml``); read once."""
    path = (base_dir or _SHIPPED_BASE) / SECTORS_FILENAME
    return _base_table(path if path.is_file() else _SHIPPED_BASE / SECTORS_FILENAME)


def load_sector_table(
    base_dir: Path | None, extra_dirs: Iterable[tuple[str, Path | None]]
) -> tuple[SectorTable, list[str]]:
    """The base table (the shipped one when ``base_dir`` has none) extended by the ``sectors.yaml`` of each
    ``(label, folder)`` in ``extra_dirs`` (later ones win), and the problems of those files as
    ``'<label> sectors.yaml: <message>'`` (a broken file never breaks the table)."""
    table = base_sector_table(base_dir)
    problems: list[str] = []
    for label, directory in extra_dirs:
        path = directory / SECTORS_FILENAME if directory is not None else None
        if path is None or not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            problems.append(f"{label} {SECTORS_FILENAME}: {error}")
            continue
        table, found = table.extended(data)
        problems.extend(f"{label} {SECTORS_FILENAME}: {message}" for message in found)
    return table, problems
