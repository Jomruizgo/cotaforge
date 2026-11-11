"""``FakeAdapter.save_document``: a deterministic, atomic stand-in for a native save.

The fake writes a canonical JSON file (the document's fingerprint plus the stamp), so the same document and stamp give
the same bytes on every run; the write goes through a temporary file and ``os.replace``; a failure never leaves a
partial file and never touches the live document."""

import hashlib
import json
from pathlib import Path

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    DocumentKind,
    DocumentStamp,
    ErrorCode,
    ErrorInfo,
    MassRequest,
    SaveDocumentRequest,
)
from tests.unit.fake.conftest import Session


def _stamp(save_seq: int = 1) -> DocumentStamp:
    return DocumentStamp(
        design_id="d",
        plan_id="plate",
        plan_revision=1,
        save_seq=save_seq,
        last_step_index=2,
        harness_version="0.0.0",
        plan_sha256="a" * 64,
    )


def _save(session: Session, path: Path, **fields: object) -> object:
    request = SaveDocumentRequest(operation_id="save", path=str(path), stamp=fields.pop("stamp", _stamp()), **fields)  # type: ignore[arg-type]
    return session.adapter.save_document(request)


def _plate(session: Session) -> None:
    session.new_part("plate")
    session.plate()


def test_save_document_is_a_capability_of_the_fake() -> None:
    assert "save_document" in FakeAdapter().capabilities().tools
    assert FakeAdapter.document_suffix == ".fakecad.json"


def test_a_save_writes_the_stamp_and_reports_hash_size_and_state(session: Session, tmp_path: Path) -> None:
    _plate(session)
    path = tmp_path / "models" / "plate.fakecad.json"
    result = _save(session, path)
    data = path.read_bytes()
    assert result.path == str(path)  # type: ignore[attr-defined]
    assert result.sha256 == hashlib.sha256(data).hexdigest()  # type: ignore[attr-defined]
    assert result.size_bytes == len(data)  # type: ignore[attr-defined]
    assert result.adapter_state["schema"] == "fake-document/2"  # type: ignore[attr-defined]
    assert result.document_uid  # type: ignore[attr-defined]
    payload = json.loads(data)
    assert DocumentStamp.from_meta(payload["meta"]) == _stamp()
    assert not list(path.parent.glob(".*tmp*"))  # no temporary file left behind


def test_the_same_document_and_stamp_give_the_same_bytes(tmp_path: Path) -> None:
    hashes = []
    for run in range(2):
        session = Session(FakeAdapter())
        _plate(session)
        hashes.append(_save(session, tmp_path / f"run{run}.fakecad.json").sha256)  # type: ignore[attr-defined]
    assert hashes[0] == hashes[1]


def test_saving_does_not_change_the_document(session: Session, tmp_path: Path) -> None:
    _plate(session)
    doc = session.adapter._active
    assert doc is not None
    before = doc.fingerprint()
    _save(session, tmp_path / "plate.fakecad.json")
    assert doc.fingerprint() == before


def test_a_relative_path_is_an_invalid_request(session: Session) -> None:
    _plate(session)
    with pytest.raises(AdapterError) as info:
        _save(session, Path("models/plate.fakecad.json"))
    assert info.value.info.code is ErrorCode.invalid_request


def test_no_active_document_is_reported(session: Session, tmp_path: Path) -> None:
    with pytest.raises(AdapterError) as info:
        _save(session, tmp_path / "x.fakecad.json")
    assert info.value.info.code is ErrorCode.no_active_document


def test_an_empty_assembly_is_saved_like_any_document(session: Session, tmp_path: Path) -> None:
    session.new_part("asm", DocumentKind.assembly)
    _save(session, tmp_path / "asm.fakecad.json")
    assert json.loads((tmp_path / "asm.fakecad.json").read_text(encoding="utf-8"))["document"]["kind"] == "assembly"


def test_an_io_failure_is_recoverable_and_leaves_no_file(session: Session, tmp_path: Path) -> None:
    _plate(session)
    blocker = tmp_path / "models"
    blocker.write_text("a file where the directory should be", encoding="utf-8")
    with pytest.raises(AdapterError) as info:
        _save(session, blocker / "plate.fakecad.json")
    assert info.value.info.code is ErrorCode.internal and info.value.info.recoverable is True
    assert info.value.info.details["reason"] == "io"


def test_an_interrupted_write_never_replaces_the_previous_file(session: Session, tmp_path: Path) -> None:
    _plate(session)
    path = tmp_path / "plate.fakecad.json"
    first = _save(session, path)
    previous = path.read_bytes()
    session.adapter.fail_before_replace = True  # type: ignore[attr-defined]
    with pytest.raises(AdapterError) as info:
        _save(session, path, stamp=_stamp(2), expected_sha256=first.sha256)  # type: ignore[attr-defined]
    assert info.value.info.recoverable is True and info.value.info.details["reason"] == "io"
    assert path.read_bytes() == previous
    assert sorted(p.name for p in tmp_path.iterdir()) == ["plate.fakecad.json"]


def test_a_file_someone_else_wrote_is_a_conflict(session: Session, tmp_path: Path) -> None:
    _plate(session)
    path = tmp_path / "plate.fakecad.json"
    first = _save(session, path)
    path.write_text("written by another program", encoding="utf-8")
    with pytest.raises(AdapterError) as info:
        _save(session, path, stamp=_stamp(2), expected_sha256=first.sha256)  # type: ignore[attr-defined]
    assert info.value.info.code is ErrorCode.internal and info.value.info.recoverable is True
    assert info.value.info.details["reason"] == "conflict"
    assert path.read_text(encoding="utf-8") == "written by another program"


def test_its_own_last_write_is_not_a_conflict_even_if_the_caller_never_heard_of_it(
    session: Session, tmp_path: Path
) -> None:
    _plate(session)
    path = tmp_path / "plate.fakecad.json"
    first = _save(session, path)
    _save(session, path, stamp=_stamp(2), expected_sha256=first.sha256)  # its reply "lost"
    third = _save(session, path, stamp=_stamp(3), expected_sha256=first.sha256)  # type: ignore[attr-defined]
    assert third.warnings == ()  # type: ignore[attr-defined]


def test_the_engineer_saving_the_same_document_is_not_a_conflict(session: Session, tmp_path: Path) -> None:
    _plate(session)
    path = tmp_path / "plate.fakecad.json"
    first = _save(session, path)
    session.adapter.simulate_engineer_save(path)  # type: ignore[attr-defined]
    assert hashlib.sha256(path.read_bytes()).hexdigest() != first.sha256  # type: ignore[attr-defined]
    second = _save(session, path, stamp=_stamp(2), expected_sha256=first.sha256)  # type: ignore[attr-defined]
    assert any("engineer" in w for w in second.warnings)  # type: ignore[attr-defined]


def test_overwrite_false_refuses_an_existing_file(session: Session, tmp_path: Path) -> None:
    _plate(session)
    path = tmp_path / "plate.fakecad.json"
    _save(session, path)
    with pytest.raises(AdapterError) as info:
        _save(session, path, overwrite=False)
    assert info.value.info.details["reason"] == "conflict"


def test_a_scripted_failure_is_raised_as_is(session: Session, tmp_path: Path) -> None:
    _plate(session)
    failure = ErrorInfo(code=ErrorCode.internal, message="disk full", details={"reason": "io"}, recoverable=True)
    session.adapter.save_failure = failure  # type: ignore[attr-defined]
    with pytest.raises(AdapterError) as info:
        _save(session, tmp_path / "plate.fakecad.json")
    assert info.value.info == failure
    assert not (tmp_path / "plate.fakecad.json").exists()
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 > 0
