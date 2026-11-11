# ruff: noqa: E501
"""Two rules for the sheet of a welded part, on synthetic facts: one weld symbol per joint, and the parts list with a
balloon per body."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_weldrules as wr  # noqa: E402


def codes(found: list[wr.WeldDocFinding]) -> list[tuple[str, tuple[str, ...]]]:
    return [(f.code, f.numbers) for f in found]


# --- one weld symbol per joint ----------------------------------------------------------------------------------------------


def test_a_second_symbol_on_a_joint_that_already_has_a_double_sided_one_is_a_duplicate() -> None:
    welds = [wr.WeldFact("w1", "plate|rib@e1", "both"), wr.WeldFact("w2", "plate|rib@e1", "other")]
    assert wr.duplicate_welds(welds) == [wr.DuplicateJoint("plate|rib@e1", ("w1", "w2"))]


def test_the_double_sided_symbol_may_come_second() -> None:
    welds = [wr.WeldFact("w1", "plate|rib@e1", "arrow"), wr.WeldFact("w2", "plate|rib@e1", "both")]
    assert [d.items for d in wr.duplicate_welds(welds)] == [("w1", "w2")]


def test_two_one_sided_symbols_on_a_joint_or_symbols_on_different_joints_are_left_alone() -> None:
    assert (
        wr.duplicate_welds([wr.WeldFact("w1", "plate|rib@e1", "arrow"), wr.WeldFact("w2", "plate|rib@e1", "other")])
        == []
    )
    assert (
        wr.duplicate_welds([wr.WeldFact("w1", "plate|rib@e1", "both"), wr.WeldFact("w2", "plate|rib@e2", "both")]) == []
    )
    assert wr.duplicate_welds([wr.WeldFact("w1", "plate|rib@e1", "both")]) == []


def test_a_weld_whose_joint_is_not_known_is_never_a_duplicate() -> None:
    assert wr.duplicate_welds([wr.WeldFact("w1", "", "both"), wr.WeldFact("w2", "", "both")]) == []


# --- the parts list and a balloon per body -------------------------------------------------------------------------------------


def sheet(welded: bool = True, listed: set[str] | None = None, balloons: set[str] | None = None) -> wr.WeldSheet:
    return wr.WeldSheet(
        welded,
        None if listed is None else frozenset(listed),
        None if balloons is None else frozenset(balloons),
    )


def test_a_welded_sheet_with_no_parts_list_is_told_to_add_one() -> None:
    assert codes(wr.weld_documentation(sheet(listed=set(), balloons={"1"}))) == [("weld_list_missing", ())]


def test_a_listed_body_without_a_balloon_and_a_balloon_that_is_not_listed_are_both_reported() -> None:
    found = wr.weld_documentation(sheet(listed={"1", "2", "3", "10"}, balloons={"1", "3", "7"}))
    assert codes(found) == [("item_without_balloon", ("2", "10")), ("balloon_not_in_list", ("7",))]


def test_a_sheet_where_every_item_has_its_balloon_is_quiet() -> None:
    assert wr.weld_documentation(sheet(listed={"1", "2", "3"}, balloons={"1", "2", "3"})) == []


def test_a_sheet_that_is_not_welded_or_whose_tables_or_balloons_were_not_read_is_not_judged() -> None:
    assert wr.weld_documentation(sheet(welded=False, listed=set(), balloons=set())) == []
    assert wr.weld_documentation(sheet(listed=None, balloons={"1"})) == []
    assert wr.weld_documentation(sheet(listed={"1", "2"}, balloons=None)) == []
