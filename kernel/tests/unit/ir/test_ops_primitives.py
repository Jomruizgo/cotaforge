"""Tests for cotaforge.ir.ops_primitives: OperationBase, Placement and the body-level primitives."""

from typing import Any

import pytest
from pydantic import ValidationError

from cotaforge.contract import AxisName
from cotaforge.ir.ops_primitives import (
    BooleanIntersectOperation,
    BooleanSubtractOperation,
    BooleanUnionOperation,
    BoxOperation,
    CopyBodyOperation,
    CylinderOperation,
    OperationBase,
    Placement,
    RotateOperation,
    RotateSpec,
    TranslateOperation,
)
from cotaforge.ir.refs import BodyRef


def test_placement_defaults() -> None:
    p = Placement()
    assert p.origin == (0.0, 0.0, 0.0)
    assert p.axis == AxisName.Z
    assert p.angle_deg == 0.0


def test_operation_base_requires_nonempty_intent() -> None:
    with pytest.raises(ValidationError):
        BoxOperation(id="box1", intent="", size=(1.0, 1.0, 1.0))


def test_box_operation_defaults() -> None:
    op = BoxOperation(id="box1", intent="base block", size=(10.0, 20.0, 5.0))
    assert op.op == "box"
    assert op.placement == Placement()


def test_box_operation_rejects_nonpositive_size() -> None:
    with pytest.raises(ValidationError):
        BoxOperation(id="box1", intent="base block", size=(10.0, 0.0, 5.0))


def test_cylinder_operation_rejects_nonpositive_diameter() -> None:
    with pytest.raises(ValidationError):
        CylinderOperation(id="cyl1", intent="boss", diameter_mm=0.0, height_mm=10.0)


def test_cylinder_operation_defaults() -> None:
    op = CylinderOperation(id="cyl1", intent="boss", diameter_mm=10.0, height_mm=20.0)
    assert op.axis == AxisName.Z
    assert op.origin == (0.0, 0.0, 0.0)


def test_boolean_union_requires_at_least_one_tool() -> None:
    with pytest.raises(ValidationError):
        BooleanUnionOperation(id="u1", intent="merge", target=BodyRef(from_op="box1"), tools=())


def test_boolean_subtract_ok() -> None:
    op = BooleanSubtractOperation(
        id="s1", intent="cut", target=BodyRef(from_op="box1"), tools=(BodyRef(from_op="cyl1"),)
    )
    assert op.op == "boolean_subtract"
    assert op.keep_tools is False


def test_boolean_subtract_keep_tools_settable() -> None:
    op = BooleanSubtractOperation(
        id="s1", intent="cut", target=BodyRef(from_op="box1"), tools=(BodyRef(from_op="cyl1"),), keep_tools=True
    )
    assert op.keep_tools is True


def test_boolean_union_and_intersect_say_why_keep_tools_is_not_theirs() -> None:
    for kind in (BooleanUnionOperation, BooleanIntersectOperation):
        with pytest.raises(ValidationError, match="keep_tools is only for boolean_subtract.*copy_body"):
            kind(
                id="u1",
                intent="merge",
                target=BodyRef(from_op="box1"),
                tools=(BodyRef(from_op="cyl1"),),
                keep_tools=True,  # type: ignore[call-arg]
            )


def test_boolean_intersect_ok() -> None:
    op = BooleanIntersectOperation(
        id="i1", intent="keep overlap", target=BodyRef(from_op="box1"), tools=(BodyRef(from_op="cyl1"),)
    )
    assert op.op == "boolean_intersect"


def test_translate_operation() -> None:
    op = TranslateOperation(id="t1", intent="move", target=BodyRef(from_op="box1"), offset=(1.0, 2.0, 3.0))
    assert op.offset == (1.0, 2.0, 3.0)


def test_rotate_operation_defaults() -> None:
    op = RotateOperation(id="r1", intent="turn", target=BodyRef(from_op="box1"), axis=AxisName.Z, angle_deg=90.0)
    assert op.about == (0.0, 0.0, 0.0)


def test_copy_body_defaults_to_an_in_place_copy() -> None:
    op = CopyBodyOperation(id="c1", intent="twin", source=BodyRef(from_op="box1"))
    assert op.op == "copy_body"
    assert op.offset == (0.0, 0.0, 0.0)
    assert op.rotate is None


def test_copy_body_rotate_spec_mirrors_rotate_operation() -> None:
    op = CopyBodyOperation(
        id="c1",
        intent="twin",
        source=BodyRef(from_op="box1"),
        offset=(0.0, 100.0, 0.0),
        rotate=RotateSpec(axis=AxisName.Z, angle_deg=90.0),
    )
    assert op.rotate is not None
    assert op.rotate.about == (0.0, 0.0, 0.0)
    assert op.refs() == (BodyRef(from_op="box1"),)


def test_copy_body_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CopyBodyOperation(id="c1", intent="twin", source=BodyRef(from_op="box1"), target=BodyRef(from_op="x"))  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RotateSpec(axis=AxisName.Z, angle_deg=90.0, pivot=(0, 0, 0))  # type: ignore[call-arg]


def test_operation_base_refs_empty_by_default() -> None:
    op = BoxOperation(id="box1", intent="base block", size=(10.0, 20.0, 5.0))
    assert op.refs() == ()


def test_operation_base_refs_collects_body_refs() -> None:
    target = BodyRef(from_op="box1")
    tool = BodyRef(from_op="cyl1")
    op = BooleanUnionOperation(id="u1", intent="merge", target=target, tools=(tool,))
    refs = op.refs()
    assert target in refs
    assert tool in refs
    assert len(refs) == 2


def test_operation_base_is_frozen() -> None:
    op = BoxOperation(id="box1", intent="base block", size=(10.0, 20.0, 5.0))
    with pytest.raises(ValidationError):
        op.id = "other"  # type: ignore[misc]


def test_operation_base_op_field_present() -> None:
    assert issubclass(BoxOperation, OperationBase)


# --- unknown fields must raise, never be silently dropped ------------------------------


def test_box_operation_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        BoxOperation(id="box1", intent="base block", size=(10.0, 20.0, 5.0), sizee=(1.0, 1.0, 1.0))  # type: ignore[call-arg]


def test_placement_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Placement(orign=(1.0, 2.0, 3.0))  # type: ignore[call-arg]


NAN, INF = float("nan"), float("inf")

_BOX = {"id": "b", "op": "box", "intent": "block", "size": [10.0, 10.0, 10.0]}


def _concept(*operations: dict) -> dict:
    return {"id": "p", "kind": "part", "operations": list(operations)}


@pytest.mark.parametrize("bad", [NAN, INF])
@pytest.mark.parametrize(
    ("operations", "where"),
    [
        ([{**_BOX, "size": ["X", 10.0, 10.0]}], "b.size[0]"),
        ([{**_BOX, "placement": {"origin": [0.0, "X", 0.0]}}], "b.placement.origin[1]"),
        ([{**_BOX, "placement": {"angle_deg": "X"}}], "b.placement.angle_deg"),
        ([{"id": "c", "op": "cylinder", "intent": "boss", "diameter_mm": "X", "height_mm": 10.0}], "c.diameter_mm"),
        (
            [
                {
                    "id": "c",
                    "op": "cylinder",
                    "intent": "boss",
                    "diameter_mm": 5.0,
                    "height_mm": 10.0,
                    "origin": [0, 0, "X"],
                }
            ],
            "c.origin[2]",
        ),
        (
            [_BOX, {"id": "t", "op": "translate", "intent": "move", "target": {"from_op": "b"}, "offset": [0, "X", 0]}],
            "t.offset[1]",
        ),
    ],
)
def test_a_non_finite_number_in_any_operation_is_a_plan_error(operations: list[dict], where: str, bad: float) -> None:
    """nan and inf pass ``> 0`` checks (``inf > 0``) or slip through ``<= 0`` ones (``nan <= 0`` is False): they must
    never reach a CAD, where they would corrupt a shape or hang a recompute (V14)."""
    from cotaforge.ir.errors import PlanError
    from cotaforge.ir.plan import parse_plan

    assert parse_plan(_concept(*[_fill(op, 1.0) for op in operations]))  # the shape itself is valid
    with pytest.raises(PlanError) as refused:
        parse_plan(_concept(*[_fill(op, bad) for op in operations]))
    if where == "c.diameter_mm" and bad != bad:  # noqa: PLR0124 - nan: the field's own ``gt=0`` already says no
        assert refused.value.rule == "schema"
    else:
        assert refused.value.rule == "V14" and where in refused.value.message and "finite" in refused.value.message


def _fill(value: Any, number: float) -> Any:
    """``value`` with every marker ``"X"`` replaced by ``number``."""
    if isinstance(value, dict):
        return {k: _fill(v, number) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, number) for v in value]
    return number if value == "X" else value
