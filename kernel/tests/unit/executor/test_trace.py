"""Trace models round-trip through JSON."""

from datetime import UTC, datetime

from cotaforge.contract import (
    BoundingBox,
    CanaryResult,
    ErrorCode,
    ErrorInfo,
    FeatureHandle,
    MassProperties,
    OperationResult,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.diagnosis import Diagnosis, DiagnosisKind, RepairAction
from cotaforge.executor.trace import (
    AttemptRecord,
    ExecutionTrace,
    ExecutorState,
    HealthEvent,
    StepRecord,
    StepStatus,
)
from cotaforge.verification.results import CheckKind, CheckResult, Expectation


def test_execution_trace_json_round_trip() -> None:
    now = datetime.now(tz=UTC)
    level1 = CheckResult(name="level1", kind=CheckKind.asserted, ok=True, expected=True, observed=True)
    level2 = (
        CheckResult(name="volume_delta", kind=CheckKind.asserted, ok=True, expected=-273.7, observed=-273.7),
        CheckResult(
            name="bbox_after",
            kind=CheckKind.asserted,
            ok=False,
            expected=BoundingBox(min=(0, 0, 0), max=(1, 1, 1)),
            observed=BoundingBox(min=(0, 0, 0), max=(1, 1, 2)),
            message="mismatch",
        ),
        CheckResult(
            name="hole_axis_direction", kind=CheckKind.reported, ok=None, expected=(0.0, 0.0, -1.0), observed=None
        ),
    )
    failed = AttemptRecord(
        number=1,
        request={"operation_id": "hole_1", "depth_mm": 8.0},
        result=None,
        error=ErrorInfo(code=ErrorCode.geometry_failed, message="boom", recoverable=True),
        level1=CheckResult(
            name="level1", kind=CheckKind.asserted, ok=False, expected=True, observed=False, message="boom"
        ),
        duration_ms=1.5,
    )
    ok = AttemptRecord(
        number=2,
        request={"operation_id": "hole_1", "depth_mm": 8.0},
        result=OperationResult(operation_id="hole_1", feature=FeatureHandle(id="feature:2"), rebuild_ok=True),
        error=None,
        level1=level1,
        level2=level2,
        flipped=True,
        duration_ms=2.0,
    )
    step = StepRecord(
        op_id="hole_1",
        op="hole",
        status=StepStatus.success,
        attempts=(failed, ok),
        resolved_refs={"face": "face:top"},
        expectations=(Expectation(name="volume_delta", kind=CheckKind.asserted, unit="mm3", value=-273.7),),
        mass_before=MassProperties(volume_mm3=48000.0, centroid=(50, 30, 4)),
        mass_after=MassProperties(volume_mm3=47726.3, centroid=(50.1, 30.1, 4)),
        started_at=now,
        finished_at=now,
    )
    trace = ExecutionTrace(
        plan_id="plate",
        adapter="stub",
        config=ExecutorConfig(max_retries=1),
        steps=(step,),
        health=(
            HealthEvent(
                when=now,
                trigger="before_plan",
                result=CanaryResult(healthy=True, checks={"plane": True}, duration_ms=0.1),
            ),
        ),
        final_state=ExecutorState.completed,
        final_mass=step.mass_after,
        final_bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 8)),
    )
    payload = trace.model_dump(mode="json")
    restored = ExecutionTrace.model_validate(payload)
    assert restored == trace
    assert restored.step("hole_1") is not None
    assert restored.step("hole_1").last_level2()[1].expected == BoundingBox(min=(0, 0, 0), max=(1, 1, 1))  # type: ignore[union-attr]
    assert restored.step("nope") is None


def test_attempt_record_diagnosis_and_repair_round_trip() -> None:
    """AttemptRecord.diagnosis/repair_applied
    round-trip through JSON exactly like every other trace field."""
    diagnosis = Diagnosis(
        kind=DiagnosisKind.underdefined_sketch,
        op_id="base_sketch",
        source="sketch_status",
        evidence="sketch sk:1: dof=2, free_geometry=[0]",
        dof=2,
        repairable=True,
        repair=RepairAction(
            kind="add_sketch_constraint",
            description="re-emit the IR's declared width/height",
            detail={"free_geometry": "0"},
        ),
    )
    attempt = AttemptRecord(
        number=1,
        request={"operation_id": "base_sketch"},
        result=None,
        error=None,
        level1=CheckResult(
            name="level1", kind=CheckKind.asserted, ok=False, expected=True, observed=False, message="rebuild failed"
        ),
        duration_ms=1.0,
        diagnosis=diagnosis,
        repair_applied=diagnosis.repair,
    )
    payload = attempt.model_dump(mode="json")
    restored = AttemptRecord.model_validate(payload)
    assert restored == attempt
    assert restored.diagnosis is not None and restored.diagnosis.kind is DiagnosisKind.underdefined_sketch
    assert restored.repair_applied is not None and restored.repair_applied.kind == "add_sketch_constraint"


def test_attempt_record_without_diagnosis_fields_defaults_to_none() -> None:
    """An ``AttemptRecord`` dict written before repair existed (no ``diagnosis``/``repair_applied`` keys)
    still loads, both fields defaulting to ``None`` -- the compatibility half of the
    ``repair_on_failure=False`` regression guarantee."""
    payload = {
        "number": 1,
        "request": {"operation_id": "hole_1"},
        "result": None,
        "error": None,
        "level1": {"name": "level1", "kind": "asserted", "ok": True, "expected": True, "observed": True},
        "duration_ms": 1.0,
    }
    attempt = AttemptRecord.model_validate(payload)
    assert attempt.diagnosis is None
    assert attempt.repair_applied is None


def test_trace_without_reverts_key_loads_and_active_steps_is_identity() -> None:
    from cotaforge.executor.trace import RevertRecord, active_steps

    now = datetime.now(tz=UTC)
    step = StepRecord(
        op_id="a",
        op="box",
        status=StepStatus.success,
        attempts=(),
        mass_before=None,
        mass_after=None,
        started_at=now,
        finished_at=now,
    )
    trace = ExecutionTrace(
        plan_id="p",
        adapter="fake",
        config=ExecutorConfig(),
        steps=(step,),
        health=(),
        final_state=ExecutorState.completed,
        final_mass=None,
        final_bbox=None,
    )
    data = trace.model_dump(mode="json")
    assert data["reverts"] == []
    del data["reverts"]
    assert ExecutionTrace.model_validate(data) == trace
    assert active_steps(trace) == (step,)
    reverted = trace.model_copy(update={"reverts": (RevertRecord(op_ids=("a",), by="e", reason="r", at=now),)})
    assert reverted.active_steps() == ()
