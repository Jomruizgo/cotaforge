"""Edge sets against the real FreeCAD worker: a 19 mm plate with an L profile, an inner R4 corner, two holes and a
counterbore, chamfered the way a machinist asks for it (the corners of the profile, the straight edges between flat
faces but not the holes) and a corner that cannot take the chamfer (the failing edge is named; ``skip_failing`` applies
the rest). Every volume is derived by hand from the closed forms of the wedges."""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepRecord, StepStatus
from cotaforge.ir import EdgeSet, parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

THICKNESS = 19.0
XY = {"kind": "plane", "name": "XY"}
# R4 on the inner corner (40, 40): the arc runs from (44, 40) to (40, 44) about (44, 44)
_ARC = {"kind": "arc3", "name": "inner", "start": [44, 40], "through": [41.171573, 41.171573], "end": [40, 44]}
TOP = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}


def _line(name: str, a: list[float], b: list[float]) -> dict[str, Any]:
    return {"kind": "line", "name": name, "start": a, "end": b}


def _plate(profile: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": "outline",
            "op": "sketch",
            "intent": "L profile with an R4 inner corner",
            "on": XY,
            "entities": profile,
        },
        {"id": "plate", "op": "extrude", "intent": "19 mm plate", "sketch": "outline", "depth_mm": THICKNESS},
        {
            "id": "hole_a",
            "op": "hole",
            "intent": "clearance hole",
            "face": TOP,
            "at": [20, 20, THICKNESS],
            "standard": "simple",
            "diameter_mm": 6.6,
            "end_condition": "through_all",
        },
        {
            "id": "seat_sketch",
            "op": "sketch",
            "intent": "round seat in the underside",
            "on": XY,
            "entities": [{"kind": "circle", "name": "seat", "center": [70, 20], "diameter": 12}],
        },
        {
            "id": "seat",
            "op": "cut",
            "intent": "seat 6 mm deep, blind: its wall and floor are hole edges too",
            "sketch": "seat_sketch",
            "end_condition": "blind",
            "depth_mm": 6,
            "direction": "positive",  # the sketch plane's normal is +Z and the plate lies above it
        },
    ]


# the corners of the L are all well inside their faces
L_PROFILE = [
    _line("bottom", [0, 0], [100, 0]),
    _line("right", [100, 0], [100, 40]),
    _line("step", [100, 40], [44, 40]),
    _ARC,
    _line("wall", [40, 44], [40, 80]),
    _line("top", [40, 80], [0, 80]),
    _line("left", [0, 80], [0, 0]),
]
# right after the R4 the wall jogs 2 mm outwards: the jog face is 2 mm wide, so the convex corner (42, 47) at its end
# cannot take 3 mm (FreeCAD: "BRep_API: command not done"); the other five corners can
NOTCHED_PROFILE = [
    _line("bottom", [0, 0], [100, 0]),
    _line("right", [100, 0], [100, 40]),
    _line("step", [100, 40], [44, 40]),
    _ARC,
    _line("short_wall", [40, 44], [40, 47]),
    _line("jog", [40, 47], [42, 47]),
    _line("wall", [42, 47], [42, 80]),
    _line("top", [42, 80], [0, 80]),
    _line("left", [0, 80], [0, 0]),
]
CORNERS = {"kind": "edges", "from_op": "plate", "where": {"parallel_to": "z", "convex": True}}
STRAIGHT_FLAT = {"kind": "edges", "from_op": "plate", "where": {"curve": "line", "planar": True}}


def _chamfer(op_id: str, edges: list[dict[str, Any]], distance: float, **extra: Any) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "chamfer",
        "intent": "machine chamfer",
        "edges": edges,
        "distance1_mm": distance,
        **extra,
    }


def _run(adapter: FreeCADAdapter, operations: list[dict[str, Any]]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "edge_sets", "kind": "part", "operations": operations})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end()


def _step(trace: ExecutionTrace, op_id: str) -> StepRecord:
    step = trace.step(op_id)
    assert step is not None, op_id
    return step


def _volume(trace: ExecutionTrace, op_id: str) -> float:
    step = _step(trace, op_id)
    assert step.mass_after is not None
    return step.mass_after.volume_mm3


def _why(trace: ExecutionTrace) -> list[Any]:
    return [
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


def test_a_3_mm_chamfer_on_the_convex_corners_of_the_profile_is_one_op(adapter: FreeCADAdapter) -> None:
    """(a) five convex corners of the L (the R4 corner and its two tangent edges are not): V = V0 - 5 * 3*3/2 * 19."""
    _, trace = _run(adapter, [*_plate(L_PROFILE), _chamfer("corners", [CORNERS], 3)])
    assert trace.final_state is ExecutorState.completed, _why(trace)
    corners = _step(trace, "corners")
    assert len(corners.attempts[-1].request["edges"]) == 5
    assert corners.notes[0].startswith("edges[0]: 5 edge(s) (parallel_to Z, convex)")
    assert len(corners.notes) == 6 and all("length 19 mm" in line for line in corners.notes[1:])
    assert _volume(trace, "corners") == pytest.approx(_volume(trace, "seat") - 5 * 3 * 3 / 2 * THICKNESS, abs=1e-3)
    delta = next(c for c in corners.last_level2() if c.name == "volume_delta")
    assert delta.kind is CheckKind.asserted and delta.ok is True


def test_1x45_on_the_straight_edges_between_flat_faces_leaves_the_holes_alone(adapter: FreeCADAdapter) -> None:
    """(b) the straight edges between planar faces, in one op: the bores and the counterbore are not touched."""
    executor, trace = _run(adapter, [*_plate(L_PROFILE), _chamfer("edge_break", [STRAIGHT_FLAT], 1)])
    assert trace.final_state is ExecutorState.completed, _why(trace)
    step = _step(trace, "edge_break")
    count = len(step.attempts[-1].request["edges"])
    assert count == 17  # 6 + 6 on the contour of the two faces and the 5 corners; no circle, no arc
    assert step.notes[0].startswith(f"edges[0]: {count} edge(s) (planar faces, curve line)")
    removed = _volume(trace, "seat") - _volume(trace, "edge_break")
    assert 0 < removed < count * 1 * 1 / 2 * 100  # meeting chamfers overlap at the corners: a bound, not a sum
    # the edges meet at the corners, so the exact sum of the wedges is only reported and the sign is what is asserted
    by_name = {c.name: c for c in step.last_level2()}
    assert by_name["volume_delta"].kind is CheckKind.reported
    assert by_name["volume_sign"].kind is CheckKind.asserted and by_name["volume_sign"].ok is True
    # the hole and the seat are untouched: the same circular edges as before the chamfer
    circles = executor.preview_edge_set(
        EdgeSet.model_validate({"kind": "edges", "from_op": "edge_break", "where": {"curve": "circle"}})
    )
    assert len(circles) == 4  # mouth and exit of the hole, the seat's mouth and its floor


def test_a_corner_that_cannot_take_the_chamfer_is_named_and_skip_failing_applies_the_rest(
    adapter: FreeCADAdapter,
) -> None:
    """(c) the jogged plate: the corner (42, 47) ends a 2 mm face right after the R4. Without skip_failing the op names
    the corner; with it the other five are chamfered and the one skipped is reported."""
    _, refused = _run(adapter, [*_plate(NOTCHED_PROFILE), _chamfer("corners", [CORNERS], 3)])
    failed = _step(refused, "corners")
    assert failed.status is StepStatus.failed
    error = failed.attempts[-1].error
    assert error is not None
    assert "cannot take 1 of the 6 edges" in error.message and "at (42, 47, 9.5)" in error.message
    assert "skip_failing" in error.message
    assert _volume(refused, "seat") > 0 and refused.final_mass is not None
    assert refused.final_mass.volume_mm3 == pytest.approx(_volume(refused, "seat"))  # nothing was left half-done

    executor, trace = _run(adapter, [*_plate(NOTCHED_PROFILE), _chamfer("corners", [CORNERS], 3, skip_failing=True)])
    assert trace.final_state is ExecutorState.completed, _why(trace)
    step = _step(trace, "corners")
    result = step.attempts[-1].result
    assert result is not None and len(result.skipped_edges) == 1
    assert any(line.startswith("skipped 1 edge(s)") for line in step.notes)
    assert "midpoint (42, 47, 9.5)" in step.notes[-1]
    removed = _volume(trace, "seat") - _volume(trace, "corners")
    assert removed == pytest.approx(5 * 3 * 3 / 2 * THICKNESS, abs=1e-3)
    assert executor.state is ExecutorState.completed


def test_exclude_holes_drops_the_circles_of_the_hole_and_the_seat(adapter: FreeCADAdapter) -> None:
    executor, trace = _run(adapter, _plate(L_PROFILE))
    assert trace.final_state is ExecutorState.completed, _why(trace)

    def edges(**where: Any) -> list[Any]:
        return executor.preview_edge_set(EdgeSet.model_validate({"kind": "edges", "from_op": "seat", "where": where}))

    convex = edges(convex=True)
    kept = edges(convex=True, exclude_holes=True)
    # the mouth and the exit of the hole and the mouth of the seat are convex circles; the floor of the seat is concave
    assert len(convex) - len(kept) == 3
    assert {e.curve.value for e in kept} == {"line", "arc"}
    assert len(kept) == 19  # 7 + 7 on the contour (the R4 arcs included) and the 5 corners
    # a point drops the edge through it; the arc of the R4 has no corner to name, so it stays
    assert len(edges(parallel_to="z", convex=True, **{"except": [[0, 0, 9]]})) == 4
    top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    contour = edges(on_face=top, exclude_holes=True)
    assert len(contour) == 7 and all(abs((e.start[2] + e.end[2]) / 2 - THICKNESS) < 1e-6 for e in contour if e.start)


def test_a_fillet_takes_the_same_set(adapter: FreeCADAdapter) -> None:
    fillet = {"id": "round", "op": "fillet", "intent": "round the corners", "edges": [CORNERS], "radius_mm": 2}
    _, trace = _run(adapter, [*_plate(L_PROFILE), fillet])
    assert trace.final_state is ExecutorState.completed, _why(trace)
    step = _step(trace, "round")
    assert len(step.attempts[-1].request["edges"]) == 5
    removed = _volume(trace, "seat") - _volume(trace, "round")
    assert removed == pytest.approx(5 * (1 - math.pi / 4) * 2 * 2 * THICKNESS, abs=1e-3)
    assert next(c for c in step.last_level2() if c.name == "fillet_radius").ok is True


def test_a_fillet_of_a_round_edge_is_judged_by_the_tube_of_its_torus(adapter: FreeCADAdapter) -> None:
    """The mouth of the hole is a circle between the top and the bore: its fillet is a torus patch (the same blend as
    the root radius of a boss), not a cylinder."""
    mouth = {"kind": "edges", "from_op": "hole_a", "where": {"curve": "circle", "on_face": TOP}}
    fillet = {"id": "round", "op": "fillet", "intent": "round the mouth of the hole", "edges": [mouth], "radius_mm": 1}
    _, trace = _run(adapter, [*_plate(L_PROFILE), fillet])
    assert trace.final_state is ExecutorState.completed, _why(trace)
    check = next(c for c in _step(trace, "round").last_level2() if c.name == "fillet_radius")
    assert check.ok is True, check


def test_a_fillet_that_one_corner_cannot_take_is_isolated_the_same_way(adapter: FreeCADAdapter) -> None:
    fillet = {"id": "round", "op": "fillet", "intent": "round the corners", "edges": [CORNERS], "radius_mm": 3}
    _, refused = _run(adapter, [*_plate(NOTCHED_PROFILE), fillet])
    error = _step(refused, "round").attempts[-1].error
    assert error is not None and "fillet cannot take 1 of the 6 edges" in error.message
    assert "at (42, 47, 9.5)" in error.message

    _, trace = _run(adapter, [*_plate(NOTCHED_PROFILE), {**fillet, "skip_failing": True}])
    assert trace.final_state is ExecutorState.completed, _why(trace)
    step = _step(trace, "round")
    result = step.attempts[-1].result
    assert result is not None and len(result.skipped_edges) == 1
    removed = _volume(trace, "seat") - _volume(trace, "round")
    assert removed == pytest.approx(5 * (1 - math.pi / 4) * 3 * 3 * THICKNESS, abs=1e-3)


def test_an_edge_set_is_resolved_again_when_the_part_changes(adapter: FreeCADAdapter) -> None:
    """A thicker plate under the same chamfer: the set finds the same corners and the volume follows."""
    ops = [*_plate(L_PROFILE), _chamfer("corners", [CORNERS], 3)]
    executor, trace = _run(adapter, ops)
    assert trace.final_state is ExecutorState.completed, _why(trace)
    again = [*_plate(L_PROFILE)]
    again[1] = {**again[1], "depth_mm": 25}
    _, thick = _run(adapter, [*again, _chamfer("corners", [CORNERS], 3)])
    assert thick.final_state is ExecutorState.completed, _why(thick)
    assert _volume(thick, "seat") - _volume(thick, "corners") == pytest.approx(5 * 3 * 3 / 2 * 25, abs=1e-3)
    assert "length 25 mm" in _step(thick, "corners").notes[1]
