"""``shrink_body``: the op that derives a mould cavity from the plastic part, linked to it, scaled by a shrinkage."""

from typing import Any

import pytest
from pydantic import ValidationError

from cotaforge.ir import ShrinkBodyOperation, parse_plan
from cotaforge.ir.ops_features import OP_CATEGORY, OP_NAMES

BLOCK = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}


def _op(**fields: Any) -> dict[str, Any]:
    op = {
        "id": "cavity",
        "op": "shrink_body",
        "intent": "cavity of the part",
        "source": {"kind": "body", "from_op": "part"},
        "shrinkage_percent": 0.6,
        "factor_source": "resin datasheet",
    }
    op.update(fields)
    return op


def test_the_op_is_in_the_vocabulary() -> None:
    assert "shrink_body" in OP_NAMES and OP_CATEGORY["shrink_body"] == "multibody"


def test_an_isotropic_shrinkage_scales_every_axis_by_one_plus_percent() -> None:
    op = ShrinkBodyOperation.model_validate(_op())
    assert op.scale() == pytest.approx((1.006, 1.006, 1.006))


def test_a_shrinkage_per_axis_scales_each_axis_on_its_own() -> None:
    op = ShrinkBodyOperation.model_validate(_op(shrinkage_percent=[0.3, 0.9, 0.6]))
    assert op.scale() == pytest.approx((1.003, 1.009, 1.006))


def test_the_intent_always_carries_the_shrinkage_and_its_source() -> None:
    op = ShrinkBodyOperation.model_validate(_op())
    assert op.intent == "cavity of the part [shrinkage 0.6 % isotropic (source: resin datasheet)]"
    per_axis = ShrinkBodyOperation.model_validate(_op(shrinkage_percent=[0.3, 0.9, 0.6], factor_source="the moulder"))
    assert "shrinkage X 0.3 % / Y 0.9 % / Z 0.6 % (source: the moulder)" in per_axis.intent


def test_a_plan_read_again_does_not_repeat_the_note() -> None:
    once = ShrinkBodyOperation.model_validate(_op())
    again = ShrinkBodyOperation.model_validate(once.model_dump(mode="json"))
    assert again.intent == once.intent


def test_three_equal_numbers_read_as_isotropic() -> None:
    op = ShrinkBodyOperation.model_validate(_op(shrinkage_percent=[0.5, 0.5, 0.5]))
    assert "0.5 % isotropic" in op.intent


@pytest.mark.parametrize("missing", ["factor_source", "shrinkage_percent"])
def test_the_shrinkage_and_its_source_are_mandatory(missing: str) -> None:
    fields = _op()
    del fields[missing]
    with pytest.raises(ValidationError):
        ShrinkBodyOperation.model_validate(fields)


@pytest.mark.parametrize("source", ["", "   ", "x"])
def test_a_source_must_say_something(source: str) -> None:
    with pytest.raises(ValidationError):
        ShrinkBodyOperation.model_validate(_op(factor_source=source))


@pytest.mark.parametrize("percent", [0, -0.5, 10.5, [0.5, 0, 0.5], [0.5, 12, 0.5], float("nan")])
def test_an_implausible_shrinkage_is_refused(percent: Any) -> None:
    with pytest.raises(ValidationError):
        ShrinkBodyOperation.model_validate(_op(shrinkage_percent=percent))


def test_it_parses_inside_a_plan_and_makes_a_body_later_steps_may_name() -> None:
    plan = parse_plan(
        {
            "id": "mould",
            "kind": "part",
            "operations": [
                BLOCK,
                _op(),
                {
                    "id": "lift",
                    "op": "translate",
                    "intent": "put the cavity aside",
                    "target": {"kind": "body", "from_op": "cavity"},
                    "offset": [0, 0, 50],
                },
            ],
        }
    )
    assert [o.id for o in plan.operations] == ["part", "cavity", "lift"]
