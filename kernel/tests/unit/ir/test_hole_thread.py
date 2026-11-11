"""IR tests for tapped and clearance holes: the diameter is filled from the thread table, ``thread_depth_mm`` is
optional metadata, and V12 rejects what cannot be drilled as written."""

import pytest

from cotaforge.contract import EndCondition, HoleStandard
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import HoleOperation
from cotaforge.ir.plan import parse_plan
from cotaforge.ir.refs import FaceRef

BASE = [
    {
        "id": "s1",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 60, "height": 40}],
    },
    {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s1", "depth_mm": 20},
]


def _hole(**fields: object) -> dict:
    body: dict = {
        "id": "h1",
        "op": "hole",
        "intent": "tapped hole",
        "face": {"from_op": "base", "select": {"surface": "plane", "normal": [0, 0, 1]}},
        "position": [0, 0],
        "standard": "iso_tapped",
        "size": "M6",
        "end_condition": "blind",
        "depth_mm": 12,
    }
    body.update(fields)
    return {k: v for k, v in body.items() if v is not None}


def _plan(hole: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": [*BASE, hole]}


def _error(hole: dict) -> PlanError:
    with pytest.raises(PlanError) as caught:
        parse_plan(_plan(hole))
    return caught.value


def _hole_op(**fields: object) -> HoleOperation:
    plan = parse_plan(_plan(_hole(**fields)))
    op = plan.operations[-1]
    assert isinstance(op, HoleOperation)
    return op


# --- the diameter is derived ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("size", "drill"), [("M3", 2.5), ("M6", 5.0), ("M8", 6.8), ("M10", 8.5), ("M12", 10.2)])
def test_iso_tapped_without_a_diameter_gets_the_tap_drill(size: str, drill: float) -> None:
    assert _hole_op(size=size).diameter_mm == drill


@pytest.mark.parametrize(("size", "clearance"), [("M6", 6.6), ("M8", 9.0), ("M12", 13.5)])
def test_iso_clearance_without_a_diameter_gets_the_medium_clearance(size: str, clearance: float) -> None:
    assert _hole_op(standard="iso_clearance", size=size).diameter_mm == clearance


def test_iso_clearance_keeps_an_explicit_diameter() -> None:
    assert _hole_op(standard="iso_clearance", size="M6", diameter_mm=7.0).diameter_mm == 7.0


def test_iso_tapped_accepts_the_tap_drill_written_out() -> None:
    assert _hole_op(size="M6", diameter_mm=5.0).diameter_mm == 5.0


def test_simple_hole_still_requires_its_diameter() -> None:
    error = _error(_hole(standard="simple", size=None))
    assert error.rule == "schema" and "diameter_mm" in str(error)


def test_an_unknown_tapped_size_lists_the_supported_sizes_and_does_not_advise_a_diameter() -> None:
    """V12 refuses any tapped diameter that is not the tap drill, so advising one would send the author in a circle."""
    error = _error(_hole(size="M7"))
    assert error.rule == "schema"
    assert "M7" in str(error) and "M6" in str(error) and "supported" in str(error)
    assert "name diameter_mm" not in str(error)


def test_an_unknown_clearance_size_without_a_diameter_asks_for_one() -> None:
    error = _error(_hole(standard="iso_clearance", size="M7"))
    assert error.rule == "schema" and "M7" in str(error) and "name diameter_mm" in str(error)


@pytest.mark.parametrize("standard", ["iso_tapped", "iso_clearance"])
def test_an_iso_hole_with_neither_size_nor_diameter_says_the_size_is_missing(standard: str) -> None:
    error = _error(_hole(standard=standard, size=None))
    assert error.rule == "schema"
    assert "size is missing" in str(error) and "M6" in str(error)
    assert "Field required" not in str(error)


@pytest.mark.parametrize(
    ("size", "canonical", "drill"),
    [("M45x1.5", "M45x1.5", 43.5), ("M45x1,5", "M45x1.5", 43.5), ("M8x1", "M8x1", 7.0), ("M45", "M45", 40.5)],
)
def test_a_fine_or_large_thread_gets_its_tap_drill_and_is_stored_in_canonical_spelling(
    size: str, canonical: str, drill: float
) -> None:
    op = _hole_op(size=size, depth_mm=60)
    assert op.size == canonical and op.diameter_mm == drill


def test_a_fine_thread_written_out_with_its_tap_drill_is_accepted_and_normalised() -> None:
    op = _hole_op(size="M45x1,5", diameter_mm=43.5, depth_mm=60)
    assert op.size == "M45x1.5" and op.diameter_mm == 43.5


def test_v12_rejects_a_fine_thread_with_another_drill_and_a_pitch_the_series_does_not_have() -> None:
    error = _error(_hole(size="M45x1.5", diameter_mm=43.0, depth_mm=60))
    assert error.rule == "V12" and "tap drill for M45x1.5 is 43.5" in str(error)
    error = _error(_hole(size="M45x1.7", depth_mm=60))
    assert error.rule == "schema" and "M45x1.5" in str(error) and "ISO 261" in str(error)


def test_a_fine_thread_has_no_tabulated_clearance_and_the_error_says_to_name_it() -> None:
    error = _error(_hole(standard="iso_clearance", size="M45x1.5", depth_mm=60))
    assert error.rule == "schema" and "M45x1.5" in str(error) and "diameter_mm" in str(error)
    assert _hole_op(standard="iso_clearance", size="M45x1.5", diameter_mm=48.0, depth_mm=60).diameter_mm == 48.0


def test_direct_construction_fills_the_diameter_too() -> None:
    op = HoleOperation(
        id="h1",
        intent="tap",
        face=FaceRef(from_op="base"),
        position=(0.0, 0.0),
        standard=HoleStandard.iso_tapped,
        size="M8",
        end_condition=EndCondition.through_all,
    )
    assert op.diameter_mm == 6.8


# --- V12 --------------------------------------------------------------------------------------------------------


def test_v12_rejects_a_diameter_that_is_not_the_tap_drill() -> None:
    error = _error(_hole(size="M6", diameter_mm=6.0))
    assert error.rule == "V12" and error.op_id == "h1"
    assert "tap drill for M6 is 5" in str(error)
    assert "omit diameter_mm" in str(error)


def test_v12_rejects_an_iso_tapped_size_outside_the_table() -> None:
    error = _error(_hole(size="M7", diameter_mm=6.0))
    assert error.rule == "V12"
    assert "M7" in str(error) and "M6" in str(error)


def test_v12_accepts_an_iso_clearance_size_outside_the_table_with_a_diameter() -> None:
    assert _hole_op(standard="iso_clearance", size="M7", diameter_mm=7.5).diameter_mm == 7.5


def test_thread_depth_is_kept_and_must_not_exceed_the_hole_depth() -> None:
    assert _hole_op(thread_depth_mm=8).thread_depth_mm == 8
    assert _hole_op(thread_depth_mm=12).thread_depth_mm == 12  # equal is fine
    error = _error(_hole(thread_depth_mm=13))
    assert error.rule == "V12"
    assert "thread_depth_mm" in str(error) and "depth_mm" in str(error)


def test_thread_depth_on_a_through_all_hole_has_nothing_to_compare_against() -> None:
    op = _hole_op(end_condition="through_all", depth_mm=None, thread_depth_mm=10)
    assert op.thread_depth_mm == 10


def test_thread_depth_requires_iso_tapped() -> None:
    error = _error(_hole(standard="iso_clearance", thread_depth_mm=5))
    assert error.rule == "V12"
    assert "iso_tapped" in str(error)
    error = _error(_hole(standard="simple", size=None, diameter_mm=5.0, thread_depth_mm=5))
    assert error.rule == "V12"


def test_thread_depth_must_be_positive() -> None:
    error = _error(_hole(thread_depth_mm=0))
    assert error.rule == "schema" and "thread_depth_mm" in str(error)


def test_a_modelled_thread_is_not_a_field() -> None:
    error = _error(_hole(modelled=True))
    assert error.rule == "schema" and "modelled" in str(error)


def test_the_plan_json_schema_does_not_require_a_diameter_the_table_can_fill_in() -> None:
    from cotaforge.ir import plan_json_schema

    definitions = plan_json_schema()["$defs"]
    hole = definitions["HoleOperation"]
    assert "diameter_mm" not in hole["required"]
    assert "diameter_mm" in hole["properties"] and "thread_depth_mm" in hole["properties"]
    assert "standard" in hole["required"] and "end_condition" in hole["required"]
