"""Save, close and open on the fake adapter: stamped round trip with the session record, adoption of any other file,
drift, damaged files and the refusal to overwrite work that is not ours."""

import json
import re
from pathlib import Path

import pytest

from cotaforge_mcp import checkpoint
from tests.conftest import Caller, plate
from tests.unit.test_tools import volume_of

HOLE = {
    "face": {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}},
    "position": [0, 0],
    "standard": "simple",
    "diameter_mm": 6,
    "end_condition": "through_all",
}


def saved_plate(call: Caller, tmp_path: Path) -> Path:
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    return tmp_path / "plate.fake"


def sidecar(tmp_path: Path) -> Path:
    return tmp_path / "plate.cotaforge.json"


def reopen(call: Caller) -> str:
    call.ok("doc", action="close")
    return call.ok("doc", action="open", path="plate.fake")


# --- the stamped round trip ---------------------------------------------------------------------------------------


def test_save_writes_the_file_and_the_session_record_next_to_it(call: Caller, tmp_path: Path) -> None:
    saved = saved_plate(call, tmp_path)
    record = checkpoint.read(sidecar(tmp_path))
    assert record is not None
    assert record.file_sha256 == checkpoint.sha256_file(saved) and record.save_seq == 1
    assert record.adapter_state["schema"] == "fake-document/2"
    assert record.facts["volume_mm3"] == pytest.approx(48000)
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []  # no temporary litter
    second = call.ok("doc", action="save")
    assert "saved:" in second and checkpoint.read(sidecar(tmp_path)).save_seq == 2  # type: ignore[union-attr]


def test_a_reopened_session_continues_with_the_same_step_ids(call: Caller, tmp_path: Path) -> None:
    saved_plate(call, tmp_path)
    opened = reopen(call)
    assert "step ids and handles kept" in opened
    hole = call.ok("op", name="hole", params=HOLE, id="drill")  # from_op s2 resolves against the reopened document
    assert volume_of(hole) == pytest.approx(48000 - 3.14159265358979 * 9 * 8, abs=1e-3)
    assert "UNSAVED" in call.ok("doc", action="status")

    undone = call.ok("undo")  # what the session added after the reopen can be undone
    assert undone.startswith("undone: drill hole") and volume_of(undone) == pytest.approx(48000)
    refused = call.err("undo")  # what was there before the reopen cannot
    assert "made before this document was reopened" in refused


def test_save_after_a_reopen_keeps_the_design_and_grows_the_sequence(call: Caller, tmp_path: Path) -> None:
    saved_plate(call, tmp_path)
    reopen(call)
    call.ok("op", name="hole", params=HOLE, id="drill")
    call.ok("doc", action="save")
    record = checkpoint.read(sidecar(tmp_path))
    assert record is not None and record.save_seq == 2
    again = reopen(call)
    assert "s2: extrude" in again and volume_of(again) == pytest.approx(48000 - 3.14159265358979 * 9 * 8, abs=1e-3)
    assert "drill" in call.ok("inspect", action="tree")


# --- any other file is adopted ---------------------------------------------------------------------------------------


def test_a_file_without_a_session_record_is_adopted_and_can_be_built_on(call: Caller, tmp_path: Path) -> None:
    saved_plate(call, tmp_path)
    call.ok("doc", action="close")
    sidecar(tmp_path).unlink()

    opened = call.ok("doc", action="open", path="plate.fake")

    assert "ADOPTED" in opened and "no step history" in opened
    alias = re.search(r"\n  (\w+): feature:", opened)
    assert alias is not None, opened
    assert volume_of(opened) == pytest.approx(48000)
    top = call.ok("inspect", action="faces", normal=[0, 0, 1])
    assert "faces: 1" in top and f"by={alias.group(1)}" in top
    hole = call.ok(
        "op",
        name="hole",
        params={**HOLE, "face": {"kind": "face", "from_op": alias.group(1), "select": {"normal": [0, 0, 1]}}},
    )
    assert volume_of(hole) == pytest.approx(48000 - 3.14159265358979 * 9 * 8, abs=1e-3)
    assert call.ok("undo").startswith("undone:")  # the new step is undoable...
    assert "nothing to undo" in call.err("undo")  # ...the adopted body is not


def test_a_file_edited_after_the_save_is_adopted_and_the_drift_is_reported(call: Caller, tmp_path: Path) -> None:
    saved = saved_plate(call, tmp_path)
    call.ok("doc", action="close")
    data = json.loads(saved.read_text(encoding="utf-8"))
    for entry in data["history"]:
        if entry["method"] == "extrude":
            entry["request"]["depth_mm"] = 12.0
    saved.write_text(json.dumps(data), encoding="utf-8")  # somebody else changed the plate thickness

    opened = call.ok("doc", action="open", path="plate.fake")

    assert "changed since the last save made by this server" in opened and "ADOPTED" in opened
    assert "drift: the file was edited after this server saved it" in opened
    assert "volume was 48000 mm3, now 72000 mm3" in opened
    assert "size was (100, 60, 8) mm, now (100, 60, 12) mm" in opened
    assert volume_of(opened) == pytest.approx(72000)


def test_a_damaged_session_record_is_ignored_and_the_file_is_adopted(call: Caller, tmp_path: Path) -> None:
    saved_plate(call, tmp_path)
    call.ok("doc", action="close")
    sidecar(tmp_path).write_text("{ not json", encoding="utf-8")
    opened = call.ok("doc", action="open", path="plate.fake")
    assert "session record ignored" in opened and "ADOPTED" in opened
    assert volume_of(opened) == pytest.approx(48000)


def test_a_session_record_of_another_file_state_is_not_trusted(call: Caller, tmp_path: Path) -> None:
    saved = saved_plate(call, tmp_path)
    call.ok("doc", action="close")
    saved.write_bytes(saved.read_bytes() + b"\n")  # same document, different bytes
    opened = call.ok("doc", action="open", path="plate.fake")
    assert "ADOPTED" in opened and "changed since the last save" in opened


# --- damaged files ---------------------------------------------------------------------------------------------------


def test_a_truncated_file_is_a_clean_error_and_nothing_stays_open(call: Caller, tmp_path: Path) -> None:
    saved = saved_plate(call, tmp_path)
    call.ok("doc", action="close")
    saved.write_bytes(saved.read_bytes()[:200])
    text = call.err("doc", action="open", path="plate.fake")
    assert "could not be opened" in text and "Traceback" not in text and "damaged or truncated" in text
    assert "document: none" in call.ok("doc", action="status")
    call.ok("doc", action="new", name="after")  # the server is still usable


def test_open_of_a_missing_file_names_the_path(call: Caller) -> None:
    text = call.err("doc", action="open", path="nope.fake")
    assert "does not exist" in text and "nope.fake" in text


# --- never overwrite work that is not ours -----------------------------------------------------------------------


def test_saving_a_new_document_over_an_existing_file_is_refused(call: Caller, tmp_path: Path) -> None:
    saved = saved_plate(call, tmp_path)
    before = saved.read_bytes()
    call.ok("doc", action="close")
    call.ok("doc", action="new", name="other")
    text = call.err("doc", action="save", path="plate.fake")
    assert "already exists" in text and "overwrite" in text
    assert saved.read_bytes() == before


def test_saving_over_a_file_changed_elsewhere_since_it_was_opened_is_refused(call: Caller, tmp_path: Path) -> None:
    saved = saved_plate(call, tmp_path)
    saved.write_text(saved.read_text(encoding="utf-8") + " ", encoding="utf-8")  # someone else wrote the file
    call.ok("op", name="box", params={"size": [1, 1, 1]})
    text = call.err("doc", action="save")
    assert "changed outside this server" in text and "another name" in text
    other = call.ok("doc", action="save", path="plate_v2.fake")
    assert "plate_v2.fake" in other


def test_a_failed_session_record_write_is_a_warning_not_a_failed_save(
    call: Caller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plate(call)

    def refuse(path: Path, record: checkpoint.Record) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(checkpoint, "write", refuse)
    saved = call.ok("doc", action="save", path="plate.fake")
    assert "saved:" in saved and "session record next to the file was not written" in saved
    assert (tmp_path / "plate.fake").is_file()
