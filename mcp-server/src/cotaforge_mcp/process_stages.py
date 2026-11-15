# ruff: noqa: E501
"""The process route of a feature and the size each stage of it gets on the sheet.

A part that passes several processes gives each operator HIS target: the outside diameter turned before the wire cut, the
blank for the wire cut, the size after it, the final size ground. The sheet carries one dimension per stage, labelled with
the process (``Ø48,3 turned``); the final one stands out and the intermediate ones are drawn grey. These are
native ``dim`` items (``stage``; ``size_mm`` for the sizes that are not the model's), editable in FreeCAD.

What this module reads is the DESIGNER's route, declared in the process section of the intent file: a line with a
feature, a colon and the stages in order (``Outside diameter: turned Ø60,3 -> ground``). It knows the
processes by name (a small table), never a part type. The stage SIZES are the engineer's: where the line gives none, the
harness only proposes the usual allowances (``ALLOWANCES``) and the engineer confirms them.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

PROCESSES: dict[str, tuple[str, str, str]] = {
    # canonical -> (regex of the words that name it, label in Spanish, label in English)
    "turn": (r"tornead|torneado|\bturn(?:ed|ing)?\b|\blathe\b", "torneado", "turned"),
    "wire_cut": (r"electroerosi|\bhilo\b|wire[\s-]*cut|\bwedm\b|\bedm\b|wire[\s-]*edm", "electroerosión", "wire cut"),
    "grind": (r"rectific|\bgrind|\bground\b", "rectificado", "ground"),
    "mill": (r"fresad|\bmill", "fresado", "milled"),
    "hone": (r"brunid|\bhon(?:e|ed|ing)\b|lapead|\blap(?:ped|ping)?\b", "bruñido", "honed"),
}
"""The processes whose stages carry a size of their own."""
STOCK_PROCESSES = frozenset({"wire_cut", "grind", "hone"})
"""The processes that finish a size from the stock the process before leaves: the route to them must be declared."""
ALLOWANCES = {
    "grind": "0,2 a 0,4 mm sobre el diámetro o el espesor antes del rectificado (0.2 to 0.4 mm on the size before grinding)",
    "wire_cut": "el bruto torneado es el diámetro de punta del perfil más 0,5 a 1 mm (the turned blank is the tip diameter of the profile plus 0.5 to 1 mm)",
    "turn": "0,5 mm por lado si hay un acabado posterior (0.5 mm per side when a finish follows)",
}
"""The usual stock left for the NEXT process: only a proposal, the engineer confirms every number."""
_ARROWS = re.compile(r"\s*(?:→|->|=>|»|\s>\s|\bluego\b|\bthen\b|\by despu[eé]s\b)\s*", re.I)
_SIZE = re.compile(r"[Ø⌀∅]?\s*(\d+(?:[.,]\d+)?)\s*(?:mm)?", re.I)
_PROCESS_RES = {name: re.compile(words, re.I) for name, (words, _, _) in PROCESSES.items()}
_TOLERANCE = 0.05


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


def process_of(text: str) -> str:
    """The canonical process a word or phrase names (``""``: none)."""
    plain = _plain(text)
    return next((name for name, rx in _PROCESS_RES.items() if rx.search(plain)), "")


def processes_in(text: str) -> set[str]:
    """Every canonical process the text names."""
    plain = _plain(text)
    return {name for name, rx in _PROCESS_RES.items() if rx.search(plain)}


@dataclass(frozen=True)
class Stage:
    process: str
    """Canonical process (``turn``, ``wire_cut``...)."""
    text: str
    """The words the intent used for it."""
    size_mm: float | None = None
    """The stage's size when the line gives it."""


@dataclass(frozen=True)
class Route:
    feature: str
    stages: tuple[Stage, ...]
    line: str


def label(process: str, language: str = "es") -> str:
    """The word the sheet prints for a process (in the sheet's language)."""
    entry = PROCESSES.get(process)
    return process if entry is None else entry[1 if language == "es" else 2]


def parse_line(line: str) -> Route | None:
    """The route a line of the intent file declares: ``feature: stage -> stage -> ...`` with at least two stages that name a
    process (a line with one process is no route: the part does not change hands)."""
    text = " ".join(line.split()).lstrip("-*• ").strip()
    if not text:
        return None
    feature, _, rest = text.partition(":")
    if not rest or not _ARROWS.search(rest):
        feature, rest = "", text
        if not _ARROWS.search(rest):
            return None
    stages: list[Stage] = []
    for part in _ARROWS.split(rest):
        process = process_of(part)
        if not process:
            continue
        found = _SIZE.search(part)
        stages.append(Stage(process, part.strip(" .;"), float(found.group(1).replace(",", ".")) if found else None))
    if len(stages) < 2:
        return None
    return Route(feature.strip(), tuple(stages), text)


def declared(text: str) -> list[Route]:
    """The routes declared by the lines of ``text`` (the process section of the intent file)."""
    return [r for line in text.splitlines() if (r := parse_line(line)) is not None]


def stage_items(items: Sequence[Any]) -> list[Any]:
    """The ``dim`` items of the sheet that carry a process stage."""
    return [i for i in items if getattr(i, "type", "") == "dim" and (getattr(i, "spec", None) or {}).get("stage")]


def problems(routes: Sequence[Route], items: Sequence[Any], language: str = "es") -> list[str]:
    """What each declared route still lacks on the sheet (English, for the agent); empty: every stage has its dimension.

    Every stage of a route needs one ``dim`` carrying its process (``stage``); every stage but the last is an intermediate
    size (``size_mm``: its own target, not the model's) and the last one is the final size the model measures. A size the
    intent line gives must be the one the dimension states."""
    have = [(process_of(str(i.spec["stage"])), i) for i in stage_items(items)]
    taken: set[str] = set()
    out: list[str] = []
    for route in routes:
        name = route.feature or route.line
        for number, stage in enumerate(route.stages):
            last = number == len(route.stages) - 1
            match = next((i for p, i in have if p == stage.process and i.id not in taken), None)
            word = label(stage.process, language)
            if match is None:
                allowance = ALLOWANCES.get(route.stages[number + 1].process) if not last else None
                size = (
                    "the final size is the one the model measures: a dim on the feature with stage and no size_mm"
                    if last
                    else (
                        f"the size {stage.size_mm:g} mm of the intent line: dim with stage and size_mm={stage.size_mm:g}"
                        if stage.size_mm is not None
                        else "its own target size (size_mm): the engineer confirms it"
                        + (f"; a proposal, the usual stock for the next process is {allowance}" if allowance else "")
                    )
                )
                item = '{"type":"dim","kind":"diameter"|"distance",...,"stage":"' + word + '"' + ("}" if last else ',"size_mm":<mm>}')
                out.append(f"route «{name}»: stage «{word}» has no dimension on the sheet; add {item} -- {size}")
                continue
            taken.add(match.id)
            stated = match.spec.get("size_mm")
            if not last and stage.size_mm is not None and (stated is None or abs(float(stated) - stage.size_mm) > _TOLERANCE):
                out.append(
                    f"route «{name}»: stage «{word}» is {stage.size_mm:g} mm in the intent file and {match.id} states "
                    f"{'no size_mm' if stated is None else f'{float(stated):g} mm'}: make them agree (the engineer decides which is right)"
                )
            elif last and stated is not None:
                out.append(
                    f"route «{name}»: the last stage «{word}» ({match.id}) is the final size: it takes no size_mm "
                    "(the model's value is the one printed)"
                )
    return out
