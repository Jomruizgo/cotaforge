"""End-to-end ``shell``: Executor + Verifier driving the real FakeAdapter."""

import math
from typing import Any

import pytest

from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.verification.results import CheckKind
from tests.unit.executor.test_with_fake import _asserted_ok, _fake, _run

TOP = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
BOTTOM = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, -1]}}
XMAX = {"kind": "face", "from_op": "block", "select": {"normal": [1, 0, 0]}}


def _plan(*ops: dict[str, Any], block: dict[str, Any] | None = None) -> dict[str, Any]:
    base = block or {"id": "block", "op": "box", "intent": "block", "size": [40, 30, 20]}
    return {"id": "cap", "kind": "part", "operations": [base, *ops]}


def _shell(faces: list[dict], op_id: str = "hollow", **extra: Any) -> dict[str, Any]:
    return {"id": op_id, "op": "shell", "intent": "hollow it", "faces": faces, "thickness_mm": 2, **extra}


def _failure(trace: Any, op_id: str) -> str:
    step = trace.step(op_id)
    assert step is not None
    return step.plan_error or " | ".join(a.error.message for a in step.attempts if a.error)


def test_a_box_hollowed_through_its_top_passes_every_check() -> None:
    traces, _ = _run([_plan(_shell([TOP]))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert _asserted_ok(trace, "hollow") == {"volume_sign", "volume_delta", "bbox_within"}
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(7152.0)


def test_two_opposite_faces_pass_too() -> None:
    traces, _ = _run([_plan(_shell([TOP, BOTTOM]))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(5280.0)


def test_a_cylinder_cup_passes() -> None:
    block = {"id": "block", "op": "cylinder", "intent": "slug", "diameter_mm": 40, "height_mm": 30}
    traces, _ = _run([_plan(_shell([TOP]), block=block)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert {"volume_sign", "volume_delta"} <= _asserted_ok(trace, "hollow")
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(math.pi * (400 * 30 - 18**2 * 28))


def test_a_wall_too_thick_for_the_box_is_a_plan_error_before_the_adapter_is_called() -> None:
    traces, adapter = _run([_plan(_shell([TOP], thickness_mm=15))], _fake())
    trace = traces[0]
    step = trace.step("hollow")
    assert step is not None and step.status is StepStatus.plan_error
    assert "leaves no cavity along Y" in _failure(trace, "hollow")
    assert not step.attempts  # never reached the adapter
    assert trace.final_state == ExecutorState.failed


def test_adjacent_removed_faces_surface_the_fakes_not_supported_message() -> None:
    traces, _ = _run([_plan(_shell([TOP, XMAX]))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    assert "adjacent" in _failure(trace, "hollow")


def test_an_outward_shell_is_refused_by_the_fake_not_approximated() -> None:
    traces, _ = _run([_plan(_shell([TOP], direction="outward"))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    assert "outward" in _failure(trace, "hollow")


def test_a_shell_forgets_the_box_so_a_later_boolean_is_reported_not_asserted() -> None:
    """The cavity changes the overlap with any tool: after a shell the primitive overlap is no longer analytic."""
    tool = {"id": "tool", "op": "box", "intent": "cutter", "size": [10, 10, 10]}
    sub = {
        "id": "cut",
        "op": "boolean_subtract",
        "intent": "notch",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "tool"}],
    }
    traces, _ = _run([_plan(_shell([TOP]), tool, sub)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    step = trace.step("cut")
    assert step is not None
    delta = next(c for c in step.last_level2() if c.name == "volume_delta")
    assert delta.kind is CheckKind.reported
