# ruff: noqa: E501
"""What the drawing review let through on two real sheets, as the table of requirements sees it: an assembly sheet with no parts
list, no balloons and no axes on its sleeves (the harness adds the list, the balloons on the isometric view and the centre
lines itself), a view with nothing in it that the agent could dismiss with its own reason, and a shaft whose chamfer nobody
dimensioned (the harness measures it and dimensions it)."""

from types import SimpleNamespace

from cotaforge_mcp import drawing_requirements as dr
from tests.unit.test_drawing_requirements import ALL_FACTS, PLATE, finding, rows

ASSEMBLY = {**ALL_FACTS, "sheet": 1, "assembly": 1, "bosses": 6, "title_general_tolerance": 1, "title_scale": 1, "title_projection": 1}
SHAFT = {**PLATE, "holes": 0, "chamfers": 1, "bosses": 1}


def item(item_id: str, kind: str) -> SimpleNamespace:
    return SimpleNamespace(id=item_id, type=kind)


def plan(facts: dict[str, int], findings: tuple = (), items: list | None = None, views: tuple[str, ...] = ("front", "top", "isometric")) -> list[dr.Addition]:
    found = list(dr.evaluate(facts, findings, {}, dr.Intent()))
    return dr.plan_additions(found, items or [], assembly=bool(facts.get("assembly")), views=views)


# --- the parts list and the balloons of an assembly -------------------------------------------------------------------------


def test_an_assembly_gets_its_parts_list_and_one_balloon_per_position_on_the_isometric_view() -> None:
    added = plan(ASSEMBLY, (finding("no_parts_list"),))
    by_id = {a.item["id"]: a.item for a in added}
    assert by_id[dr.PARTS_LIST_ID] == {"type": "parts_list", "id": dr.PARTS_LIST_ID}
    assert by_id[dr.BALLOONS_ID] == {"type": "balloons", "id": dr.BALLOONS_ID, "view": "isometric"}
    assert all(a.rows == ("req.parts_list",) for a in added)


def test_the_agents_own_parts_list_is_kept_and_only_the_balloons_are_added() -> None:
    added = plan(ASSEMBLY, (finding("parts_without_balloons"),), [item("pl", "parts_list")])
    assert [a.item["id"] for a in added] == [dr.BALLOONS_ID]


def test_no_list_is_added_to_a_sheet_that_is_not_an_assembly_or_that_was_answered() -> None:
    welded = {**PLATE, "weld_items": 2}
    assert [a for a in plan(welded, (finding("no_parts_list"),)) if a.item["type"] in ("parts_list", "balloons")] == []
    answered = {"req.parts_list": SimpleNamespace(verdict="applies", ordered_by="")}
    found = list(dr.evaluate(ASSEMBLY, (finding("no_parts_list"),), answered, dr.Intent()))
    assert dr.plan_additions(found, [], assembly=True, views=("front", "isometric")) == []


def test_the_balloons_go_on_the_first_view_when_the_sheet_has_no_isometric() -> None:
    added = plan(ASSEMBLY, (finding("no_parts_list"),), views=("front", "top"))
    assert {a.item["id"]: a.item for a in added}[dr.BALLOONS_ID]["view"] == "front"


def test_the_items_the_harness_added_for_the_list_belong_to_its_row() -> None:
    row = dr.ROWS[[r.id for r in dr.ROWS].index("req.parts_list")]
    assert dr.owns(row, dr.PARTS_LIST_ID) and dr.owns(row, dr.BALLOONS_ID) and not dr.owns(row, "pl")


# --- the centre lines of an assembly ----------------------------------------------------------------------------------------


def test_an_assembly_with_cylinders_has_the_centre_lines_row_and_the_harness_adds_them_without_hidden_lines() -> None:
    needed = rows(ASSEMBLY, (finding("cylinder_no_axis"),))
    assert "req.centre_lines" in needed and needed["req.centre_lines"].measured
    (added,) = [a for a in plan(ASSEMBLY, (finding("cylinder_no_axis"),)) if a.item["type"] == "view_lines"]
    assert added.item == {"type": "view_lines", "id": dr.VIEW_LINES_ID, "hidden": []}


def test_a_part_gets_its_centre_lines_with_the_usual_hidden_lines() -> None:
    (added,) = [a for a in plan({**PLATE, "bosses": 2}, (finding("cylinder_no_axis"),)) if a.item["type"] == "view_lines"]
    assert added.item == {"type": "view_lines", "id": dr.VIEW_LINES_ID}


def test_a_cylinder_without_axis_cannot_be_dismissed_on_the_agents_word() -> None:
    assert "cylinder_no_axis" in dr.MEASURED_FINDINGS


def test_a_flat_of_a_turned_part_without_its_diagonals_is_met_by_view_lines_and_cannot_be_dismissed() -> None:
    assert "flat_face_unmarked" in dr.MEASURED_FINDINGS
    washer = {**PLATE, "holes": 1, "bosses": 1}
    assert "req.centre_lines" in rows(washer, (finding("flat_face_unmarked"),))
    (added,) = [a for a in plan(washer, (finding("flat_face_unmarked"),)) if a.item["type"] == "view_lines"]
    assert added.item == {"type": "view_lines", "id": dr.VIEW_LINES_ID} and added.rows == ("req.centre_lines",)


# --- a view with nothing in it ----------------------------------------------------------------------------------------------


def test_a_view_that_carries_nothing_is_not_dismissed_with_the_agents_own_reason() -> None:
    assert "view_adds_nothing" in dr.MEASURED_FINDINGS
    own = SimpleNamespace(verdict="not_applies", ordered_by="")
    ordered = SimpleNamespace(verdict="not_applies", ordered_by="engineer")
    f = finding("view_adds_nothing", answer=None)
    assert not dr.answer_counts(own, f.code in dr.MEASURED_FINDINGS)
    assert dr.answer_counts(ordered, f.code in dr.MEASURED_FINDINGS)


# --- the chamfer of a shaft -------------------------------------------------------------------------------------------------


def test_a_chamfer_nobody_dimensioned_is_dimensioned_by_the_harness() -> None:
    (added,) = [a for a in plan(SHAFT, (finding("chamfer_no_dimension"),)) if a.item["type"] == "chamfer_dims"]
    assert added.item == {"type": "chamfer_dims", "id": dr.CHAMFERS_ID} and added.rows == ("req.chamfers",)
    assert "chamfer_no_dimension" in dr.MEASURED_FINDINGS


def test_a_dimensioned_chamfer_adds_nothing() -> None:
    assert [a for a in plan(SHAFT, ()) if a.item["type"] == "chamfer_dims"] == []
