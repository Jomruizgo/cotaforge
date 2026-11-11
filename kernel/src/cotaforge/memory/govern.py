"""How memory is used and governed, as pure functions over entries.

* Triggers: which entries a situation (an op, the roles it touches, the stage) or a milestone (the roles present in the
  model, the ops run, the stage) calls for. Only ``active`` entries ever trigger: a proposed or inactive one is never a
  rule.
* Examples, by sector (sectors are compared by canonical id, see ``sectors``):
  - a known project sector: its examples first, as the rule; then at most two analogies from other sectors;
  - an undeclared, general or uncovered sector: examples of up to three other sectors, all analogies, and a hint to
    declare the sector once.
* Related entries: a principle and its practices link both ways (``related``); ``get`` lists them, same sector first.
* Saving rules: one idea per entry. A ``principle`` carries no sector words, brand-like tokens or measured numbers
  (entry ids are not measures); those belong in a ``practice`` linked from it. A principle also needs ``recognize``
  and an example. A ``practice`` with a measure should say where it comes from (``sources`` or ``provenance``).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from cotaforge.memory.model import (
    JOINT_OPS,
    MATE_OPS,
    EntryStatus,
    EntryType,
    Example,
    MemoryEntry,
    Stage,
    render_entry,
)
from cotaforge.memory.sectors import SectorResolution, SectorTable, base_sector_table
from cotaforge.memory.text import normalize_text

MAX_OTHER_SECTOR_EXAMPLES = 2
MAX_UNDECLARED_SECTORS = 3
MAX_RELATED_LINES = 6
RECOGNIZE_PUSH_CHARS = 110


@dataclass(frozen=True)
class Situation:
    """What an op touches: its name, the roles of the bodies and components it involves and the current stage."""

    op: str | None = None
    roles: frozenset[str] = frozenset()
    stage: Stage | None = None


def _op_names(op: str) -> frozenset[str]:
    names = {op}
    if op in MATE_OPS:
        names.add("mates")
    if op in JOINT_OPS:
        names.add("joints")
    return frozenset(names)


def pushed_by(entry: MemoryEntry, situation: Situation) -> bool:
    """True when an op in ``situation`` calls for the entry: it names an op or a role (a stage alone is for the
    milestone, not for every op) and everything it names matches."""
    when = entry.when
    if not entry.is_active or when is None or not (when.ops or when.roles):
        return False
    if when.ops and (situation.op is None or not (set(when.ops) & _op_names(situation.op))):
        return False
    if when.roles and not (set(when.roles) & situation.roles):
        return False
    return not (when.stages and (situation.stage is None or situation.stage.value not in when.stages))


def triggered_at_milestone(entry: MemoryEntry, *, stage: Stage, roles: Iterable[str], ops_run: Iterable[str]) -> bool:
    """True when the entry was called for in the stage: its stage matches and its roles / ops are in the model."""
    when = entry.when
    if not entry.is_active or when is None or when.is_empty:
        return False
    if when.stages and stage.value not in when.stages:
        return False
    if when.roles and not (set(when.roles) & set(roles)):
        return False
    if when.ops:
        names: set[str] = set()
        for op in ops_run:
            names |= _op_names(op)
        if not (set(when.ops) & names):
            return False
    return True


def push_line(entry: MemoryEntry) -> str:
    """``memory: <id> — recognize: <recognize>`` (short), one line per entry; a practice adds its sector."""
    text = " ".join((entry.recognize or entry.applies_when).split())
    if len(text) > RECOGNIZE_PUSH_CHARS:
        text = text[: RECOGNIZE_PUSH_CHARS - 1].rstrip() + "…"
    line = f"memory: {entry.id} — recognize: {text}"
    if entry.type is EntryType.practice and entry.sector:
        line += f" [sector: {entry.sector}]"
    return line


def relevant_to_sector(entry: MemoryEntry, sector: str | None, table: SectorTable | None = None) -> bool:
    """False for a ``practice`` of one sector when the project declared another: that practice is neither pushed nor
    required (``memory get`` still reads it). Everything else is relevant, including when the sector is undeclared,
    general or unknown to the table (nothing can be judged then)."""
    if entry.type is not EntryType.practice or not entry.sector:
        return True
    table = table or base_sector_table()
    mine = table.resolve(sector)
    return not mine.declared or bool(table.resolve(entry.sector).keys() & mine.keys())


# ---- examples ------------------------------------------------------------------------------------------------------


def _first_per_sector(items: list[tuple[Example, frozenset[str]]], cap: int, *, fill: bool) -> list[Example]:
    """Up to ``cap`` examples, one per distinct sector first; ``fill`` then tops up with the remaining ones."""
    chosen: list[Example] = []
    seen: set[frozenset[str]] = set()
    for example, keys in items:
        if len(chosen) < cap and keys not in seen:
            chosen.append(example)
            seen.add(keys)
    if fill:
        for example, _ in items:
            if len(chosen) < cap and example not in chosen:
                chosen.append(example)
    return chosen


def _split_examples(
    entry: MemoryEntry, project: SectorResolution, table: SectorTable
) -> tuple[list[Example], list[Example]]:
    """``(own, analogies)`` shown for the project's sector (see the module doc)."""
    mine = project.keys()
    rows = [(example, table.resolve(example.sector).keys()) for example in entry.examples]
    own = [example for example, keys in rows if keys & mine]
    rest = [(example, keys) for example, keys in rows if not keys & mine]
    order = {sector_id: index for index, sector_id in enumerate(project.ids)}
    own.sort(key=lambda e: min((order.get(i, len(order)) for i in table.resolve(e.sector).keys()), default=0))
    if project.declared:
        return own, _first_per_sector(rest, MAX_OTHER_SECTOR_EXAMPLES, fill=True)
    return own, _first_per_sector(rest, MAX_UNDECLARED_SECTORS, fill=False)


def ordered_examples(
    entry: MemoryEntry, sector: str | None, table: SectorTable | None = None
) -> list[tuple[Example, bool]]:
    """``(example, is_analogy)``: the project's sector first (as the rule), then analogies of other sectors: at most
    two when the sector is known, up to three sectors when it is undeclared, general or not in the table."""
    table = table or base_sector_table()
    own, analogies = _split_examples(entry, table.resolve(sector), table)
    return [*((e, False) for e in own), *((e, True) for e in analogies)]


def _sector_header(sector: str | None, project: SectorResolution, table: SectorTable) -> str:
    if project.declared:
        names = " + ".join(project.ids)
        same = project.ids == (sector.strip() if sector else "",)
        return f"project sector: {names}" if same else f"project sector: {sector.strip() if sector else ''} = {names}"
    if sector and sector.strip() and project.ids != ("general",):
        return (
            f"project sector '{sector.strip()}' is not in the sector table: no base coverage, so examples are "
            "analogies; use a known sector (memory(action=brief)) or add it to sectors.yaml"
        )
    return (
        "project sector not declared: every example is an analogy from another sector; declare it once with "
        "memory(action=brief, sector=...)"
    )


def render_full(entry: MemoryEntry, sector: str | None, table: SectorTable | None = None) -> str:
    """The whole entry for ``memory get``: the file text without the examples, then the examples in order."""
    table = table or base_sector_table()
    text = render_entry(entry.model_copy(update={"examples": ()})).rstrip()
    if not entry.examples:
        return text
    project = table.resolve(sector)
    lines = [text, "", f"examples ({_sector_header(sector, project, table)}):"]
    shown = ordered_examples(entry, sector, table)
    for example, analogy in shown:
        label = f"analogy of {example.sector}: does it apply here?" if analogy else example.sector
        state = "validated" if example.validated else "NOT validated"
        lines.append(f"- [{label}] ({state}) {' '.join(example.text.split())}")
    hidden = len(entry.examples) - len(shown)
    if hidden:
        lines.append(f"({hidden} more example(s) of other sectors not shown)")
    return "\n".join(lines)


# ---- related entries -----------------------------------------------------------------------------------------------


def related_entries(entry: MemoryEntry, entries: Iterable[MemoryEntry]) -> list[MemoryEntry]:
    """The entries linked with ``entry`` in either direction (it lists them or they list it); inactive ones left out."""
    found: dict[str, MemoryEntry] = {}
    for other in entries:
        if other.id == entry.id or other.status is EntryStatus.inactive:
            continue
        if other.id in entry.related or entry.id in other.related:
            found[other.id] = other
    return list(found.values())


def related_lines(
    entry: MemoryEntry, entries: Iterable[MemoryEntry], sector: str | None, table: SectorTable | None = None
) -> list[str]:
    """``related:`` and one line per linked entry (``- id [type, sector] title``), the project's sector first."""
    table = table or base_sector_table()
    mine = table.resolve(sector)
    linked = related_entries(entry, entries)
    if not linked:
        return []

    def own(other: MemoryEntry) -> bool:
        return bool(other.sector) and bool(table.resolve(other.sector).keys() & mine.keys()) and mine.declared

    linked.sort(key=lambda other: (not own(other), other.type.value, other.id))
    lines = ["related (memory(action=get, id) reads each):"]
    for other in linked[:MAX_RELATED_LINES]:
        kind = other.type.value + (f", {other.sector}" if other.sector else "")
        pending = " PROPOSED" if other.is_proposed else ""
        lines.append(f"- {other.id} [{kind}]{pending} {' '.join(other.title.split())}")
    if len(linked) > MAX_RELATED_LINES:
        lines.append(f"(+{len(linked) - MAX_RELATED_LINES} more)")
    return lines


# ---- saving rules --------------------------------------------------------------------------------------------------

_UNITS = r"mm|cm|m|µm|um|kg|g|N|kN|Nm|N·m|MPa|GPa|bar|psi|rpm|s|ms|min|h|%|HRC|HB|HV|W|kW|V|A|Hz|l|L|mm2|mm²|mm3|mm³"
_NUMBER_WITH_UNIT = re.compile(rf"(?<![\w.])\d+(?:[.,]\d+)?\s?(?:{_UNITS})(?![\w])")
_CODE = re.compile(r"\b(?!M\d+\b)(?!ISO\d)(?!DIN\d)[A-Z][A-Za-z]*\d[A-Za-z0-9-]*\b|\b\d{2,}-\d{2,}[\w-]*\b")
_CAPITALISED = re.compile(r"(?<=[a-záéíóúñ,;)] )[A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,}\b")
# An entry id (lowercase words joined by hyphens, the first with a letter): `peine-5mm` is a name, not a measure
_ID_LIKE = re.compile(r"(?<![\w-])(?=[a-z0-9]*[a-z])[a-z0-9]+(?:-[a-z0-9]+)+(?![\w-])")
_TEXT_FIELDS = ("title", "applies_when", "not_when", "recognize", "typical_error", "body")


def _principle_text(entry: MemoryEntry) -> dict[str, str]:
    return {name: getattr(entry, name) for name in _TEXT_FIELDS if getattr(entry, name)}


def without_entry_ids(text: str, known_ids: Iterable[str] = ()) -> str:
    """``text`` with entry ids blanked: the ids in ``known_ids`` and anything shaped like an id (`word-word-5mm`)."""
    for known in sorted({i for i in known_ids if i}, key=len, reverse=True):
        text = re.sub(rf"(?<![\w-]){re.escape(known)}(?![\w-])", " ", text)
    return _ID_LIKE.sub(" ", text)


def principle_findings(entry: MemoryEntry, sector_words: Iterable[str], known_ids: Iterable[str] = ()) -> list[str]:
    """Why a ``principle`` is not one idea (empty list: it is). Sector words, brand-like tokens and measured numbers
    belong in a ``practice`` linked from the principle; examples are where a sector is named. Entry ids (the ones in
    ``known_ids`` and anything shaped like one) are names: they are never read as measures, codes or sector words."""
    if entry.type is not EntryType.principle:
        return []
    findings: list[str] = []
    words = {normalize_text(word).replace("-", " ").strip() for word in sector_words if word and word.strip()}
    words.discard("")
    ids = [*known_ids, *entry.related]
    for name, original in _principle_text(entry).items():
        text = without_entry_ids(original, ids)
        normalised = normalize_text(text)
        tokens = set(re.findall(r"\w+", normalised))
        for word in sorted(w for w in words if " " not in w and w in tokens):
            findings.append(f"{name} names the sector/process '{word}'")
        for word in sorted(w for w in words if " " in w and w in normalised):
            findings.append(f"{name} names the sector/process '{word}'")
        for match in _CODE.findall(text):
            findings.append(f"{name} has a product code or part number '{match}'")
        for match in _CAPITALISED.findall(text):
            findings.append(f"{name} has a name that looks like a brand or vendor '{match}'")
        for match in _NUMBER_WITH_UNIT.findall(text):
            findings.append(f"{name} has a measured number '{match}'")
    if entry.recognize is None:
        findings.append("a principle needs 'recognize' (the situation as seen in the model)")
    if not entry.examples:
        findings.append("a principle needs at least one example ({sector, text, validated})")
    return findings


def practice_warnings(entry: MemoryEntry) -> list[str]:
    """Warnings (never a refusal) for a ``practice``: a measure with units and no ``sources`` nor ``provenance``."""
    if entry.type is not EntryType.practice or entry.sources or entry.provenance is not None:
        return []
    texts = [*_principle_text(entry).values(), *(example.text for example in entry.examples)]
    measures = [m for text in texts for m in _NUMBER_WITH_UNIT.findall(without_entry_ids(text))]
    if not measures:
        return []
    shown = ", ".join(dict.fromkeys(m.strip() for m in measures[:3]))
    return [
        f"the practice has measures ({shown}) and no `sources` nor `provenance`: say where they come from "
        "(a standard, a manufacturer guide, a book, the engineer's own measurement)"
    ]
