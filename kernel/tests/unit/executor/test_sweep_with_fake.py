"""End-to-end ``sweep``: Executor + Verifier driving the real FakeAdapter."""

import math
from typing import Any

import pytest

from cotaforge.contract import MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier
from tests.unit.executor.test_with_fake import _asserted_ok, _fake, _run

AREA = 25.0 * math.pi
L_PATH = [[0, 0, 0], [0, 0, 100], [100, 0, 100]]


def _plan(*ops: dict[str, Any], section: dict | None = None) -> dict[str, Any]:
    entity = section or {"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}
    sketch = {
        "id": "sec",
        "op": "sketch",
        "intent": "section",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [entity],
    }
    return {"id": "tube", "kind": "part", "operations": [sketch, *ops]}


def _sweep(op_id: str = "tube", path: dict | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "sweep",
        "intent": "bent tube",
        "sketch": "sec",
        "path": path or {"points": L_PATH},
        **extra,
    }


def test_a_mitred_l_tube_passes_every_check() -> None:
    traces, _ = _run([_plan(_sweep())], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert _asserted_ok(trace, "tube") == {"volume_delta", "new_faces_count", "bbox_contains"}
    step = trace.step("tube")
    assert step is not None
    assert {c.name: c.kind for c in step.last_level2()}["bbox_after"] is CheckKind.reported
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(AREA * 200.0)


def test_a_filleted_tube_passes_with_five_faces() -> None:
    traces, _ = _run([_plan(_sweep(path={"points": L_PATH, "corner_radius_mm": 20}))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert {"volume_delta", "new_faces_count", "bbox_contains"} <= _asserted_ok(trace, "tube")
    step = trace.step("tube")
    assert step is not None
    faces = next(c for c in step.last_level2() if c.name == "new_faces_count")
    assert faces.expected == 5 and faces.observed == 5
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(AREA * (200.0 - 40.0 + 10.0 * math.pi))


def test_a_straight_tube_asserts_its_exact_box() -> None:
    traces, _ = _run([_plan(_sweep(path={"points": [[0, 0, 0], [0, 0, 100]]}))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed
    assert _asserted_ok(trace, "tube") == {"volume_delta", "new_faces_count", "bbox_after"}
    assert trace.final_bbox is not None
    assert trace.final_bbox.max == pytest.approx((5.0, 5.0, 100.0))


def test_a_bent_rectangular_section_has_no_face_count_check_but_a_straight_one_does() -> None:
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 10, "height": 4}
    traces, _ = _run([_plan(_sweep(), section=rect)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    step = trace.step("tube")
    assert step is not None
    assert "new_faces_count" not in {c.name for c in step.last_level2()}  # merges across bends on real kernels
    assert "volume_delta" in _asserted_ok(trace, "tube")
    straight = _sweep(path={"points": [[0, 0, 0], [0, 0, 100]]})
    traces, _ = _run([_plan(straight, section=rect)], _fake())
    assert "new_faces_count" in _asserted_ok(traces[0], "tube")  # 2 caps + 4 sides = 6


def test_a_bad_placement_is_a_plan_error_on_path_before_the_adapter_runs() -> None:
    off_centre = {"kind": "circle", "name": "c", "center": [10, 0], "diameter": 10}
    traces, adapter = _run([_plan(_sweep(), section=off_centre)], _fake())
    trace = traces[0]
    step = trace.step("tube")
    assert step is not None and step.status == StepStatus.plan_error
    assert step.plan_error is not None and "path" in str(step.plan_error) and "centroid" in str(step.plan_error)
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == 0.0


def test_a_sweep_with_the_wrong_volume_fails_level2_and_is_undone() -> None:
    faults = {"faults": [{"operation_id": "tube", "kind": "wrong_volume", "volume_factor": 0.5}]}
    traces, adapter = _run([_plan(_sweep())], _fake(faults))
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    step = trace.step("tube")
    assert step is not None and step.status == StepStatus.failed
    assert {c.name for c in step.last_level2() if c.ok is False} == {"volume_delta"}
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == 0.0


def test_a_sweep_can_be_moved_and_copied_by_later_operations() -> None:
    body = {"kind": "body", "from_op": "tube"}
    copy = {"id": "twin", "op": "copy_body", "intent": "twin", "source": body, "offset": [0, 100, 0]}
    traces, _ = _run([_plan(_sweep(), copy)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(2 * AREA * 200.0)


def test_undo_to_the_sketch_removes_the_sweep_and_its_registration() -> None:
    plan = parse_plan(_plan(_sweep()))
    adapter = _fake()
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig(), {})
    executor.start()
    assert executor.run_to_end().final_state == ExecutorState.completed
    assert executor.undo_to("sec") == ("tube",)
    ctx = executor._ctx
    assert ctx is not None and "tube" not in ctx.body_by_op and "tube" not in ctx.feature_by_op
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == 0.0
