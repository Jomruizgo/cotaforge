"""Tests for cotaforge.ir.plan: Plan, parse_plan and validators V1-V14."""

import json

import pytest

from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_primitives import BoxOperation
from cotaforge.ir.plan import Plan, parse_plan, plan_json_schema


def _sketch_op(op_id: str = "s1", entity: dict | None = None) -> dict:
    return {
        "id": op_id,
        "op": "sketch",
        "intent": "base sketch",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [entity or {"kind": "rect", "name": "r1", "center": [0.0, 0.0], "width": 10.0, "height": 10.0}],
    }


def _extrude_op(op_id: str = "e1", sketch: str = "s1", depth: float = 5.0) -> dict:
    return {"id": op_id, "op": "extrude", "intent": "extrude base", "sketch": sketch, "depth_mm": depth}


def _minimal_detail_plan() -> dict:
    return {
        "id": "plate",
        "kind": "part",
        "operations": [_sketch_op(), _extrude_op()],
    }


def test_parse_plan_returns_detail_plan_instance() -> None:
    plan = parse_plan(_minimal_detail_plan())
    assert isinstance(plan, Plan)
    assert plan.id == "plate"


def test_parse_plan_returns_a_plan_of_body_level_ops() -> None:
    data = {
        "id": "blank",
        "kind": "part",
        "operations": [{"id": "b1", "op": "box", "intent": "block", "size": [10.0, 10.0, 10.0]}],
    }
    plan = parse_plan(data)
    assert isinstance(plan, Plan)
    assert isinstance(plan.operations[0], BoxOperation)


def test_plan_defaults() -> None:
    plan = parse_plan(_minimal_detail_plan())
    assert plan.units == "mm"
    assert plan.description == ""


def test_parse_plan_invalid_shape_raises_plan_error() -> None:
    with pytest.raises(PlanError):
        parse_plan({"id": "bad"})  # missing operations


def test_negative_blind_cut_depth_is_a_schema_plan_error() -> None:
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [
            _sketch_op(),
            {
                "id": "c1",
                "op": "cut",
                "intent": "cut",
                "sketch": "s1",
                "end_condition": "blind",
                "depth_mm": -5.0,
            },
        ],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "schema"


def test_plan_json_schema_is_a_dict_with_definitions() -> None:
    schema = plan_json_schema()
    assert isinstance(schema, dict)


# --- V1: duplicate operation id -------------------------------------------------


def test_v1_duplicate_operation_id() -> None:
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [_sketch_op(op_id="s1"), _sketch_op(op_id="s1")],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V1"
    assert exc_info.value.message.startswith("duplicate operation id")


# --- V2: reference to unknown or later operation --------------------------------


def test_v2_reference_to_unknown_operation() -> None:
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [_extrude_op(sketch="does_not_exist")],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V2"
    assert exc_info.value.message.startswith("reference to unknown or later operation")


def test_v2_reference_to_later_operation() -> None:
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [_extrude_op(sketch="s1"), _sketch_op(op_id="s1")],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V2"


def test_v2_does_not_choke_on_a_face_ref_by_from_selection() -> None:
    """A FaceRef named by from_selection has no from_op at all --
    ``_ref_id_kinds`` must skip it instead of treating its ``None`` as a dangling reference to op id
    ``None``. Before the fix, every hole/cut/mate op using ``from_selection`` failed parse_plan/extend_plan
    with 'reference to unknown or later operation: None', even though the ref never named an op."""
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [
            _sketch_op(),
            _extrude_op(),
            {
                "id": "h1",
                "op": "hole",
                "intent": "drill through the selected face",
                "face": {"kind": "face", "from_selection": "doc:1|body:1|face:3"},
                "position": [0.0, 0.0],
                "standard": "simple",
                "diameter_mm": 5.0,
                "end_condition": "through_all",
            },
        ],
    }
    plan = parse_plan(data)
    assert plan.operations[-1].id == "h1"


# --- V3: wrong operation type for reference -------------------------------------


def test_v3_face_ref_targets_sketch_op() -> None:
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [
            _sketch_op(),
            _extrude_op(),
            {
                "id": "h1",
                "op": "hole",
                "intent": "drill",
                "face": {"kind": "face", "from_op": "s1"},
                "position": [5.0, 5.0],
                "standard": "simple",
                "diameter_mm": 6.6,
                "end_condition": "through_all",
            },
        ],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V3"
    assert exc_info.value.message.startswith("wrong operation type for reference")


def test_v3_cut_scope_body_ref_targets_a_non_body_producing_op() -> None:
    """``CutOperation.scope``'s ``BodyRef``s go through the same generic V3 check as ``boolean_*``'s
    ``target``/``tools`` (``OperationBase.refs()`` walks every field, ``scope`` included) -- no dedicated
    validator needed for it to reject a scope that names, say, a sketch instead of a body-producing op."""
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [
            _sketch_op(),
            _extrude_op(),
            {
                "id": "c1",
                "op": "cut",
                "intent": "cope the notch",
                "sketch": "s1",
                "end_condition": "through_all",
                "scope": [{"kind": "body", "from_op": "s1"}],
            },
        ],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V3"


def test_v3_sketch_field_targets_non_sketch_op() -> None:
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [_sketch_op(), _extrude_op(), _extrude_op(op_id="e2", sketch="e1")],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V3"


# --- V4: operation not allowed in this document kind ----------------------------


def test_v4_assembly_op_in_part_plan() -> None:
    data = {
        "id": "asm",
        "kind": "part",
        "operations": [
            {
                "id": "ins1",
                "op": "insert_component",
                "intent": "insert bracket",
                "part": "bracket",
                "placement": {},
            }
        ],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V4"
    assert exc_info.value.message.startswith("operation not allowed in this document kind")


def test_v4_non_assembly_op_in_assembly_plan() -> None:
    data = {
        "id": "asm",
        "kind": "assembly",
        "operations": [_sketch_op(), _extrude_op()],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V4"


def test_v4_sketch_allowed_in_assembly_plan() -> None:
    data = {
        "id": "asm",
        "kind": "assembly",
        "operations": [
            _sketch_op(),
            {
                "id": "ins1",
                "op": "insert_component",
                "intent": "insert bracket",
                "part": "bracket",
                "placement": {},
            },
        ],
    }
    plan = parse_plan(data)
    assert plan.kind == "assembly"


# --- V5: revolve axis entity not found -------------------------------------------


def test_v5_revolve_axis_entity_not_a_line() -> None:
    data = {
        "id": "spindle",
        "kind": "part",
        "operations": [
            _sketch_op(entity={"kind": "circle", "name": "c1", "center": [0.0, 0.0], "diameter": 10.0}),
            {"id": "r1", "op": "revolve", "intent": "revolve profile", "sketch": "s1", "axis": "c1"},
        ],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V5"
    assert exc_info.value.message.startswith("revolve axis entity not found")


def test_v5_revolve_axis_uv_shorthand_ok() -> None:
    data = {
        "id": "spindle",
        "kind": "part",
        "operations": [
            _sketch_op(entity={"kind": "circle", "name": "c1", "center": [0.0, 20.0], "diameter": 10.0}),
            {"id": "r1", "op": "revolve", "intent": "revolve profile", "sketch": "s1", "axis": "u"},
        ],
    }
    plan = parse_plan(data)
    assert plan.operations[1].axis == "u"


# --- V8: revolve profile on one side of the axis ----------------------------------


def _revolve_plan(entity: dict, axis: str) -> dict:
    return {
        "id": "spindle",
        "kind": "part",
        "operations": [
            _sketch_op(entity=entity),
            {"id": "r1", "op": "revolve", "intent": "revolve profile", "sketch": "s1", "axis": axis},
        ],
    }


@pytest.mark.parametrize(
    ("entity", "axis"),
    [
        ({"kind": "circle", "name": "c1", "center": [0.0, 0.0], "diameter": 10.0}, "u"),  # centred on the axis
        ({"kind": "rect", "name": "r1", "center": [0.0, 0.0], "width": 10.0, "height": 10.0}, "v"),  # straddles u = 0
        ({"kind": "rect", "name": "r1", "center": [10.0, 2.0], "width": 4.0, "height": 8.0}, "u"),  # v in [-2, 6]
    ],
)
def test_v8_revolve_profile_crossing_the_axis_is_rejected(entity: dict, axis: str) -> None:
    with pytest.raises(PlanError) as exc_info:
        parse_plan(_revolve_plan(entity, axis))
    assert exc_info.value.rule == "V8" and exc_info.value.op_id == "r1"
    assert exc_info.value.message.startswith("revolve profile crosses the axis")


def test_v8_revolve_profile_touching_the_axis_from_one_side_is_accepted() -> None:
    # a polyline with one segment ON the axis (v = 0) and the rest at v > 0
    entities = [
        {"kind": "line", "name": "axis_seg", "start": [0.0, 0.0], "end": [40.0, 0.0]},
        {"kind": "line", "name": "end", "start": [40.0, 0.0], "end": [40.0, 6.0]},
        {"kind": "line", "name": "seat", "start": [40.0, 6.0], "end": [0.0, 6.0]},
        {"kind": "line", "name": "back", "start": [0.0, 6.0], "end": [0.0, 0.0]},
    ]
    data = _revolve_plan(entities[0], "u")
    data["operations"][0]["entities"] = entities
    assert parse_plan(data).operations[1].axis == "u"
    below = {"kind": "rect", "name": "r1", "center": [10.0, -5.0], "width": 4.0, "height": 10.0}  # v in [-10, 0]
    assert parse_plan(_revolve_plan(below, "u")).operations[1].axis == "u"


def test_v8_uses_a_named_line_entity_as_the_axis() -> None:
    entities = [  # a closed loop left of u = 0 whose right edge is the axis, plus a rect right of it
        {"kind": "line", "name": "spine", "start": [0.0, -50.0], "end": [0.0, 50.0]},
        {"kind": "line", "name": "top", "start": [0.0, 50.0], "end": [-10.0, 50.0]},
        {"kind": "line", "name": "left", "start": [-10.0, 50.0], "end": [-10.0, -50.0]},
        {"kind": "line", "name": "bottom", "start": [-10.0, -50.0], "end": [0.0, -50.0]},
        {"kind": "rect", "name": "r1", "center": [10.0, 0.0], "width": 4.0, "height": 10.0},  # u in [8, 12]: right
    ]
    data = _revolve_plan(entities[0], "spine")
    data["operations"][0]["entities"] = entities
    assert parse_plan(data).operations[1].axis == "spine"
    data["operations"][0]["entities"][4]["center"] = [1.0, 0.0]  # u in [-1, 3]: crosses the spine
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V8"


# --- V6: sketch has no closed profile --------------------------------------------


def test_v6_sketch_with_open_line_chain() -> None:
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [
            _sketch_op(entity={"kind": "line", "name": "l1", "start": [0.0, 0.0], "end": [10.0, 0.0]}),
            _extrude_op(),
        ],
    }
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V6"
    assert exc_info.value.message.startswith("sketch has no closed profile")


def test_parse_plan_yields_plain_validation_error_context_for_bad_types() -> None:
    # A non-dict / structurally broken input still surfaces as a PlanError, not
    # a raw pydantic ValidationError, even though it's not one of the V-rules.
    with pytest.raises(PlanError):
        parse_plan({"id": "plate", "kind": "part", "operations": "not-a-list"})


# --- V9 / ValidationStamp: a validated stamp may not be declared by a document -----


_STAMP = {"adapter": "fake", "at": "2026-01-01T00:00:00Z", "trace": "trace-1"}


def test_plan_without_validated_field_still_parses_and_serializes_without_the_key() -> None:
    # Regression: every plan written before this field existed) carry no
    # `validated` key at all, and must keep validating and round-tripping unchanged.
    plan = parse_plan(_minimal_detail_plan())
    assert plan.validated is None
    assert "validated" not in plan.model_dump(mode="json", exclude_none=True)


def test_parse_plan_refuses_a_declared_stamp_by_default() -> None:
    data = _minimal_detail_plan()
    data["validated"] = _STAMP
    with pytest.raises(PlanError) as exc_info:
        parse_plan(data)
    assert exc_info.value.rule == "V9"


def test_parse_plan_accepts_a_declared_stamp_with_allow_stamp() -> None:
    data = _minimal_detail_plan()
    data["validated"] = _STAMP
    plan = parse_plan(data, allow_stamp=True)
    assert plan.validated is not None
    assert plan.validated.adapter == "fake"
    assert plan.validated.trace == "trace-1"


def test_parse_plan_roundtrip_with_stamp_is_byte_identical() -> None:
    data = _minimal_detail_plan()
    data["validated"] = _STAMP
    plan = parse_plan(data, allow_stamp=True)
    dumped = plan.model_dump_json()
    reparsed = parse_plan(json.loads(dumped), allow_stamp=True)
    assert reparsed.model_dump_json() == dumped


def test_schema_error_names_the_field_path_and_the_operation() -> None:
    """A pydantic schema failure inside an operation reports the plan-root path and the operation id, so a caller can
    fix the right field."""
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [
            {"id": "base_sketch", "op": "sketch", "entities": [], "intent": "missing the plane"},
        ],
    }
    with pytest.raises(PlanError) as info:
        parse_plan(data)
    assert info.value.rule == "schema"
    assert info.value.op_id == "base_sketch"
    assert "at operations.0.sketch.on" in str(info.value)


def test_extra_field_on_a_nested_selector_names_the_full_path_not_silently_dropped() -> None:
    """A mistyped selector field (nrml for normal) must fail loudly with the exact
    field path, never be silently dropped by pydantic (which would leave the selector unconstrained
    and match every candidate face)."""
    data = {
        "id": "plate",
        "kind": "part",
        "operations": [
            _sketch_op(),
            _extrude_op(),
            {
                "id": "h1",
                "op": "hole",
                "intent": "drill",
                "face": {"kind": "face", "from_op": "e1", "select": {"nrml": [0.0, 0.0, 1.0]}},
                "position": [0.0, 0.0],
                "standard": "simple",
                "diameter_mm": 5.0,
                "end_condition": "through_all",
            },
        ],
    }
    with pytest.raises(PlanError) as info:
        parse_plan(data)
    assert info.value.rule == "schema"
    assert info.value.op_id == "h1"
    assert "nrml" in str(info.value)
    assert "face.select.nrml" in str(info.value)


def test_extra_field_on_the_plan_root_is_rejected() -> None:
    data = _minimal_detail_plan()
    data["descripton"] = "typo"
    with pytest.raises(PlanError):
        parse_plan(data)
