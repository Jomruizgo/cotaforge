"""End-to-end ``copy_body``: Executor + Verifier driving the real FakeAdapter."""

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

BLOCK = 10.0 * 20.0 * 30.0  # the 10 x 20 x 30 mm block, mm3


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    block = {"id": "block", "op": "box", "intent": "block", "size": [10, 20, 30]}
    return {"id": "twins", "kind": "part", "operations": [block, *ops]}


def _copy(op_id: str = "twin", **extra: Any) -> dict[str, Any]:
    source = {"kind": "body", "from_op": "block"}
    return {"id": op_id, "op": "copy_body", "intent": "twin", "source": source, **extra}


def test_a_translated_copy_doubles_the_volume_and_passes_every_check() -> None:
    traces, _ = _run([_plan(_copy(offset=[0, 100, 0]))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert all(step.status == StepStatus.success for step in trace.steps)
    assert _asserted_ok(trace, "twin") == {
        "volume_delta",
        "copy_centroid",
        "copy_surface",
        "source_centroid",
        "copy_bbox",
    }
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(2 * BLOCK)


def test_an_oblique_copy_asserts_the_pose_and_only_reports_the_bbox() -> None:
    plan = _plan(_copy(offset=[0, 100, 0], rotate={"axis": "Z", "angle_deg": 30}))
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed
    step = trace.step("twin")
    assert step is not None
    kinds = {c.name: c.kind for c in step.last_level2()}
    assert kinds["copy_bbox"] is CheckKind.reported
    assert {"volume_delta", "copy_centroid", "copy_surface", "source_centroid"} <= _asserted_ok(trace, "twin")


def test_translating_the_copy_afterwards_moves_only_the_copy() -> None:
    move = {
        "id": "lift",
        "op": "translate",
        "intent": "lift the twin",
        "target": {"kind": "body", "from_op": "twin"},
        "offset": [0, 0, 50],
    }
    traces, adapter = _run([_plan(_copy(offset=[0, 100, 0]), move)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(2 * BLOCK)
    assert trace.final_bbox is not None
    assert trace.final_bbox.min == pytest.approx((0.0, 0.0, 0.0))
    assert trace.final_bbox.max == pytest.approx((10.0, 120.0, 80.0))


def test_the_original_and_a_half_overlapping_copy_union_with_the_analytic_overlap() -> None:
    union = {
        "id": "both",
        "op": "boolean_union",
        "intent": "fuse",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "twin"}],
    }
    traces, _ = _run([_plan(_copy(offset=[5, 0, 0]), union)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    both = trace.step("both")
    assert both is not None
    volume = next(c for c in both.last_level2() if c.name == "volume_delta")
    assert volume.kind is CheckKind.asserted and volume.ok is True
    assert volume.expected == pytest.approx(-BLOCK / 2)  # half of a block is shared with its twin
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(1.5 * BLOCK)


def test_faces_of_the_copy_can_be_referenced_by_later_operations() -> None:
    top_of_copy = {"kind": "face", "from_op": "twin", "select": {"normal": [0, 0, 1]}}
    sketch = {
        "id": "boss_sketch",
        "op": "sketch",
        "intent": "boss on the copy",
        "on": top_of_copy,
        "entities": [{"kind": "rect", "name": "boss", "center": [0, 0], "width": 4, "height": 4}],
    }
    boss = {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sketch", "depth_mm": 5}
    traces, _ = _run([_plan(_copy(offset=[0, 100, 0]), sketch, boss)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(2 * BLOCK + 4 * 4 * 5)  # only the copy grew


def test_a_copy_with_the_wrong_volume_fails_level2_and_is_undone() -> None:
    faults = {"faults": [{"operation_id": "twin", "kind": "wrong_volume", "volume_factor": 0.5}]}
    traces, _ = _run([_plan(_copy(offset=[0, 100, 0]))], _fake(faults))
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    step = trace.step("twin")
    assert step is not None and step.status == StepStatus.failed and len(step.attempts) == 1
    assert step.attempts[0].undone is True
    assert {c.name for c in step.last_level2() if c.ok is False} == {"volume_delta"}
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(BLOCK)


def test_undo_to_the_source_removes_the_copy_and_its_registration() -> None:
    plan = parse_plan(_plan(_copy(offset=[0, 100, 0])))
    adapter = _fake()
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig(), {})
    executor.start()
    assert executor.run_to_end().final_state == ExecutorState.completed
    assert executor.undo_to("block") == ("twin",)
    ctx = executor._ctx
    assert ctx is not None and "twin" not in ctx.body_by_op and "block" in ctx.body_by_op
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(BLOCK)


def test_a_boss_on_the_source_invalidates_its_tracked_shape_before_a_later_boolean() -> None:
    """A merged boss changes the block, so the analytic overlap of the block with its copy (taken after the boss)
    must not be asserted any more: the tracked box shape is stale. Real overlap = 8 x 20 x 30 of the blocks plus the
    2 x 4 x 5 where the two bosses meet = 4840 mm3, not the 4800 of two bare boxes."""
    top = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
    rect = {"kind": "rect", "name": "b", "center": [0, 0], "width": 4, "height": 4}
    sketch = {"id": "bs", "op": "sketch", "intent": "i", "on": top, "entities": [rect]}
    boss = {"id": "boss", "op": "extrude", "intent": "i", "sketch": "bs", "depth_mm": 5}
    union = {
        "id": "both",
        "op": "boolean_union",
        "intent": "fuse",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "twin"}],
    }
    traces, _ = _run([_plan(sketch, boss, _copy(offset=[2, 0, 0]), union)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    both = trace.step("both")
    assert both is not None
    volume = next(c for c in both.last_level2() if c.name == "volume_delta")
    assert volume.kind is CheckKind.reported
    assert volume.observed == pytest.approx(-4840.0)


def test_a_hole_outside_the_overlap_leaves_the_union_delta_asserted() -> None:
    """Block, its twin 8 mm along X (shared slab x in [8, 10] = 1200 mm3), then a through hole at x in [0, 4]: the
    hole never touches the slab, so the analytic overlap is still exact and the union delta is asserted."""
    hole = {
        "id": "bore",
        "op": "hole",
        "intent": "bore",
        "face": {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}},
        "position": [-3, 0],
        "standard": "simple",
        "diameter_mm": 4,
        "end_condition": "through_all",
    }
    union = {
        "id": "both",
        "op": "boolean_union",
        "intent": "fuse",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "twin"}],
    }
    traces, _ = _run([_plan(_copy(offset=[8, 0, 0]), hole, union)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    both = trace.step("both")
    assert both is not None
    volume = next(c for c in both.last_level2() if c.name == "volume_delta")
    assert volume.kind is CheckKind.asserted and volume.ok is True
    assert volume.expected == pytest.approx(-1200.0)
