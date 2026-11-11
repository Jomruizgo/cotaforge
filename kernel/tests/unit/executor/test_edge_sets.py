"""Edge sets resolved on the fake adapter: the criteria, the report, the empty-set error and the chamfer/fillet ops."""

from __future__ import annotations

from typing import Any

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import EdgeSet, parse_plan
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict


def _executor(ops: int = 3) -> Executor:
    """The 100 x 60 x 8 plate (base_sketch, base) and, with ``ops=3``, one clearance hole on its top face."""
    adapter = FakeAdapter()
    plan = {**plate_plan_dict(), "operations": plate_plan_dict()["operations"][:ops]}
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed
    return executor


def _set(**where: Any) -> EdgeSet:
    return EdgeSet.model_validate({"kind": "edges", "from_op": "base", "where": where})


def _mid(edge: Any) -> tuple[float, float, float]:
    assert edge.start is not None and edge.end is not None
    return tuple(round((a + b) / 2.0, 6) for a, b in zip(edge.start, edge.end, strict=True))  # type: ignore[return-value]


def test_the_edges_along_the_thickness_are_the_four_vertical_ones() -> None:
    executor = _executor(2)
    edges = executor.preview_edge_set(_set(parallel_to="z"))
    assert len(edges) == 4
    assert {_mid(e) for e in edges} == {(0.0, 0.0, 4.0), (100.0, 0.0, 4.0), (0.0, 60.0, 4.0), (100.0, 60.0, 4.0)}
    assert all(e.length_mm == pytest.approx(8.0) for e in edges)


def test_the_result_is_sorted_and_the_same_every_time() -> None:
    executor = _executor(2)
    first = [e.handle.id for e in executor.preview_edge_set(_set())]
    assert len(first) == 12
    assert first == [e.handle.id for e in executor.preview_edge_set(_set())]


def test_criteria_combine_length_convex_and_curve() -> None:
    executor = _executor(2)
    long_ones = executor.preview_edge_set(_set(length_mm={"min": 90}))
    assert len(long_ones) == 4 and all(e.length_mm == pytest.approx(100.0) for e in long_ones)
    exact = executor.preview_edge_set(_set(length_mm=60.0, parallel_to="y", convex=True))
    assert len(exact) == 4


def test_an_empty_match_raises_with_the_funnel() -> None:
    executor = _executor(2)
    with pytest.raises(ResolutionError) as exc:
        executor.preview_edge_set(_set(parallel_to="z", convex=False))
    text = str(exc.value)
    assert "matched 0 edges" in text and "12 edge(s)" in text and "after parallel_to: 4" in text
    assert "after concave: 0" in text
    assert exc.value.candidate_details  # the four vertical edges that the last criterion dropped


def test_except_drops_the_edge_through_a_point_and_an_edge_ref() -> None:
    executor = _executor(2)
    kept = executor.preview_edge_set(_set(parallel_to="z", **{"except": [[0.0, 60.0, 2.0]]}))
    assert len(kept) == 3 and (0.0, 60.0, 4.0) not in {_mid(e) for e in kept}
    nothing_near = executor.preview_edge_set(_set(parallel_to="z", **{"except": [[50.0, 30.0, 4.0]]}))
    assert len(nothing_near) == 4


def test_on_face_takes_only_the_contour_of_that_face() -> None:
    executor = _executor(2)
    ref = EdgeSet.model_validate(
        {
            "kind": "edges",
            "where": {"on_face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}},
        }
    )
    edges = executor.preview_edge_set(ref)
    assert len(edges) == 4 and all(e.length_mm in (pytest.approx(100.0), pytest.approx(60.0)) for e in edges)
    assert {round(_mid(e)[2], 6) for e in edges} == {8.0}


def test_planar_and_exclude_holes_leave_out_the_hole() -> None:
    executor = _executor(3)  # a through hole in the top face: a circle edge on the top and one on the bottom
    everything = executor.preview_edge_set(_set())
    circles = executor.preview_edge_set(_set(curve="circle"))
    assert len(circles) == 2 and len(everything) > len(circles)
    flat = executor.preview_edge_set(_set(planar=True))
    assert len(flat) == len(everything) - 2  # no edge of the bore wall
    assert len(executor.preview_edge_set(_set(exclude_holes=True))) == len(everything) - 2
    straight = executor.preview_edge_set(_set(curve="line", planar=True))
    assert len(straight) == 12


def test_a_set_that_names_a_step_without_geometry_is_a_plan_error() -> None:
    executor = _executor(2)
    with pytest.raises(ResolutionError, match="produced no feature"):
        executor.preview_edge_set(EdgeSet(kind="edges", from_op="nowhere"))


def _chamfer(edges: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"id": "ch", "op": "chamfer", "intent": "machine chamfer", "edges": edges, "distance1_mm": 2, **extra}


def test_a_chamfer_on_a_set_runs_once_reports_the_edges_and_verifies_the_volume() -> None:
    executor = _executor(2)
    executor.extend([_chamfer([{"kind": "edges", "from_op": "base", "where": {"parallel_to": "z", "convex": True}}])])
    executor.run_to_end()
    step = executor.trace().step("ch")
    assert step is not None and step.status is StepStatus.success
    assert step.resolved_refs["edges[0]"].count(",") == 3  # four edge ids
    assert step.notes[0].startswith("edges[0]: 4 edge(s) (parallel_to Z, convex)")
    assert len(step.notes) == 5 and "direction (0, 0, 1)" in step.notes[1]
    request = step.attempts[-1].request
    assert len(request["edges"]) == 4 and request["skip_failing"] is False
    delta = next(c for c in step.last_level2() if c.name == "volume_delta")
    assert delta.ok is True and delta.observed == pytest.approx(-4 * 2 * 2 / 2 * 8)


def test_a_set_and_a_single_edge_are_merged_without_repeating_an_edge() -> None:
    executor = _executor(2)
    corner = {
        "kind": "edge",
        "between": [
            {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}},
            {"kind": "face", "from_op": "base", "select": {"normal": [0, 1, 0]}},
        ],
    }
    set_z = {"kind": "edges", "from_op": "base", "where": {"parallel_to": "z"}}
    executor.extend([_chamfer([corner, set_z])])
    executor.run_to_end()
    step = executor.trace().step("ch")
    assert step is not None and step.status is StepStatus.success
    assert len(step.attempts[-1].request["edges"]) == 4


def test_a_set_that_matches_nothing_fails_the_step_as_a_plan_error_with_the_reason() -> None:
    executor = _executor(2)
    executor.extend([_chamfer([{"kind": "edges", "from_op": "base", "where": {"length_mm": 7}}])])
    record = executor.next_operation()
    assert record.status is StepStatus.plan_error
    assert record.plan_error is not None and "matched 0 edges" in record.plan_error


def test_a_skip_failing_op_keeps_volume_as_a_bound_and_a_corner_set_is_not_asserted() -> None:
    """The expectations for a set whose edges meet (all twelve) or that may skip edges: the exact sum is reported,
    the volume sign is what is asserted, and the face count is not assumed."""
    from cotaforge.contract import ChamferRequest  # noqa: PLC0415
    from cotaforge.ir import ChamferOperation  # noqa: PLC0415
    from cotaforge.verification.results import CheckKind  # noqa: PLC0415

    executor = _executor(2)
    edges = executor.preview_edge_set(_set())
    op = ChamferOperation.model_validate(_chamfer([{"kind": "edges", "from_op": "base"}]))
    snapshot = executor._snapshot(op, ChamferRequest(operation_id="ch", edges=(), distance1_mm=2), *_measure(executor))  # noqa: SLF001
    request = ChamferRequest(operation_id="ch", edges=tuple(e.handle for e in edges), distance1_mm=2)
    by = {e.name: e for e in executor.verifier.expected(op, request, snapshot, executor.adapter)}
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["created_faces_count"].kind is CheckKind.reported
    assert by["volume_sign"].kind is CheckKind.asserted and by["volume_sign"].value == -1
    apart = [e for e in edges if _mid(e)[2] == pytest.approx(4.0)]  # the four vertical edges never meet each other
    request = ChamferRequest(operation_id="ch", edges=tuple(e.handle for e in apart), distance1_mm=2, skip_failing=True)
    by = {e.name: e for e in executor.verifier.expected(op, request, snapshot, executor.adapter)}
    assert by["volume_delta"].kind is CheckKind.reported and "skip_failing" in by["volume_delta"].note
    request = request.model_copy(update={"skip_failing": False})
    by = {e.name: e for e in executor.verifier.expected(op, request, snapshot, executor.adapter)}
    assert by["volume_delta"].kind is CheckKind.asserted and "volume_sign" not in by


def _measure(executor: Executor) -> tuple[Any, Any]:
    from cotaforge.contract import MassRequest  # noqa: PLC0415

    return (
        executor.adapter.mass_properties(MassRequest(operation_id="m")),
        executor.adapter.bounding_box(MassRequest(operation_id="m")),
    )


def test_editing_a_chamfer_with_a_set_is_left_to_a_rebuild() -> None:
    executor = _executor(2)
    executor.extend([_chamfer([{"kind": "edges", "from_op": "base", "where": {"parallel_to": "z"}}])])
    executor.run_to_end()
    edited = [
        {**op.model_dump(mode="json"), "distance1_mm": 3.0} if op.id == "ch" else op.model_dump(mode="json")
        for op in executor.plan.operations
    ]
    new_plan = parse_plan({**executor.plan.model_dump(mode="json"), "operations": edited})
    record = executor.edit_in_place(new_plan, ["ch"])
    assert record.status == "refused" and "edge set" in (record.failure or "")
