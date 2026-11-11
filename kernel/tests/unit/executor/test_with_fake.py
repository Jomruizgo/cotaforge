"""End-to-end: Executor + Verifier driving the real FakeAdapter .

This is the only executor test that touches ``cotaforge.adapters.fake``; the import is lazy so the
module collects even when the fake is absent, and the tests skip in that case.
"""

import copy
import math
from typing import Any

import pytest

from cotaforge.contract import Adapter, ConnectRequest, DocumentHandle
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

HOLE_VOLUME = math.pi * 3.3**2 * 8.0
PLATE_VOLUME = 48000.0 - 4 * HOLE_VOLUME  # 46905.2178


def _fake(faults: dict[str, Any] | None = None) -> Adapter:
    fake_module = pytest.importorskip("cotaforge.adapters.fake.adapter")
    faults_module = pytest.importorskip("cotaforge.adapters.fake.faults")
    adapter = fake_module.FakeAdapter(faults=faults_module.FaultPlan.model_validate(faults or {"faults": []}))
    adapter.connect(ConnectRequest(operation_id="connect"))
    return adapter


def _run(
    plans: list[dict[str, Any]], adapter: Adapter, config: ExecutorConfig | None = None
) -> tuple[list[ExecutionTrace], Adapter]:
    documents: dict[str, DocumentHandle] = {}
    traces: list[ExecutionTrace] = []
    for plan_dict in plans:
        plan = parse_plan(plan_dict)
        ex = Executor(plan, adapter, Verifier(), config or ExecutorConfig(), documents)
        ex.start()
        traces.append(ex.run_to_end())
        if traces[-1].final_state != ExecutorState.completed:
            break
        documents[plan.id] = ex.document()
    return traces, adapter


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None, op_id
    return {c.name for c in step.last_level2() if c.kind == CheckKind.asserted and c.ok}


def test_plate_plan_is_green_on_the_fake(plate_plan: dict[str, Any]) -> None:
    traces, _ = _run([plate_plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert all(s.status == StepStatus.success and len(s.attempts) == 1 for s in trace.steps)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME, abs=1e-3)
    assert trace.final_bbox is not None
    assert trace.final_bbox.max == pytest.approx((100.0, 60.0, 8.0))
    assert {"hole_axis_direction", "hole_axis_position", "hole_radius", "cut_extent_along_n"} <= _asserted_ok(
        trace, "hole_1"
    )
    assert {"instance_count", "created_faces_count", "volume_delta"} <= _asserted_ok(trace, "holes")
    assert ExecutionTrace.model_validate(trace.model_dump(mode="json")) == trace


def test_fault_retry_rebuild_failed_then_raise_error(plate_plan: dict[str, Any]) -> None:
    faults = {
        "faults": [
            {"operation_id": "hole_1", "attempt": 1, "kind": "rebuild_failed"},
            {"operation_id": "holes", "attempt": 1, "kind": "raise_error"},
        ]
    }
    traces, adapter = _run([plate_plan], _fake(faults))
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None and len(hole.attempts) == 2 and hole.attempts[0].undone is True
    holes = trace.step("holes")
    assert holes is not None and len(holes.attempts) == 2 and holes.attempts[0].error is not None
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME, abs=1e-3)
    fired = [(op, attempt, kind.value) for op, attempt, kind in adapter.fired_faults()]  # type: ignore[attr-defined]
    assert fired == [("hole_1", 1, "rebuild_failed"), ("holes", 1, "raise_error")]


def test_fault_retry_with_repair_on_failure_false_is_unchanged_from_the_default(plate_plan: dict[str, Any]) -> None:
    """Regression: the exact same fault scenario as
    ``test_fault_retry_rebuild_failed_then_raise_error`` above, driven against the real ``FakeAdapter``,
    produces an identical outcome with ``repair_on_failure=False`` -- proving the failure path without repair
    is untouched -- and every ``diagnosis``/``repair_applied`` stays ``None``."""
    faults = {
        "faults": [
            {"operation_id": "hole_1", "attempt": 1, "kind": "rebuild_failed"},
            {"operation_id": "holes", "attempt": 1, "kind": "raise_error"},
        ]
    }
    traces, adapter = _run([plate_plan], _fake(faults), ExecutorConfig(repair_on_failure=False))
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None and len(hole.attempts) == 2 and hole.attempts[0].undone is True
    holes = trace.step("holes")
    assert holes is not None and len(holes.attempts) == 2 and holes.attempts[0].error is not None
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(PLATE_VOLUME, abs=1e-3)
    fired = [(op, attempt, kind.value) for op, attempt, kind in adapter.fired_faults()]  # type: ignore[attr-defined]
    assert fired == [("hole_1", 1, "rebuild_failed"), ("holes", 1, "raise_error")]
    for step in (hole, holes):
        assert all(a.diagnosis is None and a.repair_applied is None for a in step.attempts)


def test_ambiguous_face_is_a_plan_error(plate_plan: dict[str, Any]) -> None:
    plan = copy.deepcopy(plate_plan)
    plan["operations"][2]["face"]["select"] = {"surface": "plane"}
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    hole = trace.step("hole_1")
    assert hole is not None and hole.status == StepStatus.plan_error and hole.attempts == ()


def test_circle_boss_that_covers_the_host_face_is_a_plan_error() -> None:
    """The ``covers`` coverage class is only supported for a
    rectangular profile congruent with (or containing) a rectangular host face. A circle profile that
    covers a rectangular host face must fail as a ``plan_error`` in ``ExpectedComputer`` before the
    adapter is ever called -- parity between the fake and FreeCAD by construction (mirrored on real
    FreeCAD by ``tests/integration/freecad/test_boss_on_face.py``)."""
    corner_distance = math.hypot(10.0, 10.0)
    plan = {
        "id": "block_circle_cover",
        "kind": "part",
        "description": "20x20x8 block with a circle boss covering its top face",
        "operations": [
            {
                "id": "base_sketch",
                "op": "sketch",
                "intent": "Outline of the block on XY",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "outline", "center": [10, 10], "width": 20, "height": 20}],
            },
            {"id": "base", "op": "extrude", "intent": "Block thickness", "sketch": "base_sketch", "depth_mm": 8},
            {
                "id": "boss_sketch",
                "op": "sketch",
                "intent": "Circle covering the top face",
                "on": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
                "entities": [
                    {"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 2 * (corner_distance + 1.0)}
                ],
            },
            {
                "id": "boss",
                "op": "extrude",
                "intent": "Boss covering the top face",
                "sketch": "boss_sketch",
                "depth_mm": 5,
            },
        ],
    }
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    boss = trace.step("boss")
    assert boss is not None and boss.status == StepStatus.plan_error and boss.attempts == ()
    assert boss.plan_error is not None and "only a rectangular profile" in boss.plan_error


def test_wrong_volume_on_blind_hole_fails_level2_and_restores_the_document(plate_plan: dict[str, Any]) -> None:
    plan = copy.deepcopy(plate_plan)
    plan["operations"][2].update({"end_condition": "blind", "depth_mm": 8, "drill_point": "flat"})
    traces, _ = _run(
        [plan], _fake({"faults": [{"operation_id": "hole_1", "kind": "wrong_volume", "volume_factor": 0.5}]})
    )
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    hole = trace.step("hole_1")
    assert hole is not None and hole.status == StepStatus.failed and len(hole.attempts) == 1
    assert hole.attempts[0].undone is True
    failing = {c.name for c in hole.last_level2() if c.ok is False}
    assert failing == {"volume_delta"}
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(48000.0)


def test_degraded_session_stops_everything(plate_plan: dict[str, Any]) -> None:
    faults = {
        "faults": [{"operation_id": "hole_1", "kind": "canary_fail"}, {"operation_id": "hole_1", "kind": "raise_error"}]
    }
    traces, _ = _run([plate_plan], _fake(faults))
    trace = traces[0]
    assert trace.final_state == ExecutorState.degraded
    hole = trace.step("hole_1")
    assert hole is not None and hole.status == StepStatus.failed and len(hole.attempts) == 1
    assert trace.step("holes") is None
    assert trace.health[-1].result.healthy is False


# --- multi-plan session: block + pin + assembly ------------------------------------

BLOCK = {
    "id": "blk",
    "operations": [
        {"id": "box", "op": "box", "intent": "block", "size": [40, 40, 20]},
        {"id": "bore", "op": "cylinder", "intent": "bore", "diameter_mm": 6.5, "height_mm": 20, "origin": [20, 20, 0]},
        {
            "id": "sub",
            "op": "boolean_subtract",
            "intent": "bore the block",
            "target": {"kind": "body", "from_op": "box"},
            "tools": [{"kind": "body", "from_op": "bore"}],
        },
    ],
}
PIN = {
    "id": "pin",
    "operations": [{"id": "cyl", "op": "cylinder", "intent": "pin", "diameter_mm": 6, "height_mm": 40}],
}
ASSEMBLY = {
    "id": "asm",
    "kind": "assembly",
    "operations": [
        {"id": "ib", "op": "insert_component", "intent": "block", "part": "blk", "placement": {}},
        {"id": "fx", "op": "fix_component", "intent": "fix the block", "component": "ib"},
        {"id": "ip", "op": "insert_component", "intent": "pin", "part": "pin", "placement": {"origin": [100, 0, 0]}},
        {
            "id": "m1",
            "op": "mate_concentric",
            "intent": "pin in the bore",
            "a": {"kind": "face", "from_op": "ib", "select": {"surface": "cylinder"}},
            "b": {"kind": "face", "from_op": "ip", "select": {"surface": "cylinder"}},
        },
        {
            "id": "m2",
            "op": "mate_coincident",
            "intent": "pin end on the block top (the bore cap also faces +Z: select by area)",
            "a": {"kind": "face", "from_op": "ib", "select": {"normal": [0, 0, 1], "area_mm2": {"min": 1000}}},
            "b": {"kind": "face", "from_op": "ip", "select": {"normal": [0, 0, -1]}},
        },
        {"id": "chk", "op": "check_interference", "intent": "pin must not interfere"},
    ],
}
ASSEMBLY_VOLUME = 40 * 40 * 20 - math.pi * 3.25**2 * 20 + math.pi * 3.0**2 * 40


def test_pin_and_block_assembly_is_green() -> None:
    traces, _ = _run([BLOCK, PIN, ASSEMBLY], _fake())
    assert [t.final_state for t in traces] == [ExecutorState.completed] * 3
    asm = traces[2]
    assert {"interference_total", "assembly_volume"} <= _asserted_ok(asm, "chk")
    assert {"component_bbox"} <= _asserted_ok(asm, "m1")
    assert {"component_bbox"} <= _asserted_ok(asm, "m2")
    assert asm.final_mass is not None and asm.final_mass.volume_mm3 == pytest.approx(ASSEMBLY_VOLUME, abs=1e-3)


def test_check_interference_fault_engine_counter_matches_executor_attempts() -> None:
    """Regression for the gather_observation double-dispatch bug.

    Before the fix, ``gather_observation`` re-invoked ``adapter.check_interference`` to recover the
    ``InterferenceResult`` after the executor's own dispatch had already called it once, so a single
    executor attempt fired two internal adapter calls and desynced the fake adapter's per-operation
    fault-engine attempt counter. A fault scheduled for the op's 2nd fault-engine attempt then fired on
    that phantom re-invocation, raising an ``AdapterError`` outside of the executor's per-attempt
    try/except and crashing ``run_to_end`` with an uncaught exception. With the fix there is exactly one
    internal adapter call per executor attempt, so the fault-engine counter for "chk" never reaches 2 on
    a single successful attempt, the fault never fires, and the plan completes without raising.
    """
    faults = {"faults": [{"operation_id": "chk", "attempt": 2, "kind": "raise_error"}]}
    traces, adapter = _run([BLOCK, PIN, ASSEMBLY], _fake(faults))
    assert [t.final_state for t in traces] == [ExecutorState.completed] * 3
    chk = traces[2].step("chk")
    assert chk is not None and chk.status == StepStatus.success and len(chk.attempts) == 1
    assert adapter.fired_faults() == ()  # type: ignore[attr-defined]


def test_wrong_side_mate_is_recovered_by_the_flip_retry() -> None:
    traces, adapter = _run([BLOCK, PIN, ASSEMBLY], _fake({"faults": [{"operation_id": "m2", "kind": "wrong_side"}]}))
    assert traces[-1].final_state == ExecutorState.completed
    seat = traces[-1].step("m2")
    assert seat is not None and seat.status == StepStatus.success
    assert [a.flipped for a in seat.attempts] == [False, True]
    assert seat.attempts[0].undone is True
    assert [c.name for c in seat.attempts[0].level2 if c.ok is False] == ["component_bbox"]
    assert [op for op, _, _ in adapter.fired_faults()] == ["m2", "m2"]  # type: ignore[attr-defined]


def test_missing_part_document_is_a_plan_error() -> None:
    traces, _ = _run([PIN, ASSEMBLY], _fake())
    assert traces[-1].final_state == ExecutorState.failed
    step = traces[-1].step("ib")
    assert step is not None and step.status == StepStatus.plan_error
