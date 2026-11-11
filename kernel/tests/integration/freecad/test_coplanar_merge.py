"""Features whose faces are coplanar with the body they join (or cut) against the real FreeCAD worker.

PartDesign refines the fused solid, so a face of the new prism that is coplanar with an existing face merges into
it. The L bracket (wall flush with the base on its outer face and ends) used to be refused and undone with
"new_faces_count expected 6 observed 3" although the volume was right.
"""

from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

XY = {"kind": "plane", "name": "XY"}
XZ = {"kind": "plane", "name": "XZ"}
BASE_VOLUME = 80.0 * 60.0 * 6.0


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> ExecutionTrace:
    ir = parse_plan({"id": "coplanar", "kind": "part", "operations": list(operations)})
    executor = Executor(ir, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor.run_to_end()


def _assert_green(trace: ExecutionTrace) -> None:
    detail = [
        (
            s.op_id,
            s.status.value,
            s.plan_error,
            s.attempts[-1].error if s.attempts else None,
            [(c.name, c.expected, c.observed) for c in s.last_level2() if c.ok is False] if s.attempts else None,
        )
        for s in trace.steps
        if s.status is not StepStatus.success
    ]
    assert trace.final_state is ExecutorState.completed, detail
    assert all(s.status is StepStatus.success for s in trace.steps), detail


def _rect(cx: float, cy: float, w: float, h: float) -> dict[str, Any]:
    return {"kind": "rect", "name": "r", "center": [cx, cy], "width": w, "height": h}


def _sketch(op_id: str, on: dict[str, Any], entity: dict[str, Any]) -> dict[str, Any]:
    return {"id": op_id, "op": "sketch", "intent": "i", "on": on, "entities": [entity]}


def _extrude(op_id: str, sketch: str, depth: float, **extra: Any) -> dict[str, Any]:
    return {"id": op_id, "op": "extrude", "intent": "i", "sketch": sketch, "depth_mm": depth, **extra}


def _base() -> list[dict[str, Any]]:
    return [_sketch("s0", XY, _rect(40, 30, 80, 60)), _extrude("base", "s0", 6)]


def _faces(trace: ExecutionTrace, op_id: str) -> Any:
    step = trace.step(op_id)
    assert step is not None
    return next(c for c in step.last_level2() if c.name == "new_faces_count")


def test_l_bracket_wall_flush_on_the_outer_face_and_both_ends_is_accepted(adapter: FreeCADAdapter) -> None:
    """Wall 80 x 6 x 50 on top of the 80 x 60 x 6 base: outer face and both ends coplanar with the base."""
    trace = _run(
        adapter,
        *_base(),
        _sketch("sw", XZ, _rect(40, -31, 80, 50)),  # XZ frame has v = -Z: x 0..80, z 6..56
        _extrude("wall", "sw", 6),  # along +Y: y 0..6 (flush with the base's long edge)
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(BASE_VOLUME + 80 * 6 * 50, abs=1e-3)
    counted = _faces(trace, "wall")
    assert counted.kind is CheckKind.reported
    assert counted.observed < counted.expected  # faces merged into the base


def test_wall_flush_only_on_the_outer_face_is_accepted(adapter: FreeCADAdapter) -> None:
    """Wall shorter than the base: only its outer face is coplanar with the base's side face."""
    trace = _run(
        adapter,
        *_base(),
        _sketch("sw", XZ, _rect(40, -31, 60, 50)),  # x 10..70, z 6..56
        _extrude("wall", "sw", 6),
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(BASE_VOLUME + 60 * 6 * 50, abs=1e-3)
    assert _faces(trace, "wall").kind is CheckKind.reported


def test_wall_sketched_on_the_top_face_flush_with_a_long_edge_is_accepted(adapter: FreeCADAdapter) -> None:
    """Rectangle 80 x 6 on the top face, flush with its y = 0 edge, extruded 44 up: not an overhang."""
    trace = _run(
        adapter,
        *_base(),
        _sketch(
            "sw",
            {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
            _rect(0, -27, 80, 6),  # u spans the face, v 24..30 of 30: flush with one long edge
        ),
        _extrude("wall", "sw", 44),
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(BASE_VOLUME + 80 * 6 * 44, abs=1e-3)
    assert _faces(trace, "wall").kind is CheckKind.reported


def test_the_first_solid_keeps_the_face_count_asserted(adapter: FreeCADAdapter) -> None:
    trace = _run(adapter, *_base())
    _assert_green(trace)
    assert _faces(trace, "base").kind is CheckKind.asserted


def test_pocket_with_walls_coplanar_with_existing_faces_is_accepted(adapter: FreeCADAdapter) -> None:
    """A notch cut at the plate's corner: its walls are coplanar with the plate's outer faces."""
    trace = _run(
        adapter,
        *_base(),
        _sketch("sc", XY, _rect(10, 10, 20, 20)),  # x 0..20, y 0..20
        {
            "id": "notch",
            "op": "cut",
            "intent": "i",
            "sketch": "sc",
            "end_condition": "through_all",
            "both_directions": True,
        },
    )
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(BASE_VOLUME - 20 * 20 * 6, abs=1e-3)
