"""IR tests for the ``shell`` operation: schema, vocabulary tier, references (V2/V3)."""

import json

import pytest
from pydantic import ValidationError

from cotaforge.ir import OP_NAMES, produces_feature
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import OP_CATEGORY, ShellOperation
from cotaforge.ir.plan import Plan, parse_plan, plan_json_schema
from cotaforge.ir.refs import FaceRef

TOP = {"kind": "face", "from_op": "b", "select": {"normal": [0, 0, 1]}}


def _box(op_id: str = "b") -> dict:
    return {"id": op_id, "op": "box", "intent": "block", "size": [40, 30, 20]}


def _shell(op_id: str = "sh", **extra: object) -> dict:
    return {"id": op_id, "op": "shell", "intent": "hollow it", "faces": [TOP], "thickness_mm": 2, **extra}


def _plan(*operations: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": list(operations)}


def _rule_of(*operations: dict) -> PlanError:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(*operations))
    return info.value


def test_shell_defaults_to_an_inward_intersection_join() -> None:
    op = ShellOperation(id="sh", intent="hollow", faces=(FaceRef(from_op="b"),), thickness_mm=2)
    assert op.op == "shell"
    assert op.direction == "inward"
    assert op.join == "intersection"


def test_shell_needs_a_removed_face_and_a_positive_thickness() -> None:
    face = FaceRef(from_op="b")
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="hollow", faces=(), thickness_mm=2)
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="hollow", faces=(face,), thickness_mm=0)
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="hollow", faces=(face,), thickness_mm=-1)
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="hollow", faces=(face,), thickness_mm=float("inf"))
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="hollow", faces=(face,), thickness_mm=float("nan"))


def test_shell_direction_and_join_are_closed_vocabularies_and_extras_are_forbidden() -> None:
    face = FaceRef(from_op="b")
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="x", faces=(face,), thickness_mm=2, direction="sideways")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="x", faces=(face,), thickness_mm=2, join="round")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ShellOperation(id="sh", intent="x", faces=(face,), thickness_mm=2, wall_mm=3)  # type: ignore[call-arg]


def test_shell_refs_collect_every_removed_face() -> None:
    a, b = FaceRef(from_op="b"), FaceRef(from_op="c")
    op = ShellOperation(id="sh", intent="hollow", faces=(a, b), thickness_mm=2)
    assert op.refs() == (a, b)


def test_shell_is_an_everyday_detail_only_op_that_produces_a_feature() -> None:
    assert "shell" in OP_NAMES
    assert OP_CATEGORY["shell"] == "everyday"
    assert produces_feature("shell")


def test_shell_round_trips_through_a_detail_plan_and_the_json_schema() -> None:
    plan = parse_plan(_plan(_box(), _shell(direction="outward", join="arc")))
    assert isinstance(plan, Plan)
    op = plan.operations[1]
    assert isinstance(op, ShellOperation)
    assert (op.direction, op.join, op.thickness_mm) == ("outward", "arc", 2)
    assert "ShellOperation" in json.dumps(plan_json_schema())


def test_shell_faces_must_name_an_earlier_operation() -> None:
    assert _rule_of(_shell()).rule == "V2"


def test_a_shell_is_not_a_body_producing_op_for_body_refs() -> None:
    move = {
        "id": "mv",
        "op": "translate",
        "intent": "move",
        "target": {"kind": "body", "from_op": "sh"},
        "offset": [0, 0, 5],
    }
    assert _rule_of(_box(), _shell(), move).rule == "V3"
