"""Integration test: the shared "plate with 4 holes" fixture parses as a valid plan."""

from cotaforge.ir.ops_features import ExtrudeOperation, HoleOperation, LinearPatternOperation, SketchOperation
from cotaforge.ir.plan import Plan, parse_plan

from .fixtures import plate_plan_dict


def test_plate_plan_parses() -> None:
    plan = parse_plan(plate_plan_dict())
    assert isinstance(plan, Plan)
    assert plan.id == "plate_4_holes"
    assert len(plan.operations) == 4


def test_plate_plan_operation_types_in_order() -> None:
    plan = parse_plan(plate_plan_dict())
    sketch, extrude, hole, pattern = plan.operations
    assert isinstance(sketch, SketchOperation)
    assert isinstance(extrude, ExtrudeOperation)
    assert isinstance(hole, HoleOperation)
    assert isinstance(pattern, LinearPatternOperation)


def test_plate_plan_hole_is_iso_clearance_m6() -> None:
    plan = parse_plan(plate_plan_dict())
    hole = plan.operations[2]
    assert hole.standard == "iso_clearance"
    assert hole.size == "M6"
    assert hole.diameter_mm == 6.6


def test_plate_plan_pattern_is_2x2() -> None:
    plan = parse_plan(plate_plan_dict())
    pattern = plan.operations[3]
    assert pattern.count == 2
    assert pattern.count2 == 2
    assert pattern.spacing_mm == 80.0
    assert pattern.spacing2_mm == 40.0


def test_plate_plan_round_trips_through_yaml_loader() -> None:
    import yaml

    from cotaforge.ir.loader import load_plan

    plan_from_dict = parse_plan(plate_plan_dict())
    plan_from_yaml = load_plan(yaml.safe_dump(plate_plan_dict()))
    assert plan_from_yaml == plan_from_dict
