"""IR tests for the datum ops ``ref_plane`` / ``ref_axis`` and the refs they introduce
(``PlaneRef.from_op``, ``AxisRef``): schema, validators V2/V3/V4/V7/V10, vocabulary and narration."""

import json

import pytest
from pydantic import ValidationError

from cotaforge.ir import NON_FEATURE_OP_NAMES, produces_feature
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import (
    OP_CATEGORY,
    OP_NAMES,
    RefAxisOperation,
    RefPlaneOperation,
    SketchOperation,
)
from cotaforge.ir.plan import Plan, parse_plan, plan_json_schema
from cotaforge.ir.refs import REF_TYPES, AxisRef, FaceRef, PlaneRef

# --- refs --------------------------------------------------------------------------


def test_plane_ref_accepts_name_or_from_op() -> None:
    assert PlaneRef(name="XY").from_op is None
    ref = PlaneRef(from_op="mid")
    assert ref.name is None
    assert ref.kind == "plane"


def test_plane_ref_needs_exactly_one_of_name_and_from_op() -> None:
    with pytest.raises(ValidationError, match="exactly one of"):
        PlaneRef()
    with pytest.raises(ValidationError, match="exactly one of"):
        PlaneRef(name="XY", from_op="mid")


def test_axis_ref_is_a_walkable_ref() -> None:
    assert AxisRef in REF_TYPES
    ref = AxisRef(from_op="tube_axis")
    assert ref.kind == "axis_ref"
    with pytest.raises(ValidationError):
        AxisRef()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AxisRef(from_op="a", extra=1)  # type: ignore[call-arg]


# --- ops ---------------------------------------------------------------------------


def _plane(op_id: str = "dp", define: dict | None = None) -> dict:
    return {
        "id": op_id,
        "op": "ref_plane",
        "intent": "datum",
        "define": define or {"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 10.0},
    }


def _axis(op_id: str = "da", define: dict | None = None) -> dict:
    return {
        "id": op_id,
        "op": "ref_axis",
        "intent": "datum axis",
        "define": define or {"how": "two_points", "points": [[0, 0, 0], [0, 0, 5]]},
    }


def _plan(*operations: dict, kind: str = "part") -> dict:
    return {"id": "p", "kind": kind, "operations": list(operations)}


def _rect_sketch(op_id: str, on: dict) -> dict:
    return {
        "id": op_id,
        "op": "sketch",
        "intent": "profile",
        "on": on,
        "entities": [{"kind": "rect", "name": "r", "center": [0.0, 0.0], "width": 10.0, "height": 10.0}],
    }


@pytest.mark.parametrize(
    "define",
    [
        {"how": "offset", "base": {"kind": "plane", "name": "XZ"}, "distance_mm": -3.0},
        {"how": "offset", "base": {"kind": "face", "from_selection": "doc|body|Face3"}, "distance_mm": 0},
        {
            "how": "angle",
            "base": {"kind": "plane", "name": "XY"},
            "about": {"origin": [0, 0, 0], "direction": [1, 0, 0]},
            "angle_deg": 30.0,
        },
        {"how": "three_points", "points": [[0, 0, 0], [1, 0, 0], [0, 1, 0]]},
        {"how": "normal_to", "axis": {"origin": [0, 0, 0], "direction": [0, 0, 1]}, "at_mm": 12.5},
        {
            "how": "midplane",
            "a": {"kind": "plane", "name": "XY"},
            "b": {"kind": "face", "from_selection": "doc|body|Face3"},
        },
    ],
)
def test_ref_plane_define_variants_parse(define: dict) -> None:
    plan = parse_plan(_plan(_plane(define=define)))
    assert isinstance(plan.operations[0], RefPlaneOperation)
    assert plan.operations[0].define.how == define["how"]


@pytest.mark.parametrize(
    "define",
    [
        {"how": "two_points", "points": [[0, 0, 0], [0, 0, 5]]},
        {"how": "point_direction", "point": [1, 2, 3], "direction": [0, 0, 1]},
        {"how": "point_direction", "point": [1, 2, 3], "direction": "Z"},
        {"how": "entity", "entity": {"kind": "face", "from_selection": "doc|body|Face3"}},
        {"how": "plane_intersection", "a": {"kind": "plane", "name": "XY"}, "b": {"kind": "plane", "name": "YZ"}},
    ],
)
def test_ref_axis_define_variants_parse(define: dict) -> None:
    plan = parse_plan(_plan(_axis(define=define)))
    assert isinstance(plan.operations[0], RefAxisOperation)
    assert plan.operations[0].define.how == define["how"]


def test_ref_ops_reject_unknown_fields_anywhere() -> None:
    bad_op = {**_plane(), "colour": "red"}
    with pytest.raises(PlanError):
        parse_plan(_plan(bad_op))
    bad_define = _plane(define={"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 1, "x": 1})
    with pytest.raises(PlanError):
        parse_plan(_plan(bad_define))
    with pytest.raises(PlanError):
        parse_plan(_plan(_plane(define={"how": "spline"})))


def test_ref_ops_require_intent() -> None:
    op = _plane()
    del op["intent"]
    with pytest.raises(PlanError):
        parse_plan(_plan(op))


def test_json_round_trip() -> None:
    plan = parse_plan(
        _plan(_axis(), _plane("dp2", {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "da"}, "at_mm": 3}))
    )
    again = parse_plan(json.loads(plan.model_dump_json()))
    assert again == plan
    assert "ref_plane" in json.dumps(plan_json_schema())
    assert "axis_ref" in json.dumps(plan_json_schema())


# --- vocabulary --------------------------------------------------------------------


def test_datum_ops_are_detail_everyday_and_non_feature() -> None:
    for name in ("ref_plane", "ref_axis"):
        assert name in OP_NAMES
        assert OP_CATEGORY[name] == "everyday"
        assert name in NON_FEATURE_OP_NAMES
        assert not produces_feature(name)


@pytest.mark.parametrize("op", [_plane(), _axis()])
def test_datum_ops_are_rejected_in_assemblies(op: dict) -> None:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(op, kind="assembly"))
    assert info.value.rule == "V4"


# --- V2 / V3 -----------------------------------------------------------------------


def test_sketch_on_datum_plane_parses_and_exposes_refs() -> None:
    plan = parse_plan(_plan(_plane(), _rect_sketch("s", {"kind": "plane", "from_op": "dp"})))
    sketch = plan.operations[1]
    assert isinstance(sketch, SketchOperation)
    assert isinstance(sketch.on, PlaneRef)
    assert sketch.on.from_op == "dp"


def test_v2_forward_reference_to_a_datum_is_rejected() -> None:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_rect_sketch("s", {"kind": "plane", "from_op": "dp"}), _plane()))
    assert info.value.rule == "V2"


def test_v3_plane_ref_must_target_a_ref_plane() -> None:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_axis(), _rect_sketch("s", {"kind": "plane", "from_op": "da"})))
    assert info.value.rule == "V3"
    assert "datum_plane ref to 'da'" in info.value.message


def test_v3_axis_ref_must_target_a_ref_axis() -> None:
    normal_to = {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "dp"}, "at_mm": 1}
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_plane(), _plane("dp2", normal_to)))
    assert info.value.rule == "V3"
    assert "datum_axis ref to 'dp'" in info.value.message


def test_datum_chain_parses() -> None:
    offset = _plane("dp1")
    angled = _plane(
        "dp2",
        {
            "how": "angle",
            "base": {"kind": "plane", "from_op": "dp1"},
            "about": {"origin": [0, 0, 10], "direction": [1, 0, 0]},
            "angle_deg": 45,
        },
    )
    axis = _axis(
        "da",
        {
            "how": "plane_intersection",
            "a": {"kind": "plane", "from_op": "dp1"},
            "b": {"kind": "plane", "from_op": "dp2"},
        },
    )
    normal = _plane("dp3", {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "da"}, "at_mm": 5})
    plan = parse_plan(_plan(offset, angled, axis, normal))
    assert isinstance(plan, Plan)


def test_face_ref_cannot_name_a_datum_op() -> None:
    hole_like = {
        "id": "cut1",
        "op": "chamfer",
        "intent": "x",
        "edges": [{"kind": "edge", "of": {"kind": "face", "from_op": "dp"}, "curve": "line"}],
        "distance1_mm": 1.0,
    }
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_plane(), hole_like))
    assert info.value.rule == "V3"


def test_axis_ref_consumers_in_patterns_are_type_checked() -> None:
    plan = parse_plan(
        _plan(
            {"id": "b", "op": "box", "intent": "x", "size": [10.0, 10.0, 10.0]},
            _axis("da"),
            {
                "id": "cp",
                "op": "circular_pattern",
                "intent": "x",
                "features": ["b"],
                "axis": {"kind": "axis_ref", "from_op": "da"},
                "count": 3,
            },
            {
                "id": "lp",
                "op": "linear_pattern",
                "intent": "x",
                "features": ["b"],
                "direction": {"kind": "axis_ref", "from_op": "da"},
                "spacing_mm": 5.0,
                "count": 2,
                "direction2": "X",
                "spacing2_mm": 5.0,
                "count2": 2,
            },
        )
    )
    assert len(plan.operations) == 4
    with pytest.raises(PlanError) as info:
        parse_plan(
            _plan(
                {"id": "b", "op": "box", "intent": "x", "size": [10.0, 10.0, 10.0]},
                _plane("dp"),
                {
                    "id": "cp",
                    "op": "circular_pattern",
                    "intent": "x",
                    "features": ["b"],
                    "axis": {"kind": "axis_ref", "from_op": "dp"},
                    "count": 3,
                },
            )
        )
    assert info.value.rule == "V3"


# --- V10 ---------------------------------------------------------------------------


def test_v10_collinear_three_points() -> None:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_plane(define={"how": "three_points", "points": [[0, 0, 0], [1, 1, 1], [3, 3, 3]]})))
    assert info.value.rule == "V10"
    assert "ref_plane.define.points are collinear: choose three non-collinear points" in info.value.message


def test_v10_coincident_two_points_axis_and_zero_direction() -> None:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_axis(define={"how": "two_points", "points": [[1, 1, 1], [1, 1, 1]]})))
    assert info.value.rule == "V10"
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_axis(define={"how": "point_direction", "point": [0, 0, 0], "direction": [0, 0, 0]})))
    assert info.value.rule == "V10"


def test_v10_non_finite_numbers() -> None:
    with pytest.raises(PlanError) as info:
        parse_plan(
            _plan(
                _plane(define={"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": float("inf")})
            )
        )
    assert info.value.rule == "V10"
    with pytest.raises(PlanError) as info:
        parse_plan(
            _plan(_plane(define={"how": "three_points", "points": [[0, 0, 0], [1, 0, 0], [0, float("nan"), 0]]}))
        )
    assert info.value.rule == "V10"


def test_face_ref_still_works_as_datum_base() -> None:
    op = _plane(define={"how": "offset", "base": FaceRef(from_op="b").model_dump(mode="json"), "distance_mm": 2.0})
    box = {"id": "b", "op": "box", "intent": "x", "size": [10.0, 10.0, 10.0]}
    assert len(parse_plan(_plan(box, op)).operations) == 2


# --- narration ---------------------------------------------------------------------


# --- review fixes: literal axis validation and FaceRef axes -------------------------------------------


@pytest.mark.parametrize("field", ["about", "axis"])
def test_a_zero_literal_axis_direction_says_so(field: str) -> None:
    if field == "about":
        define = {
            "how": "angle",
            "base": {"kind": "plane", "name": "XY"},
            "about": {"origin": [0, 0, 0], "direction": [0, 0, 0]},
            "angle_deg": 10,
        }
    else:
        define = {"how": "normal_to", "axis": {"origin": [0, 0, 0], "direction": [0, 0, 0]}, "at_mm": 3}
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(_plane(define=define)))
    assert "axis direction must be non-zero" in info.value.message
    assert "from_op" not in info.value.message  # not the confusing union error about AxisRef


def test_a_cylindrical_face_is_accepted_as_a_normal_to_axis_and_an_angle_axis() -> None:
    cylinder = {"kind": "face", "from_op": "base", "select": {"surface": "cylinder"}}
    plan = parse_plan(
        _plan(
            {
                "id": "s0",
                "op": "sketch",
                "intent": "i",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}],
            },
            {"id": "base", "op": "extrude", "intent": "i", "sketch": "s0", "depth_mm": 5},
            _plane("np", {"how": "normal_to", "axis": cylinder, "at_mm": 2}),
            _plane("an", {"how": "angle", "base": {"kind": "plane", "name": "XY"}, "about": cylinder, "angle_deg": 5}),
        )
    )
    assert plan.operations[2].define.axis == FaceRef(from_op="base", select={"surface": "cylinder"})
