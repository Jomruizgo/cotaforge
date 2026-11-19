"""``mould_split``: the op that cuts the shrunk part's negative into the cavity and the core."""

from typing import Any

import pytest
from pydantic import ValidationError

from cotaforge.ir import MouldSplitOperation, parse_plan
from cotaforge.ir.ops_features import OP_CATEGORY, OP_NAMES

PART = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}
CAVITY = {
    "id": "shrunk",
    "op": "shrink_body",
    "intent": "shrunk part",
    "source": {"kind": "body", "from_op": "part"},
    "shrinkage_percent": 0.6,
    "factor_source": "resin datasheet",
}


def _op(**fields: Any) -> dict[str, Any]:
    op = {
        "id": "inserts",
        "op": "mould_split",
        "intent": "cavity and core",
        "source": {"kind": "body", "from_op": "shrunk"},
        "pull": "Z",
    }
    op.update(fields)
    return op


def test_the_op_is_in_the_vocabulary_as_a_multibody_op() -> None:
    assert "mould_split" in OP_NAMES and OP_CATEGORY["mould_split"] == "multibody"


def test_a_plan_with_the_split_after_the_shrunk_part_parses_and_the_split_makes_bodies() -> None:
    plan = parse_plan({"id": "m", "kind": "part", "operations": [PART, CAVITY, _op()]})
    assert [o.op for o in plan.operations] == ["box", "shrink_body", "mould_split"]


def test_the_defaults_decide_nothing_for_the_engineer() -> None:
    op = MouldSplitOperation.model_validate(_op())
    assert op.slides == "none" and op.zero_draft_to is None and op.block_size_mm is None and op.margin_mm is None
    assert op.min_draft_deg == 1.0


def test_the_intent_carries_the_pull_the_block_the_decision_and_the_slides() -> None:
    op = MouldSplitOperation.model_validate(
        _op(block_size_mm=[160, 140, 90], zero_draft_to="cavity", slides=[1, 2], pull=[0, 0, 1])
    )
    assert op.intent == (
        "cavity and core [mould split: pull (0, 0, 1) (the core opens along it), block 160 x 140 x 90 mm (given by the "
        "engineer), walls with no draft go to the cavity, slides accepted: 1, 2]"
    )


def test_a_proposed_block_is_said_to_be_a_proposal() -> None:
    op = MouldSplitOperation.model_validate(_op())
    assert (
        "proposed margin" in op.intent
        and "no decision on walls with no draft" in op.intent
        and "no slides" in op.intent
    )
    given = MouldSplitOperation.model_validate(_op(margin_mm=30, slides="all"))
    assert (
        "grown 30 mm on every side (given by the engineer)" in given.intent
        and "slides: all the proposed" in given.intent
    )


def test_a_plan_read_again_does_not_repeat_the_note() -> None:
    once = MouldSplitOperation.model_validate(_op(slides="all"))
    assert MouldSplitOperation.model_validate(once.model_dump(mode="json")).intent == once.intent


def test_the_block_is_a_size_or_a_margin_never_both() -> None:
    with pytest.raises(ValidationError, match="not both"):
        MouldSplitOperation.model_validate(_op(block_size_mm=[160, 140, 90], margin_mm=30))


@pytest.mark.parametrize("bad", [{"margin_mm": 0}, {"block_size_mm": [100, -1, 50]}, {"slides": "some"}, {"pull": "W"}])
def test_nonsense_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MouldSplitOperation.model_validate(_op(**bad))


def test_the_source_is_a_body_the_plan_must_have_made_before() -> None:
    op = MouldSplitOperation.model_validate(_op(source={"kind": "body", "from_op": "ghost"}))
    assert any(ref.from_op == "ghost" for ref in op.refs())
