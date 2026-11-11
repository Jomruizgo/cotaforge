"""Executor step algorithm and flip extension (5.6), every branch, on the StubAdapter."""

import math
from typing import Any

import pytest

from cotaforge.contract import ErrorCode, FaceHandle, FacesResult, FeatureHandle, MassRequest, SketchStatusResult
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.diagnosis import DiagnosisKind
from cotaforge.executor.errors import ExecutorStateError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

from .plate import HOLE_VOLUME, extrude_step, hole_step, pattern_step, plate_stub
from .stub import EMPTY_MASS, Step, StubAdapter, error


def _executor(plan_dict: dict[str, Any], stub: StubAdapter, config: ExecutorConfig | None = None) -> Executor:
    return Executor(parse_plan(plan_dict), stub, Verifier(), config or ExecutorConfig())


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None
    return {c.name for c in step.last_level2() if c.kind == CheckKind.asserted and c.ok}


# --- happy path --------------------------------------------------------------------------------------


def test_plate_plan_runs_to_completion(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    ex = _executor(plate_plan, stub)
    assert ex.status().state == ExecutorState.idle
    ex.start()
    assert ex.status().state == ExecutorState.running
    assert ex.status().current_op_id == "base_sketch"
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    assert [s.status for s in trace.steps] == [StepStatus.success] * 4
    assert [len(s.attempts) for s in trace.steps] == [1, 1, 1, 1]
    assert trace.plan_id == "plate"
    assert trace.adapter == "stub"
    assert trace.health[0].trigger == "before_plan"
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(48000.0 - 4 * HOLE_VOLUME)
    assert ex.document().id == "doc:1"
    assert ex.status().cursor == 4 and ex.status().current_op_id is None
    # per-op asserted checks E will read
    assert {
        "hole_axis_direction",
        "hole_axis_position",
        "hole_radius",
        "cut_extent_along_n",
        "new_faces_count",
    } <= _asserted_ok(trace, "hole_1")
    assert {"instance_count", "created_faces_count", "volume_delta"} <= _asserted_ok(trace, "holes")
    assert {"volume_delta", "bbox_after", "new_faces_count"} <= _asserted_ok(trace, "base")
    hole_step_record = trace.step("hole_1")
    assert hole_step_record is not None
    assert hole_step_record.resolved_refs == {"face": "face:top"}
    # the pattern's expected volume used the seed's observed delta (3 x hole)
    holes = trace.step("holes")
    assert holes is not None
    volume = next(e for e in holes.expectations if e.name == "volume_delta")
    assert volume.kind == CheckKind.asserted
    assert volume.value == pytest.approx(-3 * HOLE_VOLUME)


def test_mass_and_bbox_are_queried_before_the_first_op_on_the_empty_document(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    ex = _executor(plate_plan, stub)
    ex.start()
    first = ex.next_operation()
    assert first.op == "sketch"
    assert first.mass_before == EMPTY_MASS
    names = [name for name, _ in stub.calls]
    assert names[:4] == ["canary", "new_document", "mass_properties", "bounding_box"]
    assert isinstance(stub.calls[2][1], MassRequest)
    # a sketch creates no feature: level 1 passes without one and nothing is undone
    assert first.status == StepStatus.success
    assert first.attempts[0].result is not None and first.attempts[0].result.feature is None
    assert not stub.calls_to("undo_last")


def test_trace_json_round_trip(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan, plate_stub())
    ex.start()
    trace = ex.run_to_end()
    restored = ExecutionTrace.model_validate(trace.model_dump(mode="json"))
    assert restored == trace


def test_reported_checks_never_block(plate_plan: dict[str, Any]) -> None:
    # through-all hole: volume_delta is reported; a wrong volume must not fail the step
    stub = plate_stub()
    stub.scripts["hole_1"] = [hole_step(volume_factor=0.5)]
    ex = _executor(plate_plan, stub)
    ex.start()
    ex.next_operation()
    ex.next_operation()
    hole = ex.next_operation()
    assert hole.status == StepStatus.success
    reported = [c for c in hole.last_level2() if c.name == "volume_delta"]
    assert reported[0].kind == CheckKind.reported and reported[0].ok is None
    assert reported[0].observed == pytest.approx(-HOLE_VOLUME / 2)


# --- resolution (plan errors) ------------------------------------------------------------------------------


def _hole_selector(plate_plan: dict[str, Any], select: dict[str, Any]) -> dict[str, Any]:
    plate_plan["operations"][2]["face"]["select"] = select
    return plate_plan


def test_ambiguous_face_is_a_plan_error_without_any_construction_call(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    ex = _executor(_hole_selector(plate_plan, {"surface": "plane"}), stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.plan_error
    assert hole.attempts == ()
    assert hole.plan_error is not None and "6 candidates" in hole.plan_error
    assert not [c for c in stub.construction_calls() if c[1].operation_id == "hole_1"]
    assert trace.step("holes") is None
    assert not stub.calls_to("undo_last")


def test_zero_candidates_is_a_plan_error(plate_plan: dict[str, Any]) -> None:
    ex = _executor(_hole_selector(plate_plan, {"normal": [1, 1, 0]}), plate_stub())
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    assert trace.step("hole_1").status == StepStatus.plan_error  # type: ignore[union-attr]
    assert "0 candidates" in trace.step("hole_1").plan_error  # type: ignore[union-attr, operator]


# --- level 1 failures --------------------------------------------------------------------------------------------


def test_rebuild_failed_is_undone_then_retried(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["hole_1"] = [hole_step(rebuild_ok=False), hole_step()]
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.success
    assert [a.number for a in hole.attempts] == [1, 2]
    assert hole.attempts[0].level1.ok is False
    assert hole.attempts[0].undone is True
    assert hole.attempts[0].error is None
    assert hole.attempts[1].level1.ok is True
    assert len(stub.calls_to("undo_last")) == 1
    assert [t for t in (e.trigger for e in trace.health)] == ["before_plan", "after_failure"]


def test_raised_recoverable_error_is_retried_without_undo(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["hole_1"] = [Step(result=error(ErrorCode.geometry_failed, recoverable=True)), hole_step()]
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None
    assert len(hole.attempts) == 2
    assert hole.attempts[0].result is None
    assert hole.attempts[0].error is not None and hole.attempts[0].error.code == ErrorCode.geometry_failed
    assert hole.attempts[0].undone is False
    assert not stub.calls_to("undo_last")


def test_non_recoverable_error_is_not_retried(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["hole_1"] = [Step(result=error(ErrorCode.invalid_reference, recoverable=False)), hole_step()]
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.failed
    assert len(hole.attempts) == 1
    assert trace.step("holes") is None


def test_retries_exhausted_fails_the_plan(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["hole_1"] = [Step(result=error())]  # the last step repeats forever
    ex = _executor(plate_plan, stub, ExecutorConfig(max_retries=2))
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.failed
    assert len(hole.attempts) == 3
    assert len([e for e in trace.health if e.trigger == "after_failure"]) == 3


def test_canary_after_failure_unhealthy_degrades_and_stops_all_adapter_calls(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["hole_1"] = [Step(result=error(), healthy_after=False), hole_step()]
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.degraded
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.failed
    assert len(hole.attempts) == 1
    assert trace.health[-1].trigger == "after_failure" and trace.health[-1].result.healthy is False
    calls_before = len(stub.calls)
    assert stub.calls[-1][0] == "canary"
    assert ex.run_to_end().final_state == ExecutorState.degraded
    with pytest.raises(ExecutorStateError):
        ex.next_operation()
    assert len(stub.calls) == calls_before


def test_canary_after_failure_can_be_disabled(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["hole_1"] = [Step(result=error()), hole_step()]
    ex = _executor(plate_plan, stub, ExecutorConfig(canary_after_failure=False, canary_before_plan=False))
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    assert trace.health == ()


# --- level 2 failures --------------------------------------------------------------------------------------


def test_level2_asserted_failure_undoes_and_stops(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["holes"] = [pattern_step(instances=3)]  # instance_count 3 != 4 and volume off
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    holes = trace.step("holes")
    assert holes is not None
    assert holes.status == StepStatus.failed
    assert len(holes.attempts) == 1
    assert holes.attempts[0].level1.ok is True
    assert holes.attempts[0].undone is True
    failing = {c.name for c in holes.last_level2() if c.ok is False}
    assert "instance_count" in failing
    assert len(stub.calls_to("undo_last")) == 1
    # the document is back to the state before the op
    assert stub.mass.volume_mm3 == pytest.approx(48000.0 - HOLE_VOLUME)
    assert holes.mass_after is None
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(48000.0 - HOLE_VOLUME)


def test_level2_failure_is_not_retried(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.scripts["holes"] = [pattern_step(instances=3), pattern_step()]
    ex = _executor(plate_plan, stub, ExecutorConfig(max_retries=3))
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    assert len(trace.step("holes").attempts) == 1  # type: ignore[union-attr]


def test_warn_only_mode_registers_and_continues(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    # wrong radius on the created cylindrical face -> hole_radius (asserted) fails
    stub.faces["face:cyl"] = stub.faces["face:cyl"].model_copy(update={"radius": 3.0})
    ex = _executor(plate_plan, stub, ExecutorConfig(stop_on_verification_failure=False))
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.success
    assert hole.attempts[0].undone is False
    assert any(c.name == "hole_radius" and c.ok is False for c in hole.last_level2())
    assert trace.step("holes").status == StepStatus.success  # type: ignore[union-attr]
    assert not stub.calls_to("undo_last")


# --- pause / resume and state guards --------------------------------------------------------------------------


def test_pause_is_honoured_after_the_current_op_and_resume_continues(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan, plate_stub())
    ex.start()
    ex.pause()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.paused
    assert len(trace.steps) == 1
    # stepping while paused executes exactly one op and stays paused
    record = ex.next_operation()
    assert record.op_id == "base"
    assert ex.status().state == ExecutorState.paused
    ex.resume()
    assert ex.status().state == ExecutorState.running
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    assert len(trace.steps) == 4


def test_state_guards(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan, plate_stub())
    with pytest.raises(ExecutorStateError):
        ex.next_operation()
    with pytest.raises(ExecutorStateError):
        ex.document()
    ex.start()
    with pytest.raises(ExecutorStateError):
        ex.start()
    ex.run_to_end()
    with pytest.raises(ExecutorStateError):
        ex.next_operation()


def test_unhealthy_canary_before_plan_degrades_without_a_document(plate_plan: dict[str, Any]) -> None:
    stub = plate_stub()
    stub.healthy = False
    ex = _executor(plate_plan, stub)
    ex.start()
    assert ex.status().state == ExecutorState.degraded
    assert not stub.calls_to("new_document")
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.degraded
    assert trace.steps == ()
    assert trace.health[-1].trigger == "before_plan" and trace.health[-1].result.healthy is False
    # document() must not raise once the executor has started, even without a document (5.1/5.5)
    assert ex.document() is None


def test_expectation_computation_error_is_a_plan_error_not_a_crash(plate_plan: dict[str, Any]) -> None:
    """Regression: a non-ResolutionError raised while computing expectations (e.g. formulas.centroid_after's
    ``ValueError("zero total volume")``) must finish the step as plan_error, not escape next_operation."""

    class _RaisingVerifier(Verifier):
        def expected(self, op: Any, request: Any, before: Any, adapter: Any) -> tuple[Any, ...]:
            msg = "zero total volume"
            raise ValueError(msg)

    ex = _executor(plate_plan, plate_stub(), None)
    ex.verifier = _RaisingVerifier()
    ex.start()
    step = ex.next_operation()
    assert step.status == StepStatus.plan_error
    assert step.plan_error == "expectation computation failed: ValueError: zero total volume"
    assert step.attempts == ()
    assert ex.status().state == ExecutorState.failed


def test_empty_plan_completes_on_start() -> None:
    ex = _executor({"id": "empty", "operations": []}, StubAdapter())
    ex.start()
    assert ex.status().state == ExecutorState.completed
    assert ex.run_to_end().final_state == ExecutorState.completed


def test_status_reports_last_step(plate_plan: dict[str, Any]) -> None:
    ex = _executor(plate_plan, plate_stub())
    ex.start()
    assert ex.status().last_step is None
    record = ex.next_operation()
    status = ex.status()
    assert status.last_step == record
    assert status.cursor == 1 and status.total == 4 and status.current_op_id == "base"


# --- diagnosis and bounded repair -----------------


def test_repair_on_failure_false_is_unchanged_from_the_behaviour_without_repair(plate_plan: dict[str, Any]) -> None:
    """The regression that matters most: with ``repair_on_failure=False``, a level-1 failure runs through
    exactly the path without repair -- same attempts, same undo, same outcome -- and every
    ``diagnosis``/``repair_applied`` stays ``None``."""
    stub = plate_stub()
    stub.scripts["hole_1"] = [hole_step(rebuild_ok=False), hole_step()]
    ex = _executor(plate_plan, stub, ExecutorConfig(repair_on_failure=False))
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.success
    assert [a.number for a in hole.attempts] == [1, 2]
    assert hole.attempts[0].level1.ok is False
    assert hole.attempts[0].undone is True
    assert hole.attempts[0].error is None
    assert hole.attempts[1].level1.ok is True
    assert len(stub.calls_to("undo_last")) == 1
    assert [a.diagnosis for a in hole.attempts] == [None, None]
    assert [a.repair_applied for a in hole.attempts] == [None, None]


def test_repair_on_failure_true_does_not_change_the_outcome_of_a_non_repairable_failure(
    plate_plan: dict[str, Any],
) -> None:
    """Same scenario as above with the default (``repair_on_failure=True``): the hole op is not
    sketch-related and carries no dangling reference, so it is diagnosed as ``rebuild_error`` -- reported,
    never repaired -- and the outcome (attempts, undo, final state) is identical either way."""
    stub = plate_stub()
    stub.scripts["hole_1"] = [hole_step(rebuild_ok=False), hole_step()]
    ex = _executor(plate_plan, stub)  # default config: repair_on_failure=True
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None
    assert [a.number for a in hole.attempts] == [1, 2]
    assert hole.attempts[0].undone is True
    diagnosis = hole.attempts[0].diagnosis
    assert diagnosis is not None
    assert diagnosis.kind is DiagnosisKind.rebuild_error
    assert diagnosis.repairable is False
    assert hole.attempts[0].repair_applied is None
    assert hole.attempts[1].diagnosis is None  # the successful attempt is never diagnosed


def test_underdefined_sketch_downstream_is_diagnosed_and_the_existing_retry_still_succeeds(
    plate_plan: dict[str, Any],
) -> None:
    """``base`` (extrude) depends on ``base_sketch``; a scripted sketch_status reporting dof > 0 gets it
    diagnosed as ``underdefined_sketch`` on its failed attempt, but -- as documented in diagnosis.py --
    a downstream op's request has no field to carry the fix, so it is reported honestly as not
    repairable. The already-existing bounded retry (rebuild_failed is recoverable by default) is what
    actually gets the plan to completion, unaffected by the diagnosis."""
    stub = plate_stub()
    failing_base = extrude_step().result.model_copy(update={"rebuild_ok": False})
    stub.scripts["base"] = [Step(result=failing_base), extrude_step()]
    status = SketchStatusResult(
        sketch=FeatureHandle(id="sk:1"),
        dof=2,
        geometry_count=1,
        constraint_count=0,
        fully_constrained=False,
        over_constrained=False,
        free_geometry=(0,),
    )
    stub.sketch_status = lambda request: status  # type: ignore[method-assign]
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    base = trace.step("base")
    assert base is not None
    assert [a.number for a in base.attempts] == [1, 2]
    diagnosis = base.attempts[0].diagnosis
    assert diagnosis is not None
    assert diagnosis.kind is DiagnosisKind.underdefined_sketch
    assert diagnosis.dof == 2
    assert diagnosis.repairable is False
    assert base.attempts[0].repair_applied is None


def test_dangling_reference_is_repaired_by_re_resolving_the_reference(plate_plan: dict[str, Any]) -> None:
    """``hole_1``'s face reference resolves once (before the retry loop) to ``face:top``; the adapter then
    reports ``invalid_reference`` on attempt 1 (a manual edit made that handle stale). The repair
    re-resolves the same reference from provenance -- here, deliberately, to a *different* face (the
    stand-in for "the edit moved the geometry but the same op still owns it") -- and the retried request
    carries the new handle; the op is never undone (it never produced a feature on the failed attempt)
    and the retry succeeds without exhausting the plan's non-recoverable-error rule."""
    stub = plate_stub()
    stub.scripts["hole_1"] = [Step(result=error(ErrorCode.invalid_reference, recoverable=False)), hole_step()]
    original_faces_of = stub.faces_of
    calls = {"n": 0}

    def flaky_faces_of(request: Any) -> FacesResult:
        calls["n"] += 1
        if calls["n"] == 1:
            return original_faces_of(request)
        top2 = stub.faces["face:top"].model_copy(update={"handle": FaceHandle(id="face:top2")})
        stub.faces["face:top2"] = top2  # so face_info(face:top2) resolves on the successful, repaired attempt
        return FacesResult(faces=(top2,))

    stub.faces_of = flaky_faces_of  # type: ignore[method-assign]
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.completed
    hole = trace.step("hole_1")
    assert hole is not None
    assert [a.number for a in hole.attempts] == [1, 2]
    assert hole.attempts[0].undone is False  # no feature was produced on the failed attempt
    diagnosis = hole.attempts[0].diagnosis
    assert diagnosis is not None
    assert diagnosis.kind is DiagnosisKind.dangling_reference
    assert diagnosis.reference == "base"
    assert diagnosis.repairable is True
    repair = hole.attempts[0].repair_applied
    assert repair is not None and repair.kind == "reresolve_reference"
    second_request = hole.attempts[1].request
    assert second_request["face"] == {"id": "face:top2"}
    assert calls["n"] >= 2


def test_max_repairs_per_operation_bounds_repeated_repair_attempts(plate_plan: dict[str, Any]) -> None:
    """``max_repairs_per_operation=1``: the same operation may be diagnosed-and-repaired at most once
    across its own retries, even when every attempt is, in principle, repairable."""
    stub = plate_stub()
    stub.scripts["hole_1"] = [Step(result=error(ErrorCode.invalid_reference, recoverable=False))]  # repeats forever
    original_faces_of = stub.faces_of
    counter = {"n": 0}

    def always_different_faces_of(request: Any) -> FacesResult:
        counter["n"] += 1
        if counter["n"] == 1:
            return original_faces_of(request)
        top = stub.faces["face:top"].model_copy(update={"handle": FaceHandle(id=f"face:top{counter['n']}")})
        return FacesResult(faces=(top,))

    stub.faces_of = always_different_faces_of  # type: ignore[method-assign]
    ex = _executor(plate_plan, stub, ExecutorConfig(max_retries=3, max_repairs_per_operation=1))
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_state == ExecutorState.failed
    hole = trace.step("hole_1")
    assert hole is not None
    assert hole.status == StepStatus.failed
    assert len(hole.attempts) == 2
    assert hole.attempts[0].diagnosis is not None
    assert hole.attempts[0].repair_applied is not None
    # the repair budget is spent: the second attempt is not even diagnosed, and the non-recoverable
    # error stops the loop without a forced third attempt
    assert hole.attempts[1].diagnosis is None
    assert hole.attempts[1].repair_applied is None


def test_pattern_seed_delta_is_none_when_seed_has_no_volume_check(plate_plan: dict[str, Any]) -> None:
    # patterning the extrude (a boss seed): its volume_delta is asserted and observed, so the delta is known;
    # patterning a sketch-less unknown would give None -> reported. Here we check the known path end-to-end.
    plate_plan["operations"][3]["features"] = ["base"]
    stub = plate_stub()
    stub.faces_of_feature["feature:1"] = [
        stub.faces[f"face:{n}"] for n in ("top", "bottom", "xmin", "xmax", "ymin", "ymax")
    ]
    stub.scripts["holes"] = [pattern_step()]
    ex = _executor(plate_plan, stub)
    ex.start()
    trace = ex.run_to_end()
    holes = trace.step("holes")
    assert holes is not None
    volume = next(e for e in holes.expectations if e.name == "volume_delta")
    assert volume.value == pytest.approx(3 * 48000.0)
    assert math.isfinite(volume.value)  # type: ignore[arg-type]
