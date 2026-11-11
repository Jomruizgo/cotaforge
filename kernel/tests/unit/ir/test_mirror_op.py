"""IR tests for ``mirror`` and ``mirror_body``: schema, tiers, references (V2/V3)."""

import json

import pytest
from pydantic import ValidationError

from cotaforge.ir import OP_NAMES, produces_feature
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import OP_CATEGORY, MirrorBodyOperation, MirrorOperation
from cotaforge.ir.plan import Plan, parse_plan, plan_json_schema
from cotaforge.ir.refs import BodyRef, FaceRef, PlaneRef

SIDE = {"kind": "face", "from_op": "b", "select": {"normal": [1, 0, 0]}}


def _box(op_id: str = "b") -> dict:
    return {"id": op_id, "op": "box", "intent": "block", "size": [40, 30, 20]}


def _boss(op_id: str = "boss") -> dict:
    return {"id": op_id, "op": "extrude", "intent": "boss", "sketch": "sk", "depth_mm": 5}


def _sketch() -> dict:
    return {
        "id": "sk",
        "op": "sketch",
        "intent": "boss profile",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "circle", "name": "c", "center": [10, 10], "diameter": 8}],
    }


def _mirror(op_id: str = "m", **extra: object) -> dict:
    fields: dict = {
        "id": op_id,
        "op": "mirror",
        "intent": "mirror the boss",
        "features": ["boss"],
        "plane": {"kind": "plane", "name": "YZ"},
    }
    fields.update(extra)
    return fields


def _mirror_body(op_id: str = "mb", **extra: object) -> dict:
    fields: dict = {
        "id": op_id,
        "op": "mirror_body",
        "intent": "mirror the half",
        "source": {"kind": "body", "from_op": "b"},
        "plane": {"kind": "plane", "name": "YZ"},
    }
    fields.update(extra)
    return fields


def _plan(*operations: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": list(operations)}


def _rule_of(*operations: dict) -> PlanError:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(*operations))
    return info.value


def test_mirror_builds_from_features_and_a_plane_or_a_face() -> None:
    op = MirrorOperation(id="m", intent="i", features=("boss",), plane=PlaneRef(name="YZ"))
    assert op.op == "mirror"
    assert op.features == ("boss",)
    assert MirrorOperation(id="m", intent="i", features=("boss",), plane=FaceRef(from_op="b")).plane == FaceRef(
        from_op="b"
    )


def test_mirror_needs_a_feature_a_plane_and_forbids_extras() -> None:
    with pytest.raises(ValidationError):
        MirrorOperation(id="m", intent="i", features=(), plane=PlaneRef(name="YZ"))
    with pytest.raises(ValidationError):
        MirrorOperation(id="m", intent="i", features=("boss",), plane="YZ")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        MirrorOperation(id="m", intent="i", features=("boss",), plane=PlaneRef(name="YZ"), count=2)  # type: ignore[call-arg]


def test_mirror_body_builds_from_a_body_and_a_plane() -> None:
    op = MirrorBodyOperation(id="mb", intent="i", source=BodyRef(from_op="b"), plane=PlaneRef(name="XZ"))
    assert op.op == "mirror_body"
    with pytest.raises(ValidationError):
        MirrorBodyOperation(id="mb", intent="i", source="b", plane=PlaneRef(name="XZ"))  # type: ignore[arg-type]


def test_refs_collect_the_plane_and_the_source_body() -> None:
    plane = PlaneRef(from_op="pl")
    assert MirrorOperation(id="m", intent="i", features=("boss",), plane=plane).refs() == (plane,)
    body = BodyRef(from_op="b")
    assert MirrorBodyOperation(id="mb", intent="i", source=body, plane=plane).refs() == (body, plane)


def test_mirror_is_an_everyday_feature_op_and_mirror_body_a_multibody_one() -> None:
    for name in ("mirror", "mirror_body"):
        assert name in OP_NAMES
    assert OP_CATEGORY["mirror"] == "everyday"
    assert OP_CATEGORY["mirror_body"] == "multibody"
    assert produces_feature("mirror")
    assert produces_feature("mirror_body")


def test_both_round_trip_through_a_detail_plan_and_the_json_schema() -> None:
    plan = parse_plan(_plan(_box(), _sketch(), _boss(), _mirror(), _mirror_body()))
    assert isinstance(plan, Plan)
    assert isinstance(plan.operations[3], MirrorOperation)
    assert isinstance(plan.operations[4], MirrorBodyOperation)
    schema = json.dumps(plan_json_schema())
    assert "MirrorOperation" in schema
    assert "MirrorBodyOperation" in schema


def test_mirror_features_must_name_an_earlier_feature_operation() -> None:
    assert _rule_of(_mirror()).rule == "V2"
    assert _rule_of(_mirror(), _sketch(), _boss()).rule == "V2"


def test_mirror_features_cannot_be_a_sketch_or_a_datum() -> None:
    assert _rule_of(_sketch(), _mirror(features=["sk"])).rule == "V3"


def test_mirror_plane_must_name_an_earlier_ref_plane() -> None:
    assert _rule_of(_box(), _mirror_body(plane={"kind": "plane", "from_op": "later"})).rule == "V2"
    assert _rule_of(_box(), _mirror_body(plane={"kind": "plane", "from_op": "b"})).rule == "V3"


def test_a_mirror_body_source_must_be_a_body_producing_op_and_a_mirror_body_is_one() -> None:
    assert _rule_of(_sketch(), _mirror_body(source={"kind": "body", "from_op": "sk"})).rule == "V3"
    move = {
        "id": "mv",
        "op": "translate",
        "intent": "move",
        "target": {"kind": "body", "from_op": "mb"},
        "offset": [0, 0, 5],
    }
    parse_plan(_plan(_box(), _mirror_body(), move))
    # a feature mirror makes no body: a later body ref to it is a V3 error
    after_mirror = {**move, "target": {"kind": "body", "from_op": "m"}}
    assert _rule_of(_box(), _sketch(), _boss(), _mirror(), after_mirror).rule == "V3"
