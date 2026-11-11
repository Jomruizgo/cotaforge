"""IR tests for the ``draft`` operation: schema, vocabulary tier, references (V2/V3) and V13."""

import json

import pytest
from pydantic import ValidationError

from cotaforge.contract import AxisName
from cotaforge.ir import OP_NAMES, produces_feature
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import OP_CATEGORY, DraftOperation
from cotaforge.ir.plan import Plan, parse_plan, plan_json_schema
from cotaforge.ir.refs import FaceRef, PlaneRef

SIDE = {"kind": "face", "from_op": "b", "select": {"normal": [1, 0, 0]}}
BOTTOM = {"kind": "face", "from_op": "b", "select": {"normal": [0, 0, -1]}}


def _box(op_id: str = "b") -> dict:
    return {"id": op_id, "op": "box", "intent": "block", "size": [40, 30, 20]}


def _draft(op_id: str = "d", **extra: object) -> dict:
    fields: dict = {
        "id": op_id,
        "op": "draft",
        "intent": "taper the wall",
        "faces": [SIDE],
        "neutral": {"kind": "plane", "name": "XY"},
        "pull": "Z",
        "angle_deg": 3,
    }
    fields.update(extra)
    return fields


def _plan(*operations: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": list(operations)}


def _rule_of(*operations: dict) -> PlanError:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(*operations))
    return info.value


def _op(**extra: object) -> DraftOperation:
    fields: dict = {
        "id": "d",
        "intent": "taper",
        "faces": (FaceRef(from_op="b"),),
        "neutral": PlaneRef(name="XY"),
        "pull": AxisName.Z,
        "angle_deg": 3.0,
    }
    fields.update(extra)
    return DraftOperation(**fields)


def test_draft_builds_from_a_face_a_neutral_plane_a_pull_and_an_angle() -> None:
    op = _op()
    assert op.op == "draft"
    assert op.pull == AxisName.Z
    assert op.angle_deg == 3.0


def test_draft_needs_at_least_one_face() -> None:
    with pytest.raises(ValidationError):
        _op(faces=())


@pytest.mark.parametrize("angle", [0, -1, 30.0001, 45, float("inf"), float("nan")])
def test_draft_angle_must_be_above_zero_and_at_most_thirty_degrees(angle: float) -> None:
    with pytest.raises(ValidationError):
        _op(angle_deg=angle)


@pytest.mark.parametrize("angle", [0.1, 3, 30])
def test_draft_angle_accepts_its_range(angle: float) -> None:
    assert _op(angle_deg=angle).angle_deg == angle


def test_draft_neutral_is_a_plane_or_a_face_and_extras_are_forbidden() -> None:
    assert _op(neutral=FaceRef(from_op="b")).neutral == FaceRef(from_op="b")
    with pytest.raises(ValidationError):
        _op(neutral="XY")
    with pytest.raises(ValidationError):
        _op(draft_deg=3)


def test_draft_pull_is_an_axis_name_or_a_vector() -> None:
    assert _op(pull=(0.0, 0.0, -1.0)).pull == (0.0, 0.0, -1.0)
    with pytest.raises(ValidationError):
        _op(pull="W")


def test_draft_refs_collect_the_faces_and_the_neutral_reference() -> None:
    a, b, neutral = FaceRef(from_op="b"), FaceRef(from_op="c"), PlaneRef(from_op="pl")
    assert _op(faces=(a, b), neutral=neutral).refs() == (a, b, neutral)


def test_draft_is_an_everyday_detail_only_op_that_produces_a_feature() -> None:
    assert "draft" in OP_NAMES
    assert OP_CATEGORY["draft"] == "everyday"
    assert produces_feature("draft")


def test_draft_round_trips_through_a_detail_plan_and_the_json_schema() -> None:
    plan = parse_plan(_plan(_box(), _draft(pull=[0, 0, -1])))
    assert isinstance(plan, Plan)
    op = plan.operations[1]
    assert isinstance(op, DraftOperation)
    assert (op.pull, op.angle_deg) == ((0.0, 0.0, -1.0), 3)
    assert "DraftOperation" in json.dumps(plan_json_schema())


def test_draft_faces_and_neutral_must_name_an_earlier_operation() -> None:
    assert _rule_of(_draft()).rule == "V2"
    assert _rule_of(_box(), _draft(neutral={"kind": "plane", "from_op": "later"})).rule == "V2"


def test_a_neutral_plane_reference_must_be_a_ref_plane() -> None:
    assert _rule_of(_box(), _draft(neutral={"kind": "plane", "from_op": "b"})).rule == "V3"


def test_a_draft_is_not_a_body_producing_op_for_body_refs() -> None:
    move = {
        "id": "mv",
        "op": "translate",
        "intent": "move",
        "target": {"kind": "body", "from_op": "d"},
        "offset": [0, 0, 5],
    }
    assert _rule_of(_box(), _draft(), move).rule == "V3"


@pytest.mark.parametrize("pull", [[0, 0, 0], [0, 0, 1e-12], [0, 0, float("nan")], [0, float("inf"), 0]])
def test_v13_refuses_a_pull_with_no_direction(pull: list[float]) -> None:
    error = _rule_of(_box(), _draft(pull=pull))
    assert error.rule == "V13"
    assert "pull" in str(error)


def test_v13_accepts_a_signed_axis_name_and_a_non_unit_vector() -> None:
    parse_plan(_plan(_box(), _draft(pull="Y")))
    parse_plan(_plan(_box(), _draft(pull=[0, 0, -5])))
