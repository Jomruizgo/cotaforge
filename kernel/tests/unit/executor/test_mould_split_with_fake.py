"""``mould_split`` through the Executor: the request it builds, and what the fake (which has no parting surface) says."""

from typing import Any

import pytest

from cotaforge.executor.trace import ExecutorState
from tests.unit.executor.test_with_fake import _fake, _run

PART = {"id": "part", "op": "box", "intent": "plastic part", "size": [100, 60, 20]}
SHRUNK = {
    "id": "shrunk",
    "op": "shrink_body",
    "intent": "shrunk part",
    "source": {"kind": "body", "from_op": "part"},
    "shrinkage_percent": 0.6,
    "factor_source": "resin datasheet",
}
SPLIT = {
    "id": "inserts",
    "op": "mould_split",
    "intent": "cavity and core",
    "source": {"kind": "body", "from_op": "shrunk"},
    "pull": "Z",
    "zero_draft_to": "cavity",
    "slides": [1],
    "margin_mm": 30,
}


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"id": "mould", "kind": "part", "operations": list(ops)}


def test_the_request_carries_the_pull_as_a_unit_vector_the_slides_and_the_block() -> None:
    from cotaforge.executor.build import DISPATCH

    method, builder = DISPATCH["mould_split"]
    assert method == "mould_split"
    from cotaforge.ir import parse_plan

    op = parse_plan(_plan(PART, SHRUNK, SPLIT)).operations[-1]

    class Resolver:
        def body(self, ref: Any, op_id: str, field: str) -> Any:
            from cotaforge.contract import BodyHandle

            return BodyHandle(id="body:2")

    request = builder(op, None, Resolver())  # type: ignore[arg-type]
    assert request.pull == pytest.approx((0.0, 0.0, 1.0))
    assert request.source.id == "body:2" and request.slides == (1,) and request.margin_mm == 30
    assert request.zero_draft_to == "cavity" and request.block_size is None


def test_the_fake_cannot_cut_a_parting_surface_and_the_step_says_so() -> None:
    traces, _ = _run([_plan(PART, SHRUNK, SPLIT)], _fake())
    trace = traces[0]
    assert trace.final_state != ExecutorState.completed
    why = str([(s.op_id, s.status, s.plan_error, [a.error for a in s.attempts if a.error]) for s in trace.steps])
    assert "cannot split a block along a parting surface" in why
    # the part and its shrunk copy before the split are untouched by the refusal
    assert [s.op_id for s in trace.steps][:2] == ["part", "shrunk"]
