"""A document saved before ``hole`` had a drill point keeps its flat-bottomed holes when it is reopened."""

from cotaforge.ir import parse_plan

from cotaforge_mcp.checkpoint import pin_legacy_hole_points

SAVED = {
    "id": "old",
    "kind": "part",
    "operations": [
        {
            "id": "s",
            "op": "sketch",
            "intent": "i",
            "on": {"kind": "plane", "name": "XY"},
            "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 40, "height": 40}],
        },
        {"id": "base", "op": "extrude", "intent": "i", "sketch": "s", "depth_mm": 10},
        {
            "id": "h",
            "op": "hole",
            "intent": "i",
            "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
            "position": [0, 0],
            "standard": "simple",
            "diameter_mm": 5,
            "end_condition": "blind",
            "depth_mm": 6,
        },
    ],
}


def test_a_stored_hole_without_a_drill_point_stays_flat_and_a_new_one_gets_freecads_default() -> None:
    assert parse_plan(SAVED).operations[2].drill_point.value == "angled"  # a NEW plan: FreeCAD's default
    assert parse_plan(pin_legacy_hole_points(SAVED)).operations[2].drill_point.value == "flat"  # a saved one


def test_a_stored_hole_that_names_its_point_keeps_it() -> None:
    plan = {**SAVED, "operations": [*SAVED["operations"][:2], {**SAVED["operations"][2], "drill_point": "angled"}]}
    assert parse_plan(pin_legacy_hole_points(plan)).operations[2].drill_point.value == "angled"
