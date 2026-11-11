"""Executor + StubAdapter: datum ops are non-feature steps that register a pose and are dropped by undo_to."""

from typing import Any

from cotaforge.contract import (
    Adapter,
    Axis,
    DatumAxisHandle,
    DatumAxisResult,
    DatumPlaneHandle,
    DatumPlaneResult,
    DatumPlaneTarget,
    ErrorCode,
    PlaneName,
    Request,
    SketchHandle,
    SketchResult,
    frame_for_plane,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification import datum
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

from .stub import Step, StubAdapter

RECT = {"kind": "rect", "name": "r", "center": [0, 0], "width": 10, "height": 10}
FRAME = datum.plane_offset(frame_for_plane(PlaneName.XY), 10.0)


def _plan() -> dict[str, Any]:
    return {
        "id": "datums",
        "operations": [
            {"id": "s0", "op": "sketch", "intent": "i", "on": {"kind": "plane", "name": "XY"}, "entities": [RECT]},
            {
                "id": "dp",
                "op": "ref_plane",
                "intent": "i",
                "define": {"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 10.0},
            },
            {
                "id": "da",
                "op": "ref_axis",
                "intent": "i",
                "define": {"how": "point_direction", "point": [0, 0, 0], "direction": "Z"},
            },
            {"id": "s1", "op": "sketch", "intent": "i", "on": {"kind": "plane", "from_op": "dp"}, "entities": [RECT]},
        ],
    }


def _stub() -> StubAdapter:
    stub = StubAdapter()
    xy = SketchResult(sketch=SketchHandle(id="sk:0"), frame=frame_for_plane(PlaneName.XY), profile_areas_mm2=(100.0,))
    on_datum = SketchResult(sketch=SketchHandle(id="sk:1"), frame=FRAME, profile_areas_mm2=(100.0,))
    stub.script("s0", Step(result=xy))
    stub.script("dp", Step(result=DatumPlaneResult(datum=DatumPlaneHandle(id="datum_plane:1"), frame=FRAME)))
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    stub.script("da", Step(result=DatumAxisResult(datum=DatumAxisHandle(id="datum_axis:1"), axis=axis)))
    stub.script("s1", Step(result=on_datum))
    return stub


def test_datum_steps_succeed_register_and_feed_the_next_sketch() -> None:
    stub = _stub()
    ex = Executor(parse_plan(_plan()), stub, Verifier(), ExecutorConfig())
    ex.start()
    trace = ex.run_to_end()
    assert [s.status for s in trace.steps] == [StepStatus.success] * 4
    dp = trace.step("dp")
    assert dp is not None
    assert dp.attempts[0].result is not None and dp.attempts[0].result.feature is None
    asserted = {c.name for c in dp.last_level2() if c.kind is CheckKind.asserted and c.ok}
    assert {"volume_delta", "datum_origin", "datum_u", "datum_normal"} <= asserted
    assert not stub.calls_to("undo_last")
    ctx = ex._ctx
    assert ctx is not None
    assert ctx.datum_plane_by_op["dp"] == DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=FRAME)
    assert ctx.datum_axis_by_op["da"].direction == (0.0, 0.0, 1.0)
    sketch_request = stub.calls_to("sketch")[-1]
    assert sketch_request.on == ctx.datum_plane_by_op["dp"]  # type: ignore[attr-defined]
    assert ctx.sketch_frame_by_op["s1"] == FRAME
    assert trace.step("s1").resolved_refs == {"on": "datum_plane:1"}  # type: ignore[union-attr]


def test_undo_to_drops_the_datum_registrations() -> None:
    ex = Executor(parse_plan(_plan()), _stub(), Verifier(), ExecutorConfig())
    ex.start()
    ex.run_to_end()
    reverted = ex.undo_to("s0")
    assert reverted == ("s1", "da", "dp")
    assert ex._ctx is not None
    assert ex._ctx.datum_plane_by_op == {} and ex._ctx.datum_axis_by_op == {}


def test_a_pose_mismatch_reported_by_the_adapter_fails_level2() -> None:
    stub = _stub()
    wrong = FRAME.model_copy(update={"origin": (0.0, 0.0, 12.0)})
    stub.scripts["dp"] = [Step(result=DatumPlaneResult(datum=DatumPlaneHandle(id="datum_plane:1"), frame=wrong))]
    ex = Executor(parse_plan(_plan()), stub, Verifier(), ExecutorConfig())
    ex.start()
    trace = ex.run_to_end()
    dp = trace.step("dp")
    assert dp is not None and dp.status is StepStatus.failed
    failing = {c.name for c in dp.last_level2() if c.ok is False}
    assert failing == {"datum_origin"}
    assert ex._ctx is not None and "dp" not in ex._ctx.datum_plane_by_op


class _NoDatums(StubAdapter):
    """An adapter that never overrides the optional datum tools (the contract default)."""

    def datum_plane(self, request: Request) -> Any:  # type: ignore[override]
        return Adapter.datum_plane(self, request)  # type: ignore[arg-type]


def test_an_adapter_without_datum_support_fails_the_step_with_an_actionable_error() -> None:
    stub = _NoDatums()
    stub.script("s0", _stub().scripts["s0"][0])
    ex = Executor(parse_plan(_plan()), stub, Verifier(), ExecutorConfig())
    ex.start()
    trace = ex.run_to_end()
    dp = trace.step("dp")
    assert dp is not None and dp.status is StepStatus.failed
    error = dp.attempts[-1].error
    assert error is not None and error.code is ErrorCode.not_supported
    assert "ref_plane" in error.message and "sketch on a base plane" in error.message
    assert len(dp.attempts) == 1  # not_supported is not recoverable: no retry loop
