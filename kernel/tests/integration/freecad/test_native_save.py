"""Native save on the real FreeCAD worker, through ``FreeCADAdapter.save_document``, on whichever FreeCAD
``COTAFORGE_FREECAD_ROOT`` points at (run it once per supported version).

A 14-operation multi-body plan (``tube_frame.py``) is built through the executor and saved; a SECOND worker process
opens the file and finds the same part as the live document: object list, per-body volume/area/exact box/Placement/
counts/face signature in element order, ``Document.Uid`` and the identity stamp in ``Document.Meta``. The write is
atomic and leaves no backup or temporary file behind.
"""

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import AdapterError, DocumentStamp, ErrorCode, SaveDocumentRequest
from tests.integration.freecad.tube_frame import tube_frame_plan
from tests.integration.freecad.worker_tools import call_hook, run_plan, stamp_for

pytestmark = pytest.mark.freecad


@pytest.fixture
def inspector(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=120.0,
        log_dir=tmp_path_factory.mktemp("inspect-logs"),
        enable_test_hooks=True,
    ) as adapter:
        yield adapter


@pytest.fixture
def writer(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=300.0,
        log_dir=tmp_path_factory.mktemp("writer-logs"),
        enable_test_hooks=True,
    ) as adapter:
        yield adapter


def test_a_saved_part_opens_identical_in_another_worker(
    tmp_path: Path, writer: FreeCADAdapter, inspector: FreeCADAdapter
) -> None:
    executor = run_plan(writer, tube_frame_plan())
    steps = len(executor.trace().active_steps())
    stamp = stamp_for("tube_frame", steps)
    model = tmp_path / "models" / "tube_frame.FCStd"
    model.parent.mkdir()

    saved = writer.save_document(SaveDocumentRequest(operation_id="save", path=str(model), stamp=stamp))
    live = call_hook(writer, "test_document_summary")

    assert Path(saved.path) == model and saved.size_bytes == model.stat().st_size
    assert sorted(os.listdir(model.parent)) == ["tube_frame.FCStd"]  # no .FCBak, no temporary
    bodies = [b for b in saved.adapter_state["bodies"] if not b["deleted"]]
    names = [b["result_obj"] for b in bodies]
    assert names and set(names) <= set(live["signatures"])

    reopened = call_hook(
        inspector, "test_inspect_saved_document", {"path": str(model), "objects": list(live["signatures"])}
    )
    assert reopened["invalid"] == []
    assert reopened["uid"] == saved.document_uid == live["uid"]
    assert reopened["objects"] == live["objects"]
    assert reopened["signatures"] == live["signatures"]  # the same part, face by face, in element order
    assert DocumentStamp.from_meta(reopened["meta"]) == stamp
    assert {k: v for k, v in reopened["meta"].items() if k.startswith("cotaforge.")} == {
        k: v for k, v in live["meta"].items() if k.startswith("cotaforge.")
    }
    for body in bodies:
        signature = reopened["signatures"][body["result_obj"]]
        measure = body["measure"]  # the worker's own per-body reading, kept in the adapter state
        assert signature is not None and signature["faces"]
        assert signature["volume"] == pytest.approx(measure["volume_mm3"], rel=1e-9)
        assert signature["counts"] == [measure["faces"], measure["edges"], measure["solids"]]
        live_faces = [f["index"] for f in body["faces"] if not f["stale"]]
        assert live_faces and max(live_faces) < len(signature["faces"])  # every live face handle has its index


def test_a_second_save_replaces_the_file_atomically(tmp_path: Path, writer: FreeCADAdapter) -> None:
    run_plan(writer, tube_frame_plan())
    model = tmp_path / "tube_frame.FCStd"
    first = writer.save_document(
        SaveDocumentRequest(operation_id="s1", path=str(model), stamp=stamp_for("tube_frame", 14))
    )
    assert first.sha256 == hashlib.sha256(model.read_bytes()).hexdigest()

    second = writer.save_document(
        SaveDocumentRequest(
            operation_id="s2",
            path=str(model),
            stamp=stamp_for("tube_frame", 14, save_seq=2),
            expected_sha256=first.sha256,
        )
    )

    assert second.sha256 == hashlib.sha256(model.read_bytes()).hexdigest() and second.sha256 != first.sha256
    assert sorted(os.listdir(tmp_path)) == ["tube_frame.FCStd"]  # no .FCBak, no temporary


def test_a_save_that_must_not_overwrite_refuses_an_existing_file(tmp_path: Path, writer: FreeCADAdapter) -> None:
    run_plan(writer, tube_frame_plan())
    model = tmp_path / "tube_frame.FCStd"
    writer.save_document(SaveDocumentRequest(operation_id="s1", path=str(model), stamp=stamp_for("tube_frame", 14)))
    kept = model.read_bytes()

    with pytest.raises(AdapterError) as refused:
        writer.save_document(
            SaveDocumentRequest(
                operation_id="s2", path=str(model), stamp=stamp_for("tube_frame", 14, 2), overwrite=False
            )
        )

    assert refused.value.info.recoverable and refused.value.info.code in (ErrorCode.internal, ErrorCode.invalid_request)
    assert model.read_bytes() == kept  # the live document is untouched and so is the file
