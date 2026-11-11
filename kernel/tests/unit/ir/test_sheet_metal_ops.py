"""The sheet-metal ops in the IR: fields, defaults, and the plan rules around them."""

from typing import Any

import pytest
from pydantic import ValidationError

from cotaforge.ir import OP_CATEGORY, OP_NAMES, PlanError, parse_plan
from cotaforge.ir.ops_features import BaseFlangeOperation, EdgeFlangeOperation, UnfoldOperation

SKETCH = {
    "id": "s1",
    "op": "sketch",
    "intent": "outline",
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "o", "center": [50, 30], "width": 100, "height": 60}],
}
BASE = {"id": "b1", "op": "base_flange", "intent": "sheet", "sketch": "s1", "thickness_mm": 2}
TOP = {"kind": "face", "from_op": "b1", "select": {"normal": [0, 0, 1]}}
SIDE = {"kind": "face", "from_op": "b1", "select": {"normal": [0, -1, 0]}}
FLANGE = {
    "id": "f1",
    "op": "edge_flange",
    "intent": "lip",
    "edge": {"kind": "edge", "between": [TOP, SIDE]},
    "length_mm": 20,
    "bend_radius_mm": 3,
}
UNFOLD = {
    "id": "u1",
    "op": "unfold",
    "intent": "flat",
    "face": {"kind": "face", "from_op": "b1", "select": {"normal": [0, 0, -1]}},
    "k_factor": 0.4,
}


def plan(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"id": "sheet", "kind": "part", "operations": list(operations)}


def test_the_three_ops_are_in_the_vocabulary_and_in_their_own_category() -> None:
    assert {"base_flange", "edge_flange", "unfold"} <= OP_NAMES
    assert {OP_CATEGORY[n] for n in ("base_flange", "edge_flange", "unfold")} == {"sheet_metal"}


def test_a_sheet_plan_parses_and_the_flange_defaults_are_a_right_angle_inside_to_the_outer_sharp() -> None:
    parsed = parse_plan(plan(SKETCH, BASE, FLANGE, UNFOLD))
    base, flange, flat = parsed.operations[1:]
    assert isinstance(base, BaseFlangeOperation) and base.direction.value == "positive"
    assert isinstance(flange, EdgeFlangeOperation)
    assert (flange.angle_deg, flange.position, flange.length_from) == (90.0, "material_inside", "outer_sharp")
    assert isinstance(flat, UnfoldOperation)


def test_the_bend_radius_and_the_k_factor_have_no_default() -> None:
    no_radius = {k: v for k, v in FLANGE.items() if k != "bend_radius_mm"}
    with pytest.raises(PlanError):
        parse_plan(plan(SKETCH, BASE, no_radius))
    no_k = {k: v for k, v in UNFOLD.items() if k != "k_factor"}
    with pytest.raises(PlanError):
        parse_plan(plan(SKETCH, BASE, no_k))


@pytest.mark.parametrize("k", [0, 1, -0.2, 1.5])
def test_a_k_factor_must_be_strictly_between_zero_and_one(k: float) -> None:
    with pytest.raises(PlanError):
        parse_plan(plan(SKETCH, BASE, {**UNFOLD, "k_factor": k}))


@pytest.mark.parametrize("angle", [0, 180, -10, 200])
def test_a_bend_angle_must_be_inside_zero_to_one_eighty(angle: float) -> None:
    with pytest.raises(PlanError):
        parse_plan(plan(SKETCH, BASE, {**FLANGE, "angle_deg": angle}))


def test_unknown_fields_and_values_are_refused() -> None:
    with pytest.raises(ValidationError):
        BaseFlangeOperation(id="b", intent="i", sketch="s", thickness_mm=2, bend_radius_mm=1)  # type: ignore[call-arg]
    with pytest.raises(PlanError):
        parse_plan(plan(SKETCH, BASE, {**FLANGE, "position": "middle"}))


def test_a_sheet_sketch_is_an_outline_with_holes_inside_it() -> None:
    hole = {"kind": "circle", "name": "h", "center": [50, 30], "diameter": 8}
    parse_plan(plan({**SKETCH, "entities": [*SKETCH["entities"], hole]}, BASE))  # a hole inside: fine
    outside = {"kind": "circle", "name": "h", "center": [150, 30], "diameter": 8}
    with pytest.raises(PlanError) as info:
        parse_plan(plan({**SKETCH, "entities": [*SKETCH["entities"], outside]}, BASE))
    assert info.value.rule == "V6" and "not inside the outline" in info.value.message
    touching = {"kind": "circle", "name": "h", "center": [4, 30], "diameter": 8}  # tangent to the edge: no material
    with pytest.raises(PlanError):
        parse_plan(plan({**SKETCH, "entities": [*SKETCH["entities"], touching]}, BASE))


def test_the_base_flange_makes_a_body_and_faces_but_the_unfold_makes_neither() -> None:
    mirror = {
        "id": "m1",
        "op": "mirror_body",
        "intent": "twin",
        "source": {"kind": "body", "from_op": "b1"},
        "plane": {"kind": "plane", "name": "YZ"},
    }
    parse_plan(plan(SKETCH, BASE, mirror))  # a sheet is a body
    after_unfold = {**FLANGE, "id": "f2", "edge": {"kind": "edge", "between": [{**TOP, "from_op": "u1"}, SIDE]}}
    with pytest.raises(PlanError) as info:
        parse_plan(plan(SKETCH, BASE, UNFOLD, after_unfold))
    assert info.value.rule == "V3"
    with pytest.raises(PlanError) as info:
        parse_plan(plan(SKETCH, BASE, UNFOLD, {**mirror, "source": {"kind": "body", "from_op": "u1"}}))
    assert info.value.rule == "V3"
