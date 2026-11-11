"""IR tests for ``split``, ``BodyRef.side`` (V3) and the scaled ``copy_body``."""

import json

import pytest
from pydantic import ValidationError

from cotaforge.ir import OP_NAMES, produces_feature
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import OP_CATEGORY, SplitOperation
from cotaforge.ir.ops_primitives import CopyBodyOperation
from cotaforge.ir.plan import Plan, parse_plan, plan_json_schema
from cotaforge.ir.refs import BodyRef, FaceRef, PlaneRef


def _box(op_id: str = "b") -> dict:
    return {"id": op_id, "op": "box", "intent": "block", "size": [40, 30, 20]}


def _split(op_id: str = "sp", **extra: object) -> dict:
    fields: dict = {
        "id": op_id,
        "op": "split",
        "intent": "cut the block at the parting plane",
        "source": {"kind": "body", "from_op": "b"},
        "plane": {"kind": "plane", "name": "XY"},
    }
    fields.update(extra)
    return fields


def _move(target: dict, op_id: str = "mv") -> dict:
    return {"id": op_id, "op": "translate", "intent": "move", "target": target, "offset": [0, 0, 5]}


def _plan(*operations: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": list(operations)}


def _rule_of(*operations: dict) -> PlanError:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(*operations))
    return info.value


def test_split_builds_with_both_as_the_default_keep() -> None:
    op = SplitOperation(id="sp", intent="i", source=BodyRef(from_op="b"), plane=PlaneRef(name="XY"))
    assert op.op == "split"
    assert op.keep == "both"
    assert SplitOperation(id="sp", intent="i", source=BodyRef(from_op="b"), plane=FaceRef(from_op="b"), keep="negative")


def test_split_rejects_an_unknown_keep_a_string_source_and_extras() -> None:
    base = {"id": "sp", "intent": "i", "source": BodyRef(from_op="b"), "plane": PlaneRef(name="XY")}
    with pytest.raises(ValidationError):
        SplitOperation(**{**base, "keep": "top"})
    with pytest.raises(ValidationError):
        SplitOperation(**{**base, "source": "b"})
    with pytest.raises(ValidationError):
        SplitOperation(**{**base, "count": 2})


def test_split_is_a_multibody_detail_op_that_produces_a_feature() -> None:
    assert "split" in OP_NAMES
    assert OP_CATEGORY["split"] == "multibody"
    assert produces_feature("split")


def test_split_round_trips_through_a_detail_plan_and_the_json_schema() -> None:
    plan = parse_plan(_plan(_box(), _split(keep="positive")))
    assert isinstance(plan, Plan)
    assert isinstance(plan.operations[1], SplitOperation)
    assert "SplitOperation" in json.dumps(plan_json_schema())


def test_a_split_source_must_be_an_earlier_body_and_the_plane_a_datum_or_base_plane() -> None:
    assert _rule_of(_split()).rule == "V2"
    assert _rule_of(_box(), _split(plane={"kind": "plane", "from_op": "b"})).rule == "V3"


def test_body_ref_side_accepts_only_positive_and_negative() -> None:
    assert BodyRef(from_op="sp", side="negative").side == "negative"
    assert BodyRef(from_op="b").side is None
    with pytest.raises(ValidationError):
        BodyRef(from_op="sp", side="left")  # type: ignore[arg-type]


def test_a_ref_to_a_split_that_keeps_both_needs_a_side() -> None:
    error = _rule_of(_box(), _split(), _move({"kind": "body", "from_op": "sp"}))
    assert error.rule == "V3"
    assert "side" in error.message and "keep" in error.message
    parse_plan(_plan(_box(), _split(), _move({"kind": "body", "from_op": "sp", "side": "positive"})))
    parse_plan(_plan(_box(), _split(), _move({"kind": "body", "from_op": "sp", "side": "negative"})))


def test_side_is_forbidden_for_a_split_that_keeps_one_piece() -> None:
    for keep in ("positive", "negative"):
        error = _rule_of(_box(), _split(keep=keep), _move({"kind": "body", "from_op": "sp", "side": "positive"}))
        assert error.rule == "V3"
        assert "side" in error.message
        parse_plan(_plan(_box(), _split(keep=keep), _move({"kind": "body", "from_op": "sp"})))


def test_side_is_forbidden_for_a_ref_to_any_other_op() -> None:
    error = _rule_of(_box(), _move({"kind": "body", "from_op": "b", "side": "negative"}))
    assert error.rule == "V3"
    assert "side" in error.message


def test_a_split_can_split_a_piece_of_an_earlier_split() -> None:
    again = _split("sp2", source={"kind": "body", "from_op": "sp", "side": "negative"})
    plan = parse_plan(_plan(_box(), _split(), again))
    assert plan.operations[2].source.side == "negative"  # type: ignore[union-attr]


def test_a_face_ref_to_a_split_needs_no_side() -> None:
    cut = {
        "id": "sk",
        "op": "sketch",
        "intent": "profile",
        "on": {"kind": "face", "from_op": "sp"},
        "entities": [{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 4}],
    }
    parse_plan(_plan(_box(), _split(), cut))


def test_copy_body_scale_defaults_to_one_about_the_origin() -> None:
    op = CopyBodyOperation(id="c", intent="i", source=BodyRef(from_op="b"))
    assert op.scale == 1.0
    assert op.scale_about == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf")])
def test_copy_body_scale_must_be_a_finite_positive_number(scale: float) -> None:
    with pytest.raises(ValidationError):
        CopyBodyOperation(id="c", intent="i", source=BodyRef(from_op="b"), scale=scale)


@pytest.mark.parametrize("about", [(float("nan"), 0.0, 0.0), (0.0, float("inf"), 0.0)])
def test_copy_body_scale_about_must_be_finite(about: tuple[float, float, float]) -> None:
    with pytest.raises(ValidationError):
        CopyBodyOperation(id="c", intent="i", source=BodyRef(from_op="b"), scale=1.006, scale_about=about)


def test_a_scaled_copy_parses() -> None:
    copy = {
        "id": "c",
        "op": "copy_body",
        "intent": "shrinkage allowance",
        "source": {"kind": "body", "from_op": "b"},
        "scale": 1.006,
        "scale_about": [20, 15, 0],
        "offset": [60, 0, 0],
    }
    plan = parse_plan(_plan(_box(), copy))
    assert plan.operations[1].scale == 1.006  # type: ignore[union-attr]
