"""End-to-end ``split`` and scaled ``copy_body``: Executor + Verifier driving the real FakeAdapter."""

from typing import Any

import pytest

from cotaforge.contract import MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier
from tests.unit.executor.test_with_fake import _asserted_ok, _fake, _run

XY = {"kind": "plane", "name": "XY"}
BLOCK = {"id": "block", "op": "box", "intent": "stock", "size": [100, 60, 20]}
PARTING = {
    "id": "parting",
    "op": "ref_plane",
    "intent": "parting plane 5 mm up",
    "define": {"how": "offset", "base": XY, "distance_mm": 5},
}


def _split(keep: str | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {
        "id": "halves",
        "op": "split",
        "intent": "cut at the parting plane",
        "source": {"kind": "body", "from_op": "block"},
        "plane": {"kind": "plane", "from_op": "parting"},
    }
    if keep is not None:
        op["keep"] = keep
    return op


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"id": "mould", "kind": "part", "operations": list(ops)}


def _move(side: str | None, offset: list[float], op_id: str = "lift") -> dict[str, Any]:
    target: dict[str, Any] = {"kind": "body", "from_op": "halves"}
    if side is not None:
        target["side"] = side
    return {"id": op_id, "op": "translate", "intent": "separate the halves", "target": target, "offset": offset}


def _why(trace: ExecutionTrace) -> str:
    return str([(s.op_id, s.status, s.plan_error, [a.error for a in s.attempts if a.error]) for s in trace.steps])


def test_a_split_that_keeps_both_passes_every_check_and_the_document_volume_is_unchanged() -> None:
    traces, _ = _run([_plan(BLOCK, PARTING, _split())], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert _asserted_ok(trace, "halves") == {"volume_delta", "split_volume_sum", "split_centroid", "split_sides"}
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(120000.0)


def test_each_piece_can_be_moved_apart_by_side() -> None:
    plan = _plan(BLOCK, PARTING, _split(), _move("positive", [0, 0, 30]), _move("negative", [0, 0, -30], "drop"))
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert trace.final_bbox is not None
    assert trace.final_bbox.min == pytest.approx((0.0, 0.0, -30.0))
    assert trace.final_bbox.max == pytest.approx((100.0, 60.0, 50.0))
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(120000.0)


def test_a_split_that_keeps_one_piece_asserts_the_kept_volume_for_a_tracked_box() -> None:
    traces, _ = _run([_plan(BLOCK, PARTING, _split("positive"), _move(None, [0, 0, 1]))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert _asserted_ok(trace, "halves") == {"volume_delta", "split_sides"}
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(90000.0)


def test_a_plane_that_misses_the_body_fails_in_one_attempt() -> None:
    far = {**PARTING, "define": {**PARTING["define"], "distance_mm": 50}}  # type: ignore[dict-item]
    traces, _ = _run([_plan(BLOCK, far, _split())], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    step = trace.step("halves")
    assert step is not None and len(step.attempts) == 1
    assert "plane does not cut the body" in " | ".join(a.error.message for a in step.attempts if a.error)


def test_the_pieces_by_side_are_registered_and_forgotten_when_the_split_is_undone() -> None:
    plan = parse_plan(_plan(BLOCK, PARTING, _split(), _move("negative", [0, 0, -30])))
    adapter = _fake()
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig(), {})
    executor.start()
    executor.run_to_end()
    ctx = executor._ctx
    assert ctx is not None and set(ctx.body_sides["halves"]) == {"positive", "negative"}
    assert ctx.body_by_op["halves"] == ctx.body_sides["halves"]["positive"]
    executor.undo_to("parting")
    assert "halves" not in ctx.body_sides and "halves" not in ctx.body_by_op
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(120000.0)


def test_a_scaled_copy_passes_every_check() -> None:
    copy = {
        "id": "shrunk",
        "op": "copy_body",
        "intent": "shrinkage allowance",
        "source": {"kind": "body", "from_op": "block"},
        "scale": 1.5,
        "scale_about": [0, 0, 0],
        "offset": [200, 0, 0],
    }
    traces, _ = _run([_plan(BLOCK, copy)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert _asserted_ok(trace, "shrunk") == {
        "volume_delta",
        "copy_centroid",
        "copy_surface",
        "source_centroid",
        "copy_bbox",
    }
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(120000.0 * (1 + 1.5**3))


def test_a_copy_of_a_piece_is_verified_against_that_piece() -> None:
    copy = {
        "id": "twin",
        "op": "copy_body",
        "intent": "duplicate the lower half",
        "source": {"kind": "body", "from_op": "halves", "side": "negative"},
        "offset": [200, 0, 0],
    }
    traces, _ = _run([_plan(BLOCK, PARTING, _split(), copy)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    step = trace.step("twin")
    assert step is not None
    delta = next(c for c in step.last_level2() if c.name == "volume_delta")
    assert delta.kind is CheckKind.asserted and delta.ok
    assert delta.expected == pytest.approx(30000.0)
