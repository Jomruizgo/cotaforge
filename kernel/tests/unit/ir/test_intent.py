"""Intent links on operations ."""

import pytest
from pydantic import ValidationError

from cotaforge.ir import (
    NON_FEATURE_OP_NAMES,
    BoxOperation,
    ExtrudeOperation,
    HoleOperation,
    IntentLink,
    parse_plan,
    plan_json_schema,
    produces_feature,
)
from tests.unit.ir.fixtures import plate_plan_dict


def test_links_default_to_empty() -> None:
    op = BoxOperation(id="b", intent="a box", size=(1.0, 2.0, 3.0))
    assert op.links == ()


def test_links_parse_from_plan_dict_and_round_trip() -> None:
    data = plate_plan_dict()
    data["operations"][1]["links"] = [
        {"kind": "brief", "ref": "constraints[0]"},
        {"kind": "calc", "ref": "plate_thickness", "note": "from the bending calc"},
    ]
    plan = parse_plan(data)
    extrude = plan.operations[1]
    assert isinstance(extrude, ExtrudeOperation)
    assert extrude.links == (
        IntentLink(kind="brief", ref="constraints[0]"),
        IntentLink(kind="calc", ref="plate_thickness", note="from the bending calc"),
    )
    assert parse_plan(plan.model_dump(mode="json")) == plan


@pytest.mark.parametrize("bad", [{"kind": "wish", "ref": "x"}, {"kind": "brief", "ref": ""}, {"kind": "brief"}])
def test_bad_links_are_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        IntentLink.model_validate(bad)


def test_unknown_link_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        IntentLink.model_validate({"kind": "brief", "ref": "x", "extra": 1})


def test_refs_ignore_links() -> None:
    hole = HoleOperation(
        id="h",
        intent="hole",
        face={"kind": "face", "from_op": "base"},
        position=(0.0, 0.0),
        standard="simple",
        diameter_mm=6.0,
        end_condition="through_all",
        links=(IntentLink(kind="brief", ref="interfaces[0]"),),
    )
    refs = hole.refs()
    assert len(refs) == 1
    assert refs[0].kind == "face"


def test_json_schema_includes_links() -> None:
    schema = plan_json_schema()
    box = schema["$defs"]["BoxOperation"]
    assert "links" in box["properties"]
    link = schema["$defs"]["IntentLink"]
    assert set(link["properties"]) == {"kind", "ref", "note"}
    assert link["properties"]["kind"]["enum"] == ["brief", "calc", "rule", "requirement"]


def test_produces_feature_predicate() -> None:
    assert NON_FEATURE_OP_NAMES == frozenset({"sketch", "ref_plane", "ref_axis", "check_interference"})
    assert produces_feature("extrude")
    assert not produces_feature("sketch")
    assert not produces_feature("check_interference")
