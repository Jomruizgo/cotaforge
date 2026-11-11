"""Executor on an assembly plan: documents mapping, flip-retry for mates."""

from typing import Any

import pytest

from cotaforge.contract import (
    BoundingBox,
    ComponentHandle,
    DocumentHandle,
    FeatureHandle,
    MassProperties,
    MateCoincidentRequest,
    OperationResult,
    Transform,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier
from tests.unit.verification.helpers import planar_face

from .stub import Step, StubAdapter

BLOCK_BBOX = BoundingBox(min=(0, 0, 0), max=(100, 60, 20))
PIN_BBOX = BoundingBox(min=(-3, -3, 0), max=(3, 3, 40))
PIN_PLACED = BoundingBox(min=(197, -3, 5), max=(203, 3, 45))
PIN_MATED = BoundingBox(min=(197, -3, 20), max=(203, 3, 60))
PIN_WRONG_SIDE = BoundingBox(min=(197, -3, -20), max=(203, 3, 20))


def assembly_plan() -> dict[str, Any]:
    return {
        "id": "asm",
        "kind": "assembly",
        "operations": [
            {"id": "ins_block", "op": "insert_component", "intent": "i", "part": "block", "placement": {}},
            {"id": "fix", "op": "fix_component", "intent": "i", "component": "ins_block"},
            {
                "id": "ins_pin",
                "op": "insert_component",
                "intent": "i",
                "part": "pin",
                "placement": {"origin": [200, 0, 5]},
            },
            {
                "id": "seat",
                "op": "mate_coincident",
                "intent": "pin end on the block top",
                "a": {"kind": "face", "from_op": "ins_block", "select": {"normal": [0, 0, 1]}},
                "b": {"kind": "face", "from_op": "ins_pin", "select": {"normal": [0, 0, -1]}},
            },
            {"id": "chk", "op": "check_interference", "intent": "i"},
        ],
    }


def _mate_step(bbox: BoundingBox) -> Step:
    return Step(
        result=OperationResult(operation_id="seat", feature=FeatureHandle(id="feature:m1"), rebuild_ok=True),
        component_bbox_after={"comp:2": bbox},
    )


def assembly_stub(wrong_side: bool) -> StubAdapter:
    stub = StubAdapter()
    stub.mass_by_document["doc:block"] = MassProperties(volume_mm3=120000.0, centroid=(50, 30, 10))
    stub.bbox_by_document["doc:block"] = BLOCK_BBOX
    stub.mass_by_document["doc:pin"] = MassProperties(volume_mm3=1130.97, centroid=(0, 0, 20))
    stub.bbox_by_document["doc:pin"] = PIN_BBOX
    stub.add_face(
        planar_face(
            "face:top", (0, 0, 1), (50, 30, 20), 6000.0, BLOCK_BBOX, created_by="feature:c1", component="comp:1"
        ),
        feature_id="feature:c1",
    )
    stub.add_face(
        planar_face(
            "face:end", (0, 0, -1), (200, 0, 5), 28.27, PIN_PLACED, created_by="feature:c2", component="comp:2"
        ),
        feature_id="feature:c2",
    )
    stub.script(
        "ins_block",
        Step(
            result=OperationResult(
                operation_id="ins_block",
                feature=FeatureHandle(id="feature:c1"),
                rebuild_ok=True,
                component=ComponentHandle(id="comp:1"),
            ),
            mass_after=MassProperties(volume_mm3=120000.0, centroid=(50, 30, 10)),
            bbox_after=BLOCK_BBOX,
            component_bbox_after={"comp:1": BLOCK_BBOX},
            component_transform_after={"comp:1": Transform.identity()},
        ),
    )
    stub.script(
        "fix", Step(result=OperationResult(operation_id="fix", feature=FeatureHandle(id="feature:f"), rebuild_ok=True))
    )
    stub.script(
        "ins_pin",
        Step(
            result=OperationResult(
                operation_id="ins_pin",
                feature=FeatureHandle(id="feature:c2"),
                rebuild_ok=True,
                component=ComponentHandle(id="comp:2"),
            ),
            mass_after=MassProperties(volume_mm3=121130.97, centroid=(50, 30, 10)),
            bbox_after=BoundingBox(min=(0, -3, 0), max=(203, 60, 45)),
            component_bbox_after={"comp:2": PIN_PLACED},
            component_transform_after={"comp:2": Transform(origin=(200, 0, 5))},
        ),
    )
    if wrong_side:
        stub.script("seat", _mate_step(PIN_WRONG_SIDE), _mate_step(PIN_MATED))
    else:
        stub.script("seat", _mate_step(PIN_MATED))
    return stub


def _run(
    stub: StubAdapter, documents: dict[str, DocumentHandle] | None, config: ExecutorConfig | None = None
) -> Executor:
    ex = Executor(parse_plan(assembly_plan()), stub, Verifier(), config or ExecutorConfig(), documents)
    ex.start()
    ex.run_to_end()
    return ex


DOCS = {"block": DocumentHandle(id="doc:block"), "pin": DocumentHandle(id="doc:pin")}


def test_assembly_happy_path() -> None:
    ex = _run(assembly_stub(wrong_side=False), DOCS)
    trace = ex.trace()
    assert trace.final_state == ExecutorState.completed
    assert [s.status for s in trace.steps] == [StepStatus.success] * 5
    seat = trace.step("seat")
    assert seat is not None and len(seat.attempts) == 1 and seat.attempts[0].flipped is False
    assert {c.name for c in seat.last_level2() if c.kind == CheckKind.asserted and c.ok} == {
        "volume_delta",
        "component_bbox",
    }
    chk = trace.step("chk")
    assert chk is not None
    ok = {c.name for c in chk.last_level2() if c.ok}
    assert {"interference_total", "assembly_volume"} <= ok
    ins = trace.step("ins_pin")
    assert ins is not None and ins.resolved_refs == {"part": "doc:pin"}
    assert {"volume_delta", "component_bbox", "component_transform"} <= {c.name for c in ins.last_level2() if c.ok}


def test_check_interference_is_queried_exactly_once_per_attempt() -> None:
    """Regression: gather_observation must reuse the raw dispatch result, not re-invoke the adapter.

    Re-invoking ``check_interference`` from ``gather_observation`` desyncs the fake adapter's
    per-operation fault-engine attempt counter and can re-enter a fault outside the executor's
    per-attempt try/except (see test_with_fake.py for the crash repro against the real adapter).
    """
    stub = assembly_stub(wrong_side=False)
    ex = _run(stub, DOCS)
    trace = ex.trace()
    chk = trace.step("chk")
    assert chk is not None and chk.status == StepStatus.success and len(chk.attempts) == 1
    assert len(stub.calls_to("check_interference")) == 1


def test_missing_document_for_insert_component_is_a_plan_error() -> None:
    stub = assembly_stub(wrong_side=False)
    ex = _run(stub, {"block": DocumentHandle(id="doc:block")})
    trace = ex.trace()
    assert trace.final_state == ExecutorState.failed
    pin = trace.step("ins_pin")
    assert pin is not None and pin.status == StepStatus.plan_error and pin.attempts == ()
    assert "pin" in (pin.plan_error or "")
    assert not [c for c in stub.construction_calls() if c[1].operation_id == "ins_pin"]


def test_flip_retry_on_wrong_side_mate() -> None:
    stub = assembly_stub(wrong_side=True)
    ex = _run(stub, DOCS)
    trace = ex.trace()
    assert trace.final_state == ExecutorState.completed
    seat = trace.step("seat")
    assert seat is not None
    assert seat.status == StepStatus.success
    assert [a.flipped for a in seat.attempts] == [False, True]
    assert seat.attempts[0].undone is True
    assert seat.attempts[0].request["flip"] is False
    assert seat.attempts[1].request["flip"] is True
    first_failed = [c.name for c in seat.attempts[0].level2 if c.ok is False]
    assert first_failed == ["component_bbox"]
    assert all(c.ok for c in seat.attempts[1].level2 if c.kind == CheckKind.asserted)
    requests = [r for name, r in stub.calls if name == "mate_coincident"]
    assert [r.flip for r in requests] == [False, True]  # type: ignore[attr-defined]
    assert all(isinstance(r, MateCoincidentRequest) for r in requests)
    assert len(stub.calls_to("undo_last")) == 1


def test_flip_retry_disabled_fails_the_step() -> None:
    stub = assembly_stub(wrong_side=True)
    ex = _run(stub, DOCS, ExecutorConfig(flip_retry_for_mates=False))
    trace = ex.trace()
    assert trace.final_state == ExecutorState.failed
    seat = trace.step("seat")
    assert seat is not None and seat.status == StepStatus.failed and len(seat.attempts) == 1


def test_flip_retry_does_not_mark_undone_when_nothing_was_undone() -> None:
    """Regression: the flip-retry branch must only record ``undone=True`` when ``_undo_if_feature``
    actually undid something, mirroring the guard already used by the stop-on-failure branch."""

    class _NeverUndoes(Executor):
        def _undo_if_feature(self, op: Any, result: Any) -> bool:
            super()._undo_if_feature(op, result)  # still exercised, but its outcome is ignored
            return False

    stub = assembly_stub(wrong_side=True)
    ex = _NeverUndoes(parse_plan(assembly_plan()), stub, Verifier(), ExecutorConfig(), DOCS)
    ex.start()
    ex.run_to_end()
    trace = ex.trace()
    assert trace.final_state == ExecutorState.completed
    seat = trace.step("seat")
    assert seat is not None
    assert [a.flipped for a in seat.attempts] == [False, True]
    assert seat.attempts[0].undone is False


def test_flip_retry_happens_at_most_once() -> None:
    stub = assembly_stub(wrong_side=True)
    stub.scripts["seat"] = [_mate_step(PIN_WRONG_SIDE)]  # always wrong
    ex = _run(stub, DOCS)
    trace = ex.trace()
    assert trace.final_state == ExecutorState.failed
    seat = trace.step("seat")
    assert seat is not None
    assert [a.flipped for a in seat.attempts] == [False, True]
    assert seat.attempts[1].undone is True
    assert stub.component_bboxes["comp:2"] == PIN_PLACED  # restored by the undos


@pytest.mark.parametrize("op", ["mate_concentric"])
def test_flip_does_not_apply_to_concentric_mates(op: str) -> None:
    from cotaforge.executor.executor import MATE_FLIP_OPS

    assert op not in MATE_FLIP_OPS
