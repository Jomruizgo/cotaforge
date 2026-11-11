"""``FakeAdapter.open_document``: reopen the canonical JSON file ``save_document`` wrote,
in a FRESH adapter (a new process), with the same ids, and report how the recorded handles re-bind.

The fake's "native file" carries the recorded construction history of the document, so reopening restores it
deterministically; a file edited out of band (the stand-in for an engineer editing the part in the CAD) is reported
as drift, never silently accepted. The fake never writes the file when opening it."""

import hashlib
import json
from pathlib import Path

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    ConnectRequest,
    CutRequest,
    DocumentStamp,
    Empty,
    ErrorCode,
    MassRequest,
    OpenDocumentRequest,
    PlaneName,
    ProvenanceRole,
    SaveDocumentRequest,
    SketchHandle,
)
from tests.unit.fake.conftest import Session


def _stamp() -> DocumentStamp:
    return DocumentStamp(
        design_id="d",
        plan_id="plate",
        plan_revision=1,
        save_seq=4,
        last_step_index=2,
        harness_version="0.0.0",
        plan_sha256="a" * 64,
    )


def _saved(session: Session, tmp_path: Path) -> tuple[Path, dict]:
    session.new_part("plate")
    plate = session.plate()
    top = session.faces(plate.feature, ProvenanceRole.any).faces  # type: ignore[arg-type]
    top_face = max(top, key=lambda f: f.center[2])
    hole_sketch = session.circle_sketch("hole_sketch", top_face.handle, (0.0, 0.0), 10.0)
    session.adapter.cut(CutRequest(operation_id="hole", sketch=SketchHandle(id=hole_sketch), depth_mm=4.0))
    path = tmp_path / "models" / "plate.fakecad.json"
    result = session.adapter.save_document(SaveDocumentRequest(operation_id="save", path=str(path), stamp=_stamp()))
    return path, result.adapter_state


def _fresh() -> FakeAdapter:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    return adapter


def _open(adapter: FakeAdapter, path: Path, state: dict) -> object:
    return adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(path), adapter_state=state))


def test_open_document_is_a_capability_of_the_fake() -> None:
    assert "open_document" in FakeAdapter().capabilities().tools


def test_a_fresh_adapter_reopens_the_same_document_with_the_same_ids(session: Session, tmp_path: Path) -> None:
    path, state = _saved(session, tmp_path)
    before = session.adapter._doc(Empty(operation_id="x"))
    mass_before = session.adapter.mass_properties(MassRequest(operation_id="m"))
    bytes_before = path.read_bytes()
    fresh = _fresh()
    result = _open(fresh, path, state)
    assert result.rebind.clean and result.rebind.bodies_ok  # type: ignore[attr-defined]
    assert result.stamp == _stamp()  # type: ignore[attr-defined]
    assert result.sha256 == hashlib.sha256(bytes_before).hexdigest()  # type: ignore[attr-defined]
    assert result.document.handle.id == before.id  # type: ignore[attr-defined]
    after = fresh._doc(Empty(operation_id="x"))
    assert after.fingerprint() == before.fingerprint()
    assert set(after.faces) == set(before.faces) and after.counters == before.counters
    assert fresh.mass_properties(MassRequest(operation_id="m")) == mass_before
    assert path.read_bytes() == bytes_before  # opening never writes
    # the reopened document goes on: a new op allocates the next ids, exactly as the original would
    sketch = Session(fresh).rect_sketch("more", PlaneName.XZ, (0.0, 0.0), 5.0, 5.0)
    ref = Session(session.adapter).rect_sketch("more", PlaneName.XZ, (0.0, 0.0), 5.0, 5.0)
    assert sketch == ref


def test_a_hand_edited_file_is_reported_as_drift(session: Session, tmp_path: Path) -> None:
    path, state = _saved(session, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["history"][1]["request"]["depth_mm"] = 12.0  # the engineer changed the plate's thickness in the CAD
    data["saved_by"] = "engineer"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = _open(_fresh(), path, state)
    assert not result.rebind.clean  # type: ignore[attr-defined]
    assert result.rebind.bodies_drifted  # type: ignore[attr-defined]


def test_a_feature_added_by_hand_is_a_foreign_object(session: Session, tmp_path: Path) -> None:
    session.new_part("plate")
    session.plate()
    session.adapter.inject_manual_feature("Pocket", "MyPocket", {"depth": 3.0})
    path = tmp_path / "plate.fakecad.json"
    result = session.adapter.save_document(SaveDocumentRequest(operation_id="s", path=str(path), stamp=_stamp()))
    state = dict(result.adapter_state)
    state["features"] = [f for f in state["features"] if f != "feature:2"]  # the save happened before the hand edit
    reopened = _open(_fresh(), path, state)
    assert reopened.rebind.foreign_objects == ("MyPocket",)  # type: ignore[attr-defined]


def test_errors_are_structured_and_leave_the_file_untouched(session: Session, tmp_path: Path) -> None:
    path, state = _saved(session, tmp_path)
    with pytest.raises(AdapterError) as missing:
        _open(_fresh(), tmp_path / "nope.fakecad.json", state)
    assert missing.value.info.code is ErrorCode.invalid_request and missing.value.info.details["reason"] == "missing"
    with pytest.raises(AdapterError) as relative:
        _fresh().open_document(OpenDocumentRequest(operation_id="o", path="plate.fakecad.json", adapter_state=state))
    assert relative.value.info.code is ErrorCode.invalid_request
    with pytest.raises(AdapterError) as schema:
        _open(_fresh(), path, {"schema": "fake-document/999"})
    assert schema.value.info.details["reason"] == "adapter_state"
    for corrupt in (b"", b"{not json", path.read_bytes()[: len(path.read_bytes()) // 2]):
        bad = tmp_path / "bad.fakecad.json"
        bad.write_bytes(corrupt)
        with pytest.raises(AdapterError) as broken:
            _open(_fresh(), bad, state)
        assert broken.value.info.code is ErrorCode.internal and broken.value.info.recoverable
        assert broken.value.info.details["reason"] == "corrupt"
        assert bad.read_bytes() == corrupt
