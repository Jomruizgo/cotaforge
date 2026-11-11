"""``save_document``: the native save.

Plain asserts run inside ``freecadcmd`` (see ``run.py``) on both supported FreeCAD lines. Every round trip saves the
live document, closes it, loads the file again with ``App.openDocument`` (no recompute) and compares, object by object,
what the save must preserve: volume, area, exact box, Placement, face/edge/solid counts and the type, area and centre
of every face in element order, plus the object list, the ``Document.Meta`` stamp and ``Document.Uid``. The
cross-PROCESS round trip (a second worker opens the file) is ``tests/integration/freecad/test_native_save.py``.
"""

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import Any

import FreeCAD as App
import gui
import main
import persist
import queries
from check_edit_in_place import build, frame_plan
from check_undo_depth import active_fc, active_rec
from harness import build_part01, build_part03, build_part04, call, expect_error, op

STAMP = {
    "design_id": "demo",
    "plan_id": "part_a",
    "plan_revision": 2,
    "save_seq": 1,
    "last_step_index": 5,
    "harness_version": "0.0.0",
    "plan_sha256": "a" * 64,
    "sidecar_sha256": None,
}


@contextlib.contextmanager
def workdir() -> Iterator[str]:
    path = tempfile.mkdtemp(prefix="cadh-save-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def save(path: str, **extra: Any) -> dict[str, Any]:
    return call("save_document", op("save", path=path, stamp={**STAMP, **extra.pop("stamp", {})}, **extra))


def sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def result_objects() -> list[str]:
    return [body.result_obj for body in active_rec().live_bodies()]


def forget_active() -> None:
    """Close the live document the way the worker would at exit (the file stays): a reload must not find it open."""
    rec = active_rec()
    App.closeDocument(rec.fc_doc.Name)
    main.WORKER.documents.pop(rec.id, None)
    main.WORKER.active = None


def round_trip(directory: str, name: str) -> dict[str, Any]:
    """Save the active document, reload it from disk and assert it is the same part; returns the save result."""
    names = result_objects()
    assert names, "nothing to compare"
    path = os.path.join(directory, "models", f"{name}.FCStd")
    result = save(path, stamp={"plan_id": name})
    live = queries.document_summary(active_fc(), names)
    assert live["invalid"] == [], live["invalid"]
    forget_active()
    again = queries.test_inspect_saved_document({"operation_id": "q", "path": path, "objects": names})
    assert again["invalid"] == [], again["invalid"]
    assert again["uid"] == live["uid"] == result["document_uid"]
    assert again["objects"] == live["objects"]
    for obj in names:
        assert live["signatures"][obj] is not None and live["signatures"][obj]["faces"], obj
        assert again["signatures"][obj] == live["signatures"][obj], (
            obj,
            again["signatures"][obj],
            live["signatures"][obj],
        )
    assert again["meta"]["cotaforge.plan_id"] == name
    assert again["meta"]["cotaforge.schema"] == "1" and again["meta"]["cotaforge.save_seq"] == "1"
    assert again["meta"]["cotaforge.sidecar_sha256"] == ""
    return result


def check_partdesign_parts_survive_a_save_and_reload() -> None:
    """Sketch + pad + hole + linear pattern; pocket on a face + fillet + chamfer; circular pattern on a bore axis."""
    with workdir() as directory:
        for name, builder in (("plate", build_part01), ("bracket", build_part03), ("flange", build_part04)):
            builder()
            result = round_trip(directory, name)
            state = result["adapter_state"]
            assert state["schema"] == persist.STATE_SCHEMA
            assert json.loads(json.dumps(state)) == state  # plain JSON all the way down


def check_a_part_chain_survives_a_save_and_reload() -> None:
    """The shape of a tubular frame: hollow tubes (cylinder - cylinder), a rotated stay, a lug, unions."""
    with workdir() as directory:
        build(frame_plan(), "frame")
        round_trip(directory, "frame")


def check_the_save_is_a_plain_copy_with_no_backup_and_points_freecad_at_it() -> None:
    build_part01()
    with workdir() as directory:
        path = os.path.join(directory, "models", "plate.FCStd")
        first = save(path)
        second = save(path, stamp={"save_seq": 2}, expected_sha256=first["sha256"])
        assert sorted(os.listdir(os.path.dirname(path))) == ["plate.FCStd"]  # no .FCBak, no temporary file
        assert second["sha256"] == sha256(path) and second["size_bytes"] == os.path.getsize(path)
        assert active_fc().FileName == path
        assert active_fc().Meta["cotaforge.save_seq"] == "2"
        # FreeCAD's own save (the engineer's Ctrl+S) now writes the SAME file, no "Save as"
        active_fc().save()
        assert os.path.isfile(path) and sha256(path) != second["sha256"]
        assert not [n for n in os.listdir(os.path.dirname(path)) if n.endswith(persist.TMP_MARK + ".FCStd")]


def check_a_save_as_of_the_harness_moves_freecads_own_save_to_the_new_file_without_a_warning() -> None:
    """The harness saves the document under another name: that is its own "save as", not the engineer saving elsewhere.
    FreeCAD's Ctrl+S follows the new file and nothing claims that the engineer did anything."""
    build_part01()
    with workdir() as directory:
        first_path = os.path.join(directory, "plate.FCStd")
        second_path = os.path.join(directory, "plate_v2.FCStd")
        save(first_path)
        moved = save(second_path, overwrite=False)
        assert moved["warnings"] == [], moved["warnings"]
        assert active_fc().FileName == second_path
        before = sha256(first_path)
        active_fc().save()
        assert sha256(first_path) == before  # the first file stays as it was last saved
        # an engineer's own save under a third name is a different story, said plainly
        third_path = os.path.join(directory, "elsewhere.FCStd")
        active_fc().saveAs(third_path)
        gui.note_engineer_save(active_fc().Name, third_path)
        again = save(second_path, stamp={"save_seq": 2})
        text = " ".join(again["warnings"])
        assert third_path in text and second_path in text and "Ctrl+S" in text, again["warnings"]


def check_an_engineer_save_is_kept_and_any_other_writer_is_a_conflict() -> None:
    build_part01()
    with workdir() as directory:
        path = os.path.join(directory, "plate.FCStd")
        first = save(path)
        lost = save(path, stamp={"save_seq": 2}, expected_sha256=first["sha256"])
        # the harness never heard of ``lost`` (a reply lost after the replace): its own last write is no conflict
        save(path, stamp={"save_seq": 3}, expected_sha256=first["sha256"])
        assert lost["sha256"] != first["sha256"]
        first = save(path, stamp={"save_seq": 4}, expected_sha256=sha256(path))
        with open(path, "wb") as handle:
            handle.write(b"another program wrote this")
        failure = expect_error(
            "internal", "save_document", op("s", path=path, stamp=STAMP, expected_sha256=first["sha256"])
        )
        assert failure.recoverable and failure.details["reason"] == "conflict"
        with open(path, "rb") as handle:
            assert handle.read() == b"another program wrote this"
        # what the visible worker's slotFinishSaveDocument records when the engineer presses Ctrl+S
        active_fc().save()
        gui.note_engineer_save(active_fc().Name, path)
        kept = save(path, stamp={"save_seq": 2}, expected_sha256=first["sha256"])
        assert any("engineer" in w for w in kept["warnings"]), kept["warnings"]
        refused = op("s", path=path, stamp=STAMP, overwrite=False)
        assert expect_error("internal", "save_document", refused).details["reason"] == "conflict"


def check_an_interrupted_save_never_replaces_the_previous_file() -> None:
    build_part01()
    with workdir() as directory:
        path = os.path.join(directory, "plate.FCStd")
        first = save(path)
        persist.FAIL_BEFORE_REPLACE = True
        try:
            failure = expect_error(
                "internal", "save_document", op("s", path=path, stamp=STAMP, expected_sha256=first["sha256"])
            )
        finally:
            persist.FAIL_BEFORE_REPLACE = False
        assert failure.recoverable and failure.details["reason"] == "io"
        assert sha256(path) == first["sha256"]
        assert sorted(os.listdir(directory)) == ["plate.FCStd"]
        assert call("mass_properties", op("m"))["volume_mm3"] > 0  # the live document is untouched


def check_bad_destinations_fail_without_touching_the_document() -> None:
    build_part01()
    volume = call("mass_properties", op("m"))["volume_mm3"]
    with workdir() as directory:
        expect_error("invalid_request", "save_document", op("s", path="models/plate.FCStd", stamp=STAMP))
        expect_error("invalid_request", "save_document", op("s", path=os.path.join(directory, "p.step"), stamp=STAMP))
        expect_error("invalid_request", "save_document", op("s", path=os.path.join(directory, "p.FCStd")))
        too_long = os.path.join(directory, "x" * 300 + ".FCStd")
        failure = expect_error("internal", "save_document", op("s", path=too_long, stamp=STAMP))
        assert failure.recoverable and failure.details["reason"] == "io"
        blocked = os.path.join(directory, "blocked")
        with open(blocked, "w", encoding="utf-8") as handle:
            handle.write("a file where the directory should be")
        failure = expect_error("internal", "save_document", op("s", path=os.path.join(blocked, "p.FCStd"), stamp=STAMP))
        assert failure.details["reason"] == "io"
        if os.name != "nt" and os.geteuid() != 0:
            read_only = os.path.join(directory, "read_only")
            os.makedirs(read_only)
            os.chmod(read_only, 0o555)
            try:
                failure = expect_error(
                    "internal", "save_document", op("s", path=os.path.join(read_only, "p.FCStd"), stamp=STAMP)
                )
                assert failure.details["reason"] == "io" and os.listdir(read_only) == []
            finally:
                os.chmod(read_only, 0o755)
    assert call("mass_properties", op("m"))["volume_mm3"] == volume


def check_an_assembly_is_saved_with_its_parts_by_relative_path() -> None:
    """An assembly is saved like a part; its components name the part files relative to its own."""
    with workdir() as directory:
        part = call("new_document", op("n", kind="part", name="cube"))
        call("box", op("cube_box", size=[10, 10, 10]))
        call("save_document", op("s", path=os.path.join(directory, "parts", "cube.FCStd"), stamp=STAMP))
        call("new_document", op("n", kind="assembly", name="asm"))
        saved = call("save_document", op("s", path=os.path.join(directory, "asm.FCStd"), stamp=STAMP))
        assert "components" not in saved["adapter_state"]
        call("insert_component", op("ins", part=part["handle"], transform={"origin": [0, 0, 0]}))
        saved = call("save_document", op("s", path=os.path.join(directory, "asm.FCStd"), stamp=STAMP))
        (component,) = saved["adapter_state"]["components"]
        assert component["part_file"] == "parts/cube.FCStd", component
        assert component["part_doc"] == part["handle"]["id"]


def check_the_registry_dump_covers_every_handle() -> None:
    parts = build_part03()
    rec = active_rec()
    state = persist.dump(rec)
    assert state["counters"] == dict(sorted(rec.counters.items()))
    assert [f["id"] for f in state["features"]] == [f.id for f in rec.features]
    assert all("journal" not in f and f["has_journal"] for f in state["features"])
    body = state["bodies"][0]
    live = rec.bodies[body["id"]]
    assert [f["id"] for f in body["faces"]] == list(live.faces)
    assert [f["index"] for f in body["faces"]] == [f.index for f in live.faces.values()]
    assert [e["id"] for e in body["edges"]] == list(live.edges)
    assert body["faces"][0]["sig"]["surface"] == live.faces[body["faces"][0]["id"]].sig.surface
    assert body["measure"]["volume_mm3"] > 0 and body["measure"]["solids"] == 1 and body["measure"]["faces"] > 6
    assert {s["id"] for s in state["sketches"]} == set(rec.sketches)
    assert parts["fillet"]["feature"]["id"] in {f["id"] for f in state["features"]}
    assert persist.dump(rec) == state  # deterministic for one document
    json.dumps(state)


def check_a_recovery_copy_leaves_the_document_and_its_file_alone() -> None:
    """``recovery_copy``: a snapshot of the live document for a crash to find. The document keeps its file, its stamp
    and the file of the engineer's own save, the copy replaces what is at its path, and it opens like a save."""
    build_part03()
    with workdir() as directory:
        engineer = os.path.join(directory, "models", "plate.FCStd")
        save(engineer)
        before = sha256(engineer)
        fc_doc = active_fc()
        meta, name = dict(fc_doc.Meta), str(fc_doc.FileName)
        copy = os.path.join(directory, "recovery", "plate.FCStd")
        for _ in range(2):  # the second one replaces the first
            result = call("save_document", op("recover", path=copy, stamp=STAMP, recovery_copy=True))
            assert result["path"] == copy and result["sha256"] == sha256(copy) and result["adapter_state"]["schema"]
        assert sha256(engineer) == before, "the engineer's file was touched"
        assert str(fc_doc.FileName) == name and dict(fc_doc.Meta) == meta, "the document moved to the copy"
        assert not [n for n in os.listdir(os.path.dirname(copy)) if n.startswith(".") or n.endswith(".tmp")]
        names = result_objects()
        live = queries.document_summary(fc_doc, names)
        forget_active()
        again = queries.test_inspect_saved_document({"operation_id": "q", "path": copy, "objects": names})
        assert again["invalid"] == [] and again["objects"] == live["objects"], "the copy is not the document"


def check_a_recovery_copy_of_an_assembly_is_refused() -> None:
    with workdir() as directory:
        part = call("new_document", op("n", kind="part", name="cube"))
        call("box", op("cube_box", size=[10, 10, 10]))
        call("save_document", op("s", path=os.path.join(directory, "parts", "cube.FCStd"), stamp=STAMP))
        call("new_document", op("n", kind="assembly", name="asm"))
        call("save_document", op("s", path=os.path.join(directory, "asm.FCStd"), stamp=STAMP))
        call("insert_component", op("ins", part=part["handle"], transform={"origin": [0, 0, 0]}))
        copy = os.path.join(directory, "recovery", "asm.FCStd")
        expect_error("invalid_request", "save_document", op("r", path=copy, stamp=STAMP, recovery_copy=True))
        assert not os.path.exists(copy)
