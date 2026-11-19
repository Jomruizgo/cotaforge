"""``shrink_body`` end to end: Executor + Verifier over the FakeAdapter (the cavity of a plastic part)."""

from typing import Any

import pytest

from cotaforge.contract import AdapterError, BodyHandle, ErrorCode, MassRequest, ShrinkBodyRequest
from cotaforge.executor.trace import ExecutorState
from tests.unit.executor.test_with_fake import _asserted_ok, _fake, _run

BLOCK = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}
CAVITY = {
    "id": "cavity",
    "op": "shrink_body",
    "intent": "cavity of the part",
    "source": {"kind": "body", "from_op": "part"},
    "shrinkage_percent": 0.6,
    "factor_source": "resin datasheet",
}


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"id": "mould", "kind": "part", "operations": list(ops)}


def _why(trace: Any) -> str:
    return str([(s.op_id, s.status, s.plan_error, [a.error for a in s.attempts if a.error]) for s in trace.steps])


def test_the_cavity_is_a_second_body_scaled_by_the_shrinkage_and_every_check_passes() -> None:
    traces, adapter = _run([_plan(BLOCK, CAVITY)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert {"volume_delta", "copy_bbox", "copy_centroid", "source_centroid"} <= _asserted_ok(trace, "cavity")
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(120000.0 * (1 + 1.006**3))
    assert trace.final_bbox is not None
    assert trace.final_bbox.max == pytest.approx((100.6, 60.36, 20.12))


def test_a_shrinkage_per_axis_is_something_the_fake_cannot_do_and_says_so() -> None:
    per_axis = {**CAVITY, "shrinkage_percent": [0.3, 0.9, 0.6]}
    adapter = _fake()
    traces, _ = _run([_plan(BLOCK)], adapter)
    body = BodyHandle(id="body:1")
    with pytest.raises(AdapterError) as failure:
        adapter.shrink_body(ShrinkBodyRequest(operation_id="x", source=body, scale=(1.003, 1.009, 1.006)))
    assert failure.value.info.code is ErrorCode.not_supported
    assert "per axis" in failure.value.info.message or "three axes" in failure.value.info.message
    assert per_axis["shrinkage_percent"] == [0.3, 0.9, 0.6]  # the op itself is valid: the adapter is what refuses
    assert traces[0].final_state == ExecutorState.completed
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(120000.0)
