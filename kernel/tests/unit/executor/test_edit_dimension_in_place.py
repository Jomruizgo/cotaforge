"""A dimension of a sketch or of a PartDesign-style feature is changed IN PLACE (``Executor.edit_in_place``): the steps
that come after keep their ids and none of them runs again. Over the fake adapter, whose edit has the same policy as the
FreeCAD worker's (``contract.dimensions``)."""

import copy
from typing import Any

import pytest

from cotaforge.adapters.fake.adapter import FakeAdapter
from cotaforge.contract import ConnectRequest, MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier

# a 100 x 60 x 8 plate with its corner at the origin, a D6 through hole and a pocket cut from the top
OPS: list[dict[str, Any]] = [
    {
        "id": "s1",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
        "named_dimensions": [{"name": "W", "entity": "r", "attribute": "width"}],
    },
    {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s1", "depth_mm": 8},
    {
        "id": "h1",
        "op": "hole",
        "intent": "hole",
        "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
        "position": [-30, -20],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
    },
]


def plan(**changes: dict[str, Any]) -> Any:
    ops = copy.deepcopy(OPS)
    for op in ops:
        op.update(changes.get(op["id"], {}))
    return parse_plan({"id": "p", "kind": "part", "operations": ops})


def run() -> Executor:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    executor = Executor(plan(), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed
    return executor


def volume(executor: Executor) -> float:
    return executor.adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3


def fresh_volume(**changes: dict[str, Any]) -> float:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    executor = Executor(plan(**changes), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    return volume(executor)


WIDER = {"entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 120, "height": 60}]}


def test_a_sketch_dimension_is_set_in_place_and_nothing_runs_again() -> None:
    executor = run()
    steps = executor.trace().steps

    record = executor.edit_in_place(plan(s1=WIDER), ["s1"], by="agent", reason="wider plate")

    assert record.status == "applied", record.failure
    assert record.changes == {"s1": ("entities",)}
    assert record.recomputed_ops == ("base", "h1")  # the features that consume the sketch and what follows
    assert executor.trace().steps == steps and executor.trace().reverts == ()
    assert volume(executor) == pytest.approx(fresh_volume(s1=WIDER))
    assert executor.plan.operations[0].entities[0].width == 120  # type: ignore[union-attr]


def test_the_edited_sketch_is_the_one_a_later_edit_starts_from() -> None:
    executor = run()
    first = executor.edit_in_place(plan(s1=WIDER), ["s1"], by="agent", reason="wider")
    assert first.status == "applied"
    deeper = executor.edit_in_place(plan(s1=WIDER, base={"depth_mm": 12}), ["base"], by="agent", reason="thicker")
    assert deeper.status == "applied", deeper.failure
    assert volume(executor) == pytest.approx(fresh_volume(s1=WIDER, base={"depth_mm": 12}))


def test_an_extrude_depth_is_set_in_place() -> None:
    executor = run()
    record = executor.edit_in_place(plan(base={"depth_mm": 12}), ["base"], by="agent", reason="thicker")
    assert record.status == "applied", record.failure
    assert record.recomputed_ops == ("base", "h1")
    assert volume(executor) == pytest.approx(fresh_volume(base={"depth_mm": 12}))


def test_a_hole_diameter_is_set_in_place() -> None:
    executor = run()
    record = executor.edit_in_place(plan(h1={"diameter_mm": 10}), ["h1"], by="agent", reason="bigger")
    assert record.status == "applied", record.failure
    assert volume(executor) == pytest.approx(fresh_volume(h1={"diameter_mm": 10}))


def test_a_sketch_with_another_number_of_entities_is_refused_with_the_reason() -> None:
    executor = run()
    two = {
        "entities": [
            *OPS[0]["entities"],
            {"kind": "circle", "name": "c", "center": [20, 20], "diameter": 5},
        ]
    }
    record = executor.edit_in_place(plan(s1=two), ["s1"], by="agent", reason="more")
    assert record.status == "refused"
    assert "other entities" in (record.failure or "")
    assert executor.plan.operations[0].entities == plan().operations[0].entities  # type: ignore[union-attr]


def test_what_is_not_a_dimension_of_a_feature_is_refused() -> None:
    executor = run()
    record = executor.edit_in_place(plan(base={"direction": "negative"}), ["base"], by="agent", reason="flip")
    assert record.status == "refused"
    assert "direction" in (record.failure or "") and "not a dimension" in (record.failure or "")


def test_a_hole_that_changes_its_end_condition_is_refused() -> None:
    executor = run()
    record = executor.edit_in_place(
        plan(h1={"end_condition": "blind", "depth_mm": 3}), ["h1"], by="agent", reason="blind"
    )
    assert record.status == "refused"


def test_a_value_that_breaks_the_part_is_rolled_back_and_says_why() -> None:
    executor = run()
    before = volume(executor)
    # a hole at (-30, -20) from the centre falls off a plate 30 mm wide
    record = executor.edit_in_place(
        plan(s1={"entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 30, "height": 60}]}),
        ["s1"],
        by="agent",
        reason="narrow",
    )
    assert record.status == "rolled_back"
    assert record.failure
    assert volume(executor) == pytest.approx(before)
    assert executor.plan.operations[0].entities[0].width == 100  # type: ignore[union-attr]


def test_a_hole_position_is_set_in_place_and_nothing_runs_again() -> None:
    executor = run()
    steps = executor.trace().steps
    record = executor.edit_in_place(plan(h1={"position": [-20, 10]}), ["h1"], by="agent", reason="moved hole")
    assert record.status == "applied", record.failure
    assert record.changes == {"h1": ("position",)}
    assert executor.trace().steps == steps and executor.trace().reverts == ()
    assert volume(executor) == pytest.approx(fresh_volume(h1={"position": [-20, 10]}))
    assert executor.plan.operations[2].position == (-20, 10)  # type: ignore[union-attr]


def test_a_hole_moved_off_the_plate_is_not_applied_and_the_plan_keeps_its_position() -> None:
    executor = run()
    before = volume(executor)
    record = executor.edit_in_place(plan(h1={"position": [-300, 10]}), ["h1"], by="agent", reason="off the plate")
    assert record.status != "applied"
    assert executor.plan.operations[2].position == (-30, -20)  # type: ignore[union-attr]
    assert volume(executor) == pytest.approx(before)
