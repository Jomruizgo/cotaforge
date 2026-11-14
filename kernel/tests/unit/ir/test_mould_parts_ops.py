"""``mould_component`` and ``slide_mechanism``: what a plan may say, what is refused at parse time, and what the intent carries."""

from typing import Any

import pytest

from cotaforge.ir import MouldComponentOperation, PlanError, SlideMechanismOperation, parse_plan
from cotaforge.ir.ops_features import OP_CATEGORY, OP_NAMES

PART = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}
SHRUNK = {
    "id": "shrunk",
    "op": "shrink_body",
    "intent": "shrunk part",
    "source": {"kind": "body", "from_op": "part"},
    "shrinkage_percent": 0.6,
    "factor_source": "resin datasheet",
}
SPLIT = {
    "id": "inserts",
    "op": "mould_split",
    "intent": "cavity and core",
    "source": {"kind": "body", "from_op": "shrunk"},
    "pull": "Z",
    "slides": "all",
}


def _component(**fields: Any) -> dict[str, Any]:
    return {
        "id": "base",
        "op": "mould_component",
        "intent": "the mould base",
        "kind": "mould_base",
        "size": "1012",
        **fields,
    }


def _mechanism(**fields: Any) -> dict[str, Any]:
    return {
        "id": "mech",
        "op": "slide_mechanism",
        "intent": "the slide mechanism",
        "split": "inserts",
        "slide": 1,
        "part": {"kind": "body", "from_op": "shrunk"},
        "pull": "Z",
        "angle_deg": 15,
        **fields,
    }


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"id": "mould", "kind": "part", "operations": list(ops)}


def test_both_are_in_the_vocabulary_as_multibody_ops() -> None:
    assert {"mould_component", "slide_mechanism"} <= OP_NAMES
    assert OP_CATEGORY["mould_component"] == OP_CATEGORY["slide_mechanism"] == "multibody"


def test_a_component_by_size_or_by_ref_parses_and_a_single_placement_may_be_an_object() -> None:
    op = parse_plan(_plan(_component(at={"origin": [0, 0, 30], "axis": "Z"}))).operations[0]
    assert isinstance(op, MouldComponentOperation) and len(op.at) == 1 and tuple(op.at[0].origin) == (0, 0, 30)
    assert parse_plan(_plan(_component(size=None, ref="XPA1012-23-23-35"))).operations[0].op == "mould_component"
    pin = parse_plan(
        _plan({"id": "pin", "op": "mould_component", "intent": "p", "kind": "angle_pin", "ref": "APD1260"})
    )
    assert pin.operations[0].at == ()  # no placement: one pin at the origin along Z  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"kind": "guide_pin", "size": None, "ref": "5114GL"}, "not a DME guide pin"),
        ({"kind": "sprue_bush", "size": None, "ref": "B0299"}, "B-series"),
        ({"kind": "mould_base", "size": "1212"}, "not encoded"),
        ({"kind": "guide_pin", "size": None}, "needs ref"),
        ({"kind": "wear_plate", "size": None, "ref": "SLP9999"}, "wear plate"),
    ],
)
def test_a_reference_that_is_not_in_the_tables_is_refused_when_the_plan_is_read(
    fields: dict[str, Any], message: str
) -> None:
    with pytest.raises(PlanError, match=message):
        parse_plan(_plan(_component(**fields)))


def test_the_intent_ends_up_carrying_the_component_and_where_it_goes() -> None:
    op = parse_plan(_plan(_component(at=[{"origin": [0, 0, 30], "axis": [0, 0, 1]}]))).operations[0]
    assert "mould component: mould_base 1012" in op.intent and "DME" in op.intent and "(0, 0, 30)" in op.intent


def test_the_mechanism_names_a_mould_split_step_and_the_intent_carries_its_numbers() -> None:
    op = parse_plan(_plan(PART, SHRUNK, SPLIT, _mechanism(stroke_mm=6))).operations[-1]
    assert isinstance(op, SlideMechanismOperation)
    assert "angle pin 15 deg" in op.intent and "locking heel +2 deg" in op.intent and "stroke 6 mm" in op.intent
    free = parse_plan(_plan(PART, SHRUNK, SPLIT, _mechanism())).operations[-1]
    assert "undercut depth + 2 mm" in free.intent


def test_a_split_that_is_not_a_mould_split_is_refused_and_so_is_one_that_does_not_exist() -> None:
    with pytest.raises(PlanError, match="V3"):
        parse_plan(_plan(PART, SHRUNK, _mechanism(split="shrunk")))
    with pytest.raises(PlanError, match="V2"):
        parse_plan(_plan(PART, SHRUNK, _mechanism(split="inserts")))  # no such step before it


@pytest.mark.parametrize(
    "fields",
    [
        {"angle_deg": 0},
        {"angle_deg": 45},
        {"wedge_extra_deg": 0},
        {"angle_deg": 40, "wedge_extra_deg": 5},
        {"stroke_mm": -1},
        {"slide": 0},
    ],
)
def test_angles_that_make_no_mechanism_are_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(PlanError):
        parse_plan(_plan(PART, SHRUNK, SPLIT, _mechanism(**fields)))
