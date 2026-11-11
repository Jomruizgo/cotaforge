"""Sheet metal on the fake adapter, through the executor and the verifier: the capability present and absent, the
measured facts of each op, the flat pattern's size against the closed form, and a flat size that lies being caught."""

import math
from typing import Any

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    ConnectRequest,
    ErrorCode,
    FlatPattern,
    MassRequest,
    OperationResult,
    UnfoldRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification import formulas
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

SHEET_TOOLS = {"base_flange", "edge_flange", "unfold"}
SKETCH = {
    "id": "s1",
    "op": "sketch",
    "intent": "outline",
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "o", "center": [50, 30], "width": 100, "height": 60}],
}
BASE = {"id": "b1", "op": "base_flange", "intent": "sheet", "sketch": "s1", "thickness_mm": 2}
TOP = {"kind": "face", "from_op": "b1", "select": {"normal": [0, 0, 1], "largest": True}}


def side(normal: list[float]) -> dict[str, Any]:
    return {"kind": "face", "from_op": "b1", "select": {"normal": normal}}


def flange(op_id: str, towards: list[float], **extra: Any) -> dict[str, Any]:
    body = {
        "id": op_id,
        "op": "edge_flange",
        "intent": "lip",
        "edge": {"kind": "edge", "between": [TOP, side(towards)]},
        "length_mm": 20,
        "angle_deg": 90,
        "bend_radius_mm": 3,
    }
    return {**body, **extra}


def unfold(k: float = 0.4, op_id: str = "u1") -> dict[str, Any]:
    face = {"kind": "face", "from_op": "b1", "select": {"normal": [0, 0, -1]}}
    return {"id": op_id, "op": "unfold", "intent": "flat", "face": face, "k_factor": k}


def run(adapter: FakeAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    adapter.connect(ConnectRequest(operation_id="connect"))
    executor = Executor(
        parse_plan({"id": "sheet", "kind": "part", "operations": list(operations)}),
        adapter,
        Verifier(),
        ExecutorConfig(),
    )
    executor.start()
    return executor, executor.run_to_end()


def result_of(trace: ExecutionTrace, op_id: str) -> OperationResult:
    step = trace.step(op_id)
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    return step.attempts[-1].result


def asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None
    return {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok}


# --- the capability -------------------------------------------------------------------------------------------------


def test_the_fake_has_the_sheet_metal_tools_and_a_switch_to_take_them_away() -> None:
    assert SHEET_TOOLS <= FakeAdapter().capabilities().tools
    assert FakeAdapter().unavailable_tools() == {}
    absent = FakeAdapter(sheet_metal=False)
    assert not SHEET_TOOLS & absent.capabilities().tools
    assert set(absent.unavailable_tools()) == SHEET_TOOLS


def test_a_call_that_arrives_without_the_capability_says_why_instead_of_building() -> None:
    adapter = FakeAdapter(sheet_metal=False)
    executor, trace = run(adapter, SKETCH, BASE)
    step = trace.step("b1")
    assert step is not None and step.status is StepStatus.failed
    error = step.attempts[-1].error
    assert error is not None and error.code is ErrorCode.not_supported
    assert "base_flange" in error.message and "switched off" in error.message
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == 0.0


# --- the ops and their measured facts -------------------------------------------------------------------------------


def test_a_base_flange_is_area_times_thickness_with_a_box_one_thickness_deep() -> None:
    _, trace = run(FakeAdapter(), SKETCH, BASE)
    assert trace.final_state is ExecutorState.completed
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(100 * 60 * 2)
    assert trace.final_bbox is not None
    assert trace.final_bbox.max == pytest.approx((100, 60, 2)) and trace.final_bbox.min == pytest.approx((0, 0, 0))
    assert {"volume_delta", "bbox_after"} <= asserted_ok(trace, "b1")


def test_a_flange_adds_the_closed_form_volume_and_never_takes_reach_away() -> None:
    _, trace = run(FakeAdapter(), SKETCH, BASE, flange("f1", [0, -1, 0]))
    assert trace.final_state is ExecutorState.completed
    # inside the footprint, outer sharp: width x t x (bend + (20 - setback) - (r + t)) = 100 x 2 x (7.854 + 15 - 5)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(12000 + 100 * 2 * (math.pi / 2 * 4 + 15 - 5))
    assert {"volume_delta", "bbox_contains"} <= asserted_ok(trace, "f1")


def test_a_flat_pattern_reports_its_size_and_leaves_the_solid_alone() -> None:
    adapter = FakeAdapter()
    _, trace = run(adapter, SKETCH, BASE, flange("f1", [0, -1, 0]), unfold())
    assert trace.final_state is ExecutorState.completed
    flat = result_of(trace, "u1").flat_pattern
    assert flat is not None and flat.valid and flat.thickness_mm == pytest.approx(2)
    along = 60 + formulas.edge_flange_flat_gain(90, 2, 3, 0.4, 20, material_inside=True, from_outer_sharp=True)
    assert flat.size_mm == pytest.approx((100, along))
    assert flat.area_mm2 == pytest.approx(100 * along) and flat.volume_mm3 == pytest.approx(100 * along * 2)
    assert asserted_ok(trace, "u1") >= {"flat_length_mm", "flat_width_mm", "flat_thickness_mm", "flat_pattern_valid"}
    before, after = trace.step("f1"), trace.step("u1")
    assert before is not None and after is not None
    assert before.mass_after is not None and after.mass_after is not None
    assert after.mass_after.volume_mm3 == pytest.approx(before.mass_after.volume_mm3)  # the solid did not change


def test_flanges_on_opposite_sides_both_add_to_the_flat_length() -> None:
    _, trace = run(FakeAdapter(), SKETCH, BASE, flange("f1", [0, -1, 0]), flange("f2", [0, 1, 0]), unfold())
    assert trace.final_state is ExecutorState.completed
    gain = formulas.edge_flange_flat_gain(90, 2, 3, 0.4, 20, material_inside=True, from_outer_sharp=True)
    flat = result_of(trace, "u1").flat_pattern
    assert flat is not None and flat.size_mm == pytest.approx((100, 60 + 2 * gain))


def test_undoing_the_unfold_and_the_flange_gives_the_bare_sheet_back() -> None:
    adapter = FakeAdapter()
    executor, trace = run(adapter, SKETCH, BASE, flange("f1", [0, -1, 0]), unfold())
    assert trace.final_state is ExecutorState.completed
    assert executor.undo_to("b1") == ("u1", "f1")
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(12000)


def test_an_impossible_flange_is_refused_before_the_adapter_is_called() -> None:
    adapter = FakeAdapter()
    _, trace = run(adapter, SKETCH, BASE, flange("f1", [0, -1, 0], length_mm=4))  # shorter than the bend
    step = trace.step("f1")
    assert step is not None and step.status is StepStatus.plan_error
    assert "length_from=leg" in str(step.plan_error)
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(12000)


def test_a_bend_longer_than_the_sheet_is_refused() -> None:
    _, trace = run(FakeAdapter(), SKETCH, BASE, flange("f1", [0, -1, 0], bend_radius_mm=70, length_from="leg"))
    step = trace.step("f1")
    assert step is not None and step.status is StepStatus.plan_error
    assert "material_outside" in str(step.plan_error)


# --- a flat size that lies is caught --------------------------------------------------------------------------------


class LyingFake(FakeAdapter):
    """Reports a flat pattern one millimetre longer than the sheet really unfolds to."""

    def unfold(self, request: UnfoldRequest) -> OperationResult:
        honest = super().unfold(request)
        assert honest.flat_pattern is not None
        lie = honest.flat_pattern.model_copy(update={"size_mm": (honest.flat_pattern.size_mm[0] + 1.0, 60.0)})
        return honest.model_copy(update={"flat_pattern": FlatPattern.model_validate(lie.model_dump())})


def test_the_verifier_refuses_a_flat_pattern_that_disagrees_with_the_closed_form() -> None:
    adapter = LyingFake()
    _, trace = run(adapter, SKETCH, BASE, flange("f1", [0, -1, 0]), unfold())
    step = trace.step("u1")
    assert step is not None and step.status is StepStatus.failed
    failed = {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok is False}
    assert "flat_length_mm" in failed


def test_the_fake_says_what_it_cannot_model_instead_of_approximating() -> None:
    hole = {"kind": "circle", "name": "h", "center": [50, 30], "diameter": 8}
    sketch_with_hole = {**SKETCH, "entities": [*SKETCH["entities"], hole]}
    _, trace = run(FakeAdapter(), sketch_with_hole, BASE)
    step = trace.step("b1")
    assert step is not None and step.status is StepStatus.failed
    error = step.attempts[-1].error
    assert error is not None and error.code is ErrorCode.not_supported and "without holes" in error.message
