"""``Executor.reattach`` (``resume`` already un-pauses an executor): an executor rebuilt over a REOPENED document
from its trace and its saved context, without running anything, and the undo floor: after a resume, ``undo_to``
cannot revert a step that ran before it (refused before the adapter is called)."""

from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    ConnectRequest,
    DocumentStamp,
    Empty,
    OpenDocumentRequest,
    SaveDocumentRequest,
)
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import UndoFloorError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict


def _fake() -> FakeAdapter:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    return adapter


def _saved_after(tmp_path: Path, steps: int) -> tuple[Executor, Path, dict[str, Any]]:
    executor = Executor(parse_plan(plate_plan_dict()), _fake(), Verifier())
    executor.start()
    for _ in range(steps):
        assert executor.next_operation().status is StepStatus.success
    path = tmp_path / "plate.fakecad.json"
    stamp = DocumentStamp(
        design_id="d",
        plan_id="plate",
        plan_revision=1,
        save_seq=1,
        last_step_index=steps,
        harness_version="0",
        plan_sha256="a",
    )
    state = executor.adapter.save_document(SaveDocumentRequest(operation_id="s", path=str(path), stamp=stamp))
    return executor, path, state.adapter_state


def _resumed(original: Executor, path: Path, state: dict[str, Any]) -> tuple[Executor, FakeAdapter]:
    adapter = _fake()
    adapter.open_document(OpenDocumentRequest(operation_id="o", path=str(path), adapter_state=state))
    ctx = original.context
    assert ctx is not None
    context = ExecutionContext(
        document=ctx.document,
        documents=dict(ctx.documents),
        feature_by_op=dict(ctx.feature_by_op),
        sketch_by_op=dict(ctx.sketch_by_op),
        sketch_frame_by_op=dict(ctx.sketch_frame_by_op),
        sketch_entities_by_op=dict(ctx.sketch_entities_by_op),
        body_by_op=dict(ctx.body_by_op),
        body_sides={k: dict(v) for k, v in ctx.body_sides.items()},
        component_by_op=dict(ctx.component_by_op),
        datum_plane_by_op=dict(ctx.datum_plane_by_op),
        datum_axis_by_op=dict(ctx.datum_axis_by_op),
    )
    resumed = Executor.reattach(
        original.plan,
        adapter,
        Verifier(),
        trace=original.trace(),
        context=context,
        cursor=original.cursor,
        state=ExecutorState.paused if original.cursor < len(original.plan.operations) else ExecutorState.completed,
        reverted_cursor=original.reverted_cursor,
        edited_requests=original.edited_requests,
        external_features=dict(original.external_features),
    )
    return resumed, adapter


def test_resume_runs_nothing_and_continues_where_the_saved_executor_stood(tmp_path: Path) -> None:
    original, path, state = _saved_after(tmp_path, 2)
    resumed, adapter = _resumed(original, path, state)
    assert resumed.cursor == 2 and resumed.state is ExecutorState.paused
    assert resumed.trace().steps == original.trace().steps
    assert resumed.undo_floor == len(original.trace().steps)
    assert len(adapter._doc(Empty(operation_id="x")).history) == 2  # the reopened document, nothing re-run
    resumed.resume()
    while resumed.state is ExecutorState.running:
        assert resumed.next_operation().status is StepStatus.success
    original.resume()
    original.run_to_end()
    assert resumed.trace().final_mass == original.trace().final_mass
    assert resumed.context.feature_by_op == original.context.feature_by_op  # type: ignore[union-attr]


def test_undo_below_the_resume_point_is_refused_before_the_adapter_is_called(tmp_path: Path) -> None:
    original, path, state = _saved_after(tmp_path, 3)
    resumed, adapter = _resumed(original, path, state)
    resumed.resume()
    resumed.run_to_end()  # the fourth op runs after the resume
    calls: list[str] = []
    real = adapter.undo_last
    adapter.undo_last = lambda request: calls.append(request.operation_id) or real(request)  # type: ignore[method-assign]
    first_op = original.plan.operations[0].id
    with pytest.raises(UndoFloorError) as refused:
        resumed.undo_to(first_op)
    assert calls == [] and resumed.state is ExecutorState.completed
    assert refused.value.floor_op == original.plan.operations[2].id
    # undoing what ran AFTER the resume is fine
    third = original.plan.operations[2].id
    assert resumed.undo_to(third) == (original.plan.operations[3].id,)
    assert calls == [original.plan.operations[3].id]
