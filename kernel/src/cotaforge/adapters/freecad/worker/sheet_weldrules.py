# ruff: noqa: E501
"""Two rules for the sheet of a welded part, on plain facts (no FreeCAD import, adapter-neutral: an adapter builds the facts
from its CAD and calls the functions below).

* ``duplicate_welds``: one weld symbol per joint. A symbol drawn on both sides of the reference line already means both
  sides of the joint; a second symbol for the other side of the same joint is a duplicate.
* ``weld_documentation``: a welded drawing has a parts list (the weldment cut list) and a balloon per body, with item
  numbers that match the list.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass

BOTH = "both"  # a symbol on both sides of the reference line


@dataclass(frozen=True)
class WeldFact:
    """A weld item of the sheet: ``joint`` names the joint it is on (the same pair of bodies at the same edge or seam gives
    the same key, whatever the order), ``sides`` is ``arrow`` | ``other`` | ``both``."""

    item: str
    joint: str
    sides: str = "arrow"


@dataclass(frozen=True)
class DuplicateJoint:
    joint: str
    items: tuple[str, ...]


def duplicate_welds(welds: Sequence[WeldFact]) -> list[DuplicateJoint]:
    """The joints that carry more than one weld item when at least one of them is double-sided: that symbol already says
    both sides, so any other on the same joint repeats it. Two one-sided symbols on a joint (arrow side and other side) are
    left to the engineer."""
    by_joint: dict[str, list[WeldFact]] = {}
    for weld in welds:
        if weld.joint:
            by_joint.setdefault(weld.joint, []).append(weld)
    return [
        DuplicateJoint(joint, tuple(w.item for w in group))
        for joint, group in by_joint.items()
        if len(group) > 1 and any(w.sides == BOTH for w in group)
    ]


@dataclass(frozen=True)
class WeldSheet:
    """What the sheet of a welded part holds: ``welded`` (weld items on it, or more than one welded body), whether it has a
    parts list and its item numbers, and the numbers on its balloons. ``None`` for the lists or the balloons: the adapter
    could not read them (nothing is judged on what is not known)."""

    welded: bool
    list_numbers: frozenset[str] | None = None  # None: tables not read; empty: no parts list on the sheet
    balloon_numbers: frozenset[str] | None = None


@dataclass(frozen=True)
class WeldDocFinding:
    code: str  # weld_list_missing | item_without_balloon | balloon_not_in_list
    numbers: tuple[str, ...] = ()


def _order(numbers: Collection[str]) -> tuple[str, ...]:
    return tuple(sorted(numbers, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n)))


def weld_documentation(sheet: WeldSheet) -> list[WeldDocFinding]:
    """A welded sheet with no parts list; a listed item (a body) with no balloon; a balloon whose number is not in the list."""
    if not sheet.welded or sheet.list_numbers is None:
        return []
    if not sheet.list_numbers:
        return [WeldDocFinding("weld_list_missing")]
    if sheet.balloon_numbers is None:
        return []
    out: list[WeldDocFinding] = []
    missing = sheet.list_numbers - sheet.balloon_numbers
    extra = sheet.balloon_numbers - sheet.list_numbers
    if missing:
        out.append(WeldDocFinding("item_without_balloon", _order(missing)))
    if extra:
        out.append(WeldDocFinding("balloon_not_in_list", _order(extra)))
    return out
