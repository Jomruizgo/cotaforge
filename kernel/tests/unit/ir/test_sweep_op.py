"""IR tests for the ``sweep`` operation: schema, vocabulary tier, references (V2/V3), one closed profile (V6)
and the static path checks (V11)."""

import json
import math

import pytest
from pydantic import ValidationError

from cotaforge.ir import OP_NAMES, produces_feature
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import OP_CATEGORY, PolylinePath, SweepOperation
from cotaforge.ir.plan import Plan, parse_plan, plan_json_schema
from cotaforge.ir.refs import BodyRef

L_PATH = [[0, 0, 0], [0, 0, 100], [100, 0, 100]]


def _circle_sketch(op_id: str = "s1") -> dict:
    return {
        "id": op_id,
        "op": "sketch",
        "intent": "tube section",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}],
    }


def _sweep(op_id: str = "sw", sketch: str = "s1", path: dict | None = None, **extra: object) -> dict:
    return {
        "id": op_id,
        "op": "sweep",
        "intent": "bent tube",
        "sketch": sketch,
        "path": path if path is not None else {"points": L_PATH},
        **extra,
    }


def _plan(*operations: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": list(operations)}


def _rule_of(*operations: dict) -> PlanError:
    with pytest.raises(PlanError) as info:
        parse_plan(_plan(*operations))
    return info.value


# --- schema ------------------------------------------------------------------------------------------------


def test_polyline_path_defaults_to_mitred_corners() -> None:
    path = PolylinePath(points=((0, 0, 0), (0, 0, 10)))
    assert path.kind == "polyline"
    assert path.corner_radius_mm is None


def test_sweep_defaults_merge_and_forbids_unknown_fields() -> None:
    op = SweepOperation(id="sw", intent="tube", sketch="s1", path=PolylinePath(points=((0, 0, 0), (0, 0, 10))))
    assert op.op == "sweep"
    assert op.merge is True
    assert op.refs() == ()
    with pytest.raises(ValidationError):
        SweepOperation(id="sw", intent="tube", sketch="s1", path={"points": L_PATH}, twist_deg=10)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        PolylinePath(points=((0, 0, 0), (0, 0, 10)), radius=3)  # type: ignore[call-arg]


def test_path_needs_two_points_and_a_positive_radius() -> None:
    with pytest.raises(ValidationError):
        PolylinePath(points=((0, 0, 0),))
    with pytest.raises(ValidationError):
        PolylinePath(points=((0, 0, 0), (0, 0, 1)), corner_radius_mm=0)
    with pytest.raises(ValidationError):
        PolylinePath(points=((0, 0, 0), (0, 0, 1)), corner_radius_mm=-2)


def test_path_kind_is_a_discriminator_literal() -> None:
    with pytest.raises(ValidationError):
        PolylinePath(kind="spline", points=((0, 0, 0), (0, 0, 1)))  # type: ignore[arg-type]


def test_sweep_is_an_everyday_detail_only_op_that_produces_a_feature() -> None:
    assert "sweep" in OP_NAMES
    assert OP_CATEGORY["sweep"] == "everyday"
    assert produces_feature("sweep")


def test_sweep_round_trips_through_a_detail_plan_and_the_json_schema() -> None:
    plan = parse_plan(_plan(_circle_sketch(), _sweep(path={"points": L_PATH, "corner_radius_mm": 20}, merge=False)))
    assert isinstance(plan, Plan)
    op = plan.operations[1]
    assert isinstance(op, SweepOperation)
    assert op.path.corner_radius_mm == 20
    assert op.merge is False
    assert "SweepOperation" in json.dumps(plan_json_schema())


# --- references (V2/V3) ------------------------------------------------------------------------------------


def test_sweep_sketch_must_exist_earlier_and_be_a_sketch() -> None:
    assert _rule_of(_sweep(sketch="nope")).rule == "V2"
    other = {"id": "b", "op": "box", "intent": "block", "size": [1, 2, 3]}
    assert _rule_of(other, _sweep(sketch="b")).rule == "V3"


def test_a_sweep_is_a_body_producing_op_for_body_refs() -> None:
    plan = parse_plan(
        _plan(
            _circle_sketch(),
            _sweep(),
            {
                "id": "mv",
                "op": "translate",
                "intent": "move",
                "target": {"kind": "body", "from_op": "sw"},
                "offset": [0, 0, 5],
            },
        )
    )
    assert BodyRef(from_op="sw") in plan.operations[2].refs()


# --- V6: exactly one closed profile ------------------------------------------------------------------------


def test_sweep_needs_exactly_one_profile_and_says_why_a_ring_is_two() -> None:
    ring = {
        "id": "s1",
        "op": "sketch",
        "intent": "ring",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [
            {"kind": "circle", "name": "outer", "center": [0, 0], "diameter": 10},
            {"kind": "circle", "name": "inner", "center": [0, 0], "diameter": 6},
        ],
    }
    error = _rule_of(ring, _sweep())
    assert error.rule == "V6"
    assert "exactly ONE closed profile" in str(error)
    assert "ring" in str(error)


def test_sweep_over_a_sketch_without_a_closed_profile_is_a_v6_error() -> None:
    open_chain = {
        "id": "s1",
        "op": "sketch",
        "intent": "open",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "line", "name": "l", "start": [0, 0], "end": [5, 0]}],
    }
    assert _rule_of(open_chain, _sweep()).rule == "V6"


# --- V11: static path checks -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        ({"points": [[0, 0, 0], [0, 0, math.nan]]}, "finite"),
        ({"points": [[0, 0, 0], [math.inf, 0, 0]]}, "finite"),
        ({"points": [[0, 0, 0], [0, 0, 100], [0, 0, 100]]}, "coincide"),
        ({"points": [[0, 0, 0], [0, 0, 100], [0, 0, 40]]}, "reverses"),
        ({"points": [[0, 0, 0], [0, 0, 50], [0, 0, 100]]}, "drop the redundant point"),
        ({"points": L_PATH, "corner_radius_mm": math.inf}, "finite"),
    ],
)
def test_v11_rejects_paths_that_cannot_be_swept(path: dict, needle: str) -> None:
    error = _rule_of(_circle_sketch(), _sweep(path=path))
    assert error.rule == "V11"
    assert error.op_id == "sw"
    assert needle in str(error)


def test_v11_names_the_offending_point() -> None:
    error = _rule_of(_circle_sketch(), _sweep(path={"points": [[0, 0, 0], [0, 0, 100], [0, 0, 100]]}))
    assert "points 1 and 2" in str(error)


def test_v11_uses_the_turn_angle_not_the_angle_at_the_first_point() -> None:
    # a 1000 mm segment then a 0.001 mm one at ~30 deg: collinear when seen from the far end, a real corner at
    # the vertex -- the path is legal here (analyse_path in verification uses the same turn-angle rule)
    points = [[0, 0, 0], [1000, 0, 0], [1000 + 0.001 * math.cos(0.5), 0.001 * math.sin(0.5), 0]]
    parse_plan(_plan(_circle_sketch(), _sweep(path={"points": points})))


def test_a_two_point_and_a_bent_path_pass_v11() -> None:
    parse_plan(_plan(_circle_sketch(), _sweep(path={"points": [[0, 0, 0], [0, 0, 100]]})))
    parse_plan(_plan(_circle_sketch(), _sweep(path={"points": L_PATH, "corner_radius_mm": 20})))
