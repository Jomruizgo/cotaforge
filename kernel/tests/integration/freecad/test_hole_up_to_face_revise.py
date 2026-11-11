"""``up_to_face`` is emulated (PartDesign::Hole has no such end): the hole is drilled blind to the distance measured
when the step runs. The target face stays in the step, so editing the part so that the face moves and running the
step again measures the depth again; the step result says what it measured."""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

PLAN: list[dict[str, Any]] = [
    {
        "id": "plate_sketch",
        "op": "sketch",
        "intent": "plate",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 40, "height": 40}],
    },
    {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 10},
    {
        "id": "boss_sketch",
        "op": "sketch",
        "intent": "boss",
        "on": {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}},
        "entities": [{"kind": "rect", "name": "b", "center": [0, 0], "width": 20, "height": 20}],
    },
    {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sketch", "depth_mm": 5},
    {
        "id": "drill",
        "op": "hole",
        "intent": "from below up to the top of the boss",
        "face": {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, -1]}},
        "position": [0, 0],
        "standard": "simple",
        "diameter_mm": 4,
        "end_condition": "up_to_face",
        "up_to_face": {"kind": "face", "from_op": "boss", "select": {"normal": [0, 0, 1], "role": "created"}},
    },
]


def _volume(adapter: FreeCADAdapter) -> float:
    return adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3


def _warnings(trace: ExecutionTrace) -> list[str]:
    step = next(s for s in reversed(trace.active_steps()) if s.op_id == "drill")
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    return list(step.attempts[-1].result.warnings)


def test_moving_the_target_face_and_running_the_hole_again_measures_the_depth_again(adapter: FreeCADAdapter) -> None:
    plan = parse_plan({"id": "up_to", "kind": "part", "operations": PLAN})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    trace = executor.run_to_end()
    assert trace.final_state is ExecutorState.completed, _debug(trace)
    disc = math.pi * 4.0
    base = 40 * 40 * 10 + 20 * 20 * 5
    assert _volume(adapter) == pytest.approx(base - disc * 15.0, abs=1e-3)
    note = " ".join(_warnings(trace))
    assert "emulated" in note and "depth 15 mm" in note and "measured to" in note

    # the boss grows 3 mm: its top face moves up, the hole is run again with the SAME target face reference
    operations = [op.model_dump(mode="python") for op in executor.plan.operations]
    index = next(i for i, op in enumerate(operations) if op["id"] == "boss")
    executor.undo_to(operations[index - 1]["id"])
    executor.extend([{**operations[index], "depth_mm": 8}, *operations[index + 1 :]])
    trace = executor.run_to_end()
    assert trace.final_state is ExecutorState.completed
    assert _volume(adapter) == pytest.approx(40 * 40 * 10 + 20 * 20 * 8 - disc * 18.0, abs=1e-3)
    assert "depth 18 mm" in " ".join(_warnings(trace))


def _debug(trace: ExecutionTrace) -> list[Any]:
    return [
        (
            s.op_id,
            s.status.value,
            s.plan_error,
            s.attempts[-1].error if s.attempts else None,
            [(c.name, c.expected, c.observed) for c in s.last_level2() if c.ok is False],
        )
        for s in trace.steps
    ]
