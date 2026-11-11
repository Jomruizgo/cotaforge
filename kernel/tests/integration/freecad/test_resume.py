"""Resume on the real FreeCAD worker, through ``FreeCADAdapter.open_document`` and ``Executor.reattach``, on whichever
FreeCAD ``COTAFORGE_FREECAD_ROOT`` points at (run it once per supported version).

* the invariant: a 14-operation multi-body plan (``tube_frame.py``) is built and saved, its worker is KILLED (nothing
  closed or saved), and a NEW worker resumes it: the handles re-bind cleanly, same objects and same per-body
  volume/area/box/Placement/counts/face signatures in element order, same ``measure_bodies``; then an in-place edit and
  an ``extend`` give the same part as the same changes made in a session that never died;
* the loop save -> kill -> resume -> edit -> save -> kill -> resume;
* drift: the saved file is edited by ANOTHER FreeCAD process (a dimension changed, a feature added) and the re-bind
  says so; a truncated file is refused and left alone.
"""

import copy
import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.adapters.freecad import rpc as freecad_rpc
from cotaforge.contract import AdapterError, ConnectRequest, Empty, OpenDocumentRequest, SaveDocumentRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.errors import UndoFloorError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.integration.freecad.tube_frame import tube_frame_plan
from tests.integration.freecad.worker_tools import assert_close, call_hook, kill_worker, run_plan, stamp_for

pytestmark = pytest.mark.freecad

PLAN_ID = "tube_frame"
EXTRA = {"id": "lug_extra", "op": "box", "intent": "a lug added after the resume", "size": [6, 6, 6]}


def new_adapter(freecad_root: Path, log_dir: Path) -> FreeCADAdapter:
    return FreeCADAdapter(freecad_root=freecad_root, timeout_s=300.0, log_dir=log_dir, enable_test_hooks=True)


def save(adapter: FreeCADAdapter, executor: Executor, path: Path, seq: int, previous: str | None = None) -> Any:
    stamp = stamp_for(PLAN_ID, len(executor.trace().active_steps()), save_seq=seq)
    return adapter.save_document(
        SaveDocumentRequest(operation_id="save", path=str(path), stamp=stamp, expected_sha256=previous)
    )


def reopen(adapter: FreeCADAdapter, path: Path, adapter_state: dict[str, Any], original: Executor) -> Executor:
    """What a harness does with a reopened document: ``open_document``, then ``Executor.reattach`` over the trace and
    the context the previous executor left (nothing runs)."""
    request = OpenDocumentRequest(operation_id="open", path=str(path), adapter_state=adapter_state)
    opened = adapter.open_document(request)
    assert opened.rebind.clean, opened.rebind
    assert opened.rebind.bodies_ok and opened.stamp is not None and opened.stamp.plan_id == PLAN_ID
    context = original.context
    assert context is not None
    return Executor.reattach(
        original.plan,
        adapter,
        Verifier(),
        ExecutorConfig(),
        trace=original.trace(),
        context=copy.deepcopy(context),
        cursor=original.cursor,
        state=ExecutorState.completed,
        reverted_cursor=original.reverted_cursor,
        edited_requests=original.edited_requests,
        external_features=dict(original.external_features),
    )


def same_part(a: dict[str, Any], b: dict[str, Any], rel: float = 1e-9) -> None:
    assert a["objects"] == b["objects"] and a["invalid"] == [] and b["invalid"] == []
    assert sorted(a["signatures"]) == sorted(b["signatures"])
    for name, x in a["signatures"].items():
        y = b["signatures"][name]
        assert x["counts"] == y["counts"] and x["faces"] == y["faces"], name
        assert x["volume"] == pytest.approx(y["volume"], rel=rel), name


def measures(adapter: FreeCADAdapter) -> dict[str, Any]:
    return {b.body.id: b.measure.model_dump() for b in adapter.measure_bodies(Empty(operation_id="m")).bodies}


def widen_lug(plan: dict[str, Any], extra: float = 2.0) -> dict[str, Any]:
    changed = copy.deepcopy(plan)
    lug = next(op for op in changed["operations"] if op["id"] == "lug_plate")
    lug["size"][1] += extra
    return changed


def test_the_part_survives_a_killed_worker_and_goes_on_identically(
    freecad_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = tmp_path / "tube_frame.FCStd"
    with new_adapter(freecad_root, tmp_path / "logs-a") as first:
        original = run_plan(first, tube_frame_plan())
        saved = save(first, original, model, 1)
        before = call_hook(first, "test_document_summary")
        measured = measures(first)
        kill_worker(first)

    with new_adapter(freecad_root, tmp_path / "logs-b") as second:
        resumed = reopen(second, model, saved.adapter_state, original)
        same_part(call_hook(second, "test_document_summary"), before)
        assert_close(measures(second), measured)
        assert resumed.undo_floor == len(original.trace().steps)

        record = resumed.edit_in_place(parse_plan(widen_lug(tube_frame_plan())), ["lug_plate"], by="test")
        assert record.status == "applied", record.failure
        resumed.extend([EXTRA])
        resumed.run_to_end()
        assert resumed.state is ExecutorState.completed, resumed.trace().active_steps()[-1]
        after_resume = call_hook(second, "test_document_summary")
        measured_after = measures(second)

    with new_adapter(freecad_root, tmp_path / "logs-c") as reference:
        straight = run_plan(reference, tube_frame_plan())
        straight_record = straight.edit_in_place(parse_plan(widen_lug(tube_frame_plan())), ["lug_plate"], by="test")
        assert straight_record.status == "applied", straight_record.failure
        straight.extend([EXTRA])
        straight.run_to_end()
        # NOT necessarily bit-identical: reloaded shapes can differ from live ones in the last ulp of a volume
        same_part(after_resume, call_hook(reference, "test_document_summary"))
        assert_close(measured_after, measures(reference))
    with capsys.disabled():
        print(f"\nresume of the 14-op plan on {freecad_root}: re-bound clean; edit + extend equal a straight run")


def test_nothing_below_the_resume_point_can_be_undone(freecad_root: Path, tmp_path: Path) -> None:
    model = tmp_path / "tube_frame.FCStd"
    with new_adapter(freecad_root, tmp_path / "logs-a") as first:
        original = run_plan(first, tube_frame_plan())
        saved = save(first, original, model, 1)
    with new_adapter(freecad_root, tmp_path / "logs-b") as second:
        resumed = reopen(second, model, saved.adapter_state, original)
        resumed.extend([EXTRA])
        resumed.run_to_end()  # the new op runs after the resume
        with pytest.raises(UndoFloorError):
            resumed.undo_to("tube_o")
        assert resumed.state is ExecutorState.completed
        assert resumed.undo_to("frame") == ("lug_extra",)  # what ran after the resume can be undone
        assert [s.status for s in resumed.trace().active_steps()[-1:]] == [StepStatus.success]


def test_save_kill_resume_edit_save_kill_resume(freecad_root: Path, tmp_path: Path) -> None:
    model = tmp_path / "tube_frame.FCStd"
    plan = tube_frame_plan()
    with new_adapter(freecad_root, tmp_path / "logs-0") as first:
        executor = run_plan(first, plan)
        saved = save(first, executor, model, 1)
        kill_worker(first)
    for round_ in range(2):
        plan = widen_lug(plan, extra=1.0 + round_)
        with new_adapter(freecad_root, tmp_path / f"logs-{round_ + 1}") as adapter:
            executor = reopen(adapter, model, saved.adapter_state, executor)
            record = executor.edit_in_place(parse_plan(plan), ["lug_plate"], by="test")
            assert record.status == "applied", (round_, record.failure)
            saved = save(adapter, executor, model, round_ + 2, previous=saved.sha256)
            live = call_hook(adapter, "test_document_summary")
            kill_worker(adapter)
    assert executor.undo_floor > 0
    with new_adapter(freecad_root, tmp_path / "logs-final") as final:
        reopen(final, model, saved.adapter_state, executor)
        same_part(call_hook(final, "test_document_summary"), live)


def edit_with_freecad(freecad_root: Path, path: Path, script: str) -> None:
    """The engineer edits the saved file in ANOTHER FreeCAD process and saves it."""
    command = freecad_rpc._binaries(freecad_root)[0]
    code = (
        "import FreeCAD as App\n"
        f"doc = App.openDocument({str(path)!r})\n"
        f"{script}\n"
        "doc.recompute()\n"
        "doc.save()\n"
        "App.closeDocument(doc.Name)\n"
    )
    subprocess.run([str(command), "-c", code], check=True, capture_output=True, timeout=300)


def test_a_file_edited_in_another_freecad_is_re_bound_as_drifted(freecad_root: Path, tmp_path: Path) -> None:
    model = tmp_path / "tube_frame.FCStd"
    with new_adapter(freecad_root, tmp_path / "logs-a") as first:
        original = run_plan(first, tube_frame_plan())
        saved = save(first, original, model, 1)
    edit_with_freecad(
        freecad_root,
        model,
        "(plate,) = doc.getObjectsByLabel('lug_plate')\nplate.Width = plate.Width.Value + 5\n"
        "box = doc.addObject('Part::Box', 'HandBox')",
    )
    with new_adapter(freecad_root, tmp_path / "logs-b") as second:
        opened = second.open_document(
            OpenDocumentRequest(operation_id="open", path=str(model), adapter_state=saved.adapter_state)
        )
    assert not opened.rebind.clean
    assert opened.rebind.bodies_drifted and "HandBox" in opened.rebind.foreign_objects, opened.rebind


def test_a_truncated_file_is_refused_and_left_alone(freecad_root: Path, tmp_path: Path) -> None:
    model = tmp_path / "tube_frame.FCStd"
    with new_adapter(freecad_root, tmp_path / "logs-a") as first:
        original = run_plan(first, tube_frame_plan())
        saved = save(first, original, model, 1)
    model.write_bytes(model.read_bytes()[:5000])
    damaged = hashlib.sha256(model.read_bytes()).hexdigest()
    with new_adapter(freecad_root, tmp_path / "logs-b") as second:
        with pytest.raises(AdapterError) as refused:
            second.open_document(
                OpenDocumentRequest(operation_id="open", path=str(model), adapter_state=saved.adapter_state)
            )
        assert refused.value.info.details.get("reason") == "corrupt" and refused.value.info.recoverable
        assert second.connect(ConnectRequest(operation_id="again")) is not None  # the worker is still usable
    assert hashlib.sha256(model.read_bytes()).hexdigest() == damaged
