"""IR of the complete hole: counterbore, countersink, drill point and up_to_face, with FreeCAD's names and defaults,
and the messages for what cannot be drilled."""

import math

import pytest

from cotaforge.contract import DrillPoint, EndCondition
from cotaforge.contract.holetool import hole_outline, outline_volume
from cotaforge.ir.errors import PlanError
from cotaforge.ir.ops_features import CutOperation, HoleOperation
from cotaforge.ir.plan import parse_plan
from cotaforge.ir.refs import FaceRef

FACE = {"from_op": "base", "select": {"surface": "plane", "normal": [0, 0, 1]}}


def _hole(**fields: object) -> HoleOperation:
    body: dict = {
        "id": "h1",
        "op": "hole",
        "intent": "hole",
        "face": FACE,
        "position": [0, 0],
        "standard": "iso_clearance",
        "size": "M8",
        "end_condition": "through_all",
    }
    body.update(fields)
    return HoleOperation.model_validate(body)


def test_an_iso_clearance_with_a_counterbore_is_one_op() -> None:
    op = _hole(counterbore={"diameter_mm": 14, "depth_mm": 9})
    assert op.diameter_mm == 9.0  # from the thread table
    assert op.counterbore is not None and (op.counterbore.diameter_mm, op.counterbore.depth_mm) == (14, 9)
    assert op.countersink is None


def test_defaults_are_freecads() -> None:
    op = _hole(countersink={"diameter_mm": 12})
    assert op.countersink is not None and op.countersink.angle_deg == 90.0  # HoleCutCountersinkAngle
    assert op.drill_point is DrillPoint.angled and op.drill_point_angle_deg == 118.0  # DrillPointAngle


def test_up_to_face_goes_with_its_face() -> None:
    op = _hole(end_condition="up_to_face", up_to_face={"from_op": "base", "select": {"normal": [0, 0, -1]}})
    assert op.end_condition is EndCondition.up_to_face and isinstance(op.up_to_face, FaceRef)
    # the target face is a reference of the op like any other: the plan orders and resolves it
    assert any(ref is op.up_to_face for ref in op.refs())


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (
            {"counterbore": {"diameter_mm": 14, "depth_mm": 3}, "countersink": {"diameter_mm": 14}},
            "counterbore or a countersink, not both",
        ),
        ({"counterbore": {"diameter_mm": 8, "depth_mm": 3}}, "must be larger than the hole's diameter_mm"),
        ({"countersink": {"diameter_mm": 9}}, "must be larger than the hole's diameter_mm"),
        (
            {"end_condition": "blind", "depth_mm": 5, "counterbore": {"diameter_mm": 14, "depth_mm": 5}},
            "includes the counterbore",
        ),
        (
            {"end_condition": "blind", "depth_mm": 2, "countersink": {"diameter_mm": 20}},
            "countersink cone",
        ),
        ({"end_condition": "up_to_face"}, "goes with end_condition=up_to_face"),
        ({"up_to_face": FACE}, "goes with end_condition=up_to_face"),
        (
            {"end_condition": "up_to_face", "up_to_face": FACE, "depth_mm": 4},
            "takes no depth_mm",
        ),
        ({"counterbore": {"diameter_mm": 14, "depth_mm": 3, "angle_deg": 90}}, "Extra inputs are not permitted"),
        ({"countersink": {"diameter_mm": 14, "angle_deg": 180}}, "less than 180"),
        ({"drill_point": "conical"}, "flat"),
    ],
)
def test_what_cannot_be_drilled_says_why(fields: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _hole(**fields)


def test_a_cut_cannot_end_up_to_a_face() -> None:
    with pytest.raises(ValueError, match="up_to_face is for hole only"):
        CutOperation.model_validate(
            {"id": "c", "op": "cut", "intent": "i", "sketch": "s", "end_condition": "up_to_face", "depth_mm": 1}
        )


def test_a_plan_with_a_target_face_orders_it_after_the_op_that_makes_it() -> None:
    sketch = {
        "id": "s",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 60, "height": 40}],
    }
    plate = {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s", "depth_mm": 20}
    hole = {
        "id": "h1",
        "op": "hole",
        "intent": "hole",
        "face": FACE,
        "position": [0, 0],
        "standard": "simple",
        "diameter_mm": 5,
        "end_condition": "up_to_face",
        "up_to_face": {"from_op": "later", "select": {"normal": [0, 0, 1]}},
    }
    with pytest.raises(PlanError):
        parse_plan({"id": "p", "kind": "part", "operations": [sketch, plate, hole]})


# --- the half section both the verification and the adapter read ---------------------------------------------------


def test_the_half_section_volume_is_the_closed_form() -> None:
    plain = hole_outline(9.0, 19.0)
    assert outline_volume(plain) == pytest.approx(math.pi * 4.5**2 * 19.0)
    cbore = hole_outline(9.0, 19.0, counterbore=(14.0, 9.0))
    assert outline_volume(cbore) == pytest.approx(math.pi * (49.0 * 9.0 + 20.25 * 10.0))
    csink = hole_outline(5.5, 19.0, countersink=(10.4, 90.0))
    cone = 2.45
    expected = (
        math.pi * 2.75**2 * 19.0 + math.pi * cone / 3.0 * (5.2**2 + 5.2 * 2.75 + 2.75**2) - math.pi * 2.75**2 * cone
    )
    assert outline_volume(csink) == pytest.approx(expected)
    tip = 3.0 / math.tan(math.radians(59.0))
    assert outline_volume(hole_outline(6.0, 10.0, point_angle_deg=118.0)) == pytest.approx(
        math.pi * 9.0 * 10.0 + math.pi * 9.0 * tip / 3.0
    )
